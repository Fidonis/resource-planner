"""Fail when a file of the repository matches a rule of a deny list.

The rules are not kept in the repository. They come from the environment: ``WORDING_RULES``
holds them as text, or ``WORDING_RULES_FILE`` names a file that holds them. Each line is a
regular expression; a line may start with ``(?i)`` to match without regard to case, and empty
lines and lines that start with ``#`` are ignored. CI passes the rules from a repository secret.

Without rules the check says so and passes: a pull request from a fork gets no secrets, and it is
checked again once it is merged. Lock files and generated assets are skipped because third-party
names are not ours to change.

Usage: python scripts/check_wording.py [ROOT]
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".import_linter_cache",
    ".build",
    "vendor",
    "test-results",
    "playwright-report",
}
SKIP_NAMES = {"uv.lock", "package-lock.json", "app.css"}
SKIP_SUFFIXES = {".woff2", ".png", ".ico", ".gz", ".zip", ".pyc"}


def load_rules() -> list[re.Pattern[str]]:
    text = os.environ.get("WORDING_RULES", "")
    rules_file = os.environ.get("WORDING_RULES_FILE", "")
    if not text.strip() and rules_file:
        text = Path(rules_file).read_text(encoding="utf-8")
    rules: list[re.Pattern[str]] = []
    for line in text.splitlines():
        pattern = line.strip()
        if pattern and not pattern.startswith("#"):
            rules.append(re.compile(pattern))
    return rules


def files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES:
            continue
        found.append(path)
    return found


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    rules = load_rules()
    if not rules:
        print("wording check skipped: no rules in WORDING_RULES or WORDING_RULES_FILE")
        return 0
    failures = 0
    for path in files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for rule in rules:
                match = rule.search(line)
                if match:
                    failures += 1
                    print(f"{path.relative_to(root)}:{number}: {match.group(0)!r}")
    if failures:
        print(f"{failures} occurrence(s) of forbidden wording")
        return 1
    print(f"wording check passed ({len(rules)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
