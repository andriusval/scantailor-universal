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
    dev = [abs(v - med) for v in vals]
    mad = statistics.median(dev)
    if mad == 0:  # more than half the values are identical: use the mean deviation, floored at 1 % of the median
        mad = max(statistics.fmean(dev) * 0.6745 / 0.7979, 0.01 * abs(med) * 0.6745, 1e-9)
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


def _rows_with_ink(ink: np.ndarray, x0: int, x1: int, y0: int, y1: int, min_frac: float = 0.01,
                   min_px: int = 2) -> list[int]:
    if y1 <= y0 or x1 <= x0:
        return []
    band = ink[y0:y1, x0:x1]
    counts = band.sum(axis=1)
    return [y0 + i for i, c in enumerate(counts) if c >= max(min_px, min_frac * (x1 - x0))]


def _dilate(m: np.ndarray) -> np.ndarray:
    d = m.copy()
    d[1:, :] |= m[:-1, :]
    d[:-1, :] |= m[1:, :]
    e = d.copy()
    e[:, 1:] |= d[:, :-1]
    e[:, :-1] |= d[:, 1:]
    return e


def _cols_with_ink(ink: np.ndarray, x0: int, x1: int, y0: int, y1: int, min_frac: float = 0.004) -> list[int]:
    if y1 <= y0 or x1 <= x0:
        return []
    counts = ink[y0:y1, x0:x1].sum(axis=0)
    return [x0 + i for i, c in enumerate(counts) if c >= max(2, min_frac * (y1 - y0))]


