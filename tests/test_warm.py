import httpx
import pytest

from jarvis.brain.ollama import Brain, BrainUnavailable

CFG = {"ollama_url": "http://127.0.0.1:1", "model": "m", "fallback_model": "f", "min_free_gb": 0,
       "fallback_min_free_gb": 0, "keep_alive": "30m", "num_ctx": 4096, "num_batch": 64,
       "max_steps": 2, "request_timeout": 5}


class Registry:
    def model_schemas(self):
        return [{"type": "function"}]


class Ex:
    registry = Registry()


@pytest.fixture
def brain(monkeypatch):
    b = Brain(CFG, Ex())
    b.sent = []

    def req(model, messages, tools, **extra):
        b.sent.append((messages, tools, b.warming.is_set()))
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(b, "_request", req)
    monkeypatch.setattr(b, "pick_model", lambda: "m")
    return b


def test_cold_model_is_primed_with_the_real_prompt(brain, monkeypatch):
    monkeypatch.setattr(brain, "_loaded", set)
    assert brain.warm() == "warmed"
    messages, tools, warming = brain.sent[0]
    assert messages[0]["content"] == brain.system and tools and warming
    assert not brain.warming.is_set()


def test_loaded_model_only_refreshes(brain, monkeypatch):
    monkeypatch.setattr(brain, "_loaded", lambda: {"m"})
    assert brain.warm() == "ready"
    assert brain.sent == [([], None, False)]


@pytest.mark.parametrize("err", [BrainUnavailable("low memory"), httpx.ConnectError("down")])
def test_failure_is_quiet_and_recovers(brain, monkeypatch, err):
    def boom():
        raise err

    monkeypatch.setattr(brain, "pick_model", boom)
    assert brain.warm() == "skipped"
    assert not brain.warming.is_set() and not brain._warm_lock.locked()


def test_only_one_warm_up_at_a_time(brain):
    with brain._warm_lock:
        assert brain.warm() == "busy"
    assert brain.sent == []
