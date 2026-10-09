# DentisAI · Supabase (auth + CRUD + storage)

Skema Postgres, RLS, RPC, Storage, Edge Functions, dan uji untuk proyek Supabase Free (Singapore, `ap-southeast-1`). Rujukan: `../../docs/PRD.md` §6.1/§6.9/§9.3–9.6/§10, ADR 005 & 008, `../CLAUDE.md` §6.

## Menjalankan / How to run

```bash
cd backend                         # project root of the Supabase CLI
npx supabase@2.120.0 start         # needs Docker Desktop running
npx supabase db reset              # applies migrations/ + seed/seed.sql
npx supabase test db               # pgTAP suite in supabase/tests/*.test.sql
```

Env for `config.toml` (read from the process / `supabase/.env`; never commit values): `AUTH_SITE_URL`, `AUTH_REDIRECT_URL`, `RESEND_API_KEY`, `CAPTCHA_PROVIDER` (`hcaptcha`|`turnstile`), `CAPTCHA_SECRET`. With CAPTCHA enabled the CLI refuses to load the config when `CAPTCHA_PROVIDER` is empty. For offline local development without a Resend key set `[auth.email.smtp] enabled = false` temporarily (mail then goes to the local SMTP catcher on :54324) — do not commit that change.

**Fallback without Docker** (what was used to verify this folder): a throwaway PostgreSQL 17 + minimal Supabase stubs (roles `anon/authenticated/service_role`, `auth.users`/`auth.uid()`, `storage.buckets/objects` + `protect_delete`) + a small pgTAP-compatible shim:

```bash
python supabase/scripts/local-harness/run_local_tests.py \
  --pg-bin "C:/Program Files/PostgreSQL/17/bin" --data-dir <scratch>/pgdata --port 54329 --stop
```
It applies all migrations + seed, re-applies all migrations (re-runnability check), then runs the same `tests/*.test.sql` files. The stub is an approximation — the canonical gate is `supabase test db`.

## Isi folder

| Path | Isi |
|---|---|
| `migrations/0100_foundation` | precondition (migration role must have BYPASSRLS), `pgcrypto`, schema `private` (not exposed), revoke default grants, enums |
| `migrations/0200_core_tables` | `guardian`, `user_role`, `child_profile`, `consent`, `screening`, `image_upload`, `referral`, `brushing_event`, `class_roster(+_member)`, `deletion_request`, `audit_log`, `sync_event`, `feature_flag`, `app_setting`, `private.secret` + CHECK validators |
| `migrations/0300_helpers_and_triggers` | SECURITY DEFINER helpers (`search_path=''`), signup trigger (always `guardian`), append-only `audit_log`, consent guard |
| `migrations/0400_rls_policies` | RLS ENABLE+FORCE on all tables, column-scoped grants, policies |
| `migrations/0500_sync_events` | RPC `sync_events(batch jsonb)` (contract in the file header) |
| `migrations/0600_storage` | buckets `manifests`, `dataset_photos`; storage policies; `admin_storage_usage()` |
| `migrations/0700_deletion_retention` | `request_deletion()`, deletion executor + proof, retention, service RPCs, pg_cron jobs |
| `migrations/0800_aggregates_admin_keepalive` | `facility_screening_summary()`, audited admin RPCs, `keepalive()` |
| `functions/process-deletion`, `functions/retention-sweep` | Edge Functions (Deno, hanya `CRON_SECRET`, log hanya jumlah) |
| `seed/seed.sql` | SYNTHETIC data only (`*.invalid` e-mails, `5eed…` UUIDs, no names/photos/passwords) |
| `tests/*.test.sql` + `tests/_helpers.psql` | pgTAP suite (helpers are included with `\ir`) |
| `templates/*.html` | e-mail templates (Indonesian, link only, no child data) |
| `scripts/local-harness/` | Docker-free fallback runner (local only, never for a hosted DB) |

