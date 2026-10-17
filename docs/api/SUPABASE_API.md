# Supabase API (Auth, PostgREST, RPC, Storage)

> **Ringkasan (ID).** Dokumen ini merinci apa yang boleh dipanggil app ke Supabase: endpoint Auth, tabel (siapa boleh baca/tulis), RPC beserta contoh JSON dan kode galat, bucket Storage, batas laju, dan contoh `supabase-js` serta `curl`. Semua diturunkan dari `supabase/migrations/*.sql` dan `supabase/config.toml`. Jalur tulis data anak yang dianjurkan adalah RPC `sync_events` ([`SYNC_EVENTS.md`](SYNC_EVENTS.md)).

Placeholder yang dipakai: `<project-ref>`, `<anon-key>`, `<jwt>` (access token pengguna), `<uid>` (id akun), `<child-id>`. Jangan menaruh nilai asli di dokumen, log, atau repo.

Base URL: `https://<project-ref>.supabase.co` (lokal: `http://127.0.0.1:54321`).
Header untuk REST/RPC/Storage: `apikey: <anon-key>` dan `Authorization: Bearer <jwt>` (tanpa login, `Authorization: Bearer <anon-key>`).

## 1. Auth (GoTrue)

Konfigurasi (`supabase/config.toml`):

| Pengaturan | Nilai |
|---|---|
| Metode | email + password (`[auth.email] enable_signup = true`, `enable_confirmations = true`) |
| Panjang sandi minimum | 8; `password_requirements = "lower_upper_letters_digits"` (huruf kecil, besar, dan angka) |
| Anonymous sign-in, SMS/phone, MFA (TOTP/phone/WebAuthn), manual linking | mati |
| JWT | `jwt_expiry = 3600` (1 jam); `enable_refresh_token_rotation = true`; `refresh_token_reuse_interval = 10` (detik) |
| Konfirmasi perubahan email | `double_confirm_changes = true`; `secure_password_change = true` |
| Jeda kirim email per pengguna | `max_frequency = "60s"`; OTP 6 digit, kedaluwarsa 3600 dtk |
| Site URL | `env(AUTH_SITE_URL)` (runbook: `https://app.ryhnfir.dev/auth/callback`) |
| Redirect URLs | `env(AUTH_REDIRECT_URL)` dan `dentisai://auth/callback` |
| Pengirim email | hook Send Email ke mailer (lihat [`EDGE_FUNCTIONS_AND_HOOKS.md`](EDGE_FUNCTIONS_AND_HOOKS.md)); cadangan SMTP Resend `no-reply@auth.ryhnfir.dev` |

Catatan: SMTP, CAPTCHA, dan hook hanya aktif di proyek hosted setelah `supabase config push` dengan saklar `AUTH_*_ENABLED=true`; lokal selalu Mailpit tanpa CAPTCHA. `supabase/README.md` ("Decisions" butir 9) masih menyebut sandi minimal 10; **nilai di `config.toml` (8, keputusan pemilik 2026-10-10) yang berlaku**.

### 1.1 Endpoint yang dipakai app

| Aksi | supabase-js | HTTP |
|---|---|---|
| Daftar | `auth.signUp({ email, password, options: { data: { account_type: 'guardian' \| 'teen' }, emailRedirectTo, captchaToken } })` | `POST /auth/v1/signup` |
| Masuk | `auth.signInWithPassword({ email, password, options: { captchaToken } })` | `POST /auth/v1/token?grant_type=password` |
| Perpanjang sesi | otomatis (`autoRefreshToken`) | `POST /auth/v1/token?grant_type=refresh_token` |
| Lupa sandi | `auth.resetPasswordForEmail(email, { redirectTo, captchaToken })` | `POST /auth/v1/recover` |
| Ganti sandi (setelah deep link recovery) | `auth.updateUser({ password })` | `PUT /auth/v1/user` |
| Keluar | `auth.signOut()` | `POST /auth/v1/logout` |

Perilaku penting:

