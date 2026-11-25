"""Volume, media keys, time, system status, web."""
from __future__ import annotations

import ctypes
import os
import time
import urllib.parse
from datetime import datetime

import psutil

from ..safety.policy import url_ok
from .registry import ToolError

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "mute": 0xAD, "stop": 0xB2}


def _press(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def _endpoint():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(percent: int) -> str:
    percent = max(0, min(100, int(percent)))
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(percent / 100, None)
    if percent > 0:
        ep.SetMute(0, None)
    return f"Volume set to {percent} percent."


def change_volume(delta: int) -> str:
    return set_volume(get_volume() + delta)


def set_mute(on: bool) -> str:
    _endpoint().SetMute(1 if on else 0, None)
    return "Muted." if on else "Sound is back on."


def media(action: str) -> str:
    if action not in VK:
        raise ToolError(f"Unknown media action {action}.")
    _press(VK[action])
    return {"play_pause": "Done.", "next": "Next track.", "previous": "Previous track.",
            "mute": "Toggled mute.", "stop": "Stopped."}[action]


def system_control(action: str, value: int | None = None) -> str:
    if action == "set_volume":
        if value is None:
            raise ToolError("Which volume level?")
        return set_volume(value)
    if action == "volume_up":
        return change_volume(value or 10)
    if action == "volume_down":
        return change_volume(-(value or 10))
    if action in ("mute", "unmute"):
        return set_mute(action == "mute")
    if action in VK:
        return media(action)
    raise ToolError(f"Unknown action {action}.")


def now_text() -> str:
    d = datetime.now()
    return d.strftime("It's %I:%M %p on %A, %B %d.").replace(" 0", " ")


def system_status() -> str:
    cpu = psutil.cpu_percent(interval=0.3)
    vm = psutil.virtual_memory()
    parts = [f"CPU at {cpu:.0f} percent", f"memory {vm.percent:.0f} percent used, {vm.available / 2**30:.1f} gigabytes free"]
    bat = psutil.sensors_battery()
    if bat is not None:
        plug = "charging" if bat.power_plugged else "on battery"
        parts.append(f"battery {bat.percent:.0f} percent, {plug}")
    for drive in ("C:\\", "D:\\"):
        try:
            du = psutil.disk_usage(drive)
            parts.append(f"drive {drive[0]} has {du.free / 2**30:.0f} gigabytes free")
        except OSError:
            pass
    up = time.time() - psutil.boot_time()
    parts.append(f"up for {int(up // 3600)} hours")
    return "; ".join(parts) + "."


def top_memory_apps(n: int = 3) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info["name"] or "").removesuffix(".exe")
            totals[name] = totals.get(name, 0) + p.info["memory_info"].rss / 2**30
        except (psutil.Error, AttributeError, TypeError):
            continue
    skip = {"System", "Registry", "Memory Compression", "svchost", "python", "ollama", "Idle"}
    return sorted(((k, v) for k, v in totals.items() if k not in skip), key=lambda kv: -kv[1])[:n]


def web_search(query: str) -> str:
    os.startfile("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Searching the web for {query}."


def open_url(url: str) -> str:
    if not url_ok(url):
        raise ToolError("That isn't a web address I can open.")
    os.startfile(url)
    return f"Opened {urllib.parse.urlparse(url).netloc}."
