# Series scripts

Scripts for one book series. They apply the rules of two projects that a person corrected by hand.
They use no LLM. A model looks only at the small review images.

Run them with a `python3` that has `scipy`, from `tools/st-mcp`. They find `scantailor-universal-cli` as st-mcp does (`ST_CLI`, the build folder or `PATH`).
They also need `tesseract` on the `PATH` and the `zbar` library.
The ScanTailor CLI gives the same result on each system, so the steps run on each system.

## Order

1. Make `projektas.ScanTailor` in each book folder with filters 1-5: st-mcp `project_create` and `run_stages(1, 5)`, or `st-prepare-books.ps1 -Root <series>` in PowerShell.
2. `python3 series/series_fix.py <series> --skip <book> ...` changes each project:
   - It keeps the automatic project as `projektas.auto.ScanTailor`. A second run starts from that copy.
   - Covers (first and last image): rotation 0, single page without a cut, filters 1-5 again.
   - Front cover: the content box is the cover edge (found on the dark scanner lid), margins 0, no match, Color 300.
   - Back cover: the content box is the ISBN barcode. The margins keep the barcode position on the cover. Match, left and bottom, Color 300.
   - Other pages: vertical auto alignment, horizontal centre, match, B/W 600.
   - Page 17: the box ends at the bottom of the page number, because a library stamp is under it.
   - Report: `<series>/st-review/series-fix.json` (small pages, flags), log `series-fix.log`.
3. `python3 series/review_sheets.py <series>` makes one image for each book in `st-review/sheets/`.
   The image shows the covers, the bottom of page 17, and the small pages (box below 70 % of the median).
4. Review of the images; corrections with the st-mcp tools.
   - Size gate: `check_pages(kind="size")` lists the boxes wider than the book median by more than 3 mm or taller by more than 3 %.
     A stamp or a handwritten library mark made such a box larger, and printed books have no handwritten marks.
   - `python3 series/fit_box.py <series> <items.json> --sheets <dir>` shows the fix; `--apply` writes it and runs filters 4-5.
     Body pages: the box sides go to the rule under the running head; a stamp left inside, beside the page number, gets a fill zone.
     Imprint pages (page 4): the box goes from the UDK word to the ISBN and copyright lines (OCR).
5. Output (filters 5-6): st-mcp `run_stages(5, 6)`, `series_fix.run_cli`, or `st-prepare-books.ps1 -Root <series> -OutputOnly`.
6. Before the output step: `python3 series/stamp_zones.py <series> --exclude <json>` writes fill zones into the projects.
   - Page 4 and the title page: a stamp block between the text blocks, also found by OCR of the library name.
   - Page 17: only handwritten numbers in the margin. Stamps next to the page number broke text in tests, so a person does them.
   - Back cover: library stickers (decoded barcode, or barcode stripes in a corner), filled with the colour around them.
   - The zones are in original image px, so every output run applies them and the GUI shows them (Output, Fill zones).
   - Check images: `st-review/zones/zones-NN.png`. A second run does not add the same zone again.
- A book without a cover scan: put a downloaded cover as `001.jpg` in the book folder.
  Then `python3 series/add_front_cover.py <book> ...` adds it to the finished project and makes only `out/001.tif`.
  It keeps the old project as `projektas.be-virselio.ScanTailor`. Set the page offset of the book to 0.
7. The output step (see step 5).
   Clean only with fill zones in the project. Do not change the output TIFF files: a later output run writes them again and the stamps come back.
