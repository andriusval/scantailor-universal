"""Tool implementations shared by the MCP server and the command line.

Every public function here is one tool: JSON-serialisable arguments in,
JSON-serialisable dict out (previews return PIL images, wrapped by the caller).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
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
        # With --json-progress, stdout holds only JSON events, so the human text goes to the log file.
        self.json = "--json-progress" in cmd
        self.page_errors: list[dict] = []
        self.warnings: list[str] = []
        self.run_end: dict | None = None
        self._log = open(self.log_path, "w", encoding="utf-8")
        # Older builds read relative project paths from the current directory, so the job starts in the project folder.
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=self._log if self.json else subprocess.STDOUT, env=cli_env(),
                                      cwd=str(project.parent), text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=self._pump, daemon=True).start()

    def _event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "stage_start":
            self.current_filter = int(ev.get("stage", self.current_filter))
            self.processed = 0
            self.n_pages = int(ev.get("pages", self.n_pages))
            self._split_done = True
        elif kind == "page_done":
            self.processed += 1
        elif kind == "page_error":
            self.processed += 1
            self.page_errors.append({k: ev.get(k) for k in ("stage", "image", "side", "error") if k in ev})
        elif kind == "warning":
            self.warnings.append(str(ev.get("message") or ev.get("warning") or ev))
        elif kind == "run_end":
            self.run_end = {k: v for k, v in ev.items() if k != "event"}

    def _pump(self) -> None:
        if self.json:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    self._event(json.loads(line))
                except ValueError:
                    self._log.write(line + "\n")
        else:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                self._log.write(line)
                m = re.match(r"Filter: (\d+)", line.strip())
                if m:
                    self.current_filter = int(m.group(1))
                    self.processed = 0
                    if self.current_filter > 2:
                        self._split_done = False if self.start <= 2 else True
                elif line.strip().startswith("Processing:"):
                    self.processed += 1
        self.returncode = self._proc.wait()
        self._log.close()
        self.finished = time.time()

    def status(self) -> dict:
        done = self.finished is not None
        tail = ""
        if done and self.returncode:
            tail = "".join(Path(self.log_path).read_text(encoding="utf-8", errors="replace").splitlines(True)[-15:])
        out = {
            "job_id": self.id,
            "state": ("ok" if self.returncode == 0 else "failed") if done else "running",
            "stages": f"{self.start}-{self.end}",
            "current_stage": f"{self.current_filter} {STAGES.get(self.current_filter, '')}",
            "pages_in_current_stage": (f"{self.processed}/{self.n_pages}" if self._split_done
                                       else f"{self.processed} (total known after the split stage)"),
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
            "log": str(self.log_path),
        }
        if self.page_errors:
            out["page_errors"] = self.page_errors[:20]
        if self.warnings:
            out["warnings"] = self.warnings[:10]
        if self.run_end:
            out["run_end"] = self.run_end
        if tail:
            out["error_tail"] = tail
        return out


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
    if isinstance(pages, int):
        pages = [pages]
    elif isinstance(pages, str):
        pages = [x.strip() for x in pages.split(",") if x.strip()]
    return [p.find_page(x).id for x in pages]


def _image_dpi(path: Path) -> tuple[float, float] | None:
    try:
        with Image.open(path) as im:
            d = im.info.get("dpi")
            # 72/96 are what cameras, phones and screen tools write when they know no DPI: not a scan DPI
            if d and d[0] and d[0] > 1 and round(float(d[0])) not in (72, 96):
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
    # Each image keeps its own DPI, and an image without a usable DPI gets the dpi argument.
    want = {n: (float(dpi), float(dpi)) if force_dpi or d is None else d for n, d in dpis.items()}
    if force_dpi:
        dpi_note = f"DPI {dpi} forced on all images"
    else:
        counts: dict[int, int] = {}
        for d in dpis.values():
            if d:
                counts[round(d[0])] = counts.get(round(d[0]), 0) + 1
        dpi_note = "DPI from the image files: " + (", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))
                                                   or "none")
        if missing:
            dpi_note += f"; {len(missing)} image(s) without a usable DPI set to {dpi}"
    cmd = [find_cli(), "--end-filter=1", f"--output-project={proj}"]
    if force_dpi:
        cmd.insert(2, f"--dpi={dpi}")
    # Give the files as arguments to keep the order, and use stdin only for a very long list.
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
    # Older CLI builds give all images one default DPI, so the tool writes each DPI into the project.
    for el in p.root.findall("./images/image"):
        im = p.images[el.get("id")]
        d = el.find("dpi")
        if d is None:
            d = ET.SubElement(el, "dpi")
        h, v = want.get(im.path.name, (float(dpi), float(dpi)))
        d.set("horizontal", str(round(h)))
        d.set("vertical", str(round(v)))
    p.save()
    p = Project(proj)
    # A multi-page file gives one image per page, so compare the files in order without repeats.
    got = [n for i, n in enumerate(im.path.name for im in p.images.values())
           if i == 0 or n != list(p.images.values())[i - 1].path.name]
    if got != [f.name for f in files]:
        raise RuntimeError(f"ScanTailor changed the page order: {got[:10]}…")
    return {"project": str(proj), "output_dir": str(out), "images": len(p.images),
            "pages": len(p.pages), "dpi": dpi_note,
            "order": {"mode": order, "first": got[:5], "last": got[-5:]},
            "next": "look at a few originals with image_preview (orientation? two-page spreads?), "
                    "then run_stages(1, 5, orientation=..., layout=...)"}


def _check_inputs_reachable(p: Project, cli: str) -> None:
    """The CLI silently 'processes' missing images (exit 0) and then saves a broken project, so check
    first that it will be able to open every scan with the paths written in the project."""
    raw = [d.get("path", "") for d in p.root.findall("./directories/directory")]
    windows_cli = os.name == "nt" or cli.lower().endswith(".exe")
    if not windows_cli and any(re.match(r"^[A-Za-z]:[\\/]", d) for d in raw):
        raise RuntimeError(f"project has Windows paths {raw}: the Linux CLI cannot open them; run it with the "
                           "Windows CLI (ST_CLI=...scantailor-universal-cli.exe) or fix the paths")
    missing = [str(im.path) for im in p.images.values() if not im.path.exists()]
    if missing:
        raise RuntimeError(f"{len(missing)} scan(s) not found, e.g. {missing[:3]}")


# A bare file name is enough unless several scans share it; then the path relative to the project.
def _page_ref(p: Project, proj: Path, pg) -> str:
    names = [im.path.name for im in p.images.values()]
    ref = pg.image.path.name
    if names.count(ref) > 1:
        try:
            ref = pg.image.path.resolve().relative_to(proj.parent).as_posix()
        except ValueError:
            ref = pg.image.path.as_posix()
    return ref + {"left": ":L", "right": ":R"}.get(pg.side, "")


def run_stages(project: str, start: int = 1, end: int = 5, orientation: str | None = None,
               layout: str | None = None, layout_direction: str | None = None,
               dpi: int | None = None, content_detection: str | None = None,
               wait_s: float = 0, content_snap: str | None = None,
               pages: str | list[str] | None = None) -> dict:
    """Run ScanTailor filters start..end (1 orientation, 2 split, 3 deskew, 4 content, 5 margins, 6 output)
    on all pages, in the background. Per-page manual settings in the project are kept.

    orientation/layout/... are applied to ALL pages when given (use only on the first run or to reset).
    pages: only these pages (ids or labels), with a CLI that has --pages; the others keep their settings
    and output files. Stage 5 still uses the size of all pages: if the job reports a size warning, or
    after margin changes, run stages 5-6 on all pages.
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
    if content_snap:
        cmd.append(f"--content-snap={content_snap}")
    note = None
    if pages not in (None, "all"):
        if _cli_supports("pages"):
            cmd.append("--pages=" + ";".join(_page_ref(p, proj, p.page(i)) for i in _resolve_pages(p, pages)))
        else:
            note = "this CLI has no --pages, so all pages were processed"
    if _cli_supports("json-progress"):
        cmd.append("--json-progress")
    cmd += [f"--output-project={proj}", str(proj), str(p.output_dir)]
    shutil.copy2(proj, proj.with_suffix(proj.suffix + f".before-run{start}-{end}"))
    _check_inputs_reachable(p, cmd[0])
    job = Job(cmd, proj, start, end, len(p.images) if start <= 2 else len(p.pages))
    _jobs[job.id] = job
    res = job_status(job.id, wait_s) if wait_s else job.status()
    if note:
        res["note"] = note
    return res


