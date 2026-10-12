PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install install-dev auth run test

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements.txt

install-dev: install
	$(BIN)/pip install -r requirements-dev.txt

# Outlook sign-in (one-off): device code flow, writes token_cache.json
auth:
	$(BIN)/python -u -m app.auth

run:
	$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port 8000

# Offline tests: no Outlook or Jev calls, temporary DB
test:
	@for t in tests/test_*.py; do echo "== $$t"; PYTHONPATH=. $(BIN)/python $$t || exit 1; done
