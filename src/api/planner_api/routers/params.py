"""Query parameters and response headers shared by the resource routers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, Query, Response

from planner_api.auth.deps import IfMatch
from planner_api.services.context import Expected


@dataclass(frozen=True, slots=True)
class PageParams:
    page: int
    page_size: int
    sort: str | None


async def page_params(
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1.")] = 1,
    page_size: Annotated[
        int, Query(alias="pageSize", ge=1, le=200, description="Rows per page (at most 200).")
    ] = 50,
    sort: Annotated[
        str | None,
        Query(description="Comma-separated sort keys; prefix a key with '-' for descending."),
    ] = None,
) -> PageParams:
    return PageParams(page, page_size, sort)


async def expected_on_delete(
    if_match: IfMatch,
    version: Annotated[
        int | None,
        Query(description="Version being deleted; an alternative to the If-Match header."),
    ] = None,
) -> Expected:
    return Expected(header=if_match, body=version)


PageDep = Annotated[PageParams, Depends(page_params)]
DeleteDep = Annotated[Expected, Depends(expected_on_delete)]


def etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


@dataclass(frozen=True, slots=True)
class Horizon:
    start: date | None
    weeks: int | None


async def horizon_params(
    start: Annotated[
        date | None,
        Query(
            alias="from",
            description="Any day of the first week; the horizon starts on that week's Monday. "
            "Default: the current week.",
        ),
    ] = None,
    weeks: Annotated[
        int | None,
        Query(
            ge=1,
            le=26,
            description="Number of weeks. Default: the setting `planningHorizonWeeks`.",
        ),
    ] = None,
) -> Horizon:
    return Horizon(start, weeks)


HorizonDep = Annotated[Horizon, Depends(horizon_params)]

CSV_REQUEST: dict[str, object] = {
    "requestBody": {
        "required": True,
        "description": "The file as UTF-8 text. The delimiter is `;`, `,` or a tab.",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}
CSV_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {
        "description": "The file as UTF-8 text with `;` as delimiter.",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}


def csv_response(text: str, filename: str) -> Response:
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def with_csv_request(options: dict[str, Any]) -> dict[str, Any]:
    """Add the text/csv request body to the route options built by ``guard``."""
    return {**options, "openapi_extra": {**options["openapi_extra"], **CSV_REQUEST}}
