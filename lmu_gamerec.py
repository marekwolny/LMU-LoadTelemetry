"""Import nagran z wbudowanej telemetrii LMU (UserData/Telemetry/*.duckdb).

Gra (Ustawienia > Sterowanie > Rozgrywka > Telemetry Recording) zapisuje kazda sesje do pliku
DuckDB: Twoje auto z duza czestotliwoscia + caly setup (metadata.CarSetup). Ten modul zamienia
plik na format nagran aplikacji (records/<stem>.csv.gz + .metadata.json + .setup1.svm), wiec
dzialaja wszystkie zakladki (wyniki, wykresy, mapa, paliwo, setup, analiza toru).

Format (z LMU Telemetry Lab, MIT): kazdy kanal to tabela; probkowane maja kolumne value
(kola: value1..value4) rozlozona rowno w czasie tabeli "GPS Time" (100 Hz), zdarzenia maja
(ts, value). Pozycja z GPS jest dopasowywana (obrot + przesuniecie) do ukladu wspolrzednych
gry z wczesniejszego nagrania tego toru, zeby linie zgadzaly sie na mapie.
Wymaga: pip install duckdb
"""

from __future__ import annotations

import bisect
import csv
import gzip
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import lmu_setup

# Tabela kluczy setupu: sekcja .svm -> (klucz .svm, klucz w CarSetup) - z LMU Telemetry Lab (MIT,
# https://github.com/rabbit20031225/LMU-Telemetry-Lab, backend/app/services/setup_exporter.py)
SVM_MAPPING = {
    "GENERAL": [
        ("Notes", '""'),
        ("Symmetric", '1'),
        ("CGHeightSetting", '0//Non-adjustable'),
        ("CGRightSetting", '0//Non-adjustable'),
        ("CGRearSetting", '0//Non-adjustable'),
        ("WedgeSetting", 'VM_WEIGHT_WEDGE'),
        ("FuelSetting", 'VM_FUEL_LEVEL'),
        ("FuelCapacitySetting", 'VM_FUEL_CAPACITY'),
        ("VirtualEnergySetting", 'VM_VIRTUAL_ENERGY'),
        ("NumPitstopsSetting", 'VM_NUM_PITSTOPS'),
        ("Pitstop1Setting", 'VM_PITSTOP_1'),
        ("Pitstop2Setting", 'VM_PITSTOP_2'),
        ("Pitstop3Setting", 'VM_PITSTOP_3'),
    ],
    "LEFTFENDER": [
        ("FenderFlareSetting", 'VM_LEFT_FENDER_FLARE'),
    ],
    "RIGHTFENDER": [
        ("FenderFlareSetting", 'VM_RIGHT_FENDER_FLARE'),
    ],
    "FRONTWING": [
        ("FWSetting", 'VM_FRONT_WING'),
    ],
    "REARWING": [
        ("RWSetting", 'VM_REAR_WING'),
    ],
    "BODYAERO": [
        ("WaterRadiatorSetting", 'VM_WATER_RADIATOR'),
        ("OilRadiatorSetting", 'VM_OIL_RADIATOR'),
        ("BrakeDuctSetting", 'VM_BRAKE_DUCTS'),
        ("BrakeDuctRearSetting", 'VM_BRAKE_DUCTS_REAR'),
    ],
    "SUSPENSION": [
        ("FrontWheelTrackSetting", 'VM_FRONT_WHEEL_TRACK'),
        ("RearWheelTrackSetting", 'VM_REAR_WHEEL_TRACK'),
        ("FrontAntiSwaySetting", 'VM_FRONT_ANTISWAY'),
        ("RearAntiSwaySetting", 'VM_REAR_ANTISWAY'),
        ("FrontToeInSetting", 'VM_FRONT_TOEIN'),
        ("FrontToeOffsetSetting", 'VM_FRONT_TOEOFFSET'),
        ("RearToeInSetting", 'VM_REAR_TOEIN'),
        ("RearToeOffsetSetting", 'VM_REAR_TOEOFFSET'),
        ("LeftCasterSetting", 'VM_LEFT_CASTER'),
        ("RightCasterSetting", 'VM_RIGHT_CASTER'),
        ("LeftTrackBarSetting", 'VM_LEFT_TRACK_BAR'),
        ("RightTrackBarSetting", 'VM_RIGHT_TRACK_BAR'),
        ("Front3rdPackerSetting", 'VM_FRONT_3RD_PACKERS'),
        ("Front3rdSpringSetting", 'VM_FRONT_3RD_SPRING'),
        ("Front3rdTenderSpringSetting", 'VM_FRONT_3RD_TENDERSPRING'),
        ("Front3rdTenderTravelSetting", 'VM_FRONT_3RD_TENDERSPRINGTRAVEL'),
        ("Front3rdSlowBumpSetting", 'VM_FRONT_3RD_SLOWBUMP'),
        ("Front3rdFastBumpSetting", 'VM_FRONT_3RD_FASTBUMP'),
        ("Front3rdSlowReboundSetting", 'VM_FRONT_3RD_SLOWREBOUND'),
        ("Front3rdFastReboundSetting", 'VM_FRONT_3RD_FASTREBOUND'),
        ("Rear3rdPackerSetting", 'VM_REAR_3RD_PACKERS'),
        ("Rear3rdSpringSetting", 'VM_REAR_3RD_SPRING'),
        ("Rear3rdTenderSpringSetting", 'VM_REAR_3RD_TENDERSPRING'),
        ("Rear3rdTenderTravelSetting", 'VM_REAR_3RD_TENDERSPRINGTRAVEL'),
        ("Rear3rdSlowBumpSetting", 'VM_REAR_3RD_SLOWBUMP'),
        ("Rear3rdFastBumpSetting", 'VM_REAR_3RD_FASTBUMP'),
        ("Rear3rdSlowReboundSetting", 'VM_REAR_3RD_SLOWREBOUND'),
        ("Rear3rdFastReboundSetting", 'VM_REAR_3RD_FASTREBOUND'),
        ("ChassisAdj00Setting", 'VM_CHASSIS_ADJ_00'),
        ("ChassisAdj01Setting", 'VM_CHASSIS_ADJ_01'),
        ("ChassisAdj02Setting", 'VM_CHASSIS_ADJ_02'),
        ("ChassisAdj03Setting", 'VM_CHASSIS_ADJ_03'),
        ("ChassisAdj04Setting", 'VM_CHASSIS_ADJ_04'),
        ("ChassisAdj05Setting", 'VM_CHASSIS_ADJ_05'),
        ("ChassisAdj06Setting", 'VM_CHASSIS_ADJ_06'),
        ("ChassisAdj07Setting", 'VM_CHASSIS_ADJ_07'),
        ("ChassisAdj08Setting", 'VM_CHASSIS_ADJ_08'),
        ("ChassisAdj09Setting", 'VM_CHASSIS_ADJ_09'),
        ("ChassisAdj10Setting", 'VM_CHASSIS_ADJ_10'),
        ("ChassisAdj11Setting", 'VM_CHASSIS_ADJ_11'),
    ],
    "CONTROLS": [
        ("SteerLockSetting", 'VM_STEER_LOCK'),
        ("RearBrakeSetting", 'VM_BRAKE_BALANCE'),
        ("BrakeMigrationSetting", 'VM_BRAKE_MIGRATION'),
        ("BrakePressureSetting", 'VM_BRAKE_PRESSURE'),
        ("HandfrontbrakePressSetting", 'VM_HANDFRONTBRAKE_PRESSURE'),
        ("HandbrakePressSetting", 'VM_HANDBRAKE_PRESSURE'),
        ("TCSetting", 'VM_TRACTION_CONTROL'),
        ("ABSSetting", 'VM_ANTILOCK_BRAKES'),
        ("TractionControlMapSetting", 'VM_TRACTIONCONTROLMAP'),
        ("TCPowerCutMapSetting", 'VM_TRACTIONCONTROLPOWERCUTMAP'),
        ("TCSlipAngleMapSetting", 'VM_TRACTIONCONTROLSLIPANGLEMAP'),
        ("AntilockBrakeSystemMapSetting", 'VM_ANTILOCKBRAKESYSTEMMAP'),
    ],
    "ENGINE": [
        ("RevLimitSetting", 'VM_REV_LIMITER'),
        ("EngineBoostSetting", 'VM_ENGINE_BOOST'),
        ("RegenerationMapSetting", 'VM_REGEN_LEVEL'),
        ("ElectricMotorMapSetting", 'VM_ELECTRIC_MOTOR_MAP'),
        ("EngineMixtureSetting", 'VM_ENGINE_MIXTURE'),
        ("EngineBrakingMapSetting", 'VM_ENGINE_BRAKEMAP'),
    ],
    "DRIVELINE": [
        ("FinalDriveSetting", 'VM_GEAR_FINAL'),
        ("ReverseSetting", 'VM_GEAR_REVERSE'),
        ("Gear1Setting", 'VM_GEAR_1'),
        ("Gear2Setting", 'VM_GEAR_2'),
        ("Gear3Setting", 'VM_GEAR_3'),
        ("Gear4Setting", 'VM_GEAR_4'),
        ("Gear5Setting", 'VM_GEAR_5'),
        ("Gear6Setting", 'VM_GEAR_6'),
        ("RatioSetSetting", 'VM_RATIO_SET'),
        ("DiffPumpSetting", 'VM_DIFF_PUMP'),
        ("DiffPowerSetting", 'VM_DIFF_POWER'),
        ("DiffCoastSetting", 'VM_DIFF_COAST'),
        ("DiffPreloadSetting", 'VM_DIFF_PRELOAD'),
        ("FrontDiffPumpSetting", 'VM_FRONT_DIFF_PUMP'),
        ("FrontDiffPowerSetting", 'VM_FRONT_DIFF_POWER'),
        ("FrontDiffCoastSetting", 'VM_FRONT_DIFF_COAST'),
        ("FrontDiffPreloadSetting", 'VM_FRONT_DIFF_PRELOAD'),
        ("RearSplitSetting", 'VM_TORQUE_SPLIT'),
        ("GearAutoUpShiftSetting", 'VM_GEAR_AUTOUPSHIFT'),
        ("GearAutoDownShiftSetting", 'VM_GEAR_AUTODOWNSHIFT'),
    ],
    "FRONTLEFT": [
        ("CamberSetting", 'WM_CAMBER-W_FL'),
        ("PressureSetting", 'WM_PRESSURE-W_FL'),
        ("PackerSetting", 'WM_PACKERS-W_FL'),
        ("SpringSetting", 'WM_SPRING-W_FL'),
        ("TenderSpringSetting", 'WM_TENDERSPRING-W_FL'),
        ("TenderTravelSetting", 'WM_TENDERSPRINGTRAVEL-W_FL'),
        ("SpringRubberSetting", 'WM_SRUBBER-W_FL'),
        ("RideHeightSetting", 'WM_RIDEHEIGHT-W_FL'),
        ("SlowBumpSetting", 'WM_SLOWBUMP-W_FL'),
        ("FastBumpSetting", 'WM_FASTBUMP-W_FL'),
        ("SlowReboundSetting", 'WM_SLOWREBOUND-W_FL'),
        ("FastReboundSetting", 'WM_FASTREBOUND-W_FL'),
        ("BrakeDiscSetting", 'WM_BRAKEDISC-W_FL'),
        ("BrakePadSetting", 'WM_BRAKEPAD-W_FL'),
        ("CompoundSetting", 'VM_FRONT_TIRE_COMPOUND'),
    ],
    "FRONTRIGHT": [
        ("CamberSetting", 'WM_CAMBER-W_FR'),
        ("PressureSetting", 'WM_PRESSURE-W_FR'),
        ("PackerSetting", 'WM_PACKERS-W_FR'),
        ("SpringSetting", 'WM_SPRING-W_FR'),
        ("TenderSpringSetting", 'WM_TENDERSPRING-W_FR'),
        ("TenderTravelSetting", 'WM_TENDERSPRINGTRAVEL-W_FR'),
        ("SpringRubberSetting", 'WM_SRUBBER-W_FR'),
        ("RideHeightSetting", 'WM_RIDEHEIGHT-W_FR'),
        ("SlowBumpSetting", 'WM_SLOWBUMP-W_FR'),
        ("FastBumpSetting", 'WM_FASTBUMP-W_FR'),
        ("SlowReboundSetting", 'WM_SLOWREBOUND-W_FR'),
        ("FastReboundSetting", 'WM_FASTREBOUND-W_FR'),
        ("BrakeDiscSetting", 'WM_BRAKEDISC-W_FR'),
        ("BrakePadSetting", 'WM_BRAKEPAD-W_FR'),
        ("CompoundSetting", 'VM_FRONT_TIRE_COMPOUND'),
    ],
    "REARLEFT": [
        ("CamberSetting", 'WM_CAMBER-W_RL'),
        ("PressureSetting", 'WM_PRESSURE-W_RL'),
        ("PackerSetting", 'WM_PACKERS-W_RL'),
        ("SpringSetting", 'WM_SPRING-W_RL'),
        ("TenderSpringSetting", 'WM_TENDERSPRING-W_RL'),
        ("TenderTravelSetting", 'WM_TENDERSPRINGTRAVEL-W_RL'),
        ("SpringRubberSetting", 'WM_SRUBBER-W_RL'),
        ("RideHeightSetting", 'WM_RIDEHEIGHT-W_RL'),
        ("SlowBumpSetting", 'WM_SLOWBUMP-W_RL'),
        ("FastBumpSetting", 'WM_FASTBUMP-W_RL'),
        ("SlowReboundSetting", 'WM_SLOWREBOUND-W_RL'),
        ("FastReboundSetting", 'WM_FASTREBOUND-W_RL'),
        ("BrakeDiscSetting", 'WM_BRAKEDISC-W_RL'),
        ("BrakePadSetting", 'WM_BRAKEPAD-W_RL'),
        ("CompoundSetting", 'VM_REAR_TIRE_COMPOUND'),
    ],
    "REARRIGHT": [
        ("CamberSetting", 'WM_CAMBER-W_RR'),
        ("PressureSetting", 'WM_PRESSURE-W_RR'),
        ("PackerSetting", 'WM_PACKERS-W_RR'),
        ("SpringSetting", 'WM_SPRING-W_RR'),
        ("TenderSpringSetting", 'WM_TENDERSPRING-W_RR'),
        ("TenderTravelSetting", 'WM_TENDERSPRINGTRAVEL-W_RR'),
        ("SpringRubberSetting", 'WM_SRUBBER-W_RR'),
        ("RideHeightSetting", 'WM_RIDEHEIGHT-W_RR'),
        ("SlowBumpSetting", 'WM_SLOWBUMP-W_RR'),
        ("FastBumpSetting", 'WM_FASTBUMP-W_RR'),
        ("SlowReboundSetting", 'WM_SLOWREBOUND-W_RR'),
        ("FastReboundSetting", 'WM_FASTREBOUND-W_RR'),
        ("BrakeDiscSetting", 'WM_BRAKEDISC-W_RR'),
        ("BrakePadSetting", 'WM_BRAKEPAD-W_RR'),
        ("CompoundSetting", 'VM_REAR_TIRE_COMPOUND'),
    ],
    "BASIC": [
        ("Downforce", '0.500000'),
        ("Balance", '0.500000'),
        ("Ride", '0.500000'),
        ("Gearing", '0.500000'),
        ("Custom", '1')
    ]
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
SESSION_IDS = {"practice": 1, "quali": 5, "warmup": 9, "race": 10, "testday": 0}
RATE = 50.0          # Hz zapisu do CSV


def telemetry_dir() -> Path | None:
    d = lmu_setup.player_dir()
    if not d:
        return None
    p = d.parent / "Telemetry"
    return p if p.is_dir() else None


def available() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def pending(records: Path, registry: Path) -> list[Path]:
    """Pliki .duckdb z gry, ktorych jeszcze nie zaimportowano (albo zmienione)."""
    d = telemetry_dir()
    if not d:
        return []
    reg = _read_reg(registry)
    out = []
    for p in sorted(d.glob("*.duckdb")):
        try:
            st = p.stat()
        except OSError:
            continue
        if busy(p):
            continue
        r = reg.get(p.name)
        if (r and r.get("covered")) or covered_by(p, records):
            continue
        if r and r.get("size") == st.st_size and (records / f"{r['stem']}.csv.gz").exists():
            continue
        if r and r.get("skip") and r.get("size") == st.st_size:
            continue
        out.append(p)
    return out


def busy(p: Path) -> bool:
    """Gra jeszcze pisze (sesja trwa): plik zablokowany albo swiezy dziennik .wal."""
    wal = p.with_name(p.name + ".wal")
    try:
        if wal.exists() and time.time() - wal.stat().st_mtime < 120:
            return True
        with open(p, "rb"):
            pass
    except OSError:
        return True
    return False


def in_progress() -> list[Path]:
    d = telemetry_dir()
    return [p for p in sorted(d.glob("*.duckdb")) if busy(p)] if d else []


def _read_reg(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_reg(path: Path, reg: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- odczyt DuckDB
class Source:
    def __init__(self, path: Path):
        import duckdb
        self.con = duckdb.connect(str(path), read_only=True)
        self.tables = {t[0] for t in self.con.execute("SHOW TABLES").fetchall()}
        self.meta = {}
        if "metadata" in self.tables:
            self.meta = {k: v for k, v in self.con.execute("SELECT key, value FROM metadata").fetchall()}
        g = self.con.execute('SELECT value FROM "GPS Time" ORDER BY rowid').fetchall()
        self.gps = [r[0] for r in g if r[0] is not None]
        if len(self.gps) < 10:
            raise ValueError("pusty plik (brak GPS Time)")

    def close(self):
        self.con.close()

    def cols(self, table):
        return [c[0] for c in self.con.execute(f'DESCRIBE "{table}"').fetchall()]

    def sampled(self, table, col="value"):
        """(czasy, wartosci) kanalu probkowanego - wiersze rozlozone rowno na osi GPS Time."""
        if table not in self.tables:
            return None
        cols = self.cols(table)
        if "ts" in cols or col not in cols:
            return None
        vals = [r[0] for r in self.con.execute(f'SELECT "{col}" FROM "{table}" ORDER BY rowid').fetchall()]
        n, g = len(vals), self.gps
        if n < 2:
            return None
        k = (len(g) - 1) / (n - 1)
        ts = []
        for i in range(n):
            p = i * k
            j = min(int(p), len(g) - 2)
            ts.append(g[j] + (p - j) * (g[j + 1] - g[j]))
        return ts, vals

    def events(self, table, col="value"):
        if table not in self.tables:
            return []
        cols = self.cols(table)
        if "ts" not in cols:
            return []
        c = col if col in cols else next((x for x in cols if x != "ts"), None)
        if not c:
            return []
        return [(t, v) for t, v in self.con.execute(f'SELECT ts, "{c}" FROM "{table}" ORDER BY ts').fetchall()
                if t is not None]


class Lin:
    """Interpolacja liniowa po czasie (wywolania z rosnacym t)."""

    def __init__(self, tv):
        self.t, self.v = tv if tv else ([], [])
        self.i = 0

    def __call__(self, t):
        T, V = self.t, self.v
        if not T:
            return None
        while self.i + 1 < len(T) and T[self.i + 1] <= t:
            self.i += 1
        i = self.i
        if i + 1 >= len(T) or t <= T[i]:
            return V[i]
        a, b = V[i], V[i + 1]
        if a is None or b is None:
            return a
        f = (t - T[i]) / max(T[i + 1] - T[i], 1e-9)
        return a + (b - a) * f


class Step:
    """Ostatnia wartosc zdarzenia <= t."""

    def __init__(self, ev, default=None):
        self.t = [e[0] for e in ev]
        self.v = [e[1] for e in ev]
        self.d = default

    def __call__(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else self.d


# ---------------------------------------------------------------- setup
def setup_svm(meta: dict) -> str | None:
    raw = meta.get("CarSetup")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    lines = [f'VehicleClassSetting="{meta.get("CarClass", "")}"', "UpgradeSetting=(0,0,0,0)",
             "// z wbudowanej telemetrii LMU (import LMU Telemetry)", ""]
    for section, mapping in SVM_MAPPING.items():
        lines.append(f"[{section}]")
        for key, src in mapping:
            if "//" in src or src in ('""', "1", "0.500000"):
                lines.append(f"{key}={src}")
                continue
            e = data.get(src)
            if isinstance(e, dict) and e.get("available"):
                lines.append(f"{key}={e.get('value', 0)}//{e.get('stringValue', 'N/A')}")
            else:
                lines.append(f"{key}=0//N/A")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- dopasowanie GPS -> uklad gry
def _gps_xy(lat, lon, lat0, lon0):
    R = 6371000.0
    return (math.radians(lon - lon0) * R * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * R)


def _fit(src, dst):
    """Najlepszy obrot + przesuniecie (z ewentualnym odbiciem) src -> dst; (f, blad_sredni)."""
    best = None
    n = len(src)
    for mirror in (1, -1):
        s = [(x, y * mirror) for x, y in src]
        cx = sum(p[0] for p in s) / n
        cy = sum(p[1] for p in s) / n
        dx = sum(p[0] for p in dst) / n
        dy = sum(p[1] for p in dst) / n
        sxx = sxy = 0.0
        for (ax, ay), (bx, by) in zip(s, dst):
            ax, ay, bx, by = ax - cx, ay - cy, bx - dx, by - dy
            sxx += ax * bx + ay * by
            sxy += ax * by - ay * bx
        th = math.atan2(sxy, sxx)
        c, si = math.cos(th), math.sin(th)

        def f(x, y, c=c, si=si, m=mirror, cx=cx, cy=cy, dx=dx, dy=dy):
            y = y * m
            x, y = x - cx, y - cy
            return dx + c * x - si * y, dy + si * x + c * y

        err = sum(math.hypot(*(a - b for a, b in zip(f(*p), q))) for p, q in zip(src, dst)) / n
        if best is None or err < best[1]:
            best = (f, err)
    return best


def _reference_line(analysis_dir: Path, track: str):
    """(dystans -> (x, z)) z najlepszego okrazenia w dowolnej analizie tego toru."""
    for d in sorted(Path(analysis_dir).glob("*")):
        try:
            s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if s.get("track") != track:
            continue
        ref = s.get("reference") or ((s.get("drivers") or [{}])[0].get("name"))
        pts = []
        try:
            with open(d / "traces.csv", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r["driver"] == ref:
                        pts.append((float(r["distance_m"]), float(r["x"]), float(r["z"])))
        except (OSError, ValueError, KeyError):
            continue
        if len(pts) > 100:
            return pts
    return None


# ---------------------------------------------------------------- import
_NAME_RE = re.compile(r"^(?P<track>.+)_(?P<s>[PQRWT])\d*_(?P<d>\d{4}-\d{2}-\d{2})T(?P<t>\d{2}_\d{2}_\d{2})Z$")
_LETTER = {"P": "practice", "Q": "quali", "R": "race", "W": "warmup", "T": "testday"}


def parse_name(path: Path):
    """Z nazwy pliku gry ('Grand Prix of Long Beach_P_2026-11-01T17_04_38Z'): (tor, sesja, start lokalnie)."""
    m = _NAME_RE.match(Path(path).stem)
    if not m:
        return None, None, None
    utc = datetime.strptime(f"{m['d']} {m['t']}", "%Y-%m-%d %H_%M_%S").replace(tzinfo=timezone.utc)
    return m["track"], _LETTER.get(m["s"]), utc.astimezone().replace(tzinfo=None)


def covered_by(path: Path, records: Path):
    """Nagranie aplikacji tej samej sesji, ktore pokrywa >= 90% czasu pliku gry, albo None.
    (Gdy aplikacja nagrywala krocej niz gra - np. zatrzymana wczesniej - plik gry importujemy,
    bo ma okrazenia, ktorych aplikacja nie zlapala.)"""
    track, sess, start = parse_name(path)
    if not start:
        return None
    try:
        g_end = datetime.fromtimestamp(Path(path).stat().st_mtime)
    except OSError:
        return None
    g_len = max((g_end - start).total_seconds(), 1.0)
    first = (re.findall(r"[a-z0-9]+", (track or "").lower()) or [""])[0]
    for m in Path(records).glob("*.metadata.json"):
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
            st = datetime.fromisoformat(meta.get("started_at", ""))
        except (OSError, ValueError):
            continue
        if meta.get("source") == "lmu_telemetry":
            continue
        t = (re.findall(r"[a-z0-9]+", (meta.get("track") or "").lower()) or [""])[0]
        if abs((st - start).total_seconds()) <= 600 and t == first and (not sess or meta.get("session") == sess):
            csv_path = m.with_name(m.name[:-len(".metadata.json")] + ".csv.gz")
            try:
                a_end = datetime.fromtimestamp(csv_path.stat().st_mtime)
            except OSError:
                continue
            overlap = (min(a_end, g_end) - max(st, start)).total_seconds()
            if overlap >= 0.9 * g_len:
                return m.name[:-len(".metadata.json")]
    return None


def _session_name(meta: dict, path: Path) -> str:
    sess = parse_name(path)[1]
    if sess:
        return sess
    txt = " ".join(str(v) for k, v in meta.items() if "session" in k.lower()) + " " + path.stem
    t = txt.lower()
    for key, name in (("race", "race"), ("qual", "quali"), ("warm", "warmup"), ("prac", "practice"),
                      ("test", "testday")):
        if key in t:
            return name
    return "practice"


def _match_track(name: str, length: float, records: Path) -> tuple[str, float]:
    """Nazwa toru jak w nagraniach z pamieci wspoldzielonej (np. 'Paul Ricard - 1A')."""
    first = (re.findall(r"[a-z0-9]+", (name or "").lower()) or [""])[0]
    for m in sorted(Path(records).glob("*.metadata.json")):
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("track") == name and meta.get("track_length_m"):
            return name, float(meta["track_length_m"])
    for m in sorted(Path(records).glob("*.metadata.json")):
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        t = meta.get("track") or ""
        tl = float(meta.get("track_length_m") or 0)
        if (re.findall(r"[a-z0-9]+", t.lower()) or [""])[0] == first and tl and abs(tl - length) / tl < 0.04:
            return t, tl
    return name, length


def import_file(path: Path, records: Path, analysis_dir: Path, player: str | None, log=print) -> str:
    """Zamienia jeden plik .duckdb na nagranie aplikacji. Zwraca stem."""
    path = Path(path)
    src = Source(path)
    try:
        meta = src.meta
        g = src.gps
        t0, t1 = g[0], g[-1]
        speed = src.sampled("Ground Speed") or src.sampled("GPS Speed")
        if not speed:
            raise ValueError("brak kanalu predkosci")
        kmh = max(v for v in speed[1] if v is not None) > 150
        ch = {
            "speed": Lin(speed), "thr": Lin(src.sampled("Throttle Pos")),
            "brk": Lin(src.sampled("Brake Pos")), "steer": Lin(src.sampled("Steering Pos")),
            "rpm": Lin(src.sampled("Engine RPM")), "fuel": Lin(src.sampled("Fuel Level")),
            "ve": Lin(src.sampled("Virtual Energy")), "soc": Lin(src.sampled("SoC")),
            "lat": Lin(src.sampled("GPS Latitude")), "lon": Lin(src.sampled("GPS Longitude")),
            "plat": Lin(src.sampled("Path Lateral")), "edge": Lin(src.sampled("Track Edge")),
        }
        ld = src.sampled("Lap Dist")
        thr_scale = 100.0 if ch["thr"].v and max(x for x in ch["thr"].v if x is not None) > 1.5 else 1.0
        brk_scale = 100.0 if ch["brk"].v and max(x for x in ch["brk"].v if x is not None) > 1.5 else 1.0
        ev = {k: Step(src.events(k)) for k in ("Gear", "In Pits", "Lap Time", "Best LapTime",
                                                "Last Sector1", "Last Sector2", "Best Sector1",
                                                "Best Sector2", "Current Sector", "TC", "ABS")}
        if not any((v or 0) > 0 for _, v in src.events("Lap Time")):
            raise ValueError("brak pełnego okrążenia (sam wyjazd z boksu)")
        laps = src.events("Lap")
        lap_starts = [t for t, _ in laps] or [t0]
        lap_nums = [int(v) if isinstance(v, (int, float)) else i for i, (_, v) in enumerate(laps)] or [0]
        track_len = max((v for v in (ld[1] if ld else []) if v), default=0.0)
        # TrackLayout to ta sama nazwa co w pamieci wspoldzielonej (np. "Paul Ricard - 1A")
        track, track_len = _match_track(meta.get("TrackLayout") or meta.get("TrackName", "?"),
                                        track_len, records)
        session = _session_name(meta, path)
        driver = meta.get("DriverName") or player or "Ja"
        vehicle = meta.get("CarName", "")
        cls = meta.get("CarClass", "")
        # dystans okrazenia: kanal gry (10 Hz) + calka predkosci miedzy probkami i przy przejsciu linii
        ldt, ldv = (ld or ([], []))

        def lapdist(t, v):
            i = bisect.bisect_right(ldt, t) - 1
            if i < 0:
                return None
            if i + 1 < len(ldt) and ldv[i + 1] is not None and ldv[i] is not None and ldv[i + 1] >= ldv[i]:
                f = (t - ldt[i]) / max(ldt[i + 1] - ldt[i], 1e-9)
                return ldv[i] + (ldv[i + 1] - ldv[i]) * f
            d = (ldv[i] or 0) + v * (t - ldt[i])
            return min(d, track_len) if track_len else d

        # rows: probki co 1/RATE s
        n = int((t1 - t0) * RATE)
        lat0 = lon0 = None
        rows = []
        for k in range(n):
            t = t0 + k / RATE
            v = ch["speed"](t) or 0.0
            v = v / 3.6 if kmh else v
            la, lo = ch["lat"](t), ch["lon"](t)
            if la is None or lo is None:
                continue
            if lat0 is None:
                lat0, lon0 = la, lo
            x, z = _gps_xy(la, lo, lat0, lon0)
            li = bisect.bisect_right(lap_starts, t) - 1
            lap = lap_nums[li] if li >= 0 else 0
            ls = lap_starts[li] if li >= 0 else t0
            d = lapdist(t, v)
            # kanal dystansu (10 Hz) tuz po linii jeszcze pokazuje koniec poprzedniego okrazenia
            if d is not None and track_len and t - ls < 2.0 and d > track_len * 0.8:
                d = v * (t - ls)
            rows.append([t, v, x, z, lap, ls, d, 1 if ev["In Pits"](t) else 0])
        if not rows:
            raise ValueError("brak pozycji GPS")
        # dopasowanie GPS do ukladu wspolrzednych gry (jesli jest wczesniejsze nagranie toru)
        ref = _reference_line(analysis_dir, track)
        if ref:
            rd = [p[0] for p in ref]
            src_pts, dst_pts = [], []
            for r in rows[::10]:
                d = r[6]
                if d is None or r[7]:          # pit lane nie lezy na linii toru
                    continue
                j = bisect.bisect_left(rd, d)
                if 0 <= j < len(ref) and abs(rd[j] - d) < 5:
                    src_pts.append((r[2], r[3]))
                    dst_pts.append((ref[j][1], ref[j][2]))
            if len(src_pts) > 50:
                f, err = _fit(src_pts, dst_pts)
                # drugie przejscie bez odstajacych par (np. wyjazd poza tor, dystans z opoznieniem)
                res = [math.hypot(*(a - b for a, b in zip(f(*p), q))) for p, q in zip(src_pts, dst_pts)]
                lim = max(5.0, 3 * sorted(res)[len(res) // 2])
                keep = [i for i, e in enumerate(res) if e <= lim]
                if len(keep) > 50:
                    f, err = _fit([src_pts[i] for i in keep], [dst_pts[i] for i in keep])
                if err < 25:
                    for r in rows:
                        r[2], r[3] = f(r[2], r[3])
                    log(f"[GRA] pozycje dopasowane do układu gry (średni błąd {err:.1f} m)")
        # zapis
        start = parse_name(path)[2] or datetime.fromtimestamp(path.stat().st_mtime - (t1 - t0))
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", track).strip("_")
        stem = f"{start:%Y%m%d_%H%M%S}_{safe}_{session}_gra"
        out_csv = Path(records) / f"{stem}.csv.gz"
        with gzip.open(out_csv, "wt", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(FIELDS)
            for t, v, x, z, lap, ls, d, _pit in rows:
                thr = ch["thr"](t)
                brk = ch["brk"](t)
                ve = ch["ve"](t)
                rec = {
                    "wall": f"{start.timestamp() + (t - t0):.3f}", "session_et": f"{t - t0:.3f}",
                    "car_et": f"{t - t0:.3f}", "track": track,
                    "session_id": SESSION_IDS.get(session, 1), "slot": 0, "car_id": 0,
                    "driver": driver, "vehicle": vehicle, "car_class": cls, "control": 0,
                    "is_player": 1, "lap": lap, "lap_start_et": f"{ls - t0:.3f}",
                    "lap_dist": f"{d:.2f}" if d is not None else "",
                    "path_lateral": _fmt(ch["plat"](t)), "track_edge": _fmt(ch["edge"](t)),
                    "best_lap": _fmt(ev["Best LapTime"](t)), "last_lap": _fmt(ev["Lap Time"](t)),
                    "throttle": _fmt(thr / thr_scale if thr is not None else None),
                    "brake": _fmt(brk / brk_scale if brk is not None else None),
                    "steering": _fmt(ch["steer"](t)), "gear": ev["Gear"](t) or 0,
                    "rpm": _fmt(ch["rpm"](t)), "speed": f"{v:.3f}", "x": f"{x:.3f}", "y": "0",
                    "z": f"{z:.3f}", "fuel": _fmt(ch["fuel"](t)),
                    "in_pits": 1 if ev["In Pits"](t) else 0, "sector": ev["Current Sector"](t) or "",
                    "virtual_energy": _fmt(ve), "soc": _fmt(ch["soc"](t)),
                    "tc": ev["TC"](t) or 0, "abs": ev["ABS"](t) or 0,
                    "best_sector1": _fmt(ev["Best Sector1"](t)), "best_sector2": _fmt(ev["Best Sector2"](t)),
                    "last_sector1": _fmt(ev["Last Sector1"](t)), "last_sector2": _fmt(ev["Last Sector2"](t)),
                }
                w.writerow([rec.get(k, "") for k in FIELDS])
        md = {"started_at": start.isoformat(timespec="seconds"), "track": track,
              "track_length_m": track_len, "session_id": SESSION_IDS.get(session, 1),
              "session": session, "player": driver, "source": "lmu_telemetry",
              "source_file": path.name, "vehicles": [{"id": 0, "driver": driver, "vehicle": vehicle,
                                                      "class": cls, "is_player": True, "control": 0}],
              "game_meta": {k: v for k, v in meta.items() if k not in ("CarSetup", "CarConfig")}}
        svm = setup_svm(meta)
        if svm:
            fname = f"{stem}.setup1.svm"
            (Path(records) / fname).write_text(svm, encoding="utf-8")
            ps = lmu_setup.parse(svm)
            md["setups"] = [{"file": fname, "session_et": None, "vehicle": ps["vehicle"],
                             "name": lmu_setup.describe(ps), "from_game": True}]
        (Path(records) / f"{stem}.metadata.json").write_text(
            json.dumps(md, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[GRA] {path.name} -> {stem} ({len(rows)} próbek, {len(set(r[4] for r in rows))} okr.)")
        return stem
    finally:
        src.close()


def _fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.{nd}f}"
    return v


def import_all(records: Path, analysis_dir: Path, player: str | None, log=print) -> list[str]:
    reg_path = Path(analysis_dir) / "_imported.json"
    reg = _read_reg(reg_path)
    done = []
    for p in pending(Path(records), reg_path):
        cov = covered_by(p, Path(records))
        if cov:
            # ta sama sesja jest juz nagrana aplikacja (z rywalami i podwoziem) - bez duplikatu
            log(f"[GRA] {p.name}: pominięty — ta sesja jest już nagrana aplikacją ({cov})")
            reg[p.name] = {"size": p.stat().st_size, "covered": cov}
            _write_reg(reg_path, reg)
            continue
        try:
            stem = import_file(p, Path(records), Path(analysis_dir), player, log)
            reg[p.name] = {"size": p.stat().st_size, "stem": stem}
            done.append(stem)
        except Exception as e:
            log(f"[GRA] {p.name}: pominięty — {type(e).__name__}: {e}")
            reg[p.name] = {"size": p.stat().st_size, "skip": True, "error": str(e)}
        _write_reg(reg_path, reg)
    return done
