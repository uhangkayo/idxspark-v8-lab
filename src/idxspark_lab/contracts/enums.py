# contracts/enums.py — Enum kontrak Lampiran C (dibekukan, jangan diubah diam-diam)

# Artifact.kind
ARTIFACT_KINDS = (
    "RAW", "DATA_MANIFEST", "UNIVERSE", "CONFIG", "FEATURE_SPEC", "MODEL",
    "CALIBRATOR", "HEAD_MANIFEST", "TARGET_SPEC", "EXECUTION_POLICY",
    "SELECTION_CONTEXT", "CORPORATE_ACTION", "MATRIX", "REPORT", "CHECKPOINT",
)

REGISTRY_EVENTS = ("REGISTERED", "VALIDATED", "REJECTED", "RETIRED")

# Experiment.mode / Run.evaluation_clock
EVALUATION_CLOCKS = ("REPLAY", "FORWARD")

# Experiment.research_role — ASSUMED_LAG hanya DIAGNOSTIC/EXPLORATORY, tidak CONFIRMATORY
RESEARCH_ROLES = ("DIAGNOSTIC", "EXPLORATORY", "CONFIRMATORY")

# Availability mode (blueprint §4.2)
AVAILABILITY_MODES = ("OBSERVED", "PUBLIC_ARCHIVE_VERIFIED", "ASSUMED_LAG")

# AttemptEvent.state — jalur tanpa model melewati SCORED dengan manifest alasan eksplisit
ATTEMPT_STATES = (
    "STARTED", "INPUT_VALIDATED", "FEATURES_READY", "SCORED",
    "DECIDED", "PUBLISHED", "FAILED", "CANCELLED",
)
ATTEMPT_TERMINAL = ("PUBLISHED", "FAILED", "CANCELLED")

# Publication.result
PUBLICATION_RESULTS = ("VALID_WITH_SELECTION", "VALID_EMPTY", "DIAGNOSTIC_ONLY")

# Publication.temporal_status
TEMPORAL_STATUS = ("FORWARD_ON_TIME", "RETROSPECTIVE")

# Publication.quality_status
QUALITY_STATUS = ("COMPLETE", "DEGRADED_ALLOWED")

# terminal_bucket kandidat (7 bucket disjoint; jumlah = universe)
TERMINAL_BUCKETS = (
    "DATA_UNUSABLE", "INELIGIBLE", "FEATURE_INVALID", "MODEL_INVALID",
    "GATE_REJECTED", "QUALIFIED_NOT_SELECTED", "SELECTED",
)

# Bucket diagnostik profil 0.1 (bukan enum seleksi; dilaporkan terpisah)
PROFILE_STATUS = ("PROFILE_OK", "DATA_UNUSABLE")

# RawObservation.ingest_mode
INGEST_MODES = ("LIVE", "ARCHIVE", "BACKFILL")

# OutcomeVersion.status — NO_FILL/PENDING/CENSORED bukan LOSS
OUTCOME_STATUS = ("PENDING", "CENSORED", "NO_FILL", "CLOSED", "INVALID")

# Snapshot lifecycle (blueprint §4.1)
SNAPSHOT_STATUS = ("INCOMING", "VALIDATING", "READY", "QUARANTINED")

# Kodifikasi versi kontrak
CODE_VERSION = "v8lab-0.1.0"
LEDGER_SCHEMA_VERSION = "1"
