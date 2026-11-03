"""Generuje SYNTEtyczna sesje kwalifikacyjna do testu analizatora i raportu.

To NIE sa prawdziwe dane. Sluzy do sprawdzenia pipeline'u bez uruchamiania LMU:
    python make_demo.py --out records_demo
    python analyze.py records_demo/*.csv.gz --out analysis_demo
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
from datetime import datetime

from record import FIELDS

TRACK_LEN = 4000.0
DRIVERS = [
    # nazwa, predkosc bazowa km/h, hamowanie wczesniej [m], agresywnosc gazu
    ("Demo_Aliens", 252.0, 0.0, 1.00),
    ("Demo_Fast", 249.0, 7.0, 0.96),
    ("Demo_Mid", 245.0, 18.0, 0.90),
]
FUEL_START = 60.0
FUEL_PER_LAP = {"Demo_Aliens": 2.95, "Demo_Fast": 2.85, "Demo_Mid": 2.70}   # L/okr. (syntetyczne)
CORNERS = [(430, 95), (980, 75), (1560, 110), (2180, 70), (2790, 90), (3400, 65)]


def track_point(d: float):
    """Pozycja x,z na zamknietej petli o dlugosci TRACK_LEN."""
    th = 2 * math.pi * (d % TRACK_LEN) / TRACK_LEN
    x = 520 * math.cos(th) + 170 * math.cos(3 * th + 0.4) + 45 * math.sin(5 * th)
    z = 330 * math.sin(th) + 95 * math.sin(2 * th - 0.7) + 35 * math.cos(4 * th)
    return x, z


def speed_profile(vmax_kmh: float, brake_shift_m: float, ds=1.0):
    """Profil predkosci po dystansie: hamowanie przed zakretami + przyspieszanie."""
    n = int(TRACK_LEN / ds)
    vmax = vmax_kmh / 3.6
    v = [vmax] * n
    # ograniczenia w zakretach (przesuniete o brake_shift - pozniej = mniej hamowania)
    for apex, corner_kmh in CORNERS:
        vmin = (corner_kmh + (vmax_kmh - 252) * 0.5) / 3.6
        vmin = max(vmin, 16.0)
        for i in range(n):
            d = i * ds
            # zakret ma szerokosc ~120 m, spowolnienie narasta przed apeksem
            dd = (d - (apex - brake_shift_m))
            if -80 <= dd <= 90:
                t = 1.0 - min(1.0, max(0.0, (dd + 80) / 170))
                lim = vmin + (vmax - vmin) * (1 - t) ** 0.7
                v[i] = min(v[i], lim)
    # hamowanie wstecz
    a_brake = 5.2
    for _ in range(3):
        for i in range(n - 2, -1, -1):
            v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * a_brake * ds))
        for i in range(1, n):
            v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * 2.2 * ds))
    return v


def sim_driver(name, vmax, brake_shift, gas, hz=30.0):
    """Zwraca liste probek (d, speed, throttle, brake) dla jednego okrazenia."""
    ds = 0.5
    v = speed_profile(vmax, brake_shift, ds)
    n = len(v)
    samples = []
    # pedaly z profilu przyspieszen
    prev_thr = prev_brk = 0.0
    for i in range(n):
        i2 = min(i + 1, n - 1)
        dv2 = v[i2] ** 2 - v[i] ** 2
        a = dv2 / (2 * ds)
        if a < -0.15:
            brk = min(1.0, -a / 5.2 * 1.15)
            thr = 0.0
        elif a > 0.15:
            thr = min(1.0, a / 2.2 * gas)
            brk = 0.0
        else:
            thr = 0.25
            brk = 0.0
        # wygladzanie jak nogi kierowcy
        thr = prev_thr * 0.55 + thr * 0.45
        brk = prev_brk * 0.45 + brk * 0.55
        prev_thr, prev_brk = thr, brk
        samples.append((i * ds, v[i], thr, brk))
    return samples


def resample_time(samples, hz, start_et):
    """Probkuje okrazenie po czasie (z dystansu) - jak logger 30 Hz."""
    # czas narastajacy: dt = ds / v
    t = 0.0
    times = []
    ds = samples[1][0] - samples[0][0]
    for d, v, thr, brk in samples:
        times.append(t)
        t += ds / max(v, 1.0)
    out = []
    total_t = times[-1]
    j = 0
    step = 1.0 / hz
    tt = 0.0
    while tt <= total_t:
        while j + 1 < len(times) and times[j + 1] < tt:
            j += 1
        if j + 1 >= len(times):
            break
        f = (tt - times[j]) / max(times[j + 1] - times[j], 1e-9)
        d = samples[j][0] + (samples[j + 1][0] - samples[j][0]) * f
        v = samples[j][1] + (samples[j + 1][1] - samples[j][1]) * f
        thr = samples[j][2] + (samples[j + 1][2] - samples[j][2]) * f
        brk = samples[j][3] + (samples[j + 1][3] - samples[j][3]) * f
        out.append((tt + start_et, d, v, thr, brk))
        tt += step
    return out, total_t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="records_demo")
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--hz", type=float, default=30.0)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(args.out, f"{stamp}_DemoRaceway_quali")
    path = base + ".csv.gz"
    meta_path = base + ".metadata.json"

    profiles = []
    for name, vmax, shift, gas in DRIVERS:
        prof = sim_driver(name, vmax, shift, gas, args.hz)
        profiles.append((name, prof))

    # Wspolny zegar sesji: kazdy kierowca startuje okrazenie w tym samym momencie.
    session_t = 60.0
    lap_no = 0
    rows = 0
    meta = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "track": "Demo Raceway (SYNTHETIC)",
        "track_length_m": TRACK_LEN,
        "session_id": 5,
        "session": "quali",
        "game_phase": 5,
        "game_mode": 2,
        "player": "Demo_Mid",
        "server": "DEMO (synthetic data)",
        "vehicles": [{"id": i + 1, "driver": n, "vehicle": "Demo Car", "class": "GT3",
                      "is_player": n == "Demo_Mid", "control": 2}
                     for i, (n, _) in enumerate(profiles)],
        "synthetic": True,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FIELDS)
        # czasy okrazen i sektorow jak w scoringu gry (last_sector2 = S1+S2, narastajaco)
        done = {name: {"best": 0.0, "last": 0.0, "s1": 0.0, "s2": 0.0} for name, _ in profiles}
        for lap in range(args.laps):
            lap_start = session_t
            resampled = []
            max_t = 0
            for idx, (name, prof) in enumerate(profiles):
                samples, total_t = resample_time(prof, args.hz, lap_start)
                # niewielka roznica czasu okrazenia miedzy kierowcami
                resampled.append((name, samples))
                max_t = max(max_t, total_t)
            # dopisz probki az do najdluzszego okrazenia
            for i, (name, samples) in enumerate(resampled):
                last = samples[-1] if samples else (lap_start, 0.0, 0.0, 0.0, 0.0)
                prev = done[name]
                cut1 = next((s[0] for s in samples if s[1] >= TRACK_LEN / 3), lap_start) - lap_start
                cut2 = next((s[0] for s in samples if s[1] >= TRACK_LEN * 2 / 3), lap_start) - lap_start
                for (et, d, v, thr, brk) in samples:
                    x, z = track_point(d)
                    gear = max(1, min(6, 1 + int(v * 3.6 / 45)))
                    row = {
                        "wall": 1793440000.0 + et,
                        "session_et": et, "car_et": et, "track": meta["track"],
                        "session_id": 5, "phase": 5, "mode": 2,
                        "slot": i, "car_id": i + 1, "driver": name,
                        "vehicle": "Demo Car", "car_class": "GT3", "place": i + 1,
                        "control": 2, "is_player": name == "Demo_Mid",
                        "lap": lap + 1, "lap_start_et": lap_start,
                        "lap_dist": d, "path_lateral": 0.0, "track_edge": 4.0,
                        "best_lap": prev["best"], "last_lap": prev["last"],
                        "best_sector1": prev["s1"], "best_sector2": prev["s2"],
                        "last_sector1": prev["s1"], "last_sector2": prev["s2"],
                        "throttle": thr, "brake": brk, "steering": 0.0,
                        "gear": gear, "rpm": 4000 + v * 60,
                        "speed": v, "vel_z": -v, "x": x, "y": 0.0, "z": z,
                        "fuel": FUEL_START - (lap + d / TRACK_LEN) * FUEL_PER_LAP[name],
                        "in_pits": False, "sector": int(d / TRACK_LEN * 3),
                        "delta_best": 0.0, "virtual_energy": 1.0, "soc": 0.0,
                        "tc": 0, "abs": 0, "tc_active": False, "abs_active": False,
                        "lap_invalid": False,
                    }
                    w.writerow([row.get(k, "") for k in FIELDS])
                    rows += 1
                t_lap = last[0] - lap_start
                done[name] = {"best": t_lap, "last": t_lap, "s1": cut1, "s2": cut2}
            session_t += max_t + 0.4
            lap_no += 1
    print(f"demo: {path} ({rows} wierszy), meta: {meta_path}")
    print("UWAGA: dane syntetyczne, nie z LMU.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
