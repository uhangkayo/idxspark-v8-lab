# Runbook Ops — idxspark V8-Lab 0.2

Semua perintah dari host Hermes (168.144.241.60). Produksi TIDAK disentuh.

## Peran

- **Operator** (root): bridge, verifikasi, aktifasi unit.
- **Runner** (`idxspark-lab`): preflight, profile, freshness, e0, report, status.
  Hanya membaca `inputs/`, menulis `state/` + `results/` + log.

## Harian (OTOMATIS sejak 0.2 — systemd)

- Timer: `idxspark-v8-lab.timer`, harian **17:15 WIB** (Persistent=true, catch-up
  saat boot). Trigger `pipeline.service` → `Requires=bridge.service` (root) →
  urutan: bridge snapshot → freshness v3 → E0 replay 126 sesi → report.
- Gerbang fail-closed: sentinel `/var/lib/idxspark-v8-lab/ENABLE` — hapus untuk
  mematikan seluruh chain (`rm` + `systemctl stop idxspark-v8-lab.timer`).
- Log: `journalctl -u idxspark-v8-lab-pipeline.service` (bridge+pipeline).
- Snapshot id hari ini: `/etc/idxspark-v8-lab/snapshot.env` (ditulis bridge).

```bash
# jalankan sekarang tanpa nunggu 17:15 (full chain, ~2 menit):
systemctl start idxspark-v8-lab-pipeline.service

# lihat hasil:
journalctl -u idxspark-v8-lab-pipeline.service -n 80 --no-pager
```

## Manual (debug / run ulang)

```bash
APP=/opt/idxspark-v8-lab/app
VENV=/opt/idxspark-v8-lab/venv/bin/python
CFG=/etc/idxspark-v8-lab/lab.yaml
SNAP=$(grep -o '[0-9a-f]\{64\}' /etc/idxspark-v8-lab/snapshot.env)

# bridge ulang (operator, idempotent; snapshot identik -> skip):
$VENV $APP/src/idxspark_lab/cli.py bridge --config $CFG

# freshness v3 (coverage + klaster stale-tail, gap > 5 sesi):
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py freshness $SNAP --config $CFG

# E0 replay B0<->B1 (fee produksi + sensitivitas AJAIB_EXACT):
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py e0 $SNAP --days 126 --config $CFG

# profile v2 klasik / preflight / report / status:
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py profile $SNAP --config $CFG
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py report --config $CFG
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py status --config $CFG

# tests (repo kerja, 83 test, stdlib saja):
cd /root/idxspark-v8-lab-repo && python -m unittest discover -s tests -q
```

## Trap yang sudah diketahui

- **UMask bridge**: wajib `UMask=0022` di `bridge.service` — 0077 membuat
  manifest snapshot 0600 root dan runner gagal `PermissionError` saat load.
- **Fee arah**: AJAIB_EXACT round-trip = 0.402606% (PPh 0.1% di jual) >
  PROD_FLAT 0.4% → delta_fee_impact NEGATIF (ajaib lebih mahal ~0.26 bp).
- **E0 idempotent**: run yang sama (config+snapshot+days) menghasilkan bytes
  `e0.json` identik; `--force-retry` untuk menghitung ulang.
- **Deploy**: `rsync -a --delete --exclude .git /root/idxspark-v8-lab-repo/
  /opt/idxspark-v8-lab/app/ && chmod -R go-w /opt/idxspark-v8-lab/app && systemctl daemon-reload`.

## Pemulihan bencana

Kode = repo `/root/idxspark-v8-lab-repo` (deploy: lihat Trap di atas). Config =
`configs/lab.example.yaml`. Data lab boleh hilang: snapshot di-bridge ulang,
ledger adalah riset record — backup opsional (`cp` saat DB tidak dibuka writer).
Snapshot lama: retention otomatis 2 terakhir.

## Fail-closed yang aktif

- `enabled: false` → unit systemd/`run_experiment.py` mode scheduled menolak jalan.
- Parameter seleksi null → SHADOW_QUALIFIED tidak mungkin (belum diimplementasi
  sekalipun).
- Hash snapshot mismatch/manifest bukan READY → runner menolak.
- Runner menulis di luar root lab → tidak punya permission (user terisolasi).
- Unit systemd TIDAK punya [Install] dan sentinel `/var/lib/idxspark-v8-lab/ENABLE`
  tidak dibuat.

## Yang BELUM ada (jujur)

- Tidak ada cron/timer otomatis (bridge & profile manual — sesuai fase 0.1).
- Anggaran runtime (CPUQuota dll.) belum diukur — angka unit = ilustrasi.
- Bucket 7-enum seleksi, baseline B0/B1, kernel transaksi, splitter, paper book —
  fase 0.2+ sesuai blueprint.
