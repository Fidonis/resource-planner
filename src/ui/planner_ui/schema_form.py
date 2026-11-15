"""Forms for the settings of a provider, drawn from the JSON Schema the API publishes for it.

A provider describes its settings and its mapping as a JSON Schema, so the interface needs no code
for a provider it has never seen. Every input is a plain text, a number, a choice, a checkbox, one
value per line or ``key=value`` per line. A secret (``writeOnly``) is never shown: leaving its
input empty keeps the stored value.

Two markers of the schema add behaviour: ``x-remoteOptions`` on a list of strings draws check
boxes whose choices are loaded from the source, and ``x-showWhen`` (``{"field": ..., "equals":
...}``) shows a setting only while another one has that value.

The texts of a schema come from the provider and are shown as given. The interface translates the
ones of the providers that ship with it; any other text appears as the provider wrote it.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from starlette.datastructures import FormData

from planner_ui.i18n import N_, gettext_

Kind = Literal[
    "text",
    "secret",
    "integer",
    "decimal",
    "checkbox",
    "select",
    "lines",
    "pairs",
    "json",
    "options",
]
REMOTE_OPTIONS = "x-remoteOptions"
SHOW_WHEN = "x-showWhen"

NOT_A_WHOLE_NUMBER = N_("Not a whole number.")
NOT_A_NUMBER = N_("Not a number.")
NOT_PAIRS = N_("One entry per line, written as name=value.")
NOT_JSON = N_("Not valid JSON.")


@dataclass
class SchemaField:
    """One input of the form."""

    key: str  # name of the setting, as the API knows it (camelCase)
    name: str  # name of the input in the form
    label: str
    kind: Kind
    required: bool = False
    help: str | None = None
    value: Any = ""  # what the input shows: a string, or a bool for a checkbox
    options: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    maxlength: int | None = None
    secret_set: bool = False
    error: str | None = None
    # (name of the controlling input, value) while which the field is shown
    show_when: tuple[str, str] | None = None
    # Choices of an "options" field: (value, label); before loading, the selected values only.
    choices: list[tuple[str, str]] = field(default_factory=list)
    options_url: str | None = None  # where the interface loads the choices from the source
    load_on_open: bool = False  # load them as soon as the form opens

    @property
    def id(self) -> str:
        return "f-" + self.name.replace(".", "-")

    @property
    def rows(self) -> int:
        text = str(self.value)
        return max(3, min(8, text.count("\n") + 2))


def _plain(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> dict[str, Any]:
    """The schema of a setting without references and without the "or null" alternative."""
    current = dict(schema)
    for _ in range(4):
        if "$ref" in current:
            target = defs.get(str(current["$ref"]).rsplit("/", 1)[-1], {})
            current = {**target, **{k: v for k, v in current.items() if k != "$ref"}}
        elif "anyOf" in current:
            variants = [v for v in current["anyOf"] if v.get("type") != "null"]
            outer = {k: v for k, v in current.items() if k != "anyOf"}
            current = {**(variants[0] if variants else {}), **outer}
        else:
            break
    return current


def kind_of(plain: Mapping[str, Any]) -> Kind:
    if plain.get("writeOnly"):
        return "secret"
    if "enum" in plain:
        return "select"
    kind = plain.get("type")
    if kind == "array" and plain.get(REMOTE_OPTIONS):
        return "options"
    if kind == "boolean":
        return "checkbox"
    if kind == "integer":
        return "integer"
    if kind == "number":
        return "decimal"
    if kind == "array" and plain.get("items", {}).get("type") == "string":
        return "lines"
    if kind == "object" and isinstance(plain.get("additionalProperties"), dict):
        return "pairs"
    if kind == "string":
        return "text"
    return "json"


def defaults_of(schema: Mapping[str, Any]) -> dict[str, Any]:
    """What a setting has when nobody gives it a value."""
    return {
        key: spec["default"]
        for key, spec in schema.get("properties", {}).items()
        if spec.get("default") is not None
    }


def _show(kind: Kind, value: Any) -> Any:
    if kind == "checkbox":
        return bool(value)
    if value is None or kind == "secret":
        return ""
    if kind == "options":
        return [str(item) for item in value]
    if kind == "lines":
        return "\n".join(str(item) for item in value)
    if kind == "pairs":
        return "\n".join(f"{key}={item}" for key, item in value.items())
    if kind == "json":
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def shown_values(schema: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The values of the stored settings, as the inputs show them."""
    defs = schema.get("$defs", {})
    merged = {**defaults_of(schema), **values}
    return {
        key: _show(kind_of(_plain(spec, defs)), merged.get(key))
        for key, spec in schema.get("properties", {}).items()
    }


