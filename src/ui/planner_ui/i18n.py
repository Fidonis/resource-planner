"""Interface languages: one gettext catalogue per directory under ``locale/``.

A language is a directory ``locale/<code>/LC_MESSAGES/messages.po``. The ``.po`` files are read when
the process starts and compiled in memory, so there is no build step and nothing binary in the
repository. English is the language the source is written in: its message ids are the texts, it
needs no catalogue and is always offered.

The language of the request being served is a ``ContextVar`` set once per request. It is not read
from the template context: a macro imported without context cannot see the request, and a message
raised in a route has no template context at all.
"""

from __future__ import annotations

import gettext
import io
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from babel import Locale
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

logger = logging.getLogger(__name__)

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
SOURCE_LOCALE = "en"
_CODE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")


@dataclass(frozen=True, slots=True)
class LocaleInfo:
    code: str
    name: str  # the language's name in that language, for the picker


def catalog_path(code: str, locale_dir: Path = LOCALE_DIR) -> Path:
    return locale_dir / code / "LC_MESSAGES" / "messages.po"


def _display_name(code: str) -> str:
    try:
        name = Locale.parse(code).get_display_name(code) or code
    except Exception:  # an unknown code must not take the page down
        return code
    return name[:1].upper() + name[1:]


def _compile(code: str, path: Path) -> gettext.NullTranslations | None:
    try:
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=code)
        mo = io.BytesIO()
        write_mo(mo, catalog, use_fuzzy=False)  # fuzzy entries are guesses, not translations
        mo.seek(0)
        return gettext.GNUTranslations(mo)
    except Exception as exc:  # a broken catalogue must not stop the interface
        logger.warning("catalogue %s unreadable, the language is left out: %s", path, exc)
        return None


class Catalogs:
    def __init__(self, locale_dir: Path = LOCALE_DIR) -> None:
        self._source = gettext.NullTranslations()
        self._by_code: dict[str, gettext.NullTranslations] = {}
        entries = sorted(locale_dir.iterdir()) if locale_dir.is_dir() else []
        for entry in entries:
            if (
                _CODE.match(entry.name)
                and entry.name != SOURCE_LOCALE
                and catalog_path(entry.name, locale_dir).is_file()
            ):
                loaded = _compile(entry.name, catalog_path(entry.name, locale_dir))
                if loaded is not None:
                    self._by_code[entry.name] = loaded
        self.locales = [
            LocaleInfo(SOURCE_LOCALE, _display_name(SOURCE_LOCALE)),
            *(LocaleInfo(code, _display_name(code)) for code in self._by_code),
        ]

    def codes(self) -> list[str]:
        return [info.code for info in self.locales]

    def translations(self, code: str) -> gettext.NullTranslations:
        return self._by_code.get(code, self._source)

    def match(self, tag: str) -> str | None:
        """The shipped language a tag asks for; ``de-AT`` finds ``de`` without its own catalogue."""
        normalised = tag.strip().replace("-", "_")
        if not normalised:
            return None
        lowered = {code.lower(): code for code in self.codes()}
        if normalised.lower() in lowered:
            return lowered[normalised.lower()]
        return lowered.get(normalised.split("_", 1)[0].lower())

    def negotiate(self, header: str | None) -> str | None:
        """The best shipped language of an ``Accept-Language`` header, or None."""
        if not header:
            return None
        ranked: list[tuple[float, int, str]] = []
        for position, part in enumerate(header.split(",")):
            tag, _, params = part.strip().partition(";")
            weight = 1.0
            match = re.search(r"q=([0-9.]+)", params)
            if match:
                try:
                    weight = float(match.group(1))
                except ValueError:
                    weight = 0.0
            if tag and tag != "*" and weight > 0:
                ranked.append((-weight, position, tag))
        for _w, _p, tag in sorted(ranked):
            found = self.match(tag)
            if found:
                return found
        return None


_catalogs: Catalogs | None = None


def get_catalogs() -> Catalogs:
    global _catalogs
    if _catalogs is None:
        _catalogs = Catalogs()
    return _catalogs


def reload_catalogs(locale_dir: Path = LOCALE_DIR) -> Catalogs:
    global _catalogs
    _catalogs = Catalogs(locale_dir)
    return _catalogs


_locale: ContextVar[str] = ContextVar("planner_locale", default=SOURCE_LOCALE)


def current_locale() -> str:
    return _locale.get()


def set_locale(code: str) -> None:
    _locale.set(code if code in get_catalogs().codes() else SOURCE_LOCALE)


@contextmanager
def use_locale(code: str) -> Iterator[None]:
    """Run a block in a language; for code outside a request, and for tests."""
    token = _locale.set(code)
    try:
        yield
    finally:
        _locale.reset(token)


def _translations() -> gettext.NullTranslations:
    return get_catalogs().translations(current_locale())


def gettext_(message: str) -> str:
    """The message in the request's language; the message itself when there is no translation."""
    return _translations().gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translations().ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    return _translations().pgettext(context, message)


def npgettext(context: str, singular: str, plural: str, n: int) -> str:
    return _translations().npgettext(context, singular, plural, n)


def N_(message: str) -> str:  # noqa: N802 - Babel's extraction knows this spelling
    """Mark a text for extraction and return it unchanged (for tables built at import time)."""
    return message


def html_lang() -> str:
    return current_locale().replace("_", "-")


def locale_name(code: str) -> str:
    for info in get_catalogs().locales:
        if info.code == code:
            return info.name
    return code
