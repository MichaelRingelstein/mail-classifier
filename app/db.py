import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from . import preferences
from .config import CATEGORIES, DB_FILE, FLAG_UNCERTAIN_REPLIES, INJECTION_THRESHOLD, REVIEW

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  from_addr TEXT,
  subject TEXT,
  date TEXT,
  labels TEXT,
  snippet TEXT,
  body TEXT,
  has_unsub INTEGER DEFAULT 0,
  status TEXT DEFAULT 'new',
  category TEXT,
  confidence REAL,
  probabilities TEXT,
  action_required REAL,
  injection REAL,
  spam REAL,
  importance TEXT,
  rule TEXT,
  needs_review INTEGER DEFAULT 0,
  user_category TEXT,
  latency_ms REAL,
  input_tokens INTEGER,
  request_id TEXT,
  error TEXT,
  fetched_at REAL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  started_at REAL,
  finished_at REAL,
  status TEXT,
  params TEXT,
  metrics TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT,
  email_ids TEXT,
  applied_at REAL,
  undone INTEGER DEFAULT 0
);
"""


def init() -> None:
    global _conn
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute('PRAGMA table_info(emails)')}
        for name, typ in (('spam', 'REAL'), ('importance', 'TEXT'), ('rule', 'TEXT')):
            if name not in cols:
                _conn.execute(f'ALTER TABLE emails ADD COLUMN {name} {typ}')
        _conn.commit()


def _q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        cur = _conn.execute(sql, args)
        rows = cur.fetchall()
        _conn.commit()
        return rows


def _cutoff(prefs: dict, now: datetime | None = None) -> str:
    """Cutoff date (UTC, ISO format truncated to the second): a newsletter older than this is expired."""
    return ((now or datetime.now(timezone.utc)) - timedelta(days=prefs["max_age_days"])).strftime("%Y-%m-%dT%H:%M:%S")


def effective(row, prefs: dict | None = None, cutoff: str | None = None) -> tuple[str, str | None]:
    """(display column, rule applied). Order: manual correction > to review > newsletter rules > Jev's answer.

    Jev's raw answer stays in the DB; the rules (per-sender decision, expiry) are computed here,
    so a decision applies at once to all mails, past and future, without calling Jev again."""
    if row["user_category"]:
        return row["user_category"], None
    cat = row["category"]
    if cat is None:
        return "", None
    prefs = prefs or preferences.load()
    decision = preferences.decision_for(row["from_addr"], prefs)
    # a sender whose fate I decided is a newsletter, unless Jev sees a real message (important / awaiting reply)
    is_newsletter = cat == "newsletter" or (decision and cat in ("to_read", "discard"))
    injection_alert = (row["injection"] or 0) > INJECTION_THRESHOLD
    if row["needs_review"] and not (is_newsletter and decision and not injection_alert):
        return REVIEW, None
    if not is_newsletter:
        return cat, None
    cutoff = cutoff or _cutoff(prefs)
    if (row["date"] or "")[:19] < cutoff:
        return "discard", f"expired (> {prefs['max_age_days']} d)"
    if decision == "discard":
        return "discard", "sender: discard"
    return "newsletter", ("sender: keep" if decision == "keep" else None)


def view_of(row) -> str:
    return effective(row)[0]


def sender_name(raw: str) -> str:
    raw = (raw or "").strip()
    if "<" in raw:
        name = raw.split("<")[0].strip().strip('"')
        return name or raw.split("<")[1].rstrip(">")
    return raw


def card(row, pending: bool = False, prefs: dict | None = None, cutoff: str | None = None) -> dict:
    """Table row. `pending` = shown without a result (waiting for Jev)."""
    done = row["category"] is not None and not pending
    view, rule = effective(row, prefs, cutoff) if done else ("", None)
    return {
        "id": row["id"],
        "subject": row["subject"] or "(no subject)",
        "from": sender_name(row["from_addr"]),
        "date": row["date"],
        "classified": done,
        "view": view,
        "category": row["category"] if done else None,
        "importance": row["importance"] if done else None,
        "confidence": row["confidence"] if done else None,
        "spam": row["spam"] if done else None,
        "action_required": row["action_required"] if done else None,
        "injection": row["injection"] if done else None,
        "needs_review": bool(row["needs_review"]) if done else False,
        "latency_ms": row["latency_ms"] if done else None,
        "input_tokens": row["input_tokens"] if done else None,
        "rule": rule,
        "corrected": bool(row["user_category"]),
        "error": None if pending else row["error"],
    }


# --- emails -----------------------------------------------------------------

def known_ids() -> set[str]:
    return {r["id"] for r in _q("SELECT id FROM emails")}


def insert_emails(emails: list[dict]) -> None:
    now = time.time()
    with _lock:
        _conn.executemany(
            """INSERT OR IGNORE INTO emails
               (id, thread_id, from_addr, subject, date, labels, snippet, body, has_unsub, status, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,'new',?)""",
            [
                (e["id"], e["thread_id"], e["from"], e["subject"], e["date"],
                 json.dumps(e["labels"]), e["snippet"], e["body"], int(e["has_unsub"]), now)
                for e in emails
            ],
        )
        _conn.commit()


def reconcile(unread_ids: list[str]) -> dict:
    """Mails no longer unread -> read_elsewhere; mails that came back -> reactivated."""
    unread = set(unread_ids)
    gone = reread = 0
    with _lock:
        for r in _conn.execute("SELECT id, status, category FROM emails WHERE status NOT IN ('processed','filed')").fetchall():
            if r["id"] not in unread and r["status"] != "read_elsewhere":
                _conn.execute("UPDATE emails SET status='read_elsewhere' WHERE id=?", (r["id"],))
                gone += 1
            elif r["id"] in unread and r["status"] == "read_elsewhere":
                _conn.execute(
                    "UPDATE emails SET status=? WHERE id=?",
                    ("classified" if r["category"] else "new", r["id"]),
                )
                reread += 1
        _conn.commit()
    return {"read_elsewhere": gone, "returned": reread}


def pending(reclassify: bool) -> list[sqlite3.Row]:
    if reclassify:
        return _q("SELECT * FROM emails WHERE status IN ('new','classified')")
    return _q("SELECT * FROM emails WHERE status='new' OR (status='classified' AND category IS NULL)")


def save_classification(email_id: str, c: dict) -> sqlite3.Row:
    _q(
        """UPDATE emails SET status=CASE WHEN status IN ('processed','read_elsewhere') THEN status ELSE ? END, category=?, confidence=?, probabilities=?, action_required=?,
           injection=?, spam=?, importance=?, rule=?, needs_review=?, latency_ms=?, input_tokens=?, request_id=?, error=?
           WHERE id=?""",
        (
            "classified" if c.get("category") else "new",
            c.get("category"), c.get("confidence"), json.dumps(c.get("probabilities")),
            c.get("action_required"), c.get("injection"), c.get("spam"), c.get("importance"), c.get("rule"),
            int(c.get("needs_review", 0)),
            c.get("latency_ms"), c.get("input_tokens"), c.get("request_id"), c.get("error"),
            email_id,
        ),
    )
    return get(email_id)


def get(email_id: str):
    rows = _q("SELECT * FROM emails WHERE id=?", (email_id,))
    return rows[0] if rows else None


def set_user_category(email_id: str, category: str | None) -> None:
    _q("UPDATE emails SET user_category=? WHERE id=?", (category, email_id))


def board() -> list[dict]:
    """All mails still unread, one per table row."""
    prefs = preferences.load()
    cutoff = _cutoff(prefs)
    return [card(r, prefs=prefs, cutoff=cutoff) for r in _q("SELECT * FROM emails WHERE status IN ('new','classified') ORDER BY date DESC")]


def plan() -> dict[str, list]:
    """What "Apply" will do: trash, Newsletter folder, "needs reply" flag.
    A mail already flagged (by me or by the app) is never moved or deleted automatically."""
    prefs = preferences.load()
    cutoff = _cutoff(prefs)
    out: dict[str, list] = {"trash": [], "newsletters": [], "flag": [], "flag_uncertain": []}
    for r in _q("SELECT * FROM emails WHERE status='classified'"):
        if "FLAGGED" in json.loads(r["labels"] or "[]"):
            continue
        view = effective(r, prefs, cutoff)[0]
        if view == "discard":
            out["trash"].append(r)
        elif view == "newsletter":
            out["newsletters"].append(r)
        elif view == "awaiting_reply":
            out["flag"].append(r)
        elif (FLAG_UNCERTAIN_REPLIES and view == REVIEW and r["category"] == "awaiting_reply" and not r["user_category"]
              and (r["injection"] or 0) <= INJECTION_THRESHOLD and (r["action_required"] or 0) >= 0.5):
            out["flag_uncertain"].append(r)  # a flag is reversible: better a false positive than a forgotten reply
    return out


def eligible_for_trash() -> list[sqlite3.Row]:
    return plan()["trash"]


def add_label(ids: list[str], label: str) -> None:
    """Adds a local label (e.g. FLAGGED) to stay consistent with Outlook without re-syncing."""
    with _lock:
        for i in ids:
            r = _conn.execute("SELECT labels FROM emails WHERE id=?", (i,)).fetchone()
            if r:
                labels = json.loads(r["labels"] or "[]")
                if label not in labels:
                    _conn.execute("UPDATE emails SET labels=? WHERE id=?", (json.dumps(labels + [label]), i))
        _conn.commit()


def senders() -> list[dict]:
    """Newsletter senders (Jev's answer or a saved decision), with their counters, for the "By sender" view."""
    prefs = preferences.load()
    cutoff = _cutoff(prefs)
    groups: dict[str, dict] = {}
    for r in _q("SELECT * FROM emails WHERE status='classified' AND category IS NOT NULL ORDER BY date DESC"):
        if r["user_category"]:
            continue
        decision = preferences.decision_for(r["from_addr"], prefs)
        if r["category"] != "newsletter" and not (decision and r["category"] in ("to_read", "discard")):
            continue
        addr = preferences.sender_addr(r["from_addr"])
        g = groups.setdefault(addr, {"sender": addr, "name": sender_name(r["from_addr"]), "total": 0, "recent": 0,
                                     "spam_sum": 0.0, "spam_n": 0, "subject": r["subject"] or "", "decision": decision})
        g["total"] += 1
        g["recent"] += (r["date"] or "")[:19] >= cutoff
        if r["spam"] is not None:
            g["spam_sum"] += r["spam"]
            g["spam_n"] += 1
    out = []
    for g in groups.values():
        spam = g.pop("spam_sum") / g["spam_n"] if g["spam_n"] else None
        g.pop("spam_n")
        out.append({**g, "spam": spam, "suggestion": None if spam is None else ("discard" if spam > 0.7 else "keep")})
    return sorted(out, key=lambda x: -x["total"])


def rename_id(old: str, new: str) -> None:
    _q("UPDATE emails SET id=? WHERE id=?", (new, old))


def set_status(ids: list[str], status: str) -> None:
    with _lock:
        _conn.executemany("UPDATE emails SET status=? WHERE id=?", [(status, i) for i in ids])
        _conn.commit()


# --- runs / actions ---------------------------------------------------------

def create_run(kind: str, params: dict) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO runs (kind, started_at, status, params) VALUES (?,?,?,?)",
            (kind, time.time(), "running", json.dumps(params)),
        )
        _conn.commit()
        return cur.lastrowid


