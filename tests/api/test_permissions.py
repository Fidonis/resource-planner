"""Every route declares its permission, and that permission is what the route checks (§8)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from tests.api.support import Api, Caller

from planner_api.auth.permissions import ALL_PERMISSIONS, BUILTIN_ROLES
from planner_api.auth.ratelimit import RateLimiter

NO_PERMISSION_NEEDED = {"public", "authenticated"}
METHODS = ("get", "post", "put", "patch", "delete")


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    ]


async def spec_of(api: Api) -> dict[str, Any]:
    response = await api.anonymous().get("/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    return spec


def filled(path: str) -> str:
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


async def call(caller: Caller, method: str, path: str) -> int:
    body: dict[str, Any] | None = {} if method in {"POST", "PUT", "PATCH"} else None
    response = await caller.send(method, filled(path), json=body)
    return response.status_code


async def test_every_operation_declares_what_it_needs(api: Api) -> None:
    spec = await spec_of(api)
    missing = [
        f"{method} {path}" for method, path, op in operations(spec) if "x-permission" not in op
    ]
    assert missing == [], f"routes without x-permission: {missing}"
    known = {*ALL_PERMISSIONS, *NO_PERMISSION_NEEDED}
    unknown = {op["x-permission"] for _, _, op in operations(spec)} - known
    assert unknown == set(), f"unknown permissions: {unknown}"


async def test_operation_ids_are_unique_and_documented(api: Api) -> None:
    spec = await spec_of(api)
    ids = [op["operationId"] for _, _, op in operations(spec)]
    assert len(ids) == len(set(ids)), "duplicate operationId"
    for method, path, op in operations(spec):
        assert op.get("summary"), f"{method} {path} has no summary"
        assert op["operationId"].replace("_", "").isalpha(), op["operationId"]
        assert op["operationId"] == op["operationId"].lower()


async def test_unauthenticated_calls_are_401_except_for_public_operations(api: Api) -> None:
    spec = await spec_of(api)
    anonymous = api.anonymous()
    for method, path, op in operations(spec):
        if op["x-permission"] == "public":
            continue
        status = await call(anonymous, method, path)
        assert status == 401, f"{method} {path} answered {status} without credentials"


async def test_a_caller_without_the_permission_gets_403_before_validation(api: Api) -> None:
    """The check runs before the body is read, so a refused caller learns nothing about it."""
    spec = await spec_of(api)
    nobody = await api.as_role(permissions=[])
    for method, path, op in operations(spec):
        needed = op["x-permission"]
        if needed in NO_PERMISSION_NEEDED:
            continue
        caller = nobody
        response = await caller.send(
            method, filled(path), json={} if method in {"POST", "PUT", "PATCH"} else None
        )
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        problem = response.json()
        assert problem["code"] == "PERMISSION_DENIED"
        assert problem["permission"] == needed, f"{method} {path}"


async def test_exactly_the_declared_permission_is_enough(api: Api) -> None:
    """Catches a route that checks a different permission than the one it documents."""
    spec = await spec_of(api)
    by_permission: dict[str, list[tuple[str, str]]] = {}
    for method, path, op in operations(spec):
        if op["x-permission"] not in NO_PERMISSION_NEEDED:
            by_permission.setdefault(op["x-permission"], []).append((method, path))
    # one sign-in per permission is more than the limiter of a real deployment allows
    api.app.state.login_limiter = RateLimiter(limit=1000, window_seconds=300)
    for permission, routes in by_permission.items():
        caller = await api.as_role(permissions=[permission])
        for method, path in routes:
            status = await call(caller, method, path)
            assert status != 403, f"{method} {path} refused a caller holding {permission}"


async def test_built_in_roles_hold_the_permissions_the_spec_lists(api: Api) -> None:
    by_key = {role.key: set(role.permissions) for role in BUILTIN_ROLES}
    assert by_key["admin"] == set(ALL_PERMISSIONS)
    assert "audit:read" not in by_key["viewer"], "viewers must not read the audit log"
    assert not {p for p in by_key["viewer"] if p.endswith(":write")}
    assert by_key["member"] == by_key["viewer"] | {"absence:write:own"}
    assert {"assignment:write", "task:write", "absence:write"} <= by_key["planner"]
    assert "member:write" not in by_key["planner"]
    assert {"team:write", "skill:write", "member:write", "absence-type:write"} <= by_key[
        "master-data-admin"
    ]


@pytest.mark.parametrize("role", [r.key for r in BUILTIN_ROLES])
async def test_each_built_in_role_signs_in_with_its_permissions(api: Api, role: str) -> None:
    caller = await api.as_role(role)
    assert caller.me is not None
    expected = next(set(r.permissions) for r in BUILTIN_ROLES if r.key == role)
    assert set(caller.me["permissions"]) == expected
