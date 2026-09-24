# tests/test_v7_core.py — unit pure-core V7 (B0/B1 momentum, skor, ATR, regime)

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.baselines import v7_core as core


class TestMomentum(unittest.TestCase):
    def test_r20_intervals_b0_vs_b1(self):
        # 21 bar naik linear: beda interval => beda r20
        closes = [100.0 + 2.0 * i for i in range(21)]
        r5b, r20b = core.momentum_pct_n(closes, core.B0_R20_INTERVALS)
        r5c, r20c = core.momentum_pct_n(closes, core.B1_R20_INTERVALS)
        # B0: c[-1]/c[-20] = 140/102; B1: 140/100
        self.assertAlmostEqual(r20b, (140 - 102) / 102 * 100, places=6)
        self.assertAlmostEqual(r20c, (140 - 100) / 100 * 100, places=6)
        self.assertGreater(r20c, r20b)
        self.assertAlmostEqual(r5b, r5c)  # r5 identik antar arm

    def test_short_history_r20_zero(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        _, r20 = core.momentum_pct_n(closes, core.B1_R20_INTERVALS)
        self.assertEqual(r20, 0.0)  # paritas produksi: len<21 -> 0

    def test_r5_short_history_zero(self):
        _, r5 = core.momentum_pct_n([100.0, 101.0], 20)
        self.assertEqual(r5, 0.0)


class TestVolumeRatio(unittest.TestCase):
    def test_prior_mean(self):
        vols = [100.0] * 9 + [250.0]  # prior mean = 100
        self.assertAlmostEqual(core.volume_ratio(vols), 2.5)

    def test_single_bar_neutral(self):
        self.assertEqual(core.volume_ratio([100.0]), 1.0)

    def test_zero_prior_mean_neutral(self):
        self.assertEqual(core.volume_ratio([0.0, 0.0, 10.0]), 1.0)


class TestScore(unittest.TestCase):
    def _closes(self, slope=2.2, n=45):
        return [100.0 + slope * i for i in range(n)]

    def test_full_score(self):
        closes = self._closes(slope=2.5)
        vols = [1_000_000.0] * 9 + [2_500_000.0]  # vr = 2.5
        sc = core.score_core(closes, vols, 20)
        self.assertEqual(sc["score"], 100)  # momentum 40 + volume 30 + price 30
        self.assertEqual(sc["momentum_pts"], 40)
        self.assertEqual(sc["volume_pts"], 30)
        self.assertEqual(sc["price_pts"], 30)
        self.assertGreater(sc["r20"], 30.0)  # lolos R23
        self.assertGreater(sc["r5"], 5.0)

    def test_min_bars_none(self):
        self.assertIsNone(core.score_core([1.0] * 19, [1.0] * 19))

    def test_flat_low_score(self):
        closes = [100.0] * 45
        vols = [1_000_000.0] * 45
        sc = core.score_core(closes, vols, 20)
        self.assertEqual(sc["score"], 0)


class TestAtrLevels(unittest.TestCase):
    def _bars(self, n, spread):
        closes = [100.0 + 3.0 * i for i in range(n)]
        highs = [c + spread for c in closes]
        lows = [c - spread for c in closes]
        return highs, lows, closes

    def test_clamp_5pct_min(self):
        # ATR sangat kecil -> sl_dist clamp ke 5% entry
        highs, lows, closes = self._bars(30, 0.05)
        lv = core.atr_levels(highs, lows, closes)
        entry = closes[-1]
        self.assertAlmostEqual(lv["sl_dist"], entry * 0.05, places=6)

    def test_clamp_8pct_max(self):
        highs, lows, closes = self._bars(30, 50.0)
        lv = core.atr_levels(highs, lows, closes)
        entry = closes[-1]
        self.assertAlmostEqual(lv["sl_dist"], entry * 0.08, places=6)
        self.assertAlmostEqual(lv["sl"], entry - 2 * lv["sl_dist"], places=6)
        self.assertAlmostEqual(lv["tp1"], entry + 0.5 * lv["sl_dist"], places=6)
        self.assertAlmostEqual(lv["tp2"], entry + 2 * lv["sl_dist"], places=6)

    def test_insufficient_bars_none(self):
        highs = [101.0] * 10
        lows = [99.0] * 10
        closes = [100.0] * 10
        self.assertIsNone(core.atr_levels(highs, lows, closes))


class TestRegime(unittest.TestCase):
    def test_below_ma200_off(self):
        # input DESC (index 0 = terbaru, konvensi produksi); makin lama makin
        # tinggi -> trend TURUN -> last < MA200 -> OFF (True)
        closes_desc = [5000.0 + i for i in range(250)]
        self.assertTrue(core.regime_cap(closes_desc))

    def test_above_ma200_on(self):
        # DESC; makin lama makin rendah -> trend NAIK -> ON (False)
        closes_desc = [5000.0 - i for i in range(250)]
        self.assertFalse(core.regime_cap(closes_desc))

    def test_short_history_none(self):
        self.assertIsNone(core.regime_cap([5000.0] * 100))


class TestUniverseAvgValue(unittest.TestCase):
    def test_window_20cd(self):
        bars = [(f"2026-09-{d:02d}", 1e6) for d in range(1, 21)]
        # cutoff 09-20: semua bar > 08-31 masuk
        self.assertAlmostEqual(core.universe_avg_value(bars, "2026-09-20"), 1e6)
        # cutoff 09-25: bar 09-01..05 masih dalam 20cd (>= 09-05)
        v = core.universe_avg_value(bars, "2026-09-25")
        self.assertAlmostEqual(v, 1e6)

    def test_empty_window(self):
        self.assertEqual(core.universe_avg_value([], "2026-09-20"), 0.0)


if __name__ == "__main__":
    unittest.main()
