"""Double-clap detector (pure numpy, no model).

A clap is a sharp, broadband burst that decays fast. Speech is mostly
low-frequency and sustained; typing is broadband but quiet; a door bang is
usually a single event. We look for two clap-like onsets 150-700 ms apart.
"""
from __future__ import annotations

from collections import deque

import numpy as np

SR = 16000
FRAME = 160  # 10 ms


class ClapDetector:
    def __init__(self, sensitivity: float = 0.6, min_gap: float = 0.15, max_gap: float = 1.0) -> None:
        s = float(min(max(sensitivity, 0.0), 1.0))
        self.jump_ratio = 14.0 - 9.0 * s      # how far above background an onset must jump
        self.abs_min = 0.10 - 0.07 * s       # minimum RMS (float scale, full scale = 1.0)
        # High-frequency share (diff energy / energy). Laptop mic processing low-passes claps
        # (measured 0.06-0.46 on this PC), so this only rejects pure low hums.
        self.hf_min = 0.02
        self.decay_frames = 3                # must fall below `decay_to` of peak within 30 ms (measured: 10-20 ms)
        self.decay_to = 0.35
        self.min_gap = min_gap
        self.max_gap = max_gap
        self.noise = 0.003
        self.t = 0.0                         # seconds of audio processed
        self._recent: deque[float] = deque(maxlen=12)  # last frame RMS values
        self._pending: dict | None = None     # onset waiting for decay confirmation
        self._claps: list[float] = []
        self._refractory_until = 0.0

    def reset(self) -> None:
        self._pending = None
        self._claps.clear()

    def process(self, block: np.ndarray) -> bool:
        """Feed int16 or float32 mono samples at 16 kHz. Returns True on a double clap."""
        x = block.astype(np.float32)
        if block.dtype == np.int16:
            x /= 32768.0
        fired = False
        for i in range(0, len(x) - FRAME + 1, FRAME):
            if self._frame(x[i:i + FRAME]):
                fired = True
        return fired

    def _frame(self, f: np.ndarray) -> bool:
        self.t += FRAME / SR
        energy = float(np.mean(f * f))
        rms = energy ** 0.5
        prev = max(self._recent) if self._recent else 0.0

        # confirm a pending onset once enough frames passed
        if self._pending is not None:
            p = self._pending
            p["peak"] = max(p["peak"], rms)
            p["frames"] += 1
            if rms < p["peak"] * self.decay_to:
                self._pending = None
                self._recent.append(rms)
                return self._register(p["t"])
            if p["frames"] > self.decay_frames:
                self._pending = None  # too sustained: speech, music, a long noise
                self._refractory_until = self.t + 0.25

        elif self.t >= self._refractory_until and rms > self.abs_min and rms > self.jump_ratio * max(self.noise, prev * 0.5):
            d = np.diff(f)
            hf = float(np.mean(d * d)) / (energy + 1e-12)
            if hf >= self.hf_min:
                self._pending = {"t": self.t, "peak": rms, "frames": 0}

        # background level: slow EMA, only from quiet frames
        if self._pending is None and rms < self.noise * 4 + 0.01:
            self.noise = 0.995 * self.noise + 0.005 * rms
        self._recent.append(rms)

        # forget a lone clap
        if self._claps and self.t - self._claps[-1] > self.max_gap:
            self._claps.clear()
        return False

    def _register(self, t: float) -> bool:
        self._refractory_until = t + 0.08
        if self._claps and self.min_gap <= t - self._claps[-1] <= self.max_gap:
            self._claps.clear()
            self._refractory_until = t + 1.0
            return True
        self._claps = [t]
        return False
