"""Tabler outline icons, read once from ``planner_ui/icons`` and drawn inline.

Inline SVG takes the text colour (``currentColor``), needs no request and no CSP exception.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from markupsafe import Markup, escape

ICON_DIR = Path(__file__).resolve().parent / "icons"
_BODY = re.compile(r"<svg[^>]*>(.*)</svg>", re.DOTALL)


@cache
def _inner(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        raise KeyError(f"unknown icon '{name}'")
    match = _BODY.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"icon '{name}' is not an SVG")
    # The first path of every Tabler icon is an invisible bounding box; it only adds bytes.
    return re.sub(
        r'<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*/>', "", match.group(1)
    ).strip()


def icon(name: str, css: str = "h-4 w-4 shrink-0") -> Markup:
    return Markup(  # noqa: S704 - the markup comes from our own files, the class from our templates
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'class="{escape(css)}" aria-hidden="true">{_inner(name)}</svg>'
    )


def available() -> list[str]:
    return sorted(path.stem for path in ICON_DIR.glob("*.svg"))
