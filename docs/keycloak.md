# Single sign-on with Keycloak

The planner signs people in through any OpenID Connect provider; Keycloak is the reference. This
guide connects the planner to a Keycloak realm you run already. Keycloak can serve the planner in
three ways, each set up on its own:

| Use | What it needs in Keycloak | Section |
|---|---|---|
| Sign-in for people | A confidential client with the standard flow, and roles | [1](#1-create-the-client) to [6](#6-sign-out) |
| Programs that call the API with a user's token | An audience in that token | [7](#7-programs-with-bearer-tokens-optional) |
| Members imported from the directory | A second client with a service account | [8](#8-the-directory-import-optional) |

To try single sign-on without a Keycloak of your own, use the development Keycloak of
`docker/docker-compose.dev.yml` (see the [README](../README.md#trying-it-with-a-proxy-and-single-sign-on)).
Its realm, `docker/keycloak/planner-realm.json`, is set up the way this guide describes and can
serve as an example.

The steps follow the admin console of Keycloak 26, the version of the development setup.

## Before you start

- **One public address for the planner.** A proxy that terminates TLS serves the interface and
  the API on the same address, with `/api/` routed to the API (see the
  [README](../README.md#run-it)). `PUBLIC_BASE_URL` is that address, for example
  `https://planner.example.com`. Keycloak sends the browser back to
  `<PUBLIC_BASE_URL>/api/v1/auth/oidc/callback`, so the callback must reach the API through the
  proxy.
- **A realm** for the people who will use the planner. The examples below call it `company`.
- **An administrator account** in Keycloak with the right to manage clients and roles of that
  realm.

## 1. Create the client

In the realm, open *Clients → Create client*:

1. **General settings**: client type *OpenID Connect*, client ID `resource-planner` (any ID works;
   it becomes `OIDC_CLIENT_ID`), a name if you like.
2. **Capability config**:
   - *Client authentication*: **On** (a confidential client; the planner sends a client secret).
   - *Authorization*: Off.
   - *Authentication flow*: only **Standard flow**. *Direct access grants*, *Implicit flow*,
     *Service accounts* and the device grant stay off.
   - *PKCE Method*: **S256**, if your Keycloak shows the setting here; otherwise set
     *Proof Key for Code Exchange Code Challenge Method* to **S256** under *Advanced* after saving.
     The planner always sends a PKCE challenge, so requiring it costs nothing.
3. **Login settings** (replace the address with your `PUBLIC_BASE_URL`):

   | Field | Value |
   |---|---|
   | Root URL, Home URL | `https://planner.example.com` |
   | Valid redirect URIs | `https://planner.example.com/api/v1/auth/oidc/callback` |
   | Valid post logout redirect URIs | `https://planner.example.com/login` |
   | Web origins | empty (the planner never calls Keycloak from browser scripts) |

   Give the redirect URI in full, without wildcards.
4. Save, then copy the secret from the *Credentials* tab. It becomes `OIDC_CLIENT_SECRET`.
5. On the *Client scopes* tab, check that the default scopes include **`basic`**, **`profile`**,
   **`email`** and **`roles`** (Keycloak assigns them to new clients). The planner needs what they
   put into the tokens:

   | Scope | Claims the planner reads |
   |---|---|
   | `basic` | `sub`, the identity of the account. Without it the access token has no `sub` and the sign-in fails. |
   | `profile` | `preferred_username` (the user name) and `name` (the display name) |
   | `email` | `email` and `email_verified`, used to link the account to a member |
   | `roles` | `realm_access.roles` and `resource_access`, the roles mapped to the planner's roles |

## 2. Give people roles

The planner reads the roles of a person from the access token and turns them into its own roles
through the *role mapping*. On its first start it creates this mapping:

| Keycloak realm role | Role in the planner |
|---|---|
| `rp-admin` | Administrator (everything) |
| `rp-planner` | Planner (tasks, assignments and absences of all teams) |
| `rp-viewer` | Viewer (read only) |

In Keycloak, create these realm roles (*Realm roles → Create role*) and assign them, preferably
to groups (*Groups → a group → Role mapping → Assign role*) rather than to single users. One
person should get `rp-admin` before local sign-in is switched off (see
[step 4](#4-configure-the-planner)).

In the planner, *Roles → Role mapping* (permission `role:admin`) changes the mapping. Further
realm roles can give the other built-in roles (*Member*, *Team lead*, *Master data admin*) or roles
of your own, including roles limited to teams, for example `rp-lead-backend` for a *Team lead*
role limited to the team *Backend*. A person with several mapped roles gets the permissions of all
of them.

Things to know:

- **Roles are read at sign-in.** A role given or taken away in Keycloak counts from the person's
  next sign-in to the planner. A change of the mapping in the planner counts from the next request.
  *Users* shows the Keycloak roles of each account as they were at its last sign-in.
- **A person without a mapped role** can sign in, but has no permissions and sees only the
  account page.
- **Client roles instead of realm roles.** To keep the planner's roles on its own client, create
  them on the client (*Clients → resource-planner → Roles*) and set
  `OIDC_ROLES_CLAIM=resource_access.resource-planner.roles` (use your client ID in the middle).
- **Groups instead of roles.** Add a mapper to the client (*Clients → resource-planner → Client
  scopes → resource-planner-dedicated → Add mapper → By configuration → Group Membership*) with the
  token claim name `groups`, *Full group path* off and *Add to access token* on, set
  `OIDC_ROLES_CLAIM=groups`, and map the group names in the planner.

## 3. Accounts and members

The first sign-in of a person creates an account in the planner:

- The account is bound to the `sub` of the token, so a renamed Keycloak user keeps their account.
  The user name comes from `preferred_username` (`OIDC_USERNAME_CLAIM`); when another account has
  that name already, the new account gets `<name>~<the first eight characters of sub>`.
- Display name and e-mail address follow Keycloak at every sign-in.
- When the e-mail address is **verified** in Keycloak (`email_verified`), the account is linked to
  the member with the same address that has no account yet. A linked account can, with the
  *Member* role, maintain its own absences. Mark the addresses as verified in Keycloak, or let the
  directory import (section 8) create the members with the same addresses. An administrator can
  also link an account to a member by hand under *Users*.
- An administrator can switch an account off under *Users*; its next sign-in is refused, whatever
  Keycloak says.

## 4. Configure the planner

Set in `.env`:

```bash
AUTH_OIDC_ENABLED=true
OIDC_ISSUER=https://sso.example.com/realms/company
OIDC_CLIENT_ID=resource-planner
OIDC_CLIENT_SECRET=<the secret from the Credentials tab>
```

Apply it with `docker compose up -d`, which recreates the API container. The sign-in page then
shows *Sign in with single sign-on*.

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_OIDC_ENABLED` | `false` | Switches single sign-on on. |
| `OIDC_ISSUER` | | The issuer of the realm. It must equal the `issuer` in `<OIDC_ISSUER>/.well-known/openid-configuration` exactly (scheme, host, port, path; a trailing slash does not matter). Keycloak builds it from its hostname setting. |
| `OIDC_DISCOVERY_URL` | `<OIDC_ISSUER>/.well-known/openid-configuration` | Where the API fetches the discovery document, when it cannot use the public address (see [step 5](#5-how-the-api-reaches-keycloak)). |
| `OIDC_CLIENT_ID` | | The client ID from step 1 (`resource-planner` in `.env.example`). |
| `OIDC_CLIENT_SECRET` | | The client secret from step 1. |
| `OIDC_SCOPES` | `openid profile email` | Scopes requested at sign-in. Default scopes of the client (`basic`, `roles`) are added by Keycloak anyway. |
| `OIDC_ROLES_CLAIM` | `realm_access.roles` | Where the roles are in the token, as a dotted path. A list of strings or one string with names separated by spaces or commas. |
| `OIDC_ROLES_SOURCE` | `access_token` | Which document holds the roles: `access_token`, `id_token` or `userinfo`. Keycloak puts realm roles into the access token. |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | The claim that becomes the user name. |
| `OIDC_API_AUDIENCES` | empty | Audiences accepted on bearer tokens; empty refuses bearer tokens (see [step 7](#7-programs-with-bearer-tokens-optional)). |

**Local sign-in.** `AUTH_LOCAL_ENABLED=true` keeps the local accounts, including the first
administrator, next to single sign-on; the sign-in page shows both. Keep it on until a person with
`rp-admin` has signed in through Keycloak and the role mapping works. Then switch it off with
`AUTH_LOCAL_ENABLED=false` if only Keycloak shall sign people in. API tokens keep working either
way.

## 5. How the API reaches Keycloak

The browser visits only Keycloak's sign-in and logout pages. Everything else, the discovery
document, the signing keys, the token exchange and (with `OIDC_ROLES_SOURCE=userinfo`) the userinfo
endpoint, is fetched by the API container. It must therefore reach Keycloak itself.

**Keycloak is reachable at its public address from the container.** Nothing more to do; leave
`OIDC_DISCOVERY_URL` empty.

**It is not**, for example because both run on one host and the public address does not work from
inside a container. Then let the API use an internal address:

1. Put the API on a network on which Keycloak is reachable, with a `docker-compose.override.yml`
   next to `docker-compose.yml` (Compose reads it automatically):

   ```yaml
   services:
     api:
       networks: [default, sso]

   networks:
     sso:
       external: true
       name: keycloak_default   # the network of the Keycloak container
   ```

2. Set `OIDC_DISCOVERY_URL` to the internal address, for example
   `http://keycloak:8080/realms/company/.well-known/openid-configuration`.
3. Start Keycloak with `KC_HOSTNAME` set to its public address and
   `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`. The document fetched internally then still names the
   public issuer and the public sign-in page for the browser, while the token, key and userinfo
   endpoints carry the internal address. Without the second setting all endpoints carry the public
   address, which the API cannot reach.

`docker/docker-compose.dev.yml` works this way.

**Keycloak's certificate comes from your own certificate authority.** The API trusts the public
authorities of its image only. Mount a bundle that contains them and your own authority, and point
`SSL_CERT_FILE` at it, again in `docker-compose.override.yml`:

```yaml
services:
  api:
    environment:
      SSL_CERT_FILE: /etc/ssl/certs/host-ca-bundle.crt
    volumes:
      - /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/host-ca-bundle.crt:ro
```

The example uses the bundle of a Debian or Ubuntu host after `update-ca-certificates`. The file
replaces the list of trusted authorities for every outgoing connection of the API, the Jira and
Keycloak integrations included, so a file with your authority alone would break the others.

## 6. Sign out

*Sign out* ends the session in the planner and then sends the browser to Keycloak's logout page,
with the ID token as a hint and `<PUBLIC_BASE_URL>/login` as the address to return to. That
address must be one of the *Valid post logout redirect URIs* of the client (step 1); otherwise
Keycloak shows an error page instead of returning.

A session in the planner lasts `SESSION_TTL_HOURS` (12 by default), independent of the Keycloak
session. The planner does not refresh tokens; it reads the roles again at the next sign-in.

## 7. Programs with bearer tokens (optional)

A program that holds a person's access token, a script or a later MCP server, can call the API
with `Authorization: Bearer <token>` and gets exactly that person's permissions. The API checks
the signature, issuer, expiry and audience of the token and reads the roles from it, not from an
earlier sign-in.

1. Choose the audience the API accepts, usually the planner's client ID, and set
   `OIDC_API_AUDIENCES=resource-planner` (several separated by commas).
2. Make Keycloak put it into the tokens of the program's client: *Clients → the program's client →
   Client scopes → <client>-dedicated → Add mapper → By configuration → Audience*, with
   *Included Client Audience* `resource-planner`, *Add to access token* on and *Add to ID token*
   off. The program's client also needs the scopes `basic` and `roles`.
3. Send the header `X-Client-Name` with a short name of the program; the audit log records it.

The first call with a token of a person who never signed in creates their account, as a sign-in
would. [mcp-preparation.md](mcp-preparation.md#trying-the-bearer-path) shows a call against the
development Keycloak.

## 8. The directory import (optional)

The *Keycloak* integration imports the users of a realm as members, with team and skills from
their groups. It uses a second client with a service account, never the sign-in client. The set-up
in Keycloak, the settings and the mapping are in
[providers.md](providers.md#keycloak-directory-import). Members imported this way carry the e-mail
addresses of Keycloak, so the accounts of people who sign in are linked to them (section 3).

## When it does not work

A failed sign-in sends the browser back to the sign-in page with a short message. The reason is
written to the API's log only (`docker compose logs api`, lines with `OIDC`), never shown in the
browser.

| Symptom or log line | Cause and remedy |
|---|---|
| Keycloak shows *Invalid parameter: redirect_uri* | The redirect URI of the client does not equal `<PUBLIC_BASE_URL>/api/v1/auth/oidc/callback`. Check both, including `https` and the port. |
| *Single sign-on is not available right now*, log: `Cannot start the OIDC sign-in` | The API cannot read the discovery document. See step 5. |
| `The issuer of the discovery document differs from OIDC_ISSUER` | `OIDC_ISSUER` does not equal the `issuer` Keycloak reports. Copy it from the discovery document, or fix Keycloak's hostname setting. |
| `GET … failed: ConnectError` or `ConnectTimeout`, `The token request failed: …` | The API cannot reach that address (network, name resolution, a public address used from inside the container), or does not trust Keycloak's certificate. See step 5. |
| `The token endpoint answered 401` | The client secret is wrong or missing. |
| `The token endpoint answered 400` | Keycloak refused the code: the redirect URI or the PKCE setting differs, or the code expired. |
| `The access token has no 'sub' claim` | The client lacks the scope `basic`. |
| `The sign-in was not started by this browser` | The browser came back on another address than it started from, so the sign-in cookie is missing: `PUBLIC_BASE_URL` differs from the address in the browser, or an `https` setting is reached over `http`. |
| Signed in, but only the account page | No role of the person is mapped. *Users* shows the roles the token carried; Keycloak's *Client scopes → Evaluate* shows the token a person would get. |
| *The account is disabled.* | An administrator switched the account off in the planner. |
| Keycloak shows an error after *Sign out* | `<PUBLIC_BASE_URL>/login` is missing from the *Valid post logout redirect URIs*. |
