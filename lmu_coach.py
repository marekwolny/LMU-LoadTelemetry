"""Plan treningu: gdzie tracisz najwiecej i dlaczego - po polsku, z konkretna wskazowka.

Dla kazdego odcinka (od hamowania do hamowania) porownuje Twoj przejazd z rywalem (albo z
najszybszym na tym odcinku): punkt i sila hamowania, puszczanie hamulca (trail braking),
toczenie bez gazu i hamulca, predkosc w szczycie, moment pelnego gazu, predkosc wyjscia
i linie w szczycie. Strata jest dzielona na wejscie (do szczytu) i wyjscie.
"""

from __future__ import annotations

import bisect
import math


def _idx(tr, d):
    return min(max(bisect.bisect_left(tr["d"], d), 0), len(tr["d"]) - 1)


def profile(a, name, d0, d1):
    """Punkty charakterystyczne przejazdu odcinka [d0, d1]."""
    tr = a.traces[name]
    D, B, T, V = tr["d"], tr["brk"], tr["thr"], tr["spd"]
    i0, i1 = _idx(tr, d0), _idx(tr, d1)
    if i1 - i0 < 5:
        return None
    rng = range(i0, i1 + 1)
    on = next((i for i in rng if B[i] > 0.1), None)
    apex = min(range(on if on is not None else i0, i1 + 1), key=lambda i: V[i])
    p = {"d0": d0, "d1": d1, "apex_i": apex, "apex_d": D[apex], "vmin": V[apex],
         "vin": V[i0], "vout": V[i1], "brk_d": D[on] if on is not None else None}
    if on is not None:
        pk = max(range(on, apex + 1), key=lambda i: B[i])
        rel = next((i for i in range(pk, i1 + 1) if B[i] < 0.05), apex)
        p.update(peak=B[pk], peak_d=D[pk], rel_d=D[rel], v_brk=V[on],
                 brk_len=D[rel] - D[on])
    full = next((i for i in range(apex, i1 + 1) if T[i] > 0.95), None)
    pick = next((i for i in range(apex, i1 + 1) if T[i] > 0.2), None)
    p["full_d"] = D[full] if full is not None else None
    p["pick_d"] = D[pick] if pick is not None else None
    start = on if on is not None else i0
    end = full if full is not None else i1
    p["coast"] = sum(D[i + 1] - D[i] for i in range(start, min(end, len(D) - 1))
                     if T[i] < 0.05 and B[i] < 0.05)
    # linia w szczycie: odleglosc od wewnetrznej krawedzi
    side = tr.get("side")
    if side and a.edges:
        j0, j1 = max(apex - 15, 0), min(apex + 15, len(D) - 1)
        h0 = math.atan2(tr["x"][apex] - tr["x"][j0], tr["z"][apex] - tr["z"][j0])
        h1 = math.atan2(tr["x"][j1] - tr["x"][apex], tr["z"][j1] - tr["z"][apex])
        turn = (h1 - h0 + math.pi) % (2 * math.pi) - math.pi
        el, er = a.edge_at(D[apex])
        # side > 0 = lewa strona; zakret w lewo, gdy kurs rosnie przeciwnie do ruchu wskazowek
        left = turn < 0
        p["inside"] = (el - side[apex]) if left else (side[apex] - er)
        p["dir"] = "lewy" if left else "prawy"
    return p


