"""Heuristics that replace "sort by Width/Height in the GUI and eyeball the extremes".

Every finding carries evidence (numbers, coordinates in page px) so the agent can
look at the preview and decide; nothing here edits the project.
"""
from __future__ import annotations

import statistics

import numpy as np
from PIL import Image

from .preview import render_page
from .project import Project

ANALYSIS_PX = 1000          # analyse pages at this long-side resolution
EDGE_IGNORE = 0.04          # ignore this fraction at page edges (scan borders, spine shadow)


def _robust_z(values: dict[str, float]) -> dict[str, float]:
    vals = list(values.values())
    if len(vals) < 3:
        return {k: 0.0 for k in values}
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals) or 1e-9
    return {k: 0.6745 * (v - med) / mad for k, v in values.items()}


def content_stats(project: Project) -> list[dict]:
    """One row per page: content size + robust z-scores for width/height (the GUI sort keys)."""
    rows = []
    for pg in project.pages:
        c = project.content(pg.id)
        r = c.get("content_rect_px") if c.get("processed") else None
        rows.append({"page_id": pg.id, "label": pg.label, "index": pg.index,
                     "empty": bool(c.get("empty")) if c.get("processed") else None,
                     "mode": c.get("mode"),
                     "w": r["width"] if r else 0.0, "h": r["height"] if r else 0.0})
    nonempty = {r["page_id"]: r for r in rows if r["empty"] is False}
    zw = _robust_z({k: r["w"] for k, r in nonempty.items()})
    zh = _robust_z({k: r["h"] for k, r in nonempty.items()})
    for r in rows:
        r["z_width"] = round(zw.get(r["page_id"], 0.0), 2)
        r["z_height"] = round(zh.get(r["page_id"], 0.0), 2)
    return rows


def _ink_mask(project: Project, page_id: str) -> tuple[np.ndarray, float]:
    im = render_page(project, page_id, max_px=ANALYSIS_PX, overlay=False)
    g = np.asarray(im.convert("L"), dtype=np.float32)
    # page background estimate: bright percentile; ink = clearly darker than paper
    paper = np.percentile(g, 90)
    ink = g < paper * 0.55
    pt = project.transform(page_id)
    k = min(1.0, ANALYSIS_PX / max(pt.width, pt.height))
    return ink, k


def _rows_with_ink(ink: np.ndarray, x0: int, x1: int, y0: int, y1: int, min_frac: float = 0.01) -> list[int]:
    if y1 <= y0 or x1 <= x0:
        return []
    band = ink[y0:y1, x0:x1]
    counts = band.sum(axis=1)
    return [y0 + i for i, c in enumerate(counts) if c >= max(2, min_frac * (x1 - x0))]


def _group(rows: list[int], gap: int = 3) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for r in rows:
        if out and r - out[-1][1] <= gap:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


