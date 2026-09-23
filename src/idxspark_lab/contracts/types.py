# contracts/types.py — Record bertipe untuk ledger 0.1 (Lampiran C §2/§4/§5/§7)
# Payload identitas MENGECUALIKAN ledger_seq/recorded_at/waktu retry.
# Nilai default None = kolom nullable; wajib diisi konfigurasi eksperimen.

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

from .canonical import canonical_id


@dataclass
class ArtifactRec:
    kind: str            # ARTIFACT_KINDS
    uri: str             # path relatif root lab (bukan URL produksi)
    sha256: str
    byte_length: int
    schema_version: str
    created_by: str

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class RegistryEventRec:
    artifact_id: str
    event: str           # REGISTRY_EVENTS
    actor: str
    reason: str
    evidence_artifact_id: Optional[str] = None
    supersedes_id: Optional[str] = None

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class ExperimentRec:
    name: str
    charter_artifact_id: str
    config_id: str
    feature_spec_id: str
    universe_policy_id: str
    git_commit: str
    dependency_lock_hash: str
    mode: str            # EVALUATION_CLOCKS (REPLAY untuk lab 0.1)
    research_role: str   # RESEARCH_ROLES
    availability_mode: str
    target_spec_id: Optional[str] = None
    execution_policy_id: Optional[str] = None
    parent_experiment_id: Optional[str] = None
    availability_policy_id: Optional[str] = None

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class DecisionSlotRec:
    experiment_id: str
    decision_at: int     # Utc
    expected_by: int
    entry_decision_deadline: int
    freshness_deadline: int
    session_id: str

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class RunRec:
    """run_id = H(slot/generation, evaluation_clock, SelectionContext,
    kontrak input/config/universe/model/code/cutoff/seed) — Lampiran C §4."""
    slot_id: str
    generation: int
    config_id: str
    universe_manifest_id: str
    input_manifest_id: str
    feature_spec_id: str
    availability_mode: str
    ledger_cutoff_seq: int
    evaluation_clock: str
    price_asof: int
    decision_at: int
    git_commit: str
    environment_hash: str
    seed: int
    model_id: Optional[str] = None
    calibrator_id: Optional[str] = None
    paper_book_id: Optional[str] = None
    selection_context_id: Optional[str] = None

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class AttemptRec:
    run_id: str
    attempt_no: int
    worker_build: str
    started_at: int = 0

    def identity(self) -> Dict[str, Any]:
        # started_at = waktu dinding, BUKAN identitas semantik
        return {"run_id": self.run_id, "attempt_no": self.attempt_no,
                "worker_build": self.worker_build}

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class AttemptEventRec:
    attempt_id: str
    event_no: int
    state: str           # ATTEMPT_STATES
    error_code: Optional[str] = None
    details_artifact_id: Optional[str] = None

    def identity(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def id(self) -> str:
        return canonical_id(self.identity())


@dataclass
class PublicationRec:
    run_id: str
    attempt_id: str
    result: str          # PUBLICATION_RESULTS
    n_universe: int
    disjoint_counts: Dict[str, int]
    data_asof: int
    published_at: int
    temporal_status: str  # TEMPORAL_STATUS
    freshness_deadline: int
    candidate_manifest_id: Optional[str] = None
    decision_manifest_id: Optional[str] = None
    metrics_manifest_id: Optional[str] = None
    diagnostic_reason: Optional[str] = None

    def identity(self) -> Dict[str, Any]:
        # published_at dicatat, tetapi BUKAN identitas semantik run (retry identik
        # memakai run_id sama). Identitas publication = run_id (UNIQUE).
        return {"run_id": self.run_id}

    @property
    def id(self) -> str:
        return canonical_id(self.identity())

    def result_digest(self) -> Dict[str, Any]:
        """Digest hasil immutable untuk perbandingan retry (bukan identitas)."""
        return {
            "result": self.result,
            "n_universe": self.n_universe,
            "disjoint_counts": self.disjoint_counts,
            "data_asof": self.data_asof,
            "temporal_status": self.temporal_status,
            "metrics_manifest_id": self.metrics_manifest_id,
            "diagnostic_reason": self.diagnostic_reason,
        }
