# LMU LoadTelemetry

[English](README.md) · **Polski**

> **Uwaga:** interfejs aplikacji, raport HTML i brief AI są teraz po angielsku. Nazwy
> przycisków i zakładek w tym opisie pochodzą z wcześniejszej, polskiej wersji — aktualne
> nazwy są w [README po angielsku](README.md).

## 0. Aplikacja offline (najprościej)

Kliknij dwuklikiem **`START_LMU_TELEMETRY.cmd`** (albo `pythonw lmu_app.py`).
Aplikacja jest w całości lokalna — nie wymaga internetu.

Wszystko jest w **jednym oknie** — wyniki przeglądasz w aplikacji, bez przeglądarki:

- **Pasek nagrywania (góra)** — `● Nagrywaj` / `■ Stop` (albo **F9**), licznik czasu,
  stan na żywo (tor, sesja, liczba aut, najlepszy czas, rozmiar). Z opcją
  „po stopie analizuj i pokaż” nagranie jest od razu analizowane i otwiera się po prawej.
  **Test połączenia** = 10-sekundowa diagnostyka LMU (wynik w logu).
- **Biblioteka (lewa strona)** — tory i ich sesje (najlepszy czas, Twój czas i pozycja).
  Niebieskie = jeszcze bez analizy (dwuklik / Enter analizuje). **Wiersz toru** = analiza
  ze wszystkich sesji z tego toru. Szukanie po torze lub kierowcy, filtr typu sesji,
  „Analizuj nowe” przetwarza wszystko naraz. Prawy przycisk = menu.
