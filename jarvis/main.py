"""Jarvis entry point.

    .venv\\Scripts\\python.exe -m jarvis                 # normal: microphone, voice, wallpaper
    .venv\\Scripts\\python.exe -m jarvis --text "open notepad"   # one typed command, no microphone
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import queue
import random
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import numpy as np
import psutil

from . import config as C
from .audio.stt import only_wake_word
from .brain.ollama import Brain, BrainUnavailable
from .brain.router import PRONOUNS, route, split_compound
from .bus import Bus
from .reports.manager import Reports
from .safety.approve import Dialog, parse_yes_no
from .safety.audit import Store
from .tools import apps as apps_mod
from .tools.catalog import build
from .tools.executor import Executor
from .tools.reminders import Reminders

log = logging.getLogger("jarvis")


def setup_logging(verbose: bool) -> None:
    C.DATA.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(C.DATA / "jarvis.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "faster_whisper", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def startup_greeting(template: str, now: time.struct_time | None = None) -> str:
    """'Good {part} boss...' with {part} = morning/afternoon/evening and {time} = '8:15 AM'."""
    if not template:
        return ""
    now = now or time.localtime()
    h = now.tm_hour
    part = "morning" if 4 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening"
    return template.format(part=part, time=time.strftime("%I:%M %p", now).lstrip("0"),
                           day=time.strftime("%A", now))


class Jarvis:
    def __init__(self, cfg: C.Config, voice: bool = True, startup: bool = False) -> None:
        self.cfg = cfg
        self.startup = startup
        apps_mod.DEFAULT_WINDOW = cfg["apps"].get("open_window", "maximize")
        self.voice = voice
        self.name = cfg["assistant"]["name"]
        self.bus = Bus()
        self.store = Store()
        self.reminders = Reminders(self.store)
        self.reports = Reports(cfg, self.store)
        registry = build(cfg.allowed_dirs, self.reminders, self.reports.report)
        self.executor = Executor(registry, self.store, self.approve)
        self.brain = Brain(cfg["brain"], self.executor, assistant_name=self.name)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.utterances: queue.Queue[np.ndarray | None] = queue.Queue()
        self.mic_level = 0.0
        self.last_app = ""
        self.paused = False
        self.heard_sound = False
        self.stopping = threading.Event()
        self.speaker = None
        self.stt = None
        self.engine = None

    # ---------- startup ----------
    def load_voice(self) -> None:
        from .audio.engine import AudioEngine
        from .audio.stt import Transcriber
        from .audio.tts import Speaker

        self.stt = Transcriber(self.cfg["assistant"]["language"])
        self.speaker = Speaker(self.cfg["voice"]["piper_voice"], self.cfg["voice"]["speed"])
        for g in self.cfg["assistant"]["greetings"] or []:
            self.speaker.prepare(g)
        self.engine = AudioEngine(self.cfg, self._on_wake, self._on_utterance, self._on_mic_level,
                                  verifier=self.stt.heard_wake_word)

    def _on_wake(self, source: str) -> None:
        self.events.put(("wake", source))

    def _on_utterance(self, audio: np.ndarray | None) -> None:
        self.utterances.put(audio)

    def _on_mic_level(self, v: float) -> None:
        self.mic_level = max(v, self.mic_level * 0.9)

    # ---------- speech helpers (session thread) ----------
    def say(self, text: str, kind: str = "") -> None:
        self.bus.feed(text, kind)
        log.info("say: %s", text)
        if not self.voice or self.speaker is None:
            print(f"{self.name}: {text}")
            return
        self.bus.set_state("speaking")
        # never let Jarvis wake itself by saying its own name
        resume = self.engine is not None and self.engine.mode == "standby"
        if resume:
            self.engine.set_mode("paused")
        last = [0.0]

        def level(v: float) -> None:
            now = time.monotonic()
            if now - last[0] > 0.05 or v == 0:
                last[0] = now
                self.bus.publish({"type": "level", "value": round(v, 3)})

        try:
            self.speaker.speak(text, on_level=level)
        except Exception:
            log.exception("speech failed")
        finally:
            if resume and not self.paused:
                self.engine.set_mode("standby")

    def listen(self, start_timeout: float | None = None) -> str:
        """Record one utterance and transcribe it. Returns '' on silence."""
        if self.engine is None:
            return ""
        while not self.utterances.empty():
            self.utterances.get_nowait()
        saved = self.cfg["listen"]["start_timeout"]
        if start_timeout is not None:
            self.cfg["listen"]["start_timeout"] = start_timeout
        self.bus.set_state("listening")
        self.engine.set_mode("record")
        try:
            audio = self.utterances.get(timeout=float(self.cfg["listen"]["max_seconds"]) + 10)
        except queue.Empty:
            audio = None
        finally:
            self.cfg["listen"]["start_timeout"] = saved
        self.heard_sound = audio is not None
        if audio is None:
            return ""
        self.bus.set_state("thinking")
        text = self.stt.transcribe(audio)
        log.info("heard: %r", text)
        return text

    # ---------- approval (called from the session thread via the executor) ----------
    def approve(self, summary: str, click_only: bool) -> bool:
        timeout = int(self.cfg["safety"]["approval_timeout"])
        self.bus.set_state("approval")
        self.bus.feed(f"Approval needed: {summary.splitlines()[0][:120]}", "warn")
        dialog = Dialog(summary, click_only, timeout)
        threading.Thread(target=dialog.show, daemon=True).start()
        if click_only:
            self.say("That needs your click. Please confirm on screen.", "warn")
            self.bus.set_state("approval")
        elif self.voice and self.engine is not None:
            self.say(f"Shall I {summary.splitlines()[0][:150]}? Say yes or no.", "warn")
            deadline = time.monotonic() + timeout
            while not dialog.done.is_set() and time.monotonic() < deadline:
                answer = parse_yes_no(self.listen(start_timeout=min(8, max(1, deadline - time.monotonic()))))
                if answer is not None and not dialog.done.is_set():
                    dialog.result = answer
                    dialog.close()
                    break
                self.bus.set_state("approval")
        dialog.done.wait(timeout + 5)
        result = bool(dialog.result)
        self.bus.feed("Approved." if result else "Not approved.", "" if result else "warn")
        self.bus.set_state("thinking")
        return result

    # ---------- handling one request ----------
    def handle_text(self, text: str) -> str | None:
        """Returns the reply to speak, or None to end the conversation quietly."""
        self.bus.feed(f'"{text}"', "user")
        r = route(text)
        if r is not None and r.kind == "control":
            if r.name == "sleep":
                return "Standing by."
            return None
        if r is not None:
            self.executor.new_request()
            return self._run_route(r.name, dict(r.args))
        parts = split_compound(text)
        if parts:  # "open notepad and maximize it": several quick commands, no AI needed
            self.executor.new_request()
            replies = []
            for p in parts:
                sub = route(p)
                replies.append(self._run_route(sub.name, dict(sub.args)))
            return " ".join(replies)
        if self.brain.warming.is_set():
            self.say("One moment boss, my AI is still warming up.")
        self.bus.set_state("thinking")
        try:
            return self.brain.handle(text, on_step=lambda name: self.bus.feed(f"Using {name.replace('_', ' ')}"))
        except BrainUnavailable as e:
            return str(e)

    def _run_route(self, name: str, args: dict) -> str:
        # "close it" / "maximize it" refer to the app Jarvis opened last (or the active window)
        if name == "close_app" and args.get("name", "").lower() in PRONOUNS:
            if not self.last_app:
                return "Which app should I close, boss?"
            args["name"] = self.last_app
        if name == "window_control" and args.get("name", "").lower() in PRONOUNS:
            args["name"] = ""
        res = self.executor.run(name, args, source="router")
        if res.ok and name == "open_app":
            self.last_app = args["name"]
        return res.text

    def _warm_brain(self) -> None:
        result = self.brain.warm()
        if result == "warmed":
            self.bus.feed("AI model ready.")
        elif result == "skipped":
            self.bus.feed("AI model not loaded: low memory or Ollama is off.", "warn")

    def session(self, source: str) -> None:
        self.bus.feed(f"Wake: {source}")
        if self.cfg["brain"].get("warm_on_wake", True):
            threading.Thread(target=self._warm_brain, name="warm", daemon=True).start()
        greetings = self.cfg["assistant"]["greetings"] or []
        if greetings:
            self.say(random.choice(greetings))
        listen = self.cfg["listen"]
        active = float(listen["active_seconds"])
        # nothing understood yet (maybe a false wake): don't stay open as long
        deadline = time.monotonic() + min(active, float(listen["first_seconds"]))
        turns, apologised = 0, False
        while turns < int(listen["max_turns"]) and not self.stopping.is_set() and not self.paused:
            self._say_pending()
            left = deadline - time.monotonic()
            if left <= 0:
                break
            text = self.listen(start_timeout=min(8.0, max(1.0, left)))
            if self.paused:
                break
            if not text:  # silence or an unclear sound must not end the conversation
                if self.heard_sound and turns == 0 and not apologised:
                    apologised = True
                    self.say("Sorry boss, I didn't catch that.")
                continue
            if only_wake_word(text):
                self.say("Yes, boss?")
                turns += 1
                deadline = time.monotonic() + active
                continue
            reply = self.handle_text(text)
            if reply is None:
                break
            self.say(reply)
            if reply == "Standing by.":
                break
            turns += 1
            deadline = time.monotonic() + active
        self.bus.feed("Standing by.")

    def _say_pending(self) -> None:
        """Speak reminders that came due mid-conversation; drop extra wakes (already awake)."""
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "say":
                self.say(payload)

    def session_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                kind, payload = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "wake" and not self.paused:
                    self.session(payload)
                elif kind == "say":
                    self.say(payload)
            except Exception:
                log.exception("session failed")
                self.bus.feed("Something went wrong; see data/jarvis.log", "warn")
            finally:
                self.bus.set_state("idle")
                if self.engine is not None and not self.paused:
                    self.engine.set_mode("standby")

    # ---------- background tasks ----------
    async def stats_task(self) -> None:
        psutil.cpu_percent(None)
        while not self.stopping.is_set():
            await asyncio.sleep(2)
            self.bus.publish({"type": "stats", "cpu": psutil.cpu_percent(None),
                              "ram": psutil.virtual_memory().percent, "mic": round(self.mic_level * 100)})
            self.mic_level *= 0.3

    async def reminder_task(self) -> None:
        last_report_day = None
        daily = (self.cfg["reports"].get("daily_time") or "").strip()
        while not self.stopping.is_set():
            await asyncio.sleep(10)
            for _, text in self.reminders.due():
                self.events.put(("say", f"Reminder: {text}"))
            if daily and time.strftime("%H:%M") == daily and last_report_day != time.strftime("%Y-%m-%d"):
                last_report_day = time.strftime("%Y-%m-%d")
                report = await asyncio.to_thread(self.reports.report, "all")
                self.events.put(("say", report))

    async def location_task(self, config_event: dict) -> None:
        from .location import LocationError, current_place

        loc = self.cfg["location"]
        if not loc.get("auto", True):
            return
        while not self.stopping.is_set():
            try:
                place = await asyncio.to_thread(current_place)
                new = {"name": place.name, "lat": round(place.lat, 4), "lon": round(place.lon, 4)}
                if new != config_event.get("location"):
                    config_event["location"] = new  # also what newly connected wallpapers receive
                    self.bus.publish(config_event)
                    log.info("location: %s (accuracy %.0f m)", place.name, place.accuracy_m or -1)
            except LocationError as e:
                log.warning("location unavailable: %s", e)
            except Exception:
                log.exception("location lookup failed")
            await asyncio.sleep(max(5, float(loc.get("refresh_minutes", 30))) * 60)

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        if self.engine is not None:
            self.engine.set_mode("paused" if self.paused else "standby")
        self.bus.set_state("sleeping" if self.paused else "idle")
        self.bus.feed("Listening paused." if self.paused else "Listening resumed.")
        return self.paused

    async def run(self) -> None:
        from .ui.server import UiServer
        from .ui.tray import Tray

        loop = asyncio.get_running_loop()
        self.bus.bind(loop)
        port = int(self.cfg["ui"]["port"])
        C.write_wallpaper_token(port)
        loc = self.cfg["location"]
        config_event = {
            "type": "config", "name": self.name,
            "location": {"name": loc.get("name", ""), "lat": loc.get("lat"), "lon": loc.get("lon")},
        }
        if loc.get("auto", True):
            config_event["location"] = {"name": "Locating...", "lat": None, "lon": None}
        server = UiServer(self.bus, C.ui_token(), port, config_event)
        try:
            await server.start()
        except OSError:
            log.error("port %d is busy: is Jarvis already running?", port)
            print(f"Port {port} is in use. Jarvis may already be running.")
            return
        self.bus.set_state("thinking")
        self.bus.feed("Initializing system protocols...")
        apps_mod.load_apps()
        await asyncio.to_thread(self.load_voice)
        threading.Thread(target=self.session_loop, name="session", daemon=True).start()
        self.engine.start()
        self.bus.set_state("idle")
        wakes = " or ".join(w for w, on in (("say 'Hey Jarvis'", self.cfg["wake"]["wake_word"]),
                                             ("clap twice", self.cfg["wake"]["clap"])) if on)
        self.bus.feed(f"Online. {wakes[0].upper() + wakes[1:]}.")
        (C.DATA / "run.pid").write_text(str(os.getpid()))
        if self.startup and (text := startup_greeting(self.cfg["assistant"].get("startup_greeting", ""))):
            self.events.put(("say", text))
        tray = Tray(self.name, on_pause=self.toggle_pause, on_quit=lambda: loop.call_soon_threadsafe(self.stopping.set),
                    log_path=C.DATA / "jarvis.log")
        tray.start()
        presence = None
        if self.cfg["presence"].get("enabled", True):
            from .ui.presence import Presence

            presence = Presence(self.bus, self.cfg["presence"])
            presence.start()
        tasks = [asyncio.create_task(self.stats_task()), asyncio.create_task(self.reminder_task()),
                 asyncio.create_task(self.location_task(config_event))]
        try:
            while not self.stopping.is_set():
                await asyncio.sleep(0.5)
        finally:
            for t in tasks:
                t.cancel()
            self.engine.stop()
            tray.stop()
            if presence is not None:
                presence.stop()
            self.bus.set_state("offline")
            await server.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis")
    ap.add_argument("--text", help="run one typed command and print the reply (no microphone)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--startup", action="store_true", help="started by Windows at sign-in: speak the startup greeting")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)
    cfg = C.load()
    if args.text:
        j = Jarvis(cfg, voice=False)
        apps_mod.load_apps()
        reply = j.handle_text(args.text)
        print(f"{j.name}: {reply}" if reply else f"{j.name}: (stops listening)")
        return 0
    j = Jarvis(cfg, startup=args.startup)
    try:
        asyncio.run(j.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