def check_page(project: Project, page_id: str) -> dict:
    """Ink outside the content box (missed page numbers / headers) and junk near box edges."""
    pg = project.page(page_id)
    c = project.content(pg.id)
    if not c.get("processed"):
        return {"page_id": pg.id, "label": pg.label, "error": "select-content not run"}
    ink, k = _ink_mask(project, pg.id)
    H, W = ink.shape
    ex, ey = int(W * EDGE_IGNORE), int(H * EDGE_IGNORE)
    findings = []

    if c["empty"]:
        dark = float(ink[ey:H - ey, ex:W - ex].mean())
        if dark > 0.002:
            findings.append({"kind": "empty_but_ink", "ink_fraction": round(dark, 4),
                             "hint": "content box is empty but the page has ink; check preview"})
        return {"page_id": pg.id, "label": pg.label, "findings": findings, "text_lines": None}

    r = c["content_rect_px"]
    bx0, by0 = int(r["x"] * k), int(r["y"] * k)
    bx1, by1 = int((r["x"] + r["width"]) * k), int((r["y"] + r["height"]) * k)

    # text lines inside the box -> typical line height, last/first line geometry
    lines = [ln for ln in _group(_rows_with_ink(ink, bx0, bx1, by0, by1)) if ln[1] - ln[0] + 1 >= 3]
    line_h = statistics.median([b - a + 1 for a, b in lines]) if lines else 0

    # ink bands above / below the box (page numbers, running heads)
    for where, (y0, y1) in (("above", (ey, by0 - 2)), ("below", (by1 + 2, H - ey))):
        bands = _group(_rows_with_ink(ink, max(ex, bx0 - 20), min(W - ex, bx1 + 20), y0, y1, 0.004))
        for a, b in bands:
            hgt = b - a + 1
            if hgt < 3 or (line_h and hgt > 3 * line_h):
                continue  # specks or big dark areas (scan border), not text
            cols = np.where(ink[a:b + 1, ex:W - ex].any(axis=0))[0]
            findings.append({
                "kind": f"ink_{where}_box",
                "band_px": {"x": round((ex + int(cols.min())) / k), "y": round(a / k),
                            "width": round((cols.max() - cols.min() + 1) / k), "height": round(hgt / k)},
                "distance_px": round(((by0 - b) if where == "above" else (a - by1)) / k),
                "hint": "possibly a page number or running head outside the content box",
            })

    # page-number position inside the box: short isolated last/first line
    number_pos = None
    if len(lines) >= 3:
        def width_of(a: int, b: int) -> int:
            # number of columns with ink (not the extent: edge shadows would span the whole box)
            return int(ink[a:b + 1, bx0:bx1].any(axis=0).sum())
        bw = bx1 - bx0
        gaps = [lines[i + 1][0] - lines[i][1] for i in range(len(lines) - 1)]
        tg = statistics.median(gaps)
        if width_of(*lines[-1]) < 0.15 * bw and gaps[-1] > 1.5 * tg:
            number_pos = "bottom"
        elif width_of(*lines[0]) < 0.15 * bw and gaps[0] > 1.5 * tg:
            number_pos = "top"

    # junk: ink touching the box edge but separated from text by a big gap
    if len(lines) >= 3:
        gaps = [lines[i + 1][0] - lines[i][1] for i in range(len(lines) - 1)]
        typical_gap = statistics.median(gaps)
        for name, (a, b), gap in (("first", lines[0], gaps[0]), ("last", lines[-1], gaps[-1])):
            hgt = b - a + 1
            is_number = number_pos == ("top" if name == "first" else "bottom")
            if gap > 4 * max(typical_gap, 1) and hgt < 0.5 * line_h and not is_number:
                findings.append({"kind": f"thin_{name}_line_isolated", "gap_px": round(gap / k),
                                 "height_px": round(hgt / k),
                                 "hint": "isolated thin ink line at box edge: likely scan junk, consider shrinking"})

    if number_pos is None:  # a small isolated block right outside the box also tells where numbers sit
        for f in findings:
            b = f.get("band_px")
            if b and line_h and b["height"] * k <= 2 * line_h and b["width"] * k < 0.15 * (bx1 - bx0):
                number_pos = "bottom" if f["kind"] == "ink_below_box" else "top"
                f["hint"] = f"looks like a page number missed by the content box ({number_pos})"
    return {"page_id": pg.id, "label": pg.label, "findings": findings,
            "text_lines": len(lines), "page_number_in_box": number_pos}


