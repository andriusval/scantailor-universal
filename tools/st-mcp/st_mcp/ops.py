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
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from PIL import Image

from . import checks, corrections, review
from .masks import cli_masks_available, dump_masks
from .preview import contact_sheet, grid_view, render_image, render_page
from .project import COLOR_MODES, Project

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


# Half of the cores leaves CPU time for the agent and the previews, and each parallel page needs its own memory.
def cli_threads() -> int:
    value = os.environ.get("ST_CLI_THREADS", "").strip()
    if not value:
        return max(1, (os.cpu_count() or 2) // 2)
    if value == "auto":
        return 0
    if not value.isdigit():
        raise ValueError(f"ST_CLI_THREADS={value!r}: use a number from 0, or auto")
    return int(value)


def _cli_threads_args() -> list[str]:
    # Older CLI builds stop on an unknown option, so pass it only when the help lists it.
    return [f"--threads={cli_threads()}"] if _cli_supports("threads") else []


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
        self.grayscale_auto: list[dict] = []
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
            if "grayscale_detected" in ev:
                chroma = ev.get("chroma")
                margin = None if chroma is None else round(float(chroma) - checks.GRAY_CHROMA_THRESHOLD, 1)
                self.grayscale_auto.append({
                    "image": ev.get("image"), "side": ev.get("side"),
                    "chroma": None if chroma is None else round(float(chroma), 1),
                    "grayscale_detected": ev.get("grayscale_detected"), "margin": margin,
                    "confidence": "low" if margin is None or abs(margin) < checks.GRAY_CHROMA_NEAR else "high"})
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
        if self.grayscale_auto:
            out["grayscale_auto"] = self.grayscale_auto
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
    cmd = [find_cli(), "--end-filter=1", f"--output-project={proj}", *_cli_threads_args()]
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
               pages: str | list[str] | None = None, output_rotate: str | None = None) -> dict:
    """Run ScanTailor filters start..end (1 orientation, 2 split, 3 deskew, 4 content, 5 margins, 6 output)
    on all pages, in the background. Per-page manual settings in the project are kept.

    orientation/layout/... are applied to ALL pages when given (use only on the first run or to reset).
    orientation='auto' finds the rotation of each scan from its text (CLI --orientation=auto); a scan
    without a clear result keeps its rotation. output_rotate='auto' (stage 6) finds the output rotation
    of each page from the finished page, for sideways tables; use it with pages=<those pages>.
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
    cmd = [find_cli(), "--verbose", f"--start-filter={start}", f"--end-filter={end}", *_cli_threads_args()]
    if orientation:
        cmd.append(f"--orientation={orientation}")
    if output_rotate is not None:
        if str(output_rotate) == "auto" and not _cli_supports("detect-orientation"):
            raise RuntimeError(f"{find_cli()} does not know --output-rotate=auto: build a newer CLI")
        cmd.append(f"--output-rotate={output_rotate}")
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


def pages_snap_content(project: str, pages: str | list[str] = "", sides: str | list[str] = "auto",
                       points: list[dict] | None = None, wait_s: float = 0) -> dict:
    """Shrink content boxes to the text with ScanTailor's own double-click adjustment (the GUI
    feature from ScanTailor Advanced, run through the CLI option --content-snap): for each side, a
    'double-click' on the outermost text makes ScanTailor snap that box edge to the ink there, so
    junk between the edge and the text drops out. Use it when check_pages reports junk at the box
    sides (thin_*_column_isolated, thin_*_line_isolated, box_exceeds_text).
    sides: 'auto' (the sides check_pages flags as junk), 'all', or a list of left|right|top|bottom.
    points: your own double-clicks instead of pages/sides, a list of {page, x, y, side} in page px
    (page_preview coordinates). Put the point on the outermost PRINTED text of that side, for example
    the first letter of a line for side=left, or the page number for side=bottom: the edge then snaps to
    that text, and a stamp, a sticker or handwriting beyond it goes out of the box. Use it for the pages
    of check_pages(kind="size"). A point outside the box adds the ink there to the box.
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

    for pt in points or []:
        pg = p.find_page(str(pt["page"]))
        side = pt.get("side", "nearest")
        if pg.image.file_page > 0:
            skipped.append({"page": pg.label, "why": "multi-page image files are not supported"})
            continue
        sub_ = {"left": ":L", "right": ":R"}.get(pg.side, "")
        reqs.append(f"{image_ref(pg.image.path)}{sub_}@{float(pt['x'])},{float(pt['y'])}:{side}")
        prev = next((x for x in plan if x["page"] == pg.label), None)
        if prev is None:
            prev = {"page": pg.label, "snaps": []}
            plan.append(prev)
        prev["snaps"].append({"side": side, "at": {"x": pt["x"], "y": pt["y"]}})
    for i in (_resolve_pages(p, pages) if pages and not points else []):
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
    with review.Notes(p) as notes:
        for x, pid in zip(plan, snapped_ids):
            notes.add(pid, "content", "edges snapped to the text: " + ", ".join(s["side"] for s in x["snaps"]),
                      0.75, "points chosen on the printed text" if points else "junk at the box sides (check_pages)")
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
        cmd = [find_cli(), "--start-filter=4", "--end-filter=4", *_cli_threads_args(),
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


def page_preview(project: str, page: str, max_px: int = 1200, engine: str = "auto", grid: bool = False,
                 roi: list[float] | None = None, zones: list[list[float]] | None = None) -> Image.Image:
    """The page as in the GUI's Select Content view (deskewed, cropped) with the content box.
    engine: 'auto' (ScanTailor draws it through CLI --render-preview when available, else Python),
    'cli' or 'python' (fast, st-mcp's own copy of the geometry).
    grid=true: a page px grid with labels (a line each 25, 50 or 100 px, a number each 100 px), the
    content box in green and the fill zones of the project in blue. Read the points for
    pages_snap_content(points=...) and the rectangles for page_add_fill_zone from it.
    roi=[x0, y0, x1, y1] (page px, implies grid): a close view of that part, for exact coordinates.
    zones=[[x0, y0, x1, y1], ...] (implies grid): draw planned fill zones in red, to check before
    page_add_fill_zone that no zone touches printed text."""
    p = _proj(project)
    pid = p.find_page(page).id
    if grid or roi or zones:
        return grid_view(p, pid, tuple(roi) if roi else None, max_px if roi else min(max_px, 1000),
                         max(max_px, 1400) if not roi else max_px, zones)
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
    note = None
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
    with review.Notes(p) as notes:
        if mode == "auto":
            notes.clear([pg.id], "content")
        else:
            conf, why = _content_confidence(mode, pg, before, rect if mode != "empty" else None, note)
            notes.add(pg.id, "content", f"content box: {mode}", conf, why)
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


# Modes that find a clear edge or band get a higher confidence than the modes that only guess.
def _content_confidence(mode: str, pg, before: dict, rect: dict | None, kept) -> tuple[float, str]:
    pr = before.get("page_rect_px") or {}
    if mode == "cover" and rect and pr:
        frac = rect["width"] * rect["height"] / max(1.0, pr["width"] * pr["height"])
        if frac > 0.97:
            return 0.5, "no clear cover edge: the box covers almost the whole scan"
        return 0.8, "cover found on the darker scanner background"
    if mode == "tighten":
        return (0.5, f"sides kept or widened: {', '.join(dict.fromkeys(kept))}") if kept else \
            (0.8, "box fitted to the text block")
    return {"include_numbers": (0.8, "page number band found outside the box"),
            "all_content": (0.7, "box around all printed parts of a picture page"),
            "empty": (0.75, "blank leaf: only specks or bleed-through"),
            "full": (0.8, "whole page, set from a preview"),
            "manual": (0.8, "set by the agent from a preview")}.get(mode, (0.7, ""))


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


def pages_fit_width(project: str, margin_mm: float = 10.0, percentile: float = 50.0) -> dict:
    """One page width for the whole book: the typical text-block width plus margin_mm on each side.
    Pages whose content is wider than the typical width get left/right margins 0. With match size and
    horizontal centering they are still centered on the common page, and a later small change of their
    content box (a page number, a snap) does not make every page wider.
    The typical width is the given percentile (default: the median) of the content widths of the text
    pages (B/W body pages; covers, picture pages and pages with match size off do not count).
    Top/bottom margins and alignment do not change. Pages with left or right alignment keep their
    side margins, because pages_align_horizontal sets them. Run it after the last content box change,
    then run_stages(5, 5). The person's corrections of 4 books follow this rule."""
    import statistics
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    no_layout: list[str] = []
    body = _body_pages(p, no_layout)
    if len(body) < 3:
        raise RuntimeError("too few pages with content and page layout; run stages 1-5 first")
    text = [(pid, c) for pid, c in body if p.output(pid).get("color_mode") in (None, "bw")]
    # Picture pages have their own widths, so they set the typical width only in a book of pictures.
    ref = text if len(text) >= 3 else body
    widths = sorted(c["content_mm"]["width"] for _, c in ref)
    typical = widths[min(len(widths) - 1, int(round(percentile / 100.0 * (len(widths) - 1))))]
    page_w = round(typical + 2 * margin_mm, 1)
    changed, changed_ids, too_wide, kept_aligned = [], [], [], []
    for pid, c in body:
        w = c["content_mm"]["width"]
        lr = margin_mm if w <= typical else 0.0
        lay = p.layout(pid)
        cur = lay["margins_mm"]
        # pages_align_horizontal gives these pages one margin on the aligned side, and the minimum of both sides would remove it.
        if lay.get("align_h") in ("left", "right"):
            kept_aligned.append(p.page(pid).label)
            continue
        # Smaller margins set on purpose (picture pages: 0) stay, because they never widen the page.
        lr = min(lr, cur["left"], cur["right"])
        if abs(cur["left"] - lr) > 0.05 or abs(cur["right"] - lr) > 0.05:
            p.set_layout(pid, {"left": lr, "right": lr})
        if lr != margin_mm:
            changed.append({"page": p.page(pid).label, "content_width_mm": w, "left_right_mm": lr})
            changed_ids.append(pid)
        if w > page_w:
            too_wide.append(p.page(pid).label)
    p.save()
    with review.Notes(p) as notes:
        notes.clear([pid for pid, _ in body], "page_width")
        for c, pid in zip(changed, changed_ids):
            if c["content_width_mm"] > page_w:
                notes.add(pid, "page_width", "side margins 0", 0.4,
                          f"content {c['content_width_mm']} mm is wider than the page width {page_w} mm")
            elif c["content_width_mm"] > typical:
                notes.add(pid, "page_width", "side margins 0", 0.8,
                          f"content {c['content_width_mm']} mm, wider than the typical {typical} mm")
    return {"typical_width_mm": typical, "skipped_no_page_layout": no_layout, "page_width_mm": page_w,
            "median_width_mm": statistics.median(widths), "zero_side_margins": len(changed),
            "wider_than_page": too_wide, "narrowed_margins": changed[:40],
            "kept_horizontal_alignment": kept_aligned,
            "next": "run_stages(5, 5); if a content box changes later, run pages_fit_width again"
                    + (", then pages_align_horizontal again" if kept_aligned else "")}


def pages_fit_text_column(project: str, tolerance_mm: float = 0.5, height_ratio: float = 0.015) -> dict:
    """Narrow the content boxes that are wider than the book's text column, keeping top and bottom.
    The column width is the median content width of the B/W body pages. On each page wider than it by
    more than tolerance_mm, the left and right edges move to the text column (justified edge), as in
    page_set_content(mode=tighten): pencil marks, stamps, inventory numbers and specks in the side
    margin drop out, while ink that continues a text line (hyphens, punctuation) stays in.
    Pages taller than the median by more than height_ratio are only listed (taller_pages, review notes):
    a stamp or a mark above or below the text is likely there, so preview them and fix the box with
    page_set_content(mode=manual). Run it after stage 5 (it needs the content sizes), then
    run_stages(4, 5) and pages_fit_width. The person's corrections of 4 books follow this rule."""
    import statistics
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    body = [(pid, c) for pid, c in _body_pages(p) if p.output(pid).get("color_mode") in (None, "bw")]
    if len(body) < 3:
        raise RuntimeError("too few text pages with content and page layout; run stages 1-5 first")
    med_w = statistics.median(c["content_mm"]["width"] for _, c in body)
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)
    narrowed, kept, taller = [], [], []
    with review.Notes(p) as notes:
        for pid, c in body:
            lab = p.page(pid).label
            mm, rect = c["content_mm"], c["content_rect_px"]
            if mm["height"] > med_h * (1 + height_ratio):
                taller.append({"page": lab, "height_mm": mm["height"]})
                notes.add(pid, "content_height", "box taller than the text block", 0.5,
                          f"{mm['height']} mm against the median {med_h} mm: a stamp or a mark above or below the text?")
            if mm["width"] <= med_w + tolerance_mm:
                continue
            px_per_mm = rect["width"] / mm["width"]
            t = checks.tighten_rect(p, pid, column_px=med_w * px_per_mm)
            if t is None:
                continue
            x0, x1 = max(rect["x"], t["x"]), min(rect["x"] + rect["width"], t["x"] + t["width"])
            sides = [s for s in t.get("kept_sides", []) if ":" not in s]
            # Less than 1 px per side is noise, so such a page keeps its box.
            if x0 - rect["x"] < 1 and rect["x"] + rect["width"] - x1 < 1:
                if sides:
                    kept.append({"page": lab, "width_mm": mm["width"], "kept_sides": sides})
                continue
            p.set_content(pid, {"x": x0, "y": rect["y"], "width": x1 - x0, "height": rect["height"]})
            new_w = round((x1 - x0) / px_per_mm, 1)
            # A cut of more than 3 mm on one side can remove a side note, so the preview must confirm it.
            big = new_w < med_w - 2 or max(x0 - rect["x"], rect["x"] + rect["width"] - x1) > 3 * px_per_mm
            narrowed.append({"page": lab, "width_mm_before": mm["width"], "width_mm_after": new_w,
                             "cut_px_left": round(x0 - rect["x"]), "cut_px_right": round(rect["x"] + rect["width"] - x1),
                             "large_cut": bool(big)})
            notes.add(pid, "content", "content box: text column", 0.55 if big else 0.8,
                      f"{mm['width']} -> {new_w} mm, text column {med_w} mm" + ("; large cut: preview" if big else ""))
        p.save()
    return {"text_column_mm": med_w, "median_height_mm": med_h, "narrowed": narrowed, "kept_sides": kept,
            "taller_pages": taller,
            "next": "run_stages(4, 5), preview the large cuts and the taller pages, then pages_fit_width"}


