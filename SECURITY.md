# Security policy

Thank you for helping to keep Resource Planner and its users safe.

## Supported versions

Security fixes are issued for the latest published release. Older releases receive only
critical-severity fixes, on a best-effort basis.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Please use the private vulnerability reporting of this repository (the *Security* tab, then
*Report a vulnerability*). It sends the report to the maintainers as a private advisory.

If you cannot use that, write to `security@fidonis.de` and we will open the private advisory on
your behalf.

Please include:

- a description of the vulnerability and its impact,
- steps to reproduce (a minimal proof of concept is ideal),
- the version or commit affected and how you run it (Compose, behind which proxy),
- a suggested mitigation or fix, if you have one.

## What to expect

- **Acknowledgement** within 3 working days.
- **Initial triage** (severity, confirmation, scope) within 10 working days.
- **Coordinated disclosure**: when a fix is ready we publish a security advisory and a patched
  release. Embargo periods are agreed with the reporter case by case; 90 days is the default upper
  bound.
- **Credit**: with your permission, your name or handle is listed in the advisory and the release
  notes.

## Security design considerations

For operators and reviewers, so that the intended behaviour is not mistaken for a flaw.

**One enforcement point.** The API decides what a user may do. The web interface calls it with the
signed-in user's own session cookie and CSRF token, holds no credential of its own and has no
database access. Each route declares the permission it needs, and a test fails when one does not.

**Sessions and sign-in.** Sessions are rows in the database behind an `HttpOnly`, `SameSite=Lax`
cookie (`Secure` when `PUBLIC_BASE_URL` is `https://`); every unsafe request needs the session's
CSRF token. Passwords are hashed with argon2id; repeated failures lock an account and are rate
limited. The rate limiter keeps its counters in memory, so it counts per API process.
OpenID Connect uses the authorisation code flow with PKCE; the state, nonce and verifier are kept
server side, and only relative return addresses are accepted. Bearer tokens of the identity
provider are accepted only when `OIDC_API_AUDIENCES` names an audience, and are checked for
signature, issuer, expiry and audience.

**API tokens** (`rpt_…`) are shown once, stored as hashes, revocable, and never carry more rights
than their owner has.

**Secrets at rest.** Secrets that integrations store (a Keycloak client secret, for example) are
encrypted with `APP_ENCRYPTION_KEY` (Fernet), are never returned by the API, and are left out of
logs, run logs and the audit log. A database backup alone does not reveal them; the key must be
kept apart from it and backed up on its own. Losing the key means entering the secrets again.

**Files.** Providers read files only from inside `IMPORT_DIR`; paths that leave it, including
through links, are refused. CSV upload through the API is limited to 5 MiB and 20,000 rows and reports errors per line.

**Web interface.** A strict content security policy (own origin only, no inline script, no
`eval`, no framing), `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` and, on
HTTPS, `Strict-Transport-Security`. Pages are sent with `Cache-Control: no-store`. HTML is
escaped by the template engine.

**Deployment.** The Compose file publishes the interface and the API on the loopback address only
and terminates no TLS; put a TLS-terminating reverse proxy in front of them before exposing them,
and set `PUBLIC_BASE_URL` to the public `https://` address. Do not publish the API port itself
beyond the proxy's routes. The Keycloak of `docker/docker-compose.dev.yml` is for development
only: development mode, no TLS, demo accounts. Containers do not mount the Docker socket. Run one
API instance (the scheduler and the rate limiter are per process).

## Out of scope

- Vulnerabilities in third-party software listed in
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); please report those to the project concerned.
- Issues that need an attacker-controlled identity provider configuration or administrator rights
  to set up.
- Denial of service by exhausting the resources of the host.
- Findings against the development compose file (proxy, Keycloak) that stem from its being a development setup.
