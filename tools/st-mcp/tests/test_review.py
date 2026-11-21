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
        with review.Notes(self.p) as n:
            n.add("001", "content", "content box: cover", 0.8, "a")
            n.add("001", "content", "content box: manual", 0.3, "b")
            n.add("002", "pictures", "picture page: grayscale", 0.9)
        data = review.load(self.p)
        self.assertEqual(data["pages"]["001"]["content"]["decision"], "content box: manual")
        self.assertTrue(data["pages"]["001"]["content"]["check"])
        self.assertFalse(data["pages"]["002"]["pictures"]["check"])
        with review.Notes(self.p) as n:
            n.clear(["002"], "pictures")
        self.assertNotIn("002", review.load(self.p)["pages"])

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
