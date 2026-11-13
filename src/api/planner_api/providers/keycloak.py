"""Directory provider for Keycloak, through the Admin REST API (FR-DIR-01 to 05).

The planner needs a client with a service account (client credentials) that may ``view-users``
and ``query-groups`` of the realm; to read roles it also needs ``view-realm``. Users that are
service accounts of clients are left out, they are no people.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from planner_api.config import Settings
from planner_api.providers.base import (
    KIND_DIRECTORY,
    ConfigModel,
    ExternalGroup,
    ExternalPerson,
    ProviderBase,
    ProviderError,
    SourceProblem,
    TestResult,
)

log = logging.getLogger("planner_api.providers.keycloak")
TIMEOUT = httpx.Timeout(20.0, connect=8.0)
SERVICE_ACCOUNT_PREFIX = "service-account-"


class KeycloakConfig(ConfigModel):
    base_url: str = Field(
        title="Keycloak URL",
        description="Address of Keycloak as the API reaches it, for example http://keycloak:8080 "
        "(without /realms/...).",
        min_length=1,
        max_length=300,
        pattern=r"^https?://",
    )
    realm: str = Field(title="Realm", min_length=1, max_length=120)
    client_id: str = Field(
        title="Client ID",
        description="Client with a service account that may view users and query groups.",
        min_length=1,
        max_length=200,
    )
    client_secret: str = Field(
        title="Client secret",
        min_length=1,
        max_length=500,
        json_schema_extra={"writeOnly": True},
    )
    verify_tls: bool = Field(
        default=True, title="Verify the certificate", description="Switch off only for tests."
    )
    roles: list[str] = Field(
        default_factory=list,
        title="Realm roles to read",
        description="One per line. Only the holders of these roles are told apart by role, for "
        "the mapping's 'include roles'. Needs the right view-realm.",
    )
    page_size: int = Field(default=100, ge=10, le=500, title="Page size")


class KeycloakDirectory(ProviderBase):
    key: ClassVar[str] = "keycloak"
    kind: ClassVar[str] = KIND_DIRECTORY
    title: ClassVar[str] = "Keycloak"
    config_model: ClassVar[type[BaseModel]] = KeycloakConfig
    # Tests put a mock transport here.
    transport: ClassVar[httpx.AsyncBaseTransport | None] = None

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- the HTTP side ---------------------------------------------------------------------

    @asynccontextmanager
    async def _session(self, config: KeycloakConfig) -> AsyncIterator[httpx.AsyncClient]:
        base = config.base_url.rstrip("/")
        async with httpx.AsyncClient(
            timeout=TIMEOUT, verify=config.verify_tls, transport=self.transport
        ) as client:
            try:
                answer = await client.post(
                    f"{base}/realms/{config.realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": config.client_id,
                        "client_secret": config.client_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise ProviderError(f"Keycloak cannot be reached ({type(exc).__name__}).") from exc
            if answer.status_code in (400, 401):
                raise ProviderError("Keycloak refused the client ID or the client secret.")
            if answer.status_code == 404:
                raise ProviderError(f"Keycloak has no realm '{config.realm}'.")
            if answer.status_code != 200:
                raise ProviderError(f"Keycloak answered {answer.status_code} to the sign-in.")
            token = answer.json().get("access_token")
            if not token:
                raise ProviderError("Keycloak sent no access token.")
            client.headers["Authorization"] = f"Bearer {token}"
            client.base_url = httpx.URL(f"{base}/admin/realms/{config.realm}/")
            yield client

    async def _get(self, client: httpx.AsyncClient, path: str, **params: Any) -> Any:
        try:
            answer = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Keycloak cannot be reached ({type(exc).__name__}).") from exc
        if answer.status_code == 403:
            raise ProviderError(
                f"The service account may not read '{path}': give it view-users, query-groups "
                "(and view-realm for roles)."
            )
        if answer.status_code != 200:
            raise ProviderError(f"Keycloak answered {answer.status_code} for '{path}'.")
        return answer.json()

    async def _pages(
        self, client: httpx.AsyncClient, path: str, size: int, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        first = 0
        while True:
            rows = await self._get(client, path, first=first, max=size, **params)
            for row in rows:
                yield row
            if len(rows) < size:
                return
            first += size

    async def _groups(self, client: httpx.AsyncClient, size: int) -> AsyncIterator[tuple[str, str]]:
        """``(id, path)`` of every group, subgroups included."""
        pending: list[dict[str, Any]] = [g async for g in self._pages(client, "groups", size)]
        while pending:
            group = pending.pop(0)
            yield group["id"], group.get("path") or "/" + group["name"]
            if group.get("subGroupCount") or group.get("subGroups"):
                pending.extend(
                    [g async for g in self._pages(client, f"groups/{group['id']}/children", size)]
                )

    # --- the provider interface ------------------------------------------------------------

    async def test_connection(self, config: BaseModel) -> TestResult:
        assert isinstance(config, KeycloakConfig)
        try:
            async with self._session(config) as client:
                count = await self._get(client, "users/count")
        except ProviderError as exc:
            return TestResult(False, str(exc))
        return TestResult(
            True, f"Connected to the realm '{config.realm}' ({count} users).", {"users": count}
        )

    async def fetch_groups(self, config: BaseModel) -> AsyncIterator[ExternalGroup]:
        assert isinstance(config, KeycloakConfig)
        async with self._session(config) as client:
            async for group_id, path in self._groups(client, config.page_size):
                yield ExternalGroup(group_id, path)

    async def fetch_members(
        self, config: BaseModel
    ) -> AsyncIterator[ExternalPerson | SourceProblem]:
        assert isinstance(config, KeycloakConfig)
        async with self._session(config) as client:
            size = config.page_size
            groups_of: dict[str, set[str]] = {}
            async for group_id, path in self._groups(client, size):
                async for member in self._pages(client, f"groups/{group_id}/members", size):
                    groups_of.setdefault(member["id"], set()).add(path)
            roles_of: dict[str, set[str]] = {}
            for role in config.roles:
                async for holder in self._pages(client, f"roles/{role}/users", size):
                    roles_of.setdefault(holder["id"], set()).add(role)
            async for user in self._pages(client, "users", size, briefRepresentation="true"):
                username = str(user.get("username") or "")
                if not username or username.startswith(SERVICE_ACCOUNT_PREFIX):
                    continue
                name = f"{user.get('firstName') or ''} {user.get('lastName') or ''}".strip()
                yield ExternalPerson(
                    external_id=user["id"],
                    username=username,
                    display_name=name or username,
                    email=user.get("email") or None,
                    active=bool(user.get("enabled", True)),
                    groups=tuple(sorted(groups_of.get(user["id"], ()))),
                    roles=tuple(sorted(roles_of.get(user["id"], ()))),
                )
