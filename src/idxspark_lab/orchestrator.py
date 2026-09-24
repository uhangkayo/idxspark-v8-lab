# orchestrator.py — Siklus hidup run profil diagnostik (Lampiran C §4/§7)
# run_id deterministik dari input; publication tunggal per run_id; retry
# identik = attempt baru, hasil semantik sama. Crash sebelum commit → nol
# publication; setelah commit → publication sama terbaca kembali.

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .adapters.snapshot_reader import SnapshotReader
from .config import load_config
from .contracts import enums
from .contracts.canonical import (
    canonical_id, canonical_json, environment_hash, sha256_hex, utc_from_iso, utc_now,
)
from .contracts.types import (
    ArtifactRec, AttemptEventRec, AttemptRec, DecisionSlotRec, ExperimentRec,
    PublicationRec, RegistryEventRec, RunRec,
)
from .persistence.ledger import Ledger, LedgerIntegrityError
from .preflight import preflight
from .reporting import profile as prof
from .reporting import report_md

FRESHNESS_HOURS = 24
DECISION_HOUR_WIB = 8  # slot 08:00 WIB = 01:00 UTC


def _wib_decision_utc(cutoff_date: str) -> int:
    return utc_from_iso(f"{cutoff_date}T01:00:00+00:00")


def _write_artifact_atomic(staging: Path, final: Path, name: str,
                           content: bytes) -> Dict[str, Any]:
    f = staging / name
    f.write_bytes(content)
    with open(f, "rb") as fh:
        os.fsync(fh.fileno())
    return {"file": name, "sha256": sha256_hex(content),
            "byte_length": len(content)}


def _register_artifact(ledger: Ledger, kind: str, uri: str, sha: str,
                       nbytes: int, schema_version: str, created_by: str) -> str:
    rec = ArtifactRec(kind=kind, uri=uri, sha256=sha, byte_length=nbytes,
                      schema_version=schema_version, created_by=created_by)
    rid = ledger.append("Artifact", rec)
    ev = RegistryEventRec(artifact_id=rid, event="REGISTERED", actor=created_by,
                          reason="registered by v8lab orchestrator")
    try:
        ledger.append("RegistryEvent", ev)
    except LedgerIntegrityError:
        pass  # event registry idempotent-unsafe (actor sama) — abaikan dup
    return rid


def _config_artifact(cfg_path: str, ledger: Ledger) -> str:
    content = Path(cfg_path).read_bytes()
    return _register_artifact(
        ledger, "CONFIG", "configs/lab.yaml", sha256_hex(content), len(content),
        "lab-config/1", "v8lab")


def _profile_spec_artifact(ledger: Ledger, spec_version: str = "v2") -> str:
    if spec_version == "v3":
        spec = canonical_json({
            "kind": "FEATURE_SPEC", "name": "profile_v3",
            "stage": "S1-S3-lite+freshness-tail",
            "features": [],
            "checks": ["history", "missing_sessions", "zero_volume", "ohlc_violation",
                       "nonpos_close", "stale_tail", "survivorship_lrp", "raw_coverage",
                       "tail_gap_sessions"],
            "min_history_sessions": 250,
            "ohlc_epsilon_relative": 1e-4,
            "ohlc_unusable_rule": ">3 baris DAN >0.2% baris",
            "stale_tail_gap_sessions": 5,
        })
        return _register_artifact(
            ledger, "FEATURE_SPEC", "specs/profile_v3.json",
            sha256_hex(spec.encode()), len(spec.encode()), "profile-spec/1", "v8lab")
    spec = canonical_json({
        "kind": "FEATURE_SPEC", "name": "profile_v2",
        "stage": "S1-S3-lite", "features": [],
        "checks": ["history", "missing_sessions", "zero_volume", "ohlc_violation",
                   "nonpos_close", "stale_tail", "survivorship_lrp", "raw_coverage"],
        "min_history_sessions": 250,
        "ohlc_epsilon_relative": 1e-4,
        "ohlc_unusable_rule": ">3 baris DAN >0.2% baris",
    })
    return _register_artifact(
        ledger, "FEATURE_SPEC", "specs/profile_v2.json",
        sha256_hex(spec.encode()), len(spec.encode()), "profile-spec/1", "v8lab")


