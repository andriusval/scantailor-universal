# Agent task: boxes larger than the median, and library marks

You clean the pages of a book series where libraries put their marks.
Do this work after `series_fix.py` and `review_sheets.py`, before the output step.
Do part 1 first: a narrower box removes most of a mark, and a fill zone covers only what stays.

## Part 1: boxes larger than the book median

1. Run `python3 series/size_gate.py <series>`.
   It writes the list to `st-review/size-gate.json`: the text pages with a box larger than the book median.
   The limits are 3 mm more width, or 3 % more height.
   It draws each page with a page px grid in `st-review/size-sheets/`.
2. The list only shows where to look. Never cut a box to the median size without a look at the page.
3. For each page, find the side where a stamp, a sticker or handwriting made the box larger.
   Choose a point on the outermost printed text of that side, as a double-click in the GUI:
   - `left` or `right`: a letter at the start or the end of a printed line;
   - `bottom`: the printed page number, or the last printed line;
   - `top`: the running head, or the first printed line.
   Use a close view to read the point: `python3 series/stamp_sheet.py <series> --only <book> --label <label> --roi x0,y0,x1,y1`.
4. A page that is really larger (title picture, table, contents) stays as it is. Say this in the report.
5. Write the points into one JSON file: `[{"book": "...", "page": "<label>", "x": 1428, "y": 2033, "side": "right"}]`.
6. Run `python3 series/size_gate.py <series> --snap <points.json>`.
   It keeps `projektas.before-snap.ScanTailor`, snaps the edges, runs filters 4-5 and draws the pages again (`*_after.png`).
   It adds the book to `st-review/fit-books.txt`, because the book needs a full output run.
7. Look at the after images: the box edge is on the printed text, and no printed text is outside the box.
   If an edge snapped to the wrong ink, choose a better point and snap again.

For one book outside a series, use st-mcp: `check_pages(kind="size")`, `page_preview` and `pages_snap_content(points=[...])`.

## Part 2: library marks

The library marks are on the title pages (printed pages 3 and 4), on printed page 17 and on the back cover.

### What to remove

- Remove all that is not printed: stamps, handwritten numbers, pencil marks, ticks, library stickers and their barcodes.
- Keep all printed text, printed lines, printed numbers, the ISBN barcode of the book and the publisher logo.
- The printed text of a book has a regular font, straight lines and a fixed column. A stamp is a frame or a block of letters at an angle, often grey or blue. Handwriting has irregular strokes.

### Find the pages

1. Run `python3 series/stamp_sheet.py <series> --only <book>`.
   It draws printed pages 3, 4, 17 and the back cover with a page px grid.
   The content box is green, and the fill zones in the project are blue.
2. Read the printed page number on the image. If the page is not the expected page, run the tool again with `--around 2` and find the correct page.
   - If the printed number is one less than the project page, the front cover scan is possibly missing. Say this in the report of the book.
   - Use `--label <page label>` for a page that you found.
3. Only the part of the page in the content box goes into the output. Ignore the marks outside the green box.

### Choose the zones

1. Make a close view of each mark: `python3 series/stamp_sheet.py <series> --only <book> --label <label> --roi x0,y0,x1,y1`.
   The grid has a line every 25 px, with a number every 100 px. Read the coordinates from this view.
2. A zone is a rectangle `[x0, y0, x1, y1]` in page px. Cover the full mark with a margin of 5 to 10 px.
3. A zone must not touch printed text. Keep 5 px between a zone and the nearest printed letter.
4. If a mark touches or crosses printed text, do not cover it. Put it on the list for a person.
5. Write all the zones of the series into one JSON file: `[{"book": "...", "label": "...", "zones": [[x0, y0, x1, y1]]}]`.
   For a sticker on a colored back cover, add `"color": "#rrggbb"` with the color of the cover around it.

### Check and write

1. Run `python3 series/add_zones.py <series> <zones.json>`. It writes the check sheets to `st-review/zones-check/` and changes no project.
2. Look at each sheet: the red zone covers the mark, and no printed letter is in a red zone. Correct the JSON and check again.
3. Run `python3 series/add_zones.py <series> <zones.json> --apply`.
   It keeps `projektas.before-zones.ScanTailor`, writes the zones and adds the pages to `st-review/zones-pages.txt`.
4. Run the output only when the person tells you to. Then add `--output`: it runs filter 6 for these pages only.

## Report

For each book give:
- the pages with a snapped box, and the larger pages that stay;
- the pages with zones and what they cover;
- the marks that touch printed text (for a person);
- the books where the expected page was not at its project number (possibly a missing cover).
