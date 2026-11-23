# Check and write the fill zones of an agent (page px), and make the output of these pages only.
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import grid_view  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.series_fix import page_ref, run_cli  # noqa: E402

PROJECT = "projektas.ScanTailor"
BACKUP = "projektas.before-zones.ScanTailor"


def check_tile(p: Project, pid: str, zones: list, title: str) -> Image.Image:
    # The view shows the zones with a ring of the page around them, because the risk is printed text next to a zone.
    roi = (min(z[0] for z in zones) - 200, min(z[1] for z in zones) - 200,
           max(z[2] for z in zones) + 200, max(z[3] for z in zones) + 200)
    crop = grid_view(p, pid, roi, 620, 440, zones)
    tile = Image.new("RGB", (630, 465), (40, 40, 40))
    tile.paste(crop, (5, 20))
    ImageDraw.Draw(tile).text((5, 4), title, fill=(255, 255, 0))
    return tile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check, write and output the fill zones of a zone list.")
    ap.add_argument("series")
    ap.add_argument("zones", help="JSON list of {book, label, zones: [[x0, y0, x1, y1], ...], color?} in page px")
    ap.add_argument("--apply", action="store_true", help="write the zones into the projects")
    ap.add_argument("--output", action="store_true", help="after --apply, run filter 6 for these pages only")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    rev = series / "st-review"
    items = json.loads(Path(args.zones).read_text(encoding="utf-8"))
    tiles, pages = [], OrderedDict()
    for it in items:
        book = series / it["book"]
        proj = book / PROJECT
        p = Project(proj)
        pg = p.find_page(it["label"])
        if not it.get("zones"):
            raise ValueError(f"{it['book']} {it['label']}: the zone list is empty")
        tr = p.transform(pg.id)
        zones = [(max(0, a), max(0, b), min(tr.width, c), min(tr.height, d)) for a, b, c, d in it["zones"]]
        if args.apply:
            if not (book / BACKUP).exists():
                (book / BACKUP).write_bytes(proj.read_bytes())
            inv = tr.xform.inverted()
            for x0, y0, x1, y1 in zones:
                p.add_fill_zone(pg.id, [inv.map(c) for c in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))],
                                it.get("color", "#ffffff"))
            p.save()
            p = Project(proj)
            pg = p.find_page(it["label"])
            pages.setdefault(it["book"], []).append(pg)
        tiles.append(check_tile(p, pg.id, zones, f"{len(tiles) + 1} {it['book'][:45]} {it['label']}"))
    sheets = rev / ("zones-applied" if args.apply else "zones-check")
    sheets.mkdir(parents=True, exist_ok=True)
    # Red is a zone and green the content box; only the part of a zone in the box changes the output.
    for s in range(0, len(tiles), 9):
        part = tiles[s:s + 9]
        sheet = Image.new("RGB", (3 * 630, ((len(part) + 2) // 3) * 465), (20, 20, 20))
        for i, t in enumerate(part):
            sheet.paste(t, ((i % 3) * 630, (i // 3) * 465))
        sheet.save(sheets / f"sheet-{s // 9 + 1:02d}.png")
    if args.apply:
        lines = [f"{b}\t{';'.join(page_ref(pg) for pg in pgs)}\n" for b, pgs in pages.items()]
        with open(rev / "zones-pages.txt", "a", encoding="utf-8") as f:
            f.writelines(lines)
        if args.output:
            with open(rev / "zones-output.log", "a", encoding="utf-8") as log:
                for b, pgs in pages.items():
                    run_cli(series / b / PROJECT, 6, 6, [page_ref(pg) for pg in pgs], log)
                    print("output", b, [pg.label for pg in pgs], flush=True)
    print(f"{len(tiles)} pages; check sheets in {sheets}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
