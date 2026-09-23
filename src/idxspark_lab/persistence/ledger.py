# persistence/ledger.py — Single-writer, append-only, idempotent (Lampiran C §1/§7)
# Aturan yang ditegakkan di DB (bukan hanya konvensi):
#   * Tidak ada UPDATE/DELETE pada tabel ledger (trigger ABORT).
#   * Tidak ada INSERT OR REPLACE: insert ON CONFLICT DO NOTHING lalu bandingkan
#     result_digest — konflik ID dengan digest berbeda = IntegrityError.
#   * Satu writer: file lock (fcntl) + BEGIN IMMEDIATE.
#   * Setiap baris mendapat ledger_seq global dari LedgerAppend dalam transaksi sama.

import fcntl
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts.canonical import canonical_json, utc_now
from ..contracts.types import (
    ArtifactRec, AttemptEventRec, AttemptRec, DecisionSlotRec,
    ExperimentRec, PublicationRec, RegistryEventRec, RunRec,
)

# Tabel append-only inti (Lampiran C). Tabel 0.2+ (Candidate, Prediction,
# PaperBook, ...) dibuat DDL lengkap sejak 0.1 agar kontrak material sejak hari-1.
APPEND_TABLES = [
    "Artifact", "RegistryEvent", "Experiment", "TargetSpec",
    "DecisionSlot", "SelectionContext", "Run", "Attempt", "AttemptEvent",
    "Checkpoint", "Candidate", "FeatureSnapshot", "Prediction",
    "CandidateDecision", "OutcomeVersion", "PaperBook", "PaperOrder",
    "ReservationEvent", "PaperLedgerEvent", "Publication",
]

