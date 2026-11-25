"""Jarvis's on-screen presence: a small animated core in the bottom-left corner.

When apps or folders cover the wallpaper, this keeps Jarvis visible. It is a
click-through, always-on-top layered window (mouse clicks pass straight through
to whatever is under it), drawn with Pillow and fed by the same bus events as
the wallpaper. It hides itself when the desktop is showing, since the wallpaper
already shows Jarvis there, and over full-screen apps while Jarvis is idle.
"""
from __future__ import annotations

import ctypes
import logging
import math
import re
import threading
import time
from ctypes import wintypes
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

CYAN = (0, 229, 255)
AMBER = (255, 176, 32)
DIM = (20, 90, 97)
ACTIVE = {"listening", "thinking", "speaking", "approval"}
LABEL = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "PROCESSING", "speaking": "SPEAKING",
         "approval": "AWAITING APPROVAL", "sleeping": "STANDBY", "offline": "OFFLINE"}
STAMP = re.compile(r"^\[\d{1,2}:\d{2} [AP]M\]\s*")
SHOW_MESSAGE_FOR = 7.0  # seconds a new line stays visible once Jarvis is idle again

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
ULW_ALPHA = 2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
HWND_TOPMOST = wintypes.HWND(-1)
DESKTOP_CLASSES = {"Progman", "WorkerW"}
IGNORE_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                  "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"}


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _monitor(hwnd: int | None) -> MONITORINFO:
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    hmon = user32.MonitorFromWindow(hwnd, 2) if hwnd else user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