- `enable_confirmations = true`: `signUp` tidak menghasilkan sesi; pengguna harus membuka tautan di email. `signInWithPassword` sebelum konfirmasi gagal (`email_not_confirmed`).
- Akun baru otomatis berperan `guardian`. `options.data.account_type` hanya menerima `'guardian'` (orang tua) atau `'teen'` (remaja, untuk dirinya sendiri); nilai lain diabaikan (dianggap `guardian`). Field `options.data` **tidak** memberi peran apa pun. **Akun anak** tidak dibuat lewat `signUp`: orang tua membuatnya lewat Edge Function `child-accounts`, anak masuk lewat `child-login` (bukan `signInWithPassword`); lihat [`ACCOUNTS_AND_FLOWS.md`](ACCOUNTS_AND_FLOWS.md).
- Penghapusan akun **bukan** lewat Auth API klien; pakai RPC `request_deletion('account')` (bagian 3.2).
- Isi email hanya tautan atau kode; tanpa data anak.

### 1.2 Tautan email dan deep link

1. Supabase memanggil mailer (hook Send Email). Mailer membangun `https://<project-ref>.supabase.co/auth/v1/verify?token=<token_hash>&type=<signup|recovery|...>&redirect_to=<redirect>` dan mengirimnya ke email pengguna. `redirect_to` hanya disertakan bila lolos daftar putih mailer (`ALLOWED_REDIRECT_PREFIXES`); bila tidak, Supabase memakai Site URL.
2. Pengguna mengetuk tautan, Supabase memverifikasi lalu mengalihkan ke `https://app.ryhnfir.dev/auth/callback` (halaman statis `web/auth/callback/`).
3. Halaman itu menyalin **query string dan fragment** apa adanya ke `dentisai://auth/callback` (tujuan tetap, bukan open redirect), menghapus kode dari address bar, lalu mencoba membuka app. Tidak ada data yang dikirim ke server.
4. App menangani URL `dentisai://auth/callback?code=...` (PKCE) atau `#access_token=...&refresh_token=...` / `#error=...` (implicit) dan menukarnya menjadi sesi (`auth.exchangeCodeForSession(code)` atau `auth.setSession`). Jenis alur yang dipakai app `[PERLU VERIFIKASI: uji pada proyek nyata dengan hook Send Email aktif]`.
5. Untuk `recovery`, setelah sesi terbentuk, app menampilkan layar sandi baru lalu memanggil `updateUser({ password })`.

`emailRedirectTo` / `redirectTo` yang dikirim app harus `https://app.ryhnfir.dev/auth/callback` (daftar putih mailer + Redirect URLs Supabase) atau `dentisai://auth/callback`.

### 1.3 CAPTCHA (Cloudflare Turnstile)

`[auth.captcha] provider = "turnstile"`, `enabled = env(AUTH_CAPTCHA_ENABLED)`. Lokal `false`; di hosted hanya `true` setelah app memakai widget (kalau tidak, pendaftaran gagal). Status di proyek dev `[PERLU VERIFIKASI]`.

Bila aktif, `signUp`, `signInWithPassword`, dan `resetPasswordForEmail` **wajib** membawa token (`captchaToken` di supabase-js; HTTP: `gotrue_meta_security: { "captcha_token": "<token>" }`). Token sekali pakai.

Mendapatkan token: buka WebView ke `https://app.ryhnfir.dev/captcha/?sitekey=<turnstile-site-key>&action=signup` (site key bersifat publik). Halaman mengirim ke app:

```json
{ "type": "turnstile", "token": "<token>" }
```
lewat `window.ReactNativeWebView.postMessage(JSON.stringify(...))`; galat: `{ "type": "turnstile_error", "code": "<kode>" }` (`bad_sitekey`, `expired`, atau kode Turnstile). Fallback tanpa WebView bridge: navigasi ke `dentisai://captcha?token=<token>`. Sitekey harus cocok `^[0-9A-Za-z_-]{10,64}$`.

### 1.4 Contoh

```ts
import { createClient } from '@supabase/supabase-js';

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { storage: secureStoreAdapter, autoRefreshToken: true, persistSession: true, detectSessionInUrl: false },
});

await supabase.auth.signUp({
  email, password,
  options: { emailRedirectTo: 'https://app.ryhnfir.dev/auth/callback', captchaToken },
});
await supabase.auth.signInWithPassword({ email, password, options: { captchaToken } });
await supabase.auth.resetPasswordForEmail(email, {
  redirectTo: 'https://app.ryhnfir.dev/auth/callback', captchaToken,
});
```

