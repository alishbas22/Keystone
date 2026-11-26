"""Google sign-in for the Gmail and Drive connectors (KAN-21). Read-only access only.

Two ways to authenticate:

1. Your own Google account (OAuth "Desktop app" client). Download the client file from the
   Google Cloud Console to data/google/credentials.json, then sign in once:

       uv run python -m keystone.connectors.google login

   A browser opens; after you allow read-only access, a token is saved to data/google/token.json
   (git-ignored) and refreshed automatically from then on.

2. A Google Workspace service account with domain-wide delegation (set by an administrator):
   `service_account_file` plus `delegated_user` (the mailbox or Drive to read).

See docs/google.md for the step-by-step setup.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SCOPES = [GMAIL_SCOPE, DRIVE_SCOPE]  # one sign-in covers both connectors

DEFAULT_DIR = Path("data/google")


class GoogleAuthRequired(RuntimeError):
    """No usable credentials yet. The message says what to do."""


class GoogleAuth(BaseModel):
    """Authentication settings shared by the Gmail and Drive connectors."""

    credentials_file: Path = Field(
        default=DEFAULT_DIR / "credentials.json",
        description="OAuth client file (Desktop app) from the Google Cloud Console.",
    )
    token_file: Path = Field(
        default=DEFAULT_DIR / "token.json", description="Saved sign-in, created by `login`."
    )
    service_account_file: Path | None = Field(
        default=None, description="Workspace service account key (instead of a personal login)."
    )
    delegated_user: str | None = Field(
        default=None, description="With a service account: the user whose data is read."
    )


def load_credentials(auth: GoogleAuth, scopes: list[str] | None = None) -> Any:
    """Credentials for the Google APIs, refreshed if needed. Never opens a browser."""
    scopes = scopes or SCOPES
    if auth.service_account_file is not None:
        from google.oauth2 import service_account

        if not auth.service_account_file.exists():
            raise GoogleAuthRequired(f"Service account file not found: {auth.service_account_file}")
        credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(auth.service_account_file), scopes=scopes
        )
        return credentials.with_subject(auth.delegated_user) if auth.delegated_user else credentials

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not auth.token_file.exists():
        raise GoogleAuthRequired(
            "Not signed in to Google yet. Run `uv run python -m keystone.connectors.google login` "
            "(see docs/google.md)."
        )
    credentials = Credentials.from_authorized_user_file(str(auth.token_file), SCOPES)  # type: ignore[no-untyped-call]
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        auth.token_file.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise GoogleAuthRequired("The saved Google sign-in is no longer valid. Run `login` again.")
    return credentials


def login(auth: GoogleAuth) -> str:
    """Sign in through the browser (once) and save the token. Returns the token file path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not auth.credentials_file.exists():
        raise GoogleAuthRequired(
            f"OAuth client file not found: {auth.credentials_file}. Download it from the Google "
            "Cloud Console (APIs & Services > Credentials > OAuth client ID > Desktop app)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(auth.credentials_file), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent")
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(credentials.to_json(), encoding="utf-8")
    return str(auth.token_file)


def build_service(api: str, version: str, auth: GoogleAuth) -> Any:
    """A Google API client (e.g. build_service('drive', 'v3', auth))."""
    from googleapiclient.discovery import build

    return build(api, version, credentials=load_credentials(auth), cache_discovery=False)
