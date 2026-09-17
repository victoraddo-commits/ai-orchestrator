"""Unit tests for KAI Bet calibration / value gate (§13/§19). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.calibration import (
    longshot_margin, shrink_toward_market, calibrated_value,
)


class TestCalibration(unittest.TestCase):
    def test_longshot_margin_grows_with_odds(self):
        self.assertLess(longshot_margin(1.5), longshot_margin(5.0))
        self.assertLess(longshot_margin(5.0), longshot_margin(20.0))
        self.assertAlmostEqual(longshot_margin(1.5), 0.02)  # below 2.0 -> base

    def test_shrink_toward_market_at_tails(self):
        # extreme low model prob pulled up toward market
        p = shrink_toward_market(0.05, 0.12)
        self.assertGreater(p, 0.05)
        self.assertLess(p, 0.12)
        # central probability unchanged
        self.assertEqual(shrink_toward_market(0.5, 0.5), 0.5)

    def test_longshot_false_value_suppressed(self):
        # model says 8% on a 30.0 longshot (implied 3.3%) -> naive edge +4.7%,
        # but the longshot margin (0.02+0.025*28=0.72) must reject it
        v = calibrated_value(0.08, 30.0)
        self.assertEqual(v.value, "none")
        self.assertGreater(v.required_edge, v.edge)

    def test_genuine_value_still_passes(self):
        # strong favourite fairly priced: model 0.80 @ 1.30 (implied 0.769) -> edge +3.1%
        v = calibrated_value(0.80, 1.30, market_p=0.769)
        self.assertIn(v.value, ("low", "moderate", "high"))

    def test_uncertainty_raises_bar(self):
        a = calibrated_value(0.6, 2.0, uncertainty=0.0)
        b = calibrated_value(0.6, 2.0, uncertainty=0.8)
        self.assertGreater(b.required_edge, a.required_edge)

    def test_bad_odds(self):
        with self.assertRaises(ValueError):
            calibrated_value(0.5, 1.0)


if __name__ == "__main__":
    unittest.main()
