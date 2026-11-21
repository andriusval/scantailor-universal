# Corrections log: what the person changed in the project that the agent made, one JSONL record per change.
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path

from .project import Project

CATEGORIES = ["page_removed", "page_added", "split", "rotation", "deskew_picture", "deskew_text", "page_box",
              "content_empty", "content_box_trim", "content_box_extend", "content_box_numbers", "cover",
              "margins", "alignment", "match_size", "output_mode", "dpi", "picture_zones", "other"]

# The workflow step that each category points to, so that the statistics show which rule to improve.
CATEGORY_RULE = {
    "page_removed": "step 11: removal candidates (blank leaves, half-titles); the person removed the page",
    "page_added": "step 1: project_create missed a scan",
    "split": "step 2: layout of the scans (images_set_geometry)",
    "rotation": "step 2: orientation of the scans",
    "deskew_picture": "step 5: picture pages, pages_straighten_pictures",
    "deskew_text": "ScanTailor deskew of text pages (no st-mcp rule yet)",
    "page_box": "page box detection (no st-mcp rule yet)",
    "content_empty": "step 4: mode=empty for blank leaves",
    "content_box_trim": "step 4: junk in the box (pages_snap_content, mode=tighten)",
    "content_box_extend": "step 4: text cut off at the sides (ink_left/right_of_box)",
    "content_box_numbers": "step 4: missed page numbers or headers (mode=include_numbers)",
    "cover": "step 6: covers and endpapers",
    "margins": "steps 7-8: margins and pages_fit_width",
    "alignment": "step 9: pages_align_auto",
    "match_size": "steps 6-8: match size with other pages",
    "output_mode": "step 5: picture pages, output mode",
    "dpi": "steps 5-6: output DPI",
    "picture_zones": "step 5: picture zones of mixed pages",
    "other": "no rule",
}

CONTENT_PX_TOL = 3.0


def log_path(log: str | None = None) -> Path:
    if log:
        return Path(log).expanduser()
    env = os.environ.get("ST_CORRECTIONS_LOG")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "st-mcp" / "corrections.jsonl"


# One name per book on WSL and Windows: /mnt/c/x and C:\x both become C:/x.
def book_id(folder: str | os.PathLike) -> str:
    s = str(folder).replace("\\", "/").rstrip("/")
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", s)
    if m:
        s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
    m = re.match(r"^([a-zA-Z]):(/.*)?$", s)
    if m:
        s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
    return s


def _split_state(p: Project, image_id: str) -> dict | None:
    f = p.filter_el("page-split")
    img = f.find(f"./image[@id='{image_id}']") if f is not None else None
    if img is None:
        return None
    params = img.find("params")
    cuts = []
    if params is not None:
        for c in params.findall("./pages/cutter1") + params.findall("./pages/cutter2"):
            pts = [c.find("p1"), c.find("p2")]
            cuts += [round(float(q.get("x", 0))) for q in pts if q is not None]
    return {"layout": img.get("layoutType") or (params.find("pages").get("type")
                                                if params is not None and params.find("pages") is not None
                                                else None),
            "mode": params.get("mode") if params is not None else None, "cut": cuts}


