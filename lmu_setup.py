"""Setupy LMU (.svm): odnalezienie, rozpoznanie nazwy, porownanie.

LMU zapisuje setup auta, ktorym aktualnie jedziesz, do UserData/player/TempModFile.svm
(nadpisywany przy wyjezdzie z garazu / zmianie setupu). Zapisane setupy leza w
UserData/player/Settings/<tor>/<nazwa>.svm. Rejestrator kopiuje TempModFile do nagrania
przy kazdej zmianie (z czasem sesji), wiec kazde okrazenie ma przypisany setup.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_player_dir = None
_lib_cache: dict = {}

SECTION_NAMES = {
    "GENERAL": "Ogólne", "FRONTWING": "Aero", "REARWING": "Aero", "BODYAERO": "Aero / chłodzenie",
    "SUSPENSION": "Zawieszenie", "CONTROLS": "Hamulce i elektronika", "ENGINE": "Silnik",
    "DRIVELINE": "Skrzynia i dyferencjał", "LEFTFENDER": "Aero", "RIGHTFENDER": "Aero",
}
WHEELS = ("FRONTLEFT", "FRONTRIGHT", "REARLEFT", "REARRIGHT")
LABELS = {
    "FuelSetting": "Paliwo (mnożnik)", "VirtualEnergySetting": "Energia wirtualna",
    "NumPitstopsSetting": "Liczba pit stopów", "FWSetting": "Przednie skrzydło",
    "RWSetting": "Tylne skrzydło", "WaterRadiatorSetting": "Chłodnica wody",
    "OilRadiatorSetting": "Chłodnica oleju", "BrakeDuctSetting": "Wlot hamulców przód",
    "BrakeDuctRearSetting": "Wlot hamulców tył", "FrontAntiSwaySetting": "Stabilizator przód",
    "RearAntiSwaySetting": "Stabilizator tył", "FrontToeInSetting": "Zbieżność przód",
    "RearToeInSetting": "Zbieżność tył", "Front3rdSpringSetting": "3. sprężyna przód",
    "Rear3rdSpringSetting": "3. sprężyna tył", "Front3rdPackerSetting": "3. odbojnik przód",
    "Rear3rdPackerSetting": "3. odbojnik tył", "SteerLockSetting": "Skręt kierownicy",
    "RearBrakeSetting": "Balans hamulców (P:T)", "BrakeMigrationSetting": "Migracja hamulców",
    "BrakePressureSetting": "Ciśnienie hamulców", "ABSSetting": "ABS (dostępność)",
    "TractionControlMapSetting": "TC mapa", "TCPowerCutMapSetting": "TC cut",
    "TCSlipAngleMapSetting": "TC slip", "AntilockBrakeSystemMapSetting": "Mapa ABS",
    "RevLimitSetting": "Ogranicznik obrotów", "EngineMixtureSetting": "Mapa silnika",
    "RegenerationMapSetting": "Rekuperacja", "ElectricMotorMapSetting": "Mapa silnika el.",
    "EngineBrakingMapSetting": "Hamowanie silnikiem", "FinalDriveSetting": "Przełożenie główne",
    "RatioSetSetting": "Zestaw przełożeń", "DiffPreloadSetting": "Dyferencjał: preload",
    "DiffPowerSetting": "Dyferencjał: gaz", "DiffCoastSetting": "Dyferencjał: hamowanie",
    "DiffPumpSetting": "Dyferencjał: pompa", "RearSplitSetting": "Rozdział napędu",
    "CamberSetting": "Pochylenie (camber)", "PressureSetting": "Ciśnienie opon",
    "PackerSetting": "Odbojnik", "SpringSetting": "Sprężyna", "RideHeightSetting": "Prześwit",
    "SlowBumpSetting": "Dobicie wolne", "FastBumpSetting": "Dobicie szybkie",
    "SlowReboundSetting": "Odbicie wolne", "FastReboundSetting": "Odbicie szybkie",
    "BrakeDiscSetting": "Tarcza hamulcowa", "BrakePadSetting": "Klocki",
    "CompoundSetting": "Mieszanka opon", "TenderSpringSetting": "Sprężyna pomocnicza",
    "TenderTravelSetting": "Skok spr. pomocniczej", "TractionControlSetting": "TC (dostępność)",
    "FuelCapacitySetting": "Pojemność baku", "ReverseSetting": "Wsteczny",
    "GearAutoUpShiftSetting": "Auto zmiana w górę", "GearAutoDownShiftSetting": "Auto zmiana w dół",
}
LABELS.update({f"Gear{i}Setting": f"Bieg {i}" for i in range(1, 9)})
HIDDEN = {"N/A", "Non-adjustable", "", "Fixed", "Detached"}
SKIP = {"Symmetric"}


def player_dir() -> Path | None:
    """UserData/player gry (LMU_USERDATA albo biblioteki Steam z rejestru)."""
    global _player_dir
    if _player_dir is not None:
        return _player_dir or None
    cands = []
    if os.environ.get("LMU_USERDATA"):
        cands.append(Path(os.environ["LMU_USERDATA"]))
    libs = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        libs.append(steam)
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except (OSError, ImportError):
        pass
    for d in "CDEFGH":
        libs.append(Path(f"{d}:/SteamLibrary"))
    for lib in libs:
        cands.append(lib / "steamapps" / "common" / "Le Mans Ultimate" / "UserData" / "player")
    for c in cands:
        if (c / "Settings").is_dir() or (c / "TempModFile.svm").exists():
            _player_dir = c
            return c
    _player_dir = False
    return None


def temp_setup_path() -> Path | None:
    d = player_dir()
    return d / "TempModFile.svm" if d else None


def parse(text: str) -> dict:
    """{"vehicle": ..., "values": {"SEKCJA.Klucz": opis}, "sections": [(sekcja, [(klucz, opis)])]}"""
    vehicle, sec, sections, values = "", "", [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            sections.append((sec, []))
            continue
        if "=" not in line:
            continue
        key, rest = line.split("=", 1)
        if key == "VehicleClassSetting":
            vehicle = rest.strip('"')
            continue
        if not sec or key == "Notes":
            continue
        raw, _, disp = rest.partition("//")
        disp = disp.strip() or raw.strip()
        values[f"{sec}.{key}"] = disp
        sections[-1][1].append((key, disp))
    return {"vehicle": vehicle, "values": values, "sections": sections}


def read(path) -> dict | None:
    try:
        return parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return None


def diff(va: dict, vb: dict) -> list[str]:
    return [k for k in sorted(set(va) | set(vb)) if va.get(k) != vb.get(k)
            and not k.startswith("BASIC.")]


_lib_list: tuple = (0.0, [])


def library(max_age: float = 30.0) -> list[dict]:
    """Wszystkie zapisane setupy z Settings/** (cache po czasie modyfikacji; lista
    odswiezana najwyzej co `max_age` s - folder ma ~1000+ plikow)."""
    global _lib_list
    import time
    if time.time() - _lib_list[0] < max_age:
        return _lib_list[1]
    d = player_dir()
    if not d:
        return []
    out = []
    for p in (d / "Settings").glob("*/*.svm"):
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        hit = _lib_cache.get(str(p))
        if not hit or hit["mtime"] != mt:
            ps = read(p)
            if not ps:
                continue
            hit = _lib_cache[str(p)] = dict(path=str(p), name=p.stem, folder=p.parent.name,
                                            mtime=mt, vehicle=ps["vehicle"], values=ps["values"])
        out.append(hit)
    _lib_list = (time.time(), out)
    return out


