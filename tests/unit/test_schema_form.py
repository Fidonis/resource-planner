"""Forms drawn from the JSON Schema of a provider, and read back."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.datastructures import FormData

from planner_ui import schema_form
from planner_ui.schema_form import build_fields, kind_of, parse, shown_from_form, shown_values

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["baseUrl", "clientSecret"],
    "properties": {
        "baseUrl": {"type": "string", "title": "Keycloak URL", "maxLength": 300},
        "clientSecret": {"type": "string", "title": "Secret", "writeOnly": True},
        "verifyTls": {"type": "boolean", "default": True, "title": "Verify"},
        "pageSize": {"type": "integer", "default": 100, "minimum": 10, "maximum": 500},
        "factor": {"type": "number", "default": 1.0},
        "strategy": {"enum": ["MANUAL", "EVEN_SPLIT"], "type": "string", "default": "MANUAL"},
        "roles": {"type": "array", "items": {"type": "string"}},
        "teams": {"type": "object", "additionalProperties": {"type": "string"}},
        "defaultTeam": {"anyOf": [{"type": "string", "maxLength": 120}, {"type": "null"}]},
        "share": {"anyOf": [{"type": "integer", "minimum": 10}, {"type": "null"}]},
        "choice": {"$ref": "#/$defs/Choice"},
        "nested": {"type": "object", "properties": {"a": {"type": "string"}}},
    },
    "$defs": {"Choice": {"enum": ["A", "B"], "type": "string", "title": "Choice"}},
}


def test_the_kind_of_an_input_follows_the_type_of_the_setting() -> None:
    kinds = {
        key: kind_of(schema_form._plain(spec, SCHEMA["$defs"]))
        for key, spec in SCHEMA["properties"].items()
    }
    assert kinds == {
        "baseUrl": "text",
        "clientSecret": "secret",
        "verifyTls": "checkbox",
        "pageSize": "integer",
        "factor": "decimal",
        "strategy": "select",
        "roles": "lines",
        "teams": "pairs",
        "defaultTeam": "text",
        "share": "integer",
        "choice": "select",
        "nested": "json",
    }


def test_stored_values_are_shown_as_the_inputs_take_them() -> None:
    shown = shown_values(
        SCHEMA,
        {
            "baseUrl": "http://kc",
            "clientSecret": "must-not-appear",
            "roles": ["a", "b"],
            "teams": {"web": "Team Web", "app": "Team App"},
            "factor": 0.125,
        },
    )
    assert shown["baseUrl"] == "http://kc"
    assert shown["clientSecret"] == ""  # a secret is never shown
    assert shown["verifyTls"] is True and shown["pageSize"] == "100"  # the defaults
    assert shown["roles"] == "a\nb"
    assert shown["teams"] == "web=Team Web\napp=Team App"
    assert shown["factor"] == "0.125" and shown["strategy"] == "MANUAL"


def test_the_fields_carry_label_help_and_what_the_user_may_enter() -> None:
    fields = {
        f.key: f
        for f in build_fields(
            SCHEMA,
            shown_values(SCHEMA, {}),
            "config",
            secrets_set={"clientSecret"},
            errors={"config.baseUrl": "Required."},
        )
    }
    assert fields["baseUrl"].label == "Keycloak URL" and fields["baseUrl"].required
    assert fields["baseUrl"].maxlength == 300 and fields["baseUrl"].error == "Required."
    assert fields["baseUrl"].name == "config.baseUrl" and fields["baseUrl"].id == "f-config-baseUrl"
    assert fields["clientSecret"].secret_set
    assert (fields["pageSize"].minimum, fields["pageSize"].maximum) == (10, 500)
    assert fields["strategy"].options == ["MANUAL", "EVEN_SPLIT"]
    assert fields["choice"].options == ["A", "B"]
    assert fields["defaultTeam"].maxlength == 120 and not fields["defaultTeam"].required


def form(**values: str) -> FormData:
    return FormData([(f"config.{key}", value) for key, value in values.items()])


def test_what_was_typed_is_read_back_as_settings() -> None:
    typed = form(
        baseUrl="  http://kc  ",
        clientSecret="s3cret",
        verifyTls="on",
        pageSize="50",
        factor="0,25",
        strategy="EVEN_SPLIT",
        roles="a\n\n b \n",
        teams="web = Team Web\napp=Team App",
        defaultTeam="",
        share="",
        choice="B",
        nested='{"a": "x"}',
    )
    values, errors = parse(SCHEMA, shown_from_form(SCHEMA, typed, "config"), "config")
    assert errors == {}
    assert values == {
        "baseUrl": "http://kc",
        "clientSecret": "s3cret",
        "verifyTls": True,
        "pageSize": 50,
        "factor": 0.25,
        "strategy": "EVEN_SPLIT",
        "roles": ["a", "b"],
        "teams": {"web": "Team Web", "app": "Team App"},
        "choice": "B",
        "nested": {"a": "x"},
    }


def test_an_empty_input_is_left_out_and_an_empty_secret_keeps_the_stored_one() -> None:
    values, errors = parse(
        SCHEMA, shown_from_form(SCHEMA, form(baseUrl="http://kc"), "config"), "config"
    )
    assert errors == {}
    assert values == {"baseUrl": "http://kc", "verifyTls": False}  # an unticked box is "off"


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("pageSize", "many"),
        ("pageSize", "1.5"),
        ("factor", "a lot"),
        ("teams", "no separator"),
        ("teams", "=value"),
        ("nested", "{broken"),
    ],
)
def test_a_value_that_cannot_be_read_is_reported_at_its_input(field: str, text: str) -> None:
    values, errors = parse(
        SCHEMA, shown_from_form(SCHEMA, form(**{field: text}), "config"), "config"
    )
    assert list(errors) == [f"config.{field}"] and field not in values


def test_the_form_comes_back_as_it_was_typed() -> None:
    typed = form(baseUrl="http://kc", pageSize="many", teams="x", verifyTls="on")
    shown = shown_from_form(SCHEMA, typed, "config")
    assert shown["pageSize"] == "many" and shown["teams"] == "x" and shown["verifyTls"] is True
    assert shown["clientSecret"] == "" and shown["roles"] == ""


OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scope", "projects"],
    "properties": {
        "scope": {"enum": ["ALL", "SELECTED"], "type": "string", "default": "SELECTED"},
        "projects": {
            "type": "array",
            "items": {"type": "string"},
            "x-remoteOptions": True,
            "x-showWhen": {"field": "scope", "equals": "SELECTED"},
        },
    },
}


def test_choices_from_the_source_are_check_boxes_shown_for_one_value() -> None:
    fields = {
        f.key: f
        for f in build_fields(
            OPTIONS_SCHEMA, shown_values(OPTIONS_SCHEMA, {"projects": ["10001"]}), "config"
        )
    }
    projects = fields["projects"]
    assert projects.kind == "options"
    assert projects.value == ["10001"] and projects.choices == [("10001", "10001")]
    assert projects.show_when == ("config.scope", "SELECTED")
    assert projects.required is False  # it may be hidden, the API checks it
    assert fields["scope"].show_when is None and fields["scope"].required is True


def test_ticked_choices_are_read_back_as_a_list() -> None:
    form = FormData(
        [("config.scope", "SELECTED"), ("config.projects", "10001"), ("config.projects", "10002")]
    )
    shown = shown_from_form(OPTIONS_SCHEMA, form, "config")
    assert shown["projects"] == ["10001", "10002"]
    values, errors = parse(OPTIONS_SCHEMA, shown, "config")
    assert values == {"scope": "SELECTED", "projects": ["10001", "10002"]} and errors == {}
    nothing = parse(
        OPTIONS_SCHEMA, shown_from_form(OPTIONS_SCHEMA, FormData([]), "config"), "config"
    )
    assert nothing[0]["projects"] == []
