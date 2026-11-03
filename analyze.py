"""Analiza telemetrii z record.py: najszybsze okrazenia i strefy hamowania.

Przyklad:
    python analyze.py records/20261031_220000_Long_Beach_quali.csv.gz --out analysis
    python analyze.py records/*.csv.gz --top 6 --grid 2

Wyniki:
    analysis/best_laps.csv    - najlepsze okrazenie kazdego kierowcy
    analysis/braking.csv      - strefy hamowania i delty wzgledem najszybszego
    analysis/traces.csv       - przeskalowane przebiegi (do Excela/MoTeC)
    analysis/summary.json
    analysis/report.html      - interaktywny raport (bez zewnetrznych bibliotek)
"""

from __future__ import annotations

import argparse
import bisect
import csv
import glob
import gzip
import html as html_lib
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime

SESSION_NAMES = {
    0: "testday", 1: "practice", 2: "practice", 3: "practice", 4: "practice",
    5: "quali", 6: "quali", 7: "quali", 8: "quali", 9: "warmup",
    10: "race", 11: "race", 12: "race", 13: "race",
}


# ------------------------------------------------------------------ pomoc
def open_csv(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def fnum(s, default=None):
    try:
        if s is None or s == "":
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def fmt_lap(t):
    if t is None:
        return "-"
    m = int(t // 60)
    s = t - m * 60
    return f"{m}:{s:06.3f}"


def lap_key(driver, car, lap, start, fallback=False):
    return (driver, car, int(lap), None if fallback else round(start, 3))


def meta_for(path: str) -> dict:
    base = path
    if base.endswith(".csv.gz"):
        base = base[:-7]
    mp = base + ".metadata.json"
    if os.path.exists(mp):
        try:
            return json.load(open(mp, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def expand_inputs(items):
    out = []
    for it in items:
        if os.path.isdir(it):
            out.extend(sorted(glob.glob(os.path.join(it, "*.csv.gz"))))
        else:
            g = glob.glob(it)
            out.extend(sorted(g) if g else [it])
    return out


# ------------------------------------------------------------------ 1. okrazenia
class LapState:
    __slots__ = ("start", "lap", "min_d", "max_d", "last_et", "n", "vehicle")

    def __init__(self, start, lap, d, et, vehicle=""):
        self.start = start
        self.lap = lap
        self.min_d = d
        self.max_d = d
        self.last_et = et
        self.n = 1
        self.vehicle = vehicle


def scan_laps(paths):
    lap_states = {}
    candidates = []
    max_d = defaultdict(float)
    min_best = defaultdict(lambda: 1e9)

    for path in paths:
        meta = meta_for(path)
        track_hint = meta.get("track", "")
        with open_csv(path) as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                track = row.get("track") or track_hint
                sess = row.get("session_id") or ""
                car = row.get("car_id") or row.get("slot") or ""
                drv = row.get("driver") or ""
                key = (track, sess, car, drv)
                d = fnum(row.get("lap_dist"))
                start = fnum(row.get("lap_start_et"))
                et = fnum(row.get("car_et"))
                if et is None:
                    et = fnum(row.get("session_et"))
                lap = int(fnum(row.get("lap"), 0) or 0)
                if d is None or start is None or et is None:
                    continue
                track_key = (track, sess)
                if d >= 0:
                    max_d[track_key] = max(max_d[track_key], d)
                b = fnum(row.get("best_lap"), 0)
                if b and 0 < b < 1e8:
                    min_best[drv] = min(min_best[drv], b)
                if start <= 0 or d < 0:
                    continue

                st = lap_states.get(key)
                if st is not None and (abs(st.start - start) > 1e-4 or st.lap != lap):
                    dur = start - st.start
                    if dur > 5:
                        candidates.append({
                            "track": track, "session": sess, "car_id": car,
                            "driver": drv, "vehicle": st.vehicle,
                            "lap": st.lap, "start": st.start, "duration": dur,
                            "min_d": st.min_d, "max_d": st.max_d,
                            "last_et": st.last_et, "samples": st.n,
                            "file": path, "start_fallback": False,
                        })
                    st = None
                # tuz po linii scoring (5 Hz) podaje jeszcze dystans z konca poprzedniego
                # okrazenia - w 3 s nie da sie przejechac 500 m
                lag = et - start < 3.0 and d > 500
                if st is None:
                    st = LapState(start, lap, 0.0 if lag else d, et, row.get("vehicle", ""))
                    lap_states[key] = st
                else:
                    if not lag:
                        st.min_d = min(st.min_d, d)
                        st.max_d = max(st.max_d, d)
                    st.last_et = et
                    st.n += 1
                    if row.get("vehicle"):
                        st.vehicle = row.get("vehicle")

    # domkniecie ostatnich okrazen (bez nastepnego startu - tylko jesli pelne)
    for key, st in lap_states.items():
        track, sess, car, drv = key
        dur = st.last_et - st.start
        if dur > 5:
            candidates.append({
                "track": track, "session": sess, "car_id": car,
                "driver": drv, "vehicle": st.vehicle, "lap": st.lap,
                "start": st.start, "duration": dur,
                "min_d": st.min_d, "max_d": st.max_d,
                "last_et": st.last_et, "samples": st.n, "file": "",
                "start_fallback": False,
            })
    return candidates, max_d, min_best


def scan_laps_by_number(paths, covered_keys):
    """Fallback: segmentuje okrazenia po mLapNumber / zawinieciu dystansu.

    Przydaje sie, gdy zdalne auta maja lap_start_et == 0.
    """
    states = {}
    out = []

    def close(key, st):
        if st and st["n"] > 5:
            track, sess, car, drv = key
            out.append({
                "track": track, "session": sess, "car_id": car,
                "driver": drv, "vehicle": st["vehicle"], "lap": st["lap"],
                "start": st["first_et"],
                "duration": st["last_et"] - st["first_et"],
                "min_d": st["min_d"], "max_d": st["max_d"],
                "last_et": st["last_et"], "samples": st["n"], "file": "",
                "start_fallback": True,
            })

    for path in paths:
        meta = meta_for(path)
        track_hint = meta.get("track", "")
        with open_csv(path) as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                track = row.get("track") or track_hint
                sess = row.get("session_id") or ""
                car = row.get("car_id") or row.get("slot") or ""
                drv = row.get("driver") or ""
                key = (track, sess, car, drv)
                if key in covered_keys:
                    continue
                d = fnum(row.get("lap_dist"))
                et = fnum(row.get("car_et"))
                if et is None:
                    et = fnum(row.get("session_et"))
                lap = int(fnum(row.get("lap"), 0) or 0)
                if d is None or et is None or d < 0:
                    continue
                st = states.get(key)
                new_lap = st is None or st["lap"] != lap
                if st is not None and not new_lap and d < st["max_d"] - 500:
                    new_lap = True
                if new_lap:
                    close(key, st)
                    states[key] = {
                        "lap": lap, "first_et": et, "last_et": et,
                        "min_d": d, "max_d": d, "n": 1,
                        "vehicle": row.get("vehicle", ""),
                    }
                else:
                    st["min_d"] = min(st["min_d"], d)
                    st["max_d"] = max(st["max_d"], d)
                    st["last_et"] = et
                    st["n"] += 1
                    if row.get("vehicle"):
                        st["vehicle"] = row.get("vehicle")
    for key, st in states.items():
        close(key, st)
    return out


def pick_session(candidates, max_d):
    """Jesli podano wiele sesji, analizujemy najwieksza."""
    counts = defaultdict(int)
    for c in candidates:
        counts[(c["track"], c["session"])] += 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def valid_laps(candidates, track_len, key, min_cov=0.90, min_best=None):
    out = []
    best_by_key = {}
    for c in candidates:
        if (c["track"], c["session"]) != key:
            continue
        # szybciej niz rekord kierowcy wg gry = kawalek okrazenia (przerwany wyjazd)
        if min_best and c["duration"] < min_best.get(c["driver"], 0) - 0.3:
            continue
        cov = (c["max_d"] - c["min_d"]) / track_len if track_len else 0
        if c["duration"] < 15 or c["duration"] > 1800:
            continue
        if cov < min_cov:
            continue
        if c["min_d"] > 0.03 * track_len:
            continue
        if c["max_d"] < 0.97 * track_len:
            continue
        if c["samples"] < max(50, c["duration"] * 4):
            continue
        c = dict(c)
        c["coverage"] = cov
        out.append(c)
        k = (c["driver"], c["vehicle"], c["car_id"])
        if k not in best_by_key or c["duration"] < best_by_key[k]["duration"]:
            best_by_key[k] = c
    for c in out:
        k = (c["driver"], c["vehicle"], c["car_id"])
        c["is_best"] = best_by_key[k] is c
    return out, best_by_key


# ------------------------------------------------------------------ 2. probki
class EdgeAcc:
    """Granice toru z wszystkich aut: mTrackEdge (po stronie auta) w koszykach dystansu.

    path_lateral / track_edge sa liczone wzgledem tej samej, przyblizonej osi toru z gry;
    dodatnie wartosci = jedna strona, ujemne = druga.
    """

    def __init__(self, bin_m=5.0):
        self.bin = bin_m
        self.pos = defaultdict(list)
        self.neg = defaultdict(list)

    def add(self, row):
        d = fnum(row.get("lap_dist"))
        e = fnum(row.get("track_edge"))
        if d is None or e is None or abs(e) < 0.5 or abs(e) > 60:
            return
        if (row.get("in_pits") or "0") not in ("0", "False", "false", ""):
            return
        b = int(d // self.bin)
        (self.pos if e > 0 else self.neg)[b].append(e)

    def write(self, path, track_len):
        n = int(track_len // self.bin) + 1
        if not self.pos and not self.neg:
            return False

        def series(src):
            raw = [statistics.median(src[i]) if len(src.get(i, ())) >= 2 else None
                   for i in range(n)]
            known = [i for i, v in enumerate(raw) if v is not None]
            if not known:
                return None
            out = []
            for i in range(n):
                if raw[i] is not None:
                    out.append(raw[i])
                    continue
                j = bisect.bisect_left(known, i)
                a = known[max(0, j - 1)]
                b = known[min(len(known) - 1, j)]
                out.append(raw[a] if a == b else raw[a] + (raw[b] - raw[a]) * (i - a) / (b - a))
            # wygladzenie mediana z 5 koszykow
            return [statistics.median(out[max(0, i - 2):i + 3]) for i in range(n)]

        left, right = series(self.pos), series(self.neg)
        if left is None or right is None:
            return False
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["distance_m", "edge_pos_m", "edge_neg_m"])
            for i in range(n):
                w.writerow([f"{i * self.bin:.1f}", f"{left[i]:.2f}", f"{right[i]:.2f}"])
        return True


def collect_traces(paths, targets, key_filter, edges=None):
    """targets: set (driver, car_id, lap_start) -> probki."""
    out = defaultdict(list)
    for path in paths:
        with open_csv(path) as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                track = row.get("track") or ""
                sess = row.get("session_id") or ""
                if (track, sess) != key_filter:
                    continue
                if edges is not None:
                    edges.add(row)
                drv = row.get("driver") or ""
                car = row.get("car_id") or row.get("slot") or ""
                start = fnum(row.get("lap_start_et"), 0.0) or 0.0
                lap = int(fnum(row.get("lap"), 0) or 0)
                tkey = lap_key(drv, car, lap, start, fallback=(start <= 0))
                if tkey not in targets:
                    continue
                d = fnum(row.get("lap_dist"))
                if d is None:
                    continue
                et = fnum(row.get("car_et"))
                if et is None:
                    et = fnum(row.get("session_et"), 0.0)
                out[tkey].append((
                    d,
                    fnum(row.get("throttle"), 0.0),
                    fnum(row.get("brake"), 0.0),
                    fnum(row.get("steering"), 0.0),
                    int(fnum(row.get("gear"), 0) or 0),
                    fnum(row.get("speed"), 0.0),
                    fnum(row.get("x"), 0.0),
                    fnum(row.get("z"), 0.0),
                    et,
                    fnum(row.get("path_lateral"), 0.0),
                ))
    return out


def resample(samples, track_len, step):
    """Probki -> siatka po dystansie. Zwraca dict z listami (None = brak)."""
    ss = [s for s in samples if 0 <= s[0] <= track_len * 1.08]
    ss.sort(key=lambda s: s[0])
    # deduplikacja dystansu
    clean = []
    for s in ss:
        if clean and abs(s[0] - clean[-1][0]) < 1e-6:
            clean[-1] = s
        else:
            clean.append(s)
    if len(clean) < 10:
        return None
    ds = [s[0] for s in clean]
    grid = [i * step for i in range(int(track_len / step) + 1)]
    if not grid or grid[-1] < track_len - 1e-6:
        grid.append(track_len)

    def interp(d, idx, is_int=False):
        j = bisect.bisect_left(ds, d)
        if j <= 0:
            v = clean[0][idx]
        elif j >= len(clean):
            v = clean[-1][idx]
        else:
            d0, d1 = ds[j - 1], ds[j]
            f = (d - d0) / (d1 - d0) if d1 > d0 else 0.0
            v = clean[j - 1][idx] + (clean[j][idx] - clean[j - 1][idx]) * f
        if is_int:
            return int(round(v))
        return v

    thr = [max(0.0, min(1.0, interp(d, 1))) for d in grid]
    brk = [max(0.0, min(1.0, interp(d, 2))) for d in grid]
    ste = [interp(d, 3) for d in grid]
    gear = [interp(d, 4, True) for d in grid]
    spd = [max(0.0, interp(d, 5)) for d in grid]
    xs = [interp(d, 6) for d in grid]
    zs = [interp(d, 7) for d in grid]
    lat = [interp(d, 9) for d in grid] if len(clean[0]) > 9 else None
    cov = max(0.0, min(1.0, (clean[-1][0] - clean[0][0]) / track_len)) if track_len else 0.0
    return {
        "grid": grid, "throttle": thr, "brake": brk, "steering": ste,
        "gear": gear, "speed": spd, "x": xs, "z": zs, "lat": lat, "coverage": cov,
    }


def find_zones(grid, brake, throttle, speed):
    zones = []
    in_zone = False
    start_i = 0
    peak = 0.0
    min_speed = 1e9
    quiet = 0
    for i, b in enumerate(brake):
        if not in_zone:
            if b >= 0.10:
                in_zone = True
                start_i = i
                peak = b
                min_speed = speed[i]
                quiet = 0
            continue
        peak = max(peak, b)
        min_speed = min(min_speed, speed[i])
        if b <= 0.03:
            quiet += 1
            if quiet >= 3:
                end_i = i - quiet + 1
                zones.append(_zone(grid, brake, throttle, speed, start_i, end_i, peak, min_speed))
                in_zone = False
        else:
            quiet = 0
    if in_zone:
        zones.append(_zone(grid, brake, throttle, speed, start_i, len(brake) - 1,
                           peak, min_speed))
    # scal bliskie strefy (szum progu)
    merged = []
    for z in zones:
        if z is None:
            continue
        if merged and z["start_d"] - merged[-1]["end_d"] < 60:
            prev = merged[-1]
            prev["end_d"] = max(prev["end_d"], z["end_d"])
            prev["pickup_d"] = z["pickup_d"] if z["pickup_d"] is not None else prev["pickup_d"]
            if z["min_speed"] < prev["min_speed"]:
                prev["min_speed"] = z["min_speed"]
                prev["apex_d"] = z["apex_d"]
            prev["max_brake"] = max(prev["max_brake"], z["max_brake"])
            prev["length"] = prev["end_d"] - prev["start_d"]
            continue
        merged.append(z)
    return merged


def _zone(grid, brake, throttle, speed, a, b, peak, min_speed):
    if b <= a:
        return None
    length = grid[b] - grid[a]
    if length < 6 or peak < 0.15:
        return None
    apex_i = min(range(a, b + 1), key=lambda i: speed[i])
    pickup = None
    for i in range(b, min(len(grid), b + int(400 / max(grid[1] - grid[0], 0.1)))):
        if throttle[i] >= 0.85 and brake[i] <= 0.06:
            pickup = grid[i]
            break
    return {
        "start_d": grid[a], "end_d": grid[b], "length": length,
        "apex_d": grid[apex_i],
        "max_brake": peak, "min_speed": min_speed, "pickup_d": pickup,
    }


# ------------------------------------------------------------------ 3. tabela
def braking_table(drivers, ref_name):
    ref = next((d for d in drivers if d["name"] == ref_name), drivers[0])
    ref_zones = ref["zones"]
    rows = []
    for zi, rz in enumerate(ref_zones, 1):
        row = {"corner": f"B{zi}", "ref_start": rz["start_d"], "drivers": []}
        for d in drivers:
            best = None
            best_delta = 1e9
            for z in d["zones"]:
                delta = z.get("apex_d", z["start_d"]) - rz.get("apex_d", rz["start_d"])
                if abs(delta) < abs(best_delta) and abs(delta) <= 220:
                    best, best_delta = z, delta
            if best is None:
                row["drivers"].append({
                    "name": d["name"], "matched": False,
                    "brake_start": None, "delta_start": None,
                    "brake_end": None, "pickup": None, "delta_pickup": None,
                    "min_speed_kmh": None, "max_brake": None,
                })
            else:
                pickup = best.get("pickup_d")
                dp = (pickup - rz["pickup_d"]) if (pickup is not None and rz.get("pickup_d") is not None) else None
                row["drivers"].append({
                    "name": d["name"], "matched": True,
                    "brake_start": best["start_d"],
                    "delta_start": best["start_d"] - rz["start_d"],
                    "brake_end": best["end_d"],
                    "pickup": pickup,
                    "delta_pickup": dp,
                    "min_speed_kmh": best["min_speed"] * 3.6,
                    "max_brake": best["max_brake"],
                })
        rows.append(row)
    return rows


def driver_summary(drivers, ref_name):
    ref = next((d for d in drivers if d["name"] == ref_name), drivers[0])
    out = []
    for d in drivers:
        deltas = []
        pickups = []
        for rz in ref["zones"]:
            best = None
            bd = 1e9
            for z in d["zones"]:
                delta = z.get("apex_d", z["start_d"]) - rz.get("apex_d", rz["start_d"])
                if abs(delta) < abs(bd) and abs(delta) <= 220:
                    best, bd = z, delta
            if best is not None:
                deltas.append(bd)
                if best.get("pickup_d") is not None and rz.get("pickup_d") is not None:
                    pickups.append(best["pickup_d"] - rz["pickup_d"])
        out.append({
            "name": d["name"],
            "mean_brake_delta_m": statistics.mean(deltas) if deltas else None,
            "mean_pickup_delta_m": statistics.mean(pickups) if pickups else None,
            "corners": len(deltas),
        })
    return out


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description="LMU telemetry analysis (all cars)")
    ap.add_argument("inputs", nargs="+", help=".csv.gz files or folders")
    ap.add_argument("--out", default="analysis", help="output folder")
    ap.add_argument("--top", type=int, default=6, help="how many drivers to analyse (max 8)")
    ap.add_argument("--grid", type=float, default=2.0, help="distance grid step [m]")
    ap.add_argument("--min-coverage", type=float, default=0.97)
    ap.add_argument("--ref", default=None, help="force this driver as the reference")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--cls", default=None,
                    help="one car class only: class name, 'mine' = player's class, 'all' = all classes")
    args = ap.parse_args(argv)

    paths = [p for p in expand_inputs(args.inputs) if os.path.exists(p)]
    if not paths:
        print("No input files.")
        return 2
    top_n = max(1, min(8, args.top))
    os.makedirs(args.out, exist_ok=True)

    print(f"[ANALYSIS] files: {len(paths)}")
    candidates, max_d, min_best = scan_laps(paths)
    covered = {(c["track"], c["session"], c["car_id"], c["driver"])
               for c in candidates}
    fb = scan_laps_by_number(paths, covered)
    if fb:
        candidates += fb
        print(f"[ANALYSIS] fallback by lap number: +{len(fb)} laps")
    if not candidates:
        print("No laps found (no lap_start_et / lap_dist in the data).")
        return 2
    key = pick_session(candidates, max_d)
    track, sess = key

    meta = {}
    for p in paths:
        m = meta_for(p)
        if m.get("track") == track:
            meta = m
            break
    track_len = float(meta.get("track_length_m") or max_d[key] or 0.0)
    if track_len <= 0:
        print("Unknown track length (no metadata).")
        return 2
    try:
        sess_label = SESSION_NAMES.get(int(sess), sess)
    except (TypeError, ValueError):
        sess_label = sess
    if meta.get("session"):
        sess_label = meta["session"]
    if len(set((c["track"], c["session"]) for c in candidates)) > 1:
        print(f"[ANALYSIS] note: several sessions, analysing the largest: {track} / {sess_label}")

    laps, best_by_key = valid_laps(candidates, track_len, key, args.min_coverage,
                                    {k: v for k, v in min_best.items() if v < 1e8})
    if not best_by_key:
        print("No complete laps after filtering (min_coverage / samples). "
              "Try: --min-coverage 0.94")
        return 2

    best_sorted = sorted(best_by_key.values(), key=lambda c: c["duration"])
    cmap = {}
    for p in paths:
        for v in meta_for(p).get("vehicles") or []:
            if v.get("driver"):
                cmap.setdefault(v["driver"], v.get("class") or "")
    if args.cls and args.cls != "all":
        want = args.cls
        if want == "mine":
            pl = next((meta_for(p).get("player") for p in paths if meta_for(p).get("player")), None)
            want = cmap.get(pl)
        only = [c for c in best_sorted if cmap.get(c["driver"]) == want] if want else []
        if only:
            best_sorted = only
            print(f"[ANALYSIS] class {want} only: {len(only)} drivers")
        else:
            print(f"[ANALYSIS] class {args.cls!r}: no data, using all classes")
    # kierowca = unikalna nazwa (jesli ten sam kierowca w 2 autach, rozdziel)
    seen = set()
    selected = []
    for c in best_sorted:
        orig = c["driver"]
        name = orig
        if name in seen:
            name = f'{name} ({c["vehicle"] or c["car_id"]})'
        seen.add(name)
        c = dict(c)
        c["display"] = name
        c["driver_csv"] = orig
        official = min_best.get(orig)
        if official and 0 < official < 1e8:
            c["official_lap_time"] = official
        selected.append(c)
        if len(selected) >= top_n:
            break

    # pass 2: probki dla najlepszego i drugiego okrazenia kazdego wybranego
    target_laps = defaultdict(list)
    for c in selected:
        all_drv = sorted([x for x in best_by_key.values()
                          if x["driver"] == c["driver_csv"] and x["vehicle"] == c["vehicle"]
                          and x["car_id"] == c["car_id"]],
                         key=lambda x: x["duration"])
        for c2 in all_drv[:2]:
            target_laps[lap_key(c2["driver"], c2["car_id"], c2["lap"], c2["start"],
                                c2.get("start_fallback", False))].append(c2)
    targets = set(target_laps.keys())
    print(f"[ANALYSIS] reading traces for {len(selected)} drivers...")
    edges = EdgeAcc()
    traces_raw = collect_traces(paths, targets, key, edges)

    drivers = []
    for c in selected:
        name = c["display"]
        tries = target_laps.get(lap_key(c["driver_csv"], c["car_id"], c["lap"], c["start"],
                                        c.get("start_fallback", False)), [c])
        chosen = None
        for tc in tries:
            tkey = lap_key(tc["driver"], tc["car_id"], tc["lap"], tc["start"],
                           tc.get("start_fallback", False))
            r = resample(traces_raw.get(tkey, []), track_len, args.grid)
            if r is None:
                continue
            if chosen is None or r["coverage"] > chosen[0]:
                chosen = (r["coverage"], r, tc)
        if chosen is None:
            continue
        cov, r, tc = chosen
        zones = find_zones(r["grid"], r["brake"], r["throttle"], r["speed"])
        official = c.get("official_lap_time")
        lap_time = tc["duration"]
        if official and abs(official - tc["duration"]) <= 1.5:
            lap_time = official   # dokladny czas z mLastLapTime/mBestLapTime
        drivers.append({
            "name": name,
            "vehicle": c["vehicle"],
            "cls": cmap.get(c["driver_csv"], ""),
            "car_id": c["car_id"],
            "lap": tc["lap"],
            "lap_number": tc["lap"],
            "lap_time": lap_time,
            "lap_start_et": tc["start"],
            "samples": tc["samples"],
            "coverage": cov,
            "grid": r["grid"],
            "throttle": r["throttle"],
            "brake": r["brake"],
            "speed": r["speed"],
            "gear": r["gear"],
            "x": r["x"],
            "z": r["z"],
            "lat": r["lat"],
            "zones": zones,
        })
    if not drivers:
        print("Could not build traces (no samples in the laps).")
        return 2

    drivers.sort(key=lambda d: d["lap_time"])
    ref_name = args.ref if args.ref else drivers[0]["name"]
    if not any(d["name"] == ref_name for d in drivers):
        ref_name = drivers[0]["name"]

    table = braking_table(drivers, ref_name)
    dsum = driver_summary(drivers, ref_name)

    # --- CSV: najlepsze okrazenia
    laps_path = os.path.join(args.out, "best_laps.csv")
    with open(laps_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "driver", "vehicle", "lap", "lap_time_s", "gap_s",
                    "coverage", "samples", "lap_start_et"])
        ref_t = drivers[0]["lap_time"]
        for i, d in enumerate(drivers, 1):
            w.writerow([i, d["name"], d["vehicle"], d["lap"], f"{d['lap_time']:.3f}",
                        f"{d['lap_time'] - ref_t:.3f}", f"{d['coverage']:.3f}",
                        d.get("samples", ""), f"{d.get('lap_start_et', 0):.3f}"])

    # --- CSV: hamowanie
    brk_path = os.path.join(args.out, "braking.csv")
    with open(brk_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["corner", "ref_brake_start_m", "driver", "brake_start_m",
                    "delta_start_m", "brake_end_m", "pickup_m", "delta_pickup_m",
                    "min_speed_kmh", "max_brake", "matched"])
        for row in table:
            for dr in row["drivers"]:
                w.writerow([
                    row["corner"], f"{row['ref_start']:.1f}", dr["name"],
                    "" if dr["brake_start"] is None else f"{dr['brake_start']:.1f}",
                    "" if dr["delta_start"] is None else f"{dr['delta_start']:+.1f}",
                    "" if dr["brake_end"] is None else f"{dr['brake_end']:.1f}",
                    "" if dr["pickup"] is None else f"{dr['pickup']:.1f}",
                    "" if dr["delta_pickup"] is None else f"{dr['delta_pickup']:+.1f}",
                    "" if dr["min_speed_kmh"] is None else f"{dr['min_speed_kmh']:.1f}",
                    "" if dr["max_brake"] is None else f"{dr['max_brake']:.3f}",
                    "1" if dr["matched"] else "0",
                ])

    # --- CSV: przebiegi
    tr_path = os.path.join(args.out, "traces.csv")
    with open(tr_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["driver", "distance_m", "throttle", "brake", "speed_kmh",
                    "gear", "x", "z", "lat"])
        for d in drivers:
            lat = d.get("lat")
            for i, dist in enumerate(d["grid"]):
                w.writerow([d["name"], f"{dist:.1f}", f"{d['throttle'][i]:.4f}",
                            f"{d['brake'][i]:.4f}", f"{d['speed'][i] * 3.6:.2f}",
                            d["gear"][i], f"{d['x'][i]:.2f}", f"{d['z'][i]:.2f}",
                            "" if lat is None else f"{lat[i]:.2f}"])

    # --- CSV: granice toru (z wszystkich aut w nagraniu)
    if edges.write(os.path.join(args.out, "edges.csv"), track_len):
        print("[ANALYSIS] track edges: edges.csv")

    # --- summary.json
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "track": track, "session": sess_label,
        "track_length_m": track_len,
        "inputs": paths,
        "reference": ref_name,
        "drivers": [{
            "name": d["name"], "vehicle": d["vehicle"], "lap": d["lap"],
            "lap_time_s": d["lap_time"], "gap_s": d["lap_time"] - drivers[0]["lap_time"],
            "coverage": d["coverage"], "zones": len(d["zones"]),
            "mean_brake_delta_m": next((x["mean_brake_delta_m"] for x in dsum
                                        if x["name"] == d["name"]), None),
            "mean_pickup_delta_m": next((x["mean_pickup_delta_m"] for x in dsum
                                         if x["name"] == d["name"]), None),
        } for d in drivers],
        "braking": table,
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    # --- raport HTML
    if not args.no_html:
        tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "report_template.html")
        if os.path.exists(tpl_path):
            def rnd(arr, nd):
                return [round(v, nd) if isinstance(v, float) else v for v in arr]
            drv_payload = []
            for d in drivers:
                dd = {k: v for k, v in d.items() if k != "grid"}
                dd["throttle"] = rnd(dd["throttle"], 3)
                dd["brake"] = rnd(dd["brake"], 3)
                dd["speed"] = rnd(dd["speed"], 2)
                dd["x"] = rnd(dd["x"], 1)
                dd["z"] = rnd(dd["z"], 1)
                dd["zones"] = [{k: (round(v, 2) if isinstance(v, float) else v)
                                for k, v in z.items()} for z in dd["zones"]]
                drv_payload.append(dd)
            payload = {
                "track": track, "session": sess_label,
                "generated": summary["generated_at"],
                "track_length": round(track_len, 2),
                "step": args.grid,
                "grid": [round(g, 2) for g in drivers[0]["grid"]],
                "inputs": [os.path.basename(p) for p in paths],
                "synthetic": bool(meta.get("synthetic")),
                "player": meta.get("player"),
                "reference": ref_name,
                "drivers": drv_payload,
                "braking": table,
                "summary": dsum,
            }
            tpl = open(tpl_path, encoding="utf-8").read()
            page = tpl.replace("__DATA_JSON__",
                               json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
            page = page.replace("__PAGE_TITLE__",
                                html_lib.escape(f"LMU telemetry — {track} / {sess_label}"))
            out_html = os.path.join(args.out, "report.html")
            open(out_html, "w", encoding="utf-8").write(page)
            print(f"[ANALYSIS] report: {out_html}")
        else:
            print(f"[ANALYSIS] no template {tpl_path}, skipping HTML")

    # --- konsola
    print(f"\n[ANALYSIS] {track} / {sess_label} | length {track_len:.0f} m")
    print(f"{'#':>2} {'driver':28} {'lap':>4} {'time':>9} {'gap':>8} {'coverage':>9}")
    for i, d in enumerate(drivers, 1):
        print(f"{i:>2} {d['name'][:28]:28} {d['lap']:>4} {fmt_lap(d['lap_time']):>9} "
              f"{d['lap_time'] - drivers[0]['lap_time']:+8.3f} {d['coverage']*100:>8.1f}%")
    print(f"\n[ANALYSIS] saved: {laps_path}, {brk_path}, {tr_path}, summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
