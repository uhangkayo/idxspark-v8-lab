# baselines/v7_core.py — Pure-core V7 frozen (B0) + corrected (B1) [LAB-05]
#
# Sumber perilaku beku (port best-effort, stdlib-only):
#   /root/.hermes/skills/idx-trading/scripts/scan_daily_v7_quality_gates.py (V7.2)
#   /root/.hermes/skills/idx-trading/scripts/paper_engine.py (V7.3 REAL-EXIT)
# Label forensik: PURE_CORE = hanya komponen harga/volume yang dapat
# direkonstruksi dari snapshot. Yang DIKELUARKAN (tidak ada di snapshot /
# butuh jaringan): anomaly bonus (15), alpha158 bonus (10), fundamental
# EPS/DER (20), gorengan tier, kill-switch, threshold_override learning.
# Skor maksimum pure-core = 100 (bukan 145) — threshold 65 produksi tetap
# dipakai apa adanya; pindah threshold = eksperimen baru, bukan fix bug.
#
# B0_V7_FROZEN  : r20 aktual produksi = 19 interval (close[-1]/close[-20]).
# B1_V7_CORRECTED: r20 = tepat 20 interval (close[-1]/close[-21]) — satu-satunya
#   fix correctness terdaftar yang berada di dalam lingkup pure-core harga
#   (Blueprint §A.1: "Mengubah r20 ke tepat 20 interval bukan rename:
#   versi fitur baru... jangan menyamakan hasil B0 dengan B1").

from typing import Dict, List, Optional, Tuple

# ---- Parameter beku (sumber: scan_daily_v7 + paper_engine) ----
V7_CORE_PARAMS = {
    "threshold_score": 65,          # scan_daily_v7(threshold=65)
    "max_signals": 3,               # fail-open cap
    "cap_regime_on": 2,             # VAR-B: ON -> top-2
    "cap_regime_off": 1,            # VAR-B: OFF -> top-1
    "min_avg_value_20d": 100_000_000.0,  # get_active_tickers min_value
    "univ_vol_min": 1.2,            # volume gate: volume_ratio > 1.2 (score>0)
    "r23_gate_min_pct": 30.0,       # R23: r20 >= 30% hard reject
    "atr_period": 14,
    "sl_dist_lo": 0.05,             # clamp(2*ATR, 5%, 8%)
    "sl_dist_hi": 0.08,
    "sl_mult": 2.0,                 # SL = entry - 2 * sl_dist
    "tp1_mult": 0.5,                # TP1 = entry + 0.5 * sl_dist
    "tp2_mult": 2.0,
    "max_hold_bars": 10,            # paper_engine MAX_HOLD_BARS
    "max_positions": 3,             # paper_engine MAX_POSITIONS
    "cost_rt_flat": 0.0040,         # paper_engine COST_RT (fee produksi)
    "starting_capital": 10_000_000.0,
    "hist_window": 100,             # get_ohlcv_data(limit=100)
    "min_bars": 20,                 # len(df) < 20 -> None
    "regime_ma": 200,
}
B0_R20_INTERVALS = 19
B1_R20_INTERVALS = 20


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0 if b else 0.0


def momentum_pct_n(closes: List[float], r20_intervals: int) -> Tuple[float, float]:
    n = len(closes)
    r5 = _pct(closes[-1], closes[-6]) if n >= 6 else 0.0
    if n >= r20_intervals + 1:
        r20 = _pct(closes[-1], closes[-1 - r20_intervals])
    else:
        r20 = 0.0
    return r5, r20


def volume_ratio(volumes: List[float]) -> float:
    """Produksi: recent = vols[-1]; avg = mean(vols[:-1]) pada jendela
    100 bar (hist_window). avg <= 0 -> 1.0 (fail-netral produksi)."""
    prior = volumes[:-1]
    if not prior:
        return 1.0
    avg = sum(prior) / len(prior)
    return volumes[-1] / avg if avg > 0 else 1.0


def price_vs_ma20(closes: List[float]) -> float:
    ma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else (sum(closes) / len(closes) if closes else 0.0)
    return _pct(closes[-1], ma20) if ma20 else 0.0


def score_core(closes: List[float], volumes: List[float],
               r20_intervals: int = B0_R20_INTERVALS) -> Optional[Dict]:
    """V6 base score (max 100) pure-core. Return None jika bar < min_bars
    (paritas v7_score). Komponen: momentum 40 / volume 30 / price 30."""
    if len(closes) < V7_CORE_PARAMS["min_bars"]:
        return None
    r5, r20 = momentum_pct_n(closes, r20_intervals)
    vr = volume_ratio(volumes)
    pma = price_vs_ma20(closes)

    momentum_score = 0
    if r5 > 5:
        momentum_score += 20
    elif r5 > 2:
        momentum_score += 10
    if r20 > 10:
        momentum_score += 20
    elif r20 > 5:
        momentum_score += 10

    volume_score = 0
    if vr > 2.0:
        volume_score = 30
    elif vr > 1.5:
        volume_score = 20
    elif vr > 1.2:
        volume_score = 10

    price_score = 0
    if pma > 5:
        price_score = 30
    elif pma > 2:
        price_score = 20
    elif pma > 0:
        price_score = 10

    total = momentum_score + volume_score + price_score
    return {"score": total, "r5": r5, "r20": r20, "volume_ratio": vr,
            "price_vs_ma": pma, "momentum_pts": momentum_score,
            "volume_pts": volume_score, "price_pts": price_score}


def atr_levels(highs: List[float], lows: List[float], closes: List[float]) -> Optional[Dict]:
    """ATR14 → entry/SL/TP1/TP2 (compute_atr_levels produksi).
    entry = close terakhir. Return None jika data kurang/invalid."""
    p = V7_CORE_PARAMS
    n = len(closes)
    if n < p["atr_period"] + 1:
        return None
    trs = []
    for i in range(1, n):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - pc), abs(lows[i] - pc)))
    atr = sum(trs[-p["atr_period"]:]) / p["atr_period"]
    entry = closes[-1]
    if entry <= 0 or atr <= 0:
        return None
    sl_dist = min(max(2 * atr, p["sl_dist_lo"] * entry), p["sl_dist_hi"] * entry)
    return {"entry": entry, "sl": entry - sl_dist * p["sl_mult"],
            "tp1": entry + sl_dist * p["tp1_mult"],
            "tp2": entry + sl_dist * p["tp2_mult"], "atr": atr,
            "sl_dist": sl_dist}


def universe_avg_value(bars: List[Tuple[str, float]], cutoff_date: str,
                       window_days: int = 20) -> float:
    """AVG(volume*close) pada trailing 20 HARI KALENDER (produksi:
    date >= date('now','-20 days')). bars = [(date, vol*close)]."""
    from datetime import datetime, timedelta
    lo = (datetime.strptime(cutoff_date, "%Y-%m-%d") - timedelta(days=window_days)).strftime("%Y-%m-%d")
    vals = [vc for d, vc in bars if d > lo]
    return sum(vals) / len(vals) if vals else 0.0


def regime_cap(index_closes_desc: List[float]) -> Optional[bool]:
    """VAR-B: last < MA200 (input urut DESC seperti produksi) -> OFF (True).
    Data < 200 bar -> None (fail-open: cap = max_signals)."""
    p = V7_CORE_PARAMS
    if len(index_closes_desc) < p["regime_ma"]:
        return None
    ma = sum(index_closes_desc) / len(index_closes_desc)
    last = index_closes_desc[0]
    return last < ma
