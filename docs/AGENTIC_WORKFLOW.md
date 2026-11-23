# ScanTailor agentinis workflow (MCP)

*English version: [AGENTIC_WORKFLOW.en.md](AGENTIC_WORKFLOW.en.md) (ją agentas gauna per `workflow_guide`).*

Tikslas: agentas atlieka tą patį darbą, kurį žmogus daro ScanTailor GUI, tik per
įrankius (kiekvienas yra ir CLI komanda, ir MCP tool), o žmogus patvirtina tik
tai, kas iš tikrųjų reikalauja sprendimo.

Principai (perimti iš craft-anything):
- **Vienas variklis.** GUI, CLI ir MCP naudoja tą patį `src/core` ir tą patį
  `.ScanTailor` projekto XML. Agentas gali sustoti, o žmogus atsidaryti projektą
  GUI ir tęsti (ir atvirkščiai).
- **Tikrinama, ne spėjama.** Kiekvienas sprendimas remiasi peržiūros paveikslėliu
  arba skaičiais iš projekto, ne prielaidomis.
- **Smulkūs įrankiai.** Ne „apdorok knygą“, o atskiri etapai ir atskiri
  puslapiai, kad agentas galėtų taisyti vietoje.

## Rankinis workflow → agento žingsniai

| # | Žmogus GUI | Agentas | Įrankiai |
|---|---|---|---|
| 0 | Naujas projektas, sudedami JPG, *Fix DPI → 300* | Nuskaito DPI iš failų; jei nėra — 300 ir praneša | `project_create` |
| 1 | (GUI to neprašo, bet kai kurios knygos skenuotos pasuktos) | Peržiūri 2–3 originalus dėl atvartų; kiekvieno skeno pasukimą randa detektorius pagal tekstą, pažymėtus skenus agentas peržiūri | `image_preview`, `run_stages(1, 5, orientation=auto, layout=…)`, `check_pages(kind=orientation)` |
| 2 | *Margins → Play* | Paleidžia filtrus 1–5 | `run_stages`, `job_status` |
| 3 | *Select Content*, rūšiuoja pagal **Width**, žiūri apačią (plačiausi) — content box dažnai apima šiukšles; mažina iki spausdinto teksto | Ima išskirtis pagal plotį (robust z-score), peržiūri su content box overlay, taiso | `pages_list(sort_by=z_width, descending=true)`, `pages_preview`, `page_set_content` |
| 4 | Tas pats, žiūri **siauriausius** | Tas pats kitam sąrašo galui (nepilnas turinys, tušti puslapiai) | tie patys |
| 5 | Rūšiuoja pagal **Height** — ar paimti puslapių numeriai apačioje | Ieško rašalo už content box ribų (virš/po); peržiūri ir praplečia | `check_pages(kind=content)`, `page_set_content(expand_bottom=…)` |
| 6 | *Margins*: kairė/dešinė 10 mm, viršus/apačia 5 mm → *Apply → All pages*; lygiavimas pagal puslapio numerio vietą (viršus/apačia) | Numerių vietą ima iš 5 žingsnio „balsavimo“; taiko visiems | `pages_set_layout(all, margin_left=10, margin_right=10, margin_top=5, margin_bottom=5, align_v=top\|bottom)` |
| 7 | Rūšiuoja pagal aukštį, tikrina aukščiausius — ar atitinka knygos layoutą; kai kuriuos centruoja, kai kuriuos lygiuoja į viršų | Peržiūri išskirtis (skyrių pradžios, trumpi puslapiai, iliustracijos) ir parenka lygiavimą | `pages_preview`, `pages_set_layout` |
| 8 | Viršeliai: margins 0, nuimti *Match size with other pages*, content apima visą viršelį | Pirmas/paskutinis vaizdas (ar žmogaus nurodyti) → content = visas puslapis, margins 0, `match_size=false` | `page_set_content(mode=full)`, `pages_set_layout` |
| 9 | *Output*: tekstas B/W 600 DPI; viršeliai ir paveikslėlių puslapiai Color/Grayscale 300 DPI arba *Mixed* | Numatytai B/W 600; viršeliai color 300; aptinka iliustracijas ir siūlo mixed/color | `check_pages(kind=pictures)`, `pages_set_output` |
| 10 | Išsaugo projektą | Projektas saugomas po kiekvieno pakeitimo (su `.bak`) | (automatiškai) |
| 11 | Priekinis viršelis → *Output → Play* | Paleidžia 6 filtrą, patikrina rezultatus | `run_stages(4, 6)`, `export_summary` |