# ---------------------------------------------------------------- drawing
class Painter:
    """Pre-renders the sprites once; each frame only rotates, scales and composites."""

    def __init__(self, scale: float) -> None:
        s = self.s = scale
        self.orb = round(118 * s)                  # square area holding the core and rings
        self.panel_w = round(270 * s)
        self.w = self.orb + self.panel_w
        self.h = self.orb
        self.font_head = _font(["bahnschrift.ttf", "seguisb.ttf", "segoeuib.ttf"], round(12 * s))
        self.font_text = _font(["segoeui.ttf", "arial.ttf"], round(13 * s))
        self._sprites: dict[tuple[str, tuple[int, int, int]], Image.Image] = {}
        self._panel_key: tuple | None = None
        self._panel: Image.Image | None = None

    def _ss(self, size: int, draw) -> Image.Image:
        """Draw at 3x, then shrink: smooth anti-aliased edges."""
        big = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
        draw(ImageDraw.Draw(big), size * 3)
        return big.resize((size, size), Image.LANCZOS)

    def sprite(self, kind: str, color: tuple[int, int, int]) -> Image.Image:
        key = (kind, color)
        if key in self._sprites:
            return self._sprites[key]
        n = self.orb
        r, g, b = color
        if kind in ("glow", "base"):
            img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            if kind == "glow":
                ImageDraw.Draw(img).ellipse((n * .3, n * .3, n * .7, n * .7), fill=(r, g, b, 150))
                img = img.filter(ImageFilter.GaussianBlur(n * .09))
            img.alpha_composite(self._ss(n, lambda dr, N: dr.ellipse((N * .07, N * .07, N * .93, N * .93),
                                                                      fill=(4, 14, 20, 150))))
        elif kind == "arcs":   # outer ring: four long arcs
            def d(dr, N):
                m = N * .06
                for a in (0, 90, 180, 270):
                    dr.arc((m, m, N - m, N - m), a + 8, a + 70, fill=(r, g, b, 235), width=round(N * .022))
            img = self._ss(n, d)
        elif kind == "ticks":  # middle ring: short dashes
            def d(dr, N):
                c, r1, r2 = N / 2, N * .36, N * .40
                for i in range(48):
                    if i % 12 in (5, 6):
                        continue
                    a = math.radians(i * 7.5)
                    dr.line((c + r1 * math.cos(a), c + r1 * math.sin(a), c + r2 * math.cos(a), c + r2 * math.sin(a)),
                            fill=(r, g, b, 170), width=round(N * .012))
            img = self._ss(n, d)
        elif kind == "scan":   # inner sweep used while thinking
            def d(dr, N):
                m = N * .2
                dr.arc((m, m, N - m, N - m), 0, 55, fill=(r, g, b, 230), width=round(N * .016))
                dr.arc((m, m, N - m, N - m), 180, 235, fill=(r, g, b, 230), width=round(N * .016))
            img = self._ss(n, d)
        elif kind == "core":
            def d(dr, N):
                c = N / 2
                for i in range(40, 0, -1):    # radial gradient: bright centre, deeper edge
                    t = i / 40
                    rr = N * .24 * t
                    col = tuple(int(v * (0.72 + 0.28 * (1 - t)) + (255 - v) * 0.18 * (1 - t)) for v in (r, g, b))
                    dr.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(*col, 255))
                rr = N * .24
                dr.ellipse((c - rr, c - rr, c + rr, c + rr), outline=(*[min(255, v + 60) for v in (r, g, b)], 255),
                           width=round(N * .01))
            img = self._ss(n, d)
        else:
            raise KeyError(kind)
        self._sprites[key] = img
        return img

    def panel(self, state: str, text: str, color: tuple[int, int, int]) -> Image.Image:
        key = (state, text, color)
        if key == self._panel_key and self._panel is not None:
            return self._panel
        s, w, h = self.s, self.panel_w, self.h
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = round(12 * s)
        top, bottom = round(h * .2), round(h * .8)
        d.rounded_rectangle((0, top, w - 2, bottom), radius=round(6 * s), fill=(4, 14, 20, 205),
                            outline=(*color, 140), width=max(1, round(s)))
        d.line((0, top + round(8 * s), 0, bottom - round(8 * s)), fill=(*color, 255), width=max(2, round(2 * s)))
        d.text((pad, top + round(7 * s)), f"JARVIS  ·  {LABEL.get(state, state.upper())}", font=self.font_head,
               fill=(*color, 255))
        lines = self._wrap(text, w - 2 * pad, 2)
        y = top + round(27 * s)
        for ln in lines:
            d.text((pad, y), ln, font=self.font_text, fill=(214, 251, 255, 245))
            y += round(17 * s)
        full = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        full.paste(img, (self.orb, 0))
        self._panel_key, self._panel = key, full
        return full

    def _wrap(self, text: str, width: int, max_lines: int) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for wd in words:
            trial = f"{cur} {wd}".strip()
            if self.font_text.getlength(trial) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = wd
            if len(lines) == max_lines:
                break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        if len(lines) == max_lines and " ".join(lines) != " ".join(words):
            last = lines[-1]
            while last and self.font_text.getlength(last + "…") > width:
                last = last[:-1]
            lines[-1] = last.rstrip() + "…"
        return lines

    def frame(self, t: float, state: str, level: float, text: str, show_panel: bool) -> Image.Image:
        color = AMBER if state == "approval" else DIM if state in ("sleeping", "offline") else CYAN
        n = self.orb
        orb = self.sprite("glow" if state in ACTIVE else "base", color).copy()
        spin = {"thinking": 3.0, "listening": 1.6, "speaking": 1.3}.get(state, 1.0)
        orb.alpha_composite(self.sprite("arcs", color).rotate(-(t * 22 * spin) % 360, Image.BILINEAR))
        orb.alpha_composite(self.sprite("ticks", color).rotate((t * 14 * spin) % 360, Image.BILINEAR))
        if state == "thinking":
            orb.alpha_composite(self.sprite("scan", color).rotate(-(t * 240) % 360, Image.BILINEAR))
        if state == "speaking":
            k = 1 + level * 0.22
        elif state == "listening":
            k = 1 + 0.07 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.2))
        elif state == "idle":
            k = 1 + 0.03 * math.sin(t * 2 * math.pi / 4)
        else:
            k = 1.0
        core = self.sprite("core", color)
        if abs(k - 1) > 0.004:
            m = max(2, round(n * k))
            core = core.resize((m, m), Image.BILINEAR)
            off = (m - n) // 2
            core = core.crop((off, off, off + n, off + n))  # same size; crop pads when shrinking
        orb.alpha_composite(core)
        out = self.panel(state, text, color).copy() if show_panel else Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        out.alpha_composite(orb)
        return out


