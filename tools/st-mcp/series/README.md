# Serijos skriptai

Skriptai vienai knygų serijai. Jie taiko taisykles iš dviejų projektų, kuriuos žmogus pataisė ranka.
Nė vienas žingsnis nenaudoja OCR. Puslapių vaizdus žiūri agentas (žr. `stamp-agent.md`).
Agentas prilipdo už knygos medianą didesnių puslapių rėmų kraštus prie spausdinto teksto, o bibliotekos ženklus uždengia fill zonomis.

Skriptus leisti iš `tools/st-mcp` su `python3`, kuriame yra `scipy` (`pip install -e .[series]`).
`scantailor-universal-cli` jie randa taip pat kaip st-mcp (`ST_CLI`, build katalogas arba `PATH`).
ISBN brūkšniniam kodui reikia sisteminės `zbar` bibliotekos (Ubuntu: `libzbar0`).
ScanTailor CLI visose sistemose duoda tą patį rezultatą, todėl žingsniai veikia bet kurioje sistemoje.

## Tvarka

1. Kiekvieno knygos katalogo `projektas.ScanTailor` su 1–5 filtrais: st-mcp `project_create` ir `run_stages(1, 5)` arba `st-prepare-books.ps1 -Root <series>` per PowerShell.
2. `python3 series/series_fix.py <series> --skip <book> ...` pakeičia kiekvieną projektą:
   - Automatinį projektą pasilieka kaip `projektas.auto.ScanTailor`. Antras paleidimas pradeda nuo šios kopijos.
   - Viršeliai (pirmas ir paskutinis vaizdas): pasukimas 0, vienas puslapis be pjūvio, 1–5 filtrai iš naujo.
   - Priekinis viršelis: rėmas – viršelio kraštas (randamas ant tamsaus skenerio dangčio), paraštės 0, be match, Color 300.
   - Galinis viršelis: rėmas – ISBN brūkšninis kodas. Paraštės išlaiko kodo vietą viršelyje. Match, kairė ir apačia, Color 300.
   - Kiti puslapiai: vertikalus auto lygiavimas, horizontaliai centre, match, B/W 600.
   - Ataskaita: `<series>/st-review/series-fix.json` (maži puslapiai, žymės), žurnalas `series-fix.log`.
3. `python3 series/review_sheets.py <series>` padaro po vieną vaizdą kiekvienai knygai į `st-review/sheets/`.
   Vaizde – viršeliai, titulinis ir maži puslapiai (rėmas mažesnis nei 70 % medianos). Taisyti st-mcp įrankiais.
4. Dydžio vartai (`stamp-agent.md`, 1 dalis). `python3 series/size_gate.py <series>` pirma kiekvienai knygai paleidžia `pages_fit_text_column`.
   Paskui išvardija teksto puslapius, kurių rėmas lieka didesnis už knygos medianą, pagal `check_pages(kind="size")` ribas.
   - Agentas peržiūri kiekvieną puslapį ir pasirenka tašką ant kraštinio spausdinto teksto toje pusėje, kur yra ženklas.
   - `--snap <points.json>` prilipdo kraštus šiuose taškuose, kaip GUI dvigubas spragtelėjimas.
     Naudojama CLI parinktis `--content-snap`, kaip ir st-mcp `pages_snap_content(points=...)`.
   - Pakeistos knygos įrašomos į `st-review/fit-books.txt`. Tikrai didesnis puslapis (titulinio paveikslas, lentelė, turinys) lieka.
5. Agentas uždengia bibliotekos ženklus spausdintuose 3, 4 ir 17 puslapiuose ir galiniame viršelyje fill zonomis.
   Žr. `stamp-agent.md` 2 dalį ir skriptus `stamp_sheet.py` bei `add_zones.py`.
   Zonos saugomos originalaus vaizdo px, todėl kiekvienas output jas pritaiko, o GUI jas rodo (Output, Fill zones).
6. Output (5–6 filtrai), tik kai žmogus liepia: st-mcp `run_stages(5, 6)`, `series_fix.run_cli` arba `st-prepare-books.ps1 -Root <series> -OutputOnly`.
   - Knygai iš `fit-books.txt` reikia pilno output, nes pakeistas rėmas keičia visos knygos puslapio dydį.
   - Kitoms knygoms užtenka 6 filtro tik puslapiams iš `zones-pages.txt` (`add_zones.py --output`).
   - Valyti tik projekto fill zonomis. Output TIFF failų nekeisti: kitas output juos perrašo ir ženklai grįžta.
7. Po output: peržiūrėti pakeistų puslapių output failus. Jei ženklas liko, pridėti naują zoną (5 žingsnis) ir paleisti output tam puslapiui.

Knyga be viršelio skenuotės: viršelio vaizdą (pvz. iš knygyno svetainės) įdėti į knygos katalogą.
Tada st-mcp `project_add_front_cover(project, image)` prideda jį kaip pirmą puslapį ir paleidžia jam 1–5 etapus.
Knygos puslapių poslinkį `st-review/page-offset.json` nustatyti į 0.

Bendri įrankiai yra st-mcp: `check_pages(kind="size")`, `pages_fit_text_column`, `pages_snap_content(points=...)`,
`page_preview(grid, roi, zones)`, `page_add_fill_zone` ir `project_add_front_cover`. Šie skriptai juos paleidžia visoms serijos knygoms.