Workflow vyksta **iki galo be stabdymo patvirtinimui**. Agentas sprendžia
pagal peržiūras ir skaičius, o kiekvieną sprendimą (puslapiai + kodėl) įrašo į
galutinę suvestinę; žmogus rezultatą tikrina atsidaręs projektą GUI.

## Agento procedūra (`workflow_guide` grąžina EN versiją)

1. `project_create(images_dir, project, output_dir)`. Jei kataloge jau yra
   žmogaus projektas (pvz. `projektas.ScanTailor`) ar `out/` — jų neliesti:
   naudoti `agentas.ScanTailor` ir `out-agentas/`.
2. `image_preview(project, images=[0, 1, 3, N/2, -2, -1])` (indeksai). Nuspręsti:
   - **orientation:** 3 žingsnyje naudoti `orientation="auto"`. Detektorius
     randa kiekvieno skeno pasukimą pagal tekstą (CLI `--orientation=auto`).
     EXIF žymė yra tik užuomina: kai tekstas jai prieštarauja, laimi tekstas.
     Raidžių kryptį padidintoje iškarpoje tikrinti tik tiems skenams, kuriuos
     pažymi 3 žingsnis;
   - **layout**: `2` atvartams, `1` pavieniams puslapiams;
   - viršeliai ir kiti pavieniai skenai (`001`, `9999`, …) dažniausiai
     `orientation=none, layout=1` (`images_set_geometry` po 3 žingsnio auto paleidimo);
   - kiekvieną tokį skeną peržiūrėti: priešlapio skenas (vidinis viršelis su
     žemėlapiu ir priešlapis, pvz. `002`, `9998`) paprastai yra atvartas →
     `layout=2`, orientacija tokia, kokią rodo skenas.
3. Pirma `run_stages(project, 1, 1, orientation="auto", layout=…)`, nes
   `orientation="auto"` įrašo kiekvieno skeno su sprendimu pasukimą. Tada
   `check_pages(kind="orientation")` ir peržiūrėti kiekvieną pažymėtą skeną:
   „no decision“ (viršeliai, nuotraukos, tušti lapai), „low confidence“,
   „EXIF žymė prieštarauja tekstui“ arba „text runs across the book“.
   Paskutinis dažniausiai yra šonu išspausdinta lentelė: skenas lieka knygos
   pasukimo, o 5 žingsnis pasuka tik jo išvestį. Po to viršelius, priešlapius
   ir neteisingus skenus nustatyti su `images_set_geometry` ir paleisti
   `run_stages(project, 1, 5)` be `orientation` (~0,5 s puslapiui).
4. **Content:** `check_pages(kind="content")` ir plačiausi/aukščiausi iš
   `pages_list`. Pažymėtus peržiūrėti, tada:
   - šiukšlės prie rėmelio kraštų (`thin_*_column_isolated`,
     `thin_*_line_isolated`, `box_exceeds_text`) →
     `pages_snap_content(pages=[visi pažymėti], sides="auto")` vienu kvietimu:
     paties ScanTailor dvigubo paspaudimo pritraukimas (CLI `--content-snap`)
     pritraukia pažymėtus kraštus prie kraštinio teksto; po darbo peržiūrėti;
   - jei pritraukus šiukšlė liko (pvz. arčiau nei ~2,5 mm nuo teksto) ar kas
     nukirpta → `page_set_content(mode="tighten")` (numerius
     palieka; pusė, kurios juostoje rašalo 3+ eilutėse, lieka — `kept_sides`;
     jei peržiūroje tai šiukšlė, pvz. ranka rašytas bibliotekos šifras, kraštą
     nustatyti `mode="manual"`);
   - rašalas virš/po rėmeliu su „looks like a page number missed“ →
     `mode="include_numbers"`;
   - tuščias lapas, kuriame aptiktos tik dulkės ar persišviečiantis tekstas →
     `mode="empty"`;
   - `ink_left/right_of_box` „lines may be cut off“ → peržiūrėti; jei tikra —
     praplėsti;
   - tada `pages_fit_text_column(project)`. Teksto stulpelis yra teksto
     puslapių pločio mediana. Platesniems rėmams kairysis ir dešinysis
     kraštai perkeliami į stulpelio kraštus: pieštuko žymės, antspaudai ir
     inventoriaus numeriai paraštėje lieka už rėmo, o brūkšneliai eilučių
     galuose lieka viduje. Peržiūrėti puslapius su dideliu nukirpimu
     (pastaba peržiūrai) ir `taller_pages`: aukštesniame rėme dažniausiai
     yra antspaudas ar žymė virš teksto ar po juo. Juos taisyti
     `mode="manual"`.