def compare(a, me, rival, name, d0, d1):
    pm, pr = profile(a, me, d0, d1), profile(a, rival, d0, d1)
    if not pm or not pr:
        return None
    loss = a.seg_delta(me, rival, d0, d1)
    ap = (pm["apex_d"] + pr["apex_d"]) / 2
    entry = a.seg_delta(me, rival, d0, ap)
    exit_ = (loss - entry) if None not in (loss, entry) else None
    reasons = []

    def add(score, text, tip):
        reasons.append((score, text, tip))

    vb = max(pm.get("v_brk") or pm["vin"], 40) / 3.6
    if pm.get("brk_d") is not None and pr.get("brk_d") is not None:
        db = pm["brk_d"] - pr["brk_d"]
        if db < -5:
            add(-db / vb * 0.6, f"hamujesz {-db:.0f} m wcześniej niż {short(rival)}",
                f"przesuń punkt hamowania o ~{-db / 2:.0f}–{-db:.0f} m dalej (stopniowo, po 5 m)")
        elif db > 5 and pm["vmin"] < pr["vmin"] - 2:
            add(db / vb * 0.4 + 0.05, f"hamujesz {db:.0f} m później, ale w szczycie jesteś wolniej",
                "za późne / za ostre hamowanie — hamuj wcześniej i wnieś więcej prędkości w zakręt")
        if pm.get("peak") and pr.get("peak") and pm["peak"] < pr["peak"] - 0.12:
            add((pr["peak"] - pm["peak"]) * 0.5,
                f"za słaby nacisk na hamulec: max {pm['peak'] * 100:.0f}% vs {pr['peak'] * 100:.0f}%",
                "wciśnij hamulec mocniej na początku hamowania (do progu ABS), potem odpuszczaj")
        if pm.get("rel_d") and pr.get("rel_d"):
            dr = pr["rel_d"] - pm["rel_d"]
            if dr > 8:
                add(dr / vb * 0.3, f"puszczasz hamulec {dr:.0f} m wcześniej",
                    "trail braking: odpuszczaj hamulec stopniowo aż do skrętu, nie zdejmuj nogi naraz")
    dc = pm["coast"] - pr["coast"]
    if dc > 10:
        add(dc / max(pm["vmin"] / 3.6, 10) * 0.35,
            f"toczysz się {pm['coast']:.0f} m bez gazu i hamulca ({pr['coast']:.0f} m u rywala)",
            "skróć toczenie: płynnie przejdź z hamulca na lekki gaz zaraz po szczycie")
    dv = pm["vmin"] - pr["vmin"]
    if dv < -2:
        add(-dv * 0.012, f"w szczycie zakrętu wolniej o {-dv:.0f} km/h ({pm['vmin']:.0f} vs {pr['vmin']:.0f})",
            "wnieś więcej prędkości: wcześniej odpuść hamulec, szerszy łuk na wejściu")
    if pm.get("full_d") and pr.get("full_d"):
        dg = pm["full_d"] - pr["full_d"]
        if dg > 8:
            add(dg / max(pm["vmin"] / 3.6, 10) * 0.25, f"pełny gaz {dg:.0f} m później",
                "otwieraj kierownicę i wcześniej dochodź do pełnego gazu na wyjściu")
    dvo = pm["vout"] - pr["vout"]
    if dvo < -3:
        add(-dvo * 0.01, f"wyjście wolniej o {-dvo:.0f} km/h",
            "lepsze wyjście ważniejsze niż wejście — późniejszy szczyt, wcześniej gaz")
    corner = pm["vmin"] < pm["vin"] * 0.85        # prosta / lekki luk - linia bez znaczenia
    if corner and pm.get("inside") is not None and pr.get("inside") is not None:
        di = pm["inside"] - pr["inside"]
        if abs(di) > 1.2:
            add(abs(di) * 0.03, f"w szczycie {abs(di):.1f} m {'dalej od' if di > 0 else 'bliżej'} "
                                f"wewnętrznej krawędzi niż rywal",
                "przyciśnij szczyt bliżej wewnętrznej (tarka)" if di > 0 else
                "nie ścinaj tak mocno — szerszy łuk, późniejszy szczyt")
    reasons.sort(key=lambda r: -r[0])
    return {"name": name, "d0": d0, "d1": d1, "loss": loss, "entry": entry, "exit": exit_,
            "rival": rival, "me": pm, "them": pr, "reasons": reasons}


def short(n, k=16):
    n = (n or "").split("#")[0]
    return n if len(n) <= k else n[:k - 1] + "…"


def plan(a, me, rival=None, segments=None, top=3):
    """Lista porownan odcinkow posortowana od najwiekszej straty.
    rival=None -> na kazdym odcinku najszybszy tam kierowca."""
    segments = segments or a.segments
    out = []
    for name, d0, d1 in segments:
        r = rival
        if r is None:
            cands = [n for n in a.drivers if n != me and n in a.traces]
            if not cands:
                continue
            r = min(cands, key=lambda n: (a.t_at(n, d1) or 1e9) - (a.t_at(n, d0) or 0))
        if r == me:
            continue
        c = compare(a, me, r, name, d0, d1)
        if c and c["loss"] is not None:
            out.append(c)
    out.sort(key=lambda c: -c["loss"])
    return out
