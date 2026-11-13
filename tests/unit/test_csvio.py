"""Reading and writing the CSV files of the imports and exports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from planner_api import csvio
from planner_api.errors import ProblemError


def test_the_delimiter_is_the_one_the_header_uses_most() -> None:
    assert csvio.read_table("a;b;c\n1;2;3")[1][1] == ["1", "2", "3"]
    assert csvio.read_table("a,b,c\n1,2,3")[1][1] == ["1", "2", "3"]
    assert csvio.read_table("a\tb\tc\n1\t2\t3")[1][1] == ["1", "2", "3"]
    assert csvio.read_table("a;b,c;d\n1;2,5;3")[1][1] == ["1", "2,5", "3"]
    assert csvio.read_table("single\nvalue") == [(1, ["single"]), (2, ["value"])]


def test_rows_keep_their_line_number_and_blank_lines_are_left_out() -> None:
    rows = csvio.read_table("a;b\n\n  ;  \n1;2\n")
    assert [(line, cells) for line, cells in rows] == [(1, ["a", "b"]), (4, ["1", "2"])]


def test_quoted_cells_may_hold_the_delimiter_and_line_breaks() -> None:
    rows = csvio.read_table('a;b\n"x;y";"two\nlines"\n')
    assert rows[1][1] == ["x;y", "two\nlines"]
    assert rows[1][0] == 3, "the number is the line the row ends on"


def test_a_header_is_dropped_by_the_name_of_its_first_column() -> None:
    rows = csvio.read_table("Key;Title\nA;b")
    assert csvio.drop_header(rows, "key") == [(2, ["A", "b"])]
    assert csvio.drop_header(rows, "name") == rows
    assert csvio.drop_header([], "key") == []
    bom = csvio.read_table("﻿Key;Title\nA;b")
    assert csvio.drop_header(bom, "key") == [(2, ["A", "b"])]


def test_a_file_is_decoded_as_utf8_with_or_without_a_bom() -> None:
    assert csvio.decode("Größe".encode()) == "Größe"
    assert csvio.decode("﻿a".encode()) == "a"
    with pytest.raises(ProblemError) as bad:
        csvio.decode("Größe".encode("latin-1"))
    assert bad.value.status == 422
    with pytest.raises(ProblemError) as big:
        csvio.decode(b"x" * (csvio.MAX_BYTES + 1))
    assert big.value.status == 413


def test_a_file_with_too_many_rows_is_refused() -> None:
    with pytest.raises(ProblemError) as many:
        csvio.read_table("a\n" * (csvio.MAX_ROWS + 2))
    assert many.value.status == 422


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1", "1"), ("1.5", "1.5"), ("1,5", "1.5"), (" 2 ", "2"), ("0.25", "0.25"), ("-1", "-1")],
)
def test_decimals_may_use_a_point_or_a_comma(text: str, expected: str) -> None:
    assert csvio.parse_decimal(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "1.234", "1,234.5", "1.234,5", "NaN", "inf"])
def test_other_numbers_are_cell_errors(text: str) -> None:
    with pytest.raises(csvio.CellError):
        csvio.parse_decimal(text)


def test_dates_are_german_or_iso() -> None:
    assert csvio.parse_date("07.01.2030") == date(2030, 1, 7)
    assert csvio.parse_date("7.1.2030") == date(2030, 1, 7)
    assert csvio.parse_date("2030-01-07") == date(2030, 1, 7)
    assert csvio.parse_date(" 2030-01-07 ") == date(2030, 1, 7)


@pytest.mark.parametrize(
    "text", ["", "31.02.2030", "2030-13-01", "01/07/2030", "tomorrow", "2030-1"]
)
def test_other_dates_are_cell_errors(text: str) -> None:
    with pytest.raises(csvio.CellError):
        csvio.parse_date(text)


def test_cells_beyond_the_row_are_empty() -> None:
    assert csvio.cell(["a", " b "], 1) == "b"
    assert csvio.cell(["a"], 3) == ""


def test_person_days_are_written_without_trailing_zeros() -> None:
    assert csvio.plain(Decimal("1.50")) == "1.5"
    assert csvio.plain(Decimal("2.00")) == "2"
    assert csvio.plain(Decimal("0.25")) == "0.25"
    assert csvio.plain(Decimal("10")) == "10"
    assert csvio.plain(Decimal("100.00")) == "100"


def test_a_cell_that_a_spreadsheet_would_run_as_a_formula_is_defused() -> None:
    assert csvio.safe_cell("=1+1") == "'=1+1"
    assert csvio.safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csvio.safe_cell("+49 170") == "'+49 170"
    assert csvio.safe_cell("-cmd") == "'-cmd"
    assert csvio.safe_cell("\tx") == "'\tx"
    assert csvio.safe_cell("-1.5") == "-1.5", "a negative number is not a formula"
    assert csvio.safe_cell("plain") == "plain"
    assert csvio.safe_cell(None) == ""
    assert csvio.safe_cell(3) == "3"


def test_a_table_is_written_with_semicolons_and_crlf() -> None:
    text = csvio.write_table(("A", "B"), [("x", "=y"), ("p;q", 1)])
    assert text == 'A;B\r\nx;\'=y\r\n"p;q";1\r\n'
    assert csvio.read_table(text)[2][1] == ["p;q", "1"]
