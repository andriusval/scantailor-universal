"""Tool implementations shared by the MCP server and the command line.

Every public function here is one tool: JSON-serialisable arguments in,
JSON-serialisable dict out (previews return PIL images, wrapped by the caller).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from PIL import Image

from . import checks
from .preview import contact_sheet, render_image, render_page
from .project import Project

STAGES = {1: "fix_orientation", 2: "page_split", 3: "deskew", 4: "select_content",
          5: "page_layout", 6: "output"}
IMAGE_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".bmp"}

# ---------------------------------------------------------------- CLI binary

def find_cli() -> str:
    env = os.environ.get("ST_CLI")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    repo = here.parents[3]
    for cand in (repo / "build/src/app_cli/scantailor-universal-cli",
                 repo / "build/src/app_cli/Release/scantailor-universal-cli.exe",
                 here.parents[1] / "scantailor-universal-cli.exe"):
        if cand.exists():
            return str(cand)
    for name in ("scantailor-universal-cli", "scantailor-universal-cli.exe"):
        p = shutil.which(name)
        if p:
            return p
    raise FileNotFoundError("scantailor-universal-cli not found; set ST_CLI to its path")


def cli_env() -> dict:
    """Environment for scantailor-universal-cli.

    ScanTailor's OpenMP code is ~8x slower with many threads (threads mostly spin) and its
    output then differs slightly from run to run. One OpenMP thread is faster and deterministic
    (measured 2026-11-20: 4 spreads, all stages, 88 s -> 11 s, bit-identical across runs).
    Override with ST_OMP_THREADS.
    """
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = os.environ.get("ST_OMP_THREADS", "1")
    return env


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, cmd: list[str], project: Path, start: int, end: int, n_pages: int):
        self.id = uuid.uuid4().hex[:8]
        self.cmd, self.project, self.start, self.end = cmd, project, start, end
        self.n_pages = n_pages
        self.log_path = project.with_suffix(f".job-{self.id}.log")
        self.started = time.time()
        self.finished: float | None = None
        self.returncode: int | None = None
        self.current_filter = start
        self.processed = 0
        self._split_done = start > 2
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=cli_env(),
                                      text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        with open(self.log_path, "w", encoding="utf-8") as log:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                log.write(line)
                m = re.match(r"Filter: (\d+)", line.strip())
                if m:
                    self.current_filter = int(m.group(1))
                    self.processed = 0
                    if self.current_filter > 2:
                        self._split_done = False if self.start <= 2 else True
                elif line.strip().startswith("Processing:"):
                    self.processed += 1
        self.returncode = self._proc.wait()
        self.finished = time.time()

    def status(self) -> dict:
        done = self.finished is not None
        tail = ""
        if done and self.returncode:
            tail = "".join(Path(self.log_path).read_text(encoding="utf-8", errors="replace").splitlines(True)[-15:])
        return {
            "job_id": self.id,
            "state": ("ok" if self.returncode == 0 else "failed") if done else "running",
            "stages": f"{self.start}-{self.end}",
            "current_stage": f"{self.current_filter} {STAGES.get(self.current_filter, '')}",
            "pages_in_current_stage": (f"{self.processed}/{self.n_pages}" if self._split_done
                                       else f"{self.processed} (total known after the split stage)"),
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
            "log": str(self.log_path),
            **({"error_tail": tail} if tail else {}),
        }


_jobs: dict[str, Job] = {}


def _running_job_for(project: Path) -> Job | None:
    for j in _jobs.values():
        if j.project == project and j.finished is None:
            return j
    return None


def _ensure_idle(project: Path) -> None:
    j = _running_job_for(project)
    if j:
        raise RuntimeError(f"job {j.id} is still processing this project; wait for it (job_status)")


# ---------------------------------------------------------------- helpers

def _proj(project: str) -> Project:
    return Project(project)


def _resolve_pages(p: Project, pages) -> list[str]:
    if pages in (None, "all"):
        return [pg.id for pg in p.pages]
    if isinstance(pages, (str, int)):
        pages = [x.strip() for x in str(pages).split(",") if x.strip()]
    return [p.page(x).id for x in pages]


def _image_dpi(path: Path) -> tuple[float, float] | None:
    try:
        with Image.open(path) as im:
            d = im.info.get("dpi")
            if d and d[0] and d[0] > 1:
                return float(d[0]), float(d[1])
    except Exception:
        pass
    return None


# ================================================================ TOOLS

def _natural_key(path: Path):
    """'009' < '0100' < '9999' by number, not by text ('0100' would sort before '011')."""
    parts = re.split(r"(\d+)", path.stem.lower())
    return [(0, int(t), len(t)) if t.isdigit() else (1, t, 0) for t in parts if t != ""]


def project_create(images_dir: str, project: str, output_dir: str | None = None,
                   dpi: int = 300, force_dpi: bool = False, order: str = "natural") -> dict:
    """Create a ScanTailor project from a folder of page images (runs stage 1 only, which is fast).

    DPI rule from the manual workflow: if an image has no usable DPI, use `dpi` (300).
    Set force_dpi=true to override DPI stored in the images.
    order=natural sorts file names by their numbers (009 < 0100 < 9999); order=name is plain text
    order (what ScanTailor does on its own). Check the returned first/last files.
    """
    src = Path(images_dir).resolve()
    files = [f for f in src.iterdir() if f.suffix.lower() in IMAGE_EXT]
    if order == "natural":
        files.sort(key=_natural_key)
    elif order == "name":
        files.sort()
    else:
        raise ValueError("order must be natural|name")
    if not files:
        raise FileNotFoundError(f"no images in {src}")
    proj = Path(project).resolve()
    out = Path(output_dir).resolve() if output_dir else proj.parent / "out"
    out.mkdir(parents=True, exist_ok=True)
    dpis = {f.name: _image_dpi(f) for f in files}
    missing = [n for n, d in dpis.items() if d is None]
    found = [round(d[0]) for d in dpis.values() if d]
    # The CLI does not read DPI from files given via a list and silently assumes 600,
    # so the DPI is always passed explicitly (one value for the whole project).
    if force_dpi or not found:
        use_dpi = dpi
        dpi_note = f"DPI set to {dpi}" + (" (forced)" if force_dpi else " (images have no DPI)")
    else:
        use_dpi = max(set(found), key=found.count)
        dpi_note = f"DPI {use_dpi} read from image files"
        if len(set(found)) > 1 or missing:
            dpi_note += (f"; WARNING mixed/missing DPI: {sorted(set(found))}, {len(missing)} without DPI"
                         " - all pages treated as " + str(use_dpi))
    cmd = [find_cli(), "--end-filter=1", f"--dpi={use_dpi}", f"--output-project={proj}"]
    # Pass files explicitly (keeps our order). Arguments are Unicode-safe on Windows (paths like
    # "Knygų"); stdin ("-") is only used when the list would exceed the Windows command-line limit.
    stdin = None
    if sum(len(str(f)) + 3 for f in files) < 28000:
        cmd += [str(f) for f in files] + [str(out)]
    else:
        cmd += ["-", str(out)]
        stdin = "\n".join(str(f) for f in files) + "\n"
    r = subprocess.run(cmd, input=stdin, capture_output=True, env=cli_env(),
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not proj.exists():
        raise RuntimeError(f"CLI failed ({r.returncode}): {(r.stdout + r.stderr)[-1500:]}")
    p = Project(proj)
    got = [im.path.name for im in p.images.values()]
    if got != [f.name for f in files]:
        raise RuntimeError(f"ScanTailor changed the page order: {got[:10]}…")
    return {"project": str(proj), "output_dir": str(out), "images": len(p.images),
            "pages": len(p.pages), "dpi": dpi_note,
            "order": {"mode": order, "first": got[:5], "last": got[-5:]},
            "next": "look at a few originals with image_preview (orientation? two-page spreads?), "
                    "then run_stages(1, 5, orientation=..., layout=...)"}


def run_stages(project: str, start: int = 1, end: int = 5, orientation: str | None = None,
               layout: str | None = None, layout_direction: str | None = None,
               dpi: int | None = None, content_detection: str | None = None,
               wait_s: float = 0) -> dict:
    """Run ScanTailor filters start..end (1 orientation, 2 split, 3 deskew, 4 content, 5 margins, 6 output)
    on all pages, in the background. Per-page manual settings in the project are kept.

    orientation/layout/... are applied to ALL pages when given (use only on the first run or to reset).
    Returns a job; poll with job_status. wait_s>0 blocks up to that many seconds first.
    """
    proj = Path(project).resolve()
    _ensure_idle(proj)
    if not (1 <= start <= end <= 6):
        raise ValueError("need 1 <= start <= end <= 6")
    p = Project(proj)
    cmd = [find_cli(), "--verbose", f"--start-filter={start}", f"--end-filter={end}"]
    if orientation:
        cmd.append(f"--orientation={orientation}")
    if layout:
        cmd.append(f"--layout={layout}")
    if layout_direction:
        cmd.append(f"--layout-direction={layout_direction}")
    if dpi:
        cmd.append(f"--dpi={dpi}")
    if content_detection:
        cmd.append(f"--content-detection={content_detection}")
    cmd += [f"--output-project={proj}", str(proj), str(p.output_dir)]
    shutil.copy2(proj, proj.with_suffix(proj.suffix + f".before-run{start}-{end}"))
    job = Job(cmd, proj, start, end, len(p.images) if start <= 2 else len(p.pages))
    _jobs[job.id] = job
    return job_status(job.id, wait_s) if wait_s else job.status()


def job_status(job_id: str, wait_s: float = 0) -> dict:
    """Status of a background run_stages job. wait_s blocks until it finishes or the time passes."""
    job = _jobs.get(job_id)
    if not job:
        raise KeyError(f"unknown job {job_id}")
    deadline = time.time() + float(wait_s)
    while job.finished is None and time.time() < deadline:
        time.sleep(0.5)
    return job.status()


def project_info(project: str) -> dict:
    """Overview: counts, which stages have results, defaults, output dir."""
    p = _proj(project)
    stage_done = {}
    for name in ("fix-orientation", "page-split", "deskew", "select-content", "page-layout", "output"):
        f = p.filter_el(name)
        stage_done[name] = 0 if f is None else len(f.findall("./page")) + len(f.findall("./image"))
    rots = {}
    for im in p.images.values():
        rots[p.rotation(im.id)] = rots.get(p.rotation(im.id), 0) + 1
    dpis = sorted({(im.dpi_x, im.dpi_y) for im in p.images.values()})
    return {"project": str(p.path), "output_dir": str(p.output_dir), "images": len(p.images),
            "pages": len(p.pages), "image_dpi": dpis, "rotations": rots,
            "entries_per_stage": stage_done,
            "running_job": (_running_job_for(p.path).status() if _running_job_for(p.path) else None)}


def pages_list(project: str, sort_by: str = "index", descending: bool = False,
               limit: int = 0, offset: int = 0) -> dict:
    """Page table, like the GUI thumbnail list. sort_by: index | content_width | content_height | z_width | z_height.
    z_* are robust z-scores of content size: |z|>3 are the pages a human would inspect after sorting."""
    p = _proj(project)
    stats = {r["page_id"]: r for r in checks.content_stats(p)}
    rows = []
    for pg in p.pages:
        s, lay, out = stats[pg.id], p.layout(pg.id), p.output(pg.id)
        c = p.content(pg.id)
        flags = []
        if s["empty"]:
            flags.append("empty_content")
        if abs(s["z_width"]) > 3:
            flags.append("outlier_width")
        if abs(s["z_height"]) > 3:
            flags.append("outlier_height")
        if pg.index in (0, len(p.pages) - 1) or pg.image.index in (0, len(p.images) - 1):
            flags.append("cover_candidate")
        rows.append({
            "page_id": pg.id, "label": pg.label, "index": pg.index,
            "content_mode": s["mode"], "content_width": round(s["w"]), "content_height": round(s["h"]),
            "content_mm": c.get("content_mm"),
            "z_width": s["z_width"], "z_height": s["z_height"],
            "margins_mm": lay.get("margins_mm"), "align": f"{lay.get('align_v')}/{lay.get('align_h')}"
            if lay.get("processed") else None,
            "match_size": lay.get("match_size"),
            "color_mode": out.get("color_mode"), "output_dpi": out.get("dpi"),
            "flags": flags,
        })
    key = {"index": "index", "content_width": "content_width", "content_height": "content_height",
           "z_width": "z_width", "z_height": "z_height", "width": "content_width",
           "height": "content_height"}.get(sort_by)
    if not key:
        raise ValueError("bad sort_by")
    rows.sort(key=lambda r: r[key], reverse=descending)
    total = len(rows)
    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    return {"total": total, "sort_by": key, "pages": rows}


def page_preview(project: str, page: str, max_px: int = 1200) -> Image.Image:
    """The page as in the GUI's Select Content view (deskewed, cropped) with the content box in blue."""
    p = _proj(project)
    return render_page(p, p.page(page).id, max_px=max_px)


