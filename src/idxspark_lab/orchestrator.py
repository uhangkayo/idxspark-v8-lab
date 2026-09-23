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


def _profile_spec_artifact(ledger: Ledger) -> str:
    spec = canonical_json({
        "kind": "FEATURE_SPEC", "name": "profile_v1",
        "stage": "S1-S3-lite", "features": [],
        "checks": ["history", "missing_sessions", "zero_volume", "ohlc_violation",
                   "nonpos_close", "stale_tail", "survivorship_lrp", "raw_coverage"],
        "min_history_sessions": 250,
    })
    return _register_artifact(
        ledger, "FEATURE_SPEC", "specs/profile_v1.json",
        sha256_hex(spec.encode()), len(spec.encode()), "profile-spec/1", "v8lab")


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
                force_retry: bool = False) -> Dict[str, Any]:
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
            feature_spec_id = _profile_spec_artifact(ledger)
            universe_policy_id = _universe_policy_artifact(ledger)
            charter_id = _charter_artifact(ledger)

            experiment = ExperimentRec(
                name="v8lab-01-profile", charter_artifact_id=charter_id,
                config_id=config_id, feature_spec_id=feature_spec_id,
                universe_policy_id=universe_policy_id,
                git_commit=_git_commit(), dependency_lock_hash=platform.python_version(),
                mode="REPLAY", research_role="DIAGNOSTIC", availability_mode="OBSERVED")
            experiment_id = ledger.append("Experiment", experiment)

            session_id = f"PROFILE-{cutoff}"
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
            run = RunRec(
                slot_id=slot_id, generation=1, config_id=config_id,
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
            coverage = prof.coverage_report(reader)
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
                disjoint_counts=dict(coverage["funnel"]),
                data_asof=decision_at, published_at=utc_now(),
                temporal_status="RETROSPECTIVE",
                freshness_deadline=freshness_deadline,
                metrics_manifest_id=metrics_id,
                diagnostic_reason="profil coverage tanpa model/seleksi (LAB-03)")
            try:
                ledger.append("Publication", pub)
            except LedgerIntegrityError as e:
                # publication sudah ada dengan digest beda → ini bug determinisme
                raise
            ev(5, "PUBLISHED")

            return {"status": "PUBLISHED", "run_id": run_id,
                    "attempt_id": attempt_id,
                    "result_dir": str(final),
                    "funnel": coverage["funnel"],
                    "snapshot_id": snapshot_id, "cutoff": cutoff}
    finally:
        ledger.close()
