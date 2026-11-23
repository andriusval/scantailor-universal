# Write library stamp removal into the ScanTailor projects as fill zones, so that every output run applies it.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import render_page  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.stamp_find import (PAD_MM, find_back_sticker, find_page4_stamp,  # noqa: E402
                               find_title_stamp, margin_handwriting, mm_px)

PROJECT = "projektas.ScanTailor"
# The output turns grey stamp ink black, so a light limit keeps faint stamp parts in the search image.
INK_LIMIT = 175
def find_p17_margin_notes(im: Image.Image) -> list:
    # Stamps next to the page number broke text in tests, so page 17 gets only the handwritten margin numbers.
    return margin_handwriting(np.asarray(im.convert("L")) < 128, mm_px(im))


FINDERS = {"p3": find_title_stamp, "p4": find_page4_stamp, "p17": find_p17_margin_notes, "back": find_back_sticker}


def ring_color(img: Image.Image, box: tuple[int, int, int, int], r: int) -> str:
    a = np.asarray(img.convert("RGB"))
    x0, y0, x1, y1 = box
    ring = np.concatenate([a[max(0, y0 - r):y0, x0:x1].reshape(-1, 3), a[y1:y1 + r, x0:x1].reshape(-1, 3),
                           a[y0:y1, max(0, x0 - r):x0].reshape(-1, 3), a[y0:y1, x1:x1 + r].reshape(-1, 3)])
    c = np.median(ring, axis=0) if len(ring) else (255, 255, 255)
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


def page_boxes(p: Project, pg, tag: str) -> tuple[list[tuple[int, int, int, int]], Image.Image, float, str]:
    pt = p.transform(pg.id)
    img = render_page(p, pg.id, max_px=100000, overlay=False, pad=(255, 255, 255))
    k = img.width / pt.width
    dpi = (pg.image.dpi_x or 300) * k
    mm = dpi / 25.4
    if tag == "back":
        search, ox, oy = img.copy(), 0, 0
    else:
        # Body pages go to the output only inside the content box, so the search sees the same part.
        c = p.content(pg.id).get("content_rect_px")
        if not c or p.content(pg.id).get("empty"):
            return [], img, k, "#ffffff"
        ox, oy = max(0, int(c["x"] * k - mm)), max(0, int(c["y"] * k - mm))
        x1, y1 = int((c["x"] + c["width"]) * k + mm), int((c["y"] + c["height"]) * k + mm)
        search = img.crop((ox, oy, x1, y1)).convert("L").point(lambda v: 255 if v > INK_LIMIT else 0).convert("1")
    search.info["dpi"] = (dpi, dpi)
    found = FINDERS[tag](search)
    if not found:
        return [], img, k, "#ffffff"
    found = found if isinstance(found, list) else [found]
    boxes = []
    for b in found:
        pad = int((b[4] if len(b) > 4 else PAD_MM) * mm)
        boxes.append((int(b[0]) + ox - pad, int(b[1]) + oy - pad, int(b[2]) + ox + pad, int(b[3]) + oy + pad))
    color = "#ffffff"
    if tag == "back":
        u = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        color = ring_color(img, u, int(3 * mm))
    return boxes, img, k, color


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add fill zones over library stamps to the projects of a series.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--tags", default="p3,p4,p17,back")
    ap.add_argument("--exclude", help="JSON {book: [tags]} of pages where the detectors are known to be wrong")
    ap.add_argument("--dry-run", action="store_true", help="make the check sheets; change no project")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    # A dry run writes to its own folder, so it keeps the report and the sheets of the real run.
    rev = series / "st-review" / ("zones-dry" if args.dry_run else "zones")
    rev.mkdir(parents=True, exist_ok=True)
    off_path = series / "st-review" / "page-offset.json"
    offsets = json.loads(off_path.read_text(encoding="utf-8")) if off_path.exists() else {}
    exclude = json.loads(Path(args.exclude).read_text(encoding="utf-8")) if args.exclude else {}
    books = sorted(d for d in series.iterdir() if (d / PROJECT).exists())
    if args.only:
        books = [b for b in books if b.name in args.only]
    books = [b for b in books if b.name not in args.skip]
    report, pairs = {}, []
    for book in books:
        p = Project(book / PROJECT)
        off = int(offsets.get(book.name, 0))
        pages = {"p3": 2 + off, "p4": 3 + off, "p17": 16 + off, "back": len(p.pages) - 1}
        res, added = {}, 0
        for tag in args.tags.split(","):
            if tag in exclude.get(book.name, []):
                res[tag] = "excluded"
                continue
            if not 0 <= pages[tag] < len(p.pages):
                continue
            pg = p.pages[pages[tag]]
            boxes, img, k, color = page_boxes(p, pg, tag)
            if not boxes:
                res[tag] = "nothing found"
                continue
            inv = p.transform(pg.id).xform.inverted()
            after = img.copy()
            d = ImageDraw.Draw(after)
            n = 0
            for x0, y0, x1, y1 in boxes:
                corners = [(x0 / k, y0 / k), (x1 / k, y0 / k), (x1 / k, y1 / k), (x0 / k, y1 / k)]
                n += p.add_fill_zone(pg.id, [inv.map(c) for c in corners], color)
                d.rectangle([x0, y0, x1, y1], fill=color)
            added += n
            res[tag] = {"page": pg.label, "zones": len(boxes), "new": n}
            u = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
            m = int(15 * (pg.image.dpi_x or 300) * k / 25.4)
            crop = (max(0, u[0] - m), max(0, u[1] - m), min(img.width, u[2] + m), min(img.height, u[3] + m))
            a, b = img.crop(crop), after.crop(crop)
            a.thumbnail((260, 260))
            b.thumbnail((260, 260))
            pairs.append((f"{book.name[:28]} {tag}", a, b))
        if added and not args.dry_run:
            p.save()
        report[book.name] = res
        print(book.name[:50], {t: (v if isinstance(v, str) else v["zones"]) for t, v in res.items()}, flush=True)
    (rev / "zones.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in rev.glob("zones-*.png"):
        f.unlink()
    # One sheet holds 12 pairs, so that one look at it checks 12 pages.
    for n in range(0, len(pairs), 12):
        chunk = pairs[n:n + 12]
        sheet = Image.new("RGB", (4 * 270, ((len(chunk) + 1) // 2) * 290), (40, 40, 40))
        dr = ImageDraw.Draw(sheet)
        for i, (label, a, b) in enumerate(chunk):
            ox, oy = (i % 2) * 540, (i // 2) * 290
            dr.text((ox + 4, oy + 2), label, fill=(255, 255, 0))
            sheet.paste(a, (ox + 5, oy + 18))
            sheet.paste(b, (ox + 275, oy + 18))
        sheet.save(rev / f"zones-{n // 12 + 1:02d}.png", optimize=True)
    print(f"books: {len(report)}; sheets in {rev}")
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=None))
