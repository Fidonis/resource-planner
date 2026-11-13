"""Message catalogues of the web interface.

    uv run python scripts/i18n.py extract    # rewrite src/ui/planner_ui/locale/messages.pot
    uv run python scripts/i18n.py update     # add new messages (untranslated) to every language
    uv run python scripts/i18n.py missing    # list untranslated messages; exit 1 when there are any

The texts are read from the Python sources (``_``, ``gettext_``, ``N_``, ``ngettext``) and from the
templates. English is the language of the source and needs no catalogue.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
# The dates stay out of the files, so regenerating them changes nothing that did not change.
PLACEHOLDER_DATE = "YEAR-MO-DA HO:MI+ZONE"
PACKAGE = ROOT / "src" / "ui" / "planner_ui"
LOCALE = PACKAGE / "locale"
KEYWORDS = {
    "_": None,
    "gettext": None,
    "gettext_": None,
    "N_": None,
    "ngettext": (1, 2),
    "pgettext": ((1, "c"), 2),
}
METHODS = [
    ("static/**", "ignore"),
    ("templates/**.html", "jinja2"),
    ("**.py", "python"),
]
OPTIONS = {"templates/**.html": {"extensions": "jinja2.ext.i18n", "trimmed": "true"}}


def extract() -> Catalog:
    catalog = Catalog(project="Resource Planner", charset="utf-8")
    for filename, lineno, message, comments, context in extract_from_dir(
        str(PACKAGE),
        method_map=METHODS,
        options_map=OPTIONS,
        keywords=KEYWORDS,
        comment_tags=(),
        strip_comment_tags=False,
    ):
        catalog.add(
            message, locations=[(filename, lineno)], auto_comments=comments, context=context
        )
    return catalog


def tidy(catalog: Catalog) -> None:
    """A header without the placeholders the tool would otherwise leave in a public repository."""
    catalog.header_comment = (
        f"# {catalog.locale.get_display_name('en')} translations of the Resource Planner.\n"
        "# Distributed under the licence of the project."
    )
    catalog.fuzzy = False
    catalog.project = "Resource Planner"
    catalog.version = ""
    catalog.copyright_holder = ""
    catalog.msgid_bugs_address = ""
    catalog.last_translator = "Resource Planner contributors"
    catalog.language_team = ""
    catalog.revision_date = PLACEHOLDER_DATE


def tidy_template(catalog: Catalog) -> None:
    catalog.header_comment = "# Message template of the Resource Planner web interface."
    catalog.project = "Resource Planner"
    catalog.version = ""
    catalog.copyright_holder = ""
    catalog.msgid_bugs_address = ""
    catalog.revision_date = PLACEHOLDER_DATE
    catalog.last_translator = ""
    catalog.language_team = ""
    catalog.fuzzy = False


def languages() -> list[str]:
    return sorted(p.name for p in LOCALE.iterdir() if (p / "LC_MESSAGES" / "messages.po").is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extract", "update", "missing"])
    command = parser.parse_args().command
    template = extract()
    pot = LOCALE / "messages.pot"
    if command == "extract":
        tidy_template(template)
        with pot.open("wb") as handle:
            write_po(handle, template, width=100, include_lineno=False)
        print(f"{len(list(template))} messages -> {pot}")
        return 0
    status = 0
    for code in languages():
        path = LOCALE / code / "LC_MESSAGES" / "messages.po"
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=code)
        if command == "update":
            catalog.update(template, no_fuzzy_matching=True)
            tidy(catalog)
            with path.open("wb") as handle:
                write_po(handle, catalog, width=100, include_lineno=False)
            print(f"{code}: updated")
        else:
            gaps = [m.id for m in catalog if m.id and not m.string]
            known = {m.id for m in catalog}
            gaps += [m.id for m in template if m.id and m.id not in known]
            for message in gaps:
                print(f"{code}: {message!r}")
            status = status or int(bool(gaps))
    return status


if __name__ == "__main__":
    sys.exit(main())