5. **Puslapiai su iliustracijomis:** `check_pages(kind="pictures")`; peržiūrėti
   kiekvieną pažymėtą (padidinus iliustraciją). `all_content` rėmas neapima
   plonų juostų prie lapo kraštų, nugarėlės šešėlio ir popieriaus po parašu.
   - `deskew_disagree` išvardija puslapius, kuriuose teksto eilutės ir
     paveikslas (ar lapo kraštai) duoda kampus, besiskiriančius daugiau kaip
     0,5°. Kiekvieną peržiūrėti ir nustatyti kampą, kuriuo puslapis atrodo
     tiesus. 4 knygų pataisymuose žmogus kiekvieną kartą rinkosi paveikslo
     ar lapo krašto kampą;
   - juodai baltos linijos (graviūros, piešiniai, žemėlapiai) → Output
     **B/W 600**;
   - nuotraukos, rastras ir spalvoti įklijai →
     `pages_straighten_pictures` (ScanTailor tiesina pagal teksto eilutes ir
     nuotraukas gali pakreipti), `run_stages(3, 5)`,
     `page_set_content(mode="all_content")` (nuotrauka + parašas), tada `pages_set_output(color_mode="color", dpi=300,
     white_margins=true, equalize_illumination=true, grayscale_first=<žr. žemiau>)` ir
     `pages_set_layout(<nuotraukų puslapiai>, margin_left=0, margin_right=0)`.
     Jei CLI turi `--deskew-pictures`, įrankis kampą matuoja pagal nuotraukos
     kraštus C++ kode. Šio fork'o CLI nuo 2026-11-22 tai daro 3 etape pagal
     nutylėjimą, todėl nauji projektai nuotraukos kampą gauna iš karto.
     Įrankis lieka naudingas puslapiams, kurie kampą jau turi, ir senesniems
     build'ams.
   - `grayscale_first` yra GUI parinktis „Convert to grayscale first“: skenas
     paverčiamas pilku prieš visus kitus išvesties žingsnius, todėl spalvoto
     JPG popieriaus atspalvis negadina paraščių, apšvietimo išlyginimo ir
     nuotraukos. Išvesties TIFF yra 8 bitų pilkas ir apie 4 kartus mažesnis.
   - Pirmiausia perskaityti matavimą: `check_pages(<nuotraukų puslapiai>,
     kind="grayscale")`. Pilka nuotrauka spalvotame skene turi
     `grayscale=true` (chroma mažesnė nei 10). Tokiems puslapiams
     `grayscale_first="auto"`.
   - Spalvoti įklijai (`grayscale=false`) turi likti spalvoti:
     `grayscale_first=false`.
   - Kiekvieną puslapį su `near_threshold=true` peržiūrėti ir nuspręsti
     true ar false pačiam. Po 6 etapo `job_status` lauke `grayscale_auto`
     rodo ScanTailor rezultatą. Ataskaitoje nurodyti puslapius su
     `confidence="low"`.
   - šonu išspausdinta lentelė ar iliustracija (`check_pages` radinys
     `text_runs_vertically`) → 1–5 etapuose palikti knygos orientaciją, kad
     tiesinimas, content box, paraštės ir puslapio dydis liktų tokie pat kaip
     kitų puslapių. Peržiūrėti puslapį ir nustatyti
     `pages_set_output(<puslapiai>, output_rotation=270)`, kai teksto eilutės
     eina iš viršaus į apačią, arba `output_rotation=90`, kai iš apačios į
     viršų. Pasukamas tik išvesties TIFF (CLI `--output-rotate`, pagal
     laikrodžio rodyklę); su 90 ar 270 jis gulsčias. Tada šiems puslapiams
     paleisti 6 etapą. Po pirmo 6 etapo radinys turi ir
     `detected_output_rotation`. Kitas būdas:
     `run_stages(6, 6, pages=<puslapiai>, output_rotate="auto")` — pasukimą
     pagal baigtą puslapį nustato detektorius (CLI `--output-rotate=auto`). Po
     6 etapo `check_pages(kind="orientation")` išvardija išvesties puslapius,
     kurių tekstui reikia kito pasukimo; juos peržiūrėti.
