# adapters/snapshot_reader.py — Pembaca snapshot read-only (ENG-01)
# Runner hanya membaca inputs/<snapshot_id>/ milik root lab. Hash diverifikasi
# saat open. Tolak traversal/symlink escape/file nonregular.

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from ..data import manifest as mf


class SnapshotAccessError(Exception):
    pass


class SnapshotReader:
    def __init__(self, inputs_root: str, snapshot_id: str, verify: bool = True):
        self.root = Path(inputs_root).resolve()
        self.snapshot_id = snapshot_id
        self.dir = (self.root / snapshot_id).resolve()
        # guard traversal/symlink escape
        if not str(self.dir).startswith(str(self.root) + "/"):
            raise SnapshotAccessError("path traversal/symlink escape ditolak")
        if not self.dir.is_dir():
            raise SnapshotAccessError(f"snapshot tidak ada: {snapshot_id}")
        result = mf.verify_snapshot(self.dir)
        if not result["ok"]:
            raise SnapshotAccessError(
                "snapshot tidak lolos verifikasi: " + "; ".join(result["problems"]))
        self.manifest = result["manifest"]
        self.conns: Dict[str, sqlite3.Connection] = {}

    def dataset_path(self, name: str) -> Path:
        for ds in self.manifest["datasets"]:
            if ds["name"] == name:
                p = (self.dir / ds["file"]).resolve()
                if not str(p).startswith(str(self.dir) + "/"):
                    raise SnapshotAccessError("dataset path escape ditolak")
                if not p.is_file():
                    raise SnapshotAccessError(f"file dataset hilang: {ds['file']}")
                return p
        raise SnapshotAccessError(f"dataset tidak ada di manifest: {name}")

    def connect(self, name: str) -> sqlite3.Connection:
        if name not in self.conns:
            p = self.dataset_path(name)
            if not p.is_file() or p.is_symlink():
                raise SnapshotAccessError("file nonregular/symlink ditolak")
            self.conns[name] = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=60)
        return self.conns[name]

    def rows(self, dataset: str, sql: str, params: tuple = ()) -> List[tuple]:
        cur = self.connect(dataset).execute(sql, params)
        return cur.fetchall()

    def table_manifest(self, dataset: str) -> Dict[str, Any]:
        for ds in self.manifest["datasets"]:
            if ds["name"] == dataset:
                return ds
        raise SnapshotAccessError(f"dataset tidak ada: {dataset}")

    def close(self) -> None:
        for c in self.conns.values():
            c.close()
        self.conns.clear()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
