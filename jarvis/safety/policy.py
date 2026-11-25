"""Permission levels, enforced in code (never left to the AI model)."""
from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass
from pathlib import Path


class Level(enum.IntEnum):
    FREE = 0    # runs
    ASK = 1     # spoken "yes" or a click
    CLICK = 2   # on-screen click only; voice cannot approve
    NEVER = 3   # refused


@dataclass(frozen=True)
class Decision:
    level: Level
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.level != Level.NEVER


class PathError(ValueError):
    pass


def resolve_in(path: str, allowed: list[Path]) -> Path:
    """Resolve `path` (expanding ~ and env vars, following symlinks) and require it to sit inside
    one of the allowed folders. Raises PathError otherwise."""
    if not path or "\x00" in path:
        raise PathError("empty or invalid path")
    raw = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    p = Path(raw)
    if not p.is_absolute():
        # relative paths are taken relative to the first allowed folder
        p = allowed[0] / p
    # \\server\share, \\?\ and device paths are never allowed
    if str(p).startswith("\\\\"):
        raise PathError("network and device paths are not allowed")
    try:
        real = p.resolve(strict=False)
    except OSError as e:
        raise PathError(str(e)) from e
    for base in allowed:
        try:
            real.relative_to(base)
            return real
        except ValueError:
            continue
    raise PathError(f"{real} is outside the folders Jarvis may use")


# Shell commands that are refused outright, even with a click.
NEVER_SHELL = [
    r"\bformat(-volume)?\b", r"\bdiskpart\b", r"\bbcdedit\b", r"\bcipher\s+/w", r"\bvssadmin\b.*\bdelete\b",
    r"\bwbadmin\b.*\bdelete\b", r"\bclear-disk\b", r"\binitialize-disk\b", r"\bremove-partition\b",
    r"\bset-mppreference\b", r"\badd-mppreference\b.*exclusion", r"\bdisable(-)?(windows)?(defender|firewall)",
    r"netsh\s+advfirewall\s+set.*\boff\b", r"\bcmdkey\b", r"\bvaultcmd\b", r"\bmimikatz\b",
    r"\bsam\b.*\bsave\b", r"reg(\.exe)?\s+(save|export).*\b(sam|security|system)\b",
    r"\bcredentialmanager\b", r"\bget-storedcredential\b", r"\bsc(\.exe)?\s+delete\b",
    r"\bremove-item\b.*\b(c:\\windows|c:\\program files|\\system32)", r"\brd\b.*\s/s\b.*c:\\(windows|users)\b",
    r"\bschtasks\b.*\/create\b", r"\bnew-localuser\b", r"\bnet\s+user\b.*\/add\b", r"\bshutdown\b.*\/[pf]\b",
    r"iex\s*\(|invoke-expression|downloadstring|frombase64string|-enc(odedcommand)?\b",
    r"\buac\b|consentpromptbehavior|enablelua",
]
_NEVER_SHELL_RE = re.compile("|".join(NEVER_SHELL), re.I)


def shell_decision(command: str) -> Decision:
    if not command.strip():
        return Decision(Level.NEVER, "empty command")
    if _NEVER_SHELL_RE.search(command):
        return Decision(Level.NEVER, "that command could damage Windows, security settings or saved passwords")
    return Decision(Level.CLICK, "runs a system command")


def url_ok(url: str) -> bool:
    return bool(re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I))
