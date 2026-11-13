"""Create ``.env`` from ``.env.example`` with generated secrets.

Every ``CHANGE_ME`` is replaced by a random value. An existing ``.env`` is never overwritten.
Only the standard library is used, so the script runs before anything is installed.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "CHANGE_ME"


def generate(name: str) -> str:
    if name == "APP_ENCRYPTION_KEY":
        return base64.urlsafe_b64encode(os.urandom(32)).decode()
    if name == "BOOTSTRAP_ADMIN_PASSWORD":
        return secrets.token_urlsafe(18)
    return secrets.token_hex(24)  # also safe inside a database URL


def fill(template: str) -> tuple[str, dict[str, str]]:
    created: dict[str, str] = {}
    lines = []
    for line in template.splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value.strip() == PLACEHOLDER:
            created[name] = generate(name)
            line = f"{name}={created[name]}"
        lines.append(line)
    return "\n".join(lines) + "\n", created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    parser.add_argument("--template", type=Path, default=ROOT / ".env.example")
    args = parser.parse_args()

    if args.output.exists():
        print(f"{args.output} exists; nothing was changed.", file=sys.stderr)
        return 1
    text, created = fill(args.template.read_text(encoding="utf-8"))
    args.output.write_text(text, encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):  # not supported on every platform
        args.output.chmod(0o600)
    print(f"Wrote {args.output}.")
    if "BOOTSTRAP_ADMIN_PASSWORD" in created:
        print(
            "First sign-in: user "
            f"'{_value(text, 'BOOTSTRAP_ADMIN_USER')}' with the BOOTSTRAP_ADMIN_PASSWORD from "
            "that file. A new password is requested right away."
        )
    return 0


def _value(text: str, name: str) -> str:
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key == name:
            return value
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
