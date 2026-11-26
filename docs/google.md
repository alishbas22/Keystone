# Connecting Gmail and Google Drive

Keystone reads Gmail and Google Drive **read-only** (scopes `gmail.readonly` and
`drive.readonly`). It never sends, edits or deletes anything.

| Connector | Reads | Business function from |
|---|---|---|
| `google-drive` | Files in a folder tree; Google Docs, Sheets and Slides exported as Word, Excel and PowerPoint | Folder names `Sales`, `Finance`, `HR`, `Operations` |
| `gmail` | Emails matching a Gmail search, plus supported attachments (PDF, Word, Excel…) | Gmail labels named `Sales`, `Finance`, `HR`, `Operations` |

Emails keep their sender and sent date, so facts from an email are dated by when it was sent.

## One-time setup (about 10 minutes)

### 1. Create a Google Cloud project and enable the APIs

1. Open https://console.cloud.google.com and create a project (e.g. "Keystone").
2. Go to **APIs & Services → Library** and enable **Gmail API** and **Google Drive API**.

### 2. Configure the consent screen

1. **APIs & Services → OAuth consent screen** (or *Google Auth Platform → Branding*).
2. User type **External** (or **Internal** for a Google Workspace organisation), app name
   "Keystone", your email as support and developer contact.
3. Under **Audience / Test users**, add the Google account you will read. While the app is in
   *Testing*, only test users can sign in.

### 3. Create an OAuth client

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type **Desktop app**, name "Keystone".
3. **Download JSON** and save it in the keystone folder as `data/google/credentials.json`.
   (`data/` is git-ignored: the file is never committed.)

### 4. Sign in once

```bash
uv run python -m keystone.connectors.google login
```

A browser opens. Choose the account, allow **read-only** access to Gmail and Drive. The sign-in
is saved to `data/google/token.json` and refreshed automatically. You can also sign in from the UI:
*Sources & agents → Sources → Google Drive / Gmail → Sign in with Google*.

Google shows "Google hasn't verified this app" for apps in Testing: choose **Continue** (it is
your own app).

## Sync

In the UI: *Sources & agents → Sources → Google Drive* (choose a folder ID, `root` = My Drive) or
*Gmail* (a Gmail search such as `newer_than:90d from:@harborhotels.example`), check, then sync.

From Python:

```python
from keystone.connectors import create_connector
from keystone.graph.engine import open_graph
from keystone.ingest.pipeline import sync_connector

drive = create_connector("google-drive", {"folder_id": "root", "business_function": "Operations"})
gmail = create_connector("gmail", {"query": "newer_than:90d", "max_messages": 200})

async with open_graph() as graph:
    print((await sync_connector(graph, drive)).summary())
    print((await sync_connector(graph, gmail)).summary())
```

Re-syncing skips unchanged files and emails; `since=` reads only what changed.

## Google Workspace: service account instead of a personal sign-in

An administrator can create a service account with **domain-wide delegation** for the two
read-only scopes, then configure:

```python
{"service_account_file": "data/google/service-account.json", "delegated_user": "sales@company.com"}
```

## Revoking access

Delete `data/google/token.json` and remove Keystone at https://myaccount.google.com/permissions.
