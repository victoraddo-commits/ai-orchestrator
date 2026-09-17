"""Unit tests for KAI Bet validation (§18/§19/§47). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.validation import (
    walk_forward_splits, brier_score, log_loss, calibration_bins,
    expected_calibration_error, drift_score, drift_psi,
)


class TestWalkForward(unittest.TestCase):
    def test_no_future_leakage(self):
        splits = walk_forward_splits(500, train=200, test=50)
        self.assertGreaterEqual(len(splits), 5)
        for train_idx, test_idx in splits:
            self.assertLess(max(train_idx), min(test_idx))  # train strictly before test

    def test_too_small(self):
        self.assertEqual(walk_forward_splits(100, train=200, test=50), [])

    def test_bad_args(self):
        with self.assertRaises(ValueError):
            walk_forward_splits(0)


class TestCalibration(unittest.TestCase):
    def test_perfect_predictions(self):
        self.assertAlmostEqual(brier_score([0.0, 1.0], [0, 1]), 0.0)

    def test_worst_brier(self):
        self.assertAlmostEqual(brier_score([1.0, 0.0], [0, 1]), 1.0)

    def test_log_loss_bounds(self):
        self.assertGreater(log_loss([0.6, 0.4], [1, 0]), 0.0)
        # confident and correct -> low loss
        self.assertLess(log_loss([0.99, 0.01], [1, 0]), 0.05)

    def test_calibration_bins_and_ece(self):
        # 100 predictions at 0.5 with 50% outcomes -> near-perfect calibration
        probs = [0.5] * 100
        outcomes = [1] * 50 + [0] * 50
        ece = expected_calibration_error(probs, outcomes, bins=10)
        self.assertLess(ece, 0.01)
        bins = calibration_bins(probs, outcomes, bins=10)
        used = [b for b in bins if b["n"]]
        self.assertTrue(used)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            brier_score([0.5], [0, 1])
        with self.assertRaises(ValueError):
            brier_score([1.5], [1])


class TestDrift(unittest.TestCase):
    def test_no_drift_on_identical(self):
        d = drift_score([0.5] * 100, [0.5] * 100)
        self.assertFalse(d.drifted)

    def test_mean_shift_detected(self):
        d = drift_score([0.5] * 100, [0.7] * 100)
        self.assertTrue(d.drifted)
        self.assertAlmostEqual(d.mean_shift, 0.2, places=6)

    def test_psi_identical_is_zero(self):
        self.assertAlmostEqual(drift_psi([0.5] * 100, [0.5] * 100), 0.0, places=6)

    def test_psi_shift_positive(self):
        self.assertGreater(drift_psi([0.2] * 100 + [0.8] * 100, [0.5] * 200), 0.0)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            drift_score([], [0.5])


if __name__ == "__main__":
    unittest.main()
