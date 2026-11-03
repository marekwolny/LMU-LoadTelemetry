"""Konwersja nagran z record.py do plikow TinyPedal Best Lap (.tplap).

Plik .tplap wczytujesz w TinyPedal: Tools -> Best Lap Manager -> Import.
Okrazenie staje sie wtedy referencja dla delty i widgetu "Best lap telemetry"
(dla toru i klasy z nagrania).

Przyklady:
    python to_tinypedal.py records/plik.csv.gz                  # Twoje najlepsze okrazenie
    python to_tinypedal.py records/*.csv.gz --driver "Bacon"    # okrazenie innego kierowcy
    python to_tinypedal.py records/plik.csv.gz --top 5          # 5 najszybszych (kazdy osobny plik)
    python to_tinypedal.py records/plik.csv.gz --class LMP2_ELMS --top 3
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sys
from datetime import datetime

from analyze import (
    collect_traces,
    expand_inputs,
    fmt_lap,
    lap_key,
    meta_for,
    open_csv,
    pick_session,
    scan_laps,
    scan_laps_by_number,
    valid_laps,
)

TPLAP_FORMAT = "tinypedal-best-lap"
TPLAP_FORMAT_VERSION = 1
INVALID_CHAR = re.compile(r'[\\/:*?"<>|\0]')  # jak strip_invalid_char w TinyPedal


def strip_invalid_char(name: str) -> str:
    return INVALID_CHAR.sub("", name)


def driver_classes(paths, key):
    """driver -> klasa auta (z metadanych, w razie braku z CSV)."""
    classes = {}
    for p in paths:
        for v in meta_for(p).get("vehicles", []):
            if v.get("driver") and v.get("class"):
                classes.setdefault(v["driver"], v["class"])
    for p in paths:
        with open_csv(p) as fh:
            for row in csv.DictReader(fh):
                if (row.get("track"), row.get("session_id")) != key:
                    continue
                drv = row.get("driver") or ""
                if drv and drv not in classes and row.get("car_class"):
                    classes[drv] = row["car_class"]
    return classes


def build_lap(samples, track_len, lap_start, lap_time, step):
    """Probki (d, thr, brk, ste, gear, spd, x, z, et) -> (delta_best, telemetry)."""
    clean = []
    for s in sorted((s for s in samples if 0 <= s[0] <= track_len), key=lambda s: s[0]):
        if clean and s[0] - clean[-1][0] < 1e-6:
            clean[-1] = s
        else:
            clean.append(s)
    if len(clean) < 10:
        return None
    ds = [s[0] for s in clean]

    def interp(d, idx):
        j = bisect.bisect_left(ds, d)
        if j <= 0:
            return clean[0][idx]
        if j >= len(clean):
            return clean[-1][idx]
        d0, d1 = ds[j - 1], ds[j]
        f = (d - d0) / (d1 - d0) if d1 > d0 else 0.0
        return clean[j - 1][idx] + (clean[j][idx] - clean[j - 1][idx]) * f

    # Czas od startu okrazenia; bez lap_start_et zaczynamy od pierwszej probki
    t0 = lap_start if lap_start > 0 else clean[0][8]
    last_d = ds[-1]
    grid = [i * step for i in range(1, int(last_d / step) + 1)]
    delta = [(0.0, 0.0)]
    telemetry = []
    last_t = 0.0
    for d in grid:
        t = max(interp(d, 8) - t0, last_t)  # czas nie moze malec
        if t >= lap_time:
            break
        last_t = t
        delta.append((round(d, 2), round(t, 4)))
        telemetry.append((
            round(d, 2),
            round(min(max(interp(d, 1), 0.0), 1.0), 4),
            round(min(max(interp(d, 2), 0.0), 1.0), 4),
            round(max(interp(d, 5), 0.0), 3),
            round(min(max(interp(d, 3), -1.0), 1.0), 4),
            float(round(interp(d, 4))),
        ))
    if len(telemetry) < 10:
        return None
    delta.append((round(delta[-1][0] + 10, 2), round(lap_time, 4)))  # jak w TinyPedal
    return delta, telemetry


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nagrania LMU -> TinyPedal Best Lap (.tplap)")
    ap.add_argument("inputs", nargs="+", help="pliki .csv.gz lub katalogi")
    ap.add_argument("--out", default="tinypedal_laps", help="katalog wynikow")
    ap.add_argument("--driver", default=None,
                    help="fragment nazwy kierowcy (domyslnie: gracz z metadanych)")
    ap.add_argument("--class", dest="car_class", default=None, help="tylko ta klasa auta")
    ap.add_argument("--top", type=int, default=0,
                    help="eksportuj N najszybszych kierowcow zamiast jednego")
    ap.add_argument("--step", type=float, default=5.0, help="krok dystansu [m] (TinyPedal: 5)")
    ap.add_argument("--min-coverage", type=float, default=0.97)
    args = ap.parse_args(argv)

    paths = [p for p in expand_inputs(args.inputs) if os.path.exists(p)]
    if not paths:
        print("Brak plikow wejsciowych.")
        return 2

    candidates, max_d, min_best = scan_laps(paths)
    covered = {(c["track"], c["session"], c["car_id"], c["driver"]) for c in candidates}
    candidates += scan_laps_by_number(paths, covered)
    if not candidates:
        print("Nie znaleziono okrazen.")
        return 2
    key = pick_session(candidates, max_d)
    track, _ = key
    meta = next((m for m in map(meta_for, paths) if m.get("track") == track), {})
    track_len = float(meta.get("track_length_m") or max_d[key] or 0.0)
    if track_len <= 0:
        print("Nie znam dlugosci toru (brak metadanych).")
        return 2

    _, best_by_key = valid_laps(candidates, track_len, key, args.min_coverage)
    best = sorted(best_by_key.values(), key=lambda c: c["duration"])
    classes = driver_classes(paths, key)
    if args.car_class:
        best = [c for c in best if classes.get(c["driver"], "").lower() == args.car_class.lower()]

    if args.top > 0:
        selected = best[:args.top]
    else:
        name = args.driver or meta.get("player", "")
        if not name:
            print("Podaj --driver (brak gracza w metadanych).")
            return 2
        selected = [c for c in best if name.lower() in c["driver"].lower()][:1]
    if not selected:
        print("Brak kompletnego okrazenia dla wybranych filtrow.")
        print("Kierowcy z pelnym okrazeniem:", ", ".join(sorted({c["driver"] for c in best})) or "-")
        return 2

    targets = {lap_key(c["driver"], c["car_id"], c["lap"], c["start"], c.get("start_fallback", False))
               for c in selected}
    traces = collect_traces(paths, targets, key)
    os.makedirs(args.out, exist_ok=True)
    recorded = meta.get("started_at", "").replace("T", " ")

    for c in selected:
        tkey = lap_key(c["driver"], c["car_id"], c["lap"], c["start"], c.get("start_fallback", False))
        lap_time = c["duration"]
        official = min_best.get(c["driver"])
        if official and abs(official - lap_time) <= 1.5:
            lap_time = official  # dokladny czas z mBestLapTime
        lap_start = 0.0 if c.get("start_fallback") else c["start"]
        built = build_lap(traces.get(tkey, []), track_len, lap_start, lap_time, args.step)
        if built is None:
            print(f"[POMIJAM] {c['driver']}: za malo probek")
            continue
        delta, telemetry = built
        class_name = classes.get(c["driver"], "")
        combo = strip_invalid_char(f"{track} - {class_name}")
        data = {
            "format": TPLAP_FORMAT,
            "format_version": TPLAP_FORMAT_VERSION,
            "app_version": "LMU-LoadTelemetry",
            "combo_name": combo,
            "track_name": strip_invalid_char(track),
            "class_name": strip_invalid_char(class_name),
            "lap_time": lap_time,
            "recorded_date": recorded,
            "exported_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "driver_name": c["driver"],
            "vehicle_name": c["vehicle"],
            "delta_best": delta,
            "telemetry": telemetry,
        }
        laptime_name = f"{lap_time // 60:.0f}m{lap_time % 60:06.3f}s"
        filename = strip_invalid_char(f"{combo} - {c['driver']} - {laptime_name}.tplap")
        path = os.path.join(args.out, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        print(f"[OK] {fmt_lap(lap_time)}  {c['driver']} ({class_name}, {c['vehicle']})  "
              f"probek: {c['samples']} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
