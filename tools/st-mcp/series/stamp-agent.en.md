# Agent task: size gate and library marks for a series

The general rules are in `docs/AGENTIC_WORKFLOW.en.md`, step 9 (size gate): snap points on printed text, fill zones only on what is not printed.
This file gives the series part: the scripts that do the work for all books, and the pages where libraries put their marks.
Do this work after `series_fix.py` and `review_sheets.py`, before the output step.

## Part 1: boxes larger than the book median

1. Run `python3 series/size_gate.py <series>`.
   - It first runs `pages_fit_text_column` on each book: the side edges move to the text column without a model.
   - Then it writes the pages that stay larger than the median to `st-review/size-gate.json`, with the pages that got a cut of more than 3 mm.
   - It draws each page with a page px grid in `st-review/size-sheets/`.
2. For each page, find the side where a stamp, a sticker or handwriting made the box larger.
   Read a point on the outermost printed text of that side from a close view:
   `python3 series/stamp_sheet.py <series> --only <book> --label <label> --roi x0,y0,x1,y1`.
3. A page that is really larger (title picture, table, contents) stays as it is. Say this in the report.
4. Write the points into one JSON file: `[{"book": "...", "page": "<label>", "x": 1428, "y": 2033, "side": "right"}]`.
5. Run `python3 series/size_gate.py <series> --snap <points.json>`.
   It keeps `projektas.before-snap.ScanTailor`, snaps the edges, runs filters 4-5 and draws the pages again (`*_after.png`).
6. Look at the after images: the box edge is on the printed text, and no printed text is outside the box.
   If an edge snapped to the wrong ink, choose a better point and snap again.

`size_gate.py` adds each changed book to `st-review/fit-books.txt`, because the book needs a full output run.

## Part 2: library marks

The libraries of this series put their marks on six pages. These are the flyleaf (loan slip), printed pages 3, 4 and 17, the colophon (price, barcode sticker) and the back cover.

### Find the pages

1. Run `python3 series/stamp_sheet.py <series> --only <book>`.
   It draws these six pages with a page px grid. The colophon is the last page with content before the back cover.
   The content box is green, and the fill zones in the project are blue.
2. Read the printed page number on the image. If the page is not the expected page, run the tool again with `--around 2` and find the correct page.
   - If the printed number is one less than the project page, the front cover scan is possibly missing. Say this in the report of the book.
   - A cover image from a book shop goes in with st-mcp `project_add_front_cover`. Then set the page offset of the book to 0 in `st-review/page-offset.json`.
   - Use `--label <page label>` for a page that you found.
3. Only the part of the page in the content box goes into the output. Ignore the marks outside the green box.
4. A loan slip on the flyleaf: make the page empty (st-mcp `page_set_content(mode="empty")`), as it came from the printer.

### Choose, check and write the zones

1. Read each zone `[x0, y0, x1, y1]` (page px) from a close view (`--roi`). Cover the full mark with a margin of 5 to 10 px.
   Do not read coordinates from a scaled thumbnail. To fit a rough box to the ink, use st-mcp `page_add_fill_zone(fit=true, dry_run=true)`.
2. Write all the zones of the series into one JSON file: `[{"book": "...", "label": "...", "zones": [[x0, y0, x1, y1]]}]`.
   For a sticker on a colored back cover, add `"color": "#rrggbb"` with the color of the cover around it.
3. Run `python3 series/add_zones.py <series> <zones.json>`. It writes the check sheets to `st-review/zones-check/` and changes no project.
4. Look at each sheet: the red zone covers the mark, and no printed letter is in a red zone. Correct the JSON and check again.
   The script lists each crossing: ink on both sides of a zone edge. Look at these pages closely, because a crossing can cut printed text.
5. Run `python3 series/add_zones.py <series> <zones.json> --apply`.
   It keeps `projektas.before-zones.ScanTailor`, writes the zones and adds the pages to `st-review/zones-pages.txt`.
6. Run the output only when the person tells you to. Then add `--output`: it runs filters 5-6 for these pages only.

## Part 3: output and the check after it

1. Merge all lists into one run: `python3 series/output_script.py <series> <name> --full <books.txt> --pages <pages.txt>`.
   It writes `<name>.sh` (Linux CLI) and `<name>.ps1` (Windows CLI) in `st-review`. Both run filters 5-6 with `--output-project`.
   A page run of filter 6 alone keeps an old page size, so the scripts always start at filter 5.
2. After the run: `python3 series/output_check.py <series> --zones <zones.json>`.
   It lists the books with a missing or bad TIFF or with a page of another size. It also lists the zones that are not in a project.
   A book with a page of another size needs a full run of filters 5-6.
3. Look at the output, not at the project: `python3 series/out_sheet.py <series>` draws the six pages of each book from the output TIFFs.
   With `--zones <zones.json>`, it draws only the pages of that list.

## Report

For each book give:
- the pages with a snapped box, and the larger pages that stay;
- the pages with zones and what they cover;
- the marks that touch printed text (for a person);
- the books where the expected page was not at its project number (possibly a missing cover).
