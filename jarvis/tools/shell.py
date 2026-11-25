"""Run a PowerShell command (click-only approval; dangerous patterns refused in policy)."""
from __future__ import annotations

import subprocess

from .registry import ToolError


def run_powershell(command: str) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError("The command took longer than 60 seconds and was stopped.") from e
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    text = out if out else "(no output)"
    if r.returncode != 0:
        text += f"\nExit code {r.returncode}. {err[:600]}"
    return text[:2000]
