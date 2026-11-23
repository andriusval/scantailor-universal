# st-prepare-books

Batch scripts that make a ScanTailor project for each book folder under a root folder, and later make the output.

- `st-prepare-books.ps1`: Windows PowerShell. Put it next to the `windows` folder of the portable build, because it uses `windows\scantailor-universal-cli.exe`.
- `st-prepare-books.sh`: bash 4 or later, with the same options and files. It finds the CLI as st-mcp does (`ST_CLI`, the build folder of this repository, or `PATH`). It needs `python3` with Pillow.

## Steps

1. Without options, the script makes `<book>/projektas.ScanTailor` with filters 1-5 for each book folder that has no project yet. It writes no output images.
2. Look at the projects and correct them (GUI or st-mcp).
3. With `-OutputOnly` (`--output-only`), the script runs filters 5-6 on each project.
   - The CLI writes again only the pages that changed. The project keeps values from the last output run: the output settings, the fill zones and the output file size. The CLI compares them.
   - The CLI saves these values into the project (`--output-project`), so the next run can skip a page. A project without these values gets all its pages once.
   - With `-Force` (`--force`), the script removes these values first and keeps a copy `projektas.ScanTailor.before-force-<time>`. Then the CLI writes all pages again.

Logs: `<root>/st-prepare-logs`, one log for each book and `summary.csv`. The note of each book tells how many output files were written again.
