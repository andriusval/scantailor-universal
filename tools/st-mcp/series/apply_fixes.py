# Apply page fixes from a list to the existing projects of a series, without a reset.
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp import ops  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.detect import all_ink_rect, barcode_rect_by_density, cover_rect_page  # noqa: E402
from series.series_fix import PROJECT, page_ref, px_to_mm, run_cli  # noqa: E402
from series.zbar import decode  # noqa: E402


def back_from_sticker(p: Project, page_id: str) -> str:
    # When a library sticker covers the ISBN, its barcode takes the place of the ISBN box; the stamp agent covers it later.
    pg = p.page(page_id)
    br = cover_rect_page(p, page_id)
    if br is None:
        return "back cover edge not found"
    t = p.transform(page_id).xform
    syms = decode(np.asarray(Image.open(pg.image.path).convert("L")))
    low = [s for s in syms if t.map(((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2))[1] > br["y"] + br["height"] / 2]
    if low:
        s = max(low, key=lambda d: d["x1"] - d["x0"])
        pc = [t.map((s["x0"], s["y0"])), t.map((s["x1"], s["y1"]))]
        x0, x1 = sorted(c[0] for c in pc)
        y0, y1 = sorted(c[1] for c in pc)
        rect = {"x": x0 - 10, "y": y0 - 40, "width": x1 - x0 + 20, "height": y1 - y0 + 80}
        what = f"{s['type']} {s['data']}"
    else:
        rect = barcode_rect_by_density(p, page_id, br)
        if rect is None:
            return "no barcode found"
        what = "bars found by density"
    p.set_content(page_id, rect)
    dpi = pg.image.dpi_x or 300
    left = px_to_mm(rect["x"] - br["x"], dpi)
    bottom = px_to_mm(br["y"] + br["height"] - rect["y"] - rect["height"], dpi)
    p.set_layout(page_id, margins_mm={"left": left, "top": 5, "right": left, "bottom": bottom},
                 align_v="bottom", align_h="left", match_size=True)
    p.save()
    return what


def apply_book(book: Path, fixes: list[dict], log) -> list[str]:
    proj = book / PROJECT
    done, refs = [], []
    for f in fixes:
        p = Project(proj)
        pg = p.pages[-1] if f["label"] == "BACK" else p.find_page(f["label"])
        act = f["action"]
        if act in ("all_content", "empty", "auto"):
            ops.page_set_content(str(proj), pg.id, mode=act)
            res = act
        elif act == "all_ink":
            # The st-mcp all_content mode misses text on title and contents pages, so this box takes all ink.
            r = all_ink_rect(p, pg.id, pg.image.dpi_x or 300)
            if r is None:
                res = "all_ink: no ink found"
            else:
                p.set_content(pg.id, r)
                p.save()
                res = "all_ink"
        elif act == "bottom":
            c = p.content(pg.id)["content_rect_px"]
            p.set_content(pg.id, {**c, "height": float(f["y"]) - c["y"]})
            p.save()
            res = f"bottom {f['y']}"
        elif act == "back_sticker":
            res = back_from_sticker(p, pg.id)
        else:
            raise ValueError(f"unknown action {act}")
        refs.append(page_ref(Project(proj).page(pg.id)))
        done.append(f"{pg.label}: {res}")
    if refs:
        run_cli(proj, 4, 5, sorted(set(refs)), log)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply page fixes (fixes.json) to existing projects.")
    ap.add_argument("series")
    ap.add_argument("fixes", help="JSON list of {book, label, action[, y]}; label BACK = last page")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    fixes = json.loads(Path(args.fixes).read_text(encoding="utf-8"))
    by_book = defaultdict(list)
    for f in fixes:
        by_book[f["book"]].append(f)
    out = series / "st-review"
    report_path = out / "apply-fixes.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    failed = 0
    with open(out / "apply-fixes.log", "a", encoding="utf-8") as log:
        for n, (name, items) in enumerate(sorted(by_book.items()), 1):
            log.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} {name}\n")
            try:
                res = apply_book(series / name, items, log)
                status = "done"
            except Exception as e:  # noqa: BLE001
                res, status = [str(e)], "failed"
            report.setdefault(name, []).append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status,
                                                "fixes": res})
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{n}/{len(by_book)} {status} {name[:45]}: {res}", flush=True)
            failed += status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
