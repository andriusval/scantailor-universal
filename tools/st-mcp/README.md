# st-mcp — ScanTailor Universal for agents

MCP server + command line that let an agent (Claude Code, Claude Desktop, any MCP
client) turn a folder of book scans into clean page TIFFs with ScanTailor
Universal — following the same steps a person does in the GUI.
Workflow: [`docs/AGENTIC_WORKFLOW.en.md`](../../docs/AGENTIC_WORKFLOW.en.md)
(lietuviškai: [`docs/AGENTIC_WORKFLOW.md`](../../docs/AGENTIC_WORKFLOW.md)).

Every tool is both an MCP tool and a CLI command; both drive
`scantailor-universal-cli` and the same `.ScanTailor` project, which can be
opened in the GUI at any point.

## Setup

Needs Python ≥ 3.10 and a built `scantailor-universal-cli`.

To build the CLI on Ubuntu 24.04 (Ninja, ccache and mold are optional; they make
rebuilds and new build folders much faster):

```sh
sudo apt install -y g++ cmake pkg-config ninja-build ccache mold \
    qt6-base-dev qt6-svg-dev qt6-tools-dev libtiff-dev libjpeg-dev libpng-dev \
    libboost-all-dev libcanberra-dev libopenjp2-7-dev libexiv2-dev python3-venv
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DQtPkgList=Qt6 \
    -DCMAKE_CXX_STANDARD=17 -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold -DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold
ninja -C build scantailor-universal-cli
```

Run these commands in the repository root. Add `libmupdf-dev` for PDF input.

```sh
cd tools/st-mcp
python3 -m venv .venv            # Windows: py -m venv .venv
.venv/bin/pip install -e .       # Windows: .venv\Scripts\pip install -e .
```

The CLI binary is found in this order: `$ST_CLI`, `build/src/app_cli/…` in this
repo, `PATH`. On Windows (portable build from CI) set `ST_CLI` to
`…\scantailor-universal\scantailor-universal-cli.exe`.

## Use from Claude Code

```sh
claude mcp add scantailor -- /path/to/tools/st-mcp/.venv/bin/st-mcp serve
# Windows:
claude mcp add scantailor -e ST_CLI=C:\path\scantailor-universal-cli.exe -- C:\path\tools\st-mcp\.venv\Scripts\st-mcp.exe serve
```

Then e.g.: *"Process the scans in ~/books/onetti with ScanTailor; follow
workflow_guide."*

## Use from the shell

```sh
st-mcp list                                            # tools and parameters
st-mcp project_create images_dir=scans project=book.ScanTailor
st-mcp image_preview project=book.ScanTailor 'images=[0,1,-1]' out=orig.png
st-mcp images_set_geometry project=book.ScanTailor images=all orientation=right layout=2
st-mcp images_set_geometry project=book.ScanTailor 'images=["001","9999"]' orientation=none layout=1
st-mcp run_stages project=book.ScanTailor start=1 end=5
st-mcp pages_list project=book.ScanTailor sort_by=z_width descending=true limit=10
st-mcp check_pages project=book.ScanTailor kind=content
st-mcp pages_preview project=book.ScanTailor 'pages=["011_2R","001"]' out=check.png
st-mcp page_set_content project=book.ScanTailor page=011_2R expand_bottom=90
st-mcp page_set_content project=book.ScanTailor page=001 mode=cover
st-mcp pages_set_layout project=book.ScanTailor pages=all margin_left=10 margin_right=10 margin_top=5 margin_bottom=5 align_v=bottom align_h=center
st-mcp pages_set_layout project=book.ScanTailor 'pages=["001","9999"]' margins_mm=0 match_size=false
st-mcp pages_set_output project=book.ScanTailor 'pages=["001","9999"]' color_mode=color dpi=300
st-mcp run_stages project=book.ScanTailor start=4 end=6
st-mcp export_summary project=book.ScanTailor
st-mcp review_sheet project=book.ScanTailor                  # out/review/index.html
st-mcp corrections_record original=book/agentas.orig.ScanTailor corrected=book/agentas.ScanTailor
st-mcp corrections_stats
```

