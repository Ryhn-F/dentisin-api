# Changelog dokumentasi API backend

Format: tanggal (YYYY-MM-DD), perubahan pada kontrak yang didokumentasikan. Perubahan kontrak nyata (migrasi, aturan, model) tetap dicatat di `../regulatory/CHANGELOG.md` dan ADR; berkas ini hanya melacak dokumen di `docs/api/`.

## 2026-10-18 (usia remaja 15–19, ADR 018)

- Akun `teen` hanya untuk usia **15–19 th** (`limits.teen_min_age` 10 -> **15**, migrasi `20261018000300_teen_age_15.sql`). Di bawah 15 th wajib guardian + akun anak; `ensure_self_profile` menolak dengan `age_out_of_range`. Perubahan nilai pengaturan, bukan bentuk kontrak.
- Teks persetujuan remaja memakai kalimat "Saya sendiri bersedia" (draf `consent-*-teen-*` di `CONSENT_TEXT_DRAFT.md`; di sisi app teks (a)-(d) remaja dipilih berdasarkan `account_type`).

## 2026-10-18 (kader dihapus, ADR 018)

- **Peran `kader`, mode kelas, `paper_via_kader`, dan rekap fasilitas dihapus** (ADR 018 menggantikan amandemen kader di bawah). App hanya untuk orang tua (`guardian`), remaja (`teen`), dan akun anak buatan orang tua. Peran yang dikembalikan `my_account().roles`: `guardian`, `child`, `admin`; `kader` dan `facility_viewer` tidak lagi bisa diberikan (`admin_grant_role` menjawab `role_not_available`, 22023).
- Dihapus dari API: tabel `class_roster`, `class_roster_member`; RPC `facility_screening_summary`; metode consent `paper_via_kader` (selalu `forbidden`).
- **Remaja menonton sendiri videonya:** akun `teen` mendaftarkan `brushing_proof_registered` untuk profil dirinya (`is_self`), mengunggah ke `<teen_id>/<self_child_id>/<proof_id>.mp4`, dan melihatnya lewat `list_brushing_proofs`, `child_history`, signed URL, `mark_proof_viewed`, `delete_brushing_proof`. `capabilities.register_proofs` benar untuk akun anak (`proof_allowed`) dan remaja (flag hidup). Guardian tidak mendaftarkan video.
- `child-accounts` kembali hanya memeriksa `account_type = 'guardian'`.
- Dokumen: `ACCOUNTS_AND_FLOWS.md` §2.8 (Remaja), `SYNC_EVENTS.md` §3.1/§3.8, `SUPABASE_API.md`, `README.md`, `APP_TEAM_GUIDE.md`, `EDGE_FUNCTIONS_AND_HOOKS.md`.

## 2026-10-18 (kader, amandemen ADR 017; digantikan ADR 018)

- Kader **tidak** membuat akun anak: `child-accounts` menjawab 403 untuk peran `kader`; database menolak pemilik berperan `kader` (`child_account_invalid`).
- Video bukti tersedia juga bagi kader sebagai riwayat atas murid miliknya: `brushing_proof_registered` (+ unggah `brushing_proofs`) dari kader pemilik anak; `list_brushing_proofs`, `mark_proof_viewed`, `delete_brushing_proof`, `child_history` bekerja untuk kader pemilik. `facility_viewer`/admin tetap tanpa akses.
- `my_account()` memuat `capabilities` (`create_child_accounts`, `register_proofs`). Perubahan aditif.
- Dokumen: `ACCOUNTS_AND_FLOWS.md` §2.8, `SYNC_EVENTS.md` §3.8, `SUPABASE_API.md`, `EDGE_FUNCTIONS_AND_HOOKS.md` §1.5.

## 2026-10-18 (akun v2, ADR 017)

- Baru: `ACCOUNTS_AND_FLOWS.md` (jenis akun, daftar remaja/orang tua, orang tua membuat akun anak, login anak, streak + video bukti, permintaan skrining, riwayat, hak akses, galat, daftar periksa UI).
- `SYNC_EVENTS.md`: tipe `screening_requested` dan `brushing_proof_registered` (hanya akun anak), `request_id` opsional pada `screening_recorded`, consent jenis `d`, kode penolakan `limit_exceeded` (8 tipe event).
- `SUPABASE_API.md`: `signUp` menerima `options.data.account_type` (`guardian`|`teen`); tabel baru (`child_account`, `screening_request`, `brushing_proof`); RPC §3.7; bucket `brushing_proofs`.
- `EDGE_FUNCTIONS_AND_HOOKS.md`: `child-accounts`, `child-login`; `retention-sweep` dan `process-deletion` ikut menangani video bukti.
- Kontrak berubah **aditif** (tidak ada tipe/kolom lama yang berubah makna). Catatan: trigger pendaftaran kini menerima `teen`; akun lama tetap `guardian`.

## 2026-10-17 (panduan tim app, model terpilih)

- Baru: `APP_TEAM_GUIDE.md` (mulai di sini: peta, status, urutan integrasi, jebakan, uji cepat) dan `MODEL_BUNDLE.md` (bundel detektor ADR 013: YOLOX-Nano float32, I/O, alur, ambang, batasan UI, bukti paritas).
- Baru: `examples/detector/` — dekoder TypeScript acuan (`decode.ts`) + uji `node --test` terhadap `decode_vectors.json` dan fixture `golden/golden_runtime.json` (keluaran mentah model nyata); pembuat fixture `ml/detection/make_golden.py`.
- `CLIENT_CONTRACTS.md` §4: model terpilih float32 (contoh `variant`/`int8`/`size_bytes` diperbarui), butir terbuka diperbarui, tautan ke `MODEL_BUNDLE.md`.
- Kontrak model berubah (ADR 013): masukan **float32** (bukan uint8), keluaran logit `[1,3549,7]`, ambang `set_on_val`. Tidak ada perubahan skema `labels.json` (2.0.0).

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
