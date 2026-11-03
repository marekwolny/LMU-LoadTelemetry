"""Analiza prowadzenia auta z <nagranie>.chassis.csv.gz (tylko Twoje auto).

Na kazdym zakrecie, osobno dla wejscia / srodka / wyjscia:
  * balans = sredni kat poslizgu opon przod - tyl [deg]
      > 0  podsterownosc (przod slizga sie bardziej niz tyl),
      < 0  nadsterownosc (tyl slizga sie bardziej),
  * transfer masy: udzial nacisku na przod, na kola zewnetrzne, LLTD przod
      (jaka czesc bocznego przenoszenia nacisku bierze przednia os - wiecej = bardziej
      podsterowne; zmienia sie stabilizatorami / sprezynami),
  * minimalny przeswit przod / tyl (dobijanie), przeciazenia, czas.
Plus opony (temperatury wewn./srodek/zewn., cisnienia) i przeswit dla calego okrazenia.

Zakrety wykrywane sa z krzywizny toru (predkosc obrotu / predkosc) na najlepszym
okrazeniu, wiec maja te same pozycje w kazdej sesji na danym torze.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from pathlib import Path

VERSION = 5
G = 9.81
WH = ("fl", "fr", "rl", "rr")
PHASES = (("entry", "entry"), ("mid", "mid"), ("exit", "exit"))
BAL_TH = 0.35          # deg - ponizej tego balans uznajemy za neutralny


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_laps(path: Path, track_len: float):
    """Zwraca liste okrazen: dict(lap, time, rows=[dict kanalow + d])."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        head = next(r)
        ix = {k: i for i, k in enumerate(head)}
        laps, cur, cur_lap = [], [], None
        for row in r:
            try:
                lap = int(float(row[ix["lap"]]))
            except (ValueError, IndexError):
                continue
            if lap != cur_lap:
                if cur:
                    laps.append((cur_lap, cur))
                cur, cur_lap = [], lap
            cur.append(row)
        if cur:
            laps.append((cur_lap, cur))
    keys = [k for k in head]
    out = []
    for n, (lap, rows) in enumerate(laps):
        if len(rows) < 200:
            continue
        recs = []
        for row in rows:
            rec = {k: _f(row[ix[k]]) if ix[k] < len(row) else None for k in keys}
            if rec["car_et"] is None or rec["speed"] is None:
                continue
            recs.append(rec)
        if len(recs) < 200:
            continue
        # dystans: calka z predkosci, dopasowana do dlugosci toru
        d, dist = 0.0, [0.0]
        for a, b in zip(recs, recs[1:]):
            d += max(0.0, (b["car_et"] - a["car_et"])) * (a["speed"] + b["speed"]) / 2
            dist.append(d)
        sd0 = next((x["lap_dist"] for x in recs if x.get("lap_dist") is not None), None)
        sd1 = next((x["lap_dist"] for x in reversed(recs) if x.get("lap_dist") is not None), None)
        # dystans ze scoringu (5 Hz) na starcie okrazenia jeszcze pokazuje koniec poprzedniego
        if sd0 is not None and sd0 > track_len - 150:
            sd0 -= track_len
        # okrazenie wyjazdowe zaczyna sie przed linia (ujemny dystans z pit lane) - nie liczymy
        complete = (sd0 is not None and sd1 is not None and -30 < sd0 < 80 and sd1 > track_len - 80
                    and 0.95 * track_len < d < 1.05 * track_len)
        if not complete:
            continue
        k = track_len / d
        for rec, x in zip(recs, dist):
            rec["d"] = x * k
        nxt = laps[n + 1][1][0] if n + 1 < len(laps) else None
        t_end = _f(nxt[ix["car_et"]]) if nxt else recs[-1]["car_et"]
        out.append({"lap": lap, "time": t_end - recs[0]["car_et"], "rows": recs})
    return out