_cli_help_cache: dict[str, str] = {}


def _cli_supports(option: str) -> bool:
    """True when the CLI help lists the option (builds before 2026-11-21 do not know --content-snap)."""
    cli = find_cli()
    if cli not in _cli_help_cache:
        r = subprocess.run([cli, "--help"], capture_output=True, env=cli_env(),
                           text=True, encoding="utf-8", errors="replace")
        _cli_help_cache[cli] = r.stdout + r.stderr
    return f"--{option}" in _cli_help_cache[cli]


def pages_snap_content(project: str, pages: str | list[str], sides: str | list[str] = "auto",
                       wait_s: float = 0) -> dict:
    """Shrink content boxes to the text with ScanTailor's own double-click adjustment (the GUI
    feature from ScanTailor Advanced, run through the CLI option --content-snap): for each side, a
    'double-click' on the outermost text makes ScanTailor snap that box edge to the ink there, so
    junk between the edge and the text drops out. Use it when check_pages reports junk at the box
    sides (thin_*_column_isolated, thin_*_line_isolated, box_exceeds_text).
    sides: 'auto' (the sides check_pages flags as junk), 'all', or a list of left|right|top|bottom.
    Runs stages 4-5 in the background (returns a job, like run_stages); preview the pages afterwards."""
    if not _cli_supports("content-snap"):
        raise RuntimeError(f"{find_cli()} does not know --content-snap: build the CLI from branch "
                           "agentic/windows-build (2026-11-21 or later), or use page_set_content(mode=tighten)")
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    reqs, plan, skipped = [], [], []
    names = [im.path.name for im in p.images.values()]
    dup_names = {n for n in names if names.count(n) > 1}

    def image_ref(path: Path) -> str:
        # a bare name is enough unless several scans share it; then a path relative to the project
        if path.name not in dup_names:
            return path.name
        try:
            return path.resolve().relative_to(proj.parent).as_posix()
        except ValueError:
            return path.as_posix()

    for i in _resolve_pages(p, pages):
        pg = p.page(i)
        if pg.image.file_page > 0:
            skipped.append({"page": pg.label, "why": "multi-page image files are not supported"})
            continue
        c = p.content(pg.id)
        if not c.get("processed") or c.get("empty"):
            skipped.append({"page": pg.label, "why": "no content box"})
            continue
        if sides == "auto":
            want = checks.junk_sides(p, pg.id)
        elif sides == "all":
            want = ["left", "right", "top", "bottom"]
        else:
            want = [sides] if isinstance(sides, str) else list(sides)
        if not want:
            skipped.append({"page": pg.label, "why": "no junk side found"})
            continue
        sub_ = {"left": ":L", "right": ":R"}.get(pg.side, "")
        done = []
        for side in want:
            pt = checks.snap_point(p, pg.id, side)
            if pt is None:
                continue
            # Name the edge, because on a short page the nearest edge can be the opposite one.
            reqs.append(f"{image_ref(pg.image.path)}{sub_}@{pt['x']},{pt['y']}:{side}")
            done.append({"side": side, "at": pt})
        if done:
            plan.append({"page": pg.label, "snaps": done})
        else:
            skipped.append({"page": pg.label, "why": "no text block to snap to"})
    if not reqs:
        return {"snapped": [], "skipped": skipped, "note": "nothing to do"}
    if any(";" in r for r in reqs):
        raise ValueError("an image file name contains ';', which --content-snap cannot pass")
    snapped_ids = [p.find_page(x["page"]).id for x in plan]
    res = run_stages(project, 4, 5, content_snap=";".join(reqs), wait_s=wait_s, pages=snapped_ids)
    res.update({"snapped": plan, "skipped": skipped,
                "next": "when the job is done: pages_preview on these pages; check_pages(kind=content)"})
    return res


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


