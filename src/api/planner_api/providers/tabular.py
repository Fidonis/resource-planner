"""Reading the delimited files of the file providers, always inside the import directory."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from planner_api import csvio
from planner_api.config import Settings
from planner_api.errors import ProblemError
from planner_api.providers.base import ProviderError, TestResult

_NAME = re.compile(r"[\s_\-]+")


def normalise(header: str) -> str:
    return _NAME.sub("", header.lstrip("﻿")).lower()


@dataclass(frozen=True, slots=True)
class Row:
    line: int
    cells: dict[str, str]

    def get(self, *names: str) -> str:
        for name in names:
            value = self.cells.get(name, "")
            if value:
                return value
        return ""


def resolve(settings: Settings, relative: str) -> Path:
    """The file below ``IMPORT_DIR``; a path that leaves the directory is refused."""
    base = settings.import_dir.resolve()
    if not relative or Path(relative).is_absolute():
        raise ProviderError("The path must be relative to the import directory.")
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise ProviderError("The path leaves the import directory.")
    if not target.is_file():
        raise ProviderError(f"There is no file '{relative}' in the import directory.")
    return target


def read_rows(settings: Settings, relative: str, required: tuple[str, ...]) -> list[Row]:
    """The data rows of a file with a header; ``required`` are normalised column names."""
    path = resolve(settings, relative)
    try:
        table = csvio.read_table(csvio.decode(path.read_bytes()))
    except ProblemError as exc:
        raise ProviderError(exc.detail) from exc
    except OSError as exc:
        raise ProviderError(f"The file cannot be read: {exc.strerror or exc}.") from exc
    if not table:
        raise ProviderError("The file is empty.")
    header = [normalise(h) for h in table[0][1]]
    missing = [name for name in required if name not in header]
    if missing:
        raise ProviderError(f"The file has no column {', '.join(missing)}.")
    return [Row(line, dict(zip(header, cells, strict=False))) for line, cells in table[1:]]


def check_file(settings: Settings, relative: str, required: tuple[str, ...]) -> TestResult:
    try:
        rows = read_rows(settings, relative, required)
    except ProviderError as exc:
        return TestResult(False, str(exc))
    return TestResult(True, f"The file can be read ({len(rows)} rows).", {"rows": len(rows)})


def split_list(text: str) -> tuple[str, ...]:
    """``a|b, c`` becomes ``("a", "b", "c")``."""
    return tuple(part.strip() for part in re.split(r"[|,]", text) if part.strip())


def split_pipe(text: str) -> tuple[str, ...]:
    """``Backend:2,5|Frontend:1`` becomes two entries: a comma may be a decimal comma."""
    return tuple(part.strip() for part in text.split("|") if part.strip())


def guarded[T](reference: str, read: Callable[[], T]) -> T | str:
    """The value, or the message of the cell error (for a :class:`SourceProblem`)."""
    try:
        return read()
    except csvio.CellError as exc:
        return f"{reference}: {exc}"
