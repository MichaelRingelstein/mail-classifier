"""Outlook access through Microsoft Graph (delegated account, MSAL device code flow)."""
import html
import re
import threading
import unicodedata
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import msal
import requests

from .config import (
    BODY_MAX_CHARS, GRAPH_FETCH_CONCURRENCY, MS_CLIENT_ID, MS_TENANT, OUTLOOK_FOLDER,
    TOKEN_CACHE_FILE,
)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite"]
# Plain-text bodies, and ids that stay stable after a folder move.
PREFER = 'outlook.body-content-type="text", IdType="ImmutableId"'
SELECT = "id,subject,from,receivedDateTime,bodyPreview,body,categories,flag,importance,conversationId,inferenceClassification,internetMessageHeaders"
BATCH = 20  # Graph maximum per $batch request

_lock = threading.Lock()
_cache = msal.SerializableTokenCache()
_app: msal.PublicClientApplication | None = None
_http = requests.Session()
STATS: dict[str, int] = {}  # status codes of failed sub-requests (diagnostics)


class AuthError(RuntimeError):
    pass


def app() -> msal.PublicClientApplication:
    global _app
    if _app is None:
        if not MS_CLIENT_ID:
            raise AuthError("MS_CLIENT_ID missing from .env.")
        if TOKEN_CACHE_FILE.exists():
            _cache.deserialize(TOKEN_CACHE_FILE.read_text())
        _app = msal.PublicClientApplication(
            MS_CLIENT_ID, authority=f"https://login.microsoftonline.com/{MS_TENANT}", token_cache=_cache
        )
    return _app


def save_cache() -> None:
    if _cache.has_state_changed:
        TOKEN_CACHE_FILE.write_text(_cache.serialize())
        TOKEN_CACHE_FILE.chmod(0o600)  # contains access tokens for the mailbox


def token() -> str:
    with _lock:
        a = app()
        accounts = a.get_accounts()
        if not accounts:
            raise AuthError("Outlook not authorized: run `make auth` first.")
        res = a.acquire_token_silent(SCOPES, account=accounts[0])
        save_cache()
        if not res or "access_token" not in res:
            raise AuthError("Outlook session expired: run `make auth` again.")
        return res["access_token"]


def request(method: str, url: str, **kw) -> requests.Response:
    """Graph call with retries on 429 / 503 / 504 (honoring Retry-After)."""
    for attempt in range(5):
        headers = {"Authorization": f"Bearer {token()}", "Prefer": PREFER, **kw.pop("headers", {})}
        r = _http.request(method, url, headers=headers, timeout=60, **kw)
        if r.status_code in (429, 503, 504) and attempt < 4:
            time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 30))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")
        return r
    raise RuntimeError("Graph: too many attempts")


def _messages_url() -> str:
    return f"{GRAPH}/me/messages" if OUTLOOK_FOLDER == "all" else f"{GRAPH}/me/mailFolders/{OUTLOOK_FOLDER}/messages"


def list_unread_ids() -> list[str]:
    ids: list[str] = []
    url, params = _messages_url(), {"$filter": "isRead eq false", "$select": "id", "$top": 1000}
    while url:
        data = request("GET", url, params=params).json()
        ids += [m["id"] for m in data.get("value", [])]
        url, params = data.get("@odata.nextLink"), None
    return ids


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _is_bulk(m: dict, text: str) -> bool:
    """Bulk mail: List-Unsubscribe or Precedence: bulk header (reliable), otherwise an unsubscribe keyword in the body (English or French)."""
    for h in m.get("internetMessageHeaders") or []:
        name, value = (h.get("name") or "").lower(), (h.get("value") or "").lower()
        if name == "list-unsubscribe" or (name == "precedence" and value in ("bulk", "list", "junk")):
            return True
    return bool(re.search(r"unsubscribe|d\u00e9sabonn|d\u00e9sinscri", text, re.I))


def _parse(m: dict) -> dict:
    addr = (m.get("from") or {}).get("emailAddress") or {}
    sender = f"{addr.get('name', '')} <{addr.get('address', '')}>".strip() if addr else ""
    body = m.get("body") or {}
    text = body.get("content") or ""
    if body.get("contentType") == "html":
        text = _html_to_text(text)
    text = re.sub(r"\n{3,}", "\n\n", text.strip())[:BODY_MAX_CHARS]
    labels = list(m.get("categories") or [])
    if (m.get("flag") or {}).get("flagStatus") == "flagged":
        labels.append("FLAGGED")
    if m.get("importance") == "high":
        labels.append("IMPORTANCE_HIGH")
    if m.get("inferenceClassification") == "other":
        labels.append("OTHER_INBOX")  # Outlook filed it under "Other"
    return {
        "id": m["id"],
        "thread_id": m.get("conversationId"),
        "from": sender,
        "subject": m.get("subject") or "",
        "date": m.get("receivedDateTime") or "",
        "labels": labels,
        "snippet": m.get("bodyPreview") or "",
        "body": text,
        "has_unsub": _is_bulk(m, text),
    }


def _batch(requests_: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in request("POST", f"{GRAPH}/$batch", json={"requests": requests_}).json()["responses"]}


