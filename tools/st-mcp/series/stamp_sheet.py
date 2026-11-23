# Draw pages with a page px grid, so that an agent can find library marks and read the fill zone coordinates.
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import grid_view  # noqa: E402
from st_mcp.project import Project  # noqa: E402

PROJECT = "projektas.ScanTailor"
DEFAULT_PAGES = "3,4,17,back"


def offsets(series: Path) -> dict:
    path = series / "st-review" / "page-offset.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def targets(p: Project, pages: str, offset: int, around: int) -> list[tuple[int, str]]:
    # Printed page N is project page N + offset, because a missing or extra scan moves the numbers.
    out = []
    for tok in pages.split(","):
        tok = tok.strip()
        if tok == "front":
            base, what = 0, "front cover"
        elif tok == "back":
            base, what = len(p.pages) - 1, "back cover"
        else:
            base, what = int(tok) - 1 + offset, f"printed page {tok}?"
        for i in range(base - around, base + around + 1):
            if 0 <= i < len(p.pages):
                out.append((i, what if i == base else f"near {what}"))
    seen, uniq = set(), []
    for i, what in out:
        if i not in seen:
            seen.add(i)
            uniq.append((i, what))
    return uniq


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Page images with a page px grid for the stamp review.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[], help="book folder name (repeat)")
    ap.add_argument("--pages", default=DEFAULT_PAGES, help=f"printed page numbers, front, back (default {DEFAULT_PAGES})")
    ap.add_argument("--around", type=int, default=0, help="also draw this number of pages before and after")
    ap.add_argument("--label", action="append", default=[], help="page label instead of --pages (repeat)")
    ap.add_argument("--roi", help="x0,y0,x1,y1 in page px: a close view of one --label")
    ap.add_argument("--out", help="output folder (default <series>/st-review/stamp-sheets)")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    out = Path(args.out) if args.out else series / "st-review" / "stamp-sheets"
    offs = offsets(series)
    books = sorted(d for d in series.iterdir() if (d / PROJECT).exists())
    if args.only:
        books = [b for b in books if b.name in args.only]
    roi = tuple(float(v) for v in args.roi.split(",")) if args.roi else None
    for book in books:
        p = Project(book / PROJECT)
        if args.label:
            pages = [(p.pages.index(p.find_page(lab)), "page") for lab in args.label]
        else:
            pages = targets(p, args.pages, int(offs.get(book.name, 0)), args.around)
        folder = out / re.sub(r"[^\w.-]+", "_", book.name)[:70]
        folder.mkdir(parents=True, exist_ok=True)
        for i, what in pages:
            pg = p.pages[i]
            view = grid_view(p, pg.id, roi, 1400 if roi else 1000, 900 if roi else 1400)
            sheet = Image.new("RGB", (view.width, view.height + 20), (40, 40, 40))
            sheet.paste(view, (0, 20))
            ImageDraw.Draw(sheet).text((4, 4), f"{book.name[:60]} | {pg.label} | project page {i + 1} | {what}",
                                       fill=(255, 255, 0))
            name = f"{i + 1:03d}_{pg.label}" + ("_roi" if roi else "") + ".png"
            sheet.save(folder / name)
            print(folder / name, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
