# contracts/canonical.py — Identitas, hash, dan JSON kanonis (Lampiran C §1)
# Id = SHA256(canonical_payload); Utc = int64 epoch microseconds UTC.
# Pure stdlib. Tidak membuka DB, jaringan, file, atau env saat import.

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> int:
    """Epoch microseconds UTC (Utc)."""
    return time.time_ns() // 1000


def iso_from_utc(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc).isoformat()


def utc_from_iso(s: str) -> int:
    return int(datetime.fromisoformat(s).astimezone(timezone.utc).timestamp() * 1_000_000)


def _check_finite(obj: Any) -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("nilai non-finite dilarang dalam payload kanonis")
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_finite(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_finite(v)


def canonical_json(payload: Dict[str, Any]) -> str:
    """JSON kanonis: key terurut, tanpa whitespace, angka finite, list berurut.

    Urutan key dibekukan (sort_keys=True); urutan list adalah bagian identitas
    dan TIDAK diurutkan ulang oleh serializer.
    """
    _check_finite(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_id(payload: Dict[str, Any]) -> str:
    """Id = SHA256(canonical_payload)."""
    return sha256_hex(canonical_json(payload).encode("utf-8"))


# ---- Value: Known | Missing | NotApplicable | Invalid | Conflict ----------
# Unknown tidak pernah diubah menjadi 0 (aturan Lampiran C §1).

VALUE_MISSING = "MISSING"
VALUE_NA = "NOT_APPLICABLE"
VALUE_INVALID = "INVALID"
VALUE_CONFLICT = "CONFLICT"


def value_known(v: Any) -> Dict[str, Any]:
    return {"kind": "KNOWN", "value": v}


def value_missing(reason: str) -> Dict[str, Any]:
    return {"kind": VALUE_MISSING, "reason": reason}


def value_invalid(reason: str) -> Dict[str, Any]:
    return {"kind": VALUE_INVALID, "reason": reason}


def value_na(reason: str) -> Dict[str, Any]:
    return {"kind": VALUE_NA, "reason": reason}


def value_conflict(version_ids) -> Dict[str, Any]:
    return {"kind": VALUE_CONFLICT, "version_ids": list(version_ids)}


def environment_hash(py_version: str, code_version: str) -> str:
    """Hash lingkungan eksekusi untuk Run.environment_hash."""
    return sha256_hex(f"python={py_version};code={code_version}".encode("utf-8"))
