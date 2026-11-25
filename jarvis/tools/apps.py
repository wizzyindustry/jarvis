"""Open and close applications, type text."""
from __future__ import annotations

import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

import psutil
import win32con
import win32gui
import win32process

from ..config import DATA
from .registry import ToolError

log = logging.getLogger(__name__)

ALIASES = {
    "explorer": "File Explorer", "file explorer": "File Explorer", "files": "File Explorer",
    "my files": "File Explorer", "calc": "Calculator", "browser": "Brave", "web browser": "Brave",
    "control panel": "Control Panel", "terminal": "Terminal", "cmd": "Command Prompt",
    "command prompt": "Command Prompt", "task manager": "Task Manager", "word": "Word",
    "excel": "Excel", "powerpoint": "PowerPoint", "chrome": "Google Chrome", "vs code": "Visual Studio Code",
    "code": "Visual Studio Code", "camera": "Camera", "store": "Microsoft Store", "photos": "Photos",
}

# Things people call "apps" that are really websites.
WEBSITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com", "x": "https://x.com",
    "twitter": "https://x.com", "tiktok": "https://www.tiktok.com", "linkedin": "https://www.linkedin.com",
    "whatsapp web": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com", "github": "https://github.com",
    "netflix": "https://www.netflix.com", "maps": "https://maps.google.com", "google maps": "https://maps.google.com",
}

SYSTEM = {  # always available without Start-menu lookup
    "Task Manager": ["taskmgr.exe"],
    "Command Prompt": ["cmd.exe"],
    "Control Panel": ["control.exe"],
}

_CACHE = DATA / "apps.json"
_apps: dict[str, str] = {}
_lock = threading.Lock()


def refresh_apps() -> None:
    """Slow (~5 s): reads every Start menu entry via Get-StartApps."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        items = json.loads(out or "[]")
        apps = {i["Name"]: i["AppID"] for i in items if i.get("Name") and i.get("AppID")}
        if apps:
            with _lock:
                _apps.clear()
                _apps.update(apps)
            DATA.mkdir(exist_ok=True)
            _CACHE.write_text(json.dumps(apps, ensure_ascii=False))
    except Exception:
        log.exception("could not list Start menu apps")


def load_apps() -> None:
    if _CACHE.exists():
        try:
            _apps.update(json.loads(_CACHE.read_text()))
        except ValueError:
            pass
    threading.Thread(target=refresh_apps, daemon=True).start()


def find_app(name: str) -> tuple[str, str] | None:
    q = name.strip().lower().removesuffix(" app").strip()
    q = ALIASES.get(q, q)
    with _lock:
        names = list(_apps)
    lower = {n.lower(): n for n in names}
    if q.lower() in lower:
        n = lower[q.lower()]
        return n, _apps[n]
    starts = [n for n in names if n.lower().startswith(q.lower())]
    if starts:
        n = min(starts, key=len)
        return n, _apps[n]
    close = difflib.get_close_matches(q.lower(), list(lower), n=1, cutoff=0.75)
    if close:
        n = lower[close[0]]
        return n, _apps[n]
    # misheard single word against each word of an app name: "crome" -> Google Chrome
    if " " not in q and len(q) >= 4:
        words = {}
        for n in names:
            for w in re.findall(r"[a-z0-9]{4,}", n.lower()):
                words.setdefault(w, []).append(n)
        close = difflib.get_close_matches(q.lower(), list(words), n=1, cutoff=0.8)
        if close:
            n = min(words[close[0]], key=len)
            return n, _apps[n]
    return None


# How newly opened apps and folders appear: "maximize" (full window, in front) or "normal".
# Set from config.toml [apps] open_window.
DEFAULT_WINDOW = "maximize"


def open_app(name: str, window: str = "") -> str:
    window = window or DEFAULT_WINDOW
    before = {h for h, _, _ in _visible_windows()}
    key = ALIASES.get(name.strip().lower(), name.strip())
    if key in SYSTEM:
        subprocess.Popen(SYSTEM[key], creationflags=subprocess.CREATE_NO_WINDOW)
        label = key
    elif found := find_app(name):
        label, app_id = found
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
    elif site := WEBSITES.get(name.strip().lower()):
        os.startfile(site)
        label = f"{name} in your browser"
    else:
        raise ToolError(f"I couldn't find an app called {name}.")
    # a website usually opens as a tab whose title contains the site name ("YouTube")
    hint = name.strip() if label.endswith(" in your browser") else label
    _present_new_window(before, hint, window, label)
    return f"Opened {label}."


def _present_new_window(before: set[int], hint: str, window: str, label: str) -> None:
    """Find the window that just opened and bring it to the front (Windows leaves it in the taskbar otherwise)."""
    hwnd = _wait_for_window(before, hint)
    if not hwnd:
        log.warning("opened %s but found no window to bring forward", label)
        return
    if window in SW:
        win32gui.ShowWindow(hwnd, SW[window])
    if window != "minimize":
        bring_to_front(hwnd)


def bring_to_front(hwnd: int) -> None:
    """Windows refuses focus requests from background programs; attaching to the
    current foreground thread's input (and a harmless Alt tap) lets it through."""
    u = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    fg = u.GetForegroundWindow()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True))
    try:
        u.keybd_event(0x12, 0, 0, 0)            # Alt down
        u.keybd_event(0x12, 0, 2, 0)            # Alt up
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetActiveWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_thread, False)


