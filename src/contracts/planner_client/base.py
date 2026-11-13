"""Transport of the generated client: credentials per call, problem errors, no shared cookies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel

SESSION_COOKIE = "rp_session"
CSRF_HEADER = "X-CSRF-Token"

M = TypeVar("M", bound=BaseModel)


@dataclass
class Credentials:
    """Who a call is made for. One instance per incoming request, never shared.

    The client keeps no session of its own: the web interface forwards the cookie of the browser
    that sent the request. A login or logout response updates ``session_cookie`` in place so the
    caller can hand the new value on to the browser.
    """

    session_cookie: str | None = None
    csrf_token: str | None = None
    bearer_token: str | None = None
    client_name: str | None = None
    request_id: str | None = None
    forwarded_for: str | None = None
    language: str | None = None


class ApiError(Exception):
    """The API answered with a problem document (or something that is not one)."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors or []
        self.extra = extra or {}
        self.headers = dict(headers or {})

    @property
    def field_errors(self) -> dict[str, str]:
        """First message per field, for showing next to the inputs."""
        found: dict[str, str] = {}
        for error in self.errors:
            if error.get("field"):
                found.setdefault(error["field"], error.get("message", ""))
        return found


class ApiUnreachableError(Exception):
    """The API could not be reached or did not answer in time."""


class _NoCookies(CookieJar):
    """A jar that stores nothing, so one user's cookie can never reach another user's call."""

    def set_cookie(self, cookie: Cookie) -> None:
        return None

    def extract_cookies(self, response: Any, request: Any) -> None:
        return None


def _drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return [_query_value(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class ClientBase:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            # Passed as the jar itself: httpx copies a ``Cookies`` object into a jar of its own.
            cookies=_NoCookies(),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, creds: Credentials, method: str, if_match: int | None) -> dict[str, str]:
        headers = {"Accept": "application/json, application/problem+json, text/csv"}
        if creds.bearer_token:
            headers["Authorization"] = f"Bearer {creds.bearer_token}"
        if creds.session_cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={creds.session_cookie}"
        if creds.csrf_token and method not in {"GET", "HEAD", "OPTIONS"}:
            headers[CSRF_HEADER] = creds.csrf_token
        if creds.client_name:
            headers["X-Client-Name"] = creds.client_name
        if creds.request_id:
            headers["X-Request-ID"] = creds.request_id
        if creds.forwarded_for:
            headers["X-Forwarded-For"] = creds.forwarded_for
        if creds.language:
            headers["Accept-Language"] = creds.language
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        return headers

    async def _fetch(
        self,
        method: str,
        path: str,
        creds: Credentials,
        *,
        response: type[M],
        body: BaseModel | None = None,
        text: str | None = None,
        query: Mapping[str, Any] | None = None,
        if_match: int | None = None,
    ) -> M:
        """Call an operation that returns a document."""
        raw = await self._send(method, path, creds, body, query, if_match, text)
        if not raw.content:
            raise ApiError(raw.status_code, "EMPTY_RESPONSE", "The API returned no document.")
        return response.model_validate(raw.json())

    async def _fetch_text(
        self,
        method: str,
        path: str,
        creds: Credentials,
        *,
        query: Mapping[str, Any] | None = None,
    ) -> str:
        """Call an operation that returns a text file (CSV)."""
        return (await self._send(method, path, creds, None, query, None)).text

    async def _call(
        self,
        method: str,
        path: str,
        creds: Credentials,
        *,
        body: BaseModel | None = None,
        query: Mapping[str, Any] | None = None,
        if_match: int | None = None,
    ) -> None:
        """Call an operation that returns nothing (204)."""
        await self._send(method, path, creds, body, query, if_match)

    async def _send(
        self,
        method: str,
        path: str,
        creds: Credentials,
        body: BaseModel | None,
        query: Mapping[str, Any] | None,
        if_match: int | None,
        text: str | None = None,
    ) -> httpx.Response:
        quoted = quote(path, safe="/:@")  # ids are uuids; this guards against odd input
        content = None
        headers = self._headers(creds, method, if_match)
        if body is not None:
            content = json.dumps(body.model_dump(mode="json", by_alias=True)).encode()
            headers["Content-Type"] = "application/json"
        elif text is not None:
            content = text.encode("utf-8")
            headers["Content-Type"] = "text/csv; charset=utf-8"
        params = {k: _query_value(v) for k, v in _drop_none(query or {}).items()}
        try:
            raw = await self._http.request(
                method, quoted, headers=headers, params=params, content=content
            )
        except httpx.HTTPError as exc:
            raise ApiUnreachableError(str(exc) or exc.__class__.__name__) from exc
        self._remember_cookie(creds, raw)
        if raw.status_code >= 400:
            raise self._problem(raw)
        return raw

    @staticmethod
    def _remember_cookie(creds: Credentials, raw: httpx.Response) -> None:
        for header in raw.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if name.strip() != SESSION_COOKIE:
                continue
            value = rest.split(";", 1)[0]
            expired = "max-age=0" in header.lower() or value == '""' or not value
            creds.session_cookie = None if expired else value

    @staticmethod
    def _problem(raw: httpx.Response) -> ApiError:
        try:
            data = raw.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        known = {"type", "title", "status", "detail", "code", "errors", "instance", "requestId"}
        return ApiError(
            raw.status_code,
            str(data.get("code", f"HTTP_{raw.status_code}")),
            str(data.get("detail", raw.reason_phrase)),
            errors=data.get("errors"),
            extra={k: v for k, v in data.items() if k not in known},
            headers=raw.headers,
        )
