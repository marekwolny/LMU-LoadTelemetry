"""Podglad wynikow analizy w aplikacji (tkinter Canvas, bez przegladarki).

  * Analysis   — wczytuje summary.json + traces.csv; liczy czas po dystansie i delte,
  * ChartView  — delta / predkosc / gaz / hamulec / bieg po dystansie: zoom (przeciagnij,
                 kolko), przesuwanie (prawy przycisk), kursor z wartosciami, pasek
                 "ja vs rywal" i kafelki odcinkow z zyskiem/strata,
  * MapView    — mapa toru pokolorowana zyskiem/strata (albo hamowaniem); klik = zoom.

Czas po dystansie liczony jest z predkosci (t = suma ds / v) i skalowany tak, zeby na mecie
zgadzal sie z czasem okrazenia z gry. Dla rywali (~5 Hz) delta jest przyblizona.
"""

from __future__ import annotations

import bisect
import csv
import json
import math
from pathlib import Path
import tkinter as tk

from lmu_theme import round_rect

# kolory systemowe iOS (lekko przyciemnione tam, gdzie na bialym byloby za jasno)
PALETTE = ["#007aff", "#ff3b30", "#28b14c", "#af52de", "#ff9500",
           "#0fa5c2", "#d63a9e", "#8e8e93", "#a2845e", "#5856d6"]
C_CARD = "#ffffff"
C_GRID = "#ececf0"
C_AXIS = "#8e8e93"
C_TEXT = "#1c1c1e"
C_MUTED = "#6e6e73"
C_GAIN = "#248a3d"
C_LOSS = "#e0291e"
C_LINE_SOFT = "#e5e5ea"
C_ACCENT = "#007aff"
C_BG = "#f2f2f7"
FONT = "Segoe UI Variable Text"

_cache: dict = {}


def fmt_lap(t):
    if t is None:
        return "—"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def short(n, k=16):
    n = (n or "").split("#")[0]
    return n if len(n) <= k else n[:k - 1] + "…"


