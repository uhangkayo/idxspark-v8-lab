# tests/test_e02_replay.py — E0 replay: divergensi B0/B1, bucket invariant,
# determinisme, bookkeeping + integrasi bridge→profile→e0 (fixture +^IHSG).

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.contracts.canonical import canonical_json  # noqa: E402
from idxspark_lab.evaluation import replay as rep  # noqa: E402


def _sessions(n, d0=datetime.date(2026, 1, 5)):
    days, d = [], d0
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return days


class FakeReader:
    """Reader minimal: manifest + dua query yang dipakai replay."""

    def __init__(self, price_rows, index_rows, cutoff):
        self._price_rows = sorted(price_rows, key=lambda r: (r[0], r[1]))
        self._index_rows = index_rows
        self.manifest = {"effective_cutoff": cutoff, "snapshot_id": "fake",
                         "datasets": []}

    def rows(self, db, sql, params=()):
        if "ticker='^IHSG'" in sql:
            return self._index_rows
        lo = params[0] if params else "0000"
        return [r for r in self._price_rows if r[1] >= lo]


def _pump_rows(ticker, sessions, n_bars, slope=3.5, vol0=1_000_000.0,
               vol_slope=27_000.0, end_offset=0):
    """Bar pump linear: close 100+slope*i, high +6, low −6, vol naik linear.
    end_offset>0: bar berakhir offset sesi sebelum akhir (kasus STALE)."""
    bars = sessions[:n_bars] if end_offset == 0 \
        else sessions[:len(sessions) - end_offset]
    rows = []
    for i, d in enumerate(bars):
        c = 100.0 + slope * i
        v = vol0 + vol_slope * i
        rows.append((ticker, d, c - 0.5, c + 6.0, c - 6.0, c, v))
    return rows


def _index_rows(sessions, start=5000.0, slope=2.0):
    return [(d, start + slope * i) for i, d in enumerate(sessions)]


def _index_price_rows(sessions, start=5000.0, slope=2.0, vol=5e5):
    """^IHSG sebagai bar prices penuh (produksi menyimpannya di tabel prices)."""
    return [("^IHSG", d, c - 1, c + 1, c - 2, c, vol)
            for c, d in ((start + slope * i, d) for i, d in enumerate(sessions))]


def _flat_rows(ticker, sessions, n_bars, close=100.0, vol=1_000_000.0):
    return [(ticker, d, close, close + 1, close - 1, close, vol)
            for d in sessions[:n_bars]]


class TestReplayDivergence(unittest.TestCase):
    def setUp(self):
        S_all = _sessions(210)          # >= 200 bar index untuk regime MA200
        S = S_all[-62:]                 # emiten hidup di 62 sesi terakhir
        self.S = S
        # index turun panjang -> regime OFF -> cap 1
        idx = _index_rows(S_all, start=12000.0, slope=-5.0)
        price_rows = (
            _pump_rows("PUMP", S, 60, slope=5.0) +   # r20 B0 akhir 31.7% > R23
            _pump_rows("STALE", S, 57, slope=4.9, vol_slope=25_000.0,
                       end_offset=3) +               # bar berhenti 3 sesi sebelum akhir
            _flat_rows("FLAT", S, 60) +
            _index_price_rows(S_all))                # ^IHSG ada di prices (paritas produksi)
        self.reader = FakeReader(price_rows, idx, S[-1])

    def test_b0_selects_stale_b1_blocks(self):
        window = rep.load_window(self.reader, self.S[-1])
        idx_d, idx_c = rep.load_index(self.reader)
        days = self.S[-4:]  # 4 tanggal keputusan terakhir (regime OFF, cap 1)
        b0 = rep.replay_arm(window, idx_d, idx_c, days, "B0")
        b1 = rep.replay_arm(window, idx_d, idx_c, days, "B1")
        b0_all_sel = [t for day in b0["bucket_daily"]
                      for t in day["selected_tickers"]]
        b1_all_sel = [t for day in b1["bucket_daily"]
                      for t in day["selected_tickers"]]
        self.assertIn("STALE", b0_all_sel)
        self.assertNotIn("STALE", b1_all_sel)
        # B0 memegang posisi stale di akhir, B1 tidak
        self.assertGreater(b0["summary"]["n_positions_open_end"],
                           b1["summary"]["n_positions_open_end"])
        # GATE_REJECTED B1 > B0 (stale terhitung GATE_REJECTED di B1)
        gr_b1 = sum(d["buckets"]["GATE_REJECTED"] for d in b1["bucket_daily"])
        gr_b0 = sum(d["buckets"]["GATE_REJECTED"] for d in b0["bucket_daily"])
        self.assertGreater(gr_b1, gr_b0)

    def test_bucket_invariant_every_date(self):
        window = rep.load_window(self.reader, self.S[-1])
        idx_d, idx_c = rep.load_index(self.reader)
        for arm in ("B0", "B1"):
            r = rep.replay_arm(window, idx_d, idx_c, self.S, arm)
            for day in r["bucket_daily"]:
                self.assertEqual(sum(day["buckets"].values()), 3,
                                 f"{arm} {day['date']}: {day['buckets']}")
                self.assertIn("SELECTED", day["buckets"])

    def test_index_excluded_from_universe(self):
        window = rep.load_window(self.reader, self.S[-1])
        self.assertIn("^IHSG", window)  # ter-load untuk kalender
        idx_d, idx_c = rep.load_index(self.reader)
        r = rep.replay_arm(window, idx_d, idx_c, self.S[-1:], "B0")
        # ^IHSG tidak pernah dihitung sebagai kandidat (bucket sum = 3 emiten)
        self.assertEqual(sum(r["bucket_daily"][0]["buckets"].values()), 3)