# ---------------------------------------------------------------- window
class Presence:
    def __init__(self, bus: Any, config: dict | None = None) -> None:
        self.bus = bus
        cfg = config or {}
        self.margin = int(cfg.get("margin", 16))
        self.state = "idle"
        self.level = 0.0
        self.text = ""
        self.text_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsub = None

    # bus listener: runs on the asyncio loop, so only store values here
    def _on_event(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        with self._lock:
            if t == "state":
                self.state = ev.get("state", "idle")
                if self.state != "speaking":
                    self.level = 0.0
            elif t == "level":
                self.level = float(ev.get("value") or 0)
            elif t == "feed":
                text = STAMP.sub("", str(ev.get("text", ""))).strip()
                if ev.get("kind") == "user":
                    text = "You: " + text
                if text:
                    self.text, self.text_at = text[:300], time.monotonic()

    def start(self) -> None:
        self._unsub = self.bus.subscribe(self._on_event)
        self._thread = threading.Thread(target=self._run, name="presence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._unsub:
            self._unsub()

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("presence overlay stopped")

    def _create_window(self, w: int, h: int) -> int:
        import win32api
        import win32gui

        hinst = win32api.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "JarvisPresence"
        wc.hInstance = hinst
        wc.lpfnWndProc = {}  # default handling for every message
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass  # already registered
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        return user32.CreateWindowExW(ex, "JarvisPresence", "Jarvis presence", WS_POPUP, 0, 0, w, h,
                                      None, None, hinst, None)

    def _position(self, p: Painter) -> tuple[int, int]:
        mi = _monitor(None)
        work, mon = mi.rcWork, mi.rcMonitor
        bottom = work.bottom
        if work.bottom == mon.bottom:      # auto-hide taskbar: stay clear of where it pops up
            bottom -= round(52 * p.s)
        return work.left + round(self.margin * p.s) - round(10 * p.s), bottom - p.h - round(self.margin * p.s) + round(14 * p.s)

    def _covered(self) -> tuple[bool, bool]:
        """(an app is in front of the wallpaper, that app is full-screen)."""
        fg = user32.GetForegroundWindow()
        if not fg:
            return False, False
        cls = _class_name(fg)
        if cls in DESKTOP_CLASSES:
            return False, False
        if cls in IGNORE_CLASSES:          # taskbar / Start / tray: keep the previous answer
            return self._last_cover
        r = wintypes.RECT()
        user32.GetWindowRect(fg, ctypes.byref(r))
        mon = _monitor(fg).rcMonitor
        covers = r.left <= mon.left and r.top <= mon.top and r.right >= mon.right and r.bottom >= mon.bottom
        # a maximized window also fills the screen when the taskbar auto-hides; only true
        # full-screen (videos, games, F11) counts
        return True, covers and not user32.IsZoomed(fg)

    def _loop(self) -> None:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # crisp text on scaled screens
        except (AttributeError, OSError):
            pass
        dpi = 96
        try:
            dpi = user32.GetDpiForSystem() or 96
        except (AttributeError, OSError):
            pass
        p = Painter(dpi / 96)
        hwnd = self._create_window(p.w, p.h)
        if not hwnd:
            log.error("could not create the presence window")
            return
        import win32gui

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), p.w, -p.h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem_dc, hbmp)
        size = wintypes.SIZE(p.w, p.h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha

        self._last_cover = (False, False)
        opacity, shown = 0.0, False
        t0 = time.monotonic()
        next_check = next_top = 0.0
        want = False
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                win32gui.PumpWaitingMessages()
                with self._lock:
                    state, level, text, text_at = self.state, self.level, self.text, self.text_at
                if now >= next_check:
                    self._last_cover = self._covered()
                    covered, full = self._last_cover
                    want = covered and (not full or state in ACTIVE)
                    next_check = now + 0.4
                target = 1.0 if want else 0.0
                if not want and not shown:
                    self._stop.wait(0.25)
                    continue
                opacity += (target - opacity) * 0.3
                if abs(target - opacity) < 0.02:
                    opacity = target
                if opacity <= 0.0:
                    user32.ShowWindow(hwnd, SW_HIDE)
                    shown = False
                    continue
                if not shown:
                    x, y = self._position(p)
                    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                    shown = True
                elif now >= next_top:          # other apps can push themselves on top; reclaim it quietly
                    x, y = self._position(p)
                    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
                    next_top = now + 3.0
                show_panel = bool(text) and (state in ACTIVE or now - text_at < SHOW_MESSAGE_FOR)
                img = p.frame(now - t0, state, level, text, show_panel)
                if state not in ACTIVE:
                    opacity_mul = 0.8
                else:
                    opacity_mul = 1.0
                self._blit(img, bits, mem_dc, screen_dc, hwnd, size, src, blend, opacity * opacity_mul)
                fps = 24 if state in ACTIVE or opacity != target else 10
                self._stop.wait(1 / fps)
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            user32.DestroyWindow(hwnd)

    @staticmethod
    def _blit(img: Image.Image, bits, mem_dc, screen_dc, hwnd, size, src, blend, opacity: float) -> None:
        data = img.convert("RGBa").tobytes("raw", "BGRa")   # Windows wants premultiplied BGRA
        ctypes.memmove(bits, data, len(data))
        blend.SourceConstantAlpha = max(0, min(255, round(255 * opacity)))
        user32.UpdateLayeredWindow(hwnd, screen_dc, None, ctypes.byref(size), mem_dc, ctypes.byref(src), 0,
                                   ctypes.byref(blend), ULW_ALPHA)
