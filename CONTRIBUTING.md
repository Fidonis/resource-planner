# Contributing to Resource Planner

Thanks for considering a contribution. This page says how to ask, report and propose changes, and
what a change needs before it can be merged.

## Code of Conduct

Be kind, assume good intent and keep discussions about the project. See
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Where to ask

- **Questions and ideas** → [GitHub Discussions](https://github.com/Fidonis/resource-planner/discussions).
- **Bugs, feature requests, documentation problems** →
  [GitHub Issues](https://github.com/Fidonis/resource-planner/issues), using the
  [issue forms](https://github.com/Fidonis/resource-planner/issues/new/choose) (*Bug report*,
  *Feature request*, *Documentation*). Blank issues are switched off.
- **Security vulnerabilities** →
  [private vulnerability reporting](https://github.com/Fidonis/resource-planner/security/advisories/new),
  see [`SECURITY.md`](SECURITY.md). Not a public issue.

## Before you start

Open an issue or comment on an existing one, so that nobody works twice on the same thing. Larger
changes should be agreed there first. The specification
([`ressource-planner.md`](ressource-planner.md)) and
[`docs/implementation-notes.md`](docs/implementation-notes.md) say what the product should do and
what it does differently; requirement IDs (`FR-PLAN-04`, `NFR-API-09`, …) can be used in issues,
commits and tests.

## Pull requests

1. Create a branch from the branch your change is for: the open milestone branch
   `releases/<x.y.z>` for work planned for that release, otherwise `main`. Name it
   `feat/<name>`, `fix/<name>`, `docs/<name>`, `refactor/<name>`, `test/<name>`, `ci/<name>` or
   `chore/<name>`.
2. Make small, reviewable commits.
3. Open a pull request against that branch and fill in the template: linked issues, type of
   change, test plan.
4. CI must be green. Then ask for a review; every change needs the approval of a code owner
   ([`CODEOWNERS`](CODEOWNERS)).
5. Pull requests are squash-merged. The pull request title becomes the commit message, so it follows
   [Conventional Commits](https://www.conventionalcommits.org/): `<type>[(<scope>)][!]: <subject>`,
   with the types `feat`, `fix`, `docs`, `refactor`, `perf`, `style`, `test`, `ci`, `build`,
   `chore` and `revert`. The subject is lowercase, imperative and has no full stop. A `!` marks a
   breaking change.

`main` and the `releases/*` branches only take changes through pull requests; they are never
force-pushed or deleted. When a release is cut, its milestone branch is merged into `main` with a
merge commit, so that the squashed commits of its pull requests stay visible, and the release
`vX.Y.Z` is published from `main`. The version and the [`CHANGELOG.md`](CHANGELOG.md) entry are
updated by hand in a pull request of their own just before the cut; feature pull requests do not
touch them. Publishing a release builds and signs the images
`ghcr.io/fidonis/resource-planner-api` and `ghcr.io/fidonis/resource-planner-ui`.

## Checks

Everything CI runs can be run locally (see the *Develop* section of the [README](README.md) for the
setup):

```bash
uvx yamllint .
uv run ruff check . && uv run ruff format --check .
uv run mypy src/api/planner_api src/ui/planner_ui src/contracts/planner_client tests
uv run lint-imports
uv run pytest
uv run python scripts/generate_client.py --check
uv run python scripts/i18n.py missing
uv run python scripts/check_wording.py
```

Browser tests (`tests/e2e`) run against a running stack; run them when you change the web
interface.

`scripts/check_wording.py` checks every file against a list of words that must not appear in the
repository. The maintainers keep the list as a repository secret, and CI passes it in
`WORDING_RULES`; without it, the check is skipped. Locally, point `WORDING_RULES_FILE` at a copy
of the list.

The workflow `License check` rejects a pull request that adds a dependency under a copyleft
license, and refreshes [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) after every push to
`main`; do not edit that file by hand.

## What a change needs

- **Tests.** A behaviour change comes with a test on the level where it lives: pure rules as unit
  tests, API behaviour in `tests/api` (against a real PostgreSQL), pages and fragments in
  `tests/ui`, anything that needs a real browser in `tests/e2e`. A bug fix starts with a test that
  fails.
- **The contract.** When an endpoint, a model or a description changes, refresh
  `src/contracts/openapi.json` and the generated client (commands in the README). Every operation
  keeps a stable `operationId`, and every route declares its permission.
- **The layers.** Routers stay thin, rules live in `domain`, use cases in `services`, database access
  only in `repositories`. The web interface never imports the API package and never recomputes a
  figure the API returns. `lint-imports` enforces the main rules.
- **Texts.** Every text in the web interface goes through `_()` and has an English source and a
  German translation: run `scripts/i18n.py extract` and `update`, translate the new entries in
  `src/ui/planner_ui/locale/de/LC_MESSAGES/messages.po`, and check `scripts/i18n.py missing`.
  New error codes of the API need a translation as well.
- **Accessibility.** Information is never conveyed by colour alone; controls have names; dialogs
  and the board work with the keyboard.
- **Dependencies.** New runtime dependencies need a reason in the pull request and an entry in
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Browser files are vendored from npm packages by
  `src/ui/scripts/vendor.mjs`, never loaded from a CDN.
- **No secrets.** Never commit credentials or real data. `.env` is ignored; `.env.example` holds
  `CHANGE_ME` placeholders only.

## Writing a provider

New integrations are separate packages that register themselves in the entry-point group
`resource_planner.providers`; see [`docs/providers.md`](docs/providers.md). A provider for a specific
product (Jira, SAP, …) is welcome as a pull request in its own directory or as its own package.

## License of your contributions (Inbound = Outbound)

This project is published under the [MIT License](LICENSE). By submitting a pull request, a comment
with a code suggestion or any other contribution to this repository, **you agree that your
contribution is licensed under the same MIT License** that the project itself uses ("Inbound =
Outbound"). No separate Contributor License Agreement (CLA) is required.

You also confirm that:

- You have the right to license the contribution under MIT.
- Your contribution does not knowingly infringe a third party's copyright, patent or trademark.

### Copyleft licenses are not accepted

To keep the project freely redistributable under MIT, **contributions containing or directly
depending on code under a copyleft license are not accepted** without prior written approval from
the Fidonis management. This includes (non-exhaustive list):

- GNU General Public License (GPL), any version
- GNU Lesser General Public License (LGPL), any version
- GNU Affero General Public License (AGPL), any version
- Mozilla Public License (MPL) 2.0 when used in a way that would make the project a covered work
- Server Side Public License (SSPL)
- European Union Public Licence (EUPL)

The approved exceptions are listed in the `License check` workflow, each with its reason in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Trademarks

"Fidonis" and its logo are trademarks of Fidonis GmbH (in Gründung) and are not covered by the MIT
License. See [`TRADEMARK.md`](TRADEMARK.md) for the rules on how you may and may not use them.
