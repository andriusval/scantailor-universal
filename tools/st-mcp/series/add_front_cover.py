# Add a downloaded front cover (001.jpg) to a finished project, and make only its output.
from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.project import Project  # noqa: E402

from series.series_fix import run_cli  # noqa: E402

PROJECT = "projektas.ScanTailor"
BACKUP = "projektas.be-virselio.ScanTailor"
COVER = "001.jpg"


def zone_count(p: Project) -> int:
    return len(p.root.findall("./filters/output/page/fill-zones/zone"))


def insert_cover(proj: Path) -> None:
    p = Project(proj)
    r = p.root
    ids = [int(e.get("id")) for sec in ("files/file", "images/image", "pages/page") for e in r.findall(sec)]
    fid, iid, pid = (str(max(ids) + k) for k in (1, 2, 3))
    dir_id = r.find("./directories/directory").get("id")
    im = Image.open(proj.parent / COVER)
    dpi = im.info.get("dpi", (300, 300))
    fe = ET.Element("file", {"name": COVER, "dirId": dir_id, "id": fid})
    ie = ET.Element("image", {"subPages": "1", "fileImage": "0", "id": iid, "fileId": fid})
    ET.SubElement(ie, "size", {"height": str(im.height), "width": str(im.width)})
    ET.SubElement(ie, "dpi", {"vertical": str(round(dpi[1])), "horizontal": str(round(dpi[0]))})
    ET.SubElement(ie, "grayscale", {"value": "0"})
    pe = ET.Element("page", {"id": pid, "imageId": iid, "subPage": "single"})
    # The book order comes from the order of these lists, so the cover goes first.
    for sec, el in (("files", fe), ("images", ie), ("pages", pe)):
        r.find(sec).insert(0, el)
    p.set_rotation(iid, 0)
    p.set_split_layout(iid, "single-uncut")
    p.save()


def cover_page(p: Project):
    pages = [pg for pg in p.pages if pg.image.path.name == COVER]
    if len(pages) != 1:
        raise RuntimeError(f"the cover did not become one page: {len(pages)}")
    return pages[0]


def add_cover(book: Path, log) -> str:
    proj = book / PROJECT
    p = Project(proj)
    if any(im.path.name == COVER for im in p.images.values()):
        return "the project already has 001.jpg"
    if not (book / COVER).exists():
        return "no 001.jpg in the folder"
    zones = zone_count(p)
    pages = len(p.pages)
    shutil.copy2(proj, book / BACKUP)
    insert_cover(proj)
    run_cli(proj, 1, 5, [COVER], log)
    p = Project(proj)
    front = cover_page(p)
    pt = p.transform(front.id)
    # The downloaded image is the cover only, without the scanner lid, so the content box is the whole image.
    p.set_content(front.id, {"x": 0.0, "y": 0.0, "width": float(pt.width), "height": float(pt.height)})
    p.set_layout(front.id, margins_mm={"left": 0, "top": 0, "right": 0, "bottom": 0},
                 align_v="top", align_h="hcenter", match_size=False)
    p.set_output(front.id, color_mode="color", dpi=300)
    p.save()
    run_cli(proj, 4, 6, [COVER], log)
    p = Project(proj)
    if len(p.pages) != pages + 1 or zone_count(p) != zones or p.pages[0].image.path.name != COVER:
        raise RuntimeError(f"check failed: pages {pages}->{len(p.pages)}, zones {zones}->{zone_count(p)}")
    return f"ok: {len(p.pages)} pages, {zones} fill zones kept, output {p.output_name(p.pages[0].id)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add 001.jpg as the front cover and make only its output.")
    ap.add_argument("books", nargs="+")
    args = ap.parse_args(argv)
    rc = 0
    for b in args.books:
        book = Path(b).resolve()
        with open(book.parent / "st-review" / "add-front-cover.log", "a", encoding="utf-8") as log:
            try:
                msg = add_cover(book, log)
            except Exception as e:
                msg, rc = f"ERROR {e}", 1
        print(book.name, "|", msg, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
