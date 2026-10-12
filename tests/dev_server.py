"""Demo server: fake Outlook (120 mails), real Jev. `PYTHONPATH=. .venv/bin/python tests/dev_server.py`"""
import os, random, tempfile, time
os.environ.setdefault("MAIL_DB", os.path.join(tempfile.mkdtemp(), "demo.db"))
import uvicorn
from app import outlook

TPL = [
    ("ShopNow", "promo@shopnow.example.com", "-{n}% on the whole collection, this weekend only", "Enjoy our sale. Unsubscribe."),
    ("Mary Dunn", "mary@example.com", "Question about quote {n}", "Hi, can you confirm the budget before Thursday?"),
    ("PowerCo", "billing@powerco.example.com", "Your invoice #{n} is available", "Your invoice of ${n}.40 is available in your customer area."),
    ("Daily Business", "newsletter@newsletter.example.com", "Today's business essentials #{n}", "Summary of today's business news. Unsubscribe."),
    ("Social", "noreply@social.example.com", "{n} people viewed your profile", "See who viewed your profile. Unsubscribe."),
    ("John Smith", "john@example.com", "Re: Thursday meeting ({n})", "Hi, is Thursday 2pm OK? Let me know if that works, I need your answer today."),
    ("Airways", "booking@airways.example.com", "Confirmation of your flight AW{n}", "Here is your boarding pass, Paris to Lisbon."),
]
MAILS = {}
for i in range(120):
    name, addr, subj, body = TPL[i % len(TPL)]
    n = random.randint(10, 99)
    MAILS[f"m{i}"] = {"id": f"m{i}", "thread_id": "t", "from": f"{name} <{addr}>", "subject": subj.format(n=n),
        "date": f"2026-10-06T{8 + i // 60:02d}:{i % 60:02d}:00+00:00", "labels": [], "snippet": body[:40],
        "body": body.format(n=n), "has_unsub": "unsubscribe" in body.lower()}

def list_ids():
    time.sleep(0.4); return list(MAILS)
def fetch(ids, on_progress):
    out = []
    for k in range(0, len(ids), 25):
        time.sleep(0.25); out += [MAILS[i] for i in ids[k:k + 25]]; on_progress(min(k + 25, len(ids)), len(ids))
    return out, []
outlook.list_unread_ids, outlook.fetch_details = list_ids, fetch
def fake_move(ids, dest, on_batch=None):
    moved = {}
    for k in range(0, len(ids), 20):
        time.sleep(0.05); b = {i: i for i in ids[k:k + 20]}; moved.update(b)
        if on_batch: on_batch(b, len(moved), len(ids))
    return moved, []
def fake_flag(ids, on_batch=None):
    for k in range(0, len(ids), 20):
        time.sleep(0.05)
        if on_batch: on_batch(ids[k:k + 20], min(k + 20, len(ids)), len(ids))
    return list(ids), []
outlook.move, outlook.flag = fake_move, fake_flag
outlook.find_folder = lambda name: "FAKE-FOLDER" if os.environ.get("DEMO_FOLDER", "1") == "1" else None

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8766, log_level="warning")
