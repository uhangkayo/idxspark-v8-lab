# evaluation/replay.py — E0 replay B0_V7_FROZEN ↔ B1_V7_CORRECTED [LAB-05/06]
#
# Replay per tanggal keputusan (sesi pasar, urut naik) di atas snapshot:
#   universe aktif (likuiditas 20 hari kalender) → skor pure-core V7 →
#   gate (threshold/volume/R23) → regime cap VAR-B → seleksi top-N
#   (tie-break urutan likuiditas; sort stabil) → buku simulasi 1 kernel
#   (entry @close keputusan, exit V7.3, fee profil).
#
# Dua arm:
#   B0: r20 = 19 interval; kandidat stale tetap diskor & bisa terpilih
#       (paritas bug data-as-of produksi).
#   B1: r20 = 20 interval; kandidat wajib fresh (bar terakhir = tanggal
#       keputusan) — fix data-as-of terdaftar.
# Semua hasil = simulasi lab; SELECTED bukan order nyata.

from typing import Any, Dict, List, Optional, Tuple

from ..baselines import v7_core as core
from ..execution import kernel as kern

WINDOW_BACK_DAYS = 800  # kalender; cukup utk 252 sesi keputusan + lookback


def load_window(reader, cutoff: str, back_days: int = WINDOW_BACK_DAYS) -> Dict[str, Dict]:
    """prices (adjusted) jendela back_days sebelum cutoff → per ticker
    arrays paralel (urut tanggal naik)."""
    from datetime import datetime, timedelta
    lo = (datetime.strptime(cutoff, "%Y-%m-%d") - timedelta(days=back_days)).strftime("%Y-%m-%d")
    rows = reader.rows(
        "idx_ohlcv",
        "SELECT ticker, date, open, high, low, close, volume FROM prices "
        "WHERE date >= ? ORDER BY ticker, date", (lo,))
    out: Dict[str, Dict[str, List]] = {}
    for t, d, o, h, l, c, v in rows:
        rec = out.get(t)
        if rec is None:
            rec = out[t] = {"dates": [], "open": [], "high": [], "low": [],
                            "close": [], "vol": []}
        rec["dates"].append(d)
        rec["open"].append(o)
        rec["high"].append(h)
        rec["low"].append(l)
        rec["close"].append(c)
        rec["vol"].append(v)
    return out


def load_index(reader) -> Tuple[List[str], List[float]]:
    rows = reader.rows(
        "idx_ohlcv",
        "SELECT date, close FROM prices WHERE ticker='^IHSG' ORDER BY date")
    return [r[0] for r in rows], [r[1] for r in rows]


def _last_bar_at_or_before(rec: Dict, d: str) -> int:
    """Index bar terakhir dengan date <= d (-1 jika tidak ada).
    Binary search (dates urut naik)."""
    ds = rec["dates"]
    lo, hi = 0, len(ds) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if ds[mid] <= d:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


class _Book:
    """Buku simulasi: cash + posisi open; sizing & fee produksi."""

    def __init__(self, fee_profile: str):
        self.cash = core.V7_CORE_PARAMS["starting_capital"]
        self.positions: List[Dict] = []
        self.trades: List[Dict] = []
        self.fee_profile = fee_profile

    def mark_equity(self, window: Dict[str, Dict], date: str) -> float:
        eq = self.cash
        for p in self.positions:
            rec = window.get(p["ticker"])
            if rec:
                i = _last_bar_at_or_before(rec, date)
                px = rec["close"][i] if i >= 0 else p["entry"]
            else:
                px = p["last_known_close"]
            eq += p["qty"] * px
        return eq

    def open_tickers(self) -> set:
        return {p["ticker"] for p in self.positions}

    def close_position(self, p: Dict, price: float, date: str, reason: str) -> None:
        buy_not = p["entry"] * p["qty"]
        sell_not = price * p["qty"]
        fb = kern.fees_buy(buy_not, self.fee_profile)
        fs = kern.fees_sell(sell_not, self.fee_profile)
        net = sell_not - buy_not - fb - fs
        self.trades.append({
            "ticker": p["ticker"], "sig_date": p["sig_date"],
            "entry": p["entry"], "exit": price, "qty": p["qty"],
            "exit_date": date, "exit_reason": reason,
            "bars_held": p["bars_held"], "fee_buy": fb, "fee_sell": fs,
            "net": net, "ret_pct": net / buy_not * 100.0 if buy_not else 0.0})
        # fee beli sudah keluar dari cash saat entry (lihat _Book.entry);
        # di close hanya notional jual + fee jual.
        self.cash += p["qty"] * price - fs
        self.positions.remove(p)

    def entry(self, ticker: str, levels: Dict, qty: int, sig_date: str) -> None:
        alloc = qty * levels["entry"]
        self.cash -= alloc + kern.fees_buy(alloc, self.fee_profile)
        self.positions.append({
            "ticker": ticker, "sig_date": sig_date, "qty": qty,
            "entry": levels["entry"], "sl": levels["sl"],
            "tp1": levels["tp1"], "tp2": levels["tp2"],
            "bars_held": 0, "last_known_close": levels["entry"]})


