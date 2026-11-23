"""Preview rendering: the page as ScanTailor's Select Content view shows it,
with the content box (blue) drawn on top, plus a few raw-image helpers."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from .project import Project

BLUE = (40, 110, 255)
RED = (230, 40, 40)
GREEN = (20, 170, 60)


def _load(path: Path) -> Image.Image:
    # ScanTailor ignores EXIF orientation, so do we
    return Image.open(path).convert("RGB")


def render_page(project: Project, page_id: str, max_px: int = 1200,
                extra_rects: list[tuple[dict, tuple[int, int, int]]] | None = None,
                overlay: bool = True, pad: tuple[int, int, int] = (128, 128, 128)) -> Image.Image:
    """overlay=False returns the bare page (for analysis: no box outlines or label bar)."""
    pg = project.page(page_id)
    pt = project.transform(pg.id)
    src = _load(pg.image.path)

    k = min(1.0, max_px / max(pt.width, pt.height))
    w, h = max(1, round(pt.width * k)), max(1, round(pt.height * k))
    # PIL wants the inverse mapping: output px -> source px
    from .geometry import scale
    fwd = pt.xform.then(scale(k, k))
    inv = fwd.inverted()
    page = src.transform((w, h), Image.Transform.AFFINE,
                         (inv.a, inv.c, inv.e, inv.b, inv.d, inv.f),
                         resample=Image.Resampling.BILINEAR, fillcolor=pad)
    # grey-out everything outside the page outline
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(x * k, y * k) for x, y in pt.outline], fill=255)
    page = Image.composite(page, Image.new("RGB", (w, h), pad), mask)

    if not overlay:
        return page
    d = ImageDraw.Draw(page, "RGBA")
    c = project.content(pg.id)
    rects = []
    if c.get("content_rect_px") and not c.get("empty"):
        rects.append((c["content_rect_px"], BLUE))
    rects += extra_rects or []
    for r, col in rects:
        x0, y0 = r["x"] * k, r["y"] * k
        x1, y1 = (r["x"] + r["width"]) * k, (r["y"] + r["height"]) * k
        d.rectangle([x0, y0, x1, y1], fill=col + (40,), outline=col + (255,), width=2)
    label = f"{pg.label}  page {pg.id}  content: {c.get('mode', '-')}"
    if c.get("empty"):
        label += "  (EMPTY)"
    d.rectangle([0, 0, w, 22], fill=(0, 0, 0, 160))
    d.text((6, 5), label, fill=(255, 255, 255))
    return page


def grid_view(project: Project, page_id: str, roi: tuple | None = None, max_w: int = 1000, max_h: int = 1400,
              zones: list | None = None) -> Image.Image:
    # Labels in page px let an agent read the coordinates of a snap point or a fill zone from the image.
    img = render_page(project, page_id, max_px=100000, overlay=False, pad=(255, 255, 255)).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    fwd = project.transform(page_id).xform
    for z in project.fill_zones(page_id):
        od.polygon([fwd.map(tuple(c)) for c in z["points"]], fill=(0, 0, 255, 60))
    img = Image.alpha_composite(img, over).convert("RGB")
    d = ImageDraw.Draw(img)
    c = project.content(page_id).get("content_rect_px")
    if c and c["width"] > 0:
        d.rectangle([c["x"], c["y"], c["x"] + c["width"], c["y"] + c["height"]], outline=(0, 170, 0), width=3)
    for x0, y0, x1, y1 in zones or []:
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
    x0, y0, x1, y1 = roi or (0, 0, img.width, img.height)
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(img.width, int(x1)), min(img.height, int(y1))
    crop = img.crop((x0, y0, x1, y1))
    k = min(1.0, max_w / crop.width, max_h / crop.height)
    crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))))
    d = ImageDraw.Draw(crop)
    # A fine grid on a close view and a coarse grid on a whole page keep the labels readable.
    step = 25 if k >= 0.6 else 50 if k >= 0.3 else 100
    for g in range((x0 // step + 1) * step, x1, step):
        x = (g - x0) * k
        d.line([(x, 0), (x, crop.height)], fill=(255, 0, 0) if g % 100 == 0 else (255, 205, 205))
        if g % 100 == 0:
            d.text((x + 2, 2), str(g), fill=(200, 0, 0))
    for g in range((y0 // step + 1) * step, y1, step):
        y = (g - y0) * k
        d.line([(0, y), (crop.width, y)], fill=(255, 0, 0) if g % 100 == 0 else (255, 205, 205))
        if g % 100 == 0:
            d.text((2, y + 2), str(g), fill=(200, 0, 0))
    return crop


def render_image(project: Project, image_id: str, max_px: int = 1200) -> Image.Image:
    im = project.images[str(image_id)]
    src = _load(im.path)
    src.thumbnail((max_px, max_px))
    rot = project.rotation(im.id)
    if rot:
        src = src.rotate(-rot, expand=True)
    return src


def contact_sheet(images: list[tuple[str, Image.Image]], cell: int = 360, cols: int = 4) -> Image.Image:
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 18)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(images):
        im = im.copy()
        im.thumbnail((cell - 8, cell - 8))
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        sheet.paste(im, (x + (cell - im.width) // 2, y + 18 + (cell - im.height) // 2))
        d.text((x + 4, y + 3), label, fill=(255, 255, 255))
    return sheet


def to_png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
