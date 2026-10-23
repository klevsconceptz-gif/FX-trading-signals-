"""Outbound e-mail through the Cloudflare Worker's ``send_email`` binding.

The container has no mail server of its own. It posts the message to the
Worker's token-protected ``/__internal/email`` route, and the Worker sends it
with Cloudflare Email Service. The Worker answers ``202`` when the message is
accepted, ``400`` for a malformed request, and ``502`` when the send fails.

Environment (set by the Worker, see ``edge/wrangler.jsonc``):

    KLEVS_EMAIL_URL         full URL of the relay, e.g. https://<origin>/__internal/email
    KLEVS_INTERNAL_TOKEN    shared secret; sent as the ``X-Internal-Token`` header

Message bodies, recipients, and the relay's error text are never logged.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("klevs.mail")

TIMEOUT_SECONDS = 10
MAX_SUBJECT = 200
MAX_BODY = 10_000


def _url() -> str:
    return os.environ.get("KLEVS_EMAIL_URL", "").strip()


def _token() -> str:
    return os.environ.get("KLEVS_INTERNAL_TOKEN", "").strip()


def configured() -> bool:
    return bool(_url() and _token())


def send(to: str, subject: str, text: str) -> bool:
    """Send one plain-text message. Returns True only when the relay accepted it (202)."""
    url, token = _url(), _token()
    if not url or not token:
        log.warning("e-mail not sent: the e-mail relay is not configured (KLEVS_EMAIL_URL / KLEVS_INTERNAL_TOKEN)")
        return False
    if len(subject) > MAX_SUBJECT or len(text) > MAX_BODY or not to:
        log.error("e-mail not sent: message failed local validation")
        return False
    body = json.dumps({"to": to, "subject": subject, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-internal-token": token,
            "user-agent": "klevs-fx/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        log.error("e-mail relay refused the message (HTTP %s)", exc.code)
        return False
    except (OSError, ValueError):
        log.error("e-mail relay could not be reached")
        return False
    if status != 202:
        log.error("e-mail relay answered HTTP %s, expected 202", status)
        return False
    return True
