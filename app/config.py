import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN_CACHE_FILE = Path(os.getenv("TOKEN_CACHE_FILE", ROOT / "token_cache.json"))
DB_FILE = Path(os.getenv("MAIL_DB", ROOT / "data" / "mail.db"))

JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
JEV_CONCURRENCY = int(os.getenv("JEV_CONCURRENCY", "20"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.8"))
INJECTION_THRESHOLD = float(os.getenv("INJECTION_THRESHOLD", "0.5"))
BODY_MAX_CHARS = int(os.getenv("BODY_MAX_CHARS", "2000"))
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_TENANT = os.getenv("MS_TENANT", "common")  # common, consumers (personal account) or organizations
OUTLOOK_FOLDER = os.getenv("OUTLOOK_FOLDER", "inbox")  # "all" = every folder
NEWSLETTER_FOLDER = os.getenv("NEWSLETTER_FOLDER", "Newsletter")  # Outlook folder (at the root or under the inbox)
FLAG_UNCERTAIN_REPLIES = os.getenv("FLAG_UNCERTAIN_REPLIES", "1") == "1"  # also flag the uncertain "awaiting reply" mails
GRAPH_FETCH_CONCURRENCY = int(os.getenv("GRAPH_FETCH_CONCURRENCY", "4"))
PRICE_PER_MTOK = float(os.getenv("PRICE_PER_MTOK", "0.042"))  # $ per million tokens, for the displayed cost estimate
# Hostnames accepted by the local API (see security.py); extend only if you expose it behind a proxy.
ALLOWED_HOSTS = {h.strip().lower() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost,::1").split(",") if h.strip()}

CATEGORIES = {
    "to_read": "To read",
    "awaiting_reply": "Awaiting reply",
    "important": "Important to keep",
    "newsletter": "Newsletters",
    "discard": "Discard",
}
REVIEW = "to_review"
