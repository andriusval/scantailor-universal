# Tests for the book queue with a fake command in place of claude.
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_queue  # noqa: E402

# The fake agent counts its runs and writes the report, except in a folder whose name contains "fail".
FAKE = """
import sys, pathlib
here = pathlib.Path.cwd()
with open(here.parent / "calls.txt", "a") as fh:
    fh.write(here.name + "\\n")
assert "-p" in sys.argv and "--mcp-config" in sys.argv
if "fail" in here.name:
    sys.exit(3)
if "slow" in here.name:
    import subprocess, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (here / "child.pid").write_text(str(child.pid))
    time.sleep(60)
if "stale" in here.name:
    sys.exit(0)
if "noreport" not in here.name:
    (here / "out-agentas" / "review").mkdir(parents=True, exist_ok=True)
    (here / "out-agentas" / "review" / "report.md").write_text("ok")
    (here / "out-agentas" / "review" / "index.html").write_text("<html></html>")
"""


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-queue-"))
        for name in ("Book A", "Book fail", "Book noreport", "Book C"):
            (self.tmp / name).mkdir()
        fake = self.tmp / "fake.py"
        fake.write_text(FAKE)
        self.cmd = f"{sys.executable} {fake}"
        self.queue = self.tmp / "books.txt"
        self.queue.write_text("# test queue\n" + "\n".join(
            str(self.tmp / n) for n in ("Book A", "Book fail", "Book noreport", "Missing")) +
            f"\n{self.tmp / 'Book C'} | covers are 001 and 9999\n\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_queue(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_queue.main([str(self.queue), "--command", self.cmd, "--timeout-min", "1", *extra])
        return code, out.getvalue()

    def state(self):
        data = json.loads((self.tmp / "books.state.json").read_text(encoding="utf-8"))
        return {Path(k).name: v for k, v in data["books"].items()}

    def calls(self):
        f = self.tmp / "calls.txt"
        return f.read_text().split("\n")[:-1] if f.exists() else []

    def test_dry_run_changes_nothing(self):
        code, out = self.run_queue("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("--permission-mode dontAsk", out)
        self.assertIn("covers are 001 and 9999", out)
        self.assertFalse((self.tmp / "books.state.json").exists())
        self.assertEqual(self.calls(), [])

    def test_states_resume_and_retry(self):
        code, _ = self.run_queue()
        self.assertEqual(code, 1)
        st = self.state()
        self.assertEqual(st["Book A"]["status"], "done")
        self.assertEqual(st["Book C"]["status"], "done")
        self.assertEqual((st["Book fail"]["status"], st["Book fail"]["exit_code"]), ("failed", 3))
        self.assertIn("report.md", st["Book noreport"]["error"])
        self.assertEqual(st["Missing"]["error"], "book folder not found")
        self.assertTrue(Path(st["Book A"]["log"]).exists())
        self.assertEqual(sorted(self.calls()), ["Book A", "Book C", "Book fail", "Book noreport"])
        summary = (self.tmp / "books-summary.md").read_text(encoding="utf-8")
        self.assertEqual(summary.count("[review](file:"), 2)
        self.assertIn("| Book fail | failed (exit code 3)", summary)

        # A second start skips done and failed books.
        self.run_queue()
        self.assertEqual(len(self.calls()), 4)

        # A book left as running after a crash runs again.
        data = json.loads((self.tmp / "books.state.json").read_text(encoding="utf-8"))
        key = next(k for k in data["books"] if k.endswith("Book A"))
        data["books"][key]["status"] = "running"
        (self.tmp / "books.state.json").write_text(json.dumps(data))
        self.run_queue()
        self.assertEqual(self.calls()[-1], "Book A")
        self.assertEqual(self.state()["Book A"]["attempts"], 2)

        self.run_queue("--retry-failed")
        self.assertEqual(sorted(self.calls()[5:]), ["Book fail", "Book noreport"])
        self.assertFalse((self.tmp / "books.lock").exists())

    def test_old_report_and_timeout(self):
        import os
        for name in ("Book stale", "Book slow"):
            (self.tmp / name / "out-agentas" / "review").mkdir(parents=True)
            (self.tmp / name / "out-agentas" / "review" / "report.md").write_text("old")
            os.utime(self.tmp / name / "out-agentas" / "review" / "report.md", (1, 1))
        self.queue.write_text(f"{self.tmp / 'Book stale'}\n{self.tmp / 'Book slow'}\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_queue.main([str(self.queue), "--command", self.cmd, "--timeout-min", "0.03"])
        st = self.state()
        self.assertIn("wrote no", st["Book stale"]["error"])
        self.assertIn("timeout", st["Book slow"]["error"])
        pid = int((self.tmp / "Book slow" / "child.pid").read_text())
        self.assertFalse(run_queue.pid_alive(pid) and Path(f"/proc/{pid}").exists()
                         and "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2])

    def test_lock_blocks_a_second_runner(self):
        import os
        (self.tmp / "books.lock").write_text(str(os.getppid()))
        with self.assertRaises(SystemExit):
            self.run_queue()

    def test_windows_paths_in_the_queue(self):
        if sys.platform.startswith("win"):
            self.skipTest("POSIX only")
        self.assertEqual(run_queue.local_folder("C:\\nes\\A B"), Path("/mnt/c/nes/A B"))
        self.assertEqual(run_queue.windows_form(Path("/mnt/c/nes/A B")), "C:\\nes\\A B")

    def test_prompt_has_the_workflow_steps(self):
        p = run_queue.make_prompt({"folder": Path("/mnt/c/nes/x"), "note": ""})
        self.assertIn("project_create", p)
        self.assertIn("out-agentas/review/report.md", p)
        self.assertIn("C:\\nes\\x", p)


if __name__ == "__main__":
    unittest.main()
