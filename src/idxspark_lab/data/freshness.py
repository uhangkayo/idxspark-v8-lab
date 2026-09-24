# data/freshness.py — Check freshness jendela-akhir (profile v3) [audit 24-09]
#
# Temuan audit 0.1: klaster 62 emiten bar terakhir 09-03 (vs pasar 09-23,
# 14 sesi gap) lolos PROFILE_OK karena stale_tail 0.1 hanya mendeteksi
# close identik berurutan (carry-forward), BUKAN gap kalender.
# v3 menambahkan tail_gap_sessions = jumlah sesi pasar antara bar
# terakhir emiten dan tanggal pasar maksimum, lalu:
#   tail_gap > STALE_TAIL_GAP_SESSIONS (default 5) -> PROFILE_OK_STALE_TAIL
# Bucket v3: PROFILE_OK_FRESH | PROFILE_OK_STALE_TAIL | DATA_UNUSABLE
# (diagnostik; TIDAK mengubah verdict v2 — run v2 tetap bisa direplay).

from typing import Dict, List, Optional, Tuple

STALE_TAIL_GAP_SESSIONS = 5

V3_STATUSES = ("PROFILE_OK_FRESH", "PROFILE_OK_STALE_TAIL", "DATA_UNUSABLE")


def tail_gap_sessions(last_date: Optional[str], calendar: List[str]) -> Optional[int]:
    """Jumlah sesi pasar SETELAH bar terakhir emiten (0 = fresh di sesi
    terakhir). calendar urut naik. None jika tanggal tak ada di kalender
    (data di luar bursa / tak diketahui)."""
    if not last_date or not calendar:
        return None
    if last_date in calendar:
        return len(calendar) - 1 - calendar.index(last_date)
    if last_date < calendar[0]:
        return None
    # tanggal tak di kalender (mis. non-sesi): hitung sesi pasar setelahnya
    n = 0
    for d in calendar:
        if d > last_date:
            n += 1
    return n if n else None


def classify_v3(rec: Dict, gap: Optional[int],
                threshold: int = STALE_TAIL_GAP_SESSIONS) -> Tuple[str, List[str]]:
    """Klasifikasi v3 di atas record profile_tickers 0.1:
    v2-unusable tetap DATA_UNUSABLE; selain itu bedakan fresh vs stale."""
    base_status, reasons = rec_status_v2(rec)
    if base_status == "DATA_UNUSABLE":
        return "DATA_UNUSABLE", reasons
    if gap is None:
        return "PROFILE_OK_FRESH", ["TAIL_GAP_UNKNOWN"]
    if gap > threshold:
        return "PROFILE_OK_STALE_TAIL", [f"TAIL_GAP_{gap}_SESSIONS"]
    return "PROFILE_OK_FRESH", []


def rec_status_v2(rec: Dict) -> Tuple[str, List[str]]:
    """Reimplementasi classify() universe.py 0.1 (v2) untuk dipakai v3
    tanpa import circular."""
    reasons: List[str] = []
    if rec.get("n_adj", 0) == 0 and rec.get("n_close_only", 0) == 0:
        reasons.append("NO_PRICE_DATA")
    if rec.get("n_adj", 0) > 0:
        ms = rec.get("missing_sessions")
        ss = rec.get("span_sessions")
        if ms is not None and ss:
            if ss > 0 and ms / ss > 0.20:
                reasons.append("MISSING_SESSIONS_GT20PCT")
        ov = rec.get("ohlc_viol", 0)
        if ov is not None and ov > 3 and ov / rec["n_adj"] > 0.002:
            reasons.append("OHLC_VIOLATION")
        if rec.get("nonpos_close", 0):
            reasons.append("NONPOSITIVE_CLOSE")
    if reasons:
        return "DATA_UNUSABLE", reasons
    return "PROFILE_OK", []


def v3_funnel(records: List[Dict]) -> Dict[str, int]:
    counts = {s: 0 for s in V3_STATUSES}
    for rec in records:
        counts[rec["profile_status_v3"]] = counts.get(rec["profile_status_v3"], 0) + 1
    return counts