def _side_junk(ink: np.ndarray, bx0: int, bx1: int, by0: int, by1: int, line_h: float) -> list[dict]:
    """Narrow ink column groups at the left/right edge of the box, separated from the text block by a
    wide empty gap (spine-shadow edges, specks, finger marks that stretched the box sideways)."""
    gap = max(3, int(line_h))
    groups = _group(_cols_with_ink(ink, bx0, bx1, by0, by1), gap=gap)
    if len(groups) < 2:
        return []
    bw = bx1 - bx0
    main = max(groups, key=lambda g: g[1] - g[0])
    lines = [ln for ln in _group(_rows_with_ink(ink, bx0, bx1, by0, by1, 0.004)) if ln[1] - ln[0] + 1 >= 3]
    edge_lines = [lines[0], lines[-1]] if lines else []
    out = []
    for side, grp, nxt in (("left", groups[0], groups[1]), ("right", groups[-1], groups[-2])):
        if grp == main:
            continue
        width = grp[1] - grp[0] + 1
        dist = (nxt[0] - grp[1]) if side == "left" else (grp[0] - nxt[1])
        rows = np.where(ink[by0:by1, grp[0]:grp[1] + 1].any(axis=1))[0] + by0
        if len(rows) and any(a - 2 <= rows.min() and rows.max() <= b + 2 for a, b in edge_lines):
            continue  # Ink only in the first or last line is a page number or a header in a corner.
        if width < 0.08 * bw and dist > 2 * max(line_h, 3):
            out.append({"side": side, "x0": grp[0], "x1": grp[1], "gap": dist, "width": width})
    return out


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

    # Ink bands above or below the box are often page numbers or page headers.
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

    # ink left / right of the box (text cut off by the box, marginalia, junk)
    for where, (x0, x1) in (("left", (ex, bx0 - 2)), ("right", (bx1 + 2, W - ex))):
        for a, b in _group(_cols_with_ink(ink, x0, x1, by0, by1), gap=max(3, int(line_h))):
            band = ink[by0:by1, a:b + 1]
            rows = np.where(band.any(axis=1))[0]
            if band.sum() < max(12, line_h * line_h) or not len(rows):
                continue  # specks
            if band[rows.min():rows.max() + 1].mean() > 0.35:
                continue  # solid dark area: spine shadow, finger, scan border - not text, already outside
            dist = (bx0 - b) if where == "left" else (a - bx1)
            bw_, bh_ = b - a + 1, rows.max() - rows.min() + 1
            if line_h and bw_ < 1.5 * line_h and bh_ > 5 * line_h and dist > 0.5 * line_h:
                continue  # A tall narrow band away from the text is the spine, the page edge or a shadow.
            n_lines = len([g for g in _group(list(rows)) if g[1] - g[0] + 1 >= 3])
            findings.append({
                "kind": f"ink_{where}_of_box",
                "band_px": {"x": round(a / k), "y": round((by0 + rows.min()) / k),
                            "width": round((b - a + 1) / k), "height": round((rows.max() - rows.min() + 1) / k)},
                "distance_px": round(dist / k), "text_lines_touched": n_lines,
                "hint": ("text-like ink right next to the box: lines may be cut off, consider expanding"
                         if dist <= 0.5 * max(line_h, 3) and n_lines >= 2
                         else "ink beside the box: marginalia, cut text or junk; check preview"),
            })

    # junk at the left/right edge inside the box (stretches the box sideways)
    for j in _side_junk(ink, bx0, bx1, by0, by1, line_h):
        findings.append({"kind": f"thin_{j['side']}_column_isolated", "gap_px": round(j["gap"] / k),
                         "width_px": round(j["width"] / k),
                         "hint": "isolated narrow ink at box side: likely scan junk; pages_snap_content(sides=auto)"})

    # empty space between the box and the text block (faint junk ScanTailor counted as content)
    ex = box_excess(project, pg.id)
    if ex:
        over = {s_: v for s_, v in ex["excess_px"].items() if v > ex["threshold_px"]}
        if over:
            findings.append({"kind": "box_exceeds_text", "excess_px": over, "threshold_px": ex["threshold_px"],
                             "hint": "box is larger than the text on these sides (junk at the edge): "
                                     "pages_snap_content(sides=auto), then preview"})

    # page-number position inside the box: short isolated last/first line
    number_pos = None
    if len(lines) >= 3:
        def width_of(a: int, b: int) -> int:
            # Count the columns with ink, because an edge shadow can span the full box width.
            return int(ink[a:b + 1, bx0:bx1].any(axis=0).sum())
        bw = bx1 - bx0
        gaps = [lines[i + 1][0] - lines[i][1] for i in range(len(lines) - 1)]
        tg = statistics.median(gaps)
        if width_of(*lines[-1]) < 0.15 * bw and gaps[-1] > 1.5 * tg:
            number_pos = "bottom"
        elif width_of(*lines[0]) < 0.15 * bw and gaps[0] > 1.5 * tg:
            number_pos = "top"

    # Ink at the box edge with a big gap to the text is junk.
    if len(lines) >= 3:
        gaps = [lines[i + 1][0] - lines[i][1] for i in range(len(lines) - 1)]
        typical_gap = statistics.median(gaps)
        for name, (a, b), gap in (("first", lines[0], gaps[0]), ("last", lines[-1], gaps[-1])):
            hgt = b - a + 1
            is_number = number_pos == ("top" if name == "first" else "bottom")
            if gap > 4 * max(typical_gap, 1) and hgt < 0.5 * line_h and not is_number:
                findings.append({"kind": f"thin_{name}_line_isolated", "gap_px": round(gap / k),
                                 "height_px": round(hgt / k),
                                 "hint": "isolated thin ink line at box edge: likely scan junk; pages_snap_content(sides=auto)"})

    if number_pos is None:  # A small block just outside the box also shows where the page numbers are.
        for f in findings:
            if f["kind"] not in ("ink_above_box", "ink_below_box"):
                continue  # marks beside the text say nothing about where page numbers sit
            b = f.get("band_px")
            if b and line_h and b["height"] * k <= 2 * line_h and b["width"] * k < 0.15 * (bx1 - bx0):
                number_pos = "bottom" if f["kind"] == "ink_below_box" else "top"
                f["hint"] = f"looks like a page number missed by the content box ({number_pos})"
    return {"page_id": pg.id, "label": pg.label, "findings": findings,
            "text_lines": len(lines), "page_number_in_box": number_pos}