def _cli_previews(p: Project, ids: list[str], max_px: int) -> dict[str, Image.Image]:
    """Previews drawn by ScanTailor itself (CLI --render-preview), so they show what the GUI shows."""
    import tempfile
    proj = p.path
    with tempfile.TemporaryDirectory(prefix="st-preview-") as tmp:
        cmd = [find_cli(), "--start-filter=4", "--end-filter=4",
               "--pages=" + ";".join(_page_ref(p, proj, p.page(i)) for i in ids),
               f"--render-preview={tmp}/png", f"--render-preview-size={int(max_px)}", str(proj), f"{tmp}/out"]
        r = subprocess.run(cmd, capture_output=True, env=cli_env(), cwd=str(proj.parent),
                           text=True, encoding="utf-8", errors="replace")
        out = {}
        for i in ids:
            f = Path(tmp) / "png" / (p.output_name(i) + ".png")
            if f.exists():
                with Image.open(f) as im:
                    out[i] = im.convert("RGB")
        if not out:
            raise RuntimeError(f"CLI preview failed ({r.returncode}): {(r.stdout + r.stderr)[-800:]}")
        return out


def _use_cli_preview(engine: str) -> bool:
    if engine == "cli":
        if not _cli_supports("render-preview") or not _cli_supports("pages"):
            raise RuntimeError("this CLI has no --render-preview/--pages; use engine='python'")
        return True
    if engine == "auto":
        return _cli_supports("render-preview") and _cli_supports("pages")
    return False


def page_preview(project: str, page: str, max_px: int = 1200, engine: str = "auto") -> Image.Image:
    """The page as in the GUI's Select Content view (deskewed, cropped) with the content box.
    engine: 'auto' (ScanTailor draws it through CLI --render-preview when available, else Python),
    'cli' or 'python' (fast, st-mcp's own copy of the geometry)."""
    p = _proj(project)
    pid = p.find_page(page).id
    if _use_cli_preview(engine):
        return _cli_previews(p, [pid], max_px)[pid]
    return render_page(p, pid, max_px=max_px)


