# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

First version of the implementation of the specification (phases P1a to P1c, P2a and P2c).

### Added

- Planning board with a pool of tasks, a grid of members and weeks, drag and drop, the *Assign…*
  dialog, capacity with part-time shares, absences and half days, over-booking marks.
- Overview of demand, planned work and utilisation; availability read model.
- Master data: teams, skills, members, absence types, absences with a calendar; tasks with an
  effort per skill; CSV import and export.
- Local accounts and OpenID Connect sign-in, roles with permissions and team limits, role mapping
  from the identity provider, API tokens, bearer tokens, audit log, settings.
- Provider framework with a sync engine, run log, scheduler and ingest API; CSV providers for
  tasks, members and absences; Keycloak directory import.
- REST API under `/api/v1` with an OpenAPI 3.1 contract, optimistic locking, dry runs and problem
  documents; generated Python client.
- Web interface in English and German, light and dark theme.
- `docker-compose.yml` with database, API and interface; `docker/docker-compose.dev.yml` with a
  reverse proxy and a development Keycloak; demo data.

### Not included

- Jira and SAP providers, the MCP server and the P3 items of the specification.
  See [`docs/implementation-notes.md`](docs/implementation-notes.md).
