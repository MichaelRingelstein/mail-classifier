"""Safeguards for an unauthenticated local API: only pages served by the app itself may drive it.

- Host: rejects unknown names (protects against DNS rebinding).
- Origin / Sec-Fetch-Site: rejects state-changing requests (POST, PATCH...) coming from another site,
  e.g. a web page that would send a POST /api/apply to 127.0.0.1.
Clients without these headers (curl, local scripts) are still accepted.
"""
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse

from .config import ALLOWED_HOSTS

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


class LocalOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        reason = None
        if _hostname(headers.get("host", "")) not in ALLOWED_HOSTS:
            reason = "Host not allowed"
        elif scope["method"] not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None and _hostname(origin.removeprefix("http://").removeprefix("https://")) not in ALLOWED_HOSTS:
                reason = "Origin not allowed"
            elif headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                reason = "Cross-site request refused"
        if reason:
            return await PlainTextResponse(reason, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)
