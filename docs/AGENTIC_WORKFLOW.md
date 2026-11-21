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
| 1 | (GUI to neprašo, bet kai kurios knygos skenuotos pasuktos) | Peržiūri 2–3 originalus: orientacija ir ar skenai yra atvartai | `image_preview`, tada `run_stages(1, 5, orientation=…, layout=…)` |
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
   - **orientation** pagal raidžių kryptį padidintoje iškarpoje;
   - **layout**: `2` atvartams, `1` pavieniams puslapiams;
   - viršeliai ir kiti pavieniai skenai (`001`, `9999`, …) dažniausiai
     `orientation=none, layout=1` (`images_set_geometry`);
   - kiekvieną tokį skeną peržiūrėti: priešlapio skenas (vidinis viršelis su
     žemėlapiu ir priešlapis, pvz. `002`, `9998`) paprastai yra atvartas →
     `layout=2`, orientacija tokia, kokią rodo skenas.
3. `run_stages(project, 1, 5)` (~0,5 s puslapiui).
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
     praplėsti.
5. **Puslapiai su iliustracijomis:** `check_pages(kind="pictures")`; peržiūrėti
   kiekvieną pažymėtą (padidinus iliustraciją):
   - juodai baltos linijos (graviūros, piešiniai, žemėlapiai) → Output
     **B/W 600**;
   - grayscale iliustracijos (nuotraukos, rastras) →
     `pages_straighten_pictures` (ScanTailor tiesina pagal teksto eilutes ir
     nuotraukas gali pakreipti), `run_stages(3, 5)`,
     `page_set_content(mode="all_content")` (nuotrauka + parašas, be nugarėlės
     šešėlio), tada `pages_set_output(color_mode="color", dpi=300,
     white_margins=true, equalize_illumination=true)` ir
     `pages_set_layout(<nuotraukų puslapiai>, margin_left=0, margin_right=0)`.
6. **Viršeliai ir priešlapiai:** `page_set_content(mode="cover")` (randa ir
   tamsius viršelius ant juodo fono; kiekvieną kraštą pritraukia prie viršelio
   krašto ir dar 1 mm į vidų, kad nesimatytų fono), `pages_set_layout(margins_mm=0,
   match_size=false)`, `pages_set_output(color_mode="color", dpi=300)`.
   Peržiūroje padidinti kampus.
7. **Paraštės:** `pages_set_layout("all", margin_left=10, margin_right=10,
   margin_top=5, margin_bottom=5, align_v=<suggested_align_v>,
   align_h="center")`, tada vėl viršelių layout (6 žingsnis).
8. **Vienas puslapio plotis:** `pages_fit_width(project)`. Puslapio plotis =
   tipinis teksto bloko plotis + po 10 mm iš šonų. Platesniems puslapiams
   šoninės paraštės sumažinamos (iki 0), kad jie neplatintų visos knygos.
9. **Lygiavimas:** `pages_align_auto(project)`. Įžanginiai puslapiai
   (titulinis, metrikos, dedikacija, epigrafas) → `vcenter`, bet mažas
   logotipas viršuje lieka `top`; skyrių ir sąrašų pradžios (tekstas
   prasideda žemiau nei įprastai) → `bottom`; trumpi puslapiai (skyrių
   pabaigos, sąrašai) ir pilni teksto puslapiai be numerio juostos → `top`;
   kiti → `bottom`. Jei peržiūrose matosi lentelės ar diagramos, pridėti
   `review_full_pages=true`: jis išvardija aukštus puslapius su mažai
   eilučių, pvz. pasuktas lenteles. Peržiūrėti `top` ir `vcenter` puslapius, pavienius
   pataisyti per `pages_set_layout`. Tada `run_stages(4, 6)`,
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
    gali atidaryti.
