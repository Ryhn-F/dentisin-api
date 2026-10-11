# Changelog dokumentasi API backend

Format: tanggal (YYYY-MM-DD), perubahan pada kontrak yang didokumentasikan. Perubahan kontrak nyata (migrasi, aturan, model) tetap dicatat di `../regulatory/CHANGELOG.md` dan ADR; berkas ini hanya melacak dokumen di `docs/api/`.

## 2026-10-11 (kuota)

- Migrasi `20261011000100_quota_monitoring.sql`: RPC `admin_quota_usage()` (admin, diaudit) dan `service_quota_usage()` (service); 6 baris `app_setting` `quota_*`.
- Edge Function `quota-check` (`CRON_SECRET`) + job `quota-check` di `scheduled-jobs.yml`. Didokumentasikan di `SUPABASE_API.md` dan `EDGE_FUNCTIONS_AND_HOOKS.md` §1.3b/§2.

## 2026-10-11

- Dokumen awal: `README.md` (indeks, arsitektur, peran, galat umum), `SUPABASE_API.md`, `SYNC_EVENTS.md`, `EDGE_FUNCTIONS_AND_HOOKS.md`, `CLIENT_CONTRACTS.md`.
- Basis kode yang didokumentasikan: migrasi `20261008000100` s.d. `20261010000200`; `supabase/config.toml`; Edge Functions `process-deletion` dan `retention-sweep`; mailer `smtp-handler` (hook `POST /hooks/send-email`, `GET /healthz`); `ml/detection/DECODE_SPEC.md` v1.0.0 dan `labels.schema.json` 2.0.0; `tools/manifest/SPEC.md` v1; triase aturan `0.2.0-draft`; bundel rekomendasi `rules_version 0.1.0-draft`.
- Kontrak yang berlaku saat ini dan perlu diketahui engineer app:
  - `sync_events`: 6 tipe event; batch 1..100 (<= 256 KiB), payload <= 8 KiB; penolakan final kecuali `internal_error`.
  - `screening_recorded.findings` tidak lagi menerima `rel_area` (migrasi `20261010000200`, ADR 014); kelas v1 `caries_permanent` / `caries_primary`.
  - Kata sandi minimal 8 karakter (huruf kecil, besar, angka); konfirmasi email wajib.
  - `labels.json` skema 2.0.0 dengan `box_use = ui_overlay_only`.
- Belum terverifikasi dari kode (ditandai `[PERLU VERIFIKASI]` di dokumen): status CAPTCHA dan hook email di proyek hosted, jenis alur auth (PKCE atau implicit) dengan hook aktif, pemetaan status HTTP PostgREST/GoTrue per versi, ada tidaknya `../../api/docs/API.md` (FastAPI, ditulis engineer lain).
