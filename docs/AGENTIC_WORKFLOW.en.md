# ScanTailor agentic workflow (MCP)

Goal: an agent does the same job a person does in the ScanTailor GUI, through
tools (each one is both a CLI command and an MCP tool), and the person only
confirms what genuinely needs a decision.

Principles (borrowed from craft-anything):
- **One engine.** GUI, CLI and MCP share `src/core` and the same `.ScanTailor`
  project XML. The agent can stop and a person can open the project in the GUI
  and carry on, or the other way round.
- **The project keeps each run.** The CLI writes the project only with
  `--save-project` (back to the input project) or `--output-project=<file>`.
  Without these options, the file keeps the old layout data. A later
  `--pages` run then gives pages with a different size. The
  output directory comes from the project when the command line has none
  (`--output-dir=<dir>` or the last argument overrides it). Paths inside the
  project folder are relative to that folder.
- **Checked, not guessed.** Every decision is based on a preview image or on
  numbers from the project, never on assumptions.
- **Small tools.** Not "process the book" but separate stages and separate
  pages, so the agent can fix things in place.

## Manual workflow → agent steps

| # | Person in the GUI | Agent | Tools |
|---|---|---|---|
| 0 | New project, add the JPGs, *Fix DPI → 300* | Reads DPI from the images; if missing, uses 300 and says so | `project_create` |
| 1 | (Not asked by the GUI, but some books are scanned rotated) | Looks at 2–3 originals for the page layout; lets the detector find the rotation of each scan from its text and previews the flagged scans | `image_preview`, `run_stages(1, 5, orientation=auto, layout=…)`, `check_pages(kind=orientation)` |
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
   - **orientation:** use `orientation="auto"` in step 3. The detector finds
     the rotation of each scan from its text (CLI `--orientation=auto`). The
     EXIF tag is only a hint: when the text disagrees, the text wins. Check the
     letters in a zoomed crop only for the scans that step 3 flags;
   - **layout**: `2` for open-book spreads, `1` for single pages;
   - covers and other one-image scans (`001`, `9999`, …) usually
     `orientation=none, layout=1`: `images_set_geometry` for them after the auto run of step 3;
   - look at each such scan. An endpaper scan (`002`, `9998`: the inside
     cover with a map and the flyleaf) is usually a spread. Give it
     `layout=2` and the orientation that the scan shows.
3. `run_stages(project, 1, 1, orientation="auto", layout=…)` first, because
   `orientation="auto"` writes the rotation of every scan with a decision.
   Then `check_pages(kind="orientation")` and preview each flagged scan: "no
   decision" (covers, photos, blank pages), "low confidence", "the EXIF tag
   disagrees", or "text runs across the book". The last one is usually a
   sideways table: the scan keeps the rotation of the book, and step 5 turns
   only its output. After that, set the covers, the endpapers and each wrong
   scan with `images_set_geometry`, and run `run_stages(project, 1, 5)`
   without `orientation` (about 0.5 s per page).
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
   - `ink_left/right_of_box` "lines may be cut off" → preview; extend if real;
   - then `pages_fit_text_column(project)`. The text column is the median
     width of the text pages. Boxes that are wider than the column get the
     left and right edges of the column. Pencil marks, stamps and inventory
     numbers in the side margin drop out, and hyphens at the line ends stay
     in. Preview the pages with a large cut (review note). Also preview the
     `taller_pages`, because their box often holds a stamp or a mark above
     or below the text. Correct these in the size gate (step 9) with your
     own snap points.
