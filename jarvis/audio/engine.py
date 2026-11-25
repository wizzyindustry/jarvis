"""Microphone loop: wake word + clap in standby, endpointed recording after a wake.

Runs in its own thread. Audio is kept in memory only and never written to disk.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

from ..compat import import_openwakeword
from ..config import SPEECH
from .clap import ClapDetector

log = logging.getLogger(__name__)

SR = 16000
BLOCK = 1280  # 80 ms, the frame size openWakeWord expects


class AudioEngine:
    """Modes: 'standby' (listen for wake), 'record' (capture a command), 'paused' (ignore input)."""

    def __init__(self, cfg, on_wake: Callable[[str], None], on_utterance: Callable[[np.ndarray | None], None],
                 on_level: Callable[[float], None] | None = None,
                 verifier: Callable[[np.ndarray], bool] | None = None) -> None:
        self.cfg = cfg
        # Second stage for accents the wake model scores low: Whisper checks candidates.
        self.verifier = verifier if cfg["wake"].get("verify", True) else None
        self.prefilter = float(cfg["wake"].get("prefilter", 0.05))
        self._history: deque[np.ndarray] = deque(maxlen=32)  # ~2.5 s
        self._candidate_at: float | None = None
        self._verifying = threading.Event()
        self._verified = threading.Event()
        self.on_wake = on_wake
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.mode = "paused"
        self.muted = False
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: sd.InputStream | None = None
        self._last_wake = 0.0
        self.noise = 0.004
        self._rec: list[np.ndarray] = []
        self._rec_started = 0.0
        self._speech_seen = False
        self._last_voice = 0.0

        wake = cfg["wake"]
        self.clap = ClapDetector(wake["clap_sensitivity"]) if wake["clap"] else None
        self.oww = None
        if wake["wake_word"]:
            _, Model = import_openwakeword()
            d = SPEECH / "openwakeword"
            self.oww = Model(
                wakeword_models=[str(d / "hey_jarvis_v0.1.onnx")],
                inference_framework="onnx",
                melspec_model_path=str(d / "melspectrogram.onnx"),
                embedding_model_path=str(d / "embedding_model.onnx"),
            )
            self.wake_threshold = float(wake["wake_threshold"])

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK,
                                      callback=self._callback)
        self._stream.start()
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()
        self.set_mode("standby")

    def stop(self) -> None:
        self._stop.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def set_mode(self, mode: str) -> None:
        if mode == "record":
            self._rec = []
            self._rec_started = time.monotonic()
            self._speech_seen = False
            self._last_voice = 0.0
        if mode == "standby":
            if self.clap:
                self.clap.reset()
            if self.oww:
                self.oww.reset()
            self._history.clear()
            self._candidate_at = None
            self._verified.clear()
        self.mode = mode

    def _verify(self, audio: np.ndarray) -> None:
        try:
            if self.verifier(audio) and self.mode == "standby":
                self._verified.set()
        except Exception:
            log.exception("wake verification failed")
        finally:
            self._verifying.clear()

    # ---------- audio thread ----------
    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread: keep tiny
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(block)
            except Exception:
                log.exception("audio processing error")

    def _process(self, block: np.ndarray) -> None:
        f = block.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(f * f)))
        if self.on_level:
            self.on_level(min(1.0, rms * 12))
        if self.muted or self.mode == "paused":
            return

        if self.mode == "standby":
            self._history.append(block)
            if rms < self.noise * 3:
                self.noise = 0.98 * self.noise + 0.02 * rms
            now = time.monotonic()
            cooling = now - self._last_wake < float(self.cfg["wake"]["cooldown"])
            source = None
            if self._verified.is_set():
                self._verified.clear()
                source = "wake word"
            if self.oww is not None:
                score = self.oww.predict(block).get("hey_jarvis_v0.1", 0.0)
                if score >= self.wake_threshold:
                    source = "wake word"
                    log.info("wake word score %.2f", score)
                elif self.verifier is not None and score >= self.prefilter and self._candidate_at is None:
                    # Possible wake word: wait for the end of the phrase, then let Whisper confirm.
                    self._candidate_at = now + 0.5
                    log.info("wake word candidate: score %.2f", score)
            if self._candidate_at is not None and now >= self._candidate_at:
                self._candidate_at = None
                if not self._verifying.is_set():
                    audio = np.concatenate(list(self._history)).astype(np.float32) / 32768.0
                    self._verifying.set()
                    threading.Thread(target=self._verify, args=(audio,), daemon=True).start()
            if source is None and self.clap is not None and self.clap.process(block):
                source = "clap"
            if source and not cooling:
                self._last_wake = now
                self.set_mode("paused")
                self.on_wake(source)
            return

        if self.mode == "record":
            self._rec.append(block)
            now = time.monotonic()
            listen = self.cfg["listen"]
            voice_level = max(self.noise * 3.0, 0.012)
            if rms > voice_level:
                self._speech_seen = True
                self._last_voice = now
            elif not self._speech_seen:
                del self._rec[:-6]  # keep ~0.5 s of lead-in, not seconds of silence
            elapsed = now - self._rec_started
            done = None
            if not self._speech_seen and elapsed > float(listen["start_timeout"]):
                done = "timeout"
            elif self._speech_seen and now - self._last_voice > float(listen["silence_seconds"]):
                done = "silence"
            elif elapsed > float(listen["max_seconds"]):
                done = "max"
            if done:
                self.set_mode("paused")
                audio = None
                if self._speech_seen:
                    audio = np.concatenate(self._rec).astype(np.float32) / 32768.0
                self._rec = []
                self.on_utterance(audio)