def _smooth(vals, k):
    out, acc, q = [], 0.0, []
    for v in vals:
        q.append(v)
        acc += v
        if len(q) > k:
            acc -= q.pop(0)
        out.append(acc / len(q))
    # wyrownanie przesuniecia
    s = k // 2
    return out[s:] + [out[-1]] * s


def _smooth_d(ds, vals, half):
    """Srednia ruchoma po dystansie (+-half m), niezalezna od czestotliwosci probek."""
    out, j0, j1, acc = [], 0, 0, 0.0
    n = len(vals)
    for i in range(n):
        while j1 < n and ds[j1] <= ds[i] + half:
            acc += vals[j1]
            j1 += 1
        while ds[j0] < ds[i] - half:
            acc -= vals[j0]
            j0 += 1
        out.append(acc / max(1, j1 - j0))
    return out


def find_corners(rows, track_len):
    """Zakrety z krzywizny |yaw / v| na okrazeniu referencyjnym (histereza + podzial
    dlugich lukow w miejscach, gdzie krzywizna wyraznie spada)."""
    ds = [r["d"] for r in rows]
    kap = _smooth_d(ds, [abs(r["yaw"] or 0) / max(r["speed"], 8.0) for r in rows], 12)
    on, off = 1 / 220.0, 1 / 380.0
    regions, start = [], None
    for i, k in enumerate(kap):
        if start is None and k > on:
            start = i
            while start > 0 and kap[start - 1] > off:
                start -= 1
        elif start is not None and k <= off:
            regions.append([start, i])
            start = None
    if start is not None:
        regions.append([start, len(kap) - 1])
    merged = []
    for a, b in regions:
        if merged and ds[a] - ds[merged[-1][1]] < 20:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    split = []
    for a, b in merged:
        stack = [(a, b)]
        while stack:
            a, b = stack.pop()
            if ds[b] - ds[a] > 350:
                inner = [i for i in range(a, b + 1) if ds[a] + 60 < ds[i] < ds[b] - 60]
                if inner:
                    m = min(inner, key=lambda i: kap[i])
                    peak = max(kap[a:b + 1])
                    if kap[m] < peak * 0.45:
                        stack += [(m, b), (a, m)]
                        continue
            split.append((a, b))
    split.sort()
    corners = []
    for a, b in split:
        if ds[b] - ds[a] < 25 or ds[b] < 30 or ds[a] > track_len - 30:
            continue
        seg = range(a, b + 1)
        ia = max(seg, key=lambda i: kap[i])
        iv = min(seg, key=lambda i: rows[i]["speed"])
        apex = ds[iv] if rows[iv]["speed"] < rows[a]["speed"] * 0.97 else ds[ia]
        d0, d1 = ds[a], ds[b]
        # wejscie zaczyna sie od hamowania, jesli bylo do 250 m przed zakretem
        k = a
        while k > 0 and ds[a] - ds[k] < 250 and (rows[k]["brake"] or 0) <= 0.05:
            k -= 1
        if (rows[k]["brake"] or 0) > 0.05:
            j = k
            while j > 0 and ds[a] - ds[j] < 300 and (rows[j]["brake"] or 0) > 0.05:
                j -= 1
            if not corners or ds[j] > corners[-1]["d1"]:
                d0 = min(d0, ds[j])
        corners.append({"d0": round(d0, 1), "apex": round(apex, 1), "d1": round(min(d1 + 20, track_len), 1),
                        "dir": "L" if sum(rows[i]["ax"] or 0 for i in seg) > 0 else "P"})
    for n, c in enumerate(corners, 1):
        c["name"] = f"C{n}"
    return corners


def _phase_bounds(c):
    half = max(10.0, (c["d1"] - c["d0"]) * 0.12)
    return {"entry": (c["d0"], c["apex"] - half), "mid": (c["apex"] - half, c["apex"] + half),
            "exit": (c["apex"] + half, c["d1"])}


