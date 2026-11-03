"""Zuzycie paliwa / energii wirtualnej Twojego auta z nagran + kalkulator tankowania.

Dla kazdego okrazenia gracza: paliwo [L] i energia wirtualna [%] na starcie i na koncu
(pierwsza probka poza boksem), czas, rodzaj (wyjazdowe / pomiarowe / zjazdowe) oraz
stinty (od wyjazdu z boksu do zjazdu / konca paliwa). Cache: analysis/_fuel/<stem>.json.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from pathlib import Path

VERSION = 3


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def scan(path: Path, player: str | None) -> dict:
    pl = (player or "").lower().split("#")[0].strip()
    rows = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        head = next(r)
        ix = {k: i for i, k in enumerate(head)}
        need = ("driver", "lap", "fuel", "session_et", "in_pits")
        if any(k not in ix for k in need):
            return {"laps": [], "stints": []}
        g = lambda row, k: row[ix[k]] if k in ix and ix[k] < len(row) else ""
        for row in r:
            d = row[ix["driver"]].lower().split("#")[0].strip()
            if pl and d != pl:
                continue
            if not pl and g(row, "is_player") not in ("1", "True", "true"):
                continue
            rows.append((int(float(g(row, "lap") or 0)), _f(g(row, "session_et")),
                         _f(g(row, "fuel")), _f(g(row, "virtual_energy")),
                         g(row, "in_pits") in ("1", "True", "true"), _f(g(row, "last_lap")),
                         _f(g(row, "speed")), _f(g(row, "lap_dist"))))
    laps, stints = [], []
    if not rows:
        return {"laps": laps, "stints": stints}
    # kolejne okrazenia: pierwsza probka poza boksem na starcie kazdego okrazenia
    cur = None
    stint = None

    def close_lap(end_row, in_pit):
        nonlocal cur
        if cur is None:
            return
        f0, f1 = cur["f0"], end_row[2] if end_row else cur["flast"]
        e0, e1 = cur["e0"], end_row[3] if end_row else cur["elast"]
        lap = {"lap": cur["lap"], "fuel": (f0 - f1) if None not in (f0, f1) else None,
               "ve": (e0 - e1) if None not in (e0, e1) and (e0 or e1) else None,
               "f_start": f0, "f_end": f1, "time": None,
               "kind": "out" if cur["out"] else ("in" if in_pit else "fly"),
               "complete": end_row is not None}
        if end_row is not None and end_row[5] and end_row[5] > 0:
            lap["time"] = end_row[5]
        elif end_row is not None and cur["et0"] is not None and end_row[1] is not None:
            lap["time"] = end_row[1] - cur["et0"]
        if lap["fuel"] is not None and lap["fuel"] >= -0.05:
            laps.append(lap)
            stint["laps"].append(lap)
        cur = None

    prev = None
    for row in rows:
        lap, et, fuel, ve, pit, last, spd, dist = row
        if pit:
            if cur is not None and prev is not None and not prev[4]:
                cur["flast"], cur["elast"] = prev[2], prev[3]
                close_lap(None, True)
                laps[-1:] and laps[-1].update(kind="in") if laps and laps[-1]["lap"] == lap else None
            if stint and stint["laps"] and not stint.get("closed"):
                stint["closed"] = True
                stint["end"] = stint.get("end") or "pitted"
            prev = row
            continue
        # poza boksem
        if stint is None or stint.get("closed") or (prev is not None and fuel is not None
                                                     and prev[2] is not None and fuel > prev[2] + 0.5):
            stint = {"start_fuel": fuel, "start_ve": ve, "laps": [], "end": None}
            stints.append(stint)
            cur = {"lap": lap, "f0": fuel, "e0": ve, "et0": et, "out": True,
                   "flast": fuel, "elast": ve}
        elif cur is None or lap != cur["lap"]:
            if cur is not None:
                close_lap(row, False)
            cur = {"lap": lap, "f0": fuel, "e0": ve, "et0": et, "out": False,
                   "flast": fuel, "elast": ve}
        cur["flast"], cur["elast"] = fuel, ve
        if fuel is not None and fuel <= 0.05 and stint.get("end") is None:
            stint["end"] = f"out of fuel on lap {lap} ({dist or 0:.0f} m)"
            stint["empty_lap"] = lap
        prev = row
    if cur is not None:
        close_lap(None, False)
    # wiersze z garazu (pelny bak, zero zuzycia) to nie sa prawdziwe okrazenia
    laps = [l for l in laps if l["complete"] or (l["fuel"] or 0) > 0.05]
    out = []
    for s in stints:
        s["laps"] = [l for l in s["laps"] if l in laps]
        if not s["laps"]:
            continue
        used = sum(l["fuel"] or 0 for l in s["laps"])
        s.update(laps=[l["lap"] for l in s["laps"]], used=used, n=len(s["laps"]))
        s.setdefault("end", None)
        out.append(s)
    return {"laps": laps, "stints": out}


def cached(path: Path, player: str | None, cache_dir: Path) -> dict:
    st = path.stat()
    stem = path.name[:-7] if path.name.endswith(".csv.gz") else path.stem
    cpath = Path(cache_dir) / f"{stem}.json"
    sig = [VERSION, st.st_size, int(st.st_mtime), player or ""]
    try:
        c = json.loads(cpath.read_text(encoding="utf-8"))
        if c.get("sig") == sig:
            return c["data"]
    except (OSError, ValueError, KeyError):
        pass
    data = scan(path, player)
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"sig": sig, "data": data}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def rates(laps: list[dict], key="fuel") -> dict:
    """Zuzycie na okrazenie: pomiarowe (srednia, maks), wyjazdowe, zjazdowe, sredni czas."""
    fly = [l for l in laps if l["kind"] == "fly" and l.get("complete") and l.get(key)]
    times = [l["time"] for l in fly if l.get("time")]
    if times:
        best = min(times)
        fly = [l for l in fly if not l.get("time") or l["time"] <= best * 1.07]
    # virtual energy is a 0..1 fraction (3-8 % per lap = 0.03-0.08); the 0.05 threshold is for litres only
    vals = [l[key] for l in fly if l[key] > (0.05 if key == "fuel" else 0.0005)]
    out = [l[key] for l in laps if l["kind"] == "out" and l.get(key) and not l.get("race")]
    form = [l[key] for l in laps if l["kind"] == "out" and l.get(key) and l.get("race")]
    inn = [l[key] for l in laps if l["kind"] == "in" and l.get(key)]
    return {
        "n": len(vals),
        "avg": statistics.fmean(vals) if vals else None,
        "max": max(vals) if vals else None,
        "out": statistics.fmean(out) if out else None,
        "in": statistics.fmean(inn) if inn else None,
        # 1. okrazenie wyscigu (ze startu zatrzymanego/lotnego) zawiera okrazenie formujace
        "formation": (max(0.0, statistics.fmean(form) - statistics.fmean(vals))
                      if form and vals else None),
        "lap_time": statistics.median([l["time"] for l in fly if l.get("time")]) if fly else None,
    }


def plan(per_lap, mode, laps=0, minutes=0.0, lap_time=None, out_lap=None, in_lap=True,
         in_lap_rate=None, formation=False, margin_laps=1.0, formation_rate=None):
    """Ile zatankowac. Zwraca (suma, lista skladnikow [(opis, ilosc)])."""
    parts = []
    if not per_lap:
        return None, parts
    if mode == "quali":
        parts.append(("out lap", out_lap if out_lap else per_lap))
        parts.append((f"{laps} × timed lap", laps * per_lap))
        if in_lap:
            parts.append(("in lap", in_lap_rate if in_lap_rate else per_lap * 0.8))
    else:
        if mode == "race_time":
            # po uplywie czasu konczysz okrazenie, na ktorym jestes
            n = math.floor(minutes * 60 / lap_time) + 1 if lap_time else 0
            parts.append((f"race {minutes:g} min ≈ {n} laps (incl. the lap on which time runs out)",
                          n * per_lap))
        else:
            parts.append((f"race {laps} laps", laps * per_lap))
        if formation:
            parts.append(("formation lap", formation_rate if formation_rate else per_lap * 0.5))
    if margin_laps:
        parts.append((f"margin {margin_laps:g} laps", margin_laps * per_lap))
    return sum(v for _, v in parts), parts