def pages_preview(project: str, pages: str | list[str], cell_px: int = 400, cols: int = 4,
                  engine: str = "python") -> Image.Image:
    """Contact sheet of several pages with their content boxes (max 16 pages).
    engine: 'python' (default, fast) or 'cli' (drawn by ScanTailor; one CLI run, about 1 s per page)."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)[:16]
    if _use_cli_preview(engine):
        imgs = _cli_previews(p, ids, cell_px * 2)
        return contact_sheet([(p.page(i).label, imgs[i]) for i in ids if i in imgs], cell=cell_px, cols=cols)
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
    if isinstance(images, int):
        images = [images]
    elif isinstance(images, str):
        images = [x.strip() for x in images.split(",") if x.strip()]
    out = []
    for x in images:
        sx = str(x).strip()
        if isinstance(x, int) or sx.startswith("#"):  # an index, never a file name (files may be 1.jpg, 2.jpg...)
            out.append(ims[int(sx.lstrip("#"))].id)
            continue
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
    0-based indexes (-1 = last) given as numbers or '#3' (a string such as '3' is a file stem first). orientation: none|right|left|upsidedown (right = 90° clockwise).
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

    mode=auto: back to automatic detection. mode=full: whole page. mode=empty: blank page (no content;
    for flyleaves where only specks or bleed-through were detected).
    mode=cover: detect the book cover on a darker scanner background and use its bounds (covers).
    mode=tighten: fit the box to the text block on all four sides, dropping junk and faint specks at
    its edges (keeps page numbers and running heads; a side whose strip has ink in 3+ text lines is
    left as is and reported in kept_sides). Use for box_exceeds_text findings. Check the preview.
    mode=all_content: box around everything printed (photos, plates, captions) - for picture pages,
    where ScanTailor's text-oriented detection crops or misses the image.
    mode=include_numbers: extend the box to page numbers that check_pages reports as missed
    ("looks like a page number missed by the content box").
    mode=manual: set x/y/width/height, and/or grow (+) or shrink (-) individual edges with expand_*.
    Afterwards run_stages(4, 5) (or (4, 6)) so margins/output pick it up.
    """
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    pg = p.find_page(page)
    before = p.content(pg.id)
    if mode == "auto":
        p.set_content(pg.id, None)
    elif mode == "empty":
        p.set_content(pg.id, {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0})
    else:
        pr = before["page_rect_px"]
        if mode == "full":
            rect = dict(pr)
        elif mode == "tighten":
            rect = checks.tighten_rect(p, pg.id)
            if rect is None:
                raise RuntimeError("nothing to tighten (empty or unprocessed content)")
            note = rect.pop("kept_sides", None)
        elif mode == "all_content":
            rect = checks.all_content_rect(p, pg.id)
            if rect is None:
                raise RuntimeError("nothing printed found on the page")
            pad = 0.004 * max(pr["width"], pr["height"])
            rect = {"x": rect["x"] - pad, "y": rect["y"] - pad,
                    "width": rect["width"] + 2 * pad, "height": rect["height"] + 2 * pad}
        elif mode == "include_numbers":
            res = checks.check_page(p, pg.id)
            bands = [f for f in res.get("findings", []) if f["kind"] in ("ink_below_box", "ink_above_box")
                     and f["hint"].startswith("looks like a page number")]
            if not bands:
                raise RuntimeError("no missed page number found outside the box")
            cur = dict(before["content_rect_px"])
            x0, y0 = cur["x"], cur["y"]
            x1, y1 = x0 + cur["width"], y0 + cur["height"]
            for f in bands:
                b = f["band_px"]
                pad = max(4, b["height"] * 0.3)
                x0, y0 = min(x0, b["x"] - pad), min(y0, b["y"] - pad)
                x1, y1 = max(x1, b["x"] + b["width"] + pad), max(y1, b["y"] + b["height"] + pad)
            rect = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
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
            raise ValueError("mode must be auto|empty|manual|full|cover|tighten|include_numbers|all_content")
        # Clip each edge to the page separately, so that a clip does not move the opposite edge.
        rx0, ry0 = max(pr["x"], rect["x"]), max(pr["y"], rect["y"])
        rx1 = min(pr["x"] + pr["width"], rect["x"] + rect["width"])
        ry1 = min(pr["y"] + pr["height"], rect["y"] + rect["height"])
        rect = {"x": rx0, "y": ry0, "width": rx1 - rx0, "height": ry1 - ry0}
        if rect["width"] <= 0 or rect["height"] <= 0:
            raise ValueError(f"resulting box is empty: {rect}")
        p.set_content(pg.id, rect)
    p.save()
    res = {"page": pg.label, "before": before.get("content_rect_px"),
           "after": Project(proj).content(pg.id).get("content_rect_px"), "mode": mode,
           "next": "run_stages(4, 5) to propagate"}
    if mode == "tighten" and note:
        kept = [s for s in dict.fromkeys(note) if ":" not in s]
        widened = [s.split(":")[0] for s in dict.fromkeys(note) if s.endswith(":line")]
        res["kept_sides"] = kept + [f"{s}:line" for s in widened]
        msgs = []
        if kept:
            msgs.append(f"not trimmed: {', '.join(kept)} (the strip had ink in 3+ text lines)")
        if widened:
            msgs.append(f"trimmed but widened for a single overhanging line: {', '.join(widened)}")
        res["warning"] = "; ".join(msgs) + "; check the preview"
    return res


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


