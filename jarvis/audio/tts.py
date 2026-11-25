"""Offline voice with Piper; publishes loudness so the wallpaper core pulses."""
from __future__ import annotations

import re
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from ..config import SPEECH


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, voice: str, speed: float = 1.0) -> None:
        from piper import PiperVoice
        from piper.config import SynthesisConfig

        path = SPEECH / "piper" / f"{voice}.onnx"
        self.voice = PiperVoice.load(str(path))
        self.syn = SynthesisConfig(length_scale=float(speed))
        self._stop = threading.Event()
        self._cache: dict[str, list[tuple[np.ndarray, int]]] = {}

    def prepare(self, text: str) -> None:
        """Synthesise a phrase ahead of time (e.g. greetings) so it plays instantly."""
        clean = clean_for_speech(text)
        self._cache[clean] = [(c.audio_int16_array.reshape(-1), c.sample_rate)
                              for c in self.voice.synthesize(clean, syn_config=self.syn)]

    def stop(self) -> None:
        self._stop.set()
        sd.stop()

    def speak(self, text: str, on_level: Callable[[float], None] | None = None) -> None:
        """Blocking. Synthesises sentence by sentence so speech starts quickly."""
        self._stop.clear()
        text = clean_for_speech(text)
        if not text:
            return
        if text in self._cache:
            for audio, sr in self._cache[text]:
                if self._stop.is_set():
                    break
                self._play(audio, sr, on_level)
            return
        for chunk in self.voice.synthesize(text, syn_config=self.syn):
            if self._stop.is_set():
                break
            audio = chunk.audio_int16_array.reshape(-1)
            sr = chunk.sample_rate
            self._play(audio, sr, on_level)

    def _play(self, audio: np.ndarray, sr: int, on_level) -> None:
        step = int(sr * 0.05)
        pos = 0
        with sd.OutputStream(samplerate=sr, channels=1, dtype="int16") as out:
            while pos < len(audio) and not self._stop.is_set():
                block = audio[pos:pos + step]
                if on_level is not None:
                    f = block.astype(np.float32) / 32768.0
                    on_level(min(1.0, float(np.sqrt(np.mean(f * f))) * 5))
                out.write(block.reshape(-1, 1))
                pos += step
        if on_level is not None:
            on_level(0.0)