5. **Picture pages:** `check_pages(kind="pictures")`; preview every flagged
   page (zoom into the illustration). The box of `all_content` does not
   include thin strips at the page edges, the spine shadow or the paper
   below the caption.
   - `deskew_disagree` lists the pages where the text lines and the picture
     (or the page edges) give angles more than 0.5° apart. Preview each
     page and set the angle that looks straight. In the corrections of 4
     books, the person chose the picture or page-edge angle each time;
   - black-and-white line art (engravings, drawings, maps) → keep output
     **B/W 600**;
   - photos, halftones and color plates → `pages_straighten_pictures`,
     because ScanTailor deskews by text lines and can tilt photos. Then
     `run_stages(3, 5)`, `page_set_content(mode="all_content")`
     (photo + caption), then
     `pages_set_output(color_mode="color", dpi=300, white_margins=true,
     equalize_illumination=true, grayscale_first=<see below>)` and
     `pages_set_layout(<picture pages>, margin_left=0, margin_right=0)`.
     With a CLI that has `--deskew-pictures`, the tool measures the angle
     from the picture edges in C++. A CLI of this fork from 2026-11-22 does
     this at stage 3 by default, so new projects get the picture angle
     already. The tool stays useful for pages that have an angle already
     and for older builds.
   - `grayscale_first` is the GUI option "Convert to grayscale first". The
     scan becomes grayscale before all other output steps. Thus the paper
     tint of a color JPEG does not change the margins, the illumination or
     the photo. The output TIFF is 8-bit gray and about 4 times smaller.
   - Read the measurement first: `check_pages(<picture pages>,
     kind="grayscale")`. A gray picture in a color scan has `grayscale=true`
     (chroma below 10). Give these pages `grayscale_first="auto"`.
   - Color plates (`grayscale=false`) must stay in color: give them
     `grayscale_first=false`.
   - Preview each page with `near_threshold=true` and set true or false
     yourself. After stage 6, `job_status` shows the result of ScanTailor in
     `grayscale_auto`. Report the pages with `confidence="low"`.
   - a table or picture printed sideways (`check_pages` finding
     `text_runs_vertically`) → keep the book orientation in stages 1–5.
     Then deskew, the content box, the margins and the page size stay the
     same as on the other pages. Preview the page and set
     `pages_set_output(<pages>, output_rotation=270)` when the text lines run
     from top to bottom, or `output_rotation=90` when they run from bottom to
     top. Only the output TIFF turns (CLI `--output-rotate`, clockwise), and
     with 90 or 270 it is landscape. Then run stage 6 on these pages. After a
     first stage 6 run, the finding also has `detected_output_rotation`.
     Alternatively, `run_stages(6, 6, pages=<pages>, output_rotate="auto")`
     lets the detector set the rotation from the finished page (CLI
     `--output-rotate=auto`). After stage 6, `check_pages(kind="orientation")`
     lists the output pages whose text needs another rotation; preview them.
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
   median text-block width plus 10 mm on each side. Pages with wider content
   receive left and right margins 0. Match size and the centered horizontal
   alignment keep them on the common page. Run it after the last content box change.
