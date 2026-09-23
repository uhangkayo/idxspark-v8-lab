# bridge.py — Snapshot bridge operator (Lampiran D §3)
# Komponen SATU-SATUNYA yang membaca data pasar produksi. Berjalan sebagai
# operator (root), BUKAN user runner. Aturan:
#   * source path hanya dari config operator (tidak menyapu /root otomatis)
#   * SQLite dibuka URI mode=ro; snapshot via online backup API
#     (WAL committed ikut; cp file live DILARANG)
#   * staging ekspor terpisah; verifikasi integrity+counts; seal lalu
#     rename atomik ke inputs/<snapshot_id>/ (filesystem sama)
#   * partial export TIDAK dipublikasikan; gagal → quarantine + alasan

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from .contracts.canonical import utc_now
from .data import manifest as mf
from .config import ConfigError


class BridgeError(Exception):
    pass


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    return conn


def _backup_db(src: Path, dst: Path) -> None:
    """SQLite online backup API — konsisten terhadap WAL aktif."""
    src_conn = _connect_ro(src)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA journal_mode=DELETE")  # seal: tanpa WAL
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _table_stats(conn: sqlite3.Connection, table: str,
                 date_column: str | None) -> Dict[str, Any]:
    q = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()
    stats: Dict[str, Any] = {"rows": int(q[0])}
    if date_column and stats["rows"] > 0:
        try:
            mn, mx = conn.execute(
                f"SELECT MIN(\"{date_column}\"), MAX(\"{date_column}\") FROM \"{table}\""
            ).fetchone()
            stats["min_date"] = mn if isinstance(mn, str) else (str(mn) if mn is not None else None)
            stats["max_date"] = mx if isinstance(mx, str) else (str(mx) if mx is not None else None)
        except sqlite3.Error:
            stats["min_date"] = None
            stats["max_date"] = None
    stats["date_column"] = date_column
    return stats


def _verify_backup(dst: Path, tables: Dict[str, str]) -> Dict[str, Any]:
    """integrity_check + rowcount + watermark per tabel allowlist."""
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integ != "ok":
            raise BridgeError(f"integrity_check gagal pada {dst.name}: {integ}")
        out: Dict[str, Any] = {}
        total = 0
        for tbl, date_col in tables.items():
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()
            if not present:
                raise BridgeError(f"tabel allowlist hilang: {tbl} di {dst.name}")
            st = _table_stats(conn, tbl, date_col)
            out[tbl] = st
            total += st["rows"]
        return {"integrity": "ok", "tables": out, "rows": total}
    finally:
        conn.close()


def run_bridge(config: Dict[str, Any], run_as: str = "operator") -> Dict[str, Any]:
    """Bangun satu snapshot dari allowlist config. Output: dict ringkas."""
    bridge_cfg = config.get("bridge")
    if not bridge_cfg or not bridge_cfg.get("sources"):
        raise ConfigError("bridge.sources kosong — source path wajib manifest operator")
    storage = config["storage"]
    base = Path(storage["input_root"])
    inbox = base / "inbox"
    staging_root = base / "bridge_staging"
    quarantine_root = base / "quarantine"
    for d in (base, inbox, staging_root, quarantine_root):
        d.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    staging = staging_root / f"bridge-{stamp}"
    staging.mkdir(parents=True)
    started = utc_now()

    sources: List[Dict[str, Any]] = []
    for spec in bridge_cfg["sources"]:
        name = spec["name"]
        src = Path(str(spec["path"]))
        if not src.is_file():
            raise BridgeError(f"source tidak ditemukan (manifest operator salah?): {src}")
        tables: Dict[str, str] = {}
        for t in spec.get("tables", []):
            if isinstance(t, str):
                tables[t] = spec.get("date_columns", {}).get(t)
            else:
                tables[t["name"]] = t.get("date_column")
        dst = staging / f"{name}.db"
        # budget hard-stop durasi per source (detik, default 900)
        budget = int(bridge_cfg.get("per_source_budget_seconds", 900))
        t0 = time.monotonic()
        _backup_db(src, dst)
        if time.monotonic() - t0 > budget:
            raise BridgeError(f"budget backup {name} terlampaui — bridge berhenti, produksi aman")
        stats = _verify_backup(dst, tables)
        fhash = mf.hash_file(dst)
        sources.append({
            "name": name, "file": dst.name, "sha256": fhash,
            "bytes": dst.stat().st_size, "rows": stats["rows"],
            "tables": stats["tables"], "source_path": str(src),
            "integrity": stats["integrity"],
        })

    manifest = mf.build_manifest(sources, created_at=started)
    manifest["status"] = "VALIDATING"
    mf.write_manifest(staging, manifest)

    # verifikasi ulang semua hash sebelum READY (partial tidak dipublikasikan)
    checks = {s["file"]: mf.hash_file(staging / s["file"]) for s in sources}
    for s in sources:
        if checks[s["file"]] != s["sha256"]:
            raise BridgeError(f"hash berubah saat seal: {s['file']}")
    manifest["status"] = "READY"
    mf.write_manifest(staging, manifest)

    snapshot_id = manifest["snapshot_id"]
    final_dir = base / "inputs" / snapshot_id
    if final_dir.exists():
        # snapshot identik sudah ada → idempotent: buang staging
        shutil.rmtree(staging)
        return {"snapshot_id": snapshot_id, "status": "READY",
                "idempotent_hit": True, "datasets": len(sources)}
    (base / "inputs").mkdir(parents=True, exist_ok=True)
    final_dir_tmp = base / "inputs" / (snapshot_id + ".staging")
    if final_dir_tmp.exists():
        shutil.rmtree(final_dir_tmp)
    shutil.move(str(staging), str(final_dir_tmp))
    final_dir_tmp.rename(final_dir)  # rename atomik dalam filesystem sama

    # retention: keep N snapshot READY terakhir
    keep = int(bridge_cfg.get("keep_snapshots", 2))
    inputs = base / "inputs"
    snaps = sorted([d for d in inputs.iterdir() if d.is_dir() and d != final_dir],
                   key=lambda d: d.stat().st_mtime, reverse=True)
    for old in snaps[keep - 1:]:
        shutil.rmtree(old)

    return {"snapshot_id": snapshot_id, "status": "READY", "idempotent_hit": False,
            "datasets": len(sources), "effective_cutoff": manifest["effective_cutoff"],
            "bytes_total": sum(s["bytes"] for s in sources),
            "per_source_watermarks": manifest["per_source_watermarks"]}