def shown_from_form(schema: Mapping[str, Any], form: FormData, prefix: str) -> dict[str, Any]:
    """What the user typed, untouched, so that a form with a mistake comes back as it was sent."""
    defs = schema.get("$defs", {})
    shown: dict[str, Any] = {}
    for key, spec in schema.get("properties", {}).items():
        kind = kind_of(_plain(spec, defs))
        name = f"{prefix}.{key}"
        if kind == "checkbox":
            shown[key] = name in form
        elif kind == "options":
            shown[key] = [str(item) for item in form.getlist(name) if str(item)]
        else:
            shown[key] = str(form.get(name) or "")
    return shown


def build_fields(
    schema: Mapping[str, Any],
    shown: Mapping[str, Any],
    prefix: str,
    *,
    secrets_set: Collection[str] = (),
    errors: Mapping[str, str] | None = None,
) -> list[SchemaField]:
    """The inputs for the settings of ``schema``, in the order the provider declares them."""
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    found: list[SchemaField] = []
    for key, spec in schema.get("properties", {}).items():
        plain = _plain(spec, defs)
        kind = kind_of(plain)
        name = f"{prefix}.{key}"
        title = str(plain.get("title") or key)
        help_text = plain.get("description")
        when = plain.get(SHOW_WHEN)
        show_when = (
            (f"{prefix}.{when['field']}", str(when.get("equals", "")))
            if isinstance(when, Mapping) and when.get("field")
            else None
        )
        value = shown.get(key, False if kind == "checkbox" else [] if kind == "options" else "")
        found.append(
            SchemaField(
                key=key,
                name=name,
                label=gettext_(title),
                kind=kind,
                # A field that may be hidden must not keep the browser from sending the form.
                required=key in required and show_when is None,
                help=gettext_(str(help_text)) if help_text else None,
                value=value,
                show_when=show_when,
                choices=[(str(v), str(v)) for v in value] if kind == "options" else [],
                options=[str(v) for v in plain.get("enum", [])],
                minimum=plain.get("minimum"),
                maximum=plain.get("maximum"),
                maxlength=plain.get("maxLength"),
                secret_set=key in secrets_set,
                error=(errors or {}).get(name),
            )
        )
    return found


def parse(
    schema: Mapping[str, Any], shown: Mapping[str, Any], prefix: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """The settings the inputs stand for, and the problems found on the way (by input name).

    An empty input is left out, so the setting falls back to its default; an empty secret is left
    out too, which keeps the stored one.
    """
    defs = schema.get("$defs", {})
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, spec in schema.get("properties", {}).items():
        kind = kind_of(_plain(spec, defs))
        raw = shown.get(key, "")
        name = f"{prefix}.{key}"
        if kind == "checkbox":
            values[key] = bool(raw)
            continue
        if kind == "options":
            values[key] = [str(item) for item in raw or [] if str(item)]
            continue
        text = str(raw)
        if kind == "secret":
            if text:
                values[key] = text
            continue
        if not text.strip():
            continue
        try:
            values[key] = _value(kind, text)
        except _InvalidError as problem:
            errors[name] = gettext_(problem.message)
    return values, errors


class _InvalidError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _value(kind: Kind, text: str) -> Any:
    if kind == "integer":
        try:
            return int(text.strip())
        except ValueError as exc:
            raise _InvalidError(NOT_A_WHOLE_NUMBER) from exc
    if kind == "decimal":
        try:
            return float(text.strip().replace(",", "."))
        except ValueError as exc:
            raise _InvalidError(NOT_A_NUMBER) from exc
    if kind == "lines":
        return [line.strip() for line in text.splitlines() if line.strip()]
    if kind == "pairs":
        pairs: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.strip():
                raise _InvalidError(NOT_PAIRS)
            pairs[key.strip()] = value.strip()
        return pairs
    if kind == "json":
        try:
            return json.loads(text)
        except ValueError as exc:
            raise _InvalidError(NOT_JSON) from exc
    return text.strip()