6. **Viršeliai ir priešlapiai:** `page_set_content(mode="cover")` (randa ir
   tamsius viršelius ant juodo fono; kiekvieną kraštą pritraukia prie viršelio
   krašto ir dar 1 mm į vidų, kad nesimatytų fono), `pages_set_layout(margins_mm=0,
   match_size=false)`, `pages_set_output(color_mode="color", dpi=300)`.
   Peržiūroje padidinti kampus.
7. **Paraštės:** `pages_set_layout("all", margin_left=10, margin_right=10,
   margin_top=5, margin_bottom=5, align_v=<suggested_align_v>,
   align_h="center")`, tada vėl viršelių layout (6 žingsnis).
8. **Vienas puslapio plotis:** `pages_fit_width(project)`. Puslapio plotis =
   teksto bloko pločio mediana + po 10 mm iš šonų. Platesniems puslapiams
   šoninės paraštės 0: „match size“ ir horizontalus centravimas juos vis
   tiek išdėsto bendrame puslapyje. Paleisti po paskutinio rėmo pakeitimo.
9. **Lygiavimas:** `pages_align_auto(project)`. Įžanginiai puslapiai
   (titulinis, metrikos, dedikacija, epigrafas) → `vcenter`, bet mažas
   logotipas viršuje lieka `top`; skyrių ir sąrašų pradžios (tekstas
   prasideda žemiau nei įprastai) → `bottom`; iliustracijų puslapiai →
   `vcenter` (spalvotos ar pilkos nuotraukų įklijos ir B/W raižiniai,
   žemesni už teksto bloką); trumpi puslapiai (skyrių
   pabaigos, sąrašai) ir pilni teksto puslapiai be numerio juostos → `top`;
   kiti → `bottom`. Jei peržiūrose matosi lentelės ar diagramos, pridėti
   `review_full_pages=true`: jis išvardija aukštus puslapius su mažai
   eilučių, pvz. pasuktas lenteles. Peržiūrėti `top` ir `vcenter` puslapius, pavienius
   pataisyti per `pages_set_layout`.
   **Dydžio vartai prieš output:** `check_pages(kind="size")` išvardija
   teksto puslapius, kurių rėmas platesnis už knygos medianą daugiau kaip
   3 mm arba aukštesnis daugiau kaip 3 %. Vartai tik išvardija puslapius:
   rėmo niekada nekirpti aklai iki medianos. Kiekvieną puslapį peržiūrėti
   ir rasti pusę su ženklu. Dukart spragtelėti ant tos pusės kraštinio
   spausdinto teksto su `pages_snap_content(points=[{page, x, y, side}])`,
   kaip GUI: kraštas prilimpa prie spausdinto teksto. Antspaudą ar įrašą,
   kuris lieka rėme (pvz. šalia puslapio numerio), uždengti
   `page_add_fill_zone`. Tikrai didesnis puslapis (titulinio
   paveikslas, lentelė, turinys) lieka, bet su `review_note` priežastimi.
   Output daryti tik tada, kai sąraše nėra nepaaiškinto puslapio.
   Tada `run_stages(4, 6)`,
   `export_summary`, patikrinti, ar visi teksto puslapiai vienodo dydžio
   (`pages_list(sort_by="content_width", descending=true)`). Peržiūrėti 8–16
   išvesties puslapių.
10. **Peržiūros lapas:** `review_sheet(project)` įrašo
    `out-agentas/review/index.html`. Puslapyje — kiekvieno išvesties
    puslapio miniatiūra ir content box peržiūra kiekvienam puslapiui su
    agento sprendimu. Kiekvienas sprendimas turi patikimumą ir priežastį.
    Puslapiai su mažu patikimumu ar vėliava eina pirmi, su žyma
    „CHECK IN GUI“. Įrankiai savo sprendimus įrašo į
    `out-agentas/review/decisions.json`. Savo sprendimus (orientacija,
    viršeliai, B/W paliekami iliustracijų puslapiai) įrašyti per
    `review_note(project, pages, decision, reason, confidence)`. Lapą daryti
    prieš `project_for_windows`, nes CLI peržiūroms reikia kelių, kuriuos CLI
    gali atidaryti. Įspėjimas „a rotated table?“ (`pages_align_auto`)
    nuimamas, kai orientacijos detektorius output faile randa stačią tekstą
    (`upright_confirmed`). Puslapis, kuriame teksto detektoriui per mažai,
    arba su `check_pages` radiniu `text_runs_vertically`, lieka sąraše.
