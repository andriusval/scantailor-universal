# Narrow content boxes that a stamp or a handwritten mark made wider than the text, and cover what stays inside.
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.preview import render_page  # noqa: E402
from st_mcp.project import Project  # noqa: E402

from series.detect import ocr_page_number  # noqa: E402
from series.series_fix import page_ref, run_cli  # noqa: E402

PROJECT = "projektas.ScanTailor"
INK = 128


def page_image(p: Project, pid: str) -> tuple[np.ndarray, float]:
    img = render_page(p, pid, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("L")
    pg = p.page(pid)
    k = img.width / p.transform(pid).width
    return np.asarray(img) < INK, (pg.image.dpi_x or 300) * k / 25.4


def head_rule(ink: np.ndarray, r: dict, mm: float, col_px: float) -> tuple[int, int] | None:
    # The rule under the running head spans the text column exactly, so its ends give the box sides.
    xa, xb = int(r["x"]), int(r["x"] + r["width"])
    best = None
    for y in range(int(r["y"]), min(int(r["y"] + 15 * mm), ink.shape[0] - 3)):
        # Three rows together hold a thin rule that is not exactly level.
        row = ink[y:y + 3, xa:xb].any(axis=0)
        idx = np.flatnonzero(row)
        if idx.size < 0.85 * col_px:
            continue
        # A gap below 0.8 mm is a break in the scanned rule, not the end of the rule.
        breaks = np.flatnonzero(np.diff(idx) > 0.8 * mm)
        starts = np.concatenate([[idx[0]], idx[breaks + 1]])
        ends = np.concatenate([idx[breaks], [idx[-1]]])
        j = int(np.argmax(ends - starts))
        run = (int(starts[j] + xa), int(ends[j] + xa))
        if best is None or run[1] - run[0] > best[1] - best[0]:
            best = run
    if best and 0.95 * col_px <= best[1] - best[0] <= 1.03 * col_px:
        return best
    return None


def body_fix(p: Project, pid: str, number: str, col_mm: float, anchor: str | None = None) -> dict:
    ink, mm = page_image(p, pid)
    r = p.content(pid)["content_rect_px"]
    rule = head_rule(ink, r, mm, col_mm * mm)
    # Without a head rule, a person can say which box side is on the text edge; the column width gives the other.
    if not rule and anchor == "right":
        rule = (int(r["x"] + r["width"] - col_mm * mm + 0.5 * mm), int(r["x"] + r["width"] - 0.5 * mm))
    if not rule:
        return {"error": "no running head rule of the column width"}
    pad = 0.5 * mm
    x0, x1 = max(r["x"], rule[0] - pad), min(r["x"] + r["width"], rule[1] + pad)
    new = {"x": float(x0), "y": r["y"], "width": float(x1 - x0), "height": r["height"]}
    zones = []
    num = ocr_page_number(p, pid, number)
    bottom = int(r["y"] + r["height"])
    if num and num["top"] < bottom:
        # The last text line is the lowest ink left of the number, because the stamps sit right of it.
        left = ink[int(r["y"]):num["top"] - int(0.5 * mm), int(x0):max(int(x0) + 1, num["left"] - int(2 * mm))]
        rows = np.flatnonzero(left.any(axis=1))
        if rows.size:
            band0 = int(r["y"]) + int(rows[-1]) + int(0.8 * mm)
            for a, b in ((int(x0), num["left"] - int(1 * mm)), (num["right"] + int(1 * mm), int(x1))):
                if b - a > 2 * mm and ink[band0:bottom + 1, a:b].sum() > 30:
                    zones.append((a, band0, b, bottom + int(1 * mm)))
    return {"new": new, "zones": zones, "number": num}


def ocr_words(img: Image.Image) -> list[dict]:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=buf.getvalue(),
                       capture_output=True)
    out = []
    for row in csv.DictReader(io.StringIO(r.stdout.decode(errors="replace")), delimiter="\t"):
        t = (row.get("text") or "").strip()
        if t and float(row.get("conf") or -1) > 30:
            out.append({"t": t, "x0": int(row["left"]), "y0": int(row["top"]),
                        "x1": int(row["left"]) + int(row["width"]), "y1": int(row["top"]) + int(row["height"])})
    return out


