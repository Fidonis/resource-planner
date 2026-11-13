"""Command line: ``planner-api serve|migrate|seed-demo|export-openapi|healthcheck``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from planner_api import migrate
from planner_api.config import Settings, get_settings
from planner_api.errors import ProblemError


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "planner_api.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
        log_config=None,
    )
    return 0


def _migrate(args: argparse.Namespace) -> int:
    migrate.upgrade(get_settings().database_url, args.revision)
    return 0


async def _load_demo_data(settings: Settings, today: date) -> None:
    from planner_api import bootstrap
    from planner_api.db import create_engine, create_session_factory
    from planner_api.services import demo

    engine = create_engine(settings.database_url, pool_size=2)
    try:
        factory = create_session_factory(engine)
        await bootstrap.reconcile(factory, settings)
        async with factory() as session:
            await demo.seed_demo(session, today)
            await session.commit()
    finally:
        await engine.dispose()


def _seed_demo(args: argparse.Namespace) -> int:
    """Load the demo data into an empty installation (needs ``ENABLE_DEMO_SEED=true``)."""
    settings = get_settings()
    if not settings.enable_demo_seed:
        sys.stderr.write("Demo data is switched off. Set ENABLE_DEMO_SEED=true to load it.\n")
        return 1
    migrate.upgrade(settings.database_url, "head")
    try:
        asyncio.run(_load_demo_data(settings, args.today or date.today()))
    except ProblemError as exc:
        sys.stderr.write(f"{exc.detail}\n")
        return 1
    sys.stdout.write("Demo data loaded.\n")
    return 0


def _export_openapi(args: argparse.Namespace) -> int:
    from planner_api.config import Settings
    from planner_api.main import create_app

    # The schema does not depend on the environment; no database is touched.
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@localhost/x"))
    text = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    return 0


def _healthcheck(args: argparse.Namespace) -> int:
    """Exit 0 when the ready endpoint answers; used by the container health check."""
    url = f"http://127.0.0.1:{args.port}/health/ready"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:  # noqa: S310 (fixed local URL)
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planner-api")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the API server.")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 (inside the container)
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--forwarded-allow-ips",
        default="127.0.0.1",
        help="Proxies whose X-Forwarded-For is trusted; used for sign-in rate limiting.",
    )
    serve.set_defaults(handler=_serve)

    up = commands.add_parser("migrate", help="Bring the database schema up to date.")
    up.add_argument("revision", nargs="?", default="head")
    up.set_defaults(handler=_migrate)

    seed = commands.add_parser("seed-demo", help="Load the demo data into an empty installation.")
    seed.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="The day the demo weeks are placed around. Default: today.",
    )
    seed.set_defaults(handler=_seed_demo)

    spec = commands.add_parser("export-openapi", help="Write the OpenAPI document.")
    spec.add_argument("output", nargs="?", default="-")
    spec.set_defaults(handler=_export_openapi)

    health = commands.add_parser("healthcheck", help="Exit 0 when the server is ready.")
    health.add_argument("--port", type=int, default=8000)
    health.set_defaults(handler=_healthcheck)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
