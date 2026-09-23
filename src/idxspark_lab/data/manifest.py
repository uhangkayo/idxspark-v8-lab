# data/manifest.py — Kontrak snapshot & manifest (blueprint §4.1)
# Lifecycle: INCOMING → VALIDATING → READY | QUARANTINED.
# Nama file/folder BUKAN bukti ketersediaan — hanya manifest READY yang valid.

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts.canonical import canonical_id, sha256_hex, canonical_json

MANIFEST_NAME = "manifest.json"


def hash_file(path: Path) -> str:
    h = sha256_hex(b"")
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(sources: List[Dict[str, Any]], created_at: int,
                   timezone_name: str = "Asia/Jakarta") -> Dict[str, Any]:
    """sources: [{name, file (path relatif staging), sha256, bytes, rows,
    tables: {tbl: {rows, min_date, max_date, date_column}}, source_path,
    integrity: 'ok'}] — effective_cutoff = MIN watermark sumber wajib."""
    watermarks: List[str] = []
    for s in sources:
        for tbl, meta in (s.get("tables") or {}).items():
            mx = meta.get("max_date")
            if mx:
                watermarks.append(str(mx))
    # cutoff efektif = MIN watermark ber-granulari HARI (YYYY-MM-DD).
    # Watermark bulanan (YYYY-MM) hanya dicatat — tidak menarik cutoff turun.
    day_watermarks = [w for w in watermarks
                      if re.match(r"^\d{4}-\d{2}-\d{2}$", w)]
    effective_cutoff = min(day_watermarks) if day_watermarks else None
    payload = {
        "schema_version": "1",
        "created_at": created_at,
        "timezone": timezone_name,
        "status": "INCOMING",
        "effective_cutoff": effective_cutoff,
        "per_source_watermarks": {
            s["name"]: {tbl: meta.get("max_date")
                        for tbl, meta in (s.get("tables") or {}).items()}
            for s in sources},
        "datasets": [
            {"name": s["name"], "file": s["file"], "sha256": s["sha256"],
             "byte_length": s["bytes"], "rows": s.get("rows", 0),
             "tables": s.get("tables", {}),
             "source": s.get("source_path", "operator-manifest"),
             "integrity": s.get("integrity", "unknown"),
             "completeness": "COMPLETE" if s.get("integrity") == "ok" else "PARTIAL"}
            for s in sources],
    }
    payload["snapshot_id"] = canonical_id({
        "kind": "SNAPSHOT",
        "schema_version": payload["schema_version"],
        "datasets": [
            {"name": s["name"], "sha256": s["sha256"], "tables": sorted((s.get("tables") or {}).keys())}
            for s in sources],
        "effective_cutoff": effective_cutoff,
    })
    return payload


def write_manifest(snapshot_dir: Path, manifest: Dict[str, Any]) -> Path:
    p = snapshot_dir / MANIFEST_NAME
    p.write_text(canonical_json(manifest), encoding="utf-8")
    return p


def load_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    p = snapshot_dir / MANIFEST_NAME
    if not p.is_file():
        raise FileNotFoundError(f"manifest tidak ada: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def verify_snapshot(snapshot_dir: Path) -> Dict[str, Any]:
    """Verifikasi penuh: status READY, hash tiap file, ukuran.
    Gagal hash/ukuran → kembalikan status QUARANTINED beserta alasan."""
    manifest = load_manifest(snapshot_dir)
    problems: List[str] = []
    if manifest.get("status") != "READY":
        problems.append(f"status manifest = {manifest.get('status')} (butuh READY)")
    for ds in manifest.get("datasets", []):
        f = snapshot_dir / ds["file"]
        if not f.is_file():
            problems.append(f"file hilang: {ds['file']}")
            continue
        actual_hash = hash_file(f)
        if actual_hash != ds["sha256"]:
            problems.append(f"hash mismatch: {ds['file']}")
        if f.stat().st_size != ds["byte_length"]:
            problems.append(f"ukuran berubah: {ds['file']}")
    ok = not problems
    return {"ok": ok, "snapshot_id": manifest.get("snapshot_id"),
            "problems": problems, "manifest": manifest}


def mark_ready(snapshot_dir: Path) -> None:
    manifest = load_manifest(snapshot_dir)
    manifest["status"] = "READY"
    write_manifest(snapshot_dir, manifest)


def mark_quarantined(snapshot_dir: Path, reason: str) -> None:
    manifest = load_manifest(snapshot_dir)
    manifest["status"] = "QUARANTINED"
    manifest["quarantine_reason"] = reason
    write_manifest(snapshot_dir, manifest)


def quarantine_snapshot(snapshot_dir: Path, reason: str,
                        quarantine_root: Optional[Path] = None) -> Optional[Path]:
    mark_quarantined(snapshot_dir, reason)
    if quarantine_root is not None:
        quarantine_root.mkdir(parents=True, exist_ok=True)
        dst = quarantine_root / (snapshot_dir.name + ".quarantined")
        if snapshot_dir.exists():
            shutil.move(str(snapshot_dir), str(dst))
        return dst
    return None