def _stats(rows):
    """Metryki dla probek jednej fazy zakretu (jednego okrazenia)."""
    cn = [r for r in rows if r["speed"] > 12 and abs(r["ax"] or 0) > 2.5
          and min(r["fl_load"], r["fr_load"], r["rl_load"], r["rr_load"]) > 0]
    out = {"n": len(rows)}
    if rows:
        out["t"] = rows[-1]["car_et"] - rows[0]["car_et"]
        out["vmin"] = min(r["speed"] for r in rows) * 3.6
        out["lat_g"] = statistics.fmean(abs(r["ax"] or 0) for r in rows) / G
        out["long_g"] = statistics.fmean((r["az"] or 0) for r in rows) / G
        out["thr"] = statistics.fmean(r["throttle"] or 0 for r in rows)
        out["brk"] = statistics.fmean(r["brake"] or 0 for r in rows)
        out["rh_f"] = min(min(r["fl_rh"], r["fr_rh"]) for r in rows) * 100
        out["rh_r"] = min(min(r["rl_rh"], r["rr_rh"]) for r in rows) * 100
        out["tc"] = statistics.fmean(r.get("tc_active") or 0 for r in rows)
        out["abs"] = statistics.fmean(r.get("abs_active") or 0 for r in rows)
    if len(cn) >= 5:
        af = [(abs(r["fl_slip"]) + abs(r["fr_slip"])) / 2 for r in cn]
        ar = [(abs(r["rl_slip"]) + abs(r["rr_slip"])) / 2 for r in cn]
        out["slip_f"] = statistics.fmean(af)
        out["slip_r"] = statistics.fmean(ar)
        out["bal"] = out["slip_f"] - out["slip_r"]
        tot = [r["fl_load"] + r["fr_load"] + r["rl_load"] + r["rr_load"] for r in cn]
        out["front"] = statistics.fmean((r["fl_load"] + r["fr_load"]) / t for r, t in zip(cn, tot)) * 100
        outside = []
        lltd = []
        for r, t in zip(cn, tot):
            left = (r["ax"] or 0) > 0          # przyspieszenie w lewo = zakret w lewo
            o = (r["fr_load"] + r["rr_load"]) if left else (r["fl_load"] + r["rl_load"])
            outside.append(o / t)
            tf, tr = abs(r["fl_load"] - r["fr_load"]), abs(r["rl_load"] - r["rr_load"])
            if tf + tr > 500:
                lltd.append(tf / (tf + tr))
        out["outside"] = statistics.fmean(outside) * 100
        if lltd:
            out["lltd"] = statistics.fmean(lltd) * 100
        out["grip_f"] = statistics.fmean((r["fl_grip"] + r["fr_grip"]) / 2 for r in cn)
        out["grip_r"] = statistics.fmean((r["rl_grip"] + r["rr_grip"]) / 2 for r in cn)
    return out


def _median_dicts(ds):
    keys = set().union(*ds) if ds else set()
    out = {}
    for k in keys:
        vals = [d[k] for d in ds if d.get(k) is not None]
        if vals:
            out[k] = statistics.median(vals)
    return out


def _slice(rows, a, b):
    return [r for r in rows if a <= r["d"] <= b]


def hint(phase, bal, lltd=None):
    """Krotka podpowiedz ustawien dla fazy z wyraznym pod-/nadsterowaniem."""
    if bal is None or abs(bal) < BAL_TH:
        return ""
    us = bal > 0
    if phase == "entry":
        return ("brake bias rearward, more diff coast lock, softer front"
                if us else "brake bias forward, less diff coast / more preload, stiffer front")
    if phase == "mid":
        base = ("softer front anti-roll bar or stiffer rear, more front downforce"
                if us else "stiffer front anti-roll bar or softer rear, more rear wing")
        if lltd is not None:
            base += f" (front LLTD {lltd:.0f}%)"
        return base
    return ("less diff power lock, stiffer rear, smoother throttle"
            if us else "higher TC, more diff power / less preload, softer rear, later throttle")


