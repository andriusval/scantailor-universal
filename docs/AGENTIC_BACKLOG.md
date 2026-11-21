# Agento workflow: C++ pakeitimų sąrašas

Kas ScanTailor C++ kode pagreitintų ar pagerintų agento workflow (`tools/st-mcp`).
Sudaryta 2026-11-21 po bandymų su Jakšto, Zweig ir Peary knygomis. Anglų k.: [AGENTIC_BACKLOG.en.md](AGENTIC_BACKLOG.en.md).

Siūloma eilė: 1 ir 4, tada 2, tada 5 ir 6, vėliau 7–9.

## Greitis

1. **Tik pasirinkti puslapiai** (`--pages=<id,...>` arba `--images=<file,...>`).
   `run_stages` visada apdoroja visus puslapius. Jakšto knygoje dėl 6 pakeistų
   puslapių 4–5 etapai perdirbo visus 278 (~50 s); su filtru — kelios sekundės.
2. **OpenMP laukimas sukantis.** Dabar `OMP_NUM_THREADS=1`, nes su visomis
   gijomis CLI ~9× lėtesnis. Pirmiausia išbandyti `OMP_WAIT_POLICY=PASSIVE`
   (be C++ pakeitimų); jei nepadės — sutvarkyti `#pragma omp` vietas su per
   mažais ciklais, kad output etapas naudotų visus branduolius.
3. **Neperdaryti jau naujos išvesties.** Patikrinti, ar CLI praleidžia
   puslapius, kurių parametrai nepasikeitė (`--disable-check-output` yra, bet
   ar tikrinimas veikia CLI — nežinoma). Jei ne — įjungti.

## Patikimumas

4. **Klaidos kodas, kai trūksta skenų.** CLI baigia su 0 ir išsaugo sugadintą
   projektą; dabar tai gaudo `st-mcp` `_check_inputs_reachable`.
5. **Struktūrizuota eiga** (`--json-progress`): JSON eilutė kiekvienam
   puslapiui — etapas, content ir page rėmeliai, deskew kampas, klaidos — vietoj
   `--verbose` teksto skaitymo ir XML perskaitymo po kiekvieno paleidimo.
6. **Peržiūros iš ScanTailor** (`--render-preview=<page>:<stage>:<file.png>`).
   `st-mcp` dabar perkuria geometriją Python'e (`geometry.py`); nesutapimas
   reikštų, kad agentas mato ne tą patį, ką GUI.
7. **ScanTailor analizės eksportas** (`--dump-masks=<dir>`): `ContentMask` ir
   teksto kaukė, kad šiukšlių ir puslapių numerių paieška naudotų tą pačią
   kaukę kaip ScanTailor (kaip jau daro `--content-snap`).

## Kokybė (pagerintų ir GUI)

8. **Deskew nuotraukoms.** ScanTailor kampą randa pagal teksto eilutes ir
   nuotraukas pakreipia iki 7,5° (Jakšto `098_2R`, Peary). Agentas taiso
   `picture_skew` (Python); C++ galėtų matuoti pagal nuotraukos kraštus.
9. **Puslapių numeriai ir viršeliai.** `ContentBoxFinder` dažnai praleidžia
   atskirą puslapio numerį ir neranda viršelio ant tamsaus fono. Agentas taiso
   `include_numbers` ir `cover` režimais.

## Jau padaryta (2026-11-21, šaka `agentic/windows-build`)

- Dvigubas paspaudimas content rėmeliui (iš ScanTailor Advanced) ir CLI `--content-snap`.
- Reliatyvūs keliai nuo projekto failo: skaitymas ir saugojimas.
- CLI pats sukuria išvesties katalogą.
- CLI skaito kiekvieno skeno DPI iš failo.
- Relinking dialogas: „Find in folder…“, suvestinė, filtras.
- Windows buildas su Qt 6.12.1 LTS; minimalus Qt 6.4 / 5.15.