Migration lain (mis. `20261009000100_ml_review_queue.sql` milik engineer FastAPI) **wajib** `enable` + `force row level security` pada setiap tabel baru — `tests/00_meta.test.sql` gagal bila tidak.

## Matriks tabel × peran (RLS)

| Tabel | guardian | kader | facility_viewer | admin | anon | service_role |
|---|---|---|---|---|---|---|
| `guardian` | R/U milik sendiri | R/U milik sendiri | R milik sendiri | R milik sendiri | – | semua |
| `user_role` | R milik sendiri, **tanpa tulis** | sama | sama | lewat RPC `admin_grant_role/revoke` (diaudit) | – | semua |
| `child_profile` | R anak milik; I hanya "shell" kosong; U bila consent (b) aktif | + anak di roster sendiri | **tidak ada** | **tidak ada** (hanya `admin_get_child_record`, wajib reason code, diaudit) | – | semua |
| `consent` | R anak milik; I (in_app); U hanya `withdrawn_at` | + `paper_via_kader` | – | – | – | semua |
| `screening`, `brushing_event`, `referral` | R anak milik; I bila consent (b) aktif & tidak ada permintaan hapus | + roster sendiri | – | – | – | semua |
| `image_upload` | R; I bila consent (c) + flag `dataset_contribution` + path prefiks sendiri | sama | – | – | – | semua |
| `class_roster(_member)` | – | CRUD roster sendiri; anggota hanya anak milik sendiri; `facility_code` = cakupan sendiri | – | – | – | semua |
| `deletion_request` | R milik sendiri; I lewat `request_deletion()` | sama | – | – | – | semua |
| `audit_log` | – | – | – | R | – | R/I (tanpa U/D, juga owner diblok trigger) |
| `sync_event` | R milik sendiri (tulis via `sync_events`) | sama | – | – | – | semua |
| `feature_flag` | R | R | R | R; tulis via `admin_set_feature_flag` (dataset_contribution butuh `approval_ref` KEPK) | – | semua |
| `app_setting` | – | – | – | R | – | semua |
| `private.secret` | – | – | – | – | – | – (hanya owner) |
| Storage `dataset_photos` | **INSERT saja** di `<uid>/<child>/<uuid>.jpg`, consent (c) + flag ON, ≤ 500 KiB JPEG; tanpa select/update/delete | sama | – | – | – | baca massal (PC tim) |
| Storage `manifests` | baca publik (URL) | sama | sama | tulis | baca publik | tulis |

Agregat `facility_viewer`: `facility_screening_summary(from, to)` → hitungan per fasilitas × bulan × level, hanya `facility_code` miliknya, **sel < 5 disembunyikan** (`screenings = NULL, suppressed = true`), rentang ≤ 400 hari, setiap panggilan diaudit.

## Kontrak `sync_events`
Lihat header `migrations/20261008000500_sync_events.sql`. Ringkas: array 1..100 event `{event_id, type, payload}` (≤ 256 KiB; payload ≤ 8 KiB, kunci di-whitelist sehingga nama/teks bebas ditolak). Hasil per event `{index, event_id, status: applied|rejected, code?}`. Replay `event_id` yang sama → hasil identik; `event_id` sama dengan isi berbeda / milik akun lain → `event_id_conflict`. Kode: `schema_invalid, unknown_event_type, forbidden, consent_required, feature_disabled, deletion_pending, entity_conflict, event_id_conflict, internal_error` (hanya `internal_error` yang boleh dikirim ulang dengan `event_id` sama). Urutan untuk anak baru: `consent_changed` (grant a/b) → `child_upserted` → data. Penarikan (b) menghentikan semua sinkron data anak itu (RPC + RLS); penarikan (c) menandai foto `withdrawn` → dihapus `retention-sweep`.

