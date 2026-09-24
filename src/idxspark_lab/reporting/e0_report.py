# reporting/e0_report.py — Markdown report E0 (B0 ↔ B1) dari e0.json

from typing import Any, Dict


def _fmt_pct(v) -> str:
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"


def _fmt_rp(v) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    return f"Rp{v:,.0f}".replace(",", ".")


def render_e0_report_md(e0: Dict[str, Any], run_id: str) -> str:
    s0 = e0["arms"]["B0"]["summary"]
    s1 = e0["arms"]["B1"]["summary"]
    d = e0["decision_dates"]
    lines = [
        "# V8-LAB 0.2 — E0 Baseline Parity (B0 ↔ B1)",
        "",
        f"- Run: `{run_id[:16]}…`",
        f"- Snapshot cutoff: **{e0['cutoff']}**",
        f"- Tanggal keputusan: {d[0]} → {d[-1]} ({e0['n_decision_dates']} sesi)",
        f"- Fee arm: {s0['fee_profile']} | Sensitivitas AJAIB_EXACT dilampirkan",
        "",
        "Semua hasil = simulasi lab (DIAGNOSTIC_ONLY). SELECTED bukan order.",
        "",
        "## Ringkasan per arm",
        "",
        "| Metrik | B0_V7_FROZEN (r20=19) | B1_V7_CORRECTED (r20=20+fresh) |",
        "|---|---|---|",
        f"| Trade tertutup | {s0['n_trades_closed']} | {s1['n_trades_closed']} |",
        f"| Posisi open (akhir) | {s0['n_positions_open_end']} | {s1['n_positions_open_end']} |",
        f"| Win rate | {s0['win_rate_pct']}% | {s1['win_rate_pct']}% |",
        f"| Mean ret/trade | {_fmt_pct(s0['mean_ret_pct'])} | {_fmt_pct(s1['mean_ret_pct'])} |",
        f"| Median ret/trade | {_fmt_pct(s0['median_ret_pct'])} | {_fmt_pct(s1['median_ret_pct'])} |",
        f"| Total net (fee produksi) | {_fmt_rp(s0['total_net'])} | {_fmt_rp(s1['total_net'])} |",
        f"| Equity akhir (mark) | {_fmt_rp(s0['final_equity_marked'])} | {_fmt_rp(s1['final_equity_marked'])} |",
        f"| Mean bars held | {s0['mean_bars_held']} | {s1['mean_bars_held']} |",
        "",
        "## Exit reasons",
        "",
        "| Reason | B0 | B1 |",
        "|---|---|---|",
    ]
    reasons = sorted(set(s0["exit_reasons"]) | set(s1["exit_reasons"]))
    for r in reasons:
        lines.append(f"| {r} | {s0['exit_reasons'].get(r, 0)} | {s1['exit_reasons'].get(r, 0)} |")

    lines += [
        "",
        "## E0 delta (perbedaan seleksi)",
        "",
        f"- Trade hanya di B0: **{len(e0['e0_delta']['only_b0'])}**",
        f"- Trade hanya di B1: **{len(e0['e0_delta']['only_b1'])}**",
        f"- Trade sama (ticker+tanggal): {e0['e0_delta']['common']}",
        "",
        "Atribusi: (1) fix r20 19→20 interval menggeser momentum_pts & R23;",
        "(2) fix data-as-of B1 memblokir kandidat dengan bar terakhir < tanggal",
        "keputusan (B0 mereplikasi bug produksi: kandidat stale tetap diskor).",
        "",
        "## Sensitivitas fee (harga/qty sama, fee AJAIB_EXACT)",
        "",
        "| Arm | Net PROD_FLAT | Net AJAIB_EXACT | Delta fee |",
        "|---|---|---|---|",
    ]
    for arm in ("B0", "B1"):
        s = e0["ajaib_sensitivity"][arm]
        lines.append(f"| {arm} | {_fmt_rp(s['total_net_prod_flat'])} | "
                     f"{_fmt_rp(s['total_net_ajaib_exact'])} | "
                     f"{_fmt_rp(s['delta_fee_impact'])} |")

    lines += [
        "",
        "## Bucket 7-enum (rata-rata emiten per tanggal keputusan, arm B0)",
        "",
        "| Bucket | Avg emiten/hari |",
        "|---|---|",
    ]
    for b, v in sorted(e0["bucket_avg_per_date"].items(), key=lambda x: -x[1]):
        lines.append(f"| {b} | {v} |")
    lines += [
        "",
        "DATA_UNUSABLE dihitung terpisah oleh run profile v3 (QA penuh);",
        "MODEL_INVALID = 0 by design pada arm baseline (tanpa model).",
        "",
        "## Catatan kejujuran",
        "",
        "- Pure-core: skor maksimum 100 (bukan 145) — anomaly/alpha158/",
        "fundamental/DER/gorengan/kill-switch TIDAK direplay (di luar snapshot",
        "atau butuh jaringan). Label reimplementation best-effort.",
        "- Threshold 65 dipakai apa adanya pada skala 100 (konservatif:",
        "produksi memakai skala 145 dengan bonus di luar lingkup).",
        "- Fill = close tanggal keputusan (paritas level engine produksi);",
        "varian fill next-open = trial terpisah yang belum dijalankan.",
        "- Buku mulai Rp10.000.000, MAX_POSITIONS 3, lot 100.",
        "",
    ]
    return "\n".join(lines)
