"""LMU Telemetry — lokalna aplikacja offline, wszystko w jednym oknie.

  * pasek nagrywania (gora)  — start/stop (F9), licznik, stan na zywo, auto-analiza po stopie,
  * biblioteka (lewa strona) — tory i ich sesje; wiersz toru = analiza ze wszystkich sesji,
  * podglad (prawa strona)   — wyniki, wykresy predkosc/gaz/hamulec + mapa toru,
                               strefy hamowania i brief AI — bez otwierania przegladarki.

Ustawienia (kierowca, filtr, opcje) sa zapamietywane w lmu_app_settings.json.

Uruchomienie: START_LMU_TELEMETRY.cmd albo pythonw lmu_app.py
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import lmu_classes
import lmu_gamerec
import lmu_coach
import lmu_results
import lmu_engineer
import lmu_fuel
import lmu_handling
import lmu_setup
import lmu_theme
from lmu_view import FONT as FONT_UI, Analysis, ChartView, MapView

BASE = Path(__file__).resolve().parent
RECORDS = BASE / "records"
ANALYSIS = BASE / "analysis"
TINYPEDAL = BASE / "tinypedal_laps"
STOP_FILE = RECORDS / ".stop"
SETTINGS = BASE / "lmu_app_settings.json"
MIN_RECORD_BYTES = 200_000
SESSION_NAMES = {
    0: "testday", 1: "practice", 2: "practice", 3: "practice", 4: "practice",
    5: "quali", 6: "quali", 7: "quali", 8: "quali", 9: "warmup",
    10: "race", 11: "race", 12: "race", 13: "race",
}
FILTERS = [
    ("szybsi lub szybszy sektor", "faster_or_sector"),
    ("tylko szybsi", "faster"),
    ("wszyscy", "all"),
]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

# status z record.py: "[REC] tor | sesja | auta=N | best=97.707 (kierowca) | 123 wierszy | 1.2 MB"
REC_STATUS_RE = re.compile(
    r"\[REC\] (?P<track>.+?) \| (?P<session>.+?) \| auta=(?P<cars>\d+) \| "
    r"best=(?P<best>\S+)(?: \((?P<who>.*?)\))? \| (?P<rows>\d+) wierszy \| "
    r"(?P<mb>[\d.]+) MB")

C_BG = lmu_theme.BG
C_CARD = lmu_theme.CARD
C_TEXT = lmu_theme.TEXT
C_MUTED = lmu_theme.MUTED
C_LINE = lmu_theme.LINE
C_ACCENT = lmu_theme.ACCENT
C_OK = "#248a3d"
C_WARN = "#c93400"
C_ERR = "#d70015"
C_REC = lmu_theme.RED
C_ME = "#fff5d6"
C_GROUP = "#f7f7fa"
FONT = FONT_UI


def meta_for(csv_path: Path) -> dict:
    mp = csv_path.with_name(csv_path.name[:-7] + ".metadata.json")
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fmt_lap(t):
    if not t:
        return "—"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def fmt_gap(g):
    if g is None:
        return ""
    return "—" if abs(g) < 1e-6 else f"+{g:.3f}"


def fmt_m(v):
    return "" if v is None else f"{v:+.0f} m"


def fmt_dur(sec):
    sec = int(sec)
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def same_driver(a, b):
    if not a or not b:
        return False
    a, b = a.lower().split("#")[0].strip(), b.lower().split("#")[0].strip()
    return a == b or a in b or b in a


def track_slug(track):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", track).strip("_")[:80] or "track"


def short_name(n, k=22):
    n = (n or "").split("#")[0]
    return n if len(n) <= k else n[:k - 1] + "…"


class LmuApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.engineer = lmu_engineer.Engineer()
        self.voice = lmu_engineer.Voice()
        self.vr_win = None
        self.root.title("LMU Telemetry")
        self.root.geometry("1440x900")
        self.root.minsize(1100, 700)
        self.q: queue.Queue = queue.Queue()
        self.sessions = {}        # stem -> info
        self.tracks = {}          # track -> info
        self.current = None       # iid w bibliotece
        self.view = None          # Analysis albo None
        self.view_sel = {}        # outdir -> lista zaznaczonych kierowcow
        self.compare = {}         # outdir -> (porownywany, wzgledem kogo)
        self.rec_proc = None
        self.rec_started = None
        self.rec_error = None
        self.last_saved = None
        self.busy = None          # opis trwajacej operacji albo None
        self.batch = []           # kolejka "analizuj nowe"
        self.settings = read_json(SETTINGS)
        RECORDS.mkdir(exist_ok=True)
        ANALYSIS.mkdir(exist_ok=True)
        self._style()
        self._build()
        self._bind_keys()
        self.refresh_library(select=self.settings.get("selected"))
        self.root.after(100, self._poll_queue)
        # indeks zapisanych setupow (1000+ plikow) budujemy w tle, zeby zakladka Setup byla od razu
        threading.Thread(target=lmu_setup.library, daemon=True).start()
        self.root.after(1000, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ================================================================ wyglad
    def _style(self):
        lmu_theme.apply(self.root, FONT)

    def _card(self, parent, padding=12):
        return ttk.Frame(parent, style="Card.TFrame", padding=padding)

    def _build(self):
        # --- naglowek
        header = self.header = tk.Frame(self.root, background=C_BG)
        header.pack(fill="x")
        tk.Label(header, text="LMU Telemetry", background=C_BG, foreground=C_TEXT,
                 font=(FONT, 22, "bold")).pack(side="left", padx=(18, 12), pady=(12, 2))
        tk.Label(header, text="nagrywanie · analiza · porównanie z rywalami — offline",
                 background=C_BG, foreground=C_MUTED, font=(FONT, 10)).pack(side="left", pady=(14, 0))
        self.pill = ttk.Label(header, text="● Gotowe", style="PillOk.TLabel")
        self.pill.pack(side="right", padx=18, pady=(12, 0))
        self.header_line = tk.Frame(self.root, height=0, background=C_BG)
        self.header_line.pack(fill="x")

        # --- stopka (status + log)
        foot = ttk.Frame(self.root, padding=(14, 3, 14, 5))
        foot.pack(side="bottom", fill="x")
        self.status = ttk.Label(foot, text="Gotowe.", style="Muted.TLabel")
        self.status.pack(side="left")
        self.btn_log = ttk.Button(foot, text="Pokaż log ▾", style="Link.TButton",
                                  command=self.toggle_log)
        self.btn_log.pack(side="right")
        ttk.Label(foot, text="F9 nagrywanie · F5 odśwież · Ctrl+L log",
                  style="Muted.TLabel").pack(side="right", padx=(0, 14))
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=150)

        self.logf = ttk.Frame(self.root, padding=(14, 0, 14, 0))
        self.log_text = tk.Text(self.logf, height=9, wrap="word", font=("Consolas", 9),
                                background=C_CARD, foreground=C_TEXT, relief="flat",
                                borderwidth=0, highlightthickness=0, padx=8, pady=6)
        lsb = ttk.Scrollbar(self.logf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log_text.tag_configure("err", foreground=C_ERR)
        self.log_text.tag_configure("ok", foreground=C_OK)
        self.log_text.configure(state="disabled")
        self.log_visible = False
        if self.settings.get("log_visible"):
            self.toggle_log(save=False)

        # --- pasek nagrywania
        recwrap = self.recwrap = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        recwrap.pack(fill="x")
        self._build_rec_bar(recwrap)

        # --- biblioteka | podglad
        body = self.body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14, pady=10)
        left = self.left_pane = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=3)
        self._build_library(left)
        self._build_viewer(right)

    # ================================================================ pasek nagrywania
    def _build_rec_bar(self, parent):
        card = self._card(parent, padding=(12, 10))
        card.pack(fill="x")
        card.columnconfigure(2, weight=1)
        self.btn_rec = ttk.Button(card, text="●  Nagrywaj", style="Rec.TButton",
                                  command=self.toggle_recording)
        self.btn_rec.grid(row=0, column=0, rowspan=2, sticky="w")
        self.rec_timer = tk.Label(card, text="0:00:00", background=C_CARD, foreground=C_MUTED,
                                  font=("Consolas", 20, "bold"))
        self.rec_timer.grid(row=0, column=1, rowspan=2, padx=(16, 16))
        self.rec_state = tk.Label(card, text="Nie nagrywa", background=C_CARD,
                                  foreground=C_TEXT, font=(FONT, 11, "bold"), anchor="w")
        self.rec_state.grid(row=0, column=2, sticky="w")
        self.rec_hint = tk.Label(card, background=C_CARD, foreground=C_MUTED, anchor="w",
                                 font=(FONT, 9), justify="left",
                                 text="Uruchom LMU, wjedź na tor albo odtwórz replay i kliknij "
                                      "Nagrywaj. Zapisują się wszystkie auta.")
        self.rec_hint.grid(row=1, column=2, sticky="w")
        tools = ttk.Frame(card, style="White.TFrame")
        tools.grid(row=0, column=3, rowspan=2, sticky="e")
        self.auto_analyze_var = tk.BooleanVar(value=self.settings.get("auto_analyze", True))
        ttk.Checkbutton(tools, text="po stopie analizuj i pokaż", style="Card.TCheckbutton",
                        variable=self.auto_analyze_var, command=self.save_settings).pack(
            side="left", padx=(0, 12))
        self.voice_on = tk.BooleanVar(value=self.settings.get("voice_on", False))
        ttk.Checkbutton(tools, text="inżynier głosowy", style="Card.TCheckbutton",
                        variable=self.voice_on, command=self._toggle_voice).pack(side="left",
                                                                                 padx=(0, 8))
        ttk.Button(tools, text="Panel VR", command=self.open_vr_panel).pack(side="left",
                                                                         padx=(0, 12))
        ttk.Label(tools, text="Hz:", style="WMuted.TLabel").pack(side="left")
        self.hz_var = tk.StringVar(value=str(self.settings.get("hz", "30")))
        ttk.Combobox(tools, textvariable=self.hz_var, width=4, state="readonly",
                     values=["30", "60", "100"]).pack(side="left", padx=(4, 12))
        self.btn_probe = ttk.Button(tools, text="Test połączenia", command=self.run_probe)
        self.btn_probe.pack(side="left")
        ttk.Button(tools, text="Folder nagrań",
                   command=lambda: self._open_path(RECORDS)).pack(side="left", padx=(8, 0))

    # ================================================================ biblioteka
    def _build_library(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var)
        ent.pack(side="left", fill="x", expand=True)
        self._placeholder(ent, "Szukaj toru lub kierowcy…")
        self.search_var.trace_add("write", lambda *a: self._fill_library())
        self.sess_filter = tk.StringVar(value="wszystkie")
        cb = ttk.Combobox(bar, textvariable=self.sess_filter, width=10, state="readonly",
                          values=["wszystkie", "practice", "quali", "race", "warmup", "testday"])
        cb.pack(side="left", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_library())

        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        self.lib = ttk.Treeview(box, columns=("best", "mine", "rep"), show="tree headings",
                                selectmode="browse")
        self.lib.heading("#0", text="Tor / sesja")
        self.lib.heading("best", text="Najlepszy")
        self.lib.heading("mine", text="Mój")
        self.lib.heading("rep", text="✓")
        self.lib.column("#0", width=190, stretch=True)
        self.lib.column("best", width=74, anchor="center", stretch=False)
        self.lib.column("mine", width=90, anchor="center", stretch=False)
        self.lib.column("rep", width=28, anchor="center", stretch=False)
        self.lib.tag_configure("track", font=(FONT, 10, "bold"), background=C_GROUP)
        self.lib.tag_configure("demo", foreground=C_MUTED)
        self.lib.tag_configure("new", foreground=C_ACCENT)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.lib.yview)
        self.lib.configure(yscrollcommand=sb.set)
        self.lib.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lib.bind("<<TreeviewSelect>>", lambda e: self._on_lib_select())
        self.lib.bind("<Double-1>", lambda e: self._lib_double())
        self.lib.bind("<Return>", lambda e: self._lib_double())
        self.lib.bind("<Button-3>", self._lib_menu)
        self.lib_empty = tk.Label(box, background=C_CARD, foreground=C_MUTED, justify="center",
                                  font=(FONT, 10), wraplength=280)

        bot = ttk.Frame(f)
        bot.pack(fill="x", pady=(6, 0))
        self.btn_batch = ttk.Button(bot, text="Analizuj nowe", command=self.analyze_all_missing)
        self.btn_batch.pack(side="left")
        self.btn_import = ttk.Button(bot, text="Import z gry", command=self.import_game)
        self.btn_import.pack(side="left", padx=(6, 0))
        ttk.Button(bot, text="⟳ Odśwież", command=self.refresh_library).pack(side="right")
        self.lib_count = ttk.Label(f, text="", style="Muted.TLabel", wraplength=380)
        self.lib_count.pack(fill="x", pady=(4, 0))

        self.lib_menu = tk.Menu(self.root, tearoff=0)
        self.lib_menu.add_command(label="Analizuj", command=self.analyze_current)
        self.lib_menu.add_command(label="Otwórz raport HTML", command=self.open_in_browser)
        self.lib_menu.add_command(label="Eksport do TinyPedal", command=self.export_tinypedal)
        self.lib_menu.add_separator()
        self.lib_menu.add_command(label="Pokaż plik nagrania", command=self.reveal_record)
        self.lib_menu.add_command(label="Folder wyników", command=self.open_result_dir)

    def _placeholder(self, entry, text):
        var = self.search_var
        self._ph_active = False

        def show(_=None):
            if not var.get():
                self._ph_active = True
                entry.configure(foreground=C_MUTED)
                var.set(text)

        def hide(_=None):
            if self._ph_active:
                self._ph_active = False
                var.set("")
                entry.configure(foreground=C_TEXT)

        entry.bind("<FocusIn>", hide)
        entry.bind("<FocusOut>", show)
        show()

    def _query(self):
        if self._ph_active:
            return ""
        return self.search_var.get().strip().lower()

    # ================================================================ podglad
    def _build_viewer(self, f):
        top = self.v_top = self._card(f, padding=(14, 10))
        top.pack(fill="x")
        self.v_title = tk.Label(top, text="Wybierz sesję albo tor z listy", background=C_CARD,
                                foreground=C_TEXT, font=(FONT, 14, "bold"), anchor="w")
        self.v_title.pack(fill="x")
        self.v_sub = tk.Label(top, text="", background=C_CARD, foreground=C_MUTED, anchor="w",
                              font=(FONT, 9), justify="left")
        self.v_sub.pack(fill="x")

        # opcje analizy toru (widoczne tylko dla wiersza toru)
        self.track_opts = ttk.Frame(top, style="White.TFrame")

        def lbl(t):
            ttk.Label(self.track_opts, text=t, style="WMuted.TLabel").pack(side="left", padx=(0, 4))

        lbl("Ja:")
        self.player_var = tk.StringVar(value=self.settings.get("player", ""))
        self.player_combo = ttk.Combobox(self.track_opts, textvariable=self.player_var, width=22)
        self.player_combo.pack(side="left", padx=(0, 12))
        lbl("moje okrążenie:")
        self.player_lap_var = tk.StringVar(value=str(self.settings.get("player_lap", 1)))
        ttk.Combobox(self.track_opts, textvariable=self.player_lap_var, width=3, state="readonly",
                     values=["1", "2", "3"]).pack(side="left", padx=(0, 12))
        lbl("rywale:")
        self.filter_var = tk.StringVar(value=self.settings.get("filter", FILTERS[0][0]))
        if self.filter_var.get() not in [x[0] for x in FILTERS]:
            self.filter_var.set(FILTERS[0][0])
        ttk.Combobox(self.track_opts, textvariable=self.filter_var, width=22, state="readonly",
                     values=[x[0] for x in FILTERS]).pack(side="left", padx=(0, 12))
        lbl("max:")
        self.top_var = tk.StringVar(value=str(self.settings.get("top", 8)))
        ttk.Combobox(self.track_opts, textvariable=self.top_var, width=3, state="readonly",
                     values=[str(i) for i in range(2, 9)]).pack(side="left")

        acts = ttk.Frame(top, style="White.TFrame")
        acts.pack(fill="x", pady=(8, 0))
        self.acts = acts
        self.btn_analyze = ttk.Button(acts, text="Analizuj", style="Accent.TButton",
                                      command=self.analyze_current, state="disabled")
        self.btn_analyze.pack(side="left")
        self.btn_browser = ttk.Button(acts, text="Raport HTML", command=self.open_in_browser,
                                      state="disabled")
        self.btn_browser.pack(side="left", padx=(8, 0))
        self.btn_tp = ttk.Button(acts, text="Eksport do TinyPedal", command=self.export_tinypedal,
                                 state="disabled")
        self.btn_tp.pack(side="left", padx=(8, 0))
        self.btn_dir = ttk.Button(acts, text="Folder", command=self.open_result_dir,
                                  state="disabled")
        self.btn_dir.pack(side="left", padx=(8, 0))
        self.cls_mode = tk.StringVar(value=self.settings.get("cls_mode", "moja klasa"))
        cb = ttk.Combobox(acts, textvariable=self.cls_mode, width=14, state="readonly",
                          values=["moja klasa", "wszystkie klasy"])
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_settings())
        ttk.Label(acts, text="Analiza porównuje:", style="WMuted.TLabel").pack(side="right",
                                                                              padx=(0, 6))

        body = self.v_body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.v_empty = ttk.Frame(body, style="Card.TFrame")
        self.v_empty_msg = tk.Label(self.v_empty, background=C_CARD, foreground=C_MUTED,
                                    font=(FONT, 12), justify="center", wraplength=560)
        self.v_empty_msg.place(relx=0.5, rely=0.42, anchor="center")
        self.v_empty_btn = ttk.Button(self.v_empty, text="Analizuj", style="Accent.TButton",
                                      command=self.analyze_current)

        self.vnb = ttk.Notebook(body)
        self._build_tab_results()
        self._build_tab_charts()
        self._build_tab_braking()
        self._build_tab_brief()
        self._build_tab_classes()
        self._build_tab_setup()
        self._build_tab_handling()
        self._build_tab_fuel()
        self._build_tab_coach()
        self.vnb.bind("<<NotebookTabChanged>>", lambda e: self._on_view_tab())
        self._show_empty("Wybierz sesję albo tor z listy po lewej.")

    def _build_tab_results(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Wyniki  ")
        cols = ("show", "pos", "driver", "lap", "gap", "brake", "pickup", "rate", "vehicle")
        self.board = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, (text, width, anchor) in {
            "show": ("Wykres", 64, "center"), "pos": ("#", 34, "center"),
            "driver": ("Kierowca", 230, "w"), "lap": ("Czas", 90, "center"),
            "gap": ("Strata", 75, "center"), "brake": ("Δ hamowania", 100, "center"),
            "pickup": ("Δ gazu", 85, "center"), "rate": ("Dane", 70, "center"),
            "vehicle": ("Auto", 220, "w"),
        }.items():
            self.board.heading(c, text=text)
            self.board.column(c, width=width, anchor=anchor, stretch=c == "vehicle")
        self.board.tag_configure("me", background=C_ME)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.board.yview)
        self.board.configure(yscrollcommand=sb.set)
        ttk.Label(f, style="Muted.TLabel", wraplength=900, justify="left",
                  text="Kliknij ☐/☑ w kolumnie „Wykres”, aby dodać/usunąć kierowcę z wykresów "
                       "i mapy. Dwuklik = przejdź do wykresów.  ★ = referencja.  Δ hamowania / "
                       "Δ gazu: średnio w metrach względem referencji (ujemne = wcześniej). "
                       "„Dane” = ile próbek/s ma okrążenie: przy ~5 Hz różnice < 10–15 m są przybliżone.").pack(
            side="bottom", fill="x", pady=(6, 0))
        self.board.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.board.bind("<Button-1>", self._board_click)
        self.board.bind("<Double-1>", self._board_double)
        self.board.bind("<<TreeviewSelect>>", lambda e: self._update_actions())

    def _build_tab_charts(self):
        f = ttk.Frame(self.vnb, padding=(8, 6, 8, 8))
        self.vnb.add(f, text="  Wykresy i mapa  ")
        cmp = ttk.Frame(f)
        cmp.pack(fill="x", pady=(0, 6))
        ttk.Label(cmp, text="Porównaj:", font=(FONT, 10, "bold")).pack(side="left")
        self.focus_var = tk.StringVar()
        self.focus_combo = ttk.Combobox(cmp, textvariable=self.focus_var, width=24,
                                        state="readonly")
        self.focus_combo.pack(side="left", padx=(6, 6))
        ttk.Label(cmp, text="vs").pack(side="left")
        self.base_var = tk.StringVar()
        self.base_combo = ttk.Combobox(cmp, textvariable=self.base_var, width=24,
                                       state="readonly")
        self.base_combo.pack(side="left", padx=(6, 6))
        ttk.Button(cmp, text="↔ zamień", command=self._swap_compare).pack(side="left")
        for cb in (self.focus_combo, self.base_combo):
            cb.bind("<<ComboboxSelected>>", lambda e: self._on_compare())
        self.btn_full = ttk.Button(cmp, text="⛶ Pełny ekran", command=self.toggle_fullscreen)
        self.btn_full.pack(side="right")
        ttk.Button(cmp, text="Cały tor", command=lambda: self.chart.reset_zoom()).pack(
            side="right", padx=(0, 8))
        self.map_delta_var = tk.BooleanVar(value=self.settings.get("map_delta", True))
        ttk.Checkbutton(cmp, text="mapa: zysk / strata", variable=self.map_delta_var,
                        command=self._on_compare).pack(side="right", padx=(0, 12))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        self.legend = tk.Frame(bar, background=C_BG)
        self.legend.pack(side="left", fill="x", expand=True)
        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True)
        cbox = ttk.Frame(pane, style="Card.TFrame", padding=(8, 8, 8, 6))
        mbox = ttk.Frame(pane, style="Card.TFrame", padding=8)
        pane.add(cbox, weight=3)
        pane.add(mbox, weight=2)
        self.chart = ChartView(cbox, on_hover=self._chart_hover, on_range=self._chart_range,
                               on_option=self.save_settings,
                               seg_mode=self.settings.get("seg_mode", "hamowania"),
                               seg_sort=self.settings.get("seg_sort", "po kolei"))
        self.chart.pack(fill="both", expand=True, pady=(4, 0))
        self.map = MapView(mbox, on_pick=self._map_pick, on_hover=self._map_hover)
        self.map.pack(fill="both", expand=True)

    def _build_tab_braking(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Strefy hamowania  ")
        cols = ("start", "dstart", "end", "pickup", "dpickup", "vmin", "maxb")
        self.brk = ttk.Treeview(f, columns=cols, show="tree headings")
        self.brk.heading("#0", text="Zakręt / kierowca")
        self.brk.column("#0", width=230)
        for c, (text, width) in {
            "start": ("Start ham. [m]", 105), "dstart": ("Δ start", 80),
            "end": ("Koniec [m]", 90), "pickup": ("Gaz [m]", 80), "dpickup": ("Δ gaz", 80),
            "vmin": ("V min [km/h]", 95), "maxb": ("Max ham.", 80),
        }.items():
            self.brk.heading(c, text=text)
            self.brk.column(c, width=width, anchor="center", stretch=False)
        self.brk.tag_configure("corner", font=(FONT, 10, "bold"), background=C_GROUP)
        self.brk.tag_configure("me", background=C_ME)
        self.brk.tag_configure("miss", foreground=C_MUTED)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.brk.yview)
        self.brk.configure(yscrollcommand=sb.set)
        ttk.Label(f, style="Muted.TLabel",
                  text="Pokazani są kierowcy zaznaczeni w Wynikach.  Δ ujemna = wcześniej niż "
                       "referencja.  Dwuklik na zakręcie = przybliż go na wykresie.").pack(
            side="bottom", fill="x", pady=(6, 0))
        self.brk.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.brk.bind("<Double-1>", self._brk_double)

    def _build_tab_brief(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Brief AI  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, style="Muted.TLabel",
                  text="Gotowe tabele + prompt. Skopiuj i wklej do Claude / ChatGPT — "
                       "nic nie jest wysyłane automatycznie.").pack(side="left")
        self.btn_copy = ttk.Button(bar, text="Kopiuj do schowka", style="Accent.TButton",
                                   command=self.copy_brief)
        self.btn_copy.pack(side="right")
        ttk.Button(bar, text="Zapisz jako…", command=self.save_brief).pack(side="right", padx=(0, 8))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        self.brief = tk.Text(box, wrap="word", font=("Consolas", 10), background=C_CARD,
                             foreground=C_TEXT, relief="flat", borderwidth=0, padx=14, pady=10,
                             highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.brief.yview)
        self.brief.configure(yscrollcommand=sb.set)
        self.brief.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.brief_text = ""
        self.brief_for = None

    # ================================================================ plan treningu
    def _build_tab_coach(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Plan treningu  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Porównaj z:", style="Muted.TLabel").pack(side="left")
        self.co_rival = tk.StringVar(value="najszybszy na każdym odcinku")
        self.co_rival_cb = ttk.Combobox(bar, textvariable=self.co_rival, width=30, state="readonly")
        self.co_rival_cb.pack(side="left", padx=(6, 14))
        self.co_rival_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_coach())
        ttk.Label(bar, text="Pokaż:", style="Muted.TLabel").pack(side="left")
        self.co_top = tk.StringVar(value=self.settings.get("co_top", "3 zakręty"))
        cb = ttk.Combobox(bar, textvariable=self.co_top, width=12, state="readonly",
                          values=["3 zakręty", "5 zakrętów", "wszystkie"])
        cb.pack(side="left", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: (self._fill_coach(), self.save_settings()))
        box = self._card(f, padding=(4, 4))
        box.pack(fill="both", expand=True)
        self.co_text = tk.Text(box, wrap="word", background=C_CARD, foreground=C_TEXT,
                               relief="flat", borderwidth=0, highlightthickness=0, padx=18, pady=14,
                               font=(FONT, 11), cursor="arrow", spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.co_text.yview)
        self.co_text.configure(yscrollcommand=sb.set)
        self.co_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        T = self.co_text
        T.tag_configure("h1", font=(FONT, 17, "bold"), spacing3=4)
        T.tag_configure("sum", font=(FONT, 11), foreground=C_MUTED, spacing3=10)
        T.tag_configure("corner", font=(FONT, 14, "bold"), spacing1=14)
        T.tag_configure("loss", font=("Consolas", 14, "bold"), foreground=C_ERR)
        T.tag_configure("gain", font=("Consolas", 12, "bold"), foreground=C_OK)
        T.tag_configure("split", font=(FONT, 10), foreground=C_MUTED)
        T.tag_configure("reason", font=(FONT, 11, "bold"), lmargin1=26, lmargin2=40, spacing1=6)
        T.tag_configure("tip", font=(FONT, 11), foreground=C_ACCENT, lmargin1=40, lmargin2=40)
        T.tag_configure("note", font=(FONT, 9), foreground=C_MUTED, spacing1=14)
        T.tag_configure("h2", font=(FONT, 13, "bold"), spacing1=18)

    def _load_coach(self):
        a, info = self.view, self._info()
        if not a or not info:
            return
        me = next((n for n in a.drivers if same_driver(n, self._player_for(info))), None)
        vals = ["najszybszy na każdym odcinku"] + [n for n in a.drivers if n != me]
        self.co_rival_cb["values"] = vals
        if self.co_rival.get() not in vals:
            self.co_rival.set(vals[0])
        self._fill_coach()

    def _fill_coach(self):
        a, info = self.view, self._info()
        T = self.co_text
        T.configure(state="normal")
        T.delete("1.0", "end")
        for w in T.winfo_children():
            w.destroy()
        if not a or not info:
            T.configure(state="disabled")
            return
        me = next((n for n in a.drivers if same_driver(n, self._player_for(info))), None)
        if not me:
            T.insert("end", "Nie ma Cię w tej analizie.\n", "h1")
            T.insert("end", "Ustaw swojego kierowcę („Ja”) albo przeanalizuj sesję, w której jechałeś.",
                     "sum")
            T.configure(state="disabled")
            return
        rv = self.co_rival.get()
        rival = None if rv == "najszybszy na każdym odcinku" else rv
        res = lmu_coach.plan(a, me, rival)
        losses = [c for c in res if c["loss"] > 0.01]
        gains = sorted([c for c in res if c["loss"] < -0.01], key=lambda c: c["loss"])
        n = {"3 zakręty": 3, "5 zakrętów": 5}.get(self.co_top.get(), len(losses))
        top = losses[:n]
        top_sum = sum(c["loss"] for c in top)
        who = short_name(rival, 24) if rival else "najszybszych na każdym odcinku"
        T.insert("end", f"Plan treningu — {short_name(me, 24)} vs {who}\n", "h1")
        if top:
            word = "zakręt" if len(top) == 1 else ("zakręty" if len(top) < 5 else "zakrętów")
            T.insert("end", f"Na okrążeniu tracisz łącznie {sum(c['loss'] for c in losses):.2f} s. "
                            f"{len(top)} {word} poniżej to {top_sum:.2f} s — od nich zacznij.\n", "sum")
        else:
            T.insert("end", "Nigdzie nie tracisz wyraźnie — świetnie!\n", "sum")
        for k, c in enumerate(top, 1):
            T.insert("end", f"{k}. {c['name']}   ", "corner")
            T.insert("end", f"{c['loss']:+.3f} s", "loss")
            parts = []
            if c["entry"] is not None:
                parts.append(f"wejście {c['entry']:+.2f}")
            if c["exit"] is not None:
                parts.append(f"wyjście {c['exit']:+.2f}")
            T.insert("end", f"    {' · '.join(parts)}"
                            + (f"  ·  vs {short_name(c['rival'], 18)}" if not rival else "")
                            + f"  ·  {c['d0']:.0f}–{c['d1']:.0f} m   ", "split")
            btn = ttk.Button(T, text="pokaż na wykresie", style="Link.TButton",
                             command=lambda c=c: self._coach_show(c))
            T.window_create("end", window=btn)
            T.insert("end", "\n")
            if not c["reasons"]:
                T.insert("end", "• różnica rozłożona równo — bez jednej wyraźnej przyczyny\n", "reason")
            for _, text, tip in c["reasons"][:3]:
                T.insert("end", f"• {text}\n", "reason")
                T.insert("end", f"→ {tip}\n", "tip")
        if gains:
            T.insert("end", "Gdzie jesteś szybszy (tak trzymaj)\n", "h2")
            for c in gains[:6]:
                T.insert("end", f"{c['name']}  ", "split")
                T.insert("end", f"{c['loss']:+.3f} s", "gain")
                T.insert("end", (f"  vs {short_name(c['rival'], 18)}" if not rival else "") + "     ",
                         "split")
            T.insert("end", "\n")
        rivals = [rival] if rival else [c["rival"] for c in top]
        low = any(a.rate.get(r, 99) < 15 for r in rivals)
        T.insert("end", ("Uwaga: dane rywali mają ~5 Hz — punkty hamowania i gazu ±10 m, linia "
                         "przybliżona. " if low else "")
                 + "Wskazówki są orientacyjne: zmieniaj jedną rzecz naraz i sprawdzaj w kolejnej "
                   "sesji.\n", "note")
        T.configure(state="disabled")

    def _coach_show(self, c):
        info = self._info()
        me = next((n for n in self.view.drivers if same_driver(n, self._player_for(info))), None)
        if me:
            self.focus_var.set(me)
        self.base_var.set(c["rival"])
        self._on_compare()
        self.vnb.select(1)
        self.root.update_idletasks()
        self.chart.zoom_to(c["d0"] - 20, c["d1"] + 20)

    # ================================================================ VR: inzynier glosowy + panel
    def _on_lap_event(self, line):
        try:
            ev = json.loads(line.split("[LAP]", 1)[1])
        except (ValueError, IndexError):
            return
        lang = self.voice.lang if self.voice_on.get() else "pl"
        if self.voice_on.get():
            lang = self.voice.ensure()
        msg = self.engineer.feed(ev, lang)
        self.log(f"inżynier: {msg}")
        if self.voice_on.get() and msg:
            self.voice.say(msg)
        self._vr_update(msg if lang == "pl" else self.engineer.message("pl"))

    def _toggle_voice(self):
        self.save_settings()
        if self.voice_on.get():
            def hello():
                lang = self.voice.ensure()
                self.voice.say("Inżynier gotowy." if lang == "pl" else "Engineer ready.")
                if lang != "pl":
                    self.q.put(("log", f"głos: {self.voice.voice} (brak polskiego głosu — dodaj "
                                       "go w Ustawienia Windows → Czas i język → Mowa)"))
            threading.Thread(target=hello, daemon=True).start()
        else:
            self.voice.stop()

    def open_vr_panel(self):
        if self.vr_win and self.vr_win.winfo_exists():
            self.vr_win.lift()
            return
        w = self.vr_win = tk.Toplevel(self.root)
        w.title("LMU Panel VR")
        w.configure(background="#000000")
        w.geometry("560x330")
        w.attributes("-topmost", bool(self.settings.get("vr_top", False)))
        big, mid, small = ("Consolas", 44, "bold"), (FONT, 20, "bold"), (FONT, 13)
        self.vr_lbl = {}
        for key, font, fg in (("time", big, "#ffffff"), ("delta", mid, "#30d158"),
                              ("fuel", mid, "#ffffff"), ("finish", small, "#ff9f0a"),
                              ("msg", small, "#aeaeb2")):
            lab = tk.Label(w, text="", font=font, fg=fg, bg="#000000", anchor="w", justify="left",
                           wraplength=530)
            lab.pack(fill="x", padx=16, pady=(8 if key == "time" else 0, 0))
            self.vr_lbl[key] = lab
        self.vr_lbl["msg"].configure(
            text="Czekam na pierwsze okrążenie…\nW goglach: OpenKneeboard → dodaj kartę "
                 "„Window capture” → okno „LMU Panel VR”.")
        if self.engineer.state:
            self._vr_update("")

    def _vr_update(self, msg):
        if not (self.vr_win and self.vr_win.winfo_exists()):
            return
        s = self.engineer.state
        L = self.vr_lbl
        lap = f"Okr. {s.get('lap')}  " if s.get("lap") is not None else ""
        L["time"].configure(text=(lap + fmt_lap(s["time"])) if s.get("time") else lap + "—")
        d = s.get("delta")
        if s.get("pb"):
            L["delta"].configure(text="NAJLEPSZE OKRĄŻENIE", fg="#bf5af2")
        elif d is not None:
            L["delta"].configure(text=f"{d:+.3f} s do najlepszego", fg="#ff453a" if d > 0 else "#30d158")
        else:
            L["delta"].configure(text="", fg="#30d158")
        if s.get("left") is not None:
            unit = "%" if s["use_ve"] else " L"
            amount = s["ve"] * (100 if s["use_ve"] and s["ve"] < 1.5 else 1) if s["use_ve"] else s["fuel"]
            L["fuel"].configure(text=f"Paliwo {amount:.1f}{unit}  ·  na {s['left']:.1f} okr.",
                                fg="#ff453a" if s["left"] < 1.6 else "#ffffff")
        need = s.get("need")
        if need is not None and s.get("session") == "race":
            L["finish"].configure(
                text=(f"Do mety (~{s['remain']} okr.): brakuje {need:.1f} — oszczędzaj "
                      f"{need / s['remain']:.2f}/okr." if need > 0 else
                      f"Do mety (~{s['remain']} okr.): wystarczy, zapas {-need:.1f}"),
                fg="#ff453a" if need > 0 else "#30d158")
        else:
            L["finish"].configure(text="")
        if msg:
            L["msg"].configure(text=msg)

    # ================================================================ paliwo
    def _build_tab_fuel(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Paliwo  ")
        left = self._card(f, padding=(16, 12))
        left.pack(side="left", fill="y", padx=(0, 10))
        W = lambda: tk.Frame(left, background=C_CARD)

        def lbl(parent, text, **kw):
            return tk.Label(parent, text=text, background=C_CARD, foreground=kw.pop("fg", C_MUTED),
                            font=kw.pop("font", (FONT, 9)), anchor="w", **kw)

        lbl(left, "Kalkulator paliwa", fg=C_TEXT, font=(FONT, 13, "bold")).pack(anchor="w")
        lbl(left, "z Twojego realnego zużycia na tym torze", ).pack(anchor="w", pady=(0, 6))
        self.fu_mode = tk.StringVar(value=self.settings.get("fu_mode", "Kwalifikacje"))
        self.fu_laps = tk.StringVar(value=str(self.settings.get("fu_laps", 3)))
        self.fu_min = tk.StringVar(value=str(self.settings.get("fu_min", 30)))
        self.fu_margin = tk.StringVar(value=str(self.settings.get("fu_margin", 1)))
        self.fu_rate = tk.StringVar(value=self.settings.get("fu_rate", "maksymalne"))
        self.fu_src = tk.StringVar(value=self.settings.get("fu_src", "wszystkie sesje na torze"))
        self.fu_inlap = tk.BooleanVar(value=self.settings.get("fu_inlap", True))
        self.fu_form = tk.BooleanVar(value=self.settings.get("fu_form", True))
        form = tk.Frame(left, background=C_CARD)
        form.pack(anchor="w", fill="x")
        rows = [("Tryb", ttk.Combobox(form, textvariable=self.fu_mode, state="readonly", width=20,
                                       values=["Kwalifikacje", "Wyścig — czas", "Wyścig — okrążenia"])),
                ("Okrążenia", ttk.Spinbox(form, from_=1, to=200, width=8,
                                                               textvariable=self.fu_laps)),
                ("Czas wyścigu [min]", ttk.Spinbox(form, from_=5, to=1440, increment=5, width=8,
                                                    textvariable=self.fu_min)),
                ("Zapas [okrążenia]", ttk.Spinbox(form, from_=0, to=5, increment=0.5, width=8,
                                                   textvariable=self.fu_margin)),
                ("Zużycie na okrążenie", ttk.Combobox(form, textvariable=self.fu_rate, width=20,
                                                      state="readonly",
                                                      values=["maksymalne", "średnie"])),
                ("Dane z", ttk.Combobox(form, textvariable=self.fu_src, width=24, state="readonly",
                                        values=["wszystkie sesje na torze", "tylko ta sesja"]))]
        self.fu_rows = {}
        for r, (label, w) in enumerate(rows):
            lbl(form, label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            w.grid(row=r, column=1, sticky="w", pady=3)
            self.fu_rows[label] = w
            if isinstance(w, ttk.Combobox):
                w.bind("<<ComboboxSelected>>", lambda e: self._fuel_changed(reload=True))
            else:
                w.configure(command=self._fuel_changed)
                w.bind("<KeyRelease>", lambda e: self._fuel_changed())
        for text, var in (("okrążenie zjazdowe (kwalifikacje)", self.fu_inlap),
                          ("okrążenie formujące (wyścig)", self.fu_form)):
            ttk.Checkbutton(left, text=text, variable=var, style="Card.TCheckbutton",
                            command=self._fuel_changed).pack(anchor="w", pady=(4, 0))
        tk.Frame(left, background=C_LINE, height=1).pack(fill="x", pady=8)
        lbl(left, "Zatankuj", fg=C_MUTED, font=(FONT, 10)).pack(anchor="w")
        self.fu_total = lbl(left, "—", fg=C_ACCENT, font=(FONT, 26, "bold"))
        self.fu_total.pack(anchor="w")
        self.fu_parts = lbl(left, "", justify="left", wraplength=300)
        self.fu_parts.pack(anchor="w", pady=(4, 0))
        self.fu_game = lbl(left, "", justify="left", wraplength=300, fg=C_WARN)
        self.fu_game.pack(anchor="w", pady=(8, 0))
        tk.Frame(left, background=C_LINE, height=1).pack(fill="x", pady=8)
        lbl(left, "Ustaw w grze: setup bazowy").pack(anchor="w")
        self.fu_base = tk.StringVar()
        self.fu_base_cb = ttk.Combobox(left, textvariable=self.fu_base, width=34, state="readonly")
        self.fu_base_cb.pack(anchor="w", pady=(2, 6))
        self.fu_base_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_fuel_calc())
        ttk.Button(left, text="Zapisz setup z tym paliwem", style="Accent.TButton",
                   command=self._write_fuel_setup).pack(anchor="w")
        self.fu_write = lbl(left, "Zapisze kopię setupu z ustawionym paliwem do folderu setupów "
                                  "gry — wczytasz ją w garażu (Setup → Load).", justify="left",
                            wraplength=300)
        self.fu_write.pack(anchor="w", pady=(6, 0))
        self.fu_bases = {}

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)
        self.fu_info = tk.Label(right, background=C_BG, foreground=C_MUTED, anchor="w",
                                justify="left", font=(FONT, 9), wraplength=900)
        self.fu_info.pack(fill="x", pady=(0, 6))
        self.fu_stints = tk.Frame(right, background=C_BG)
        self.fu_stints.pack(fill="x", pady=(0, 6))
        box = ttk.Frame(right)
        box.pack(fill="both", expand=True)
        cols = ("lap", "kind", "time", "fuel", "ve", "start")
        self.fu_tree = ttk.Treeview(box, columns=cols, show="tree headings", selectmode="browse")
        self.fu_tree.heading("#0", text="Sesja / stint")
        self.fu_tree.column("#0", width=230, stretch=False)
        for c, (text, width) in {"lap": ("Okr.", 50), "kind": ("Rodzaj", 110),
                                 "time": ("Czas", 80), "fuel": ("Paliwo [L]", 90),
                                 "ve": ("Energia [%]", 90), "start": ("Na starcie [L]", 100)}.items():
            self.fu_tree.heading(c, text=text)
            self.fu_tree.column(c, width=width, anchor="center")
        self.fu_tree.tag_configure("grp", font=(FONT, 10, "bold"), background=C_GROUP)
        self.fu_tree.tag_configure("skip", foreground="#aeaeb2")
        self.fu_tree.tag_configure("empty", background="#ffe5e3")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.fu_tree.yview)
        self.fu_tree.configure(yscrollcommand=sb.set)
        self.fu_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.fu_data = {}
        self.fu_loading = set()

    def _fuel_changed(self, reload=False):
        if reload:
            self._load_fuel()
        else:
            self._fill_fuel_calc()
        self.save_settings()

    def _fuel_stems(self, info):
        if info["kind"] == "track" or self.fu_src.get() == "wszystkie sesje na torze":
            track = info["track"]
            stems = self.tracks.get(track, {}).get("stems", [])
        else:
            stems = [info["stem"]]
        return sorted(stems, key=lambda s: self.sessions[s]["dt"])

    def _player_vehicle(self, si):
        pl = si.get("player")
        return next((v.get("vehicle") for v in si["meta"].get("vehicles") or []
                     if same_driver(v.get("driver"), pl)), None)

    def _load_fuel(self):
        info = self._info()
        if not info:
            return
        stems = self._fuel_stems(info)
        todo = [s for s in stems if s not in self.fu_data and s not in self.fu_loading]
        for s in todo:
            self.fu_loading.add(s)
            si = self.sessions[s]

            def work(s=s, path=si["path"], pl=si.get("player")):
                try:
                    data = lmu_fuel.cached(path, pl, ANALYSIS / "_fuel")
                except Exception as e:
                    data = {"laps": [], "stints": [], "error": str(e)}
                self.q.put(("fuel_done", s, data))

            threading.Thread(target=work, daemon=True).start()
        if any(s in self.fu_loading for s in stems):
            self.fu_info.configure(text="Liczę zużycie paliwa z nagrań…")
            return
        self._fill_fuel()

    def _fuel_done(self, stem, data):
        self.fu_loading.discard(stem)
        self.fu_data[stem] = data
        if self.vnb.index(self.vnb.select()) == 7:
            self._load_fuel()

    def _fuel_laps(self):
        """Okrazenia do kalkulatora: z wybranych sesji, tym samym autem co biezaca sesja."""
        info = self._info()
        if not info:
            return [], []
        stems = self._fuel_stems(info)
        car = self._player_vehicle(info) if info["kind"] == "session" else None
        laps, used = [], []
        for s in stems:
            si = self.sessions[s]
            if car and self._player_vehicle(si) and self._player_vehicle(si) != car:
                continue
            d = self.fu_data.get(s) or {}
            race = si["session"] == "race"
            for l in d.get("laps", []):
                laps.append(dict(l, race=race, stem=s))
            used.append(s)
        return laps, used

    def _fill_fuel(self):
        info = self._info()
        t = self.fu_tree
        t.delete(*t.get_children())
        for c in self.fu_stints.winfo_children():
            c.destroy()
        laps, used = self._fuel_laps()
        rate = lmu_fuel.rates(laps)
        kinds = {"out": "wyjazdowe", "fly": "pomiarowe", "in": "zjazdowe"}
        ok_laps = {id(l) for l in laps}
        for s in reversed(used):
            si = self.sessions[s]
            d = self.fu_data.get(s) or {}
            if not d.get("laps"):
                continue
            gid = t.insert("", "end", open=s == (info.get("stem") or used[-1]), tags=("grp",),
                           text=f"{si['session']}  ·  {si['dt']:%d.%m %H:%M}",
                           values=("", "", "", f"{sum((l['fuel'] or 0) for l in d['laps']):.1f}",
                                   "", ""))
            for n, st in enumerate(d.get("stints", []), 1):
                empty = st.get("empty_lap") is not None
                sid = t.insert(gid, "end", open=True, tags=("empty",) if empty else (),
                               text=f"stint {n}: start {st['start_fuel'] or 0:.1f} L",
                               values=("", st.get("end") or "", "", f"{st.get('used', 0):.1f}",
                                       "", ""))
                for l in d["laps"]:
                    if l["lap"] not in st["laps"]:
                        continue
                    ve = l.get("ve")
                    t.insert(sid, "end", tags=() if l["kind"] == "fly" else ("skip",),
                             values=(l["lap"], kinds.get(l["kind"], l["kind"]),
                                     fmt_lap(l["time"]) if l.get("time") else "—",
                                     f"{l['fuel']:.3f}" if l.get("fuel") is not None else "—",
                                     f"{ve * 100 if ve < 1.5 else ve:.2f}" if ve else "",
                                     f"{l['f_start']:.2f}" if l.get("f_start") is not None else ""))
            # ostrzezenie o braku paliwa
            for st in d.get("stints", []):
                if st.get("empty_lap") is not None:
                    lp = [l for l in d["laps"] if l["lap"] in st["laps"] and l["kind"] == "fly"]
                    r = lmu_fuel.rates(lp)["avg"]
                    msg = (f"⚠ {si['session']} {si['dt']:%d.%m}: zabrakło paliwa — {st['end']}. "
                           f"Start {st['start_fuel']:.1f} L przy zużyciu {r:.2f} L/okr. = paliwo na "
                           f"{st['start_fuel'] / r:.1f} okr." if r else "")
                    if msg:
                        tk.Label(self.fu_stints, text=msg, background="#ffe5e3", foreground=C_ERR,
                                 font=(FONT, 9, "bold"), anchor="w", padx=10, pady=4).pack(fill="x")
        if not laps:
            self.fu_info.configure(text="Brak Twoich okrążeń z danymi paliwa w tych sesjach.",
                                   foreground=C_WARN)
        else:
            self.fu_info.configure(
                text=f"Zużycie z {rate['n']} okrążeń pomiarowych ({len(used)} sesji, to samo auto): "
                     f"średnio {rate['avg']:.3f} L/okr., maks. {rate['max']:.3f} L/okr."
                     + (f", wyjazd z boksu {rate['out']:.2f} L" if rate['out'] else "")
                     + (f", formujące ≈ {rate['formation']:.2f} L" if rate.get('formation') else "")
                     + (f"  ·  czas okrążenia ≈ {fmt_lap(rate['lap_time'])}" if rate['lap_time'] else "")
                     + ".  Szare = wyjazdowe/zjazdowe (nie liczą się do średniej).",
                foreground=C_MUTED)
        self._fu_rate = rate
        ve = lmu_fuel.rates(laps, key="ve")
        if ve.get("avg"):   # energia wirtualna: ulamek 0..1 -> %
            k = 100.0 if (ve["max"] or 0) < 1.5 else 1.0
            ve = {kk: (v * k if isinstance(v, float) and kk not in ("lap_time",) else v)
                  for kk, v in ve.items()}
        self._fu_ve = ve
        self._fill_fuel_bases(used)
        self._fill_fuel_calc()

    def _fill_fuel_bases(self, used):
        """Setupy, na ktorych mozna ustawic paliwo: z nagran tej sesji/toru + zapisane tego auta."""
        info = self._info()
        self.fu_bases = {}
        cls = None
        stems = [info["stem"]] if info and info["kind"] == "session" else list(reversed(used))
        for st in stems:
            si = self.sessions.get(st)
            if not si:
                continue
            pl = si.get("player")
            cls = cls or next((v.get("class") for v in si["meta"].get("vehicles") or []
                               if same_driver(v.get("driver"), pl)), None)
            for i, s in enumerate(self._session_setups(st), 1):
                self.fu_bases[f"z nagrania {si['session']} {si['dt']:%d.%m} #{i} — "
                              f"{s.get('name') or '?'}"] = (s["path"], None)
        folder = lmu_setup.track_folder(info["track"]) if info else None
        tok = (cls or "").lower()
        for e in sorted(lmu_setup.library(), key=lambda e: e["name"]):
            if folder and e["folder"] != folder.name:
                continue
            if tok and not any(w.lower().startswith(tok[:4]) for w in e["vehicle"].split()):
                continue
            self.fu_bases[f"{e['name']}"] = (Path(e["path"]), Path(e["path"]).parent)
        self.fu_base_cb["values"] = list(self.fu_bases)
        if self.fu_base.get() not in self.fu_bases:
            self.fu_base.set(next(iter(self.fu_bases), ""))

    def _write_fuel_setup(self):
        base = self.fu_bases.get(self.fu_base.get())
        total = getattr(self, "_fu_total", None)
        info = self._info()
        if not base or not info:
            self.fu_write.configure(text="Wybierz setup bazowy.", foreground=C_WARN)
            return
        src, folder = base
        folder = folder or lmu_setup.track_folder(info["track"])
        text = Path(src).read_text(encoding="utf-8", errors="ignore")
        new, desc = lmu_setup.with_fuel(text, liters=total, ve_pct=getattr(self, "_fu_total_ve", None))
        if new is None:
            self.fu_write.configure(text=f"Nie da się: {desc}.", foreground=C_ERR)
            return
        stem = Path(src).stem if folder == Path(src).parent else (
            lmu_setup.identify(lmu_setup.parse(text))[0] or "setup")
        stem = re.sub(r"\s*-\s*paliwo .*$", "", stem)
        mode = {"Kwalifikacje": "Q", "Wyścig — czas": "R", "Wyścig — okrążenia": "R"}[self.fu_mode.get()]
        tag = f"{desc.split()[-1]}VE" if "%" in desc else "".join(desc.split()[-2:])
        name = re.sub(r'[<>:"/\|?*]', "", f"{stem} - paliwo {mode} {tag}")
        dest = Path(folder) / f"{name}.svm"
        if dest.exists() and not messagebox.askyesno("Setup", f"Plik {dest.name} już istnieje. Nadpisać?"):
            return
        try:
            dest.write_text(new, encoding="utf-8")
        except OSError as e:
            self.fu_write.configure(text=f"Nie udało się zapisać: {e}", foreground=C_ERR)
            return
        self.log(f"setup z paliwem: {dest}")
        self.fu_write.configure(
            text=f"✓ Zapisano „{dest.name}” ({desc}) w Settings/{dest.parent.name}. W grze: "
                 f"Garaż → Setup → Load i wybierz ten plik.", foreground=C_OK)

    def _game_fuel_estimate(self):
        """L/okr. zakladane przez gre (z zapisanego setupu: 'xx.xL (yy.y laps)')."""
        info = self._info()
        if not info or info["kind"] != "session":
            return None
        sets = self._session_setups(info["stem"])
        if not sets:
            return None
        ps = lmu_setup.read(sets[0]["path"])
        for k, v in (ps or {}).get("values", {}).items():
            m = re.search(r"([\d.]+)\s*L\s*\(([\d.]+)\s*laps\)", v)
            if m and k.startswith("GENERAL.Fuel") and float(m.group(2)) > 0:
                return float(m.group(1)) / float(m.group(2)), k
        return None

    def _fill_fuel_calc(self):
        rate = getattr(self, "_fu_rate", None)
        mode = self.fu_mode.get()
        q = mode == "Kwalifikacje"
        self.fu_rows["Czas wyścigu [min]"].configure(state="normal" if mode == "Wyścig — czas" else "disabled")
        self.fu_rows["Okrążenia"].configure(
            state="disabled" if mode == "Wyścig — czas" else "normal")
        if not rate or not rate.get("avg"):
            self.fu_total.configure(text="—")
            self.fu_parts.configure(text="Brak danych o zużyciu.")
            self.fu_game.configure(text="")
            return
        per = rate["max"] if self.fu_rate.get() == "maksymalne" else rate["avg"]

        def num(v, d):
            try:
                return float(str(v.get()).replace(",", "."))
            except ValueError:
                return d

        total, parts = lmu_fuel.plan(
            per, "quali" if q else ("race_time" if mode == "Wyścig — czas" else "race_laps"),
            laps=int(num(self.fu_laps, 3)), minutes=num(self.fu_min, 30),
            lap_time=rate.get("lap_time"), out_lap=rate.get("out"), in_lap=self.fu_inlap.get(),
            in_lap_rate=rate.get("in"), formation=self.fu_form.get(),
            margin_laps=num(self.fu_margin, 1), formation_rate=rate.get("formation"))
        self._fu_total = total
        self.fu_total.configure(text=f"{math.ceil(total * 10) / 10:.1f} L")
        # auta z energia wirtualna (Hypercar / GT3) tankuje sie w %
        ve = getattr(self, "_fu_ve", None) or {}
        self._fu_total_ve = None
        if ve.get("avg"):
            per_ve = ve["max"] if self.fu_rate.get() == "maksymalne" else ve["avg"]
            tv, _ = lmu_fuel.plan(
                per_ve, "quali" if q else ("race_time" if mode == "Wyścig — czas" else "race_laps"),
                laps=int(num(self.fu_laps, 3)), minutes=num(self.fu_min, 30),
                lap_time=rate.get("lap_time"), out_lap=ve.get("out"), in_lap=self.fu_inlap.get(),
                in_lap_rate=ve.get("in"), formation=self.fu_form.get(),
                margin_laps=num(self.fu_margin, 1), formation_rate=ve.get("formation"))
            self._fu_total_ve = tv
            self.fu_total.configure(text=f"{math.ceil(total * 10) / 10:.1f} L  ·  {math.ceil(tv)}% VE")
        self.fu_parts.configure(text="\n".join(f"{name}: {v:.2f} L" for name, v in parts)
                                + f"\n(zużycie {per:.3f} L/okr. — {self.fu_rate.get()})")
        game = self._game_fuel_estimate()
        if game:
            g = game[0]
            diff = (per - g) / g * 100
            self.fu_game.configure(
                text=f"Gra w setupie zakłada {g:.3f} L/okr. — realnie zużywasz {per:.3f} "
                     f"({diff:+.0f}%). Na {total:.0f} L gra pokaże o "
                     f"{total / g - total / per:.1f} okr. więcej niż faktycznie przejedziesz."
                if abs(diff) >= 1 else f"Szacunek gry ({g:.3f} L/okr.) zgadza się z Twoim zużyciem.")
        else:
            self.fu_game.configure(text="")

    # ================================================================ prowadzenie
    def _build_tab_handling(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Prowadzenie  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Porównaj z sesją:", style="Muted.TLabel").pack(side="left")
        self.hd_cmp = tk.StringVar(value="(nic)")
        self.hd_cmp_cb = ttk.Combobox(bar, textvariable=self.hd_cmp, width=46, state="readonly")
        self.hd_cmp_cb.pack(side="left", padx=(6, 14))
        self.hd_cmp_cb.bind("<<ComboboxSelected>>", lambda e: self._load_handling())
        self.hd_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="tylko fazy z wyraźnym pod-/nadsterowaniem",
                        variable=self.hd_only, command=self._fill_handling).pack(side="left")
        self.hd_info = tk.Label(f, background=C_BG, foreground=C_MUTED, anchor="w",
                                justify="left", font=(FONT, 9))
        self.hd_info.pack(fill="x", pady=(0, 6))
        self.hd_cards = ttk.Frame(f)
        self.hd_cards.pack(fill="x", pady=(0, 8))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        cols = ("bal", "dbal", "vmin", "lat", "front", "lltd", "outs", "rh", "time", "dtime", "hint")
        self.hd_tree = ttk.Treeview(box, columns=cols, show="tree headings", selectmode="browse")
        self.hd_tree.heading("#0", text="Zakręt / faza")
        self.hd_tree.column("#0", width=170, stretch=False)
        for c, (text, width, anchor) in {
            "bal": ("Balans", 205, "w"), "dbal": ("Δ balans", 78, "center"),
            "vmin": ("v min", 58, "center"), "lat": ("G bok", 52, "center"),
            "front": ("Nacisk przód", 86, "center"), "lltd": ("LLTD przód", 78, "center"),
            "outs": ("Na zewn.", 64, "center"), "rh": ("Min prześwit P/T", 110, "center"),
            "time": ("Czas", 62, "center"), "dtime": ("Δ czas", 66, "center"),
            "hint": ("Podpowiedź ustawień", 360, "w")}.items():
            self.hd_tree.heading(c, text=text)
            self.hd_tree.column(c, width=width, anchor=anchor, stretch=c == "hint")
        self.hd_tree.tag_configure("corner", font=(FONT, 10, "bold"), background=C_GROUP)
        self.hd_tree.tag_configure("us", background="#fff0e0")
        self.hd_tree.tag_configure("os", background="#e6f0ff")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.hd_tree.yview)
        self.hd_tree.configure(yscrollcommand=sb.set)
        self.hd_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.hd_tree.bind("<Double-1>", self._hd_double)
        ttk.Label(f, style="Muted.TLabel", wraplength=1150, justify="left",
                  text="Balans = średni kąt poślizgu opon przód − tył: pomarańczowe = podsterowność "
                       "(przód ślizga się bardziej), niebieskie = nadsterowność. LLTD przód = jaka "
                       "część bocznego przenoszenia nacisku idzie na przednią oś (więcej = bardziej "
                       "podsterowne; zmieniasz stabilizatorami i sprężynami). Δ = ta sesja − "
                       "porównywana (Δ balans + = bardziej podsterownie, Δ czas + = wolniej). Średnia z 3 "
                       "najszybszych okrążeń. Dwuklik na zakręcie = pokaż na wykresach. Dane "
                       "podwozia są tylko z Twojego auta i tylko w nowych nagraniach.").pack(
            fill="x", pady=(6, 0))
        self.hd_data = {}
        self.hd_loading = set()
        self.hd_cmps = {}

    def _chassis_path(self, stem):
        p = RECORDS / f"{stem}.chassis.csv.gz"
        return p if p.exists() else None

    def _corner_names(self, info):
        out = []
        for z in (info.get("summary") or {}).get("braking") or []:
            if z.get("ref_start") is not None:
                out.append((z.get("corner", "?"), float(z["ref_start"])))
        return out

    def _handling_for(self, stem, then):
        """Wynik analizy prowadzenia sesji (z cache albo liczony w tle)."""
        if stem in self.hd_data:
            return self.hd_data[stem]
        info = self.sessions.get(stem)
        path = self._chassis_path(stem) if info else None
        if not path:
            return None
        if stem not in self.hd_loading:
            self.hd_loading.add(stem)
            tl = float(info["meta"].get("track_length_m") or 0) or (
                Analysis.load(info["outdir"]).track_len if info["summary"] else 0)
            names = self._corner_names(info)

            def work():
                try:
                    data = lmu_handling.cached(path, tl, ANALYSIS / "_handling", names)
                except Exception as e:
                    data = {"error": f"{type(e).__name__}: {e}"}
                self.q.put(("handling_done", stem, data))

            threading.Thread(target=work, daemon=True).start()
        return "loading"

    def _handling_done(self, stem, data):
        self.hd_loading.discard(stem)
        self.hd_data[stem] = data
        if self.vnb.index(self.vnb.select()) == 6:
            self._load_handling()

    def _load_handling(self):
        info = self._info()
        if not info:
            return
        if info["kind"] == "track":
            return self._fill_handling_track(info)
        stem = info["stem"]
        # sesje do porownania: ten sam tor, maja dane podwozia
        self.hd_cmps = {}
        for st in sorted(self.tracks.get(info["track"], {}).get("stems", []),
                         key=lambda s: self.sessions[s]["dt"]):
            if st != stem and self._chassis_path(st):
                si = self.sessions[st]
                self.hd_cmps[f"{si['session']} {si['dt']:%d.%m %H:%M} — "
                             f"{self._used_setup_name(si)}"] = st
        self.hd_cmp_cb["values"] = ["(nic)"] + list(self.hd_cmps)
        if self.hd_cmp.get() not in self.hd_cmps:
            self.hd_cmp.set("(nic)")
        if not self._chassis_path(stem):
            self._hd_clear("To nagranie nie ma danych podwozia — zapisuje je dopiero nowa wersja "
                           "rejestratora (tylko Twoje auto). Nagraj sesję ponownie, a tu pojawi się "
                           "analiza pod-/nadsterowności i transferu masy w każdym zakręcie.", C_WARN)
            return
        a = self._handling_for(stem, None)
        other = self.hd_cmps.get(self.hd_cmp.get())
        b = self._handling_for(other, None) if other else None
        if a == "loading" or b == "loading":
            self._hd_clear("Liczę prowadzenie auta z danych podwozia…", C_MUTED)
            return
        self._hd_cur = (a, b)
        self._fill_handling()

    def _used_setup_name(self, si):
        sets = self._session_setups(si["stem"])
        et, _ = self._best_lap_et(si)
        used = lmu_setup.setup_for_lap(sets, et)
        return (used.get("name") or "?") if used else "setup nieznany"

    def _hd_clear(self, msg, color):
        self.hd_tree.delete(*self.hd_tree.get_children())
        for c in self.hd_cards.winfo_children():
            c.destroy()
        self.hd_cards.pack_forget()
        self.hd_info.configure(text=msg, foreground=color)

    def _hd_card(self, title, lines):
        if not self.hd_cards.winfo_ismapped():
            self.hd_cards.pack(fill="x", pady=(0, 8), before=self.hd_tree.master)
        card = self._card(self.hd_cards, padding=(14, 8))
        card.pack(side="left", padx=(0, 8), fill="y")
        tk.Label(card, text=title, background=C_CARD, foreground=C_TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        for txt, col in lines:
            tk.Label(card, text=txt, background=C_CARD, foreground=col, font=(FONT, 9),
                     justify="left").pack(anchor="w")

    def _fill_handling(self):
        cur = getattr(self, "_hd_cur", None)
        info = self._info()
        if not cur or not info or info["kind"] != "session":
            return
        a, b = cur
        self._hd_clear("", C_MUTED)
        if not a or a.get("error"):
            self.hd_info.configure(text=(a or {}).get("error", "Brak danych."), foreground=C_WARN)
            return
        laps = ", ".join(fmt_lap(l["time"]) for l in a["laps"])
        txt = (f"Średnia z {len(a['laps'])} najszybszych okrążeń ({laps}) z {a['all_laps']} pełnych"
               f"  ·  setup: {self._used_setup_name(info)}")
        if b and not b.get("error"):
            txt += f"   ⇄ porównanie: {self.hd_cmp.get()}"
        self.hd_info.configure(text=txt, foreground=C_MUTED)
        H = lmu_handling
        # karty: balans ogolny, opony, przeswit
        lines = []
        for ph, lab in H.PHASES:
            vals = [c["phases"][ph].get("bal") for c in a["corners"] if c["phases"][ph].get("bal") is not None]
            v = statistics.fmean(vals) if vals else None
            col = C_TEXT if v is None or abs(v) < H.BAL_TH else ("#c25e00" if v > 0 else C_ACCENT)
            lines.append((f"{lab}: {H.describe_bal(v)}", col))
        self._hd_card("Balans (średnio)", lines)
        ty = a["tyres"]
        tl = []
        for w, lab in zip(H.WH, ("LP", "PP", "LT", "PT")):
            t = ty[w]
            spread = t["t_in"] - t["t_out"]
            tl.append((f"{lab}: {t['t_in']:.0f} / {t['t_mid']:.0f} / {t['t_out']:.0f} °C  ·  "
                       f"{t['press']:.0f} kPa  ·  camber {t['camber']:.1f}°  (w−z {spread:+.0f})", C_TEXT))
        self._hd_card("Opony: wewn. / środek / zewn.", tl)
        r = a["ride"]
        self._hd_card("Prześwit", [
            (f"minimum: przód {r['min_f']:.1f} cm · tył {r['min_r']:.1f} cm", C_TEXT),
            (f"przy V max: przód {r['fast_f']:.1f} · tył {r['fast_r']:.1f} cm "
             f"(rake {r['fast_r'] - r['fast_f']:+.1f})", C_TEXT),
            (f"dobicia (<0.4 cm): {r['bottom']:.0f} próbek / okr.",
             C_ERR if r["bottom"] > 5 else C_MUTED)])
        pairs = H.match(a["corners"], b["corners"]) if b and not b.get("error") else {}
        t = self.hd_tree
        for c in a["corners"]:
            o = pairs.get(c["name"])
            dt = (c["time"] - o["time"]) if o else None
            worst = max(((ph, c["phases"][ph].get("bal")) for ph, _ in H.PHASES
                         if c["phases"][ph].get("bal") is not None),
                        key=lambda x: abs(x[1]), default=(None, None))
            rows = []
            for ph, lab in H.PHASES:
                p = c["phases"][ph]
                bal = p.get("bal")
                if self.hd_only.get() and (bal is None or abs(bal) < H.BAL_TH):
                    continue
                op = o["phases"][ph] if o else {}
                dbal = (bal - op["bal"]) if bal is not None and op.get("bal") is not None else None
                tag = () if bal is None or abs(bal) < H.BAL_TH else (("us",) if bal > 0 else ("os",))
                rows.append((lab, (
                    H.describe_bal(bal), f"{dbal:+.2f}°" if dbal is not None else "",
                    f"{p['vmin']:.0f}" if p.get("vmin") else "", f"{p['lat_g']:.2f}" if p.get("lat_g") else "",
                    f"{p['front']:.1f}%" if p.get("front") else "", f"{p['lltd']:.0f}%" if p.get("lltd") else "",
                    f"{p['outside']:.0f}%" if p.get("outside") else "",
                    f"{p['rh_f']:.1f} / {p['rh_r']:.1f}" if p.get("rh_f") is not None else "",
                    f"{p['t']:.2f}" if p.get("t") else "", "", p.get("hint", "")), tag))
            if self.hd_only.get() and not rows:
                continue
            cid = t.insert("", "end", iid=f"C|{c['name']}", open=True, tags=("corner",),
                           text=f"{c['name']}  {'↰' if c['dir'] == 'L' else '↱'}",
                           values=(f"{H.describe_bal(worst[1])} · {dict(H.PHASES)[worst[0]]}"
                                   if worst[0] else "",
                                   "", f"{c['vmin']:.0f}", "", "", "", "",
                                   "", f"{c['time']:.2f}",
                                   (f"{dt:+.3f}" if dt is not None else ""),
                                   f"{c['d0']:.0f}–{c['d1']:.0f} m"))
            for lab, vals, tag in rows:
                t.insert(cid, "end", text=f"   {lab}", values=vals, tags=tag)

    def _fill_handling_track(self, info):
        self._hd_clear("Twoje sesje na tym torze z danymi podwozia — balans średnio na wejściu / "
                       "środku / wyjściu i setup. Dwuklik = otwórz sesję.", C_MUTED)
        self.hd_cmp_cb["values"] = ["(nic)"]
        self.hd_cmp.set("(nic)")
        t = self.hd_tree
        any_rows = False
        for st in sorted(info["stems"], key=lambda s: self.sessions[s]["dt"], reverse=True):
            if not self._chassis_path(st):
                continue
            si = self.sessions[st]
            data = self._handling_for(st, None)
            vals = ["…"] * 3
            if isinstance(data, dict) and not data.get("error"):
                vals = []
                for ph, _ in lmu_handling.PHASES:
                    v = [c["phases"][ph].get("bal") for c in data["corners"]
                         if c["phases"][ph].get("bal") is not None]
                    vals.append(lmu_handling.describe_bal(statistics.fmean(v) if v else None))
            any_rows = True
            t.insert("", "end", iid=f"S|{st}", text=f"{si['session']} {si['dt']:%d.%m %H:%M}",
                     values=(vals[0], "", "", "", "", "", "", "", "", "",
                             f"Twój najlepszy {si.get('mine') or '—'}  ·  wejście / środek / "
                             f"wyjście: {vals[0]} · {vals[1]} · {vals[2]}  ·  "
                             f"setup: {self._used_setup_name(si)}"))
        if not any_rows:
            self.hd_info.configure(text="Żadna sesja na tym torze nie ma jeszcze danych podwozia "
                                        "(nagraj nową sesję).", foreground=C_WARN)

    def _hd_double(self, e):
        iid = self.hd_tree.identify_row(e.y)
        if iid.startswith("S|") and self.lib.exists(iid[2:]):
            self.lib.selection_set(iid[2:])
            self.lib.see(iid[2:])
            return
        while iid and not iid.startswith("C|"):
            iid = self.hd_tree.parent(iid)
        cur = getattr(self, "_hd_cur", None)
        if not iid or not cur or not self.view:
            return
        c = next((c for c in cur[0]["corners"] if c["name"] == iid[2:]), None)
        if c:
            self.vnb.select(1)
            self.root.update_idletasks()
            self.chart.zoom_to(c["d0"] - 40, c["d1"] + 40)

    # ================================================================ setup
    def _build_tab_setup(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Setup  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Setup:", font=(FONT, 10, "bold")).pack(side="left")
        self.su_pick = tk.StringVar()
        self.su_pick_cb = ttk.Combobox(bar, textvariable=self.su_pick, width=44, state="readonly")
        self.su_pick_cb.pack(side="left", padx=(6, 14))
        self.su_pick_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_setup())
        ttk.Label(bar, text="porównaj z:", style="Muted.TLabel").pack(side="left")
        self.su_cmp = tk.StringVar(value="(nic)")
        self.su_cmp_cb = ttk.Combobox(bar, textvariable=self.su_cmp, width=40, state="readonly")
        self.su_cmp_cb.pack(side="left", padx=(6, 10))
        self.su_cmp_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_setup())
        self.su_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="tylko różnice", variable=self.su_only,
                        command=self._fill_setup).pack(side="left")
        ttk.Button(bar, text="Folder setupów", command=self._open_setup_dir).pack(side="right")
        ttk.Button(bar, text="Przypisz setup z pliku…",
                   command=self._assign_setup).pack(side="right", padx=(0, 8))
        self.su_info = tk.Label(f, background=C_BG, foreground=C_MUTED, anchor="w",
                                justify="left", font=(FONT, 9))
        self.su_info.pack(fill="x", pady=(0, 6))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        self.su_tree = ttk.Treeview(box, columns=("val", "cmp"), show="tree headings",
                                    selectmode="browse")
        self.su_tree.heading("#0", text="Parametr")
        self.su_tree.heading("val", text="Ten setup")
        self.su_tree.heading("cmp", text="Porównanie")
        self.su_tree.column("#0", width=260, stretch=False)
        self.su_tree.column("val", width=300, anchor="w")
        self.su_tree.column("cmp", width=300, anchor="w")
        self.su_tree.tag_configure("grp", font=(FONT, 10, "bold"), background=C_GROUP)
        self.su_tree.tag_configure("diff", background="#fff1d6")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.su_tree.yview)
        self.su_tree.configure(yscrollcommand=sb.set)
        self.su_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.su_tree.bind("<Double-1>", self._setup_hist_open)
        self.su_opts = {}     # etykieta -> sciezka .svm (wersje tej sesji)
        self.su_cmps = {}     # etykieta -> sciezka .svm (do porownania)

    def _session_setups(self, stem):
        info = self.sessions.get(stem)
        if not info:
            return []
        out = []
        for s in (info["meta"].get("setups") or []):
            p = RECORDS / s.get("file", "")
            if p.exists():
                out.append(dict(s, path=p))
        return out

    def _best_lap_et(self, info):
        """ET startu Twojego najlepszego okrazenia (z best_laps.csv analizy)."""
        import csv
        try:
            with open(info["outdir"] / "best_laps.csv", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if same_driver(r.get("driver"), info.get("player")):
                        return float(r["lap_start_et"]), float(r["lap_time_s"])
        except (OSError, ValueError, KeyError):
            pass
        return None, None

    def _setup_name(self, path):
        ps = lmu_setup.read(path)
        return lmu_setup.describe(ps) if ps else "?"

    def _load_setup(self):
        info = self._info()
        if not info:
            return
        self.su_opts, self.su_cmps = {}, {}
        if info["kind"] == "track":
            return self._fill_setup_history(info)
        sets = self._session_setups(info["stem"])
        et, lap = self._best_lap_et(info)
        used = lmu_setup.setup_for_lap(sets, et)
        pick = None
        for i, s in enumerate(sets, 1):
            when = "od startu" if s.get("session_et") is None else f"od {s['session_et'] / 60:.0f}. min"
            star = "  ★ najlepsze okr." if s is used and lap else ""
            lab = f"#{i} {when} — {s.get('name') or self._setup_name(s['path'])}{star}"
            self.su_opts[lab] = s["path"]
            if s is used:
                pick = lab
        self.su_pick_cb["values"] = list(self.su_opts)
        self.su_pick.set(pick or (list(self.su_opts)[0] if self.su_opts else ""))
        # do porownania: setupy z innych sesji na tym torze + zapisane setupy tego auta
        ps = lmu_setup.read(self.su_opts[self.su_pick.get()]) if self.su_opts else None
        tinfo = self.tracks.get(info["track"], {})
        for st in sorted(tinfo.get("stems", []), key=lambda s: self.sessions[s]["dt"]):
            if st == info["stem"]:
                continue
            for i, s in enumerate(self._session_setups(st), 1):
                si = self.sessions[st]
                self.su_cmps[f"{si['session']} {si['dt']:%d.%m %H:%M} #{i} — "
                              f"{s.get('name') or '?'}"] = s["path"]
        if ps:
            for e in sorted(lmu_setup.library(), key=lambda e: e["name"]):
                if e["vehicle"] == ps["vehicle"]:
                    self.su_cmps[f"zapisany: {e['folder']} / {e['name']}"] = Path(e["path"])
        self.su_cmp_cb["values"] = ["(nic)"] + list(self.su_cmps)
        if self.su_cmp.get() not in self.su_cmps:
            self.su_cmp.set("(nic)")
        if not sets:
            self.su_info.configure(
                text="To nagranie nie ma zapisanego setupu (nagrane starszą wersją). Nowe nagrania "
                     "zapisują go same. Możesz przypisać setup ręcznie: „Przypisz setup z pliku…”.",
                foreground=C_WARN)
        else:
            txt = (f"Setup z gry (TempModFile.svm) zapisany przy nagrywaniu · {len(sets)} wersj"
                   f"{'a' if len(sets) == 1 else 'e'} w tej sesji.")
            if lap:
                txt += f"  ★ = setup użyty na Twoim najlepszym okrążeniu ({fmt_lap(lap)})."
            veh = ps["vehicle"] if ps else ""
            if veh:
                txt += f"  Auto w setupie: {veh}."
            self.su_info.configure(text=txt, foreground=C_MUTED)
        self._fill_setup()

    def _fill_setup(self):
        t = self.su_tree
        t.delete(*t.get_children())
        t.configure(columns=("val", "cmp"), show="tree headings")
        t.heading("#0", text="Parametr")
        t.heading("val", text="Ten setup")
        t.heading("cmp", text="Porównanie")
        path = self.su_opts.get(self.su_pick.get())
        if not path:
            return
        ps = lmu_setup.read(path)
        other = lmu_setup.read(self.su_cmps[self.su_cmp.get()]) if self.su_cmp.get() in self.su_cmps else None
        t.heading("cmp", text="Porównanie" if not other else self.su_cmp.get()[:48])
        groups, ndiff = {}, 0
        for grp, lab, val, oval, changed in lmu_setup.rows(ps, other):
            ndiff += changed
            if self.su_only.get() and other and not changed:
                continue
            if grp not in groups:
                groups[grp] = t.insert("", "end", text=grp, open=True, tags=("grp",))
            t.insert(groups[grp], "end", text=lab, values=(val, oval),
                     tags=("diff",) if changed else ())
        if other:
            self.su_info.configure(text=self.su_info.cget("text").split("   ⇄")[0]
                                   + f"   ⇄ różnic: {ndiff}")

    def _fill_setup_history(self, info):
        t = self.su_tree
        t.delete(*t.get_children())
        t.configure(columns=("best", "setup"), show="tree headings")
        t.heading("#0", text="Sesja")
        t.heading("best", text="Twój najlepszy (pozycja)")
        t.heading("setup", text="Setup na najlepszym okrążeniu")
        t.column("best", width=200, anchor="center")
        t.column("setup", width=420, anchor="w")
        self.su_pick_cb["values"] = []
        self.su_pick.set("")
        self.su_cmp_cb["values"] = ["(nic)"]
        self.su_cmp.set("(nic)")
        for st in sorted(info["stems"], key=lambda s: self.sessions[s]["dt"], reverse=True):
            si = self.sessions[st]
            sets = self._session_setups(st)
            et, _ = self._best_lap_et(si)
            used = lmu_setup.setup_for_lap(sets, et)
            name = (used.get("name") or self._setup_name(used["path"])) if used else "— brak zapisu"
            t.insert("", "end", iid=f"S|{st}", text=f"{si['session']}  ·  {si['dt']:%d.%m %H:%M}",
                     values=(si.get("mine") or "—", name))
        self.su_info.configure(text="Twoje sesje na tym torze i setup z najlepszego okrążenia. "
                                    "Dwuklik = otwórz sesję.", foreground=C_MUTED)

    def _setup_hist_open(self, e):
        iid = self.su_tree.identify_row(e.y)
        if iid.startswith("S|") and self.lib.exists(iid[2:]):
            self.lib.selection_set(iid[2:])
            self.lib.see(iid[2:])
            self.root.after(50, lambda: self.vnb.select(5))

    def _assign_setup(self):
        info = self._info()
        if not info or info["kind"] != "session":
            return
        folder = lmu_setup.track_folder(info["track"])
        path = filedialog.askopenfilename(
            title="Wybierz setup użyty w tej sesji", initialdir=str(folder) if folder else None,
            filetypes=[("Setup LMU", "*.svm"), ("Wszystkie", "*.*")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        sets = self._session_setups(info["stem"])
        fname = f"{info['stem']}.setup{len(sets) + 1}.svm"
        (RECORDS / fname).write_text(text, encoding="utf-8")
        meta = info["meta"]
        entry = {"file": fname, "session_et": None, "manual": True, "name": Path(path).stem,
                 "vehicle": lmu_setup.parse(text)["vehicle"]}
        if sets:
            entry["session_et"] = 0.0
        meta["setups"] = [s for s in (meta.get("setups") or [])] + [entry]
        mpath = info["path"].with_name(info["path"].name[:-7] + ".metadata.json")
        mpath.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"setup przypisany: {Path(path).name} -> {info['stem']}")
        self._load_setup()

    def _open_setup_dir(self):
        info = self._info()
        folder = lmu_setup.track_folder(info["track"]) if info else None
        if folder:
            self._open_path(folder)
        else:
            messagebox.showinfo("Setup", "Nie znalazłem folderu LMU (UserData/player/Settings).")

    # ================================================================ klasy i auta
    def _cls_arg(self):
        return "all" if self.cls_mode.get() == "wszystkie klasy" else "mine"

    def _build_tab_classes(self):
        f = ttk.Frame(self.vnb, padding=8)
        self.vnb.add(f, text="  Klasy i auta  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        self.cls_group = tk.StringVar(value=self.settings.get("cls_group", "klasa"))
        self.cls_show = tk.StringVar(value=self.settings.get("cls_show", "wszystkie klasy"))
        self.cls_ref = tk.StringVar(value=self.settings.get("cls_ref", "lidera klasy"))
        self.cls_view = tk.StringVar(value=self.settings.get("cls_view", "czas + strata"))
        if self.cls_group.get() not in ("klasa", "klasa → model auta", "klasa → zespół"):
            self.cls_group.set("klasa")
        for label, var, vals, w in (("Grupuj:", self.cls_group,
                                     ["klasa", "klasa → model auta", "klasa → zespół"], 17),
                                    ("Pokaż:", self.cls_show, ["wszystkie klasy", "moja klasa"], 14),
                                    ("Sektory:", self.cls_view,
                                     ["czas + strata", "tylko strata", "tylko czasy"], 13),
                                    ("strata do:", self.cls_ref,
                                     ["mnie (Ty)", "lidera klasy", "najlepszych sektorów klasy"], 24)):
            ttk.Label(bar, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 6))
            cb = ttk.Combobox(bar, textvariable=var, values=vals, width=w, state="readonly")
            cb.pack(side="left", padx=(0, 14))
            cb.bind("<<ComboboxSelected>>", lambda e: (self._fill_classes(), self.save_settings()))
        self.cls_info = ttk.Label(bar, style="Muted.TLabel", text="")
        self.cls_info.pack(side="left")
        self.cls_cards = ttk.Frame(f)
        self.cls_cards.pack(fill="x", pady=(0, 8))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        cols = ("pos", "driver", "car", "best", "gap", "pct", "d1", "d2", "d3", "s1", "s2", "s3",
                "ideal", "laps", "sess")
        self.cls_tree = ttk.Treeview(box, columns=cols, show="tree headings", selectmode="browse")
        self.cls_tree.heading("#0", text="Klasa / auto")
        self.cls_tree.column("#0", width=190, stretch=False)
        for c, (text, width, anchor) in {
            "pos": ("#", 34, "center"), "driver": ("Kierowca", 190, "w"),
            "car": ("Auto / zespół", 210, "w"), "best": ("Najlepszy", 82, "center"),
            "gap": ("Strata", 70, "center"), "pct": ("%", 54, "center"),
            "d1": ("Δ S1", 74, "center"), "d2": ("Δ S2", 74, "center"), "d3": ("Δ S3", 74, "center"),
            "s1": ("S1", 62, "center"), "s2": ("S2", 62, "center"), "s3": ("S3", 62, "center"),
            "ideal": ("Idealne", 82, "center"), "laps": ("Okr.", 44, "center"),
            "sess": ("Sesja", 120, "w")}.items():
            self.cls_tree.heading(c, text=text)
            self.cls_tree.column(c, width=width, anchor=anchor, stretch=c in ("driver", "car"))
        self.cls_tree.tag_configure("grp", font=(FONT, 10, "bold"), background=C_GROUP)
        self.cls_tree.tag_configure("sub", font=(FONT, 10, "bold"))
        self.cls_tree.tag_configure("me", background=C_ME)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.cls_tree.yview)
        self.cls_tree.configure(yscrollcommand=sb.set)
        self.cls_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.cls_tree.bind("<Double-1>", lambda e: self._cls_compare())
        ttk.Label(f, style="Muted.TLabel", wraplength=1100, justify="left",
                  text="Najlepsze okrążenia wg gry, osobno dla każdej klasy (Hypercar / LMP2 / "
                       "GT3…). Na wierszu toru: zbiorczo ze wszystkich sesji na tym torze. "
                       "S1–S3 = sektory najlepszego okrążenia, Idealne = suma najlepszych "
                       "sektorów. Dwuklik na kierowcy = porównaj z nim na wykresach (jeśli jest "
                       "w analizie).").pack(fill="x", pady=(6, 0))
        self.cls_data = {}
        self.cls_loading = set()

    def _load_classes(self):
        info = self._info()
        if not info:
            return
        key = self.current
        if key in self.cls_data:
            return self._fill_classes()
        self.cls_info.configure(text="Liczę najlepsze czasy…")
        if key in self.cls_loading:
            return
        self.cls_loading.add(key)
        stems = info["stems"] if info["kind"] == "track" else [info["stem"]]
        jobs = [(self.sessions[s]["path"],
                 f"{self.sessions[s]['session']} {self.sessions[s]['dt']:%d.%m %H:%M}",
                 self.sessions[s].get("player"), s, self.sessions[s]["track"])
                for s in stems if s in self.sessions]

        def work():
            out, players = [], set()
            for path, label, pl, stem, track in jobs:
                try:
                    rows = lmu_classes.best_laps(path, ANALYSIS / "_best")
                    # sektory wszystkich kierowcow z pliku wynikow LMU (tez dla starszych nagran)
                    rows = lmu_classes.enrich(rows, lmu_results.for_recording(stem, track))
                    out.append((label, rows))
                    if pl:
                        players.add(pl)
                except Exception as e:
                    self.q.put(("log", f"klasy: {path.name}: {e}"))
            self.q.put(("classes_done", key, lmu_classes.merge(out), players, len(out)))

        threading.Thread(target=work, daemon=True).start()

    def _classes_done(self, key, rows, players, nsess):
        self.cls_loading.discard(key)
        self.cls_data[key] = (rows, players, nsess)
        if key == self.current:
            self._fill_classes()

    def _is_me(self, e, players):
        pl = self.player_var.get().strip()
        return bool(e.get("player") or any(same_driver(e["driver"], p) for p in players)
                    or (pl and same_driver(e["driver"], pl)))

    def _fill_classes(self):
        t = self.cls_tree
        t.delete(*t.get_children())
        for c in self.cls_cards.winfo_children():
            c.destroy()
        got = self.cls_data.get(self.current)
        if not got:
            return
        rows, players, nsess = got
        by_cls = {}
        for e in rows:
            by_cls.setdefault(e.get("cls") or "?", []).append(e)
        my_cls = {e.get("cls") or "?" for e in rows if self._is_me(e, players)}
        order = sorted(by_cls, key=lambda c: (c not in my_cls, lmu_classes.class_rank(c), c))
        if self.cls_show.get() == "moja klasa" and my_cls:
            order = [c for c in order if c in my_cls]
        fmt_s = lambda v: f"{v:.3f}" if v else ""
        grp = self.cls_group.get()
        sub = grp != "klasa"
        to_best = self.cls_ref.get() == "najlepszych sektorów klasy"
        to_me = self.cls_ref.get() == "mnie (Ty)"
        KS = ("s1", "s2", "s3")

        view = self.cls_view.get()

        def sec_deltas(e, ref):
            """Strata w kazdym sektorze; najwieksza oznaczona ▲."""
            if not e.get("s1") or None in ref:
                return ["", "", ""]
            ds = [(r - e[k]) if mine_ref else (e[k] - r) for k, r in zip(KS, ref)]
            worst = max(range(3), key=lambda i: ds[i])
            return [("—" if abs(d) < 0.0005 else f"{d:+.3f}") + (" ▲" if i == worst and d > 0.05 else "")
                    for i, d in enumerate(ds)]

        def sec_cells(e, ref):
            """Kolumny S1-S3: sam czas albo (tryb laczony) czas + strata w nawiasie."""
            times = [fmt_s(e.get(k)) for k in KS]
            if view != "czas + strata":
                return times
            ds = sec_deltas(e, ref)
            return [f"{t}  ({d})" if t and d and d != "—" else t for t, d in zip(times, ds)]

        for cls in order:
            arr = sorted(by_cls[cls], key=lambda e: e["best"])
            lead = arr[0]["best"]
            bs = [min((e[k] for e in arr if e.get(k)), default=None) for k in KS]
            me = next(((i, e) for i, e in enumerate(arr, 1) if self._is_me(e, players)), None)
            mine_ref = bool(to_me and me)
            if mine_ref:
                ref, base_t = [me[1].get(k) for k in KS], me[1]["best"]
            else:
                ref, base_t = (bs if to_best else [arr[0].get(k) for k in KS]), lead
            # karta podsumowania klasy
            card = self._card(self.cls_cards, padding=(14, 8))
            card.pack(side="left", padx=(0, 8))
            tk.Label(card, text=lmu_classes.class_label(cls), background=C_CARD, foreground=C_TEXT,
                     font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(card, text=f"{len(arr)} aut · najlepszy {fmt_lap(lead)} "
                                f"({short_name(arr[0]['driver'], 18)})",
                     background=C_CARD, foreground=C_MUTED, font=(FONT, 9)).pack(anchor="w")
            if me:
                gap = me[1]["best"] - lead
                tk.Label(card, text=f"Ty: P{me[0]} · {fmt_lap(me[1]['best'])} · "
                                    + ("najszybszy w klasie" if gap < 1e-4 else
                                       f"+{gap:.3f} s ({gap / lead * 100:.2f}%)"),
                         background=C_CARD, foreground=C_OK if gap < 1e-4 else C_ACCENT,
                         font=(FONT, 9, "bold")).pack(anchor="w")
                e = me[1]
                lref = bs if to_best else [arr[0].get(k) for k in KS]
                if e.get("s1") and None not in lref:
                    ds = [e[k] - r for k, r in zip(KS, lref)]
                    worst = max(range(3), key=lambda i: ds[i])
                    row = tk.Frame(card, background=C_CARD)
                    row.pack(anchor="w", pady=(2, 0))
                    for i, d in enumerate(ds):
                        tk.Label(row, text=f"S{i + 1} {d:+.3f}", background=C_CARD,
                                 foreground=C_ERR if (i == worst and d > 0.05) else
                                 (C_OK if d <= 0.0005 else C_TEXT),
                                 font=(FONT, 9, "bold" if i == worst and d > 0.05 else "normal")
                                 ).pack(side="left", padx=(0, 10))
                    if e.get("ideal"):
                        tk.Label(card, text=f"Twoje idealne okrążenie: {fmt_lap(e['ideal'])} "
                                            f"({e['ideal'] - e['best']:+.3f})",
                                 background=C_CARD, foreground=C_MUTED, font=(FONT, 9)).pack(anchor="w")
            gid = f"C|{cls}"
            t.insert("", "end", iid=gid, open=True, tags=("grp",),
                     text=f"{lmu_classes.class_label(cls)}  ·  {len(arr)}",
                     values=("", "", "", fmt_lap(lead), "", "", "", "", "",
                             fmt_s(bs[0]), fmt_s(bs[1]), fmt_s(bs[2]),
                             fmt_lap(sum(bs)) if None not in bs else "", "", ""))
            parents = {}
            if sub:
                groups = {}
                for e in arr:
                    key = (e.get("model") or lmu_classes.team_of(e["vehicle"])) if grp == "klasa → model auta" \
                        else lmu_classes.team_of(e["vehicle"])
                    groups.setdefault(key, []).append(e)
                for tn, te in sorted(groups.items(), key=lambda kv: kv[1][0]["best"]):
                    pid = f"{gid}|{tn}"
                    mine = any(self._is_me(e, players) for e in te)
                    t.insert(gid, "end", iid=pid, open=True,
                             tags=("sub",) + (("me",) if mine else ()),
                             text=f"{tn}  ·  {len(te)}",
                             values=("", "", "", fmt_lap(te[0]["best"]),
                                     (f"{base_t - te[0]['best']:+.3f}" if mine_ref else
                                      f"+{te[0]['best'] - lead:.3f}")
                                     if abs(te[0]["best"] - base_t) > 1e-4 else "—",
                                     "", *sec_deltas(te[0], ref), *sec_cells(te[0], ref),
                                     "", "", ""))
                    for e in te:
                        parents[id(e)] = pid
            for i, e in enumerate(arr, 1):
                gap = (base_t - e["best"]) if mine_ref else (e["best"] - lead)
                mine = self._is_me(e, players)
                t.insert(parents.get(id(e), gid), "end", tags=("me",) if mine else (),
                         values=(i, short_name(e["driver"], 28) + ("  (Ty)" if mine else ""),
                                 e["vehicle"], fmt_lap(e["best"]),
                                 "—" if abs(gap) < 1e-4 else f"{gap:+.3f}",
                                 "" if abs(gap) < 1e-4 else f"{gap / base_t * 100:+.2f}",
                                 *sec_deltas(e, ref), *sec_cells(e, ref),
                                 fmt_lap(e["ideal"]) if e.get("ideal") else "", e.get("laps") or "",
                                 e.get("session", "")))
        has_sec = any(e.get("s1") or e.get("ideal") for e in rows)
        hide = set() if has_sec else {"d1", "d2", "d3", "s1", "s2", "s3", "ideal"}
        if view == "czas + strata":
            hide |= {"d1", "d2", "d3"}
        elif view == "tylko strata":
            hide |= {"s1", "s2", "s3"}
        if nsess <= 1:
            hide.add("sess")
        t.configure(displaycolumns=[c for c in t["columns"] if c not in hide])
        wide = view == "czas + strata"
        for i, k in enumerate(KS, 1):
            t.column(k, width=150 if wide else 62)
            t.heading(k, text=f"S{i}" + (("  (Ty vs on)" if to_me else "  (strata)") if wide else ""))
        for i in (1, 2, 3):
            t.heading(f"d{i}", text=f"Δ S{i}" + (" Ty vs on" if to_me else
                                                 (" do najl." if to_best else "")))
        t.heading("gap", text="Ty vs on" if to_me else "Strata")
        t.heading("pct", text="%")
        self.cls_info.configure(
            text=f"{len(rows)} kierowców · {len(by_cls)} klas(y) · "
                 + (f"zbiorczo z {nsess} sesji" if nsess > 1 else "1 sesja")
                 + ("  ·  względem Ciebie: + = tyle tracisz do niego, − = tyle jesteś szybszy"
                    if to_me else ""))

    def _cls_compare(self):
        sel = self.cls_tree.selection()
        if not sel or not self.view:
            return
        name = str(self.cls_tree.set(sel[0], "driver")).replace("  (Ty)", "")
        if not name:
            return
        hit = next((n for n in self.view.drivers
                    if short_name(n, 28) == name or same_driver(n, name)), None)
        if not hit:
            self.set_status(f"{name} nie jest w tej analizie — zwiększ „max” albo zmień filtr "
                            f"i analizuj ponownie.")
            return
        focus, _ = self._compare_pair()
        if hit == focus:
            return
        self.focus_var.set(focus or "")
        self.base_var.set(hit)
        self._on_compare()
        self.vnb.select(1)

    def _bind_keys(self):
        self.root.bind("<F9>", lambda e: self.toggle_recording())
        self.root.bind("<F5>", lambda e: self.refresh_library())
        self.root.bind("<Control-l>", lambda e: self.toggle_log())
        self.root.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda e: self.fullscreen and self.toggle_fullscreen())
        self.fullscreen = False
        ttk.Style().layout("Full.TNotebook.Tab", [])

    def toggle_fullscreen(self):
        """Same wykresy + mapa na calym ekranie (F11 / Esc)."""
        if not self.fullscreen and not self.view:
            return
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.vnb.select(1)
            self._log_before_full = self.log_visible
            if self.log_visible:
                self.toggle_log(save=False)
            for w in (self.header, self.header_line, self.recwrap, self.v_top):
                w.pack_forget()
            self.body.forget(self.left_pane)
            self.body.pack_configure(padx=4, pady=4)
            self.v_body.pack_configure(pady=0)
            self.vnb.configure(style="Full.TNotebook")
            self.btn_full.configure(text="✕ Zamknij pełny ekran (Esc)")
            self.root.attributes("-fullscreen", True)
        else:
            self.root.attributes("-fullscreen", False)
            self.header.pack(fill="x", side="top", before=self.body)
            self.header_line.pack(fill="x", side="top", after=self.header)
            self.recwrap.pack(fill="x", side="top", before=self.body)
            self.body.insert(0, self.left_pane, weight=1)
            self.body.pack_configure(padx=14, pady=10)
            self.v_top.pack(fill="x", before=self.v_body)
            self.v_body.pack_configure(pady=(8, 0))
            self.vnb.configure(style="TNotebook")
            self.btn_full.configure(text="⛶ Pełny ekran")
            if getattr(self, "_log_before_full", False) and not self.log_visible:
                self.toggle_log(save=False)

    # ================================================================ ustawienia / log / stan
    def save_settings(self):
        try:
            data = {
                "player": self.player_var.get().strip(),
                "player_lap": int(self.player_lap_var.get() or 1),
                "filter": self.filter_var.get(),
                "top": int(self.top_var.get() or 8),
                "auto_analyze": bool(self.auto_analyze_var.get()),
                "map_delta": bool(self.map_delta_var.get()),
                "hz": self.hz_var.get(),
                "seg_mode": self.chart.seg_mode.get(),
                "cls_mode": self.cls_mode.get(),
                "voice_on": bool(self.voice_on.get()),
                "co_top": self.co_top.get(),
                "fu_mode": self.fu_mode.get(), "fu_laps": self.fu_laps.get(),
                "fu_min": self.fu_min.get(), "fu_margin": self.fu_margin.get(),
                "fu_rate": self.fu_rate.get(), "fu_src": self.fu_src.get(),
                "fu_inlap": bool(self.fu_inlap.get()), "fu_form": bool(self.fu_form.get()),
                "cls_group": self.cls_group.get(),
                "cls_show": self.cls_show.get(),
                "cls_ref": self.cls_ref.get(),
                "cls_view": self.cls_view.get(),
                "seg_sort": self.chart.seg_sort.get(),
                "log_visible": self.log_visible,
                "selected": self.current,
            }
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def toggle_log(self, save=True):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.logf.pack(side="bottom", fill="x")
            self.btn_log.configure(text="Ukryj log ▴")
        else:
            self.logf.pack_forget()
            self.btn_log.configure(text="Pokaż log ▾")
        if save:
            self.save_settings()

    def log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        low = text.lower()
        tag = "err" if ("błąd" in low or "blad" in low or "traceback" in low
                        or "error" in low or "nie udało" in low) else ""
        if "raport gotowy" in low or "zapisane:" in low or text.startswith("[OK]"):
            tag = "ok"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {text}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, text):
        self.status.configure(text=text)

    def _recording(self):
        return bool(self.rec_proc and self.rec_proc.poll() is None)

    def _update_pill(self):
        if self._recording():
            self.pill.configure(text="● Nagrywanie", style="PillRec.TLabel")
        elif self.busy:
            self.pill.configure(text="● " + self.busy + "…", style="PillWarn.TLabel")
        else:
            self.pill.configure(text="● Gotowe", style="PillOk.TLabel")
        if self.busy:
            if not self.progress.winfo_ismapped():
                self.progress.pack(side="right", padx=(0, 12))
                self.progress.start(12)
        elif self.progress.winfo_ismapped():
            self.progress.stop()
            self.progress.pack_forget()

    def _set_busy(self, label):
        self.busy = label
        self._update_pill()
        self._update_actions()
        self.btn_probe.configure(state="disabled" if (label or self._recording()) else "normal")
        missing = any(not r["summary"] for r in self.sessions.values())
        self.btn_batch.configure(state="normal" if (missing and not label) else "disabled")
        if hasattr(self, "btn_import"):
            self.btn_import.configure(state="disabled" if label else "normal")

    # ================================================================ biblioteka: dane
    def _scan(self):
        self.sessions.clear()
        self.tracks.clear()
        self.hidden_small = 0
        players = []
        for p in RECORDS.glob("*.csv.gz"):
            st = p.stat()
            meta = meta_for(p)
            if meta.get("player"):
                players.append((st.st_mtime, meta["player"]))
            if st.st_size < MIN_RECORD_BYTES:
                self.hidden_small += 1
                continue
            stem = p.name[:-7]
            outdir = ANALYSIS / stem
            summ = read_json(outdir / "summary.json")
            try:
                dt = datetime.fromisoformat(meta["started_at"])
            except Exception:
                dt = datetime.fromtimestamp(st.st_mtime)
            sess = meta.get("session")
            if not sess:
                try:
                    sess = SESSION_NAMES.get(int(meta.get("session_id")),
                                             str(meta.get("session_id")))
                except Exception:
                    sess = "?"
            drivers = summ.get("drivers") or []
            player = meta.get("player")
            mine = next(((i, d) for i, d in enumerate(drivers, 1)
                         if same_driver(d.get("name"), player)), None)
            track = meta.get("track") or "?"
            self.sessions[stem] = {
                "kind": "session", "path": p, "meta": meta, "outdir": outdir, "stem": stem,
                "summary": summ, "dt": dt, "track": track, "session": sess,
                "cars": len(meta.get("vehicles") or []), "size": st.st_size / 1e6,
                "demo": bool(meta.get("synthetic")), "player": player,
                "best": fmt_lap(drivers[0].get("lap_time_s")) if drivers else "",
                "mine": f"{fmt_lap(mine[1].get('lap_time_s'))} P{mine[0]}" if mine else "",
            }
            self.tracks.setdefault(track, {"stems": [], "track": track})["stems"].append(stem)
        for track, t in self.tracks.items():
            t["kind"] = "track"
            t["outdir"] = ANALYSIS / f"track_{track_slug(track)}"
            t["summary"] = read_json(t["outdir"] / "summary.json")
            t["last"] = max(self.sessions[s]["dt"] for s in t["stems"])
            drivers = t["summary"].get("drivers") or []
            pl = t["summary"].get("player") or self.player_var.get()
            mine = next(((i, d) for i, d in enumerate(drivers, 1)
                         if same_driver(d.get("name"), pl)), None)
            t["best"] = fmt_lap(drivers[0].get("lap_time_s")) if drivers else ""
            t["mine"] = f"{fmt_lap(mine[1].get('lap_time_s'))} P{mine[0]}" if mine else ""
        self.player_combo["values"] = sorted({n for _, n in players})
        if not self.player_var.get().strip() and players:
            self.player_var.set(max(players)[1])

    def refresh_library(self, select=None):
        select = select or self.current
        self.cls_data.clear()
        self.fu_data.clear()
        self._scan()
        self._fill_library()
        if select and self.lib.exists(select):
            self._select(select)
        elif self.lib.get_children():
            first = self.lib.get_children()[0]
            kids = self.lib.get_children(first)
            self._select(kids[0] if kids else first)
        else:
            self.lib.selection_set(())
            self._on_lib_select()

    def _select(self, iid):
        parent = self.lib.parent(iid)
        if parent:
            self.lib.item(parent, open=True)
        self.lib.selection_set(iid)
        self.lib.focus(iid)
        self.lib.see(iid)
        self._on_lib_select()

    def _fill_library(self):
        if not hasattr(self, "lib"):
            return
        sel = self.current
        closed = {i for i in self.lib.get_children() if not self.lib.item(i, "open")}
        self.lib.delete(*self.lib.get_children())
        q = self._query()
        sf = self.sess_filter.get()
        shown = 0
        for track, t in sorted(self.tracks.items(), key=lambda kv: kv[1]["last"], reverse=True):
            stems = []
            for s in sorted(t["stems"], key=lambda s: self.sessions[s]["dt"], reverse=True):
                r = self.sessions[s]
                if sf != "wszystkie" and r["session"] != sf:
                    continue
                if q and q not in track.lower() and not any(
                        q in (v.get("driver") or "").lower()
                        for v in r["meta"].get("vehicles") or []):
                    continue
                stems.append(s)
            if not stems:
                continue
            tid = "T|" + track
            self.lib.insert("", "end", iid=tid, text=f" {track}", tags=("track",),
                            open=tid not in closed,
                            values=(t["best"], t["mine"], "✓" if t["summary"] else ""))
            for s in stems:
                r = self.sessions[s]
                tags = ["demo"] if r["demo"] else (["new"] if not r["summary"] else [])
                label = f"{r['session']} · {r['dt']:%d.%m %H:%M} · {r['cars']} aut"
                if r["meta"].get("source") == "lmu_telemetry":
                    label = f"{r['session']} · {r['dt']:%d.%m %H:%M} · z gry"
                self.lib.insert(tid, "end", iid=s, text=label, tags=tags, values=(
                    r["best"], r["mine"], "✓" if r["summary"] else "•"))
                shown += 1
        missing = sum(1 for r in self.sessions.values() if not r["summary"])
        self.btn_batch.configure(text=f"Analizuj nowe ({missing})",
                                 state="normal" if (missing and not self.busy) else "disabled")
        self._update_import_button()
        if not self.sessions:
            self.lib_empty.configure(text="Brak nagrań.\n\nKliknij ● Nagrywaj u góry, gdy LMU "
                                          "działa i jesteś na torze (albo w replayu).")
            self.lib_empty.place(relx=0.5, rely=0.4, anchor="center")
        elif not shown:
            self.lib_empty.configure(text="Nic nie pasuje do filtra.")
            self.lib_empty.place(relx=0.5, rely=0.4, anchor="center")
        else:
            self.lib_empty.place_forget()
        extra = f", {self.hidden_small} krótkich ukrytych" if self.hidden_small else ""
        self.lib_count.configure(text=f"{len(self.sessions)} nagrań, {len(self.tracks)} tor(y)"
                                      f"{extra}.  Niebieskie = bez analizy.  Wiersz toru = "
                                      f"analiza ze wszystkich sesji.")
        if sel and self.lib.exists(sel):
            self.lib.selection_set(sel)

    def _info(self, iid=None):
        iid = iid or self.current
        if not iid:
            return None
        if iid.startswith("T|"):
            return self.tracks.get(iid[2:])
        return self.sessions.get(iid)

    # ================================================================ biblioteka: akcje
    def _on_lib_select(self):
        sel = self.lib.selection()
        iid = sel[0] if sel else None
        changed = iid != self.current
        self.current = iid
        self._load_view(new_item=changed)
        if changed:
            self.save_settings()

    def _lib_double(self):
        info = self._info()
        if info and not info["summary"]:
            self.analyze_current()

    def _lib_menu(self, event):
        row = self.lib.identify_row(event.y)
        if not row:
            return
        self._select(row)
        info = self._info()
        st = lambda ok: "normal" if ok else "disabled"
        is_sess = info["kind"] == "session"
        self.lib_menu.entryconfigure(0, state=st(not self.busy),
                                     label="Analizuj ponownie" if info["summary"] else "Analizuj")
        self.lib_menu.entryconfigure(1, state=st((info["outdir"] / "report.html").exists()))
        self.lib_menu.entryconfigure(2, state=st(is_sess and not self.busy))
        self.lib_menu.entryconfigure(4, state=st(is_sess))
        self.lib_menu.entryconfigure(5, state=st(info["outdir"].exists()))
        try:
            self.lib_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.lib_menu.grab_release()

    # ================================================================ podglad: ladowanie
    def _show_empty(self, msg, button=None):
        self.vnb.pack_forget()
        self.v_empty.pack(fill="both", expand=True)
        self.v_empty_msg.configure(text=msg)
        if button:
            self.v_empty_btn.configure(text=button)
            self.v_empty_btn.place(relx=0.5, rely=0.62, anchor="center")
        else:
            self.v_empty_btn.place_forget()

    def _load_view(self, new_item=False):
        info = self._info()
        if not info:
            self.view = None
            self.v_title.configure(text="Wybierz sesję albo tor z listy")
            self.v_sub.configure(text="")
            self.track_opts.pack_forget()
            self._update_actions()
            if self.sessions:
                self._show_empty("Wybierz sesję albo tor z listy po lewej.")
            else:
                self._show_empty("Nie ma jeszcze nagrań.\n\nUruchom LMU, wjedź na tor (albo "
                                 "odtwórz replay) i kliknij ● Nagrywaj u góry.")
            return
        if info["kind"] == "track":
            self.v_title.configure(text=f"{info['track']}  ·  analiza toru")
            sub = f"Łączy {len(info['stems'])} nagrań z tego toru: Twoje najlepsze okrążenia vs rywale."
            if info["summary"]:
                when = datetime.fromtimestamp(
                    (info["outdir"] / "summary.json").stat().st_mtime).strftime("%d.%m %H:%M")
                sub += f"  Ostatnia analiza: {when}."
            self.v_sub.configure(text=sub)
            self.track_opts.pack(fill="x", pady=(8, 0), before=self.acts)
        else:
            m = info["meta"]
            classes = sorted({v.get("class") for v in m.get("vehicles") or [] if v.get("class")})
            self.v_title.configure(text=f"{info['track']}  ·  {info['session']}"
                                        + ("  (DEMO)" if info["demo"] else ""))
            self.v_sub.configure(
                text=f"{info['dt']:%Y-%m-%d %H:%M}  ·  {info['cars']} aut"
                     + (f" ({', '.join(classes)})" if classes else "")
                     + (f"  ·  Ty: {info['player']}" if info["player"] else "")
                     + f"  ·  {info['size']:.1f} MB")
            self.track_opts.pack_forget()
        self._update_actions()
        if not info["summary"]:
            self.view = None
            if info["kind"] == "track":
                self._show_empty("Ten tor nie ma jeszcze analizy.\n\nUstaw swojego kierowcę i "
                                 "filtr rywali powyżej, potem kliknij przycisk.",
                                 "Generuj analizę toru")
            else:
                self._show_empty("To nagranie nie jest jeszcze przeanalizowane.", "Analizuj teraz")
            return
        try:
            a = Analysis.load(info["outdir"])
        except Exception as e:
            self.view = None
            self._show_empty(f"Nie udało się wczytać analizy:\n{e}", "Analizuj ponownie")
            return
        same = a is self.view
        self.view = a
        key = str(info["outdir"])
        sel = [n for n in self.view_sel.get(key, []) if n in a.drivers]
        self.view_sel[key] = sel or self._default_selection(a, info)
        self.v_empty.pack_forget()
        self.vnb.pack(fill="both", expand=True)
        self._fill_results()
        self._refresh_charts(keep_zoom=same and not new_item)
        self._fill_braking()
        self.brief_for = None
        self._on_view_tab()

    def _player_for(self, info):
        if info["kind"] == "track":
            return info["summary"].get("player") or self.player_var.get()
        return info.get("player")

    def _default_selection(self, a, info):
        me = next((n for n in a.drivers if same_driver(n, self._player_for(info))), None)
        sel = [n for n in [me, a.reference] if n]
        for n in a.drivers:
            if len(sel) >= 4:
                break
            if n not in sel:
                sel.append(n)
        return sel

    def _selected(self):
        info = self._info()
        return self.view_sel.get(str(info["outdir"]), []) if info else []

    def _fill_results(self):
        a, info = self.view, self._info()
        self.board.delete(*self.board.get_children())
        sel = self._selected()
        me = self._player_for(info)
        for i, d in enumerate(a.summary.get("drivers") or [], 1):
            n = d.get("name", "?")
            tag = f"c{i}"
            self.board.tag_configure(tag, foreground=a.colors.get(n, C_TEXT))
            mine = same_driver(n, me)
            is_ref = n == a.reference
            self.board.insert("", "end", iid=n, tags=[tag] + (["me"] if mine else []), values=(
                "☑" if n in sel else "☐", i,
                ("★ " if is_ref else "") + n + ("   (Ty)" if mine else ""),
                fmt_lap(d.get("lap_time_s")), fmt_gap(d.get("gap_s")),
                "ref." if is_ref else fmt_m(d.get("mean_brake_delta_m")),
                "ref." if is_ref else fmt_m(d.get("mean_pickup_delta_m")),
                f"{a.rate[n]:.0f} Hz" if n in a.rate else "",
                d.get("vehicle", "")))

    def _toggle_driver(self, name, force_on=False):
        info = self._info()
        if not info or not self.view:
            return
        sel = self.view_sel.setdefault(str(info["outdir"]), [])
        if name in sel:
            if force_on or len(sel) <= 1:
                return
            sel.remove(name)
        else:
            sel.append(name)
        if self.board.exists(name):
            self.board.set(name, "show", "☑" if name in sel else "☐")
        self._refresh_charts(keep_zoom=True)
        self._fill_braking()

    def _board_click(self, e):
        if self.board.identify_region(e.x, e.y) == "cell" and self.board.identify_column(e.x) == "#1":
            row = self.board.identify_row(e.y)
            if row:
                self._toggle_driver(row)
                return "break"

    def _board_double(self, e):
        row = self.board.identify_row(e.y)
        if row:
            self._toggle_driver(row, force_on=True)
            self.vnb.select(1)

    def _refresh_charts(self, keep_zoom=False):
        a = self.view
        sel = self._selected()
        for c in self.legend.winfo_children():
            c.destroy()
        if a:
            tk.Label(self.legend, text="Klik = ukryj/pokaż:", background=C_BG,
                     foreground=C_MUTED, font=(FONT, 9)).grid(row=0, column=0, padx=(0, 6))
            for i, n in enumerate(a.drivers):
                on = n in sel
                lab = tk.Label(self.legend, text=("■ " if on else "□ ") + short_name(n, 16),
                               background=C_CARD if on else C_BG,
                               foreground=a.colors[n] if on else "#aeaeb2",
                               font=(FONT, 9, "bold" if on else "normal"), padx=6, pady=2,
                               cursor="hand2", relief="flat", borderwidth=0)
                lab.grid(row=i // 5, column=1 + i % 5, sticky="w", padx=(0, 4), pady=1)
                lab.bind("<Button-1>", lambda e, n=n: self._toggle_driver(n))
        focus, base = self._compare_pair()
        names = a.drivers if a else []
        self.focus_combo["values"] = names
        self.base_combo["values"] = names
        self.focus_var.set(focus or "")
        self.base_var.set(base or "")
        self.chart.set_data(a, sel, focus=focus, base=base, keep_zoom=keep_zoom)
        self.map.set_data(a, sel, base=focus,
                          delta_vs=base if self.map_delta_var.get() else None, rival=base)
        self.map.set_range(self.chart.x0, self.chart.x1)

    def _compare_pair(self):
        """(focus, base) dla biezacej analizy: domyslnie Ty vs najszybszy (albo P2)."""
        a, info = self.view, self._info()
        if not a or not info:
            return None, None
        key = str(info["outdir"])
        pair = self.compare.get(key)
        if pair and pair[0] in a.drivers and pair[1] in a.drivers:
            return pair
        me = next((n for n in a.drivers if same_driver(n, self._player_for(info))), None)
        focus = me or (a.drivers[1] if len(a.drivers) > 1 else a.reference)
        base = a.reference if a.reference != focus else next(
            (n for n in a.drivers if n != focus), None)
        self.compare[key] = (focus, base)
        return focus, base

    def _on_compare(self):
        info = self._info()
        if not info or not self.view:
            return
        focus, base = self.focus_var.get(), self.base_var.get()
        if focus == base:
            others = [n for n in self.view.drivers if n != focus]
            base = others[0] if others else base
        self.compare[str(info["outdir"])] = (focus, base)
        sel = self.view_sel.setdefault(str(info["outdir"]), [])
        for n in (focus, base):
            if n and n not in sel:
                sel.append(n)
                if self.board.exists(n):
                    self.board.set(n, "show", "☑")
        self.save_settings()
        self._refresh_charts(keep_zoom=True)
        self._fill_braking()

    def _swap_compare(self):
        f, b = self.focus_var.get(), self.base_var.get()
        self.focus_var.set(b)
        self.base_var.set(f)
        self._on_compare()

    def _chart_hover(self, d):
        self.map.set_hover(d)

    def _chart_range(self, d0, d1):
        self.map.set_range(d0, d1)

    def _map_pick(self, d):
        self.chart.zoom_to(d - 250, d + 250)

    def _map_hover(self, d):
        self.chart.show_cursor(d)
        self.map.set_hover(self.chart._hover_d)

    def _fill_braking(self):
        a, info = self.view, self._info()
        self.brk.delete(*self.brk.get_children())
        if not a:
            return
        sel = set(self._selected())
        me = self._player_for(info)

        def num(v, fmt="{:.0f}"):
            return "" if v is None else fmt.format(v)

        for zi, z in enumerate(a.braking):
            cid = f"z{zi}"
            rs = z.get("ref_start")
            self.brk.insert("", "end", iid=cid, open=True, tags=("corner",),
                            text=f"{z.get('corner', '?')}   (ref. {rs:.0f} m)" if rs is not None
                            else z.get("corner", "?"))
            for d in z.get("drivers") or []:
                n = d.get("name")
                if n not in sel:
                    continue
                tags = ["me"] if same_driver(n, me) else []
                if not d.get("matched", True):
                    self.brk.insert(cid, "end", text=short_name(n, 28), tags=tags + ["miss"],
                                    values=("brak", "", "", "", "", "", ""))
                    continue
                self.brk.insert(cid, "end", text=short_name(n, 28), tags=tags, values=(
                    num(d.get("brake_start")), fmt_m(d.get("delta_start")),
                    num(d.get("brake_end")), num(d.get("pickup")), fmt_m(d.get("delta_pickup")),
                    num(d.get("min_speed_kmh")), num((d.get("max_brake") or 0) * 100, "{:.0f}%")))

    def _brk_double(self, e):
        row = self.brk.identify_row(e.y)
        if not row or not self.view:
            return
        cid = row if row.startswith("z") else self.brk.parent(row)
        try:
            z = self.view.braking[int(cid[1:])]
        except (ValueError, IndexError):
            return
        rs = z.get("ref_start") or 0
        ends = [d.get("pickup") for d in z.get("drivers") or [] if d.get("pickup")]
        self.vnb.select(1)
        self.root.update_idletasks()
        self.chart.zoom_to(rs - 150, (max(ends) if ends else rs + 300) + 100)

    def _on_view_tab(self):
        i = self.vnb.index(self.vnb.select())
        if i == 3:
            self._load_brief()
        elif i == 4:
            self._load_classes()
        elif i == 5:
            self._load_setup()
        elif i == 6:
            self._load_handling()
        elif i == 7:
            self._load_fuel()
        elif i == 8:
            self._load_coach()

    def _load_brief(self):
        info = self._info()
        if not info or not info["summary"] or self.brief_for == self.current:
            return
        try:
            import ai_coach
            text = ai_coach.build_brief(info["outdir"], self._player_for(info))
            (info["outdir"] / "ai_brief.md").write_text(text, encoding="utf-8")
        except Exception as e:
            text = f"Nie udało się zbudować briefu: {e}"
        self.brief_text = text
        self.brief_for = self.current
        self.brief.configure(state="normal")
        self.brief.delete("1.0", "end")
        self.brief.insert("1.0", text)
        self.brief.configure(state="disabled")

    def copy_brief(self):
        self._load_brief()
        if not self.brief_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.brief_text)
        self.btn_copy.configure(text="✓ Skopiowano")
        self.root.after(1800, lambda: self.btn_copy.configure(text="Kopiuj do schowka"))
        self.set_status("Brief skopiowany do schowka.")

    def save_brief(self):
        self._load_brief()
        if not self.brief_text:
            return
        p = filedialog.asksaveasfilename(defaultextension=".md",
                                         filetypes=[("Markdown", "*.md"), ("All", "*.*")],
                                         initialfile="ai_brief.md")
        if p:
            Path(p).write_text(self.brief_text, encoding="utf-8")
            self.set_status(f"Zapisano {p}")

    # ================================================================ przyciski akcji
    def _update_actions(self):
        info = self._info()
        idle = not self.busy
        if not info:
            for b in (self.btn_analyze, self.btn_browser, self.btn_tp, self.btn_dir):
                b.configure(state="disabled")
            return
        is_track = info["kind"] == "track"
        has = bool(info["summary"])
        if is_track:
            label = "Generuj ponownie" if has else "Generuj analizę toru"
        else:
            label = "Analizuj ponownie" if has else "Analizuj"
        self.btn_analyze.configure(text=label, state="normal" if idle else "disabled")
        self.v_empty_btn.configure(state="normal" if idle else "disabled")
        self.btn_browser.configure(
            state="normal" if (info["outdir"] / "report.html").exists() else "disabled")
        self.btn_dir.configure(state="normal" if info["outdir"].exists() else "disabled")
        if is_track:
            self.btn_tp.pack_forget()
        else:
            if not self.btn_tp.winfo_ismapped():
                self.btn_tp.pack(side="left", padx=(8, 0), before=self.btn_dir)
            who = self._board_driver() or info.get("player")
            self.btn_tp.configure(
                text=f"TinyPedal: {short_name(who, 18)}" if who else "Eksport do TinyPedal",
                state="normal" if (who and idle) else "disabled")

    def _board_driver(self):
        sel = self.board.selection()
        return sel[0] if sel else None

    def analyze_current(self):
        info = self._info()
        if not info or self.busy:
            return
        if info["kind"] == "track":
            self.generate_track_report()
        else:
            self.analyze_session(info["stem"])

    def open_in_browser(self):
        info = self._info()
        if info and (info["outdir"] / "report.html").exists():
            self._open_path(info["outdir"] / "report.html")

    def open_result_dir(self):
        info = self._info()
        if info and info["outdir"].exists():
            self._open_path(info["outdir"])

    def reveal_record(self):
        info = self._info()
        if not info or info["kind"] != "session":
            return
        try:
            subprocess.Popen(["explorer", "/select,", str(info["path"])])
        except Exception:
            self._open_path(RECORDS)

    def _open_path(self, path: Path):
        try:
            os.startfile(str(path))
        except Exception:
            webbrowser.open(Path(path).as_uri())

    # ================================================================ procesy
    def _run(self, cmd, done_kind, *extra):
        def worker():
            try:
                p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace",
                                     env=CHILD_ENV, creationflags=NO_WINDOW)
                tail = []
                for line in p.stdout:
                    line = line.rstrip()
                    tail = (tail + [line])[-6:]
                    self.q.put(("log", line))
                rc = p.wait()
                self.q.put((done_kind, rc, tail, *extra))
            except Exception as e:
                self.q.put(("log", f"BŁĄD: {e}"))
                self.q.put((done_kind, 1, [str(e)], *extra))

        threading.Thread(target=worker, daemon=True).start()

    def _fail(self, title, tail):
        detail = "\n".join(t for t in tail if t.strip())[-700:]
        if not self.log_visible:
            self.toggle_log()
        messagebox.showerror(title, "Nie udało się.\n\n" + (detail or "Szczegóły w logu."))

    # ================================================================ analiza sesji
    def analyze_session(self, stem):
        if self.busy or stem not in self.sessions:
            return
        info = self.sessions[stem]
        info["outdir"].mkdir(parents=True, exist_ok=True)
        left = f" (w kolejce: {len(self.batch)})" if self.batch else ""
        self._set_busy("Analizuję")
        self.set_status(f"Analizuję {info['track']} / {info['session']}…{left}")
        self.log(f"analiza: {info['path'].name}")
        cmd = [sys.executable, "-u", str(BASE / "analyze.py"), str(info["path"]),
               "--out", str(info["outdir"]), "--top", "8", "--cls", self._cls_arg()]
        self._run(cmd, "analysis_done", stem)

    # ---------------------------------------------------------------- import z gry (DuckDB)
    def _update_import_button(self):
        try:
            n = len(lmu_gamerec.pending(RECORDS, ANALYSIS / "_imported.json"))
        except Exception:
            n = 0
        self.btn_import.configure(text=f"Import z gry ({n})" if n else "Import z gry",
                                  state="disabled" if self.busy else "normal")

    def import_game(self):
        if self.busy:
            return
        if not lmu_gamerec.available():
            messagebox.showinfo(
                "Import z gry", "Do czytania nagrań gry potrzebna jest biblioteka duckdb.\n\n"
                                "Zainstaluj ją w terminalu:\n    python -m pip install duckdb\n\n"
                                "i uruchom aplikację ponownie.")
            return
        todo = lmu_gamerec.pending(RECORDS, ANALYSIS / "_imported.json")
        if not todo and lmu_gamerec.in_progress():
            names = ", ".join(p.stem for p in lmu_gamerec.in_progress())
            messagebox.showinfo("Import z gry", f"Gra jeszcze zapisuje sesję:\n{names}\n\n"
                                                "Zaimportuj ją po wyjściu z sesji (albo z gry).")
            return
        if not todo:
            d = lmu_gamerec.telemetry_dir()
            messagebox.showinfo(
                "Import z gry", "Brak nowych nagrań gry do importu.\n\n"
                                "Włącz w LMU: Ustawienia → Sterowanie → Rozgrywka → Telemetry "
                                f"Recording, przejedź sesję i kliknij ponownie.\n\nFolder: {d or '?'}")
            return
        self._set_busy("Import z gry")
        self.set_status(f"Importuję {len(todo)} nagrań gry…")
        player = self.player_var.get().strip() or None

        def work():
            try:
                stems = lmu_gamerec.import_all(RECORDS, ANALYSIS, player,
                                               log=lambda m: self.q.put(("log", m)))
            except Exception as e:
                self.q.put(("log", f"import z gry: {type(e).__name__}: {e}"))
                stems = []
            self.q.put(("import_done", stems))

        threading.Thread(target=work, daemon=True).start()

    def _import_done(self, stems):
        self._set_busy(None)
        self.refresh_library(select=stems[0] if stems else None)
        self.set_status(f"Zaimportowano {len(stems)} nagrań gry." if stems else
                        "Import z gry: nic nie zaimportowano (szczegóły w logu).")
        if stems and self.auto_analyze_var.get():
            self.batch = stems[1:]
            self.analyze_session(stems[0])

    def analyze_all_missing(self):
        if self.busy:
            return
        todo = [s for s, r in sorted(self.sessions.items()) if not r["summary"]]
        if not todo:
            return
        if len(todo) > 1 and not messagebox.askyesno(
                "Analiza", f"Przeanalizować {len(todo)} nagrań bez analizy?\n"
                           "Może to potrwać kilka minut."):
            return
        self.batch = todo[1:]
        self.analyze_session(todo[0])

    def _analysis_done(self, rc, tail, stem):
        self._set_busy(None)
        ok = rc == 0 and (ANALYSIS / stem / "summary.json").exists()
        self.log(f"raport gotowy: {stem}" if ok else f"analiza nie powiodła się: {stem}")
        self.view_sel.pop(str(ANALYSIS / stem), None)
        if self.batch:
            nxt = self.batch.pop(0)
            self.refresh_library()
            self.analyze_session(nxt)
            return
        self.refresh_library(select=stem)
        if ok:
            self.set_status("Analiza gotowa.")
        else:
            self.set_status("Analiza nie powiodła się.")
            self._fail("Analiza sesji", tail)

    # ================================================================ TinyPedal
    def export_tinypedal(self):
        info = self._info()
        if not info or info["kind"] != "session" or self.busy:
            return
        who = self._board_driver() or info.get("player")
        cmd = [sys.executable, "-u", str(BASE / "to_tinypedal.py"), str(info["path"]),
               "--out", str(TINYPEDAL)]
        if who:
            cmd += ["--driver", who]
        self._set_busy("Eksport TinyPedal")
        self.set_status(f"Eksport okrążenia: {who}…")
        self.log(f"TinyPedal: {info['path'].name} | {who}")
        self._run(cmd, "tp_done", who)

    def _tp_done(self, rc, tail, who):
        self._set_busy(None)
        ok_lines = [t for t in tail if t.startswith("[OK]")]
        if rc == 0 and ok_lines:
            self.set_status(f"Zapisano okrążenie {who} do tinypedal_laps.")
            if messagebox.askyesno(
                    "TinyPedal", ok_lines[-1][5:] + "\n\nPlik zapisany w tinypedal_laps/.\n"
                    "W TinyPedal: Tools → Best Lap Manager → Import.\n\nOtworzyć folder?"):
                self._open_path(TINYPEDAL)
        else:
            self.set_status("Eksport do TinyPedal nie powiódł się.")
            self._fail("TinyPedal", tail)

    # ================================================================ analiza toru
    def generate_track_report(self):
        info = self._info()
        if self.busy or not info or info["kind"] != "track":
            return
        track = info["track"]
        player = self.player_var.get().strip()
        if not player:
            messagebox.showinfo("Analiza toru", "Wpisz albo wybierz swojego kierowcę (pole „Ja”).")
            return
        fkey = dict(FILTERS).get(self.filter_var.get(), "faster_or_sector")
        plap = max(1, min(3, int(self.player_lap_var.get() or 1)))
        top = max(2, min(8, int(self.top_var.get() or 8)))
        cmd = [sys.executable, "-u", str(BASE / "track_analysis.py"),
               "--track", track, "--player", player, "--filter", fkey,
               "--top", str(top), "--player-lap", str(plap), "--out", str(info["outdir"]),
               "--cls", self._cls_arg()]
        self.save_settings()
        self._set_busy("Analizuję tor")
        self.set_status(f"Analiza toru {track} ({len(info['stems'])} nagrań)…")
        self.log(f"analiza toru: {track} | {player} | {fkey} | lap#{plap} | top={top}")
        self._run(cmd, "track_done", "T|" + track)

    def _track_done(self, rc, tail, iid):
        self._set_busy(None)
        self.view_sel.pop(str(ANALYSIS / f"track_{track_slug(iid[2:])}"), None)
        self.refresh_library(select=iid)
        info = self._info(iid)
        if rc == 0 and info and info["summary"]:
            self.set_status("Analiza toru gotowa.")
            self.log("raport gotowy: analiza toru")
        else:
            self.set_status("Analiza toru nie powiodła się.")
            self._fail("Analiza toru", tail)

    # ================================================================ nagrywanie
    def toggle_recording(self):
        if self._recording():
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self._recording():
            return
        try:
            STOP_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        cmd = [sys.executable, "-u", str(BASE / "record.py"),
               "--out", str(RECORDS), "--hz", self.hz_var.get() or "30",
               "--max-seconds", "7200", "--stop-file", str(STOP_FILE)]
        try:
            self.rec_proc = subprocess.Popen(
                cmd, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=CHILD_ENV, creationflags=NO_WINDOW)
        except Exception as e:
            self.rec_proc = None
            messagebox.showerror("Nagrywanie", f"Nie udało się uruchomić: {e}")
            return
        self.rec_started = time.time()
        self.rec_error = None
        self.last_saved = None
        self.btn_rec.configure(text="■  Stop", style="Stop.TButton")
        self.rec_timer.configure(text="0:00:00", foreground=C_REC)
        self.rec_state.configure(text="Łączę z LMU…", foreground=C_WARN)
        self.rec_hint.configure(text="Czekam na dane z gry (musisz być na torze albo w replayu).")
        self.btn_probe.configure(state="disabled")
        self.set_status("Nagrywanie…")
        self.log("start nagrywania")
        self._update_pill()

        def reader(proc):
            try:
                for line in proc.stdout:
                    self.q.put(("rec_log", line.rstrip()))
            finally:
                rc = proc.wait()
                self.q.put(("rec_done", rc))

        threading.Thread(target=reader, args=(self.rec_proc,), daemon=True).start()

    def _rec_line(self, line):
        if line.startswith("[LAP]"):
            return self._on_lap_event(line)
        self.log(line)
        m = REC_STATUS_RE.search(line)
        if m:
            try:
                best = fmt_lap(float(m["best"]))
            except ValueError:
                best = "—"
            who = f" ({short_name(m['who'], 20)})" if m["who"] else ""
            rows = f"{int(m['rows']):,}".replace(",", " ")
            self.rec_state.configure(text=f"Nagrywa:  {m['track']}  ·  {m['session']}",
                                     foreground=C_REC)
            self.rec_hint.configure(text=f"{m['cars']} aut   ·   najlepszy {best}{who}   ·   "
                                         f"{rows} próbek   ·   {m['mb']} MB")
        elif "podlaczono do LMU_Data" in line:
            self.rec_state.configure(text="Połączono — czekam na auta", foreground=C_WARN)
            self.rec_hint.configure(text="Jeśli to trwa: wejdź na tor / uruchom replay "
                                         "(menu i garaż nie dają danych).")
        elif "czekam: telemetria pusta" in line:
            replay = "3" in line.split("ctrl=")[1].split(",")[0] if "ctrl=" in line else False
            frozen = "ET=0.0)" in line
            self.rec_state.configure(text="Połączono — gra nie wysyła danych aut", foreground=C_WARN)
            if replay:
                self.rec_hint.configure(
                    text="Replay: lista aut jest, ale telemetria pusta"
                         + (" i zegar stoi" if frozen else "")
                         + ". Puść replay (play, nie pauza) w widoku z torem.")
            else:
                self.rec_hint.configure(text="Wejdź na tor / uruchom replay "
                                             "(menu i garaż nie dają danych).")
        elif "zapisane:" in line:
            self.last_saved = Path(line.split("zapisane:", 1)[1].split(" (")[0].strip())
        elif "BLAD" in line:
            self.rec_error = line
            self.rec_state.configure(text="Brak połączenia z LMU", foreground=C_ERR)
            self.rec_hint.configure(text="Uruchom LMU i włącz Settings → Gameplay → "
                                         "Enable external plugins.")

    def _tick(self):
        if self._recording() and self.rec_started:
            self.rec_timer.configure(text=fmt_dur(time.time() - self.rec_started))
        self.root.after(1000, self._tick)

    def stop_recording(self):
        if not self._recording():
            return
        try:
            STOP_FILE.write_text("", encoding="utf-8")
        except Exception as e:
            self.log(f"nie mogę zapisać stop-file: {e}")
        self.btn_rec.configure(state="disabled")
        self.rec_state.configure(text="Zatrzymuję i zapisuję…", foreground=C_WARN)
        self.set_status("Zatrzymuję nagrywanie…")

    def _rec_done(self, rc):
        self.rec_proc = None
        self.btn_rec.configure(text="●  Nagrywaj", style="Rec.TButton", state="normal")
        self.rec_timer.configure(foreground=C_MUTED)
        self.btn_probe.configure(state="disabled" if self.busy else "normal")
        self.log(f"nagrywanie zakończone (rc={rc})")
        self._update_pill()
        saved = self.last_saved if self.last_saved and self.last_saved.exists() else None
        stem = saved.name[:-7] if saved else None
        self.refresh_library(select=stem)
        if self.rec_error:
            self.set_status("Nie udało się połączyć z LMU.")
            return
        self.rec_state.configure(text="Nagranie zakończone", foreground=C_OK)
        if not saved:
            self.rec_hint.configure(text="Nic nie zapisano (brak aut na torze).")
            self.set_status("Nagrywanie zakończone — brak danych.")
            return
        size = saved.stat().st_size / 1e6
        if stem not in self.sessions:
            self.rec_hint.configure(text=f"{saved.name} ({size:.1f} MB) — za krótkie do analizy.")
        elif self.auto_analyze_var.get():
            self.rec_hint.configure(text=f"{saved.name} ({size:.1f} MB) — analizuję, wynik "
                                         f"pojawi się po prawej.")
            self.analyze_session(stem)
        else:
            self.rec_hint.configure(text=f"{saved.name} ({size:.1f} MB) — zaznaczone na liście.")
        self.set_status("Nagrywanie zakończone.")

    def run_probe(self):
        if self.busy or self._recording():
            return
        if not self.log_visible:
            self.toggle_log()
        self._set_busy("Test połączenia")
        self.set_status("Test połączenia z LMU (10 s)…")
        self._run([sys.executable, "-u", str(BASE / "record.py"), "--probe-seconds", "10"],
                  "probe_done")

    def _probe_done(self, rc, tail):
        self._set_busy(None)
        self.set_status("Test zakończony — wynik w logu." if rc == 0
                        else "Brak połączenia z LMU (szczegóły w logu).")

    # ================================================================ petla
    def _poll_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log(item[1])
                elif kind == "rec_log":
                    self._rec_line(item[1])
                elif kind == "analysis_done":
                    self._analysis_done(*item[1:])
                elif kind == "track_done":
                    self._track_done(*item[1:])
                elif kind == "tp_done":
                    self._tp_done(*item[1:])
                elif kind == "import_done":
                    self._import_done(item[1])
                elif kind == "fuel_done":
                    self._fuel_done(*item[1:])
                elif kind == "handling_done":
                    self._handling_done(*item[1:])
                elif kind == "classes_done":
                    self._classes_done(*item[1:])
                elif kind == "probe_done":
                    self._probe_done(*item[1:])
                elif kind == "rec_done":
                    self._rec_done(item[1])
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def on_close(self):
        if self._recording():
            if not messagebox.askyesno("Nagrywanie", "Nagrywanie trwa. Zatrzymać, zapisać "
                                                     "i zamknąć?"):
                return
            self.stop_recording()
            try:
                self.rec_proc.wait(timeout=8)
            except Exception:
                pass
        self.save_settings()
        self.voice.stop()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    LmuApp(root)
    if "--smoke-test" in sys.argv:
        root.after(1500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = BASE / "lmu_app_error.log"
        err.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("LMU Telemetry", f"Błąd aplikacji.\nSzczegóły: {err}")
        except Exception:
            pass
        raise