def detect_cover_rect(project: Project, page_id: str) -> dict | None:
    """Bounding box of the book cover lying on a darker scanner background (page px)."""
    pg = project.page(page_id)
    im = render_page(project, pg.id, max_px=ANALYSIS_PX, overlay=False)
    g = np.asarray(im.convert("L"), dtype=np.float32)
    pt = project.transform(pg.id)
    k = min(1.0, ANALYSIS_PX / max(pt.width, pt.height))
    bg = np.percentile(g, 10)
    fg = np.percentile(g, 90)
    if fg - bg < 40:
        return None  # no contrast between cover and background
    light = g > (bg + 0.35 * (fg - bg))

    def longest_run(mask: np.ndarray) -> tuple[int, int] | None:
        best, start = None, None
        for i, v in enumerate(list(mask) + [False]):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if best is None or i - start > best[1] - best[0]:
                    best = (start, i)
                start = None
        return best

    cols = longest_run(light.mean(axis=0) > 0.35)
    if not cols:
        return None
    rows = longest_run(light[:, cols[0]:cols[1]].mean(axis=1) > 0.35)
    if not rows:
        return None
    # refine columns inside the found rows (removes background noise columns)
    cols = longest_run(light[rows[0]:rows[1], :].mean(axis=0) > 0.35) or cols
    pad = 2
    x0, x1 = max(0, cols[0] - pad), min(g.shape[1], cols[1] + pad)
    y0, y1 = max(0, rows[0] - pad), min(g.shape[0], rows[1] + pad)
    return {"x": x0 / k, "y": y0 / k, "width": (x1 - x0) / k, "height": (y1 - y0) / k}


def picture_score(project: Project, page_id: str) -> dict:
    """Fraction of the content area that looks like a halftone/photo rather than text."""
    pg = project.page(page_id)
    c = project.content(pg.id)
    im = render_page(project, pg.id, max_px=ANALYSIS_PX, overlay=False)
    g = np.asarray(im.convert("L"), dtype=np.float32)
    pt = project.transform(pg.id)
    k = min(1.0, ANALYSIS_PX / max(pt.width, pt.height))
    if c.get("processed") and not c.get("empty"):
        r = c["content_rect_px"]
        g = g[int(r["y"] * k):int((r["y"] + r["height"]) * k), int(r["x"] * k):int((r["x"] + r["width"]) * k)]
    if g.size == 0:
        return {"page_id": pg.id, "label": pg.label, "picture_fraction": 0.0}
    paper = np.percentile(g, 90)
    B = 16
    h, w = (g.shape[0] // B) * B, (g.shape[1] // B) * B
    blocks = g[:h, :w].reshape(h // B, B, w // B, B).swapaxes(1, 2).reshape(-1, B * B)
    mean = blocks.mean(axis=1)
    # mid-tone fraction per block: text is mostly paper or ink, photos are full of mid-tones
    mid = ((blocks > paper * 0.25) & (blocks < paper * 0.80)).mean(axis=1)
    pic = (mid > 0.5) & (mean < paper * 0.85)
    return {"page_id": pg.id, "label": pg.label, "picture_fraction": round(float(pic.mean()), 3)}


def tighten_rect(project: Project, page_id: str) -> dict | None:
    """Shrink the content box vertically to the text block: drop tiny isolated specks at the top or
    bottom edge (scan junk) but keep page numbers and real text lines. Returns the new rect (page px)."""
    pg = project.page(page_id)
    c = project.content(pg.id)
    if not c.get("processed") or c.get("empty"):
        return None
    ink, k = _ink_mask(project, pg.id)
    r = c["content_rect_px"]
    bx0, by0 = int(r["x"] * k), int(r["y"] * k)
    bx1, by1 = int((r["x"] + r["width"]) * k), int((r["y"] + r["height"]) * k)
    lines = _group(_rows_with_ink(ink, bx0, bx1, by0, by1, 0.004))
    if not lines:
        return None

    def cols(a: int, b: int) -> int:
        return int(ink[a:b + 1, bx0:bx1].any(axis=0).sum())

    real = [ln for ln in lines if ln[1] - ln[0] + 1 >= 3]
    line_h = statistics.median([b - a + 1 for a, b in real]) if real else 3
    # a speck: very short in both directions compared with a text line
    keep = [ln for ln in lines if not (ln[1] - ln[0] + 1 < 0.5 * line_h and cols(*ln) < 2 * line_h)]
    if not keep:
        return None
    top, bottom = keep[0][0], keep[-1][1]
    pad = max(2, int(0.2 * line_h))
    y0 = max(by0, top - pad)
    y1 = min(by1, bottom + pad)
    return {"x": r["x"], "y": y0 / k, "width": r["width"], "height": (y1 - y0) / k}
