# Security

The app is designed to run **locally only** (127.0.0.1): the API has no authentication and can move mails.
A middleware (`app/security.py`) rejects unknown Hosts and write requests coming from another site.
Do not expose it on a network (`--host 0.0.0.0`, tunnel, public proxy).

Sensitive files (git-ignored): `.env`, `token_cache.json` (mailbox access tokens), `data/mail.db` (the content of your mails), `preferences.json`.

To report a vulnerability, open a private security advisory on the GitHub repository rather than a public issue.
