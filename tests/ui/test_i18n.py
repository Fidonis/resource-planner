"""Languages: nothing is left untranslated, and the choice sticks to the account."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from babel.messages.pofile import read_po
from tests.api.support import Api
from tests.ui.conftest import Browser

from planner_api.errors import ErrorCode
from planner_ui import i18n
from planner_ui.texts import PROBLEMS

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"%\(\w+\)s")


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("i18n_script", ROOT / "scripts" / "i18n.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_text_of_the_interface_is_translated() -> None:
    script = load_script()
    wanted = {m.id for m in script.extract() if m.id}
    assert len(wanted) > 100
    for code in script.languages():
        with (script.LOCALE / code / "LC_MESSAGES" / "messages.po").open("rb") as handle:
            catalog = read_po(handle, locale=code)
        have = {m.id: m.string for m in catalog if m.id}
        assert not (wanted - have.keys()), (
            f"{code}: not in the catalogue: {sorted(wanted - have.keys())}"
        )
        assert not {k for k, v in have.items() if k in wanted and not v}, f"{code}: untranslated"
        stale = have.keys() - wanted
        assert not stale, f"{code}: no longer used: {sorted(stale)}"
        for message_id, text in have.items():
            assert PLACEHOLDER.findall(str(message_id)) == PLACEHOLDER.findall(str(text or "")), (
                f"{code}: placeholders differ in {message_id!r}"
            )


def test_every_problem_code_of_the_api_has_a_text() -> None:
    known = {code.value for code in ErrorCode}
    assert known <= PROBLEMS.keys(), sorted(known - PROBLEMS.keys())
    assert set(PROBLEMS) - known <= {"UNREACHABLE"}


def test_a_catalogue_that_cannot_be_read_is_left_out(tmp_path: Path) -> None:
    broken = tmp_path / "xx" / "LC_MESSAGES"
    broken.mkdir(parents=True)
    (broken / "messages.po").write_bytes(
        b'msgid ""\nmsgstr "Content-Type: text/plain; charset=no-such-charset\\n"\n'
        b'\nmsgid "a"\nmsgstr "b"\n'
    )
    catalogs = i18n.Catalogs(tmp_path)
    assert catalogs.codes() == ["en"]


def test_a_browser_language_finds_the_shipped_one() -> None:
    catalogs = i18n.get_catalogs()
    assert catalogs.negotiate("de-AT,de;q=0.8,en;q=0.5") == "de"
    assert catalogs.negotiate("fr-FR,fr;q=0.9,en;q=0.5") == "en"
    assert catalogs.negotiate("fr-FR") is None
    assert catalogs.negotiate("*") is None
    assert catalogs.negotiate(None) is None


async def test_the_login_page_follows_the_browser(browser: Browser, make_browser: Any) -> None:
    english = await browser.page("/login")
    assert "Sign in" in english.text and '<html lang="en"' in english.text
    german = await make_browser("de-DE,de;q=0.9,en;q=0.5").page("/login")
    assert "Anmelden" in german.text and '<html lang="de"' in german.text
    assert "Benutzername" in german.text


async def test_the_language_of_the_account_is_remembered(api: Api, make_browser: Any) -> None:
    await api.add_user("ada", "viewer")
    first = make_browser()
    await first.sign_in("ada")
    assert "Teams" in (await first.page("/teams")).text
    switched = await first.http.post(
        "/account/language",
        data={"csrf_token": first.csrf, "locale": "de"},
        headers={"Referer": "http://test/teams"},
    )
    assert switched.status_code == 303 and switched.headers["location"] == "/teams"
    page = await first.page("/teams")
    assert "Stammdaten" in page.text and "Abmelden" in page.text

    # another computer, another browser language: the account decides once it is signed in
    second = make_browser("en-US")
    await second.sign_in("ada")
    assert "Stammdaten" in (await second.page("/teams")).text

    await first.http.post("/account/language", data={"csrf_token": first.csrf, "locale": "auto"})
    first.http.cookies.clear()
    third = make_browser("en-US")
    await third.sign_in("ada")
    assert "Master data" in (await third.page("/teams")).text


async def test_errors_are_shown_in_the_language_of_the_page(api: Api, make_browser: Any) -> None:
    await api.add_user("zoe", "admin")
    german = make_browser("de")
    await german.sign_in("zoe")
    await german.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})
    duplicate = await german.send(
        "POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""}
    )
    assert duplicate.status_code == 422 and "Bereits vergeben." in duplicate.text
    missing = await german.send("GET", "/teams/00000000-0000-0000-0000-000000000000/edit")
    assert missing.status_code == 404
    assert (
        "existiert nicht mehr" in missing.headers["hx-trigger"].encode().decode("unicode_escape")
        or "existiert" in missing.headers["hx-trigger"]
    )
