# Agent task: remove library marks with fill zones

You clean the pages of a book series where libraries put their marks.
These pages are the title pages (printed pages 3 and 4), printed page 17 and the back cover.
Do this step after `fit_box.py`, before the output step.

## What to remove

- Remove all that is not printed: stamps, handwritten numbers, pencil marks, ticks, library stickers and their barcodes.
- Keep all printed text, printed lines, printed numbers, the ISBN barcode of the book and the publisher logo.
- The printed text of a book has a regular font, straight lines and a fixed column. A stamp is a frame or a block of letters at an angle, often grey or blue. Handwriting has irregular strokes.

## Find the pages

1. Run `python3 series/stamp_sheet.py <series> --only <book>`.
   It draws printed pages 3, 4, 17 and the back cover with a page px grid.
   The content box is green, and the fill zones in the project are blue.
2. Read the printed page number on the image. If the page is not the expected page, run the tool again with `--around 2` and find the correct page.
   - If the printed number is one less than the project page, the front cover scan is possibly missing. Say this in the report of the book.
   - Use `--label <page label>` for a page that you found.
3. Only the part of the page in the content box goes into the output. Ignore the marks outside the green box.

## Choose the zones

1. Make a close view of each mark: `python3 series/stamp_sheet.py <series> --only <book> --label <label> --roi x0,y0,x1,y1`.
   The grid has a line every 25 px, with a number every 100 px. Read the coordinates from this view.
2. A zone is a rectangle `[x0, y0, x1, y1]` in page px. Cover the full mark with a margin of 5 to 10 px.
3. A zone must not touch printed text. Keep 5 px between a zone and the nearest printed letter.
4. If a mark touches or crosses printed text, do not cover it. Put it on the list for a person.
5. Write all the zones of the series into one JSON file: `[{"book": "...", "label": "...", "zones": [[x0, y0, x1, y1]]}]`.
   For a sticker on a colored back cover, add `"color": "#rrggbb"` with the color of the cover around it.

## Check and write

1. Run `python3 series/add_zones.py <series> <zones.json>`. It writes the check sheets to `st-review/zones-check/` and changes no project.
2. Look at each sheet: the red zone covers the mark, and no printed letter is in a red zone. Correct the JSON and check again.
3. Run `python3 series/add_zones.py <series> <zones.json> --apply`.
   It keeps `projektas.before-zones.ScanTailor`, writes the zones and adds the pages to `st-review/zones-pages.txt`.
4. Run the output only when the person tells you to. Then add `--output`: it runs filter 6 for these pages only.

## Report

For each book give:
- the pages with zones and what they cover;
- the marks that touch printed text (for a person);
- the books where the expected page was not at its project number (possibly a missing cover).
