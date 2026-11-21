#!/usr/bin/env python3
# Book queue for overnight runs: one headless Claude Code session per book, with a state file for resume.
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW_DOC = HERE.parents[1] / "docs" / "AGENTIC_WORKFLOW.en.md"
REPORT = Path("out-agentas") / "review" / "report.md"
SHEET = Path("out-agentas") / "review" / "index.html"
DEFAULT_TOOLS = "mcp__scantailor,Read,Write,Edit,Glob,Grep"

PROMPT = """Process the book scans in this folder with the scantailor MCP server.

Book folder: {folder}{win}
{note}
Rules:
- Run the whole workflow to the end without approval stops. Nobody answers questions during this run.
- In the book folder, write only agentas.ScanTailor, agentas.orig.ScanTailor and out-agentas/.
  Never change projektas.ScanTailor or out/. Never delete agentas.orig.ScanTailor.
- If agentas.ScanTailor exists from an earlier unfinished run, create the project again.
- Black-and-white line art stays B/W 600 DPI. Grayscale pictures (photos, halftones) get Color/Grayscale
  with white margins and equalize illumination.
- Record your own decisions (orientation, covers, picture pages) with review_note.
- Before project_for_windows, run review_sheet (it writes out-agentas/review/index.html).
- The final project must open in the Windows GUI: run project_for_windows at the end.
- Write the final report in Markdown to {report}: every decision (pages and the reason), the pages
  to check from review_sheet, the removal candidates, and anything left for a person.

Workflow steps (from docs/AGENTIC_WORKFLOW.en.md; call workflow_guide for the full text):

{steps}
"""


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# Outside Windows, the runner opens a Windows path of the queue file through /mnt/<drive>.
def local_folder(text: str) -> Path:
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if m and os.name != "nt":
        return Path("/mnt", m.group(1).lower(), *[x for x in re.split(r"[\\/]+", m.group(2)) if x])
    return Path(text).expanduser()


def windows_form(path: Path) -> str | None:
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path.as_posix())
    return f"{m.group(1).upper()}:\\{m.group(2).replace('/', chr(92))}" if m else None


def file_link(path: Path) -> str:
    w = windows_form(path)
    return "file:///" + w.replace("\\", "/").replace(" ", "%20") if w else path.resolve().as_uri()


def read_queue(queue: Path) -> list[dict]:
    books = []
    for line in queue.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        folder, _, note = line.partition("|")
        books.append({"key": folder.strip(), "folder": local_folder(folder.strip()), "note": note.strip()})
    return books


def workflow_steps() -> str:
    if not WORKFLOW_DOC.exists():
        return "(call workflow_guide)"
    text = WORKFLOW_DOC.read_text(encoding="utf-8")
    m = re.search(r"^## Agent procedure.*?\n(.*?)(?=^## )", text, re.S | re.M)
    return (m.group(1) if m else text).strip()


def make_prompt(book: dict) -> str:
    folder = book["folder"]
    win = windows_form(folder)
    return PROMPT.format(folder=folder, win=f" (Windows: {win})" if win else "",
                         note=f"Note from the person: {book['note']}\n" if book["note"] else "",
                         report=REPORT.as_posix(), steps=workflow_steps())


def mcp_config(path: Path) -> Path:
    for exe in (HERE / ".venv" / "bin" / "st-mcp", HERE / ".venv" / "Scripts" / "st-mcp.exe"):
        if exe.exists():
            server = {"command": str(exe), "args": ["serve"]}
            break
    else:
        server = {"command": sys.executable, "args": ["-m", "st_mcp", "serve"], "env": {"PYTHONPATH": str(HERE)}}
    if os.environ.get("ST_CLI"):
        server.setdefault("env", {})["ST_CLI"] = os.environ["ST_CLI"]
    path.write_text(json.dumps({"mcpServers": {"scantailor": server}}, indent=2), encoding="utf-8")
    return path


