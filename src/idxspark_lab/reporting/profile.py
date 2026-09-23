# reporting/profile.py — Profil coverage S1-S3-lite → coverage.json
# Semua angka punya denominator eksplisit; missing ≠ nol.

import json
from typing import Any, Dict, List

from ..adapters.snapshot_reader import SnapshotReader
from ..data import universe as uni


def coverage_report(reader: SnapshotReader) -> Dict[str, Any]:
    universe, umeta = uni.resolve_universe(reader)
    calendar = uni.market_calendar(reader)
    manifest = reader.manifest
    common_cutoff = manifest.get("effective_cutoff")

    tickers = uni.profile_tickers(reader, universe, calendar, common_cutoff)
    classified = []
    counts = {"PROFILE_OK": 0, "DATA_UNUSABLE": 0}
    reason_counts: Dict[str, int] = {}
    for rec in tickers:
        status, reasons = uni.classify(rec)
        rec["profile_status"] = status
        rec["unusable_reasons"] = reasons
        counts[status] += 1
        for r in reasons:
            reason_counts[r] = reason_counts.get(r, 0) + 1
        classified.append(rec)

    # coverage sumber pelengkap
    cov: Dict[str, Any] = {}
    for tbl, ds, sql in [
        ("sbl_daily", "idx_monthly", "SELECT COUNT(*), MIN(Date), MAX(Date) FROM sbl_daily"),
        ("sbl_lendable", "idx_monthly", "SELECT COUNT(*), MIN(Date), MAX(Date) FROM sbl_lendable"),
        ("sbl_top", "idx_monthly", "SELECT COUNT(*), MIN(Date), MAX(Date) FROM sbl_top"),
        ("broker_monthly", "idx_monthly", "SELECT COUNT(*), MIN(Month), MAX(Month) FROM broker_monthly"),
        ("stock_monthly", "idx_monthly", "SELECT COUNT(*), MIN(Month), MAX(Month) FROM stock_monthly"),
        ("flow_stock_summary", "idx_flow", "SELECT COUNT(*), MIN(Date), MAX(Date) FROM stock_summary"),
        ("dividends", "idx_ohlcv", "SELECT COUNT(*), MIN(date), MAX(date) FROM dividends"),
        ("news", "idx_ohlcv", "SELECT COUNT(*) FROM news"),
        ("sb_news", "idx_ohlcv", "SELECT COUNT(*) FROM sb_news"),
        ("sectors", "ticker_sectors", "SELECT COUNT(*) FROM sectors"),
    ]:
        try:
            row = reader.rows(ds, sql)[0]
            cov[tbl] = {"rows": row[0], "min": row[1] if len(row) > 1 else None,
                        "max": row[2] if len(row) > 2 else None}
        except Exception as e:  # tabel tak ada/berubah → tercatat, bukan nol
            cov[tbl] = {"rows": None, "error": str(e).split(":")[0]}

    # peringkat data_unusable untuk laporan
    unusable = [r for r in classified if r["profile_status"] == "DATA_UNUSABLE"]

    report = {
        "schema_version": "1",
        "kind": "COVERAGE_PROFILE",
        "snapshot_id": manifest.get("snapshot_id"),
        "effective_cutoff": common_cutoff,
        "per_source_watermarks": manifest.get("per_source_watermarks"),
        "calendar": {"sessions": len(calendar),
                     "first": calendar[0] if calendar else None,
                     "last": calendar[-1] if calendar else None,
                     "method": ">=60% emiten aktif (60 sesi trailing) punya bar"},
        "universe": umeta,
        "funnel": {"n_universe": len(universe), **counts,
                   "invariant_ok": counts["PROFILE_OK"] + counts["DATA_UNUSABLE"] == len(universe)},
        "unusable_reasons": reason_counts,
        "unusable_tickers": [
            {"ticker": r["ticker"], "reasons": r["unusable_reasons"],
             "n_adj": r.get("n_adj"), "n_close_only": r.get("n_close_only"),
             "n_raw": r.get("n_raw"), "last": r.get("last"),
             "last_real_print": r.get("last_real_print"),
             "stale_tail": r.get("stale_tail"),
             "missing_sessions": r.get("missing_sessions"),
             "span_sessions": r.get("span_sessions")}
            for r in unusable],
        "aux_coverage": cov,
        "notes": [
            "prices = basis adjusted produksi; prices_raw = raw OHLCV; kedua basis dicatat terpisah.",
            "Basis harga snapshot: " + str([f"{d['name']}:{d['rows']} bar" for d in manifest.get("datasets", [])]),
        ],
    }
    return report
