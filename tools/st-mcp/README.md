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
```

Values are JSON when they parse as JSON (`10`, `true`, `["a","b"]`), else strings.

## Notes and known limits (P1)

- File order is **natural** (`009 < 0100 < 9999`); ScanTailor on its own sorts
  by text, which puts `0100.jpg` before `011.jpg`.
- DPI is always passed explicitly: the CLI assumes 600 DPI for files given as a
  list, which would make millimetre margins wrong.
- ScanTailor runs with **one OpenMP thread** (`OMP_NUM_THREADS=1`, override with
  `ST_OMP_THREADS`). With the default (all cores) its OpenMP code is ~9× slower
  (threads mostly spin) and output pixels differ slightly from run to run.
  Whole 191-scan / 380-page book, all six stages: 54 min → 5 min 49 s, same
  parameters, deterministic output.
- `run_stages` always processes all pages (a CLI limitation); re-runs after
  content/layout changes are faster because ScanTailor caches.
- Heuristics (`check_pages`) report evidence; the agent is expected to look at
  `pages_preview` before changing anything. Page-number position detection
  works on a subset of pages and is used as a vote.
- Previews re-implement ScanTailor's page geometry (`geometry.py`); they match
  ScanTailor's page rectangles to the pixel, but they are not ScanTailor's own
  rendering (that is P2).
