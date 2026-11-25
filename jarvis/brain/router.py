"""Fast path: common commands are parsed with patterns, so they skip the slow AI model."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Route:
    kind: str                          # "tool" | "control"
    name: str                          # tool name, or control word ("stop", "sleep")
    args: dict[str, Any] = field(default_factory=dict)


FILLER = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[,!.]?\s*)?(?:please\s+|can you\s+|could you\s+|would you\s+|will you\s+|i want you to\s+)*", re.I)
NUM_WORDS = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100,
             "max": 100, "maximum": 100, "full": 100, "half": 50}


def normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[\"“”!?]", "", t)
    t = re.sub(r"\s+", " ", t)
    t = t.rstrip(". ,")
    t = FILLER.sub("", t)
    return t.removesuffix(" please").removesuffix(" for me").strip(" ,.")


def _num(s: str) -> int | None:
    s = s.strip().rstrip("%").replace(" percent", "").strip()
    if s.isdigit():
        return int(s)
    return NUM_WORDS.get(s)


def route(text: str) -> Route | None:
    t = normalize(text)
    if not t:
        return None

    # ---- control words ----
    if re.fullmatch(r"(stop|cancel|never ?mind|forget it|that'?s all|nothing|shut up|be quiet|quiet)", t):
        return Route("control", "stop")
    if re.fullmatch(r"(go to sleep|sleep|goodbye|good night|bye|dismissed|standby|stand by)", t):
        return Route("control", "sleep")

    # ---- time ----
    if re.search(r"\b(what(?:'s| is) the (time|date)|what time is it|what day is (it|today)|today'?s date)\b", t):
        return Route("tool", "tell_time")

    # ---- volume ----
    m = re.fullmatch(r"(?:set |turn |change )?(?:the )?(?:volume|sound)(?: level)? (?:to |at )?(.+)", t) or \
        re.fullmatch(r"(?:set |turn )(?:the )?(?:volume|sound) (?:up |down )?to (.+)", t)
    if m:
        word = m.group(1).strip()
        if word in ("up", "higher", "louder"):
            return Route("tool", "system_control", {"action": "volume_up"})
        if word in ("down", "lower", "quieter", "softer"):
            return Route("tool", "system_control", {"action": "volume_down"})
        n = _num(word)
        if n is not None and 0 <= n <= 100:
            return Route("tool", "system_control", {"action": "set_volume", "value": n})
    if re.fullmatch(r"(turn (it|the volume|the sound) up|louder|volume up|increase (the )?volume|make it louder)", t):
        return Route("tool", "system_control", {"action": "volume_up"})
    if re.fullmatch(r"(turn (it|the volume|the sound) down|quieter|softer|volume down|lower (the )?volume|decrease (the )?volume)", t):
        return Route("tool", "system_control", {"action": "volume_down"})
    if re.fullmatch(r"(mute|mute (the )?(sound|volume|audio|computer))", t):
        return Route("tool", "system_control", {"action": "mute"})
    if re.fullmatch(r"(unmute|unmute (the )?(sound|volume|audio|computer)|sound on)", t):
        return Route("tool", "system_control", {"action": "unmute"})

    # ---- media ----
    if re.fullmatch(r"(pause|play|resume|pause (the )?(music|song|video)|play (the )?(music|song)|resume (the )?(music|song|video))", t):
        return Route("tool", "system_control", {"action": "play_pause"})
    if re.fullmatch(r"(next|skip|next (song|track)|skip (this )?(song|track))", t):
        return Route("tool", "system_control", {"action": "next"})
    if re.fullmatch(r"(previous|go back|previous (song|track)|last (song|track))", t):
        return Route("tool", "system_control", {"action": "previous"})

    # ---- system ----
    if re.search(r"\b(system status|system report|status of (my|the) (pc|computer|system)|how(?:'s| is) (my|the) (pc|computer|laptop|system)( doing)?|battery level|how much (ram|memory|battery))\b", t):
        return Route("tool", "system_status")

    # ---- reminders ----
    m = re.fullmatch(r"remind me (?:to )?(.+?) (in \d.+|at \d.+)", t)
    if m:
        return Route("tool", "set_reminder", {"text": m.group(1).strip(), "when": m.group(2).strip()})
    m = re.fullmatch(r"remind me (in \d.+?|at \d[\d:]*(?: ?[ap]\.?m\.?)?) (?:to )?(.+)", t)
    if m:
        return Route("tool", "set_reminder", {"text": m.group(2).strip(), "when": m.group(1).strip()})
    if re.fullmatch(r"(what are my reminders|list (my )?reminders|any reminders|show (my )?reminders)", t):
        return Route("tool", "list_reminders")

    # ---- reports ----
    if re.search(r"\b(my (daily |social media |morning )?report|(give|read) me (the |my )?report|report on my (accounts|socials|social media)|check my (socials|social media|accounts))\b", t):
        return Route("tool", "get_report", {"account": "all"})
    m = re.search(r"\b(?:check|read|any new|do i have (?:any )?(?:new )?)\s*(?:my )?(email|emails|mail|inbox|gmail)\b", t)
    if m:
        return Route("tool", "get_report", {"account": "gmail"})
    m = re.search(r"\b(?:check|how(?:'s| is)) my (youtube|instagram|facebook|tiktok|linkedin|twitter|x)\b", t)
    if m:
        acct = {"twitter": "x"}.get(m.group(1), m.group(1))
        return Route("tool", "get_report", {"account": acct})

    # ---- web ----
    m = re.fullmatch(r"(?:search(?: the web| online| google)?(?: for)?|google|look up) (.+)", t)
    if m:
        return Route("tool", "web_search", {"query": m.group(1).strip()})

    # ---- folders ----  (before windows: "show me my downloads folder" is a folder, not a window)
    m = re.fullmatch(r"(?:open|show me|show|go to|take me to|bring up)(?: up)? (?:the |my )?(.+?) (?:folder|directory)", t) or         re.fullmatch(r"(?:open|show me|go to|take me to) (?:the |my )?(downloads|documents|desktop|pictures|videos|recycle bin|"
                     r"this pc|my computer|[a-z] drive|drive [a-z])", t)
    if m:
        return Route("tool", "open_folder", {"name": m.group(1).strip()})

    # ---- windows ----  (name "" or a pronoun = the active window, resolved by the caller)
    m = re.fullmatch(r"(maximi[sz]e|minimi[sz]e|restore|switch to|focus on|show me)(?: the| my)?(?: (.+?))?(?: window| app)?", t)
    if m:
        verb = m.group(1)
        action = "maximize" if verb.startswith("maxim") else "minimize" if verb.startswith("minim") else \
            "restore" if verb == "restore" else "focus"
        name = (m.group(2) or "").strip()
        if action != "focus" or name:
            return Route("tool", "window_control", {"action": action, "name": name})
    m = re.fullmatch(r"make (?:(?:the |my )?(.+?) )?(full ?screen|full window|full size|bigger|big|maximi[sz]ed|smaller|small|minimi[sz]ed)", t)
    if m:
        action = "minimize" if m.group(2).startswith(("small", "minim")) else "maximize"
        return Route("tool", "window_control", {"action": action, "name": (m.group(1) or "").strip()})

    # ---- apps ----
    if not re.search(r"\b(file|folder|document|the .+ in|and)\b", t):
        m = re.fullmatch(r"(?:open|launch|start up|fire up|bring up)(?: up)? (?:the |my )?(.+?)(?: app| application| program)?"
                         r"(?: (?:in |on )?(full ?screen|full window|full size|maximi[sz]ed|big|minimi[sz]ed))?", t)
        if m:
            name = re.sub(r" (app|application|program|window)$", "", m.group(1).strip())
            if m.group(2):
                return Route("tool", "open_app", {"name": name,
                                                  "window": "minimize" if m.group(2).startswith("minim") else "maximize"})
            return Route("tool", "open_app", {"name": name})
        m = re.fullmatch(r"(?:close|quit|exit|shut down|kill) (?:the |my )?(.+?)(?: app| application| program| window)?", t)
        if m and m.group(1) not in ("computer", "pc", "laptop", "windows", "system", "everything", "all"):
            return Route("tool", "close_app", {"name": m.group(1).strip()})

    return None


PRONOUNS = {"", "it", "this", "that", "them", "this one", "that one", "this window", "that window"}


def split_compound(text: str) -> list[str] | None:
    """'open notepad and maximize it' -> ['open notepad', 'maximize it'] when every part is a known command."""
    t = normalize(text)
    parts = [p.strip() for p in re.split(r",? (?:and then|and also|then|and) ", t) if p.strip()]
    if len(parts) < 2 or any(route(p) is None for p in parts):
        return None
    return parts
