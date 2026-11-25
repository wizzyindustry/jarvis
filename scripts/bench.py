"""Phase 0 go/no-go: can a local model drive Jarvis's tools well enough on this PC?

Dry run: the model's first tool call is checked, never executed.
Usage:  .venv\\Scripts\\python.exe scripts\\bench.py [model ...]
Pass mark: >= 16/20 correct and median time < 30 s.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis import config as C  # noqa: E402
from jarvis.brain.prompts import SYSTEM  # noqa: E402
from jarvis.safety.audit import Store  # noqa: E402
from jarvis.tools.catalog import build  # noqa: E402
from jarvis.tools.reminders import Reminders  # noqa: E402

HOME = str(Path.home())

# (request, expected tool or None for a spoken answer, argument check)
CASES = [
    ("Open Notepad and then type hello world", "open_app", lambda a: "notepad" in a.get("name", "").lower()),
    ("Can you put on some music on Spotify", "open_app", lambda a: "spotify" in a.get("name", "").lower()),
    ("Make a note on my desktop called shopping.txt with milk, eggs and bread",
     "write_file", lambda a: "shopping" in a.get("path", "").lower() and "milk" in a.get("content", "").lower()),
    ("What files are in my Downloads folder?", "list_files", lambda a: "download" in a.get("folder", "").lower()),
    ("Find my CV", "find_files", lambda a: "cv" in a.get("name", "").lower()),
    ("Read me the file todo.txt in Documents", "read_file", lambda a: "todo" in a.get("path", "").lower()),
    ("Delete the file old_notes.txt from my desktop", "delete_file", lambda a: "old_notes" in a.get("path", "")),
    ("Move report.pdf from Downloads to Documents", "move_file",
     lambda a: "report" in a.get("source", "") and "document" in a.get("destination", "").lower()),
    ("Turn the sound down a bit", "system_control", lambda a: a.get("action") in ("volume_down", "set_volume")),
    ("Skip this song", "system_control", lambda a: a.get("action") == "next"),
    ("I need to leave in 20 minutes, remind me to take my keys", "set_reminder",
     lambda a: "key" in a.get("text", "").lower() and "20" in a.get("when", "")),
    ("Look up flights from Dar es Salaam to Nairobi", "web_search", lambda a: "nairobi" in a.get("query", "").lower()),
    ("Take me to github.com", "open_url", lambda a: "github.com" in a.get("url", "")) ,
    ("Is my laptop running hot on memory?", "system_status", lambda a: True),
    ("How many followers do I have on Instagram?", "get_report", lambda a: a.get("account") in ("instagram", "all", None)),
    ("Close all the Brave windows", "close_app", lambda a: "brave" in a.get("name", "").lower()),
    ("What's my IP address? Use PowerShell", "run_powershell", lambda a: "ip" in a.get("command", "").lower()),
    ("What's 15 percent of 240?", None, None),
    ("Tell me a one-line joke", None, None),
    ("Who wrote Things Fall Apart?", None, None),
]


def run(model: str, url: str, num_ctx: int, num_batch: int) -> None:
    store = Store(C.DATA / "bench.db")
    reg = build([Path(HOME) / "Documents", Path(HOME) / "Desktop", Path(HOME) / "Downloads"], Reminders(store),
                lambda a: "")
    tools = reg.model_schemas()
    system = SYSTEM.format(name="Jarvis", home=HOME)
    http = httpx.Client(base_url=url, timeout=300)
    print(f"\n=== {model} === free RAM before: {psutil.virtual_memory().available / 2**30:.1f} GB")
    # warm-up: load the model and cache the system prompt + tools
    t = time.perf_counter()
    http.post("/api/chat", json={"model": model, "messages": [{"role": "system", "content": system},
              {"role": "user", "content": "hi"}], "tools": tools, "stream": False, "think": False,
              "options": {"num_ctx": num_ctx, "num_thread": 4, "num_batch": num_batch}}).raise_for_status()
    print(f"load + first prompt: {time.perf_counter() - t:.1f}s")
    ps = http.get("/api/ps").json().get("models", [])
    for m in ps:
        print(f"model in memory: {m['name']} {m.get('size', 0) / 2**30:.1f} GB")

    times, correct = [], 0
    for text, want, check in CASES:
        t = time.perf_counter()
        r = http.post("/api/chat", json={
            "model": model, "stream": False, "think": False, "tools": tools,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"[Monday 23 November 2026, 20:00] {text}"}],
            "options": {"num_ctx": num_ctx, "temperature": 0.3, "num_thread": 4, "num_batch": num_batch}})
        dt = time.perf_counter() - t
        times.append(dt)
        msg = r.json()["message"]
        calls = msg.get("tool_calls") or []
        got = calls[0]["function"]["name"] if calls else None
        args = calls[0]["function"].get("arguments", {}) if calls else {}
        ok = got == want and (check is None or bool(check(args)))
        correct += ok
        shown = f"{got}({args})" if got else repr((msg.get('content') or '')[:60])
        print(f"{'PASS' if ok else 'FAIL'} {dt:5.1f}s  {text[:45]:45}  -> {shown[:110]}")
    med = statistics.median(times)
    verdict = "GO" if correct >= 16 and med < 30 else "NO-GO"
    print(f"\n{model}: {correct}/20 correct, median {med:.1f}s, worst {max(times):.1f}s -> {verdict}")


def main() -> None:
    cfg = C.load()["brain"]
    models = sys.argv[1:] or [cfg["model"], cfg["fallback_model"]]
    for m in models:
        try:
            run(m, cfg["ollama_url"], int(cfg["num_ctx"]), int(cfg["num_batch"]))
        except httpx.HTTPError as e:
            print(f"{m}: failed ({e})")


if __name__ == "__main__":
    main()