def _run_batches(items: list[str], build: Callable[[str], dict], ok: Callable[[str, dict], None],
                 after_batch: Callable[[int, int], None] | None = None) -> list[str]:
    """Send items in batches of 20 and retry throttled sub-requests (429), honoring
    Graph's Retry-After. Returns the ids that failed for good."""
    todo = items
    done = 0
    for attempt in range(6):
        failed: list[str] = []
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            wait = 0.0
            try:
                res = _batch([{"id": str(n), **build(x)} for n, x in enumerate(chunk)])
            except Exception as e:
                key = f"batch:{str(e)[:60]}"
                STATS[key] = STATS.get(key, 0) + 1
                failed += chunk
                time.sleep(2)
                continue
            n_ok = 0
            for n, x in enumerate(chunk):
                r = res.get(str(n))
                if r and r["status"] < 300:
                    ok(x, r["body"])
                    n_ok += 1
                else:
                    k = str(r["status"]) if r else "missing"
                    STATS[k] = STATS.get(k, 0) + 1
                    failed.append(x)
                    if r and r["status"] == 429:
                        wait = max(wait, float((r.get("headers") or {}).get("Retry-After", 2)))
            done += n_ok
            if after_batch:
                after_batch(done, len(items))
            if wait:
                time.sleep(min(wait, 30))  # slow down as soon as Graph throttles
        if not failed:
            return []
        todo = failed
        time.sleep(min(2 ** attempt, 30))
    return todo


def fetch_details(ids: list[str], on_progress: Callable[[int, int], None]) -> tuple[list[dict], list[str]]:
    """Fetch mails in $batch groups of 20, several groups in parallel (Outlook allows only 4 concurrent requests)."""
    token()  # refresh the token before starting threads
    chunks = [ids[i:i + BATCH * 5] for i in range(0, len(ids), BATCH * 5)]
    emails: list[dict] = []
    failed: list[str] = []
    done = 0
    lock = threading.Lock()

    def work(chunk):
        nonlocal done
        got: list[dict] = []
        bad = _run_batches(
            chunk,
            lambda i: {"method": "GET", "url": f"/me/messages/{i}?$select={SELECT}", "headers": {"Prefer": PREFER}},
            lambda i, body: got.append(_parse(body)),
        )
        with lock:
            emails.extend(got)
            failed.extend(bad)
            done += len(chunk)
            on_progress(done, len(ids))

    with ThreadPoolExecutor(max_workers=GRAPH_FETCH_CONCURRENCY) as pool:
        list(pool.map(work, chunks))
    return emails, failed


def move(ids: list[str], destination: str,
         on_batch: Callable[[dict[str, str], int, int], None] | None = None) -> tuple[dict[str, str], list[str]]:
    """Move mails (`deleteditems` = trash, `inbox`). Returns ({old_id: new_id}, failures).
    `on_batch(moved_in_batch, total_moved, total)` is called after each batch so everything is recorded as it goes."""
    moved: dict[str, str] = {}
    pending: dict[str, str] = {}

    def after(done: int, total: int) -> None:
        if on_batch and pending:
            batch = dict(pending)
            pending.clear()
            on_batch(batch, done, total)

    def ok(i: str, body: dict) -> None:
        moved[i] = body["id"]
        pending[i] = body["id"]

    failed = _run_batches(
        ids,
        lambda i: {"method": "POST", "url": f"/me/messages/{i}/move", "body": {"destinationId": destination},
                   "headers": {"Content-Type": "application/json", "Prefer": PREFER}},
        ok, after,
    )
    return moved, failed


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().strip().lower()


def match_folder(folders: list[dict], name: str) -> str | None:
    """Id of the folder whose name matches (ignoring case, accents and a trailing \"s\")."""
    n = _norm(name)
    for f in folders:
        if _norm(f.get("displayName")) in (n, n + "s", n.rstrip("s")):
            return f["id"]
    return None


def find_folder(name: str) -> str | None:
    """Look for a folder at the mailbox root or directly under the inbox."""
    folders: list[dict] = []
    for url in (f"{GRAPH}/me/mailFolders", f"{GRAPH}/me/mailFolders/inbox/childFolders"):
        folders += request("GET", url, params={"$top": 100, "$select": "id,displayName"}).json().get("value", [])
    return match_folder(folders, name)


def flag(ids: list[str], on_batch: Callable[[list[str], int, int], None] | None = None) -> tuple[list[str], list[str]]:
    """Set a follow-up flag on mails. Returns (flagged ids, failures)."""
    flagged: list[str] = []
    pending: list[str] = []

    def ok(i: str, body: dict) -> None:
        flagged.append(i)
        pending.append(i)

    def after(done: int, total: int) -> None:
        if on_batch and pending:
            batch = list(pending)
            pending.clear()
            on_batch(batch, done, total)

    failed = _run_batches(
        ids,
        lambda i: {"method": "PATCH", "url": f"/me/messages/{i}", "body": {"flag": {"flagStatus": "flagged"}},
                   "headers": {"Content-Type": "application/json", "Prefer": PREFER}},
        ok, after,
    )
    return flagged, failed
