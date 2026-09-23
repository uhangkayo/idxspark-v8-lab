# reporting/report_md.py — Laporan Markdown lab (bahasa Indonesia, jujur)

from typing import Any, Dict


def render_report_md(coverage: Dict[str, Any], run_id: str,
                     publication: Dict[str, Any]) -> str:
    f = coverage["funnel"]
    cal = coverage["calendar"]
    u = coverage["universe"]
    cov = coverage["aux_coverage"]
    water = coverage.get("per_source_watermarks") or {}

    def c(t: str) -> str:
        v = cov.get(t, {})
        rows = v.get("rows")
        if rows is None:
            return f"⚠ {v.get('error', 'n/a')}"
        return f"{rows:,} ({v.get('min', '—')} → {v.get('max', '—')})"

    def pct(a: int, b: int) -> str:
        return f"{100.0 * a / b:.1f}%" if b else "—"

    lines = []
    ap = lines.append
    ap("# IdxSpark V8-Lab — Laporan Coverage (DIAGNOSTIC_ONLY)")
    ap("")
    ap("> **EKSPERIMEN/SIMULASI LAB — bukan sinyal produksi.** Tidak ada order,")
    ap("> tidak ada promosi model, tidak mengubah `/signals/today`.")
    ap("")
    ap(f"- Run: `{run_id[:16]}…`")
    ap(f"- Snapshot: `{coverage['snapshot_id'][:16]}…` — cutoff efektif **{coverage['effective_cutoff']}**")
    ap(f"- Mode: `{publication['temporal_status']}` / result `{publication['result']}`")
    ap("")
    ap("## Funnel profil (disjoint, jumlah = universe)")
    ap("")
    ap(f"- n_universe: **{f['n_universe']:,}**")
    ap(f"- PROFILE_OK: **{f['PROFILE_OK']:,}** ({pct(f['PROFILE_OK'], f['n_universe'])})")
    ap(f"- DATA_UNUSABLE: **{f['DATA_UNUSABLE']:,}** ({pct(f['DATA_UNUSABLE'], f['n_universe'])})")
    ap(f"- Invariant jumlah bucket = universe: **{'OK' if f['invariant_ok'] else 'GAGAL'}**")
    ap("")
    if coverage["unusable_reasons"]:
        ap("Alasan DATA_UNUSABLE (multi-alasan dihitung sekali per emiten):")
        for r, n in sorted(coverage["unusable_reasons"].items(), key=lambda x: -x[1]):
            ap(f"- {r}: {n}")
        ap("")
    ap("## Universe & basis data")
    ap("")
    ap(f"- Emiten adjusted (`prices`): {u['n_adjusted']}")
    ap(f"- Emiten close-only delisted: {u['n_delisted_prices']}")
    ap(f"- Survivorship `delisted_final`: {u['n_survivorship']}")
    ap(f"- Universe gabungan: {u['n_universe']}")
    ap(f"- Kalender bursa estimasi: {cal['sessions']:,} sesi ({cal['first']} → {cal['last']}) — {cal['method']}")
    ap("")
    ap("## Watermark per sumber (common cutoff = MIN)")
    ap("")
    for src, tbls in water.items():
        parts = [f"`{t}`={d or '—'}" for t, d in tbls.items() if d] or ["(tanpa kolom tanggal)"]
        ap(f"- **{src}**: " + ", ".join(parts))
    ap("")
    ap("## Coverage sumber pelengkap")
    ap("")
    ap(f"- sbl_daily: {c('sbl_daily')}")
    ap(f"- sbl_lendable: {c('sbl_lendable')}")
    ap(f"- sbl_top: {c('sbl_top')}")
    ap(f"- broker_monthly: {c('broker_monthly')}")
    ap(f"- stock_monthly: {c('stock_monthly')}")
    ap(f"- flow stock_summary: {c('flow_stock_summary')}")
    ap(f"- dividends: {c('dividends')}")
    ap(f"- news: {c('news')}")
    ap(f"- sb_news: {c('sb_news')}")
    ap(f"- sectors: {c('sectors')}")
    ap("")
    bad = [r for r in coverage["unusable_tickers"]]
    if bad:
        ap("## Emiten DATA_UNUSABLE (semua)")
        ap("")
        for r in bad:
            ap(f"- `{r['ticker']}` — {'+'.join(r['reasons'])}; adj={r.get('n_adj')}, "
               f"close_only={r.get('n_close_only')}, raw={r.get('n_raw')}, "
               f"last={r.get('last')}, lrp={r.get('last_real_print')}")
        ap("")
    ap("## Keterbatasan (jujur, tanpa gula-gula)")
    ap("")
    ap("- Profil 0.1 adalah DIAGNOSTIC_ONLY: tidak ada model, tidak ada seleksi,")
    ap("  tidak ada qualified/selected. Bucket seleksi 7-enum baru dipakai mulai 0.2.")
    ap("- Kalender bursa adalah estimator dari data (≥60% emiten aktif), bukan")
    ap("  kalender resmi IDX; libur nasional parsial bisa terlewat.")
    ap("- Stale tail = indikasi carry-forward, bukan bukti; keputusan cutoff")
    ap("  pakai `last_real_print` survivorship saat tersedia.")
    ap("- SBL/broker flow hanya pelengkap coverage di 0.1; kualitas barisnya")
    ap("  (freshness guard produksi) belum diaudit ulang di lab.")
    ap("- Semua angka di atas hanya untuk snapshot ini; snapshot baru = run baru.")
    ap("")
    return "\n".join(lines)
