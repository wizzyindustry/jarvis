"""Approval dialogs.

ASK level: an on-screen Yes/No dialog *and* a spoken yes/no; whichever comes first.
CLICK level: the dialog only. Voice cannot approve, so a video or a person
in the room saying "yes" can't trigger a delete or a shell command.
No answer within the timeout counts as "no".
"""
from __future__ import annotations

import ctypes
import itertools
import re
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

MB_YESNO = 0x04
MB_ICONWARNING = 0x30
MB_DEFBUTTON2 = 0x100
MB_SYSTEMMODAL = 0x1000
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000
IDYES = 6
IDNO = 7
WM_COMMAND = 0x0111

# MessageBoxTimeoutW is exported by user32 and has been stable since XP (undocumented).
_MessageBoxTimeoutW = user32.MessageBoxTimeoutW
_MessageBoxTimeoutW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT, wintypes.WORD, wintypes.DWORD]
_MessageBoxTimeoutW.restype = ctypes.c_int
_FindWindowW = user32.FindWindowW
_FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_FindWindowW.restype = wintypes.HWND
_PostMessageW = user32.PostMessageW
_PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND

_counter = itertools.count(1)

YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|confirm(ed)?|approve(d)?|okay|ok)\b", re.I)
NO = re.compile(r"\b(no|nope|don'?t|stop|cancel|never ?mind|deny|denied)\b", re.I)


def parse_yes_no(text: str) -> bool | None:
    """True for yes, False for no, None if unclear. 'No' wins if both appear."""
    if NO.search(text):
        return False
    if YES.search(text):
        return True
    return None


class Dialog:
    """A Yes/No box with a timeout that can be closed from another thread."""

    def __init__(self, action: str, click_only: bool, timeout_s: int) -> None:
        self.title = f"Jarvis approval #{next(_counter)}"
        hint = "Voice can't approve this one: click Yes or No." if click_only else "Click, or say yes or no."
        self.text = f"Jarvis wants to:\n\n{action}\n\n{hint}\nNo answer in {timeout_s} s means No."
        self.timeout_ms = int(timeout_s * 1000)
        self.result: bool | None = None
        self.done = threading.Event()

    def show(self) -> None:
        flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
        r = _MessageBoxTimeoutW(None, self.text, self.title, flags, 0, self.timeout_ms)
        if self.result is None:
            self.result = r == IDYES  # timeout (32000) or No or closed -> False
        self.done.set()

    def close(self) -> None:
        """Dismiss the box after a spoken answer. A Yes/No box ignores WM_CLOSE (it has no Cancel),
        so press the button matching self.result instead."""
        hwnd = _FindWindowW(None, self.title)
        if not hwnd:
            return
        button = IDYES if self.result else IDNO
        _PostMessageW(hwnd, WM_COMMAND, button, user32.GetDlgItem(hwnd, button))
