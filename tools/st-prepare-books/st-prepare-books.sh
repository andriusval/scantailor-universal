#!/usr/bin/env bash
# Bash version of st-prepare-books.ps1 with the same options and files; it needs bash 4 or later.
#
# Usage:
#   st-prepare-books.sh --root <folder> [options]
#
# For each book folder under the root (a folder with jpg, jpeg, tif, tiff or png files, at any depth)
# the script makes <book>/projektas.ScanTailor with automatic settings and filters 1-5.
# It writes no output images. A book folder that already has the project stays as it is.
#
# --output-only runs filters 5-6 on each book folder that has the project.
# The CLI writes again only the pages that changed: it compares the output settings, the fill zones and
# the size of the output file with the values that the project keeps from the last output run.
# The CLI saves these values into the project (--output-project), so the next run can skip a page.
# --output-only --force removes these values first (with a copy of the project), so all pages are written.
#
# Options:
#   --root DIR            the folder with the book folders (required)
#   --cli PATH            scantailor-universal-cli (default: $ST_CLI, the build folder of this repository, or PATH)
#   --project-name NAME   default projektas.ScanTailor
#   --out-name NAME       default out
#   --layout 0|1|1.5|2    0 auto, 1 one page, 1.5 one page with a cut, 2 two pages (default 0)
#   --orientation auto|none|left|right|upsidedown   (default auto)
#   --fallback-dpi N      the DPI for images without a usable DPI (none, 72 or 96); default 300
#   --threads N|auto      pages of one filter at the same time (default auto)
#   --extra ARG           another CLI option, for example --extra=--content-detection=cautious (repeat)
#   --output-only         filters 5-6 on the existing projects
#   --force               with --output-only: write all output images again
#   --dry-run             show the book folders and the commands, and change nothing
#
# Logs go to <root>/st-prepare-logs: one log for each book, run-<time>.log and summary.csv.
set -u -o pipefail

root="" cli="" project_name="projektas.ScanTailor" out_name="out" layout="0" orientation="auto"
fallback_dpi=300 threads="auto" output_only=0 force=0 dry_run=0
extra=()

die() { echo "error: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --root) root="$2"; shift 2 ;; --root=*) root="${1#*=}"; shift ;;
        --cli) cli="$2"; shift 2 ;; --cli=*) cli="${1#*=}"; shift ;;
        --project-name) project_name="$2"; shift 2 ;; --project-name=*) project_name="${1#*=}"; shift ;;
        --out-name) out_name="$2"; shift 2 ;; --out-name=*) out_name="${1#*=}"; shift ;;
        --layout) layout="$2"; shift 2 ;; --layout=*) layout="${1#*=}"; shift ;;
        --orientation) orientation="$2"; shift 2 ;; --orientation=*) orientation="${1#*=}"; shift ;;
        --fallback-dpi) fallback_dpi="$2"; shift 2 ;; --fallback-dpi=*) fallback_dpi="${1#*=}"; shift ;;
        --threads) threads="$2"; shift 2 ;; --threads=*) threads="${1#*=}"; shift ;;
        --extra) extra+=("$2"); shift 2 ;; --extra=*) extra+=("${1#*=}"); shift ;;
        --output-only) output_only=1; shift ;;
        --force) force=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

[ -n "$root" ] || die "--root is required"
[ -d "$root" ] || die "no folder: $root"
case "$layout" in 0|1|1.5|2) ;; *) die "--layout must be 0, 1, 1.5 or 2" ;; esac
case "$orientation" in auto|none|left|right|upsidedown) ;; *) die "--orientation must be auto, none, left, right or upsidedown" ;; esac
[ "$force" -eq 0 ] || [ "$output_only" -eq 1 ] || die "--force works only together with --output-only"
command -v python3 >/dev/null || die "python3 is required (it reads the image DPI and the project XML)"

root="$(cd "$root" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$cli" ]; then
    # The same order as st-mcp: the ST_CLI variable, the build folder of this repository, then PATH.
    if [ -n "${ST_CLI:-}" ] && [ -x "${ST_CLI}" ]; then cli="$ST_CLI"
    elif [ -x "$here/../../build/src/app_cli/scantailor-universal-cli" ]; then cli="$here/../../build/src/app_cli/scantailor-universal-cli"
    else cli="$(command -v scantailor-universal-cli || true)"
    fi
