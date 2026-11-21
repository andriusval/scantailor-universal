"""Read/modify a ScanTailor Universal project (.ScanTailor XML).

Only the fields the agentic workflow needs are modelled; everything else in the
XML is preserved untouched, so the project stays openable in the GUI.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import PageTransform, page_transform

COLOR_MODES = {"bw": "bw", "black_and_white": "bw", "color": "colorOrGray",
               "grayscale": "colorOrGray", "color_grayscale": "colorOrGray",
               "colorOrGray": "colorOrGray", "mixed": "mixed"}
ALIGN_V = {"top", "vcenter", "bottom", "vauto", "voriginal"}
ALIGN_H = {"left", "hcenter", "right", "hauto", "horiginal"}
ALIGN_ALIASES = {"center": None, "centre": None}


def _f(el: ET.Element | None, attr: str, default: float = 0.0) -> float:
    if el is None:
        return default
    v = el.get(attr)
    return float(v) if v not in (None, "") else default


def _rect(el: ET.Element | None) -> dict | None:
    if el is None:
        return None
    return {k: _f(el, k) for k in ("x", "y", "width", "height")}


def _points(el: ET.Element | None) -> list[tuple[float, float]] | None:
    if el is None:
        return None
    pts = [(_f(p, "x"), _f(p, "y")) for p in el.findall("point")]
    return pts or None


@dataclass
class ImageInfo:
    id: str
    path: Path
    width: int
    height: int
    dpi_x: float
    dpi_y: float
    sub_pages: int
    index: int


@dataclass
class PageInfo:
    id: str
    image_id: str
    side: str            # single | left | right
    index: int           # position in book order
    image: ImageInfo = field(repr=False)

    @property
    def label(self) -> str:
        stem = self.image.path.stem
        return stem if self.side == "single" else f"{stem}_{'1L' if self.side == 'left' else '2R'}"


class Project:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).resolve()
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()
        self._index()

    # ------------------------------------------------------------------ index
    def _index(self) -> None:
        r = self.root
        dirs = {d.get("id"): self.local_path(d.get("path")) for d in r.findall("./directories/directory")}
        files = {f.get("id"): dirs[f.get("dirId")] / f.get("name") for f in r.findall("./files/file")}
        self.images: dict[str, ImageInfo] = {}
        for i, im in enumerate(r.findall("./images/image")):
            size, dpi = im.find("size"), im.find("dpi")
            self.images[im.get("id")] = ImageInfo(
                id=im.get("id"), path=files[im.get("fileId")],
                width=int(_f(size, "width")), height=int(_f(size, "height")),
                dpi_x=_f(dpi, "horizontal", 300), dpi_y=_f(dpi, "vertical", 300),
                sub_pages=int(im.get("subPages", "1")), index=i)
        self.pages: list[PageInfo] = []
        for i, p in enumerate(r.findall("./pages/page")):
            img = self.images[p.get("imageId")]
            self.pages.append(PageInfo(id=p.get("id"), image_id=img.id,
                                       side=p.get("subPage", "single"), index=i, image=img))
        self.pages_by_id = {p.id: p for p in self.pages}

    def local_path(self, p: str) -> Path:
        """A path as written in the project, usable on this machine: relative paths are taken from the
        project file's folder; a Windows drive path (C:\\x or C:/x) maps to /mnt/c/x under WSL/Linux."""
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", p or "")
        if m and os.name != "nt":
            return Path("/mnt", m.group(1).lower(), *re.split(r"[\\/]+", m.group(2)))
        q = Path(p or ".")
        return q if q.is_absolute() else (self.path.parent / q).resolve()

    @property
    def output_dir(self) -> Path:
        return self.local_path(self.root.get("outputDirectory", ""))

    def filter_el(self, name: str) -> ET.Element | None:
        return self.root.find(f"./filters/{name}")

    def page(self, page_id: str | int) -> PageInfo:
        pid = str(page_id)
        if pid in self.pages_by_id:
            return self.pages_by_id[pid]
        for p in self.pages:  # also accept labels such as "0100_2R"
            if p.label == pid:
                return p
        raise KeyError(f"unknown page: {page_id}")

    def _page_params(self, filter_name: str, page_id: str) -> ET.Element | None:
        f = self.filter_el(filter_name)
        if f is None:
            return None
        return f.find(f"./page[@id='{page_id}']/params")

    # ------------------------------------------------------------------ read
    def rotation(self, image_id: str) -> int:
        f = self.filter_el("fix-orientation")
        el = f.find(f"./image[@id='{image_id}']/rotation") if f is not None else None
        return int(_f(el, "degrees", 0))

    def deskew_angle(self, page_id: str) -> float:
        p = self._page_params("deskew", page_id)
        return _f(p, "angle", 0.0)

    def page_outline(self, page_id: str) -> list[tuple[float, float]] | None:
        p = self._page_params("deskew", page_id)
        if p is not None:
            return _points(p.find("./dependencies/page-outline"))
        sc = self._page_params("select-content", page_id)
        if sc is not None:
            return _points(sc.find("./dependencies/rotated-page-outline"))
        return None

    def transform(self, page_id: str) -> PageTransform:
        pg = self.page(page_id)
        im = pg.image
        return page_transform(im.width, im.height, im.dpi_x, im.dpi_y,
                              self.rotation(im.id), self.page_outline(pg.id),
                              self.deskew_angle(pg.id))

    def content(self, page_id: str) -> dict:
        p = self._page_params("select-content", page_id)
        if p is None:
            return {"processed": False}
        cr = _rect(p.find("content-rect"))
        mm = p.find("content-size-mm")
        empty = not cr or cr["width"] <= 0 or cr["height"] <= 0
        return {
            "processed": True,
            "mode": p.get("mode"),
            "content_rect_px": cr,
            "page_rect_px": _rect(p.find("page-rect")),
            "content_mm": None if mm is None else {"width": round(_f(mm, "width"), 1),
                                                   "height": round(_f(mm, "height"), 1)},
            "empty": empty,
        }

    def layout(self, page_id: str) -> dict:
        p = self._page_params("page-layout", page_id)
        if p is None:
            return {"processed": False}
        m = p.find("hardMarginsMM")
        a = p.find("alignment")
        return {
            "processed": True,
            "margins_mm": {k: _f(m, k) for k in ("left", "top", "right", "bottom")},
            "align_v": a.get("vert") if a is not None else None,
            "align_h": a.get("hor") if a is not None else None,
            "match_size": not (a is not None and a.get("null") == "1"),
        }

    def output(self, page_id: str) -> dict:
        p = self._page_params("output", page_id)
        if p is None:
            return {"processed": False}
        dpi = p.find("dpi")
        cp = p.find("color-params")
        return {
            "processed": True,
            "color_mode": cp.get("colorMode") if cp is not None else None,
            "dpi": int(_f(dpi, "horizontal", 600)),
            "despeckle": p.get("despeckleLevel"),
            "white_margins": _cg(cp, "whiteMargins"),
            "equalize_illumination": _cg(cp, "normalizeIllumination"),
        }

    # ------------------------------------------------------------------ write
    def set_content(self, page_id: str, rect: dict | None) -> None:
        """rect in page px (select-content coords); None = back to auto detection."""
        p = self._page_params("select-content", page_id)
        if p is None:
            raise RuntimeError(f"page {page_id}: select-content not run yet (run stages 1-4 first)")
        cr = p.find("content-rect")
        if rect is None:
            p.set("mode", "auto")
            p.set("content-detect", "true")
            return
        p.set("mode", "manual")
        p.set("content-detect", "true")
        if cr is None:
            cr = ET.SubElement(p, "content-rect")
        for k in ("x", "y", "width", "height"):
            cr.set(k, repr(float(rect[k])))
        mm = p.find("content-size-mm")
        if mm is not None:  # stale; ScanTailor recomputes it
            p.remove(mm)

    def set_layout(self, page_id: str, margins_mm: dict | None = None, align_v: str | None = None,
                   align_h: str | None = None, match_size: bool | None = None) -> None:
        p = self._page_params("page-layout", page_id)
        if p is None:
            raise RuntimeError(f"page {page_id}: page-layout not run yet (run stages 1-5 first)")
        if margins_mm:
            m = p.find("hardMarginsMM")
            for k, v in margins_mm.items():
                if k not in ("left", "top", "right", "bottom"):
                    raise ValueError(f"bad margin side: {k}")
                m.set(k, repr(float(v)))
            m.set("autoMargins", "false")
        a = p.find("alignment")
        if align_v is not None:
            if align_v == "center":
                align_v = "vcenter"
            if align_v not in ALIGN_V:
                raise ValueError(f"align_v must be one of {sorted(ALIGN_V)}")
            a.set("vert", align_v)
        if align_h is not None:
            if align_h == "center":
                align_h = "hcenter"
            if align_h not in ALIGN_H:
                raise ValueError(f"align_h must be one of {sorted(ALIGN_H)}")
            a.set("hor", align_h)
        if match_size is not None:
            a.set("null", "0" if match_size else "1")

    def set_output(self, page_id: str, color_mode: str | None = None, dpi: int | None = None,
                   despeckle: str | None = None, white_margins: bool | None = None,
                   equalize_illumination: bool | None = None) -> None:
        p = self._page_params("output", page_id)
        if p is None:
            raise RuntimeError(f"page {page_id}: output params missing (run stages 1-5 first)")
        if color_mode is not None:
            if color_mode not in COLOR_MODES:
                raise ValueError(f"color_mode must be one of {sorted(COLOR_MODES)}")
            p.find("color-params").set("colorMode", COLOR_MODES[color_mode])
        if dpi is not None:
            d = p.find("dpi")
            d.set("horizontal", str(int(dpi)))
            d.set("vertical", str(int(dpi)))
        if despeckle is not None:
            if despeckle not in ("off", "cautious", "normal", "aggressive"):
                raise ValueError("despeckle must be off|cautious|normal|aggressive")
            p.set("despeckleLevel", despeckle)
        # Color/Grayscale (and Mixed) options: "White margins", "Equalize illumination"
        for attr, val in (("whiteMargins", white_margins), ("normalizeIllumination", equalize_illumination)):
            if val is not None:
                cp = p.find("color-params")
                cg = cp.find("color-or-grayscale")
                if cg is None:
                    cg = ET.SubElement(cp, "color-or-grayscale")
                cg.set(attr, "1" if val else "0")

    def _filters(self) -> ET.Element:
        f = self.root.find("./filters")
        if f is None:
            f = ET.SubElement(self.root, "filters")
        return f

    def _filter_or_create(self, name: str) -> ET.Element:
        el = self.filter_el(name)
        if el is None:
            el = ET.SubElement(self._filters(), name)
        return el

    def set_deskew(self, page_id: str, angle: float) -> None:
        """Manual deskew angle (degrees, ScanTailor's sign). Stages 3+ must be re-run afterwards."""
        p = self._page_params("deskew", page_id)
        if p is None:
            raise RuntimeError(f"page {page_id}: deskew not run yet (run stages 1-3 first)")
        p.set("mode", "manual")
        p.set("angle", repr(float(angle)))

    def set_rotation(self, image_id: str, degrees: int) -> None:
        """Per-image orientation fix (Fix Orientation filter): 0, 90, 180, 270 clockwise."""
        if degrees % 90:
            raise ValueError("rotation must be a multiple of 90")
        f = self._filter_or_create("fix-orientation")
        img = f.find(f"./image[@id='{image_id}']")
        if img is None:
            img = ET.SubElement(f, "image", {"id": image_id})
        rot = img.find("rotation")
        if rot is None:
            rot = ET.SubElement(img, "rotation")
        rot.set("degrees", str(degrees % 360))

    def set_split_layout(self, image_id: str, layout: str) -> None:
        """Per-image page layout: single-uncut | single-cut | two-pages | auto.
        Drops stored split params so ScanTailor re-detects the split with this layout."""
        if layout not in ("single-uncut", "single-cut", "two-pages", "auto"):
            raise ValueError("layout must be single-uncut|single-cut|two-pages|auto")
        f = self._filter_or_create("page-split")
        img = f.find(f"./image[@id='{image_id}']")
        if img is None:
            img = ET.SubElement(f, "image", {"id": image_id})
        for p in img.findall("params"):
            img.remove(p)
        if layout == "auto":
            img.attrib.pop("layoutType", None)
        else:
            img.set("layoutType", layout)

    def save(self) -> None:
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        shutil.copy2(self.path, bak)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        os.close(fd)
        self.tree.write(tmp, encoding="utf-8", xml_declaration=False)
        os.replace(tmp, self.path)


def _cg(cp: ET.Element | None, attr: str) -> bool | None:
    cg = cp.find("color-or-grayscale") if cp is not None else None
    return None if cg is None else cg.get(attr) == "1"