class TestR20Divergence(unittest.TestCase):
    def test_b1_r20_fix_flips_r23_gate(self):
        S = _sessions(30)
        idx = _index_rows(S)  # naik -> ON -> cap 2
        # X: dip 21 bar lalu naik — r20 20-interval lolos R23, 19-interval tidak
        closes = [77.0] + [98.0] * 19 + [103.0]
        vols = [1_000_000.0] * 20 + [2_400_000.0]
        rows = []
        for i, d in enumerate(S[-len(closes):]):
            c = closes[i]
            rows.append(("X", d, c - 0.5, c + 3.0, c - 3.0, c, vols[i]))
        reader = FakeReader(rows, idx, S[-1])
        window = rep.load_window(reader, S[-1])
        idx_d, idx_c = rep.load_index(reader)
        b0 = rep.replay_arm(window, idx_d, idx_c, S[-1:], "B0")
        b1 = rep.replay_arm(window, idx_d, idx_c, S[-1:], "B1")
        self.assertEqual(b0["bucket_daily"][0]["selected_tickers"], [])
        self.assertEqual(b1["bucket_daily"][0]["selected_tickers"], ["X"])
        self.assertEqual(b0["bucket_daily"][0]["buckets"]["GATE_REJECTED"], 1)


class TestDeterminismAndBookkeeping(unittest.TestCase):
    def setUp(self):
        S = _sessions(62)
        idx = _index_rows(S)
        price_rows = _pump_rows("PUMP", S, 60) + _flat_rows("FLAT", S, 60)
        self.reader = FakeReader(price_rows, idx, S[-1])

    def test_run_e0_deterministic(self):
        e1 = rep.run_e0_replay(self.reader, days=40)
        e2 = rep.run_e0_replay(self.reader, days=40)
        self.assertEqual(canonical_json(e1), canonical_json(e2))

    def test_trades_close_and_arithmetic(self):
        e0 = rep.run_e0_replay(self.reader, days=40)
        for arm in ("B0", "B1"):
            trs = e0["arms"][arm]["trades"]
            self.assertGreaterEqual(len(trs), 3)
            for t in trs:
                buy = t["entry"] * t["qty"]
                sell = t["exit"] * t["qty"]
                self.assertAlmostEqual(t["net"], sell - buy - t["fee_buy"] - t["fee_sell"],
                                       places=6)
                self.assertAlmostEqual(t["ret_pct"], t["net"] / buy * 100.0, places=6)
            total = sum(t["net"] for t in trs)
            self.assertAlmostEqual(e0["arms"][arm]["summary"]["total_net"], round(total, 2),
                                   places=1)

    def test_equity_consistency_with_open_positions(self):
        e0 = rep.run_e0_replay(self.reader, days=40)
        for arm in ("B0", "B1"):
            s = e0["arms"][arm]["summary"]
            # equity akhir >= 10jt + net tertutup − fee open (posisi open mark
            # di atas entry pada tren naik; toleransi fee)
            self.assertGreaterEqual(s["final_equity_marked"],
                                    10_000_000 + s["total_net"] - 50.0)

    def test_ajaib_sensitivity_pays_pph_on_sell(self):
        e0 = rep.run_e0_replay(self.reader, days=40)
        for arm in ("B0", "B1"):
            sens = e0["ajaib_sensitivity"][arm]
            # AJAIB_EXACT: 0.151303% beli + 0.251303% jual (PPh 0.1% di jual)
            # = 0.402606% round-trip -> LEBIH MAHAL ~0.26bp dari flat 0.4%.
            self.assertLess(sens["delta_fee_impact"], 0.0)

    def test_sensitivity_same_prices(self):
        e0 = rep.run_e0_replay(self.reader, days=40)
        trs = e0["arms"]["B0"]["trades"]
        self.assertGreaterEqual(len(trs), 1)
        from idxspark_lab.execution import kernel as kern
        expected_delta = 0.0
        for t in trs:
            buy = t["entry"] * t["qty"]
            sell = t["exit"] * t["qty"]
            ajaib_net = (sell - buy - kern.fees_buy(buy, "AJAIB_EXACT")
                         - kern.fees_sell(sell, "AJAIB_EXACT"))
            expected_delta += ajaib_net - t["net"]
        sens = e0["ajaib_sensitivity"]["B0"]
        self.assertAlmostEqual(sens["total_net_ajaib_exact"]
                               - sens["total_net_prod_flat"],
                               round(expected_delta, 2), places=1)


