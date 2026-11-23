# Apply the rules of one book series to the projects that filters 1-5 made.
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.ops import cli_env, find_cli  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.detect import barcode_rect_page, cover_rect_page  # noqa: E402

PROJECT = "projektas.ScanTailor"
BACKUP = "projektas.auto.ScanTailor"
MM_PER_INCH = 25.4
# The user's boxes are about 10 px inside the detected cover edge, where the scan shows a shadow.
COVER_INSET_PX = 8
SMALL_RATIO = 0.70


def run_cli(proj: Path, start: int, end: int, files: list[str], log) -> None:
    p = Project(proj)
    cmd = [find_cli(), f"--start-filter={start}", f"--end-filter={end}", "--threads=auto",
           "--pages=" + ";".join(files), f"--output-project={proj}", str(proj), str(p.output_dir)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**cli_env(), "OMP_NUM_THREADS": "1"}, cwd=str(proj.parent))
    log.write(f"$ {' '.join(cmd)}\n{r.stdout[-3000:]}{r.stderr[-3000:]}\n")
    if r.returncode != 0:
        raise RuntimeError(f"CLI filters {start}-{end} failed ({r.returncode}): {(r.stdout + r.stderr)[-500:]}")


def page_ref(pg) -> str:
    side = {"left": ":L", "right": ":R"}.get(pg.side, "")
    return pg.image.path.name + side


def px_to_mm(px: float, dpi: float) -> float:
    return px / dpi * MM_PER_INCH