def _e0_spec_artifact(ledger: Ledger, days: int, fee_profile: str) -> str:
    from .baselines.v7_core import V7_CORE_PARAMS
    spec = canonical_json({
        "kind": "FEATURE_SPEC", "name": "e0_b01_core_v1",
        "stage": "E0-baseline-parity",
        "arms": {
            "B0": {"name": "B0_V7_FROZEN", "r20_intervals": 19,
                   "stale_candidates": "scored (paritas bug data-as-of produksi)"},
            "B1": {"name": "B1_V7_CORRECTED", "r20_intervals": 20,
                   "stale_candidates": "blocked (fix data-as-of)"},
        },
        "params": V7_CORE_PARAMS,
        "excluded_components": ["anomaly_bonus_15", "alpha158_bonus_10",
                                "fundamental_eps_der_20", "gorengan_tier",
                                "kill_switch", "threshold_override_learning"],
        "max_score_pure_core": 100,
        "fee_profile": fee_profile,
        "decision_sessions": days,
        "kernel": "exit chronology V7.3 REAL-EXIT; fill @close keputusan",
    })
    return _register_artifact(
        ledger, "FEATURE_SPEC", "specs/e0_b01_core_v1.json",
        sha256_hex(spec.encode()), len(spec.encode()), "e0-spec/1", "v8lab")


def _e0_charter_artifact(ledger: Ledger) -> str:
    charter = canonical_json({
        "kind": "CHARTER", "experiment": "v8lab-02-e0-b01",
        "goal": "parity B0 (V7 frozen) vs B1 (r20 20-interval + fix data-as-of) "
                "di atas satu kernel transaksi yang sama",
        "non_goals": ["klaim CAGR produksi", "promosi model", "order nyata"],
        "acceptance": "7-bucket terminal invariant per tanggal keputusan; "
                      "delta seleksi B0/B1 teratribusi",
    })
    return _register_artifact(
        ledger, "REPORT", "charters/v8lab-02.json",
        sha256_hex(charter.encode()), len(charter.encode()), "charter/1", "v8lab")


def _universe_policy_artifact(ledger: Ledger) -> str:
    pol = canonical_json({
        "kind": "UNIVERSE_POLICY", "name": "union-adjusted-delisted-survivorship",
        "include": ["prices", "prices_delisted", "survivorship.delisted_final"],
        "basis": "adjusted+close_only+raw dicatat terpisah",
    })
    return _register_artifact(
        ledger, "UNIVERSE", "specs/universe_union_v1.json",
        sha256_hex(pol.encode()), len(pol.encode()), "universe-policy/1", "v8lab")


def _charter_artifact(ledger: Ledger) -> str:
    charter = canonical_json({
        "kind": "CHARTER", "experiment": "v8lab-01-profile",
        "goal": "profil coverage & kualitas data snapshot sebelum fitur/model",
        "non_goals": ["seleksi sinyal", "paper order", "promosi model"],
        "acceptance": "funnel disjoint = universe; report reproducible dari manifest",
    })
    return _register_artifact(
        ledger, "REPORT", "charters/v8lab-01.json",
        sha256_hex(charter.encode()), len(charter.encode()), "charter/1", "v8lab")


def _git_commit() -> str:
    env = os.environ.get("V8LAB_GIT_COMMIT", "none")
    return env


