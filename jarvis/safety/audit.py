"""Action log and small persistent store (SQLite in data/jarvis.db)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY, ts REAL, source TEXT, tool TEXT, args TEXT,
    level TEXT, decision TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY, due REAL, text TEXT, done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY, ts REAL, account TEXT, data TEXT
);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        path = path or DATA / "jarvis.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def execute(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._db.execute(sql, params)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    def log_action(self, source: str, tool: str, args: dict[str, Any], level: str, decision: str, result: str) -> None:
        self.execute(
            "INSERT INTO actions (ts, source, tool, args, level, decision, result) VALUES (?,?,?,?,?,?,?)",
            (time.time(), source, tool, json.dumps(args, ensure_ascii=False)[:2000], level, decision, result[:2000]),
        )
