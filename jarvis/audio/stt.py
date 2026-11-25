"""Speech-to-text with faster-whisper on CPU."""
from __future__ import annotations

import logging
import os
import re
import threading

import numpy as np

from ..config import SPEECH

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

log = logging.getLogger(__name__)

# How Whisper tends to spell "Jarvis" in different accents
WAKE_RE = re.compile(r"\b(jarvis|javis|javris|jarvus|jervis|jarvi|jarves|jarvice|garvis|charvis|jarbis|javiz|jarviz"
                     r"|jahvis|jaavis|javies|jarviss)\b", re.I)

# Whisper invents these on silence or noise
HALLUCINATIONS = {"thank you.", "thanks for watching.", "thank you for watching.", "you", ".", "bye.", "",
                  "thank you", "okay.", "hmm.", "so.", "um."}


def only_wake_word(text: str) -> bool:
    """True for just 'Hey Jarvis' with no request after it."""
    rest, n = WAKE_RE.subn(" ", text)
    return n > 0 and not re.sub(r"\b(hey|hi|hello|ok|okay|yo)\b|[^a-z0-9]", "", rest.lower())


class Transcriber:
    def __init__(self, language: str = "en", model: str = "base") -> None:
        from faster_whisper import WhisperModel

        self.language = language
        self._lock = threading.Lock()
        root = SPEECH / "whisper"
        # after the first download (scripts/download_models.py) never contact the internet
        local = root.exists() and any(root.rglob("model.bin"))
        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=4,
                                  download_root=str(root), local_files_only=local)

    def heard_wake_word(self, audio: np.ndarray) -> bool:
        """Second-stage wake check. No prompt, so Whisper isn't nudged towards 'Jarvis'."""
        with self._lock:
            segments, _ = self.model.transcribe(audio, language=self.language, beam_size=1, vad_filter=False,
                                                condition_on_previous_text=False)
            text = " ".join(s.text for s in segments if s.no_speech_prob < 0.6)
        hit = bool(WAKE_RE.search(text))
        log.info("wake check heard %r -> %s", text.strip()[:80], "yes" if hit else "no")
        return hit

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            return self._transcribe(audio)

    def _transcribe(self, audio: np.ndarray) -> str:
        # No initial_prompt: on noise Whisper tends to repeat the prompt back, and a prompt
        # like "open Notepad" turned background sound into real commands.
        segments, _ = self.model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=False, condition_on_previous_text=False,
        )
        parts = []
        for seg in segments:
            # skip segments Whisper itself thinks are not speech, or is guessing at
            # (accented but correct commands score around -1.04, garbage around -1.7)
            if seg.no_speech_prob >= 0.6 or seg.avg_logprob < -1.4 or seg.compression_ratio > 2.4:
                log.info("dropped unclear segment %r (no_speech %.2f, logprob %.2f, repeat %.1f)",
                         seg.text.strip()[:60], seg.no_speech_prob, seg.avg_logprob, seg.compression_ratio)
                continue
            parts.append(seg.text.strip())
        text = " ".join(parts).strip()
        # a lone letter ("I") is noise, never a command
        if text.lower() in HALLUCINATIONS or sum(c.isalnum() for c in text) < 2:
            return ""
        return text
