"""Idempotent start-up reconciliation: built-in roles, default data, the bootstrap admin."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from planner_api.auth.passwords import hash_password, policy_violations
from planner_api.auth.permissions import BUILTIN_ROLES, DEFAULT_ROLE_MAPPING
from planner_api.config import Settings
from planner_api.models import (
    AbsenceType,
    AppSetting,
    AppUser,
    AuditLog,
    ExternalRoleMapping,
    Role,
    RolePermission,
    UserRole,
)
from planner_api.repositories import auth as auth_repo
from planner_api.services.accounts import LOCAL_PROVIDER

log = logging.getLogger("planner_api.bootstrap")

# Serialises concurrent starts of several workers.
_LOCK_KEY = 727275

DEFAULT_ABSENCE_TYPES: tuple[tuple[str, str, str, str, int], ...] = (
    ("vacation", "Vacation", "#2a78d6", "V", 10),
    ("sick", "Sick", "#c53c3c", "S", 20),
    ("training", "Training", "#7a4fc4", "T", 30),
    ("public-holiday", "Public holiday", "#a87820", "H", 40),
)


async def reconcile(factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    async with factory() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
        roles = await reconcile_roles(session)
        await seed_defaults(session, settings, roles)
        await bootstrap_admin(session, settings, roles)
        await session.commit()


async def reconcile_roles(session: AsyncSession) -> dict[str, Role]:
    """Make the built-in roles match the code. Custom roles are never touched."""
    stmt = select(Role).options(selectinload(Role.permission_links))
    existing = {role.key: role for role in (await session.scalars(stmt)).all()}
    for spec in BUILTIN_ROLES:
        role = existing.get(spec.key)
        wanted = set(spec.permissions)
        if role is None:
            role = Role(
                key=spec.key,
                name=spec.name,
                description=spec.description,
                builtin=True,
                permission_links=[RolePermission(permission=p) for p in sorted(wanted)],
            )
            session.add(role)
            existing[spec.key] = role
            continue
        role.builtin = True
        role.name = spec.name
        role.description = spec.description
        have = {link.permission for link in role.permission_links}
        for link in [link for link in role.permission_links if link.permission not in wanted]:
            role.permission_links.remove(link)
        role.permission_links.extend(RolePermission(permission=p) for p in sorted(wanted - have))
    await session.flush()
    return existing


async def _seeded(session: AsyncSession, key: str) -> bool:
    return (await session.get(AppSetting, key)) is not None


async def _mark_seeded(session: AsyncSession, key: str) -> None:
    statement = insert(AppSetting).values(key=key, value=True)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[AppSetting.key], set_={"value": statement.excluded.value}
        )
    )


async def seed_defaults(session: AsyncSession, settings: Settings, roles: dict[str, Role]) -> None:
    """Defaults a first start creates once. A later deletion by an administrator stays deleted."""
    if not await _seeded(session, "seeded_absence_types"):
        have = set((await session.scalars(select(AbsenceType.key))).all())
        for key, name, color, letter, order in DEFAULT_ABSENCE_TYPES:
            if key not in have:
                session.add(
                    AbsenceType(key=key, name=name, color=color, letter=letter, sort_order=order)
                )
        await _mark_seeded(session, "seeded_absence_types")
    if not await _seeded(session, "seeded_role_mapping"):
        for external, key in DEFAULT_ROLE_MAPPING.items():
            session.add(
                ExternalRoleMapping(
                    provider_id=settings.oidc_provider_id,
                    external_role=external,
                    role_id=roles[key].id,
                )
            )
        await _mark_seeded(session, "seeded_role_mapping")
    await session.flush()


async def bootstrap_admin(
    session: AsyncSession, settings: Settings, roles: dict[str, Role]
) -> None:
    """On an empty user table create the first administrator, who must pick a new password."""
    if not settings.auth_local_enabled or await auth_repo.count_users(session):
        return
    username = settings.bootstrap_admin_user.strip()
    password = settings.bootstrap_admin_password
    if not username or not password:
        log.warning(
            "There are no users and BOOTSTRAP_ADMIN_USER / BOOTSTRAP_ADMIN_PASSWORD are not set; "
            "nobody can sign in with a local account until they are."
        )
        return
    problems = policy_violations(password, minimum=settings.password_min_length, username=username)
    if problems:
        raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD is not acceptable: {problems[0]}")
    user = AppUser(
        username=username,
        display_name="Administrator",
        auth_provider=LOCAL_PROVIDER,
        password_hash=hash_password(password),
        must_change_password=True,
        user_roles=[UserRole(role_id=roles["admin"].id)],
    )
    session.add(user)
    await session.flush()
    session.add(
        AuditLog(
            actor_name="system",
            via="system",
            action="bootstrap-admin",
            entity_type="user",
            entity_id=str(user.id),
            after={"username": username, "at": datetime.now(UTC).isoformat()},
        )
    )
    log.info("Created the bootstrap administrator '%s'; a password change is required.", username)