11. `project_for_windows(project)`, jei projektą atidarys GUI kitoje
    sistemoje nei ta, kurioje jis apdorotas (pvz. apdorota WSL, atidaroma
    Windows). Su `relative=true` keliai rašomi nuo projekto, todėl projektas
    atsidaro bet kurioje sistemoje.
    Jis taip pat išsaugo `agentas.orig.ScanTailor` — baigto projekto kopiją.
    Išvalyti agento žurnalus ir paleidimų atsargines kopijas, bet niekada ne
    `.orig`: `project_diff(orig, pataisytas)` vėliau palygina jį su žmogaus
    pataisymais, kad rastume naujų taisyklių.
12. Suvestinė: kas pakeista ir kodėl, iliustracijų sprendimai, kas liko
    žmogui (pvz. antspaudai ar dėmės tekste). Išvardyti `pages_align_auto`
    šalinimo kandidatus (tušti priešlapiai, titulinį kartojantis antraštinis
    lapas, beveik tušti puslapiai) ir peržiūros sąrašą (aukšti puslapiai su
    mažai eilučių: pasuktos lentelės) — nešalinti, sprendžia žmogus.
    Išvardyti tikrintinus puslapius iš `review_sheet` (`to_check`) ir nurodyti
    peržiūros lapo kelią. Suvestinę įrašyti į `out-agentas/review/report.md`.

## Knygų eilė (naktiniai paleidimai)

`tools/st-mcp/run_queue.py books.txt` paleidžia šį workflow kiekvienam
knygos katalogui iš `books.txt`: po vieną headless Claude Code sesiją
(`claude -p`) knygai. Užduotyje (prompt) — taisyklės ir aukščiau esantis
žingsnių sąrašas, ataskaita prašoma į `out-agentas/review/report.md`. Failas
`books.state.json` saugo kiekvienos knygos būseną, todėl po strigties naujas
paleidimas tęsia ir praleidžia baigtas knygas. Kiekviena knyga turi savo
žurnalą `books-logs/`. Pabaigoje `books-summary.md` — po eilutę knygai su
nuoroda į jos peržiūros lapą. Komandas pirma pažiūrėti su `--dry-run`.
Smulkiau — `tools/st-mcp/README.md`.

## Pataisymų žurnalas

Kai žmogus knygą pataisė GUI, paleisti
`corrections_record("<knyga>/agentas.orig.ScanTailor", "<knyga>/agentas.ScanTailor")`.
Jis įrašo po vieną JSONL įrašą kiekvienam pakeistam puslapio parametrui:
knygos katalogas, puslapis, etapas, laukas, agento reikšmė, žmogaus reikšmė ir
kategorija. Žurnalas — `~/.local/share/st-mcp/corrections.jsonl`; kitą failą
nurodo `ST_CORRECTIONS_LOG`. Pakeitimas, kuris tai knygai jau yra žurnale,
antrą kartą neįrašomas, todėl įrankį galima leisti po kiekvienos peržiūros.

Kategorijos:
- puslapiai ir geometrija: `page_removed`, `page_added`, `split`, `rotation`,
  `deskew_picture`, `deskew_text`, `page_box`;
- content box: `content_empty`, `content_box_trim` (rėmelis sumažintas),
  `content_box_extend`, `content_box_numbers` (pridėta siaura juosta viršuje ar apačioje);
- viršeliai: `cover` (rėmelio, maketo ar išvesties pakeitimas viršelyje);
- maketas: `margins`, `alignment`, `match_size`;
- išvestis: `output_mode`, `dpi`, `picture_zones`; visa kita: `other`.

Content box pakeitimas neįrašomas, jei to puslapio pasukimas, padalijimas ar
kampas irgi pakeistas: tada rėmelio koordinatės nepalyginamos.

`corrections_stats()` rodo dažniausias kategorijas ir workflow taisyklę,
į kurią kiekviena rodo. Pirma taisyti tą taisyklę.

Vienetai: content box — ištiesinto puslapio pikseliais (tie patys, kaip
`page_preview`); paraštės — mm; DPI — sveikieji skaičiai.

