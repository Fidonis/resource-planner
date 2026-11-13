"""Sign-in with an identity provider: login state, code exchange and the account behind a token."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.oidc import OidcError, OidcProvider, new_code_verifier, roles_from
from planner_api.auth.passwords import hash_token, new_secret
from planner_api.config import Settings
from planner_api.models import AppUser, AuditLog, LoginState, Member
from planner_api.repositories import auth as repo
from planner_api.services.accounts import oidc_provider_name
from planner_api.services.context import utcnow

LOGIN_STATE_MINUTES = 10
BINDING_COOKIE = "rp_oidc"
MAX_FIELD = 160
MAX_ID_TOKEN_HINT = 4000  # the width of app_session.id_token_hint


class OidcAccountDisabledError(OidcError):
    """The account exists but an administrator switched it off."""


def safe_return_to(value: str | None) -> str:
    """Only a path of this site: no scheme, no host, no way out of it."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 32 for ch in value) or len(value) > 500:
        return "/"
    return value


async def begin_login(
    session: AsyncSession, provider: OidcProvider, next_url: str | None
) -> tuple[str, str]:
    """Store the state of a new sign-in. Returns the provider's URL and the binding secret.

    The secret goes into a cookie, so only the browser that started the sign-in can finish it.
    """
    state, nonce, binding = new_secret(24), new_secret(24), new_secret(24)
    verifier = new_code_verifier()
    url = await provider.authorization_url(state=state, nonce=nonce, verifier=verifier)
    now = utcnow()
    await repo.delete_expired_sessions(session, now)
    session.add(
        LoginState(
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            binding_hash=hash_token(binding),
            return_to=safe_return_to(next_url),
            expires_at=now + timedelta(minutes=LOGIN_STATE_MINUTES),
        )
    )
    await session.commit()
    return url, binding


async def _roles_for_login(
    provider: OidcProvider, settings: Settings, id_claims: dict[str, Any], tokens: dict[str, Any]
) -> list[str]:
    path = settings.oidc_roles_claim
    source = settings.oidc_roles_source
    if source == "id_token":
        return roles_from(id_claims, path)
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        raise OidcError("The token response carries no access token")
    if source == "userinfo":
        return roles_from(await provider.userinfo(access), path)
    access_claims = await provider.verify(access, audience=None)
    if "sub" not in access_claims:
        raise OidcError(
            "The access token has no 'sub' claim (Keycloak adds it with the client scope 'basic')"
        )
    if access_claims["sub"] != id_claims.get("sub"):
        raise OidcError("The access token belongs to somebody else than the ID token")
    return roles_from(access_claims, path)


async def complete_login(
    session: AsyncSession,
    settings: Settings,
    provider: OidcProvider,
    *,
    code: str,
    state: str,
    binding: str | None,
) -> tuple[AppUser, str, str]:
    """Finish a sign-in. Returns the account, the ID token (for logout) and where to go next."""
    row = await repo.get_login_state(session, state)
    if row is None:
        raise OidcError("The sign-in state is unknown or was used already")
    await session.delete(row)
    await session.commit()  # a state is good for one attempt, whatever its outcome
    if row.expires_at <= utcnow():
        raise OidcError("The sign-in took too long")
    if not binding or not secrets.compare_digest(row.binding_hash, hash_token(binding)):
        raise OidcError("The sign-in was not started by this browser")

    tokens = await provider.exchange_code(code, row.code_verifier)
    id_token: str = tokens["id_token"]
    claims = await provider.verify(id_token, audience=settings.oidc_client_id, nonce=row.nonce)
    audience = claims.get("aud")
    if (
        isinstance(audience, list)
        and len(audience) > 1
        and claims.get("azp") != (settings.oidc_client_id)
    ):
        raise OidcError("The ID token was issued to another client")
    roles = await _roles_for_login(provider, settings, claims, tokens)
    user, _ = await provision_user(session, settings, claims, roles, signed_in=True)
    await session.commit()
    return user, id_token, row.return_to


def _text(value: object, limit: int = MAX_FIELD) -> str | None:
    return value.strip()[:limit] or None if isinstance(value, str) else None


async def _free_username(session: AsyncSession, wanted: str, subject: str) -> str:
    if await repo.get_user_by_username(session, wanted) is None:
        return wanted
    return f"{wanted[: MAX_FIELD - 9]}~{subject[:8]}"


async def _link_member(session: AsyncSession, user: AppUser, email: str) -> None:
    """Connect the account to the member with the same (verified) e-mail address."""
    if await repo.member_of_user(session, user.id) is not None:
        return
    stmt = select(Member).where(func.lower(Member.email) == email.lower(), Member.user_id.is_(None))
    member = (await session.scalars(stmt)).first()
    if member is not None:
        member.user_id = user.id


async def provision_user(
    session: AsyncSession,
    settings: Settings,
    claims: dict[str, Any],
    roles: list[str],
    *,
    signed_in: bool,
) -> tuple[AppUser, bool]:
    """The account for a verified token, created on first sight (FR-AUTH-04).

    Returns the account and whether anything about it changed. ``signed_in`` marks an interactive
    sign-in, which also records the time of the sign-in.
    """
    provider_name = oidc_provider_name(settings)
    subject = _text(claims.get("sub"), 255)
    if subject is None:
        raise OidcError("The token has no subject")
    username = _text(claims.get(settings.oidc_username_claim)) or subject[:MAX_FIELD]
    display_name = _text(claims.get("name")) or username
    email = _text(claims.get("email"), 255)
    verified = claims.get("email_verified") is True

    user = await repo.get_user_by_subject(session, provider_name, subject)
    changed = False
    if user is None:
        user = AppUser(
            username=await _free_username(session, username, subject),
            display_name=display_name,
            email=email,
            auth_provider=provider_name,
            external_subject=subject,
            external_roles=roles,
            active=True,
        )
        session.add(user)
        await session.flush()
        session.add(
            AuditLog(
                actor_name="system",
                via="oidc",
                action="provision",
                entity_type="user",
                entity_id=str(user.id),
                after={
                    "username": user.username,
                    "authProvider": provider_name,
                    "externalRoles": roles,
                },
            )
        )
        changed = True
    else:
        if not user.active:
            raise OidcAccountDisabledError("The account is disabled")
        for name, value in (
            ("display_name", display_name),
            ("email", email or user.email),
            ("external_roles", roles),
        ):
            if getattr(user, name) != value:
                setattr(user, name, value)
                changed = True
    if signed_in:
        await session.flush()
        await repo.record_login(session, user.id, utcnow())
        changed = True
        session.add(
            AuditLog(
                actor_user_id=user.id,
                actor_name=user.username,
                via="session",
                action="login",
                entity_type="user",
                entity_id=str(user.id),
            )
        )
    if email and verified:
        await _link_member(session, user, email)
    await session.flush()
    return user, changed
