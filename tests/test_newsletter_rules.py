"""Newsletter rules: expiry, per-sender decisions, protections. Temporary DB and preferences."""
import json, os, pathlib, tempfile
from datetime import datetime, timedelta, timezone

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAIL_DB"] = str(tmp / "t.db")
from app import db, preferences
preferences.FILE = tmp / "preferences.json"
preferences.FILE.write_text(json.dumps({"newsletter_max_age_days": 30, "newsletters": {"newsletter.example.com": "keep"}}))
db.init()

NOW = datetime.now(timezone.utc)
def iso(days): return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
N = 0
def add(sender, category, age, labels=None, user=None, review=0, status="classified", inj=0.0):
    global N; N += 1
    db.insert_emails([{"id": f"e{N}", "thread_id": "t", "from": sender, "subject": f"s{N}", "date": iso(age), "labels": labels or [],
                       "snippet": "", "body": "", "has_unsub": True}])
    db._q("UPDATE emails SET status=?, category=?, confidence=0.95, injection=?, needs_review=?, user_category=?, spam=0.1 WHERE id=?",
          (status, category, inj, review, user, f"e{N}"))
    return f"e{N}"

def view(i): return db.effective(db.get(i))

checks = []
def check(name, got, want):
    ok = got == want; checks.append(ok); print(("OK  " if ok else "FAIL"), name, "" if ok else f"-> {got} (expected {want})")

a = add("Weekly <editor@weekly.example.com>", "newsletter", 29);  check("29-day-old newsletter stays newsletter", view(a)[0], "newsletter")
b = add("Weekly <editor@weekly.example.com>", "newsletter", 31);  check("31-day-old newsletter -> discard (expired)", view(b)[0], "discard")
c = add("Billing <f@powerco.example.com>", "important", 400); check("old invoice is never discarded", view(c)[0], "important")
d = add("Mary <mary@example.com>", "awaiting_reply", 90);          check("old awaiting reply is never discarded", view(d)[0], "awaiting_reply")
e = add("NL <contact@newsletter.example.com>", "discard", 3); check("Keep: Jev said discard -> newsletter", view(e)[0], "newsletter")
f = add("NL <contact@newsletter.example.com>", "to_read", 3);    check("Keep: recent to_read -> newsletter", view(f)[0], "newsletter")
g = add("NL <contact@newsletter.example.com>", "to_read", 45);   check("Keep + 45 days: age wins -> discard", view(g)[0], "discard")
preferences.set_decision("deals.example.com", "discard")
h = add("Deals <news@deals.example.com>", "newsletter", 2);      check("Discard: recent mail -> discard", view(h)[0], "discard")
i = add("Deals <news@deals.example.com>", "important", 2);       check("Discarding a sender does not touch an important mail", view(i)[0], "important")
j = add("Weekly <editor@weekly.example.com>", "newsletter", 31, user="to_read"); check("manual correction is never overridden", view(j)[0], "to_read")
k = add("Weekly <editor@weekly.example.com>", "newsletter", 2, review=1);       check("uncertain newsletter -> to_review", view(k)[0], "to_review")
l = add("Spam <spam@spam.example.com>", "newsletter", 40, review=1, inj=0.9);         check("injection: stays to_review even when expired", view(l)[0], "to_review")
m = add("Weekly <editor@weekly.example.com>", "newsletter", 31, labels=["FLAGGED"])
ids = {r["id"] for r in db.eligible_for_trash()}
check("eligible: expired + discard decision", ids >= {b, g, h}, True)
check("eligible: no recent, invoice, corrected, flagged or uncertain mail", ids & {a, c, d, e, f, i, j, k, l, m}, set())
n = add("Old <old@example.com>", "newsletter", 60, status="processed"); check("already processed mail is not eligible", n in {r['id'] for r in db.eligible_for_trash()}, False)
check("idempotent", [db.effective(db.get(x)) for x in (a, b, g)], [db.effective(db.get(x)) for x in (a, b, g)])

preferences.set_decision("deals.example.com", None)
check("decision removal", view(h)[0], "newsletter")
raw = json.loads(preferences.FILE.read_text()); check("file preserved after write", raw.get("newsletter_max_age_days"), 30)
s = {x["sender"]: x for x in db.senders()}
check("per-sender view: NL grouped, decision 'keep'", (s["contact@newsletter.example.com"]["decision"], s["contact@newsletter.example.com"]["total"]), ("keep", 3))
print(f"\n{sum(checks)}/{len(checks)} OK"); raise SystemExit(0 if all(checks) else 1)
