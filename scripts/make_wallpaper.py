"""Package the HUD for Lively: creates dist/Jarvis-HUD.zip (drag it onto Lively's window).

The zip contains your private wallpaper token, so don't share it.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis import config as C  # noqa: E402

FILES = ["index.html", "style.css", "app.js", "token.js", "LivelyInfo.json", "thumbnail.jpg"]


def main() -> None:
    cfg = C.load()
    C.write_wallpaper_token(int(cfg["ui"]["port"]))
    out = ROOT / "dist" / "Jarvis-HUD.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            p = C.WALLPAPER / name
            if p.exists():
                z.write(p, name)
    print(f"Created {out}")
    print("Open Lively Wallpaper and drag this file onto its window, then pick 'Jarvis HUD'.")


if __name__ == "__main__":
    main()
