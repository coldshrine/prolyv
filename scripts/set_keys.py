#!/usr/bin/env python
"""Write sign-in keys into .env (or another env file) without printing them.

    uv run python scripts/set_keys.py            # interactive, writes .env
    uv run python scripts/set_keys.py --file /etc/prolyv.env

Asks for each value; press Enter to keep the current one. Secrets are read with getpass (nothing is echoed) and
never printed back. Existing lines in the file are kept; missing keys are appended.
"""

from __future__ import annotations

import argparse
import getpass
import re
import secrets
from pathlib import Path

KEYS = [
    ("GOOGLE_CLIENT_ID", "Google OAuth client ID (…apps.googleusercontent.com)", False),
    ("GOOGLE_CLIENT_SECRET", "Google OAuth client secret", True),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token from @BotFather (123456:ABC…)", True),
    ("TELEGRAM_BOT_USERNAME", "Telegram bot username without @", False),
    ("SMTP_HOST", "SMTP host for sign-in e-mails (empty = none)", False),
    ("SMTP_PORT", "SMTP port", False),
    ("SMTP_USER", "SMTP user", False),
    ("SMTP_PASSWORD", "SMTP password", True),
    ("SMTP_FROM", "From address for sign-in e-mails", False),
]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        match = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if match and match.group(1) in updates:
            lines[i] = f"{match.group(1)}={updates[match.group(1)]}"
            seen.add(match.group(1))
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=".env")
    args = parser.parse_args()
    path = Path(args.file)
    current = read_env(path)
    updates: dict[str, str] = {}

    if not current.get("SECRET_KEY") or current.get("SECRET_KEY") in ("change-me", ""):
        updates["SECRET_KEY"] = secrets.token_urlsafe(48)
        print("SECRET_KEY: generated")

    print("Press Enter to keep the current value. Secrets are not echoed.\n")
    for key, label, secret in KEYS:
        has = "set" if current.get(key) else "empty"
        prompt = f"{label} [{has}]: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if value:
            updates[key] = value
    if not updates:
        print("Nothing changed.")
        return 0
    write_env(path, updates)
    print(f"\nSaved {len(updates)} value(s) to {path}. Restart the app to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