_DDL = """
CREATE TABLE IF NOT EXISTS LedgerAppend(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  row_id TEXT NOT NULL,
  recorded_at INTEGER NOT NULL,
  UNIQUE(table_name, row_id)
);
CREATE TABLE IF NOT EXISTS Artifact(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  kind TEXT NOT NULL, uri TEXT NOT NULL, sha256 TEXT NOT NULL,
  byte_length INTEGER NOT NULL, schema_version TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS RegistryEvent(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES Artifact(id), event TEXT NOT NULL,
  actor TEXT NOT NULL, reason TEXT NOT NULL, evidence_artifact_id TEXT,
  supersedes_id TEXT);
CREATE TABLE IF NOT EXISTS Experiment(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  name TEXT NOT NULL, charter_artifact_id TEXT NOT NULL REFERENCES Artifact(id),
  config_id TEXT NOT NULL REFERENCES Artifact(id),
  feature_spec_id TEXT NOT NULL REFERENCES Artifact(id),
  universe_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  target_spec_id TEXT, execution_policy_id TEXT, parent_experiment_id TEXT,
  availability_policy_id TEXT,
  git_commit TEXT NOT NULL, dependency_lock_hash TEXT NOT NULL,
  mode TEXT NOT NULL, research_role TEXT NOT NULL,
  availability_mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS TargetSpec(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  strategy_version_id TEXT NOT NULL REFERENCES Artifact(id),
  entry_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  exit_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  cost_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  corporate_action_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  reference_size_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  participation_limit REAL NOT NULL,
  tick_lot_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  entry_window TEXT NOT NULL, outcome_horizon TEXT NOT NULL,
  label_definition TEXT NOT NULL, maturity_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  reference_shares INTEGER, reference_notional INTEGER);
CREATE TABLE IF NOT EXISTS DecisionSlot(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  experiment_id TEXT NOT NULL REFERENCES Experiment(id),
  decision_at INTEGER NOT NULL, expected_by INTEGER NOT NULL,
  entry_decision_deadline INTEGER NOT NULL, freshness_deadline INTEGER NOT NULL,
  session_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS SelectionContext(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  paper_book_id TEXT NOT NULL REFERENCES PaperBook(id),
  book_state_artifact_id TEXT NOT NULL REFERENCES Artifact(id),
  book_state_hash TEXT NOT NULL, book_version INTEGER NOT NULL,
  global_ledger_cutoff_seq INTEGER NOT NULL, reservation_cutoff_seq INTEGER NOT NULL,
  portfolio_asof INTEGER NOT NULL, sizing_policy_id TEXT NOT NULL REFERENCES Artifact(id));
CREATE TABLE IF NOT EXISTS Run(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  slot_id TEXT NOT NULL REFERENCES DecisionSlot(id), generation INTEGER NOT NULL,
  config_id TEXT NOT NULL REFERENCES Artifact(id),
  universe_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  input_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  feature_spec_id TEXT NOT NULL REFERENCES Artifact(id),
  model_id TEXT, calibrator_id TEXT,
  availability_mode TEXT NOT NULL, ledger_cutoff_seq INTEGER NOT NULL,
  evaluation_clock TEXT NOT NULL, paper_book_id TEXT, selection_context_id TEXT,
  price_asof INTEGER NOT NULL, decision_at INTEGER NOT NULL,
  git_commit TEXT NOT NULL, environment_hash TEXT NOT NULL, seed INTEGER NOT NULL,
  UNIQUE(slot_id, generation));
CREATE TABLE IF NOT EXISTS Attempt(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES Run(id), attempt_no INTEGER NOT NULL,
  worker_build TEXT NOT NULL, started_at INTEGER NOT NULL,
  resume_checkpoint_id TEXT, UNIQUE(run_id, attempt_no));
CREATE TABLE IF NOT EXISTS AttemptEvent(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  attempt_id TEXT NOT NULL REFERENCES Attempt(id), event_no INTEGER NOT NULL,
  state TEXT NOT NULL, error_code TEXT, details_artifact_id TEXT,
  UNIQUE(attempt_id, event_no));
CREATE TABLE IF NOT EXISTS Checkpoint(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  attempt_id TEXT NOT NULL REFERENCES Attempt(id), stage TEXT NOT NULL,
  partition_key TEXT NOT NULL, step INTEGER NOT NULL,
  completed_through TEXT NOT NULL, input_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
  output_artifact_id TEXT NOT NULL REFERENCES Artifact(id), state_artifact_id TEXT,
  UNIQUE(attempt_id, stage, partition_key, step));
CREATE TABLE IF NOT EXISTS Candidate(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES Run(id), subject_id TEXT NOT NULL,
  universe_ordinal INTEGER NOT NULL, instrument_version_id TEXT NOT NULL,
  price_snapshot_id TEXT, UNIQUE(run_id, subject_id));
CREATE TABLE IF NOT EXISTS FeatureSnapshot(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  candidate_id TEXT NOT NULL REFERENCES Candidate(id),
  feature_spec_id TEXT NOT NULL REFERENCES Artifact(id),
  feature_values_artifact_id TEXT NOT NULL REFERENCES Artifact(id),
  lineage_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  value_status_counts TEXT NOT NULL, max_input_available_at INTEGER,
  feature_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS Prediction(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  candidate_id TEXT NOT NULL REFERENCES Candidate(id),
  feature_snapshot_id TEXT NOT NULL REFERENCES FeatureSnapshot(id),
  model_id TEXT NOT NULL REFERENCES Artifact(id),
  head_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  target_spec_id TEXT NOT NULL REFERENCES TargetSpec(id),
  status TEXT NOT NULL, raw_score REAL, p_fill REAL,
  p_profit_given_fill REAL, expected_net_return REAL, downside REAL,
  downside_definition TEXT, return_conditioning TEXT NOT NULL,
  invalid_reason TEXT, score_semantics TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS CandidateDecision(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  candidate_id TEXT NOT NULL REFERENCES Candidate(id), feature_snapshot_id TEXT,
  prediction_id TEXT, terminal_bucket TEXT NOT NULL, primary_reason TEXT NOT NULL,
  gate_results_artifact_id TEXT NOT NULL REFERENCES Artifact(id),
  pre_gate_rank INTEGER, qualified_rank INTEGER, tie_break_key TEXT NOT NULL,
  sizing_plan_artifact_id TEXT);
CREATE TABLE IF NOT EXISTS OutcomeVersion(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  candidate_id TEXT NOT NULL REFERENCES Candidate(id),
  target_spec_id TEXT NOT NULL REFERENCES TargetSpec(id),
  execution_policy_id TEXT NOT NULL REFERENCES Artifact(id),
  evaluation_data_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  corporate_action_manifest_id TEXT NOT NULL REFERENCES Artifact(id),
  status TEXT NOT NULL, label_available_at INTEGER,
  entry_at INTEGER, exit_at INTEGER, gross_return REAL, net_return REAL,
  exit_reason TEXT, calculation_code_id TEXT NOT NULL, supersedes_id TEXT);
CREATE TABLE IF NOT EXISTS PaperBook(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  experiment_id TEXT NOT NULL REFERENCES Experiment(id),
  policy_id TEXT NOT NULL REFERENCES Artifact(id),
  currency TEXT NOT NULL, money_scale INTEGER NOT NULL,
  mode TEXT NOT NULL, initial_capital INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS PaperOrder(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES PaperBook(id),
  candidate_decision_id TEXT NOT NULL REFERENCES CandidateDecision(id),
  client_key TEXT NOT NULL, side TEXT NOT NULL, requested_qty INTEGER NOT NULL,
  qty_unit TEXT NOT NULL, lot_size INTEGER NOT NULL,
  order_policy_id TEXT NOT NULL REFERENCES Artifact(id), submitted_at INTEGER NOT NULL,
  UNIQUE(book_id, client_key));
CREATE TABLE IF NOT EXISTS ReservationEvent(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES PaperBook(id),
  order_id TEXT NOT NULL REFERENCES PaperOrder(id), event_key TEXT NOT NULL,
  event TEXT NOT NULL, qty INTEGER NOT NULL, cash_amount INTEGER NOT NULL,
  risk_amount INTEGER NOT NULL, expected_book_version INTEGER NOT NULL,
  UNIQUE(book_id, event_key));
CREATE TABLE IF NOT EXISTS PaperLedgerEvent(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES PaperBook(id), order_id TEXT,
  position_id TEXT, event_key TEXT NOT NULL, event_type TEXT NOT NULL,
  event_at INTEGER NOT NULL, qty_delta INTEGER NOT NULL, cash_delta INTEGER NOT NULL,
  fill_price INTEGER, fee_amount INTEGER NOT NULL, reference_price INTEGER,
  market_data_version_id TEXT, action_artifact_id TEXT, action_type TEXT,
  action_stage TEXT, receivable_delta INTEGER NOT NULL,
  fractional_entitlement_num INTEGER, fractional_entitlement_den INTEGER,
  supersedes_event_id TEXT, UNIQUE(book_id, event_key));
CREATE TABLE IF NOT EXISTS Publication(
  id TEXT PRIMARY KEY, ledger_seq INTEGER UNIQUE NOT NULL REFERENCES LedgerAppend(seq),
  recorded_at INTEGER NOT NULL, identity_payload TEXT NOT NULL, result_digest TEXT NOT NULL,
  run_id TEXT NOT NULL UNIQUE REFERENCES Run(id),
  attempt_id TEXT NOT NULL REFERENCES Attempt(id),
  result TEXT NOT NULL, candidate_manifest_id TEXT, decision_manifest_id TEXT,
  metrics_manifest_id TEXT, n_universe INTEGER NOT NULL,
  disjoint_counts TEXT NOT NULL, data_asof INTEGER NOT NULL,
  published_at INTEGER NOT NULL, temporal_status TEXT NOT NULL,
  diagnostic_reason TEXT, freshness_deadline INTEGER NOT NULL);
"""

