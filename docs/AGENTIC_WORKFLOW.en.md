# ScanTailor agentic workflow (MCP)

Goal: an agent does the same job a person does in the ScanTailor GUI, through
tools (each one is both a CLI command and an MCP tool), and the person only
confirms what genuinely needs a decision.

Principles (borrowed from craft-anything):
- **One engine.** GUI, CLI and MCP share `src/core` and the same `.ScanTailor`
  project XML. The agent can stop and a person can open the project in the GUI
  and carry on, or the other way round.
- **Checked, not guessed.** Every decision is based on a preview image or on
  numbers from the project, never on assumptions.
- **Small tools.** Not "process the book" but separate stages and separate
  pages, so the agent can fix things in place.

## Manual workflow → agent steps

| # | Person in the GUI | Agent | Tools |
|---|---|---|---|
| 0 | New project, add the JPGs, *Fix DPI → 300* | Reads DPI from the images; if missing, uses 300 and says so | `project_create` |
| 1 | (Not asked by the GUI, but some books are scanned rotated) | Looks at 2–3 originals: orientation, and whether each scan is a two-page spread | `image_preview`, then `run_stages(1, 5, orientation=…, layout=…)` |
| 2 | *Margins → Play* | Runs filters 1–5 | `run_stages`, `job_status` |
| 3 | *Select Content*, sort by **Width**, look at the bottom (widest) — the box often includes scan junk; shrink it to the printed text | Takes outliers by width (robust z-score), previews them with the content box overlay, fixes | `pages_list(sort_by=z_width, descending=true)`, `pages_preview`, `page_set_content` |
| 4 | Same, looking at the **narrowest** | Same for the other end of the list (incomplete content, empty pages) | same |
| 5 | Sort by **Height** — are page numbers at the bottom included? | Checks for ink outside the box (above/below); previews and extends | `check_pages(kind=content)`, `page_set_content(expand_bottom=…)` |
| 6 | *Margins*: 10 mm on all sides → *Apply → All pages*; align to where page numbers are (top/bottom) | Takes the page-number position from step 5 votes; applies to all | `pages_set_layout(all, margins_mm=10, align_v=top\|bottom)` |
| 7 | Sort by height, check the tallest — does it match the book's layout? Some pages need centring, some top alignment | Previews the outliers (chapter openings, short pages, illustrations) and picks alignment | `pages_preview`, `pages_set_layout` |
| 8 | Covers: margins 0, untick *Match size with other pages*, content covers the whole cover | First/last image (or as the person says) → content = full page, margins 0, `match_size=false` | `page_set_content(mode=full)`, `pages_set_layout` |
| 9 | *Output*: text B/W 600 DPI; covers and picture pages Color/Grayscale 300 DPI or *Mixed* | Default B/W 600; covers color 300; detects illustrations and proposes mixed/color | `check_pages(kind=pictures)`, `pages_set_output` |
| 10 | Save the project | The project is saved after every change (with a `.bak`) | (automatic) |
| 11 | Front cover → *Output → Play* | Runs filter 6, spot-checks results | `run_stages(4, 6)`, `export_summary` |

