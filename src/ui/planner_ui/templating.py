"""The Jinja environment and the helpers that turn a context into a response."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from babel.dates import format_date, format_datetime
from babel.numbers import format_decimal
from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from planner_ui import i18n, nav
from planner_ui.icons import icon
from planner_ui.session import UiContext

PACKAGE = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE / "templates"
STATIC_DIR = PACKAGE / "static"


@cache
def _fingerprint(path: str) -> str:
    target = STATIC_DIR / path
    if not target.is_file():
        return "0"
    return hashlib.sha256(target.read_bytes()).hexdigest()[:10]


def asset_url(path: str) -> str:
    return f"/static/{path}?v={_fingerprint(path)}"


def _locale() -> str:
    return i18n.current_locale()


def fmt_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    day = value.date() if isinstance(value, datetime) else value
    return format_date(day, format="medium", locale=_locale())


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return format_datetime(value, format="medium", locale=_locale())


def fmt_number(value: int | float | Decimal | None, places: int = 2) -> str:
    if value is None:
        return ""
    pattern = "#,##0" if places == 0 else "#,##0." + "0" * places
    return format_decimal(value, format=pattern, locale=_locale())


def fmt_daymonth(value: date | None) -> str:
    return "" if value is None else format_date(value, format="d MMM", locale=_locale())


def fmt_weekday(value: date | None) -> str:
    return "" if value is None else format_date(value, format="EEE", locale=_locale())


def fmt_days(value: int | float | Decimal | None) -> str:
    """Person-days without trailing zeros: 2 stays 2, 2.5 stays 2.5, 2.25 stays 2.25."""
    if value is None:
        return ""
    return format_decimal(value, format="#,##0.##", locale=_locale())


def build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=["jinja2.ext.i18n"],
    )
    env.policies["ext.i18n.trimmed"] = True
    env.install_gettext_callables(  # type: ignore[attr-defined]
        i18n.gettext_,
        i18n.ngettext,
        newstyle=True,
        pgettext=i18n.pgettext,
        npgettext=i18n.npgettext,
    )
    env.globals.update(
        icon=icon,
        asset_url=asset_url,
        html_lang=i18n.html_lang,
        current_locale=i18n.current_locale,
        available_locales=lambda: i18n.get_catalogs().locales,
        locale_name=i18n.locale_name,
        nav_active=nav.is_active,
    )
    env.filters.update(
        date=fmt_date,
        datetime=fmt_datetime,
        number=fmt_number,
        days=fmt_days,
        daymonth=fmt_daymonth,
        weekday=fmt_weekday,
        tojson_attr=lambda value: json.dumps(value, ensure_ascii=True),
    )
    return env


def render(
    request: Request,
    name: str,
    ctx: UiContext | None = None,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    **context: Any,
) -> HTMLResponse:
    env: Environment = request.app.state.templates
    base: dict[str, Any] = {"request": request, "ctx": ctx, "path": request.url.path}
    if ctx is not None:
        base.update(me=ctx.me, csrf_token=ctx.csrf_token, can=ctx.can, nav_groups=nav.visible(ctx))
    base.update(context)
    body = env.get_template(name).render(base)
    return HTMLResponse(body, status_code=status, headers=dict(headers or {}))


def trigger(**events: Any) -> dict[str, str]:
    """An ``HX-Trigger`` header: events the page script reacts to (ASCII, so any codec is safe)."""
    return {"HX-Trigger": json.dumps(events, ensure_ascii=True)}


def toast(kind: str, message: str, **events: Any) -> dict[str, str]:
    return trigger(toast={"kind": kind, "message": message}, **events)