def pages_preview(project: str, pages: str | list[str], cell_px: int = 400, cols: int = 4) -> Image.Image:
    """Contact sheet of several pages with their content boxes (max 16 pages)."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)[:16]
    return contact_sheet([(p.page(i).label, render_page(p, i, max_px=cell_px * 2)) for i in ids],
                         cell=cell_px, cols=cols)


def image_preview(project: str, images: str | list[int] | None = None, max_px: int = 800) -> Image.Image:
    """Original scans (after the current orientation fix). images: list of image index (0-based) or 'first3'."""
    p = _proj(project)
    ims = list(p.images.values())
    if images in (None, "first3"):
        sel = ims[:3]
    else:
        if isinstance(images, (str, int)):
            images = [x for x in str(images).split(",") if x.strip()]
        sel = [ims[int(i)] for i in images][:8]
    previews = [(im.path.name, render_image(p, im.id, max_px)) for im in sel]
    return contact_sheet(previews, cell=max_px // 2 if len(sel) > 1 else max_px, cols=min(4, len(sel)))


ORIENTATION_DEG = {"none": 0, "right": 90, "upsidedown": 180, "left": 270}
LAYOUTS = {"1": "single-uncut", "single": "single-uncut", "1.5": "single-cut", "2": "two-pages",
           "spread": "two-pages", "0": "auto", "auto": "auto",
           "single-uncut": "single-uncut", "single-cut": "single-cut", "two-pages": "two-pages"}


def _resolve_images(p: Project, images) -> list[str]:
    ims = list(p.images.values())
    if images in (None, "all"):
        return [im.id for im in ims]
    if isinstance(images, (str, int)):
        images = [x.strip() for x in str(images).split(",") if x.strip()]
    out = []
    for x in images:
        sx = str(x)
        match = [im for im in ims if im.path.name == sx or im.path.stem == sx]
        if match:
            out.append(match[0].id)
        elif sx.lstrip("-").isdigit():
            out.append(ims[int(sx)].id)  # 0-based index, negative from the end
        else:
            raise KeyError(f"unknown image {x}")
    return out


def images_set_geometry(project: str, images: str | list[str], orientation: str | None = None,
                        layout: str | None = None) -> dict:
    """Per-image orientation and page layout, for books where scans differ (e.g. covers upright as single
    pages, inner scans rotated two-page spreads). images: 'all', file names ('001.jpg'), stems ('001') or
    0-based indexes (-1 = last). orientation: none|right|left|upsidedown (right = 90° clockwise).
    layout: 1 (single page) | 1.5 (single, cut) | 2 (two-page spread) | auto.
    Afterwards run_stages(1, 5) WITHOUT orientation/layout arguments (those would reset every image)."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    ids = _resolve_images(p, images)
    for i in ids:
        if orientation is not None:
            if orientation not in ORIENTATION_DEG:
                raise ValueError(f"orientation must be one of {list(ORIENTATION_DEG)}")
            p.set_rotation(i, ORIENTATION_DEG[orientation])
        if layout is not None:
            if str(layout) not in LAYOUTS:
                raise ValueError(f"layout must be one of {list(LAYOUTS)}")
            p.set_split_layout(i, LAYOUTS[str(layout)])
    p.save()
    return {"updated_images": [p.images[i].path.name for i in ids], "orientation": orientation,
            "layout": layout, "next": "run_stages(1, 5) without orientation/layout"}


