"""Measure what claps look like on this microphone. Audio stays in memory; only numbers are printed.

Usage: .venv\\Scripts\\python.exe scripts\\clap_probe.py [seconds]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.clap import ClapDetector  # noqa: E402

SR, FRAME = 16000, 160
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
print(f"Recording {secs:.0f}s... clap now", flush=True)
audio = sd.rec(int(SR * secs), samplerate=SR, channels=1, dtype="int16")
sd.wait()
x = audio[:, 0].astype(np.float32) / 32768.0

frames = x[: len(x) // FRAME * FRAME].reshape(-1, FRAME)
energy = (frames ** 2).mean(axis=1)
rms = np.sqrt(energy)
d = np.diff(frames, axis=1)
hf = (d ** 2).mean(axis=1) / (energy + 1e-12)
noise = float(np.median(rms))
print(f"background rms median {noise:.4f}, 95th pct {np.percentile(rms, 95):.4f}, max {rms.max():.4f}")

# peaks: local maxima well above background
events = []
i = 0
while i < len(rms):
    if rms[i] > max(noise * 5, 0.01):
        j = i + int(np.argmax(rms[i:i + 10]))
        peak = rms[j]
        prev = rms[max(0, j - 12):max(0, j - 1)].max() if j > 1 else 0
        after = rms[j + 1:j + 8]
        decay = int(np.argmax(after < peak * 0.35)) + 1 if (after < peak * 0.35).any() else -1
        events.append((j * FRAME / SR, peak, peak / max(noise, prev * 0.5, 1e-6), hf[j], decay))
        i = j + 10
    else:
        i += 1
print(f"{len(events)} loud events (time s, peak rms, jump x, hf ratio, frames to decay):")
for t, p, jump, h, dec in events[:60]:
    print(f"  {t:6.2f}  rms {p:.3f}  jump {jump:6.1f}  hf {h:.2f}  decay {dec}")

for s in (0.6, 0.8, 1.0):
    det = ClapDetector(s)
    hits = [k * 1280 / SR for k in range(len(x) // 1280) if det.process(audio[k * 1280:(k + 1) * 1280, 0])]
    print(f"detector sensitivity {s}: fired at {[round(h, 1) for h in hits]}")
