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
| 6 | *Margins*: 10 mm left/right, 5 mm top/bottom → *Apply → All pages*; align to where page numbers are (top/bottom) | Takes the page-number position from step 5 votes; applies to all | `pages_set_layout(all, margin_left=10, margin_right=10, margin_top=5, margin_bottom=5, align_v=top\|bottom)` |
| 7 | Sort by height, check the tallest — does it match the book's layout? Some pages need centring, some top alignment | Previews the outliers (chapter openings, short pages, illustrations) and picks alignment | `pages_preview`, `pages_set_layout` |
| 8 | Covers: margins 0, untick *Match size with other pages*, content covers the whole cover | First/last image (or as the person says) → content = full page, margins 0, `match_size=false` | `page_set_content(mode=full)`, `pages_set_layout` |
| 9 | *Output*: text B/W 600 DPI; covers and picture pages Color/Grayscale 300 DPI or *Mixed* | Default B/W 600; covers color 300; detects illustrations and proposes mixed/color | `check_pages(kind=pictures)`, `pages_set_output` |
| 10 | Save the project | The project is saved after every change (with a `.bak`) | (automatic) |
| 11 | Front cover → *Output → Play* | Runs filter 6, spot-checks results | `run_stages(4, 6)`, `export_summary` |

The workflow runs **to the end without stopping for approval**. The agent
decides from previews and numbers, and writes every decision (pages + why) into
its final summary; the person checks the result by opening the project in the
GUI.

## Agent procedure (what `workflow_guide` returns)

1. `project_create(images_dir, project, output_dir)`. If the folder already
   has the person's own project (e.g. `projektas.ScanTailor`) or `out/`, never
   touch them: use `agentas.ScanTailor` and `out-agentas/`.
2. `image_preview(project, images=[0, 1, 3, N/2, -2, -1])` (indices). Decide:
   - **orientation** from the direction of the letters in a zoomed crop:
     `right` = rotate 90° clockwise, `left`, `upsidedown`, `none`;
   - **layout**: `2` for open-book spreads, `1` for single pages;
   - covers and other one-image scans (`001`, `9999`, …) usually
     `orientation=none, layout=1`: `images_set_geometry` for them;
   - look at each such scan. An endpaper scan (`002`, `9998`: the inside
     cover with a map and the flyleaf) is usually a spread. Give it
     `layout=2` and the orientation that the scan shows.
3. `run_stages(project, 1, 5)` (about 0.5 s per page).
4. **Content:** `check_pages(kind="content")` and the widest/tallest pages
   from `pages_list`. Preview what is flagged (`pages_preview`, zoom on edges
   when unsure). Then:
   - junk at the box sides (`thin_*_column_isolated`, `thin_*_line_isolated`,
     `box_exceeds_text`) → `pages_snap_content(pages=[all flagged], sides="auto")`
     in one call: ScanTailor's own double-click adjustment (CLI `--content-snap`)
     snaps each flagged edge to the outermost text; preview the pages after the job;
   - if a snap left junk in (e.g. junk closer than ~2.5 mm to the text) or cut
     something → `page_set_content(mode="tighten")` (keeps page
     numbers; a side whose strip has ink in 3+ lines stays — `kept_sides`; if
     the preview shows that strip is junk such as a handwritten library mark,
     set the edge with `mode="manual"`);
   - ink above/below with hint "looks like a page number missed" →
     `mode="include_numbers"`;
   - only specks/bleed-through on a blank leaf → `mode="empty"`;
   - `ink_left/right_of_box` "lines may be cut off" → preview; extend if real.
5. **Picture pages:** `check_pages(kind="pictures")`; preview every flagged
   page (zoom into the illustration):
   - black-and-white line art (engravings, drawings, maps) → keep output
     **B/W 600**;
   - grayscale illustrations (photos, halftones) →
     `pages_straighten_pictures` (ScanTailor deskews by text lines and can
     tilt photos), `run_stages(3, 5)`, `page_set_content(mode="all_content")`
     (photo + caption, no spine shadow), then
     `pages_set_output(color_mode="color", dpi=300, white_margins=true,
     equalize_illumination=true)` and
     `pages_set_layout(<picture pages>, margin_left=0, margin_right=0)`.
6. **Covers and endpapers:** `page_set_content(mode="cover")`, then
   `pages_set_layout(margins_mm=0, match_size=false)` and
   `pages_set_output(color_mode="color", dpi=300)`. The cover mode also finds
   dark covers on the black scanner lid. It moves each edge to the cover edge
   and then 1 mm inside, so that no background shows. Zoom on the corners in
   the preview.
