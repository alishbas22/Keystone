"""Sign in to Google once for the Gmail and Drive connectors.

uv run python -m keystone.connectors.google login
uv run python -m keystone.connectors.google login --credentials path/to/credentials.json
"""

import argparse
from pathlib import Path

from keystone.connectors.google.auth import DEFAULT_DIR, GoogleAuth, GoogleAuthRequired, login
from keystone.net import configure_network


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("login", help="open the browser and allow read-only access")
    cmd.add_argument("--credentials", type=Path, default=DEFAULT_DIR / "credentials.json")
    cmd.add_argument("--token", type=Path, default=DEFAULT_DIR / "token.json")
    args = parser.parse_args()
    configure_network()
    try:
        path = login(GoogleAuth(credentials_file=args.credentials, token_file=args.token))
    except GoogleAuthRequired as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Signed in. Token saved to {path} (keep it private; it is git-ignored).")


if __name__ == "__main__":
    main()
