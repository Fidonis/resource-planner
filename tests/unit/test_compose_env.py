"""Every variable of `.env.example` reaches a container; one that does not would change nothing."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.yml", "docker/docker-compose.dev.yml")


def test_every_example_variable_is_used_by_a_compose_file() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE))
    compose = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in COMPOSE_FILES)
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))
    assert sorted(names - used) == []