Ask the person for approval (like craft-anything's approval card): after step 1
(orientation/spreads), after 3–5 (list of changed content boxes with
thumbnails), after 8–9 (covers and picture pages) and before 11.

## Agent procedure (what `workflow_guide` returns)

1. `project_create(images_dir, project)`. Report the DPI note.
2. `image_preview(project, "first3")` and one from the middle
   (`images=[N/2]`). Decide:
   - **orientation**: text must read normally. `right` = rotate 90° clockwise,
     `left` = 90° counter-clockwise, `upsidedown`, `none`.
   - **layout**: `2` if each scan is an open book (two pages side by side),
     `1` if single pages, `0` (auto) only when unsure.
   - Ask the person to confirm; show them the preview.
3. `run_stages(project, 1, 5, orientation=…, layout=…)`, then
   `job_status(job_id, wait_s=60)` until `state` is `ok`.
   Roughly 10–25 s per scan on a desktop CPU.
4. **Content (width):** `pages_list(sort_by="z_width", descending=true, limit=12)`
   and the same ascending. Preview candidates with `pages_preview` (up to 16
   per sheet). The blue box must cover all printed text (including page
   numbers and running heads) and nothing else (no spine shadow, fingers,
   scan junk). Fix with `page_set_content` (`expand_*` in page px; negative
   shrinks).
5. **Content (height / page numbers):** `check_pages(kind="content")`. For
   every `ink_below_box`/`ink_above_box` finding, preview the page; if it is a
   page number or a running head, extend the box to include it. For
   `thin_*_line_isolated`, preview; if it is junk, shrink. For
   `empty_but_ink`, decide whether the page really is blank.
6. `run_stages(4, 5)` to propagate content changes.
7. **Margins:** `pages_set_layout("all", margins_mm=10, align_v=<suggested_align_v
   from check_pages>, align_h="center")`.
8. **Layout outliers:** `pages_list(sort_by="z_height")` both ends; preview
   chapter openings and short pages. Pages whose text starts lower than usual
   in the original (chapter openings) should keep their position: use
   `align_v="bottom"` when numbers are at the bottom, or `voriginal`.
9. **Covers:** for the front and back cover pages (flag `cover_candidate`;
   confirm with a preview) → `page_set_content(mode="full")`,
   `pages_set_layout([...], margins_mm=0, match_size=false)`,
   `pages_set_output([...], color_mode="color", dpi=300)`.
10. **Pictures:** `check_pages(kind="pictures")`; preview flagged pages;
    illustrations inside text → `color_mode="mixed"`; full-page images →
    `color_mode="color", dpi=300`.
11. Show the person a summary of everything changed (pages + why) and ask
    before the final run.
12. `run_stages(5, 6)` (or `4, 6` if content changed again),
    `export_summary`. Spot-check 3–4 output TIFFs.

Units: content boxes are in page pixels of the deskewed page (same as
`page_preview`); margins in mm; DPI integers.

## Tools (MCP = CLI)

All return JSON; `*_preview` return a PNG (MCP image content).
CLI: `python -m st_mcp <tool> key=value …` (previews: `out=file.png`).

| Tool | Purpose |
|---|---|
| `workflow_guide()` | This document |
| `project_create(images_dir, project, output_dir?, dpi=300, force_dpi=false)` | Creates the `.ScanTailor`, handles DPI |
| `project_info(project)` | Page count, DPI, rotations, stage progress, running job |
| `image_preview(project, images?)` | Original scans |
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?)` | Runs filters in the background |
| `job_status(job_id, wait_s?)` | Progress / result |
| `pages_list(project, sort_by, descending, limit, offset)` | Table: id, label, content size + z-scores, margins, alignment, match size, color mode, DPI, flags (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page)` / `pages_preview(project, pages)` | Page(s) with the content box overlay |
| `page_set_content(project, page, mode=manual\|auto\|full, x,y,width,height, expand_*)` | `select-content` |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `pages_set_output(project, pages, color_mode, dpi, despeckle)` | `output` |
| `check_pages(project, pages, kind=content\|pictures)` | Heuristics with evidence |
| `export_summary(project)` | Output TIFF list and anything missing |

## Verified so far (2026-11-20, Onetti "Trumpas gyvenimas", 300 DPI JPG)

- The CLI runs headless; `--start-filter/--end-filter` run stages separately.
- All per-page settings live in the project XML and the CLI honours them when
  re-run from the project file:
  - content box: `select-content/page/params[@mode=manual]/content-rect` (px);
  - margins: `page-layout/page/params/hardMarginsMM` (mm);
  - alignment: `page-layout/.../alignment@vert,@hor`;
  - *Match size with other pages* off = `alignment@null="1"`;
  - colour mode / DPI: `output/page/params/color-params@colorMode`, `dpi`.
- Default settings do not fit this book: scans are rotated 90° (needs
  `orientation=right, layout=2`). Without a preview the agent would not notice.
- The CLI reads DPI from the JPGs; `--dpi` only matters when it is missing.
- Preview geometry re-implements `ImageTransformation` and matches
  ScanTailor's `page-rect` to the pixel.
- Speed: ~23 s per spread (14 cores), all stages.

## Implementation plan

1. **P1 — Python MCP server** (`tools/st-mcp/`): calls `scantailor-universal-cli`,
   edits project XML, renders previews with Pillow. Fast to build; runs on
   Windows and Linux.
2. **P2 — native C++ commands** `scantailor-universal-cli <subcommand> --json`
   for the same tools, previews rendered by `src/core` (exactly like the GUI),
   per-page processing (the CLI currently always processes all pages); the MCP
   server becomes a thin layer.
3. **P3 — evaluation:** several books with a person's manual result as ground
   truth; measure how many pages the agent gets right without help.
