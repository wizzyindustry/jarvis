"""Where is this PC? Windows Location (Wi-Fi based) + an offline city list.

The position never leaves the computer: the city name comes from the GeoNames
cities file in models/geo (CC BY 4.0, geonames.org), not from a web service.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .config import MODELS

log = logging.getLogger(__name__)
GEO = MODELS / "geo"

PS_SCRIPT = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher('High')
$null = $w.TryStart($false, [TimeSpan]::FromSeconds(20))
$end = (Get-Date).AddSeconds(20)
while ($w.Status -ne 'Ready' -and (Get-Date) -lt $end) { Start-Sleep -Milliseconds 200 }
$c = $w.Position.Location
if ($w.Permission -ne 'Granted') { 'DENIED'; exit }
if ($c.IsUnknown) { 'UNKNOWN'; exit }
$ci = [Globalization.CultureInfo]::InvariantCulture
$c.Latitude.ToString($ci) + ',' + $c.Longitude.ToString($ci) + ',' + $c.HorizontalAccuracy.ToString($ci)
"""


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    accuracy_m: float | None = None


class LocationError(Exception):
    pass


def windows_position() -> tuple[float, float, float]:
    """Asks Windows Location Services. Raises LocationError if it's off or unavailable."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
                       capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    out = (r.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if last == "DENIED":
        raise LocationError("Location access is off (Settings > Privacy & security > Location).")
    if not last or last == "UNKNOWN" or last.count(",") != 2:
        raise LocationError("Windows couldn't determine a location.")
    lat, lon, acc = (float(x) for x in last.split(","))
    return lat, lon, acc


@lru_cache(maxsize=1)
def _cities():
    path = GEO / "cities5000.txt"
    if not path.exists():
        raise LocationError("City list missing: run scripts\\download_models.py")
    names, ccs, lats, lons, pops = [], [], [], [], []
    with path.open(encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 15:
                continue
            names.append(p[1])
            lats.append(float(p[4]))
            lons.append(float(p[5]))
            ccs.append(p[8])
            pops.append(int(p[14] or 0))
    countries = {}
    info = GEO / "countryInfo.txt"
    if info.exists():
        for line in info.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                p = line.split("\t")
                if len(p) > 4:
                    countries[p[0]] = p[4]
    return names, ccs, np.radians(np.array(lats)), np.radians(np.array(lons)), np.array(pops), countries


def nearest_city(lat: float, lon: float) -> str:
    names, ccs, la, lo, pops, countries = _cities()
    rlat, rlon = np.radians(lat), np.radians(lon)
    # haversine distance in km
    a = np.sin((la - rlat) / 2) ** 2 + np.cos(rlat) * np.cos(la) * np.sin((lo - rlon) / 2) ** 2
    dist = 6371 * 2 * np.arcsin(np.sqrt(a))
    # prefer the bigger town when two are about equally close (suburbs vs the city itself)
    score = dist - np.log10(np.maximum(pops, 1)) * 0.8
    i = int(np.argmin(score))
    country = countries.get(ccs[i], ccs[i])
    return f"{names[i]}, {country}"


def current_place() -> Place:
    lat, lon, acc = windows_position()
    return Place(nearest_city(lat, lon), lat, lon, acc)