9. **Alignment:** `pages_align_auto(project)`. The groups come from the
   content height against the book median and from the place of the text.
   The usual top and bottom lines of the full pages give that place. Full pages go to the
   page-number side (`top` or `bottom`; `number_position=auto` reads it from a
   sample of full pages). Picture pages go to `vcenter`. Sparse text (title,
   half-title, epigraph, dedication) goes to `vcenter`. Text that starts on the
   usual top line goes to `top`: chapter ends and full text pages without the
   page-number band. Text that starts lower and ends on the usual bottom line
   goes to `bottom`: chapter starts. Other small blocks (chapter or part
   titles, colophons) go to `vcenter`. Preview the pages a bit lower than the
   median and the `review` list. Chapter starts far from the usual
   chapter-start height go to that list. The rules stay the same for the whole
   book. Correct single pages with `pages_set_layout`. When the previews show
   tables or diagrams, add `review_full_pages=true`: it lists full-height
   pages with few text lines, such as rotated tables.
   **Optional, horizontal:** `pages_align_horizontal(project)`. In most
   poetry books, the narrow pages start on the same left line as the full
   pages. Use the tool only when the previews show this. Pages on the usual left line
   go to `left`, and pages only on the usual right line go to `right`. All
   of them get the same margin on the aligned side, so that their text
   starts at the same place. The other side gets 0, so the page width grows
   only by one margin. Other pages go to `hcenter`, with side margins that
   do not make the page wider than the aligned pages. The front matter,
   line-art pages and pages without a usual line keep their settings. Run it
   after `pages_fit_width`. Then run stages 5-6. `pages_fit_width` keeps the
   margins of `left` and `right` pages, but after a later `pages_fit_width`,
   run `pages_align_horizontal` again.
   **Size gate before the output:** `check_pages(kind="size")` lists the
   body pages with a box wider than the book median by more than 3 mm, or
   taller by more than 3 %. After `pages_fit_text_column` (step 4) it lists
   only the pages that the free column fit could not fix. The gate only
   lists the pages: never cut a box to the median size. Preview each page
   with `page_preview(grid=true)` and find the side with the mark. Read the
   point on the outermost printed text of that side from a close view
   (`page_preview(roi=[x0, y0, x1, y1])`). Double-click it with
   `pages_snap_content(points=[{page, x, y, side}])`, as in the GUI, so that
   the edge snaps to the printed text. Cover a stamp, a sticker or a
   handwritten note that stays inside the box with `page_add_fill_zone`.
   Give a rough box with `fit=true` and `dry_run=true`: the tool fits the
   zone to the ink of the mark. Do not keep a zone that has `crossings`
   on printed text. A crossing shows ink on both sides of a zone edge.
   A page from `check_pages(kind="size")` with `stray_ink` has only a small
   ink group at that side: preview it, and snap to its `point`.
   Remove only what is not printed, and keep 5 px between a zone and the
   nearest printed letter. Check the planned zones first with
   `page_preview(zones=[...])`. A mark that touches printed text stays
   for a person. A page that is really larger (title picture, table,
   contents) stays, with a `review_note` that gives the reason. Make the
   output only when the list has no page without a reason.
   Then `run_stages(4, 6)`,
   `export_summary`, and run the gate after the output:
   `check_pages(kind="output")`. Each page must have a readable TIFF, and
   all pages that match the book size must have one TIFF size. Another
   size shows an old page layout: run stages 5–6 for the whole book.
   Look at 8–16 output pages (corrected ones, picture pages, covers).
10. **Review sheet:** `review_sheet(project)` writes
    `out-agentas/review/index.html`. The page shows a thumbnail of each output
    page and the content box preview of each page with an agent decision.
    Each decision has a confidence and a reason. Pages with a low confidence
    or a flag come first, with the marker "CHECK IN GUI". The tools record
    their decisions in `out-agentas/review/decisions.json`. Record your own
    decisions (orientation, covers, picture pages that stay B/W) with
    `review_note(project, pages, decision, reason, confidence)`. Run the sheet
    before `project_for_windows`, because the CLI previews need paths that
    the CLI can open.
    st-mcp removes the warning "a rotated table?" (`pages_align_auto`) when
    the orientation detector finds upright text in the output file
    (`upright_confirmed`). A page with too little text for the detector, or
    with a `text_runs_vertically` finding of `check_pages`, stays on the list.
11. `project_for_windows(project)` when the GUI on a different system opens
    the project (for example, processed under WSL and opened on Windows).
    With `relative=true`, the paths are relative to the project, so the
    project opens on each system.
    It also keeps `agentas.orig.ScanTailor`, a copy of the finished project.
    Remove the job logs and the run backups. Never remove the `.orig` copy.
    Later, `project_diff(orig, corrected)` compares it with the corrections
    of the person, so that we find new rules.
12. Summarise: pages changed and why, picture decisions, anything left for a
    person (e.g. stamps or stains inside the text). List the removal
    candidates from `pages_align_auto`: blank leaves, a half-title that
    repeats the title page, and near-empty pages. Also list its review pages:
    full-height pages with few text lines, such as rotated tables. Do not
    remove them: the person decides. List the pages to check from
    `review_sheet` (`to_check`) and give the path of the review sheet. Write
    the summary to `out-agentas/review/report.md`.

## Book queue (overnight runs)