## Įrankiai (MCP = CLI)

Visi grąžina JSON; `*_preview` grąžina PNG (MCP image content).
CLI: `python -m st_mcp <tool> key=value …` (peržiūros: `out=failas.png`).

| Įrankis | Paskirtis |
|---|---|
| `workflow_guide()` | Šis dokumentas (EN) |
| `project_create(images_dir, project, output_dir?, dpi=300, force_dpi=false)` | Sukuria `.ScanTailor`, sutvarko DPI |
| `project_info(project)` | Puslapių skaičius, DPI, pasukimai, etapų progresas, vykdomas darbas |
| `image_preview(project, images?)` | Originalūs skenai |
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?, pages?, output_rotate?)` | Paleidžia filtrus fone; `pages` — tik šie puslapiai (CLI `--pages`); eiga ir puslapių klaidos iš CLI `--json-progress`; `orientation=auto` ir `output_rotate=auto` naudoja orientacijos detektorių |
| `job_status(job_id, wait_s?)` | Progresas / rezultatas |
| `pages_list(project, sort_by, descending, limit, offset)` | Lentelė: id, label, content dydis + z-score, paraštės, lygiavimas, match size, spalvų režimas, DPI, vėliavos (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page, engine?)` / `pages_preview(project, pages, engine?)` | Puslapis(-iai) su content box overlay; `engine="cli"` — piešia pats ScanTailor (CLI `--render-preview`), kaip GUI |
| `page_set_content(project, page, mode=manual\|auto\|full\|empty\|cover\|tighten\|include_numbers\|all_content, x,y,width,height, expand_*)` | `select-content` |
| `pages_snap_content(project, pages, sides=auto\|all\|[left,right,top,bottom], points?, wait_s?)` | GUI dvigubo spragtelėjimo snap per `--content-snap`, paleidžia 4–5 etapus; `points` – savi spragtelėjimai ant spausdinto teksto |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `project_diff(original, corrected, record?)` | Skirtumai tarp agento projekto ir žmogaus pataisyto, puslapiais; `record=true` juos dar įrašo į pataisymų žurnalą |
| `corrections_record(original, corrected, book?, log?)` | Prideda žmogaus pataisymus į pataisymų žurnalą (JSONL): vienas įrašas kiekvienam pakeistam puslapio parametrui, be pasikartojimų |
| `corrections_stats(log?, book?, top?)` | Pataisymai pagal kategoriją ir knygą, dažniausios kategorijos su workflow taisykle |
| `pages_fit_text_column(project, tolerance_mm?, height_ratio?)` | Susiaurina rėmus, platesnius už knygos teksto stulpelį; išvardija puslapius su aukštesniu rėmu |
| `pages_fit_width(project, margin_mm?, percentile?)` | Vienas puslapio plotis: šoninės paraštės 0 puslapiams, platesniems už teksto bloko medianą |
| `pages_align_auto(project, short_ratio?, missing_number_ratio?)` | Vertikalus lygiavimas visiems teksto puslapiams ir šalinimo kandidatai ataskaitai |
| `pages_set_output(project, pages, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color?, fill_offcut?, grayscale_first?, output_rotation?)` | `output`; `grayscale_first` (true, false arba "auto") įrašo `color-or-grayscale@convertToGrayscale="1"` arba `"auto"` (CLI `--grayscale-first`); `output_rotation` (0/90/180/270, pagal laikrodžio rodyklę) pasuka tik išvesties TIFF (CLI `--output-rotate`) |
| `page_add_fill_zone(project, page, x, y, width, height, color?)` | Output fill zona (puslapio px): antspaudas ar įrašas rėmo viduje; saugoma originalo px, matoma GUI |
| `check_pages(project, pages, kind=content\|size\|pictures\|grayscale\|orientation)` | Heuristikos su įrodymais; `size` išvardija rėmus, didesnius už medianą (vartai prieš output); `grayscale` duoda kiekvieno puslapio chroma, klasifikaciją ir atstumą iki slenksčio; `text_runs_vertically` žymi šonu išspausdintas lenteles; `orientation` paleidžia detektorių (CLI `--detect-orientation`) skenams ir išvesties failams ir išvardija tuos, kuriuos reikia peržiūrėti |
| `export_summary(project)` | Išvesties TIFF sąrašas ir kas trūksta |
| `review_note(project, pages, decision, reason?, confidence?, topic?)` | Įrašo agento sprendimą peržiūros lapui |
| `review_sheet(project, pages?, thumb_px?, engine?)` | Įrašo `out-agentas/review/index.html`: miniatiūros, sprendimai su patikimumu, abejotini puslapiai pirmi |
| `pages_straighten_pictures(project, pages)` | Nuotraukų puslapių kampas pagal spaudos stačiakampius |
| `project_for_windows(project, relative?)` | Projekto keliai GUI kitoje sistemoje: reliatyvūs nuo projekto arba /mnt/c → C:/ (paskutinis žingsnis) |