def page_set_content(project: str, page: str, mode: str = "manual", x: float | None = None,
                     y: float | None = None, width: float | None = None, height: float | None = None,
                     expand_top: float = 0, expand_bottom: float = 0, expand_left: float = 0,
                     expand_right: float = 0) -> dict:
    """Change one page's content box (page px, same coords as page_preview / pages_list).

    mode=auto: back to automatic detection. mode=full: whole page.
    mode=cover: detect the book cover on a darker scanner background and use its bounds (covers).
    mode=tighten: shrink the box vertically to the text, dropping isolated specks at its top/bottom
    edge (keeps page numbers). Check the preview afterwards.
    mode=manual: set x/y/width/height, and/or grow (+) or shrink (-) individual edges with expand_*.
    Afterwards run_stages(4, 5) (or (4, 6)) so margins/output pick it up.
    """
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    pg = p.page(page)
    before = p.content(pg.id)
    if mode == "auto":
        p.set_content(pg.id, None)
    else:
        pr = before["page_rect_px"]
        if mode == "full":
            rect = dict(pr)
        elif mode == "tighten":
            rect = checks.tighten_rect(p, pg.id)
            if rect is None:
                raise RuntimeError("nothing to tighten (empty or unprocessed content)")
        elif mode == "cover":
            rect = checks.detect_cover_rect(p, pg.id)
            if rect is None:
                raise RuntimeError("could not find a cover on a darker background; use mode=full or manual")
        elif mode == "manual":
            cur = before.get("content_rect_px") or dict(pr)
            rect = {"x": cur["x"] if x is None else x, "y": cur["y"] if y is None else y,
                    "width": cur["width"] if width is None else width,
                    "height": cur["height"] if height is None else height}
            rect["x"] -= expand_left
            rect["width"] += expand_left + expand_right
            rect["y"] -= expand_top
            rect["height"] += expand_top + expand_bottom
        else:
            raise ValueError("mode must be auto|manual|full|cover|tighten")
        # clamp to page
        rect["x"] = max(pr["x"], rect["x"])
        rect["y"] = max(pr["y"], rect["y"])
        rect["width"] = min(rect["width"], pr["x"] + pr["width"] - rect["x"])
        rect["height"] = min(rect["height"], pr["y"] + pr["height"] - rect["y"])
        if rect["width"] <= 0 or rect["height"] <= 0:
            raise ValueError(f"resulting box is empty: {rect}")
        p.set_content(pg.id, rect)
    p.save()
    return {"page": pg.label, "before": before.get("content_rect_px"),
            "after": Project(proj).content(pg.id).get("content_rect_px"), "mode": mode,
            "next": "run_stages(4, 5) to propagate"}


