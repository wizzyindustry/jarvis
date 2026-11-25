"""Ollama client and the tool-calling loop."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import psutil

from ..tools.executor import Executor
from ..tools.system import top_memory_apps
from .prompts import SYSTEM

log = logging.getLogger(__name__)


class BrainUnavailable(Exception):
    """The model can't run right now; the message is safe to speak."""


@dataclass
class Brain:
    cfg: dict[str, Any]
    executor: Executor
    assistant_name: str = "Jarvis"
    history: list[dict[str, Any]] = field(default_factory=list)
    last_used: float = 0.0

    def __post_init__(self) -> None:
        self._warm_lock = threading.Lock()
        self.warming = threading.Event()
        self.http = httpx.Client(base_url=self.cfg["ollama_url"], timeout=float(self.cfg["request_timeout"]))
        self.system = SYSTEM.format(name=self.assistant_name, home=str(Path.home()))

    # ---------- model selection ----------
    def _loaded(self) -> set[str]:
        try:
            r = self.http.get("/api/ps", timeout=5)
            r.raise_for_status()
            return {m["name"] for m in r.json().get("models", [])}
        except httpx.HTTPError as e:
            raise BrainUnavailable("My AI engine isn't running. Start Ollama and try again.") from e

    def _installed(self) -> set[str]:
        r = self.http.get("/api/tags", timeout=5)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}

    def pick_model(self) -> str:
        primary, fallback = self.cfg["model"], self.cfg["fallback_model"]
        loaded = self._loaded()
        if primary in loaded:
            return primary
        if fallback in loaded and psutil.virtual_memory().available / 2**30 < float(self.cfg["min_free_gb"]):
            return fallback
        installed = self._installed()
        free = psutil.virtual_memory().available / 2**30
        if primary in installed and free >= float(self.cfg["min_free_gb"]):
            return primary
        if fallback in installed and free >= float(self.cfg["fallback_min_free_gb"]):
            return fallback
        if primary not in installed and fallback not in installed:
            raise BrainUnavailable("My AI model isn't downloaded yet.")
        hogs = ", ".join(f"{n} with {gb:.1f} gigabytes" for n, gb in top_memory_apps(3))
        raise BrainUnavailable(
            f"There isn't enough free memory to think: {free:.1f} gigabytes free. "
            f"The biggest users are {hogs}. Close some of them and ask again."
        )

    # ---------- warm-up ----------
    def warm(self) -> str:
        """Load the model and have it read the fixed instructions, so the first request doesn't wait
        for that. Returns what happened: 'ready', 'warmed', 'busy' or 'skipped'. Never raises."""
        if not self._warm_lock.acquire(blocking=False):
            return "busy"
        try:
            model = self.pick_model()
            if model in self._loaded():
                # already in memory: an empty request only restarts the keep_alive countdown
                self._request(model, [], None)
                return "ready"
            self.warming.set()
            t = time.monotonic()
            # same system prompt and tools as a real request, so its cache is reused
            self._request(model, [{"role": "system", "content": self.system}, {"role": "user", "content": "hi"}],
                          self.executor.registry.model_schemas(), num_predict=1)
            log.info("AI model warmed up in %.0f s", time.monotonic() - t)
            return "warmed"
        except (BrainUnavailable, httpx.HTTPError) as e:
            log.warning("AI warm-up skipped: %s", e)
            return "skipped"
        finally:
            self.warming.clear()
            self._warm_lock.release()

    def _request(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                 **extra: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False, "think": False,
            "keep_alive": self.cfg["keep_alive"],
            "options": {"num_ctx": int(self.cfg["num_ctx"]), "temperature": 0.3, "num_thread": 4,
                        "num_batch": int(self.cfg["num_batch"]), **extra},
        }
        if tools:
            body["tools"] = tools
        r = self.http.post("/api/chat", json=body)
        r.raise_for_status()
        return r.json()

    # ---------- chat ----------
    def _chat(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        try:
            return self._request(model, messages, tools)["message"]
        except httpx.TimeoutException as e:
            raise BrainUnavailable("That took too long to think about. Try a simpler request.") from e
        except httpx.HTTPError as e:
            raise BrainUnavailable("My AI engine returned an error.") from e

    def handle(self, text: str, on_step: Callable[[str], None] | None = None) -> str:
        if time.time() - self.last_used > 300:
            self.history.clear()
        model = self.pick_model()
        self.executor.new_request()
        stamp = time.strftime("%A %d %B %Y, %H:%M")
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system}, *self.history,
                                          {"role": "user", "content": f"[{stamp}] {text}"}]
        tools = self.executor.registry.model_schemas()
        reply = ""
        for step in range(int(self.cfg["max_steps"])):
            msg = self._chat(model, messages, tools)
            calls = msg.get("tool_calls") or []
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            if not calls:
                reply = (msg.get("content") or "").strip()
                break
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                if on_step:
                    on_step(name)
                res = self.executor.run(name, fn.get("arguments") or {}, source="model")
                messages.append({"role": "tool", "tool_name": name, "content": res.text})
        else:
            reply = "I've stopped there; that was taking too many steps."
        if not reply:
            reply = "Done."
        self.history = (self.history + [{"role": "user", "content": text}, {"role": "assistant", "content": reply}])[-6:]
        self.last_used = time.time()
        return reply
