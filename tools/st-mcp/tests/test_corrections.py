# Tests for the corrections log on a synthetic pair: a copy of a finished agent project with known changes.
import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from st_mcp import corrections, ops
from st_mcp.project import Project

SAMPLE = Path(os.environ.get(
    "ST_TEST_PROJECT",
    "/mnt/c/nes/Serijos_HIGH/(Atradimų pasaulis)/Stefan Zweig. Magelano žygis (1989)/agentas.orig.ScanTailor"))


@unittest.skipUnless(SAMPLE.exists(), f"sample project {SAMPLE} not found; set ST_TEST_PROJECT")
class CorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-corr-"))
        self.book = self.tmp / "Book"
        self.book.mkdir()
        self.orig = self.book / "agentas.orig.ScanTailor"
        self.corr = self.book / "agentas.ScanTailor"
        shutil.copy(SAMPLE, self.orig)
        shutil.copy(SAMPLE, self.corr)
        self.log = str(self.tmp / "log.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _edit(self):
        p = Project(self.corr)
        body = [pg for pg in p.pages if p.content(pg.id).get("content_rect_px") and not p.content(pg.id)["empty"]
                and p.layout(pg.id).get("match_size") and p.output(pg.id).get("color_mode") == "bw"]
        body = body[10:]
        want = {}

        def rect(pg):
            return dict(p.content(pg.id)["content_rect_px"])

        pg = body[0]
        r = rect(pg)
        r["width"] -= 50
        p.set_content(pg.id, r)
        want[pg.label] = {"content_box_trim"}
        pg = body[1]
        r = rect(pg)
        r["height"] += 60
        p.set_content(pg.id, r)
        want[pg.label] = {"content_box_numbers"}
        pg = body[2]
        r = rect(pg)
        r["x"] -= 40
        r["width"] += 40
        p.set_content(pg.id, r)
        want[pg.label] = {"content_box_extend"}
        pg = body[3]
        p.set_content(pg.id, {"x": 0, "y": 0, "width": 0, "height": 0})
        want[pg.label] = {"content_empty"}
        pg = body[4]
        p.set_deskew(pg.id, p.deskew_angle(pg.id) + 1.5)
        r = rect(pg)
        r["width"] -= 80
        p.set_content(pg.id, r)
        want[pg.label] = {"deskew_text"}
        pg = body[5]
        p.set_layout(pg.id, {"left": 0.0, "right": 0.0})
        want[pg.label] = {"margins"}
        pg = body[6]
        p.set_layout(pg.id, None, "vcenter" if p.layout(pg.id)["align_v"] != "vcenter" else "top")
        want[pg.label] = {"alignment"}
        pg = body[7]
        p.set_layout(pg.id, None, None, None, False)
        want[pg.label] = {"match_size"}
        pg = body[8]
        p.set_output(pg.id, "color", 300, None, True, True)
        want[pg.label] = {"output_mode", "dpi"}
        pg = body[9]
        zones = p.root.find(f"./filters/output/page[@id='{pg.id}']/zones")
        ET.SubElement(zones, "zone")
        want[pg.label] = {"picture_zones"}
        first = p.pages[0]
        if p.content(first.id).get("content_rect_px"):
            r = rect(first)
            r["width"] -= 30
            p.set_content(first.id, r)
            want[first.label] = {"cover"}
        im = body[11].image
        p.set_rotation(im.id, (p.rotation(im.id) + 90) % 360)
        want[im.path.stem] = {"rotation"}
        im = body[14].image
        p.set_split_layout(im.id, "single-uncut")
        want[im.path.stem] = {"split"}
        p.save()
        return want

    def test_categories_and_no_repeats(self):
        want = self._edit()
        res = ops.corrections_record(str(self.orig), str(self.corr), log=self.log)
        recs = [json.loads(x) for x in Path(self.log).read_text(encoding="utf-8").splitlines()]
        got: dict[str, set] = {}
        for r in recs:
            got.setdefault(r["page"], set()).add(r["category"])
        for label, cats in want.items():
            self.assertEqual(got.get(label), cats, label)
        self.assertEqual(res["written"], len(recs))
        again = ops.corrections_record(str(self.orig), str(self.corr), log=self.log)
        self.assertEqual(again["written"], 0)
        self.assertEqual(again["skipped_duplicates"], len(recs))
        st = ops.corrections_stats(log=self.log)
        self.assertEqual(st["records"], len(recs))
        self.assertEqual(st["books"], 1)
        self.assertIn("content_box_trim", st["by_category"])
        self.assertTrue(st["most_frequent"][0]["workflow_rule"])

    def test_book_id_is_the_same_on_wsl_and_windows(self):
        self.assertEqual(corrections.book_id("/mnt/c/nes/x"), corrections.book_id("C:\\nes\\x"))
        self.assertEqual(corrections.book_id("c:/nes/x/"), "C:/nes/x")

    def test_env_sets_the_log(self):
        os.environ["ST_CORRECTIONS_LOG"] = self.log
        try:
            self.assertEqual(str(corrections.log_path()), self.log)
        finally:
            del os.environ["ST_CORRECTIONS_LOG"]


if __name__ == "__main__":
    unittest.main()
