"""Compare classification before / after new criteria, on a COPY of the database (no mail is touched)."""
import asyncio, os, shutil, sqlite3, tempfile, collections
tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
shutil.copy("data/mail.db", tmp); os.environ["MAIL_DB"] = tmp
from app import classify, db, preferences
db.init()
groups = {
  "newsletter.example.com (to_read wanted)": "from_addr like '%newsletter.example.com%'",
  "editorial newsletters (should be newsletter)": "(from_addr like '%substack%' or from_addr like '%newsletter%')",
  "promo control (discard expected)": "(from_addr like '%promo%' or from_addr like '%deals%' or subject like '%-%%')",
  "invoice/confirmation control": "(subject like '%invoice%' or subject like '%confirmation%') and from_addr not like '%newsletter.example.com%'",
}
rows, tag = [], {}
for g, w in groups.items():
    for r in db._q(f"select * from emails where {w} order by random() limit 60"):
        rows.append(r); tag[r["id"]] = (g, r["category"])
m = asyncio.run(classify.run(rows, lambda *a, **k: None))
res = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    n = db.get(r["id"]); g, before = tag[r["id"]]
    view, rule = db.effective(n)
    res[g][f"{before} -> Jev: {n['category']}{'*' if n['needs_review'] else ''}  | displayed: {view}" + (f" ({rule})" if rule else "")] += 1
for g, c in res.items():
    print(f"\n{g} ({sum(c.values())} mails)")
    for k, v in c.most_common(): print(f"   {v:3d}  {k}")
print("\n(* = sent to 'to_review')", m["wall_s"], "s")
