"""One-shot messages across a redirect, carried in a short-lived signed cookie."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from prolyv.config import Settings
from prolyv.security import FLASH_COOKIE, Signer
from prolyv.web.cookies import queue_delete_cookie, set_cookie

FLASH_MAX_AGE = 120


def set_flash(response: Response, settings: Settings, signer: Signer, kind: str, text: str) -> None:
    set_cookie(response, settings, FLASH_COOKIE, signer.dumps("flash", {"kind": kind, "text": text}), FLASH_MAX_AGE)


def pop_flash(request: Request, settings: Settings, signer: Signer) -> dict | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    queue_delete_cookie(request, settings, FLASH_COOKIE)
    payload = signer.loads("flash", raw, FLASH_MAX_AGE)
    return payload if isinstance(payload, dict) and "text" in payload else None
