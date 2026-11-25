"""Load config.toml with defaults and resolve project paths."""
from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODELS = ROOT / "models"
SPEECH = MODELS / "speech"
WALLPAPER = ROOT / "wallpaper"

DEFAULTS: dict[str, dict[str, Any]] = {
    "assistant": {"name": "Jarvis", "language": "en", "greetings": ["Hello boss, what can we do today?"],
                  "startup_greeting": "Good {part}, boss. Jarvis is online and all systems are running."},
    "location": {"auto": True, "refresh_minutes": 30, "name": "", "lat": None, "lon": None},
    "wake": {"wake_word": True, "wake_threshold": 0.5, "clap": True, "clap_sensitivity": 0.6, "cooldown": 2.0},
    "listen": {"max_seconds": 12, "start_timeout": 5, "silence_seconds": 0.9, "active_seconds": 60,
               "first_seconds": 20, "max_turns": 30},
    "brain": {
        "ollama_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b", "fallback_model": "qwen3.5:2b",
        "min_free_gb": 4.0, "fallback_min_free_gb": 2.6, "keep_alive": "30m", "warm_on_wake": True, "num_ctx": 4096, "num_batch": 64,
        "max_steps": 6, "request_timeout": 240,
    },
    "voice": {"piper_voice": "en_GB-alan-medium", "speed": 1.0},
    "files": {"allowed": ["~/Documents", "~/Desktop", "~/Downloads"]},
    "safety": {"approval_timeout": 30},
    "ui": {"port": 8765},
    "apps": {"open_window": "maximize"},
    "presence": {"enabled": True, "margin": 16},
    "reports": {
        "daily_time": "", "gmail": False, "youtube": False, "meta": False, "x": False,
        "tiktok": False, "linkedin": False, "notification_emails": True,
    },
}


@dataclass
class Config:
    raw: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self.raw[section]

    @property
    def allowed_dirs(self) -> list[Path]:
        out = []
        for p in self.raw["files"]["allowed"]:
            out.append(Path(os.path.expandvars(os.path.expanduser(p))).resolve())
        return out


def load(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    user: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as f:
            user = tomllib.load(f)
    merged: dict[str, dict[str, Any]] = {}
    for section, defaults in DEFAULTS.items():
        merged[section] = {**defaults, **(user.get(section) or {})}
    return Config(merged)


def ui_token() -> str:
    """Stable random token shared with the wallpaper page (stored in data/ and wallpaper/token.js)."""
    DATA.mkdir(exist_ok=True)
    tok_file = DATA / "ui_token"
    if tok_file.exists():
        token = tok_file.read_text().strip()
    else:
        token = secrets.token_urlsafe(24)
        tok_file.write_text(token)
    return token


def write_wallpaper_token(port: int, extra_dirs: list[Path] | None = None) -> None:
    token = ui_token()
    js = f'window.JARVIS = {{ token: "{token}", port: {int(port)} }};\n'
    for d in [WALLPAPER, *(extra_dirs or [])]:
        if d.is_dir():
            target = d / "token.js"
            if not target.exists() or target.read_text() != js:
                target.write_text(js)
