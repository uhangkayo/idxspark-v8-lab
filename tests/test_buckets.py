# tests/test_buckets.py — unit bucket 7-enum terminal + freshness v3

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.selection import buckets as bk
from idxspark_lab.data import freshness as fr


def cand(**kw):
    base = {"data_ok": True, "n_bars": 100, "feature_ok": True, "scored": True,
            "score": 80, "volume_pts": 30, "r20": 40.0, "gates_passed": True,
            "overlap_open": False, "selected": False}
    base.update(kw)
    return base


class TestBuckets(unittest.TestCase):
    def test_seven_buckets_exclusive(self):
        self.assertEqual(len(bk.BUCKETS), 7)
        self.assertEqual(len(set(bk.BUCKETS)), 7)

    def test_data_unusable(self):
        self.assertEqual(bk.assign(cand(data_ok=False, data_reason="QA_V3_FAIL"))["bucket"],
                         "DATA_UNUSABLE")

    def test_ineligible(self):
        self.assertEqual(bk.assign(cand(n_bars=19))["bucket"], "INELIGIBLE")
        self.assertEqual(bk.assign(cand(n_bars=19))["primary_reason"],
                         "INSUFFICIENT_HISTORY")

    def test_feature_invalid(self):
        r = bk.assign(cand(feature_ok=False, feature_reason="ATR_FAIL"))
        self.assertEqual(r["bucket"], "FEATURE_INVALID")
        self.assertEqual(r["primary_reason"], "ATR_FAIL")

    def test_gate_rejected_reasons(self):
        r = bk.assign(cand(gates_passed=False, score=50, volume_pts=0, r20=10.0))
        self.assertEqual(r["bucket"], "GATE_REJECTED")
        self.assertIn("BELOW_THRESHOLD", r["reasons"])
        self.assertIn("VOLUME_GATE", r["reasons"])
        self.assertIn("R23_GATE", r["reasons"])
        # primary_reason tunggal, reasons boleh >1 tapi bucket satu
        self.assertEqual(r["primary_reason"], "BELOW_THRESHOLD")

    def test_overlap_qns(self):
        r = bk.assign(cand(overlap_open=True))
        self.assertEqual(r["bucket"], "QUALIFIED_NOT_SELECTED")
        self.assertEqual(r["primary_reason"], "RISK_BLOCKED_OVERLAP")

    def test_selected(self):
        self.assertEqual(bk.assign(cand(selected=True))["bucket"], "SELECTED")

    def test_cap_blocked_qns(self):
        r = bk.assign(cand())
        self.assertEqual(r["bucket"], "QUALIFIED_NOT_SELECTED")
        self.assertEqual(r["primary_reason"], "CAP_BLOCKED")

    def test_invariant(self):
        recs = [bk.assign(c) for c in (cand(), cand(selected=True),
                                       cand(n_bars=5), cand(score=10, gates_passed=False))]
        counts = bk.bucket_counts(recs)
        self.assertEqual(sum(counts.values()), len(recs))
        self.assertTrue(bk.invariant_ok(recs))
        self.assertEqual(counts["INELIGIBLE"], 1)
        self.assertEqual(counts["GATE_REJECTED"], 1)
        self.assertEqual(counts["SELECTED"], 1)
        self.assertEqual(counts["QUALIFIED_NOT_SELECTED"], 1)


class TestFreshness(unittest.TestCase):
    CAL = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07",
           "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14",
           "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21",
           "2026-09-22", "2026-09-23"]

    def test_gap_from_calendar_end(self):
        self.assertEqual(fr.tail_gap_sessions("2026-09-23", self.CAL), 0)
        # 09-16 -> sesi setelahnya: 17,18,21,22,23 = 5
        self.assertEqual(fr.tail_gap_sessions("2026-09-16", self.CAL), 5)
        self.assertEqual(fr.tail_gap_sessions("2026-09-01", self.CAL), 16)

    def test_non_session_date(self):
        # 2026-09-05 Sabtu: sesi pasar setelahnya dari 09-07 = 13
        self.assertEqual(fr.tail_gap_sessions("2026-09-05", self.CAL), 13)

    def test_before_calendar_none(self):
        self.assertIsNone(fr.tail_gap_sessions("2025-01-01", self.CAL))

    def test_classify_v3_stale(self):
        rec = {"n_adj": 300, "n_close_only": 0, "missing_sessions": 0,
               "span_sessions": 300, "ohlc_viol": 0, "nonpos_close": 0}
        st, rs = fr.classify_v3(rec, 14)
        self.assertEqual(st, "PROFILE_OK_STALE_TAIL")
        self.assertEqual(rs, ["TAIL_GAP_14_SESSIONS"])

    def test_classify_v3_fresh(self):
        rec = {"n_adj": 300, "n_close_only": 0, "missing_sessions": 0,
               "span_sessions": 300, "ohlc_viol": 0, "nonpos_close": 0}
        st, rs = fr.classify_v3(rec, 3)
        self.assertEqual(st, "PROFILE_OK_FRESH")
        self.assertEqual(rs, [])

    def test_classify_v3_unknown_gap(self):
        rec = {"n_adj": 300, "n_close_only": 0, "missing_sessions": 0,
               "span_sessions": 300, "ohlc_viol": 0, "nonpos_close": 0}
        st, rs = fr.classify_v3(rec, None)
        self.assertEqual(st, "PROFILE_OK_FRESH")
        self.assertEqual(rs, ["TAIL_GAP_UNKNOWN"])

    def test_classify_v3_unusable_dominates(self):
        rec = {"n_adj": 0, "n_close_only": 0, "missing_sessions": None,
               "span_sessions": 0, "ohlc_viol": 0, "nonpos_close": 0}
        st, rs = fr.classify_v3(rec, 14)
        self.assertEqual(st, "DATA_UNUSABLE")
        self.assertEqual(rs, ["NO_PRICE_DATA"])

    def test_v3_funnel(self):
        recs = [{"profile_status_v3": s} for s in
                ("PROFILE_OK_FRESH", "PROFILE_OK_FRESH", "PROFILE_OK_STALE_TAIL",
                 "DATA_UNUSABLE")]
        f = fr.v3_funnel(recs)
        self.assertEqual(f["PROFILE_OK_FRESH"], 2)
        self.assertEqual(sum(f.values()), 4)

    def test_v2_status_reimplemented(self):
        # paritas dengan universe.classify 0.1
        rec = {"n_adj": 100, "n_close_only": 0, "missing_sessions": 30,
               "span_sessions": 100, "ohlc_viol": 0, "nonpos_close": 0}
        st, rs = fr.rec_status_v2(rec)
        self.assertEqual(st, "DATA_UNUSABLE")
        self.assertEqual(rs, ["MISSING_SESSIONS_GT20PCT"])


if __name__ == "__main__":
    unittest.main()
