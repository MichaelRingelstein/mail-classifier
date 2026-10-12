import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import classify, db, outlook, preferences
from .security import LocalOnlyMiddleware
from .config import CATEGORIES, CONFIDENCE_THRESHOLD, JEV_CONCURRENCY, NEWSLETTER_FOLDER, REVIEW

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Mail Classifier Jev", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LocalOnlyMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# --- events of the current run (replayed when the browser reconnects) -----
EVENTS: list[dict] = []
RUN = {"id": 0, "running": False}
_ev_lock = threading.Lock()


def emit(type_: str, **data) -> None:
    with _ev_lock:
        EVENTS.append({"type": type_, "t": time.time(), **data})


@app.on_event("startup")
def startup() -> None:
    db.init()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/events")
async def events(request: Request):
    async def gen():
        run_id, i = RUN["id"], 0
        while not await request.is_disconnected():
            if run_id != RUN["id"]:
                run_id, i = RUN["id"], 0
            while i < len(EVENTS):
                yield f"data: {json.dumps(EVENTS[i])}\n\n"
                i += 1
            await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/state")
def state():
    return {
        "emails": db.board(),
        "running": RUN["running"],
        "categories": CATEGORIES,
        "review": REVIEW,
        "threshold": CONFIDENCE_THRESHOLD,
        "concurrency": JEV_CONCURRENCY,
        "max_age_days": preferences.load()["max_age_days"],
        "runs": db.runs(10),
        "pending": len(db.pending(False)),
        "agreement": db.agreement(),
    }


# --- two separate jobs: Outlook fetch, then Jev classification ----------------------

class ClassifyRequest(BaseModel):
    reclassify: bool = False
    concurrency: int = JEV_CONCURRENCY


async def fetch_job() -> None:
    """Fetches the new unread mails from Outlook and stores them, unclassified."""
    run_id = db.create_run("fetch", {})
    try:
        emit("sync_start")
        t0 = time.perf_counter()
        ids = await asyncio.to_thread(outlook.list_unread_ids)
        list_s = time.perf_counter() - t0
        known = db.known_ids()
        new_ids = [i for i in ids if i not in known]
        emit("sync_listed", unread=len(ids), new=len(new_ids), list_s=round(list_s, 2))

        t1 = time.perf_counter()
        failed: list[str] = []
        if new_ids:
            def progress(done, total):
                emit("sync_progress", done=done, total=total)  # emit is thread-safe

            emails, failed = await asyncio.to_thread(outlook.fetch_details, new_ids, progress)
            db.insert_emails(emails)
        fetch_s = time.perf_counter() - t1
        rec = db.reconcile(ids)
        metrics = {"unread": len(ids), "new": len(new_ids), "list_s": round(list_s, 2),
                   "fetch_s": round(fetch_s, 2), "failed": len(failed)}
        db.finish_run(run_id, "done", metrics)
        emit("sync_done", fetched=len(new_ids) - len(failed), failed=len(failed), fetch_s=round(fetch_s, 2),
             sync_s=round(time.perf_counter() - t0, 2), errors=dict(outlook.STATS), pending=len(db.pending(False)), **rec)
        emit("board", cards=db.board())  # mails that left the inbox disappear, new ones show up unclassified
    except Exception as e:
        db.finish_run(run_id, "error", {"error": str(e)})
        emit("run_error", message=f"{type(e).__name__}: {e}")
    finally:
        RUN["running"] = False
        emit("run_end")


async def classify_job(reclassify: bool, concurrency: int) -> None:
    """Classifies the stored mails with Jev (no Outlook call)."""
    run_id = db.create_run("classify", {"reclassify": reclassify, "concurrency": concurrency, "threshold": CONFIDENCE_THRESHOLD})
    try:
        rows = db.pending(reclassify)
        emit("rows", cards=[db.card(r, pending=True) for r in rows])
        emit("classify_start", total=len(rows), concurrency=concurrency)
        metrics = await classify.run(rows, emit, concurrency) if rows else {"mails": 0, "wall_s": 0}
        db.finish_run(run_id, "done", metrics)
        emit("classify_done", metrics=metrics, agreement=db.agreement())
    except Exception as e:
        db.finish_run(run_id, "error", {"error": str(e)})
        emit("run_error", message=f"{type(e).__name__}: {e}")
    finally:
        RUN["running"] = False
        emit("run_end")


