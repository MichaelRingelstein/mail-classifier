"""Host / Origin safeguards of the local API. Temporary DB, no Outlook or Jev calls."""
import os, pathlib, tempfile
os.environ["MAIL_DB"] = str(pathlib.Path(tempfile.mkdtemp()) / "t.db")
from fastapi.testclient import TestClient
from app.main import app

checks = []
def check(name, got, want):
    ok = got == want; checks.append(ok); print(("OK  " if ok else "FAIL"), name, "" if ok else f"-> {got} (expected {want})")

with TestClient(app, base_url="http://127.0.0.1:8000") as c:
    check("GET /api/state from localhost", c.get("/api/state").status_code, 200)
    check("unknown Host rejected (DNS rebinding)", c.get("/api/state", headers={"Host": "evil.example"}).status_code, 403)
    check("POST without Origin accepted (curl, scripts)", c.post("/api/apply/undo").status_code != 403, True)
    check("POST with foreign Origin rejected", c.post("/api/apply", headers={"Origin": "https://evil.example"}).status_code, 403)
    check("POST with local Origin accepted", c.post("/api/apply/undo", headers={"Origin": "http://127.0.0.1:8000"}).status_code != 403, True)
    check("cross-site POST (Sec-Fetch-Site) rejected", c.post("/api/apply", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
    check("/docs disabled", c.get("/docs").status_code, 404)
raise SystemExit(0 if all(checks) else 1)
