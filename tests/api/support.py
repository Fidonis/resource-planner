"""Helpers shared by the API tests: a caller with its own session, and a test harness."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner_api.auth.passwords import hash_password
from planner_api.config import Settings
from planner_api.models import (
    Absence,
    AbsenceType,
    AppUser,
    Assignment,
    Base,
    Role,
    RolePermission,
    Task,
    UserRole,
    UserRoleTeam,
)

BOOTSTRAP_USER = "admin"
BOOTSTRAP_PASSWORD = "Bootstrap-Passw0rd!"
USER_PASSWORD = "Correct-Horse-Battery-9"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
FIRST_WEEK = date(2030, 1, 7)  # a Monday


class Caller:
    """An HTTP client with its own cookie jar and, once signed in, the CSRF token."""

    def __init__(self, http: httpx.AsyncClient, me: dict[str, Any] | None = None) -> None:
        self.http = http
        self.me = me
        self.csrf: str | None = me["csrfToken"] if me else None

    async def send(
        self, method: str, url: str, *, client_name: str | None = "tests", **kwargs: Any
    ) -> httpx.Response:
        headers: dict[str, str] = dict(kwargs.pop("headers", {}))
        if client_name:
            headers.setdefault("X-Client-Name", client_name)
        if self.csrf and method in UNSAFE:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return await self.http.request(method, url, headers=headers, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.send("GET", url, **kwargs)

    async def post(self, url: str, json: Any = None, **kwargs: Any) -> httpx.Response:
        return await self.send("POST", url, json=json, **kwargs)

    async def put(self, url: str, json: Any = None, **kwargs: Any) -> httpx.Response:
        return await self.send("PUT", url, json=json, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.send("DELETE", url, **kwargs)

    async def create(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST and insist on success; returns the created record."""
        response = await self.post(url, body)
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created


@dataclass
class Api:
    app: FastAPI
    factory: async_sessionmaker[AsyncSession]
    settings: Settings
    _clients: list[httpx.AsyncClient] = field(default_factory=list)

    def http(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self._clients.append(client)
        return client

    def anonymous(self) -> Caller:
        return Caller(self.http())

    async def add_user(
        self,
        username: str,
        *roles: str,
        teams: Iterable[uuid.UUID] = (),
        permissions: Iterable[str] | None = None,
        password: str = USER_PASSWORD,
        active: bool = True,
        must_change_password: bool = False,
    ) -> AppUser:
        """Create a local user with built-in roles, or a custom role of exactly ``permissions``."""
        team_ids = list(teams)
        async with self.factory() as session:
            keys = list(roles)
            if permissions is not None:
                custom = Role(
                    key=f"custom-{uuid.uuid4().hex[:8]}",
                    name="Custom",
                    permission_links=[RolePermission(permission=p) for p in permissions],
                )
                session.add(custom)
                await session.flush()
                keys.append(custom.key)
            found = {
                role.key: role
                for role in (await session.scalars(select(Role).where(Role.key.in_(keys)))).all()
            }
            user = AppUser(
                username=username,
                display_name=username.title(),
                email=f"{username}@example.test",
                auth_provider="local",
                password_hash=hash_password(password),
                active=active,
                must_change_password=must_change_password,
                user_roles=[
                    UserRole(
                        role_id=found[key].id,
                        teams=[UserRoleTeam(team_id=t) for t in team_ids],
                    )
                    for key in keys
                ],
            )
            session.add(user)
            await session.commit()
            return user

    async def sign_in(self, username: str, password: str = USER_PASSWORD) -> Caller:
        http = self.http()
        response = await http.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return Caller(http, response.json())

    async def as_role(
        self,
        *roles: str,
        teams: Iterable[uuid.UUID] = (),
        permissions: Iterable[str] | None = None,
    ) -> Caller:
        name = f"user-{uuid.uuid4().hex[:8]}"
        await self.add_user(name, *roles, teams=teams, permissions=permissions)
        return await self.sign_in(name)

    async def admin(self) -> Caller:
        return await self.as_role("admin")

    # --- rows the API of this milestone cannot create yet -------------------------------

    async def add_assignment(
        self,
        member_id: str,
        skill_id: str,
        *,
        task_key: str = "T-1",
        week: date = FIRST_WEEK,
        person_days: str = "1.0",
    ) -> uuid.UUID:
        async with self.factory() as session:
            task = (
                await session.scalars(select(Task).where(Task.key == task_key))
            ).one_or_none() or Task(key=task_key, title=f"Task {task_key}")
            session.add(task)
            await session.flush()
            row = Assignment(
                task_id=task.id,
                skill_id=uuid.UUID(skill_id),
                member_id=uuid.UUID(member_id),
                week_start=week,
                person_days=Decimal(person_days),
            )
            session.add(row)
            await session.commit()
            return row.id

    async def add_absence(
        self, absence_type_id: str, member_id: str | None, day: date = date(2030, 1, 8)
    ) -> uuid.UUID:
        async with self.factory() as session:
            row = Absence(
                member_id=uuid.UUID(member_id) if member_id else None,
                absence_type_id=uuid.UUID(absence_type_id),
                start_date=day,
                end_date=day,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def count(self, model: type[Base]) -> int:
        async with self.factory() as session:
            return len((await session.scalars(select(model))).all())

    async def count_assignments(self) -> int:
        return await self.count(Assignment)

    @staticmethod
    def week(offset: int = 0) -> date:
        """The Monday ``offset`` weeks after the fixed Monday the helpers book into."""
        return FIRST_WEEK + timedelta(weeks=offset)

    async def close(self) -> None:
        for client in self._clients:
            await client.aclose()


async def absence_type_id(api: Api, key: str = "vacation") -> str:
    async with api.factory() as session:
        row = (await session.scalars(select(AbsenceType).where(AbsenceType.key == key))).one()
        return str(row.id)
