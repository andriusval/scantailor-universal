# Contact sheets of output TIFFs, so that an agent checks the final pages and not the project view.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.project import Project  # noqa: E402

from series.stamp_sheet import DEFAULT_PAGES, offsets, targets  # noqa: E402

PROJECT = "projektas.ScanTailor"
W, H = 420, 600


def thumb(path: Path) -> Image.Image | None:
    if not path.exists():
        return None
    im = Image.open(path)
    # The draft mode reads a small version of a large TIFF, so a sheet of a whole series stays fast.
    im.draft("L", (W, H))
    im = im.convert("L")
    im.thumbnail((W, H), Image.LANCZOS)
    return im


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Contact sheets of output TIFFs for the review after output.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[], help="book folder name (repeat)")
    ap.add_argument("--pages", default=DEFAULT_PAGES, help=f"printed pages and tokens (default {DEFAULT_PAGES})")
    ap.add_argument("--zones", help="zone list JSON: draw only its pages (book, label)")
    ap.add_argument("--per-sheet", type=int, default=12)
    ap.add_argument("--out", help="output folder (default <series>/st-review/out-sheets)")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    out = Path(args.out) if args.out else series / "st-review" / "out-sheets"
    out.mkdir(parents=True, exist_ok=True)
    items = []
    if args.zones:
        for it in json.loads(Path(args.zones).read_text(encoding="utf-8")):
            items.append((it["book"], it["label"], "zone"))
    else:
        offs = offsets(series)
        books = sorted(d.name for d in series.iterdir() if (d / PROJECT).exists())
        if args.only:
            books = [b for b in books if b in args.only]
        for b in books:
            p = Project(series / b / PROJECT)
            for i, what in targets(p, args.pages, int(offs.get(b, 0)), 0):
                items.append((b, p.pages[i].label, what))
    tiles = []
    for b, label, what in items:
        t = Image.new("RGB", (W + 10, H + 30), (50, 50, 50))
        im = thumb(series / b / "out" / f"{label}.tif")
        if im is not None:
            t.paste(im.convert("RGB"), (5, 25))
        ImageDraw.Draw(t).text((4, 4), f"{b[:44]} | {label} | {what}", fill=(255, 255, 0))
        tiles.append(t)
    cols = 4
    for s in range(0, len(tiles), args.per_sheet):
        part = tiles[s:s + args.per_sheet]
        rows = (len(part) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (W + 10), rows * (H + 30)), (20, 20, 20))
        for i, t in enumerate(part):
            sheet.paste(t, ((i % cols) * (W + 10), (i // cols) * (H + 30)))
        name = out / f"out-{s // args.per_sheet + 1:03d}.png"
        sheet.save(name)
        print(name, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