## Hak hapus & retensi
1. App memanggil `request_deletion('child', id)` atau `request_deletion('account')` → sinkron subjek langsung berhenti.
2. `process-deletion` (Edge) menghapus objek foto lewat Storage API, lalu `service_execute_deletion`.
3. pg_cron `dentisai-process-deletions` (tiap 15 menit) menyelesaikan permintaan tanpa foto langsung di SQL; yang masih punya foto menunggu langkah 2 (`awaiting_storage_purge`).
4. Bukti di `deletion_request`: hitungan per tabel, `storage_objects_deleted`, `subject_hash` (HMAC berkunci pepper internal), `proof_sha256`, `executed_by`, `completed_at`; `subject_id` dikosongkan; entri `audit_log` hanya hash. Uji `07_deletion` memindai **semua** tabel public/private/storage/auth: id anak/akun dan e-mail tidak tersisa.

Retensi: `image_upload_ttl_days` (default 365 — **[PERLU VERIFIKASI] sesuai protokol KEPK**), `sync_event_retention_days` (180), di tabel `app_setting` (terbaca drg/etik). Penjadwalan Edge Functions (tidak bisa oleh pg_cron tanpa pg_net + vault): jalankan dari scheduler eksternal (mis. GitHub Actions, sama seperti keepalive) lewat `.github/workflows/scheduled-jobs.yml` dengan secret CI `SUPABASE_URL` + `CRON_SECRET` (bukan service_role) — **pemilik: isi 2 secret di GitHub**.

## Kendala Free plan (ADR 008, `../CLAUDE.md` §6)
- Proyek **dijeda setelah ±1 minggu tanpa aktivitas** → ping eksternal `POST /rest/v1/rpc/keepalive` (anon key) via `../.github/workflows/keepalive.yml`. **pg_cron tidak dapat membangunkan proyek yang sudah dijeda**, dan apakah aktivitas pg_cron internal mencegah penjedaan **[PERLU VERIFIKASI]**.
- **Tanpa backup otomatis** → `../tools/backup_db.sh` (pg_dump terenkripsi age), jangan simpan dump di repo/CI publik.
- Kuota: DB 500 MB, Storage 1 GB (maks. 50 MB/berkas), egress 5 GB, 2 proyek (dev+prod) **[PERLU VERIFIKASI berkala di supabase.com/pricing]**; pantau dengan `admin_storage_usage()` (estimasi sisa kapasitas foto 500 KiB).
- SMTP bawaan Supabase hanya 2 email/jam → SMTP Resend wajib; Resend Free 100 email/hari → `auth.rate_limit.email_sent = 10`/jam, email hanya konfirmasi & reset.

## TODO pemilik produk: DNS / SMTP / redirect
- [ ] Resend: tambah domain `auth.ryhnfir.dev`, pasang rekaman **SPF (TXT), DKIM (TXT), MX** (bounce) persis seperti di dashboard Resend; tunggu status *Verified*.
- [ ] DMARC `_dmarc.auth.ryhnfir.dev` mulai `v=DMARC1; p=none; rua=mailto:<alamat-laporan>`; setelah laporan bersih ±2–4 minggu naikkan ke `p=quarantine`.
- [ ] Buat API key Resend (akses *sending* saja) → simpan di Supabase Dashboard → Auth → SMTP (bukan di repo); host `smtp.resend.com`, port 465, user `resend`, pengirim `no-reply@auth.ryhnfir.dev`, nama "DentisAI".
- [ ] Halaman statis `https://app.ryhnfir.dev/auth/callback` (GitHub Pages/Cloudflare Pages) yang meneruskan ke `dentisai://auth/callback`; set *Site URL* + *Redirect URLs* (juga `dentisai://auth/callback`); opsional Android App Links (`/.well-known/assetlinks.json`).
- [ ] CAPTCHA: daftar hCaptcha/Turnstile (gratis), isi `CAPTCHA_PROVIDER/SECRET` di dashboard; site key di app.
- [ ] Uji kirim konfirmasi & reset ke kotak masuk Gmail/Yahoo; cek header SPF/DKIM/DMARC = pass.
- [ ] Admin pertama: di SQL editor `insert into public.user_role (user_id, role) values ('<uuid>', 'admin');`
- [ ] Jadwalkan `process-deletion` (≥ harian) dan `retention-sweep` (harian) dari scheduler eksternal.
- [ ] Cek `select rolbypassrls from pg_roles where rolname = 'postgres'` = true di proyek hosted (prasyarat migrasi 0100).

