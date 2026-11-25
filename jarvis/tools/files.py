"""File tools, limited to the folders listed in config.toml."""
from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from send2trash import send2trash

from ..safety.policy import PathError, resolve_in
from .registry import ToolError

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".css", ".xml", ".yaml", ".yml",
            ".toml", ".ini", ".bat", ".ps1"}
MAX_READ = 3000


class Files:
    def __init__(self, allowed: list[Path]) -> None:
        self.allowed = allowed

    def path(self, p: str) -> Path:
        try:
            return resolve_in(p, self.allowed)
        except PathError as e:
            raise ToolError(str(e)) from e

    def list_files(self, folder: str = "", pattern: str = "*") -> str:
        base = self.path(folder) if folder else self.allowed[0]
        if not base.is_dir():
            raise ToolError(f"{base} is not a folder.")
        items = sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        names = [p.name + ("\\" if p.is_dir() else "") for p in items if fnmatch.fnmatch(p.name.lower(), pattern.lower())]
        if not names:
            return f"No matching files in {base}."
        more = f" (and {len(names) - 30} more)" if len(names) > 30 else ""
        return f"{base}: " + ", ".join(names[:30]) + more

    def find_files(self, name: str) -> str:
        hits: list[str] = []
        for base in self.allowed:
            if not base.is_dir():
                continue
            for p in base.rglob(f"*{name}*"):
                hits.append(str(p))
                if len(hits) >= 15:
                    break
        return "Found: " + "; ".join(hits) if hits else f"No files named like {name}."

    def read_file(self, path: str) -> str:
        p = self.path(path)
        if not p.is_file():
            raise ToolError(f"{p} doesn't exist.")
        if p.suffix.lower() not in TEXT_EXT:
            raise ToolError("I can only read plain text files.")
        if p.stat().st_size > 2_000_000:
            raise ToolError("That file is too big to read aloud.")
        text = p.read_text(encoding="utf-8", errors="replace")
        cut = " [truncated]" if len(text) > MAX_READ else ""
        return text[:MAX_READ] + cut

    def write_file(self, path: str, content: str, overwrite: bool = False) -> str:
        p = self.path(path)
        if p.exists() and not overwrite:
            raise ToolError(f"{p.name} already exists.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Saved {p}."

    def move_file(self, source: str, destination: str) -> str:
        src, dst = self.path(source), self.path(destination)
        if not src.exists():
            raise ToolError(f"{src} doesn't exist.")
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise ToolError(f"{dst} already exists.")
        shutil.move(str(src), str(dst))
        return f"Moved to {dst}."

    def delete_file(self, path: str) -> str:
        p = self.path(path)
        if not p.exists():
            raise ToolError(f"{p} doesn't exist.")
        if p in self.allowed:
            raise ToolError("I won't delete one of the main folders.")
        send2trash(str(p))
        return f"Moved {p.name} to the Recycle Bin."