# ---------------- integrasi: bridge → profile → e0 (fixture + ^IHSG) ----------------

import sqlite3  # noqa: E402

from idxspark_lab.bridge import run_bridge  # noqa: E402
from idxspark_lab.config import load_config, parse_yaml, validate_config  # noqa: E402
from idxspark_lab.orchestrator import run_e0, run_profile  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "configs" / "lab.example.yaml"


def _mk_ohlcv_with_index(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE prices(date TEXT, ticker TEXT, open REAL, high REAL, low REAL,
      close REAL, volume REAL);
    CREATE TABLE prices_raw(date TEXT, ticker TEXT, open REAL, high REAL, low REAL,
      close REAL, volume REAL);
    CREATE TABLE prices_delisted(date TEXT, ticker TEXT, close REAL);
    CREATE TABLE prices_delisted_meta(ticker TEXT, n_bars INTEGER, first_date TEXT,
      last_date TEXT, source TEXT, fetched_at TEXT, last_real_print TEXT,
      flat_tail_bars INTEGER);
    CREATE TABLE dividends(date TEXT, ticker TEXT, amount REAL);
    CREATE TABLE news(id INTEGER, ticker TEXT, title TEXT, url TEXT,
      published_ts TEXT, source TEXT);
    CREATE TABLE sb_news(stream_id INTEGER, title TEXT, url TEXT, source TEXT, label TEXT);
    CREATE TABLE sb_dividends(id INTEGER);
    """)
    S = _sessions(320)
    # ^IHSG naik -> regime ON (cap 2)
    for i, d in enumerate(S):
        c = 5000.0 + 2.0 * i
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                     (d, "^IHSG", c - 1, c + 1, c - 2, c, 5e5))
    # PU pump 60 bar terakhir, volume besar (lolos likuiditas)
    for i, d in enumerate(S[-60:]):
        c = 100.0 + 3.5 * i
        v = 1_000_000.0 + 27_000.0 * i
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                     (d, "PU", c - 0.5, c + 6.0, c - 6.0, c, v))
    # emiten datar (likuiditas ok tapi skor rendah)
    for i, d in enumerate(S):
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                     (d, "QQ", 99.0, 101.0, 98.0, 100.0, 1_000_000.0))
    # delisted close-only (stale tail panjang)
    for i, dt in enumerate(S[:200]):
        conn.execute("INSERT INTO prices_delisted VALUES(?,?,?)", (dt, "ZZ", 50.0 + i))
    conn.execute("INSERT INTO prices_delisted_meta VALUES('ZZ',200,?,?,?,?,?,5)",
                 (S[0], S[199], "sb", "t", S[190]))
    conn.commit()
    conn.close()


def _mk_monthly(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE emiten(Code TEXT);
    CREATE TABLE stock_monthly(Month TEXT, Code TEXT, Name TEXT, Sector TEXT,
      SubSector TEXT, Sharia TEXT, MarketCap REAL, RegVol REAL);
    CREATE TABLE broker_monthly(Month TEXT, Code TEXT, Broker TEXT, Volume REAL,
      Value REAL, Freq REAL, PctVol REAL, PctVal REAL);
    CREATE TABLE sbl_daily(Date TEXT, Kode TEXT, Volume REAL, Value REAL, FeeRate TEXT);
    CREATE TABLE sbl_lendable(Date TEXT, Kode TEXT, Volume REAL, RateRegular TEXT,
      RateFrontEnd TEXT, FetchedAt TEXT);
    CREATE TABLE sbl_top(Date TEXT);
    CREATE TABLE fi_di_daily(Date TEXT);
    CREATE TABLE industry_monthly(Month TEXT);
    CREATE TABLE edd_markets(x TEXT);
    CREATE TABLE corporate_actions(id INTEGER);
    """)
    conn.execute("INSERT INTO sbl_daily VALUES('2026-01-06','AA',100,1000,'1')")
    conn.commit()
    conn.close()