def pages_set_layout(project: str, pages: str | list[str] = "all", margins_mm: float | None = None,
                     margin_left: float | None = None, margin_top: float | None = None,
                     margin_right: float | None = None, margin_bottom: float | None = None,
                     align_v: str | None = None, align_h: str | None = None,
                     match_size: bool | None = None) -> dict:
    """Margins (mm), alignment and 'Match size with other pages' for pages ('all' or list of ids/labels).
    align_v: top|center|bottom|vauto|voriginal ; align_h: left|center|right|hauto|horiginal.
    Covers: margins_mm=0, match_size=false. Then run_stages(5, 5) or (5, 6)."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    m = {}
    if margins_mm is not None:
        m = {k: margins_mm for k in ("left", "top", "right", "bottom")}
    for k, v in (("left", margin_left), ("top", margin_top), ("right", margin_right), ("bottom", margin_bottom)):
        if v is not None:
            m[k] = v
    ids = _resolve_pages(p, pages)
    for i in ids:
        p.set_layout(i, m or None, align_v, align_h, match_size)
    p.save()
    return {"updated_pages": len(ids), "margins_mm": m or "unchanged", "align_v": align_v,
            "align_h": align_h, "match_size": match_size, "next": "run_stages(5, 5) to propagate"}


def pages_set_output(project: str, pages: str | list[str], color_mode: str | None = None, dpi: int | None = None,
                     despeckle: str | None = None) -> dict:
    """Output mode per page. color_mode: bw | color (=color/grayscale) | mixed. Text: bw 600; covers and
    picture pages: color 300 or mixed."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    ids = _resolve_pages(p, pages)
    for i in ids:
        p.set_output(i, color_mode, dpi, despeckle)
    p.save()
    return {"updated_pages": len(ids), "color_mode": color_mode, "dpi": dpi, "despeckle": despeckle}


