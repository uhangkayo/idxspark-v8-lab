# data/calendar.py — Kalender sesi bursa dari data snapshot (S1)
# Harga hilang bukan otomatis libur; libur = mayoritas emiten aktif tanpa bar.
# Estimator: tanggal = sesi bursa bila ≥60% emiten aktif (terlihat dalam 60
# sesi trailing) memiliki bar. Streaming O(dates × baris), tanpa union berulang.

from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple


def trading_calendar(rows_by_date: Dict[str, Set[str]], window: int = 60,
                     min_active_pct: float = 0.6) -> List[str]:
    dates = sorted(rows_by_date)
    last_seen: Dict[str, str] = {}
    seen_dates: deque = deque()  # tanggal sesi-bursa-atau-bukan yang sudah lewat
    calendar: List[str] = []
    for d in dates:
        for t in rows_by_date[d]:
            last_seen[t] = d
        seen_dates.append(d)
        while len(seen_dates) > window:
            seen_dates.popleft()
        cutoff = seen_dates[0] if seen_dates else d
        active = sum(1 for t, ls in last_seen.items() if ls >= cutoff)
        have = len(rows_by_date[d])
        if active == 0 or have >= min_active_pct * active:
            calendar.append(d)
        # bersihkan emiten mati lama agar last_seen tak tumbuh tanpa batas
        if len(seen_dates) == window and window > 0:
            dead = [t for t, ls in last_seen.items() if ls < cutoff]
            for t in dead:
                del last_seen[t]
    return calendar


def calendar_from_price_rows(rows: List[Tuple[str, str]],
                             min_active_pct: float = 0.6) -> List[str]:
    """rows: (date, ticker) dari tabel harga snapshot."""
    by_date: Dict[str, Set[str]] = defaultdict(set)
    for d, t in rows:
        by_date[d].add(t)
    return trading_calendar(by_date, 60, min_active_pct)