def detect_cover_rect(project: Project, page_id: str) -> dict | None:
    """Bounding box of the book cover lying on a darker scanner background (page px)."""
    pg = project.page(page_id)
    # Paint the area outside the scan black, so that it looks like the scanner background and not like the cover.
    im = render_page(project, pg.id, max_px=ANALYSIS_PX, overlay=False, pad=(0, 0, 0))
    g = np.asarray(im.convert("L"), dtype=np.float32)
    pt = project.transform(pg.id)
    k = min(1.0, ANALYSIS_PX / max(pt.width, pt.height))
    f = max(2, int(0.03 * min(g.shape)))
    # The darkest side gives the background, because on a split scan the cover can reach one side of the half.
    sides = [g[:f].ravel(), g[-f:].ravel(), g[:, :f].ravel(), g[:, -f:].ravel()]
    bg = float(min(np.percentile(side, 90) for side in sides))
    fg = np.percentile(g, 90)
    if fg - bg < 15:
        return None  # no contrast between cover and background
    # dark covers (e.g. dark green on black) are only a few tens of levels above the background
    light = g > bg + min(0.35 * (fg - bg), max(12.0, 0.5 * (np.median(g[g > bg + 12]) - bg)
                                                if (g > bg + 12).any() else 12.0))

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
    rough = {"x": cols[0] / k, "y": rows[0] / k, "width": (cols[1] - cols[0]) / k, "height": (rows[1] - rows[0]) / k}
    return refine_cover_edges(project, pg.id, rough)


# Each edge of the rough box moves to the strongest brightness step near it, because the cover edge is that step.
def refine_cover_edges(project: Project, page_id: str, rect: dict, search: float = 0.03,
                       inset_mm: float = 1.0) -> dict:
    pt = project.transform(page_id)
    im = render_page(project, page_id, max_px=2400, overlay=False, pad=(0, 0, 0))
    g = np.asarray(im.convert("L"), dtype=np.float32)
    k = min(1.0, 2400 / max(pt.width, pt.height))
    x0, y0 = rect["x"] * k, rect["y"] * k
    x1, y1 = x0 + rect["width"] * k, y0 + rect["height"] * k
    w = max(4, int(search * max(g.shape)))

    def step(profile: np.ndarray, at: float, rising: bool) -> float:
        # A smooth profile ignores print detail, so the step that remains is the cover edge.
        sm = np.convolve(profile, np.ones(5) / 5, mode="same")
        lo, hi = max(2, int(at) - w), min(len(sm) - 3, int(at) + w)
        if hi <= lo:
            return at
        d = sm[lo + 2:hi + 2] - sm[lo - 2:hi - 2]
        i = int(np.argmax(d if rising else -d))
        return float(lo + i)

    # The middle 60 % of the other axis keeps the corners and the end shadows out of the profile.
    def band(a0: float, a1: float) -> slice:
        m = 0.2 * (a1 - a0)
        return slice(int(a0 + m), max(int(a0 + m) + 1, int(a1 - m)))

    cols_profile = g[band(y0, y1), :].mean(axis=0)
    rows_profile = g[:, band(x0, x1)].mean(axis=1)
    nx0 = step(cols_profile, x0, True)
    nx1 = step(cols_profile, x1, False)
    ny0 = step(rows_profile, y0, True)
    ny1 = step(rows_profile, y1, False)
    if nx1 - nx0 < 0.5 * (x1 - x0) or ny1 - ny0 < 0.5 * (y1 - y0):
        nx0, nx1, ny0, ny1 = x0, x1, y0, y1  # the steps do not look like a cover, so keep the rough box
    # Move a little inside the edge, so that no background or worn edge shows in the output.
    pg = project.page(page_id)
    inset = inset_mm / 25.4 * min(pg.image.dpi_x, pg.image.dpi_y) * k
    nx0, ny0, nx1, ny1 = nx0 + inset, ny0 + inset, nx1 - inset, ny1 - inset
    return {"x": nx0 / k, "y": ny0 / k, "width": (nx1 - nx0) / k, "height": (ny1 - ny0) / k}


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
    # line art (engravings, drawings, maps): blocks densely covered with ink; body text stays far below
    ink_frac = (blocks < paper * 0.55).mean(axis=1)
    dense = ink_frac > 0.45
    return {"page_id": pg.id, "label": pg.label, "picture_fraction": round(float(pic.mean()), 3),
            "line_art_fraction": round(float(dense.mean()), 3)}