class Analysis:
    """Wynik analyze.py / track_analysis.py wczytany do pamieci."""

    def __init__(self, outdir: Path):
        self.outdir = Path(outdir)
        self.summary = json.loads((self.outdir / "summary.json").read_text(encoding="utf-8"))
        drivers = self.summary.get("drivers") or []
        self.drivers = [d["name"] for d in drivers]
        self.lap_time = {d["name"]: d.get("lap_time_s") for d in drivers}
        self.reference = self.summary.get("reference") or (self.drivers[0] if self.drivers else None)
        self.track_len = float(self.summary.get("track_length_m") or 0)
        self.braking = sorted(self.summary.get("braking") or [],
                              key=lambda z: z.get("ref_start") or 0)
        self.traces: dict[str, dict[str, list]] = {}
        path = self.outdir / "traces.csv"
        if path.exists():
            with open(path, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    t = self.traces.get(r["driver"])
                    if t is None:
                        t = self.traces[r["driver"]] = {k: [] for k in
                                                        ("d", "thr", "brk", "spd", "gear", "x", "z")}
                    t["d"].append(float(r["distance_m"]))
                    t["thr"].append(float(r["throttle"]))
                    t["brk"].append(float(r["brake"]))
                    t["spd"].append(float(r["speed_kmh"]))
                    try:
                        t["gear"].append(int(float(r["gear"])))
                    except ValueError:
                        t["gear"].append(0)
                    t["x"].append(float(r["x"]))
                    t["z"].append(float(r["z"]))
                    if r.get("lat") not in (None, ""):
                        # w grze dodatnie = prawa strona; tu "side" > 0 = lewa (gora wykresu)
                        t.setdefault("side", []).append(-float(r["lat"]))
        for t in self.traces.values():
            if "side" in t and len(t["side"]) != len(t["d"]):
                del t["side"]
        self.has_line = any("side" in t for t in self.traces.values())
        # granice toru (edges.csv): lewa / prawa w tym samym ukladzie co "side"
        self.edges = None
        try:
            ed, el, er = [], [], []
            with open(self.outdir / "edges.csv", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    ed.append(float(r["distance_m"]))
                    el.append(-float(r["edge_neg_m"]))
                    er.append(-float(r["edge_pos_m"]))
            if ed:
                self.edges = (ed, el, er)
        except (OSError, ValueError, KeyError):
            pass
        if not self.track_len:
            self.track_len = max((t["d"][-1] for t in self.traces.values() if t["d"]), default=1.0)
        self.colors = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(self.drivers)}
        for n in self.traces:
            self.colors.setdefault(n, PALETTE[len(self.colors) % len(PALETTE)])
        # rzeczywista czestotliwosc danych na okrazeniu (probki / czas okrazenia)
        self.rate = {}
        try:
            with open(self.outdir / "best_laps.csv", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    lt, n = float(r.get("lap_time_s") or 0), float(r.get("samples") or 0)
                    if lt > 0 and n > 0:
                        self.rate[r["driver"]] = n / lt
        except (OSError, ValueError, KeyError):
            pass
        self._integrate()
        self.base = None
        self.segments = self._segments()
        self._segcache = {"hamowania": self.segments}
        self.optimal = self._optimal()

    @staticmethod
    def load(outdir: Path) -> "Analysis":
        outdir = Path(outdir)
        key = str(outdir)
        try:
            stamp = (outdir / "summary.json").stat().st_mtime
        except OSError:
            stamp = 0
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        a = Analysis(outdir)
        _cache[key] = (stamp, a)
        return a

    # ------------------------------------------------------------ czas i delta
    def _integrate(self):
        for n, tr in self.traces.items():
            d, v = tr["d"], tr["spd"]
            t = [0.0]
            for i in range(1, len(d)):
                vm = max((v[i] + v[i - 1]) / 2, 5.0) / 3.6
                t.append(t[-1] + (d[i] - d[i - 1]) / vm)
            lap = self.lap_time.get(n)
            if lap and len(t) > 1 and t[-1] > 0 and self.track_len:
                k = lap * (d[-1] / self.track_len) / t[-1]
                t = [x * k for x in t]
            tr["t"] = t

    def t_at(self, name, dist):
        tr = self.traces.get(name)
        if not tr or not tr["d"]:
            return None
        d, t = tr["d"], tr["t"]
        i = bisect.bisect_left(d, dist)
        if i <= 0:
            return t[0]
        if i >= len(d):
            return t[-1]
        f = (dist - d[i - 1]) / max(d[i] - d[i - 1], 1e-9)
        return t[i - 1] + f * (t[i] - t[i - 1])

    def set_base(self, base):
        """Delta kazdego kierowcy wzgledem `base` (dodatnia = wolniej niz base)."""
        if base == self.base or base not in self.traces:
            return
        self.base = base
        for n, tr in self.traces.items():
            tr["dl"] = [tt - self.t_at(base, dd) for dd, tt in zip(tr["d"], tr["t"])]

    def _segments(self):
        """Odcinki: od punktu hamowania Hk (-30 m) do punktu hamowania Hk+1."""
        L = self.track_len
        cuts = []
        for z in self.braking:
            rs = z.get("ref_start")
            if rs is None:
                continue
            c = max(0.0, rs - 30)
            if not cuts or c - cuts[-1][0] > 60:
                cuts.append((c, z.get("corner", "?")))
        segs = []
        if not cuts or cuts[0][0] > 60:
            segs.append(["Start", 0.0, cuts[0][0] if cuts else L])
        for i, (c, name) in enumerate(cuts):
            end = cuts[i + 1][0] if i + 1 < len(cuts) else L
            segs.append([name, 0.0 if (i == 0 and len(segs) == 0) else c, end])
        return [tuple(s) for s in segs]

    def segments_for(self, mode):
        """Podzial okrazenia: hamowania | zakręty | co 200 m | co 100 m | co 50 m."""
        hit = self._segcache.get(mode)
        if hit is None:
            hit = self._segcache[mode] = self._build_segments(mode)
        return hit

    def _build_segments(self, mode):
        L = self.track_len
        if mode.startswith("co "):
            step = float(mode.split()[1])
            n = max(1, round(L / step))
            return [(f"S{i + 1}", L * i / n, L * (i + 1) / n) for i in range(n)]
        if mode != "zakręty":
            return self.segments
        # zakret = od polowy prostej przed nim do polowy prostej za nim
        corners = []
        for z in self.braking:
            rs = z.get("ref_start")
            if rs is None:
                continue
            ups = sorted(p["pickup"] for p in z.get("drivers") or [] if p.get("pickup") is not None)
            up = ups[len(ups) // 2] if ups else rs + 150
            if corners and rs - corners[-1][1] < 60:
                corners[-1][2] = max(corners[-1][2], up)
                continue
            corners.append([z.get("corner", "?"), rs, max(up, rs + 20)])
        if not corners:
            return self.segments
        cuts = [corners[0][1] / 2]
        for (_, _, up), (_, rs, _) in zip(corners, corners[1:]):
            cuts.append((min(up, rs) + rs) / 2)
        cuts.append(L)
        segs = []
        if cuts[0] > 60:
            segs.append(("Start", 0.0, cuts[0]))
        else:
            cuts[0] = 0.0
        for i, c in enumerate(corners):
            segs.append((c[0], cuts[i], cuts[i + 1]))
        return segs

    def seg_stats(self, f, b, d0, d1):
        """Szczegoly odcinka [d0, d1] dla dwoch kierowcow."""
        out = {}
        for key, n in (("f", f), ("b", b)):
            tr = self.traces.get(n)
            if not tr or not tr["d"]:
                return None
            t0, t1 = self.t_at(n, d0), self.t_at(n, d1)
            i0 = bisect.bisect_left(tr["d"], d0)
            i1 = max(i0 + 1, bisect.bisect_right(tr["d"], d1))
            spd = tr["spd"][i0:i1] or [0.0]
            im = min(range(len(spd)), key=spd.__getitem__)
            brk = next((tr["d"][i] for i in range(i0, min(i1, len(tr["d"])))
                        if tr["brk"][i] > 0.15), None)
            full = next((tr["d"][i] for i in range(i0 + im, min(i1, len(tr["d"])))
                         if tr["thr"][i] > 0.95), None)
            out[key] = dict(t=(t1 - t0) if None not in (t0, t1) else None,
                            vin=self.value_at(n, "spd", d0), vout=self.value_at(n, "spd", d1),
                            vmin=spd[im], dmin=tr["d"][min(i0 + im, len(tr["d"]) - 1)],
                            vmax=max(spd), brk=brk, full=full)
        tf, tb = out["f"]["t"], out["b"]["t"]
        out["delta"] = (tf - tb) if None not in (tf, tb) else None
        return out

    def _optimal(self):
        """Linia "optymalna" z danych: na kazdym odcinku linia najszybszego tam kierowcy."""
        out = []
        cands = [n for n in self.drivers if "side" in self.traces.get(n, {})]
        if len(cands) < 2:
            return out
        for name, d0, d1 in self.segments:
            best = min(cands, key=lambda n: (self.t_at(n, d1) or 1e9) - (self.t_at(n, d0) or 0))
            out.append((d0, d1, best))
        return out

    def optimal_driver(self, dist):
        for d0, d1, n in self.optimal:
            if d0 <= dist <= d1:
                return n
        return None

    def seg_delta(self, name, base, d0, d1):
        a0, a1 = self.t_at(name, d0), self.t_at(name, d1)
        b0, b1 = self.t_at(base, d0), self.t_at(base, d1)
        if None in (a0, a1, b0, b1):
            return None
        return (a1 - a0) - (b1 - b0)

    def edge_at(self, dist):
        if not self.edges:
            return None
        ed, el, er = self.edges
        i = min(max(bisect.bisect_left(ed, dist), 0), len(ed) - 1)
        return el[i], er[i]

    def value_at(self, name, key, dist):
        t = self.traces.get(name)
        if not t or not t["d"] or key not in t:
            return None
        i = bisect.bisect_left(t["d"], dist)
        i = min(max(i, 0), len(t["d"]) - 1)
        return t[key][i]


def nice_step(span, target):
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class ChartView(tk.Frame):
    # (tytul, klucz, rodzaj, waga wysokosci, tlo panelu)
    PANELS = [("DELTA [s]", "dl", "delta", 1.8, "#f7f5fc"),
              ("PRĘDKOŚĆ [km/h]", "spd", "speed", 1.9, "#f3f7fe"),
              ("GAZ", "thr", "pct", 1.0, "#f2fbf4"),
              ("HAMULEC", "brk", "pct", 1.0, "#fef4f3"),
              ("BIEG", "gear", "gear", 0.6, "#f7f7f9"),
              ("LINIA [m]  góra = lewa krawędź, dół = prawa  ·  czarna przerywana = optimum "
               "(najszybszy kierowca na każdym odcinku)",
               "side", "line", 1.7, "#eef5ea")]
    ML, MR, MT, MB, GAP = 58, 14, 44, 24, 10
    LANE = (18, 38)          # pasek odcinkow nad wykresami (y od-do)
    SEG_MODES = ["hamowania", "zakręty", "co 200 m", "co 100 m", "co 50 m"]
    SORTS = ["po kolei", "największa strata", "największy zysk"]
    BAR = "#f2f2f7"

    def __init__(self, master, on_hover=None, on_range=None, on_option=None,
                 seg_mode="hamowania", seg_sort="po kolei", **kw):
        super().__init__(master, background=C_CARD, **kw)
        self.on_hover = on_hover
        self.on_range = on_range
        self.on_option = on_option
        self.a: Analysis | None = None
        self.selected: list[str] = []
        self.focus = None
        self.base = None
        self.x0, self.x1 = 0.0, 1.0
        self.seg_mode = tk.StringVar(value=seg_mode if seg_mode in self.SEG_MODES else "hamowania")
        self.seg_sort = tk.StringVar(value=seg_sort if seg_sort in self.SORTS else "po kolei")
        # pasek "ja vs rywal" (na stale u gory) + wykres + kafelki odcinkow
        self.cmp = tk.Canvas(self, background=self.BAR, highlightthickness=0, height=64)
        self.cmp.pack(fill="x")
        self.cmp.bind("<Configure>", lambda e: self._update_cmp())
        self._stats_cache = {}
        self._job = None          # zaplanowane odswiezenie (laczy wiele zdarzen w jedna klatke)
        self._job_full = False
        self._pending_hover = None
        self._chips_key = None
        self.cv = tk.Canvas(self, background=C_CARD, highlightthickness=0, cursor="crosshair",
                            takefocus=1)
        self.cv.pack(fill="both", expand=True)
        tools = tk.Frame(self, background=C_CARD)
        tools.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(tools, text="Odcinki", background=C_CARD, foreground=C_TEXT,
                 font=(FONT, 9, "bold")).pack(side="left", padx=(2, 6))
        from tkinter import ttk
        for var, vals, wd in ((self.seg_mode, self.SEG_MODES, 11), (self.seg_sort, self.SORTS, 17)):
            cb = ttk.Combobox(tools, textvariable=var, values=vals, width=wd, state="readonly")
            cb.pack(side="left", padx=(0, 6))
            cb.bind("<<ComboboxSelected>>", lambda e: self._option_changed())
        for txt, step in (("◀", -1), ("▶", 1)):
            ttk.Button(tools, text=txt, width=3,
                       command=lambda s=step: self.step_segment(s)).pack(side="left", padx=(0, 2))
        self.seg_info = tk.Label(tools, background=C_CARD, foreground=C_MUTED, font=(FONT, 8))
        self.seg_info.pack(side="left", padx=8)
        self.chipcv = tk.Canvas(self, background=C_CARD, highlightthickness=0, height=44)
        self.chipcv.pack(fill="x", padx=6, pady=(3, 6))
        self.chipcv.bind("<Configure>", lambda e: self._build_chips())
        self._drag = None
        self._pan = None
        self._hover_d = None
        self._pin = None
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self._set_hover(None))
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Double-1>", lambda e: (self._set_pin(None), self.reset_zoom()))
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<ButtonPress-3>", lambda e: setattr(self, "_pan", (e.x, self.x0, self.x1)))
        self.cv.bind("<B3-Motion>", self._pan_move)
        self.cv.bind("<Enter>", lambda e: self.cv.focus_set())
        self.cv.bind("<Left>", lambda e: self.step_segment(-1))
        self.cv.bind("<Right>", lambda e: self.step_segment(1))

    # ------------------------------------------------------------ odcinki
    def segs(self):
        return self.a.segments_for(self.seg_mode.get()) if self.a else []

    def _option_changed(self):
        self.redraw()
        self._update_cmp()
        self._build_chips()
        self.cv.focus_set()
        if self.on_option:
            self.on_option()

    def _seg_index(self):
        """Odcinek pokazany w widoku (najwieksze pokrycie) albo None przy calym torze."""
        if not self.a or (self.x1 - self.x0) > 0.6 * self.a.track_len:
            return None
        best, bi = 0.0, None
        for i, (_, s0, s1) in enumerate(self.segs()):
            ov = min(s1, self.x1) - max(s0, self.x0)
            if ov > best:
                best, bi = ov, i
        return bi

    def zoom_segment(self, i):
        segs = self.segs()
        if not segs:
            return
        i %= len(segs)
        _, s0, s1 = segs[i]
        pad = max(15.0, (s1 - s0) * 0.08)
        self.zoom_to(s0 - pad, s1 + pad)

    def step_segment(self, step):
        i = self._seg_index()
        if i is None:
            i = -1 if step > 0 else 0
        self.zoom_segment(i + step)

    def _seg_color(self, v, vmax):
        if v is None or abs(v) < 0.01:
            return "#efeff4", C_MUTED
        f = min(1.0, abs(v) / max(vmax, 0.1))
        if v > 0:
            return MapView._mix("#ffefee", "#ff9b94", f), "#c4170c" if f > 0.4 else C_LOSS
        return MapView._mix("#eaf8ee", "#8ddca5", f), "#1a6e30" if f > 0.4 else C_GAIN

    # ------------------------------------------------------------ dane
    def set_data(self, a: Analysis | None, selected: list[str], focus=None, base=None,
                 keep_zoom=False):
        same = self.a is a
        self.a = a
        self.selected = [n for n in selected if a and n in a.traces]
        self.focus = focus if a and focus in a.traces else None
        self.base = base if a and base in a.traces else None
        if a and self.base:
            a.set_base(self.base)
        if not same:
            self._pin = self._hover_d = None
        if not (keep_zoom and same):
            self.x0, self.x1 = 0.0, (a.track_len if a else 1.0)
        self.redraw()
        self._update_cmp()
        self._build_chips()
        self._notify_range()

    def reset_zoom(self):
        if self.a:
            self.zoom_to(0.0, self.a.track_len)

    def zoom_to(self, d0, d1):
        if not self.a:
            return
        L = self.a.track_len
        d0, d1 = max(0.0, d0), min(L, d1)
        if d1 - d0 < 20:
            mid = (d0 + d1) / 2
            d0, d1 = max(0.0, mid - 10), min(L, mid + 10)
        self.x0, self.x1 = d0, d1
        self._schedule(full=True)

    def _schedule(self, full=False):
        """Odswiez w najblizszej wolnej chwili; kolejne zdarzenia (kolko, przesuwanie,
        ruch myszy) przed narysowaniem sa laczone w jedno odswiezenie."""
        self._job_full = self._job_full or full
        if self._job is None:
            self._job = self.after(8, self._flush)

    def _flush(self):
        self._job = None
        full, self._job_full = self._job_full, False
        if self._pending_hover is not None:
            d = self._pending_hover[0]
            self._pending_hover = None
            self._hover_d = d if d is not None else self._pin
            if self.on_hover:
                self.on_hover(self._hover_d)
        if full:
            self.redraw()
            self._build_chips()
            self._notify_range()
        else:
            self._draw_cursor()
        self._update_cmp()

    def _notify_range(self):
        if self.on_range:
            self.on_range(self.x0, self.x1)

    # ------------------------------------------------------------ geometria
    def _active_panels(self):
        return [p for p in self.PANELS
                if (p[2] != "delta" or self.base) and (p[2] != "line" or self.a.has_line)]

    def _panels(self):
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        spec = self._active_panels()
        total = sum(p[3] for p in spec)
        avail = h - self.MT - self.MB - self.GAP * (len(spec) - 1)
        y = self.MT
        out = []
        for title, key, kind, weight, bg in spec:
            ph = max(28, avail * weight / total)
            lo, hi = self._yrange(key, kind)
            out.append(dict(title=title, key=key, kind=kind, bg=bg, l=self.ML, t=y,
                            r=w - self.MR, b=y + ph, lo=lo, hi=hi))
            y += ph + self.GAP
        return out

    def _visible(self, key):
        vals = []
        for n in self.selected:
            tr = self.a.traces[n]
            if key not in tr:
                continue
            i0 = bisect.bisect_left(tr["d"], self.x0)
            i1 = bisect.bisect_right(tr["d"], self.x1)
            vals.extend(tr[key][i0:i1])
        return vals

    def _yrange(self, key, kind):
        if kind == "pct":
            return -0.03, 1.05
        if kind == "gear":
            vals = self._visible(key)
            return -0.5, max(vals + [6]) + 0.5
        if kind == "line":
            vals = self._visible(key)
            if self.a.edges:
                ed, el, er = self.a.edges
                i0 = bisect.bisect_left(ed, self.x0)
                i1 = bisect.bisect_right(ed, self.x1) + 1
                vals += el[i0:i1] + er[i0:i1]
            lo, hi = (min(vals), max(vals)) if vals else (-6, 6)
            return lo - 1.0, hi + 1.0
        vals = self._visible(key)
        if kind == "delta":
            lo, hi = min(vals + [0.0]), max(vals + [0.0])
            pad = max((hi - lo) * 0.12, 0.05)
            return lo - pad, hi + pad
        lo, hi = (min(vals), max(vals)) if vals else (0, 1)
        pad = max((hi - lo) * 0.08, 2)
        return max(0, lo - pad), hi + pad

    def _y(self, p, v):
        return p["b"] - (v - p["lo"]) / max(p["hi"] - p["lo"], 1e-9) * (p["b"] - p["t"])

    def _xpix(self, d):
        w = self.cv.winfo_width()
        return self.ML + (d - self.x0) / max(self.x1 - self.x0, 1e-6) * (w - self.ML - self.MR)

    def _xdist(self, px):
        w = self.cv.winfo_width()
        return self.x0 + (px - self.ML) / max(w - self.ML - self.MR, 1) * (self.x1 - self.x0)

    @staticmethod
    def _fmt(kind, v):
        if kind == "pct":
            return f"{v * 100:.0f}%"
        if kind == "delta":
            return f"{v:+.2f}"
        if kind == "line":
            return "0" if abs(v) < 0.05 else f"{abs(v):.0f} {'L' if v > 0 else 'P'}"
        if kind == "gear":
            return "N" if v == 0 else ("R" if v < 0 else f"{v:.0f}")
        return f"{v:.0f}"

    # ------------------------------------------------------------ rysowanie
    def redraw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            return
        if not self.a or not self.selected:
            cv.create_text(w / 2, h / 2, fill=C_MUTED, font=(FONT, 11),
                           text="Brak danych do wykresu." if not self.a else
                           "Zaznacz kierowców w zakładce Wyniki.")
            return
        panels = self._panels()
        self._pcache = panels
        for p in panels:
            round_rect(cv, p["l"], p["t"], p["r"], p["b"], 8, fill=p["bg"], outline="")
        # siatka X
        step = nice_step(self.x1 - self.x0, max(4, (w - self.ML) // 90))
        d = math.ceil(self.x0 / step) * step
        bottom = panels[-1]["b"]
        while d <= self.x1:
            px = self._xpix(d)
            for p in panels:
                cv.create_line(px, p["t"], px, p["b"], fill=C_GRID)
            cv.create_text(px, bottom + 4, anchor="n", fill=C_AXIS, font=(FONT, 8),
                           text=f"{d:.0f}" if step >= 1 else f"{d:.1f}")
            d += step
        cv.create_text(self.ML - 6, bottom + 4, anchor="ne", fill=C_AXIS, font=(FONT, 8),
                       text="[m]")
        self._draw_lane(panels)
        # siatka Y + linie
        for p in panels:
            ys = nice_step(p["hi"] - p["lo"], max(3 if p["kind"] == "delta" else 2, int((p["b"] - p["t"]) // 24)))
            if p["kind"] == "gear":
                ys = max(1, ys)
            v = math.ceil(p["lo"] / ys) * ys
            while v <= p["hi"] + 1e-9:
                py = self._y(p, v)
                zero = p["kind"] == "delta" and abs(v) < 1e-9
                cv.create_line(p["l"], py, p["r"], py, fill="#a39bb8" if zero else C_GRID,
                               width=1.4 if zero else 1)
                v += ys
            if p["kind"] == "line" and self.a.edges:
                self._draw_track_band(p)
            if p["kind"] == "line":
                self._draw_optimal(p)
            order = [n for n in self.selected if n not in (self.focus, self.base)]
            order += [n for n in (self.base, self.focus) if n in self.selected]
            for n in order:
                tr = self.a.traces[n]
                vals = tr.get(p["key"])
                if vals is None:
                    continue
                i0 = max(0, bisect.bisect_left(tr["d"], self.x0) - 1)
                i1 = min(len(tr["d"]), bisect.bisect_right(tr["d"], self.x1) + 1)
                stride = max(1, (i1 - i0) // max(400, int(p["r"] - p["l"])))
                pts = []
                for i in range(i0, i1, stride):
                    pts.append(self._xpix(tr["d"][i]))
                    pts.append(self._y(p, vals[i]))
                if len(pts) >= 4:
                    width = 2.4 if n == self.focus else (1.9 if n == self.base else 1.3)
                    cv.create_line(*pts, fill=self.a.colors[n], width=width)
            # maska poza panelem (przy zoomie)
            cv.create_rectangle(0, p["t"] - 1, p["l"] - 1, p["b"] + 1, fill=C_CARD, outline="")
            cv.create_rectangle(p["r"] + 1, p["t"] - 1, w, p["b"] + 1, fill=C_CARD, outline="")
            cv.create_line(p["l"], p["t"], p["l"], p["b"], fill=C_AXIS)
            v = math.ceil(p["lo"] / ys) * ys
            while v <= p["hi"] + 1e-9:
                cv.create_text(p["l"] - 6, self._y(p, v), anchor="e", fill=C_AXIS,
                               font=(FONT, 8), text=self._fmt(p["kind"], v))
                v += ys
            title = p["title"]
            if p["kind"] == "delta" and self.base:
                title += f"  vs {short(self.base, 20)}   (wyżej = wolniej, niżej = szybciej)"
            cv.create_text(p["l"] + 6, p["b"] - 3, anchor="sw", text=title, fill=C_MUTED,
                           font=(FONT, 8, "bold"))
        zoomed = self.x0 > 0.5 or self.x1 < self.a.track_len - 0.5
        if zoomed:
            cv.create_text(w - self.MR, 4, anchor="ne", fill=C_MUTED, font=(FONT, 8),
                           text=f"zoom {self.x0:.0f}–{self.x1:.0f} m  ·  dwuklik = cały tor")
        self._draw_cursor()

    def _seg_deltas(self):
        segs = self.segs()
        if not (self.focus and self.base and self.focus != self.base):
            return segs, [None] * len(segs), 0.1
        vals = [self.a.seg_delta(self.focus, self.base, s0, s1) for _, s0, s1 in segs]
        return segs, vals, max([abs(v) for v in vals if v is not None] + [0.1])

    def _draw_lane(self, panels):
        """Pasek odcinkow nad wykresami (wyrownany z osia X) + granice odcinkow."""
        cv = self.cv
        y0, y1 = self.LANE
        segs, vals, vmax = self._seg_deltas()
        act = self._seg_index()
        many = len(segs) > 30
        cv.create_text(self.ML - 6, (y0 + y1) / 2, anchor="e", fill=C_AXIS,
                       font=(FONT, 7, "bold"), text="ODCINKI")
        for i, ((name, s0, s1), v) in enumerate(zip(segs, vals)):
            if s1 < self.x0 or s0 > self.x1:
                continue
            a0, a1 = self._xpix(max(s0, self.x0)), self._xpix(min(s1, self.x1))
            bg, fg = self._seg_color(v, vmax)
            round_rect(cv, a0 + 1, y0, a1 - 1, y1, 7, fill=bg, tags=("lane", f"seg{i}"),
                       outline=C_TEXT if i == act else "", width=2 if i == act else 1)
            for txt in (f"{name} {v:+.2f}" if v is not None else name, name):
                tid = cv.create_text((a0 + a1) / 2, (y0 + y1) / 2, text=txt, fill=fg,
                                     font=(FONT, 8, "bold"), tags=("lane", f"seg{i}"))
                bx = cv.bbox(tid)
                if bx[2] - bx[0] <= a1 - a0 - 4:
                    break
                cv.delete(tid)
            if s0 > 0 and self.x0 <= s0 <= self.x1:
                px = self._xpix(s0)
                for p in panels:
                    cv.create_line(px, p["t"], px, p["b"], fill="#e9e3ec" if many else "#d8d2e0",
                                   dash=(2, 4) if many else (3, 3))
        cv.tag_bind("lane", "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind("lane", "<Leave>", lambda e: cv.configure(cursor="crosshair"))

    def _draw_track_band(self, p):
        ed, el, er = self.a.edges
        i0 = max(0, bisect.bisect_left(ed, self.x0) - 1)
        i1 = min(len(ed), bisect.bisect_right(ed, self.x1) + 1)
        stride = max(1, (i1 - i0) // 600)
        idx = list(range(i0, i1, stride))
        if len(idx) < 2:
            return
        top = [(self._xpix(ed[i]), self._y(p, el[i])) for i in idx]
        bot = [(self._xpix(ed[i]), self._y(p, er[i])) for i in idx]
        poly = [c for xy in top + bot[::-1] for c in xy]
        self.cv.create_polygon(*poly, fill="#dfdfe5", outline="")
        self.cv.create_line(*[c for xy in top for c in xy], fill="#636366", width=2, dash=(6, 3))
        self.cv.create_line(*[c for xy in bot for c in xy], fill="#636366", width=2, dash=(6, 3))

    def _draw_optimal(self, p):
        for s0, s1, n in self.a.optimal:
            if s1 < self.x0 or s0 > self.x1:
                continue
            tr = self.a.traces[n]
            i0 = max(0, bisect.bisect_left(tr["d"], max(s0, self.x0)) - 1)
            i1 = min(len(tr["d"]), bisect.bisect_right(tr["d"], min(s1, self.x1)) + 1)
            stride = max(1, (i1 - i0) // 500)
            pts = [c for i in range(i0, i1, stride)
                   for c in (self._xpix(tr["d"][i]), self._y(p, tr["side"][i]))]
            if len(pts) >= 4:
                self.cv.create_line(*pts, fill="#1c1c1e", width=2, dash=(5, 3))

    def _draw_cursor(self):
        cv = self.cv
        cv.delete("cursor")
        d = self._hover_d
        if d is None or not self.a or not self.selected or not getattr(self, "_pcache", None):
            return
        panels = self._pcache
        if self._pin is not None and self.x0 <= self._pin <= self.x1:
            pp = self._xpix(self._pin)
            cv.create_line(pp, panels[0]["t"], pp, panels[-1]["b"], fill=C_ACCENT, width=1.5,
                           dash=(4, 2) if abs(pp - self._xpix(d)) > 1 else (), tags="cursor")
            cv.create_polygon(pp - 5, panels[0]["t"] - 6, pp + 5, panels[0]["t"] - 6,
                              pp, panels[0]["t"], fill=C_ACCENT, outline="", tags="cursor")
        px = self._xpix(d)
        cv.create_line(px, panels[0]["t"], px, panels[-1]["b"], fill="#1c1c1e", tags="cursor")
        right = px < self.cv.winfo_width() - 110
        show = [n for n in (self.focus, self.base) if n in self.selected] or self.selected[:2]
        show += [n for n in self.selected if n not in show][:2]
        for p in panels:
            items = []
            for n in show:
                v = self.a.value_at(n, p["key"], d)
                if v is None:
                    continue
                py = self._y(p, v)
                cv.create_oval(px - 3, py - 3, px + 3, py + 3, fill=self.a.colors[n],
                               outline=C_CARD, tags="cursor")
                items.append([py, n, v])
            # etykiety obok kursora, bez nachodzenia na siebie
            items.sort()
            lab_h = 15
            for i in range(1, len(items)):
                items[i][0] = max(items[i][0], items[i - 1][0] + lab_h)
            over = items[-1][0] + lab_h / 2 - p["b"] if items else 0
            for it in items:
                y = it[0] - max(0, over)
                y = max(y, p["t"] + lab_h / 2)
                txt = self._fmt(p["kind"], it[2]) + (" km/h" if p["kind"] == "speed" else "")
                if p["kind"] == "line":
                    txt = f"{abs(it[2]):.1f} m {'L' if it[2] > 0 else 'P'}"
                if p["kind"] == "delta" and it[1] == self.base:
                    continue
                x = px + 6 if right else px - 6
                tid = cv.create_text(x + (4 if right else -4), y, anchor="w" if right else "e",
                                     text=txt, fill="#ffffff", font=(FONT, 8, "bold"),
                                     tags="cursor")
                x0, y0, x1, y1 = cv.bbox(tid)
                rid = round_rect(cv, x0 - 5, y0 - 1, x1 + 5, y1 + 1, 6,
                                 fill=self.a.colors[it[1]], outline="", tags="cursor")
                cv.tag_raise(tid, rid)

    # ------------------------------------------------------------ pasek ja vs rywal
    # Rysowany na Canvasie o stalej wysokosci: zadnych nowych widgetow przy ruchu myszy,
    # wiec Tk nie przelicza ukladu calego okna.
    def _runs(self, x, y, runs):
        """Ciag kolorowych tekstow od x; zwraca x konca."""
        cv = self.cmp
        items = []
        for text, fg, font in runs:
            tid = cv.create_text(x, y, text=text, fill=fg, font=font, anchor="w")
            bx = cv.bbox(tid)
            x = bx[2] if bx else x
            items.append(tid)
        return x

    def _side(self, name, t, x, right=False):
        cv, a = self.cmp, self.a
        y = 17
        if not right:
            cv.create_rectangle(x, y - 5, x + 10, y + 5, fill=a.colors[name], outline="")
            x = self._runs(x + 16, y, [(short(name, 22), C_TEXT, (FONT, 9, "bold"))])
            tid = cv.create_text(x + 14, y, text=fmt_lap(t), anchor="w", fill=C_TEXT,
                                 font=("Consolas", 11, "bold"))
            bx = cv.bbox(tid)
            r = round_rect(cv, bx[0] - 7, bx[1] - 2, bx[2] + 7, bx[3] + 2, 7, fill=C_CARD, outline="")
            cv.tag_raise(tid, r)
            return bx[2] + 7
        tid = cv.create_text(x, y, text=fmt_lap(t), anchor="e", fill=C_TEXT,
                             font=("Consolas", 11, "bold"))
        bx = cv.bbox(tid)
        r = round_rect(cv, bx[0] - 7, bx[1] - 2, bx[2] + 7, bx[3] + 2, 7, fill=C_CARD, outline="")
        cv.tag_raise(tid, r)
        nid = cv.create_text(bx[0] - 14, y, text=short(name, 22), anchor="e", fill=C_TEXT,
                             font=(FONT, 9, "bold"))
        nb = cv.bbox(nid)
        cv.create_rectangle(nb[0] - 16, y - 5, nb[0] - 6, y + 5, fill=a.colors[name], outline="")
        return nb[0] - 16

    def _update_cmp(self):
        cv = self.cmp
        cv.delete("all")
        a = self.a
        W = cv.winfo_width()
        if W < 50:
            return
        if not a or not self.focus or not self.base or self.focus == self.base:
            cv.create_text(10, 17, anchor="w", fill=C_MUTED, font=(FONT, 9),
                           text="Wybierz „Porównaj” i „vs” nad wykresem, aby zobaczyć deltę.  "
                                "Przeciągnij = zoom · kółko = zoom · prawy przycisk = przesuń · "
                                "dwuklik = cały tor")
            return
        d = self._hover_d
        if d is None:
            tf, tb = a.lap_time.get(self.focus), a.lap_time.get(self.base)
            where = "całe okrążenie"
        else:
            tf, tb = a.t_at(self.focus, d), a.t_at(self.base, d)
            where = f"{d:.0f} m" + ("  📌" if self._pin is not None and d == self._pin else "")
        delta = (tf - tb) if (tf is not None and tb is not None) else None
        xl = self._side(self.focus, tf, 10)
        xr = self._side(self.base, tb, W - 12, right=True)
        runs = []
        if delta is not None:
            col = C_LOSS if delta > 0.0005 else (C_GAIN if delta < -0.0005 else C_TEXT)
            what = "tracisz" if delta > 0.0005 else ("zyskujesz" if delta < -0.0005 else "równo")
            runs += [(f"{delta:+.3f} s", col, ("Consolas", 14, "bold")),
                     (f"   {where} · {what}", C_MUTED, (FONT, 9))]
        if d is not None and a.optimal:
            on = a.optimal_driver(d)
            if on:
                runs.append((f"   · optimum tu: {short(on, 16)}", C_TEXT, (FONT, 9)))
        while runs:
            end = self._runs(0, 17, runs)
            ids = cv.find_all()[-len(runs):]
            x0 = max(xl + 16, (xl + xr) / 2 - end / 2)
            if x0 + end <= xr - 12 or len(runs) == 1:
                for tid in ids:
                    cv.move(tid, x0, 0)
                break
            cv.delete(*ids)          # za ciasno: skroc od konca (optimum, potem opis)
            runs.pop()
        cv.create_line(0, 34, W, 34, fill=C_LINE_SOFT)
        self._update_cmp2(d, W)

    def _update_cmp2(self, d, W):
        """Drugi wiersz: szczegoly odcinka pod kursorem / w widoku / zakresu widoku."""
        a, cv, y = self.a, self.cmp, 50
        segs, vals, _ = self._seg_deltas()
        i = None
        if d is not None:
            i = next((k for k, (_, s0, s1) in enumerate(segs) if s0 <= d <= s1), None)
        if i is None:
            i = self._seg_index()
        reg, bold = (FONT, 9), (FONT, 9, "bold")
        if i is None:
            pairs = [(v, s[0]) for s, v in zip(segs, vals) if v is not None]
            if pairs:
                lo, hi = min(pairs), max(pairs)
                lost = sum(v for v, _ in pairs if v > 0)
                self._runs(10, y, [
                    ("Najwięcej tracisz: ", C_MUTED, reg), (f"{hi[1]} {hi[0]:+.3f} s", C_LOSS, bold),
                    ("   ·   najwięcej zyskujesz: ", C_MUTED, reg),
                    (f"{lo[1]} {lo[0]:+.3f} s", C_GAIN, bold),
                    (f"   ·   suma strat {lost:.2f} s na {sum(v > 0 for v, _ in pairs)} z "
                     f"{len(pairs)} odcinków  ·  ◀ ▶ / strzałki = kolejny odcinek", C_MUTED, reg)])
            return
        name, s0, s1 = segs[i]
        key = (id(a), self.seg_mode.get(), self.focus, self.base, s0, s1)
        st = self._stats_cache.get(key)
        if st is None:
            st = self._stats_cache[key] = a.seg_stats(self.focus, self.base, s0, s1)
        if not st:
            return
        f, b = st["f"], st["b"]
        runs = [(name, C_TEXT, (FONT, 10, "bold")), (f"  {s0:.0f}–{s1:.0f} m  ", C_MUTED, (FONT, 8))]
        dl = st["delta"]
        if dl is not None:
            runs.append((f"{dl:+.3f} s", C_LOSS if dl > 0.0005 else (C_GAIN if dl < -0.0005 else C_TEXT),
                         ("Consolas", 11, "bold")))

        def item(title, vf, vb, fmt, better_low, unit=""):
            if vf is None or vb is None:
                return
            diff = vf - vb
            good = (diff < 0) if better_low else (diff > 0)
            col = C_TEXT if abs(diff) < 1e-9 else (C_GAIN if good else C_LOSS)
            runs.extend([(f"  ·  {title} ", C_MUTED, reg), (fmt(vf), col, bold),
                         (f" / {fmt(vb)}{unit}", C_MUTED, reg)])

        item("czas", f["t"], b["t"], lambda v: f"{v:.3f}", True, " s")
        kmh = lambda v: f"{v:.0f}"
        item("wejście", f["vin"], b["vin"], kmh, False)
        item("min.", f["vmin"], b["vmin"], kmh, False)
        item("wyjście", f["vout"], b["vout"], kmh, False, " km/h")
        if f["brk"] is not None and b["brk"] is not None:
            dd = f["brk"] - b["brk"]
            txt = ("hamujesz tak samo" if abs(dd) < 3 else
                   f"hamujesz {abs(dd):.0f} m {'później' if dd > 0 else 'wcześniej'}")
            runs.append((f"  ·  {txt}", C_GAIN if dd >= 3 else (C_LOSS if dd <= -3 else C_TEXT), reg))
        if f["full"] is not None and b["full"] is not None:
            dd = f["full"] - b["full"]
            txt = ("pełny gaz tak samo" if abs(dd) < 3 else
                   f"pełny gaz {abs(dd):.0f} m {'później' if dd > 0 else 'wcześniej'}")
            runs.append((f"  ·  {txt}", C_LOSS if dd >= 3 else (C_GAIN if dd <= -3 else C_TEXT), reg))
        self._runs(10, y, runs)
        # dowolny zakres = zoom (wlasny odcinek)
        tol = max(30.0, (s1 - s0) * 0.12)
        if self._seg_index() is not None and (abs(self.x0 - s0) > tol or abs(self.x1 - s1) > tol):
            v = a.seg_delta(self.focus, self.base, self.x0, self.x1)
            if v is not None:
                tid = cv.create_text(W - 12, y, anchor="e", font=bold,
                                     text=f"widok {self.x0:.0f}–{self.x1:.0f} m: {v:+.3f} s",
                                     fill=C_LOSS if v > 0.0005 else C_GAIN)
                bx = cv.bbox(tid)
                cv.create_rectangle(bx[0] - 8, bx[1] - 2, W, bx[3] + 2, fill=self.BAR, outline="")
                cv.tag_raise(tid)

    # ------------------------------------------------------------ kafelki odcinkow
    def _build_chips(self):
        cv = self.chipcv
        a = self.a
        key = (id(a), self.seg_mode.get(), self.seg_sort.get(), self.focus, self.base,
               self._seg_index(), cv.winfo_width())
        if key == self._chips_key:
            return
        self._chips_key = key
        cv.delete("all")
        segs = self.segs()
        if not a or not segs:
            if int(cv.cget("height")) != 4:
                cv.configure(height=4)
            self.seg_info.configure(text="")
            return
        segs, vals, vmax = self._seg_deltas()
        has = any(v is not None for v in vals)
        order = list(range(len(segs)))
        srt = self.seg_sort.get()
        if has and srt != "po kolei":
            sgn = -1 if srt == "największa strata" else 1
            order.sort(key=lambda k: sgn * vals[k] if vals[k] is not None else 1e9)
        act = self._seg_index()
        compact = len(segs) > 24
        W = cv.winfo_width()
        if W < 50:
            return
        x = y = 0
        rowh = 22 if compact else 40
        for k in order:
            name, s0, s1 = segs[k]
            v = vals[k]
            bg, fg = self._seg_color(v, vmax)
            tag = f"c{k}"
            top = f"{name}  {v:+.2f}" if v is not None else name
            sub = None
            if not compact and has:
                st = a.seg_stats(self.focus, self.base, s0, s1)
                if st:
                    sub = f"min {st['f']['vmin']:.0f} / {st['b']['vmin']:.0f}  ·  {s1 - s0:.0f} m"
            t1 = cv.create_text(0, 0, text=top, font=(FONT, 9, "bold"), fill=fg, anchor="nw",
                                tags=tag)
            w1 = cv.bbox(t1)[2] - cv.bbox(t1)[0]
            t2 = None
            if sub:
                t2 = cv.create_text(0, 0, text=sub, font=(FONT, 7), fill=C_MUTED, anchor="nw",
                                    tags=tag)
                w1 = max(w1, cv.bbox(t2)[2] - cv.bbox(t2)[0])
            cw = w1 + 16
            if x and x + cw > W:
                x, y = 0, y + rowh + 4
            round_rect(cv, x, y, x + cw, y + rowh, 10, fill=bg,
                       outline=C_TEXT if k == act else "", width=2, tags=tag)
            cv.coords(t1, x + 8, y + (3 if compact else 4))
            if t2:
                cv.coords(t2, x + 8, y + 22)
            cv.tag_raise(t1)
            if t2:
                cv.tag_raise(t2)
            cv.tag_bind(tag, "<Button-1>", lambda e, k=k: self.zoom_segment(k))
            cv.tag_bind(tag, "<Enter>", lambda e, s=(s0 + s1) / 2: (cv.configure(cursor="hand2"),
                                                                   self.show_cursor(s, notify=True)))
            cv.tag_bind(tag, "<Leave>", lambda e: (cv.configure(cursor=""), self._set_hover(None)))
            x += cw + 4
        if int(cv.cget("height")) != y + rowh + 2:
            cv.configure(height=y + rowh + 2)
        if has:
            tot = sum(v for v in vals if v is not None)
            self.seg_info.configure(text=f"{len(segs)} odcinków · suma {tot:+.3f} s · "
                                         f"klik = przybliż · najechanie = pokaż na wykresie")
        else:
            self.seg_info.configure(text=f"{len(segs)} odcinków")

    # ------------------------------------------------------------ hover
    def _set_hover(self, d):
        self._pending_hover = (d,)
        self._schedule()

    def _set_pin(self, d):
        self._pin = d
        self._set_hover(d)

    def show_cursor(self, d, notify=False):
        """Kursor ustawiony z zewnatrz (np. z mapy)."""
        if notify:
            return self._set_hover(d)
        self._hover_d = d if d is not None else self._pin
        self._schedule()

    # ------------------------------------------------------------ mysz
    def _in_plot(self, x):
        return self.ML <= x <= self.cv.winfo_width() - self.MR

    def _motion(self, e):
        if not self.a or not self._in_plot(e.x) or e.y < self.MT - 2:
            self._set_hover(None)
            return
        self._set_hover(self._xdist(e.x))

    def _press(self, e):
        self._drag = None
        if self.a and self.LANE[0] <= e.y <= self.LANE[1] and self._in_plot(e.x):
            d = self._xdist(e.x)
            k = next((i for i, (_, s0, s1) in enumerate(self.segs()) if s0 <= d <= s1), None)
            if k is not None:
                self.zoom_segment(k)
            return
        self._drag = e.x if self._in_plot(e.x) else None

    def _drag_move(self, e):
        if self._drag is None or not getattr(self, "_pcache", None):
            return
        self.cv.delete("sel")
        x = min(max(e.x, self.ML), self.cv.winfo_width() - self.MR)
        self.cv.create_rectangle(self._drag, self._pcache[0]["t"], x, self._pcache[-1]["b"],
                                 fill=C_ACCENT, stipple="gray25", outline=C_ACCENT, tags="sel")
        a, b = sorted((self._xdist(self._drag), self._xdist(x)))
        if self.a and self.focus and self.base and b - a > 1:
            v = self.a.seg_delta(self.focus, self.base, a, b)
            if v is not None:
                self.cv.create_text((self._drag + x) / 2, self._pcache[0]["t"] + 4, anchor="n",
                                    text=f"{b - a:.0f} m  {v:+.3f} s", tags="sel",
                                    font=(FONT, 9, "bold"), fill=C_LOSS if v > 0 else C_GAIN)
        self._set_hover(self._xdist(x))

    def _release(self, e):
        self.cv.delete("sel")
        if self._drag is None:
            return
        x = min(max(e.x, self.ML), self.cv.winfo_width() - self.MR)
        if abs(x - self._drag) > 8:
            a, b = sorted((self._xdist(self._drag), self._xdist(x)))
            self.zoom_to(a, b)
        else:
            # zwykly klik = przypnij kursor (klik obok pinezki = odepnij)
            d = self._xdist(x)
            near = self._pin is not None and abs(self._xpix(self._pin) - x) < 6
            self._set_pin(None if near else d)
        self._drag = None

    def _wheel(self, e):
        if not self.a:
            return
        d = self._xdist(e.x) if self._in_plot(e.x) else (self.x0 + self.x1) / 2
        f = 0.8 if e.delta > 0 else 1.25
        span = (self.x1 - self.x0) * f
        if span >= self.a.track_len:
            return self.reset_zoom()
        k = (d - self.x0) / max(self.x1 - self.x0, 1e-6)
        x0 = min(max(0.0, d - k * span), self.a.track_len - span)
        self.zoom_to(x0, x0 + span)

    def _pan_move(self, e):
        if not self._pan or not self.a:
            return
        sx, x0, x1 = self._pan
        span = x1 - x0
        dd = -(e.x - sx) / max(self.cv.winfo_width() - self.ML - self.MR, 1) * span
        n0 = min(max(0.0, x0 + dd), self.a.track_len - span)
        self.zoom_to(n0, n0 + span)


class MapView(tk.Frame):
    PAD = 22

    def __init__(self, master, on_pick=None, on_hover=None, **kw):
        super().__init__(master, background=C_CARD, **kw)
        self.on_pick = on_pick
        self.on_hover = on_hover
        self.a: Analysis | None = None
        self.selected: list[str] = []
        self.base: str | None = None      # kierowca, ktorego linia jest rysowana
        self.delta_vs = None              # gdy ustawione: kolor = zysk/strata self.base vs delta_vs
        self.rival = None                 # z kim porownujemy linie w przyblizeniu
        self.range = None
        self.hover = None
        self.cv = tk.Canvas(self, background=C_CARD, highlightthickness=0, cursor="hand2")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self.on_hover and self.on_hover(None))

    def set_data(self, a, selected, base=None, delta_vs=None, rival=None):
        self.a = a
        self.rival = rival
        self.selected = [n for n in selected if a and n in a.traces]
        if a:
            cands = [base] + self.selected + [a.reference] + list(a.traces)
            self.base = next((n for n in cands if n and n in a.traces), None)
        else:
            self.base = None
        self.delta_vs = delta_vs if (a and delta_vs in a.traces and delta_vs != self.base
                                     and a.base == delta_vs) else None
        self.redraw()

    def set_range(self, d0, d1):
        was = self._detail()
        self.range = (d0, d1)
        if was or self._detail():
            self.redraw()
        else:
            self._draw_range()

    def _detail(self):
        """Powiekszenie mapy na zaznaczony odcinek (gdy wykres jest przyblizony)."""
        if not (self.a and self.base and self.range):
            return False
        d0, d1 = self.range
        return (d0 > 0.5 or d1 < self.a.track_len - 0.5) and d1 - d0 < self.a.track_len * 0.6

    def set_hover(self, d):
        self.hover = d
        self._draw_hover()

    def _transform(self):
        tr = self.a.traces[self.base]
        w, h = self.cv.winfo_width(), self.cv.winfo_height() - 18
        xs, zs = tr["x"], tr["z"]
        minx, maxx, minz, maxz = min(xs), max(xs), min(zs), max(zs)
        s = min((w - 2 * self.PAD) / max(maxx - minx, 1), (h - 2 * self.PAD) / max(maxz - minz, 1))
        cx = w / 2 - (minx + maxx) / 2 * s
        cy = h / 2 + (minz + maxz) / 2 * s
        return lambda x: cx + x * s, lambda z: cy - z * s

    def _pt(self, name, d):
        tr = self.a.traces.get(name)
        if not tr:
            return None
        i = min(max(bisect.bisect_left(tr["d"], d), 0), len(tr["d"]) - 1)
        X, Y = self._T
        return X(tr["x"][i]), Y(tr["z"][i])

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            return
        if not self.a or not self.base:
            cv.create_text(w / 2, h / 2, text="Brak mapy.", fill=C_MUTED, font=(FONT, 11))
            return
        if self._detail():
            return self._redraw_detail(w, h)
        cv.configure(background=C_CARD)
        self._T = X, Y = self._transform()
        tr = self.a.traces[self.base]
        n = len(tr["d"])
        stride = max(1, n // 1500)
        pts = []
        for i in range(0, n, stride):
            pts += [X(tr["x"][i]), Y(tr["z"][i])]
        cv.create_line(*pts, fill="#dcdce2", width=11, capstyle="round", joinstyle="round")
        cv.create_line(*pts, fill="#f5f5f8", width=5, capstyle="round", joinstyle="round")
        if self.delta_vs:
            # zysk/strata na kazde ~20 m: zielony = szybciej, czerwony = wolniej
            dl = tr.get("dl") or []
            k = max(stride, 10)
            for i in range(0, len(dl) - k, k):
                j = i + k
                rate = dl[j] - dl[i]
                if abs(rate) < 0.002:
                    continue
                f = min(1.0, abs(rate) / 0.02)
                col = self._mix("#bdeac9", "#28b14c", f) if rate < 0 else self._mix("#ffc9c5", "#ff3b30", f)
                cv.create_line(X(tr["x"][i]), Y(tr["z"][i]), X(tr["x"][j]), Y(tr["z"][j]),
                               fill=col, width=6, capstyle="round")
            legend = (f"{short(self.base, 14)} vs {short(self.delta_vs, 14)}:  "
                      f"zielony = zysk, czerwony = strata")
        else:
            for i in range(0, n - stride, stride):
                j = i + stride
                brk, thr = tr["brk"][i], tr["thr"][i]
                if brk > 0.05:
                    col = "#ff3b30" if brk > 0.5 else "#ff9b94"
                elif thr < 0.95:
                    col = "#ff9500"
                else:
                    continue
                cv.create_line(X(tr["x"][i]), Y(tr["z"][i]), X(tr["x"][j]), Y(tr["z"][j]),
                               fill=col, width=5, capstyle="round")
            legend = f"{short(self.base, 14)}:  czerwony = hamulec, pomarańczowy = część gazu"
        sx, sy = X(tr["x"][0]), Y(tr["z"][0])
        cv.create_rectangle(sx - 4, sy - 4, sx + 4, sy + 4, fill=C_TEXT, outline=C_CARD)
        cv.create_text(sx + 8, sy, anchor="w", text="S/M", fill=C_TEXT, font=(FONT, 8, "bold"))
        for z in self.a.braking:
            rs = z.get("ref_start")
            p = self._pt(self.base, rs) if rs is not None else None
            if not p:
                continue
            x, y = p
            cv.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#3a3a3c", outline=C_CARD, width=2)
            cv.create_text(x, y, text=z.get("corner", "").lstrip("H"), fill="#ffffff",
                           font=(FONT, 7, "bold"))
        cv.create_text(8, h - 4, anchor="sw", fill=C_MUTED, font=(FONT, 8), text=legend)
        self._draw_range()
        self._draw_hover()

    # ---- widok "porownanie linii" (przyblizony zakret, ciemny styl)
    D_BG, D_ASPH, D_EDGE, D_TXT = "#141416", "#2c2c31", "#55555c", "#f2f2f7"
    C_BRK, C_COAST, C_THR, C_RIVAL = "#ff453a", "#ff9f0a", "#30d158", "#f2f2f7"

    @staticmethod
    def _phase_col(brk, thr):
        if brk > 0.08:
            return MapView.C_BRK
        if thr >= 0.9:
            return MapView.C_THR
        return MapView.C_COAST

    def _events(self, name, d0, d1):
        """Punkty kierowcy w zakresie: poczatek hamowania, szczyt (min. predkosc), pelny gaz."""
        t = self.a.traces[name]
        d, brk, thr, spd = t["d"], t["brk"], t["thr"], t["spd"]
        i0 = max(1, bisect.bisect_left(d, d0))
        i1 = min(len(d) - 1, bisect.bisect_right(d, d1))
        out = []
        i = i0
        while i < i1:
            if brk[i] > 0.1 and brk[i - 1] <= 0.1:
                b = i
                j = i
                while j < i1 and brk[j] > 0.05:
                    j += 1
                k = j
                while k < min(len(d) - 1, j + 400) and thr[k] < 0.9:
                    k += 1
                seg = range(b, max(b + 1, k))
                apex = min(seg, key=lambda q: spd[q])
                out.append({"brk": b, "apex": apex, "thr": k if thr[k] >= 0.9 else None})
                i = max(k, i + 1)
                continue
            i += 1
        return out

    def _redraw_detail(self, w, h):
        cv = self.cv
        a = self.a
        d0, d1 = self.range
        focus, rival = self.base, (self.rival if self.rival in a.traces and self.rival != self.base else None)
        tr = a.traces[focus]
        i0 = max(0, bisect.bisect_left(tr["d"], d0) - 1)
        i1 = min(len(tr["d"]), bisect.bisect_right(tr["d"], d1) + 1)
        if i1 - i0 < 3:
            return
        n = len(tr["d"])
        # krawedzie odtworzone z linii kierowcy: os = pozycja - lat * normalna
        left, right = [], []
        side = tr.get("side")
        if side and a.edges:
            for i in range(i0, i1):
                j, k = min(i + 3, n - 1), max(i - 3, 0)
                hx, hz = tr["x"][j] - tr["x"][k], tr["z"][j] - tr["z"][k]
                L = math.hypot(hx, hz) or 1.0
                rx, rz = hz / L, -hx / L
                lat = -side[i]
                cx, cz = tr["x"][i] - lat * rx, tr["z"][i] - lat * rz
                el, er = a.edge_at(tr["d"][i])
                left.append((cx - el * rx, cz - el * rz))
                right.append((cx - er * rx, cz - er * rz))
        names = [x for x in (self.selected or []) if x not in (focus, rival)]
        names += [x for x in (rival, focus) if x]
        rng = {}
        pts = left + right
        for name in names:
            t = a.traces[name]
            j0 = max(0, bisect.bisect_left(t["d"], d0) - 1)
            j1 = min(len(t["d"]), bisect.bisect_right(t["d"], d1) + 1)
            rng[name] = (j0, j1)
            pts += [(t["x"][i], t["z"][i]) for i in range(j0, j1)]
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        hh = h - 34
        pad = 34
        s = min((w - 2 * pad) / max(max(xs) - min(xs), 1), (hh - 2 * pad) / max(max(zs) - min(zs), 1))
        cx = w / 2 - (min(xs) + max(xs)) / 2 * s
        cy = hh / 2 + (min(zs) + max(zs)) / 2 * s
        X, Y = (lambda x: cx + x * s), (lambda z: cy - z * s)
        self._T = (X, Y)
        self._scale = s
        cv.configure(background=self.D_BG)
        cv.create_rectangle(0, 0, w, h, fill=self.D_BG, outline="")
        if left and right:
            poly = [c for (x, z) in left + right[::-1] for c in (X(x), Y(z))]
            cv.create_polygon(*poly, fill=self.D_ASPH, outline="", smooth=True)
            for edge in (left, right):
                cv.create_line(*[c for (x, z) in edge for c in (X(x), Y(z))],
                               fill=self.D_EDGE, width=2, smooth=True)
        else:
            t = a.traces[focus]
            j0, j1 = rng[focus]
            cv.create_line(*[c for i in range(j0, j1) for c in (X(t["x"][i]), Y(t["z"][i]))],
                           fill=self.D_ASPH, width=max(8, 12 * s), capstyle="round", joinstyle="round")
        # pozostali zaznaczeni: cienkie, przygaszone
        for name in names:
            if name in (focus, rival):
                continue
            t = a.traces[name]
            j0, j1 = rng[name]
            cv.create_line(*[c for i in range(j0, j1) for c in (X(t["x"][i]), Y(t["z"][i]))],
                           fill=self._mix(a.colors[name], self.D_BG, 0.45), width=1.5, smooth=True)
        # optimum z danych - kropkowana szara
        for s0, s1, on in a.optimal:
            if s1 < d0 or s0 > d1 or on in (focus, rival):
                continue
            t = a.traces[on]
            j0 = max(0, bisect.bisect_left(t["d"], max(s0, d0)) - 1)
            j1 = min(len(t["d"]), bisect.bisect_right(t["d"], min(s1, d1)) + 1)
            seg = [c for i in range(j0, j1) for c in (X(t["x"][i]), Y(t["z"][i]))]
            if len(seg) >= 4:
                cv.create_line(*seg, fill="#8e8e93", width=1.5, dash=(2, 4))
        # rywal: biala przerywana
        if rival:
            t = a.traces[rival]
            j0, j1 = rng[rival]
            cv.create_line(*[c for i in range(j0, j1) for c in (X(t["x"][i]), Y(t["z"][i]))],
                           fill=self.C_RIVAL, width=2, dash=(7, 5), smooth=True)
        # Ty: kolor wg fazy (hamulec / toczenie / gaz), odcinki o tym samym kolorze lacznie
        t = a.traces[focus]
        j0, j1 = rng[focus]
        run, col = [], None
        for i in range(j0, j1):
            c = self._phase_col(t["brk"][i], t["thr"][i])
            p = (X(t["x"][i]), Y(t["z"][i]))
            if c != col and run:
                run.append(p)
                if len(run) >= 2:
                    cv.create_line(*[q for xy in run for q in xy], fill=col, width=4,
                                   capstyle="round", joinstyle="round")
                run = []
            col = c
            run.append(p)
        if len(run) >= 2:
            cv.create_line(*[q for xy in run for q in xy], fill=col, width=4, capstyle="round",
                           joinstyle="round")
        # znaczniki + roznice
        ev_f = self._events(focus, d0, d1)
        ev_r = self._events(rival, d0, d1) if rival else []
        tf, tr_ = a.traces[focus], (a.traces[rival] if rival else None)
        labels = []
        for e in ev_f:
            o = min(ev_r, key=lambda r: abs(tr_["d"][r["brk"]] - tf["d"][e["brk"]]), default=None)
            if o and abs(tr_["d"][o["brk"]] - tf["d"][e["brk"]]) > 120:
                o = None
            if o:
                self._marker(tr_, o["brk"], "tri", self.C_RIVAL, X, Y)
                self._marker(tr_, o["apex"], "dot", self.C_RIVAL, X, Y)
                if o["thr"] is not None:
                    self._marker(tr_, o["thr"], "ring", self.C_RIVAL, X, Y)
            self._marker(tf, e["brk"], "tri", self.C_BRK, X, Y)
            self._marker(tf, e["apex"], "dot", self.C_COAST, X, Y)
            if e["thr"] is not None:
                self._marker(tf, e["thr"], "dot", self.C_THR, X, Y)
            if o:
                db = tf["d"][e["brk"]] - tr_["d"][o["brk"]]
                labels.append((tf, e["brk"], "hamujesz tak samo" if abs(db) < 3 else
                               f"hamujesz {abs(db):.0f} m {'później' if db > 0 else 'wcześniej'}",
                               self.C_THR if db >= 3 else (self.C_BRK if db <= -3 else self.D_TXT)))
                dv = tf["spd"][e["apex"]] - tr_["spd"][o["apex"]]
                labels.append((tf, e["apex"], f"min {tf['spd'][e['apex']]:.0f} km/h ({dv:+.0f})",
                               self.C_THR if dv > 1 else (self.C_BRK if dv < -1 else self.D_TXT)))
                if e["thr"] is not None and o["thr"] is not None:
                    dg = tf["d"][e["thr"]] - tr_["d"][o["thr"]]
                    labels.append((tf, e["thr"], "gaz tak samo" if abs(dg) < 3 else
                                   f"pełny gaz {abs(dg):.0f} m {'później' if dg > 0 else 'wcześniej'}",
                                   self.C_BRK if dg >= 3 else (self.C_THR if dg <= -3 else self.D_TXT)))
            else:
                labels.append((tf, e["apex"], f"min {tf['spd'][e['apex']]:.0f} km/h", self.D_TXT))
        self._place_labels(labels, X, Y, w, hh)
        # kierunek jazdy
        bx = [(X(t["x"][i]), Y(t["z"][i])) for i in range(j0, min(j1, j0 + 8))]
        if len(bx) > 6:
            cv.create_line(*bx[1], *bx[6], arrow="last", arrowshape=(10, 12, 4), fill=self.D_TXT,
                           width=2)
        # skala + legenda
        sx = 10 * s
        cv.create_line(14, hh - 4, 14 + sx, hh - 4, fill=self.D_TXT, width=2)
        cv.create_text(18 + sx, hh - 4, anchor="w", text="10 m", fill=self.D_TXT, font=(FONT, 8))
        lx, ly = 10, h - 12
        items = [(self.C_BRK, "hamulec"), (self.C_COAST, "toczenie / część gazu"),
                 (self.C_THR, "pełny gaz")]
        for c, txt in items:
            cv.create_line(lx, ly, lx + 16, ly, fill=c, width=4, capstyle="round")
            tid = cv.create_text(lx + 22, ly, anchor="w", text=txt, fill=self.D_TXT, font=(FONT, 8))
            lx = cv.bbox(tid)[2] + 14
        if rival:
            cv.create_line(lx, ly, lx + 22, ly, fill=self.C_RIVAL, width=2, dash=(5, 3))
            tid = cv.create_text(lx + 28, ly, anchor="w", text=short(rival, 14), fill=self.D_TXT,
                                 font=(FONT, 8))
            lx = cv.bbox(tid)[2] + 14
        cv.create_text(lx, ly, anchor="w", fill="#8e8e93", font=(FONT, 8),
                       text="▲ hamowanie  ● szczyt  ● gaz  ·  dwuklik wykresu = cały tor")
        cv.create_text(12, 10, anchor="nw", fill=self.D_TXT, font=(FONT, 10, "bold"),
                       text=f"{short(focus, 18)}" + (f"  vs  {short(rival, 18)}" if rival else ""))
        cv.create_text(12, 28, anchor="nw", fill="#8e8e93", font=(FONT, 8),
                       text=f"{d0:.0f}–{d1:.0f} m" + ("" if left else "  ·  granice toru po ponownej analizie"))
        self._draw_hover()

    def _marker(self, t, i, kind, col, X, Y):
        cv = self.cv
        x, y = X(t["x"][i]), Y(t["z"][i])
        j = min(i + 2, len(t["x"]) - 1)
        k = max(i - 2, 0)
        hx, hy = X(t["x"][j]) - X(t["x"][k]), Y(t["z"][j]) - Y(t["z"][k])
        L = math.hypot(hx, hy) or 1.0
        ux, uy = hx / L, hy / L
        if kind == "tri":       # trojkat skierowany w kierunku jazdy
            r = 7
            cv.create_polygon(x + ux * r, y + uy * r, x - ux * r * 0.6 - uy * r * 0.8,
                              y - uy * r * 0.6 + ux * r * 0.8, x - ux * r * 0.6 + uy * r * 0.8,
                              y - uy * r * 0.6 - ux * r * 0.8, fill=col, outline=self.D_BG, width=1)
        elif kind == "ring":
            cv.create_oval(x - 5, y - 5, x + 5, y + 5, outline=col, width=2)
        else:
            cv.create_oval(x - 5, y - 5, x + 5, y + 5, fill=col, outline=self.D_BG, width=1.5)

    def _place_labels(self, labels, X, Y, w, h):
        """Etykiety obok znacznikow, odsuniete na zewnatrz linii i bez nachodzenia."""
        cv = self.cv
        boxes = []
        for t, i, txt, col in labels:
            x, y = X(t["x"][i]), Y(t["z"][i])
            j, k = min(i + 3, len(t["x"]) - 1), max(i - 3, 0)
            hx, hy = X(t["x"][j]) - X(t["x"][k]), Y(t["z"][j]) - Y(t["z"][k])
            L = math.hypot(hx, hy) or 1.0
            for sgn in (1, -1):
                nx, ny = -hy / L * sgn, hx / L * sgn
                for dist in (26, 44, 62):
                    lx, ly = x + nx * dist, y + ny * dist
                    tid = cv.create_text(lx, ly, text=txt, fill=col, font=(FONT, 8, "bold"))
                    b = cv.bbox(tid)
                    b = (b[0] - 6, b[1] - 3, b[2] + 6, b[3] + 3)
                    ok = (b[0] > 2 and b[2] < w - 2 and b[1] > 40 and b[3] < h
                          and not any(b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1]
                                      for o in boxes))
                    if ok:
                        break
                    cv.delete(tid)
                    tid = None
                if tid:
                    break
            if not tid:
                continue
            boxes.append(b)
            cv.create_line(x, y, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, fill="#48484a")
            r = round_rect(cv, *b, 6, fill="#1f1f23", outline="#3a3a3f")
            cv.tag_raise(tid, r)

    def _car(self, name, d, col):
        """Auto (prostokat ~4.7 x 2 m) ustawione wzdluz kierunku jazdy."""
        t = self.a.traces.get(name)
        if not t:
            return
        X, Y = self._T
        i = min(max(bisect.bisect_left(t["d"], d), 0), len(t["d"]) - 1)
        j, k = min(i + 2, len(t["d"]) - 1), max(i - 2, 0)
        x, y = X(t["x"][i]), Y(t["z"][i])
        hx, hy = X(t["x"][j]) - X(t["x"][k]), Y(t["z"][j]) - Y(t["z"][k])
        L = math.hypot(hx, hy) or 1.0
        ux, uy = hx / L, hy / L
        s = getattr(self, "_scale", 2.0)
        hl, hw = max(9.0, 2.35 * s), max(4.5, 1.0 * s)
        pts = [(x + ux * hl - uy * hw, y + uy * hl + ux * hw), (x + ux * hl + uy * hw, y + uy * hl - ux * hw),
               (x - ux * hl + uy * hw, y - uy * hl - ux * hw), (x - ux * hl - uy * hw, y - uy * hl + ux * hw)]
        self.cv.create_polygon(*[c for p in pts for c in p], fill=col, outline="#000000", width=1,
                               tags="hover")
        # kabina / przod
        self.cv.create_line(x + ux * hl * 0.2 - uy * hw * 0.6, y + uy * hl * 0.2 + ux * hw * 0.6,
                            x + ux * hl * 0.2 + uy * hw * 0.6, y + uy * hl * 0.2 - ux * hw * 0.6,
                            fill="#000000", width=2, tags="hover")

    @staticmethod
    def _mix(c1, c2, f):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))

    def _draw_range(self):
        cv = self.cv
        cv.delete("range")
        if not self.a or not self.base or not self.range or not hasattr(self, "_T"):
            return
        d0, d1 = self.range
        if d0 <= 0.5 and d1 >= self.a.track_len - 0.5:
            return
        tr = self.a.traces[self.base]
        i0 = bisect.bisect_left(tr["d"], d0)
        i1 = bisect.bisect_right(tr["d"], d1)
        X, Y = self._T
        pts = []
        for i in range(i0, i1, max(1, (i1 - i0) // 400)):
            pts += [X(tr["x"][i]), Y(tr["z"][i])]
        if len(pts) >= 4:
            cv.create_line(*pts, fill="#9ccaff", width=15, capstyle="round",
                           joinstyle="round", tags="range")
            cv.tag_lower("range")

    def _draw_hover(self):
        cv = self.cv
        cv.delete("hover")
        if self.hover is None or not self.a or not hasattr(self, "_T"):
            return
        if self._detail():
            rival = self.rival if self.rival in self.a.traces and self.rival != self.base else None
            if rival:
                self._car(rival, self.hover, "#e5e5ea")
            self._car(self.base, self.hover, self.a.colors[self.base])
            return
        for name in reversed(self.selected):
            p = self._pt(name, self.hover)
            if p:
                x, y = p
                cv.create_oval(x - 5, y - 5, x + 5, y + 5, fill=self.a.colors[name],
                               outline=C_CARD, width=2, tags="hover")

    def _nearest(self, ex, ey):
        if not self.a or not self.base or not hasattr(self, "_T"):
            return None
        tr = self.a.traces[self.base]
        X, Y = self._T
        best, bi = 1e18, None
        for i in range(0, len(tr["d"]), 2):
            dx, dy = X(tr["x"][i]) - ex, Y(tr["z"][i]) - ey
            q = dx * dx + dy * dy
            if q < best:
                best, bi = q, i
        if bi is None or best > 30 * 30:
            return None
        return tr["d"][bi]

    def _click(self, e):
        d = self._nearest(e.x, e.y)
        if d is not None and self.on_pick:
            self.on_pick(d)

    def _motion(self, e):
        if self.on_hover:
            self.on_hover(self._nearest(e.x, e.y))