@app.post("/api/fetch")
async def fetch():
    _start_job("fetch")
    asyncio.create_task(fetch_job())
    return {"started": True}


@app.post("/api/classify")
async def run_classify(req: ClassifyRequest):
    _start_job("classify")
    asyncio.create_task(classify_job(req.reclassify, max(1, min(req.concurrency, 256))))
    return {"started": True}


# --- per-sender decisions ---------------------------------------------------

class Decision(BaseModel):
    sender: str
    decision: str | None  # "keep", "discard" or null to remove the decision


@app.get("/api/senders")
def senders():
    return {"senders": db.senders(), "max_age_days": preferences.load()["max_age_days"]}


@app.post("/api/senders/decision")
def decide(body: Decision):
    if body.decision not in ("keep", "discard", None) or not body.sender.strip():
        raise HTTPException(422, "Invalid decision.")
    preferences.set_decision(body.sender, body.decision)  # applied to all mails at display time
    return {"eligible": len(db.eligible_for_trash())}


# --- manual corrections -----------------------------------------------------

class Correction(BaseModel):
    category: str | None


@app.patch("/api/emails/{email_id}")
def correct(email_id: str, body: Correction):
    if body.category is not None and body.category not in CATEGORIES:
        raise HTTPException(422, "Unknown category.")
    if not db.get(email_id):
        raise HTTPException(404)
    db.set_user_category(email_id, body.category)
    return {"card": db.card(db.get(email_id)), "agreement": db.agreement()}


@app.get("/api/emails/{email_id}")
def detail(email_id: str):
    r = db.get(email_id)
    if not r:
        raise HTTPException(404)
    return {**db.card(r), "body": r["body"], "probabilities": json.loads(r["probabilities"] or "null")}


# --- apply: flags, Newsletter folder, trash ---------------------------

_folder_cache: dict = {"t": 0.0, "id": None}


def newsletter_folder_id() -> str | None:
    """Id of the Newsletter folder (read-only lookup), cached: 60 s if found, 10 s otherwise so a newly created folder shows up quickly."""
    ttl = 60 if _folder_cache["id"] else 10
    if time.time() - _folder_cache["t"] > ttl:
        try:
            _folder_cache["id"] = outlook.find_folder(NEWSLETTER_FOLDER)
        except Exception:
            _folder_cache["id"] = None
        _folder_cache["t"] = time.time()
    return _folder_cache["id"]


@app.get("/api/apply/preview")
def preview():
    p = db.plan()
    folder_ok = newsletter_folder_id() is not None if p["newsletters"] else None
    flag = len(p["flag"]) + len(p["flag_uncertain"])
    return {
        "trash": len(p["trash"]), "newsletters": len(p["newsletters"]), "flag": flag, "flag_uncertain": len(p["flag_uncertain"]),
        "folder": NEWSLETTER_FOLDER, "folder_ok": folder_ok,
        "count": len(p["trash"]) + (len(p["newsletters"]) if folder_ok else 0) + flag,
        "subjects": [r["subject"] for r in p["trash"][:15]],
    }


def _start_job(name: str) -> None:
    if RUN["running"]:
        raise HTTPException(409, "A job is already running.")
    with _ev_lock:
        EVENTS.clear()
        RUN["id"] += 1
        RUN["running"] = True
    emit("run_start", job=name)


async def _move_step(kind: str, ids: list[str], destination: str, status_after: str, journal: bool,
                     offset: int, grand_total: int) -> tuple[int, int]:
    """Move mails in batches. Each batch is recorded (status + journal) as soon as it is done,
    so an interruption never loses track of mails that were already moved."""
    act = db.log_action(kind, []) if journal else None
    all_ids: list[str] = []

    def on_batch(batch: dict[str, str], done: int, total: int) -> None:
        for old, new in batch.items():
            if new != old:
                db.rename_id(old, new)
        db.set_status(list(batch.values()), status_after)
        if act:
            all_ids.extend(batch.values())
            db.update_action(act, all_ids)
        emit("apply_progress", done=offset + done, total=grand_total, kind=kind)

    moved, failed = await asyncio.to_thread(outlook.move, ids, destination, on_batch)
    return len(moved), len(failed)


