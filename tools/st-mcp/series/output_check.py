# After an output run: one TIFF size per book, readable TIFFs, and the fill zones of the zone lists still in the projects.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.ops import _check_output  # noqa: E402
from st_mcp.project import Project  # noqa: E402
from st_mcp.zones import zone_rects  # noqa: E402

PROJECT = "projektas.ScanTailor"


def lost_zones(series: Path, lists: list[str]) -> list[str]:
    # A zone that is in a list but not in the project was lost, so its mark is back in the output.
    out = []
    for name in lists:
        for it in json.loads(Path(name).read_text(encoding="utf-8")):
            p = Project(series / it["book"] / PROJECT)
            pid = p.find_page(it["label"]).id
            have = zone_rects(p, pid)
            tr = p.transform(pid)
            for raw in it["zones"]:
                # add_zones.py writes each zone clipped to the page, so the list must be clipped the same way.
                z = (max(0, raw[0]), max(0, raw[1]), min(tr.width, raw[2]), min(tr.height, raw[3]))
                if not any(all(abs(a - b) <= 4 for a, b in zip(z, h)) for h in have):
                    out.append(f"{it['book']} {it['label']} {raw}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the output of the books of a series.")
    ap.add_argument("series")
    ap.add_argument("--only", action="append", default=[], help="book folder name (repeat)")
    ap.add_argument("--books", help="text file with one book folder name per line")
    ap.add_argument("--zones", action="append", default=[], help="zone list JSON of add_zones.py (repeat)")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    books = sorted(d.name for d in series.iterdir() if (d / PROJECT).exists())
    only = list(args.only)
    if args.books:
        only += [b for b in Path(args.books).read_text(encoding="utf-8").splitlines() if b]
    if only:
        books = [b for b in books if b in only]
    bad = 0
    for b in books:
        p = Project(series / b / PROJECT)
        res = _check_output(p, [pg.id for pg in p.pages])
        if not res["ok"] or res["zones_outside_box"]:
            bad += 1
            print(f"{b}: missing {res['missing_tiff']} unreadable {[u['page'] for u in res['unreadable_tiff']]} "
                  f"odd size {[o['page'] for o in res['odd_size']]} zones outside box {len(res['zones_outside_box'])}",
                  flush=True)
    lost = lost_zones(series, args.zones)
    for line in lost:
        print("lost zone:", line)
    print(f"{len(books)} books, {bad} with problems, {len(lost)} lost zones")
    return 1 if bad or lost else 0


if __name__ == "__main__":
    sys.exit(main())
