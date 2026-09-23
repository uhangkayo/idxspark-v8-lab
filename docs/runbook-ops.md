# Runbook Ops — idxspark V8-Lab 0.1

Semua perintah dari host Hermes (168.144.241.60). Produksi TIDAK disentuh.

## Peran

- **Operator** (root): bridge, verifikasi, aktifasi unit (jika nanti dinyalakan).
- **Runner** (`idxspark-lab`): preflight, profile, report, status. Hanya membaca
  `inputs/`, menulis `state/` + `results/` + log.

## Harian (manual, belum otomatis — sesuai 0.1)

```bash
APP=/opt/idxspark-v8-lab/app
VENV=/opt/idxspark-v8-lab/venv/bin/python
CFG=/etc/idxspark-v8-lab/lab.yaml

# 1. bridge (operator, ~10 detik, idempotent; snapshot identik → skip)
$VENV $APP/src/idxspark_lab/cli.py bridge --config $CFG

# 2. snapshot terbaru:
SNAP=$(ls -t /var/lib/idxspark-v8-lab/inputs/ | head -1)

# 3. preflight + profile (runner):
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py profile $SNAP --config $CFG

# 4. baca laporan:
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py report --config $CFG

# 5. status ledger:
sudo -u idxspark-lab $VENV $APP/src/idxspark_lab/cli.py status --config $CFG
```

## Pemulihan bencana

Kode = repo `/root/idxspark-v8-lab-repo` (deploy: `rsync -a --delete --exclude .git
/root/idxspark-v8-lab-repo/ /opt/idxspark-v8-lab/app/ && chmod -R a-w
/opt/idxspark-v8-lab/app`). Config = `configs/lab.example.yaml`. Data lab boleh
hilang: snapshot di-bridge ulang, ledger adalah riset record — backup opsional
(`cp` saat DB tidak dibuka writer). Snapshot lama: retention otomatis 2 terakhir.

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
