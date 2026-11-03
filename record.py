"""Logger telemetrii WSZYSTKICH aut z Le Mans Ultimate.

Dziala:
  * w trakcie sesji online/offline (w kwalifikacjach, wyścigu itd.),
  * podczas odtwarzania replaya w grze (wtedy dane sa z zapisu),
  * nie wymaga zadnego pluginu - czyta oficjalne shared memory LMU_Data
    (Settings > Gameplay > Enable Plugins / "Enable external plugins").

Przyklad:
    python record.py --probe-seconds 10        # diagnostyka rywali
    python record.py --out records --hz 30     # nagrywanie
    python record.py --out records --hz 30 --driver "Marek"
    python record.py --dump "C:/.../shm_track.bin" --out records   # test

Plik wynikowy: records/RRRRMMDD_HHMMSS_<tor>_<sesja>.csv.gz
Obok: .chassis.csv.gz - podwozie TWOJEGO auta z pelna czestotliwoscia (naciski kol, zawieszenie,
przeswit, poslizg opon, temperatury, przeciazenia) do analizy prowadzenia,
.metadata.json z dlugoscia toru i lista aut oraz .setupN.svm - kopia setupu auta
(UserData/player/TempModFile.svm) z chwili startu i z kazdej zmiany w trakcie sesji.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import signal
import sys
import time
from datetime import datetime

import lmu_shm as L

SESSION_NAMES = {
    0: "testday",
    1: "practice", 2: "practice", 3: "practice", 4: "practice",
    5: "quali", 6: "quali", 7: "quali", 8: "quali",
    9: "warmup",
    10: "race", 11: "race", 12: "race", 13: "race",
}

FIELDS = [
    "wall", "session_et", "car_et", "track", "session_id", "phase", "mode",
    "slot", "car_id", "driver", "vehicle", "car_class", "place", "control",
    "is_player", "lap", "lap_start_et", "lap_dist", "path_lateral", "track_edge",
    "best_lap", "last_lap", "throttle", "brake", "steering", "gear", "rpm",
    "speed", "vel_z", "x", "y", "z", "fuel", "in_pits", "sector",
    "delta_best", "virtual_energy", "soc", "tc", "abs",
    "best_sector1", "best_sector2", "last_sector1", "last_sector2",
    "cur_sector1", "cur_sector2",
    "tc_active", "abs_active", "lap_invalid",
]


def txt(b) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


def sanitize(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return s[:60] or "session"


def fmt_lap(t):
    if t is None or not math.isfinite(t) or t <= 0:
        return "-"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.{nd}f}"
    return v


class SessionWriter:
    def __init__(self, out_dir: str, track: str, session_id: int, meta: dict):
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_{sanitize(track)}_{SESSION_NAMES.get(session_id, str(session_id))}"
        self.path = os.path.join(out_dir, name + ".csv.gz")
        self.meta_path = os.path.join(out_dir, name + ".metadata.json")
        self.out_dir, self.name, self.meta = out_dir, name, meta
        self.setups: list[dict] = []
        self.last_setup_text = None
        self.chassis = ChassisWriter(self.path)
        self.rows = 0
        self._raw = gzip.open(self.path, "wt", encoding="utf-8", newline="", compresslevel=6)
        self._csv = csv.writer(self._raw, lineterminator="\n")
        self._csv.writerow(FIELDS)
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[REC] nowy plik: {self.path}")

    def add_setup(self, text: str, wall: float, session_et: float) -> None:
        n = len(self.setups) + 1
        fname = f"{self.name}.setup{n}.svm"
        with open(os.path.join(self.out_dir, fname), "w", encoding="utf-8") as f:
            f.write(text)
        entry = {"file": fname, "wall": round(wall, 3),
                 "session_et": None if n == 1 else round(session_et, 2)}
        try:
            import lmu_setup
            ps = lmu_setup.parse(text)
            entry["vehicle"] = ps["vehicle"]
            entry["name"] = lmu_setup.describe(ps)
        except Exception:
            pass
        self.setups.append(entry)
        self.last_setup_text = text
        self.meta["setups"] = self.setups
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)
        print(f"[REC] setup #{n}: {entry.get('name', '?')} ({entry.get('vehicle', '')})")

    def write(self, row: dict) -> None:
        self._csv.writerow([fmt(row.get(k)) for k in FIELDS])
        self.rows += 1

    def flush(self) -> None:
        try:
            self._raw.flush()
            self.chassis._raw.flush()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass
        self.chassis.close()

    def size_mb(self) -> float:
        try:
            return os.path.getsize(self.path) / 1e6
        except OSError:
            return 0.0


WHEELS = ("fl", "fr", "rl", "rr")
CHASSIS_FIELDS = (["wall", "session_et", "car_et", "lap", "lap_dist", "speed", "throttle", "brake",
                   "steering", "gear", "rpm", "ax", "ay", "az", "yaw", "vx", "vz",
                   "rh_f", "rh_r", "defl3_f", "defl3_r", "df_f", "df_r", "drag", "tc_active",
                   "abs_active"]
                  + [f"{w}_{k}" for w in WHEELS for k in
                     ("load", "defl", "rh", "grip", "slip", "lat_f", "lon_f", "t_in", "t_mid",
                      "t_out", "carcass", "press", "brake_t", "camber", "wear")])


def chassis_row(t, sc, wall: float) -> list:
    """Wiersz podwozia z TelemInfoV01 gracza (uklad lokalny rF2: +x lewo, +y gora, +z tyl)."""
    vel = t.mLocalVel
    row = [wall, float(sc.mCurrentET), float(t.mElapsedTime), int(t.mLapNumber), None,
           math.sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2), float(t.mUnfilteredThrottle),
           float(t.mUnfilteredBrake), float(t.mUnfilteredSteering), int(t.mGear),
           float(t.mEngineRPM), float(t.mLocalAccel.x), float(t.mLocalAccel.y),
           float(t.mLocalAccel.z), float(t.mLocalRot.y), float(vel.x), float(vel.z),
           float(t.mFrontRideHeight), float(t.mRearRideHeight), float(t.mFront3rdDeflection),
           float(t.mRear3rdDeflection), float(t.mFrontDownforce), float(t.mRearDownforce),
           float(t.mDrag), int(bool(t.mTCActive)), int(bool(t.mABSActive))]
    for i in range(4):
        w = t.mWheel[i]
        lon = abs(float(w.mLongitudinalGroundVel))
        slip = math.degrees(math.atan2(float(w.mLateralGroundVel), max(lon, 1.0)))
        tl, tm, tr = (float(x) - 273.15 for x in w.mTemperature)
        t_in, t_out = (tr, tl) if i in (0, 2) else (tl, tr)   # lewe kolo: lewa krawedz = zewn.
        row += [float(w.mTireLoad), float(w.mSuspensionDeflection), float(w.mRideHeight),
                float(w.mGripFract), slip, float(w.mLateralForce), float(w.mLongitudinalForce),
                t_in, tm, t_out, float(w.mTireCarcassTemperature) - 273.15, float(w.mPressure),
                float(w.mBrakeTemp) - 273.15, float(w.mCamber), float(w.mWear)]
    return row


class ChassisWriter:
    """<nagranie>.chassis.csv.gz - tylko auto gracza, kazda nowa klatka telemetrii."""

    def __init__(self, base_path: str):
        self.path = base_path[:-7] + ".chassis.csv.gz"
        self._raw = gzip.open(self.path, "wt", encoding="utf-8", newline="", compresslevel=6)
        self._csv = csv.writer(self._raw, lineterminator="\n")
        self._csv.writerow(CHASSIS_FIELDS)
        self.rows = 0
        self.last = None

    def write(self, sm, wall: float, lap_dist) -> None:
        tel = sm.telemetry
        if not tel.playerHasVehicle or tel.playerVehicleIdx >= min(tel.activeVehicles, L.MAX_VEHICLES):
            return
        t = tel.telemInfo[tel.playerVehicleIdx]
        et = float(t.mElapsedTime)
        if et == self.last:
            return
        self.last = et
        row = chassis_row(t, sm.scoring.scoringInfo, wall)
        row[4] = lap_dist
        self._csv.writerow([fmt(v, 5) for v in row])
        self.rows += 1

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass
        if self.rows == 0:
            try:
                os.remove(self.path)
            except OSError:
                pass


class LapWatch:
    """Zdarzenie [LAP] {json} po kazdym okrazeniu Twojego auta - dla inzyniera glosowego / panelu VR."""

    def __init__(self):
        self.lap = None
        self.start = None          # (paliwo, energia) na starcie okrazenia
        self.from_pit = False
        self.pitted = False

    def poll(self, sm, vs) -> None:
        tel = sm.telemetry
        pv = None
        if tel.playerHasVehicle:
            pv = next((v for v in vs if v["slot"] == int(tel.playerVehicleIdx)), None)
        if pv is None:
            pv = next((v for v in vs if v["is_player"]), None)
        if pv is None:
            return
        fuel, ve = pv["fuel"], pv["virtual_energy"]
        if pv["in_pits"]:
            self.pitted = True
        if self.lap is None:
            self.lap, self.start, self.from_pit = pv["lap"], (fuel, ve), pv["in_pits"]
            return
        if pv["lap"] == self.lap:
            if fuel > (self.start[0] or 0) + 0.5:          # tankowanie
                self.start, self.from_pit = (fuel, ve), True
            return
        sc = sm.scoring.scoringInfo
        f0, e0 = self.start
        ev = {
            "lap": self.lap, "time": pv["last_lap"], "best": pv["best_lap"],
            "fuel": fuel, "fuel_used": (f0 - fuel) if f0 is not None else None,
            "ve": ve, "ve_used": (e0 - ve) if e0 is not None else None,
            "out_lap": self.from_pit, "in_lap": self.pitted and not self.from_pit,
            "invalid": pv["lap_invalid"], "place": pv["place"], "driver": pv["driver"],
            "session": SESSION_NAMES.get(int(sc.mSession), str(int(sc.mSession))),
            "et": float(sc.mCurrentET), "end_et": float(sc.mEndET), "max_laps": int(sc.mMaxLaps),
            "track_len": float(sc.mLapDist),
        }
        print("[LAP] " + json.dumps(ev, ensure_ascii=False), flush=True)
        self.lap, self.start = pv["lap"], (fuel, ve)
        self.from_pit, self.pitted = pv["in_pits"], pv["in_pits"]


class SetupWatch:
    """Kopiuje TempModFile.svm (setup, ktorym jedziesz) do nagrania przy kazdej zmianie."""

    def __init__(self):
        try:
            import lmu_setup
            self.src = lmu_setup.temp_setup_path()
        except Exception:
            self.src = None
        self.mtime = None
        self.next = 0.0

    def poll(self, writer, session_et: float, now: float, force: bool = False) -> None:
        if not self.src or writer is None or (not force and now < self.next):
            return
        self.next = now + 2.0
        try:
            mt = os.path.getmtime(self.src)
            if not force and mt == self.mtime:
                return
            self.mtime = mt
            with open(self.src, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            return
        if text and text != writer.last_setup_text:
            try:
                writer.add_setup(text, now, session_et)
            except OSError as e:
                print(f"[REC] nie udalo sie zapisac setupu: {e}")


def build_meta(sm, wall: float) -> dict:
    sc = sm.scoring.scoringInfo
    veh = []
    for i in range(min(int(sc.mNumVehicles), L.MAX_VEHICLES)):
        v = sm.scoring.vehScoringInfo[i]
        veh.append({
            "id": int(v.mID),
            "driver": txt(v.mDriverName),
            "vehicle": txt(v.mVehicleName),
            "class": txt(v.mVehicleClass),
            "is_player": bool(v.mIsPlayer),
            "control": int(v.mControl),
        })
    return {
        "started_at": datetime.fromtimestamp(wall).isoformat(timespec="seconds"),
        "track": txt(sc.mTrackName),
        "track_length_m": float(sc.mLapDist),
        "session_id": int(sc.mSession),
        "session": SESSION_NAMES.get(int(sc.mSession), str(int(sc.mSession))),
        "game_phase": int(sc.mGamePhase),
        "game_mode": int(sc.mGameMode),
        "player": txt(sc.mPlayerName),
        "server": txt(sc.mServerName),
        "vehicles": veh,
    }


def row_from_vehicle(v: dict, sm, wall: float) -> dict:
    sc = sm.scoring.scoringInfo
    vel = v["vel"]
    return {
        "wall": wall,
        "session_et": float(sc.mCurrentET),
        "car_et": v["elapsed"],
        "track": v["track"],
        "session_id": int(sc.mSession),
        "phase": int(sc.mGamePhase),
        "mode": int(sc.mGameMode),
        "slot": v["slot"],
        "car_id": v["id"],
        "driver": v["driver"],
        "vehicle": v["vehicle"],
        "car_class": v["class"],
        "place": v["place"],
        "control": v["control"],
        "is_player": v["is_player"],
        "lap": v["lap"],
        "lap_start_et": v["lap_start_et"],
        "lap_dist": v["lap_dist"],
        "path_lateral": v["path_lateral"],
        "track_edge": v["track_edge"],
        "best_lap": v["best_lap"],
        "last_lap": v["last_lap"],
        "throttle": v["throttle"],
        "brake": v["brake"],
        "steering": v["steering"],
        "gear": v["gear"],
        "rpm": v["rpm"],
        "speed": v["speed"],
        "vel_z": vel[2],
        "x": v["pos"][0],
        "y": v["pos"][1],
        "z": v["pos"][2],
        "fuel": v["fuel"],
        "in_pits": v["in_pits"],
        "sector": v["sector"],
        "delta_best": v["delta_best"],
        "virtual_energy": v["virtual_energy"],
        "soc": v["soc"],
        "tc": v["tc"],
        "abs": v["abs"],
        "tc_active": v["tc_active"],
        "abs_active": v["abs_active"],
        "lap_invalid": v["lap_invalid"],
        "best_sector1": v.get("best_sector1"),
        "best_sector2": v.get("best_sector2"),
        "last_sector1": v.get("last_sector1"),
        "last_sector2": v.get("last_sector2"),
        "cur_sector1": v.get("cur_sector1"),
        "cur_sector2": v.get("cur_sector2"),
    }


def vehicles_full(sm):
    """Jak L.vehicles(), ale z pelnym zestawem pol do CSV."""
    scoring = {}
    n = min(max(int(sm.scoring.scoringInfo.mNumVehicles), 0), L.MAX_VEHICLES)
    for i in range(n):
        s = sm.scoring.vehScoringInfo[i]
        scoring[int(s.mID)] = s

    out = []
    for i in range(min(int(sm.telemetry.activeVehicles), L.MAX_VEHICLES)):
        t = sm.telemetry.telemInfo[i]
        s = scoring.get(int(t.mID))
        vel = (float(t.mLocalVel.x), float(t.mLocalVel.y), float(t.mLocalVel.z))
        out.append({
            "slot": i,
            "id": int(t.mID),
            "driver": txt(s.mDriverName) if s else "",
            "vehicle": txt(t.mVehicleName),
            "class": txt(s.mVehicleClass) if s else "",
            "is_player": bool(s.mIsPlayer) if s else False,
            "control": int(s.mControl) if s else None,
            "place": int(s.mPlace) if s else None,
            "track": txt(t.mTrackName),
            "lap": int(t.mLapNumber),
            "lap_start_et": float(t.mLapStartET),
            "elapsed": float(t.mElapsedTime),
            "lap_dist": float(s.mLapDist) if s else None,
            "path_lateral": float(s.mPathLateral) if s else None,
            "track_edge": float(s.mTrackEdge) if s else None,
            "best_lap": float(s.mBestLapTime) if s else None,
            "last_lap": float(s.mLastLapTime) if s else None,
            "throttle": float(t.mUnfilteredThrottle),
            "brake": float(t.mUnfilteredBrake),
            "steering": float(t.mUnfilteredSteering),
            "gear": int(t.mGear),
            "rpm": float(t.mEngineRPM),
            "speed": math.sqrt(vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2),
            "vel": vel,
            "pos": (float(t.mPos.x), float(t.mPos.y), float(t.mPos.z)),
            "fuel": float(t.mFuel),
            "in_pits": bool(s.mInPits) if s else False,
            "sector": int(t.mCurrentSector),
            "delta_best": float(t.mDeltaBest),
            "virtual_energy": float(t.mVirtualEnergy),
            "soc": float(t.mSoC),
            "tc": int(t.mTC),
            "abs": int(t.mABS),
            "tc_active": bool(t.mTCActive),
            "abs_active": bool(t.mABSActive),
            "lap_invalid": bool(t.mLapInvalidated),
            "best_sector1": float(s.mBestSector1) if s else None,
            "best_sector2": float(s.mBestSector2) if s else None,
            "last_sector1": float(s.mLastSector1) if s else None,
            "last_sector2": float(s.mLastSector2) if s else None,
            "cur_sector1": float(s.mCurSector1) if s else None,
            "cur_sector2": float(s.mCurSector2) if s else None,
        })
    return out


def run_probe(shm, seconds: float) -> int:
    """Diagnostyka: czy w pamieci sa realne inputy wszystkich aut."""
    print(f"[PROBE] nasluch {seconds:g} s... (Ctrl+C przerwie)")
    stats = {}
    t_end = time.time() + seconds
    last_print = 0.0
    while time.time() < t_end:
        if shm.has_events:
            shm.wait_update(500)
        else:
            time.sleep(0.2)
        try:
            sm = shm.snapshot()
        except Exception as e:
            print(f"[PROBE] blad odczytu: {e}")
            return 2
        vs = vehicles_full(sm)
        if not vs:
            continue
        sc = sm.scoring.scoringInfo
        track = txt(sc.mTrackName)
        for v in vs:
            st = stats.setdefault(v["id"], {
                "driver": v["driver"], "vehicle": v["vehicle"],
                "control": v["control"], "thr": [1.0, 0.0], "brk": [1.0, 0.0],
                "lap": v["lap"],
                "best": v["best_lap"] if (v["best_lap"] and v["best_lap"] > 0) else None,
            })
            st["thr"][0] = min(st["thr"][0], v["throttle"])
            st["thr"][1] = max(st["thr"][1], v["throttle"])
            st["brk"][0] = min(st["brk"][0], v["brake"])
            st["brk"][1] = max(st["brk"][1], v["brake"])
            st["lap"] = max(st["lap"], v["lap"])
            if v["best_lap"] and v["best_lap"] > 0:
                st["best"] = min(st["best"], v["best_lap"]) if st["best"] else v["best_lap"]
        now = time.time()
        if now - last_print >= 5:
            last_print = now
            print(f"[PROBE] {track} | sesja {sc.mSession} | faza {sc.mGamePhase} | "
                  f"active={sm.telemetry.activeVehicles}/{sc.mNumVehicles} | "
                  f"inputy widoczne: {sum(1 for st in stats.values() if (st['thr'][1]-st['thr'][0])>0.01 or (st['brk'][1]-st['brk'][0])>0.01)}/{len(stats)}")

    print("\n[PROBE] podsumowanie (control: 0=gracz, 1=AI, 2=remote, 3=replay)")
    print(f"{'id':>4} {'ctrl':>4} {'kierowca':22} {'gaz min/max':>13} {'ham min/max':>13} {'okr':>4} {'best':>9}")
    for cid in sorted(stats):
        st = stats[cid]
        best = fmt_lap(st["best"]) if st["best"] else "-"
        varies = (st["thr"][1] - st["thr"][0] > 0.01) or (st["brk"][1] - st["brk"][0] > 0.01)
        flag = "  <-- zmienne inputy" if varies else ""
        print(f"{cid:>4} {str(st['control']):>4} {st['driver'][:22]:22} "
              f"{st['thr'][0]:.2f}/{st['thr'][1]:.2f}   {st['brk'][0]:.2f}/{st['brk'][1]:.2f}   "
              f"{st['lap']:>4} {best:>9}{flag}")
    if not stats:
        print("Brak aut w telemetti. Upewnij sie, ze jestes w sesji (nie w menu/monitorze).")
    return 0


def run_dump(path: str, out_dir: str, frames: int) -> int:
    raw = open(path, "rb").read()
    sm = L.SharedMemoryObjectOut.from_buffer_copy(raw[:L.SIZE])
    wall = time.time()
    meta = build_meta(sm, wall)
    writer = SessionWriter(out_dir, meta["track"], meta["session_id"], meta)
    vs = vehicles_full(sm)
    print(f"[TEST] dump: {meta['track']} / {meta['session']} / active={len(vs)}")
    for i in range(frames):
        w = wall + i / 30.0
        for v in vs:
            row = row_from_vehicle(v, sm, w)
            row["session_et"] = float(sm.scoring.scoringInfo.mCurrentET) + i / 30.0
            writer.write(row)
    writer.close()
    print(f"[TEST] zapisano {writer.rows} wierszy -> {writer.path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Loguje telemetrie wszystkich aut LMU")
    ap.add_argument("--out", default="records", help="katalog wyjsciowy")
    ap.add_argument("--hz", type=float, default=30.0, help="czestotliwosc probkowania (domyslnie 30)")
    ap.add_argument("--driver", default=None,
                    help="opcjonalnie: loguj tylko kierowcow, ktorych nazwa zawiera ten tekst")
    ap.add_argument("--dump", default=None, help="tryb testowy: zapisz ramke z pliku shm_*.bin")
    ap.add_argument("--frames", type=int, default=1, help="ile ramek w trybie --dump")
    ap.add_argument("--probe-seconds", type=float, default=0,
                    help="tryb diagnostyczny: przez N s sprawdzaj, czy rywale maja realne inputy")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="zapisuj klatki nawet gdy session_et sie nie zmienia (pauza)")
    ap.add_argument("--max-seconds", type=float, default=0,
                    help="zatrzymaj automatycznie po N sekundach (0 = bez limitu)")
    ap.add_argument("--stop-file", default=None,
                    help="zatrzymaj, gdy powstanie ten plik (graceful close)")
    args = ap.parse_args(argv)

    if args.dump:
        return run_dump(args.dump, args.out, args.frames)

    stop = {"flag": False}

    def _sig(signum, frame):
        stop["flag"] = True
        print("\n[REC] koncze na Ctrl+C...")

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    period = 1.0 / max(args.hz, 1.0)
    try:
        shm = L.LmuSharedMemory().open()
    except Exception as e:
        print(f"[REC] BLAD: {e}")
        print("      Uruchom LMU i wlacz Settings > Gameplay > Enable Plugins "
              "(external plugins).")
        return 2
    print(f"[REC] podlaczono do LMU_Data (eventy: {'tak' if shm.has_events else 'nie, polling'})")
    if args.probe_seconds > 0:
        try:
            return run_probe(shm, args.probe_seconds)
        finally:
            shm.close()
    print(f"[REC] probkowanie {args.hz:g} Hz, Ctrl+C konczy")

    writer = None
    cur_key = None
    last_write = 0.0
    last_status = 0.0
    last_sig = None
    car_sig = {}
    setup_watch = SetupWatch()
    lap_watch = LapWatch()
    t0 = time.time()
    stop_file = args.stop_file
    if stop_file and os.path.exists(stop_file):
        print(f"[REC] uwaga: plik stopu juz istnieje: {stop_file} (usuwam i zaczynam)")
        try:
            os.remove(stop_file)
        except OSError:
            pass
    try:
        while not stop["flag"]:
            if stop_file and os.path.exists(stop_file):
                print(f"[REC] znaleziono plik stopu {stop_file} - koncze")
                break
            if args.max_seconds and time.time() - t0 >= args.max_seconds:
                print(f"[REC] minelo {args.max_seconds:g} s - koncze")
                break
            if shm.has_events:
                # krotki timeout: nie zakladamy, ze event przychodzi przy kazdej klatce telemetrii
                shm.wait_update(max(5, int(period * 1000)))
            else:
                time.sleep(period)
            now = time.time()
            if now - last_write < period - 0.002:
                continue
            try:
                sm = shm.snapshot()
            except Exception as e:
                print(f"[REC] utrata pamieci wspoldzielonej: {e}")
                break
            vs = vehicles_full(sm)
            if not vs:
                if now - last_status >= 5.0:
                    last_status = now
                    sc0 = sm.scoring.scoringInfo
                    n = int(sc0.mNumVehicles)
                    ctrls = {int(sm.scoring.vehScoringInfo[i].mControl)
                             for i in range(min(max(n, 0), 104))}
                    print(f"[REC] czekam: telemetria pusta (scoring: {n} aut, "
                          f"ctrl={sorted(ctrls)}, ET={float(sc0.mCurrentET):.1f})")
                continue
            sc = sm.scoring.scoringInfo
            track = txt(sc.mTrackName)
            session_id = int(sc.mSession)
            key = (track, session_id)
            if key != cur_key:
                if writer:
                    writer.close()
                    print(f"[REC] zamkniety {writer.path} ({writer.rows} wierszy)")
                wall = now
                meta = build_meta(sm, wall)
                writer = SessionWriter(args.out, track, session_id, meta)
                cur_key = key
                # nowa sesja: bez tego pierwsze [LAP] liczy okrazenie i paliwo z poprzedniej sesji
                lap_watch = LapWatch()
                setup_watch.poll(writer, float(sc.mCurrentET), now, force=True)
            # scoring (mCurrentET) odswieza sie tylko ~5 Hz, telemetria do 100 Hz -
            # klatka jest nowa, jesli zmienil sie czas scoringu ALBO telemetrii
            sig = (session_id, round(float(sc.mCurrentET), 3),
                   round(max(v["elapsed"] for v in vs), 4),
                   int(sm.telemetry.activeVehicles))
            if not args.keep_duplicates and sig == last_sig:
                continue
            last_sig = sig
            for v in vs:
                if args.driver and args.driver.lower() not in v["driver"].lower():
                    continue
                # auto zapisujemy tylko gdy jego dane sie zmienily (rywale online ~5 Hz,
                # wlasne auto do 100 Hz) - bez schodkow z powtorzonych wartosci
                csig = (v["elapsed"], v["pos"][0], v["pos"][2], v["throttle"], v["brake"],
                        v["lap_dist"])
                if not args.keep_duplicates and car_sig.get(v["id"]) == csig:
                    continue
                car_sig[v["id"]] = csig
                writer.write(row_from_vehicle(v, sm, now))
            last_write = now
            pv = next((v for v in vs if v["slot"] == int(sm.telemetry.playerVehicleIdx)), None)
            writer.chassis.write(sm, now, pv["lap_dist"] if pv else None)
            setup_watch.poll(writer, float(sc.mCurrentET), now)
            lap_watch.poll(sm, vs)

            if now - last_status >= 5.0:
                last_status = now
                best = min((v["best_lap"] for v in vs
                            if v["best_lap"] and 0 < v["best_lap"] < 1e9), default=None)
                best_s = f"{best:.3f}" if best else "-"
                fastest = ""
                if best:
                    fast = min((v for v in vs if v["best_lap"] == best),
                               key=lambda x: x["best_lap"])
                    fastest = f" ({fast['driver']})"
                left = ""
                if args.max_seconds:
                    left = f" | pozostalo {max(0, args.max_seconds - (now - t0)):.0f}s"
                print(f"[REC] {track} | {SESSION_NAMES.get(session_id, session_id)} | "
                      f"auta={len(vs)} | best={best_s}{fastest} | "
                      f"{writer.rows} wierszy | {writer.size_mb():.1f} MB{left}")
                writer.flush()
    finally:
        if writer:
            writer.close()
            print(f"[REC] zapisane: {writer.path} ({writer.rows} wierszy, "
                  f"{writer.size_mb():.1f} MB)")
        shm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