Values are JSON when they parse as JSON (`10`, `true`, `["a","b"]`), else strings.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests
```

The tests use a finished agent project as a sample (`ST_TEST_PROJECT`, default:
the Zweig book in `C:\nes\Serijos_HIGH`) and skip when it is not there.

## Corrections log

`corrections_record` appends the corrections of the person (agent project
against the corrected project) to `~/.local/share/st-mcp/corrections.jsonl`,
one JSON line per changed page parameter. `ST_CORRECTIONS_LOG` sets another
file. `corrections_stats` counts them by category and by book.

## Review sheet

Some tools make decisions: cover, content box modes, snaps, picture deskew,
page width, alignment, output mode and `check_pages` flags. They write each
decision with a confidence (0 to 1) and a reason to
`<output_dir>/review/decisions.json`.
The agent adds its own decisions with `review_note`. `review_sheet` writes
`<output_dir>/review/index.html` (one file with inline CSS, images in
`review/thumbs/` with relative paths) and `review.json`. A decision below 0.6
puts the page first, with the marker "CHECK IN GUI".

## Book queue for overnight runs

The workflow needs the judgment of an agent, so `run_queue.py` starts one
headless Claude Code session (`claude -p`) for each book in a queue file.

```sh
cat > books.txt <<'TXT'
# one book folder per line; Windows paths work too; text after | goes to the agent
C:\nes\Serijos_HIGH\(Atradimų pasaulis)\Stefan Zweig. Magelano žygis (1989)
/mnt/c/nes/Serijos_HIGH/Other book | covers are 001 and 9999
TXT
.venv/bin/python run_queue.py books.txt --dry-run     # show the commands and the prompt
.venv/bin/python run_queue.py books.txt               # run; start again to resume
```

- The prompt holds the book folder, the rules and the step list of
  `docs/AGENTIC_WORKFLOW.en.md`. It tells the agent to run to the end without
  approval and to write its report to `out-agentas/review/report.md`.
- Each session gets `--mcp-config books-mcp.json --strict-mcp-config`
  (only the `scantailor` server of this folder), `--permission-mode dontAsk`,
  `--allowedTools mcp__scantailor,Read,Write,Edit,Glob,Grep` and
  `--output-format stream-json --verbose`. Options: `--model`,
  `--max-budget-usd`, `--timeout-min` (default 240), `--max-books`,
  `--allowed-tools`, `--permission-mode`, `--mcp-config`.
- `books.state.json` keeps the status of each book (`pending`, `running`,
  `done`, `failed`), the times, the exit code and the log path. A book is
  `done` only when the session ends with exit code 0 and the report exists.
- A new start skips `done` and `failed` books and runs a book that a stopped
  runner left as `running`. `--retry-failed` runs the failed books again.
- `books-logs/` holds one log per book. `books-summary.md` has one line per
  book with links to its review sheet and log. `books.lock` stops a second
  runner on the same queue.
- `--command` replaces `claude`, so the tests use a fake agent.

## Notes and known limits (P1)

- File order is **natural** (`009 < 0100 < 9999`); ScanTailor on its own sorts
  by text, which puts `0100.jpg` before `011.jpg`.
- `project_create` writes the DPI of each image into the project. An image
  without a usable DPI (none, 72 or 96) gets the `dpi` argument (300). Older CLI
  builds give all listed files one DPI, so the tool does not rely on the CLI.
- ScanTailor runs with **one OpenMP thread** (`OMP_NUM_THREADS=1`, override with
  `ST_OMP_THREADS`). With the default (all cores) its OpenMP code is ~9× slower
  (threads mostly spin) and output pixels differ slightly from run to run.
  Whole 191-scan / 380-page book, all six stages: 54 min → 5 min 49 s, same
  parameters, deterministic output.
- Image and output paths in the project may be absolute, relative to the
  project file, or Windows paths (`C:/nes/book`, written by the Windows GUI);
  under WSL the latter are read from `/mnt/c/...`. Reading/previews/checks work
  on such projects; `run_stages` from WSL still needs paths the Linux CLI can open.
- Relative paths in a project are taken from the project file's folder by
  ScanTailor builds from this fork (GUI and CLI); older builds resolve them
  against the working directory, so `run_stages` still starts the CLI in the
  project's folder. It first checks that every
  scan can be opened: the CLI itself "processes" missing images with exit 0 and
  then saves a project pointing at the wrong folder.
- `pages_snap_content` uses the CLI option `--content-snap`, which exists only
  in CLI builds from this fork (branch `agentic/windows-build`, 2026-11-21 or
  later). It is the GUI's double-click adjustment of the content box (ported
  from ScanTailor Advanced), so agent and GUI snap edges the same way.
- `run_stages(pages=...)` processes only those pages with a CLI that has
  `--pages`; older builds process all pages. Stage 5 still uses the size of all
  pages. So after margin changes, or after a size warning, run stages 5-6 on
  all pages.
- With a CLI that has `--json-progress`, jobs follow the JSON events and report
  page errors and warnings. Older builds fall back to the `--verbose` text.
- `page_preview` draws through the CLI (`--render-preview`) when it can, so the
  agent sees what the GUI shows; `pages_preview` stays in Python for speed
  unless `engine="cli"`.
- Heuristics (`check_pages`) report evidence; the agent is expected to look at
  `pages_preview` before changing anything. Page-number position detection
  works on a subset of pages and is used as a vote.
- Previews re-implement ScanTailor's page geometry (`geometry.py`); they match
  ScanTailor's page rectangles to the pixel, but they are not ScanTailor's own
  rendering (that is P2).