def build_command(args, book: dict, prompt: str, cfg: Path) -> list[str]:
    base = shlex.split(args.command) if args.command else ["claude"]
    cmd = base + ["-p", prompt, "--output-format", "stream-json", "--verbose",
                  "--mcp-config", str(cfg), "--strict-mcp-config",
                  "--permission-mode", args.permission_mode, "--allowedTools", args.allowed_tools,
                  "--add-dir", str(book["folder"])]
    if args.model:
        cmd += ["--model", args.model]
    if args.max_budget_usd:
        cmd += ["--max-budget-usd", str(args.max_budget_usd)]
    return cmd


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"books": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def book(self, key: str) -> dict:
        return self.data["books"].setdefault(key, {"status": "pending", "attempts": 0})

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# Two runners on one queue would start the same book twice, so a lock file holds the runner's process id.
def take_lock(lock: Path) -> None:
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and pid_alive(pid):
            raise SystemExit(f"another runner (pid {pid}) uses this queue; remove {lock} if it is not running")
        lock.unlink()
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def run_book(args, book: dict, st: dict, log: Path, cfg: Path) -> None:
    folder = book["folder"]
    st.update({"status": "running", "started": now(), "finished": None, "exit_code": None, "error": None,
               "log": str(log), "runner_pid": os.getpid()})
    st["attempts"] = st.get("attempts", 0) + 1
    if not folder.is_dir():
        st.update({"status": "failed", "finished": now(), "error": "book folder not found"})
        return
    cmd = build_command(args, book, make_prompt(book), cfg)
    t0 = time.time()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"# {now()} start, attempt {st['attempts']}: {folder}\n")
        fh.flush()
        proc = subprocess.Popen(cmd, cwd=str(folder), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            code = proc.wait(timeout=args.timeout_min * 60 if args.timeout_min else None)
            error = None
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
            error = f"timeout after {args.timeout_min} min"
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        fh.write(f"\n# {now()} end, exit code {code}\n")
    st.update({"finished": now(), "exit_code": code, "duration_min": round((time.time() - t0) / 60, 1)})
    if error:
        st.update({"status": "failed", "error": error})
    elif code != 0:
        st.update({"status": "failed", "error": f"exit code {code}"})
    elif not (folder / REPORT).exists():
        st.update({"status": "failed", "error": f"no {REPORT.as_posix()}"})
    else:
        st["status"] = "done"


def write_summary(path: Path, books: list[dict], state: State) -> None:
    lines = [f"# Book queue: {path.stem}", "", f"Updated {now()}.", "",
             "| Book | Status | Minutes | Pages to check | Review sheet | Log |", "|---|---|---|---|---|---|"]
    for b in books:
        st = state.data["books"].get(b["key"], {"status": "pending"})
        sheet = b["folder"] / SHEET
        to_check = ""
        rj = sheet.parent / "review.json"
        if rj.exists():
            try:
                to_check = str(len(json.loads(rj.read_text(encoding="utf-8")).get("to_check", [])))
            except ValueError:
                pass
        status = st["status"] + (f" ({st['error']})" if st.get("error") else "")
        link = f"[review]({file_link(sheet)})" if sheet.exists() else "-"
        log = f"[log]({file_link(Path(st['log']))})" if st.get("log") else "-"
        lines.append(f"| {b['folder'].name} | {status} | {st.get('duration_min', '')} | {to_check} | {link} | {log} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("_")[:60] or "book"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the ScanTailor agent workflow on a queue of book folders, "
                                             "one headless Claude Code session per book.")
    ap.add_argument("queue", help="text file: one book folder per line, optional '| note'; # starts a comment")
    ap.add_argument("--dry-run", action="store_true", help="print the commands; change nothing")
    ap.add_argument("--command", help="program to run instead of 'claude' (for tests), split like a shell")
    ap.add_argument("--retry-failed", action="store_true", help="run failed books again")
    ap.add_argument("--timeout-min", type=float, default=240, help="stop a book after this time (0: no limit)")
    ap.add_argument("--max-books", type=int, default=0, help="stop after this number of books (0: all)")
    ap.add_argument("--model", help="claude --model")
    ap.add_argument("--max-budget-usd", type=float, help="claude --max-budget-usd, per book")
    ap.add_argument("--mcp-config", help="MCP config file (default: one written next to the queue)")
    ap.add_argument("--permission-mode", default="dontAsk", help="claude --permission-mode (default dontAsk)")
    ap.add_argument("--allowed-tools", default=DEFAULT_TOOLS, help=f"claude --allowedTools (default {DEFAULT_TOOLS})")
    args = ap.parse_args(argv)

    queue = Path(args.queue).resolve()
    base = queue.with_suffix("")
    books = read_queue(queue)
    state = State(base.with_name(base.name + ".state.json"))
    summary = base.with_name(base.name + "-summary.md")
    logs = base.with_name(base.name + "-logs")
    todo = []
    for b in books:
        st = state.data["books"].get(b["key"], {"status": "pending"})
        # The status "running" here comes from a runner that stopped, so the book runs again.
        if st["status"] in ("pending", "running") or (st["status"] == "failed" and args.retry_failed):
            todo.append(b)
    if args.max_books:
        todo = todo[:args.max_books]

    if args.dry_run:
        cfg = Path(args.mcp_config) if args.mcp_config else base.with_name(base.name + "-mcp.json")
        print(f"# {len(books)} books in the queue, {len(todo)} to run; state: {state.path}")
        for b in books:
            note = f"  | {b['note']}" if b["note"] else ""
            print(f"#   {state.data['books'].get(b['key'], {}).get('status', 'pending'):8} {b['folder']}{note}")
        for b in todo:
            cmd = build_command(args, b, "$PROMPT", cfg)
            print(f"\ncd {shlex.quote(str(b['folder']))} && {shlex.join(cmd)}")
        if todo:
            print("\n# $PROMPT for the first book:\n" + make_prompt(todo[0]))
        return 0

    lock = base.with_name(base.name + ".lock")
    take_lock(lock)
    try:
        logs.mkdir(exist_ok=True)
        cfg = Path(args.mcp_config) if args.mcp_config else mcp_config(base.with_name(base.name + "-mcp.json"))
        for n, b in enumerate(todo, 1):
            st = state.book(b["key"])
            log = logs / f"{books.index(b) + 1:03d}-{slug(b['folder'].name)}.log"
            print(f"[{now()}] {n}/{len(todo)} {b['folder']}", flush=True)
            try:
                run_book(args, b, st, log, cfg)
            except KeyboardInterrupt:
                st.update({"status": "pending", "error": "interrupted", "finished": now()})
                state.save()
                write_summary(summary, books, state)
                raise
            state.save()
            write_summary(summary, books, state)
            print(f"[{now()}]   {st['status']}" + (f": {st['error']}" if st.get("error") else ""), flush=True)
        write_summary(summary, books, state)
        print(f"summary: {summary}")
    finally:
        lock.unlink(missing_ok=True)
    failed = [b for b in books if state.data["books"].get(b["key"], {}).get("status") == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
