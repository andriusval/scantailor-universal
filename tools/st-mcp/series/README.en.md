# Series scripts

Scripts for one book series. They apply the rules of two projects that a person corrected by hand.
No step uses OCR. An agent looks at the page images (see `stamp-agent.en.md`).
It snaps the box edges of the pages larger than the book median to the printed text. It covers the library marks with fill zones.

Run the scripts from `tools/st-mcp` with a `python3` that has `scipy` (`pip install -e .[series]`).
They find `scantailor-universal-cli` as st-mcp does (`ST_CLI`, the build folder or `PATH`).
They also need the system library `zbar` for the ISBN barcode (Ubuntu: `libzbar0`).
The ScanTailor CLI gives the same result on each system, so the steps run on each system.

## Order

1. Make `projektas.ScanTailor` in each book folder with filters 1-5: st-mcp `project_create` and `run_stages(1, 5)`, or `st-prepare-books.ps1 -Root <series>` in PowerShell.
2. `python3 series/series_fix.py <series> --skip <book> ...` changes each project:
   - It keeps the automatic project as `projektas.auto.ScanTailor`. A second run starts from that copy.
   - Covers (first and last image): rotation 0, single page without a cut, filters 1-5 again.
   - Front cover: the content box is the cover edge (found on the dark scanner lid), margins 0, no match, Color 300.
   - Back cover: the content box is the ISBN barcode. The margins keep the barcode position on the cover. Match, left and bottom, Color 300.
   - Other pages: vertical auto alignment, horizontal centre, match, B/W 600.
   - Report: `<series>/st-review/series-fix.json` (small pages, flags), log `series-fix.log`.
3. `python3 series/review_sheets.py <series>` makes one image for each book in `st-review/sheets/`.
   The image shows the covers, the title page and the small pages (box below 70 % of the median). Correct with the st-mcp tools.
4. Size gate (`stamp-agent.en.md`, part 1). `python3 series/size_gate.py <series>` first runs `pages_fit_text_column` on each book.
   Then it lists the text pages with a box that stays larger than the book median, with the limits of `check_pages(kind="size")`.
   - The agent looks at each page and chooses a point on the outermost printed text of the side with the mark.
   - `--snap <points.json>` snaps the edges at these points, as a double-click in the GUI.
     It uses the CLI option `--content-snap`, as st-mcp `pages_snap_content(points=...)` does.
   - The changed books go into `st-review/fit-books.txt`. A page that is really larger (title picture, table, contents) stays.
5. The agent covers the library marks on the flyleaf, printed pages 3, 4 and 17, the colophon and the back cover with fill zones.
   See `stamp-agent.en.md`, part 2, and the scripts `stamp_sheet.py` and `add_zones.py`.
   The zones are in original image px, so every output run applies them and the GUI shows them (Output, Fill zones).
6. Output (filters 5-6), only when the person tells you to: `output_script.py` merges the lists into one bash and one PowerShell script.
   - A book in `fit-books.txt` needs a full output run, because a changed box changes the page size of the whole book.
   - Other books need filters 5-6 only for the listed pages. Each run passes `--output-project`, so the project keeps the new page layout.
   - Clean only with fill zones in the project. Do not change the output TIFF files: a later output run writes them again and the marks come back.
7. After the output: `output_check.py` (one page size per book, readable TIFFs, no lost zone), then `out_sheet.py` for the output pages. A mark that is still there gets a new zone (step 5) and a new output run for that page.

A book without a cover scan: put a cover image (for example from a book shop) in the book folder.
Then st-mcp `project_add_front_cover(project, image)` adds it as the first page and runs stages 1-5 for it.
Set the page offset of the book to 0 in `st-review/page-offset.json`.

The general tools are in st-mcp: `check_pages(kind="size")`, `pages_fit_text_column`, `pages_snap_content(points=...)`,
`check_pages(kind="output")`, `page_preview(grid, roi, zones)`, `page_add_fill_zone(fit, dry_run)` and `project_add_front_cover`. The scripts here run them for all books of a series.