def check_pages(project: str, pages: str | list[str] = "all", kind: str = "content") -> dict:
    """Heuristic checks with evidence. kind=content: ink outside the box (missed page numbers, headers),
    isolated junk lines inside it, empty boxes on pages with ink, and where page numbers sit.
    kind=pictures: fraction of the content that looks like a photo/illustration (>0.05 -> mixed/color)."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)
    if kind == "pictures":
        res = [checks.picture_score(p, i) for i in ids]
        flagged = [r for r in res if r["picture_fraction"] > 0.05]
        return {"checked": len(res), "flagged": flagged}
    if kind != "content":
        raise ValueError("kind must be content|pictures")
    res = [checks.check_page(p, i) for i in ids]
    flagged = [r for r in res if r.get("findings")]
    pos = [r.get("page_number_in_box") for r in res if r.get("page_number_in_box")]
    summary = {k: pos.count(k) for k in set(pos)}
    return {"checked": len(res), "flagged": flagged,
            "page_number_position_votes": summary,
            "suggested_align_v": max(summary, key=summary.get) if summary else None}


def export_summary(project: str) -> dict:
    """List produced output files (after stage 6) and anything missing."""
    p = _proj(project)
    out = p.output_dir
    files = {f.stem: f for f in out.glob("*.tif")}
    missing = [pg.label for pg in p.pages if pg.label not in files]
    return {"output_dir": str(out), "tif_files": len(files), "missing": missing[:50],
            "total_mb": round(sum(f.stat().st_size for f in files.values()) / 1e6, 1)}


WORKFLOW = (Path(__file__).resolve().parents[3] / "docs" / "AGENTIC_WORKFLOW.en.md")


def workflow_guide() -> dict:
    """The step-by-step workflow the agent should follow (mirrors the manual GUI procedure)."""
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else "(workflow doc missing)"
    return {"workflow": text}


TOOLS = [project_create, images_set_geometry, run_stages, job_status, project_info, pages_list, page_preview,
         pages_preview, image_preview, page_set_content, pages_set_layout, pages_set_output,
         check_pages, export_summary, workflow_guide]
IMAGE_TOOLS = {"page_preview", "pages_preview", "image_preview"}