def identify(parsed: dict) -> tuple[str, int, str]:
    """(nazwa najblizszego zapisanego setupu, liczba roznic, folder toru); ("", -1, "")."""
    best = ("", -1, "")
    for e in library():
        if e["vehicle"] != parsed["vehicle"]:
            continue
        n = len(diff(parsed["values"], e["values"]))
        if best[1] < 0 or n < best[1] or (n == best[1] and e["mtime"] > 0):
            best = (e["name"], n, e["folder"])
            if n == 0:
                break
    return best


def describe(parsed: dict) -> str:
    name, n, _ = identify(parsed)
    if not name:
        return "własny (nie zapisany)"
    return name if n == 0 else f"{name} + {n} zmian"


def track_folder(track: str) -> Path | None:
    """Folder Settings/<tor> pasujacy do nazwy toru z gry (najlepiej jak sie da)."""
    d = player_dir()
    if not d or not (d / "Settings").is_dir():
        return None
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    t = norm(track)
    alias = {"autdromojoscarlospace": "interlagos", "circuitdelasarthe": "lemans",
             "sebringinternationalraceway": "sebring", "lusailinternationalcircuit": "qatar",
             "autodromonazionalemonza": "monza", "autodromoenzoedinoferrari": "imola",
             "fujispeedway": "fuji", "bahraininternationalcircuit": "bahrain",
             "circuitdespafrancorchamps": "spa", "autodromointernacionaldoalgarve": "portimao"}
    for a, f in alias.items():
        if a in t or t in a:
            t = f
    best = None
    for f in (d / "Settings").iterdir():
        if not f.is_dir():
            continue
        n = norm(f.name)
        if n and (n in t or t.startswith(n) or n.startswith(t[:6])):
            if best is None or len(n) > len(norm(best.name)):
                best = f
    return best or (d / "Settings")