`tools/st-mcp/run_queue.py books.txt` runs this workflow on each book folder
in `books.txt`, one headless Claude Code session (`claude -p`) per book. The
prompt holds the rules and the step list above, and asks for the report in
`out-agentas/review/report.md`. The file `books.state.json` keeps the status
of each book, so a new start resumes after a crash and skips the finished
books. The folder `books-logs/` has one log per book.

At the end, `books-summary.md` has one line per book with a link to its
review sheet. Use `--dry-run` to see the commands first. The details are in
`tools/st-mcp/README.md`.

## Corrections log

After the person corrects a book in the GUI, run
`corrections_record("<book>/agentas.orig.ScanTailor", "<book>/agentas.ScanTailor")`.
It writes one JSONL record for each changed page parameter: book folder, page,
stage, field, agent value, user value and category. The log is
`~/.local/share/st-mcp/corrections.jsonl`; set `ST_CORRECTIONS_LOG` to use a
different file. The tool skips a change that is already in the log for the
same book, so you can run it after each review.

Categories:
- pages and geometry: `page_removed`, `page_added`, `split`, `rotation`,
  `deskew_picture`, `deskew_text`, `page_box`;
- content box: `content_empty`, `content_box_trim` (a smaller box),
  `content_box_extend`, `content_box_numbers` (a small band at the top or bottom);
- covers: `cover` (a box, layout or output change on a cover page);
- layout: `margins`, `alignment`, `match_size`;
- output: `output_mode`, `dpi`, `picture_zones`; all else: `other`.

The tool skips a content box change after a new rotation, split or
deskew angle, because the box coordinates then do not compare.

`corrections_stats()` shows the categories that occur most, and the workflow
rule that each one points to. Improve that rule first.

Units: content boxes are in page pixels of the deskewed page (same as
`page_preview`); margins in mm; DPI integers.

## Tools (MCP = CLI)

All return JSON; `*_preview` return a PNG (MCP image content).
CLI: `python -m st_mcp <tool> key=value …` (previews: `out=file.png`).

