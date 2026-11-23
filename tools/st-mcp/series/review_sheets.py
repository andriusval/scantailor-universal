# Make one small review image for each book from the series-fix report, for a cheap visual check.
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import render_page as _render  # noqa: E402
from st_mcp.project import Project  # noqa: E402

CELL = 300


def render_page(p: Project, page_id: str, max_px: int) -> Image.Image:
    # The outline is drawn outside the box, so that it does not hide the ink at the box edge.
    img = _render(p, page_id, max_px=max_px, overlay=False)
    k = img.width / p.transform(page_id).width
    c = p.content(page_id)
    d = ImageDraw.Draw(img, "RGBA")
    r = c.get("content_rect_px")
    label = f"{p.page(page_id).label} content {c.get('mode', '-')}"
    if r and not c.get("empty"):
        x0, y0 = r["x"] * k, r["y"] * k
        x1, y1 = (r["x"] + r["width"]) * k, (r["y"] + r["height"]) * k
        d.rectangle([x0, y0, x1, y1], fill=(40, 90, 255, 35))
        d.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=(40, 90, 255, 255), width=2)
    else:
        label += " EMPTY"
    d.rectangle([0, 0, img.width, 20], fill=(0, 0, 0, 160))
    d.text((5, 4), label, fill=(255, 255, 255))
    return img
LABEL_H = 16


def bottom_crop(img: Image.Image, frac: float = 0.35) -> Image.Image:
    # The page number of page 17 is at the bottom, so the sheet shows only that part, larger.
    w, h = img.size
    return img.crop((0, int(h * (1 - frac)), w, h))


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((w, h))
    return img


def sheet_for(book: Path, r: dict, out: Path, cell: int) -> Path:
    p = Project(book / "projektas.ScanTailor")
    cells: list[tuple[str, Image.Image, int]] = []
    # A book without cover scans shows its first and last pages, so the reviewer sees the ends of the book.
    if r.get("front"):
        cells.append((f"FRONT p1 {p.page(r['front']).label}", render_page(p, r["front"], max_px=cell * 2), 1))
    else:
        cells.append((f"NO COVER first p1 {p.pages[0].label}", render_page(p, p.pages[0].id, max_px=cell * 2), 1))
    if r.get("back"):
        cells.append((f"BACK p{len(p.pages)}", render_page(p, r["back"], max_px=cell * 2), 1))
    else:
        cells.append((f"NO COVER last p{len(p.pages)} {p.pages[-1].label}",
                      render_page(p, p.pages[-1].id, max_px=cell * 2), 1))
    off = int(r.get("offset", 0))
    if len(p.pages) > 17 + off:
        pg17 = p.pages[16 + off]
        cells.append((f"P17 bottom {pg17.label}", bottom_crop(render_page(p, pg17.id, max_px=cell * 4)), 2))
    if len(p.pages) > 3 + off:
        pg3 = p.pages[2 + off]
        cells.append((f"p3 TITLE {pg3.label}", render_page(p, pg3.id, max_px=cell * 2), 1))
    for s in r["small"]:
        cells.append((f"p{s['page']} {s['label']} {s['w_mm']:.0f}x{s['h_mm']:.0f}mm",
                      render_page(p, s["id"], max_px=cell * 2), 1))
    cols = 6
    x = y = 0
    placed = []
    for label, img, span in cells:
        if x + span > cols:
            x, y = 0, y + 1
        placed.append((label, img, span, x, y))
        x += span
    rows = y + 1
    sheet = Image.new("RGB", (cols * cell, rows * (cell + LABEL_H)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for label, img, span, cx, cy in placed:
        im = fit(img, span * cell - 4, cell - 4)
        ox, oy = cx * cell, cy * (cell + LABEL_H)
        sheet.paste(im, (ox + (span * cell - im.width) // 2, oy + LABEL_H))
        d.text((ox + 3, oy + 2), label, fill=(255, 255, 0))
    path = out / (re.sub(r"[^\w.-]+", "_", book.name)[:70] + ".png")
    sheet.save(path, optimize=True)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review images for the books in st-review/series-fix.json.")
    ap.add_argument("series")
    ap.add_argument("--cell", type=int, default=CELL)
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    report = json.loads((series / "st-review" / "series-fix.json").read_text(encoding="utf-8"))
    off_path = series / "st-review" / "page-offset.json"
    offsets = json.loads(off_path.read_text(encoding="utf-8")) if off_path.exists() else {}
    for name, r in report.items():
        r["offset"] = offsets.get(name, 0)
    out = series / "st-review" / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, r in sorted(report.items()):
        if r.get("status") != "done" or (args.only and name not in args.only):
            continue
        path = sheet_for(series / name, r, out, args.cell)
        index[name] = str(path)
        print(path.name, flush=True)
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
