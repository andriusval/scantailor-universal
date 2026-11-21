# Tests for the decision notes and the review sheet on a copy of a finished agent project.
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from st_mcp import ops, review
from st_mcp.project import Project

SAMPLE = Path(os.environ.get(
    "ST_TEST_PROJECT",
    "/mnt/c/nes/Serijos_HIGH/(Atradimų pasaulis)/Stefan Zweig. Magelano žygis (1989)/agentas.orig.ScanTailor"))


@unittest.skipUnless(SAMPLE.exists(), f"sample project {SAMPLE} not found; set ST_TEST_PROJECT")
class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-review-"))
        self.proj = self.tmp / "agentas.ScanTailor"
        shutil.copy(SAMPLE, self.proj)
        p = Project(self.proj)
        for d in p.root.findall("./directories/directory"):
            d.set("path", str(p.local_path(d.get("path"))))
        src_out = p.output_dir
        p.root.set("outputDirectory", str(self.tmp / "out-agentas"))
        p.save()
        (self.tmp / "out-agentas").mkdir()
        self.p = Project(self.proj)
        self.labels = [pg.label for pg in self.p.pages]
        for lab in self.labels[:4]:
            tif = src_out / (self.p.output_name(self.p.find_page(lab).id) + ".tif")
            if tif.exists():
                shutil.copy(tif, self.tmp / "out-agentas" / tif.name)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_notes_replace_and_clear(self):
        a, b = self.p.pages[0], self.p.pages[1]
        with review.Notes(self.p) as n:
            n.add(a.id, "content", "content box: cover", 0.8, "a")
            n.add(a.id, "content", "content box: manual", 0.3, "b")
            n.add(b.id, "pictures", "picture page: grayscale", 0.9)
        data = review.load(self.p)
        self.assertEqual(data["pages"][a.label]["content"]["decision"], "content box: manual")
        self.assertTrue(data["pages"][a.label]["content"]["check"])
        self.assertFalse(data["pages"][b.label]["pictures"]["check"])
        with review.Notes(self.p) as n:
            n.clear([b.id], "pictures")
        self.assertNotIn(b.label, review.load(self.p)["pages"])

    def test_agent_notes_with_other_decisions_stay(self):
        lab = self.labels[2]
        ops.review_note(str(self.proj), [lab], "cover chosen", "dark edge", 0.5)
        ops.review_note(str(self.proj), [lab], "orientation none", "upright text", 0.9)
        self.assertEqual(len(review.load(self.p)["pages"][lab]), 2)

    def test_bw_output_clears_the_output_note(self):
        lab = self.labels[3]
        ops.pages_set_output(str(self.proj), [lab], color_mode="color", dpi=300)
        self.assertIn("output", review.load(self.p)["pages"][lab])
        ops.pages_set_output(str(self.proj), [lab], color_mode="bw", dpi=600)
        self.assertNotIn("output", review.load(self.p)["pages"].get(lab, {}))

    def test_sheet_puts_doubtful_pages_first(self):
        sel = self.labels[:8]
        ops.review_note(str(self.proj), [sel[5]], "spread kept", "map over both pages", 0.3)
        ops.review_note(str(self.proj), [sel[6]], "cover", "clear edge", 0.9)
        res = ops.review_sheet(str(self.proj), sel, thumb_px=120, engine="python")
        html = Path(res["html"]).read_text(encoding="utf-8")
        self.assertEqual(res["pages"], 8)
        self.assertEqual([x["page"] for x in res["to_check"]], [sel[5]])
        self.assertLess(html.index(f">{sel[5]}<"), html.index(f">{sel[6]}<"))
        self.assertLess(html.index(f">{sel[6]}<"), html.index(f">{sel[0]}<"))
        self.assertIn("CHECK IN GUI", html)
        self.assertNotIn("src=\"/", html)
        self.assertNotIn("http", html)
        self.assertGreater(res["output_thumbnails"], 0)
        for src in [x.split('"')[0] for x in html.split('src="')[1:]]:
            self.assertTrue((Path(res["html"]).parent / src).exists(), src)
        rj = json.loads((Path(res["html"]).parent / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rj["to_check"][0]["page"], sel[5])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(SAMPLE.exists(), f"sample project {SAMPLE} not found; set ST_TEST_PROJECT")
class UprightConfirmTest(unittest.TestCase):
    setUp = ReviewTest.setUp
    tearDown = ReviewTest.tearDown

    def _doubt(self, lab, flag=None):
        pid = self.p.find_page(lab).id
        with review.Notes(self.p) as n:
            n.add(pid, "alignment", "align bottom", 0.4, ops.SIDEWAYS_DOUBT)
            if flag:
                n.add(pid, "check_content", "flagged: " + flag, 0.5, "x")
        return pid

    def test_upright_clears_only_the_plain_doubt(self):
        a = self._doubt(self.labels[0])
        b = self._doubt(self.labels[1], "ink_below_box, text_runs_vertically")
        self.assertEqual(ops._sideways_doubts(self.p, [a, b]), [a])
        self.assertEqual(ops._confirm_upright(self.p, [a, b]), [self.labels[0]])
        data = review.load(self.p)["pages"]
        self.assertFalse(data[self.labels[0]]["alignment"]["check"])
        self.assertTrue(data[self.labels[1]]["alignment"]["check"])

    def test_detector_error_keeps_the_sheet(self):
        self._doubt(self.labels[0])
        old = ops._detect_orientation

        def broken(items):
            raise KeyError("image")
        ops._detect_orientation = broken
        try:
            res = ops.review_sheet(str(self.proj), engine="python")
        finally:
            ops._detect_orientation = old
        self.assertTrue(Path(res["html"]).exists())
        self.assertNotIn("upright_confirmed", res)