def text_block(project: Project, page_id: str) -> dict | None:
    """Where the printed text really is inside the content box (analysis px), ignoring specks and junk.

    Top/bottom: first/last ink line that is not a speck. Left/right: on justified text, the column where
    at least a quarter of the full-width lines have ink (junk next to the text touches only 1-2 lines);
    otherwise the ink extent after dropping isolated side junk."""
    pg = project.page(page_id)
    c = project.content(pg.id)
    if not c.get("processed") or c.get("empty"):
        return None
    ink, k = _ink_mask(project, pg.id)
    r = c["content_rect_px"]
    bx0, by0 = int(r["x"] * k), int(r["y"] * k)
    bx1, by1 = int((r["x"] + r["width"]) * k), int((r["y"] + r["height"]) * k)
    # typical text-line height from the plain mask (dilation would merge close lines at small scale)
    raw = [ln for ln in _group(_rows_with_ink(ink, bx0, bx1, by0, by1, 0.004)) if ln[1] - ln[0] + 1 >= 3]
    line_h = statistics.median([b - a + 1 for a, b in raw]) if raw else 3
    # Dilate 3x3, because thin page-number digits break into 1 px parts at the analysis scale.
    ink = _dilate(ink)
    lines = _group(_rows_with_ink(ink, bx0, bx1, by0, by1, 0.0, min_px=1))
    if not lines:
        return None

    def ncols(a: int, b: int) -> int:
        return int(ink[a:b + 1, bx0:bx1].any(axis=0).sum())

    # a speck: very short in both directions compared with a text line
    keep = [ln for ln in lines if not (ln[1] - ln[0] + 1 < 0.5 * line_h and ncols(*ln) < 2 * line_h)]
    if not keep:
        return None
    top, bottom = keep[0][0], keep[-1][1]

    body = [ln for ln in keep if ln[1] - ln[0] + 1 >= 0.6 * line_h and ncols(*ln) > 0.5 * (bx1 - bx0)]
    justified = False
    if len(body) >= 6:
        cov = np.zeros(bx1 - bx0)
        for a, b in body:
            cov += ink[a:b + 1, bx0:bx1].any(axis=0)
        idx = np.where(cov >= 0.25 * len(body))[0]
        if len(idx):
            w = max(2, int(0.3 * line_h))
            left, right = bx0 + int(idx[0]), bx0 + int(idx[-1]) + 1
            # Justified text has a sharp edge where many lines end within a few pixels.
            justified = bool(cov[idx[0]:idx[0] + w].max() >= 0.5 * len(body)
                         and cov[max(0, idx[-1] - w + 1):idx[-1] + 1].max() >= 0.5 * len(body))
    if not justified:
        lo, hi = bx0, bx1
        for j in _side_junk(ink, bx0, bx1, top, bottom + 1, line_h):
            if j["side"] == "left":
                lo = j["x1"] + 1
            else:
                hi = j["x0"]
        inked = _cols_with_ink(ink, lo, hi, top, bottom + 1)
        left, right = (inked[0], inked[-1] + 1) if inked else (bx0, bx1)
    return {"k": k, "box": (bx0, by0, bx1, by1), "line_h": line_h, "justified": justified,
            "left": left, "top": top, "right": right, "bottom": bottom + 1}


def box_excess(project: Project, page_id: str) -> dict | None:
    """Empty space between each side of the content box and the text block, in page px."""
    tb = text_block(project, page_id)
    if tb is None:
        return None
    k, (bx0, by0, bx1, by1) = tb["k"], tb["box"]
    ex = {"left": tb["left"] - bx0, "right": bx1 - tb["right"], "top": tb["top"] - by0, "bottom": by1 - tb["bottom"]}
    return {"excess_px": {s: round(float(v) / k) for s, v in ex.items()}, "line_h_px": round(float(tb["line_h"]) / k),
            "justified": tb["justified"], "threshold_px": round(0.6 * float(tb["line_h"]) / k)}


