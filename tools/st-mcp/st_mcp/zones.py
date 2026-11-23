# Fill zone helpers: fit a rough rectangle to the ink of a mark, and find printed text that a zone cuts.
from __future__ import annotations

import numpy as np

from .preview import render_page
from .project import Project

INK = 200
EDGE = 25
MIN_ROWS = 8


def page_gray(project: Project, page_id: str) -> np.ndarray:
    return np.asarray(render_page(project, page_id, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("L"))


def fit_to_ink(gray: np.ndarray, rough: tuple[float, float, float, float], grow_px: int = 150, pad: int = 12,
               ink: int = INK) -> tuple[int, int, int, int] | None:
    # A faint stamp edge often lies outside the rough box, so the box grows while ink touches it.
    h, w = gray.shape
    x0, y0, x1, y1 = (max(0, int(rough[0])), max(0, int(rough[1])), min(w, int(rough[2])), min(h, int(rough[3])))
    if x1 <= x0 or y1 <= y0:
        return None
    m = gray < ink
    ys, xs = np.nonzero(m[y0:y1, x0:x1])
    if len(xs) < 5:
        return None
    z = [x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()), y0 + int(ys.max())]
    lim = [z[0] - grow_px, z[1] - grow_px, z[2] + grow_px, z[3] + grow_px]
    for _ in range(grow_px // 10 + 1):
        moved = False
        if z[0] > max(0, lim[0]) and m[z[1]:z[3], max(0, z[0] - 15):z[0]].sum() > 3:
            z[0] -= 10
            moved = True
        if z[2] < min(w - 1, lim[2]) and m[z[1]:z[3], z[2] + 1:z[2] + 16].sum() > 3:
            z[2] += 10
            moved = True
        if z[1] > max(0, lim[1]) and m[max(0, z[1] - 15):z[1], z[0]:z[2]].sum() > 3:
            z[1] -= 10
            moved = True
        if z[3] < min(h - 1, lim[3]) and m[z[3] + 1:z[3] + 16, z[0]:z[2]].sum() > 3:
            z[3] += 10
            moved = True
        if not moved:
            break
    return max(0, z[0] - pad), max(0, z[1] - pad), min(w, z[2] + pad), min(h, z[3] + pad)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def edge_crossings(gray: np.ndarray, zone: tuple[float, float, float, float], box: dict | None = None,
                   ink: int = 140, min_rows: int = MIN_ROWS) -> list[dict]:
    # Ink near both sides of a zone edge in the same rows means a line of text or a stroke that the zone cuts.
    h, w = gray.shape
    x0, y0, x1, y1 = (max(0, int(zone[0])), max(0, int(zone[1])), min(w, int(zone[2])), min(h, int(zone[3])))
    m = gray < ink
    if box:
        # Ink outside the content box does not reach the output, so it cannot make a crossing.
        keep = np.zeros_like(m)
        bx0, by0 = max(0, int(box["x"])), max(0, int(box["y"]))
        keep[by0:int(box["y"] + box["height"]), bx0:int(box["x"] + box["width"])] = True
        m &= keep
    found = []
    for side, x in (("left", x0), ("right", x1)):
        if x - EDGE < 0 or x + EDGE > w:
            continue
        inside = m[y0:y1, x:x + EDGE] if side == "left" else m[y0:y1, x - EDGE:x]
        outside = m[y0:y1, x - EDGE:x] if side == "left" else m[y0:y1, x:x + EDGE]
        both = inside.any(axis=1) & outside.any(axis=1)
        for a, b in _runs(both):
            if b - a + 1 >= min_rows:
                found.append({"edge": side, "x": x, "y0": y0 + a, "y1": y0 + b})
    for side, y in (("top", y0), ("bottom", y1)):
        if y - EDGE < 0 or y + EDGE > h:
            continue
        inside = m[y:y + EDGE, x0:x1] if side == "top" else m[y - EDGE:y, x0:x1]
        outside = m[y - EDGE:y, x0:x1] if side == "top" else m[y:y + EDGE, x0:x1]
        both = inside.any(axis=0) & outside.any(axis=0)
        for a, b in _runs(both):
            if b - a + 1 >= min_rows:
                found.append({"edge": side, "y": y, "x0": x0 + a, "x1": x0 + b})
    return found


def zone_rects(project: Project, page_id: str) -> list[tuple[float, float, float, float]]:
    fwd = project.transform(page_id).xform
    out = []
    for z in project.fill_zones(page_id):
        if len(z["points"]) < 3:
            continue
        pts = [fwd.map(tuple(c)) for c in z["points"]]
        xs, ys = [a for a, _ in pts], [b for _, b in pts]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def stray_edges(gray: np.ndarray, box: dict, gap: int = 25, max_mass: float = 0.02, max_width: int = 120) -> list[dict]:
    # A small ink group apart from the text at a box side (a speck, a pencil mark) makes the box wider than the text.
    x0, y0 = int(box["x"]), int(box["y"])
    x1, y1 = int(box["x"] + box["width"]), int(box["y"] + box["height"])
    ink = gray[y0:y1, x0:x1] < 120
    prof = ink.sum(axis=0)
    idx = np.nonzero(prof > 2)[0]
    if len(idx) == 0:
        return []
    groups, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            groups.append((start, prev))
            start = i
        prev = i
    groups.append((start, prev))
    if len(groups) < 2:
        return []
    total = float(prof.sum())
    out = []
    for side, g, nxt in (("left", groups[0], groups[1]), ("right", groups[-1], groups[-2])):
        mass = float(prof[g[0]:g[1] + 1].sum()) / total
        if mass >= max_mass or g[1] - g[0] >= max_width:
            continue
        col = nxt[0] + 2 if side == "left" else nxt[1] - 2
        rows = np.nonzero(ink[:, max(0, col - 3):col + 4].any(axis=1))[0]
        if len(rows) == 0:
            continue
        out.append({"side": side, "stray_x": [x0 + int(g[0]), x0 + int(g[1])],
                    "point": {"x": x0 + int(col), "y": y0 + int(rows[len(rows) // 2]), "side": side},
                    "removes_px": int((g[1] if side == "right" else nxt[0]) - (nxt[1] if side == "right" else g[0]))})
    return out