```bash
curl -sS -X POST "https://<project-ref>.supabase.co/auth/v1/token?grant_type=password" \
  -H "apikey: <anon-key>" -H "Content-Type: application/json" \
  -d '{"email":"<email>","password":"<password>","gotrue_meta_security":{"captcha_token":"<token>"}}'
```

Galat GoTrue (bentuk `{"code":<http>,"error_code":"<nama>","msg":"..."}`; nama menurut dokumentasi Supabase `[PERLU VERIFIKASI per versi GoTrue]`): `weak_password` (422), `user_already_exists`/`email_exists` (422), `invalid_credentials` (400), `email_not_confirmed` (400), `captcha_failed` (400), `over_email_send_rate_limit` (429), `over_request_rate_limit` (429).

## 2. PostgREST: tabel dan hak per peran

Endpoint: `GET|POST|PATCH /rest/v1/<tabel>`. Hanya schema `public` (dan `graphql_public`) yang diekspos; schema `private` (helper, rahasia, job) **tidak**. `max_rows = 1000` per respons. Semua tabel memakai `ENABLE` + `FORCE ROW LEVEL SECURITY`; hak kolom diberikan eksplisit (grant per kolom). `anon` tidak punya akses tabel.

Kunci: R = SELECT, I = INSERT, U = UPDATE, D = DELETE. "Sendiri" = baris milik akun pemanggil. "Anak yang dapat diakses" = anak milik sendiri, atau (kader) anak di roster sendiri.

| Tabel | guardian | kader | facility_viewer | admin | service_role |
|---|---|---|---|---|---|
| `guardian` | R sendiri; U kolom `locale` (nilai `id`) | sama | R sendiri | R sendiri | semua |
| `user_role` | R sendiri; tanpa tulis | sama | sama | R sendiri (tulis lewat RPC) | semua |
| `child_profile` | R anak sendiri; I hanya "shell" kosong (`owner_id` = sendiri; tahun/bulan lahir null, alergi kosong); U (`birth_year`, `birth_month`, `allergies`) bila consent (b) aktif dan tidak ada permintaan hapus; tanpa D | + R anak di roster | tidak ada | tidak ada (hanya `admin_get_child_record`) | semua |
| `consent` | R; I (`method='in_app'`, `granted_by`=sendiri, tidak ada permintaan hapus); U hanya `withdrawn_at` (sekali; baris tidak bisa dibuka lagi) | + `method='paper_via_kader'` | - | - | semua |
| `screening`, `brushing_event`, `referral` | R; I bila consent (b) aktif, tidak ada permintaan hapus, pelaku = sendiri; tanpa U/D | sama (anak di roster) | - | - | semua |
| `image_upload` | R; I (`id`,`child_id`,`uploaded_by`,`consent_id`,`object_path`) bila consent (c) aktif, flag `dataset_contribution` hidup, path berawalan `<uid>/<child_id>/`; kolom status/waktu diatur server | sama | - | - | semua |
| `class_roster` | - | CRUD roster sendiri; `facility_code` null atau sama dengan cakupan kader | - | - | semua |
| `class_roster_member` | - | CRUD dalam roster sendiri; menambah hanya anak yang dimiliki sendiri | - | - | semua |
| `deletion_request` | R sendiri; I via `request_deletion()` (I langsung hanya untuk `pending` pada anak/akun sendiri) | sama | - | - | semua |
| `sync_event` | R sendiri (ditulis oleh `sync_events`) | sama | - | - | semua |
| `child_account` (ADR 017) | R akun anak miliknya (tanpa kredensial); tanpa tulis klien | - | - | - | semua |
| `screening_request` (ADR 017) | R untuk anaknya; status diubah lewat RPC `ack_screening_request` / `screening_recorded`; tulis lewat `sync_events` | - | - | - | semua |
| `brushing_proof` (ADR 017) | R metadata klip hidup anaknya; tandai dilihat/hapus lewat RPC | - | - | - | semua |
| `child_login_attempt` | tidak ada | - | - | - | semua |
| `feature_flag` | R (perlu login) | R | R | R; tulis lewat RPC | semua |
| `app_setting` | - | - | - | R | semua |
| `audit_log` | - | - | - | R | R/I saja (append-only) |
| `ml_review_queue`, `ml_review_result`, `ml_model_registry` | - | - | - | R | semua (ditulis FastAPI) |

