#!/usr/bin/env bash
# ops-pipeline.sh — jalan sebagai idxspark-lab (read-only inputs).
# Urutan: freshness v3 (coverage + klaster stale-tail) -> E0 replay B0/B1
# (126 sesi, kernel transaksi + fee sensitivity) -> report.
# Fail-closed: V8LAB_SNAPSHOT wajib dari snapshot.env (bridge root).
# Exit != 0 -> unit systemd FAILED (operator lihat journalctl).
set -euo pipefail

VENV_PY=/opt/idxspark-v8-lab/venv/bin/python
APP=/opt/idxspark-v8-lab/app
CFG=/etc/idxspark-v8-lab/lab.yaml
CLI="$APP/src/idxspark_lab/cli.py"

SNAP="${V8LAB_SNAPSHOT:?snapshot.env kosong - jalankan bridge dulu}"
echo "[ops-pipeline] snapshot=$SNAP"

echo "[ops-pipeline] freshness v3 ..."
"$VENV_PY" -B "$CLI" freshness "$SNAP" --config "$CFG"

echo "[ops-pipeline] E0 replay 126 sesi ..."
"$VENV_PY" -B "$CLI" e0 "$SNAP" --days 126 --config "$CFG"

echo "[ops-pipeline] report ..."
"$VENV_PY" -B "$CLI" report --config "$CFG" | head -40

echo "[ops-pipeline] selesai OK"
