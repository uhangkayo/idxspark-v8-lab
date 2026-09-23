# idxspark V8-Lab

Lab riset terisolasi untuk IdxSpark (blueprint V8-Lab v1). **Bukan produksi.**
Tidak ada order, tidak ada kanal, tidak menyentuh `/signals/today`.

## Apa ini

- **Snapshot bridge** (operator): salinan tersegel data pasar produksi →
  `inputs/<hash>/` + manifest (`INCOMING → VALIDATING → READY`), SQLite backup API.
- **Ledger append-only** (Lampiran C): 19 tabel, trigger anti-UPDATE/DELETE,
  single-writer, id = SHA256(canonical payload), idempotent retry.
- **Profiler 0.1** (DIAGNOSTIC_ONLY): universe ∪ survivorship, kalender bursa
  estimasi, pemeriksaan kualitas per emiten, funnel disjoint
  `n_universe = PROFILE_OK + DATA_UNUSABLE`, laporan Markdown.

## Struktur

```
src/idxspark_lab/
  contracts/    canonical.py enums.py types.py   # kontrak data Lampiran C
  persistence/  ledger.py                        # single-writer append-only
  adapters/     snapshot_reader.py               # read-only + hash verify
  data/         manifest.py calendar.py universe.py
  reporting/    profile.py report_md.py
  bridge.py     preflight.py orchestrator.py cli.py
run_experiment.py        # entrypoint systemd (fail-closed)
configs/lab.example.yaml # template §13 beku
configs/idxspark-v8-lab-run.service  # skeleton TIDAK diaktifkan
tests/                    # 13 test: idempotensi, crash, tamper, append-only
docs/                     # ADR-001 + runbook ops
```

## Deploy & run

Lihat `docs/runbook-ops.md`. Singkat:

```bash
/opt/idxspark-v8-lab/venv/bin/python /opt/idxspark-v8-lab/app/src/idxspark_lab/cli.py bridge
SNAP=$(ls -t /var/lib/idxspark-v8-lab/inputs/ | head -1)
sudo -u idxspark-lab /opt/idxspark-v8-lab/venv/bin/python \
  /opt/idxspark-v8-lab/app/src/idxspark_lab/cli.py profile $SNAP
```

Stdlib-only (Python 3.11). Nol dependensi pip.

## Status

0.1 selesai: LAB-01/02/03 + G0. 0.2 (baseline B0/B1, kernel transaksi, bucket
7-enum) belum dibangun — lihat ADR-001.