- **Podgląd (prawa strona)**:
  - **Wyniki** — ranking, strata, średnie Δ hamowania / gazu; ☐/☑ wybiera kierowców do wykresów,
  - **Wykresy i mapa** — **delta czasu** (Porównaj X vs Y: gdzie zyskujesz/tracisz),
    stały pasek u góry z czasem obu kierowców w punkcie kursora (klik na wykresie = przypnij
    kursor, klik w pinezkę = odepnij) i szczegółami odcinka: czas, prędkość wejścia / min. /
    wyjścia, o ile metrów wcześniej/później hamujesz i wciskasz pełny gaz,
  - **Odcinki** — pasek nad wykresami + kafelki `H1 +0.08` (kolor = wielkość straty/zysku,
    klik = zoom, najechanie = pokaż miejsce); podział: od hamowania, zakręty (od połowy prostej
    do połowy prostej), co 200 / 100 / 50 m; sortowanie po stracie/zysku; ◀ ▶ lub strzałki =
    następny odcinek; przeciągnięcie dowolnego zakresu pokazuje jego deltę (własny odcinek),
    prędkość / gaz / hamulec / bieg po dystansie + mapa toru (zielony = zysk,
    czerwony = strata); **⛶ Pełny ekran** (F11, Esc wraca) pokazuje same wykresy i mapę; przeciągnij lub
    kółko = zoom, prawy przycisk = przesuń, dwuklik = cały tor, kursor pokazuje wartości
    i pozycję aut na mapie; klik na mapie przybliża to miejsce,
  - **Klasy i auta** — najlepsze czasy Twoje i rywali osobno dla każdej klasy (Hypercar / LMP2 /
    GT3…), opcjonalnie pogrupowane po aucie/zespole; na wierszu toru zbiorczo ze wszystkich
    sesji. Strata do lidera klasy (s i %), sektory najlepszego okrążenia, idealne okrążenie
    (suma najlepszych sektorów), liczba okrążeń. Dwuklik = porównaj na wykresach.
    Δ S1/S2/S3 = w którym sektorze ile tracisz (do lidera klasy, do najlepszych sektorów
    klasy albo „mnie (Ty)”: + = tyle tracisz do danego kierowcy; ▲ = największa strata).
    Widok sektorów: czas + strata w jednej komórce / tylko strata / tylko czasy. Sektory i model auta biorę też z plików wyników LMU
    (`UserData/Log/Results/*.xml`, dopasowanie po czasie startu sesji) — działa też dla
    starszych nagrań.
    „Analiza porównuje: moja klasa” (domyślnie) sprawia, że analiza sesji i toru bierze
    rywali tylko z Twojej klasy — GT3 nie jest porównywane z Hypercarami,
  - **Setup** — rejestrator kopiuje setup, którym jedziesz (`UserData/player/TempModFile.svm`),
    do nagrania (`*.setupN.svm`) na starcie i przy każdej zmianie w trakcie sesji; ★ = setup
    z Twojego najlepszego okrążenia. Nazwa rozpoznawana przez porównanie z `Settings/<tor>`
    („nazwa + N zmian”, gdy zmodyfikowany). Porównanie z setupem z innej sesji albo zapisanym
    plikiem (różnice podświetlone, „tylko różnice”). Do starych nagrań: „Przypisz setup z
    pliku…”. Na wierszu toru: Twoje sesje + setup z najlepszego okrążenia,
  - **Prowadzenie** — z pliku `*.chassis.csv.gz` (podwozie Twojego auta, 100 Hz: naciski kół,
    ugięcie zawieszenia, prześwit, kąty poślizgu opon, temperatury, przeciążenia). Na każdym
    zakręcie (wykrywanym z krzywizny toru) osobno wejście / środek / wyjście: balans = poślizg
    opon przód − tył (+ podsterowność, − nadsterowność), nacisk na przód, LLTD przód (podział
    bocznego transferu masy między osie), na zewnętrzne koła, min. prześwit, czas fazy +
    podpowiedź ustawień. Karty: balans średnio, opony (wewn./środek/zewn., ciśnienie, camber),
    prześwit (minimum, przy V max, rake, dobicia). „Porównaj z sesją” = ten sam tor z innym
    setupem: Δ balans i Δ czas na każdym zakręcie. Średnia z 3 najszybszych okrążeń. Tylko
    nowe nagrania (stare nie mają danych podwozia), tylko Twoje auto (rywale ich nie wysyłają),
  - **Paliwo** — zużycie Twojego auta na każde okrążenie z nagrań (wyjazdowe / pomiarowe /
    zjazdowe, stinty, ostrzeżenie „zabrakło paliwa”) + kalkulator: kwalifikacje (wyjazd +
    N pomiarowych + zjazd), wyścig na czas (okrążenia z Twojego tempa + to, na którym kończy
    się czas) albo na okrążenia, okrążenie formujące (zmierzone z 1. okrążenia wyścigu), zapas.
    Domyślnie maksymalne zużycie ze wszystkich sesji tym autem na torze. Gdy sesja ma setup,
    pokazuje też, ile L/okr. zakłada gra i o ile się myli. „Zapisz setup z tym paliwem” tworzy
    kopię wybranego setupu z ustawionym paliwem (LMP2/LMP3: `FuelSetting`, litry = indeks + 5;
    Hypercar/GT3: `VirtualEnergySetting` w %) w `Settings/<tor>` — w grze: Garaż → Setup → Load,
  - **VR: inżynier głosowy + Panel VR** — podczas nagrywania rejestrator wysyła po każdym
    Twoim okrążeniu zdarzenie `[LAP]`. Przełącznik „inżynier głosowy” czyta: czas okrążenia,
    różnicę do najlepszego, na ile okrążeń starczy paliwa/energii i — w wyścigu na czas — ile
    brakuje do mety i ile oszczędzać na okrążenie. Głos: Windows (System.Speech); polski, jeśli
    zainstalowany (Ustawienia → Czas i język → Mowa → Dodaj głosy → Polski), inaczej angielski.
    „Panel VR” = czarne okno z dużymi wartościami do podpięcia w goglach przez OpenKneeboard
    (karta „Window capture” → okno „LMU Panel VR”),
  - **Porównanie linii w zakręcie** — po przybliżeniu mapa w ciemnym stylu: Twoja linia
    kolorowana hamulec / toczenie / pełny gaz, rywal biała przerywana, znaczniki ▲ hamowanie,
    ● szczyt (min. prędkość), ● pełny gaz z opisem różnic, auta przy kursorze,
  - **Plan treningu** — 3 (5 / wszystkie) odcinki, gdzie tracisz najwięcej, ze stratą na wejściu
    i wyjściu oraz przyczynami po polsku i wskazówką: punkt i siła hamowania, puszczanie
    hamulca (trail braking), toczenie bez gazu i hamulca, prędkość w szczycie, pełny gaz,
    prędkość wyjścia, linia w szczycie. Porównanie z wybranym kierowcą albo z najszybszym na
    każdym odcinku; „pokaż na wykresie” przybliża zakręt; lista miejsc, gdzie jesteś szybszy,
  - **Import z gry** — wbudowana telemetria LMU (Ustawienia → Sterowanie → Rozgrywka →
    Telemetry Recording) zapisuje każdą sesję do `UserData/Telemetry/*.duckdb` (Twoje auto +
    setup). Przycisk „Import z gry (n)” zamienia nowe pliki na nagrania aplikacji (sesja „z gry”,
    setup automatycznie, pozycja GPS dopasowana do mapy z wcześniejszego nagrania toru) i od
    razu je analizuje. Działa też dla sesji, kiedy aplikacja nie nagrywała. Pliki w trakcie
    zapisu (sesja trwa) są pomijane, tak samo pliki bez pełnego okrążenia i sesje, które
    nagranie aplikacji pokrywa w ≥90% czasu (bez duplikatu). Gdy aplikacja nagrywała krócej niż
    gra, plik gry jest importowany — ma okrążenia, których aplikacja nie złapała. Rywale:
    plik gry ma tylko Twoje auto, ale „Klasy i auta” dokłada wszystkich kierowców z pliku
    wyników LMU (najlepszy czas, sektory, model auta); ich wykresów/linii nie ma. Wymaga `python -m pip install duckdb`. Format plików
    i tabela kluczy setupu: LMU Telemetry Lab (MIT),
  - **Linia przejazdu** — panel „LINIA” pokazuje, gdzie każdy kierowca był między krawędziami
    toru (góra = lewa, dół = prawa; krawędzie z `mTrackEdge` wszystkich aut w nagraniu).
    Po przybliżeniu odcinka mapa pokazuje ten fragment z asfaltem i prawdziwymi liniami aut.
    Czarna przerywana = **optimum z danych** (na każdym odcinku linia najszybszego tam kierowcy).
    Linii z asysty gry nie da się odczytać — pliki torów LMU (`.mas`) są zaszyfrowane.
    Starsze analizy potrzebują „Analizuj ponownie”, żeby mieć linię i krawędzie.
  - **Strefy hamowania** — H1…Hn dla zaznaczonych kierowców (dwuklik = zoom na zakręt),
  - **Brief AI** — gotowy tekst do skopiowania do Claude/ChatGPT.
