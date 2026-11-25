"""Builds the spoken report. All numbers come from code; nothing here asks the AI model."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from ..safety.audit import Store
from . import google, meta
from .google import NotConnected

log = logging.getLogger(__name__)

NO_API = {
    "x": "X charges for reading account data, so it's off. I count X notification emails in Gmail instead",
    "tiktok": "TikTok stats need an approved TikTok developer app, so it's off. I count TikTok notification emails in Gmail instead",
    "linkedin": "LinkedIn doesn't give personal profile stats to apps like me. I count LinkedIn notification emails in Gmail instead",
}


def _n(v: Any) -> str:
    return f"{v:,}" if isinstance(v, int) else str(v)


class Reports:
    def __init__(self, cfg, store: Store) -> None:
        self.cfg = cfg["reports"]
        self.store = store

    def _previous(self, account: str) -> dict[str, Any] | None:
        rows = self.store.execute(
            "SELECT data FROM reports WHERE account = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
            (account, time.time() - 12 * 3600))
        return json.loads(rows[0][0]) if rows else None

    def _save(self, account: str, data: dict[str, Any]) -> None:
        self.store.execute("INSERT INTO reports (ts, account, data) VALUES (?, ?, ?)",
                           (time.time(), account, json.dumps(data)))

    def _delta(self, account: str, data: dict[str, Any], key: str) -> str:
        prev = self._previous(account)
        if not prev or not isinstance(prev.get(key), int) or not isinstance(data.get(key), int):
            return ""
        d = data[key] - prev[key]
        if d == 0:
            return ", no change since last time"
        return f", {'up' if d > 0 else 'down'} {abs(d):,} since last time"

    # each returns one spoken sentence
    def _gmail(self) -> str:
        d = google.gmail_data(self.cfg.get("notification_emails", True))
        self._save("gmail", d)
        return google.describe_gmail(d)

    def _youtube(self) -> str:
        d = google.youtube_data()
        subs = "hidden subscribers" if d["subscribers"] is None else f"{_n(d['subscribers'])} subscribers"
        text = f"YouTube: {subs}{self._delta('youtube', d, 'subscribers')}, {_n(d['views'])} total views, {d['videos']} videos."
        self._save("youtube", d)
        return text

    def _instagram(self) -> str:
        d = meta.instagram_data()
        text = f"Instagram {d.get('username') or ''}: {_n(d.get('followers'))} followers{self._delta('instagram', d, 'followers')}"
        if d.get("reach") is not None:
            text += f", reached {_n(d['reach'])} accounts today"
        if d.get("total_interactions") is not None:
            text += f" with {_n(d['total_interactions'])} interactions"
        self._save("instagram", d)
        return text + "."

    def _facebook(self) -> str:
        d = meta.facebook_data()
        text = f"Facebook page {d.get('page') or ''}: {_n(d.get('followers'))} followers{self._delta('facebook', d, 'followers')}."
        self._save("facebook", d)
        return text

    def report(self, account: str = "all") -> str:
        fetchers: dict[str, tuple[str, Callable[[], str]]] = {
            "gmail": ("gmail", self._gmail),
            "youtube": ("youtube", self._youtube),
            "instagram": ("meta", self._instagram),
            "facebook": ("meta", self._facebook),
        }
        wanted = list(fetchers) if account == "all" else [account]
        out: list[str] = []
        for acct in wanted:
            if acct in NO_API:
                out.append(NO_API[acct] + ".")
                continue
            flag, fn = fetchers.get(acct, (None, None))
            if fn is None:
                out.append(f"I don't know an account called {acct}.")
                continue
            if not self.cfg.get(flag, False):
                if account != "all":
                    out.append(f"{acct.capitalize()} isn't turned on in my settings.")
                continue
            try:
                out.append(fn())
            except NotConnected as e:
                out.append(str(e))
            except Exception as e:
                log.exception("report %s failed", acct)
                out.append(f"I couldn't reach {acct.capitalize()} right now ({e.__class__.__name__}).")
        if not out:
            return "No accounts are connected yet. Run the setup in the README to connect Gmail, YouTube or Instagram."
        return " ".join(out)
