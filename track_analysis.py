"""Analiza po torze: najlepsze okrazenia z wielu sesji + filtr rywali.

Przyklad:
    python track_analysis.py --track "Paul Ricard - 1A" --player "Marek" \
        --filter faster_or_sector --top 8 --out analysis/track_paul_ricard

Filtr:
    all                - wszyscy
    faster             - tylko kierowcy z najlepszym okrazeniem szybszym od Twojego
    faster_or_sector   - szybsi LUB majacy przynajmniej jeden szybszy sektor

Sektory sa wyliczane z telemetrii (0/1/2) dla istniejacych nagran. Nowe
nagrania beda mialy tez pola best/last sector ze scoringu.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import glob
import html as html_lib
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import analyze
import ai_coach

BASE = Path(__file__).resolve().parent
RECORDS = BASE / "records"
ANALYSIS = BASE / "analysis"
MAX_DRIVERS = 8


def fnum(s, default=None):
    try:
        if s is None or s == "":
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def txt(s):
    return (s or "").strip()


def fmt_lap(t):
    if t is None:
        return "—"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def meta_for(path: Path) -> dict:
    try:
        return json.loads(path.with_name(path.name[:-7] + ".metadata.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def track_slug(track: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", track).strip("_")[:80] or "track"


def recording_files(track: str):
    out = []
    for p in sorted(RECORDS.glob("*.csv.gz")):
        meta = meta_for(p)
        if meta.get("track") == track:
            out.append((p, meta))
        elif not meta.get("track"):
            # awaryjnie sprawdz pierwszy wiersz
            try:
                with analyze.open_csv(str(p)) as fh:
                    rd = csv.DictReader(fh)
                    row = next(rd)
                if (row.get("track") or "") == track:
                    out.append((p, meta))
            except Exception:
                pass
    return out


class LapState:
    __slots__ = ("start", "lap", "car_id", "driver", "vehicle", "file",
                 "min_d", "max_d", "last_et", "n", "first_sector")

    def __init__(self, start, lap, car_id, driver, vehicle, file, d, et, sec):
        self.start = start
        self.lap = lap
        self.car_id = car_id
        self.driver = driver
        self.vehicle = vehicle
        self.file = file
        self.min_d = d
        self.max_d = d
        self.last_et = et
        self.n = 1
        self.first_sector = {0: start, 1: None, 2: None}
        if sec in self.first_sector and self.first_sector[sec] is None:
            self.first_sector[sec] = et


def norm_sector(raw):
    try:
        v = int(float(raw or 0))
    except Exception:
        return None
    v &= 0x7FFFFFFF
    return v if v in (0, 1, 2) else None


def finalize(st: LapState, end_et: float, track: str, track_len: float,
             last1=None, last2=None):
    dur = end_et - st.start
    if dur <= 5:
        return None
    cov = (st.max_d - st.min_d) / track_len if track_len else 0
    if cov < 0.97 or st.min_d > 0.03 * track_len or st.max_d < 0.97 * track_len:
        return None
    if dur < 15 or dur > 1800 or st.n < 50:
        return None
    t1 = st.first_sector.get(1)
    t2 = st.first_sector.get(2)
    if last1 and last2 and 0 < last1 < last2 < dur:
        s1, s2, s3 = last1, last2 - last1, dur - last2
    else:
        s1 = (t1 - st.start) if t1 is not None else None
        s2 = (t2 - t1) if (t1 is not None and t2 is not None) else None
        s3 = (end_et - t2) if t2 is not None else None
    return {
        "track": track, "file": st.file, "car_id": st.car_id,
        "driver": st.driver, "vehicle": st.vehicle,
        "lap": st.lap, "start": st.start, "duration": dur,
        "min_d": st.min_d, "max_d": st.max_d, "coverage": cov,
        "samples": st.n, "s1": s1, "s2": s2, "s3": s3,
    }


def scan_track(track: str, files):
    laps = []
    track_len = 0.0
    game_best = {}   # (plik, kierowca) -> najlepszy czas wg gry (mBestLapTime)
    for p, meta in files:
        track_len = float(meta.get("track_length_m") or track_len or 0)
        states = {}
        with analyze.open_csv(str(p)) as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                row_track = row.get("track") or meta.get("track") or ""
                if row_track != track:
                    continue
                car = row.get("car_id") or row.get("slot") or ""
                drv = row.get("driver") or ""
                key = (car, drv)
                start = fnum(row.get("lap_start_et"))
                et = fnum(row.get("car_et"))
                if et is None:
                    et = fnum(row.get("session_et"))
                d = fnum(row.get("lap_dist"))
                lap = int(fnum(row.get("lap"), 0) or 0)
                b = fnum(row.get("best_lap"))
                if b and 0 < b < 1e8:
                    gk = (str(p), drv)
                    game_best[gk] = min(game_best.get(gk, 1e9), b)
                if start is None or start <= 0 or et is None or d is None or d < 0:
                    continue
                sec = norm_sector(row.get("sector"))
                st = states.get(key)
                if st is not None and (abs(st.start - start) > 1e-4 or st.lap != lap):
                    rec = finalize(st, start, track, track_len,
                                   fnum(row.get("last_sector1")),
                                   fnum(row.get("last_sector2")))
                    if rec:
                        laps.append(rec)
                    st = None
                if st is None:
                    if track_len and et - start < 3.0 and d > 0.5 * track_len:
                        d = 0.0
                    st = LapState(start, lap, car, drv, txt(row.get("vehicle")),
                                  str(p), d, et, sec)
                    states[key] = st
                else:
                    # tuz po linii scoring (5 Hz) podaje jeszcze dystans z konca poprzedniego okr.
                    if not (track_len and et - st.start < 3.0 and d > 0.5 * track_len):
                        st.min_d = min(st.min_d, d)
                        st.max_d = max(st.max_d, d)
                    st.last_et = et
                    st.n += 1
                    if txt(row.get("vehicle")):
                        st.vehicle = txt(row.get("vehicle"))
                    if sec is not None and st.first_sector.get(sec) is None:
                        st.first_sector[sec] = et
        for key, st in states.items():
            rec = finalize(st, st.last_et, track, track_len)
            if rec:
                laps.append(rec)
    # okrazenie szybsze niz rekord kierowcy wg gry = kawalek okrazenia (przerwany wyjazd, lag)
    laps = [l for l in laps if l["duration"] >= game_best.get((l["file"], l["driver"]), 0) - 0.3]
    return laps, track_len


def best_per_driver(laps):
    by = defaultdict(list)
    for lap in laps:
        by[lap["driver"]].append(lap)
    best = {}
    for driver, arr in by.items():
        arr.sort(key=lambda x: x["duration"])
        best[driver] = arr[:3]
    return best


def find_player(player, names):
    low = (player or "").lower()
    if not low:
        return None
    for n in names:
        if n.lower() == low:
            return n
    for n in names:
        if low in n.lower():
            return n
    return None


def qualifies(lap, player_best, player_sectors, mode, tol):
    if mode == "all":
        return True
    if lap["duration"] < player_best - 1e-9:
        return True
    if mode == "faster":
        return False
    for k in ("s1", "s2", "s3"):
        a = lap.get(k)
        b = player_sectors.get(k)
        if a is not None and b is not None and a < b - tol:
            return True
    return False


def collect_traces(selected, files, edges=None):
    """Zbiera probki dla wybranych okrazen (kazde z wlasnego pliku)."""
    targets = defaultdict(set)   # file -> {(driver, car_id, start, lap)}
    key_to_lap = {}
    for lap in selected:
        k = (lap["driver"], lap["car_id"], round(lap["start"], 3), lap["lap"])
        targets[lap["file"]].add(k)
        key_to_lap[k] = lap
    out = defaultdict(list)
    for p, _meta in files:
        if str(p) not in targets:
            continue
        want = targets[str(p)]
        with analyze.open_csv(str(p)) as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                if edges is not None:
                    edges.add(row)
                drv = row.get("driver") or ""
                car = row.get("car_id") or row.get("slot") or ""
                start = fnum(row.get("lap_start_et"))
                lap = int(fnum(row.get("lap"), 0) or 0)
                if start is None:
                    continue
                k = (drv, car, round(start, 3), lap)
                if k not in want:
                    continue
                d = fnum(row.get("lap_dist"))
                if d is None:
                    continue
                et = fnum(row.get("car_et"))
                if et is None:
                    et = fnum(row.get("session_et"), 0.0)
                out[k].append((
                    d, fnum(row.get("throttle"), 0.0), fnum(row.get("brake"), 0.0),
                    fnum(row.get("steering"), 0.0), int(fnum(row.get("gear"), 0) or 0),
                    fnum(row.get("speed"), 0.0), fnum(row.get("x"), 0.0),
                    fnum(row.get("z"), 0.0), et, fnum(row.get("path_lateral"), 0.0),
                ))
    return out


SESSION_TAG = (("_practice", "P"), ("_quali", "Q"), ("_race", "R"), ("_warmup", "W"))


def session_label(path) -> str:
    """'20261031_213243_..._quali.csv.gz' -> 'Q 31.10 21:32'."""
    name = Path(path).name
    tag = next((t for k, t in SESSION_TAG if k in name), "?")
    try:
        dt = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
        when = f"{dt:%d.%m %H:%M}"
    except ValueError:
        when = name[:15]
    return f"{tag} {when}"


def own_sessions(laps, player, exclude_file, n):
    """Twoje najlepsze okrazenie z kazdej innej sesji (najszybsze sesje najpierw)."""
    per = {}
    for lap in laps:
        if lap["driver"] != player or lap["file"] == exclude_file:
            continue
        if lap["file"] not in per or lap["duration"] < per[lap["file"]]["duration"]:
            per[lap["file"]] = lap
    out = {}
    for lap in sorted(per.values(), key=lambda l: l["duration"])[:max(0, n)]:
        out[f"You · {session_label(lap['file'])}"] = lap
    return out


def build(selected_names, best_by_driver, files, track, track_len, player, outdir, player_choice=None,
          own=None):
    own = own or {}
    selected = list(own.values())
    for name in selected_names:
        if name == player and player_choice is not None:
            selected.append(player_choice)
        elif name in best_by_driver:
            selected.extend(best_by_driver[name][:2])  # fallback drugie okrazenie
    edges = analyze.EdgeAcc()
    traces = collect_traces(selected, files, edges)

    drivers = []
    used = set()
    for name in list(selected_names) + list(own):
        if name in own:
            laps = [own[name]]
        elif name == player and player_choice is not None:
            laps = [player_choice]
        else:
            laps = best_by_driver.get(name, [])
        chosen = None
        for lap in laps:
            k = (lap["driver"], lap["car_id"], round(lap["start"], 3), lap["lap"])
            r = analyze.resample(traces.get(k, []), track_len, 2.0)
            if r is None:
                continue
            if chosen is None or r["coverage"] > chosen[0]:
                chosen = (r["coverage"], r, lap)
        if chosen is None:
            continue
        cov, r, lap = chosen
        zones = analyze.find_zones(r["grid"], r["brake"], r["throttle"], r["speed"])
        drivers.append({
            "name": name,
            "vehicle": lap["vehicle"],
            "car_id": lap["car_id"],
            "lap": lap["lap"],
            "lap_time": lap["duration"],
            "lap_start_et": lap["start"],
            "coverage": cov,
            "samples": lap["samples"],
            "source": Path(lap["file"]).name,
            "s1": lap.get("s1"), "s2": lap.get("s2"), "s3": lap.get("s3"),
            "grid": r["grid"], "throttle": r["throttle"], "brake": r["brake"],
            "speed": r["speed"], "gear": r["gear"], "x": r["x"], "z": r["z"],
            "lat": r["lat"], "zones": zones, "input_rate": r.get("input_rate"),
        })
        used.add(name)
    # rywale online: gaz/hamulec z sieci sa rzadkie i opoznione -> odtworzone z predkosci
    analyze.fix_remote_inputs(drivers, player)
    drivers.sort(key=lambda d: d["lap_time"])
    if not drivers:
        raise RuntimeError("Could not build traces for any driver.")
    ref_name = drivers[0]["name"]
    table = analyze.braking_table(drivers, ref_name)
    dsum = analyze.driver_summary(drivers, ref_name)

    outdir.mkdir(parents=True, exist_ok=True)
    # CSV-y
    with open(outdir / "best_laps.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "driver", "vehicle", "lap", "lap_time_s", "gap_s",
                    "s1", "s2", "s3", "coverage", "file", "lap_start_et"])
        ref_t = drivers[0]["lap_time"]
        for i, d in enumerate(drivers, 1):
            w.writerow([i, d["name"], d["vehicle"], d["lap"], f"{d['lap_time']:.3f}",
                        f"{d['lap_time'] - ref_t:.3f}",
                        "" if d["s1"] is None else f"{d['s1']:.3f}",
                        "" if d["s2"] is None else f"{d['s2']:.3f}",
                        "" if d["s3"] is None else f"{d['s3']:.3f}",
                        f"{d['coverage']:.3f}", d["source"],
                        f"{d['lap_start_et']:.3f}"])
    with open(outdir / "braking.csv", "w", encoding="utf-8", newline="") as fh:
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
    with open(outdir / "traces.csv", "w", encoding="utf-8", newline="") as fh:
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
    edges.write(str(outdir / "edges.csv"), track_len)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "track": track, "session": "track (multiple sessions)", "track_length_m": track_len,
        "player": player, "reference": ref_name,
        "inputs": [str(p) for p, _ in files],
        "drivers": [{
            "name": d["name"], "vehicle": d["vehicle"], "lap": d["lap"],
            "lap_time_s": d["lap_time"],
            "gap_s": d["lap_time"] - drivers[0]["lap_time"],
            "coverage": d["coverage"], "zones": len(d["zones"]),
            "mean_brake_delta_m": next((x["mean_brake_delta_m"] for x in dsum
                                        if x["name"] == d["name"]), None),
            "mean_pickup_delta_m": next((x["mean_pickup_delta_m"] for x in dsum
                                         if x["name"] == d["name"]), None),
        } for d in drivers],
        "braking": table,
    }
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # raport HTML
    def rnd(arr, nd):
        return [round(v, nd) if isinstance(v, float) else v for v in arr]
    drv_payload = []
    for d in drivers:
        dd = {k: v for k, v in d.items() if k != "grid"}
        for key, nd in (("throttle", 3), ("brake", 3), ("speed", 2),
                        ("x", 1), ("z", 1)):
            dd[key] = rnd(dd[key], nd)
        dd["zones"] = [{k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in z.items()} for z in dd["zones"]]
        drv_payload.append(dd)
    payload = {
        "track": track, "session": "track (multiple sessions)",
        "generated": summary["generated_at"], "track_length": round(track_len, 2),
        "step": 2.0, "grid": [round(g, 2) for g in drivers[0]["grid"]],
        "inputs": [Path(p).name for p, _ in files],
        "synthetic": False, "reference": ref_name, "player": player,
        "drivers": drv_payload, "braking": table, "summary": dsum,
    }
    tpl_path = BASE / "report_template.html"
    tpl = tpl_path.read_text(encoding="utf-8")
    page = tpl.replace("__DATA_JSON__",
                       json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                       .replace("</", "<\\/"))
    page = page.replace("__PAGE_TITLE__",
                        html_lib.escape(f"LMU telemetry — {track} / track"))
    (outdir / "report.html").write_text(page, encoding="utf-8")
    try:
        (outdir / "ai_brief.md").write_text(
            ai_coach.build_brief(outdir, player), encoding="utf-8")
    except Exception:
        pass
    return drivers, table, dsum, ref_name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-track analysis (multiple sessions)")
    ap.add_argument("--track", required=False, help="track name (exact)")
    ap.add_argument("--player", default=None, help="your driver name")
    ap.add_argument("--filter", choices=["all", "faster", "faster_or_sector"],
                    default="faster_or_sector")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--player-lap", type=int, default=1, choices=[1, 2, 3],
                    help="which of your 3 best laps to use (1=fastest)")
    ap.add_argument("--sector-tol", type=float, default=0.15,
                    help="sector tolerance [s] (5 Hz -> 0.2 s)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cls", default="all", choices=["all", "mine"],
                    help="mine = rivals from your car class only")
    ap.add_argument("--own", type=int, default=3,
                    help="how many of your laps from other sessions to add (best of each session)")
    ap.add_argument("--list-tracks", action="store_true")
    args = ap.parse_args(argv)

    if args.list_tracks:
        tracks = set()
        for p in RECORDS.glob("*.csv.gz"):
            m = meta_for(p)
            if m.get("track"):
                tracks.add(m["track"])
        for t in sorted(tracks):
            print(t)
        return 0
    if not args.track:
        print("Pass --track (or --list-tracks).")
        return 2

    files = recording_files(args.track)
    if not files:
        print(f"No recordings for track: {args.track}")
        return 2
    print(f"[TRACK] {args.track}: {len(files)} recordings")
    laps, track_len = scan_track(args.track, files)
    if not laps:
        print("No complete laps after filtering.")
        return 2
    best_by_driver = best_per_driver(laps)
    names = list(best_by_driver)
    player = find_player(args.player, names) if args.player else None
    if not player:
        print(f"Driver {args.player!r} not found. Available: {', '.join(sorted(names)[:30])}")
        return 2
    player_laps = best_by_driver[player]
    idx = min(max(args.player_lap - 1, 0), len(player_laps) - 1)
    player_choice = player_laps[idx]
    player_best = player_choice
    player_sectors = {k: player_best.get(k) for k in ("s1", "s2", "s3")}

    overall = sorted(laps, key=lambda x: x["duration"])[:3]
    print("[TRACK] 3 fastest laps on the track (everyone):")
    for i, lap in enumerate(overall, 1):
        print(f"  {i}. {fmt_lap(lap['duration'])}  {lap['driver']}  "
              f"({Path(lap['file']).name})")

    print(f"\n[TRACK] Your best laps at {args.track}:")
    for i, lap in enumerate(player_laps, 1):
        mark = "  <-- using" if i == idx + 1 else ""
        print(f"  {i}. {fmt_lap(lap['duration'])}  ({Path(lap['file']).name}, "
              f"S1={lap['s1'] and round(lap['s1'],3)}, S2={lap['s2'] and round(lap['s2'],3)}, "
              f"S3={lap['s3'] and round(lap['s3'],3)}){mark}")

    # kandydaci
    cmap = {}
    for p, meta in files:
        for v in meta.get("vehicles") or []:
            if v.get("driver"):
                cmap[(str(p), v["driver"])] = v.get("class") or ""
    pcls = cmap.get((str(player_choice["file"]), player)) if args.cls == "mine" else None
    if pcls:
        print(f"[TRACK] rivals from class {pcls} only")
    cands = []
    for name, arr in best_by_driver.items():
        if name == player:
            continue
        if pcls:
            arr = [l for l in arr if cmap.get((str(l["file"]), name)) == pcls]
            if not arr:
                continue
            best_by_driver[name] = arr
        lap = arr[0]
        if qualifies(lap, player_best["duration"], player_sectors, args.filter,
                     args.sector_tol):
            cands.append((name, lap))
    cands.sort(key=lambda x: x[1]["duration"])
    top = max(1, min(MAX_DRIVERS, args.top))
    selected_names = [player] + [n for n, _ in cands[:max(0, top - 1)]]
    selected_names = selected_names[:top]
    # pokaz kandydatow
    print(f"\n[TRACK] Filter: {args.filter}; selected ({len(selected_names)}):")
    for n in selected_names:
        lap = player_choice if n == player else best_by_driver[n][0]
        d = lap["duration"] - player_best["duration"]
        flags = []
        if n != player:
            for k in ("s1", "s2", "s3"):
                if lap.get(k) is not None and player_sectors.get(k) is not None and lap[k] < player_sectors[k] - args.sector_tol:
                    flags.append(k.upper())
        print(f"  {n:28} {fmt_lap(lap['duration'])}  ({d:+.3f} s)  "
              f"S1={lap['s1'] and round(lap['s1'],3)} S2={lap['s2'] and round(lap['s2'],3)} "
              f"S3={lap['s3'] and round(lap['s3'],3)}  faster: {','.join(flags) or '-'}")

    # Twoje okrazenia z innych sesji (paleta wykresow ma 10 kolorow)
    own = own_sessions(laps, player, player_choice["file"],
                       min(args.own, max(0, 10 - len(selected_names))))
    if own:
        print(f"\n[TRACK] Your laps from other sessions ({len(own)}):")
        for n, lap in own.items():
            print(f"  {n:28} {fmt_lap(lap['duration'])}  "
                  f"({lap['duration'] - player_best['duration']:+.3f} s)")

    outdir = Path(args.out) if args.out else ANALYSIS / f"track_{track_slug(args.track)}"
    drivers, table, dsum, ref = build(selected_names, best_by_driver, files,
                                      args.track, track_len, player, outdir,
                                      player_choice=player_choice, own=own)
    print(f"\n[TRACK] report: {outdir / 'report.html'}")
    print(f"[TRACK] reference: {ref}; drivers in the report: {len(drivers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
