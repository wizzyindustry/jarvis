"""Download the speech models (models/speech) and the offline city list (models/geo).

Usage:  .venv\\Scripts\\python.exe scripts\\download_models.py
"""
from __future__ import annotations

import os
import sys
import urllib.request
import zipfile

# hf_xet is a native downloader that Smart App Control may block; plain HTTPS works.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.compat import import_openwakeword  # noqa: E402

SPEECH = ROOT / "models" / "speech"
GEO = ROOT / "models" / "geo"
GEONAMES = "https://download.geonames.org/export/dump/"

PIPER_VOICE = "en_GB-alan-medium"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.name}")
        return
    print(f"  downloading {dest.name} ...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def fetch_cities(geo: Path = GEO) -> None:
    """City names for the wallpaper location (GeoNames, CC BY 4.0)."""
    geo.mkdir(parents=True, exist_ok=True)
    fetch(GEONAMES + "countryInfo.txt", geo / "countryInfo.txt")
    if not (geo / "cities5000.txt").exists():
        fetch(GEONAMES + "cities5000.zip", geo / "cities5000.zip")
        with zipfile.ZipFile(geo / "cities5000.zip") as z:
            z.extract("cities5000.txt", geo)


def main() -> None:
    SPEECH.mkdir(parents=True, exist_ok=True)

    print("Wake word (openWakeWord: hey_jarvis)")
    oww, _ = import_openwakeword()
    oww_dir = SPEECH / "openwakeword"
    oww.utils.download_models(["hey_jarvis"], target_directory=str(oww_dir))

    print(f"Voice (Piper {PIPER_VOICE})")
    piper_dir = SPEECH / "piper"
    piper_dir.mkdir(exist_ok=True)
    for name in (f"{PIPER_VOICE}.onnx", f"{PIPER_VOICE}.onnx.json"):
        fetch(PIPER_BASE + name + "?download=true", piper_dir / name)

    print("Speech-to-text (faster-whisper base)")
    from faster_whisper import WhisperModel

    WhisperModel("base", device="cpu", compute_type="int8", download_root=str(SPEECH / "whisper"))
    print("City list (GeoNames)")
    fetch_cities()
    print("Done.")


if __name__ == "__main__":
    main()