- **Raport HTML** otwiera pełny raport w przeglądarce, **TinyPedal** eksportuje okrążenie
  zaznaczonego kierowcy (albo Twoje).

Ustawienia (kierowca, filtr rywali, opcje) zapisują się w `lmu_app_settings.json`.
Raporty sesji trafiają do `analysis/<nazwa pliku>/`, raport toru do `analysis/track_<tor>/`.

### Filtr toru

- **szybsi lub szybszy sektor** (domyślnie) — kierowca wchodzi, jeśli ma lepsze
  okrążenie od wybranego Twojego okrążenia **lub** któryś sektor szybszy
  o więcej niż tolerancja (domyślnie 0,15 s).
- **tylko szybsi** — wyłącznie lepsze okrążenie.
- **wszyscy** — bez filtra.

Raport toru dokłada też **Twoje najlepsze okrążenie z każdej innej sesji** (do 3, linie
„Ty · Q 31.10 21:32”; T = trening, Q = kwalifikacje, W = wyścig), także z sesji importowanych
z gry — na wykresach i mapie widać, co zmieniłeś między sesjami. Okrążenia krótsze od rekordu
kierowcy wg gry (przerwany wyjazd, dystans z poprzedniego okrążenia tuż po linii) są odrzucane.

Sektory dla starszych nagrań są wyliczane z telemetrii (0/1/2) z dokładnością
~0,2 s (5 Hz). Nowe nagrania zapisują też sektory ze scoringu LMU.

