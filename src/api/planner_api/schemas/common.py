"""Building blocks shared by all request and response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema
from pydantic.alias_generators import to_camel

# Person-days are exact in the domain and plain numbers on the wire.
PersonDays = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.01, "examples": [1.5]}),
]

# Any other amount with two decimals (hours, story points), a plain number on the wire as well.
# The schema stays bare, so that an optional amount is a plain optional number for the clients.
Amount = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]

HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$", examples=["#1b5e8c"])]


class ApiModel(BaseModel):
    """camelCase on the wire, snake_case in code. Both spellings are accepted on input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        json_schema_serialization_defaults_required=True,
        str_strip_whitespace=True,
    )


class Page[T](ApiModel):
    items: list[T]
    total: int = Field(description="Number of rows that match the filters, across all pages.")


class FieldError(ApiModel):
    field: str = Field(description="Dotted path of the field the message refers to.")
    message: str
    code: str


class Problem(ApiModel):
    """RFC 9457 problem document. ``code`` is stable and meant for programs."""

    type: str
    title: str
    status: int
    detail: str
    code: str = Field(description="Stable machine-readable error code, e.g. DELETE_BLOCKED.")
    errors: list[FieldError] = Field(default_factory=list)
    instance: str | None = None
    request_id: str | None = None


class Blocker(ApiModel):
    kind: str
    count: int


class Stamped(ApiModel):
    id: uuid.UUID
    version: int = Field(description="Optimistic-lock version; send it back in If-Match.")
    created_at: datetime
    updated_at: datetime


class ListParams(ApiModel):
    page: int = 1
    page_size: int = 50
    sort: str | None = None


ErrorResponses: dict[int | str, dict[str, Any]] = {
    code: {"model": Problem, "description": desc}
    for code, desc in {
        401: "Authentication is missing or invalid.",
        403: "The permission is missing, or the CSRF check failed.",
        404: "The resource does not exist.",
        409: "The change conflicts with the current state.",
        412: "The If-Match version is out of date.",
        422: "The request is not valid.",
        428: "An If-Match version is required.",
    }.items()
}


def dates_in_order(start: date, end: date) -> bool:
    return end >= start
