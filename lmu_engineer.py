"""Inzynier wyscigowy na zywo: komunikaty glosowe po kazdym okrazeniu (do jazdy w VR).

Wejscie: zdarzenia [LAP] z record.py (czas, paliwo / energia, zuzycie, czas do konca sesji).
Wyjscie: tekst do powiedzenia + stan dla panelu VR. Glos: Windows SAPI przez jeden staly
proces PowerShell (System.Speech) - bez dodatkowych bibliotek. Polski glos, jesli jest
zainstalowany (Ustawienia > Czas i jezyk > Mowa > Dodaj glosy > Polski), inaczej angielski.
"""

from __future__ import annotations

import math
import os
import statistics
import subprocess
import threading

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Voice:
    def __init__(self, rate: int = 1):
        self.proc = None
        self.lang = "en"
        self.voice = ""
        self.rate = rate
        self._lock = threading.Lock()

    def _start(self):
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v=$s.GetInstalledVoices()|?{$_.Enabled -and $_.VoiceInfo.Culture.Name -like 'pl*'}|select -First 1;"
            "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
            f"$s.Rate={self.rate};"
            "[Console]::Out.WriteLine('VOICE '+$s.Voice.Name+' '+$s.Voice.Culture.Name);[Console]::Out.Flush();"
            "while(($l=[Console]::In.ReadLine()) -ne $null){ if($l -eq 'STOP'){$s.SpeakAsyncCancelAll()} "
            "elseif($l){$s.SpeakAsync($l)|Out-Null} }")
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=NO_WINDOW)
        first = self.proc.stdout.readline().strip()
        if first.startswith("VOICE "):
            parts = first.split()
            self.voice = " ".join(parts[1:-1])
            self.lang = "pl" if parts[-1].lower().startswith("pl") else "en"

    def ensure(self):
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
        return self.lang

    def say(self, text: str):
        if not text:
            return
        try:
            self.ensure()
            self.proc.stdin.write(text.replace("\n", " ") + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self.proc = None

    def stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.close()
                self.proc.terminate()
        except OSError:
            pass
        self.proc = None


def _num(v, nd, lang):
    s = f"{v:.{nd}f}"
    return s.replace(".", ",") if lang == "pl" else s


def _laptime(t, lang):
    """Czas okrazenia po 'wyscigowemu': 1 24 7 (minuta, sekundy, dziesiate)."""
    m = int(t // 60)
    sec = t - m * 60
    whole = int(sec)
    tenth = int(round((sec - whole) * 10)) % 10
    return f"{m} {whole:02d} {tenth}" if m else f"{whole} {tenth}"


class Engineer:
    """Laczy zdarzenia okrazen w stan i komunikaty."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.best = None
        self.fuel_rates: list[float] = []
        self.ve_rates: list[float] = []
        self.state = {}
        self.session = None

    def feed(self, ev: dict, lang: str = "pl", fuel_on=True, time_on=True) -> str:
        if ev.get("session") != self.session:
            self.reset()
            self.session = ev.get("session")
        t = ev.get("time") or 0
        valid_time = t > 0 and not ev.get("out_lap") and not ev.get("in_lap")
        pb = valid_time and not ev.get("invalid") and (self.best is None or t < self.best - 1e-4)
        delta = (t - self.best) if (valid_time and self.best) else None
        if pb:
            self.best = t
        used = ev.get("fuel_used")
        if used and used > 0.05 and not ev.get("out_lap") and not ev.get("in_lap"):
            self.fuel_rates.append(used)
        veu = ev.get("ve_used")
        if veu and veu > 0.0005 and not ev.get("out_lap") and not ev.get("in_lap"):
            self.ve_rates.append(veu)
        fuel, ve = ev.get("fuel") or 0, ev.get("ve") or 0
        use_ve = bool(self.ve_rates) and ve > 0
        rates = self.ve_rates if use_ve else self.fuel_rates
        recent = rates[-5:]
        per = statistics.fmean(recent) if recent else None
        left = (ve if use_ve else fuel) / per if per else None
        # do mety (wyscig na czas): okrazenia z tempa + to, na ktorym skonczy sie czas
        remain = None
        if ev.get("end_et") and ev.get("end_et") > ev.get("et", 0) and self.best:
            avg_t = statistics.fmean([x for x in [t] if x > 0] or [self.best])
            remain = math.floor((ev["end_et"] - ev["et"]) / max(avg_t, self.best)) + 1
        need = (remain * per - (ve if use_ve else fuel)) if (remain and per) else None
        self.state = {"lap": ev.get("lap"), "time": t if t > 0 else None, "best": self.best,
                      "delta": delta, "pb": pb, "fuel": fuel, "ve": ve, "use_ve": use_ve,
                      "per": per, "left": left, "remain": remain, "need": need,
                      "place": ev.get("place"), "session": ev.get("session"),
                      "invalid": ev.get("invalid")}
        return self.message(lang, fuel_on, time_on)

    def message(self, lang="pl", fuel_on=True, time_on=True) -> str:
        s = self.state
        pl = lang == "pl"
        out = []
        if time_on and s.get("time"):
            if s.get("pb"):
                out.append(("Najlepsze okrążenie! " if pl else "Personal best! ") + _laptime(s["time"], lang))
            else:
                out.append(_laptime(s["time"], lang))
                if s.get("delta") is not None and s["delta"] > 0.05:
                    out.append((f"plus {_num(s['delta'], 1, lang)}" if pl else
                                f"plus {_num(s['delta'], 1, lang)}"))
            if s.get("invalid"):
                out.append("okrążenie nieważne" if pl else "lap invalid")
        if fuel_on and s.get("left") is not None:
            unit = "%" if s["use_ve"] else ""
            left = s["left"]
            if left < 1.6:
                out.append(f"Uwaga, paliwo na {_num(left, 1, lang)} okrążenia, zjedź do boksu"
                           if pl else f"Warning, fuel for {_num(left, 1, lang)} laps, box box")
            else:
                out.append(f"paliwo na {_num(left, 1, lang)} okrążenia" if pl else
                           f"fuel for {_num(left, 1, lang)} laps")
            need = s.get("need")
            if need is not None and s.get("session") == "race":
                amount = _num(abs(need), 0 if s["use_ve"] else 1, lang)
                u = (" procent" if pl else " percent") if s["use_ve"] else (" litra" if pl else " litres")
                if need > 0 and s.get("remain"):
                    save = need / s["remain"]
                    out.append(f"do mety brakuje {amount}{u}, oszczędzaj "
                               f"{_num(save, 2 if not s['use_ve'] else 1, lang)} na okrążenie"
                               if pl else f"short {amount}{u} to the finish, save "
                               f"{_num(save, 2, lang)} per lap")
                elif need <= 0:
                    out.append(f"do mety wystarczy, zapas {amount}{u}" if pl else
                               f"fuel to the finish ok, {amount}{u} spare")
        return ". ".join(out) + ("." if out else "")
