""""Apply" plan (flag / Newsletter folder / trash) and folder lookup. Temporary DB, nothing is sent to Outlook."""
import json, os, pathlib, tempfile
from datetime import datetime, timedelta, timezone

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAIL_DB"] = str(tmp / "t.db")
from app import db, outlook, preferences
preferences.FILE = tmp / "preferences.json"
preferences.FILE.write_text(json.dumps({"newsletter_max_age_days": 30, "newsletters": {}}))
db.init()
NOW = datetime.now(timezone.utc)
N = 0
def add(category, age=2, labels=None, review=0, action=0.0, inj=0.0, user=None, status="classified"):
    global N; N += 1
    db.insert_emails([{"id": f"e{N}", "thread_id": "t", "from": f"S{N} <s{N}@example.com>", "subject": f"s{N}",
                       "date": (NOW - timedelta(days=age)).strftime("%Y-%m-%dT%H:%M:%SZ"), "labels": labels or [], "snippet": "", "body": "", "has_unsub": False}])
    db._q("UPDATE emails SET status=?, category=?, confidence=0.95, needs_review=?, action_required=?, injection=?, user_category=? WHERE id=?",
          (status, category, review, action, inj, user, f"e{N}"))
    return f"e{N}"

reply = add("awaiting_reply", action=0.95)
reply_flagged = add("awaiting_reply", labels=["FLAGGED"], action=0.95)
reply_doubt = add("awaiting_reply", review=1, action=0.8)
reply_doubt_low = add("awaiting_reply", review=1, action=0.2)
reply_doubt_inj = add("awaiting_reply", review=1, action=0.9, inj=0.9)
news_recent = add("newsletter", age=3)
news_flagged = add("newsletter", age=3, labels=["FLAGGED"])
news_old = add("newsletter", age=45)
spam = add("discard")
invoice = add("important")
corrected = add("awaiting_reply", user="to_read", action=0.9)
moved_already = add("newsletter", status="filed")

p = db.plan(); ids = lambda k: {r["id"] for r in p[k]}
checks = []
def check(name, got, want):
    ok = got == want; checks.append(ok); print(("OK  " if ok else "FAIL"), name, "" if ok else f"-> {got} (expected {want})")

check("flag: confident awaiting reply", ids("flag"), {reply})
check("flag: uncertain awaiting reply (action >= 0.5, no injection)", ids("flag_uncertain"), {reply_doubt})
check("recent newsletters -> folder", ids("newsletters"), {news_recent})
check("trash: expired + discard", ids("trash"), {news_old, spam})
allp = ids("flag") | ids("flag_uncertain") | ids("newsletters") | ids("trash")
check("never touched: already flagged, invoices, corrected, already filed", allp & {reply_flagged, news_flagged, invoice, corrected, moved_already}, set())
check("eligible_for_trash == plan.trash", {r["id"] for r in db.eligible_for_trash()}, ids("trash"))

db.add_label([reply], "FLAGGED")
check("after flagging, the mail leaves the plan", reply in {r["id"] for r in db.plan()["flag"]}, False)
db.set_status([news_recent], "filed")
check("filed: absent from the plan and the board", (news_recent in {r["id"] for r in db.plan()["newsletters"]}, news_recent in {c["id"] for c in db.board()}), (False, False))
db.reconcile([])  # no unread mail left on the Outlook side
check("reconcile: a filed mail stays 'filed' (not read_elsewhere)", db.get(news_recent)["status"], "filed")

F = [{"id": "A", "displayName": "Inbox"}, {"id": "B", "displayName": "Newsletters"}, {"id": "C", "displayName": "Archive"}]
check("folder: 'Newsletter' matches 'Newsletters'", outlook.match_folder(F, "Newsletter"), "B")
check("folder: case- and accent-insensitive", outlook.match_folder([{"id": "X", "displayName": "NEWSLETTER"}], "newsletter"), "X")
check("folder: not found -> None", outlook.match_folder(F, "Promo"), None)
print(f"\n{sum(checks)}/{len(checks)} OK"); raise SystemExit(0 if all(checks) else 1)
