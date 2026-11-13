"""A stand-in identity provider for the sign-in tests: keys, tokens and the endpoints a client uses.

It checks what a real provider checks that matters here: the redirect URI and client of the
authorization request, the PKCE challenge against the verifier of the token request, and that a
code is used once. Tokens are signed with a real RSA key and published through a key set.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.example.test/realms/test"
INTERNAL = "http://idp.internal:8080/realms/test"
DISCOVERY_URL = f"{INTERNAL}/.well-known/openid-configuration"
CLIENT_ID = "planner"
CLIENT_SECRET = "client-secret-for-tests"
REDIRECT_URI = "http://localhost:8080/api/v1/auth/oidc/callback"
API_AUDIENCE = "planner-api"
KID = "test-key-1"


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeIdp:
    def __init__(self) -> None:
        self.key = new_key()
        self.kid = KID
        self.reset()

    def reset(self) -> None:
        """Forget codes and calls; the user that signs in next is Zoe again."""
        self.codes: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.subject = "sub-zoe"
        self.username = "zoe"
        self.name = "Zoe Example"
        self.email: str | None = "zoe@example.test"
        self.email_verified = True
        self.roles: list[str] = ["rp-planner"]
        self.hide_roles_in: set[str] = set()  # "id_token", "access_token"
        # Claims of the access token that the token endpoint hands out; None leaves a claim out.
        self.access_overrides: dict[str, Any] = {}

    # --- keys and tokens ---------------------------------------------------------------------

    def jwks(self) -> dict[str, Any]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def sign(
        self,
        claims: dict[str, Any],
        *,
        key: Any = None,
        algorithm: str = "RS256",
        kid: str | None = None,
    ) -> str:
        headers = {"kid": kid or self.kid}
        return jwt.encode(claims, key or self.key, algorithm=algorithm, headers=headers)

    def _roles_claim(self, roles: list[str]) -> dict[str, Any]:
        return {"realm_access": {"roles": roles}}

    def base_claims(self, *, audience: Any, lifetime: int = 300) -> dict[str, Any]:
        now = int(time.time())
        return {
            "iss": ISSUER,
            "sub": self.subject,
            "aud": audience,
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid.uuid4().hex,
        }

    def id_token(self, nonce: str | None, **overrides: Any) -> str:
        claims = self.base_claims(audience=CLIENT_ID)
        claims.update(
            {
                "preferred_username": self.username,
                "name": self.name,
                "nonce": nonce,
                "azp": CLIENT_ID,
            }
        )
        if self.email:
            claims.update({"email": self.email, "email_verified": self.email_verified})
        if "id_token" not in self.hide_roles_in:
            claims.update(self._roles_claim(self.roles))
        claims.update(overrides)
        return self.sign({k: v for k, v in claims.items() if v is not None})

    def access_token(
        self, *, audience: Any = "account", roles: list[str] | None = None, **overrides: Any
    ) -> str:
        claims = self.base_claims(audience=audience)
        claims.update({"preferred_username": self.username, "azp": CLIENT_ID})
        if self.email:
            claims.update({"email": self.email, "email_verified": self.email_verified})
        claims.update(self._roles_claim(self.roles if roles is None else roles))
        claims.update(overrides)
        return self.sign({k: v for k, v in claims.items() if v is not None})

    # --- the authorization step --------------------------------------------------------------

    def authorize(self, query: dict[str, list[str]]) -> str:
        """What the user's browser does at the provider: returns the code for the callback."""
        assert query["response_type"] == ["code"]
        assert query["client_id"] == [CLIENT_ID]
        assert query["redirect_uri"] == [REDIRECT_URI]
        assert query["code_challenge_method"] == ["S256"]
        assert "openid" in query["scope"][0].split()
        code = uuid.uuid4().hex
        self.codes[code] = {"nonce": query["nonce"][0], "challenge": query["code_challenge"][0]}
        return code

    # --- the endpoints the client calls ------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{INTERNAL}/protocol/openid-connect/token",
                    "jwks_uri": f"{INTERNAL}/protocol/openid-connect/certs",
                    "userinfo_endpoint": f"{INTERNAL}/protocol/openid-connect/userinfo",
                    "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
                },
            )
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            return self._token(request)
        if path.endswith("/userinfo"):
            return self._userinfo(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        issued = self.codes.pop(form.get("code", ""), None)
        if (
            issued is None
            or form.get("client_id") != CLIENT_ID
            or form.get("client_secret") != CLIENT_SECRET
            or form.get("redirect_uri") != REDIRECT_URI
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != issued["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant", "reason": "pkce"})
        body: dict[str, Any] = {"id_token": self.id_token(issued["nonce"]), "token_type": "Bearer"}
        roles = [] if "access_token" in self.hide_roles_in else self.roles
        body["access_token"] = self.access_token(roles=roles, **self.access_overrides)
        return httpx.Response(200, json=body)

    def _userinfo(self, request: httpx.Request) -> httpx.Response:
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "sub": self.subject,
                "preferred_username": self.username,
                **self._roles_claim(self.roles),
            },
        )
