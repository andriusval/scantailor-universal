# List the text pages with a box larger than the book median, and snap their edges at the points that an agent chose.
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.ops import SIZE_HEIGHT_RATIO, SIZE_WIDTH_MM, _body_pages  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.series_fix import page_ref, run_cli  # noqa: E402
from series.stamp_sheet import grid_view  # noqa: E402

PROJECT = "projektas.ScanTailor"
BACKUP = "projektas.before-snap.ScanTailor"


def over_median(p: Project) -> list[dict]:
    body = _body_pages(p)
    if len(body) < 3:
        return []
    med_w = statistics.median(c["content_mm"]["width"] for _, c in body)
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)
    out = []
    for pid, c in body:
        pg = p.page(pid)
        # The scans 001 and 9999 are the covers, and a cover box is not a text box.
        if pg.image.path.stem in ("001", "9999"):
            continue
        dw = c["content_mm"]["width"] - med_w
        dh = c["content_mm"]["height"] - med_h
        if dw > SIZE_WIDTH_MM or c["content_mm"]["height"] > med_h * (1 + SIZE_HEIGHT_RATIO):
            out.append({"label": pg.label, "id": pid, "index": pg.index + 1,
                        "over_mm": {"width": round(dw, 1), "height": round(dh, 1)}})
    return out


def save_view(p: Project, pid: str, folder: Path, title: str, suffix: str) -> Path:
    view = grid_view(p, pid, None, 1000, 1400)
    sheet = Image.new("RGB", (view.width, view.height + 20), (40, 40, 40))
    sheet.paste(view, (0, 20))
    ImageDraw.Draw(sheet).text((4, 4), title, fill=(255, 255, 0))
    path = folder / f"{p.page(pid).index + 1:03d}_{p.page(pid).label}{suffix}.png"
    sheet.save(path)
    return path


def book_folder(root: Path, book: str) -> Path:
    folder = root / re.sub(r"[^\w.-]+", "_", book)[:70]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Size gate: list the boxes larger than the book median; snap edges.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[], help="book folder name (repeat)")
    ap.add_argument("--snap", help="JSON list of {book, page, x, y, side} in page px: double-clicks on printed text")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    rev = series / "st-review"
    root = rev / "size-sheets"
    if args.snap:
        clicks = OrderedDict()
        for c in json.loads(Path(args.snap).read_text(encoding="utf-8")):
            clicks.setdefault(c["book"], []).append(c)
        changed = []
        for book, items in clicks.items():
            proj = series / book / PROJECT
            if not (series / book / BACKUP).exists():
                (series / book / BACKUP).write_bytes(proj.read_bytes())
            p = Project(proj)
            reqs, refs = [], []
            for c in items:
                pg = p.find_page(str(c["page"]))
                side = {"left": ":L", "right": ":R"}.get(pg.side, "")
                reqs.append(f"{pg.image.path.name}{side}@{float(c['x'])},{float(c['y'])}:{c.get('side', 'nearest')}")
                if page_ref(pg) not in refs:
                    refs.append(page_ref(pg))
            with open(rev / "size-gate.log", "a", encoding="utf-8") as log:
                run_cli(proj, 4, 5, refs, log, [f"--content-snap={';'.join(reqs)}"])
            p = Project(proj)
            for label in dict.fromkeys(str(c["page"]) for c in items):
                pg = p.find_page(label)
                print(save_view(p, pg.id, book_folder(root, book), f"{book[:60]} | {pg.label} | after the snap", "_after"))
            changed.append(book)
        # A changed box changes the page size of the whole book, so these books need a full output run.
        books_path = rev / "fit-books.txt"
        old = books_path.read_text(encoding="utf-8").split("\n") if books_path.exists() else []
        books_path.write_text("".join(b + "\n" for b in dict.fromkeys([b for b in old if b] + changed)), encoding="utf-8")
        print(f"{len(changed)} books changed; full output list: {books_path}")
        return 0
    books = sorted(d for d in series.iterdir() if (d / PROJECT).exists())
    if args.only:
        books = [b for b in books if b.name in args.only]
    report = {}
    for book in books:
        p = Project(book / PROJECT)
        pages = over_median(p)
        if not pages:
            continue
        report[book.name] = pages
        for x in pages:
            title = f"{book.name[:60]} | {x['label']} | project page {x['index']} | over the median {x['over_mm']} mm"
            print(save_view(p, x["id"], book_folder(root, book.name), title, ""), flush=True)
    (rev / "size-gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(len(v) for v in report.values())} pages in {len(report)} books: {rev / 'size-gate.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
