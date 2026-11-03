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
        raise FileNotFoundError(f"Missing {summary_path} — run analyze.py first")
    s = json.load(open(summary_path, encoding="utf-8"))
    ref = s.get("reference", "")
    drivers = s.get("drivers", [])
    braking = s.get("braking", [])
    names = [d["name"] for d in drivers]
    focus = find_driver(driver, names) if driver else None

    lines = []
    lines.append("# AI brief — LMU telemetry analysis")
    lines.append("")
    lines.append(f"- Track: **{s.get('track','?')}**")
    lines.append(f"- Session: **{s.get('session','?')}**")
    lines.append(f"- Track length: **{s.get('track_length_m',0):.0f} m**")
    lines.append(f"- Reference: **{ref}** (fastest lap)")
    if focus:
        lines.append(f"- Driver analysed: **{focus}**")
    lines.append("")
    lines.append("## Lap times")
    lines.append("")
    lines.append("| # | Driver | Time | Gap | Avg Δ braking | Avg Δ throttle |")
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
    lines.append("Δ braking: negative = brakes **earlier** (shorter distance), positive = later.")
    lines.append("Δ throttle: negative = gets on the throttle earlier. Rival data in LMU refreshes at "
                 "~5 Hz (0.2 s), so deltas < 10–15 m are approximate.")
    lines.append("")

    lines.append("## Braking zones")
    lines.append("")
    for c in braking:
        lines.append(f"### {c['corner']} — reference start @ {c['ref_start']:.0f} m")
        rows = c.get("drivers", [])
        refrow = next((r for r in rows if r.get("name") == ref), None)
        ref_vmin = refrow.get("min_speed_kmh") if refrow else None
        lines.append("")
        lines.append("| Driver | Braking [m] | Δ brake [m] | End [m] | Throttle [m] | Δ throttle [m] | Vmin [km/h] | Δ Vmin | Max brake |")
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
            lines.append("## Biggest differences to the reference (pre-selection)")
            lines.append("")
            for _, corner, dist, ds, dv, dp in losses[:5]:
                lines.append(
                    f"- **{corner}** @ {dist:.0f} m: Δ braking {fmt(ds, 1)} m, "
                    f"Δ Vmin {fmt(dv, 1)} km/h, Δ throttle {fmt(dp, 1)} m"
                )
            lines.append("")

    lines.append("## Task for the AI")
    lines.append("")
    lines.append("You are an experienced race engineer analysing LMP2 telemetry in Le Mans Ultimate.")
    lines.append("Based **only on the data above**:")
    lines.append("1. Point out at most 5 specific places/time losses (corner, distance, symptom).")
    lines.append("2. For each, give the most likely mechanism (braking too early/too late, "
                 "Vmin too low, back on the throttle too late, too aggressive an entry, etc.).")
    lines.append("3. Suggest 2–3 concrete exercises or changes for the driver being analysed.")
    lines.append("4. Separate certain conclusions from uncertain ones; remember rival data is ~5 Hz, "
                 "so differences < 10–15 m may be noise.")
    lines.append("5. Do not invent numbers or corners that are not in the tables. If data is missing, say so.")
    lines.append("")
    lines.append("Extra files: `traces.csv` (full traces every 2 m), `braking.csv`, `best_laps.csv`.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Telemetry brief for an AI")
    ap.add_argument("analysis_dir", help="folder with summary.json (output of analyze.py)")
    ap.add_argument("--driver", default=None, help="driver to analyse (e.g. Marek)")
    ap.add_argument("--out", default=None, help="output path (default ai_brief.md)")
    args = ap.parse_args(argv)
    d = Path(args.analysis_dir)
    text = build_brief(d, args.driver)
    out = Path(args.out) if args.out else d / "ai_brief.md"
    out.write_text(text, encoding="utf-8")
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