def tighten_rect(project: Project, page_id: str) -> dict | None:
    """Fit the content box to the text block on all four sides: drops specks and junk at the edges
    (including faint junk next to the text) but keeps page numbers, running heads and real lines.
    Safety: a side is left as it was if the strip it would cut has ink in 3+ text lines (that is text,
    not junk). Returns the new rect (page px) plus `kept_sides` when that happened."""
    tb = text_block(project, page_id)
    if tb is None:
        return None
    k, (bx0, by0, bx1, by1) = tb["k"], tb["box"]
    pad = max(2, int(0.25 * tb["line_h"]))
    x0, y0 = max(bx0, tb["left"] - pad), max(by0, tb["top"] - pad)
    x1, y1 = min(bx1, tb["right"] + pad), min(by1, tb["bottom"] + pad)
    ink, _ = _ink_mask(project, page_id)
    lines = [ln for ln in _group(_rows_with_ink(ink, bx0, bx1, by0, by1, 0.004)) if ln[1] - ln[0] + 1 >= 3]
    # Thin junk columns at the edge cross every line, so the safety rule must ignore them.
    ink = ink.copy()
    for j in _side_junk(ink, bx0, bx1, by0, by1, tb["line_h"]):
        ink[by0:by1, j["x0"]:j["x1"] + 1] = False
    kept = []
    for side, (c0, c1) in (("left", (bx0, x0)), ("right", (x1, bx1))):
        if c1 > c0 and sum(1 for a, b in lines if ink[a:b + 1, c0:c1].any()) >= 3:
            kept.append(side)
            x0, x1 = (bx0, x1) if side == "left" else (x0, bx1)
            continue
        # Ink that continues a line within a word gap is text, so the edge moves out to keep it.
        lh = tb["line_h"]
        for a, b in lines:
            strip = ink[a:b + 1, c0:c1]
            if c1 <= c0 or strip.sum() < max(6, 0.15 * lh * lh):
                continue
            cols = np.where(strip.any(axis=0))[0] + c0
            inner = np.where(ink[a:b + 1, x0:x1].any(axis=0))[0] + x0
            if not len(inner):
                continue
            gap = (inner.min() - cols.max()) if side == "left" else (cols.min() - inner.max())
            if gap <= 0.8 * lh:
                if side == "left":
                    x0 = max(bx0, min(x0, int(cols.min()) - pad))
                else:
                    x1 = min(bx1, max(x1, int(cols.max()) + 1 + pad))
                kept.append(f"{side}:line")
    rect = {"x": x0 / k, "y": y0 / k, "width": (x1 - x0) / k, "height": (y1 - y0) / k}
    if kept:
        rect["kept_sides"] = kept
    return rect