def pages_align_auto(project: str, number_position: str = "auto", full_ratio: float = 0.985,
                     title_ratio: float = 0.5, sparse_ratio: float = 0.6, line_tolerance_mm: float = 5.0,
                     picture_ratio: float = 0.25, review_full_pages: bool = False) -> dict:
    """Vertical alignment for every body page by the groups of a printed book, and lists of pages to check.
    The groups come from the content height against the book median and from the place of the text
    on the page (the usual top and bottom lines of the full pages, within line_tolerance_mm; text above
    the usual top line or below the usual bottom line also counts as on that line):
    - Full pages (height at least full_ratio of the median, or text within half the tolerance of both
      usual lines): to the page-number side, top or bottom. number_position=auto takes the side from
      the page numbers of a sample of full text pages: top needs 3 votes and 3 times the bottom votes.
    - Picture pages (color/grayscale output, or B/W line art on picture_ratio of the content): vcenter.
    - Front matter (the pages before the first full text page): sparse pages (title, half-title,
      epigraph, dedication) and blocks lower than title_ratio of the median: vcenter. A page is sparse
      when its lines per mm are below half of the full pages, or its share of box rows with ink is below
      sparse_ratio of the full pages.
    - Text that starts on the usual top line: top. These are chapter ends (any height) and full text
      pages without the page-number band.
    - Text that starts lower and ends on the usual bottom line, at least title_ratio of the median high:
      bottom. These are chapter starts; the ones far from the usual chapter-start height go to review.
    - All other pages (small blocks off the usual lines: chapter or part titles, colophons): vcenter.
    The agent previews the pages a bit lower than the median, checks the groups, and corrects single
    pages with pages_set_layout; the rules stay the same for the whole book.
    Lists for the report (nothing is removed): blank pages, half-title candidates, near-empty pages,
    and, with review_full_pages=true, full-height text pages with few text lines (rotated tables:
    vcenter is usual there; about 0.5 s per page). Covers keep their settings. Then run_stages(5, 5)."""
    import statistics
    if number_position not in ("auto", "top", "bottom"):
        raise ValueError("number_position must be auto|top|bottom")
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    no_layout: list[str] = []
    body = _body_pages(p, no_layout)
    if len(body) < 3:
        raise RuntimeError("too few pages with content and page layout; run stages 1-5 first")
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)
    info: dict[str, dict] = {}

    def check(pid: str) -> dict:
        if pid not in info:
            info[pid] = checks.check_page(p, pid)
        return info[pid]

    def gaps_mm(c: dict) -> tuple[float, float] | None:
        cr, pr = c["content_rect_px"], c.get("page_rect_px")
        k = c["content_mm"]["height"] / max(1.0, cr["height"])
        if not pr:
            # Without a page rect the box position on the scan is the only measure, so compare it only with other such pages.
            return cr["y"] * k, -(cr["y"] + cr["height"]) * k
        return (cr["y"] - pr["y"]) * k, (pr["y"] + pr["height"] - cr["y"] - cr["height"]) * k

    # Picture plates have no page numbers and no text lines, so only text pages give the usual lines and the sample.
    full = [(pid, c) for pid, c in body if c["content_mm"]["height"] >= full_ratio * med_h
            and p.output(pid).get("color_mode") in (None, "bw")]
    # Left and right scans sit differently on the glass, so each side has its own usual lines.
    usual: dict[tuple[str, bool], tuple[float, float]] = {}
    for side in ("left", "right", "single"):
        for has_rect in (True, False):
            g = [gaps_mm(c) for pid, c in full if p.page(pid).side == side and bool(c.get("page_rect_px")) == has_rect]
            if g:
                usual[(side, has_rect)] = (statistics.median(a for a, _ in g), statistics.median(b for _, b in g))
    # A sample from the whole book gives the line count and the page-number side, because a full analysis takes long.
    n = min(30, len(full))
    sample = [full[i * len(full) // n][0] for i in range(n)]
    # Notes and index pages at the ends of a book often use a smaller font, so the medians come from the middle.
    med_sample = [pid for k, pid in enumerate(sample) if 0.1 * n <= k < 0.9 * n] or sample
    med_lines = statistics.median(check(pid).get("text_lines") or 0 for pid in med_sample) if sample else 0
    votes = [check(pid).get("page_number_in_box") for pid in sample]
    votes = {k: votes.count(k) for k in ("top", "bottom") if votes.count(k)}
    top_v, bottom_v = votes.get("top", 0), votes.get("bottom", 0)
    # One wrong vote can come from a speck, so only a clear majority moves the full pages to the top.
    auto_side = "top" if top_v >= 3 and top_v >= 3 * bottom_v else "bottom"
    number_side = number_position if number_position != "auto" else auto_side
    # The share of rows with ink does not change when tight lines merge, so it tells sparse pages from text better than the line count.
    med_ink = statistics.median(check(pid).get("ink_rows") or 0 for pid in med_sample) if sample else 0.0

    line_density = med_lines / med_h if med_h else 0.0

    def sparse(r: dict, h: float) -> bool:
        # Merged tight lines fool the line count, and pictures fool the ink rows, so either one alone marks a sparse page.
        return ((r.get("text_lines") or 0) / max(h, 1.0) < 0.5 * line_density
                or (r.get("ink_rows") or 0) < sparse_ratio * med_ink)

    # Title pages come before the first full text page, so only that front matter needs the sparse test.
    front_end = next((i for i, (pid, c) in enumerate(body[: max(5, len(body) // 10)])
                      if c["content_mm"]["height"] >= full_ratio * med_h
                      and not sparse(check(pid), c["content_mm"]["height"])), 0)
    groups = {"number_side": [], "vcenter_picture": [], "vcenter_sparse": [], "vcenter_front": [], "top_no_number_band": [],
              "top_chapter_end": [], "bottom_chapter_start": [], "vcenter_small": [], "top_no_page_rect": []}
    starts: list[tuple[str, float]] = []
    review_ids: dict[str, str] = {}
    half_titles, near_empty, full_few_lines = [], [], []
    for i, (pid, c) in enumerate(body):
        h = c["content_mm"]["height"]
        is_text = p.output(pid).get("color_mode") in (None, "bw")
        if h >= full_ratio * med_h:
            key = "vcenter_picture" if not is_text else "number_side"
            # Picture plates already have their own output mode, so only text-mode pages need this review.
            if review_full_pages and is_text and (check(pid).get("text_lines") or 0) < 0.5 * med_lines:
                full_few_lines.append(pid)
        elif not is_text:
            key = "vcenter_picture"
        else:
            r = check(pid)
            lines = r.get("text_lines") or 0
            g, u = gaps_mm(c), usual.get((p.page(pid).side, bool(c.get("page_rect_px"))))
            if c["content_mm"]["width"] * h < 0.01 * med_h * med_h and lines == 0:
                near_empty.append(pid)
            if checks.picture_score(p, pid).get("line_art_fraction", 0.0) >= picture_ratio:
                key = "vcenter_picture"
            elif i < front_end and sparse(r, h):
                key = "vcenter_sparse"
                if lines <= 5:
                    half_titles.append(pid)
            elif i < front_end and h < title_ratio * med_h:
                key = "vcenter_front"
            elif u is None:
                key = "top_no_page_rect"
            # The page-number band is only a few mm high, so a page counts as full only when it is close to both lines.
            elif g[0] - u[0] <= line_tolerance_mm / 2 and g[1] - u[1] <= line_tolerance_mm / 2:
                key = "number_side"
            elif g[0] - u[0] <= line_tolerance_mm:
                key = "top_no_number_band" if h >= 0.9 * med_h and lines >= 0.85 * med_lines else "top_chapter_end"
            elif g[1] - u[1] <= line_tolerance_mm and h >= title_ratio * med_h:
                key = "bottom_chapter_start"
                starts.append((pid, h))
            else:
                key = "vcenter_small"
                if h >= title_ratio * med_h:
                    review_ids[pid] = "text block off the usual lines: a chapter start with a cut box?"
            # A rotated table often has a box a bit lower than the text block, so near-full pages need the review too.
            if (review_full_pages and h >= 0.9 * med_h and key in ("number_side", "top_no_number_band")
                    and lines < 0.5 * med_lines):
                full_few_lines.append(pid)
        groups[key].append(pid)
    # Chapter starts of one book have about the same height, so an outlier is often a wrong group or a wrong box.
    start_h = statistics.median(h for _, h in starts) if starts else 0.0
    for pid, h in starts:
        if abs(h - start_h) > 0.1 * med_h:
            review_ids[pid] = f"chapter start {h:.0f} mm high, other chapter starts {start_h:.0f} mm"

    align_of = {"number_side": number_side, "vcenter_picture": "vcenter", "vcenter_sparse": "vcenter", "vcenter_front": "vcenter",
                "top_no_number_band": "top", "top_chapter_end": "top", "bottom_chapter_start": "bottom",
                "vcenter_small": "vcenter", "top_no_page_rect": "top"}
    why = {"number_side": (0.8, f"full page: aligned to the page-number side ({number_side})"),
           "vcenter_picture": (0.8, "picture page"),
           "vcenter_sparse": (0.7, "sparse page: title, half-title, epigraph or dedication"),
           "vcenter_front": (0.7, "small block in the front matter: title or half-title"),
           "top_no_number_band": (0.7, "full text page without the page-number band: starts on the usual top line"),
           "top_chapter_end": (0.8, "chapter end: starts on the usual top line"),
           "bottom_chapter_start": (0.7, "chapter start: starts lower and ends on the usual bottom line"),
           "vcenter_small": (0.6, "small block off the usual lines: chapter title, part title or colophon"),
           "top_no_page_rect": (0.4, "no full page of this side gives the usual lines")}
    for key, ids in groups.items():
        for pid in ids:
            p.set_layout(pid, None, align_of[key], None, None)
    p.save()
    blank_ids = [pg.id for pg in p.pages if p.content(pg.id).get("empty")]
    with review.Notes(p) as notes:
        notes.clear([pid for pid, _ in body], "alignment")
        notes.clear([pid for pid, _ in body] + blank_ids, "removal")
        for key, ids in groups.items():
            conf, reason = why[key]
            for pid in ids:
                if pid in review_ids:
                    notes.add(pid, "alignment", f"align {align_of[key]}", 0.4, review_ids[pid])
                elif pid in full_few_lines:
                    notes.add(pid, "alignment", f"align {align_of[key]}", 0.4, SIDEWAYS_DOUBT)
                elif key != "number_side":
                    notes.add(pid, "alignment", f"align {align_of[key]}", conf, reason)
        for pid, reason in ([(x, "blank page") for x in blank_ids] + [(x, "half-title candidate") for x in half_titles]
                            + [(x, "near-empty page: specks or a stamp?") for x in near_empty]):
            notes.add(pid, "removal", "removal candidate (not removed)", 0.4, reason)

    def lab(ids: list[str]) -> list[str]:
        return [p.page(i).label for i in ids]

    return {"median_height_mm": med_h, "median_text_lines": med_lines,
            "number_position": number_side, "number_votes": votes,
            "chapter_start_height_mm": round(start_h, 1),
            "number_side": len(groups["number_side"]),
            **{k: lab(v) for k, v in groups.items() if k != "number_side"},
            "skipped_no_page_layout": no_layout,
            "review": {"pages": {p.page(i).label: r for i, r in review_ids.items()},
                       "full_height_few_lines": lab(full_few_lines),
                       "note": "preview the pages lower than the median and the review pages; "
                               "rotated tables: vcenter is usual"},
            "report_removal_candidates": {"blank_pages": lab(blank_ids), "half_title_candidates": lab(half_titles),
                                          "near_empty_pages": lab(near_empty),
                                          "note": "list these in the final report; the user decides, nothing was removed"},
            "next": "run_stages(5, 5), then preview the top, vcenter and review pages"}


def pages_align_horizontal(project: str, margin_mm: float = 10.0, far_margin_mm: float = 0.0,
                           line_tolerance_mm: float = 5.0, picture_ratio: float = 0.25) -> dict:
    """Horizontal alignment by the text edges, for books whose narrow pages keep the left (or right) line
    of the text column, such as poetry. Off by default. Use it only when the previews show that the
    narrow pages start on the same left line as the full pages. Most prose books do not need it.
    The usual left and right lines come from the text pages after the front matter that are at least as
    wide as the median (one pair per page side).
    A text page whose left edge is on the usual left line (within line_tolerance_mm, or further out) goes
    to left. A page only on the usual right line goes to right.
    These pages get margin_mm on the aligned side, so their text starts at the same place in the output.
    The other side gets far_margin_mm (default 0), because ScanTailor puts all extra width there.
    Thus the common page width grows only by one margin over the widest aligned text.
    A box that reaches past the line by more than the tolerance gets a smaller margin by that amount.
    Other pages (centered titles, short centered blocks) get hcenter. Their side margins are at most
    margin_mm, and they never make the page wider than the aligned pages need.
    Front matter before the first full text page, pages without a usual line, covers, color pages, B/W
    line-art pages (line art on picture_ratio of the content) and pages without match size keep their
    settings. If an earlier run aligned such a page left or right, it goes back to hcenter with a note.
    Run it after pages_fit_width, then run_stages(5, 6). After a later pages_fit_width, run it again."""
    import statistics
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    no_layout: list[str] = []
    body = [(pid, c) for pid, c in _body_pages(p, no_layout) if p.output(pid).get("color_mode") in (None, "bw")]
    if len(body) < 3:
        raise RuntimeError("too few text pages with content and page layout; run stages 1-5 first")
    med_w = statistics.median(c["content_mm"]["width"] for _, c in body)
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)

    def gaps_mm(c: dict) -> tuple[float, float]:
        cr, pr = c["content_rect_px"], c.get("page_rect_px")
        k = c["content_mm"]["width"] / max(1.0, cr["width"])
        if not pr:
            # Without a page rect the box position on the scan is the only measure, so compare it only with other such pages.
            return cr["x"] * k, -(cr["x"] + cr["width"]) * k
        return (cr["x"] - pr["x"]) * k, (pr["x"] + pr["width"] - cr["x"] - cr["width"]) * k

    # Title pages come before the first full text page, and they stay centered.
    front_end = next((i for i, (pid, c) in enumerate(body[: max(5, len(body) // 10)])
                      if c["content_mm"]["width"] >= 0.9 * med_w and c["content_mm"]["height"] >= 0.9 * med_h), 0)
    # Title pages have their own edges, so the usual lines come only from the pages after them.
    wide = [(pid, c) for pid, c in body[front_end:] if c["content_mm"]["width"] >= med_w]
    usual: dict[tuple[str, bool], tuple[float, float]] = {}
    for side in ("left", "right", "single"):
        for has_rect in (True, False):
            g = [gaps_mm(c) for pid, c in wide if p.page(pid).side == side and bool(c.get("page_rect_px")) == has_rect]
            if g:
                usual[(side, has_rect)] = (statistics.median(a for a, _ in g), statistics.median(b for _, b in g))
    groups: dict[str, list[str]] = {"left": [], "right": [], "hcenter": [], "front": [], "picture": [],
                                    "no_usual_line": []}
    spread: dict[str, list[float]] = {"left": [], "right": []}
    margins_of: dict[str, dict[str, float]] = {}
    reduced: list[str] = []
    for i, (pid, c) in enumerate(body):
        u = usual.get((p.page(pid).side, bool(c.get("page_rect_px"))))
        if i < front_end:
            groups["front"].append(pid)
            continue
        # A line-art plate is not text, so its edges say nothing; plates can have the full text size, so every page gets the test.
        if checks.picture_score(p, pid).get("line_art_fraction", 0.0) >= picture_ratio:
            groups["picture"].append(pid)
            continue
        if u is None:
            groups["no_usual_line"].append(pid)
            continue
        g = gaps_mm(c)
        d_left, d_right = g[0] - u[0], g[1] - u[1]
        if d_left <= line_tolerance_mm:
            key, near, far = "left", "left", "right"
            d = d_left
        elif d_right <= line_tolerance_mm:
            key, near, far = "right", "right", "left"
            d = d_right
        else:
            groups["hcenter"].append(pid)
            continue
        groups[key].append(pid)
        spread[key].append(d)
        margins = {near: margin_mm, far: far_margin_mm}
        # A box that reaches past the line (a numeral or a long first line) gets a smaller margin, so that the text line stays.
        if d < -line_tolerance_mm:
            margins[near] = max(0.0, margin_mm + d)
            reduced.append(pid)
        margins_of[pid] = margins
        p.set_layout(pid, margins, None, key, None)
    width_of = {pid: c["content_mm"]["width"] for pid, c in body}
    # With match size every page gets the width of the widest page, so the centered pages must fit in the width of the aligned pages.
    aligned = [width_of[pid] + m["left"] + m["right"] for pid, m in margins_of.items()]
    target_w = max(aligned) if aligned else med_w + 2 * margin_mm

    def centered_margin(pid: str) -> float:
        return round(min(margin_mm, max(0.0, (target_w - width_of[pid]) / 2)), 1)

    for pid in groups["hcenter"]:
        lay = p.layout(pid)
        m = centered_margin(pid)
        # A smaller margin from pages_fit_width or from a person stays, but an earlier left/right run does not count.
        if lay.get("align_h") not in ("left", "right"):
            m = min(m, lay["margins_mm"]["left"], lay["margins_mm"]["right"])
        p.set_layout(pid, {"left": m, "right": m}, None, "hcenter", None)
    # An earlier run with other settings can leave these pages left or right, with the margins of that run.
    reset: list[str] = []
    for pid in groups["front"] + groups["picture"] + groups["no_usual_line"]:
        lay = p.layout(pid)
        mm = lay["margins_mm"]
        if lay.get("align_h") in ("left", "right") and abs(mm["left"] - mm["right"]) > 0.05:
            m = centered_margin(pid)
            p.set_layout(pid, {"left": m, "right": m}, None, "hcenter", None)
            reset.append(pid)
    p.save()
    why = {"right": (0.6, "text ends on the usual right line of the wide pages"),
           "hcenter": (0.6, "text block off the usual left and right lines: a centered title or block")}
    with review.Notes(p) as notes:
        notes.clear([pid for pid, _ in body], "alignment_h")
        for key, (conf, reason) in why.items():
            for pid in groups[key]:
                notes.add(pid, "alignment_h", f"align {key}", conf, reason)
        for pid in reduced:
            m = margins_of[pid]
            notes.add(pid, "alignment_h", "smaller margin", 0.6,
                      f"box reaches past the usual line; margins {m['left']}/{m['right']} mm keep the text line")
        for pid in reset:
            notes.add(pid, "alignment_h", "back to hcenter", 0.5,
                      "an earlier run aligned this page left or right; now it is not a text page on the usual line")

    def lab(ids: list[str]) -> list[str]:
        return [p.page(i).label for i in ids]

    return {"median_width_mm": med_w, "margin_mm": margin_mm, "far_margin_mm": far_margin_mm,
            "aligned_page_width_mm": round(target_w, 1),
            "left": lab(groups["left"]), "right": lab(groups["right"]), "hcenter": lab(groups["hcenter"]),
            "smaller_margin": lab(reduced), "back_to_hcenter": lab(reset),
            "front_matter_kept": lab(groups["front"]), "pictures_kept": lab(groups["picture"]),
            "no_usual_line": lab(groups["no_usual_line"]),
            "left_offset_mm": [round(min(spread["left"]), 1), round(max(spread["left"]), 1)] if spread["left"] else None,
            "skipped_no_page_layout": no_layout,
            "next": "run_stages(5, 6), then preview the hcenter, right and smaller_margin pages in the output"}


def pages_set_output(project: str, pages: str | list[str], color_mode: str | None = None, dpi: int | None = None,
                     despeckle: str | None = None, white_margins: bool | None = None,
                     equalize_illumination: bool | None = None, filling_color: str | None = None,
                     fill_offcut: bool | None = None, grayscale_first: bool | str | None = None,
                     output_rotation: int | None = None) -> dict:
    """Output mode per page. color_mode: bw | color (=color/grayscale) | mixed.
    Text and pages with black-and-white line illustrations: bw 600. Pages with grayscale (halftone/photo)
    illustrations: color, white_margins=true, equalize_illumination=true, grayscale_first="auto".
    Color plates and covers: color, grayscale_first=false (covers 300 DPI).
    white_margins / equalize_illumination are the GUI's Color/Grayscale checkboxes. grayscale_first
    (true|false|"auto") is "Convert to grayscale first": the scan becomes grayscale before all other output
    steps, so a paper tint does not change the margins, the illumination or the photos, and the output TIFF
    is 8-bit gray; "auto" does it only when the picture has no color (check_pages(kind="grayscale")
    shows the measurement; job_status gives the stage 6 result in grayscale_auto). No effect in bw mode.
    filling_color (background|white|black) fills the area outside the page; fill_offcut also fills the
    area beyond the split line (only without white margins). Both work only in color mode; mixed mode
    ignores them. filling_color and fill_offcut need a
    ScanTailor build from branch agentic/windows-build, and grayscale_first (true|false|auto) needs a
    build with --grayscale-first; older builds ignore them and write RGB.
    output_rotation (0|90|180|270, clockwise) turns only the final output TIFF, for a table or picture
    printed sideways: text lines that run top-to-bottom need 270, bottom-to-top need 90. Stages 1-5 keep
    the book orientation; with 90/270 the TIFF is landscape. Run stage 6 on these pages afterwards.
    Needs a ScanTailor build with the CLI option --output-rotate."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    ids = _resolve_pages(p, pages)
    for i in ids:
        p.set_output(i, color_mode, dpi, despeckle, white_margins, equalize_illumination, filling_color, fill_offcut,
                     grayscale_first, output_rotation)
    p.save()
    # A B/W page is the default, so only the other output modes are decisions for the review.
    with review.Notes(p) as notes:
        for i in ids:
            o = p.output(i)
            if o.get("color_mode") == "bw":
                notes.clear([i], "output")
                continue
            notes.add(i, "output", f"{o.get('color_mode')} {o.get('dpi')} DPI"
                      + (", white margins" if o.get("white_margins") else "")
                      + (", equalize illumination" if o.get("equalize_illumination") else ""), 0.8,
                      "picture page or cover")
    return {"updated_pages": len(ids), "color_mode": color_mode, "dpi": dpi, "despeckle": despeckle,
            "white_margins": white_margins, "equalize_illumination": equalize_illumination,
            "filling_color": filling_color, "fill_offcut": fill_offcut, "grayscale_first": grayscale_first,
            "output_rotation": output_rotation}


SIZE_WIDTH_MM = 3.0
SIZE_HEIGHT_RATIO = 0.03


def _check_size(p: Project, ids: list[str]) -> dict:
    import statistics
    # Covers and picture pages have a color output, so only the B/W text pages give the median, as in pages_fit_text_column.
    body = [(pid, c) for pid, c in _body_pages(p) if p.output(pid).get("color_mode") in (None, "bw")]
    if len(body) < 3:
        raise RuntimeError("too few text pages with content and page layout; run stages 1-5 first")
    med_w = statistics.median(c["content_mm"]["width"] for _, c in body)
    med_h = statistics.median(c["content_mm"]["height"] for _, c in body)
    want = set(ids)
    over = []
    with review.Notes(p) as notes:
        notes.clear(ids, "size")
        for pid, c in body:
            if pid not in want:
                continue
            mm = c["content_mm"]
            dw, dh = round(mm["width"] - med_w, 1), round(mm["height"] - med_h, 1)
            reasons = []
            if dw > SIZE_WIDTH_MM:
                reasons.append(f"width +{dw} mm")
            if mm["height"] > med_h * (1 + SIZE_HEIGHT_RATIO):
                reasons.append(f"height +{dh} mm")
            if not reasons:
                continue
            item = {"page": p.page(pid).label, "page_no": p.page(pid).index + 1, "width_mm": mm["width"],
                    "height_mm": mm["height"], "over": ", ".join(reasons)}
            if dw > SIZE_WIDTH_MM:
                from . import zones
                # A suggested point needs no preview when the only thing beyond the text is a small ink group.
                stray = zones.stray_edges(zones.page_gray(p, pid), c["content_rect_px"])
                if stray:
                    item["stray_ink"] = stray
            over.append(item)
            notes.add(pid, "size", "box larger than the book median", 0.4,
                      f"{', '.join(reasons)} against {med_w} x {med_h} mm: narrow to the text, fill zone for a stamp")
    return {"median_mm": [med_w, med_h], "checked": len(want), "over_median": over,
            "rule": f"box wider than the median by more than {SIZE_WIDTH_MM} mm or taller by more than "
                    f"{SIZE_HEIGHT_RATIO:.0%}: preview each page. Snap the edge to the printed text with "
                    "pages_snap_content(points=...); a stamp or a note that stays inside the box gets page_add_fill_zone. "
                    "A page that is really larger (title picture, table, contents) stays, with a review_note."}


def _check_output(p: Project, ids: list[str]) -> dict:
    from collections import Counter
    from . import zones
    # Pages that match the book size must have one TIFF size per DPI, so another size shows a stale layout.
    out = p.output_dir
    missing, unreadable, sizes = [], [], {}
    for pid in ids:
        pg = p.page(pid)
        if p.content(pid).get("empty") and not (out / f"{pg.label}.tif").exists():
            continue
        f = out / f"{pg.label}.tif"
        if not f.exists():
            missing.append(pg.label)
            continue
        try:
            with Image.open(f) as im:
                im.load()
                size = im.size
                # A page turned by 90 or 270 degrees has width and height swapped, and that is not another size.
                if int(p.output(pid).get("output_rotation") or 0) % 180 == 90:
                    size = (size[1], size[0])
                sizes[pid] = (size, round(float((im.info.get("dpi") or (0, 0))[0])))
        except Exception as e:  # noqa: BLE001
            unreadable.append({"page": pg.label, "error": str(e)[:120]})
    groups: dict[int, Counter] = {}
    for pid, (size, dpi) in sizes.items():
        if p.layout(pid).get("match_size"):
            groups.setdefault(dpi, Counter())[size] += 1
    odd = []
    for pid, (size, dpi) in sizes.items():
        c = groups.get(dpi)
        if c and p.layout(pid).get("match_size") and size != c.most_common(1)[0][0]:
            odd.append({"page": p.page(pid).label, "size": list(size), "book_size": list(c.most_common(1)[0][0]),
                        "dpi": dpi})
    lost = []
    for pid in ids:
        c = p.content(pid).get("content_rect_px")
        if not c or c["width"] <= 0:
            continue
        for z in zones.zone_rects(p, pid):
            if z[2] < c["x"] or z[0] > c["x"] + c["width"] or z[3] < c["y"] or z[1] > c["y"] + c["height"]:
                lost.append({"page": p.page(pid).label, "zone_px": [round(v) for v in z]})
    ok = not (missing or unreadable or odd)
    return {"output_dir": str(out), "checked": len(ids), "ok": ok, "missing_tiff": missing,
            "unreadable_tiff": unreadable, "odd_size": odd, "zones_outside_box": lost,
            "rule": "odd_size: the page was written with an old page layout; run stages 5-6 for the whole book "
                    "with a CLI that saves the project. missing/unreadable: run stage 6 for these pages. "
                    "zones_outside_box: the zone has no effect; the mark is outside the box, or the zone is wrong."}


def page_add_fill_zone(project: str, page: str, x: float, y: float, width: float, height: float,
                       color: str = "#ffffff", fit: bool = False, dry_run: bool = False) -> dict:
    """Paint a rectangle of one page in a flat color in the output (Output filter fill zone), for a
    library stamp, a sticker or a handwritten note that stays inside the content box.
    x/y/width/height are page px, the same coordinates as page_preview and page_set_content.
    fit=true: x/y/width/height is a rough box around the mark; the zone shrinks to the ink in it, grows
    while ink touches its edges (a faint stamp edge, up to 150 px) and gets a 12 px pad. Check the result.
    The result lists crossings: ink on both sides of a zone edge in the same rows or columns. A crossing
    means that the zone cuts printed text, or that the mark is not fully covered. Do not keep a zone with a
    crossing on printed text: make it smaller, or split it, and preview again.
    dry_run=true: compute the zone and the crossings, and do not write the project.
    The zone is stored in original image px, so every later output run applies it and the GUI shows it
    (Output, Fill zones). A second call with the same rectangle adds nothing. Run run_stages(6, 6) after."""
    from . import zones
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    pg = p.find_page(page)
    rect = (x, y, x + width, y + height)
    gray = zones.page_gray(p, pg.id)
    if fit:
        fitted = zones.fit_to_ink(gray, rect)
        if fitted is None:
            raise RuntimeError(f"{pg.label}: no ink in the rough box {rect}")
        rect = fitted
    crossings = zones.edge_crossings(gray, rect, p.content(pg.id).get("content_rect_px"))
    added = False
    if not dry_run:
        inv = p.transform(pg.id).xform.inverted()
        x0, y0, x1, y1 = rect
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        added = p.add_fill_zone(pg.id, [inv.map(c) for c in corners], color)
        if added:
            p.save()
            # A reload proves that the zone is in the file, because we found a lost zone only after the output.
            if len(Project(proj).fill_zones(pg.id)) != len(p.fill_zones(pg.id)):
                raise RuntimeError(f"{pg.label}: the zone is not in the saved project")
    return {"page": pg.label, "zone_px": [round(v) for v in rect], "added": bool(added), "dry_run": dry_run,
            "zones": len(p.fill_zones(pg.id)), "crossings": crossings,
            "next": ("fix the zone: it cuts ink at " + ", ".join(c["edge"] for c in crossings)) if crossings
            else "run_stages(6, 6) for this page, then page_preview to check the output"}


def _wait_job(res: dict) -> dict:
    # A job that is still running writes the project later, so the caller must wait for its end.
    while res.get("state") == "running":
        res = job_status(res["job_id"], wait_s=60)
    return res


def project_add_front_cover(project: str, image: str = "001.jpg") -> dict:
    """Add a cover image as the first page of a finished project, for a book without a cover scan
    (for example a cover from a book shop site). Put the image in the folder of the scans first.
    The cover gets rotation 0, a single page, the whole image as content box, margins 0, no match size,
    Color 300 DPI. All other pages keep their settings and fill zones. Runs stages 1-5 for the cover
    only and waits for them; if a stage fails, the project goes back to the copy made before.
    Afterwards run_stages(6, 6, pages=[the cover]) makes its output file.
    The printed page numbers then move by one against the project pages."""
    import xml.etree.ElementTree as ET
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    r = p.root
    folder = p.local_path(r.find("./directories/directory").get("path"))
    src = folder / image
    if not src.exists():
        raise FileNotFoundError(f"{src} not found: put the cover image in the folder of the scans")
    if any(im.path.name == image for im in p.images.values()):
        raise RuntimeError(f"the project already has {image}")
    pages_before, zones_before = len(p.pages), len(r.findall("./filters/output/page/fill-zones/zone"))
    backup = proj.with_suffix(proj.suffix + ".before-cover")
    shutil.copy2(proj, backup)
    ids = [int(e.get("id")) for sec in ("files/file", "images/image", "pages/page") for e in r.findall(sec)]
    fid, iid, pid = (str(max(ids) + k) for k in (1, 2, 3))
    with Image.open(src) as im:
        size, dpi = im.size, im.info.get("dpi", (300, 300))
    fe = ET.Element("file", {"name": image, "dirId": r.find("./directories/directory").get("id"), "id": fid})
    ie = ET.Element("image", {"subPages": "1", "fileImage": "0", "id": iid, "fileId": fid})
    ET.SubElement(ie, "size", {"height": str(size[1]), "width": str(size[0])})
    ET.SubElement(ie, "dpi", {"vertical": str(round(dpi[1])), "horizontal": str(round(dpi[0]))})
    ET.SubElement(ie, "grayscale", {"value": "0"})
    pe = ET.Element("page", {"id": pid, "imageId": iid, "subPage": "single"})
    # The book order comes from the order of these lists, so the cover goes first.
    for sec, el in (("files", fe), ("images", ie), ("pages", pe)):
        r.find(sec).insert(0, el)
    p.set_rotation(iid, 0)
    p.set_split_layout(iid, "single-uncut")
    p.save()
    res = _wait_job(run_stages(str(proj), 1, 5, pages=[pid]))
    if res.get("state") != "ok":
        shutil.copy2(backup, proj)
        return {"error": "stages 1-5 of the cover failed; the project is back as before", "job": res}
    p = Project(proj)
    # The CLI writes the project again with new page ids, so the file name finds the cover page.
    pid = next(pg.id for pg in p.pages if pg.image.path.name == image)
    pt = p.transform(pid)
    # The image is the cover only, without the scanner lid, so the content box is the whole image.
    p.set_content(pid, {"x": 0.0, "y": 0.0, "width": float(pt.width), "height": float(pt.height)})
    p.set_layout(pid, margins_mm={"left": 0, "top": 0, "right": 0, "bottom": 0},
                 align_v="top", align_h="hcenter", match_size=False)
    p.set_output(pid, color_mode="color", dpi=300)
    p.save()
    res = _wait_job(run_stages(str(proj), 4, 5, pages=[pid]))
    if res.get("state") != "ok":
        shutil.copy2(backup, proj)
        return {"error": "stages 4-5 of the cover failed; the project is back as before", "job": res}
    p = Project(proj)
    zones_after = len(p.root.findall("./filters/output/page/fill-zones/zone"))
    if len(p.pages) != pages_before + 1 or zones_after != zones_before or p.pages[0].image.path.name != image:
        shutil.copy2(backup, proj)
        raise RuntimeError(f"check failed: pages {pages_before}->{len(p.pages)}, zones {zones_before}->{zones_after}; "
                           "the project is back as before")
    return {"cover_page": p.pages[0].label, "pages": len(p.pages), "fill_zones_kept": zones_after,
            "backup": str(backup), "job": res,
            "next": f"run_stages(6, 6, pages=['{p.pages[0].label}']) for the cover output"}


def check_pages(project: str, pages: str | list[str] = "all", kind: str = "content") -> dict:
    """Heuristic checks with evidence. kind=content: ink outside the box above/below (missed page numbers,
    headers) and left/right (cut-off text, marginalia), isolated junk at the box's top/bottom lines and
    left/right sides, empty boxes on pages with ink, and where page numbers sit.
    With a CLI that has --dump-masks, kind=content uses the ink mask of ScanTailor's filter 4
    (ink_source=scantailor), else st-mcp's own threshold (ink_source=st-mcp); st_mcp_ink_pages lists
    the pages that still used st-mcp's threshold.
    kind=pictures: pages with illustrations: picture_fraction (grayscale/halftone areas) and
    line_art_fraction (dense black-ink drawings); each flagged page has `likely` and must be previewed.
    kind=grayscale: for picture pages, before pages_set_output: is the picture gray although the scan is
    in color (gray photo or halftone on tinted paper)? Per page: chroma (spread of the color of the
    mid-tones after a weak paper tint is removed; below 10 = gray), grayscale, margin to the threshold and
    near_threshold. chroma_estimate is computed here; chroma_output_stage is ScanTailor's own value after
    a stage 6 run with grayscale_first=auto and wins. Preview every page with near_threshold=true.
    kind=size: the gate before stage 6. Lists every body page whose content box is wider than the book's
    median by more than 3 mm, or taller by more than 3 %. Such a box usually holds a stamp, an inventory
    number or pencil notes beside the text. The gate only lists the pages: never cut a box to the median size.
    For each page: page_preview, find the side with the mark, and double-click the outermost printed text
    of that side (pages_snap_content with points), so that the edge snaps to the text. Then cover what
    stays inside the box (a stamp beside the page number) with page_add_fill_zone.
    A wide page that is really wider (title picture, table, contents) stays: write review_note with why.
    stray_ink on a page: a small ink group apart from the text at that side (speck, pencil mark, scanner
    dust). Preview it; when it is not printed, pass its point to pages_snap_content.
    kind=output: the gate after stage 6. Each page must have a readable TIFF, and the pages that match the
    book size must have one TIFF size per DPI (another size means an old page layout: run stages 5-6 for
    the whole book). Also lists fill zones outside the content box, because they have no effect.
    kind=orientation: ScanTailor's text-based orientation detector (CLI --detect-orientation) on each scan
    (stage 1 rotation, EXIF tag only as a hint) and on each existing output TIFF (stage 6 output_rotation).
    Lists only the scans/pages that need a look: no decision, low confidence, EXIF tag against the text,
    text across the book (sideways table), or a detected rotation that differs from the project."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)
    if kind == "size":
        return _check_size(p, ids)
    if kind == "output":
        return _check_output(p, ids)
    if kind == "pictures":
        res = [checks.picture_score(p, i) for i in ids]
        flagged = [r for r in res if r["picture_fraction"] > 0.05 or r["line_art_fraction"] > 0.01]
        for r in flagged:
            r["likely"] = "grayscale" if r["picture_fraction"] > 0.05 else "line_art"
        # A page with a large angle can follow a stamp or the page edge, so it gets the angle check too.
        doubt_ids = {r["page_id"] for r in flagged} | {i for i in ids if abs(p.deskew_angle(i)) >= 3.0}
        angles = [_deskew_agreement(p, i) for i in ids if i in doubt_ids]
        disagree = [a for a in angles if a.get("disagree")]
        with review.Notes(p) as notes:
            notes.clear(ids, "pictures")
            notes.clear(list(doubt_ids), "deskew_check")
            for a in disagree:
                notes.add(a["page_id"], "deskew_check", "text and picture angles differ", 0.4,
                          f"now {a['angle']} deg; text lines {a['angle_text']} deg, picture or page edges "
                          f"{a['angle_picture']} deg: preview and choose")
            for r in flagged:
                if r["likely"] == "grayscale":
                    conf = 0.8 if r["picture_fraction"] > 0.15 else 0.55
                    why = f"grayscale area {r['picture_fraction']:.0%} of the page"
                else:
                    conf = 0.75 if r["line_art_fraction"] > 0.03 else 0.5
                    why = f"line art {r['line_art_fraction']:.1%} of the page"
                notes.add(r["page_id"], "pictures", f"picture page: {r['likely']}", conf, why)
        return {"checked": len(res), "flagged": flagged, "deskew_disagree": disagree,
                "deskew_rule": "deskew_disagree: the text lines and the picture (or page edges) give angles "
                               "more than 0.5 deg apart; preview the page and set the angle that a person "
                               "would see as straight with pages_straighten_pictures or images_set_geometry",
                "rule": "preview each: photos, halftones and color plates -> color_mode=color, "
                        "white_margins=true, equalize_illumination=true, then check_pages(kind=grayscale): "
                        "gray pictures -> grayscale_first=auto, color plates -> grayscale_first=false; "
                        "black-and-white line art -> keep bw 600"}
    if kind == "grayscale":
        res = [checks.grayscale_score(p, i) for i in ids]
        return {"checked": len(res), "threshold": checks.GRAY_CHROMA_THRESHOLD, "pages": res,
                "near_threshold": [r["label"] for r in res if r["near_threshold"]],
                "rule": "gray pictures (grayscale=true) -> grayscale_first=auto (or true); color plates "
                        "(grayscale=false) -> keep color, grayscale_first=false; preview near_threshold pages "
                        "and decide true/false yourself; covers stay color"}
    if kind == "orientation":
        return _check_orientation(p, ids)
    if kind != "content":
        raise ValueError("kind must be content|pictures|grayscale|orientation")
    masks = {}
    try:
        if cli_masks_available():
            masks = dump_masks(p, [i for i in ids if p.content(i).get("processed")])
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
        masks = {}  # The checks below then use st-mcp's own ink mask, as with an older CLI.
    res = [checks.check_page(p, i, checks.ink_from_masks(p, i, masks[i]) if i in masks else None) for i in ids]
    fallback = [p.page(i).label for i in ids if i not in masks and p.content(i).get("processed")]
    flagged = [r for r in res if r.get("findings")]
    with review.Notes(p) as notes:
        notes.clear(ids, "check_content")
        for r in flagged:
            kinds = list(dict.fromkeys(f["kind"] for f in r["findings"]))
            notes.add(r["page_id"], "check_content", "flagged: " + ", ".join(kinds), 0.5,
                      "; ".join(dict.fromkeys(f.get("hint", "") for f in r["findings"] if f.get("hint")))[:200])
    _add_sideways_direction(p, flagged)
    pos = [r.get("page_number_in_box") for r in res if r.get("page_number_in_box")]
    summary = {k: pos.count(k) for k in set(pos)}
    return {"checked": len(res), "flagged": flagged, "ink_source": "scantailor" if masks else "st-mcp",
            **({"st_mcp_ink_pages": fallback} if masks and fallback else {}),
            "page_number_position_votes": summary,
            "suggested_align_v": max(summary, key=summary.get) if summary else None}


# Both residuals use the deskewed page, so the angle each one suggests is the current angle minus the residual.
def _deskew_agreement(p: Project, page_id: str, limit: float = 0.5) -> dict:
    angle = p.deskew_angle(page_id)
    pic = checks.picture_skew(p, page_id)
    txt, n_lines = checks.text_skew(p, page_id)
    out = {"page_id": page_id, "label": p.page(page_id).label, "angle": round(angle, 2), "text_lines": n_lines,
           "angle_picture": None if pic is None else round(angle - pic, 2),
           "angle_text": None if txt is None else round(angle - txt, 2)}
    out["disagree"] = (pic is not None and txt is not None and abs(pic - txt) > limit)
    return out


ORIENT_NAME = {0: "none", 90: "right", 180: "upsidedown", 270: "left"}
DETECT_THRESHOLD = 0.5


# One CLI call for many files is faster than one call per file, and it changes no project.
def _detect_orientation(items: list[tuple[Path, int]]) -> dict[tuple[str, int], dict]:
    if not items:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("".join(f"{path}\t{page}\n" for path, page in items))
        list_file = f.name
    try:
        r = subprocess.run([find_cli(), f"--detect-orientation={list_file}"], capture_output=True, env=cli_env(),
                           text=True, encoding="utf-8", errors="replace")
    finally:
        os.unlink(list_file)
    if r.returncode != 0:
        raise RuntimeError(f"--detect-orientation failed ({r.returncode}): {r.stderr.strip()[-500:]}")
    res = {}
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "orientation":
            res[(ev["image"], int(ev.get("page", 0)))] = ev
    return res


# Same rule as OrientationDetector::applyBookRotation in the CLI, so that a sideways table keeps the book rotation.
def _apply_book_rotation(results: list[dict]) -> None:
    votes = Counter(r["orientation_detected"] for r in results
                    if r.get("orientation_detected") is not None and r.get("confidence", 0) >= DETECT_THRESHOLD)
    if not votes:
        return
    book, count = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
    if count < 5 or count * 10 < sum(votes.values()) * 6:
        return
    for r in results:
        a = r.get("orientation_detected")
        if a is not None and (a - book) % 180:
            r.update(orientation_detected=book, confidence=round(r.get("confidence", 0) * 0.5, 3),
                     reason="text runs across the book, book rotation used")


SIDEWAYS_DOUBT = "full height with few text lines: a rotated table?"


def _is_upright(ev: dict) -> bool:
    return ev.get("orientation_detected") == 0 and ev.get("confidence", 0) >= DETECT_THRESHOLD


def _content_kinds(note: dict) -> list[str]:
    decision = note.get("decision", "")
    return [k.strip() for k in decision.split(":", 1)[1].split(",")] if decision.startswith("flagged:") else []


# A page that the profile check also sees as sideways keeps its doubt, because a sideways table with an upright caption can look upright.
def _sideways_doubts(p: Project, ids: list[str]) -> list[str]:
    data = review.load(p)
    res = []
    for i in ids:
        notes = data["pages"].get(review.page_key(p, i), {})
        if (notes.get("alignment", {}).get("reason") == SIDEWAYS_DOUBT
                and "text_runs_vertically" not in _content_kinds(notes.get("check_content", {}))):
            res.append(i)
    return res


# The detector reads the finished page, so upright text there shows that a full page with few lines is no sideways table.
def _confirm_upright(p: Project, upright: list[str]) -> list[str]:
    doubtful = _sideways_doubts(p, upright)
    if not doubtful:
        return []
    with review.Notes(p) as notes:
        for i in doubtful:
            notes.add(i, "alignment", f"align {p.layout(i).get('align_v') or 'bottom'}", 0.8,
                      "full height with few text lines; the orientation detector confirmed upright text")
    # Notes.save drops a write error, so report only the pages whose note is on disk.
    return [p.page(i).label for i in doubtful if i not in _sideways_doubts(p, [i])]


def _upright_outputs(p: Project, ids: list[str]) -> list[str]:
    files = {}
    for i in ids:
        o = p.output(i)
        f = p.output_dir / (p.output_name(i) + ".tif")
        # A file from an older output rotation does not show the page as the current setting makes it.
        if f.exists() and o.get("file_rotation", 0) == o.get("output_rotation", 0):
            files[i] = f
    det = _detect_orientation([(f, 0) for f in files.values()])
    return [i for i, f in files.items() if _is_upright(det.get((str(f), 0)) or {})]


def _check_orientation(p: Project, ids: list[str]) -> dict:
    if not _cli_supports("detect-orientation"):
        raise RuntimeError(f"{find_cli()} does not know --detect-orientation: build a newer CLI")
    pages = [p.page(i) for i in ids]
    image_ids = list(dict.fromkeys(pg.image_id for pg in pages))
    # ScanTailor counts the pages of a multi-page file from 1, and the CLI list file counts them from 0.
    scan_items = [(p.images[i].path, max(0, p.images[i].file_page - 1)) for i in image_ids]
    outputs = {}
    for pg in pages:
        f = p.output_dir / (p.output_name(pg.id) + ".tif")
        if f.exists():
            outputs[pg.id] = f
    det = _detect_orientation(scan_items + [(f, 0) for f in outputs.values()])
    missing = {"orientation_detected": None, "confidence": 0, "reason": "no result from the CLI"}
    scans = [dict(det.get((str(path), page), missing)) for path, page in scan_items]
    _apply_book_rotation(scans)

    flagged, scans_ok = [], 0
    for iid, r in zip(image_ids, scans):
        cur, ang, conf, reason = p.rotation(iid), r.get("orientation_detected"), r.get("confidence", 0), r["reason"]
        why = []
        if ang is None:
            why.append("no decision")
        elif conf < DETECT_THRESHOLD:
            why.append("low confidence")
        if ang is not None and ang != cur:
            why.append(f"detected {ang}, project has {cur}")
        if "exif disagrees" in reason:
            why.append("the EXIF tag disagrees with the text")
        if "across the book" in reason:
            why.append("text runs across the book (sideways table?)")
        if not why:
            scans_ok += 1
            continue
        item = {"stage": 1, "image": p.images[iid].path.name,
                "pages": [pg.label for pg in pages if pg.image_id == iid], "rotation": cur,
                "detected": ang, "confidence": conf, "reason": reason, "why": why}
        if "exif_rotation" in r:
            item["exif_rotation"] = r["exif_rotation"]
        flagged.append(item)

    outputs_ok, upright = 0, []
    for pid, f in outputs.items():
        r = det.get((str(f), 0), missing)
        o = p.output(pid)
        cur = o.get("output_rotation", 0)
        rel, reason = r.get("orientation_detected"), r["reason"]
        why, need = [], None
        if rel is None:
            # A page without text (picture, blank) keeps the book rotation, so only unclear text needs a look.
            if reason not in ("no text", "cannot load the image"):
                why.append("no decision")
        else:
            need = (o.get("file_rotation", 0) + rel) % 360
            if r.get("confidence", 0) < DETECT_THRESHOLD:
                why.append("low confidence")
            if need != cur:
                why.append(f"detected output_rotation {need}, project has {cur}")
        if not why and need == cur and _is_upright(r):
            upright.append(pid)
        if not why:
            outputs_ok += 1
            continue
        flagged.append({"stage": 6, "page": p.page(pid).label, "output_rotation": cur, "detected": need,
                        "confidence": r.get("confidence", 0), "reason": reason, "why": why})
    confirmed = _confirm_upright(p, upright)
    return {"checked_scans": len(scans), "scans_ok": scans_ok, "checked_outputs": len(outputs),
            "outputs_ok": outputs_ok, "flagged": flagged, "threshold": DETECT_THRESHOLD,
            **({"upright_confirmed": confirmed} if confirmed else {}),
            "rule": "preview every flagged scan/page; stage 1: images_set_geometry(images, orientation="
                    "none|right|upsidedown|left for 0|90|180|270), then run_stages(1, 5); stage 6: "
                    "pages_set_output(pages, output_rotation=<detected>), then run_stages(6, 6, pages=...)"}


# The profile check sees that text runs across the page, and the detector on the output file gives the direction.
def _add_sideways_direction(p: Project, flagged: list[dict]) -> None:
    sideways = [r for r in flagged if any(f["kind"] == "text_runs_vertically" for f in r["findings"])]
    if not sideways or not _cli_supports("detect-orientation"):
        return
    files = {}
    for r in sideways:
        f = p.output_dir / (p.output_name(r["page_id"]) + ".tif")
        if f.exists():
            files[r["page_id"]] = f
    det = _detect_orientation([(f, 0) for f in files.values()])
    for r in sideways:
        ev = det.get((str(files.get(r["page_id"], "")), 0))
        if not ev or ev.get("orientation_detected") is None:
            continue
        need = (p.output(r["page_id"]).get("file_rotation", 0) + ev["orientation_detected"]) % 360
        for f in r["findings"]:
            if f["kind"] == "text_runs_vertically":
                f.update(detected_output_rotation=need, confidence=ev.get("confidence"),
                         hint=f"sideways content: keep stages 1-5, preview, then "
                              f"pages_set_output(output_rotation={need}) or run_stages(6, 6, output_rotate='auto')")


def export_summary(project: str) -> dict:
    """List produced output files (after stage 6) and anything missing."""
    p = _proj(project)
    out = p.output_dir
    files = {f.stem: f for f in out.glob("*.tif")}
    missing = [pg.label for pg in p.pages if p.output_name(pg.id) not in files]
    return {"output_dir": str(out), "tif_files": len(files), "missing": missing[:50],
            "total_mb": round(sum(f.stat().st_size for f in files.values()) / 1e6, 1)}


# The CLI finds the angle only for pages without deskew parameters, so it runs on a copy without them.
def _cli_picture_angles(p: Project, proj: Path, ids: list[str]) -> dict[str, float]:
    # The JSON events give only the file name, so pages of different files with one name stay in Python.
    paths: dict[str, set] = {}
    for im in p.images.values():
        paths.setdefault(im.path.name, set()).add(im.path)
    ids = [i for i in ids if len(paths[p.page(i).image.path.name]) == 1]
    if not ids:
        return {}
    # The copy stays beside the project, because the image paths in it are relative to its folder.
    fd, name = tempfile.mkstemp(dir=proj.parent, prefix=proj.stem + ".deskew-pictures-", suffix=proj.suffix)
    os.close(fd)
    tmp = Path(name)
    shutil.copy2(proj, tmp)
    try:
        t = Project(tmp)
        for i in ids:
            t.clear_deskew(i)
        t.save()
        # New builds have the option on by default, but older builds need the switch, so the call keeps it.
        cmd = [find_cli(), "--deskew-pictures", "--json-progress", "--start-filter=3", "--end-filter=3",
               "--pages=" + ";".join(_page_ref(t, tmp, t.page(i)) for i in ids),
               f"--output-project={tmp}", str(tmp), str(p.output_dir)]
        r = subprocess.run(cmd, capture_output=True, env=cli_env(), text=True, encoding="utf-8", errors="replace",
                           timeout=60 + 10 * len(ids))
    finally:
        for f in (tmp, tmp.with_suffix(tmp.suffix + ".bak")):
            f.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"CLI --deskew-pictures failed: {r.stderr.strip()[-500:]}")
    key = {(p.page(i).image.path.name, p.page(i).image.file_page, p.page(i).side): i for i in ids}
    angles = {}
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") != "page_done" or ev.get("estimator") != "picture":
            continue
        i = key.get((ev.get("image"), ev.get("image_page", 0), ev.get("side")))
        if i is not None:
            angles[i] = float(ev["angle"])
    return angles


def review_note(project: str, pages: str | list[str], decision: str, reason: str = "",
                confidence: float = 0.7, topic: str | None = None) -> dict:
    """Record a decision of your own for the review sheet (e.g. orientation, a cover choice, a picture
    page kept B/W): confidence 0..1, a short reason. Below 0.6 the page gets "check in the GUI".
    Tools note their own decisions already. topic (default: "agent: " + decision) names the note;
    a new note with the same topic on a page replaces the old one.
    Stored in <output_dir>/review/decisions.json."""
    p = _proj(project)
    ids = _resolve_pages(p, pages)
    topic = topic or f"agent: {decision[:40]}"
    with review.Notes(p) as notes:
        for i in ids:
            notes.add(i, topic, decision, confidence, reason)
    return {"pages": [p.page(i).label for i in ids], "topic": topic, "file": str(review.decisions_path(p))}


def review_sheet(project: str, pages: str | list[str] = "all", thumb_px: int = 320,
                 engine: str = "auto", max_previews: int = 150) -> dict:
    """Write the review sheet <output_dir>/review/index.html (one self-contained page, relative image
    paths, opens from Windows): a thumbnail of each output TIFF, and for pages with agent decisions also
    the content box preview. Pages with low confidence or flags come first, with "CHECK IN GUI".
    Run it after run_stages(4, 6), before project_for_windows (the CLI previews need paths it can open;
    otherwise st-mcp draws them). engine: auto|cli|python for the content box previews.
    It runs the orientation detector on the output files of pages that pages_align_auto marked as a
    possible rotated table; upright text there clears the doubt in decisions.json (upright_confirmed).
    A page with a check_pages text_runs_vertically finding keeps the doubt.
    Returns the pages to check: list them in the final report."""
    p = _proj(project)
    rdir = review.review_dir(p)
    tdir = rdir / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    ids = _resolve_pages(p, pages)
    confirmed = []
    doubtful = _sideways_doubts(p, ids)
    # The sheet is only a report, so a missing CLI or any detector error keeps the doubt and the sheet is still written.
    try:
        if doubtful and _cli_supports("detect-orientation"):
            confirmed = _confirm_upright(p, _upright_outputs(p, doubtful))
    except Exception:
        confirmed = []
    data = review.load(p)
    entries = []
    for i in ids:
        pg = p.page(i)
        notes = data["pages"].get(review.page_key(p, i), {})
        decs = sorted(({"source": k, **v} for k, v in notes.items()), key=lambda d: d["confidence"])
        entries.append({"id": i, "index": pg.index, "label": pg.label, "output_name": p.output_name(i),
                        "decisions": decs, "check": any(d["check"] for d in decs),
                        "min_confidence": min((d["confidence"] for d in decs), default=1.0)})
    n_out = 0
    for e in entries:
        tif = p.output_dir / (e["output_name"] + ".tif")
        dst = tdir / (e["output_name"] + ".out.jpg")
        if tif.exists() and review._output_thumb(tif, dst, thumb_px):
            e["out_thumb"] = f"thumbs/{dst.name}"
            n_out += 1
    # The doubtful pages get the previews first, because max_previews can cut the list.
    want = sorted((e for e in entries if e["decisions"]),
                  key=lambda e: (not e["check"], e["min_confidence"], e["index"]))[:max_previews]
    used = "none"
    if want:
        imgs: dict = {}
        if engine != "python":
            # With engine=auto, a CLI error only means that st-mcp draws the previews itself.
            try:
                if _use_cli_preview(engine):
                    imgs = _cli_previews(p, [e["id"] for e in want], thumb_px * 2)
                    used = "cli"
            except (RuntimeError, FileNotFoundError):
                if engine == "cli":
                    raise
        for e in want:
            im = imgs.get(e["id"])
            if im is None:
                try:
                    im = render_page(p, e["id"], max_px=thumb_px * 2)
                    used = {"none": "python", "cli": "cli+python"}.get(used, used)
                except (OSError, KeyError, RuntimeError):
                    continue
            dst = tdir / (e["output_name"] + ".box.jpg")
            review._save_preview(im, dst, thumb_px)
            e["box_thumb"] = f"thumbs/{dst.name}"
    entries.sort(key=lambda e: (not e["check"], not e["decisions"], e["min_confidence"], e["index"]))
    index = rdir / "index.html"
    review.write_html(index, p.path.stem, entries, review.win_path(p.path))
    to_check = [{"page": e["label"], "why": [f"{d['source']}: {d['decision']} ({d['reason']})"
                                             for d in e["decisions"] if d["check"]]}
                for e in entries if e["check"]]
    (rdir / "review.json").write_text(json.dumps({"project": str(p.path), "to_check": to_check,
                                                  "pages": [{k: e[k] for k in ("label", "check", "min_confidence",
                                                                                "decisions")} for e in entries]},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    return {"html": str(index), "windows_path": review.win_path(index), "pages": len(entries),
            "with_decisions": sum(1 for e in entries if e["decisions"]), "to_check": to_check[:60],
            "to_check_count": len(to_check), "output_thumbnails": n_out, "preview_engine": used,
            **({"upright_confirmed": confirmed} if confirmed else {}),
            "next": "list the pages to check in the final report, with the path of the review sheet"}


def pages_straighten_pictures(project: str, pages: str | list[str], min_change: float = 0.25) -> dict:
    """Picture pages (photos, plates): ScanTailor deskews by text lines, which photos do not have, so it
    can tilt them by several degrees. Re-measures the skew from the edges of the printed pictures (with
    the CLI option --deskew-pictures when the CLI has it, else in Python) and sets a manual deskew angle.
    Pages where a person cleared "Use picture edges" in the GUI stay unchanged (skipped_text_lines).
    Afterwards run_stages(3, 5), then page_set_content(mode=all_content)."""
    proj = Path(project).resolve()
    _ensure_idle(proj)
    p = Project(proj)
    ids = _resolve_pages(p, pages)
    # A person chose the text lines for these pages in the GUI, so the tool does not change them.
    text_only = [i for i in ids if p.picture_edges_off(i)]
    ids = [i for i in ids if i not in text_only]
    cli_angles, cli_error = {}, None
    if _cli_supports("deskew-pictures"):
        # Python can measure every page, so a CLI problem must not stop the tool.
        try:
            _check_inputs_reachable(p, find_cli())
            cli_angles = _cli_picture_angles(p, proj, ids)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            cli_error = str(e)[-300:]
    changed, changed_ids, in_python = [], [], 0
    for i in ids:
        old = p.deskew_angle(i)
        if i in cli_angles:
            new, method = cli_angles[i], "cli"
        else:
            res = checks.picture_skew(p, i)
            if res is None:
                continue
            new, method = old - res, "python"
            in_python += 1
        if abs(new - old) >= min_change:
            p.set_deskew(i, new)
            changed.append({"page": p.page(i).label, "angle_before": round(old, 3), "angle_after": round(new, 3),
                            "method": method})
            changed_ids.append(i)
    p.save()
    with review.Notes(p) as notes:
        for c, pid in zip(changed, changed_ids):
            big = abs(c["angle_after"] - c["angle_before"]) > 2.0
            notes.add(pid, "deskew", f"picture deskew {c['angle_before']} -> {c['angle_after']} deg ({c['method']})",
                      0.5 if big else 0.75,
                      "large change: check that the picture is straight" if big else "from the picture edges")
    res = {"changed": changed, "measured_by_cli": len(cli_angles), "measured_in_python": in_python,
           "next": "run_stages(3, 5), then page_set_content(mode=all_content) on these pages"}
    if cli_error:
        res["cli_error"] = cli_error
    if text_only:
        res["skipped_text_lines"] = [p.page(i).label for i in text_only]
    return res


# The copy always holds the last finished run, so that project_diff shows only the corrections of the person.
def _keep_original(proj: Path) -> dict:
    orig = proj.with_name(proj.stem + ".orig" + proj.suffix)
    replaced = orig.exists()
    shutil.copy2(proj, orig)
    return {"path": str(orig), "replaced_older_copy": replaced}


def project_diff(original: str, corrected: str, limit: int = 60, record: bool = False) -> dict:
    """Compare the project the agent made (e.g. agentas.orig.ScanTailor) with the same project after a
    person corrected it in the GUI. Lists, per kind, the pages that differ: images removed or added,
    rotation, split, deskew angle, content box and mode, empty pages, margins, alignment, match size,
    output mode, DPI and output rotation. Use it to learn rules for the workflow from the corrections.
    record=true also appends the changes to the corrections log (as corrections_record does)."""
    if record:
        rec = corrections.record(original, corrected)
        res = project_diff(original, corrected, limit)
        res["corrections_log"] = rec
        return res
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
            "output": (out.get("color_mode"), out.get("dpi"), out.get("output_rotation", 0)),
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


def corrections_record(original: str, corrected: str, book: str | None = None, log: str | None = None) -> dict:
    """Append the person's corrections to the corrections log: one JSONL record per changed page
    parameter (book, page, stage, field, agent value, user value, category). original: the agent's
    project (agentas.orig.ScanTailor); corrected: the same project after the person's GUI corrections.
    A change that is already in the log for this book is not written again, so run it after each review.
    book: the book folder (default: the folder of `corrected`). log: default $ST_CORRECTIONS_LOG or
    ~/.local/share/st-mcp/corrections.jsonl. Categories: page_removed, page_added, split, rotation,
    deskew_picture, deskew_text, page_box, content_empty, content_box_trim, content_box_extend,
    content_box_numbers, cover, margins, alignment, match_size, output_mode, dpi, picture_zones, other."""
    return corrections.record(original, corrected, book, log)


def corrections_stats(log: str | None = None, book: str | None = None, top: int = 5) -> dict:
    """Summary of the corrections log: changes per category and per book, and the `top` most frequent
    categories with the workflow rule each one points to (the rule that fails most comes first).
    book: only this book folder. log: as in corrections_record."""
    return corrections.stats(log, book, top)


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


TOOLS = [project_create, project_add_front_cover, project_for_windows, project_diff, corrections_record, corrections_stats, pages_straighten_pictures, images_set_geometry, run_stages, job_status, project_info, pages_list, page_preview,
         pages_preview, image_preview, page_set_content, pages_snap_content, pages_set_layout, pages_fit_text_column, pages_fit_width, pages_align_auto, pages_align_horizontal, pages_set_output,
         page_add_fill_zone, check_pages, export_summary, review_note, review_sheet, workflow_guide]
IMAGE_TOOLS = {"page_preview", "pages_preview", "image_preview"}
