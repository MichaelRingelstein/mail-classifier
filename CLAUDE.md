# Architecture notes

Guidance for contributors and coding assistants working on this repository.

Local web app that fetches the **unread** mails of an Outlook (Microsoft Graph) mailbox, classifies each one with **Jev** (TypeSafe AI "System One" model, via `typesafe-sdk`), shows the result in a table, and moves the "discard" mails to Deleted Items on user confirmation. A secondary goal is to benchmark Jev's speed (latency per mail, throughput, cost), so timings are first-class data. The UI text and the Jev prompts/criteria (`app/classify.py`) are in English.

## Commands

```bash
make install   # venv + dependencies (make install-dev adds test deps)
make run       # uvicorn on http://127.0.0.1:8000 (no --reload: restart after any code or .env change)
make auth      # one-off Outlook sign-in (MSAL device code); writes token_cache.json
make test      # offline tests (test_*.py), temp DB, no Outlook / Jev calls
PYTHONPATH=. .venv/bin/python tests/fake_classify.py     # real Jev calls on 8 fake mails, temp DB (API key needed)
PYTHONPATH=. .venv/bin/python tests/dev_server.py      # UI demo on :8766 with a fake Outlook (120 mails) + real Jev
PYTHONPATH=. .venv/bin/python tests/criteria_check.py  # maintainer tool: real Jev on a COPY of your own data/mail.db
```

Tests are plain scripts, not pytest; run one by running its file (exit code 1 on failure). They redirect the DB with `MAIL_DB=<path>` and (for the rules test) monkeypatch `preferences.FILE`. There is no linter or build step.

## Scope

Outlook (Microsoft Graph) is the only supported mailbox and Jev (`typesafe-sdk`) the only supported classifier, on purpose. Don't add provider abstractions or other backends unless asked; keep all Graph code in `outlook.py` and all Jev code in `classify.py`.

## Architecture

One FastAPI process (`app/main.py`), SQLite (`data/mail.db`), static HTML/JS/CSS in `app/static/` (no build). The flow is split into separate jobs, each started by its own button: **fetch** (`fetch_job()`, `POST /api/fetch`, steps 1–2), **classify** (`classify_job()`, `POST /api/classify`, step 3) and **apply** (step 4):

1. **Fetch** (`outlook.py`): list unread ids in the inbox, then fetch only unknown ids through Graph `$batch` (20 sub-requests per batch, bodies as plain text, `IdType="ImmutableId"` so ids survive moves). Sub-request 429s are retried honoring `Retry-After`; Graph allows only ~4 concurrent requests per mailbox, hence `GRAPH_FETCH_CONCURRENCY=4`.
2. **Reconcile** (`db.reconcile`): mails no longer unread become `read_elsewhere`; mails already moved stay `processed`.
3. **Classify** (`classify.run`): one `system_one` call per mail carrying 5 questions at once (category Choice, importance Choice, spam / action-required / prompt-injection Noul). Async with a semaphore; each result is written to SQLite and pushed as an SSE event.
4. **Apply / undo** (`apply_all` / `undo_job` in `main.py`, plan computed by `db.plan()`): one background job in 3 steps — flag the "awaiting_reply" mails (also the uncertain ones when `FLAG_UNCERTAIN_REPLIES=1`), move recent newsletters to the Outlook folder named `NEWSLETTER_FOLDER` (status `filed`: they leave the app's scope, `reconcile` ignores them), then trash the "discard" ones (status `processed`). Each batch is journaled in the `actions` table and statuses updated as it goes, so an interrupted run never loses track of moved mails. Only the trash step is undoable from the UI. Already-flagged mails are never moved or trashed.

Things that span several files and are easy to get wrong:

- **Raw vs effective category.** The DB stores Jev's *raw* answer in `emails.category`. What the user sees (and what "Apply" trashes) is computed on the fly by `db.effective()` from raw answer + `preferences.json`: manual correction (`user_category`) > "to_review" (low confidence or injection) > newsletter rules (sender decision `keep`/`discard`, and expiry: a newsletter older than `newsletter_max_age_days` becomes "discard", age beats "keep"). Rules apply only to the `newsletter` category (or to a/b-category mails of a sender with a decision); invoices, "important" and "awaiting_reply" are never aged out. Do not write rule results back into `category`.
- **`preferences.json` is hot-reloaded** (mtime-cached in `preferences.py`, atomic write via `set_decision`); invalid JSON silently falls back to defaults, so decisions disappear. The `/api/senders*` routes and the "By sender" tab read/write it.
- **Jev's criteria live in code** (`criteria()` / `questions()` in `classify.py`); editing them needs a restart and an "All (reclassify)" run to re-score existing mails (~0.5 $ for ~6k mails).
- **One job at a time.** `RUN["running"]` guards fetch/classify/apply/undo; the SSE stream (`/api/events`) replays the current run's events, so the UI dedupes cards by id. `emit()` is thread-safe and is called from worker threads.
- Graph and Jev are only reached through `outlook.py` and `classify.py`; the demo/test servers replace `outlook.*` functions rather than mocking HTTP.

## Pitfalls

- **Never POST to `/api/apply` or `/api/apply/undo` (or `/api/fetch`, `/api/classify`) to "test the server is up"**: they start real jobs that move mails in the user's mailbox. Use GET routes (`/api/state`, `/api/apply/preview`) or the dev server with the fake Outlook.
- `data/mail.db` holds everything (classifications, corrections, undo journal); if it is renamed/reset, the app starts empty and re-fetches the unread mails. Keep `.bak` copies.
- Do not restart the server while an apply job is running (check `running` in `/api/state`).
- Secrets and personal data (`.env`, `token_cache.json`, `preferences.json`, `data/`) are gitignored. Never put real addresses or mail content in tests (use `example.com`).
- The API has no authentication; `app/security.py` (Host / Origin checks) is what keeps it local. Keep new state-changing routes behind it.
