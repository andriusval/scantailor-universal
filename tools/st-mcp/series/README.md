# Series scripts

Scripts for one book series. They apply the rules of two projects that a person corrected by hand.
The box rules use only the book median and the ink of the page; no step uses OCR.
An agent removes the library marks: it looks at page images and writes fill zones (see `stamp-agent.md`).

Run the scripts with a `python3` that has `scipy`, from `tools/st-mcp`.
They find `scantailor-universal-cli` as st-mcp does (`ST_CLI`, the build folder or `PATH`), and they need the `zbar` library for the ISBN barcode.
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
4. `python3 series/fit_box.py <series>` finds the text pages with a box wider than the book median by more than 3 mm or taller by more than 3 % (`check_pages(kind="size")`).
   A stamp or a handwritten library mark made such a box larger.
   - The new box has the median size plus 1 mm on each cut side, at the place with the most ink (`page_set_content` `mode=median`).
   - The script writes before and after sheets to `st-review/fit-box-plan/`. Give a page that is really larger (title picture, table, contents) with `--skip "<book>|<label>"`.
   - `--apply` keeps `projektas.before-fit.ScanTailor`, writes the boxes, runs filters 4-5 and lists the changed books in `st-review/fit-books.txt`.
5. An agent removes the library marks on printed pages 3, 4 and 17 and on the back cover with fill zones: `stamp-agent.md`, `stamp_sheet.py`, `add_zones.py`.
   The zones are in original image px, so every output run applies them and the GUI shows them (Output, Fill zones).
6. Output (filters 5-6), only when the person tells you to: st-mcp `run_stages(5, 6)`, `series_fix.run_cli`, or `st-prepare-books.ps1 -Root <series> -OutputOnly`.
   - A book in `fit-books.txt` needs a full output run, because a changed box changes the page size of the whole book.
   - Other books need filter 6 only for the pages in `zones-pages.txt` (`add_zones.py --output`).
   - Clean only with fill zones in the project. Do not change the output TIFF files: a later output run writes them again and the marks come back.
7. After the output: look at the output files of the changed pages. A mark that is still there gets a new zone (step 5) and a new output run for that page.

A book without a cover scan: put a downloaded cover as `001.jpg` in the book folder.
Then `python3 series/add_front_cover.py <book> ...` adds it to the finished project and makes only `out/001.tif`.
It keeps the old project as `projektas.be-virselio.ScanTailor`. Set the page offset of the book to 0 in `st-review/page-offset.json`.