# Only pages with content, match size on and stage 5 done take part in the common page size.
def _body_pages(p: Project, skipped: list | None = None) -> list[tuple[str, dict]]:
    out = []
    for pg in p.pages:
        c = p.content(pg.id)
        lay = p.layout(pg.id)
        if not c.get("processed") or c.get("empty") or not c.get("content_mm"):
            continue
        if not lay.get("processed"):
            if skipped is not None:
                skipped.append(pg.label)
            continue
        if c.get("mode") == "cover" or not lay.get("match_size", True):
            continue
        out.append((pg.id, c))
    return out


def pages_fit_width(project: str, margin_mm: float = 10.0, percentile: float = 85.0) -> dict:
    """One page width for the whole book: the typical text-block width plus margin_mm on each side.
    Pages whose content is wider than the typical width get smaller left/right margins (down to 0), so a
    few wide pages (a long line, a table, a page number at the side) do not make every page wider.
    The typical width is the given percentile of the content widths of body pages (covers and pages with
    match size off do not count). Top/bottom margins and alignment do not change.
    Then run_stages(5, 5); the user's correction of the Onetti book follows this rule."""
    import statistics
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    no_layout: list[str] = []
    body = _body_pages(p, no_layout)
    if len(body) < 3:
        raise RuntimeError("too few pages with content and page layout; run stages 1-5 first")
    widths = sorted(c["content_mm"]["width"] for _, c in body)
    typical = widths[min(len(widths) - 1, int(round(percentile / 100.0 * (len(widths) - 1))))]
    changed = []
    for pid, c in body:
        w = c["content_mm"]["width"]
        lr = margin_mm if w <= typical else max(0.0, round((typical + 2 * margin_mm - w) / 2, 1))
        cur = p.layout(pid)["margins_mm"]
        # Smaller margins set on purpose (picture pages: 0) stay, because they never widen the page.
        lr = min(lr, cur["left"], cur["right"])
        if abs(cur["left"] - lr) > 0.05 or abs(cur["right"] - lr) > 0.05:
            p.set_layout(pid, {"left": lr, "right": lr})
        if lr != margin_mm:
            changed.append({"page": p.page(pid).label, "content_width_mm": w, "left_right_mm": lr})
    p.save()
    return {"typical_width_mm": typical, "skipped_no_page_layout": no_layout, "page_width_mm": round(typical + 2 * margin_mm, 1),
            "median_width_mm": statistics.median(widths), "narrowed_margins": changed,
            "next": "run_stages(5, 5), then pages_list(sort_by='content_width') to confirm one page size"}


