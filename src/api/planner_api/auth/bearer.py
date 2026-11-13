"""Bearer tokens issued by the identity provider (FR-AUTH-05).

A client that already holds an access token of the user (a command-line tool, a later MCP server)
sends it as ``Authorization: Bearer``. The token is checked against the provider's keys, issuer
and the audiences the server accepts; the roles in it, not the roles of an earlier sign-in,
decide what the call may do.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.oidc import OidcError, OidcProvider, roles_from
from planner_api.auth.principal import Principal
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError
from planner_api.services import accounts
from planner_api.services import oidc as flow

CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _refused(detail: str) -> ProblemError:
    return ProblemError(401, ErrorCode.AUTH_REQUIRED, detail, headers=CHALLENGE)


async def bearer_principal(
    request: Request, session: AsyncSession, settings: Settings, token: str
) -> Principal:
    provider: OidcProvider | None = getattr(request.app.state, "oidc", None)
    if provider is None or not settings.auth_oidc_enabled or not settings.oidc_audience_list:
        raise _refused("Bearer tokens are not accepted on this server.")
    try:
        claims = await provider.verify(token, audience=settings.oidc_audience_list)
        roles = roles_from(claims, settings.oidc_roles_claim)
        if not roles and settings.oidc_roles_source == "userinfo":
            roles = roles_from(await provider.userinfo(token), settings.oidc_roles_claim)
        user, changed = await flow.provision_user(session, settings, claims, roles, signed_in=False)
    except flow.OidcAccountDisabledError as exc:
        raise ProblemError(
            403, ErrorCode.ACCOUNT_DISABLED, "The account is disabled.", headers=CHALLENGE
        ) from exc
    except OidcError as exc:
        raise _refused(
            "The bearer token is invalid, expired or not meant for this server."
        ) from exc
    if changed:
        await session.commit()
    return await accounts.build_principal(session, settings, user)
