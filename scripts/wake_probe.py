"""Check the "hey jarvis" wake word.

  wake_probe.py synth        -> feed Piper-spoken phrases through the model (no microphone)
  wake_probe.py mic [secs]   -> live: print the highest score each second (audio stays in memory)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.compat import import_openwakeword  # noqa: E402
from jarvis.config import SPEECH  # noqa: E402

D = SPEECH / "openwakeword"
KEY = "hey_jarvis_v0.1"


def model():
    _, Model = import_openwakeword()
    return Model(wakeword_models=[str(D / f"{KEY}.onnx")], inference_framework="onnx",
                 melspec_model_path=str(D / "melspectrogram.onnx"), embedding_model_path=str(D / "embedding_model.onnx"))


def score_audio(m, x16: np.ndarray) -> float:
    m.reset()
    best = 0.0
    pad = np.zeros(16000, dtype=np.int16)
    a = np.concatenate([pad, x16, pad])
    for i in range(0, len(a) - 1280 + 1, 1280):
        best = max(best, float(m.predict(a[i:i + 1280])[KEY]))
    return best


def synth() -> None:
    from piper import PiperVoice

    v = PiperVoice.load(str(SPEECH / "piper" / "en_GB-alan-medium.onnx"))
    m = model()
    for text in ["Hey Jarvis.", "Hey Jarvis, what time is it?", "Jarvis.", "Hey there, how are you?", "Open Notepad."]:
        chunks = list(v.synthesize(text))
        a = np.concatenate([c.audio_int16_array.reshape(-1) for c in chunks]).astype(np.float32)
        sr = chunks[0].sample_rate
        idx = np.arange(0, len(a), sr / 16000)
        x16 = np.interp(idx, np.arange(len(a)), a).astype(np.int16)
        for gain in (1.0, 0.3):
            print(f"{text:32} gain {gain}: score {score_audio(m, (x16 * gain).astype(np.int16)):.3f}")


def mic(secs: float) -> None:
    import sounddevice as sd

    m = model()
    print(f"Listening {secs:.0f}s: say 'Hey Jarvis' several times", flush=True)
    audio = sd.rec(int(16000 * secs), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    a = audio[:, 0]
    rms_all = np.sqrt(np.mean((a.astype(np.float32) / 32768) ** 2))
    print(f"overall rms {rms_all:.4f}")
    per_sec: dict[int, float] = {}
    for i in range(0, len(a) - 1280 + 1, 1280):
        s = float(m.predict(a[i:i + 1280])[KEY])
        sec = i // 16000
        per_sec[sec] = max(per_sec.get(sec, 0.0), s)
    for sec, s in per_sec.items():
        blk = a[sec * 16000:(sec + 1) * 16000].astype(np.float32) / 32768
        lvl = float(np.sqrt(np.mean(blk ** 2)))
        if s > 0.01 or lvl > 0.01:
            print(f"  {sec:3d}s  score {s:.3f}  level {lvl:.3f}")
    # same audio boosted, in case the mic is too quiet for the model
    for gain in (2, 4):
        m.reset()
        boosted = np.clip(a.astype(np.int32) * gain, -32768, 32767).astype(np.int16)
        best = max(float(m.predict(boosted[i:i + 1280])[KEY]) for i in range(0, len(boosted) - 1280 + 1, 1280))
        print(f"best score with gain x{gain}: {best:.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mic":
        mic(float(sys.argv[2]) if len(sys.argv) > 2 else 45)
    else:
        synth()
