#!/usr/bin/env python3
# run_experiment.py — Entrypoint systemd worker (Lampiran D §5)
# -I -B dipakai unit; karena isolated mode tidak menambahkan sys.path,
# path src di-insert eksplisit. Scheduled run menolak enabled=false.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from idxspark_lab.orchestrator import run_profile  # noqa: E402
from idxspark_lab.preflight import PreflightError, preflight  # noqa: E402


def main() -> int:
    import argparse
    import os
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="/etc/idxspark-v8-lab/lab.yaml")
    p.add_argument("--snapshot", default=os.environ.get("V8LAB_SNAPSHOT"))
    args = p.parse_args()
    if not args.snapshot:
        print("run_experiment: --snapshot atau V8LAB_SNAPSHOT wajib (fail-closed)",
              file=sys.stderr)
        return 78
    try:
        preflight(args.config, snapshot_id=args.snapshot, mode="scheduled")
    except PreflightError as e:
        print(f"run_experiment: {e}", file=sys.stderr)
        return 78  # EX_CONFIG: fail-closed, bukan crash
    out = run_profile(args.config, args.snapshot)
    print(f"run {out['run_id'][:16]}… → {out['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
