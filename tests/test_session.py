import queue
import threading

import pytest

from jarvis import main
from jarvis.audio.stt import only_wake_word


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t


@pytest.fixture
def run(monkeypatch):
    """Script items: text = understood, '' = silence, '?' = a sound that wasn't understood, callable = side effect."""

    def go(script, **listen):
        clock = Clock()
        monkeypatch.setattr(main.time, "monotonic", clock.monotonic)
        j = main.Jarvis.__new__(main.Jarvis)
        j.cfg = {"listen": {"active_seconds": 60, "first_seconds": 20, "max_turns": 30, **listen},
                 "assistant": {"greetings": []}, "brain": {"warm_on_wake": False}}
        j.bus = type("B", (), {"feed": lambda *a: None, "set_state": lambda *a: None})()
        j.events, j.stopping, j.paused, j.heard_sound = queue.Queue(), threading.Event(), False, False
        said, handled, items = [], [], iter(script)
        j.say = lambda t, kind="": said.append(t)

        def listen_(start_timeout=None):
            item = next(items, "")
            if callable(item):
                item = item(j)
            j.heard_sound = item != ""
            clock.t += start_timeout if item == "" else 2
            return "" if item == "?" else item

        def handle(t):
            handled.append(t)
            return {"stop": None, "sleep": "Standing by."}.get(t, "ok " + t)

        j.listen, j.handle_text = listen_, handle
        j.session("test")
        return handled, said, clock.t

    return go


def test_stays_active_through_silence_and_unclear_sounds(run):
    handled, _, _ = run(["one", "", "", "?", "", "two", "?", "three"])
    assert handled == ["one", "two", "three"]


def test_ends_after_active_seconds_of_quiet(run):
    _, _, t = run(["one"])
    assert 60 <= t <= 65


def test_false_wake_closes_sooner(run):
    handled, _, t = run([])
    assert handled == [] and t <= 22


def test_constant_unclear_noise_still_ends(run):
    handled, said, t = run(["?"] * 500)
    assert handled == [] and t <= 24 and said == ["Sorry boss, I didn't catch that."]


def test_stop_and_sleep_end_it(run):
    assert run(["one", "stop", "two"])[0] == ["one", "stop"]
    assert run(["one", "sleep", "two"])[0] == ["one", "sleep"]


def test_turn_cap(run):
    assert len(run(["x"] * 100, max_turns=5)[0]) == 5


def test_wake_word_alone_is_acknowledged_not_sent_to_ai(run):
    handled, said, _ = run(["Hey Jarvis.", "one"])
    assert handled == ["one"] and said[0] == "Yes, boss?"


def test_pausing_mid_conversation_ends_it(run):
    def pause(j):
        j.paused = True
        return "ignored"

    assert run(["one", pause, "two"])[0] == ["one"]


def test_reminder_is_spoken_and_extra_wake_dropped(run):
    def due(j):
        j.events.put(("wake", "clap"))
        j.events.put(("say", "Reminder: tea"))
        return ""

    assert "Reminder: tea" in run(["one", due])[1]


@pytest.mark.parametrize("text,want", [("Hey, Javris!", True), ("Jarvis", True), ("Hey Jarvis, open Notepad", False),
                                       ("Hello", False), ("", False)])
def test_only_wake_word(text, want):
    assert only_wake_word(text) is want
