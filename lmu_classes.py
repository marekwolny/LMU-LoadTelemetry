"""Najlepsze czasy per klasa / per auto z surowych nagran (records/*.csv.gz).

Dla kazdego kierowcy w danym aucie:
  * best   — najlepsze okrazenie wg gry (mBestLapTime),
  * s1..s3 — sektory TEGO okrazenia (z wiersza, w ktorym gra zapisala nowy best),
  * ideal  — suma najlepszych sektorow ze wszystkich ukonczonych okrazen,
  * laps   — liczba przejechanych okrazen.
Wynik kazdego nagrania jest cache'owany w analysis/_best/<stem>.json.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

CLASS_NAMES = {"Hyper": "Hypercar", "HYPER": "Hypercar", "LMP2_ELMS": "LMP2 ELMS",
               "LMGT3": "LMGT3", "GTE": "LMGTE"}
CLASS_ORDER = ["Hyper", "HYPER", "LMP2", "LMP2_ELMS", "LMP3", "GTE", "LMGT3", "GT3"]
VERSION = 2


def class_label(c):
    return CLASS_NAMES.get(c, c or "?")


def class_rank(c):
    return CLASS_ORDER.index(c) if c in CLASS_ORDER else len(CLASS_ORDER)


def team_of(vehicle):
    """'IDEC Sport #28:ELMS25' -> 'IDEC Sport'."""
    v = (vehicle or "").split("#")[0].strip(" :-")
    return v or (vehicle or "?")


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def scan(path: Path) -> list[dict]:
    best: dict = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        try:
            head = next(r)
        except StopIteration:
            return []
        ix = {k: i for i, k in enumerate(head)}
        need = ("driver", "vehicle", "best_lap")
        if any(k not in ix for k in need):
            return []
        g = lambda row, k: row[ix[k]] if k in ix and ix[k] < len(row) else ""
        for row in r:
            if len(row) < len(head) - 2:
                continue
            key = (row[ix["driver"]], row[ix["vehicle"]])
            e = best.get(key)
            if e is None:
                e = best[key] = dict(driver=key[0], vehicle=key[1], cls=g(row, "car_class"),
                                     player=g(row, "is_player") in ("1", "True", "true"),
                                     best=None, s1=None, s2=None, s3=None,
                                     m1=None, m2=None, m3=None, laps=0)
            try:
                e["laps"] = max(e["laps"], int(float(g(row, "lap") or 0)))
            except ValueError:
                pass
            if g(row, "is_player") in ("1", "True", "true"):
                e["player"] = True
            last, l1, l2 = _f(g(row, "last_lap")), _f(g(row, "last_sector1")), _f(g(row, "last_sector2"))
            if last and l1 and l2 and l2 > l1 and last > l2:
                for k, v in (("m1", l1), ("m2", l2 - l1), ("m3", last - l2)):
                    if e[k] is None or v < e[k]:
                        e[k] = v
            b = _f(g(row, "best_lap"))
            if not b or (e["best"] is not None and b > e["best"] + 1e-6):
                continue
            if e["best"] is None or b < e["best"] - 1e-6:
                e["best"], e["s1"], e["s2"], e["s3"] = b, None, None, None
            # sektory najlepszego okrazenia = last_sector* w wierszu, gdzie last_lap == best
            if e["s1"] is None and last and abs(last - b) < 1e-3 and l1 and l2 and l2 > l1:
                e["s1"], e["s2"], e["s3"] = l1, l2 - l1, b - l2
            elif e["s1"] is None:
                b1, b2 = _f(g(row, "best_sector1")), _f(g(row, "best_sector2"))
                if b1 and b2 and b2 > b1 and b > b2:
                    e["s1"], e["s2"], e["s3"] = b1, b2 - b1, b - b2
    out = []
    for e in best.values():
        if not e["best"]:
            continue
        ms = (e.pop("m1"), e.pop("m2"), e.pop("m3"))
        mins = [min(x for x in (m, s) if x is not None) if (m or s) else None
                for m, s in zip(ms, (e["s1"], e["s2"], e["s3"]))]
        e["ideal"] = sum(mins) if None not in mins else None
        out.append(e)
    return out


def best_laps(path: Path, cache_dir: Path) -> list[dict]:
    path = Path(path)
    st = path.stat()
    stem = path.name[:-7] if path.name.endswith(".csv.gz") else path.stem
    cpath = Path(cache_dir) / f"{stem}.json"
    sig = [VERSION, st.st_size, int(st.st_mtime)]
    try:
        c = json.loads(cpath.read_text(encoding="utf-8"))
        if c.get("sig") == sig:
            return c["rows"]
    except (OSError, ValueError, KeyError):
        pass
    rows = scan(path)
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"sig": sig, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return rows


def merge(per_session: list[tuple[str, list[dict]]]) -> list[dict]:
    """Laczy kilka sesji: najlepszy wynik kierowcy w danym aucie + skad pochodzi."""
    out: dict = {}
    for label, rows in per_session:
        for e in rows:
            k = (e["driver"], e["vehicle"])
            o = out.get(k)
            if o is None or e["best"] < o["best"]:
                n = dict(e, session=label, laps=e["laps"] + (o["laps"] if o else 0))
                if o and o.get("ideal") and (not n.get("ideal") or o["ideal"] < n["ideal"]):
                    n["ideal"] = o["ideal"]
                n["player"] = e["player"] or bool(o and o["player"])
                out[k] = n
            else:
                o["laps"] += e["laps"]
                o["player"] = o["player"] or e["player"]
                if e.get("ideal") and (not o.get("ideal") or e["ideal"] < o["ideal"]):
                    o["ideal"] = e["ideal"]
    return list(out.values())


def enrich(rows: list[dict], xml: dict) -> list[dict]:
    """Uzupelnia sektory / idealne okrazenie / model auta z pliku wynikow LMU (lmu_results)."""
    if not xml:
        return rows
    norm = lambda n: (n or "").lower().split("#")[0].strip()
    by = {norm(k): v for k, v in xml.items()}
    for e in rows:
        x = by.get(norm(e["driver"]))
        if not x:
            continue
        e["model"] = x.get("model") or e.get("model")
        same = x.get("best") and e.get("best") and abs(x["best"] - e["best"]) < 0.002
        if (same or not e.get("s1")) and x.get("s1"):
            e["s1"], e["s2"], e["s3"] = x["s1"], x["s2"], x["s3"]
        if x.get("ideal") and (not e.get("ideal") or x["ideal"] < e["ideal"]):
            e["ideal"] = x["ideal"]
    # kierowcy z pliku wynikow, ktorych nie ma w nagraniu (import z gry = tylko gracz,
    # albo dojechali po zatrzymaniu nagrywania)
    have = {norm(e["driver"]) for e in rows}
    for name, x in xml.items():
        if norm(name) in have or not x.get("best"):
            continue
        laps = x.get("laps") or []
        rows.append(dict(driver=name, vehicle=x.get("vehicle") or "", cls=x.get("cls") or "",
                         model=x.get("model") or "", player=False,
                         best=x["best"], s1=x.get("s1"), s2=x.get("s2"), s3=x.get("s3"),
                         ideal=x.get("ideal"),
                         laps=sum(1 for l in laps if l.get("time")) if isinstance(laps, list) else laps))
    return rows