def analyze(path: Path, track_len: float, corner_names=None) -> dict:
    laps = read_laps(path, track_len)
    if not laps:
        return {"version": VERSION, "error": "No complete laps in the chassis data."}
    laps.sort(key=lambda l: l["time"])
    best = laps[0]["time"]
    use = [l for l in laps if l["time"] <= best * 1.02][:3]
    corners = find_corners(use[0]["rows"], track_len)
    for c in corners:
        if corner_names:
            near = [n for n, d in corner_names if c["d0"] - 120 <= d <= c["apex"]]
            if near:
                c["name"] += f" ({near[-1]})"
        b = _phase_bounds(c)
        c["phases"] = {}
        for ph, _ in PHASES:
            st = _median_dicts([_stats(_slice(l["rows"], *b[ph])) for l in use])
            st["hint"] = hint(ph, st.get("bal"), st.get("lltd"))
            c["phases"][ph] = st
        c["time"] = statistics.median(
            [(_slice(l["rows"], c["d0"], c["d1"]) or [{"car_et": 0}])[-1]["car_et"]
             - (_slice(l["rows"], c["d0"], c["d1"]) or [{"car_et": 0}])[0]["car_et"] for l in use])
        c["vmin"] = min(p.get("vmin", 999) for p in c["phases"].values())
    # opony i przeswit na calym okrazeniu
    rows = [r for l in use for r in l["rows"]]
    tyres = {}
    for w in WH:
        tyres[w] = {k: statistics.fmean(r[f"{w}_{k}"] for r in rows if r.get(f"{w}_{k}") is not None)
                    for k in ("t_in", "t_mid", "t_out", "press", "carcass")}
        tyres[w]["brake_max"] = max(r[f"{w}_brake_t"] for r in rows)
        tyres[w]["camber"] = math.degrees(statistics.fmean(r[f"{w}_camber"] for r in rows))
        tyres[w]["wear"] = min(r[f"{w}_wear"] for r in rows)
    fast = sorted(rows, key=lambda r: r["speed"])[int(len(rows) * 0.9):]
    ride = {
        "min_f": min(min(r["fl_rh"], r["fr_rh"]) for r in rows) * 100,
        "min_r": min(min(r["rl_rh"], r["rr_rh"]) for r in rows) * 100,
        "fast_f": statistics.fmean((r["fl_rh"] + r["fr_rh"]) / 2 for r in fast) * 100,
        "fast_r": statistics.fmean((r["rl_rh"] + r["rr_rh"]) / 2 for r in fast) * 100,
        "bottom": sum(1 for r in rows if min(r["fl_rh"], r["fr_rh"], r["rl_rh"], r["rr_rh"]) < 0.004)
                  / max(1, len(use)),
    }
    return {"version": VERSION, "laps": [{"lap": l["lap"], "time": l["time"]} for l in use],
            "all_laps": len(laps), "corners": corners, "tyres": tyres, "ride": ride}


def cached(path: Path, track_len: float, cache_dir: Path, corner_names=None) -> dict:
    st = path.stat()
    cpath = Path(cache_dir) / (path.name.replace(".chassis.csv.gz", "") + ".json")
    sig = [VERSION, st.st_size, int(st.st_mtime), round(track_len, 1)]
    try:
        c = json.loads(cpath.read_text(encoding="utf-8"))
        if c.get("sig") == sig:
            return c["data"]
    except (OSError, ValueError, KeyError):
        pass
    data = analyze(path, track_len, corner_names)
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"sig": sig, "data": data}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def match(corners_a, corners_b):
    """Para zakretow z dwoch sesji po polozeniu szczytu (apex)."""
    out = {}
    for c in corners_a:
        best = min(corners_b, key=lambda o: abs(o["apex"] - c["apex"]), default=None)
        if best and abs(best["apex"] - c["apex"]) < 60:
            out[c["name"]] = best
    return out


def describe_bal(v):
    if v is None:
        return "—"
    if abs(v) < BAL_TH:
        return f"neutral {v:+.1f}°"
    return f"{'understeer' if v > 0 else 'oversteer'} {abs(v):.1f}°"
