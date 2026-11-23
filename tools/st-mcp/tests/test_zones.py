# Tests for the fill zone helpers on synthetic pages: ink fit, edge crossings and stray ink at a box side.
import unittest

import numpy as np

from st_mcp import zones


def page(h=400, w=600):
    return np.full((h, w), 255, dtype=np.uint8)


class ZonesTest(unittest.TestCase):
    def test_fit_shrinks_to_the_mark_and_grows_over_its_faint_edge(self):
        g = page()
        g[100:140, 200:300] = 0
        g[100:140, 300:330] = 150
        z = zones.fit_to_ink(g, (190, 90, 310, 150))
        self.assertLessEqual(z[0], 200 - 10)
        self.assertGreaterEqual(z[2], 330)
        self.assertLessEqual(z[1], 100)

    def test_fit_gives_none_without_ink(self):
        self.assertIsNone(zones.fit_to_ink(page(), (10, 10, 50, 50)))

    def test_a_zone_that_cuts_a_text_line_has_a_crossing(self):
        g = page()
        for x in range(100, 500, 14):
            g[200:220, x:x + 9] = 0
        cut = zones.edge_crossings(g, (300, 150, 550, 260))
        self.assertTrue(any(c["edge"] == "left" for c in cut))

    def test_a_zone_around_a_separate_mark_has_no_crossing(self):
        g = page()
        for x in range(100, 250, 14):
            g[200:220, x:x + 9] = 0
        g[300:340, 400:480] = 0
        self.assertEqual(zones.edge_crossings(g, (388, 288, 492, 352)), [])

    def test_ink_outside_the_box_does_not_make_a_crossing(self):
        g = page()
        g[200:220, 280:330] = 0
        box = {"x": 300, "y": 100, "width": 250, "height": 250}
        self.assertEqual(zones.edge_crossings(g, (300, 150, 400, 260), box), [])

    def test_a_small_ink_group_at_the_box_side_is_stray(self):
        g = page()
        for y in range(60, 340, 20):
            g[y:y + 10, 100:450] = 0
        g[200:205, 520:524] = 0
        res = zones.stray_edges(g, {"x": 90, "y": 50, "width": 450, "height": 300})
        self.assertEqual([r["side"] for r in res], ["right"])
        self.assertLessEqual(res[0]["point"]["x"], 450)


if __name__ == "__main__":
    unittest.main()
