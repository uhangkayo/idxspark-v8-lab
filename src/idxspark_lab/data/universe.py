# data/universe.py — Resolusi universe + pemeriksaan data per emiten (S1-S3-lite)
# Data contract: missing/invalid TIDAK menjadi nol; every ticker terminal bucket.
# Basis harga: prices = adjusted (back-adjusted oleh produksi), prices_raw =
# raw OHLCV. Kedua basis dicatat di laporan, tidak dicampur.

from typing import Any, Dict, List, Optional, Tuple

from .calendar import calendar_from_price_rows

MIN_HISTORY_SESSIONS = 250
STALE_TAIL_MIN_BARS = 5


def _iter_dates(day_sets: Dict[str, int]) -> List[str]:
    return sorted(day_sets)


def resolve_universe(reader) -> Tuple[List[str], Dict[str, Any]]:
    """Universe = semua ticker dalam prices (adjusted) ∪ prices_delisted ∪
    survivorship delisted_final. Bukan hanya yang masih hidup."""
    tick_adj = {r[0] for r in reader.rows("idx_ohlcv", "SELECT DISTINCT ticker FROM prices")}
    tick_del = {r[0] for r in reader.rows("idx_ohlcv", "SELECT DISTINCT ticker FROM prices_delisted")}
    tick_surv = {r[0] for r in reader.rows("idx_survivorship", "SELECT ticker FROM delisted_final")}
    universe = sorted(tick_adj | tick_del | tick_surv)
    meta = {
        "n_adjusted": len(tick_adj), "n_delisted_prices": len(tick_del),
        "n_survivorship": len(tick_surv), "n_universe": len(universe),
    }
    return universe, meta


def market_calendar(reader) -> List[str]:
    rows = reader.rows("idx_ohlcv", "SELECT date, ticker FROM prices")
    return calendar_from_price_rows(rows)


def profile_tickers(reader, universe: List[str], calendar: List[str],
                    common_cutoff: str) -> List[Dict[str, Any]]:
    """Pemeriksaan per emiten (subset S3 yang bisa dihitung di 0.1):
    history, missing days, zero-volume, OHLC violation, stale tail carry-forward,
    konsistensi delisting vs last_real_print, cakupan raw vs adjusted."""
    cal = set(calendar)
    cal_idx = {d: i for i, d in enumerate(calendar)}

    survivorship = {}
    for r in reader.rows(
            "idx_survivorship",
            "SELECT ticker, last_seen, note, reason, exit_type FROM delisted_final"):
        survivorship[r[0]] = {"last_seen": r[1], "note": r[2], "reason": r[3],
                              "exit_type": r[4] or ""}
    # meta last_real_print dari prices_delisted_meta (jebakan ekor flat)
    lrp = {}
    for r in reader.rows(
            "idx_ohlcv",
            "SELECT ticker, last_real_print, flat_tail_bars, last_date FROM prices_delisted_meta"):
        lrp[r[0]] = {"last_real_print": r[1], "flat_tail_bars": r[2], "last_date": r[3]}

    issues: List[Dict[str, Any]] = []
    for t in universe:
        rec: Dict[str, Any] = {"ticker": t}
        rows = reader.rows(
            "idx_ohlcv",
            "SELECT date, open, high, low, close, volume FROM prices WHERE ticker=? ORDER BY date",
            (t,))
        rec["n_adj"] = len(rows)
        if not rows:
            # cek close-only delisted
            drows = reader.rows(
                "idx_ohlcv",
                "SELECT COUNT(*), MIN(date), MAX(date) FROM prices_delisted WHERE ticker=?",
                (t,))
            rec.update({"n_close_only": drows[0][0], "first": drows[0][1], "last": drows[0][2],
                        "basis": "CLOSE_ONLY_DELISTED"})
            raw_n = reader.rows(
                "idx_ohlcv", "SELECT COUNT(*) FROM prices_raw WHERE ticker=?", (t,))
            rec["n_raw"] = raw_n[0][0]
        else:
            rec.update({"first": rows[0][0], "last": rows[-1][0], "basis": "ADJUSTED",
                        "n_close_only": 0})
            dates = [r[0] for r in rows]
            rec["n_raw"] = reader.rows(
                "idx_ohlcv", "SELECT COUNT(*) FROM prices_raw WHERE ticker=?", (t,))[0][0]
            rec["zero_vol"] = sum(1 for r in rows if not r[5])
            rec["ohlc_viol"] = sum(
                1 for r in rows
                if r[1] is not None and r[2] is not None and r[3] is not None
                and (r[2] < r[3] or (r[1] and r[1] > r[2]) or (r[4] and r[4] > r[2])
                     or (r[4] is not None and r[4] <= 0)))
            rec["nonpos_close"] = sum(1 for r in rows if r[4] is not None and r[4] <= 0)
            # missing sessions antara first..last pada kalender bursa
            if dates:
                fi = cal_idx.get(dates[0])
                li = cal_idx.get(dates[-1])
                if fi is not None and li is not None and li > fi:
                    span = set(calendar[fi:li + 1])
                    rec["missing_sessions"] = len(span - set(dates))
                    rec["span_sessions"] = len(span)
                else:
                    rec["missing_sessions"] = None
                    rec["span_sessions"] = None
            # ekor stale: close identik berurutan di ujung
            stale = 0
            last_close = rows[-1][4]
            for r in reversed(rows):
                if r[4] == last_close:
                    stale += 1
                else:
                    break
            rec["stale_tail"] = stale
        # survivorship konsistensi
        if t in survivorship:
            rec["survivorship"] = survivorship[t]
        if t in lrp:
            rec["last_real_print"] = lrp[t]["last_real_print"]
            rec["flat_tail_bars"] = lrp[t]["flat_tail_bars"]
        issues.append(rec)
    return issues


def classify(rec: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Bucket diagnostik 0.1: PROFILE_OK | DATA_UNUSABLE + alasan disjoint.
    Multi-alasan dihitung satu kali (primary reason pertama menang)."""
    reasons: List[str] = []
    if rec["n_adj"] == 0 and rec.get("n_close_only", 0) == 0:
        reasons.append("NO_PRICE_DATA")
    if rec.get("n_adj", 0) > 0:
        if rec.get("missing_sessions") is not None and rec.get("span_sessions"):
            if rec["span_sessions"] > 0 and \
               rec["missing_sessions"] / rec["span_sessions"] > 0.20:
                reasons.append("MISSING_SESSIONS_GT20PCT")
        if rec.get("ohlc_viol", 0) > 0:
            reasons.append("OHLC_VIOLATION")
        if rec.get("nonpos_close", 0) > 0:
            reasons.append("NONPOSITIVE_CLOSE")
    if reasons:
        return "DATA_UNUSABLE", reasons
    return "PROFILE_OK", []
