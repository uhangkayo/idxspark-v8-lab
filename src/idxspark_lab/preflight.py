# preflight.py — S0 fail-closed sebelum run (LAB-01)
# Scheduled run menolak budget null; diagnostic CLI diizinkan dengan enabled=false.

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import check_scheduled_allowed, load_config
from .data import manifest as mf

PYTHON_MAJOR_MINOR = (3, 11)


class PreflightError(Exception):
    pass


def preflight(config_path: str, snapshot_id: Optional[str] = None,
              mode: str = "diagnostic") -> Dict[str, Any]:
    cfg = load_config(config_path)
    storage = cfg["storage"]
    input_root = Path(storage["input_root"])
    output_root = Path(storage["output_root"])
    state_db = Path(storage["ledger"])

    problems: List[str] = []

    # versi python pin
    v = sys.version_info
    if (v.major, v.minor) != PYTHON_MAJOR_MINOR:
        problems.append(f"python {v.major}.{v.minor} ≠ pin {PYTHON_MAJOR_MINOR[0]}.{PYTHON_MAJOR_MINOR[1]}")

    # direktori output harus bisa ditulis, input TIDAK boleh bisa ditulis runner
    if not output_root.exists():
        problems.append(f"output_root tidak ada: {output_root}")
    state_db.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(state_db.parent, os.W_OK):
        problems.append(f"state dir tidak writable: {state_db.parent}")
    inputs = input_root / "inputs"
    if not inputs.is_dir():
        problems.append(f"inputs/ tidak ada: {inputs}")
    else:
        if os.geteuid() != 0 and os.access(inputs, os.W_OK):
            problems.append("runner dapat MENULIS inputs/ — read-only dilanggar")
        if snapshot_id:
            snap = inputs / snapshot_id
            if not snap.is_dir():
                problems.append(f"snapshot tidak ada: {snapshot_id}")
            else:
                res = mf.verify_snapshot(snap)
                if not res["ok"]:
                    problems.extend(res["problems"])
                elif mode != "diagnostic":
                    pass

    # scheduled gate
    if mode == "scheduled":
        problems.extend(check_scheduled_allowed(cfg))

    if problems:
        raise PreflightError("PREFLIGHT GAGAL (fail-closed):\n  - " + "\n  - ".join(problems))
    return {"ok": True, "config": cfg, "snapshot_id": snapshot_id,
            "input_root": str(input_root), "output_root": str(output_root),
            "state_db": str(state_db)}
