"""Pliki wynikow LMU (UserData/Log/Results/*.xml): okrazenia i sektory WSZYSTKICH kierowcow.

Gra zapisuje je po kazdej sesji (takze nienagrywanej). Blok sesji (<Practice1>, <Qualify>,
<Race>, <Warmup>) ma <DateTime> startu sesji - zgadza sie z czasem w nazwie nagrania, wiec
sesje dopasowujemy po czasie startu i torze.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import lmu_setup

SESSION_TAGS = {"Practice1": "practice", "Practice2": "practice", "Practice3": "practice",
                "Practice4": "practice", "Qualify": "quali", "Qualify2": "quali",
                "Warmup": "warmup", "Race": "race", "Race2": "race", "TestDay": "testday"}
_index: dict = {}
_parsed: dict = {}


def results_dir() -> Path | None:
    d = lmu_setup.player_dir()
    if not d:
        return None
    p = d.parent / "Log" / "Results"
    return p if p.is_dir() else None


def _head(path: Path):
    """(tor, [(tag, start_epoch)]) bez parsowania calego pliku."""
    st = path.stat().st_mtime
    hit = _index.get(str(path))
    if hit and hit[0] == st:
        return hit[1]
    text = path.read_text(encoding="utf-8", errors="ignore")
    venue = re.search(r"<TrackVenue>([^<]*)</TrackVenue>", text)
    sess = []
    for m in re.finditer(r"<(Practice\d|Qualify\d?|Warmup|Race\d?|TestDay)>\s*<DateTime>(\d+)</DateTime>", text):
        sess.append((m.group(1), int(m.group(2))))
    val = ((venue.group(1) if venue else ""), sess)
    _index[str(path)] = (st, val)
    return val


def _first(name):
    w = re.findall(r"[a-z0-9]+", name.lower())
    return w[0] if w else ""


def find_session(start: datetime, track: str, tolerance: float = 180.0):
    """(plik, tag) sesji LMU, ktora zaczela sie najblizej `start` na tym torze."""
    d = results_dir()
    if not d:
        return None
    t0 = start.timestamp()
    best = None
    for p in d.glob("*.xml"):
        try:
            if abs(p.stat().st_mtime - t0) > 12 * 3600:
                continue
            venue, sess = _head(p)
        except OSError:
            continue
        # nazwy roznia sie wariantem ("Paul Ricard Circuit" / "Paul Ricard - 1A") - czas startu
        # i tak jest dopasowany co do sekund, wiec wystarczy zgodne pierwsze slowo
        if track and venue and _first(venue) != _first(track):
            continue
        for tag, ep in sess:
            diff = abs(ep - t0)
            if diff <= tolerance and (best is None or diff < best[0]):
                best = (diff, p, tag)
    return (best[1], best[2]) if best else None


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def parse(path: Path, tag: str) -> dict:
    """{nazwa kierowcy: dict(vehicle, model, cls, player, laps=[...], best, s1..s3, ideal)}"""
    key = (str(path), tag, path.stat().st_mtime)
    if key in _parsed:
        return _parsed[key]
    root = ET.parse(path).getroot()
    node = root.find(f".//{tag}")
    out = {}
    if node is None:
        return out
    for drv in node.findall("Driver"):
        name = (drv.findtext("Name") or "").strip()
        laps = []
        for lap in drv.findall("Lap"):
            t = _f((lap.text or "").strip())
            laps.append({"num": int(lap.get("num") or 0), "time": t, "s1": _f(lap.get("s1")),
                         "s2": _f(lap.get("s2")), "s3": _f(lap.get("s3")),
                         "pit": lap.get("pit") == "1", "top": _f(lap.get("topspeed"))})
        timed = [l for l in laps if l["time"] and l["s1"] and l["s2"] and l["s3"]]
        bl = min(timed, key=lambda l: l["time"], default=None)
        mins = [min((l[k] for l in laps if l[k]), default=None) for k in ("s1", "s2", "s3")]
        out[name] = {
            "vehicle": drv.findtext("VehName") or "", "model": drv.findtext("CarType") or "",
            "cls": drv.findtext("CarClass") or "", "player": (drv.findtext("isPlayer") or "0") == "1",
            "best": _f(drv.findtext("BestLapTime")) or (bl["time"] if bl else None),
            "s1": bl["s1"] if bl else None, "s2": bl["s2"] if bl else None,
            "s3": bl["s3"] if bl else None,
            "ideal": sum(mins) if None not in mins else None,
            "best_s": mins, "laps": laps,
        }
    _parsed[key] = out
    return out


def for_recording(stem: str, track: str) -> dict:
    """Wyniki sesji LMU dla nagrania (stem = 'RRRRMMDD_HHMMSS_...'); {} gdy brak."""
    try:
        start = datetime.strptime(stem[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return {}
    hit = find_session(start, track)
    if not hit:
        return {}
    try:
        return parse(*hit)
    except (OSError, ET.ParseError):
        return {}
