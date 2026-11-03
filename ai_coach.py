"""Generuje zwarty brief telemetrii dla AI (Claude/ChatGPT) z wynikow analyze.py.

Nie wysyla niczego do internetu. Tworzy plik `ai_brief.md` z liczbami,
tabelami i gotowym promptem. AI dostaje tylko policzone dane, wiec nie musi
zgadywac z surowych probek.

Uzycie:
    python ai_coach.py analysis/20261031_195239_..._quali --driver "Marek"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def fmt_lap(t):
    if t is None:
        return "—"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def fmt(v, nd=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}{suffix}" if isinstance(v, float) else f"{v}{suffix}"


def find_driver(name, names):
    if not name:
        return None
    low = name.lower()
    for n in names:
        if n.lower() == low:
            return n
    for n in names:
        if low in n.lower():
            return n
    return None


def build_brief(analysis_dir: Path, driver: str | None = None) -> str:
    summary_path = analysis_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Brak {summary_path} — najpierw uruchom analyze.py")
    s = json.load(open(summary_path, encoding="utf-8"))
    ref = s.get("reference", "")
    drivers = s.get("drivers", [])
    braking = s.get("braking", [])
    names = [d["name"] for d in drivers]
    focus = find_driver(driver, names) if driver else None

    lines = []
    lines.append("# Brief AI — analiza telemetrii LMU")
    lines.append("")
    lines.append(f"- Tor: **{s.get('track','?')}**")
    lines.append(f"- Sesja: **{s.get('session','?')}**")
    lines.append(f"- Długość toru: **{s.get('track_length_m',0):.0f} m**")
    lines.append(f"- Referencja: **{ref}** (najszybsze okrążenie)")
    if focus:
        lines.append(f"- Analizowany kierowca: **{focus}**")
    lines.append("")
    lines.append("## Czasy okrążeń")
    lines.append("")
    lines.append("| # | Kierowca | Czas | Strata | Śr. Δ hamowania | Śr. Δ gazu |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    for i, d in enumerate(drivers, 1):
        name = f"**{d['name']}**" if d["name"] == focus else d["name"]
        gap = "—" if i == 1 else f"+{d.get('gap_s', 0):.3f} s"
        lines.append(
            f"| {i} | {name} | {fmt_lap(d.get('lap_time_s'))} | {gap} | "
            f"{fmt(d.get('mean_brake_delta_m'), 1, ' m')} | "
            f"{fmt(d.get('mean_pickup_delta_m'), 1, ' m')} |"
        )
    lines.append("")
    lines.append("Δ hamowania: ujemna = hamuje **wcześniej** (mniejszy dystans), dodatnia = później.")
    lines.append("Δ gazu: ujemna = wcześniej wciska gaz. Dane rywali w LMU odświeżają się "
                 "~5 Hz (0,2 s), więc delty < 10–15 m są przybliżone.")
    lines.append("")

    lines.append("## Strefy hamowania")
    lines.append("")
    for c in braking:
        lines.append(f"### {c['corner']} — start referencji @ {c['ref_start']:.0f} m")
        rows = c.get("drivers", [])
        refrow = next((r for r in rows if r.get("name") == ref), None)
        ref_vmin = refrow.get("min_speed_kmh") if refrow else None
        lines.append("")
        lines.append("| Kierowca | Hamowanie [m] | Δ ham. [m] | Koniec [m] | Gaz [m] | Δ gazu [m] | Vmin [km/h] | Δ Vmin | Max ham. |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in rows:
            if not r.get("matched"):
                lines.append(f"| {r['name']} | — | — | — | — | — | — | — | — |")
                continue
            dv = None
            if r.get("min_speed_kmh") is not None and ref_vmin is not None:
                dv = r["min_speed_kmh"] - ref_vmin
            name = f"**{r['name']}**" if r["name"] == focus else r["name"]
            lines.append(
                f"| {name} | {r.get('brake_start', 0):.0f} | "
                f"{fmt(r.get('delta_start'), 1)} | {r.get('brake_end', 0):.0f} | "
                f"{r.get('pickup') if r.get('pickup') is not None else '—'} | "
                f"{fmt(r.get('delta_pickup'), 1)} | "
                f"{r.get('min_speed_kmh', 0):.1f} | {fmt(dv, 1)} | "
                f"{(r.get('max_brake') or 0) * 100:.0f}% |"
            )
        lines.append("")
    # najwazniejsze straty focusa
    if focus:
        losses = []
        for c in braking:
            rows = c.get("drivers", [])
            refrow = next((r for r in rows if r.get("name") == ref), None)
            fr = next((r for r in rows if r.get("name") == focus), None)
            if not refrow or not fr or not fr.get("matched"):
                continue
            dv = None
            if fr.get("min_speed_kmh") is not None and refrow.get("min_speed_kmh") is not None:
                dv = fr["min_speed_kmh"] - refrow["min_speed_kmh"]
            score = abs(fr.get("delta_start") or 0) + (abs(dv) * 5 if dv is not None else 0)
            losses.append((score, c["corner"], c["ref_start"], fr.get("delta_start"), dv,
                           fr.get("delta_pickup")))
        losses.sort(reverse=True)
        if losses:
            lines.append("## Największe różnice względem referencji (wstępna selekcja)")
            lines.append("")
            for _, corner, dist, ds, dv, dp in losses[:5]:
                lines.append(
                    f"- **{corner}** @ {dist:.0f} m: Δ hamowania {fmt(ds, 1)} m, "
                    f"Δ Vmin {fmt(dv, 1)} km/h, Δ gazu {fmt(dp, 1)} m"
                )
            lines.append("")

    lines.append("## Zadanie dla AI")
    lines.append("")
    lines.append("Jesteś doświadczonym inżynierem wyścigowym analizującym telemetrię LMP2 w Le Mans Ultimate.")
    lines.append("Na podstawie **tylko powyższych danych**:")
    lines.append("1. Wskaż maksymalnie 5 konkretnych miejsc/strat czasu (zakręt, dystans, objaw).")
    lines.append("2. Dla każdego podaj najbardziej prawdopodobny mechanizm (za wczesne/za późne hamowanie, "
                 "za niska Vmin, za późny powrót na gaz, zbyt agresywne wejście itd.).")
    lines.append("3. Zaproponuj 2–3 konkretne ćwiczenia lub zmiany dla analizowanego kierowcy.")
    lines.append("4. Oddziel wnioski pewne od niepewnych; pamiętaj, że dane rywali są ~5 Hz, "
                 "więc różnice < 10–15 m mogą być szumem.")
    lines.append("5. Nie wymyślaj liczb ani zakrętów spoza tabel. Jeśli brakuje danych — napisz to.")
    lines.append("")
    lines.append("Dodatkowe pliki: `traces.csv` (pełne przebiegi co 2 m), `braking.csv`, `best_laps.csv`.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Brief telemetrii dla AI")
    ap.add_argument("analysis_dir", help="katalog z summary.json (wynik analyze.py)")
    ap.add_argument("--driver", default=None, help="kierowca do analizy (np. Marek)")
    ap.add_argument("--out", default=None, help="sciezka wyjsciowa (domyslnie ai_brief.md)")
    args = ap.parse_args(argv)
    d = Path(args.analysis_dir)
    text = build_brief(d, args.driver)
    out = Path(args.out) if args.out else d / "ai_brief.md"
    out.write_text(text, encoding="utf-8")
    print(f"zapisano: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
