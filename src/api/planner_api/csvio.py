"""Reading and writing the delimited text files of the manual import and export paths."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

from planner_api.errors import ErrorCode, ProblemError

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
DELIMITERS = ";,\t"

_GERMAN_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class CellError(ValueError):
    """A cell that cannot be read; the message is meant for the person who wrote the file."""


def decode(raw: bytes) -> str:
    if len(raw) > MAX_BYTES:
        raise ProblemError(
            413,
            ErrorCode.IMPORT_FAILED,
            f"The file is larger than {MAX_BYTES // (1024 * 1024)} MiB.",
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProblemError(
            422, ErrorCode.IMPORT_FAILED, "The file is not UTF-8 text. Save it as UTF-8."
        ) from exc


def read_table(text: str) -> list[tuple[int, list[str]]]:
    """Rows with the number of the line they end on; blank lines are left out.

    The delimiter (``;`` ``,`` or tab) is the one that occurs most often in the first line.
    """
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(DELIMITERS, key=first.count) if any(first.count(d) for d in DELIMITERS) else ";"
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: list[tuple[int, list[str]]] = []
    try:
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue
            rows.append((reader.line_num, [c.strip() for c in cells]))
            if len(rows) > MAX_ROWS:
                raise ProblemError(
                    422, ErrorCode.IMPORT_FAILED, f"The file has more than {MAX_ROWS} rows."
                )
    except csv.Error as exc:
        raise ProblemError(
            422, ErrorCode.IMPORT_FAILED, f"The file is not valid CSV: {exc}."
        ) from exc
    return rows


def drop_header(
    rows: list[tuple[int, list[str]]], first_column: str
) -> list[tuple[int, list[str]]]:
    """Remove a leading header row, recognised by the name of its first column."""
    if rows and rows[0][1] and rows[0][1][0].lower().lstrip("﻿") == first_column.lower():
        return rows[1:]
    return rows


def cell(cells: Sequence[str], index: int) -> str:
    return cells[index].strip() if index < len(cells) else ""


def parse_decimal(text: str) -> Decimal:
    """A number with a decimal point or comma and at most two decimals."""
    cleaned = text.strip().replace(" ", "")
    if "," in cleaned and "." in cleaned:
        raise CellError("Use either a decimal point or a decimal comma, without thousands marks.")
    cleaned = cleaned.replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CellError(f"'{text}' is not a number.") from exc
    if not value.is_finite():
        raise CellError(f"'{text}' is not a number.")
    if value != value.quantize(Decimal("0.01")):
        raise CellError(f"'{text}' has more than two decimals.")
    return value


def parse_date(text: str) -> date:
    """``DD.MM.YYYY`` or ``YYYY-MM-DD``."""
    cleaned = text.strip()
    try:
        match = _GERMAN_DATE.match(cleaned)
        if match:
            day, month, year = (int(g) for g in match.groups())
            return date(year, month, day)
        match = _ISO_DATE.match(cleaned)
        if match:
            year, month, day = (int(g) for g in match.groups())
            return date(year, month, day)
    except ValueError as exc:
        raise CellError(f"'{text}' is not a date that exists.") from exc
    raise CellError(f"'{text}' is not a date. Use DD.MM.YYYY or YYYY-MM-DD.")


def plain(value: Decimal) -> str:
    """A person-day figure without trailing zeros: ``1.50`` becomes ``1.5``, ``2.00`` ``2``."""
    return format(value.normalize(), "f")


def safe_cell(value: object) -> str:
    """Text for a cell that a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@\t\r" and not _NUMBER.match(text):
        return "'" + text
    return text


def write_table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return out.getvalue()
