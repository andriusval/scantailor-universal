"""Re-implementation of ScanTailor's ImageTransformation (src/core/ImageTransformation.cpp)
for the subset of steps needed to render previews:

    pre-scale (DPI equalisation) -> pre-rotate (orthogonal) -> pre-crop (page outline)
    -> post-rotate (deskew angle, bbox re-normalised to 0,0)

The resulting coordinate system is the one in which `select-content` stores
`content-rect` and `page-rect`, so boxes from the project XML can be drawn on top.

Qt uses row vectors and y-down coordinates: rotate(+deg) is clockwise on screen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Affine:
    """x' = a*x + c*y + e ; y' = b*x + d*y + f  (same layout as QTransform m11 m12 m21 m22 dx dy)."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def map(self, p: Point) -> Point:
        x, y = p
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def then(self, other: "Affine") -> "Affine":
        """Apply self first, then other."""
        o = other
        return Affine(
            a=self.a * o.a + self.b * o.c,
            b=self.a * o.b + self.b * o.d,
            c=self.c * o.a + self.d * o.c,
            d=self.c * o.b + self.d * o.d,
            e=self.e * o.a + self.f * o.c + o.e,
            f=self.e * o.b + self.f * o.d + o.f,
        )

    def inverted(self) -> "Affine":
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            raise ValueError("singular transform")
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        e = -(self.e * a + self.f * c)
        f = -(self.e * b + self.f * d)
        return Affine(a, b, c, d, e, f)


def translate(dx: float, dy: float) -> Affine:
    return Affine(e=dx, f=dy)


def rotate(deg: float) -> Affine:
    r = math.radians(deg)
    cs, sn = math.cos(r), math.sin(r)
    return Affine(a=cs, b=sn, c=-sn, d=cs)


def scale(sx: float, sy: float) -> Affine:
    return Affine(a=sx, d=sy)


def bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def orthogonal_rotation(degrees: int, w: float, h: float) -> Affine:
    """OrthogonalRotation::transform(QSizeF dimensions)."""
    degrees %= 360
    if degrees == 0:
        return Affine()
    if degrees == 90:
        t = translate(h, 0)
    elif degrees == 180:
        t = translate(w, h)
    elif degrees == 270:
        t = translate(0, w)
    else:
        raise ValueError(f"not an orthogonal rotation: {degrees}")
    # Qt applies the rotation first and the translation second.
    return rotate(degrees).then(t)


@dataclass
class PageTransform:
    xform: Affine            # original image px -> page (select-content) coords
    width: float             # size of the resulting page coordinate space
    height: float
    outline: list[Point]     # page outline in resulting coords


def page_transform(
    img_w: float,
    img_h: float,
    dpi_x: float,
    dpi_y: float,
    rotation_deg: int,
    page_outline_rotated: list[Point] | None,
    deskew_deg: float,
) -> PageTransform:
    # Step 1 is preScaleToEqualizeDpi, as in ScanTailor.
    m = min(dpi_x, dpi_y) or 1.0
    sx, sy = m / (dpi_x or m), m / (dpi_y or m)
    pre_scale = scale(sx, sy)
    sw, sh = img_w * sx, img_h * sy

    # Step 2 is the orthogonal rotation.
    pre_rot = orthogonal_rotation(rotation_deg, sw, sh)
    t12 = pre_scale.then(pre_rot)

    # Step 3 is the crop, and the outline already uses rotated coordinates.
    if page_outline_rotated:
        crop_area = list(page_outline_rotated)
    else:
        crop_area = [t12.map(p) for p in ((0, 0), (img_w, 0), (img_w, img_h), (0, img_h))]
    x0, y0, _, _ = bbox(crop_area)
    pre_crop = translate(-x0, -y0)

    # Step 4 is the deskew rotation around the crop centre.
    post_rot = Affine()
    if deskew_deg:
        bx0, by0, bx1, by1 = bbox(crop_area)
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        r = translate(-cx, -cy).then(rotate(deskew_deg)).then(translate(cx, cy))
        pre_poly = [pre_crop.map(p) for p in crop_area]
        px0, py0, _, _ = bbox(pre_poly)
        qx0, qy0, _, _ = bbox([r.map(p) for p in pre_poly])
        post_rot = r.then(translate(px0 - qx0, py0 - qy0))

    xform = t12.then(pre_crop).then(post_rot)
    outline = [pre_crop.then(post_rot).map(p) for p in crop_area]
    _, _, ox1, oy1 = bbox(outline)
    return PageTransform(xform=xform, width=ox1, height=oy1, outline=outline)
