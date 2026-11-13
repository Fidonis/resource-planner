"""OpenID Connect against any standards-compliant provider: discovery, code exchange, JWT checks.

The provider is reached through ``OIDC_DISCOVERY_URL`` (the address inside the container network)
while the browser is sent to the endpoints the discovery document names, which carry the public
host. The ``issuer`` of the document and of every token must equal ``OIDC_ISSUER``.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from planner_api.config import Settings

# Asymmetric algorithms only: a token that names HS256 or "none" is never accepted.
SIGNING_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
]
CACHE_SECONDS = 3600
# A token with an unknown key id may trigger a refresh of the key set, but not more often than this.
JWKS_REFRESH_MIN_SECONDS = 30
LEEWAY_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10.0
MAX_ROLES = 500
MAX_ROLE_LENGTH = 255
_ROLE_SPLIT = re.compile(r"[\s,]+")


class OidcError(Exception):
    """The provider refused, was unreachable, or sent something that does not check out.

    ``reason`` is short and safe to show in a log; it is never shown to the browser.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def claim_at(claims: dict[str, Any], path: str) -> Any:
    """The value at a dotted path such as ``realm_access.roles``.

    A key that itself contains dots (a Keycloak client id like ``my.app``) is found as well.
    """
    if path in claims:
        return claims[path]
    parts = path.split(".")
    for end in range(1, len(parts)):
        inner = claims.get(".".join(parts[:end]))
        if isinstance(inner, dict):
            found = claim_at(inner, ".".join(parts[end:]))
            if found is not None:
                return found
    return None


def roles_from(claims: dict[str, Any], path: str) -> list[str]:
    """Role names found at ``path``: a list of strings, or one string with several names."""
    value = claim_at(claims, path)
    if isinstance(value, str):
        value = [part for part in _ROLE_SPLIT.split(value) if part]
    if not isinstance(value, list):
        return []
    roles: list[str] = []
    for item in value:
        if isinstance(item, str) and item and len(item) <= MAX_ROLE_LENGTH and item not in roles:
            roles.append(item)
        if len(roles) >= MAX_ROLES:
            break
    return roles


class OidcProvider:
    """One configured identity provider with cached discovery document and signing keys."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, jwt.PyJWKSet] | None = None

    # --- transport ---------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False
        )

    async def _get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        try:
            async with self._client() as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OidcError(f"GET {url} failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OidcError(f"GET {url} answered {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise OidcError(f"GET {url} did not return JSON") from exc

    # --- discovery and keys ------------------------------------------------------------------

    @property
    def issuer(self) -> str:
        return self.settings.oidc_issuer.rstrip("/")

    @property
    def discovery_url(self) -> str:
        return self.settings.oidc_discovery_url or f"{self.issuer}/.well-known/openid-configuration"

    async def discovery(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._discovery is not None and now - self._discovery[0] < CACHE_SECONDS:
            return self._discovery[1]
        document = await self._get_json(self.discovery_url)
        if not isinstance(document, dict):
            raise OidcError("The discovery document is not an object")
        if str(document.get("issuer", "")).rstrip("/") != self.issuer:
            raise OidcError("The issuer of the discovery document differs from OIDC_ISSUER")
        self._discovery = (now, document)
        return document

    async def endpoint(self, name: str) -> str:
        value = (await self.discovery()).get(name)
        if not isinstance(value, str) or not value:
            raise OidcError(f"The provider has no {name}")
        return value

    async def _key_set(self, *, refresh: bool = False) -> jwt.PyJWKSet:
        now = time.monotonic()
        if self._jwks is not None:
            age = now - self._jwks[0]
            if not refresh and age < CACHE_SECONDS:
                return self._jwks[1]
            if refresh and age < JWKS_REFRESH_MIN_SECONDS:
                return self._jwks[1]
        document = await self._get_json(await self.endpoint("jwks_uri"))
        try:
            key_set = jwt.PyJWKSet.from_dict(document)
        except (jwt.PyJWTError, TypeError, ValueError, KeyError) as exc:
            raise OidcError("The key set of the provider cannot be read") from exc
        self._jwks = (now, key_set)
        return key_set

    async def _signing_key(self, token: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError("The token is not a JWT") from exc
        kid = header.get("kid")
        for refresh in (False, True):
            key_set = await self._key_set(refresh=refresh)
            for key in key_set.keys:
                if kid is None or key.key_id == kid:
                    return key.key
        raise OidcError("No key of the provider matches the token")

    async def verify(
        self, token: str, *, audience: str | list[str] | None, nonce: str | None = None
    ) -> dict[str, Any]:
        """Check signature, issuer, expiry and (when given) audience and nonce of a JWT."""
        key = await self._signing_key(token)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=SIGNING_ALGORITHMS,
                audience=audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss"], "verify_aud": audience is not None},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"The token is not valid: {type(exc).__name__}") from exc
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("The nonce of the ID token does not match")
        return claims

    # --- the authorization code flow ---------------------------------------------------------

    async def authorization_url(self, *, state: str, nonce: str, verifier: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.oidc_client_id,
                "redirect_uri": self.settings.oidc_redirect_uri,
                "scope": self.settings.oidc_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        base = await self.endpoint("authorization_endpoint")
        return f"{base}{'&' if '?' in base else '?'}{query}"

    async def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        endpoint = await self.endpoint("token_endpoint")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "code_verifier": verifier,
        }
        if self.settings.oidc_client_secret:
            form["client_secret"] = self.settings.oidc_client_secret
        try:
            async with self._client() as client:
                response = await client.post(endpoint, data=form)
        except httpx.HTTPError as exc:
            raise OidcError(f"The token request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OidcError(f"The token endpoint answered {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise OidcError("The token endpoint did not return JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
            raise OidcError("The token response carries no ID token")
        return body

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        document = await self._get_json(
            await self.endpoint("userinfo_endpoint"),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(document, dict):
            raise OidcError("The userinfo answer is not an object")
        return document

    async def end_session_url(self, id_token_hint: str | None) -> str | None:
        """The provider's logout page (RP-initiated logout), if it has one."""
        try:
            endpoint = (await self.discovery()).get("end_session_endpoint")
        except OidcError:
            return None
        if not isinstance(endpoint, str) or not endpoint:
            return None
        query: dict[str, str] = {
            "post_logout_redirect_uri": f"{self.settings.public_base_url}/login",
            "client_id": self.settings.oidc_client_id,
        }
        if id_token_hint:
            query["id_token_hint"] = id_token_hint
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(query)}"