Tidak ada kebijakan DELETE untuk klien pada tabel data anak: penghapusan hanya lewat `request_deletion`. `private.secret` tidak dapat dibaca siapa pun kecuali owner.

Contoh baca (app):

```bash
# Peran sendiri
curl -sS "https://<project-ref>.supabase.co/rest/v1/user_role?select=role,facility_code" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <jwt>"
# Flag unggah foto dataset
curl -sS "https://<project-ref>.supabase.co/rest/v1/feature_flag?select=key,enabled&key=eq.dataset_contribution" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <jwt>"
```

```ts
const { data, error } = await supabase.from('deletion_request')
  .select('id,scope,status,requested_at,completed_at,failure_code').order('requested_at', { ascending: false });
```

Menulis lewat PostgREST langsung (`POST /rest/v1/screening`, dst.) tetap dicek RLS, tetapi **tidak idempotens**; jalur resmi app adalah `sync_events`.

## 3. RPC

Panggilan: `POST /rest/v1/rpc/<nama>` dengan body JSON berisi argumen **bernama**. Bentuk galat: lihat [`README.md`](README.md) (bagian galat umum). Semua RPC bukan-publik: `anon` ditolak (401/403) kecuali `keepalive`.

### 3.1 `sync_events(batch jsonb) -> jsonb`  (authenticated)

Kontrak lengkap di [`SYNC_EVENTS.md`](SYNC_EVENTS.md). Ringkas:

```bash
curl -sS -X POST "https://<project-ref>.supabase.co/rest/v1/rpc/sync_events" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"batch":[{"event_id":"<uuid>","type":"consent_changed","payload":{"child_id":"<uuid>","consent_type":"b","action":"grant","text_version":"v1"}}]}'
```
Respons 200: `[{"index":0,"event_id":"<uuid>","status":"applied"}]`.
Galat tingkat panggilan (HTTP 400, `code` `22023`): `batch_must_be_array`, `batch_size_out_of_range` (kosong atau > batas, bawaan 100, maks 500), `batch_too_large` (> 262144 byte). `not_authenticated` (`28000`) tanpa sesi.

### 3.2 `request_deletion(p_scope deletion_scope, p_child_id uuid default null) -> uuid`  (authenticated)

Meminta hak hapus. Efek langsung: sinkronisasi untuk subjek berhenti (`deletion_pending`). `p_scope`: `child` (wajib `p_child_id`, anak milik sendiri) atau `account` (`p_child_id` harus kosong; menghapus semua anak, roster, peran, ledger, dan akun auth).

```bash
curl -sS -X POST "https://<project-ref>.supabase.co/rest/v1/rpc/request_deletion" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"p_scope":"child","p_child_id":"<child-id>"}'
# 200: "3b9d0c7e-...-..."   (id deletion_request, JSON string)
```
- Idempoten: bila sudah ada permintaan `pending`/`processing` untuk subjek yang sama, id yang ada dikembalikan.
- Galat: `forbidden` (42501: bukan pemilik anak / `p_child_id` kosong untuk `child`), `child_id_not_allowed_for_account_scope` (22023), `not_authenticated` (28000). Nilai `p_scope` selain `child|account` ditolak sebagai enum tidak valid (400).
- Status: `pending` -> `processing` -> `completed` | `failed` (lihat `deletion_request`). Penyelesaian tanpa foto: pg_cron tiap 15 menit; dengan foto: Edge Function `process-deletion` (terjadwal tiap 6 jam), jadi bisa memakan waktu jam. Setelah `account` selesai, akun dan sesi hilang.
- Bukti (di `deletion_request` setelah `completed`): `rows_deleted` (hitungan per tabel), `storage_objects_deleted`, `subject_hash`, `proof_sha256`, `executed_by`; `subject_id` dikosongkan.