## Decisions (pilihan yang lebih aman bila spesifikasi ambigu)
1. **`sync_events` SECURITY INVOKER**: setiap tulis juga diperiksa RLS (consent (b) ada di kebijakan INSERT), jadi menulis langsung lewat PostgREST tidak melewati consent. Buku besar idempotensi ditulis lewat helper definer sempit.
2. **Penolakan bersifat final dan disimpan** (replay deterministik); perbaiki penyebab lalu kirim `event_id` baru. Payload tidak pernah disimpan — hanya SHA-256.
3. **Anak dibuat sebagai "shell" kosong oleh `consent_changed`**; data profil baru diterima setelah consent (b). Profil anak buatan kader dan profil buatan orang tua **tidak ditautkan** (MVP).
4. **Admin tidak punya akses baris langsung** ke data anak; hanya RPC beralasan yang diaudit. Akun yang memiliki data anak tidak bisa menjadi `facility_viewer`/`admin` (pemisahan tugas); pemberian peran itu mencabut `guardian`/`kader`.
5. **Agregat**: granularitas bulan, k-anonim sel ≥ 5 (tidak bisa diturunkan di bawah 5). Belum ada *complementary suppression* — batasan yang diketahui.
6. **audit_log** menyimpan hash HMAC (pepper DB-internal), bukan id; append-only bahkan untuk owner. Setelah penghapusan, hash tetap ada tetapi tidak dapat ditautkan tanpa id asli.
7. **Penarikan consent (c)** → foto ditandai `withdrawn` **dan dihapus** dari Storage oleh `retention-sweep` (lebih privat dari sekadar dikeluarkan dari pelatihan). Model yang sudah dilatih tidak dapat melupakan — wajib disebut di lembar persetujuan.
8. **Penghapusan akun** menghapus baris `auth.users` lewat SQL (cascade ke identities/sessions). Log audit internal GoTrue (`auth.audit_log_entries`) dapat memuat e-mail — retensinya **[PERLU VERIFIKASI]**.
9. Anonymous sign-in **mati** (mode anak tidak memerlukan akun sama sekali). Panjang kata sandi minimal 10 + huruf besar/kecil/angka.
10. Edge Functions memakai `verify_jwt = false` + pembandingan *constant-time* dengan secret fungsi `CRON_SECRET` (fail-closed bila kosong/ < 32 karakter); kunci service_role yang disuntikkan platform tidak sama dengan kunci di dashboard, jadi tidak dipakai sebagai kredensial penjadwal.

## Status verifikasi (2026-10-09)
- `supabase test db` / `supabase start`: **belum dijalankan** (Docker daemon tidak bisa dinyalakan). `config.toml` dimuat oleh Supabase CLI 2.120.0 tanpa galat (gagal hanya di langkah Docker) bila env CAPTCHA terisi.
- Harness PG17 + stub: semua migrasi + seed + penerapan ulang migrasi berhasil; 9 berkas uji / 196 asersi lulus; uji mutasi (tabel tanpa RLS, kolom nama, kebijakan bocor) terbukti membuat suite gagal.
- **Tidak diuji**: Edge Functions (Deno tidak tersedia; belum di-typecheck), pg_cron (tidak ada di PG lokal), perilaku nyata Storage API/GoTrue/PostgREST, pgTAP asli (shim lokal dipakai).
