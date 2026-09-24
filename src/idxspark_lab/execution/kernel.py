# execution/kernel.py — Satu kernel simulasi transaksi [LAB-06, Blueprint §10]
#
# Kontrak V7.3 REAL-EXIT (paper_engine.py produksi, port 1:1):
#   * fill entry = level entry sinyal (= close tanggal keputusan) —
#     paritas perilaku engine 08:00 produksi. Varian fill next-open =
#     trial terpisah (belum dijalankan; Blueprint §10 melarang mencampur).
#   * Kronologi event dalam bar, urutan prioritas:
#       open >= TP1 -> TP_GAP   (exit @open)
#       open <= SL  -> SL_GAP   (exit @open, slippage)
#       low  <= SL  -> STOP_LOSS (exit @level SL; SL menang saat TP & SL
#                                 tersentuh bar sama — asumsi konservatif)
#       high >= TP1 -> TAKE_PROFIT_1 (exit @level TP1; TP2 tidak pernah
#                                     exit sendiri — limit TP1 lebih dekat)
#   * Tidak kena level: bars_held >= 10 -> TIME_EXIT @close terakhir.
#   * Suspensi: tidak ada bar baru >= 10 sesi pasar sejak bar terakhir
#     ticker -> TIME_EXIT_SUSPENDED @close terakhir yang diketahui.
#
# Fee dua profil (TIDAK dicampur dalam satu arm):
#   PROD_FLAT   : round-trip flat 0.40% — paritas paper engine produksi.
#   AJAIB_EXACT : fee verified broker Ajaib — beli round_half_up(notional
#                 × 0.00151303), jual × 0.00251303 (PPh final 0.1% di jual),
#                 validasi per rupiah (ledger live user).

import math
from typing import Dict, List, Optional, Tuple

FEE_PROFILES = {
    "PROD_FLAT": {"rt": 0.0040},
    "AJAIB_EXACT": {"buy": 0.00151303, "sell": 0.00251303},
}


def round_half_up(x: float) -> int:
    """round-half-up ke rupiah (bukan banker's rounding Python)."""
    return int(math.floor(x + 0.5))


def fees_buy(notional: float, profile: str) -> float:
    p = FEE_PROFILES[profile]
    if "rt" in p:
        return notional * p["rt"] / 2.0
    return float(round_half_up(notional * p["buy"]))


def fees_sell(notional: float, profile: str) -> float:
    p = FEE_PROFILES[profile]
    if "rt" in p:
        return notional * p["rt"] / 2.0
    return float(round_half_up(notional * p["sell"]))


def exit_event_for_bar(bar: Dict, pos: Dict, bars_held: int,
                       max_hold: int) -> Optional[Dict]:
    """Satu bar → event exit V7.3 atau None. bar = {date, open, high,
    low, close}. pos = {entry, sl, tp1}. bars_held = jumlah bar sejak
    sig_date SEBELUM bar ini (paper_engine: len(bars) di loop)."""
    o, h, l = bar["open"], bar["high"], bar["low"]
    if o >= pos["tp1"]:
        return {"reason": "TP_GAP", "price": o, "date": bar["date"]}
    if o <= pos["sl"]:
        return {"reason": "SL_GAP", "price": o, "date": bar["date"]}
    if l <= pos["sl"]:
        return {"reason": "STOP_LOSS", "price": pos["sl"], "date": bar["date"]}
    if h >= pos["tp1"]:
        return {"reason": "TAKE_PROFIT_1", "price": pos["tp1"], "date": bar["date"]}
    if bars_held >= max_hold:
        return {"reason": "TIME_EXIT", "price": bar["close"], "date": bar["date"]}
    return None


class ExitSimulator:
    """Evaluasi posisi terhadap stream bar berikutnya (kronologi V7.3)."""

    def __init__(self, max_hold: int = 10, suspended_gap_bars: int = 10):
        self.max_hold = max_hold
        self.suspended_gap = suspended_gap_bars

    def run(self, pos: Dict, bars_after: List[Dict],
            market_gap_fn=None) -> Optional[Dict]:
        """bars_after = bar ticker setelah sig_date (kronologis).
        market_gap_fn(last_ticker_date) -> jumlah sesi pasar sejak tanggal
        itu (untuk TIME_EXIT_SUSPENDED). Return None jika masih open di
        akhir data (censored)."""
        if not bars_after:
            if market_gap_fn is not None:
                gap = market_gap_fn(pos["sig_date"])
                if gap is not None and gap >= self.suspended_gap:
                    return {"reason": "TIME_EXIT_SUSPENDED",
                            "price": pos["last_known_close"],
                            "date": pos["sig_date"]}
            return None
        for i, b in enumerate(bars_after):
            ev = exit_event_for_bar(b, pos, i + 1, self.max_hold)
            if ev is not None:
                ev["bars_held"] = i + 1
                return ev
        return None  # masih open (censored) di akhir snapshot


def simulate_trade(entry: float, sl: float, tp1: float, qty: int,
                   sig_date: str, bars_after: List[Dict],
                   fee_profile: str = "PROD_FLAT",
                   last_known_close: Optional[float] = None,
                   market_gap_fn=None) -> Optional[Dict]:
    """Satu trade end-to-end: fill -> exit -> P&L net fee.
    qty dalam lembar (lot 100). Return trade record atau None (open).
    PnL net = gross sell − gross buy − fee_buy − fee_sell."""
    pos = {"entry": entry, "sl": sl, "tp1": tp1, "sig_date": sig_date,
           "last_known_close": last_known_close if last_known_close is not None else entry}
    sim = ExitSimulator()
    ev = sim.run(pos, bars_after, market_gap_fn)
    if ev is None:
        return None
    buy_notional = entry * qty
    sell_notional = ev["price"] * qty
    fb = fees_buy(buy_notional, fee_profile)
    fs = fees_sell(sell_notional, fee_profile)
    net = sell_notional - buy_notional - fb - fs
    return {"entry": entry, "exit": ev["price"], "qty": qty,
            "sig_date": sig_date, "exit_date": ev["date"],
            "exit_reason": ev["reason"], "bars_held": ev.get("bars_held", 0),
            "fee_buy": fb, "fee_sell": fs, "net": net,
            "ret_pct": net / buy_notional * 100.0 if buy_notional else 0.0,
            "fee_profile": fee_profile}


def position_size(equity: float, cash: float, entry: float,
                  max_positions: int = 3, lot: int = 100) -> int:
    """Sizing produksi: alloc = min(eq/max_positions, cash*0.98);
    qty = floor(alloc/entry / lot) * lot. Return 0 jika < 1 lot."""
    alloc = min(equity / max_positions, cash * 0.98)
    if entry <= 0:
        return 0
    qty = int(alloc / entry // lot) * lot
    return qty if qty >= lot else 0
