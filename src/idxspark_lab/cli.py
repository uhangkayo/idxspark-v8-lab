#!/usr/bin/env python3
# cli.py — v8lab CLI (operator: bridge/verify; runner: preflight/profile/report)

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from idxspark_lab.config import load_config  # noqa: E402
from idxspark_lab.contracts.canonical import iso_from_utc  # noqa: E402


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="/etc/idxspark-v8-lab/lab.yaml")
    p = argparse.ArgumentParser(prog="v8lab", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bridge", parents=[common],
                   help="operator: snapshot sumber produksi → inputs/")
    pv = sub.add_parser("verify", parents=[common], help="verifikasi hash snapshot")
    pv.add_argument("snapshot_id")
    pf = sub.add_parser("preflight", parents=[common], help="S0 fail-closed check")
    pf.add_argument("--snapshot", default=None)
    pf.add_argument("--mode", default="diagnostic", choices=["diagnostic", "scheduled"])
    pr = sub.add_parser("profile", parents=[common],
                        help="run profil coverage diagnostik (idempotent)")
    pr.add_argument("snapshot_id")
    pr.add_argument("--force-retry", action="store_true")
    fr = sub.add_parser("freshness", parents=[common],
                        help="profil v3: coverage + tail-gap klaster stale (idempotent)")
    fr.add_argument("snapshot_id")
    fr.add_argument("--force-retry", action="store_true")
    e0p = sub.add_parser("e0", parents=[common],
                         help="E0 replay B0↔B1 (simulasi, DIAGNOSTIC_ONLY)")
    e0p.add_argument("snapshot_id")
    e0p.add_argument("--days", type=int, default=126,
                     help="jumlah sesi keputusan terakhir (default 126)")
    e0p.add_argument("--fee-profile", default="PROD_FLAT",
                     choices=["PROD_FLAT", "AJAIB_EXACT"])
    e0p.add_argument("--force-retry", action="store_true")
    rp = sub.add_parser("report", parents=[common], help="cetak laporan run terakhir")
    rp.add_argument("run_id", nargs="?")
    sub.add_parser("status", parents=[common], help="ringkasan ledger")

    args = p.parse_args(argv)

    if args.cmd == "bridge":
        from idxspark_lab.bridge import run_bridge
        cfg = load_config(args.config)
        out = run_bridge(cfg)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "verify":
        from idxspark_lab.config import ConfigError
        from idxspark_lab.data import manifest as mf
        cfg = load_config(args.config)
        snap = Path(cfg["storage"]["input_root"]) / "inputs" / args.snapshot_id
        res = mf.verify_snapshot(snap)
        print(json.dumps({"ok": res["ok"], "snapshot_id": res["snapshot_id"],
                          "problems": res["problems"]}, indent=2))
        return 0 if res["ok"] else 1

    if args.cmd == "preflight":
        from idxspark_lab.preflight import preflight, PreflightError
        try:
            res = preflight(args.config, snapshot_id=args.snapshot, mode=args.mode)
            print(json.dumps({"ok": True, "mode": args.mode,
                              "snapshot": args.snapshot}, indent=2))
            return 0
        except PreflightError as e:
            print(str(e), file=sys.stderr)
            return 2

    if args.cmd == "profile":
        from idxspark_lab.orchestrator import run_profile
        out = run_profile(args.config, args.snapshot_id, force_retry=args.force_retry)
        if out["status"] == "IDEMPOTENT_HIT":
            pub = out["publication"]
            print(f"[IDEMPOTENT] publication sudah ada untuk run {out['run_id'][:16]}…")
            print(json.dumps({"funnel": pub["disjoint_counts"],
                              "result": pub["result"]}, indent=2))
            return 0
        print(json.dumps({k: v for k, v in out.items() if k != "publication"},
                         indent=2, default=str))
        return 0

    if args.cmd == "freshness":
        from idxspark_lab.orchestrator import run_profile
        out = run_profile(args.config, args.snapshot_id,
                          force_retry=args.force_retry, spec_version="v3")
        if out["status"] == "IDEMPOTENT_HIT":
            pub = out["publication"]
            print(f"[IDEMPOTENT] publication sudah ada untuk run {out['run_id'][:16]}…")
            print(json.dumps({"funnel_v3": pub["disjoint_counts"],
                              "result": pub["result"]}, indent=2))
            return 0
        print(json.dumps({k: v for k, v in out.items() if k != "publication"},
                         indent=2, default=str))
        return 0

    if args.cmd == "e0":
        from idxspark_lab.orchestrator import run_e0
        out = run_e0(args.config, args.snapshot_id, days=args.days,
                     fee_profile=args.fee_profile, force_retry=args.force_retry)
        if out["status"] == "IDEMPOTENT_HIT":
            pub = out["publication"]
            print(f"[IDEMPOTENT] publication sudah ada untuk run {out['run_id'][:16]}…")
            print(json.dumps({"disjoint_counts": pub["disjoint_counts"],
                              "result": pub["result"]}, indent=2))
            return 0
        print(json.dumps({k: v for k, v in out.items() if k != "publication"},
                         indent=2, default=str))
        return 0

    if args.cmd == "report":
        from idxspark_lab.persistence.ledger import Ledger
        cfg = load_config(args.config)
        led = Ledger(cfg["storage"]["ledger"])
        try:
            pubs = led.recent_publications(50)
            if not pubs:
                print("belum ada publication")
                return 1
            run_id = args.run_id or pubs[0]["run_id"]
            p = led.get_publication_by_run(run_id)
            base = Path(cfg["storage"]["output_root"]) / "results" / run_id
            rep = base / "report.md"
            if not rep.is_file():
                rep = base / "e0_report.md"
            if p is None or not rep.is_file():
                print(f"report/publication tidak ada untuk run {run_id}", file=sys.stderr)
                return 1
            print(rep.read_text(encoding="utf-8"))
            print(f"\n(published {iso_from_utc(p['published_at'])}, "
                  f"freshness s/d {iso_from_utc(p['freshness_deadline'])})")
            return 0
        finally:
            led.close()

    if args.cmd == "status":
        from idxspark_lab.persistence.ledger import Ledger
        cfg = load_config(args.config)
        led = Ledger(cfg["storage"]["ledger"])
        try:
            pubs = led.recent_publications(10)
            n_runs = led.conn.execute("SELECT COUNT(*) FROM Run").fetchone()[0]
            n_events = led.conn.execute("SELECT MAX(seq) FROM LedgerAppend").fetchone()[0]
            print(json.dumps({"ledger_appends": n_events, "runs": n_runs,
                              "publications": pubs}, indent=2, default=str))
            return 0
        finally:
            led.close()


if __name__ == "__main__":
    sys.exit(main())