def replay_arm(window: Dict[str, Dict], index_dates: List[str],
               index_closes: List[float], decision_dates: List[str],
               arm: str, fee_profile: str = "PROD_FLAT") -> Dict:
    """Satu arm (B0/B1) → trades + equity curve + bucket harian."""
    p = core.V7_CORE_PARAMS
    r20_iv = core.B0_R20_INTERVALS if arm == "B0" else core.B1_R20_INTERVALS
    book = _Book(fee_profile)
    # state per ticker: index bar terakhir yang diproses (pointer monotonic)
    ptr = {t: -1 for t in window}
    # state avg-value trailing 20 hari kalender (deque sederhana: list)
    avgq: Dict[str, List[Tuple[str, float]]] = {t: [] for t in window}
    avgsum: Dict[str, float] = {t: 0.0 for t in window}

    idx_ptr = -1  # pointer index ^IHSG
    bucket_daily: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []

    from datetime import datetime, timedelta

    for di, d in enumerate(decision_dates):
        # ---- 1. advance data state ke tanggal d ----
        d20 = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y-%m-%d")
        for t, rec in window.items():
            i = ptr[t]
            ds = rec["dates"]
            while i + 1 < len(ds) and ds[i + 1] <= d:
                i += 1
                dd = ds[i]
                vcv = (rec["vol"][i] or 0) * (rec["close"][i] or 0)
                q = avgq[t]
                q.append((dd, vcv))
                avgsum[t] += vcv
            ptr[t] = i
            q = avgq[t]
            while q and q[0][0] <= d20:
                avgsum[t] -= q.pop(0)[1]
        # index pointer
        while idx_ptr + 1 < len(index_dates) and index_dates[idx_ptr + 1] <= d:
            idx_ptr += 1

        # ---- 2. exit posisi open terhadap bar d (kronologi V7.3) ----
        for pos in list(book.positions):
            t = pos["ticker"]
            rec = window.get(t)
            has_bar_today = False
            if rec is not None and ptr[t] >= 0 and rec["dates"][ptr[t]] == d:
                has_bar_today = True
            if has_bar_today and rec is not None:
                i = ptr[t]
                pos["last_known_close"] = rec["close"][i]
                pos["bars_held"] += 1
                bar = {"date": d, "open": rec["open"][i],
                       "high": rec["high"][i], "low": rec["low"][i],
                       "close": rec["close"][i]}
                ev = kern.exit_event_for_bar(bar, pos, pos["bars_held"],
                                             p["max_hold_bars"])
                if ev is not None:
                    book.close_position(pos, ev["price"], ev["date"], ev["reason"])
            else:
                # suspensi: hitung sesi pasar sejak bar terakhir ticker
                last = rec["dates"][ptr[t]] if rec is not None and ptr[t] >= 0 \
                    else pos["sig_date"]
                gap = 0
                for dd in index_dates:
                    if dd > last:
                        gap += 1
                if gap >= p["max_hold_bars"]:
                    book.close_position(pos, pos["last_known_close"], d,
                                        "TIME_EXIT_SUSPENDED")

        # ---- 3. skor kandidat & gate (semua ticker window-visible) ----
        cands = []
        bucket_counts_d = {b: 0 for b in
                           ("DATA_UNUSABLE", "INELIGIBLE", "FEATURE_INVALID",
                            "MODEL_INVALID", "GATE_REJECTED",
                            "QUALIFIED_NOT_SELECTED", "SELECTED")}
        for t, rec in window.items():
            if t.startswith("^"):
                continue  # indeks pasar — bukan emiten universe (paritas produksi)
            i = ptr[t]
            if i < 0:
                continue  # belum listing di jendela ini
            n_bars = i + 1
            data_ok = True  # QA v3 dihitung run profile terpisah
            if not data_ok:
                bucket_counts_d["DATA_UNUSABLE"] += 1
                continue
            if n_bars < p["min_bars"]:
                bucket_counts_d["INELIGIBLE"] += 1
                continue
            avgv = avgsum[t] / len(avgq[t]) if avgq[t] else 0.0
            if avgv <= p["min_avg_value_20d"]:
                bucket_counts_d["GATE_REJECTED"] += 1  # LIQUIDITY_GATE
                continue
            stale = rec["dates"][i] != d
            if arm == "B1" and stale:
                bucket_counts_d["GATE_REJECTED"] += 1  # STALE_DATA_AS_OF
                continue
            lo = max(0, i - p["hist_window"] + 1)
            closes = rec["close"][lo:i + 1]
            vols = rec["vol"][lo:i + 1]
            sc = core.score_core(closes, vols, r20_iv)
            if sc is None:
                bucket_counts_d["FEATURE_INVALID"] += 1
                continue
            levels = core.atr_levels(rec["high"][lo:i + 1],
                                     rec["low"][lo:i + 1], closes)
            if levels is None:
                bucket_counts_d["FEATURE_INVALID"] += 1
                continue
            gates_ok = (sc["score"] >= p["threshold_score"] and
                        sc["volume_pts"] > 0 and
                        sc["r20"] >= p["r23_gate_min_pct"])
            if not gates_ok:
                bucket_counts_d["GATE_REJECTED"] += 1
                continue
            cands.append((t, sc, levels, avgv))
        bucket_counts_d["MODEL_INVALID"] = 0  # tanpa model di arm B0/B1

        # ---- 4. regime cap & overlap & seleksi ----
        n_idx = idx_ptr + 1
        trailing = index_closes[max(0, n_idx - p["regime_ma"]):n_idx] \
            if n_idx else []
        regime_off = core.regime_cap(list(reversed(trailing))) \
            if len(trailing) >= p["regime_ma"] else None
        if regime_off is None:
            cap = p["max_signals"]  # fail-open produksi
        else:
            cap = p["cap_regime_off"] if regime_off else p["cap_regime_on"]
        # urutan likuiditas DESC (input order) + sort stabil by score DESC
        cands.sort(key=lambda x: x[1]["score"], reverse=True)
        open_t = book.open_tickers()
        selected = []
        n_qns = 0
        for t, sc, levels, avgv in cands:
            if t in open_t:
                n_qns += 1  # overlap
                continue
            if len(selected) < cap:
                selected.append((t, sc, levels))
            else:
                n_qns += 1  # cap blocked
        bucket_counts_d["QUALIFIED_NOT_SELECTED"] += n_qns
        bucket_counts_d["SELECTED"] += len(selected)
        bucket_daily.append({"date": d, "regime_off": regime_off, "cap": cap,
                             "n_candidates_qualified": len(cands),
                             "selected_tickers": [t for t, _, _ in selected],
                             "buckets": dict(bucket_counts_d)})

        # ---- 5. entry (fee di entry & exit; sizing produksi: eq dihitung
        # SEKALI per hari seperti current_equity produksi) ----
        eq = book.mark_equity(window, d)
        for t, sc, levels in selected:
            qty = kern.position_size(eq, book.cash, levels["entry"],
                                      p["max_positions"])
            if qty <= 0:
                continue
            book.entry(t, levels, qty, d)

        equity_curve.append({"date": d, "equity": round(book.mark_equity(window, d), 2),
                             "n_open": len(book.positions)})

    # ---- ringkasan ----
    trades = book.trades
    n_closed = len(trades)
    wins = [t for t in trades if t["net"] > 0]
    rets = [t["ret_pct"] for t in trades]
    by_reason: Dict[str, int] = {}
    for t in trades:
        by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1
    summary = {
        "arm": arm, "r20_intervals": r20_iv,
        "fee_profile": fee_profile,
        "n_decision_dates": len(decision_dates),
        "n_trades_closed": n_closed,
        "n_positions_open_end": len(book.positions),
        "win_rate_pct": round(len(wins) / n_closed * 100.0, 2) if n_closed else None,
        "mean_ret_pct": round(sum(rets) / len(rets), 4) if rets else None,
        "median_ret_pct": round(sorted(rets)[len(rets) // 2], 4) if rets else None,
        "total_net": round(sum(t["net"] for t in trades), 2),
        "final_equity_marked": round(equity_curve[-1]["equity"], 2) if equity_curve else None,
        "exit_reasons": by_reason,
        "mean_bars_held": round(sum(t["bars_held"] for t in trades) / n_closed, 2)
        if n_closed else None,
    }
    return {"summary": summary, "trades": trades,
            "equity_curve": equity_curve, "bucket_daily": bucket_daily}


def e0_delta(trades_b0: List[Dict], trades_b1: List[Dict]) -> Dict:
    """Perbedaan seleksi B0 vs B1 (kunci = ticker+sig_date)."""
    k0 = {(t["ticker"], t["sig_date"]) for t in trades_b0}
    k1 = {(t["ticker"], t["sig_date"]) for t in trades_b1}
    return {"only_b0": sorted(f"{t}@{d}" for t, d in (k0 - k1)),
            "only_b1": sorted(f"{t}@{d}" for t, d in (k1 - k0)),
            "common": len(k0 & k1)}


def ajaib_sensitivity(trades: List[Dict]) -> Dict:
    """Re-hitung fee semua trade tertutup dengan AJAIB_EXACT
    (harga/qty sama; hanya biaya berubah) — laporan ekonomi, bukan arm."""
    tot_flat = sum(t["net"] for t in trades)
    tot_ajaib = 0.0
    for t in trades:
        buy_not = t["entry"] * t["qty"]
        sell_not = t["exit"] * t["qty"]
        net = (sell_not - buy_not - kern.fees_buy(buy_not, "AJAIB_EXACT")
               - kern.fees_sell(sell_not, "AJAIB_EXACT"))
        tot_ajaib += net
    return {"total_net_prod_flat": round(tot_flat, 2),
            "total_net_ajaib_exact": round(tot_ajaib, 2),
            "delta_fee_impact": round(tot_ajaib - tot_flat, 2)}


def run_e0_replay(reader, days: int = 126,
                  fee_profile: str = "PROD_FLAT") -> Dict:
    """Entry point E0: dua arm + delta + sensitivity fee. days = jumlah
    sesi keputusan terakhir dari kalender index."""
    cutoff = reader.manifest["effective_cutoff"]
    window = load_window(reader, cutoff)
    index_dates, index_closes = load_index(reader)
    if not index_dates:
        raise ValueError("kalender index kosong — snapshot tanpa ^IHSG")
    decision_dates = index_dates[-days:] if days and days < len(index_dates) \
        else index_dates
    b0 = replay_arm(window, index_dates, index_closes, decision_dates, "B0",
                    fee_profile)
    b1 = replay_arm(window, index_dates, index_closes, decision_dates, "B1",
                    fee_profile)
    delta = e0_delta(b0["trades"], b1["trades"])
    sens = {"B0": ajaib_sensitivity(b0["trades"]),
            "B1": ajaib_sensitivity(b1["trades"])}
    # bucket agregat (rata-rata per tanggal keputusan)
    agg: Dict[str, float] = {}
    for day in b0["bucket_daily"]:
        for b, v in day["buckets"].items():
            agg[b] = agg.get(b, 0.0) + v
    n_days = len(b0["bucket_daily"])
    agg_avg = {b: round(v / n_days, 1) for b, v in agg.items()} if n_days else {}
    return {"kind": "E0_B01_REPLAY", "cutoff": cutoff,
            "decision_dates": [decision_dates[0], decision_dates[-1]],
            "n_decision_dates": n_days,
            "arms": {"B0": b0, "B1": b1},
            "e0_delta": delta, "ajaib_sensitivity": sens,
            "bucket_avg_per_date": agg_avg}
