# tests/test_kernel.py — unit kernel transaksi: kronologi exit V7.3,
# fee PROD_FLAT/AJAIB_EXACT, sizing, suspensi.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.execution import kernel as kern


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


POS = {"entry": 100.0, "sl": 90.0, "tp1": 105.0, "sig_date": "2026-09-01",
       "last_known_close": 100.0}


class TestChronology(unittest.TestCase):
    def test_tp_gap_open_above_tp1(self):
        ev = kern.exit_event_for_bar(bar("d", 106.0, 107.0, 99.0, 106.0), POS, 1, 10)
        self.assertEqual(ev["reason"], "TP_GAP")
        self.assertEqual(ev["price"], 106.0)

    def test_sl_gap_open_below_sl(self):
        ev = kern.exit_event_for_bar(bar("d", 89.0, 95.0, 88.0, 90.0), POS, 1, 10)
        self.assertEqual(ev["reason"], "SL_GAP")
        self.assertEqual(ev["price"], 89.0)

    def test_sl_wins_over_tp_same_bar(self):
        # high >= tp1 DAN low <= sl di bar sama -> STOP_LOSS (konservatif)
        ev = kern.exit_event_for_bar(bar("d", 100.0, 106.0, 89.0, 100.0), POS, 1, 10)
        self.assertEqual(ev["reason"], "STOP_LOSS")
        self.assertEqual(ev["price"], 90.0)

    def test_stop_loss_low_touch(self):
        ev = kern.exit_event_for_bar(bar("d", 100.0, 103.0, 90.0, 99.0), POS, 1, 10)
        self.assertEqual(ev["reason"], "STOP_LOSS")

    def test_take_profit_high_touch(self):
        ev = kern.exit_event_for_bar(bar("d", 100.0, 105.0, 99.0, 104.0), POS, 1, 10)
        self.assertEqual(ev["reason"], "TAKE_PROFIT_1")
        self.assertEqual(ev["price"], 105.0)

    def test_time_exit_at_10th_bar(self):
        ev = kern.exit_event_for_bar(bar("d", 100.0, 103.0, 97.0, 101.0), POS, 10, 10)
        self.assertEqual(ev["reason"], "TIME_EXIT")
        self.assertEqual(ev["price"], 101.0)

    def test_no_event_before_hold(self):
        ev = kern.exit_event_for_bar(bar("d", 100.0, 103.0, 97.0, 101.0), POS, 5, 10)
        self.assertIsNone(ev)

    def test_tp2_never_exits(self):
        # TP2 lebih jauh dari TP1; limit TP1 kena duluan
        pos2 = dict(POS, tp1=110.0)
        ev = kern.exit_event_for_bar(bar("d", 100.0, 108.0, 99.0, 107.0), pos2, 1, 10)
        self.assertIsNone(ev)  # 108 < tp1 110: tidak exit


class TestSuspended(unittest.TestCase):
    def test_suspended_gap(self):
        sim = kern.ExitSimulator()
        pos = dict(POS, last_known_close=98.0)
        ev = sim.run(pos, [], market_gap_fn=lambda d: 12)
        self.assertEqual(ev["reason"], "TIME_EXIT_SUSPENDED")
        self.assertEqual(ev["price"], 98.0)

    def test_gap_below_threshold_open(self):
        sim = kern.ExitSimulator()
        ev = sim.run(dict(POS), [], market_gap_fn=lambda d: 3)
        self.assertIsNone(ev)

    def test_no_bars_no_fn_open(self):
        sim = kern.ExitSimulator()
        self.assertIsNone(sim.run(dict(POS), []))

    def test_bars_take_priority_over_gap(self):
        sim = kern.ExitSimulator()
        ev = sim.run(dict(POS), [bar("d2", 106.0, 107.0, 99.0, 106.0)],
                     market_gap_fn=lambda d: 99)
        self.assertEqual(ev["reason"], "TP_GAP")


class TestSimulateTrade(unittest.TestCase):
    def test_net_prod_flat(self):
        bars = [bar("d2", 106.0, 107.0, 99.0, 106.0)]  # TP_GAP @106
        tr = kern.simulate_trade(100.0, 90.0, 105.0, 100, "2026-09-01", bars,
                                 fee_profile="PROD_FLAT")
        buy = 10_000.0
        sell = 10_600.0
        self.assertAlmostEqual(tr["net"], sell - buy - 0.002 * buy - 0.002 * sell,
                               places=6)
        self.assertAlmostEqual(tr["fee_buy"], 20.0)
        self.assertAlmostEqual(tr["fee_sell"], 21.2)

    def test_ajaib_exact_rounding(self):
        bars = [bar("d2", 106.0, 107.0, 99.0, 106.0)]
        tr = kern.simulate_trade(100.0, 90.0, 105.0, 100, "2026-09-01", bars,
                                 fee_profile="AJAIB_EXACT")
        # notional beli 10_000: round_half_up(10_000*0.00151303)=round(15.1303)=15
        self.assertEqual(tr["fee_buy"], 15.0)
        # notional jual 10_600: 0.00251303*10_600=26.638 -> round_half_up=27
        self.assertEqual(tr["fee_sell"], 27.0)
        self.assertAlmostEqual(tr["net"], 10_600 - 10_000 - 15 - 27)

    def test_open_returns_none(self):
        self.assertIsNone(kern.simulate_trade(
            100.0, 90.0, 105.0, 100, "2026-09-01", []))

    def test_round_half_up_semantics(self):
        self.assertEqual(kern.round_half_up(2.5), 3)   # bukan banker's (2)
        self.assertEqual(kern.round_half_up(-2.5), -2)  # floor(-2.5+0.5)=-2
        self.assertEqual(kern.round_half_up(151.303), 151)


class TestPositionSize(unittest.TestCase):
    def test_alloc_min_of_third_and_cash(self):
        # eq 12jt, cash 9jt: min(4jt, 8.82jt) = 4jt; entry 200 -> 20000 lembar
        self.assertEqual(kern.position_size(12_000_000, 9_000_000, 200.0), 20000)

    def test_cash_bound(self):
        # eq 30jt, cash 5jt: min(10jt, 4.9jt) = 4.9jt; entry 200:
        # 4.9jt/200 = 24500 -> 245 lot -> 24500 lembar
        self.assertEqual(kern.position_size(30_000_000, 5_000_000, 200.0), 24500)

    def test_below_one_lot_zero(self):
        self.assertEqual(kern.position_size(300_000, 300_000, 5000.0), 0)

    def test_zero_entry(self):
        self.assertEqual(kern.position_size(1_000_000, 1_000_000, 0.0), 0)


if __name__ == "__main__":
    unittest.main()