7. **Margins:** `pages_set_layout("all", margin_left=10, margin_right=10,
   margin_top=5, margin_bottom=5, align_v=<suggested_align_v>,
   align_h="center")`, then the covers again (step 6 layout).
8. **One page width:** `pages_fit_width(project)`. The page width is the
   typical text-block width plus 10 mm on each side. Pages with wider content
   get smaller left and right margins, down to 0, so that they do not make
   the book wider.
9. **Alignment:** `pages_align_auto(project)`. Front matter (title,
   copyright, dedication, epigraph) goes to `vcenter`, but a small logo at
   the top stays `top`. Section starts (the text starts lower than usual) go
   to `bottom`. Short pages (chapter ends, lists) and full text pages without
   the page-number band go to `top`. Other pages go to `bottom`. When the
   previews show tables or diagrams, add `review_full_pages=true`: it lists
   full-height pages with few text lines, such as rotated tables. Preview the `top` and `vcenter` pages, and
   correct single pages with `pages_set_layout`. Then `run_stages(4, 6)`,
   `export_summary`, and check that all body pages have the same size
   (`pages_list(sort_by="content_width", descending=true)`). Look at 8–16
   output pages (corrected ones, picture pages, covers).
10. `project_for_windows(project)` when the project was processed under WSL,
    so that the Windows GUI opens it. With a Windows build from this branch,
    use `relative=true`: the project then opens under WSL and Windows.
    It also keeps `agentas.orig.ScanTailor`, a copy of the finished project.
    Remove the job logs and the run backups. Never remove the `.orig` copy.
    Later, `project_diff(orig, corrected)` compares it with the corrections
    of the person, so that we find new rules.
11. Summarise: pages changed and why, picture decisions, anything left for a
    person (e.g. stamps or stains inside the text). List the removal
    candidates from `pages_align_auto`: blank leaves, a half-title that
    repeats the title page, and near-empty pages. Also list its review pages:
    full-height pages with few text lines, such as rotated tables. Do not
    remove them: the person decides.

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
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?, pages?)` | Runs filters in the background; `pages` limits the run to some pages (CLI `--pages`); progress and page errors come from CLI `--json-progress` |
| `job_status(job_id, wait_s?)` | Progress / result |
| `pages_list(project, sort_by, descending, limit, offset)` | Table: id, label, content size + z-scores, margins, alignment, match size, color mode, DPI, flags (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page, engine?)` / `pages_preview(project, pages, engine?)` | Page(s) with the content box overlay; `engine="cli"` lets ScanTailor draw them (CLI `--render-preview`), as the GUI shows them |
| `page_set_content(project, page, mode=manual\|auto\|full\|empty\|cover\|tighten\|include_numbers\|all_content, x,y,width,height, expand_*)` | `select-content` |
| `pages_snap_content(project, pages, sides=auto\|all\|[left,right,top,bottom], wait_s?)` | GUI double-click edge snap via `--content-snap`, runs stages 4–5 |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `project_diff(original, corrected)` | Per-page differences between the agent's project and a corrected one |
| `pages_fit_width(project, margin_mm?, percentile?)` | One page width: smaller side margins for pages wider than the typical text block |
| `pages_align_auto(project, short_ratio?, missing_number_ratio?)` | Vertical alignment for all body pages, plus removal candidates for the report |
| `pages_set_output(project, pages, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color?, fill_offcut?)` | `output` |
| `check_pages(project, pages, kind=content\|pictures)` | Heuristics with evidence |
| `export_summary(project)` | Output TIFF list and anything missing |
| `pages_straighten_pictures(project, pages)` | Re-measure skew of photo pages from the printed rectangles |
| `project_for_windows(project, relative?)` | Write /mnt/c paths as C:/ for the Windows GUI, or as paths relative to the project (last step under WSL) |

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
- Speed: with `OMP_NUM_THREADS=1` (st-mcp default) the whole book (191 scans,
  380 pages, all stages) takes ~6 min; ScanTailor's default OpenMP threading was
  ~9× slower (54 min) and not deterministic.
- A new run: the output stage does not write a TIFF again when its parameters
  and its file did not change (as in the GUI). On 8 scans, stages 4–6 took 5 s, not 38 s.
  The `page_done` line (stage 6) of such a page has `"skipped":true`.
  The first run on a project from an older CLI writes all pages again.
  After a change of the output code, delete the TIFFs in `out/`, because the CLI keeps them.

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