for _t in APPEND_TABLES:
    _DDL += (f"CREATE TRIGGER IF NOT EXISTS trg_{_t}_noupd BEFORE UPDATE ON {_t} "
             "BEGIN SELECT RAISE(ABORT,'ledger append-only: UPDATE dilarang'); END;\n")
    _DDL += (f"CREATE TRIGGER IF NOT EXISTS trg_{_t}_nodel BEFORE DELETE ON {_t} "
             "BEGIN SELECT RAISE(ABORT,'ledger append-only: DELETE dilarang'); END;\n")


class LedgerIntegrityError(Exception):
    pass


class WriterLockHeld(Exception):
    pass


class Ledger:
    """Single-writer ledger lab. Satu proses writer pada satu waktu."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.db_path.parent / ".writer.lock"
        self._lock_fh = None
        self._acquire_writer_lock()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(_DDL)
        self.conn.commit()

    def _acquire_writer_lock(self) -> None:
        self._lock_fh = open(self._lock_path, "a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_fh.close()
            raise WriterLockHeld(
                "writer lock dipegang proses lain — single_writer: true (fail-closed)")

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            if self._lock_fh:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
                self._lock_fh.close()

    # ---- core append -----------------------------------------------------
    def append(self, table: str, rec) -> str:
        """Insert append-only record. Idempotent bila (id, result_digest) sama;
        error integritas bila id sama dengan digest berbeda."""
        identity = canonical_json(rec.identity())
        digest = canonical_json(rec.result_digest() if hasattr(rec, "result_digest")
                                else rec.identity())
        row_id = rec.id
        cur = self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                f"SELECT id, result_digest FROM {table} WHERE id=?", (row_id,)).fetchone()
            if existing is not None:
                if existing[1] != digest:
                    self.conn.rollback()
                    raise LedgerIntegrityError(
                        f"konflik integritas {table} id={row_id}: digest berbeda "
                        f"(existing≠incoming) — bukan retry idempotent")
                self.conn.rollback()
                return row_id  # retry idempotent
            now = utc_now()
            self.conn.execute(
                "INSERT INTO LedgerAppend(table_name,row_id,recorded_at) VALUES(?,?,?)",
                (table, row_id, now))
            seq = self.conn.execute(
                "SELECT seq FROM LedgerAppend WHERE table_name=? AND row_id=?",
                (table, row_id)).fetchone()[0]
            cols = ["id", "ledger_seq", "recorded_at", "identity_payload", "result_digest"]
            vals = [row_id, seq, now, identity, digest]
            extra = _typed_columns(rec)
            for k, v in extra.items():
                cols.append(k)
                vals.append(_to_db(v))
            ph = ",".join("?" * len(cols))
            self.conn.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) "
                "ON CONFLICT DO NOTHING", vals)
            self.conn.commit()
            return row_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise LedgerIntegrityError(f"constraint {table}: {e}") from e

    def max_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM LedgerAppend").fetchone()
        return int(row[0])

    # ---- query helpers ----------------------------------------------------
    def get_publication_by_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT id, attempt_id, result, n_universe, disjoint_counts, data_asof, "
            "published_at, temporal_status, diagnostic_reason, freshness_deadline, "
            "metrics_manifest_id FROM Publication WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "attempt_id": row[1], "result": row[2],
                "n_universe": row[3], "disjoint_counts": json.loads(row[4]),
                "data_asof": row[5], "published_at": row[6],
                "temporal_status": row[7], "diagnostic_reason": row[8],
                "freshness_deadline": row[9], "metrics_manifest_id": row[10]}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT id, slot_id, generation, evaluation_clock, availability_mode, "
            "input_manifest_id, decision_at, price_asof FROM Run WHERE id=?",
            (run_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "slot_id": row[1], "generation": row[2],
                "evaluation_clock": row[3], "availability_mode": row[4],
                "input_manifest_id": row[5], "decision_at": row[6], "price_asof": row[7]}

    def get_attempt_ids(self, run_id: str) -> List[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT id FROM Attempt WHERE run_id=? ORDER BY attempt_no", (run_id,))]

    def last_attempt_no(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(attempt_no),0) FROM Attempt WHERE run_id=?",
            (run_id,)).fetchone()
        return int(row[0])

    def append_attempt_event(self, rec: AttemptEventRec) -> str:
        return self.append("AttemptEvent", rec)

    def recent_publications(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT run_id, result, n_universe, disjoint_counts, published_at, "
            "temporal_status FROM Publication ORDER BY published_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"run_id": r[0], "result": r[1], "n_universe": r[2],
                 "disjoint_counts": json.loads(r[3]), "published_at": r[4],
                 "temporal_status": r[5]} for r in rows]


# ---- pemetaan record → kolom bertipe -------------------------------------

def _typed_columns(rec) -> Dict[str, Any]:
    import dataclasses
    d = dataclasses.asdict(rec)
    # kolom yang bukan bagian payload DB bertipe (sudah di identity/digest)
    for skip in ("id",):
        d.pop(skip, None)
    return d


def _to_db(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return canonical_json(dict(v)) if isinstance(v, dict) else canonical_json({"v": list(v)})[4:]
    if isinstance(v, bool):
        return int(v)
    return v
