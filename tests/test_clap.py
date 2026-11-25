import numpy as np

from jarvis.audio.clap import ClapDetector

SR = 16000
rng = np.random.default_rng(1)


def silence(sec, level=0.002):
    return rng.normal(0, level, int(SR * sec)).astype(np.float32)


def clap(amp=0.5):
    n = int(SR * 0.06)
    t = np.arange(n) / SR
    return (rng.normal(0, 1, n) * np.exp(-t / 0.008) * amp).astype(np.float32)


def vowel(sec=0.4, amp=0.3, f0=160):
    t = np.arange(int(SR * sec)) / SR
    env = np.minimum(1, t / 0.03) * np.minimum(1, (sec - t) / 0.05)
    sig = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (sig * env * amp / 2).astype(np.float32)


def keyclick(amp=0.03):
    n = int(SR * 0.01)
    return (rng.normal(0, 1, n) * np.exp(-np.arange(n) / 40) * amp).astype(np.float32)


def run(parts, sensitivity=0.6):
    det = ClapDetector(sensitivity)
    audio = np.concatenate(parts)
    hits = 0
    for i in range(0, len(audio) - 1280 + 1, 1280):
        if det.process(audio[i:i + 1280]):
            hits += 1
    return hits


def test_double_clap_fires():
    assert run([silence(1), clap(), silence(0.3), clap(), silence(1)]) == 1


def test_quiet_double_clap_fires_at_default_sensitivity():
    assert run([silence(1), clap(0.15), silence(0.35), clap(0.15), silence(1)]) == 1


def test_single_clap_does_not_fire():
    assert run([silence(1), clap(), silence(2)]) == 0


def test_claps_too_far_apart_do_not_fire():
    assert run([silence(1), clap(), silence(1.5), clap(), silence(1)]) == 0


def test_slow_double_clap_fires():
    # measured on this PC: one user's pair was 0.92 s apart
    assert run([silence(1), clap(), silence(0.85), clap(), silence(1)]) == 1


def lowpassed_clap(amp=0.35):
    # like this laptop's mic: loud, fast decay, but little high-frequency content
    c = clap(amp)
    k = np.ones(6) / 6
    return np.convolve(c, k, mode="same").astype(np.float32) * 2.2


def test_lowpassed_claps_fire():
    assert run([silence(1), lowpassed_clap(), silence(0.25), lowpassed_clap(), silence(1)]) == 1


def test_speech_does_not_fire():
    parts = [silence(0.5)]
    for _ in range(8):
        parts += [vowel(0.25, amp=0.5), silence(0.12)]
    assert run(parts + [silence(0.5)]) == 0


def test_typing_does_not_fire():
    parts = [silence(0.5)]
    for _ in range(40):
        parts += [keyclick(), silence(0.09)]
    assert run(parts) == 0


def test_works_over_background_noise():
    noise = silence(3, level=0.01)
    burst = np.concatenate([silence(1, 0), clap(0.6), silence(0.3, 0), clap(0.6), silence(1, 0)])
    burst = np.pad(burst, (0, len(noise) - len(burst)))
    assert run([noise + burst]) == 1
