# Contributing

1. Run `make install-dev` then `make test` (offline tests, no account required).
2. Tests are simple scripts (`tests/test_*.py`, exit code 1 on failure). Add one for any classification or "Apply" rule you change.
3. `tests/dev_server.py` starts the UI on a fake Outlook; `tests/fake_classify.py` calls the real Jev (key required).
4. Never test `/api/apply` against a real mailbox: it moves mails.
5. Do not add any real data (addresses, mail contents) to tests or fixtures: use `example.com`.