### 3.3 `keepalive() -> integer`  (anon, authenticated, service_role)

Mengembalikan `1`. Dipakai ping terjadwal agar proyek Free tidak dijeda.

```bash
curl -sS -X POST "https://<project-ref>.supabase.co/rest/v1/rpc/keepalive" \
  -H "apikey: <anon-key>" -H "Content-Type: application/json" -d '{}'
# 200: 1
```

### 3.4 `facility_screening_summary(p_from date, p_to date) -> table`  (facility_viewer, admin)

Agregat bulanan per fasilitas, hanya `facility_code` milik pemanggil (admin: semua). Sel dengan < k penapisan disembunyikan (k = `max(aggregate_min_cell_size, 5)`); tidak ada complementary suppression (batasan yang diketahui). Setiap panggilan diaudit.

```json
// POST /rest/v1/rpc/facility_screening_summary   {"p_from":"2026-09-01","p_to":"2026-10-31"}
[
  {"facility_code":"PKM-3201-01","period_month":"2026-09-01","level":"yellow","screenings":12,"suppressed":false},
  {"facility_code":"PKM-3201-01","period_month":"2026-09-01","level":"red","screenings":null,"suppressed":true}
]
```
Galat: `forbidden` (42501: bukan `facility_viewer` berscope atau admin), `invalid_range` (22023: null, `p_to < p_from`, atau rentang > 400 hari). Rentang dibulatkan ke bulan penuh. `level` = `red|orange|yellow|green`.

### 3.5 RPC admin (peran `admin`, semua diaudit)

| RPC | Argumen | Hasil | Galat (SQLSTATE) |
|---|---|---|---|
| `admin_grant_role` | `p_user uuid`, `p_role app_role`, `p_facility_code text = null` | void | `forbidden` (42501); `unknown_user` (22023); `role_conflict_owns_children` (22023, `facility_viewer`/`admin` pada akun yang memiliki anak). `facility_viewer` wajib `p_facility_code` (CHECK, 23514). Pemberian `facility_viewer`/`admin` mencabut `guardian`/`kader` |
| `admin_revoke_role` | `p_user uuid`, `p_role app_role` | void | `forbidden`; `cannot_revoke_own_admin` (22023) |
| `admin_set_feature_flag` | `p_key text`, `p_enabled boolean`, `p_approval_ref text = null` | void | `forbidden`; `approval_ref_required` (22023: mengaktifkan `dataset_contribution` tanpa nomor persetujuan KEPK); `unknown_flag` (22023). `p_approval_ref` cocok `^[A-Za-z0-9./_-]{1,64}$` |
| `admin_get_child_record` | `p_child uuid`, `p_reason_code text` (`^[a-z][a-z0-9_]{2,39}$`) | jsonb | `forbidden`; `reason_code_required` (22023) |
| `admin_quota_usage` | – | jsonb `{checked_at, warn_pct, critical_pct, overall_status, metrics[{metric, unit, used, limit, pct, status}]}` | `forbidden`. Diaudit `quota_read` (hanya `overall_status`). Tanpa id/email/path |
| `admin_storage_usage` | - | jsonb (juga untuk `service_role`) | `forbidden` |

`admin_get_child_record` mengembalikan `{"child":{...tanpa owner_id},"consent":[...],"screening":[...],"referral":[...],"brushing_event_count":n,"image_upload_count":n}` (kolom pelaku dibuang); `null` bila anak tidak ada; pembacaan tetap tercatat di `audit_log`.
`admin_storage_usage` mengembalikan `{"buckets":[{"bucket_id":"...","objects":n,"bytes":n}],"storage_bytes":n,"storage_quota_bytes":1073741824,"storage_pct":x,"db_bytes":n,"db_quota_bytes":524288000,"db_pct":x,"dataset_photos_estimated_capacity_left":n}` (kuota Free `[PERLU VERIFIKASI]` di supabase.com/pricing).

Admin pertama dibuat manual oleh owner di SQL editor: `insert into public.user_role (user_id, role) values ('<uuid>', 'admin');`.

### 3.6 RPC khusus `service_role` (bukan untuk app)