## CLI parinktys, kurias naudoja st-mcp

`--dump-masks=<katalogas>` po paleidimo kiekvienam puslapiui įrašo 4 filtro (select content) analizės kaukes.
Su `--pages` įrašo tik pasirinktiems puslapiams.
Failų vardai tokie patys kaip `--render-preview` ir išvesties TIFF:

- `<puslapis>_content.png`: rašalas, kurį mato content box pritraukimas (`--content-snap`);
- `<puslapis>_text.png`: rašalas, kurį `ContentBoxFinder` laiko tekstu;
- `<puslapis>_garbage.png`: tamsios sritys, kurios liečia puslapio kraštą (šešėliai, skenavimo kraštai);
- `<puslapis>.json`: puslapio duomenys ir transformacijos.

JSON faile yra skeno failas, sub-page, paveikslėlio dydis, DPI ir pasukimas.
Taip pat yra deskew kampas, crop sritis, page box ir content box.
`crop_area` naudoja projekto puslapio kontūro koordinates: skenas po DPI mastelio ir pasukimo.
Transformacijos: `original_to_page`, `original_to_mask` ir `page_to_mask`.

Kaukės yra 1 bito PNG, 150 DPI, baltas pikselis = nustatytas.

Transformacija yra `QTransform` sąrašas `m11 m12 m21 m22 dx dy`: `x' = m11*x + m21*y + dx`, `y' = m12*x + m22*y + dy`.
Kaukės pikselis `(x, y)` dengia kaukės koordinates nuo `(x, y)` iki `(x+1, y+1)`.
Skeno pikselį gausite su atvirkštine `original_to_mask` transformacija.
Jei puslapio kaukių nepavyko įrašyti, `--json-progress` duoda įvykį `warning` (`dump_masks_failed`), o darbas tęsiasi.
`--start-filter=5 --end-filter=4` nepaleidžia jokio filtro, todėl kaukės tada daromos iš išsaugotų 4 filtro rezultatų.
`check_pages(kind=content)` naudoja content kaukę, kai CLI turi šią parinktį.

## Kas jau patikrinta (2026-11-20, Onetti „Trumpas gyvenimas“, 300 DPI JPG)

- CLI veikia headless, `--start-filter/--end-filter` leidžia etapus atskirai.
- Visi per-puslapio nustatymai yra projekto XML ir CLI juos gerbia pakartotinai
  paleidus nuo projekto failo:
  - content box: `select-content/page/params[@mode=manual]/content-rect` (px);
  - paraštės: `page-layout/page/params/hardMarginsMM` (mm);
  - lygiavimas: `page-layout/.../alignment@vert,@hor`;
  - *Match size with other pages* išjungta = `alignment@null="1"`;
  - spalvų režimas / DPI: `output/page/params/color-params@colorMode`, `dpi`;
  - „Convert to grayscale first“: `color-params/color-or-grayscale@convertToGrayscale="1"`
    (įjungta) arba `"auto"` (jei atributo nėra — išjungta). CLI jį nustato su
    `--grayscale-first`, `--grayscale-first=auto` arba `--grayscale-first=false`.
    Su auto 6 etapas rezultatą įrašo į
    `output-params/image@grayscaleChroma,@grayscaleDetected`, o su
    `--json-progress` įvykis `page_done` duoda `grayscale_first`,
    `grayscale_detected` ir `chroma`. Su `--pages` keičiami tik pasirinkti
    puslapiai, pvz.
    `--start-filter=6 --pages="021.jpg;095.jpg:L" --grayscale-first`.
    `--white-margins`, `--normalize-illumination` ir sluoksnių parinktys jį
    atstato, todėl kartu su jomis jį reikia nurodyti dar kartą.
  - išvesties pasukimas: `output/page/params@outputRotation` (laipsniai pagal
    laikrodžio rodyklę; nėra atributo = 0). CLI: `--output-rotate=<0|90|180|270|auto>`,
    pavieniams puslapiams kartu su `--pages`.
  - orientacijos detektorius (2026-11-21): `--orientation=auto` (1 etapas,
    visas skenas prieš padalijimą) ir `--output-rotate=auto` (6 etapas, baigtas
    puslapis). Su `--json-progress` įvykis `page_done` turi
    `orientation_detected` arba `output_rotation_detected` (null be
    sprendimo), `confidence` (0–1) ir `reason`.
    `--detect-orientation=<sąrašo failas>` kiekvienam vaizdui išspausdina vieną
    JSON eilutę ir nieko nekeičia.
  - nuotraukų tiesinimas (2026-11-22): puslapyje, kurį beveik visą užima
    nuotrauka ar įklija, 3 etapas automatinį kampą randa pagal tiesius
    nuotraukos kraštus. Įjungta pagal nutylėjimą (`--deskew-pictures`);
    `--deskew-pictures=false` (arba `=0`) išjungia. Puslapiai, kurie jau turi
    deskew parametrus, savo kampą išlaiko. GUI nustatymas: „Deskew picture
    pages by the picture edges“ (Settings, Deskew).
    `deskew/page@pictureEdges="0|1"` saugo vieno puslapio pasirinkimą (jei
    atributo nėra, galioja bendras nustatymas), o
    `deskew/page/params@estimator` (`text`, `picture` arba `none`) rodo, kuris
    būdas davė automatinį kampą.
