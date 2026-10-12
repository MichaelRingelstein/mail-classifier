"""Personal preferences (preferences.json): newsletter expiry and per-sender decisions."""
import json
import os
import re
import threading

from pathlib import Path

from .config import ROOT

FILE = Path(os.getenv("PREFERENCES_FILE", ROOT / "preferences.json"))
DEFAULT_MAX_AGE_DAYS = 30
_lock = threading.Lock()
_cache: tuple[float, dict] | None = None  # (mtime, preferences): re-read only when the file changes


def sender_addr(raw: str) -> str:
    """Lowercase sender address, from "Name <address>"."""
    m = re.search(r"<([^>]+)>", raw or "")
    return (m.group(1) if m else (raw or "")).strip().lower()


def _read() -> dict:
    try:
        d = json.loads(FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    decisions = {k.lower(): v for k, v in (d.get("newsletters") or {}).items() if v in ("keep", "discard")}
    return {"max_age_days": int(d.get("newsletter_max_age_days", DEFAULT_MAX_AGE_DAYS)), "decisions": decisions}


def load() -> dict:
    global _cache
    try:
        mtime = os.path.getmtime(FILE)
    except OSError:
        mtime = 0.0
    with _lock:
        if _cache is None or _cache[0] != mtime:
            _cache = (mtime, _read())
        return _cache[1]


def decision_for(sender: str, prefs: dict | None = None) -> str | None:
    """'keep' / 'discard' if a decision covers this sender (the most specific one wins), otherwise None."""
    addr, decisions = sender_addr(sender), (prefs or load())["decisions"]
    hits = [k for k in decisions if k in addr]
    return decisions[max(hits, key=len)] if hits else None


def set_decision(key: str, decision: str | None) -> None:
    """Saves (or removes, if decision is None) the decision for an address, preserving the rest of the file."""
    key = key.strip().lower()
    with _lock:
        try:
            d = json.loads(FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            d = {}
        nl = d.setdefault("newsletters", {})
        if decision in ("keep", "discard"):
            nl[key] = decision
        else:
            nl.pop(key, None)
        d.setdefault("newsletter_max_age_days", DEFAULT_MAX_AGE_DAYS)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(FILE)