`service_pending_deletions(p_limit=20)`, `service_execute_deletion(p_request_id, p_storage_objects_deleted)`, `service_retention_candidates(p_limit=100)`, `service_mark_images_deleted(p_upload_ids uuid[])`, `service_retention_sweep_db()`, `service_ml_audit(...)` (dipakai FastAPI); akun v2: `service_resolve_child_login`, `service_child_login_check`, `service_child_login_record`, `service_expired_proofs`, `service_mark_proof_object_removed`, `service_child_users_of`, `service_deletion_targets`. Dipanggil Edge Functions / server saja; pemanggil lain mendapat `forbidden` (42501) atau 401/403. Detail alur: [`EDGE_FUNCTIONS_AND_HOOKS.md`](EDGE_FUNCTIONS_AND_HOOKS.md).

```ts
const { data, error } = await supabase.rpc('sync_events', { batch });           // 3.1
const { data: reqId } = await supabase.rpc('request_deletion', { p_scope: 'account' });  // 3.2
```

### 3.7 RPC akun v2 (ADR 017; `authenticated`, `anon` ditolak)

Semua mengembalikan `forbidden` (42501) identik untuk "bukan milik Anda" dan "tidak ada". Kontrak lengkap, contoh, dan galat: [`ACCOUNTS_AND_FLOWS.md`](ACCOUNTS_AND_FLOWS.md).

| RPC | Untuk | Hasil |
|---|---|---|
| `my_account()` | semua | jenis akun, peran, `self_child_id`, `child_account`, flag, `limits` |
| `ensure_self_profile(p_child_id, p_birth_year, p_birth_month)` | remaja | `uuid` profil diri (idempoten) |
| `list_children()` | orang tua / remaja / kader | ringkasan beranda per anak (lencana permintaan & bukti) |
| `child_history(p_child, p_before, p_limit, p_kinds, p_from)` | orang tua / remaja / kader / anak | `{items[], next_before}` |
| `brushing_streak(p_child, p_tz)` | semua yang berhak | `{current, longest, last_day, today_done, week[], ...}` |
| `list_screening_requests(p_child, p_status, p_limit)` | orang tua / remaja / anak | permintaan (status efektif) |
| `ack_screening_request(p_id, p_action)` | orang tua / remaja | `seen` atau `cancel` |
| `list_brushing_proofs(p_child, p_before, p_limit)` | orang tua / remaja | klip hidup + `object_path` untuk `createSignedUrl` |
| `mark_proof_viewed(p_proof)` / `delete_brushing_proof(p_proof)` | orang tua / remaja | status baru |

## 4. Storage

Batas proyek: `storage.file_size_limit = 50MiB`.

| Bucket | Akses | Batas | Aturan |
|---|---|---|---|
| `manifests` | **publik baca** lewat URL; tulis hanya `admin` atau `service_role` | 50 MiB; MIME `application/json`, `application/octet-stream`, `text/plain` | berisi `manifest.json`, `manifest.sig`, `model/<v>/...`, `rules/<v>/...`, `recommender/<v>/...` (lihat `CLIENT_CONTRACTS.md`) |
| `dataset_photos` | privat; klien hanya **INSERT** | 512000 byte (500 KiB); MIME `image/jpeg` | lihat di bawah; tidak ada SELECT/UPDATE/DELETE untuk klien |
| `brushing_proofs` (ADR 017) | privat; **akun anak** hanya INSERT; **orang tua/remaja pemilik** hanya SELECT (signed URL) untuk klip hidup | 10 MiB; MIME `video/mp4`, `video/quicktime` | path persis `<guardian_id>/<child_id>/<proof_id>.mp4\|.mov` dari event `brushing_proof_registered`; tanpa UPDATE/DELETE/upsert; retensi 7 hari; rinci di bawah |

Syarat unggah `dataset_photos` (kebijakan `dentisai_dataset_photos_insert`; semua harus benar):

