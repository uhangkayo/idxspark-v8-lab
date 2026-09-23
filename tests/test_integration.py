# tests/test_integration.py — Fixture DB sintetis → bridge → snapshot →
# profile → idempotensi, crash injection, tamper quarantine, no-network import.

import os
import socket
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.bridge import run_bridge  # noqa: E402
from idxspark_lab.config import load_config, parse_yaml, validate_config  # noqa: E402
from idxspark_lab.contracts.canonical import sha256_hex  # noqa: E402
from idxspark_lab.data import manifest as mf  # noqa: E402
from idxspark_lab.adapters.snapshot_reader import SnapshotAccessError, SnapshotReader  # noqa: E402
from idxspark_lab.orchestrator import run_profile  # noqa: E402

TICKERS = {"AA": 320, "BB": 320, "CC": 300}


def _mk_ohlcv(path: Path) -> None:
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
    import datetime
    d0 = datetime.date(2026, 1, 5)
    # 60 hari kerja berturut (fixture; kalender estimator akan menangkap mayoritas)
    days = []
    d = d0
    while len(days) < 320:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    for t, n in TICKERS.items():
        for i, dt in enumerate(days[:n]):
            close = 100.0 + i * 0.1
            conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                         (dt, t, close - 1, close + 1, close - 2, close, 1000))
            conn.execute("INSERT INTO prices_raw VALUES(?,?,?,?,?,?,?)",
                         (dt, t, close - 1, close + 1, close - 2, close, 1000))
    # ticker dengan OHLC violation → DATA_UNUSABLE
    conn.execute("UPDATE prices SET high=1.0, low=99.0 WHERE ticker='AA' AND date=?",
                 (days[3],))
    # ticker delisted close-only
    for i, dt in enumerate(days[:200]):
        conn.execute("INSERT INTO prices_delisted VALUES(?,?,?)", (dt, "ZZ", 50.0 + i))
    conn.execute("INSERT INTO prices_delisted_meta VALUES('ZZ',200,?,?,?,?,?,5)",
                 (days[0], days[199], "sb", "t", days[190]))
    conn.execute("INSERT INTO dividends VALUES('2026-02-02','AA',10)")
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


def _write_fixture(root: Path) -> Path:
    d = root / "sources"
    d.mkdir(parents=True)
    _mk_ohlcv(d / "idx_ohlcv.db")
    _mk_monthly(d / "idx_monthly.db")
    _mk_flow(d / "idx_flow.db")
    _mk_sectors(d / "ticker_sectors.db")
    _mk_surv(d / "idx_survivorship.db")
    return d


TEMPLATE = Path(__file__).resolve().parent.parent / "configs" / "lab.example.yaml"


def _make_config(tmp: Path, sources: Path) -> Path:
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("/var/lib/idxspark-v8-lab", str(tmp / "state"))
    text = text.replace("/root/.hermes/skills/idx-trading/scripts", str(sources))
    cfg_path = tmp / "lab.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path


class TestV8Lab011Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.sources = _write_fixture(root)
        cls.cfg = _make_config(root, cls.sources)
        cls.config = load_config(str(cls.cfg))
        out = run_bridge(cls.config)
        cls.snapshot_id = out["snapshot_id"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bridge_snapshot_ready(self):
        snap = Path(self.config["storage"]["input_root"]) / "inputs" / self.snapshot_id
        res = mf.verify_snapshot(snap)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["manifest"]["status"], "READY")
        self.assertIsNotNone(res["manifest"]["effective_cutoff"])

    def test_bridge_idempotent(self):
        out = run_bridge(self.config)
        self.assertTrue(out["idempotent_hit"])
        self.assertEqual(out["snapshot_id"], self.snapshot_id)

    def test_profile_runs_and_deterministic(self):
        out1 = run_profile(str(self.cfg), self.snapshot_id)
        self.assertEqual(out1["status"], "PUBLISHED")
        fun = out1["funnel"]
        self.assertEqual(fun["n_universe"], fun["PROFILE_OK"] + fun["DATA_UNUSABLE"])
        self.assertEqual(fun["n_universe"], 4)  # AA, BB, CC, ZZ
        self.assertGreaterEqual(fun["DATA_UNUSABLE"], 1)  # AA (OHLC violation)
        out2 = run_profile(str(self.cfg), self.snapshot_id)  # retry identik
        self.assertEqual(out2["status"], "IDEMPOTENT_HIT")
        self.assertEqual(out1["run_id"], out2["run_id"])
        # force retry → attempt baru, publication tetap 1, hash artifact sama
        out3 = run_profile(str(self.cfg), self.snapshot_id, force_retry=True)
        self.assertEqual(out3["status"], "PUBLISHED")
        self.assertEqual(out3["run_id"], out1["run_id"])
        h1 = sha256_hex((Path(out3["result_dir"]) / "coverage.json").read_bytes())
        out4 = run_profile(str(self.cfg), self.snapshot_id, force_retry=True)
        h2 = sha256_hex((Path(out4["result_dir"]) / "coverage.json").read_bytes())
        self.assertEqual(h1, h2)

    def test_tampered_snapshot_rejected(self):
        base = Path(self.config["storage"]["input_root"]) / "inputs"
        # snapshot kedua via copy manual → tamper hash
        import shutil
        tam = base / (self.snapshot_id + "-tam")
        if tam.exists():
            shutil.rmtree(tam)
        shutil.copytree(base / self.snapshot_id, tam)
        f = tam / "idx_ohlcv.db"
        with open(f, "ab") as fh:
            fh.write(b"tamper")
        mf.load_manifest(tam)["status"] = "READY"
        with self.assertRaises(SnapshotAccessError):
            SnapshotReader(str(base), self.snapshot_id + "-tam")
        shutil.rmtree(tam)

    def test_reader_rejects_traversal(self):
        base = Path(self.config["storage"]["input_root"]) / "inputs"
        with self.assertRaises(SnapshotAccessError):
            SnapshotReader(str(base), "../../etc")

    def test_import_no_network(self):
        orig = socket.socket
        calls = []
        import idxspark_lab.orchestrator as orch  # noqa: F401
        import idxspark_lab.bridge  # noqa: F401
        self.assertEqual(calls, [])  # import tidak membuka socket
        self.assertIs(socket.socket, orig)

    def test_config_frozen(self):
        cfg = parse_yaml(TEMPLATE.read_text(encoding="utf-8"))
        validate_config(cfg)  # template valid & beku
        bad = dict(cfg)
        bad["isolation"] = dict(cfg["isolation"], production_write=True)
        with self.assertRaises(Exception):
            validate_config(bad)


if __name__ == "__main__":
    unittest.main()
