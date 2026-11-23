# Find library stamps, stickers and handwritten library numbers on a page image; stamp_zones.py turns them into fill zones.
from __future__ import annotations

import re

import numpy as np
from PIL import Image
from scipy import ndimage

PAD_MM = 2.0


def mm_px(im: Image.Image) -> float:
    return float(im.info.get("dpi", (600, 600))[0]) / 25.4


def ink_blocks(ink: np.ndarray, gap_px: float) -> list[tuple[int, int, int, int]]:
    rows = ink.sum(axis=1) > 2
    blocks, start, gap, end = [], None, 0, 0
    for i, on in enumerate(rows):
        if on:
            if start is None:
                start = i
            gap, end = 0, i
        elif start is not None:
            gap += 1
            if gap > gap_px:
                blocks.append((start, end))
                start = None
    if start is not None:
        blocks.append((start, end))
    out = []
    for s, e in blocks:
        cols = np.nonzero(ink[s:e + 1].any(axis=0))[0]
        out.append((s, e + 1, int(cols[0]), int(cols[-1]) + 1))
    return out


INVENTORY = re.compile(r"[A-Z0-9]{1,3}\s*[-–]\s*\d{1,3}\s*/\s*\d{3,5}")
BOOK_WORDS = re.compile(r"ma[zž]vyd|nacional|isbn|udk|©|leidykl|spausdin|tira[zž]", re.I)


def find_page4_stamp_block(im: Image.Image) -> tuple[int, int, int, int] | None:
    mm = mm_px(im)
    ink = np.asarray(im.convert("L")) < 128
    h = ink.shape[0]
    found = []
    # The stamp is a separate block in the middle of the copyright page, between the top text and the ISBN line.
    for y0, y1, x0, x1 in ink_blocks(ink, 6 * mm)[1:-1]:
        bw, bh = (x1 - x0) / mm, (y1 - y0) / mm
        if y0 > 0.35 * h and y1 < 0.92 * h and 15 < bw < 80 and 5 < bh < 40:
            found.append((x0, y0, x1, y1))
    return found[0] if len(found) == 1 else None


def find_page4_stamp(im: Image.Image) -> list[tuple[int, int, int, int]] | None:
    # The block test found most stamps in the first run, so it goes first; OCR helps when blocks touch the text.
    block = find_page4_stamp_block(im)
    if block:
        return [block]
    return find_page4_stamp_ocr(im)


def find_page4_stamp_ocr(im: Image.Image) -> list[tuple[int, int, int, int]] | None:
    mm = mm_px(im)
    ink = np.asarray(im.convert("L")) < 128
    h = ink.shape[0]
    blocks = ink_blocks(ink, 3 * mm)
    texts = []
    for y0, y1, x0, x1 in blocks:
        pad = int(2 * mm)
        texts.append(ocr_text(im.crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad))))
    marks = []
    # The stamp and the handwritten number are in the middle part of the copyright page, never the ISBN line at the end.
    for i, ((y0, y1, x0, x1), t) in enumerate(zip(blocks, texts)):
        if i == len(blocks) - 1 or y0 < 0.30 * h or BOOK_WORDS.search(t):
            continue
        if (x1 - x0) > 90 * mm or (y1 - y0) > 45 * mm:
            continue
        if STAMP_WORDS.search(t) or INVENTORY.search(t.replace(" ", "")):
            marks.append(i)
    if not marks:
        return None
    # A handwritten number that OCR cannot read sits right above or below the stamp, so close small blocks join it.
    keep = set(marks)
    for i in marks:
        for j in (i - 1, i + 1):
            if 0 < j < len(blocks) - 1 and j not in keep and not BOOK_WORDS.search(texts[j]):
                gap = blocks[i][0] - blocks[j][1] if j < i else blocks[j][0] - blocks[i][1]
                bw, bh = blocks[j][3] - blocks[j][2], blocks[j][1] - blocks[j][0]
                if gap < 10 * mm and bw < 70 * mm and bh < 25 * mm:
                    keep.add(j)
    return [(blocks[i][2], blocks[i][0], blocks[i][3], blocks[i][1]) for i in sorted(keep)]


def find_back_sticker(im: Image.Image) -> list[tuple[int, int, int, int]] | None:
    from series.zbar import decode
    mm = mm_px(im)
    g = np.asarray(im.convert("L"))
    h, w = g.shape
    # Library stickers carry a CODE-128 barcode, and the book's own ISBN barcode is EAN-13.
    labels = [d for d in decode(g) if d["type"] not in ("ISBN-13", "EAN-13", "ISBN-10", "UPC-A")]
    boxes = []
    for d in labels:
        # The sticker has a logo left of the bars, the library name above them and a number under them.
        boxes.append((max(0, int(d["x0"] - 18 * mm)), max(0, int(d["y0"] - 10 * mm)),
                      min(w, int(d["x1"] + 4 * mm)), min(h, int(d["y1"] + 7 * mm))))
    if boxes:
        return boxes
    # The search by ink painted over cover pictures, so the fallback looks only for barcode stripes in the corners.
    isbn = [d for d in decode(g) if d["type"] in ("ISBN-13", "EAN-13")]
    h, w = g.shape
    gx = np.abs(np.diff(g.astype(np.int16), axis=1)) > 60
    dens = ndimage.uniform_filter(gx.astype(np.float32), size=(int(4 * mm), int(4 * mm)))
    m = ndimage.binary_closing(dens > 0.25, structure=np.ones((int(1 * mm), int(2 * mm))))
    lab, n = ndimage.label(m)
    out = []
    for sl in ndimage.find_objects(lab):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        bw, bh = (x1 - x0) / mm, (y1 - y0) / mm
        corner = x0 > 0.5 * w and (y1 < 0.35 * h or y0 > 0.7 * h)
        if not corner or not (12 < bw < 45 and 3 < bh < 15):
            continue
        if any(x0 < d["x1"] and d["x0"] < x1 and y0 < d["y1"] + 10 * mm and d["y0"] - 10 * mm < y1 for d in isbn):
            continue
        out.append((max(0, int(x0 - 18 * mm)), max(0, int(y0 - 10 * mm)), min(w, int(x1 + 4 * mm)), min(h, int(y1 + 7 * mm))))
    return out or None