- Numatyti nustatymai šiai knygai netinka: skenai pasukti 90° (reikia
  `orientation=right, layout=2`). Be peržiūros agentas to nepastebėtų.
- CLI DPI nuskaito iš JPG; `--dpi` reikalingas tik kai jo nėra.
- Peržiūrų geometrija atkartoja `ImageTransformation` ir pikselio tikslumu
  sutampa su ScanTailor `page-rect`.
- Greitis: su `OMP_NUM_THREADS=1` (st-mcp numatytasis) visa knyga (191 skenas,
  380 puslapių, visi etapai) — ~6 min; ScanTailor numatytas OpenMP
  daugiagijis režimas buvo ~9 kartus lėtesnis (54 min) ir nedeterministinis.
- Pakartotinis paleidimas: output etapas neperrašo TIFF, kurio parametrai ir
  failas nepasikeitė (kaip GUI). 8 skenų bandyme etapai 4–6 truko 5 s vietoj 38 s.
  Tokio puslapio `page_done` eilutėje (6 etapas) yra `"skipped":true`.
  Senesnio CLI projektas pirmą kartą perdaromas visas. Po output kodo pakeitimo
  ištrinkite `out/` TIFF failus, nes CLI jų neperrašys.
- CLI `--threads=<N>` apdoroja N vieno etapo puslapių vienu metu; kitas etapas
  prasideda tik po visų puslapių. Kiekvienas puslapis naudoja vieną OpenMP giją,
  todėl rezultatas toks pat kaip su viena gija (TIFF baitas į baitą). Jei 6 etape
  pasikeičia bendras puslapių dydis, CLI duoda įspėjimą: tada paleiskite 5–6 etapus.
  36 skenai, 72 puslapiai, visi etapai: N=1 ~100 s, N=4 ~38 s, N=8 ~25 s,
  N=14 ~22 s. Kiekvienam puslapiui reikia ~170 MB atminties.
  st-mcp perduoda `--threads` su pusės branduolių skaičiumi (`ST_CLI_THREADS`).

## Įgyvendinimo planas

1. **P1 — Python MCP serveris** (`tools/st-mcp/`): kviečia `scantailor-universal-cli`,
   redaguoja projekto XML, peržiūras piešia Pillow. Greitai, veikia kiekvienoje sistemoje, kurioje veikia CLI.
2. **P2 — native C++ komandos** `scantailor-universal-cli <subcommand> --json`
   tiems patiems įrankiams, peržiūras renderina `src/core` (tiksliai kaip GUI),
   apdorojimas po vieną puslapį (dabar CLI visada apdoroja visus); MCP
   serveris tampa plonu sluoksniu.
3. **P3 — vertinimas:** kelios knygos su žmogaus rankiniu rezultatu kaip „ground
   truth“; matuojame, kiek puslapių agentas sutvarko be žmogaus.