# ---------- folders ----------
KNOWN_FOLDERS = {
    "documents": "shell:Personal", "my documents": "shell:Personal", "downloads": "shell:Downloads",
    "download": "shell:Downloads", "desktop": "shell:Desktop", "pictures": "shell:My Pictures",
    "photos": "shell:My Pictures", "music": "shell:My Music", "videos": "shell:My Video", "video": "shell:My Video",
    "this pc": "shell:MyComputerFolder", "my computer": "shell:MyComputerFolder", "computer": "shell:MyComputerFolder",
    "recycle bin": "shell:RecycleBinFolder", "trash": "shell:RecycleBinFolder", "home": "shell:Profile",
    "user": "shell:Profile", "onedrive": "shell:OneDrive",
}
_DRIVE = re.compile(r"^(?:drive )?([a-z])(?: drive|:)?$")


def open_folder(name: str, allowed: list | None = None, window: str = "") -> str:
    """Open a folder in File Explorer: a known folder, a drive, or a folder inside the allowed folders."""
    q = re.sub(r"\s+(folder|directory)$", "", name.strip().lower()).removeprefix("my ").strip()
    target = KNOWN_FOLDERS.get(q)
    label = q.title()
    if target is None and (m := _DRIVE.match(q)) and os.path.isdir(f"{m.group(1).upper()}:\\"):
        target, label = f"{m.group(1).upper()}:\\", f"the {m.group(1).upper()} drive"
    if target is None:
        from ..safety.policy import PathError, resolve_in

        roots = list(allowed or [])
        try:  # a full path inside an allowed folder
            p = resolve_in(name, roots) if roots else None
            if p is not None and p.is_dir():
                target, label = str(p), p.name
        except PathError:
            pass
        if target is None:  # a folder name one or two levels inside an allowed folder
            for root in roots:
                if root.name.lower() == q and root.is_dir():
                    target, label = str(root), root.name
                    break
                for depth in ("*", "*/*"):
                    hits = [d for d in root.glob(depth) if d.is_dir() and d.name.lower() == q]
                    if hits:
                        target, label = str(hits[0]), hits[0].name
                        break
                if target:
                    break
    if target is None:
        raise ToolError(f"I couldn't find a folder called {name}.")
    before = {h for h, _, _ in _visible_windows()}
    subprocess.Popen(["explorer.exe", target])
    hint = "" if target.startswith("shell:") else label  # Explorer titles windows "Downloads - File Explorer"
    _present_new_window(before, hint, window or DEFAULT_WINDOW, label)
    return f"Opened {label}."


