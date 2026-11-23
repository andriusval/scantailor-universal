# Cut each content box that is larger than the book median to the median size.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp import checks  # noqa: E402
from st_mcp.ops import SIZE_HEIGHT_RATIO, SIZE_WIDTH_MM, _body_pages, _median_px  # noqa: E402
from st_mcp.preview import render_page  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.series_fix import page_ref, run_cli  # noqa: E402

PROJECT = "projektas.ScanTailor"
BACKUP = "projektas.before-fit.ScanTailor"


def over_median(p: Project) -> list[str]:
    import statistics
    body = _body_pages(p)
    if len(body) < 3:
        return []
    med_w = statistics.median(c["content_mm"]["width"] for _, c in body)
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)
    return [pid for pid, c in body
            if c["content_mm"]["width"] - med_w > SIZE_WIDTH_MM or c["content_mm"]["height"] > med_h * (1 + SIZE_HEIGHT_RATIO)]


def before_after(p: Project, pid: str, new: dict) -> Image.Image:
    img = render_page(p, pid, max_px=900, overlay=False, pad=(255, 255, 255)).convert("RGB")
    k = img.width / p.transform(pid).width
    d = ImageDraw.Draw(img)
    for rect, col in ((p.content(pid)["content_rect_px"], (0, 0, 255)), (new, (0, 170, 0))):
        d.rectangle([rect["x"] * k, rect["y"] * k, (rect["x"] + rect["width"]) * k, (rect["y"] + rect["height"]) * k],
                    outline=col, width=3)
    img.thumbnail((380, 560))
    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cut the boxes larger than the book median to the median size.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[], help="book folder name (repeat)")
    ap.add_argument("--skip", action="append", default=[],
                    help="'<book>|<page label>' to leave alone, for a page that is really larger (repeat)")
    ap.add_argument("--apply", action="store_true", help="write the projects and run filters 4-5")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    rev = series / "st-review"
    rev.mkdir(exist_ok=True)
    books = sorted(d for d in series.iterdir() if (d / PROJECT).exists())
    if args.only:
        books = [b for b in books if b.name in args.only]
    report, tiles, changed_books = [], [], []
    for book in books:
        proj = book / PROJECT
        p = Project(proj)
        todo = []
        for pid in over_median(p):
            pg = p.page(pid)
            # The scans 001 and 9999 are the covers, and a cover box is not a text box.
            if pg.image.path.stem in ("001", "9999") or f"{book.name}|{pg.label}" in args.skip:
                continue
            med = _median_px(p, pid)
            new = checks.median_rect(p, pid, med["width_px"], med["height_px"], med["pad_px"])
            if new is None:
                continue
            old = p.content(pid)["content_rect_px"]
            report.append({"book": book.name, "label": pg.label, "old": old, "new": new})
            tiles.append((f"{book.name[:34]} {pg.label}", before_after(p, pid, new)))
            todo.append((pg, new))
        if not todo:
            continue
        print(book.name[:50], [pg.label for pg, _ in todo], flush=True)
        if args.apply:
            if not (book / BACKUP).exists():
                (book / BACKUP).write_bytes(proj.read_bytes())
            for pg, new in todo:
                p.set_content(pg.id, new)
            p.save()
            with open(rev / "fit-box.log", "a", encoding="utf-8") as log:
                run_cli(proj, 4, 5, [page_ref(pg) for pg, _ in todo], log)
            changed_books.append(book.name)
    sheets = rev / ("fit-box" if args.apply else "fit-box-plan")
    sheets.mkdir(exist_ok=True)
    # One sheet holds 12 pages, blue is the old box and green the new one.
    for s in range(0, len(tiles), 12):
        part = tiles[s:s + 12]
        sheet = Image.new("RGB", (6 * 390, ((len(part) + 5) // 6) * 590), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        for i, (label, im) in enumerate(part):
            ox, oy = (i % 6) * 390, (i // 6) * 590
            d.text((ox + 4, oy + 4), f"{s + i + 1} {label}", fill=(255, 255, 0))
            sheet.paste(im, (ox + 5, oy + 22))
        sheet.save(sheets / f"sheet-{s // 12 + 1:02d}.png")
    name = "fit-box.json" if args.apply else "fit-box-plan.json"
    (rev / name).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.apply:
        # A changed box changes the page size of the whole book, so these books need a full output run.
        (rev / "fit-books.txt").write_text("".join(b + "\n" for b in changed_books), encoding="utf-8")
    print(f"{len(report)} pages in {len(set(r['book'] for r in report))} books; sheets in {sheets}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
