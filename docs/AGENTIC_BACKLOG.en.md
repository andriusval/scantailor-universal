# Agent workflow: C++ backlog

This list gives the ScanTailor C++ changes that would make the agent workflow (`tools/st-mcp`) faster or better.
It comes from the tests with the Jakštas, Zweig and Peary books on 2026-11-21. Lithuanian: [AGENTIC_BACKLOG.md](AGENTIC_BACKLOG.md).

Suggested order: 1 and 4, then 2, then 5 and 6, and 7 to 9 later.

## Speed

1. **A page filter** (`--pages=<id,...>` or `--images=<file,...>`).
   The function `run_stages` always processes all pages.
   For 6 changed pages of the Jakštas book, stages 4 and 5 processed all 278 pages in about 50 s.
   With a filter, the same run takes a few seconds.
2. **OpenMP threads that spin.**
   The tools set `OMP_NUM_THREADS=1`, because the CLI is about 9 times slower with all threads.
   First, try `OMP_WAIT_POLICY=PASSIVE`, which needs no C++ change.
   If that does not help, fix the `#pragma omp` loops that are too small, so that the output stage uses all cores.
3. **No new run for current output.**
   Find out if the CLI skips pages with no parameter change.
   The option `--disable-check-output` exists, but its effect in the CLI is not known.
   If the CLI does not skip such pages, add that check.

## Reliability

4. **An exit code for missing scans.**
   The CLI ends with code 0 and saves a broken project.
   At this time, `_check_inputs_reachable` in `st-mcp` finds the problem.
5. **Progress as JSON** (`--json-progress`).
   One JSON line for each page gives the stage, the content box, the page box, the deskew angle and the errors.
   This replaces the parse of the `--verbose` text and a new read of the XML after each run.
6. **Previews from ScanTailor** (`--render-preview=<page>:<stage>:<file.png>`).
   At this time, `st-mcp` copies the ScanTailor geometry in Python (`geometry.py`).
   A difference means that the agent does not see what the GUI shows.
7. **An export of the ScanTailor analysis** (`--dump-masks=<dir>`).
   Export the `ContentMask` and the text mask, so that the search for junk and page numbers uses the ScanTailor mask.
   The option `--content-snap` already uses that mask.

## Quality (also better for the GUI)

8. **Deskew for photos.**
   ScanTailor finds the angle from the text lines, so it can turn a photo by up to 7.5 degrees (Jakštas `098_2R`, Peary).
   The agent corrects this with `picture_skew` in Python.
   The C++ code can measure the angle from the edges of the photo.
9. **Page numbers and covers.**
   `ContentBoxFinder` often does not include a page number that stands apart from the text.
   It also does not find a cover on a dark background.
   The agent corrects this with the modes `include_numbers` and `cover`.

## Done (2026-11-21, branch `agentic/windows-build`)

- A double-click adjusts the content box (from ScanTailor Advanced), and the CLI has `--content-snap`.
- Relative paths start at the project file, when the project is read and when it is saved.
- The CLI makes the output folder.
- The CLI reads the DPI of each scan from the file.
- The Relinking dialog has "Find in folder...", a summary and a filter.
- The Windows build uses Qt 6.12.1 LTS, and the minimum Qt is 6.4 or 5.15.
- Item 7: the CLI option `--dump-masks` writes the content, text and garbage masks with their transform; `check_pages` uses them.
