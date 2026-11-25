"""Gmail and YouTube, read-only, through one Google sign-in."""
from __future__ import annotations

import re
from collections import Counter
from email.utils import parseaddr
from typing import Any

from ..config import DATA
from . import secrets

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CLIENT_SECRET = DATA / "google_client_secret.json"

# Sender domains for the notification-email fallback (X, TikTok, LinkedIn have no free API for this)
PLATFORM_SENDERS = {
    "X": ["x.com", "twitter.com"],
    "TikTok": ["tiktok.com"],
    "LinkedIn": ["linkedin.com"],
    "Facebook": ["facebookmail.com"],
    "Instagram": ["mail.instagram.com", "instagram.com"],
}


class NotConnected(Exception):
    pass


def sign_in() -> None:
    """Opens the browser for Google consent; stores the refresh token in Credential Manager."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.exists():
        raise NotConnected(f"Put your Google OAuth client file at {CLIENT_SECRET} first (see README).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    secrets.put("google", {"token": creds.to_json()})


def _creds():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    stored = secrets.get("google")
    if not stored:
        raise NotConnected("Google isn't connected.")
    import json

    creds = Credentials.from_authorized_user_info(json.loads(stored["token"]), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise NotConnected("Google sign-in expired. Run the Google setup again.") from e
        secrets.put("google", {"token": creds.to_json()})
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=_creds(), cache_discovery=False)


def _header(msg: dict[str, Any], name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name:
            return h.get("value", "")
    return ""


def gmail_data(notification_emails: bool = True) -> dict[str, Any]:
    svc = _service("gmail", "v1")
    inbox = svc.users().labels().get(userId="me", id="INBOX").execute()
    data: dict[str, Any] = {"unread_inbox": inbox.get("messagesUnread", 0)}

    recent = svc.users().messages().list(userId="me", q="is:unread newer_than:1d category:primary", maxResults=8).execute()
    items = []
    for m in recent.get("messages", [])[:8]:
        full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                          metadataHeaders=["From", "Subject"]).execute()
        name, addr = parseaddr(_header(full, "from"))
        items.append({"from": (name or addr)[:60], "subject": _header(full, "subject")[:120]})
    data["recent_unread"] = items
    data["recent_unread_total"] = recent.get("resultSizeEstimate", len(items))

    if notification_emails:
        counts: dict[str, int] = {}
        for platform, domains in PLATFORM_SENDERS.items():
            q = "newer_than:1d from:(" + " OR ".join(domains) + ")"
            res = svc.users().messages().list(userId="me", q=q, maxResults=100).execute()
            n = len(res.get("messages", []))
            if n:
                counts[platform] = n
        data["platform_notifications"] = counts
    return data


def youtube_data() -> dict[str, Any]:
    svc = _service("youtube", "v3")
    res = svc.channels().list(part="snippet,statistics", mine=True).execute()
    items = res.get("items", [])
    if not items:
        raise NotConnected("This Google account has no YouTube channel.")
    ch = items[0]
    st = ch.get("statistics", {})
    return {
        "channel": ch.get("snippet", {}).get("title", ""),
        "subscribers": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
        "views": int(st.get("viewCount", 0)),
        "videos": int(st.get("videoCount", 0)),
    }


def describe_gmail(d: dict[str, Any]) -> str:
    parts = [f"You have {d['unread_inbox']} unread emails in your inbox"]
    recent = d.get("recent_unread") or []
    if recent:
        senders = Counter(i["from"] for i in recent)
        top = ", ".join(f"{s}" + (f" ({n})" if n > 1 else "") for s, n in senders.most_common(3))
        parts.append(f"{d.get('recent_unread_total', len(recent))} new today, from {top}")
        first = re.sub(r"\s+", " ", recent[0]["subject"]).strip()
        if first:
            parts.append(f"the latest is about \"{first}\"")
    notes = d.get("platform_notifications") or {}
    if notes:
        parts.append("notification emails today: " + ", ".join(f"{n} from {p}" for p, n in notes.items()))
    return "; ".join(parts) + "."