| Tool | Purpose |
|---|---|
| `workflow_guide()` | This document |
| `project_create(images_dir, project, output_dir?, dpi=300, force_dpi=false)` | Creates the `.ScanTailor`, handles DPI |
| `project_add_front_cover(project, image?)` | Adds a cover image (for example from a book shop) as the first page of a finished project; stages 1–5 for the cover only |
| `project_info(project)` | Page count, DPI, rotations, stage progress, running job |
| `image_preview(project, images?)` | Original scans |
| `run_stages(project, start, end, orientation?, layout?, layout_direction?, dpi?, content_detection?, wait_s?, pages?, output_rotate?)` | Runs filters in the background; `pages` limits the run to some pages (CLI `--pages`); progress and page errors come from CLI `--json-progress`; `orientation=auto` and `output_rotate=auto` use the orientation detector |
| `job_status(job_id, wait_s?)` | Progress / result |
| `pages_list(project, sort_by, descending, limit, offset)` | Table: id, label, content size + z-scores, margins, alignment, match size, color mode, DPI, flags (`outlier_width`, `outlier_height`, `empty_content`, `cover_candidate`) |
| `page_preview(project, page, engine?, grid?, roi?, zones?)` / `pages_preview(project, pages, engine?)` | Page(s) with the content box overlay; `engine="cli"` lets ScanTailor draw them (CLI `--render-preview`), as the GUI shows them; `grid`/`roi`/`zones` give a page px grid, a close view and planned fill zones |
| `page_set_content(project, page, mode=manual\|auto\|full\|empty\|cover\|tighten\|include_numbers\|all_content, x,y,width,height, expand_*)` | `select-content` |
| `pages_snap_content(project, pages, sides=auto\|all\|[left,right,top,bottom], points?, wait_s?)` | GUI double-click edge snap via `--content-snap`, runs stages 4–5; `points` are your own clicks on the printed text |
| `pages_set_layout(project, pages, margins_mm, margin_*, align_v, align_h, match_size)` | `page-layout` |
| `project_diff(original, corrected, record?)` | Per-page differences between the agent's project and a corrected one; `record=true` also writes them to the corrections log |
| `corrections_record(original, corrected, book?, log?)` | Appends the person's corrections to the corrections log (JSONL), one record per changed page parameter, without repeats |
| `corrections_stats(log?, book?, top?)` | Corrections per category and per book, and the most frequent categories with their workflow rule |
| `pages_fit_text_column(project, tolerance_mm?, height_ratio?)` | Narrows content boxes that are wider than the text column of the book; lists the pages with a taller box |
| `pages_fit_width(project, margin_mm?, percentile?)` | One page width: side margins 0 for pages wider than the median text block |
| `pages_align_auto(project, number_position?, title_ratio?)` | Vertical alignment for all body pages, plus removal candidates for the report |
| `pages_align_horizontal(project, margin_mm?, line_tolerance_mm?)` | Optional: left or right alignment by the usual text lines, for books such as poetry |
| `pages_set_output(project, pages, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color?, fill_offcut?, grayscale_first?, output_rotation?)` | `output`; `grayscale_first` (true, false or "auto") writes `color-or-grayscale@convertToGrayscale="1"` or `"auto"` (CLI `--grayscale-first`); `output_rotation` (0/90/180/270, clockwise) turns only the output TIFF (CLI `--output-rotate`) |
| `page_add_fill_zone(project, page, x, y, width, height, color?)` | Output fill zone (page px) for a stamp or a note inside the box; stored in original image px, shown in the GUI |
| `check_pages(project, pages, kind=content\|size\|pictures\|grayscale\|orientation)` | Heuristics with evidence; `size` lists the boxes larger than the median (the gate before the output); `grayscale` gives the chroma per page, the classification and the margin to the threshold; `text_runs_vertically` marks sideways tables; `orientation` runs the detector (CLI `--detect-orientation`) on the scans and the output files and lists the ones that need a look |
| `export_summary(project)` | Output TIFF list and anything missing |
| `review_note(project, pages, decision, reason?, confidence?, topic?)` | Records a decision of the agent for the review sheet |
| `review_sheet(project, pages?, thumb_px?, engine?)` | Writes `out-agentas/review/index.html`: thumbnails, decisions with confidence, doubtful pages first |
| `pages_straighten_pictures(project, pages)` | Re-measure skew of photo pages from the printed rectangles |
| `project_for_windows(project, relative?)` | Write the project paths for the GUI on a different system: relative to the project, or /mnt/c as C:/ (last step) |

## CLI options for st-mcp

`--dump-masks=<directory>` writes the analysis masks of filter 4 (select content) for each page after the run.
With `--pages`, it writes them only for the selected pages.
The files use the page name of `--render-preview` and of the output TIFF:

- `<page>_content.png`: the ink, as the content box snap (`--content-snap`) sees it;
- `<page>_text.png`: the ink that `ContentBoxFinder` keeps as text;
- `<page>_garbage.png`: the dark areas that touch the page border (shadows, scan borders);
- `<page>.json`: the page data and the transforms.

The JSON file gives the scan file, the sub-page, the image size, the DPI and the rotation.
It also gives the deskew angle, the crop area, the page box and the content box.
`crop_area` uses the coordinates of the page outline in the project: the scan after the DPI scale and the rotation.
The transforms are `original_to_page`, `original_to_mask` and `page_to_mask`.

The masks are 1-bit PNG files at 150 DPI, and white is a set pixel.

A transform is the list `m11 m12 m21 m22 dx dy` of `QTransform`: `x' = m11*x + m21*y + dx`, `y' = m12*x + m22*y + dy`.
Mask pixel `(x, y)` covers the mask coordinates from `(x, y)` to `(x+1, y+1)`.
To get the scan pixel of a mask pixel, use the inverse of `original_to_mask`.
If the CLI cannot write the masks of a page, it sends the `--json-progress` event `warning` (`dump_masks_failed`), and the run continues.
`--start-filter=5 --end-filter=4` runs no filter, so the masks then come from the saved results of filter 4.
`check_pages(kind=content)` uses the content mask when the CLI has this option.