W raporcie HTML:

- wybór kierowcy (lista „Kierowca”) i checkboxy do porównania wielu,
- **zoom**: przeciągnij po wykresie, `+`/`−`, dwuklik lub **Reset zoom**,
- mapa toru z markerami `H1…Hn`, checkbox **Pokaż linie zaznaczonych kierowców**,
- tooltipy, tryb jasny/ciemny, tabele i eksport CSV.

---

Odczyt telemetrii **wszystkich aut** z Le Mans Ultimate: gaz, hamulec, kierownica,
bieg, RPM, prędkość, pozycja X/Z, okrążenie i dystans. Celem jest porównanie
najszybszych kierowców z kwalifikacji — kiedy hamują, kiedy wracają na gaz i gdzie
na torze to robią.

> **Status:** zweryfikowane na żywym LMU (online): shared memory działa,
> widać wszystkie auta (9–40), a **rywale `ctrl=2` mają realne, zmienne gaz/hamulec**.
> Nagrano i przeanalizowano practice, kwalifikacje i wyścig na Paul Ricard.
> Aplikacja offline, raporty HTML i brief AI działają. Do sprawdzenia zostaje
> odtwarzanie replaya (`ctrl=3`) — nie było testowane.

---

## 1. Co działa, a co jest do potwierdzenia

**Działa / zweryfikowane**

- Odczyt oficjalnej pamięci współdzielonej LMU `LMU_Data` (bez pluginów zewnętrznych).
- Tablica `SharedMemoryTelemetryData.telemInfo[104]` zawiera telemetrię **wszystkich
  aktywnych pojazdów**, nie tylko gracza.
- Na prawdziwym zrzucie z Barcelony (sesja z 44 autami) każdy pojazd miał własne
  `throttle`, `brake`, `steering`, `gear`, `rpm`, pozycję i dystans.
