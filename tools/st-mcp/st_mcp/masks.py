from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .geometry import Affine, scale
from .project import Project


# The analysis masks of ScanTailor filter 4 at 150 DPI, as the CLI option --dump-masks writes them.
@dataclass
class PageMasks:
    content: np.ndarray
    text: np.ndarray | None
    garbage: np.ndarray | None
    original_to_mask: Affine
    page_to_mask: Affine
    info: dict


def _affine(m: list[float]) -> Affine:
    # The CLI writes QTransform values m11 m12 m21 m22 dx dy, which is the field order of Affine.
    return Affine(*m)


def _load(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L")) > 0


def cli_masks_available() -> bool:
    from .ops import _cli_supports
    return _cli_supports("dump-masks") and _cli_supports("pages")


def dump_masks(project: Project, page_ids: list[str]) -> dict[str, PageMasks]:
    from .ops import _page_ref, cli_env, find_cli
    proj = project.path
    out: dict[str, PageMasks] = {}
    if not page_ids:
        return out
    with tempfile.TemporaryDirectory(prefix="st-masks-") as tmp:
        # Start filter 5 and end filter 4 run no filter, so the masks use the saved results of filter 4.
        cmd = [find_cli(), "--start-filter=5", "--end-filter=4", "--json-progress",
               "--pages=" + ";".join(_page_ref(project, proj, project.page(i)) for i in page_ids),
               f"--dump-masks={tmp}/masks", str(proj), f"{tmp}/out"]
        # The time limit stops a CLI that hangs, and the caller then uses its own ink.
        r = subprocess.run(cmd, capture_output=True, env=cli_env(), cwd=str(proj.parent),
                           text=True, encoding="utf-8", errors="replace", timeout=60 + 10 * len(page_ids))
        mdir = Path(tmp) / "masks"
        for i in page_ids:
            j = mdir / (project.output_name(i) + ".json")
            if not j.exists():
                continue
            info = json.loads(j.read_text(encoding="utf-8"))
            files = info["mask"]["files"]
            out[i] = PageMasks(
                content=_load(mdir / files["content"]),
                text=_load(mdir / files["text"]) if "text" in files else None,
                garbage=_load(mdir / files["garbage"]) if "garbage" in files else None,
                original_to_mask=_affine(info["transforms"]["original_to_mask"]),
                page_to_mask=_affine(info["transforms"]["page_to_mask"]),
                info=info)
        if not out:
            raise RuntimeError(f"CLI --dump-masks failed ({r.returncode}): {(r.stdout + r.stderr)[-800:]}")
    return out


def mask_on_page_grid(mask: np.ndarray, page_to_mask: Affine, k: float, size: tuple[int, int]) -> np.ndarray:
    # The grid is page px scaled by k, as render_page() draws it, so the old checks keep their coordinates.
    grid_to_mask = scale(1 / k, 1 / k).then(page_to_mask)
    m = grid_to_mask
    im = Image.fromarray(mask.astype(np.uint8) * 255).transform(
        size, Image.Transform.AFFINE, (m.a, m.c, m.e, m.b, m.d, m.f),
        resample=Image.Resampling.BILINEAR, fillcolor=0)
    # Any part of a mask pixel counts as ink, so that thin strokes stay after the reduction.
    return np.asarray(im) > 0
