# Draw numbered grids of single pages from many books, so that one review image checks many changes.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.project import Project  # noqa: E402

from series.review_sheets import bottom_crop, render_page  # noqa: E402

CELL = 260
LABEL_H = 15
COLS = 6


def cell_image(p: Project, item: dict, cell: int) -> tuple[Image.Image, int]:
    label = item["label"]
    pg = p.pages[0] if label == "FRONT" else p.pages[-1] if label == "BACK" else p.find_page(label)
    if item.get("kind") == "p17bottom":
        return bottom_crop(render_page(p, pg.id, max_px=cell * 4)), 2
    return render_page(p, pg.id, max_px=cell * 2), 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Numbered review grids for a list of pages.")
    ap.add_argument("series")
    ap.add_argument("items", help="JSON list of {book, label (page label, FRONT or BACK), kind}")
    ap.add_argument("out", help="output folder for grid-NN.png and cells.json")
    ap.add_argument("--per-sheet", type=int, default=24)
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    items = json.loads(Path(args.items).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index, projects = {}, {}
    for s in range(0, len(items), args.per_sheet):
        chunk = items[s:s + args.per_sheet]
        placed, x, y = [], 0, 0
        for k, it in enumerate(chunk, s + 1):
            p = projects.get(it["book"]) or projects.setdefault(it["book"], Project(series / it["book"] / "projektas.ScanTailor"))
            img, span = cell_image(p, it, CELL)
            if x + span > COLS:
                x, y = 0, y + 1
            placed.append((k, it, img, span, x, y))
            x += span
            index[str(k)] = it
        sheet = Image.new("RGB", (COLS * CELL, (y + 1) * (CELL + LABEL_H)), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        for k, it, img, span, cx, cy in placed:
            img = img.copy()
            img.thumbnail((span * CELL - 4, CELL - 4))
            ox, oy = cx * CELL, cy * (CELL + LABEL_H)
            sheet.paste(img, (ox + (span * CELL - img.width) // 2, oy + LABEL_H))
            d.text((ox + 3, oy + 2), f"#{k} {it.get('kind', '')} {it['label']}", fill=(255, 255, 0))
        path = out / f"grid-{s // args.per_sheet + 1:02d}.png"
        sheet.save(path, optimize=True)
        print(path, flush=True)
    (out / "cells.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