fi
[ -n "$cli" ] && [ -x "$cli" ] || die "CLI not found (use --cli <path> or set ST_CLI)"
cli="$(cd "$(dirname "$cli")" && pwd)/$(basename "$cli")"

log_dir="$root/st-prepare-logs"
stamp="$(date +%Y%m%d-%H%M%S)"
main_log="$log_dir/run-$stamp.log"
summary_csv="$log_dir/summary.csv"
[ "$dry_run" -eq 1 ] || mkdir -p "$log_dir"

# One OpenMP thread for each page is faster and gives the same result in each run.
export OMP_NUM_THREADS=1

log() {
    local line
    line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$line"
    [ "$dry_run" -eq 1 ] || echo "$line" >> "$main_log"
}

skip_dir() {
    # The review and log folders of the agent tools hold images too, but they are not books.
    case "$1" in cache|out|out-*|st-review|st-prepare-logs|"$out_name") return 0 ;; esac
    return 1
}

is_image() {
    case "${1,,}" in *.jpg|*.jpeg|*.tif|*.tiff|*.png) return 0 ;; esac
    return 1
}

find_books() {
    local dir="$1" f d
    for f in "$dir"/*; do
        if [ -f "$f" ] && is_image "$f"; then echo "$dir"; break; fi
    done
    while IFS= read -r d; do
        skip_dir "$(basename "$d")" || find_books "$d"
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
}

csv() { local s="${1//\"/\"\"}"; printf '"%s"' "$s"; }

mapfile -t books < <(find_books "$root")
log "${#books[@]} book folder(s) under $root; CLI: $cli"
[ "$dry_run" -eq 1 ] || echo '"Book","Status","Images","Pages","Dpi","Minutes","Log","Note"' > "$summary_csv"

failed=0
n=0
for dir in "${books[@]}"; do
    n=$((n + 1))
    proj="$dir/$project_name"
    out="$dir/$out_name"
    status="" pages="" dpi="" minutes="" note=""
    safe="$(basename "$dir" | sed 's/[^[:alnum:]._-]\{1,\}/_/g' | cut -c1-60)"
    book_log="$log_dir/$(printf '%03d' "$n")-$safe.log"
    mapfile -t images < <(cd "$dir" && for f in *; do [ -f "$f" ] && is_image "$f" && echo "$f"; done | sort -V)

    if [ "$output_only" -eq 1 ]; then
        if [ ! -f "$proj" ]; then
            log "$n/${#books[@]} SKIP (no project): $dir"
            [ "$dry_run" -eq 1 ] || echo "$(csv "$dir"),\"skipped\",0,\"\",\"\",\"\",\"\",$(csv "no $project_name")" >> "$summary_csv"
            continue
        fi
        if [ -d "$dir/out-orig-antspaudai" ]; then
            # Old cleanups changed only the output files, so a page that the CLI writes again gets its marks back.
            note="some pages were cleaned only in the output files: check them, or add fill zones"
        fi
        # One value on each line, because a folder name can have spaces.
        mapfile -t info < <(python3 - "$proj" "$dir" <<'EOF'
import os, sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
out = root.get("outputDirectory", "out")
print(out if os.path.isabs(out) else os.path.join(sys.argv[2], out))
print(len(root.findall("./filters/output/page/output-params")))
EOF
        )
        proj_out="${info[0]}" kept="${info[1]}"
        if [ "$force" -eq 1 ] && [ "$dry_run" -eq 0 ] && [ "$kept" -gt 0 ]; then
            # Without the values of the last output run the CLI cannot skip a page, so it writes all pages.
            cp -p "$proj" "$proj.before-force-$stamp"
            python3 - "$proj" <<'EOF'
import sys, xml.etree.ElementTree as ET
tree = ET.parse(sys.argv[1])
for page in tree.getroot().findall("./filters/output/page"):
    for node in page.findall("output-params"):
        page.remove(node)
tree.write(sys.argv[1], encoding="utf-8", xml_declaration=True)
EOF
        fi
        note="${note:+$note; }pages with values of the last output run: $kept"
        # The CLI saves the values of this run into the project, so the next run writes only the pages that changed.
        cmd=("$cli" --verbose --start-filter=5 --end-filter=6 "--threads=$threads" "${extra[@]}"
             "--output-project=$proj" "$proj" "$proj_out")
    else
        if [ -f "$proj" ]; then
            log "$n/${#books[@]} SKIP (project exists): $dir"
            [ "$dry_run" -eq 1 ] || echo "$(csv "$dir"),\"skipped\",${#images[@]},\"\",\"\",\"\",\"\",$(csv "$project_name exists")" >> "$summary_csv"
            continue
        fi
        # The CLI can force only one DPI on all images, so a book with any image without DPI gets the fallback.
        dpi="$(cd "$dir" && python3 - "${images[@]}" <<'EOF'
import sys
from collections import Counter
from PIL import Image
seen = []
for name in sys.argv[1:]:
    try:
        with Image.open(name) as im:
            d = im.info.get("dpi", (0, 0))[0]
    except Exception:
        d = 0
    seen.append(round(float(d)))
bad = [d for d in seen if d <= 0 or d in (72, 96)]
print(f"BAD {len(bad)}" if bad else ", ".join(f"{d} x{c}" for d, c in Counter(seen).most_common()))
EOF
        )"
        dpi_args=()
        if [ "${dpi%% *}" = "BAD" ]; then
            dpi_args=("--dpi=$fallback_dpi")
            dpi="forced $fallback_dpi (${dpi#BAD } image(s) without DPI)"
        fi
        cmd=("$cli" --verbose --start-filter=1 --end-filter=5 "--orientation=$orientation" "--layout=$layout"
             "--threads=$threads" "${dpi_args[@]}" "${extra[@]}" "--output-project=$proj" "${images[@]}" "$out")
    fi

    if [ "$dry_run" -eq 1 ]; then
        if [ "$output_only" -eq 1 ]; then shown=("${cmd[@]}")
        else shown=("${cmd[@]:0:$((${#cmd[@]} - ${#images[@]} - 1))}" "${images[0]:-}" "... (${#images[@]} files) ..." "$out")
        fi
        printf '%s/%s %s\n    cd "%s"\n   ' "$n" "${#books[@]}" "$dir" "$dir"
        printf ' "%s"' "${shown[@]}"
        printf '\n    DPI: %s %s\n' "$dpi" "$note"
        continue
    fi

    log "$n/${#books[@]} START $dir (${#images[@]} images${dpi:+, DPI $dpi})"
    t0=$(date +%s)
    marker="$(mktemp)"
    (cd "$dir" && "${cmd[@]}") > "$book_log" 2>&1
    code=$?
    minutes=$(awk -v s="$(( $(date +%s) - t0 ))" 'BEGIN { printf "%.1f", s / 60 }')

    if [ "$code" -ne 0 ] || [ ! -f "$proj" ]; then
        status="failed"
        note="exit code $code; see the log"
        log "$n/${#books[@]} FAILED (exit code $code): $dir"
    else
        pages="$(python3 -c 'import sys, xml.etree.ElementTree as ET; print(len(ET.parse(sys.argv[1]).getroot().findall("./pages/page")))' "$proj")"
        status="done"
        if [ "$output_only" -eq 1 ]; then
            all=$(find "$proj_out" -maxdepth 1 -name '*.tif' -type f 2>/dev/null | wc -l)
            new=$(find "$proj_out" -maxdepth 1 -name '*.tif' -type f -newer "$marker" 2>/dev/null | wc -l)
            note="${note:+$note; }written again: $new of $pages"
            if [ "$all" -lt "$pages" ]; then status="failed"; note="only $all output files for $pages pages"; fi
        fi
        log "$n/${#books[@]} ${status^^} in $minutes min: $pages pages $note"
    fi
    rm -f "$marker"
    [ "$status" = "failed" ] && failed=$((failed + 1))
    echo "$(csv "$dir"),$(csv "$status"),${#images[@]},$(csv "$pages"),$(csv "$dpi"),$(csv "$minutes"),$(csv "$book_log"),$(csv "$note")" >> "$summary_csv"
done

[ "$dry_run" -eq 1 ] || log "summary: $summary_csv"
[ "$failed" -eq 0 ] || exit 1
exit 0
