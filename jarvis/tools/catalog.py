"""Every tool Jarvis has, with its permission level."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..safety.policy import Decision, Level, PathError, resolve_in, shell_decision
from . import apps, system
from .files import Files
from .registry import Registry, Tool
from .reminders import Reminders
from .shell import run_powershell


def _path_decider(allowed: list[Path], level: Level, *keys: str) -> Callable[[dict[str, Any]], Decision]:
    def decide(args: dict[str, Any]) -> Decision:
        for k in keys:
            if k in args and args[k]:
                try:
                    resolve_in(str(args[k]), allowed)
                except PathError as e:
                    return Decision(Level.NEVER, str(e))
        return Decision(level)

    return decide


def build(allowed: list[Path], reminders: Reminders, report_fn: Callable[[str], str]) -> Registry:
    files = Files(allowed)
    r = Registry()
    s = {"type": "string"}

    r.add(Tool("open_app", "Open an installed application or a well-known website by name, e.g. 'Notepad', 'Spotify', 'YouTube'.",
               {"name": {**s, "maxLength": 80},
                "window": {**s, "enum": ["normal", "maximize", "minimize"], "description": "maximize = full window"}},
               ["name"], Level.FREE, apps.open_app, describe=lambda a: f"Open {a['name']}"))
    r.add(Tool("open_folder", "Open a folder in File Explorer: Downloads, Documents, Desktop, Pictures, Music, Videos, "
                              "This PC, Recycle Bin, a drive like 'D drive', or a folder inside the user's folders.",
               {"name": {**s, "maxLength": 300}}, ["name"], Level.FREE,
               lambda name: apps.open_folder(name, allowed), describe=lambda a: f"Open folder {a['name']}"))
    r.add(Tool("window_control", "Maximize (full window), minimize, restore or focus a window. Leave name empty for the active window.",
               {"action": {**s, "enum": ["maximize", "minimize", "restore", "focus"]}, "name": {**s, "maxLength": 80}},
               ["action"], Level.FREE, apps.window_control))
    r.add(Tool("close_app", "Close the open windows of an application (it may still ask to save).",
               {"name": {**s, "maxLength": 80}}, ["name"], Level.ASK, apps.close_app,
               describe=lambda a: apps.describe_close(a["name"])))
    r.add(Tool("web_search", "Search the web in the user's browser.",
               {"query": {**s, "maxLength": 300}}, ["query"], Level.FREE, system.web_search,
               describe=lambda a: f"Search the web for: {a['query']}"))
    r.add(Tool("open_url", "Open a web address (http or https) in the browser.",
               {"url": {**s, "maxLength": 500}}, ["url"], Level.FREE, system.open_url,
               describe=lambda a: f"Open {a['url']}"))
    r.add(Tool("system_control", "Change volume or control media playback.",
               {"action": {**s, "enum": ["set_volume", "volume_up", "volume_down", "mute", "unmute",
                                        "play_pause", "next", "previous", "stop"]},
                "value": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Volume percent or step"}},
               ["action"], Level.FREE, system.system_control))
    r.add(Tool("system_status", "Report CPU, memory, battery, disk space and uptime.",
               {}, [], Level.FREE, system.system_status))
    r.add(Tool("set_reminder", "Set a spoken reminder. 'when' is like 'in 10 minutes' or '5:30 pm'.",
               {"text": {**s, "maxLength": 200}, "when": {**s, "maxLength": 40}}, ["text", "when"], Level.FREE,
               reminders.add))
    r.add(Tool("list_files", "List files in one of the user's folders (Documents, Desktop, Downloads...).",
               {"folder": {**s, "maxLength": 300}, "pattern": {**s, "maxLength": 50, "description": "e.g. *.pdf"}},
               [], Level.FREE, files.list_files, decide=_path_decider(allowed, Level.FREE, "folder")))
    r.add(Tool("find_files", "Find files by part of their name in the user's folders.",
               {"name": {**s, "maxLength": 80}}, ["name"], Level.FREE, files.find_files))
    r.add(Tool("read_file", "Read a plain-text file from the user's folders.",
               {"path": {**s, "maxLength": 300}}, ["path"], Level.FREE, files.read_file,
               decide=_path_decider(allowed, Level.FREE, "path"), untrusted_output=True))
    r.add(Tool("write_file", "Create a new text file (a note, a list) in the user's folders.",
               {"path": {**s, "maxLength": 300}, "content": {**s, "maxLength": 20000}}, ["path", "content"],
               Level.ASK, files.write_file, decide=_path_decider(allowed, Level.ASK, "path"),
               describe=lambda a: f"Create file {a['path']} ({len(a['content'])} characters)"))
    r.add(Tool("move_file", "Move or rename a file inside the user's folders.",
               {"source": {**s, "maxLength": 300}, "destination": {**s, "maxLength": 300}}, ["source", "destination"],
               Level.ASK, files.move_file, decide=_path_decider(allowed, Level.ASK, "source", "destination"),
               describe=lambda a: f"Move {a['source']} to {a['destination']}"))
    r.add(Tool("delete_file", "Move a file to the Recycle Bin.",
               {"path": {**s, "maxLength": 300}}, ["path"], Level.CLICK, files.delete_file,
               decide=_path_decider(allowed, Level.CLICK, "path"),
               describe=lambda a: f"Move to Recycle Bin: {a['path']}"))
    r.add(Tool("type_text", "Type text into the window that currently has focus.",
               {"text": {**s, "maxLength": 2000}}, ["text"], Level.ASK, apps.type_text,
               describe=lambda a: f"Type into '{apps.foreground_title()}': {a['text'][:200]}"))
    r.add(Tool("run_powershell", "Run a PowerShell command. Only when no other tool fits.",
               {"command": {**s, "maxLength": 1000}}, ["command"], Level.CLICK, run_powershell,
               decide=lambda a: shell_decision(a.get("command", "")),
               describe=lambda a: f"Run PowerShell command:\n{a['command']}"))
    r.add(Tool("get_report", "Get the user's email and social media report. account: all, gmail, youtube, instagram, facebook, x, tiktok, linkedin.",
               {"account": {**s, "enum": ["all", "gmail", "youtube", "instagram", "facebook", "x", "tiktok", "linkedin"]}},
               [], Level.FREE, lambda account="all": report_fn(account), untrusted_output=True))
    # router-only helpers
    r.add(Tool("tell_time", "Current time and date.", {}, [], Level.FREE, system.now_text, for_model=False))
    r.add(Tool("list_reminders", "List pending reminders.", {}, [], Level.FREE, reminders.pending, for_model=False))
    return r


# Free tools that send data out of the PC. After Jarvis has read outside text (a file,
# an email report) in the same request, these need approval: that text could be trying
# to make Jarvis leak something through a URL.
ESCALATE_WHEN_TAINTED = {"open_url", "web_search", "open_app"}