# ---------- window control ----------
SW = {"maximize": win32con.SW_MAXIMIZE, "minimize": win32con.SW_MINIMIZE, "restore": win32con.SW_RESTORE}


def _show(hwnd: int, action: str) -> None:
    if action == "focus":
        bring_to_front(hwnd)
        return
    win32gui.ShowWindow(hwnd, SW[action])
    if action == "maximize":
        bring_to_front(hwnd)


def _wait_for_window(before: set[int], hint: str, timeout: float = 8.0) -> int | None:
    """After launching, find the app's window: a new window, or an existing one matching the name."""
    deadline = time.monotonic() + timeout
    hint_l = hint.lower()
    while time.monotonic() < deadline:
        time.sleep(0.3)
        wins = _visible_windows()
        new = [h for h, _, _ in wins if h not in before]
        if new:
            time.sleep(0.4)  # let it finish appearing
            return new[0]
        named = [h for h, t, p in wins if hint_l and (hint_l in t.lower() or hint_l.replace(" ", "") in p.lower())]
        if named and time.monotonic() > deadline - timeout + 2.0:
            return named[0]  # app was already open (single-instance apps like browsers reuse a window)
    return None


def window_control(action: str, name: str = "") -> str:
    """No name = the window you're using now."""
    if not name or name.strip().lower() in PRONOUNS:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            raise ToolError("There's no active window.")
        _show(hwnd, action)
        return f"Done, {action}d {win32gui.GetWindowText(hwnd)[:60] or 'the window'}."
    hits = match_windows(name)
    if not hits:
        raise ToolError(f"No open window matches {name}.")
    for hwnd, _, _ in hits[:5]:
        _show(hwnd, action)
    return f"Done, {action}d {name}."


# ---------- closing ----------
def _visible_windows() -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) and not win32gui.GetParent(hwnd):
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                return True
            out.append((hwnd, win32gui.GetWindowText(hwnd), proc))
        return True

    win32gui.EnumWindows(cb, None)
    return out


PROTECTED_PROCS = {"explorer.exe", "lively.exe", "lively.player.webview2.exe", "python.exe", "pythonw.exe",
                   "ollama.exe", "ollama app.exe", "csrss.exe", "winlogon.exe", "dwm.exe"}


PRONOUNS = {"it", "this", "that", "them", "these", "those", "everything", "all", "the app", "the window", "window"}


def match_windows(name: str) -> list[tuple[int, str, str]]:
    """Whole-word match on the window title or process name ('it' must never match 'with')."""
    q = ALIASES.get(name.strip().lower(), name.strip()).lower()
    if len(q) < 2 or q in PRONOUNS:
        raise ToolError("Which app should I close?")
    word = re.compile(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", re.I)
    compact = q.replace(" ", "")
    hits = []
    for hwnd, title, proc in _visible_windows():
        if proc.lower() in PROTECTED_PROCS:
            continue
        pname = proc.lower().removesuffix(".exe")
        if word.search(title) or pname == compact or (len(compact) >= 4 and pname.startswith(compact)):
            hits.append((hwnd, title, proc))
    return hits


def describe_close(name: str) -> str:
    try:
        hits = match_windows(name)
    except ToolError:
        return f"Close {name}"
    if not hits:
        return f"Close {name} (no open window matched)"
    titles = "; ".join(t for _, t, _ in hits[:3])
    return f"Close {len(hits)} window(s): {titles}"


def close_app(name: str) -> str:
    hits = match_windows(name)
    if not hits:
        raise ToolError(f"No open window matches {name}.")
    for hwnd, _, _ in hits:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)  # polite close: apps can still ask to save
    return f"Asked {len(hits)} window(s) of {name} to close."


# ---------- typing ----------
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def foreground_title() -> str:
    return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "the active window"


def type_text(text: str) -> str:
    send = ctypes.windll.user32.SendInput
    for ch in text:
        if ch == "\n":
            ch = "\r"
        code = ord(ch)
        down = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        arr = (INPUT * 2)(down, up)
        send(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.004)
    return f"Typed {len(text)} characters."