def run_profile(config_path: str, snapshot_id: str,
                force_retry: bool = False,
                spec_version: str = "v2") -> Dict[str, Any]:
    pre = preflight(config_path, snapshot_id=snapshot_id, mode="diagnostic")
    cfg = pre["config"]
    output_root = Path(pre["output_root"])
    state_db = pre["state_db"]
    ledger = Ledger(state_db)

    try:
        inputs_dir = str(Path(pre["input_root"]) / "inputs")
        with SnapshotReader(inputs_dir, snapshot_id) as reader:
            cutoff = reader.manifest["effective_cutoff"]
            if not cutoff:
                raise ValueError("snapshot tanpa effective_cutoff — tidak bisa run")
            decision_at = _wib_decision_utc(cutoff)

            # ---- artifacts kontrak (deterministik) ----
            config_id = _config_artifact(config_path, ledger)
            feature_spec_id = _profile_spec_artifact(ledger, spec_version)
            universe_policy_id = _universe_policy_artifact(ledger)
            charter_id = _charter_artifact(ledger)

            experiment = ExperimentRec(
                name="v8lab-01-profile", charter_artifact_id=charter_id,
                config_id=config_id, feature_spec_id=feature_spec_id,
                universe_policy_id=universe_policy_id,
                git_commit=_git_commit(), dependency_lock_hash=platform.python_version(),
                mode="REPLAY", research_role="DIAGNOSTIC", availability_mode="OBSERVED")
            experiment_id = ledger.append("Experiment", experiment)

            session_id = f"PROFILE-{cutoff}" if spec_version == "v2" \
                else f"PROFILE3-{cutoff}"
            freshness_deadline = decision_at + FRESHNESS_HOURS * 3600 * 1_000_000
            slot = DecisionSlotRec(
                experiment_id=experiment_id, decision_at=decision_at,
                expected_by=decision_at + 1800 * 1_000_000,
                entry_decision_deadline=decision_at + 3600 * 1_000_000,
                freshness_deadline=freshness_deadline, session_id=session_id)
            slot_id = ledger.append("DecisionSlot", slot)

            snapshot_manifest_bytes = json.dumps(reader.manifest, sort_keys=True).encode()
            snapshot_artifact = _register_artifact(
                ledger, "DATA_MANIFEST", f"snapshots/{snapshot_id}/manifest.json",
                sha256_hex(snapshot_manifest_bytes), len(snapshot_manifest_bytes),
                "snapshot/1", "v8lab-bridge")

            env_hash = environment_hash(platform.python_version(), enums.CODE_VERSION)
            # Kontrak slot/run (Lampiran C §4 + "snapshot baru = run baru"):
            #   * retry snapshot IDENTIK → run sama (idempotent, generation tetap)
            #   * snapshot BARU di slot sama → generasi increment (run baru)
            #   * cutoff baru → slot baru → generasi 1
            existing_run_id = ledger.get_run_id_by_slot_manifest(
                slot_id, snapshot_artifact)
            if existing_run_id is not None:
                run_id = existing_run_id
            else:
                run = RunRec(
                    slot_id=slot_id,
                    generation=ledger.last_generation(slot_id) + 1,
                    config_id=config_id,
                    universe_manifest_id=snapshot_artifact,
                    input_manifest_id=snapshot_artifact,
                    feature_spec_id=feature_spec_id,
                    availability_mode="OBSERVED", ledger_cutoff_seq=0,
                    evaluation_clock="REPLAY",
                    price_asof=decision_at, decision_at=decision_at,
                    git_commit=_git_commit(), environment_hash=env_hash, seed=0)
                run_id = ledger.append("Run", run)

            existing = ledger.get_publication_by_run(run_id)
            if existing and not force_retry:
                return {"status": "IDEMPOTENT_HIT", "run_id": run_id,
                        "publication": existing}

            attempt_no = ledger.last_attempt_no(run_id) + 1
            attempt = AttemptRec(run_id=run_id, attempt_no=attempt_no,
                                 worker_build=enums.CODE_VERSION, started_at=utc_now())
            attempt_id = ledger.append("Attempt", attempt)

            def ev(n: int, state: str, err: Optional[str] = None) -> None:
                ledger.append("AttemptEvent", AttemptEventRec(
                    attempt_id=attempt_id, event_no=n, state=state, error_code=err))

            ev(1, "STARTED")
            ev(2, "INPUT_VALIDATED")

            # ---- komputasi profil (deterministik dari snapshot) ----
            coverage = prof.coverage_report(reader, spec_version)
            ev(3, "FEATURES_READY")

            # ---- artifacts hasil: staging → fsync → rename atomik ----
            results_dir = output_root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            staging = results_dir / f"{run_id}.staging"
            if staging.exists():
                shutil.rmtree(staging)  # sisa crash sebelumnya = orphan
            staging.mkdir()
            final = results_dir / run_id
            if final.exists():
                shutil.rmtree(final)  # hasil deterministik; regenerasi identik

            cov_bytes = canonical_json(coverage).encode("utf-8")
            ev(4, "SCORED")
            report_text = report_md.render_report_md(coverage, run_id, {
                "temporal_status": "RETROSPECTIVE", "result": "DIAGNOSTIC_ONLY"})
            rep_bytes = report_text.encode("utf-8")

            cov_art = _write_artifact_atomic(staging, final, "coverage.json", cov_bytes)
            rep_art = _write_artifact_atomic(staging, final, "report.md", rep_bytes)
            staging.rename(final)
            # fsync direktori (rename atomik ≠ durable)
            dirfd = os.open(final, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)

            metrics_id = _register_artifact(
                ledger, "REPORT", f"results/{run_id}/coverage.json",
                cov_art["sha256"], cov_art["byte_length"], "coverage/1", "v8lab")
            _register_artifact(
                ledger, "REPORT", f"results/{run_id}/report.md",
                rep_art["sha256"], rep_art["byte_length"], "report/1", "v8lab")

            # ---- publication atomik ----
            pub = PublicationRec(
                run_id=run_id, attempt_id=attempt_id, result="DIAGNOSTIC_ONLY",
                n_universe=coverage["funnel"]["n_universe"],
                disjoint_counts=dict(coverage["funnel_v3"] if spec_version == "v3"
                                     else coverage["funnel"]),
                data_asof=decision_at, published_at=utc_now(),
                temporal_status="RETROSPECTIVE",
                freshness_deadline=freshness_deadline,
                metrics_manifest_id=metrics_id,
                diagnostic_reason="profil coverage tanpa model/seleksi (LAB-03)"
                                  if spec_version == "v2"
                                  else "profil coverage + freshness tail v3 (audit 0.2)")
            try:
                ledger.append("Publication", pub)
            except LedgerIntegrityError as e:
                # publication sudah ada dengan digest beda → ini bug determinisme
                raise
            ev(5, "PUBLISHED")

            out = {"status": "PUBLISHED", "run_id": run_id,
                   "attempt_id": attempt_id,
                   "result_dir": str(final),
                   "funnel": coverage["funnel"],
                   "snapshot_id": snapshot_id, "cutoff": cutoff,
                   "spec_version": spec_version}
            if spec_version == "v3":
                out["funnel_v3"] = coverage["funnel_v3"]
            return out
    finally:
        ledger.close()


