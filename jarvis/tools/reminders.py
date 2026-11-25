"""Reminders stored in SQLite; the core checks for due ones every few seconds."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from ..safety.audit import Store
from .registry import ToolError


def parse_when(when: str, now: datetime | None = None) -> datetime:
    """Accepts '10 minutes', 'in 2 hours', '1 hour 30 minutes', '17:30', '5:30 pm', '5 pm'."""
    now = now or datetime.now()
    w = when.strip().lower().removeprefix("in ").strip()
    total = 0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", w):
        n = float(num)
        u = unit[0]
        total += n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
    if total:
        return now + timedelta(seconds=total)
    m = re.fullmatch(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", w)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            raise ToolError("That time doesn't exist.")
        due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return due
    raise ToolError("Tell me when, like 'in 10 minutes' or 'at 5:30 pm'.")


class Reminders:
    def __init__(self, store: Store) -> None:
        self.store = store

    def add(self, text: str, when: str) -> str:
        due = parse_when(when)
        self.store.execute("INSERT INTO reminders (due, text) VALUES (?, ?)", (due.timestamp(), text.strip()))
        day = "" if due.date() == datetime.now().date() else " tomorrow"
        return f"I'll remind you to {text.strip()} at {due.strftime('%I:%M %p').lstrip('0')}{day}."

    def due(self) -> list[tuple[int, str]]:
        rows = self.store.execute("SELECT id, text FROM reminders WHERE done = 0 AND due <= ?", (time.time(),))
        for rid, _ in rows:
            self.store.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
        return rows

    def pending(self) -> str:
        rows = self.store.execute("SELECT due, text FROM reminders WHERE done = 0 ORDER BY due LIMIT 10")
        if not rows:
            return "You have no reminders."
        return "; ".join(f"{datetime.fromtimestamp(d).strftime('%I:%M %p').lstrip('0')}: {t}" for d, t in rows)
