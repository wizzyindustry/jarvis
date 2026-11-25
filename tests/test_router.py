import pytest

from jarvis.brain.router import route

CASES = [
    ("Open Notepad.", ("tool", "open_app", {"name": "notepad"})),
    ("Jarvis, open Spotify please.", ("tool", "open_app", {"name": "spotify"})),
    ("Hey Jarvis, can you launch the calculator app?", ("tool", "open_app", {"name": "calculator"})),
    ("Close Chrome.", ("tool", "close_app", {"name": "chrome"})),
    ("Set the volume to 30%.", ("tool", "system_control", {"action": "set_volume", "value": 30})),
    ("Volume fifty", ("tool", "system_control", {"action": "set_volume", "value": 50})),
    ("Turn it up", ("tool", "system_control", {"action": "volume_up"})),
    ("Volume down.", ("tool", "system_control", {"action": "volume_down"})),
    ("Mute.", ("tool", "system_control", {"action": "mute"})),
    ("Next song", ("tool", "system_control", {"action": "next"})),
    ("Pause the music.", ("tool", "system_control", {"action": "play_pause"})),
    ("What time is it?", ("tool", "tell_time", {})),
    ("How's my computer doing?", ("tool", "system_status", {})),
    ("Search for the weather in Dodoma.", ("tool", "web_search", {"query": "the weather in dodoma"})),
    ("Google best pizza near me", ("tool", "web_search", {"query": "best pizza near me"})),
    ("Remind me to call mum in 10 minutes.", ("tool", "set_reminder", {"text": "call mum", "when": "in 10 minutes"})),
    ("Remind me at 5:30 pm to take medicine", ("tool", "set_reminder", {"text": "take medicine", "when": "at 5:30 pm"})),
    ("Give me my report.", ("tool", "get_report", {"account": "all"})),
    ("Check my email.", ("tool", "get_report", {"account": "gmail"})),
    ("Do I have any new emails?", ("tool", "get_report", {"account": "gmail"})),
    ("How is my YouTube?", ("tool", "get_report", {"account": "youtube"})),
    ("Open Brave full window.", ("tool", "open_app", {"name": "brave", "window": "maximize"})),
    ("Open Chrome in full screen", ("tool", "open_app", {"name": "chrome", "window": "maximize"})),
    ("Maximize it.", ("tool", "window_control", {"action": "maximize", "name": "it"})),
    ("Maximise Notepad", ("tool", "window_control", {"action": "maximize", "name": "notepad"})),
    ("Minimize the browser window", ("tool", "window_control", {"action": "minimize", "name": "browser"})),
    ("Make it full screen", ("tool", "window_control", {"action": "maximize", "name": "it"})),
    ("Switch to Spotify", ("tool", "window_control", {"action": "focus", "name": "spotify"})),
    ("Close it.", ("tool", "close_app", {"name": "it"})),   # resolved to the last opened app by the caller
    ("Open my downloads folder.", ("tool", "open_folder", {"name": "downloads"})),
    ("Open Downloads", ("tool", "open_folder", {"name": "downloads"})),
    ("Show me the projects folder", ("tool", "open_folder", {"name": "projects"})),
    ("Open D drive", ("tool", "open_folder", {"name": "d drive"})),
    ("Never mind.", ("control", "stop", {})),
    ("Go to sleep.", ("control", "sleep", {})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_routes(text, expected):
    r = route(text)
    assert r is not None, text
    assert (r.kind, r.name, r.args) == expected


def test_compound_commands_split():
    from jarvis.brain.router import split_compound

    assert split_compound("Open Notepad and maximize it.") == ["open notepad", "maximize it"]
    assert split_compound("Set the volume to 20 and then play the music") == ["set the volume to 20", "play the music"]
    assert split_compound("Open Notepad and type hello") is None   # 'type hello' needs the model


def test_pronoun_resolves_to_last_opened_app(monkeypatch):
    from jarvis.main import Jarvis

    calls = []
    j = Jarvis.__new__(Jarvis)
    j.last_app = ""

    class Ex:
        def run(self, name, args, source):
            calls.append((name, dict(args)))
            from jarvis.tools.executor import Result
            return Result(True, "ok")

    j.executor = Ex()
    assert j._run_route("close_app", {"name": "it"}) == "Which app should I close, boss?"
    j._run_route("open_app", {"name": "notepad"})
    j._run_route("close_app", {"name": "it"})
    j._run_route("window_control", {"action": "maximize", "name": "it"})
    assert calls[-2] == ("close_app", {"name": "notepad"})
    assert calls[-1] == ("window_control", {"action": "maximize", "name": ""})


@pytest.mark.parametrize("text", [
    "Open the file report.txt in my documents",   # needs the model (file path)
    "Write a note about tomorrow's meeting",
    "Shut down the computer",
    "What's the capital of Tanzania?",
    "Open Notepad and type hello",                 # multi-step -> model
    "Close everything",
    "",
])
def test_falls_through_to_model(text):
    assert route(text) is None


def test_startup_greeting_time_of_day():
    import time
    from jarvis.main import startup_greeting

    t = time.strptime("2026-11-23 08:15", "%Y-%m-%d %H:%M")
    assert startup_greeting("Good {part}, boss. It's {time}.", t) == "Good morning, boss. It's 8:15 AM."
    t = time.strptime("2026-11-23 19:05", "%Y-%m-%d %H:%M")
    assert startup_greeting("Good {part}, {day}", t) == "Good evening, Monday"
    assert startup_greeting("") == ""
