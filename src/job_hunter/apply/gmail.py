"""Read one-time verification codes from Gmail when a career site requires signup.

Uses IMAP with a Gmail App Password (never the real password). If credentials
aren't configured, callers fall back to pausing and asking the user for the code.
Only the most recent unread message matching a sender/subject hint is scanned,
and we pull the first 4-8 digit code we find.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import time

_CODE_RE = re.compile(r"\b(\d{4,8})\b")


def configured() -> bool:
    return bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD"))


def fetch_code(sender_hint: str | None = None, timeout_s: int = 90, poll_s: int = 5) -> str | None:
    """Poll the inbox for a verification code. Returns the code or None on timeout."""
    if not configured():
        return None

    addr = os.environ["GMAIL_ADDRESS"]
    pw = os.environ["GMAIL_APP_PASSWORD"]
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com")
            M.login(addr, pw)
            M.select("INBOX")
            criteria = ["UNSEEN"]
            if sender_hint:
                criteria += ["FROM", sender_hint]
            typ, data = M.search(None, *criteria)
            ids = data[0].split()
            for msg_id in reversed(ids[-5:]):
                typ, msg_data = M.fetch(msg_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                body = _plain_text(msg)
                subject = str(msg.get("Subject", ""))
                m = _CODE_RE.search(subject) or _CODE_RE.search(body)
                if m:
                    M.store(msg_id, "+FLAGS", "\\Seen")
                    M.logout()
                    return m.group(1)
            M.logout()
        except Exception:  # noqa: BLE001 — transient IMAP errors, retry
            pass
        time.sleep(poll_s)
    return None


def _plain_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(errors="ignore")
    except Exception:  # noqa: BLE001
        return str(msg.get_payload())