def pages_align_auto(project: str, short_ratio: float = 0.85, missing_number_ratio: float = 0.95,
                     section_start_offset: float = 0.10, review_full_pages: bool = False) -> dict:
    """Vertical alignment for every body page, and lists of pages a person should check.
    - Front matter (pages before the first page with at least half the usual number of text lines:
      title, copyright, dedication, epigraph): vcenter. A small element at the top of such a page
      (series logo, less than 25 % of the usual height): top.
    - Section starts (the text block starts lower than usual by more than section_start_offset of
      the page height, with 3 or more text lines: preface, list or contents start): bottom, even when
      the page is short. A small block lower than usual (part title, specks): vcenter, and review.
    - Short pages (content height below short_ratio of the median: chapter ends, lists): top.
    - Full text pages without the page-number band (height below missing_number_ratio of the median
      but the usual number of lines): top, so that their text lines up with the other pages.
    - Other body pages: bottom.
    Lists for the report (nothing is removed or changed for them): blank pages, half-title candidates,
    near-empty pages (no text line, tiny box: mark them empty if they hold only specks or a stamp),
    and, with review_full_pages=true, full-height text pages with few text lines (rotated tables:
    vcenter is usual there). review_full_pages analyses every page (about 0.5 s each), so use it when
    the previews show tables or diagrams. Covers keep their settings. Then run_stages(5, 5)."""
    import statistics
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    no_layout: list[str] = []
    body = _body_pages(p, no_layout)
    if len(body) < 3:
        raise RuntimeError("too few pages with content and page layout; run stages 1-5 first")
    heights = [c["content_mm"]["height"] for _, c in body]
    med_h = statistics.median(heights)
    lines: dict[str, int] = {}

    def text_lines(pid: str) -> int:
        if pid not in lines:
            lines[pid] = checks.check_page(p, pid).get("text_lines") or 0
        return lines[pid]

    def top_rel(c: dict) -> float:
        cr, pr = c["content_rect_px"], c.get("page_rect_px") or c["content_rect_px"]
        return (cr["y"] - pr["y"]) / max(1.0, pr["height"])

    # The usual top of the text block, per page side, because left and right scans sit differently.
    usual_top = {}
    for side in ("left", "right", "single"):
        tops = [top_rel(c) for pid, c in body
                if p.page(pid).side == side and abs(c["content_mm"]["height"] - med_h) < 0.05 * med_h]
        usual_top[side] = statistics.median(tops) if tops else None

    # A sample gives the median number of lines, because the analysis of every page takes long.
    sample = [pid for pid, _ in body[len(body) // 4: len(body) // 4 + 15]]
    med_lines = statistics.median(text_lines(pid) for pid in sample) if sample else 0
    front_end = 0
    for i, (pid, _) in enumerate(body[: max(1, len(body) // 10)]):
        if text_lines(pid) >= 0.5 * med_lines:
            front_end = i
            break
    decided = {"vcenter": [], "top_small_header": [], "bottom_section_start": [], "vcenter_small_block": [],
               "top_short": [], "top_no_number_band": [], "bottom": 0}
    half_titles, near_empty, full_few_lines = [], [], []
    for i, (pid, c) in enumerate(body):
        h = c["content_mm"]["height"]
        lab = p.page(pid).label
        ut = usual_top.get(p.page(pid).side)
        offset = top_rel(c) - ut if ut is not None else 0.0
        area = c["content_mm"]["width"] * h
        if area < 0.01 * med_h * med_h and text_lines(pid) == 0:
            near_empty.append(lab)
        is_text = p.output(pid).get("color_mode") in (None, "bw")
        if i < front_end:
            if h < 0.25 * med_h and offset < section_start_offset:
                align = "top"
                decided["top_small_header"].append(lab)
            else:
                align = "vcenter"
                decided["vcenter"].append(lab)
            if text_lines(pid) <= 5:
                half_titles.append(lab)
        elif h < missing_number_ratio * med_h and offset > section_start_offset:
            if text_lines(pid) >= 3:
                align = "bottom"
                decided["bottom_section_start"].append(lab)
            else:
                align = "vcenter"
                decided["vcenter_small_block"].append(lab)
        elif h < short_ratio * med_h:
            align = "top"
            decided["top_short"].append(lab)
        elif h < missing_number_ratio * med_h and text_lines(pid) >= 0.85 * med_lines:
            align = "top"
            decided["top_no_number_band"].append(lab)
        else:
            align = "bottom"
            decided["bottom"] += 1
            # Picture pages already have their own output mode, so only text-mode pages need this review.
            if review_full_pages and is_text and h >= 0.9 * med_h and text_lines(pid) < 0.5 * med_lines:
                full_few_lines.append(lab)
        p.set_layout(pid, None, align, None, None)
    p.save()
    blank = [pg.label for pg in p.pages if p.content(pg.id).get("empty")]
    return {"median_height_mm": med_h, "median_text_lines": med_lines, **decided,
            "skipped_no_page_layout": no_layout,
            "review": {"full_height_few_lines": full_few_lines,
                       "small_blocks": decided["vcenter_small_block"],
                       "note": "rotated tables: preview them, vcenter is usual; section starts and small "
                               "blocks: preview them too, some books keep them at the top"},
            "report_removal_candidates": {"blank_pages": blank, "half_title_candidates": half_titles,
                                          "near_empty_pages": near_empty,
                                          "note": "list these in the final report; the user decides, nothing was removed"},
            "next": "run_stages(5, 5), then preview the top, vcenter and review pages"}


def pages_set_output(project: str, pages: str | list[str], color_mode: str | None = None, dpi: int | None = None,
                     despeckle: str | None = None, white_margins: bool | None = None,
                     equalize_illumination: bool | None = None, filling_color: str | None = None,
                     fill_offcut: bool | None = None, grayscale_first: bool | None = None) -> dict:
    """Output mode per page. color_mode: bw | color (=color/grayscale) | mixed.
    Text and pages with black-and-white line illustrations: bw 600. Pages with grayscale (halftone/photo)
    illustrations: color, white_margins=true, equalize_illumination=true, grayscale_first=true. Covers: color 300.
    white_margins / equalize_illumination / grayscale_first are the GUI's Color/Grayscale checkboxes
    ("Convert to grayscale first": the scan becomes grayscale before all other output steps, so a paper
    tint does not change the margins, the illumination or the photos, and the output TIFF is 8-bit gray;
    no effect in bw mode).
    filling_color (background|white|black) fills the area outside the page; fill_offcut also fills the
    area beyond the split line (only without white margins). These three need a ScanTailor build from
    branch agentic/windows-build; older builds ignore them."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    ids = _resolve_pages(p, pages)
    for i in ids:
        p.set_output(i, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color, fill_offcut,
                     grayscale_first)
    p.save()
    return {"updated_pages": len(ids), "color_mode": color_mode, "dpi": dpi, "despeckle": despeckle,
            "white_margins": white_margins, "equalize_illumination": equalize_illumination,
            "filling_color": filling_color, "fill_offcut": fill_offcut, "grayscale_first": grayscale_first}


def check_pages(project: str, pages: str | list[str] = "all", kind: str = "content") -> dict:
    """Heuristic checks with evidence. kind=content: ink outside the box above/below (missed page numbers,
    headers) and left/right (cut-off text, marginalia), isolated junk at the box's top/bottom lines and
    left/right sides, empty boxes on pages with ink, and where page numbers sit.
    kind=pictures: pages with illustrations: picture_fraction (grayscale/halftone areas) and
    line_art_fraction (dense black-ink drawings); each flagged page has `likely` and must be previewed."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)
    if kind == "pictures":
        res = [checks.picture_score(p, i) for i in ids]
        flagged = [r for r in res if r["picture_fraction"] > 0.05 or r["line_art_fraction"] > 0.01]
        for r in flagged:
            r["likely"] = "grayscale" if r["picture_fraction"] > 0.05 else "line_art"
        return {"checked": len(res), "flagged": flagged,
                "rule": "preview each: grayscale illustrations (photos, halftones) -> color_mode=color, "
                        "white_margins=true, equalize_illumination=true, grayscale_first=true; "
                        "black-and-white line art -> keep bw 600"}
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
    missing = [pg.label for pg in p.pages if p.output_name(pg.id) not in files]
    return {"output_dir": str(out), "tif_files": len(files), "missing": missing[:50],
            "total_mb": round(sum(f.stat().st_size for f in files.values()) / 1e6, 1)}


def pages_straighten_pictures(project: str, pages: str | list[str], min_change: float = 0.25) -> dict:
    """Picture pages (photos, plates): ScanTailor deskews by text lines, which photos do not have, so it
    can tilt them by several degrees. Re-measures the skew from the printed rectangles' edges and sets
    a manual deskew angle. Afterwards run_stages(3, 5), then page_set_content(mode=all_content)."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    changed = []
    for i in _resolve_pages(p, pages):
        res = checks.picture_skew(p, i)
        if res is not None and abs(res) >= min_change:
            old = p.deskew_angle(i)
            p.set_deskew(i, old - res)
            changed.append({"page": p.page(i).label, "angle_before": round(old, 3), "angle_after": round(old - res, 3)})
    p.save()
    return {"changed": changed, "next": "run_stages(3, 5), then page_set_content(mode=all_content) on these pages"}


# The copy always holds the last finished run, so that project_diff shows only the corrections of the person.
def _keep_original(proj: Path) -> dict:
    orig = proj.with_name(proj.stem + ".orig" + proj.suffix)
    replaced = orig.exists()
    shutil.copy2(proj, orig)
    return {"path": str(orig), "replaced_older_copy": replaced}


def project_diff(original: str, corrected: str, limit: int = 60) -> dict:
    """Compare the project the agent made (e.g. agentas.orig.ScanTailor) with the same project after a
    person corrected it in the GUI. Lists, per kind, the pages that differ: images removed or added,
    rotation, split, deskew angle, content box and mode, empty pages, margins, alignment, match size,
    output mode and DPI. Use it to learn rules for the workflow from the corrections."""
    a, b = Project(Path(original).resolve()), Project(Path(corrected).resolve())

    def page_state(p: Project, pg) -> dict:
        c, lay, out = p.content(pg.id), p.layout(pg.id), p.output(pg.id)
        rect = c.get("content_rect_px")
        return {
            "rotation": p.rotation(pg.image_id),
            "deskew": round(p.deskew_angle(pg.id), 2),
            "content_mode": c.get("mode"),
            "content": None if not rect else [round(rect[k]) for k in ("x", "y", "width", "height")],
            "empty": c.get("empty"),
            "margins": None if not lay.get("processed") else [round(v, 1) for v in lay["margins_mm"].values()],
            "align": None if not lay.get("processed") else lay.get("align_v"),
            "match_size": lay.get("match_size") if lay.get("processed") else None,
            "output": (out.get("color_mode"), out.get("dpi")),
        }

    ia = {im.path.name for im in a.images.values()}
    ib = {im.path.name for im in b.images.values()}
    # A label repeats for pages of one multi-page file, so the key adds the file page and the side.
    def keyed(p: Project) -> dict:
        out = {}
        for pg in p.pages:
            out[(pg.image.path.name, pg.image.file_page, pg.side)] = pg
        return out

    ka, kb = keyed(a), keyed(b)
    la = {pg.label for pg in ka.values()}
    lb = {pg.label for pg in kb.values()}
    by_kind: dict[str, list] = {}
    for key in sorted(set(ka) & set(kb), key=lambda x: ka[x].index):
        pa, pb = ka[key], kb[key]
        sa, sb = page_state(a, pa), page_state(b, pb)
        name = pa.label if not pa.image.file_page else f"{pa.label} (page {pa.image.file_page})"
        for k in sa:
            if sa[k] != sb[k]:
                by_kind.setdefault(k, []).append({"page": name, "agent": sa[k], "user": sb[k]})
    return {
        "images_removed": sorted(ia - ib), "images_added": sorted(ib - ia),
        "pages_only_in_original": sorted(set(la) - set(lb)), "pages_only_in_corrected": sorted(set(lb) - set(la)),
        "counts": {k: len(v) for k, v in by_kind.items()},
        "differences": {k: v[:limit] for k, v in by_kind.items()},
    }


def project_for_windows(project: str, relative: bool = False) -> dict:
    """Last step when the project was processed under WSL: rewrite /mnt/<drive>/... paths in the
    project (scan folder, output folder) to Windows form (C:/..., C:\\...) so the Windows GUI opens it.
    Run it only after all stages are done (the Linux CLI cannot use the rewritten project).
    relative=true instead writes the folders inside the project folder as relative paths ("."
    "out-agentas"): such a project opens under WSL and Windows alike, but only in ScanTailor builds from
    branch agentic/windows-build (2026-11-21 or later); older builds read relative paths from the
    current directory."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    if relative:
        changed = []
        targets = [(p.root, "outputDirectory")] + [(d, "path") for d in p.root.findall("./directories/directory")]
        for el, attr in targets:
            here = p.local_path(el.get(attr, ""))
            try:
                rel = here.resolve().relative_to(proj.parent).as_posix()
            except ValueError:
                continue
            new = rel if rel not in ("", ".") else "."
            if new != el.get(attr):
                el.set(attr, new)
                changed.append(new)
        p.save()
        return {"project": str(proj), "rewritten": changed, "original_copy": _keep_original(proj),
                "note": "relative paths: the project opens under WSL and Windows, in builds from branch "
                        "agentic/windows-build only; never delete the .orig copy"}

    def win(path: str, backslash: bool) -> str:
        m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path or "")
        if not m:
            return path
        rest = m.group(2).replace("/", "\\") if backslash else m.group(2)
        sep = "\\" if backslash else "/"
        return f"{m.group(1).upper()}:{sep}{rest}"

    changed = []
    out = p.root.get("outputDirectory", "")
    if win(out, True) != out:
        p.root.set("outputDirectory", win(out, True))
        changed.append(p.root.get("outputDirectory"))
    for d in p.root.findall("./directories/directory"):
        new = win(d.get("path", ""), False)
        if new != d.get("path"):
            d.set("path", new)
            changed.append(new)
    p.save()
    return {"project": str(proj), "rewritten": changed, "original_copy": _keep_original(proj),
            "note": "never delete the .orig copy: project_diff compares it with the corrections of the user; "
                    "open this file in the Windows GUI; to process it again under WSL, run_stages refuses "
                    "Windows paths, so re-run on Windows or convert back"}


WORKFLOW = (Path(__file__).resolve().parents[3] / "docs" / "AGENTIC_WORKFLOW.en.md")


def workflow_guide() -> dict:
    """The step-by-step workflow the agent should follow (mirrors the manual GUI procedure)."""
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else "(workflow doc missing)"
    return {"workflow": text}


TOOLS = [project_create, project_for_windows, project_diff, pages_straighten_pictures, images_set_geometry, run_stages, job_status, project_info, pages_list, page_preview,
         pages_preview, image_preview, page_set_content, pages_snap_content, pages_set_layout, pages_fit_width, pages_align_auto, pages_set_output,
         check_pages, export_summary, workflow_guide]
IMAGE_TOOLS = {"page_preview", "pages_preview", "image_preview"}
