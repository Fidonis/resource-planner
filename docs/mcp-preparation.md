# Preparing for an MCP server

Section 10 of the specification describes an MCP server (phase P2e) that lets MCP clients read the
plan and prepare changes. **It is not part of this version.** There is no `src/mcp`, no image for
it and no Compose profile `mcp`. This page lists what the API already offers so that the server can
be added later without changing the API.

## What exists

| Need of an MCP server | In the API today |
|---|---|
| Act for the user, with the user's permissions | `Authorization: Bearer <access token of the identity provider>`. The API checks the signature (keys of the provider), issuer, expiry and audience, reads the roles from the token and applies the role mapping. Switched on by `AUTH_OIDC_ENABLED=true` and a non-empty `OIDC_API_AUDIENCES` (comma-separated audiences the API accepts). |
| Local use without single sign-on | API tokens `rpt_…` (`Authorization: Bearer rpt_…`). Created under *API tokens*; the permissions of a token are the intersection of what was chosen and what the owner may do. Hashed at rest, revocable. |
| Tool definitions from the contract | Every operation has a stable `operationId` (verb and noun, for example `planning_get`, `assignments_create`), a summary, a description and documented error codes in `GET /openapi.json`. |
| Prepare a change without saving it | `?dryRun=true` on `POST/PUT/DELETE /assignments` and `/absences`: the request is validated and its effect computed (`overbooked`, `overplanned`), nothing is saved and no audit entry is written. The answer has the shape of the real one plus `dryRun: true`. |
| "Who has free capacity?" | `GET /availability?skillId=&from=&weeks=`: every active member with the skill, per week `capacity`, `booked`, `free`, `absentDays`, ordered by total free days. |
| Recognise the caller in the audit log | The header `X-Client-Name` (up to 80 characters) is stored as `client_name`; the way the call was authenticated (`session`, `api-token`, `bearer`) as `via`. |
| Concurrency | `If-Match` with the `version` of a resource; `412`/`409` on a mismatch, `428` when it is missing. |
| Error text a caller can act on | RFC 9457 problem documents with a stable `code` (for example `ASSIGNMENT_SKILL_MISMATCH`) and field errors. |

## What the server would still have to do

- Validate the token of the MCP client for itself (FR-MCP-02) and call the API with the same
  user's identity. Section 14, item 2 of the specification leaves open how: token exchange
  (RFC 8693), a service identity that passes the subject along, or user-created API tokens only.
  The API supports the last one today and the first two as soon as the identity provider issues a
  token with the audience from `OIDC_API_AUDIENCES`. A token issued *to the MCP server* must not be
  forwarded unchanged.
- Offer the read tools of the table in section 10 and, only with `MCP_WRITE_TOOLS_ENABLED=true`,
  the write tools with `dry_run` defaulting to `true`.
- Send `X-Client-Name: planner-mcp` on every call.

## Trying the bearer path

With the development Keycloak (see [README.md](../README.md)) and `OIDC_API_AUDIENCES` set to
the audience of the `resource-planner` client:

```bash
TOKEN=$(curl -s -d grant_type=password -d client_id=resource-planner \
  -d client_secret="$OIDC_CLIENT_SECRET" -d username=planner-demo -d password="$KEYCLOAK_DEMO_PASSWORD" \
  http://localhost:8180/realms/planner/protocol/openid-connect/token | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" -H "X-Client-Name: my-script" \
  "http://localhost:8080/api/v1/availability?skillId=<id>&weeks=4"
```

(The address is the proxy of `docker/docker-compose.dev.yml`; behind your own proxy use its address.)
The caller has exactly the permissions of the internal role that the token's roles map to. The
automated tests of this path are in `tests/api/test_oidc.py`.