11. `project_for_windows(project)`, jei apdorota per WSL — kad projektą
    atidarytų Windows GUI. Su šios šakos Windows buildu naudoti
    `relative=true`: tada projektas atsidaro ir WSL, ir Windows.
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
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?, pages?)` | Paleidžia filtrus fone; `pages` — tik šie puslapiai (CLI `--pages`); eiga ir puslapių klaidos iš CLI `--json-progress` |
| `job_status(job_id, wait_s?)` | Progresas / rezultatas |
| `pages_list(project, sort_by, descending, limit, offset)` | Lentelė: id, label, content dydis + z-score, paraštės, lygiavimas, match size, spalvų režimas, DPI, vėliavos (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page, engine?)` / `pages_preview(project, pages, engine?)` | Puslapis(-iai) su content box overlay; `engine="cli"` — piešia pats ScanTailor (CLI `--render-preview`), kaip GUI |
| `page_set_content(project, page, mode=manual\|auto\|full\|empty\|cover\|tighten\|include_numbers\|all_content, x,y,width,height, expand_*)` | `select-content` |
| `pages_snap_content(project, pages, sides=auto\|all\|[left,right,top,bottom], wait_s?)` | GUI double-click edge snap via `--content-snap`, runs stages 4–5 |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `project_diff(original, corrected, record?)` | Skirtumai tarp agento projekto ir žmogaus pataisyto, puslapiais; `record=true` juos dar įrašo į pataisymų žurnalą |
| `corrections_record(original, corrected, book?, log?)` | Prideda žmogaus pataisymus į pataisymų žurnalą (JSONL): vienas įrašas kiekvienam pakeistam puslapio parametrui, be pasikartojimų |
| `corrections_stats(log?, book?, top?)` | Pataisymai pagal kategoriją ir knygą, dažniausios kategorijos su workflow taisykle |
| `pages_fit_width(project, margin_mm?, percentile?)` | Vienas puslapio plotis: mažesnės šoninės paraštės už tipinį teksto bloką platesniems puslapiams |
| `pages_align_auto(project, short_ratio?, missing_number_ratio?)` | Vertikalus lygiavimas visiems teksto puslapiams ir šalinimo kandidatai ataskaitai |
| `pages_set_output(project, pages, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color?, fill_offcut?)` | `output` |
| `check_pages(project, pages, kind=content\|pictures)` | Heuristikos su įrodymais |
| `export_summary(project)` | Išvesties TIFF sąrašas ir kas trūksta |
| `review_note(project, pages, decision, reason?, confidence?, topic?)` | Įrašo agento sprendimą peržiūros lapui |
| `review_sheet(project, pages?, thumb_px?, engine?)` | Įrašo `out-agentas/review/index.html`: miniatiūros, sprendimai su patikimumu, abejotini puslapiai pirmi |
| `pages_straighten_pictures(project, pages)` | Nuotraukų puslapių kampas pagal spaudos stačiakampius |
| `project_for_windows(project, relative?)` | /mnt/c keliai → C:/ Windows GUI arba reliatyvūs nuo projekto (paskutinis žingsnis per WSL) |

## Kas jau patikrinta (2026-11-20, Onetti „Trumpas gyvenimas“, 300 DPI JPG)

- CLI veikia headless, `--start-filter/--end-filter` leidžia etapus atskirai.
- Visi per-puslapio nustatymai yra projekto XML ir CLI juos gerbia pakartotinai
  paleidus nuo projekto failo:
  - content box: `select-content/page/params[@mode=manual]/content-rect` (px);
  - paraštės: `page-layout/page/params/hardMarginsMM` (mm);
  - lygiavimas: `page-layout/.../alignment@vert,@hor`;
  - *Match size with other pages* išjungta = `alignment@null="1"`;
  - spalvų režimas / DPI: `output/page/params/color-params@colorMode`, `dpi`.
- Numatyti nustatymai šiai knygai netinka: skenai pasukti 90° (reikia
  `orientation=right, layout=2`). Be peržiūros agentas to nepastebėtų.
- CLI DPI nuskaito iš JPG; `--dpi` reikalingas tik kai jo nėra.
- Peržiūrų geometrija atkartoja `ImageTransformation` ir pikselio tikslumu
  sutampa su ScanTailor `page-rect`.
- Greitis: su `OMP_NUM_THREADS=1` (st-mcp numatytasis) visa knyga (191 skenas,
  380 puslapių, visi etapai) — ~6 min; ScanTailor numatytas OpenMP
  daugiagijis režimas buvo ~9 kartus lėtesnis (54 min) ir nedeterministinis.

## Įgyvendinimo planas

1. **P1 — Python MCP serveris** (`tools/st-mcp/`): kviečia `scantailor-universal-cli`,
   redaguoja projekto XML, peržiūras piešia Pillow. Greitai, veikia ir Windows, ir Linux.
2. **P2 — native C++ komandos** `scantailor-universal-cli <subcommand> --json`
   tiems patiems įrankiams, peržiūras renderina `src/core` (tiksliai kaip GUI),
   apdorojimas po vieną puslapį (dabar CLI visada apdoroja visus); MCP
   serveris tampa plonu sluoksniu.
3. **P3 — vertinimas:** kelios knygos su žmogaus rankiniu rezultatu kaip „ground
   truth“; matuojame, kiek puslapių agentas sutvarko be žmogaus.