def _print_mask(g: np.ndarray, B: int = 8) -> tuple[np.ndarray, int]:
    """Blocks (B px) holding printed matter (photo detail, halftone, letters), specks removed.

    Uses local texture (mean neighbour difference) rather than darkness: spine shadows, scan-border
    bands and page-curvature gradients can be dark but are smooth, so they are not print."""
    hf = np.zeros_like(g)
    hf[:-1, :-1] = (np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]) / 2
    paper = np.percentile(g, 95)
    h, w = (g.shape[0] // B) * B, (g.shape[1] // B) * B

    def blocks(a: np.ndarray) -> np.ndarray:
        return a[:h, :w].reshape(h // B, B, w // B, B).swapaxes(1, 2).reshape(h // B, w // B, -1)

    bh, bg = blocks(hf).mean(axis=2), blocks(g).mean(axis=2)
    plain = bh[bg > 0.9 * paper]
    base = float(np.median(plain)) if plain.size else 2.5
    m = bh > max(4.0, 1.8 * base)
    # drop isolated blocks (specks): keep blocks with at least 2 marked neighbours
    p = np.pad(m, 1)
    nb = sum(np.roll(np.roll(p, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1))[1:-1, 1:-1] - m
    return m & (nb >= 2), B


def all_content_rect(project: Project, page_id: str) -> dict | None:
    """Box around everything printed on the page (photos, plates, captions), page px.
    For picture pages, where ScanTailor's text-oriented detection crops or misses images."""
    pg = project.page(page_id)
    im = render_page(project, pg.id, max_px=ANALYSIS_PX, overlay=False, pad=(255, 255, 255))
    g = np.asarray(im.convert("L"), dtype=np.float32)
    pt = project.transform(pg.id)
    k = min(1.0, ANALYSIS_PX / max(pt.width, pt.height))
    m, B = _print_mask(g)
    H, W = m.shape
    e = max(1, int(0.015 * max(H, W)))  # page edges: shadows, scan borders
    m[:e, :] = m[-e:, :] = False
    m[:, :e] = m[:, -e:] = False
    # side columns with only scattered marks (textured spine shadow): an image edge fills its column
    col = m.sum(axis=0)
    thr = max(2, int(0.03 * H))
    xs_ok = np.where(col >= thr)[0]
    if len(xs_ok):
        m[:, :xs_ok[0]] = False
        m[:, xs_ok[-1] + 1:] = False
    # Remove only thin vertical strips with a gap, because captions are thin horizontal strips.
    for axis in (0,):
        prof = m.any(axis=axis)
        groups = _group(list(np.where(prof)[0]), gap=1)
        while len(groups) > 1 and groups[0][1] - groups[0][0] < 3 and groups[1][0] - groups[0][1] > 2:
            a, b = groups.pop(0)
            if axis == 0:
                m[:, a:b + 1] = False
            else:
                m[a:b + 1, :] = False
        while len(groups) > 1 and groups[-1][1] - groups[-1][0] < 3 and groups[-1][0] - groups[-2][1] > 2:
            a, b = groups.pop()
            if axis == 0:
                m[:, a:b + 1] = False
            else:
                m[a:b + 1, :] = False
    ys, xs = np.where(m)
    if not len(ys):
        return None
    x0, x1 = xs.min() * B, (xs.max() + 1) * B
    y0, y1 = ys.min() * B, (ys.max() + 1) * B
    return {"x": x0 / k, "y": y0 / k, "width": (x1 - x0) / k, "height": (y1 - y0) / k}


def picture_skew(project: Project, page_id: str) -> float | None:
    """Residual skew (degrees, counter-clockwise positive as in PIL) of the printed rectangles on a
    picture page, from sharpness of the row/column profiles of its print mask."""
    pg = project.page(page_id)
    im = render_page(project, pg.id, max_px=800, overlay=False, pad=(255, 255, 255))
    g = np.asarray(im.convert("L"), dtype=np.float32)
    paper = np.percentile(g, 95)
    mask = Image.fromarray(((g < paper * 0.8) * 255).astype(np.uint8))
    best, best_a = -1.0, 0.0
    for a in np.arange(-10, 10.01, 0.25):
        r = np.asarray(mask.rotate(a, resample=Image.Resampling.NEAREST, fillcolor=0), dtype=np.float32)
        score = float((np.diff(r.sum(axis=0)) ** 2).sum() + (np.diff(r.sum(axis=1)) ** 2).sum())
        if score > best:
            best, best_a = score, float(a)
    return best_a


def junk_sides(project: Project, page_id: str, finding: dict | None = None) -> list[str]:
    """Box sides with junk between the box edge and the text, from check_page findings."""
    res = finding or check_page(project, page_id)
    sides: list[str] = []
    for f in res.get("findings", []):
        kind = f["kind"]
        if kind in ("thin_left_column_isolated", "thin_right_column_isolated"):
            sides.append(kind.split("_")[1])
        elif kind == "thin_first_line_isolated":
            sides.append("top")
        elif kind == "thin_last_line_isolated":
            sides.append("bottom")
        elif kind == "box_exceeds_text":
            sides += list(f["excess_px"])
    return [s for s in ("left", "right", "top", "bottom") if s in sides]


def snap_point(project: Project, page_id: str, side: str) -> dict | None:
    """The point to 'double-click' so ScanTailor snaps one box edge to the text (--content-snap):
    on the outermost text on that side (text block edge, junk columns excluded), at the median
    line/column that reaches it. Page px; None when the page has no text block."""
    tb = text_block(project, page_id)
    if tb is None:
        return None
    ink, k = _ink_mask(project, page_id)
    L, T, R, B = tb["left"], tb["top"], tb["right"] - 1, tb["bottom"] - 1
    w = max(2, int(0.15 * tb["line_h"]))
    if side in ("left", "right"):
        c0, c1 = (L, L + w) if side == "left" else (R - w + 1, R + 1)
        rows = np.where(ink[T:B + 1, c0:c1].any(axis=1))[0] + T
        if not len(rows):
            return None
        x, y = (L if side == "left" else R), int(np.median(rows))
    elif side in ("top", "bottom"):
        r0, r1 = (T, T + w) if side == "top" else (B - w + 1, B + 1)
        cols = np.where(ink[r0:r1, L:R + 1].any(axis=0))[0] + L
        if not len(cols):
            return None
        x, y = int(np.median(cols)), (T if side == "top" else B)
    else:
        raise ValueError("side must be left|right|top|bottom")
    # aim half a pixel into the ink, in page px
    return {"x": round((x + 0.5) / k, 1), "y": round((y + 0.5) / k, 1)}
