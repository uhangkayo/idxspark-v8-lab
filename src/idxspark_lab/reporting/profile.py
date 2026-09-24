# reporting/profile.py — Profil coverage S1-S3-lite → coverage.json
# Semua angka punya denominator eksplisit; missing ≠ nol.
# spec_version="v3": tambah klasifikasi freshness jendela-akhir
# (PROFILE_OK_FRESH / PROFILE_OK_STALE_TAIL / DATA_UNUSABLE).

import json
from typing import Any, Dict, List

from ..adapters.snapshot_reader import SnapshotReader
from ..data import freshness as fr
from ..data import universe as uni


def coverage_report(reader: SnapshotReader, spec_version: str = "v2") -> Dict[str, Any]:
    universe, umeta = uni.resolve_universe(reader)
    calendar = uni.market_calendar(reader)
    manifest = reader.manifest
    common_cutoff = manifest.get("effective_cutoff")

    tickers = uni.profile_tickers(reader, universe, calendar, common_cutoff)
    classified = []
    counts = {"PROFILE_OK": 0, "DATA_UNUSABLE": 0}
    v3_counts = {s: 0 for s in fr.V3_STATUSES}
    reason_counts: Dict[str, int] = {}
    for rec in tickers:
        status, reasons = uni.classify(rec)
        rec["profile_status"] = status
        rec["unusable_reasons"] = reasons
        counts[status] += 1
        for r in reasons:
            reason_counts[r] = reason_counts.get(r, 0) + 1
        if spec_version == "v3":
            gap = fr.tail_gap_sessions(rec.get("last"), calendar)
            rec["tail_gap_sessions"] = gap
            st3, rs3 = fr.classify_v3(rec, gap)
            rec["profile_status_v3"] = st3
            rec["freshness_reasons_v3"] = rs3
            v3_counts[st3] += 1
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

    # peringkat data_unusable + (v3) stale tail untuk laporan
    unusable = [r for r in classified if r["profile_status"] == "DATA_UNUSABLE"]
    stale_v3 = [r for r in classified if r.get("profile_status_v3") == "PROFILE_OK_STALE_TAIL"]

    funnel: Dict[str, Any] = {"n_universe": len(universe), **counts,
                              "invariant_ok": counts["PROFILE_OK"] + counts["DATA_UNUSABLE"] == len(universe)}
    report = {
        "schema_version": "1",
        "kind": "COVERAGE_PROFILE",
        "spec_version": spec_version,
        "snapshot_id": manifest.get("snapshot_id"),
        "effective_cutoff": common_cutoff,
        "per_source_watermarks": manifest.get("per_source_watermarks"),
        "calendar": {"sessions": len(calendar),
                     "first": calendar[0] if calendar else None,
                     "last": calendar[-1] if calendar else None,
                     "method": ">=60% emiten aktif (60 sesi trailing) punya bar"},
        "universe": umeta,
        "funnel": funnel,
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
    if spec_version == "v3":
        report["funnel_v3"] = {"n_universe": len(universe), **v3_counts,
                               "stale_tail_gap_threshold_sessions": fr.STALE_TAIL_GAP_SESSIONS,
                               "invariant_ok": sum(v3_counts.values()) == len(universe)}
        report["stale_tail_tickers_v3"] = [
            {"ticker": r["ticker"], "last": r.get("last"),
             "tail_gap_sessions": r.get("tail_gap_sessions"),
             "reasons": r.get("freshness_reasons_v3")}
            for r in sorted(stale_v3, key=lambda x: -(x.get("tail_gap_sessions") or 0))]
        report["notes"].append(
            "v3: PROFILE_OK_STALE_TAIL = bar terakhir emiten > "
            f"{fr.STALE_TAIL_GAP_SESSIONS} sesi pasar sebelum sesi terakhir "
            "(klaster gap kalender; bukan carry-forward close identik).")
    return report