def imprint_fix(p: Project, pid: str) -> dict:
    # The printed imprint page starts with UDK and ends with the ISBN and copyright lines, all at the same column.
    img = render_page(p, pid, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("L")
    pg = p.page(pid)
    mm = (pg.image.dpi_x or 300) * (img.width / p.transform(pid).width) / 25.4
    r = p.content(pid)["content_rect_px"]
    words = ocr_words(img)
    udk = [w for w in words if w["t"].upper().startswith(("UDK", "UDC"))]
    isbn = [w for w in words if w["t"].upper().startswith(("ISBN", "ISSN"))]
    copy = [w for w in words if w["t"].startswith("©")]
    if not udk or not isbn or not copy:
        return {"error": f"OCR: UDK {len(udk)}, ISBN {len(isbn)}, © {len(copy)}"}
    u, c = min(udk, key=lambda w: w["y0"]), max(copy, key=lambda w: w["y0"])
    line = [w for w in words if abs((w["y0"] + w["y1"]) / 2 - (c["y0"] + c["y1"]) / 2) < 2 * mm and w["x0"] >= c["x0"]]
    pad = 0.7 * mm
    x0 = min(u["x0"], min(w["x0"] for w in isbn)) - pad
    x1 = max(w["x1"] for w in line) + pad
    y0 = u["y0"] - pad
    x0, x1, y0 = max(r["x"], x0), min(r["x"] + r["width"], x1), max(r["y"], y0)
    return {"new": {"x": float(x0), "y": float(y0), "width": float(x1 - x0), "height": r["y"] + r["height"] - y0},
            "zones": []}


def column_mm(p: Project) -> float:
    import statistics
    from st_mcp.ops import _body_pages
    return statistics.median(c["content_mm"]["width"] for _, c in _body_pages(p))


def draw(p: Project, pid: str, new: dict | None, zones: list) -> tuple[Image.Image, Image.Image]:
    a = render_page(p, pid, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("RGB")
    b = a.copy()
    r = p.content(pid)["content_rect_px"]
    for im, rect, col in ((a, r, (0, 0, 255)), (b, new or r, (0, 160, 0))):
        d = ImageDraw.Draw(im)
        d.rectangle([rect["x"] - 3, rect["y"] - 3, rect["x"] + rect["width"] + 3, rect["y"] + rect["height"] + 3],
                    outline=col, width=5)
    d = ImageDraw.Draw(b)
    for z in zones:
        d.rectangle(z, fill=(255, 120, 120))
    return a, b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fit boxes of wide pages to the text and add fill zones.")
    ap.add_argument("series")
    ap.add_argument("items", help="JSON list of {book, label, type: body|imprint, number}")
    ap.add_argument("--sheets", help="folder for before/after sheets")
    ap.add_argument("--apply", action="store_true", help="write the projects and run filters 4-5")
    ap.add_argument("--skip", action="append", default=[], help="item number to leave alone")
    args = ap.parse_args(argv)
    series = Path(args.series).resolve()
    items = json.loads(Path(args.items).read_text(encoding="utf-8"))
    report, pairs = [], []
    for n, it in enumerate(items, 1):
        if str(n) in args.skip:
            continue
        proj = series / it["book"] / PROJECT
        p = Project(proj)
        pg = p.find_page(it["label"])
        res = body_fix(p, pg.id, it.get("number", "17"), column_mm(p), it.get("anchor")) if it["type"] == "body" else imprint_fix(p, pg.id)
        res.pop("number", None)
        report.append({"n": n, **it, **{k: v for k, v in res.items()}})
        if "error" in res:
            print(n, it["book"][:40], it["label"], "ERROR", res["error"], flush=True)
            continue
        if args.sheets:
            a, b = draw(p, pg.id, res["new"], res["zones"])
            a.thumbnail((380, 560))
            b.thumbnail((380, 560))
            pairs.append((f"#{n} {it['book'][:30]} {it['label']}", a, b))
        if args.apply:
            p.set_content(pg.id, res["new"])
            inv = p.transform(pg.id).xform.inverted()
            k = 1.0
            for x0, y0, x1, y1 in res["zones"]:
                pts = [(x0 / k, y0 / k), (x1 / k, y0 / k), (x1 / k, y1 / k), (x0 / k, y1 / k)]
                p.add_fill_zone(pg.id, [inv.map(c) for c in pts])
            p.save()
            with open(series / "st-review" / "fit-box.log", "a", encoding="utf-8") as log:
                run_cli(proj, 4, 5, [page_ref(pg)], log)
        print(n, it["book"][:40], it["label"], "box", round(res["new"]["width"]), "zones", len(res["zones"]), flush=True)
    if args.sheets:
        out = Path(args.sheets)
        out.mkdir(parents=True, exist_ok=True)
        for s in range(0, len(pairs), 8):
            ch = pairs[s:s + 8]
            sheet = Image.new("RGB", (4 * 770, ((len(ch) + 1) // 2) * 590), (40, 40, 40))
            d = ImageDraw.Draw(sheet)
            for i, (lab, a, b) in enumerate(ch):
                ox, oy = (i % 2) * 1540, (i // 2) * 590
                d.text((ox + 4, oy + 2), lab, fill=(255, 255, 0))
                sheet.paste(a, (ox + 4, oy + 20))
                sheet.paste(b, (ox + 390, oy + 20))
            sheet.save(out / f"fitbox-{s // 8 + 1:02d}.png")
    (series / "st-review" / ("fit-box.json" if args.apply else "fit-box-plan.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
