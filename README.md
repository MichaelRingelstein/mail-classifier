# mail-classifier

A local web app that fetches the **unread** mails of an Outlook mailbox (Microsoft Graph), classifies each one with **Jev** (the "System One" model from [TypeSafe AI](https://typesafe.ai), via `typesafe-sdk`), shows the result in a table and, after confirmation, files or deletes the mails.
It also doubles as a Jev benchmark (latency per mail, throughput, cost): timings are displayed as first-class data.

> The UI and the classification prompts/criteria are **in English**. The criteria live in `app/classify.py` (`criteria()` / `questions()`) and are meant to be adapted to your own needs.

## Scope

For now the project supports exactly one mail provider and one model:

- **Mailbox: Outlook / Microsoft 365 only** (Microsoft Graph).
- **Classifier: Jev only** (TypeSafe AI, `typesafe-sdk`).

Other mailboxes (Gmail, IMAP...) and other models are possible later but not supported yet. Graph is only reached through `app/outlook.py` and Jev through `app/classify.py`, which is where such support would plug in.

## What it does

For each mail, Jev answers 5 questions in a single call: category (to read, awaiting reply, important to keep, newsletter, discard), importance, spam, action required, prompt injection.
"Apply" then runs, in this order:

1. puts a **flag** on mails that are awaiting a reply;
2. moves recent newsletters to the Outlook folder named by `NEWSLETTER_FOLDER` (it must already exist);
3. sends the mails to discard to the **trash** (Deleted Items folder, recoverable).

Mails that are already flagged are never moved. Only the trash step can be undone from the UI; the action journal is kept in SQLite. You can correct a category by hand, set a per-sender "keep" / "discard" decision, and set a maximum age for newsletters (`preferences.json`, see `preferences.example.json`).

## Privacy

For each mail, the **sender, subject, date, some Outlook signals and the first characters of the body** (`BODY_MAX_CHARS`, 2000 by default) are sent to TypeSafe AI for classification. Do not use the app on a mailbox whose content may not leave your organization without its approval.
Everything else stays local: `data/mail.db` holds the content of the fetched mails and `token_cache.json` your Outlook tokens (both are git-ignored).

## Prerequisites

- Python 3.12 or newer.
- A **TypeSafe AI API key** with access to Jev (proprietary, paid service; the estimated cost is shown in the app). There is no other model provider.
- An Outlook / Microsoft 365 account and an **Azure app registration** (free).

## Installation

```bash
make install            # creates .venv and installs dependencies
cp .env.example .env    # then fill in TYPESAFE_API_KEY, MS_CLIENT_ID, MS_TENANT
```

### Connecting to Outlook (Azure registration)

1. On [portal.azure.com](https://portal.azure.com) go to Microsoft Entra ID > *App registrations* > *New registration*.
2. Supported account types: choose according to your mailbox (personal Microsoft accounts, organization, or both) and set `MS_TENANT` accordingly (`consumers`, `organizations` or `common`).
3. *Authentication* > enable **"Allow public client flows"** (device code flow).
4. *API permissions* > Microsoft Graph > **delegated** > `Mail.ReadWrite`. Some work tenants require admin consent.
5. Copy the **Application (client) ID** into `MS_CLIENT_ID`.
6. Run `make auth`: open the displayed URL and enter the code. The tokens are written to `token_cache.json`.

## Usage

```bash
make run     # http://127.0.0.1:8000 (restart after any code or .env change)
make test    # offline tests, no account or key needed
```

In the UI, click "Fetch new unread mails" (Outlook only, free), then "Classify mails" (Jev, paid) once you are ready. Review the table (mails marked "To review" are those where Jev is unsure or detects an injection), then click "Apply" and confirm the preview.

## Security

The API has no authentication and can move mails: it listens on `127.0.0.1` only and rejects unknown Hosts and cross-site write requests (`app/security.py`). **Do not expose it on a network.** See [SECURITY.md](SECURITY.md).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CLAUDE.md](CLAUDE.md) (architecture). `tests/dev_server.py` starts the UI on a fake Outlook with 120 mails and the real Jev (key required).

## License

MIT, see [LICENSE](LICENSE).