## Verified so far (2026-11-20, Onetti "Trumpas gyvenimas", 300 DPI JPG)

- The CLI runs headless; `--start-filter/--end-filter` run stages separately.
- All per-page settings live in the project XML and the CLI honours them when
  re-run from the project file:
  - content box: `select-content/page/params[@mode=manual]/content-rect` (px);
  - margins: `page-layout/page/params/hardMarginsMM` (mm);
  - alignment: `page-layout/.../alignment@vert,@hor`;
  - *Match size with other pages* off = `alignment@null="1"`;
  - colour mode / DPI: `output/page/params/color-params@colorMode`, `dpi`;
  - "Convert to grayscale first": `color-params/color-or-grayscale@convertToGrayscale="1"`
    (on) or `"auto"` (a missing attribute means off). The CLI sets it with
    `--grayscale-first`, `--grayscale-first=auto` or `--grayscale-first=false`.
    With auto, stage 6 stores the result in
    `output-params/image@grayscaleChroma,@grayscaleDetected`, and with
    `--json-progress` the `page_done` event gives `grayscale_first`,
    `grayscale_detected` and `chroma`. Use it with `--pages` to change only
    some pages, for example
    `--start-filter=6 --pages="021.jpg;095.jpg:L" --grayscale-first`.
    `--white-margins`, `--normalize-illumination` and the layer options reset
    it, so give it again together with them.
  - output rotation: `output/page/params@outputRotation` (clockwise degrees;
    a missing attribute is 0). CLI: `--output-rotate=<0|90|180|270|auto>`,
    together with `--pages` for single pages.
  - orientation detector (2026-11-21): `--orientation=auto` (stage 1, whole
    scan before the split) and `--output-rotate=auto` (stage 6, finished
    page). With `--json-progress`, `page_done` has `orientation_detected` or
    `output_rotation_detected` (null without a decision), `confidence` (0–1)
    and `reason`. `--detect-orientation=<list file>` prints one JSON line per
    image and changes nothing.
  - picture deskew (2026-11-22): on a page that is mostly a photo or a plate,
    stage 3 finds the auto angle from the straight picture edges. It is on by
    default (`--deskew-pictures`); `--deskew-pictures=false` (or `=0`) turns it
    off. Pages that have deskew parameters keep their angle. The GUI setting
    is "Deskew picture pages by the picture edges" (Settings, Deskew).
    `deskew/page@pictureEdges="0|1"` keeps the choice of one page. A page
    without the attribute follows the global setting.
    `deskew/page/params@estimator` (`text`, `picture` or `none`) tells which
    estimator gave the auto angle.
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
- CLI `--threads=<N>` processes N pages of a stage at the same time; the next
  stage starts only after all pages. Each page uses one OpenMP thread, so the
  result is the same as with one thread (TIFFs byte for byte). If the aggregate
  page size changes in stage 6, the CLI gives a warning: then run stages 5-6.
  36 scans, 72 pages, all stages: N=1 ~100 s, N=4 ~38 s, N=8 ~25 s,
  N=14 ~22 s. Each parallel page needs about 170 MB of memory.
  st-mcp passes `--threads` with half of the cores (`ST_CLI_THREADS`).

## Implementation plan

1. **P1 — Python MCP server** (`tools/st-mcp/`): calls `scantailor-universal-cli`,
   edits project XML, renders previews with Pillow. Fast to build; runs on
   each system that runs the CLI.
2. **P2 — native C++ commands** `scantailor-universal-cli <subcommand> --json`
   for the same tools, previews rendered by `src/core` (exactly like the GUI),
   per-page processing (the CLI currently always processes all pages); the MCP
   server becomes a thin layer.
3. **P3 — evaluation:** several books with a person's manual result as ground
   truth; measure how many pages the agent gets right without help.
