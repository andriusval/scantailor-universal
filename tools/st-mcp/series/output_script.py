# Write one bash and one PowerShell script that run stages 5-6 for whole books and for listed pages.
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

BASH = r'''#!/usr/bin/env bash
# Runs stages 5-6 with the Linux CLI: all pages for {full}, listed pages for {pages}.
set -u
ROOT="{root}"
CLI="${{ST_CLI:-$HOME/andrius_ws/scantailor-universal/build/src/app_cli/scantailor-universal-cli}}"
LOG="$ROOT/st-review/{name}.log"
export OMP_NUM_THREADS=1
echo "$(date +%T) start" >> "$LOG"
while IFS= read -r b; do
  [ -n "$b" ] || continue
  d="$ROOT/$b"; p="$d/projektas.ScanTailor"
  "$CLI" --start-filter=5 --end-filter=6 --threads=auto "--output-project=$p" "$p" "$d/out" > /dev/null 2>&1
  echo "$(date +%T) full exit $? $b" >> "$LOG"
done < "$ROOT/st-review/{full}"
while IFS=$'\t' read -r b pages; do
  [ -n "$b" ] || continue
  d="$ROOT/$b"; p="$d/projektas.ScanTailor"
  "$CLI" --start-filter=5 --end-filter=6 --threads=auto "--pages=$pages" "--output-project=$p" "$p" "$d/out" > /dev/null 2>&1
  echo "$(date +%T) pages exit $? $b [$pages]" >> "$LOG"
done < "$ROOT/st-review/{pages}"
echo "$(date +%T) done" >> "$LOG"
'''

PS1 = r'''# Runs stages 5-6 with the Windows CLI: all pages for {full}, listed pages for {pages}.
$ErrorActionPreference = 'Continue'
$root = '{winroot}'
$cli = 'C:\Portable\ScanTailor-Universal\windows\scantailor-universal-cli.exe'
$env:OMP_NUM_THREADS = '1'
$log = Join-Path $root 'st-review\{name}.log'
foreach ($b in Get-Content -LiteralPath (Join-Path $root 'st-review\{full}') -Encoding UTF8) {{
    if (-not $b) {{ continue }}
    $dir = Join-Path $root $b
    $proj = Join-Path $dir 'projektas.ScanTailor'
    & $cli --start-filter=5 --end-filter=6 --threads=auto "--output-project=$proj" $proj (Join-Path $dir 'out') 2>&1 | Out-Null
    Add-Content -LiteralPath $log -Value ('{{0}} full exit {{1}} {{2}}' -f (Get-Date -Format 'HH:mm:ss'), $LASTEXITCODE, $b) -Encoding UTF8
}}
foreach ($line in Get-Content -LiteralPath (Join-Path $root 'st-review\{pages}') -Encoding UTF8) {{
    if (-not $line) {{ continue }}
    $b, $pages = $line -split "`t"
    $dir = Join-Path $root $b
    $proj = Join-Path $dir 'projektas.ScanTailor'
    & $cli --start-filter=5 --end-filter=6 --threads=auto "--pages=$pages" "--output-project=$proj" $proj (Join-Path $dir 'out') 2>&1 | Out-Null
    Add-Content -LiteralPath $log -Value ('{{0}} pages exit {{1}} {{2}} [{{3}}]' -f (Get-Date -Format 'HH:mm:ss'), $LASTEXITCODE, $b, $pages) -Encoding UTF8
}}
'''


def win_path(p: Path) -> str:
    s = str(p)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + s[6:].replace("/", "\\")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Merge book and page lists into one output run (stages 5-6).")
    ap.add_argument("series")
    ap.add_argument("name", help="base name of the scripts, lists and log in st-review")
    ap.add_argument("--full", action="append", default=[], help="file with one book per line (repeat)")
    ap.add_argument("--pages", action="append", default=[], help="file with 'book<TAB>ref;ref' lines (repeat)")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    rev = series / "st-review"
    full: list[str] = []
    for f in args.full:
        for b in Path(f).read_text(encoding="utf-8").splitlines():
            if b and b not in full:
                full.append(b)
    pages: OrderedDict[str, list[str]] = OrderedDict()
    for f in args.pages:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            b, refs = line.split("\t")
            # A book with a full run writes every page, so its page list would only repeat the work.
            if b in full:
                continue
            for r in refs.split(";"):
                if r and r not in pages.setdefault(b, []):
                    pages[b].append(r)
    fn, pn = f"{args.name}-full.txt", f"{args.name}-pages.txt"
    (rev / fn).write_text("".join(b + "\n" for b in full), encoding="utf-8")
    (rev / pn).write_text("".join(f"{b}\t{';'.join(r)}\n" for b, r in pages.items()), encoding="utf-8")
    sh = rev / f"{args.name}.sh"
    sh.write_text(BASH.format(full=fn, pages=pn, root=series, name=args.name), encoding="utf-8")
    sh.chmod(0o755)
    (rev / f"{args.name}.ps1").write_text(PS1.format(full=fn, pages=pn, winroot=win_path(series), name=args.name),
                                         encoding="utf-8-sig")
    print(f"{len(full)} full books, {len(pages)} books with pages ({sum(len(r) for r in pages.values())} pages)")
    print(f"run: {sh}  or  {rev / (args.name + '.ps1')}")
    print(f"then: python3 series/output_check.py {series} --books {rev / fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