STAMP_WORDS = re.compile(r"bibliot|visagin|mokykl|vie[sš]oji|vie[sš]os|\bcbs\b|filial|ignalin|utenos|zarasu|zarasų", re.I)


def ocr_text(im: Image.Image) -> str:
    import subprocess
    import io
    buf = io.BytesIO()
    im.convert("L").save(buf, format="PNG")
    r = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"], input=buf.getvalue(), capture_output=True)
    return r.stdout.decode(errors="replace")


def find_title_stamp(im: Image.Image) -> list[tuple[int, int, int, int]] | None:
    mm = mm_px(im)
    ink = np.asarray(im.convert("L")) < 128
    blocks = ink_blocks(ink, 5 * mm)
    found = []
    # A library stamp is a separate block with the library name in it; OCR tells it from the book title.
    for y0, y1, x0, x1 in blocks:
        if (y1 - y0) > 45 * mm or (x1 - x0) > 90 * mm:
            continue
        pad = int(2 * mm)
        crop = im.crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad))
        if STAMP_WORDS.search(ocr_text(crop)):
            found.append((x0, y0, x1, y1))
    return found


def margin_handwriting(ink: np.ndarray, mm: float) -> list[tuple[int, int, int, int]]:
    # Library numbers are written by hand along the margin, outside the text column, as a tall narrow group.
    h, w = ink.shape
    rows = np.nonzero(ink.sum(axis=1) > 2)[0]
    if not len(rows):
        return []
    lefts, rights = [], []
    for y in rows[:: max(1, len(rows) // 400)]:
        xs = np.nonzero(ink[y])[0]
        lefts.append(xs[0])
        rights.append(xs[-1])
    # Poem lines end at many places, so only the far right ends mark the text column edge.
    col_l, col_r = np.percentile(lefts, 30), np.percentile(rights, 98)
    out = []
    for x0, x1 in ((0, int(col_l - 1.5 * mm)), (int(col_r + 2 * mm), w)):
        if x1 - x0 < 3 * mm:
            continue
        sub = ink[:, x0:x1]
        # Handwriting runs along the page, so the group joins up and down, not to the side where printed numbers are.
        band = ndimage.binary_dilation(sub, structure=np.ones((int(4 * mm), max(1, int(0.5 * mm)))))
        lab, n = ndimage.label(band)
        for k, sl in enumerate(ndimage.find_objects(lab), 1):
            bh, bw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            if not (bh > 12 * mm and bh > 2.5 * bw):
                continue
            gx0, gx1 = x0 + sl[1].start, x0 + sl[1].stop
            gy0, gy1 = max(0, sl[0].start - int(8 * mm)), min(h, sl[0].stop + int(8 * mm))
            # The text edge comes from rows without handwriting, so it is exact.
            other = [lx for y, lx in zip(rows[:: max(1, len(rows) // 400)], lefts) if not (gy0 <= y < gy1)]
            edge = (np.percentile(other, 5) if other else col_l) if x0 == 0 else w
            right_lim = int(edge - 0.3 * mm) if x0 == 0 else w
            lo = max(0, gx0 - int(3 * mm))
            # On the left the search goes up to the text edge; printed line numbers there stay by their size.
            hi = right_lim if x0 == 0 else min(w, gx1 + int(3 * mm))
            if hi <= lo:
                continue
            part = ink[gy0:gy1, lo:hi]
            plab, _ = ndimage.label(part, structure=np.ones((3, 3)))
            for psl in ndimage.find_objects(plab):
                if psl is None:
                    continue
                ph, pw = psl[0].stop - psl[0].start, psl[1].stop - psl[1].start
                inside = gx0 <= lo + psl[1].start < gx1
                # Printed line numbers next to the group have digits below 2.5 mm; inside the group only specks stay.
                if (not inside and ph < 2.5 * mm and pw < 2.5 * mm) or (ph < 0.5 * mm and pw < 0.5 * mm):
                    continue
                # Box tuples with a fifth value 0 are filled without a pad, so the text next to them stays.
                out.append((lo + psl[1].start, gy0 + psl[0].start, lo + psl[1].stop, gy0 + psl[0].stop, 0))
    return out
