"""System tray icon: pause listening, open the log, quit."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw


def _icon(paused: bool) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (60, 90, 95, 255) if paused else (0, 229, 255, 255)
    d.ellipse((4, 4, 60, 60), outline=color, width=4)
    d.ellipse((18, 18, 46, 46), fill=color)
    return img


class Tray:
    def __init__(self, name: str, on_pause: Callable[[], bool], on_quit: Callable[[], None], log_path: Path) -> None:
        import pystray

        self._pystray = pystray
        self.on_pause = on_pause
        self.on_quit = on_quit
        self.log_path = log_path
        self.paused = False
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: "Resume listening" if self.paused else "Pause listening", self._toggle, default=True),
            pystray.MenuItem("Open log", lambda: os.startfile(str(self.log_path)) if self.log_path.exists() else None),
            pystray.MenuItem(f"Quit {name}", self._quit),
        )
        self.icon = pystray.Icon("jarvis", _icon(False), name, menu)

    def _toggle(self) -> None:
        self.paused = self.on_pause()
        self.icon.icon = _icon(self.paused)
        self.icon.update_menu()

    def _quit(self) -> None:
        self.on_quit()

    def start(self) -> None:
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
