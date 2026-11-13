"""The published OpenAPI document is complete, stable and matches the committed snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.api.support import Api

from planner_api.config import Settings
from planner_api.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[2] / "src" / "contracts" / "openapi.json"


def document() -> dict[str, Any]:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@localhost/x"))
    spec: dict[str, Any] = app.openapi()
    return spec


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_document_is_deterministic() -> None:
    assert render(document()) == render(document())


def test_the_committed_snapshot_is_current() -> None:
    assert SNAPSHOT.exists(), "run: uv run planner-api export-openapi src/contracts/openapi.json"
    assert SNAPSHOT.read_text(encoding="utf-8") == render(document()), (
        "src/contracts/openapi.json is out of date; run "
        "`uv run planner-api export-openapi src/contracts/openapi.json` and regenerate the client"
    )


def test_error_responses_use_the_problem_schema() -> None:
    spec = document()
    problem = spec["components"]["schemas"]["Problem"]
    assert {"type", "title", "status", "detail", "code", "errors"} <= set(problem["properties"])
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            for status, response in operation["responses"].items():
                if status.startswith("4") and "content" in response:
                    assert "application/problem+json" in response["content"] or (
                        "application/json" in response["content"]
                    ), f"{method} {path} {status}"


def test_person_days_are_numbers_in_steps_of_a_hundredth() -> None:
    spec = document()
    skill_conflict = spec["components"]["schemas"]["SkillConflict"]["properties"]["personDays"]
    assert skill_conflict["type"] == "number"
    assert skill_conflict["multipleOf"] == 0.01


def test_security_schemes_cover_session_and_bearer() -> None:
    spec = document()
    assert set(spec["components"]["securitySchemes"]) == {"sessionCookie", "bearerAuth"}
    login = spec["paths"]["/api/v1/auth/login"]["post"]
    assert login["security"] == []
    teams = spec["paths"]["/api/v1/teams"]["get"]
    assert teams["security"] == [{"sessionCookie": []}, {"bearerAuth": []}]


async def test_the_served_document_equals_the_generated_one(api: Api) -> None:
    served = (await api.anonymous().get("/openapi.json")).json()
    assert served == document()