def fix_book(book: Path, log) -> dict:
    proj = book / PROJECT
    backup = book / BACKUP
    # The backup holds the automatic result, so a second run starts from it again.
    if backup.exists():
        shutil.copy2(backup, proj)
    else:
        shutil.copy2(proj, backup)

    p = Project(proj)
    first, last = p.pages[0].image, p.pages[-1].image
    # Some books have no cover scans, so only the files 001 and 9999 are covers.
    has_front = first.path.stem == "001"
    has_back = last.path.stem == "9999" and last.id != first.id
    cover_imgs = [im for im, ok in ((first, has_front), (last, has_back)) if ok]
    cover_files = [im.path.name for im in cover_imgs]
    for im in cover_imgs:
        p.set_rotation(im.id, 0)
        p.set_split_layout(im.id, "single-uncut")
    for pg in p.pages:
        if pg.image.id in [im.id for im in cover_imgs]:
            p.clear_deskew(pg.id)
    p.save()
    if cover_files:
        run_cli(proj, 1, 5, cover_files, log)

    p = Project(proj)
    flags = []
    if not has_front:
        flags.append("no front cover scan (001)")
    if not has_back:
        flags.append("no back cover scan (9999)")
    # ScanTailor gives new ids when the page count changes, so the script finds the covers by image position.
    imgs = list(p.images.values())
    front = back = None
    if has_front:
        pages = [pg for pg in p.pages if pg.image.id == imgs[0].id]
        if len(pages) != 1:
            raise RuntimeError(f"the front cover did not become one page: {len(pages)}")
        front = pages[0]
    if has_back:
        pages = [pg for pg in p.pages if pg.image.id == imgs[-1].id]
        if len(pages) != 1:
            raise RuntimeError(f"the back cover did not become one page: {len(pages)}")
        back = pages[0]

    if front:
        fr = cover_rect_page(p, front.id)
        if fr:
            fr = {"x": fr["x"] + COVER_INSET_PX, "y": fr["y"] + COVER_INSET_PX,
                  "width": fr["width"] - 2 * COVER_INSET_PX, "height": fr["height"] - 2 * COVER_INSET_PX}
            p.set_content(front.id, fr)
        else:
            flags.append("front cover edge not found: content stays auto")
        p.set_layout(front.id, margins_mm={"left": 0, "top": 0, "right": 0, "bottom": 0},
                     align_v="top", align_h="hcenter", match_size=False)
        p.set_output(front.id, color_mode="color", dpi=300)

    if back:
        br = cover_rect_page(p, back.id)
        code = barcode_rect_page(p, back.id, br) if br else None
        dpi = back.image.dpi_x or 300
        if br and code:
            p.set_content(back.id, code)
            left = px_to_mm(code["x"] - br["x"], dpi)
            bottom = px_to_mm(br["y"] + br["height"] - code["y"] - code["height"], dpi)
            p.set_layout(back.id, margins_mm={"left": left, "top": 5, "right": left, "bottom": bottom},
                         align_v="bottom", align_h="left", match_size=True)
        else:
            flags.append("back cover: no ISBN barcode decoded (covered by a sticker?): content stays auto")
            p.set_layout(back.id, align_v="bottom", align_h="left", match_size=True)
        p.set_output(back.id, color_mode="color", dpi=300)

    cover_ids = {pg.id for pg in (front, back) if pg}
    for pg in p.pages:
        if pg.id in cover_ids:
            continue
        p.set_layout(pg.id, align_v="vauto", align_h="hcenter", match_size=True)
        p.set_output(pg.id, color_mode="bw", dpi=600)

    # A tiny box on a body page holds a scan shadow or a library sticker, never printed text.
    emptied = []
    for pg in p.pages:
        if pg.id in cover_ids:
            continue
        c = p.content(pg.id)
        mm = c.get("content_mm")
        if c.get("empty") or not mm:
            continue
        w, h = mm["width"], mm["height"]
        if w < 8 or h < 8 or (w < 50 and h < 22):
            p.set_content(pg.id, {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0})
            emptied.append(pg.label)
    refs = list(cover_files) + [page_ref(pg) for pg in p.pages if pg.label in emptied]
    if front is None:
        flags.append("no front cover scan: the printed page numbers are one less than the project numbers")
    p.save()
    if refs:
        run_cli(proj, 4, 5, refs, log)

    p = Project(proj)
    imgs = list(p.images.values())
    front = next((pg for pg in p.pages if has_front and pg.image.id == imgs[0].id), None)
    back = next((pg for pg in p.pages if has_back and pg.image.id == imgs[-1].id), None)
    cover_ids = {pg.id for pg in (front, back) if pg}
    body = [pg for pg in p.pages if pg.id not in cover_ids]
    sizes = {}
    for pg in body:
        c = p.content(pg.id)
        mm = c.get("content_mm")
        sizes[pg.id] = (mm["width"], mm["height"]) if mm and not c.get("empty") else (0.0, 0.0)
    filled = [s for s in sizes.values() if s[0] > 0]
    med_w = statistics.median(s[0] for s in filled) if filled else 0
    med_h = statistics.median(s[1] for s in filled) if filled else 0
    small = []
    for pg in body:
        w, h = sizes[pg.id]
        if w < SMALL_RATIO * med_w or h < SMALL_RATIO * med_h:
            small.append({"page": pg.index + 1, "id": pg.id, "label": pg.label, "w_mm": w, "h_mm": h})
    return {"book": book.name, "pages": len(p.pages), "front": front.id if front else None,
            "back": back.id if back else None, "median_mm": [med_w, med_h], "small": small,
            "flags": flags, "emptied": emptied,
            "back_margins_mm": p.layout(back.id)["margins_mm"] if back else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fix the covers, alignment and output modes of a series of books.")
    ap.add_argument("series", help="folder with one subfolder for each book")
    ap.add_argument("--skip", action="append", default=[], help="book folder name to leave alone (repeat)")
    ap.add_argument("--only", action="append", default=[], help="process only this book folder name (repeat)")
    ap.add_argument("--reset", action="store_true",
                    help="start again from projektas.auto.ScanTailor also for books in the report (drops later fixes)")
    args = ap.parse_args(argv)

    series = Path(args.series).resolve()
    out = series / "st-review"
    out.mkdir(exist_ok=True)
    report_path = out / "series-fix.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    books = sorted(d for d in series.iterdir() if (d / PROJECT).exists())
    if args.only:
        books = [b for b in books if b.name in args.only]
    books = [b for b in books if b.name not in args.skip]
    with open(out / "series-fix.log", "a", encoding="utf-8") as log:
        for n, book in enumerate(books, 1):
            # A book in the report can have later fixes in its project, so only --reset starts it again.
            if not args.reset and report.get(book.name, {}).get("status") == "done":
                print(f"{n}/{len(books)} skipped (in the report; use --reset): {book.name[:50]}", flush=True)
                continue
            t0 = time.time()
            log.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} {book.name}\n")
            try:
                r = fix_book(book, log)
                r["status"] = "done"
            except Exception as e:  # noqa: BLE001
                r = {"book": book.name, "status": "failed", "error": str(e)}
            r["seconds"] = round(time.time() - t0, 1)
            report[book.name] = r
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            extra = r.get("error") or f"{len(r['small'])} small, flags {r['flags']}"
            print(f"{n}/{len(books)} {r['status']} {r['seconds']}s {book.name[:50]}: {extra}", flush=True)
    failed = [k for k, v in report.items() if v.get("status") == "failed"]
    print(f"report: {report_path}; failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