def _same_split(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a == b
    if (a["layout"], a["mode"]) != (b["layout"], b["mode"]) or len(a["cut"]) != len(b["cut"]):
        return False
    return all(abs(x - y) <= 5 for x, y in zip(a["cut"], b["cut"]))


def _page_box(p: Project, page_id: str) -> dict | None:
    params = p._page_params("select-content", page_id)
    if params is None:
        return None
    r = params.find("page-rect")
    rect = None if r is None else [round(float(r.get(k, 0))) for k in ("x", "y", "width", "height")]
    return {"page_detect": params.get("page-detect") == "true", "rect": rect}


def _picture_zones(p: Project, page_id: str) -> int | None:
    f = p.filter_el("output")
    pg = f.find(f"./page[@id='{page_id}']") if f is not None else None
    if pg is None:
        return None
    return len(pg.findall("./zones/zone"))


def _page_state(p: Project, pg) -> dict:
    c, lay, out = p.content(pg.id), p.layout(pg.id), p.output(pg.id)
    rect = c.get("content_rect_px")
    done = lay.get("processed")
    return {
        "deskew": round(p.deskew_angle(pg.id), 2),
        "page_box": _page_box(p, pg.id),
        "content": None if not rect or c.get("empty") else [round(rect[k]) for k in ("x", "y", "width", "height")],
        "empty": bool(c.get("empty")) if c.get("processed") else None,
        "margins": None if not done else [round(lay["margins_mm"][k], 1) for k in ("left", "top", "right", "bottom")],
        "align_v": lay.get("align_v") if done else None,
        "align_h": lay.get("align_h") if done else None,
        "match_size": lay.get("match_size") if done else None,
        "color_mode": out.get("color_mode"),
        "dpi": out.get("dpi"),
        "white_margins": out.get("white_margins"),
        "equalize_illumination": out.get("equalize_illumination"),
        "despeckle": out.get("despeckle"),
        "picture_zones": _picture_zones(p, pg.id),
    }


STAGE_OF = {"rotation": "fix_orientation", "split": "page_split", "deskew": "deskew", "page_box": "select_content",
            "content": "select_content", "empty": "select_content", "margins": "page_layout",
            "align_v": "page_layout", "align_h": "page_layout", "match_size": "page_layout",
            "color_mode": "output", "dpi": "output", "white_margins": "output", "equalize_illumination": "output",
            "despeckle": "output", "picture_zones": "output", "image": "project"}


def _is_cover(p: Project, pg) -> bool:
    if pg.image.index in (0, len(p.images) - 1):
        return True
    lay, c = p.layout(pg.id), p.content(pg.id)
    if c.get("mode") == "cover":
        return True
    return bool(lay.get("processed") and not lay.get("match_size", True)
                and all(v == 0 for v in lay["margins_mm"].values()))


def _content_category(a: list, u: list, page_h: float) -> str:
    ax0, ay0, aw, ah = a
    ux0, uy0, uw, uh = u
    deltas = {"left_ext": ax0 - ux0, "top_ext": ay0 - uy0, "right_ext": (ux0 + uw) - (ax0 + aw),
              "bottom_ext": (uy0 + uh) - (ay0 + ah)}
    side, d = max(deltas.items(), key=lambda kv: abs(kv[1]))
    if d < 0:
        return "content_box_trim"
    # A small band at the top or bottom of the box is a page number or a header.
    if side in ("top_ext", "bottom_ext"):
        return "content_box_numbers" if d < 0.15 * max(1.0, page_h) else "content_box_extend"
    return "content_box_extend"


def _category(field: str, agent, user, cover: bool, picture: bool, page_h: float) -> str:
    if field == "image":
        return "page_removed" if user is None else "page_added"
    if field in ("rotation", "split"):
        return field
    if field == "deskew":
        return "deskew_picture" if picture else "deskew_text"
    if cover and field in ("page_box", "content", "empty", "margins", "align_v", "align_h", "match_size",
                           "color_mode", "dpi", "white_margins", "equalize_illumination"):
        return "cover"
    if field == "page_box":
        return "page_box"
    if field == "empty":
        return "content_empty"
    if field == "content":
        return _content_category(agent, user, page_h) if agent and user else "content_empty"
    if field == "margins":
        return "margins"
    if field in ("align_v", "align_h"):
        return "alignment"
    if field == "match_size":
        return "match_size"
    if field in ("color_mode", "white_margins", "equalize_illumination"):
        return "output_mode"
    if field == "dpi":
        return "dpi"
    if field == "picture_zones":
        return "picture_zones"
    return "other"


def _changed(field: str, a, b) -> bool:
    if field == "split":
        return not _same_split(a, b)
    if field == "deskew":
        return abs((a or 0) - (b or 0)) > 0.05
    if field == "content" and a and b:
        return any(abs(x - y) > CONTENT_PX_TOL for x, y in zip(a, b))
    if field == "margins" and a and b:
        return any(abs(x - y) > 0.05 for x, y in zip(a, b))
    return a != b


def diff_records(original: str | os.PathLike, corrected: str | os.PathLike) -> list[dict]:
    a, b = Project(Path(original).resolve()), Project(Path(corrected).resolve())
    recs: list[dict] = []

    def add(page: str, field: str, va, vb, cover=False, picture=False, page_h=0.0):
        recs.append({"page": page, "stage": STAGE_OF.get(field, "other"), "field": field, "agent": va, "user": vb,
                     "category": _category(field, va, vb, cover, picture, page_h)})

    ia = {im.path.name: im for im in a.images.values()}
    ib = {im.path.name: im for im in b.images.values()}
    for n in sorted(set(ia) - set(ib)):
        add(Path(n).stem, "image", n, None)
    for n in sorted(set(ib) - set(ia)):
        add(Path(n).stem, "image", None, n)
    # Rotation and split belong to a scan, so the log gets one record per scan.
    geo_changed: set[str] = set()
    for n in sorted(set(ia) & set(ib), key=lambda x: ia[x].index):
        ra, rb = a.rotation(ia[n].id), b.rotation(ib[n].id)
        if ra != rb:
            add(Path(n).stem, "rotation", ra, rb)
            geo_changed.add(n)
        sa, sb = _split_state(a, ia[n].id), _split_state(b, ib[n].id)
        if _changed("split", sa, sb):
            add(Path(n).stem, "split", sa, sb)
            geo_changed.add(n)

    def keyed(p: Project) -> dict:
        return {(pg.image.path.name, pg.image.file_page, pg.side): pg for pg in p.pages}

    ka, kb = keyed(a), keyed(b)
    for key in sorted(set(ka) & set(kb), key=lambda k: ka[k].index):
        pa, pb = ka[key], kb[key]
        sa, sb = _page_state(a, pa), _page_state(b, pb)
        name = pa.label if not pa.image.file_page else f"{pa.label} (page {pa.image.file_page})"
        cover = _is_cover(a, pa) or _is_cover(b, pb)
        picture = sa["color_mode"] in ("colorOrGray", "mixed") or sb["color_mode"] in ("colorOrGray", "mixed")
        pr = (a.content(pa.id).get("page_rect_px") or {})
        geo = pa.image.path.name in geo_changed or _changed("deskew", sa["deskew"], sb["deskew"])
        for f in sa:
            # Box coordinates depend on rotation, split and deskew, so after such a change only that change counts.
            if geo and f in ("content", "page_box"):
                continue
            if f == "content" and sa["empty"] != sb["empty"]:
                continue
            if _changed(f, sa[f], sb[f]):
                add(name, f, sa[f], sb[f], cover, picture, pr.get("height", 0.0))
    return recs


def _key(rec: dict) -> str:
    raw = json.dumps([rec["book"], rec["page"], rec["stage"], rec["field"], rec["agent"], rec["user"]],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def read_log(log: str | None = None) -> list[dict]:
    path = log_path(log)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def record(original: str, corrected: str, book: str | None = None, log: str | None = None) -> dict:
    corr = Path(corrected).resolve()
    bid = book_id(book or corr.parent)
    recs = diff_records(original, corrected)
    path = log_path(log)
    seen = {r.get("key") for r in read_log(str(path))}
    now = _dt.datetime.now().isoformat(timespec="seconds")
    new, dup = [], 0
    for r in recs:
        r = {"time": now, "book": bid, "agent_project": Path(original).name, "project": corr.name, **r}
        r["key"] = _key(r)
        if r["key"] in seen:
            dup += 1
            continue
        seen.add(r["key"])
        new.append(r)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    cats: dict[str, int] = {}
    for r in new:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    return {"log": str(path), "book": bid, "changes": len(recs), "written": len(new),
            "skipped_duplicates": dup, "written_by_category": dict(sorted(cats.items(), key=lambda kv: -kv[1]))}


def stats(log: str | None = None, book: str | None = None, top: int = 5) -> dict:
    recs = read_log(log)
    if book:
        bid = book_id(book)
        recs = [r for r in recs if r.get("book") == bid]
    by_cat: dict[str, dict] = {}
    by_book: dict[str, dict] = {}
    for r in recs:
        c = r.get("category", "other")
        e = by_cat.setdefault(c, {"changes": 0, "pages": set(), "books": set()})
        e["changes"] += 1
        e["pages"].add((r.get("book"), r.get("page")))
        e["books"].add(r.get("book"))
        b = by_book.setdefault(r.get("book"), {"changes": 0, "categories": {}})
        b["changes"] += 1
        b["categories"][c] = b["categories"].get(c, 0) + 1
    cats = sorted(by_cat.items(), key=lambda kv: (-kv[1]["changes"], kv[0]))
    return {
        "log": str(log_path(log)), "records": len(recs), "books": len(by_book),
        "by_category": {c: {"changes": e["changes"], "pages": len(e["pages"]), "books": len(e["books"])}
                        for c, e in cats},
        "by_book": {b: {"changes": e["changes"],
                        "categories": dict(sorted(e["categories"].items(), key=lambda kv: -kv[1]))}
                    for b, e in sorted(by_book.items(), key=lambda kv: -kv[1]["changes"])},
        "most_frequent": [{"category": c, "changes": e["changes"], "pages": len(e["pages"]),
                           "books": len(e["books"]), "workflow_rule": CATEGORY_RULE.get(c, "")}
                          for c, e in cats[:top]],
    }
