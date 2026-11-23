# Draw pages with a page px grid, so that an agent can find library marks and read the fill zone coordinates.
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import render_page  # noqa: E402
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


def grid_view(p: Project, pid: str, roi: tuple | None, max_w: int, max_h: int) -> Image.Image:
    img = render_page(p, pid, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    fwd = p.transform(pid).xform
    for z in p.fill_zones(pid):
        od.polygon([fwd.map(tuple(c)) for c in z["points"]], fill=(0, 0, 255, 60))
    img = Image.alpha_composite(img, over).convert("RGB")
    d = ImageDraw.Draw(img)
    c = p.content(pid).get("content_rect_px")
    if c and c["width"] > 0:
        d.rectangle([c["x"], c["y"], c["x"] + c["width"], c["y"] + c["height"]], outline=(0, 170, 0), width=3)
    x0, y0, x1, y1 = roi or (0, 0, img.width, img.height)
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(img.width, int(x1)), min(img.height, int(y1))
    crop = img.crop((x0, y0, x1, y1))
    k = min(1.0, max_w / crop.width, max_h / crop.height)
    crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))))
    d = ImageDraw.Draw(crop)
    # A fine grid on a close view and a coarse grid on a whole page keep the labels readable.
    step = 25 if k >= 0.6 else 50 if k >= 0.3 else 100
    for g in range((x0 // step + 1) * step, x1, step):
        x = (g - x0) * k
        d.line([(x, 0), (x, crop.height)], fill=(255, 0, 0) if g % 100 == 0 else (255, 205, 205))
        if g % 100 == 0:
            d.text((x + 2, 2), str(g), fill=(200, 0, 0))
    for g in range((y0 // step + 1) * step, y1, step):
        y = (g - y0) * k
        d.line([(0, y), (crop.width, y)], fill=(255, 0, 0) if g % 100 == 0 else (255, 205, 205))
        if g % 100 == 0:
            d.text((2, y + 2), str(g), fill=(200, 0, 0))
    return crop


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
