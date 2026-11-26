# Tests for the rules that come from the corrections of a person: page width, text column, alignment, photo boxes.
import os
import shutil
import statistics
import tempfile
import unittest
from pathlib import Path

import numpy as np

from st_mcp import checks, ops
from st_mcp.project import Project

SAMPLE = Path(os.environ.get(
    "ST_TEST_PROJECT",
    "/mnt/c/nes/Serijos_HIGH/(Atradimų pasaulis)/Stefan Zweig. Magelano žygis (1989)/agentas.orig.ScanTailor"))


@unittest.skipUnless(SAMPLE.exists(), f"sample project {SAMPLE} not found; set ST_TEST_PROJECT")
class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-rules-"))
        self.proj = self.tmp / "agentas.ScanTailor"
        shutil.copy(SAMPLE, self.proj)
        src = Project(SAMPLE)
        p = Project(self.proj)
        # The copy is in a temporary folder, so each relative path must point to the sample folder.
        for d in p.root.findall("./directories/directory"):
            d.set("path", str(src.local_path(d.get("path"))))
        p.root.set("outputDirectory", str(self.tmp / "out-agentas"))
        p.save()
        (self.tmp / "out-agentas").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_fit_width_gives_zero_side_margins_to_wider_pages(self):
        res = ops.pages_fit_width(str(self.proj))
        p = Project(self.proj)
        for pid, c in ops._body_pages(p):
            if p.output(pid).get("color_mode") not in (None, "bw"):
                continue
            m = p.layout(pid)["margins_mm"]
            if c["content_mm"]["width"] > res["typical_width_mm"]:
                self.assertEqual((m["left"], m["right"]), (0.0, 0.0))
            else:
                self.assertIn(m["left"], (0.0, 10.0))

    def test_text_column_only_narrows_and_keeps_top_and_bottom(self):
        before = Project(self.proj)
        res = ops.pages_fit_text_column(str(self.proj))
        after = Project(self.proj)
        self.assertTrue(res["narrowed"])
        for n in res["narrowed"]:
            pid = after.find_page(n["page"]).id
            a, b = before.content(pid)["content_rect_px"], after.content(pid)["content_rect_px"]
            self.assertGreaterEqual(b["x"], a["x"] - 0.01)
            self.assertLessEqual(b["x"] + b["width"], a["x"] + a["width"] + 0.01)
            self.assertAlmostEqual(b["y"], a["y"], places=3)
            self.assertAlmostEqual(b["height"], a["height"], places=3)
            # The new box must stay close to the text column of the book, never far narrower.
            self.assertGreater(n["width_mm_after"], res["text_column_mm"] - 2.0)

    def test_photo_box_drops_thin_edge_strips(self):
        m = np.zeros((60, 40), dtype=bool)
        m[10:35, 8:32] = True   # photo
        m[42, 10:20] = True      # caption line
        m[5:55, 1] = True        # page edge strip
        m[58, 5:35] = True       # page edge at the bottom
        y0, y1, x0, x1 = checks._pictures_and_captions(m)
        self.assertEqual((y0, y1, x0, x1), (10, 43, 8, 32))

    def test_align_auto_keeps_photo_plates_centered(self):
        p = Project(self.proj)
        body = ops._body_pages(p)
        pid = body[len(body) // 2][0]
        p.set_output(pid, "color", 300, None, None, None, None, None, None, None)
        p.save()
        ops.pages_align_auto(str(self.proj))
        self.assertEqual(Project(self.proj).layout(pid)["align_v"], "vcenter")

    def test_align_auto_puts_full_pages_on_the_page_number_side(self):
        res = ops.pages_align_auto(str(self.proj), number_position="top")
        p = Project(self.proj)
        body = ops._body_pages(p)
        med = statistics.median(c["content_mm"]["height"] for _, c in body)
        full = [pid for pid, c in body if c["content_mm"]["height"] >= 0.985 * med
                and p.output(pid).get("color_mode") in (None, "bw")]
        self.assertEqual(res["number_position"], "top")
        self.assertTrue(full)
        self.assertTrue(all(p.layout(pid)["align_v"] == "top" for pid in full))

    def test_text_skew_is_small_on_a_straight_text_page(self):
        p = Project(self.proj)
        body = ops._body_pages(p)
        heights = [c["content_mm"]["height"] for _, c in body]
        med = statistics.median(heights)
        pid = next(i for i, c in body if abs(c["content_mm"]["height"] - med) < 1)
        angle, lines = checks.text_skew(p, pid)
        self.assertGreater(lines, 10)
        self.assertLessEqual(abs(angle), 0.5)


if __name__ == "__main__":
    unittest.main()
