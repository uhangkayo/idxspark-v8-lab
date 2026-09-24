# selection/buckets.py — Terminal bucket 7-enum eksklusif [Blueprint §S]
#
# Setiap kandidat universe punya SATU bucket terminal:
#   DATA_UNUSABLE | INELIGIBLE | FEATURE_INVALID | MODEL_INVALID |
#   GATE_REJECTED | QUALIFIED_NOT_SELECTED | SELECTED
# Jumlah 7 bucket = universe (invariant). primary_reason tunggal; extra
# reasons boleh >1 tapi TIDAK dijumlahkan sebagai jumlah emiten.
# SELECTED selalu berarti simulasi lab, bukan order nyata.
#
# Urutan keputusan (paritas pipeline produksi, urutan gate source):
#   1. DATA_UNUSABLE  — gagal QA data v3 (termasuk stale tail gap)
#   2. INELIGIBLE     — sejarah < 20 bar (v7_score return None karena bar)
#   3. FEATURE_INVALID — cukup bar tapi fitur tak terhitung (ATR None /
#                       universe likuiditas 0 / avg_volume invalid)
#   4. GATE_REJECTED  — skor < 65 | volume gate (vr <= 1.2) | R23 (r20 < 30%)
#   5. MODEL_INVALID  — tidak ada model di arm B0/B1 (populasi 0 by design;
#                       slot tetap ada supaya aritmetika invariant stabil
#                       saat arm M0 ditambahkan)
#   6. QUALIFIED_NOT_SELECTED — lolos semua gate tapi terpotong cap / overlap
#   7. SELECTED       — top-N cap regime
#
# Overlap (ticker masih open di buku simulasi sendiri) = alasan
# QUALIFIED_NOT_SELECTED (RISK_BLOCKED_OVERLAP) — paritas overlap filter
# produksi yang jalan SEBELUM cap (buang dari results).

from typing import Dict, List, Optional

BUCKETS = ("DATA_UNUSABLE", "INELIGIBLE", "FEATURE_INVALID",
           "MODEL_INVALID", "GATE_REJECTED", "QUALIFIED_NOT_SELECTED",
           "SELECTED")


def assign(cand: Dict) -> Dict:
    """cand keys: data_ok (bool), n_bars, feature_ok (bool),
    scored (bool), score, volume_pts, r20, gates_passed (bool),
    overlap_open (bool), selected (bool).
    Return {bucket, primary_reason, reasons}."""
    reasons: List[str] = []
    if not cand.get("data_ok", True):
        return _r("DATA_UNUSABLE", cand.get("data_reason", "QA_V3_FAIL"))
    if cand.get("n_bars", 0) < 20:
        return _r("INELIGIBLE", "INSUFFICIENT_HISTORY")
    if not cand.get("feature_ok", True):
        return _r("FEATURE_INVALID", cand.get("feature_reason", "FEATURE_FAIL"))
    if not cand.get("scored", False):
        return _r("FEATURE_INVALID", "SCORE_COMPUTE_FAIL")
    if not cand.get("gates_passed", True):
        rs = []
        if cand.get("score", 0) < 65:
            rs.append("BELOW_THRESHOLD")
        if cand.get("volume_pts", 0) <= 0:
            rs.append("VOLUME_GATE")
        if cand.get("r20", 0.0) < 30.0:
            rs.append("R23_GATE")
        primary = rs[0] if rs else "GATE_FAIL"
        return {"bucket": "GATE_REJECTED", "primary_reason": primary,
                "reasons": rs or [primary]}
    if cand.get("overlap_open", False):
        return _r("QUALIFIED_NOT_SELECTED", "RISK_BLOCKED_OVERLAP")
    if cand.get("selected", False):
        return _r("SELECTED", "CAP_TOP_N")
    return _r("QUALIFIED_NOT_SELECTED", "CAP_BLOCKED")


def _r(bucket: str, reason: str) -> Dict:
    return {"bucket": bucket, "primary_reason": reason, "reasons": [reason]}


def bucket_counts(records: List[Dict]) -> Dict[str, int]:
    counts = {b: 0 for b in BUCKETS}
    for rec in records:
        counts[rec["bucket"]] = counts.get(rec["bucket"], 0) + 1
    return counts


def invariant_ok(records: List[Dict]) -> bool:
    """Jumlah bucket = universe; bucket valid; tanpa kandidat tanpa bucket."""
    counts = bucket_counts(records)
    return sum(counts.values()) == len(records) and \
        all(rec["bucket"] in BUCKETS for rec in records)