def finish_run(run_id: int, status: str, metrics: dict) -> None:
    _q(
        "UPDATE runs SET finished_at=?, status=?, metrics=? WHERE id=?",
        (time.time(), status, json.dumps(metrics), run_id),
    )


def runs(limit: int = 20) -> list[dict]:
    out = []
    for r in _q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)):
        out.append({
            "id": r["id"], "kind": r["kind"], "started_at": r["started_at"],
            "status": r["status"], "params": json.loads(r["params"] or "{}"),
            "metrics": json.loads(r["metrics"] or "{}"),
        })
    return out


def log_action(action: str, ids: list[str]) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO actions (action, email_ids, applied_at) VALUES (?,?,?)",
            (action, json.dumps(ids), time.time()),
        )
        _conn.commit()
        return cur.lastrowid


def update_action(action_id: int, ids: list[str]) -> None:
    _q("UPDATE actions SET email_ids=?, applied_at=? WHERE id=?", (json.dumps(ids), time.time(), action_id))


def last_trash_action():
    rows = _q("SELECT * FROM actions WHERE action='trash' AND undone=0 ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def mark_action_undone(action_id: int) -> None:
    _q("UPDATE actions SET undone=1 WHERE id=?", (action_id,))


def agreement() -> dict:
    """Agreement rate between Jev and me, over the mails I corrected or confirmed."""
    rows = _q("SELECT category, user_category FROM emails WHERE user_category IS NOT NULL AND category IS NOT NULL")
    total = len(rows)
    same = sum(1 for r in rows if r["category"] == r["user_category"])
    return {"reviewed": total, "agree": same, "rate": (same / total) if total else None}
