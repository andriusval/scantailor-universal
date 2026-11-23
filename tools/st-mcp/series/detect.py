# Image detectors for book covers, in original image pixels, without an LLM.
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

from st_mcp.project import Project


def _gray(path, factor: int) -> np.ndarray:
    im = Image.open(path)
    im.draft("L", (im.width // factor, im.height // factor))
    im = im.convert("L")
    # The draft size can differ from the request, so the caller gets the real scale.
    return np.asarray(im, dtype=np.float32), im.width


def cover_mask(path, threshold: float | None = None) -> tuple[np.ndarray, float]:
    g, w = _gray(path, 4)
    scale = Image.open(path).width / w
    if threshold is None:
        # The lid fills the scan border, so its brightness there sets the limit; dark cover pictures are just above it.
        b = max(2, g.shape[0] // 30)
        border = np.concatenate([g[:b].ravel(), g[-b:].ravel(), g[:, :b].ravel(), g[:, -b:].ravel()])
        threshold = min(45.0, max(20.0, float(np.percentile(border, 99)) + 8))
    m = g > threshold
    m = ndimage.binary_opening(m, iterations=6)
    lab, n = ndimage.label(m)
    if n == 0:
        return None, scale
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    k = int(np.argmax(sizes)) + 1
    m = ndimage.binary_fill_holes(lab == k)
    return m, scale


def cover_rect_page(p: Project, page_id: str, threshold: float | None = None) -> dict | None:
    pg = p.page(page_id)
    m, scale = cover_mask(pg.image.path, threshold)
    if m is None or m.mean() < 0.15:
        return None
    ys, xs = np.nonzero(m)
    step = max(1, len(xs) // 200000)
    xs, ys = (xs[::step] + 0.5) * scale, (ys[::step] + 0.5) * scale
    t = p.transform(page_id).xform
    pts = np.array([t.map((float(x), float(y))) for x, y in zip(xs, ys)])
    # Percentiles ignore a few bright specks on the lid next to the cover.
    x0, x1 = np.percentile(pts[:, 0], [0.05, 99.95])
    y0, y1 = np.percentile(pts[:, 1], [0.05, 99.95])
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def isbn_symbol(path) -> dict | None:
    from series.zbar import decode
    g = np.asarray(Image.open(path).convert("L"))
    syms = [d for d in decode(g) if d["type"] in ("ISBN-13", "EAN-13")]
    return syms[0] if syms else None


def barcode_rect_page(p: Project, page_id: str, cover: dict) -> dict | None:
    # The decoder tells the ISBN barcode from a library sticker barcode, which a shape test cannot do.
    pg = p.page(page_id)
    sym = isbn_symbol(pg.image.path)
    if sym is None:
        return None
    t = p.transform(page_id).xform
    cx, cy = t.map(((sym["x0"] + sym["x1"]) / 2, (sym["y0"] + sym["y1"]) / 2))
    g, w = _gray(pg.image.path, 2)
    scale = pg.image.width / w
    gx = np.abs(np.diff(g, axis=1))
    dens = ndimage.uniform_filter((gx > 40).astype(np.float32), size=(25, 25))
    m = ndimage.binary_closing(dens > 0.18, structure=np.ones((9, 15)))
    lab, n = ndimage.label(m)
    for sl in ndimage.find_objects(lab):
        corners = [(sl[1].start * scale, sl[0].start * scale), (sl[1].stop * scale, sl[0].stop * scale),
                   (sl[1].start * scale, sl[0].stop * scale), (sl[1].stop * scale, sl[0].start * scale)]
        pc = [t.map(c) for c in corners]
        xs, ys = [c[0] for c in pc], [c[1] for c in pc]
        if min(xs) <= cx <= max(xs) and min(ys) <= cy <= max(ys):
            return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}
    # Without a dense area around it, the box is the decoded bars with the digit line under them.
    pc = [t.map((sym["x0"], sym["y0"])), t.map((sym["x1"], sym["y1"]))]
    x0, x1 = sorted(c[0] for c in pc)
    y0, y1 = sorted(c[1] for c in pc)
    return {"x": x0 - 10, "y": y0 - 40, "width": x1 - x0 + 20, "height": y1 - y0 + 80}


def barcode_rect_by_density(p: Project, page_id: str, cover: dict) -> dict | None:
    # When the decoder cannot read the bars, the tallest dense bar area in the lower half is the ISBN barcode.
    pg = p.page(page_id)
    g, w = _gray(pg.image.path, 2)
    scale = pg.image.width / w
    dens = ndimage.uniform_filter((np.abs(np.diff(g, axis=1)) > 40).astype(np.float32), size=(25, 25))
    m = ndimage.binary_closing(dens > 0.18, structure=np.ones((9, 15)))
    lab, n = ndimage.label(m)
    t = p.transform(page_id).xform
    best = None
    for sl in ndimage.find_objects(lab):
        if (sl[0].stop - sl[0].start) * scale < 80 or (sl[1].stop - sl[1].start) * scale < 150:
            continue
        pc = [t.map((x * scale, y * scale)) for x in (sl[1].start, sl[1].stop) for y in (sl[0].start, sl[0].stop)]
        xs, ys = [c[0] for c in pc], [c[1] for c in pc]
        r = {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}
        if r["y"] < cover["y"] + cover["height"] * 0.5:
            continue
        if best is None or r["height"] > best["height"]:
            best = r
    return best


def all_ink_rect(p: Project, page_id: str, dpi: float, edge_mm: float = 8.0) -> dict | None:
    # A box around all printed parts of a title or contents page, without edge shadows and specks.
    from st_mcp.preview import render_page
    mm = dpi / 25.4
    img = render_page(p, page_id, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("L")
    a = np.asarray(img, dtype=np.uint8)[::2, ::2]
    m2 = mm / 2
    ink = a < 140
    lab, n = ndimage.label(ink, structure=np.ones((3, 3)))
    if n == 0:
        return None
    h, w = ink.shape
    e = edge_mm * m2
    boxes = []
    sizes = ndimage.sum(ink, lab, range(1, n + 1))
    for k, sl in enumerate(ndimage.find_objects(lab)):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        bw, bh = x1 - x0, y1 - y0
        # Specks are tiny, and shadows touch the page edge or are long thin strips.
        if sizes[k] < 6 or x0 < e or y0 < e or x1 > w - e or y1 > h - e:
            continue
        if (bh > 40 * m2 and bw < 1.5 * m2) or (bw > 40 * m2 and bh < 1.0 * m2 and bw > 0.8 * w) \
                or (bh > 0.4 * h and bw < 15 * m2):
            continue
        boxes.append((x0, y0, x1, y1))
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes) * 2 - 3
    y0 = min(b[1] for b in boxes) * 2 - 3
    x1 = max(b[2] for b in boxes) * 2 + 3
    y1 = max(b[3] for b in boxes) * 2 + 3
    return {"x": float(x0), "y": float(y0), "width": float(x1 - x0), "height": float(y1 - y0)}