def _mk_flow(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE stock_summary(Date TEXT, StockCode TEXT, StockName TEXT, "
                 "Close REAL, Volume REAL)")
    conn.execute("INSERT INTO stock_summary VALUES('2026-01-06','AA','a',100,1000)")
    conn.execute("CREATE TABLE broker_summary(Date TEXT, Broker TEXT)")
    conn.execute("INSERT INTO broker_summary VALUES('2026-01-06','BRK')")
    conn.execute("CREATE TABLE index_summary(Date TEXT, Idx TEXT)")
    conn.execute("INSERT INTO index_summary VALUES('2026-01-06','IHSG')")
    conn.commit()
    conn.close()


def _mk_sectors(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE sectors(ticker TEXT, sector TEXT)")
    conn.execute("INSERT INTO sectors VALUES('AA','X')")
    conn.commit()
    conn.close()


def _mk_surv(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE delisted_events(id INTEGER);
    CREATE TABLE delisted_final(ticker TEXT PRIMARY KEY, name TEXT, last_seen INTEGER,
      era TEXT, in_price_db INTEGER, known_2021_26 INTEGER, note TEXT, reason TEXT,
      exit_type TEXT);
    CREATE TABLE wiki_meta(x TEXT);
    CREATE TABLE wiki_universe(x TEXT);
    """)
    conn.execute("INSERT INTO delisted_final VALUES('ZZ','zz',1,'e',0,1,'n','r','x')")
    conn.commit()
    conn.close()


class TestE0Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        src = root / "sources"
        src.mkdir(parents=True)
        _mk_ohlcv_with_index(src / "idx_ohlcv.db")
        _mk_monthly(src / "idx_monthly.db")
        _mk_flow(src / "idx_flow.db")
        _mk_sectors(src / "ticker_sectors.db")
        _mk_surv(src / "idx_survivorship.db")
        text = TEMPLATE.read_text(encoding="utf-8")
        text = text.replace("/var/lib/idxspark-v8-lab", str(root / "state"))
        text = text.replace("/root/.hermes/skills/idx-trading/scripts", str(src))
        cls.cfg_path = root / "lab.yaml"
        cls.cfg_path.write_text(text, encoding="utf-8")
        cls.config = load_config(str(cls.cfg_path))
        out = run_bridge(cls.config)
        cls.snapshot_id = out["snapshot_id"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_profile_still_ok_with_index_ticker(self):
        out = run_profile(str(self.cfg_path), self.snapshot_id)
        self.assertIn(out["status"], ("PUBLISHED", "IDEMPOTENT_HIT"))
        fun = out["funnel"]
        self.assertEqual(fun["n_universe"], fun["PROFILE_OK"] + fun["DATA_UNUSABLE"])

    def test_run_e0_publishes(self):
        out = run_e0(str(self.cfg_path), self.snapshot_id, days=30)
        self.assertIn(out["status"], ("PUBLISHED", "IDEMPOTENT_HIT"))
        rd = Path(out["result_dir"]) if out["status"] == "PUBLISHED" \
            else Path(str(self.config["storage"]["output_root"])) / "results" / out["run_id"]
        self.assertTrue((rd / "e0.json").is_file())
        self.assertTrue((rd / "e0_report.md").is_file())
        if out["status"] == "IDEMPOTENT_HIT":
            s = json.loads((rd / "e0.json").read_text())["arms"]
        else:
            s = out["summary"]
        for arm in ("B0", "B1"):
            self.assertGreaterEqual(s[arm]["summary"]["n_trades_closed"], 3)
        # determinisme: e0.json identik saat force retry
        h1 = (rd / "e0.json").read_bytes()
        out2 = run_e0(str(self.cfg_path), self.snapshot_id, days=30, force_retry=True)
        self.assertEqual(out2["run_id"], out["run_id"])
        h2 = (Path(out2["result_dir"]) / "e0.json").read_bytes()
        self.assertEqual(h1, h2)

    def test_run_e0_idempotent(self):
        out1 = run_e0(str(self.cfg_path), self.snapshot_id, days=30)
        out2 = run_e0(str(self.cfg_path), self.snapshot_id, days=30)
        self.assertEqual(out2["status"], "IDEMPOTENT_HIT")
        self.assertEqual(out1["run_id"], out2["run_id"])

    def test_run_e0_days_makes_separate_slot(self):
        out30 = run_e0(str(self.cfg_path), self.snapshot_id, days=30)
        out40 = run_e0(str(self.cfg_path), self.snapshot_id, days=40)
        self.assertEqual(out40["status"], "PUBLISHED")
        self.assertNotEqual(out30["run_id"], out40["run_id"])

    def test_report_renders(self):
        from idxspark_lab.reporting.e0_report import render_e0_report_md
        out = run_e0(str(self.cfg_path), self.snapshot_id, days=30)
        e0 = json.loads((Path(out["result_dir"]) / "e0.json").read_text())
        md = render_e0_report_md(e0, out["run_id"])
        self.assertIn("E0 Baseline Parity", md)
        self.assertIn("B0_V7_FROZEN", md)
        self.assertIn("Bucket 7-enum", md)


if __name__ == "__main__":
    unittest.main()
