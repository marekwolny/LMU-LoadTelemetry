"""Wyglad w stylu iOS dla ttk (bez dodatkowych bibliotek).

Tk nie umie zaokraglonych rogow, wiec ksztalty (karty, kapsulowe przyciski, pola,
przelaczniki, zakladki-segmenty, suwaki) sa generowane jako PNG z kanalem alfa
(zlib + struct) i podpinane jako elementy obrazkowe ttk ("9-slice").
"""

from __future__ import annotations

import base64
import math
import struct
import tkinter as tk
import zlib
from tkinter import ttk

# kolory systemowe iOS (tryb jasny)
BG = "#f2f2f7"          # systemGroupedBackground
CARD = "#ffffff"
TEXT = "#1c1c1e"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
FILL = "#e3e3e8"        # pola / przyciski szare
ACCENT = "#007aff"
GREEN = "#34c759"
RED = "#ff3b30"
ORANGE = "#ff9500"
DARK = "#1c1c1e"

_keep: list = []        # referencje do obrazkow (inaczej Tk je zwolni)


def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def shade(c, f):
    """f < 0 = ciemniej, f > 0 = jasniej."""
    r, g, b = _rgb(c)
    if f < 0:
        r, g, b = (int(v * (1 + f)) for v in (r, g, b))
    else:
        r, g, b = (int(v + (255 - v) * f) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _png(w, h, px):
    raw = b"".join(b"\x00" + bytes(px[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


def _sdf_box(x, y, x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r
    qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def _pixel(layers, x, y):
    r = g = b = 0.0
    a = 0.0
    for sdf, col, alpha, soft in layers:
        d = sdf(x + 0.5, y + 0.5)
        if soft:
            cov = max(0.0, min(1.0, 1 - d / soft)) ** 2 if d > 0 else 1.0
        else:
            cov = max(0.0, min(1.0, 0.5 - d))
        la = cov * alpha
        if la <= 0:
            continue
        cr, cg, cb = _rgb(col)
        na = la + a * (1 - la)
        r = (cr * la + r * a * (1 - la)) / na
        g = (cg * la + g * a * (1 - la)) / na
        b = (cb * la + b * a * (1 - la)) / na
        a = na
    return bytes((int(r + .5), int(g + .5), int(b + .5), int(a * 255 + .5)))


def image(w, h, layers, margin=None):
    """layers: lista (sdf(x, y) -> float, kolor, alfa, miekkosc) od spodu do gory.
    margin: ksztalt jest staly dalej niz `margin` px od krawedzi - liczymy tylko brzegi,
    a srodek powielamy (duze obrazki generuja sie wtedy w milisekundach)."""
    m = margin
    cols = m is not None and 2 * m + 1 < w      # srodkowe kolumny takie same
    rows = m is not None and 2 * m + 1 < h      # srodkowe wiersze takie same

    def row(y):
        if not cols:
            return b"".join(_pixel(layers, x, y) for x in range(w))
        return (b"".join(_pixel(layers, x, y) for x in range(m))
                + _pixel(layers, m, y) * (w - 2 * m)
                + b"".join(_pixel(layers, x, y) for x in range(w - m, w)))

    if rows:
        px = b"".join([row(y) for y in range(m)] + [row(m)] * (h - 2 * m)
                      + [row(y) for y in range(h - m, h)])
    else:
        px = b"".join(row(y) for y in range(h))
    img = tk.PhotoImage(data=base64.b64encode(_png(w, h, px)), format="png")
    _keep.append(img)
    return img


def rounded(w, h, r, fill, alpha=1.0, border=None, shadow=0, pad=0):
    x0, y0, x1, y1 = pad, pad, w - pad, h - pad
    layers = []
    if shadow:
        layers.append((lambda x, y: _sdf_box(x, y - 1, x0, y0, x1, y1, r), "#000000", shadow, pad))
    if border:
        layers.append((lambda x, y: _sdf_box(x, y, x0, y0, x1, y1, r), border, alpha, 0))
        layers.append((lambda x, y: _sdf_box(x, y, x0 + 1, y0 + 1, x1 - 1, y1 - 1, r - 1),
                       fill, alpha, 0))
    else:
        layers.append((lambda x, y: _sdf_box(x, y, x0, y0, x1, y1, r), fill, alpha, 0))
    return image(w, h, layers, margin=r + pad + 3)


def switch(on):
    w, h = 44, 26
    track = GREEN if on else "#e3e3e8"
    kx = w - 13 if on else 13
    return image(w, h, [
        (lambda x, y: _sdf_box(x, y, 1, 1, w - 1, h - 1, 12), track, 1.0, 0),
        (lambda x, y: math.hypot(x - kx, y - 13.8) - 10.5, "#000000", 0.18, 3),
        (lambda x, y: math.hypot(x - kx, y - 13) - 10.5, "#ffffff", 1.0, 0),
    ])


def _button(style, name, fill, fg, font, padding, r=12, h=26):
    W = 320   # ttk kafelkuje srodek obrazka - szeroki obrazek = malo kafelkow = szybko
    imgs = {
        "": rounded(W, h, r, fill),
        "active": rounded(W, h, r, shade(fill, -0.07 if fill != FILL else -0.05)),
        "pressed": rounded(W, h, r, shade(fill, -0.16 if fill != FILL else -0.10)),
        "disabled": rounded(W, h, r, fill, alpha=0.35),
    }
    el = f"{name}.ios"
    style.element_create(el, "image", imgs[""], ("disabled", imgs["disabled"]),
                         ("pressed", imgs["pressed"]), ("active", imgs["active"]),
                         border=r, padding=0, width=2 * r + 4, height=h, sticky="nsew")
    style.layout(name, [(el, {"sticky": "nswe", "children": [
        ("Button.padding", {"sticky": "nswe", "children": [
            ("Button.label", {"sticky": "nswe"})]})]})])
    style.configure(name, foreground=fg, font=font, padding=padding, anchor="center")
    style.map(name, foreground=[("disabled", shade(fg, 0.45) if fg != "#ffffff" else "#ffffff")])


def apply(root, font):
    """Ustawia caly motyw. `font` = rodzina czcionki."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    base = (font, 10)
    semi = (font, 10, "bold")
    root.option_add("*Font", base)
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*Menu.background", CARD)
    root.option_add("*Menu.activeBackground", ACCENT)
    root.option_add("*Menu.activeForeground", "#ffffff")
    root.option_add("*Menu.relief", "flat")
    root.configure(background=BG)
    # po wyborze z listy ttk zostawia tekst zaznaczony (wyglada na wyszarzony) - czyscimy
    root.bind_class("TCombobox", "<<ComboboxSelected>>",
                    lambda e: e.widget.selection_clear(), add="+")

    style.configure(".", font=base, background=BG, foreground=TEXT, bordercolor=LINE,
                    lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground="#ffffff")
    style.configure("TFrame", background=BG)
    style.configure("White.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("WMuted.TLabel", background=CARD, foreground=MUTED)

    # karta: biala, zaokraglona, z delikatnym cieniem
    card = rounded(640, 640, 14, CARD, shadow=0.10, pad=4)
    style.element_create("Card.ios", "image", card, border=18, padding=0, width=40, height=40, sticky="nsew")
    style.layout("Card.TFrame", [("Card.ios", {"sticky": "nswe"})])
    style.configure("Card.TFrame", background=BG)

    # przyciski-kapsuly
    _button(style, "TButton", FILL, ACCENT, semi, (14, 6))
    _button(style, "Accent.TButton", ACCENT, "#ffffff", semi, (18, 7))
    _button(style, "Rec.TButton", RED, "#ffffff", (font, 11, "bold"), (22, 10), r=14, h=30)
    _button(style, "Stop.TButton", DARK, "#ffffff", (font, 11, "bold"), (22, 10), r=14, h=30)
    link = rounded(240, 26, 10, FILL)
    clear = rounded(240, 26, 10, BG, alpha=0.0)
    style.element_create("Link.ios", "image", clear, ("active", link), border=10, padding=0, width=24, height=26,
                         sticky="nsew")
    style.layout("Link.TButton", [("Link.ios", {"sticky": "nswe", "children": [
        ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
    style.configure("Link.TButton", foreground=ACCENT, padding=(8, 3), font=base)

    # przelacznik zamiast checkboxa
    off, on = switch(False), switch(True)
    style.element_create("Switch.ios", "image", off, ("selected", on),
                         ("disabled", off), padding=(0, 0, 8, 0), sticky="w")
    for name in ("TCheckbutton", "Card.TCheckbutton"):
        style.layout(name, [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Switch.ios", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
        style.configure(name, padding=(0, 2), indicatormargin=0)
    style.configure("TCheckbutton", background=BG)
    style.configure("Card.TCheckbutton", background=CARD)
    style.map("TCheckbutton", background=[("active", BG)])
    style.map("Card.TCheckbutton", background=[("active", CARD)])

    # pola tekstowe i listy rozwijane
    field = rounded(480, 24, 9, FILL, alpha=0.85)
    ffocus = rounded(480, 24, 9, "#dcdce2", border=ACCENT)
    style.element_create("Field.ios", "image", field, ("focus", ffocus), border=9, padding=0, width=22, height=24,
                         sticky="nsew")
    style.layout("TEntry", [("Field.ios", {"sticky": "nswe", "children": [
        ("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    style.configure("TEntry", padding=(10, 5), foreground=TEXT, insertcolor=ACCENT)
    style.element_create("Combo.ios", "image", field, ("focus", field), border=9, padding=0, width=22, height=24,
                         sticky="nsew")
    style.layout("TCombobox", [("Combo.ios", {"sticky": "nswe", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
            ("Combobox.textarea", {"sticky": "nswe"})]})]})])
    style.configure("TCombobox", padding=(10, 4, 4, 4), arrowcolor=ACCENT, arrowsize=11,
                    background=shade(FILL, 0.3), bordercolor=shade(FILL, 0.3),
                    lightcolor=shade(FILL, 0.3), darkcolor=shade(FILL, 0.3),
                    fieldbackground=FILL, foreground=TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", FILL)],
              selectbackground=[("readonly", "focus", FILL), ("readonly", FILL)],
              selectforeground=[("readonly", "focus", TEXT), ("readonly", TEXT)],
              foreground=[("disabled", MUTED), ("readonly", TEXT)],
              background=[("active", shade(FILL, 0.3)), ("pressed", shade(FILL, 0.3))],
              bordercolor=[("focus", shade(FILL, 0.3))], lightcolor=[("focus", shade(FILL, 0.3))],
              darkcolor=[("focus", shade(FILL, 0.3))], arrowcolor=[("disabled", MUTED)])

    # zakladki = segmenty (wybrana: biala kapsula z cieniem)
    seg_on = rounded(320, 28, 12, CARD, shadow=0.12, pad=2)
    seg_hover = rounded(320, 28, 12, "#e5e5ea", pad=2)
    seg_off = rounded(320, 28, 12, BG, alpha=0.0, pad=2)
    style.element_create("Seg.ios", "image", seg_off, ("selected", seg_on),
                         ("active", seg_hover), border=14, padding=0, width=30, height=28,
                         sticky="nsew")
    style.layout("TNotebook.Tab", [("Seg.ios", {"sticky": "nswe", "children": [
        ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
            ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 6),
                    bordercolor=BG, lightcolor=BG, darkcolor=BG)
    style.configure("TNotebook.Tab", padding=(16, 6), foreground=MUTED, font=semi,
                    background=BG, borderwidth=0)
    style.map("TNotebook.Tab", foreground=[("selected", TEXT)], expand=[("selected", (0, 0, 0, 0))],
              background=[("selected", BG)])

    # listy (Treeview)
    style.configure("Treeview", rowheight=30, font=base, background=CARD, fieldbackground=CARD,
                    foreground=TEXT, borderwidth=0, relief="flat")
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", font=(font, 9, "bold"), background=CARD,
                    foreground=MUTED, relief="flat", borderwidth=0, padding=(6, 6),
                    lightcolor=CARD, darkcolor=CARD, bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", "#f5f5f8")])
    style.map("Treeview", background=[("selected", "#dcebff")],
              foreground=[("selected", TEXT)])

    # cienkie paski przewijania z zaokraglonym suwakiem
    thumb = rounded(10, 400, 4, "#c7c7cc", pad=1)
    thumb_a = rounded(10, 400, 4, "#aeaeb2", pad=1)
    style.element_create("VThumb.ios", "image", thumb, ("active", thumb_a), ("pressed", thumb_a),
                         border=5, width=10, height=30, sticky="nsew")
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("VThumb.ios", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", troughcolor=CARD, background=CARD, bordercolor=CARD,
                    lightcolor=CARD, darkcolor=CARD, arrowsize=10, width=10)
    hthumb = rounded(400, 10, 4, "#c7c7cc", pad=1)
    style.element_create("HThumb.ios", "image", hthumb, border=5, width=30, height=10, sticky="nsew")
    style.layout("Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
        ("HThumb.ios", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Horizontal.TScrollbar", troughcolor=CARD, bordercolor=CARD,
                    lightcolor=CARD, darkcolor=CARD)

    style.configure("TProgressbar", background=ACCENT, troughcolor=LINE, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=10, gripcount=0, background=BG,
                    lightcolor=BG, darkcolor=BG, bordercolor=BG)

    # "pigulki" statusu w naglowku
    for name, col in (("Ok", GREEN), ("Rec", RED), ("Warn", ORANGE)):
        img = rounded(240, 22, 11, shade(col, 0.82))
        style.element_create(f"Pill{name}.ios", "image", img, border=11, padding=0, width=26, height=22,
                             sticky="nsew")
        style.layout(f"Pill{name}.TLabel", [(f"Pill{name}.ios", {"sticky": "nswe", "children": [
            ("Label.padding", {"sticky": "nswe", "children": [("Label.label", {"sticky": "nswe"})]})]})])
        style.configure(f"Pill{name}.TLabel", foreground=shade(col, -0.35) if name != "Rec" else "#d70015",
                        font=semi, padding=(12, 3), background=BG)
    return style


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    """Zaokraglony prostokat na Canvas (wielokat z wygladzaniem)."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(*pts, smooth=True, **kw)
