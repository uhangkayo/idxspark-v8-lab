# tests/test_ledger.py — Append-only, single-writer, idempotensi, digest konflik

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idxspark_lab.contracts.canonical import canonical_json  # noqa: E402
from idxspark_lab.contracts.types import ArtifactRec, PublicationRec  # noqa: E402
from idxspark_lab.persistence.ledger import (  # noqa: E402
    Ledger, LedgerIntegrityError, WriterLockHeld,
)


def _artifact(uri="x.bin", sha="ab" * 32):
    return ArtifactRec(kind="REPORT", uri=uri, sha256=sha, byte_length=1,
                       schema_version="1", created_by="test")


def _pub(run_id="r1", attempt="a1", result="DIAGNOSTIC_ONLY", n_uni=3):
    return PublicationRec(run_id=run_id, attempt_id=attempt, result=result,
                          n_universe=n_uni,
                          disjoint_counts={"n_universe": n_uni},
                          data_asof=1, published_at=1,
                          temporal_status="RETROSPECTIVE", freshness_deadline=2)


def _mk_chain(led: Ledger) -> tuple:
    """Rantai minimal Artifact→Experiment→Slot→Run→Attempt (untuk FK Publication)."""
    ids = [_artifact(uri=f"c{i}.bin") for i in range(4)]
    for a in ids:
        led.append("Artifact", a)
    from idxspark_lab.contracts.types import (AttemptRec, DecisionSlotRec,
                                              ExperimentRec, RunRec)
    exp = ExperimentRec(name="t", charter_artifact_id=ids[0].id,
                        config_id=ids[1].id, feature_spec_id=ids[2].id,
                        universe_policy_id=ids[3].id, git_commit="x",
                        dependency_lock_hash="x", mode="REPLAY",
                        research_role="DIAGNOSTIC", availability_mode="OBSERVED")
    eid = led.append("Experiment", exp)
    slot = DecisionSlotRec(experiment_id=eid, decision_at=1, expected_by=2,
                           entry_decision_deadline=3, freshness_deadline=4,
                           session_id="s")
    sid = led.append("DecisionSlot", slot)
    run = RunRec(slot_id=sid, generation=1, config_id=ids[1].id,
                 universe_manifest_id=ids[3].id, input_manifest_id=ids[3].id,
                 feature_spec_id=ids[2].id, availability_mode="OBSERVED",
                 ledger_cutoff_seq=0, evaluation_clock="REPLAY", price_asof=1,
                 decision_at=1, git_commit="x", environment_hash="x", seed=0)
    rid = led.append("Run", run)
    att = AttemptRec(run_id=rid, attempt_no=1, worker_build="t")
    aid = led.append("Attempt", att)
    return rid, aid


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_then_idempotent_retry(self):
        led = Ledger(self.db)
        rec = _artifact()
        rid = led.append("Artifact", rec)
        rid2 = led.append("Artifact", rec)  # retry identik → OK
        self.assertEqual(rid, rid2)
        n = led.conn.execute("SELECT COUNT(*) FROM Artifact").fetchone()[0]
        self.assertEqual(n, 1)
        led.close()

    def test_conflicting_digest_raises(self):
        led = Ledger(self.db)
        rid, aid = _mk_chain(led)
        led.append("Publication", _pub(run_id=rid, attempt=aid))
        # run_id sama (identity sama) tapi hasil beda → konflik integritas
        with self.assertRaises(LedgerIntegrityError):
            led.append("Publication", _pub(run_id=rid, attempt=aid, n_uni=7))
        led.close()

    def test_update_and_delete_forbidden(self):
        led = Ledger(self.db)
        rid = led.append("Artifact", _artifact())
        with self.assertRaises(sqlite3.IntegrityError):
            led.conn.execute("UPDATE Artifact SET uri='hacked' WHERE id=?", (rid,))
        with self.assertRaises(sqlite3.IntegrityError):
            led.conn.execute("DELETE FROM Artifact WHERE id=?", (rid,))
        led.close()

    def test_single_writer_lock(self):
        led = Ledger(self.db)
        with self.assertRaises(WriterLockHeld):
            Ledger(self.db)  # writer kedua ditolak (fail-closed)
        led.close()
        led2 = Ledger(self.db)  # lock dilepas setelah close
        led2.close()

    def test_publication_unique_per_run(self):
        led = Ledger(self.db)
        rid, aid = _mk_chain(led)
        pub = _pub(run_id=rid, attempt=aid)
        pid = led.append("Publication", pub)
        # attempt beda + published_at beda → retry idempotent (digest sama)
        pub2 = _pub(run_id=rid, attempt=aid)
        pub2.published_at = 9
        self.assertEqual(pid, led.append("Publication", pub2))
        n = led.conn.execute("SELECT COUNT(*) FROM Publication").fetchone()[0]
        self.assertEqual(n, 1)
        led.close()

    def test_canonical_json_stable(self):
        a = {"b": 1, "a": {"z": [1, 2], "y": "s"}}
        b = {"a": {"y": "s", "z": [1, 2]}, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_unique_slot_generation_not_swallowed(self):
        """Regresi 24-09: INSERT Run dengan (slot_id, generation) yang sudah ada
        WAJIB raise — bukan ditelan ON CONFLICT DO NOTHING lalu FK Attempt
        gagal dengan baris Run hantu (append seq dangling)."""
        led = Ledger(self.db)
        from idxspark_lab.contracts.types import RunRec
        rid, _ = _mk_chain(led)
        slot_id, config_id, spec_id = led.conn.execute(
            "SELECT slot_id, config_id, feature_spec_id FROM Run WHERE id=?",
            (rid,)).fetchone()
        # manifest input BARU (artifact nyata — FK harus valid)
        u2 = _artifact(uri="snap2.json", sha="cd" * 32)
        u2_id = led.append("Artifact", u2)
        common = dict(slot_id=slot_id, config_id=config_id,
                      universe_manifest_id=u2_id, input_manifest_id=u2_id,
                      feature_spec_id=spec_id, availability_mode="OBSERVED",
                      ledger_cutoff_seq=0, evaluation_clock="REPLAY", price_asof=1,
                      decision_at=1, git_commit="x", environment_hash="x", seed=0)
        # run BARU: identity beda (input manifest beda) tapi (slot, generation=1) sama
        with self.assertRaises(LedgerIntegrityError):
            led.append("Run", RunRec(generation=1, **common))
        # nol baris Run hantu; append log tanpa dangling baru
        n = led.conn.execute("SELECT COUNT(*) FROM Run").fetchone()[0]
        self.assertEqual(n, 1)
        dang = led.conn.execute(
            "SELECT COUNT(*) FROM LedgerAppend la LEFT JOIN Run r ON r.id=la.row_id "
            "WHERE la.table_name='Run' AND r.id IS NULL").fetchone()[0]
        self.assertEqual(dang, 0)
        # jalur benar: lookup (slot, manifest) → None; generasi increment → run kedua MASUK
        self.assertIsNone(led.get_run_id_by_slot_manifest(slot_id, u2_id))
        rid3 = led.append("Run", RunRec(generation=led.last_generation(slot_id) + 1, **common))
        self.assertEqual(led.last_generation(slot_id), 2)
        self.assertNotEqual(rid, rid3)
        self.assertEqual(led.get_run_id_by_slot_manifest(slot_id, u2_id), rid3)
        led.close()


if __name__ == "__main__":
    unittest.main()
