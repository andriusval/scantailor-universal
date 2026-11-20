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
| 6 | *Margins*: 10 mm iš visų pusių → *Apply → All pages*; lygiavimas pagal puslapio numerio vietą (viršus/apačia) | Numerių vietą ima iš 5 žingsnio „balsavimo“; taiko visiems | `pages_set_layout(all, margins_mm=10, align_v=top\|bottom)` |
| 7 | Rūšiuoja pagal aukštį, tikrina aukščiausius — ar atitinka knygos layoutą; kai kuriuos centruoja, kai kuriuos lygiuoja į viršų | Peržiūri išskirtis (skyrių pradžios, trumpi puslapiai, iliustracijos) ir parenka lygiavimą | `pages_preview`, `pages_set_layout` |
| 8 | Viršeliai: margins 0, nuimti *Match size with other pages*, content apima visą viršelį | Pirmas/paskutinis vaizdas (ar žmogaus nurodyti) → content = visas puslapis, margins 0, `match_size=false` | `page_set_content(mode=full)`, `pages_set_layout` |
| 9 | *Output*: tekstas B/W 600 DPI; viršeliai ir paveikslėlių puslapiai Color/Grayscale 300 DPI arba *Mixed* | Numatytai B/W 600; viršeliai color 300; aptinka iliustracijas ir siūlo mixed/color | `check_pages(kind=pictures)`, `pages_set_output` |
| 10 | Išsaugo projektą | Projektas saugomas po kiekvieno pakeitimo (su `.bak`) | (automatiškai) |
| 11 | Priekinis viršelis → *Output → Play* | Paleidžia 6 filtrą, patikrina rezultatus | `run_stages(4, 6)`, `export_summary` |

Žmogaus patvirtinimas (kaip craft-anything „approval card“) prašomas:
po 1 žingsnio (orientacija/atvartai), po 3–5 (pakeistų content box sąrašas su
miniatiūromis), po 8–9 (viršeliai ir iliustracijų puslapiai) ir prieš 11.

## Agento procedūra (tai, ką grąžina `workflow_guide`)

1. `project_create(images_dir, project)`. Pranešti DPI pastabą.
2. `image_preview(project, "first3")` ir vienas iš vidurio. Nuspręsti:
   - **orientation**: tekstas turi skaitytis normaliai. `right` = pasukti 90°
     pagal laikrodžio rodyklę, `left` = prieš, `upsidedown`, `none`;
   - **layout**: `2`, jei skenas yra atversta knyga (du puslapiai greta),
     `1` — pavieniai puslapiai, `0` (auto) — tik jei neaišku.
   - Paprašyti žmogaus patvirtinimo, parodyti peržiūrą.
3. `run_stages(project, 1, 5, orientation=…, layout=…)`, tada
   `job_status(job_id, wait_s=60)`, kol `state` = `ok`.
   Apytiksliai 10–25 s vienam skenui.
4. **Content (plotis):** `pages_list(sort_by="z_width", descending=true, limit=12)`
   ir tas pats didėjančia tvarka. Kandidatus peržiūrėti su `pages_preview`
   (iki 16 viename lape). Mėlynas rėmelis turi apimti visą spausdintą tekstą
   (taip pat puslapių numerius ir antraštes) ir nieko daugiau (jokio nugarėlės
   šešėlio, pirštų, skenavimo šiukšlių). Taisyti `page_set_content`
   (`expand_*` puslapio pikseliais; neigiama reikšmė — mažina).
5. **Content (aukštis / puslapių numeriai):** `check_pages(kind="content")`.
   Kiekvienam `ink_below_box`/`ink_above_box` — peržiūrėti; jei tai puslapio
   numeris ar antraštė — praplėsti. `thin_*_line_isolated` — peržiūrėti; jei
   šiukšlė — sumažinti. `empty_but_ink` — nuspręsti, ar puslapis tikrai tuščias.
6. `run_stages(4, 5)`, kad content pakeitimai pasiektų paraštes.
7. **Paraštės:** `pages_set_layout("all", margins_mm=10, align_v=<suggested_align_v
   iš check_pages>, align_h="center")`.
8. **Layout išskirtys:** `pages_list(sort_by="z_height")` abu galai; peržiūrėti
   skyrių pradžias ir trumpus puslapius. Puslapiai, kurių tekstas originale
   prasideda žemiau (skyrių pradžios), turi išlaikyti poziciją: `align_v="bottom"`,
   kai numeriai apačioje, arba `voriginal`.
9. **Viršeliai:** priekinis ir galinis (vėliava `cover_candidate`; patvirtinti
   peržiūra) → `page_set_content(mode="full")`,
   `pages_set_layout([...], margins_mm=0, match_size=false)`,
   `pages_set_output([...], color_mode="color", dpi=300)`.
10. **Paveikslėliai:** `check_pages(kind="pictures")`; peržiūrėti pažymėtus;
    iliustracijos tekste → `color_mode="mixed"`; viso puslapio vaizdai →
    `color_mode="color", dpi=300`.
11. Parodyti žmogui visų pakeitimų suvestinę (puslapiai + kodėl) ir paklausti
    prieš galutinį paleidimą.
12. `run_stages(5, 6)` (arba `4, 6`, jei content vėl keistas),
    `export_summary`. Patikrinti 3–4 išvesties TIFF.

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
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?)` | Paleidžia filtrus fone |
| `job_status(job_id, wait_s?)` | Progresas / rezultatas |
| `pages_list(project, sort_by, descending, limit, offset)` | Lentelė: id, label, content dydis + z-score, paraštės, lygiavimas, match size, spalvų režimas, DPI, vėliavos (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page)` / `pages_preview(project, pages)` | Puslapis(-iai) su content box overlay |
| `page_set_content(project, page, mode=manual\|auto\|full, x,y,width,height, expand_*)` | `select-content` |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `pages_set_output(project, pages, color_mode, dpi, despeckle)` | `output` |
| `check_pages(project, pages, kind=content\|pictures)` | Heuristikos su įrodymais |
| `export_summary(project)` | Išvesties TIFF sąrašas ir kas trūksta |

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
- Greitis: ~23 s / atvartas (14 branduolių), visi etapai.

## Įgyvendinimo planas

1. **P1 — Python MCP serveris** (`tools/st-mcp/`): kviečia `scantailor-universal-cli`,
   redaguoja projekto XML, peržiūras piešia Pillow. Greitai, veikia ir Windows, ir Linux.
2. **P2 — native C++ komandos** `scantailor-universal-cli <subcommand> --json`
   tiems patiems įrankiams, peržiūras renderina `src/core` (tiksliai kaip GUI),
   apdorojimas po vieną puslapį (dabar CLI visada apdoroja visus); MCP
   serveris tampa plonu sluoksniu.
3. **P3 — vertinimas:** kelios knygos su žmogaus rankiniu rezultatu kaip „ground
   truth“; matuojame, kiek puslapių agentas sutvarko be žmogaus.