async def apply_all(plan: dict, folder_id: str | None) -> None:
    flag_ids = [r["id"] for r in plan["flag"] + plan["flag_uncertain"]]
    news_ids = [r["id"] for r in plan["newsletters"]] if folder_id else []
    trash_ids = [r["id"] for r in plan["trash"]]
    total = len(flag_ids) + len(news_ids) + len(trash_ids)
    results: dict[str, dict] = {}
    t0 = time.perf_counter()
    try:
        emit("apply_start", total=total, kind="apply", counts={"flag": len(flag_ids), "newsletters": len(news_ids), "trash": len(trash_ids)})
        done = 0
        # 1. flags (safe, reversible); 2. Newsletter folder; 3. trash
        for kind in ("flag", "newsletters", "trash"):
            ids = {"flag": flag_ids, "newsletters": news_ids, "trash": trash_ids}[kind]
            if not ids:
                continue
            try:
                if kind == "flag":
                    def on_flag(batch: list[str], d: int, t: int) -> None:
                        db.add_label(batch, "FLAGGED")
                        emit("apply_progress", done=done + d, total=total, kind="flag")

                    ok, failed = await asyncio.to_thread(outlook.flag, ids, on_flag)
                    results[kind] = {"ok": len(ok), "failed": len(failed)}
                else:
                    dest, status = (folder_id, "filed") if kind == "newsletters" else ("deleteditems", "processed")
                    ok_n, failed_n = await _move_step(kind if kind == "newsletters" else "trash", ids, dest, status, True, done, total)
                    results[kind] = {"ok": ok_n, "failed": failed_n}
            except Exception as e:  # a failed step does not prevent the following ones
                results[kind] = {"ok": 0, "failed": len(ids), "error": f"{type(e).__name__}: {e}"}
            done += len(ids)
        emit("apply_done", kind="apply", results=results, seconds=round(time.perf_counter() - t0, 1),
             skipped_newsletters=len(plan["newsletters"]) if not folder_id else 0, errors=dict(outlook.STATS))
    except Exception as e:
        emit("run_error", message=f"{type(e).__name__}: {e}")
    finally:
        RUN["running"] = False
        emit("run_end")


@app.post("/api/apply")
async def apply():
    plan = db.plan()
    folder_id = newsletter_folder_id() if plan["newsletters"] else None
    if not (plan["trash"] or plan["flag"] or plan["flag_uncertain"] or (plan["newsletters"] and folder_id)):
        raise HTTPException(400, "Nothing to apply." if not plan["newsletters"] else
                            f"Folder \"{NEWSLETTER_FOLDER}\" not found: create it in Outlook (at the root or under the inbox).")
    _start_job("apply")
    asyncio.create_task(apply_all(plan, folder_id))
    return {"started": True}


async def undo_job(ids: list[str], undo_of: int) -> None:
    try:
        emit("apply_start", total=len(ids), kind="undo")
        t0 = time.perf_counter()
        moved, failed = await _move_step("undo", ids, "inbox", "classified", False, 0, len(ids))
        if not failed:
            db.mark_action_undone(undo_of)
        emit("apply_done", kind="undo", results={"undo": {"ok": moved, "failed": failed}}, seconds=round(time.perf_counter() - t0, 1),
             errors=dict(outlook.STATS))
    except Exception as e:
        emit("run_error", message=f"{type(e).__name__}: {e}")
    finally:
        RUN["running"] = False
        emit("run_end")


@app.post("/api/apply/undo")
async def undo():
    act = db.last_trash_action()
    ids = json.loads(act["email_ids"]) if act else []
    if not ids:
        raise HTTPException(400, "Nothing to undo.")
    _start_job("undo")
    asyncio.create_task(undo_job(ids, act["id"]))
    return {"started": True, "total": len(ids)}