1. Pengguna login; nama objek persis tiga segmen `<uid>/<child-id>/<upload-id>.jpg` (juga `.jpeg`), `<uid>` = id akun sendiri, `<child-id>` dan `<upload-id>` UUID huruf kecil.
2. Anak dapat diakses pemanggil dan **consent (c) aktif**.
3. Flag `dataset_contribution` **hidup** (default mati sampai KEPK menyetujui).
4. Tidak ada permintaan hapus untuk anak itu.
5. Metadata (bila ada) tidak melebihi 512000 byte dan bertipe `image/jpeg`. Penghapusan EXIF dilakukan **di app sebelum unggah** (server tidak memeriksa EXIF).

Konsekuensi: tanpa `upsert` (menimpa ditolak), tidak bisa mengunduh/daftar/membuat signed URL foto sendiri, dan tidak bisa menghapus. Penghapusan hanya lewat penarikan consent (c) -> `retention-sweep`, TTL (`image_upload_ttl_days`, bawaan 365; `[PERLU VERIFIKASI]` sesuai protokol KEPK), atau `request_deletion`. Setelah unggah, daftarkan metadatanya dengan event `image_upload_registered` (objek harus sudah ada).

```bash
# Baca manifes (tanpa login)
curl -sS "https://<project-ref>.supabase.co/storage/v1/object/public/manifests/manifest.json" -o manifest.json
# Unggah foto dataset (hanya bila consent (c) + flag hidup)
curl -sS -X POST "https://<project-ref>.supabase.co/storage/v1/object/dataset_photos/<uid>/<child-id>/<upload-id>.jpg" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: image/jpeg" -H "x-upsert: false" --data-binary @photo.jpg
```
```ts
await supabase.storage.from('dataset_photos')
  .upload(`${uid}/${childId}/${uploadId}.jpg`, jpegArrayBuffer, { contentType: 'image/jpeg', upsert: false });
```

Syarat unggah `brushing_proofs` (kebijakan `dentisai_brushing_proofs_insert`; semua harus benar): akun anak aktif (tidak dinonaktifkan); path persis milik baris `brushing_proof` yang didaftarkan anak itu (status `uploaded`, belum kedaluwarsa); flag `brushing_proof_video` hidup; consent (d) aktif; tidak ada permintaan hapus; ukuran dan MIME sesuai pendaftaran. Baca (`dentisai_brushing_proofs_select`): hanya pemilik anak (segmen pertama path = `auth.uid()`), hanya klip hidup dengan consent (d) aktif; kader, fasilitas, dan admin tidak punya akses. Pembersihan berkas oleh Edge Function `retention-sweep` / `process-deletion`.

```ts
// Anak: setelah brushing_proof_registered berstatus applied
await supabase.storage.from('brushing_proofs')
  .upload(`${guardianId}/${childId}/${proofId}.mp4`, videoArrayBuffer, { contentType: 'video/mp4', upsert: false });
// Orang tua: tonton
const { data } = await supabase.storage.from('brushing_proofs').createSignedUrl(objectPath, 300);
```

## 5. Batas laju dan kuota

| Batas | Nilai | Sumber |
|---|---|---|
| Email Auth (seluruh proyek) | `email_sent = 30` per jam | `config.toml` |
| Email per pengguna | 1 per 60 detik (`max_frequency`) | `config.toml` |
| Sign-up + sign-in | `sign_in_sign_ups = 30` | `config.toml` (jendela 5 menit per IP menurut dokumentasi Supabase `[PERLU VERIFIKASI]`) |
| Verifikasi token | `token_verifications = 30` | idem |
| Refresh token | `token_refresh = 150` | idem |
| SMS / anonymous | 1 / 1 (nilai minimum API; fiturnya mati) | `config.toml` |
| Resend Free | 100 email/hari, 3000/bulan | runbook auth |
| Mailer | 5 email per penerima per jam, 300 permintaan per IP per jam | `smtp-handler` |
| `sync_events` | 1..100 event, 256 KiB per panggilan; 8 KiB per payload | migrasi 0500 + `app_setting` |
| PostgREST | `max_rows = 1000` | `config.toml` |
| Free plan | DB 500 MB, Storage 1 GB, egress 5 GB, proyek dijeda ±1 minggu tanpa aktivitas | `supabase/README.md` `[PERLU VERIFIKASI]` |

Respons 429 membawa `Retry-After` bila diberikan server; app melakukan backoff (lihat `SYNC_EVENTS.md`).