- Logger zapisuje wszystkie auta do `csv.gz`; analiza i raport HTML działają.
- **Na żywo w online**: 9–40 aut; rywale `ctrl=2` mają realne, zmienne
  `gaz/hamulec` (potwierdzone probe'em i nagraniami quali/wyścigu na Paul Ricard).
- Demo bez gry: `make_demo.py` + `analyze.py` (dane syntetyczne, oznaczone w raporcie).
- Aplikacja offline (`lmu_app.py`) i analiza po torze (`track_analysis.py`) działają.

**Do potwierdzenia**

- Czy podczas **odtwarzania replaya** tablica zawiera wszystkie auta (`mControl == 3`).
  HUD replaya LMU pokazuje inputy każdego auta, więc jest to bardzo prawdopodobne,
  ale nie testowałem tego na żywo.
- Dokładność rywali: shared memory odświeża ich dane ~5 Hz (0,2 s, ~10–15 m przy
  dużej prędkości). Delty mniejsze niż ~10–15 m traktuj jako przybliżone.
- Jeśli online/replay pokaże tylko obserwowane auto — trzeba nagrywać po jednym
  aucie, przełączając kamerę (patrz sekcja 5).

**Odrzucona ścieżka**

- Bezpośredni parser `.Vcr` offline: format jest binarny, nieudokumentowany i inny
  niż klasyczny rF2 VCR (gMotor/network log). Nie znalazłem gotowego parsera dla LMU.
  Pewna droga to **replay w grze + `record.py`**.

---

## 2. Wymagania

- Windows + Python 3.11+ (wystarczy czysty stdlib — **żadnych `pip install`**).
- LMU zainstalowane i uruchamiane **bez** działającej atrapy `lmu-spoof`.
- W LMU: `Settings → Gameplay → Enable external plugins` = włączone
  (u Ciebie w `Settings.JSON` jest już `true`).

> **Uwaga o atrapie:** `C:\soa1\local\lmu-spoof\fake\Le Mans Ultimate.exe` trzyma
> nazwę procesu, port 6397 i `LMU_Data`. Przed odpaleniem prawdziwego LMU zamknij
> spoof/GUI, inaczej gra nie wystartuje albo będzie konflikt o pamięć/port.

---

## 3. Szybki start — kwalifikacje na żywo

```powershell
cd C:\soa1\local\LMU-LoadTelemetry

# 1. diagnostyka: czy widać wszystkich i czy inputy się zmieniają?
python record.py --probe-seconds 10

# 2. nagrywanie całej sesji (Ctrl+C kończy)
python record.py --out records --hz 30

# 3. po sesji — analiza
python analyze.py records/*.csv.gz --out analysis --top 6

# 4. otwórz raport
start analysis\report.html
```

W trybie `--probe-seconds` zobaczysz tabelę:

```
id   ctrl  kierowca          gaz min/max   ham min/max   okr   best
 5      2  Jan Kowalski      0.00/1.00     0.00/0.97      12   1:34.512
```

- `ctrl`: 0 = gracz, 1 = AI, 2 = remote (online), 3 = replay.
- Kolumna `gaz min/max` / `ham min/max` pokazuje, czy wartości się zmieniają.
  Jeśli wszyscy oprócz jednego auta mają `0.00/0.00`, to w tej sesji tablica
  zawiera tylko auto obserwowane — patrz sekcja 5.

---

## 4. Replay (bez jazdy) — jak najdokładniej

Plików `.Vcr` nie da się czytać bezpośrednio (format binarny, nieudokumentowany),
więc replay **odtwarza się w grze, a aplikacja go nagrywa** jak żywą sesję:

1. W aplikacji ustaw **Hz: 100** (pasek nagrywania).
2. W LMU: **Replay** → wybierz sesję (np. kwalifikacje).
3. Przewiń na **początek** sesji, kliknij **● Nagrywaj** (F9) i puść replay w **1×**.
4. **Nie przewijaj w trakcie** (cofanie miesza okrążenia) i nie używaj przyspieszenia.
   Wystarczy, że przejedzie najszybsze okrążenie interesujących Cię kierowców.
5. **Stop** — przy włączonym „po stopie analizuj i pokaż” wynik od razu pojawi się po prawej.

Kolumna **Dane** w Wynikach pokazuje, ile próbek/s faktycznie ma okrążenie każdego
kierowcy — tak sprawdzisz, czy replay daje rywali dokładniej niż online (~5 Hz).
Czy LMU w replayu odświeża wszystkie auta częściej niż 5 Hz, nie było jeszcze sprawdzane.

> Do 31.10.2026 rejestrator zapisywał klatkę tylko przy zmianie czasu scoringu (~5 Hz),
> więc nawet własne auto miało 5 próbek/s. Teraz zapisuje każdą nową klatkę telemetrii
> (własne auto do 100 Hz). Starsze nagrania mają 5 Hz dla wszystkich.

## 5. Gdy inputy rywali są zerowe

Kolejność działań:

1. Sprawdź `--probe-seconds` w sesji online — czy `ctrl=2` ma zmienne `gaz/ham`.
2. Jeśli nie, użyj **replaya** kwalifikacji (tam gra odtwarza wszystkie auta lokalnie).
3. Jeśli replay też pokazuje tylko obserwowane auto:
   - nagrywaj po jednym kierowcy, przełączając kamerę (środkowy przycisk myszy
     w replayu pokazuje HUD inputów),
   - albo użyj wbudowanego HUD replaya do ręcznego odczytu (bez eksportu).
4. Wbudowana telemetria LMU (`.duckdb` w `UserData/Telemetry`) — według narzędzi
   czytających te pliki zawiera metadane jednego `DriverName`, czyli raczej tylko
   własne auto; nie nadaje się do rywali.

---

## 6. Co produkuje analiza

Po `python analyze.py records/plik.csv.gz --out analysis`:

| Plik | Zawartość |
|---|---|
| `analysis/report.html` | interaktywny raport: hamulec/gaz/prędkość, mapa toru, strefy hamowania, tabele |
| `analysis/best_laps.csv` | najlepsze okrążenie każdego kierowcy, strata, pokrycie |
| `analysis/braking.csv` | strefy hamowania: początek, koniec, moment gazu, Vmin, max hamulec, delty |
| `analysis/traces.csv` | przeskalowane przebiegi co `--grid` metrów (Excel / MoTeC / własne skrypty) |
| `analysis/summary.json` | podsumowanie maszynowe (kierowcy, strefy, delty) |

Raport:

- **Hamulec / Gaz / Prędkość** — przebiegi po dystansie; kolor = kierowca.
- **Mapa toru** — linia referencji pokolorowana siłą hamowania, markery `H1…Hn`.
- **Strefy hamowania** — tabela: gdzie hamuje każdy kierowca i o ile metrów
  wcześniej/później niż referencja; `Δ` hamowania ujemna = hamuje **wcześniej** (mniejszy dystans),
dodatnia = później; `Δ` gazu ujemna = wcześniej wciska gaz. Przy danych rywali ~5 Hz
(0,2 s) delty mniejsze niż ~10–15 m traktuj jako przybliżone.
- **Najlepsze okrążenia** — czasy i średnie delty.
- **Tabela danych** — wartości co 100 m (widok tabelaryczny wykresów).
- Tryb jasny/ciemny, tooltipy, legenda z filtrowaniem kierowców.

Czas okrążenia: jeśli w danych jest `mBestLapTime`, analiza używa dokładnego czasu
z gry; w przeciwnym razie różnicy `lap_start_et` (z dokładnością do próbki).
Jeśli `lap_start_et` jest zerowe (częste dla zdalnych aut), analiza wykrywa
okrążenia po `mLapNumber` i zawinięciu `lap_dist`.

---

## 7. Pliki w projekcie

| Plik | Rola |
|---|---|
| `lmu_shm.py` | mapowanie struktur LMU + czytnik `LMU_Data` (offline dump i live) |
| `record.py` | logger wszystkich aut do `records/*.csv.gz`; tryb `--probe-seconds` |
| `analyze.py` | wykrywanie okrążeń, stref hamowania, delty, CSV + HTML |
| `report_template.html` | szablon raportu (bez zewnętrznych bibliotek/CDN) |
| `make_demo.py` | syntetyczna sesja do testów bez gry |
| `lmu_app.py` | aplikacja desktopowa (nagrywanie, biblioteka sesji, podgląd wyników, brief AI) |
| `lmu_theme.py` | wygląd w stylu iOS (zaokrąglone karty, kapsuły, przełączniki) |
| `lmu_gamerec.py` | import nagrań wbudowanej telemetrii LMU (DuckDB) z setupem |
| `lmu_coach.py` | plan treningu: diagnoza strat w zakrętach i wskazówki |
| `lmu_engineer.py` | inżynier wyścigowy na żywo: komunikaty po okrążeniu, głos Windows (VR) |
| `lmu_fuel.py` | zużycie paliwa / energii na okrążenie, stinty, kalkulator tankowania |
| `lmu_handling.py` | analiza prowadzenia: zakręty z krzywizny, balans z poślizgu opon, transfer masy, opony, prześwit |
| `lmu_setup.py` | setupy LMU: odnalezienie folderu gry, parser `.svm`, rozpoznanie nazwy, porównanie |
| `lmu_results.py` | pliki wyników LMU (XML): okrążenia i sektory wszystkich kierowców, model auta |
| `lmu_classes.py` | najlepsze czasy per klasa / auto z nagrań (cache w `analysis/_best`) |
| `lmu_view.py` | wykresy i mapa toru w aplikacji (tkinter Canvas) |
| `ai_coach.py` | generator briefu dla AI (`ai_brief.md`) |
| `track_analysis.py` | analiza po torze: 3 najlepsze okrążenia + filtr szybszych/sektorów |
| `to_tinypedal.py` | eksport najlepszych okrążeń do TinyPedal (`.tplap`) |
| `START_LMU_TELEMETRY.cmd` | dwuklik uruchamia aplikację |
| `records_demo/`, `analysis_demo/` | przykład/demo (dane **syntetyczne**) |

---

## 8. Szczegóły techniczne (dla przyszłych aktualizacji LMU)

LMU ma własny interfejs pamięci współdzielonej, opisany w:

```
<LMU>/Support/SharedMemoryInterface/SharedMemoryInterface.hpp
<LMU>/Support/SharedMemoryInterface/InternalsPlugin.hpp
```

Kluczowa struktura:

```cpp
struct SharedMemoryTelemetryData {
    uint8_t activeVehicles;
    uint8_t playerVehicleIdx;
    bool playerHasVehicle;
    TelemInfoV01 telemInfo[104];   // wszystkie aktywne auta
};
```

Zweryfikowane empirycznie dla **LMU 1.4200** (zrzut z 44 autami):

- `SharedMemoryObjectOut` = 324 820 B (mapowanie gry ma 327 680 B).
- `ScoringInfoV01` = 552 B; `vehScoringInfo` startuje pod `scoring + 560`,
  krok 584 B.
- `telemInfo` startuje pod `telemetry + 4`, krok 1888 B.
- `mID` w `TelemInfoV01` i `VehicleScoringInfoV01` to 4 B (mimo `long` w nagłówku).
- Pola wejściowe: `mUnfilteredThrottle`, `mUnfilteredBrake`, `mUnfilteredSteering`,
  `mGear`, `mEngineRPM`, `mLocalVel`, `mPos`, `mLapNumber`, `mLapStartET`,
  `mElapsedTime`; scoring: `mLapDist`, `mPathLateral`, `mTrackEdge`, `mBestLapTime`,
  `mLastLapTime`, `mDriverName`, `mControl`.
- Eventy `LMU_Data_HoldEvent` / `LMU_Data_DataEvent`; czytnik czeka na nową klatkę
  i wykonuje podwójny odczyt (spójność), bo `Interlocked*` są intrinsics i nie są
  eksportowane z `kernel32` na Win64.
- `Plugin Mask` = 4 w `Settings.JSON` (domyślnie 100 Hz). Logger domyślnie próbkuje
  30 Hz i pomija powtórzone klatki w pauzie.

Jeśli LMU po aktualizacji zmieni układ struktur, zweryfikuj offsety na nowym
zrzucie `LMU_Data` (np. `python lmu_shm.py plik.bin`).

---

## 9. Rozwiązywanie problemów

| Objaw | Przyczyna / rozwiązanie |
|---|---|
| `Nie moge otworzyc 'LMU_Data'` | LMU nie działa albo `Enable external plugins` jest wyłączone |
| `active=0` | menu/monitor/garage — wejdź na tor lub odtwórz replay |
| Wszystkie `gaz/ham` = 0 | sesja w menu; albo wersja eksponuje tylko obserwowane auto (sekcja 5) |
| Gra nie startuje | działa `lmu-spoof` (fałszywy proces, port 6397, `LMU_Data`) — zamknij go |
| `EAC Failed to get anti cheat handle` w `trace.txt` | ostrzeżenie EAC; ostatnia sesja i tak zakończyła się czysto |
| Pliki za duże | `--hz 20`, `--driver "nazwa"`, krótsze sesje |
| Brak stref hamowania | zbyt łagodne/rzadkie dane; spróbuj `--grid 1` i dłuższego nagrania |
| Krzaki w nazwach (`S�bastien`) | plik CSV jest UTF-8; otwieraj w edytorze/Excelu z UTF-8, nie CP1252 |

---

## 10. Analiza przez AI

`python ai_coach.py analysis/<sesja> --driver "Marek"` (lub `analysis/track_<tor>`) tworzy `ai_brief.md`:
tabele czasów, strefy hamowania, delty i gotowy prompt dla AI.

- AI dostaje **policzone liczby**, nie surowe 2 mln próbek — dzięki temu wnioski
  są oparte na danych, a nie zgadywane.
- Prompt wymaga wskazania konkretnych zakrętów/dystansów i oddzielenia pewnych
  wniosków od niepewnych (dane rywali ~5 Hz).
- Aplikacja ma przycisk **Brief AI** z kopiowaniem do schowka.
- Opcjonalne wysłanie do Claude API można dodać, jeśli masz `ANTHROPIC_API_KEY`;
  domyślnie wszystko zostaje lokalnie.

## 10a. Eksport do TinyPedal (referencja w delcie i widgecie)

`to_tinypedal.py` zamienia najlepsze okrążenie z nagrania na plik TinyPedal
Best Lap (`.tplap`): tor, klasa, czas, kierowca, auto, delta (czas vs dystans)
i telemetria co 5 m (gaz, hamulec, prędkość, kierownica, bieg).

```
python to_tinypedal.py records/plik.csv.gz                       # Twoje najlepsze okrążenie
python to_tinypedal.py records/plik.csv.gz --driver "Bacon"      # okrążenie rywala
python to_tinypedal.py records/plik.csv.gz --class LMP2_ELMS --top 3
```

Pliki trafiają do `tinypedal_laps/`. W TinyPedal: **Tools → Best Lap Manager →
Import**. Wczytane okrążenie staje się referencją dla delty i widgetu
**Best lap telemetry** (gaz/hamulec/prędkość) dla tego toru i klasy.
Twój własny rekord jest zawsze zachowany (`deltabest\personal_best`) i dalej
aktualizowany, gdy go poprawisz; wraca przyciskiem **Restore Own**. Jeśli
pobijesz wczytane okrążenie, Twoje automatycznie staje się referencją.

Uwagi:
- Rywale są próbkowani ~5 Hz (co ~10–15 m), więc punkty hamowania są
  przybliżone (interpolacja liniowa); Twoje auto zależy od `--hz` nagrywania.
- Klasa musi się zgadzać z tą w grze (`mVehicleClass`), inaczej TinyPedal
  nie dopasuje okrążenia do toru i klasy.
- Wymaga TinyPedal z gałęzią `feature/best-lap-telemetry` (Best Lap Manager).

## 11. Fair play / bezpieczeństwo

- Narzędzie **tylko czyta** oficjalną pamięć współdzieloną LMU. Nie wstrzykuje kodu,
  nie modyfikuje gry, nie obchodzi EAC.
- Dane rywali są widoczne w replayu/HUD samej gry; używaj ich do analizy **po sesji**,
  a nie jako live-coach w trakcie wyścigu — regulaminy online mogą tego zabraniać.
- Nie publikuj cudzych nazwisk/SteamID bez potrzeby.

---

## 12. Demo bez gry

```powershell
python make_demo.py --out records_demo
python analyze.py records_demo/*.csv.gz --out analysis_demo
start analysis_demo\report.html
```

Demo jest **syntetyczne** — raport ma żółty banner „DANE SYNTETYCZNE”.
Służy do sprawdzenia wykresów, tabel i delty bez uruchamiania LMU.
