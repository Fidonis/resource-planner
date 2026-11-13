"""Security headers and the Content-Security-Policy of every response.

The policy is strict because the interface is built for it: every script is a file served by this
origin (no inline script, no ``eval``; the Alpine build is the CSP build), styles come from one
stylesheet, and fonts and icons are local. The only concession is ``style-src-attr``: team colours
are data and travel as ``style="..."`` attributes.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, secure: bool) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._secure = secure

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self._secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if not request.url.path.startswith("/static/"):
            # Pages carry personal data and the session; static files are fingerprinted by version.
            headers.setdefault("Cache-Control", "no-store")
        return response