def label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    k = key[:-7] if key.endswith("Setting") else key
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", k)


def rows(parsed: dict, other: dict | None = None):
    """Wiersze do tabeli: (grupa, etykieta, wartosc, wartosc_porownania, zmienione).
    Kola sa skladane w jeden wiersz (przod / tyl, L=P gdy symetryczne)."""
    v = parsed["values"]
    ov = other["values"] if other else {}
    out = []
    seen_wheel = False
    for sec, items in parsed["sections"]:
        if sec == "BASIC":
            continue
        if sec in WHEELS:
            if seen_wheel:
                continue
            seen_wheel = True
            keys = []
            for s in WHEELS:
                for k, _ in dict(parsed["sections"]).get(s, []):
                    if k not in keys:
                        keys.append(k)
            for k in keys:
                vals = [v.get(f"{s}.{k}", "") for s in WHEELS]
                if all(x in HIDDEN or x == "Standard" for x in vals):
                    continue
                ovals = [ov.get(f"{s}.{k}", "") for s in WHEELS] if other else None
                out.append(("Koła", label(k), _wheels(vals),
                            _wheels(ovals) if ovals else "", bool(other) and vals != ovals))
            continue
        for k, disp in items:
            if disp in HIDDEN or k in SKIP:
                continue
            o = ov.get(f"{sec}.{k}", "") if other else ""
            out.append((SECTION_NAMES.get(sec, sec.title()), label(k), disp, o,
                        bool(other) and o != disp))
    return out


def _wheels(vals):
    fl, fr, rl, rr = vals
    f = fl if fl == fr else f"{fl} | {fr}"
    r = rl if rl == rr else f"{rl} | {rr}"
    return f"P: {f}   ·   T: {r}"


def setup_for_lap(setups: list[dict], lap_start_et) -> dict | None:
    """Ktory snapshot setupu obowiazywal na okrazeniu zaczetym w lap_start_et (ET sesji)."""
    if not setups:
        return None
    if lap_start_et is None:
        return setups[-1]
    cur = setups[0]
    for s in setups:
        et = s.get("session_et")
        if et is not None and et <= lap_start_et + 0.5:
            cur = s
    return cur


# ---------------------------------------------------------------- paliwo w setupie
_RE_FUEL = re.compile(r"^(FuelSetting)=(\d+)//\s*([\d.]+)\s*L.*$", re.M)
_RE_VE = re.compile(r"^(VirtualEnergySetting)=(\d+)//\s*(\d+)\s*%.*$", re.M)


def fuel_kind(text: str) -> str | None:
    """'ve' = auto z energia wirtualna (Hypercar/GT3), 'liters' = paliwo w litrach (LMP2/LMP3)."""
    if _RE_VE.search(text):
        return "ve"
    if _RE_FUEL.search(text):
        return "liters"
    return None


def fuel_mapping(vehicle: str) -> tuple[float, float]:
    """litry = a * indeks + b dla danego auta (z zapisanych setupow; domyslnie 1 L/krok, +5 L)."""
    pts = set()
    for e in library():
        if e["vehicle"] != vehicle:
            continue
        try:
            text = Path(e["path"]).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _RE_FUEL.search(text)
        if m:
            pts.add((int(m.group(2)), float(m.group(3))))
        if len(pts) >= 6:
            break
    pts = sorted(pts)
    if len(pts) >= 2 and pts[-1][0] != pts[0][0]:
        a = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
        return a, pts[0][1] - a * pts[0][0]
    if pts:
        return 1.0, pts[0][1] - pts[0][0]
    return 1.0, 5.0


def with_fuel(text: str, liters: float | None = None, ve_pct: float | None = None):
    """Kopia setupu z ustawionym paliwem. Zaokragla W GORE do kroku gry.
    Zwraca (nowy_tekst, opis_ustawienia) albo (None, powod)."""
    kind = fuel_kind(text)
    if kind == "ve":
        if ve_pct is None:
            return None, "to auto tankuje energię wirtualną [%], a brak danych zużycia energii"
        pct = max(1, min(100, int(-(-ve_pct // 1))))
        new = _RE_VE.sub(lambda m: f"{m.group(1)}={pct}//{pct}%", text, count=1)
        return new, f"energia wirtualna {pct}%"
    if kind == "liters":
        if liters is None:
            return None, "brak danych zużycia paliwa"
        veh = parse(text)["vehicle"]
        a, b = fuel_mapping(veh)
        idx = max(0, int(-(-(liters - b) // a)))
        val = a * idx + b
        new = _RE_FUEL.sub(lambda m: f"{m.group(1)}={idx}//{val:.1f}L", text, count=1)
        return new, f"paliwo {val:.1f} L"
    return None, "w tym setupie nie znalazłem ustawienia paliwa"
