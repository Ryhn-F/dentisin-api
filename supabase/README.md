# DentisAI · Supabase (auth + CRUD + storage)

Skema Postgres, RLS, RPC, Storage, Edge Functions, dan uji untuk proyek Supabase Free (Singapore, `ap-southeast-1`). Rujukan: `../../docs/PRD.md` §6.1/§6.9/§9.3–9.6/§10, ADR 005 & 008, `../CLAUDE.md` §6.

## Menjalankan / How to run

```bash
cd backend                         # project root of the Supabase CLI
npx supabase@2.120.0 start         # needs Docker Desktop running
npx supabase db reset              # applies migrations/ + seed/seed.sql
npx supabase test db               # pgTAP suite in supabase/tests/*.test.sql
```

Env for `config.toml` (read from the process and from `backend/.env` / `supabase/.env`; never commit values): `AUTH_SITE_URL`, `AUTH_REDIRECT_URL`, `RESEND_API_KEY`, `CAPTCHA_SECRET`, plus two **required boolean switches** `AUTH_SMTP_ENABLED` and `AUTH_CAPTCHA_ENABLED` (the CLI aborts with `CliConfigParseError` if they are unset/empty or not `true|false`). **Local safe default:** put `false` for both in `backend/.env` -> `supabase start` sends mail to Mailpit (http://127.0.0.1:54324) and never uses the real Resend key or CAPTCHA secret. They are set to `true` **only inline** on `supabase config push` (see `../docs/runbooks/auth-setup.md`, step E). To try CAPTCHA locally: `AUTH_CAPTCHA_ENABLED=true CAPTCHA_SECRET=1x0000000000000000000000000000000AA supabase start` (Cloudflare Turnstile always-pass test secret). The CAPTCHA provider is fixed to `turnstile` in `config.toml` (`CAPTCHA_PROVIDER` is no longer read).

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
| `migrations/0300_helpers_and_triggers` | SECURITY DEFINER helpers (`search_path=''`), signup trigger (always `guardian`; superseded by ADR 017 migration 300: guardian/teen from user metadata, child only from app metadata), append-only `audit_log`, consent guard |
| `migrations/0400_rls_policies` | RLS ENABLE+FORCE on all tables, column-scoped grants, policies |
| `migrations/0500_sync_events` | RPC `sync_events(batch jsonb)` (contract in the file header) |
| `migrations/0600_storage` | buckets `manifests`, `dataset_photos`; storage policies; `admin_storage_usage()` |
| `migrations/0700_deletion_retention` | `request_deletion()`, deletion executor + proof, retention, service RPCs, pg_cron jobs |
| `migrations/0800_aggregates_admin_keepalive` | `facility_screening_summary()`, audited admin RPCs, `keepalive()` |
| `migrations/20261017000100..600_account_model_v2_*` | ADR 017 (akun v2): 100 enum (`account_type`, `request_status`, `proof_status`, `app_role.child`, `consent_type.d`, 2 tipe event; file sendiri karena nilai enum baru tidak boleh dipakai di transaksi yang sama) · 200 tabel/kolom + `app_setting` ★ + flag `brushing_proof_video` · 300 helper, trigger signup v2, guard, kebijakan RLS · 400 `sync_events` · 500 RPC klien · 600 bucket `brushing_proofs`, RPC service, perluasan hak hapus/retensi |
| `functions/process-deletion`, `functions/retention-sweep`, `functions/quota-check`, `functions/_shared` | Edge Functions (Deno, hanya `CRON_SECRET`, log hanya jumlah). `handler.ts` = logika (diuji), `index.ts` = `Deno.serve`. Uji: `cd functions && deno task ci` (check + lint + fmt + 117 tes, tanpa jaringan Supabase; CI job `edge-functions`) |
| `functions/child-accounts`, `functions/child-login` | Edge Functions untuk app (ADR 017, `verify_jwt = true`): orang tua mengelola akun anak; anak login dengan email orang tua + username + password. `handler.ts` = logika (diuji dengan port tiruan), `ports.ts` = supabase-js, `index.ts` = `Deno.serve`. Lihat bagian "Akun anak" di bawah |
| `seed/seed.sql` | SYNTHETIC data only (`*.invalid` e-mails, `5eed…` UUIDs, no names/photos/passwords) |
| `tests/*.test.sql` + `tests/_helpers.psql` | pgTAP suite (helpers are included with `\ir`) |
| `templates/*.html` | e-mail templates (Indonesian, link only, no child data; `reauthentication.html` berisi kode 6 digit saja) |
| `scripts/local-harness/` | **Legacy** Docker-free fallback runner (local only, never for a hosted DB); replaced by `supabase test db` once that runs |
| `scripts/local_stack.py`, `rls_http_check.py`, `deletion_e2e_check.py` | HTTP checks against the local `supabase start` stack only (see "Uji HTTP lokal") |

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

### Akun v2 (ADR 017) — tambahan matriks
`guardian` dan `teen` = akun yang daftar sendiri (keduanya peran `guardian`; teen hanya punya satu profil diri `child_profile.is_self`). Akun `child` = pengguna Auth yang dibuat orang tua lewat Edge Function `child-accounts`; peran `child`, **tanpa** baris `guardian`. `private.can_access_child`/`owns_child` kini **selalu false** untuk akun anak, jadi semua kebijakan lama menolak akun anak; akses anak hanya lewat `private.child_can_access()` (anak miliknya, selama `child_account.disabled_at` kosong).

| Tabel | guardian / teen (pemilik anak) | child (aktif) | child nonaktif |
|---|---|---|---|
| `child_profile` | seperti lama (teen: hanya profil diri) | R baris anaknya | – |
| `consent`, `screening`, `referral`, `image_upload`, Storage `dataset_photos` | seperti lama | – | – |
| `brushing_event` | R; I (consent b) | R anaknya; I `recorded_by = auth.uid()` (consent b) | – |
| `screening_request` | R; status lewat RPC `ack_screening_request` / `screening_recorded.request_id` | R + I anaknya (consent b, ≤ 5/hari WIB) | – |
| `brushing_proof` | R baris hidup (bukan deleted/expired, consent d); viewed/hapus lewat RPC | R baris hidup; I lewat `brushing_proof_registered` (flag + consent b+d, ≤ 3/hari, ≤ 10 MiB, ≤ 180 dtk) | – |
| `child_account` | R akun anaknya; tulis hanya service_role/trigger | R barisnya | – |
| `child_login_attempt` | – | – | – (service_role saja) |
| Storage `brushing_proofs` | SELECT objek hidup di prefiks `<uid>/` (untuk `createSignedUrl`) | INSERT hanya ke path baris yang ia daftarkan | – |

Admin/kader/facility_viewer tidak punya akses ke video bukti. Username hanya ada di `child_account` (uji `16_*` memeriksa tidak ada kolom username lain). Komentar skema lama "tanpa video/nama" (0200) sekarang benar dengan pengecualian ADR 017: `COMMENT ON` `brushing_event`, `child_profile`, `guardian` diperbarui di migrasi 200 (berkas 0200 tidak diedit karena sudah diterapkan).

Agregat `facility_viewer`: `facility_screening_summary(from, to)` → hitungan per fasilitas × bulan × level, hanya `facility_code` miliknya, **sel < 5 disembunyikan** (`screenings = NULL, suppressed = true`), rentang ≤ 400 hari, setiap panggilan diaudit.

## Kontrak `sync_events`
Lihat header `migrations/20261008000500_sync_events.sql`. Ringkas: array 1..100 event `{event_id, type, payload}` (≤ 256 KiB; payload ≤ 8 KiB, kunci di-whitelist sehingga nama/teks bebas ditolak). Hasil per event `{index, event_id, status: applied|rejected, code?}`. Replay `event_id` yang sama → hasil identik; `event_id` sama dengan isi berbeda / milik akun lain → `event_id_conflict`. Kode: `schema_invalid, unknown_event_type, forbidden, consent_required, feature_disabled, deletion_pending, entity_conflict, event_id_conflict, internal_error` (hanya `internal_error` yang boleh dikirim ulang dengan `event_id` sama). Urutan untuk anak baru: `consent_changed` (grant a/b) → `child_upserted` → data. Penarikan (b) menghentikan semua sinkron data anak itu (RPC + RLS); penarikan (c) menandai foto `withdrawn` → dihapus `retention-sweep`.

ADR 017 (header `migrations/20261017000400_*`): tipe baru `screening_requested` `{request_id, child_id, reason_code ∈ toothache|sensitive_teeth|bleeding_gums|swelling|other, pain_level? 0..10}` dan `brushing_proof_registered` `{proof_id, child_id, brushing_event_id, size_bytes, duration_s, mime ∈ video/mp4|video/quicktime}` — **hanya** dari akun anak untuk anaknya sendiri; akun anak hanya boleh mengirim dua tipe itu + `brushing_recorded` (lainnya `forbidden`). `screening_recorded` menerima `request_id` opsional (permintaan pending/seen anak yang sama → `done`). `consent_changed` menerima tipe `d`. Kode baru **`limit_exceeded`** (final, disimpan). Server membangun `object_path = <guardian_id>/<child_id>/<proof_id>.(mp4|mov)`; unggah file setelah event diterapkan. Penundaan hapus akun orang tua juga menghentikan sinkron akun anaknya (`deletion_pending`).

### RPC akun v2 (`migrations/20261017000500_*`, authenticated)
`my_account()` · `ensure_self_profile(p_child_id, p_birth_year, p_birth_month)` (teen) · `list_children()` · `child_history(p_child, p_before, p_limit, p_kinds, p_from)` · `brushing_streak(p_child, p_tz)` · `list_screening_requests(p_child, p_status, p_limit)` · `ack_screening_request(p_id, 'seen'|'cancel')` · `list_brushing_proofs(p_child, p_before, p_limit)` · `mark_proof_viewed(p_proof)` · `delete_brushing_proof(p_proof)`. Bukan milik / tidak ada → galat sama `42501 forbidden`. RPC service_role (Edge Functions): `service_resolve_child_login`, `service_child_login_check`, `service_child_login_record`, `service_expired_proofs`, `service_mark_proof_object_removed`, `service_child_users_of`, `service_deletion_targets`.

★ `app_setting` baru: `teen_min_age` 10, `teen_max_age` 19, `max_child_accounts_per_guardian` 6, `child_login_max_attempts` 8, `child_login_window_s` 900, `streak_min_duration_s` 60, `request_max_per_child_per_day` 5, `request_expiry_days` 14, `proof_max_bytes` 10485760, `proof_max_duration_s` 180, `proof_max_per_child_per_day` 3, `proof_retention_days` 7. Flag `brushing_proof_video` (bawaan hidup).

## Hak hapus & retensi
1. App memanggil `request_deletion('child', id)` atau `request_deletion('account')` → sinkron subjek langsung berhenti.
2. `process-deletion` (Edge) menghapus objek foto lewat Storage API, lalu `service_execute_deletion`.
3. pg_cron `dentisai-process-deletions` (tiap 15 menit) menyelesaikan permintaan tanpa foto langsung di SQL; yang masih punya foto menunggu langkah 2 (`awaiting_storage_purge`).
4. Bukti di `deletion_request`: hitungan per tabel, `storage_objects_deleted`, `subject_hash` (HMAC berkunci pepper internal), `proof_sha256`, `executed_by`, `completed_at`; `subject_id` dikosongkan; entri `audit_log` hanya hash. Uji `07_deletion` memindai **semua** tabel public/private/storage/auth: id anak/akun dan e-mail tidak tersisa.

ADR 017: permintaan hapus baru selesai bila **tidak ada** objek subjek tersisa di `dataset_photos` **dan** `brushing_proofs`. Path bukti + id pengguna Auth anak per permintaan: `service_deletion_targets(request_id)` (`service_pending_deletions` tidak berubah). Penyelesaian juga menghapus pengguna Auth akun anak (anak yang dihapus / semua akun anak orang tua yang dihapus) dan entri audit GoTrue-nya; bukti mencatat `screening_request`, `brushing_proof`, `child_account`, `child_auth_user`, `auth_audit_log_entries`. **`process-deletion` perlu diperluas** agar menghapus `proof_paths` dari bucket `brushing_proofs`; sampai itu terjadi, permintaan yang punya video bukti tetap `awaiting_storage_purge`. Retensi harian (`retention_sweep_db`) kini juga: permintaan kedaluwarsa → `expired`, bukti lewat retensi → `expired`, hapus baris bukti yang objeknya sudah dihapus, hapus `child_login_attempt` > 1 hari. Objek bukti dihapus oleh `retention-sweep` (perlu diperluas): `service_expired_proofs` → Storage `remove` → `service_mark_proof_object_removed`.

Retensi: `image_upload_ttl_days` (default 365 — **[PERLU VERIFIKASI] sesuai protokol KEPK**), `sync_event_retention_days` (180), di tabel `app_setting` (terbaca drg/etik). Penjadwalan Edge Functions (tidak bisa oleh pg_cron tanpa pg_net + vault): jalankan dari scheduler eksternal (mis. GitHub Actions, sama seperti keepalive) lewat `.github/workflows/scheduled-jobs.yml` dengan secret CI `SUPABASE_URL` + `CRON_SECRET` (bukan service_role) — **pemilik: isi 2 secret di GitHub**.

## Akun anak — Edge Functions `child-accounts` & `child-login` (ADR 017 §2.2)
- **`child-accounts`** (JWT orang tua): `{action: create|reset_password|set_disabled|delete, child_id, ...}`. Hanya `my_account().account_type = 'guardian'` (remaja/anak → 403); kepemilikan dicek lewat RLS **dan** `owner_id` (anak orang lain = anak tak dikenal = 404 `child_not_found`). `create` membuat pengguna Auth `child-<child_id>@child.dentisai.invalid` (`email_confirm`, `app_metadata` server) → trigger membuat `child_account`. Username `^[a-z0-9][a-z0-9._-]{2,19}$` (+ kata cadangan), kata sandi = kebijakan `config.toml` (≥ 8, kecil+besar+angka, ≤ 72 byte; `_shared/validators.ts`, uji membandingkan dengan `config.toml`). Batas `max_child_accounts_per_guardian` (6). `set_disabled` mengisi `child_account.disabled_at` **dan** ban Auth (`876000h`/`none`); `delete` menghapus pengguna Auth anak (profil + data tetap).
- **`child-login`** (anon key): `{parent_email, username, password, captcha_token?}` → `service_child_login_check` (kunci `HMAC-SHA256(CHILD_LOGIN_HASH_PEPPER, email|ip)`, `child_login_max_attempts` 8 / `child_login_window_s` 900 → 429 + `Retry-After`) → `service_resolve_child_login` → **selalu** GoTrue `grant_type=password` (email dummy bila tak dikenal; token CAPTCHA diteruskan) → 200 sesi `{access_token, refresh_token, token_type, expires_in, expires_at, user:{id}, child:{username}}` atau **401 `invalid_credentials` seragam** (dipadatkan ke ≥ 600 ms). GoTrue 5xx/429/tak terjangkau → 502 (tidak dihitung).
- **Secret:** `CHILD_LOGIN_HASH_PEPPER` (≥ 32 karakter acak, wajib; kosong → 500) dan opsional `ALLOWED_ORIGINS` (CORS; bawaan `https://app.ryhnfir.dev`, tanpa wildcard). `SUPABASE_URL/ANON_KEY/SERVICE_ROLE_KEY` disuntikkan platform. Log hanya status + kode galat. Kontrak rinci: `../docs/api/EDGE_FUNCTIONS_AND_HOOKS.md` §1.5–1.6.
- **Deploy:** `supabase secrets set CHILD_LOGIN_HASH_PEPPER=<acak>` lalu `supabase functions deploy child-accounts child-login` (setelah migrasi ADR 017 di-push).

## Kendala Free plan (ADR 008, `../CLAUDE.md` §6)
- Proyek **dijeda setelah ±1 minggu tanpa aktivitas** → ping eksternal `POST /rest/v1/rpc/keepalive` (anon key) via `../.github/workflows/keepalive.yml`. **pg_cron tidak dapat membangunkan proyek yang sudah dijeda**, dan apakah aktivitas pg_cron internal mencegah penjedaan **[PERLU VERIFIKASI]**.
- **Pantau kuota (item 5.4)** → migrasi `20261011000100_quota_monitoring.sql`: `admin_quota_usage()` (admin, diaudit `quota_read`) dan `service_quota_usage()` (service, dipanggil Edge Function `quota-check`). Metrik: `db_size` (MB), `storage_size` (MB, jumlah `metadata.size` semua bucket), `mau` (login bulan kalender berjalan, UTC — platform menghitung per siklus tagihan **[PERLU VERIFIKASI]**), `auth_email_24h` (entri audit GoTrue yang mengirim email dalam 24 jam; `unknown` bila tabel audit tidak ada/terbaca). Batas & ambang di `app_setting`: `quota_db_mb` 500, `quota_storage_mb` 1024, `quota_mau` 50000, `quota_email_per_day` 100, `quota_warn_pct` 70, `quota_critical_pct` 90 (tidak pernah < warn). Status per metrik `ok|warn|critical|unknown`; `overall_status` = terburuk. Job `quota-check` di `scheduled-jobs.yml` (harian 08:07 WIB): `warn` → anotasi kuning, `critical` → run merah (GitHub mengirim email ke pemilik). **Tidak terukur dari SQL:** egress 5 GB dan pemanggilan Edge Function → cek halaman *Usage* dashboard secara manual. Aksi email terverifikasi lokal (GoTrue CLI 2.120): invite → `user_invited`; recovery & magic link → `user_recovery_requested`; `user_confirmation_requested` (daftar) **[PERLU VERIFIKASI]** (CAPTCHA lokal aktif saat diuji). `admin/generate_link` juga tercatat sebagai `user_recovery_requested` walau tidak mengirim email → hitungan bisa sedikit berlebih (aman).
- **Tanpa backup otomatis** → `../tools/backup_db.sh` (pg_dump terenkripsi age), jangan simpan dump di repo/CI publik.
- Kuota: DB 500 MB, Storage 1 GB (maks. 50 MB/berkas), egress 5 GB, 2 proyek (dev+prod) **[PERLU VERIFIKASI berkala di supabase.com/pricing]**; pantau dengan `admin_storage_usage()` (estimasi sisa kapasitas foto 500 KiB).
- SMTP bawaan Supabase hanya 2 email/jam → SMTP Resend wajib; Resend Free 100 email/hari → `auth.rate_limit.email_sent = 30`/jam (+ `max_frequency` 60 dtk/pengguna, CAPTCHA), email hanya konfirmasi & reset.

## TODO pemilik produk: DNS / SMTP / redirect
> Langkah rinci untuk non-ahli: **`../docs/runbooks/auth-setup.md`** (halaman redirect siap pakai di `../web/auth/callback/index.html`). Daftar di bawah hanya ringkasan.

- [ ] Resend: tambah domain `auth.ryhnfir.dev`, pasang rekaman **SPF (TXT), DKIM (TXT), MX** (bounce) persis seperti di dashboard Resend; tunggu status *Verified*.
- [ ] DMARC `_dmarc.auth.ryhnfir.dev` mulai `v=DMARC1; p=none; rua=mailto:<alamat-laporan>`; setelah laporan bersih ±2–4 minggu naikkan ke `p=quarantine`.
- [ ] Buat API key Resend (akses *sending* saja) → simpan di Supabase Dashboard → Auth → SMTP (bukan di repo); host `smtp.resend.com`, port 465, user `resend`, pengirim `no-reply@auth.ryhnfir.dev`, nama "DentisAI".
- [ ] Halaman statis `https://app.ryhnfir.dev/auth/callback` (GitHub Pages/Cloudflare Pages) yang meneruskan ke `dentisai://auth/callback`; set *Site URL* + *Redirect URLs* (juga `dentisai://auth/callback`); opsional Android App Links (`/.well-known/assetlinks.json`).
- [ ] CAPTCHA: daftar hCaptcha/Turnstile (gratis), secret → `CAPTCHA_SECRET` di `.env` (provider tetap `turnstile` di `config.toml`); site key di app.
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
9. Anonymous sign-in **mati** (mode anak tidak memerlukan akun sama sekali). Panjang kata sandi minimal **8** (keputusan pemilik 2026-10-10) + huruf besar/kecil/angka di `config.toml` (`password_requirements` belum terbukti diterapkan di proyek hosted — CLI melaporkannya 'not returned'; [PERLU VERIFIKASI])
10. Edge Functions memakai `verify_jwt = false` + pembandingan *constant-time* dengan secret fungsi `CRON_SECRET` (fail-closed bila kosong/ < 32 karakter); kunci service_role yang disuntikkan platform tidak sama dengan kunci di dashboard, jadi tidak dipakai sebagai kredensial penjadwal.

## Uji HTTP lokal (Supabase CLI) — `scripts/`
- `scripts/rls_http_check.py` (item 1.6): RLS/Storage lewat Kong → PostgREST/GoTrue/Storage API dengan JWT asli (anon, guardian A/B, kader, facility_viewer, admin): isolasi baris, eskalasi peran via REST, idempotensi `sync_events`, aturan unggah `dataset_photos`, bucket `manifests`.
- `scripts/deletion_e2e_check.py` (item 1.5): akun → data + foto (Storage API) → `request_deletion('account')` → Edge Function `process-deletion` (bila di-serve lokal) atau RPC yang sama → pindai **semua tabel semua skema** untuk id/e-mail, blob di disk kontainer Storage, `auth.sessions/refresh_tokens/identities/audit_log_entries`.
- Keduanya stdlib saja, **menolak URL non-localhost dan kunci yang bukan kunci demo lokal** (`iss = supabase-demo`). Jalankan dari `backend/`: `eval "$(npx supabase status -o env)"; export API_URL ANON_KEY SERVICE_ROLE_KEY; python supabase/scripts/rls_http_check.py`.
- Jalur Edge untuk `deletion_e2e_check.py`: `supabase functions serve --env-file <berkas lokal berisi CRON_SECRET=<nilai lokal ≥32 char>>`, lalu `LOCAL_CRON_SECRET=<nilai yang sama> python supabase/scripts/deletion_e2e_check.py --via edge`. Tanpa itu runtime menjawab `503 not_configured` dan skrip memakai `--via rpc`.
- **Peringatan env:** Supabase CLI ikut memuat `backend/.env` (godotenv, tidak menimpa env yang sudah ada). Lokal wajib `AUTH_SMTP_ENABLED=false` dan `AUTH_CAPTCHA_ENABLED=false` (Mailpit, tanpa CAPTCHA; secret Resend/CAPTCHA asli tidak dipakai). `--exclude studio,imgproxy,vector,logflare,postgres-meta,supavisor,realtime` menghemat disk.

## Status verifikasi akun anak (2026-10-17, Deno 2.9.6)
- `deno task ci`: **117 tes PASS** (76 baru: `child-accounts`, `child-login`, `_shared/{http,validators}`), klien Supabase dan GoTrue ditiru. Nama tabel/kolom/RPC dicocokkan dengan migrasi `20261017000*` (dibaca, tidak diubah). **Belum** di-serve lokal terhadap migrasi itu — uji HTTP nyata masih harus dilakukan.

## Status verifikasi akun v2 (2026-10-18) — **hanya harness tanpa Docker**
- Docker Desktop mati → `supabase test db` (pgTAP asli) **belum dijalankan**. Harness PG17 + stub (`scripts/local-harness/`): 18 migrasi diterapkan dua kali (uji re-runnable) + seed, **17 berkas / 476 asersi PASS** (241 lama tidak diubah + 235 baru di `11_account_signup` 37, `12_child_rls` 49, `13_child_sync` 44, `14_history_streak` 29, `15_proof_storage` 37, `16_child_service_deletion` 39).
- Stub harness diperbarui: `auth.audit_log_entries`, `auth.users.last_sign_in_at`/`banned_until` (tanpa itu `07_deletion` dan `10_quota` gagal di harness).
- `rls_http_check.py` / `deletion_e2e_check.py` butuh `supabase start` → belum dijalankan untuk akun v2. [PERLU VERIFIKASI] jalur GoTrue admin `createUser` dengan `app_metadata.account_type = child` (trigger INSERT dan trigger UPDATE `raw_app_meta_data` sama-sama didukung).

## Status verifikasi kuota (2026-10-11, CLI 2.120.0)
- `supabase db reset` (11 migrasi) + `supabase test db`: **11 berkas / 241 asersi PASS** (24 baru di `10_quota`).
- `deno task ci` (Deno 2.9.6): **41 tes PASS** (7 baru di `quota-check`).
- `quota-check` di-serve lokal: 200 dengan angka saja; secret salah → 403; GET → 405.

## Status verifikasi (2026-10-10, Supabase CLI 2.120.0, Docker 28.3, image Postgres 17.11.0.004)
- `supabase start` + `supabase db reset`: 10 migrasi + seed berhasil.
- `supabase test db` (pgTAP asli): **9 berkas / 199 asersi PASS**. Asersi baru di `07_deletion` terbukti gagal (3/26) sebelum migrasi `20261010000100` diterapkan.
- `rls_http_check.py`: **88/88** lewat Kong/PostgREST/GoTrue/Storage asli. Unggah Storage API berhasil tanpa kebijakan SELECT tambahan.
- `deletion_e2e_check.py`: **28/28** (`--via rpc`) dan **29/29** (`--via edge`, Edge Function `process-deletion` di-serve, termasuk 403 untuk secret salah). `retention-sweep` menjawab 200.
- Temuan diperbaiki: `auth.audit_log_entries` (GoTrue, tanpa FK) masih menyimpan id + e-mail setelah hapus akun → migrasi `20261010000100_deletion_purge_auth_audit.sql`. [PERLU VERIFIKASI] hak DELETE `postgres` atas tabel itu di proyek hosted.
- Di luar DB (informasi): log kontainer `auth` dan `storage` masih memuat id/e-mail (retensi log platform).
- Harness PG17 + stub (`scripts/local-harness/`) kini hanya cadangan.