def run_e0(config_path: str, snapshot_id: str, days: int = 126,
           fee_profile: str = "PROD_FLAT",
           force_retry: bool = False) -> Dict[str, Any]:
    """E0 replay B0 ↔ B1 di atas satu kernel transaksi (LAB-05/06).
    Semua hasil = DIAGNOSTIC_ONLY (simulasi, bukan order)."""
    from .evaluation import replay as rep
    from .reporting import e0_report

    pre = preflight(config_path, snapshot_id=snapshot_id, mode="diagnostic")
    output_root = Path(pre["output_root"])
    state_db = pre["state_db"]
    ledger = Ledger(state_db)

    try:
        inputs_dir = str(Path(pre["input_root"]) / "inputs")
        with SnapshotReader(inputs_dir, snapshot_id) as reader:
            cutoff = reader.manifest["effective_cutoff"]
            if not cutoff:
                raise ValueError("snapshot tanpa effective_cutoff — tidak bisa run")
            decision_at = _wib_decision_utc(cutoff)

            config_id = _config_artifact(config_path, ledger)
            feature_spec_id = _e0_spec_artifact(ledger, days, fee_profile)
            universe_policy_id = _universe_policy_artifact(ledger)
            charter_id = _e0_charter_artifact(ledger)

            experiment = ExperimentRec(
                name="v8lab-02-e0-b01", charter_artifact_id=charter_id,
                config_id=config_id, feature_spec_id=feature_spec_id,
                universe_policy_id=universe_policy_id,
                git_commit=_git_commit(), dependency_lock_hash=platform.python_version(),
                mode="REPLAY", research_role="DIAGNOSTIC", availability_mode="OBSERVED")
            experiment_id = ledger.append("Experiment", experiment)

            # slot unik per (cutoff, days, fee_profile) — days/fee mempengaruhi
            # hasil sehingga WAJIB membuat slot terpisah agar idempotensi
            # tidak salah menjawab 'sudah pernah dijalankan'.
            session_id = f"E0-{cutoff}-d{days}-{fee_profile}"
            freshness_deadline = decision_at + FRESHNESS_HOURS * 3600 * 1_000_000
            slot = DecisionSlotRec(
                experiment_id=experiment_id, decision_at=decision_at,
                expected_by=decision_at + 1800 * 1_000_000,
                entry_decision_deadline=decision_at + 3600 * 1_000_000,
                freshness_deadline=freshness_deadline, session_id=session_id)
            slot_id = ledger.append("DecisionSlot", slot)

            snapshot_manifest_bytes = json.dumps(reader.manifest, sort_keys=True).encode()
            snapshot_artifact = _register_artifact(
                ledger, "DATA_MANIFEST", f"snapshots/{snapshot_id}/manifest.json",
                sha256_hex(snapshot_manifest_bytes), len(snapshot_manifest_bytes),
                "snapshot/1", "v8lab-bridge")

            env_hash = environment_hash(platform.python_version(), enums.CODE_VERSION)
            existing_run_id = ledger.get_run_id_by_slot_manifest(
                slot_id, snapshot_artifact)
            if existing_run_id is not None:
                run_id = existing_run_id
            else:
                run = RunRec(
                    slot_id=slot_id,
                    generation=ledger.last_generation(slot_id) + 1,
                    config_id=config_id,
                    universe_manifest_id=snapshot_artifact,
                    input_manifest_id=snapshot_artifact,
                    feature_spec_id=feature_spec_id,
                    availability_mode="OBSERVED", ledger_cutoff_seq=0,
                    evaluation_clock="REPLAY",
                    price_asof=decision_at, decision_at=decision_at,
                    git_commit=_git_commit(), environment_hash=env_hash, seed=0)
                run_id = ledger.append("Run", run)

            existing = ledger.get_publication_by_run(run_id)
            if existing and not force_retry:
                return {"status": "IDEMPOTENT_HIT", "run_id": run_id,
                        "publication": existing}

            attempt_no = ledger.last_attempt_no(run_id) + 1
            attempt = AttemptRec(run_id=run_id, attempt_no=attempt_no,
                                 worker_build=enums.CODE_VERSION, started_at=utc_now())
            attempt_id = ledger.append("Attempt", attempt)

            def ev(n: int, state: str, err: Optional[str] = None) -> None:
                ledger.append("AttemptEvent", AttemptEventRec(
                    attempt_id=attempt_id, event_no=n, state=state, error_code=err))

            ev(1, "STARTED")
            ev(2, "INPUT_VALIDATED")

            e0 = rep.run_e0_replay(reader, days=days, fee_profile=fee_profile)
            ev(3, "FEATURES_READY")

            results_dir = output_root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            staging = results_dir / f"{run_id}.staging"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            final = results_dir / run_id
            if final.exists():
                shutil.rmtree(final)

            e0_bytes = canonical_json(e0).encode("utf-8")
            ev(4, "SCORED")
            rep_text = e0_report.render_e0_report_md(e0, run_id)
            rep_bytes = rep_text.encode("utf-8")

            e0_art = _write_artifact_atomic(staging, final, "e0.json", e0_bytes)
            rep_art = _write_artifact_atomic(staging, final, "e0_report.md", rep_bytes)
            staging.rename(final)
            dirfd = os.open(final, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)

            metrics_id = _register_artifact(
                ledger, "REPORT", f"results/{run_id}/e0.json",
                e0_art["sha256"], e0_art["byte_length"], "e0/1", "v8lab")
            _register_artifact(
                ledger, "REPORT", f"results/{run_id}/e0_report.md",
                rep_art["sha256"], rep_art["byte_length"], "report/1", "v8lab")

            # disjoint_counts int: bucket 7-enum tanggal keputusan TERAKHIR
            # (B0) + jumlah trade per arm.
            last_buckets = dict(e0["arms"]["B0"]["bucket_daily"][-1]["buckets"]) \
                if e0["arms"]["B0"]["bucket_daily"] else {}
            disjoint = {k: int(v) for k, v in last_buckets.items()}
            disjoint["trades_B0"] = e0["arms"]["B0"]["summary"]["n_trades_closed"]
            disjoint["trades_B1"] = e0["arms"]["B1"]["summary"]["n_trades_closed"]
            disjoint["positions_open_B0"] = e0["arms"]["B0"]["summary"]["n_positions_open_end"]
            disjoint["positions_open_B1"] = e0["arms"]["B1"]["summary"]["n_positions_open_end"]
            n_universe_last = sum(last_buckets.values()) if last_buckets else 0

            pub = PublicationRec(
                run_id=run_id, attempt_id=attempt_id, result="DIAGNOSTIC_ONLY",
                n_universe=n_universe_last,
                disjoint_counts=disjoint,
                data_asof=decision_at, published_at=utc_now(),
                temporal_status="RETROSPECTIVE",
                freshness_deadline=freshness_deadline,
                metrics_manifest_id=metrics_id,
                diagnostic_reason="E0 replay baseline B0/B1 — simulasi, bukan "
                                  "order; pure-core skor maks 100 (LAB-05)")
            ledger.append("Publication", pub)
            ev(5, "PUBLISHED")

            return {"status": "PUBLISHED", "run_id": run_id,
                    "attempt_id": attempt_id,
                    "result_dir": str(final),
                    "summary": {"B0": e0["arms"]["B0"]["summary"],
                                "B1": e0["arms"]["B1"]["summary"]},
                    "e0_delta": e0["e0_delta"],
                    "snapshot_id": snapshot_id, "cutoff": cutoff}
    finally:
        ledger.close()
