# ADR-001 — V8-Lab 0.1: paket terisolasi + ledger + snapshot + profiler

Status: DITERIMA (2026-09-24)
Konteks: Blueprint-IdxSpark-V8-Lab.md (V8-Lab v1) §3, §8.1-8.3, §13, Lampiran C/D.

## Keputusan

1. **Paket terpisah** `idxspark_lab` di `/opt/idxspark-v8-lab/app`, venv Python 3.11
   sendiri, **stdlib-only** (nol dependensi eksternal → dependency-lock trivially
   terpenuhi). Produksi tidak mengimport lab; lab tidak mengimport produksi.
2. **Same-host = alternatif terbatas** (blueprint §2.1/Lampiran D §4): isolasi
   lewat user sistem `idxspark-lab` + permission path + systemd hardening,
   bukan worker terpisah. Anggaran sumber daya memakai CPUQuota/MemoryMax
   bila unit diaktifkan (masih ilustratif — perlu pengukuran G1).
3. **Brige operator** membaca produksi via SQLite **backup API** (bukan `cp`,
   bukan `immutable=1`), staging → verify (integrity_check + row count + hash)
   → rename atomik ke `inputs/<snapshot_id>/` + manifest READY. Retention 2.
4. **Ledger** = SQLite `/var/lib/idxspark-v8-lab/state/v8lab_runs.sqlite`:
   append-only ditegakkan trigger DB, single-writer ditegakkan BEGIN IMMEDIATE
   + lockfile, idempotensi via id = SHA256(canonical payload) + digest compare.
   Schema = Lampiran C (seluruh 19 tabel dibuat; 0.1 mengisi subset).
5. **Profiler 0.1** = DIAGNOSTIC_ONLY: universe gabungan
   prices ∪ prices_delisted ∪ survivorship; funnel 2-bucket
   (PROFILE_OK / DATA_UNUSABLE) dengan invariant jumlah = universe;
   bucket 7-enum seleksi hanya dipakai mulai 0.2.
6. **Config template beku** §13 di `/etc/idxspark-v8-lab/lab.yaml` + parser
   YAML subset internal (dukungan: nested map, list `- `, scalar). Parameter
   null tetap memblokir SHADOW_QUALIFIED — belum diubah.

## Konsekuensi

- Produksi tidak berubah: nol modifikasi file/cron/unit idxspark produksi.
- Retry run identik → publication tunggal; attempt baru; hash artifact sama
  (dibuktikan test).
- Kelemahan diterima: kalender bursa = estimator dari data (≥60% emiten aktif);
  kualitas SBL/broker flow belum diaudit ulang di lab; `prices_raw` tanpa
  indeks ticker (profil full-scan per emiten — dapat diperbaiki nanti dengan
  indeks di snapshot, bukan di produksi).
- Kompromi: `bridge` adalah proses operator (root) satu-satunya yang membaca
  produksi; runner tidak pernah menyentuh path produksi.
