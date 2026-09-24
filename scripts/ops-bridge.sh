#!/usr/bin/env bash
# ops-bridge.sh — jalan sebagai ROOT (akses sumber produksi + tulis /etc).
# Bridge 5 DB -> snapshot inputs/<hash>, retention dari lab.yaml, lalu tulis
# snapshot id ke /etc/idxspark-v8-lab/snapshot.env untuk pipeline runner.
# Idempotent: snapshot hash sama tidak men-duplikat inputs.
set -euo pipefail

VENV_PY=/opt/idxspark-v8-lab/venv/bin/python
APP=/opt/idxspark-v8-lab/app
CFG=/etc/idxspark-v8-lab/lab.yaml
ENVF=/etc/idxspark-v8-lab/snapshot.env

OUT="$("$VENV_PY" -B "$APP/src/idxspark_lab/cli.py" bridge --config "$CFG")"
printf '%s\n' "$OUT"
SNAP="$(printf '%s' "$OUT" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["snapshot_id"])')"

umask 022
printf 'V8LAB_SNAPSHOT=%s\n' "$SNAP" > "$ENVF"
echo "[ops-bridge] snapshot.env -> $SNAP"
