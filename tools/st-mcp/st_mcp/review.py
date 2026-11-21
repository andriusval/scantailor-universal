# Agent decisions with a confidence value, and the review sheet that shows the doubtful pages first.
from __future__ import annotations

import datetime as _dt
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from .project import Project

# Below this confidence a decision gets the "check in the GUI" marker.
CHECK_BELOW = 0.6


def review_dir(p: Project) -> Path:
    return p.output_dir / "review"


def decisions_path(p: Project) -> Path:
    return review_dir(p) / "decisions.json"


def load(p: Project) -> dict:
    path = decisions_path(p)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("pages"), dict):
                return data
        except ValueError:
            pass
    return {"version": 1, "project": p.path.name, "pages": {}}


class Notes:
    # Collects the notes of one tool call and writes the file once at the end.
    def __init__(self, p: Project):
        self.p = p
        self.data = load(p)
        self.dirty = False

    def add(self, page: str, source: str, decision: str, confidence: float, reason: str = "",
            check: bool | None = None) -> None:
        conf = round(max(0.0, min(1.0, float(confidence))), 2)
        self.data["pages"].setdefault(page, {})[source] = {
            "decision": decision, "confidence": conf, "reason": reason,
            "check": conf < CHECK_BELOW if check is None else bool(check),
            "time": _dt.datetime.now().isoformat(timespec="seconds")}
        self.dirty = True

    def clear(self, pages, source: str) -> None:
        for page in pages:
            entry = self.data["pages"].get(page)
            if entry and entry.pop(source, None) is not None:
                self.dirty = True
                if not entry:
                    del self.data["pages"][page]

    def save(self) -> None:
        if not self.dirty:
            return
        path = decisions_path(self.p)
        # The tool that made the decision must not fail, so a write error only loses the note.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def __enter__(self) -> "Notes":
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is None:
            self.save()


def win_path(path: Path) -> str:
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path.as_posix())
    return f"{m.group(1).upper()}:\\{m.group(2).replace('/', chr(92))}" if m else str(path)


def _output_thumb(tif: Path, dst: Path, px: int) -> bool:
    if dst.exists() and dst.stat().st_mtime >= tif.stat().st_mtime:
        return True
    try:
        with Image.open(tif) as im:
            im = im.convert("L" if im.mode in ("1", "L", "I;16") else "RGB")
            im.thumbnail((px, px), reducing_gap=2.0)
            im.save(dst, "JPEG", quality=82)
        return True
    except OSError:
        return False


def _save_preview(im: Image.Image, dst: Path, px: int) -> None:
    im = im.convert("RGB")
    im.thumbnail((px, px))
    im.save(dst, "JPEG", quality=82)


CSS = """
:root{--bg:#f4f4f2;--card:#fff;--fg:#1d1d1b;--mut:#666;--line:#ddd;--warn:#c62828;--warnbg:#fdecea;--ok:#2e7d32}
@media (prefers-color-scheme: dark){:root{--bg:#1b1b1b;--card:#262626;--fg:#eee;--mut:#aaa;--line:#3a3a3a;
--warn:#ff6b6b;--warnbg:#3a1f1f;--ok:#81c784}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,Segoe UI,sans-serif}
h1{font-size:20px;margin:0 0 4px} .sum{color:var(--mut);margin-bottom:12px}
.bar{position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:1;border-bottom:1px solid var(--line)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px;margin-top:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px}
.card.check{border:2px solid var(--warn);background:var(--warnbg)}
.lab{font-weight:600;font-size:15px} .file{color:var(--mut);font-size:12px}
.flag{display:inline-block;background:var(--warn);color:#fff;border-radius:3px;padding:1px 6px;font-size:12px;
font-weight:600;margin-left:6px}
.imgs{display:flex;gap:6px;margin:8px 0;align-items:flex-start} .imgs figure{margin:0;flex:1;min-width:0}
.imgs img{max-width:100%;border:1px solid var(--line);background:#fff}
figcaption{font-size:11px;color:var(--mut)} ul{margin:0;padding-left:18px} li{margin:2px 0}
.c{font-variant-numeric:tabular-nums;font-weight:600} .low{color:var(--warn)} .hi{color:var(--ok)}
.r{color:var(--mut)} .none{color:var(--mut);font-style:italic}
body.only .card:not(.check){display:none}
"""

JS = ("document.getElementById('only').addEventListener('change',function(e){"
      "document.body.classList.toggle('only',e.target.checked);});")


def _card(e: dict) -> str:
    cls = "card check" if e["check"] else "card"
    head = f'<div class="lab">{html.escape(e["label"])}'
    if e["check"]:
        head += '<span class="flag">CHECK IN GUI</span>'
    head += f'</div><div class="file">{html.escape(e["output_name"])}.tif</div>'
    figs = []
    for key, cap in (("out_thumb", "output"), ("box_thumb", "content box")):
        if e.get(key):
            figs.append(f'<figure><img loading="lazy" src="{quote(e[key])}" alt="{cap}">'
                        f"<figcaption>{cap}</figcaption></figure>")
    imgs = f'<div class="imgs">{"".join(figs)}</div>' if figs else '<div class="none">no output yet</div>'
    items = []
    for d in e["decisions"]:
        cc = "low" if d["check"] else "hi"
        items.append(f'<li><span class="c {cc}">{round(d["confidence"] * 100)}%</span> '
                     f'<b>{html.escape(d["source"])}</b>: {html.escape(d["decision"])}'
                     + (f' <span class="r">({html.escape(d["reason"])})</span>' if d.get("reason") else "")
                     + "</li>")
    dec = f"<ul>{''.join(items)}</ul>" if items else '<div class="none">no agent decision</div>'
    return f'<div class="{cls}">{head}{imgs}{dec}</div>'


def write_html(path: Path, title: str, entries: list[dict], project_path: str) -> None:
    n_check = sum(1 for e in entries if e["check"])
    n_dec = sum(1 for e in entries if e["decisions"])
    body = "".join(_card(e) for e in entries)
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f"<title>Review: {html.escape(title)}</title><style>{CSS}</style></head><body>"
           f"<h1>Review: {html.escape(title)}</h1>"
           f'<div class="sum">{len(entries)} pages, {n_dec} with agent decisions, '
           f'<b>{n_check} to check in the GUI</b>. Project: {html.escape(project_path)}. '
           f"Pages with low confidence come first.</div>"
           f'<div class="bar"><label><input type="checkbox" id="only"> only pages to check</label></div>'
           f'<div class="grid">{body}</div><script>{JS}</script></body></html>')
    path.write_text(doc, encoding="utf-8")
