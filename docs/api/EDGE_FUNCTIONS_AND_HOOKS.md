# Edge Functions, job terjadwal, dan hook email

> **Ringkasan (ID).** Tiga Edge Function milik Supabase (`process-deletion`, `retention-sweep`, `quota-check`) hanya untuk scheduler, bukan untuk app: diautentikasi dengan `Bearer <CRON_SECRET>`, gagal-tutup, dan responsnya hanya hitungan. Email Auth (konfirmasi, reset) dikirim oleh layanan mailer terpisah (`smtp-handler`) yang menerima **Send Email hook** Supabase bertanda tangan Standard Webhooks. Dua Edge Function lain (`child-accounts`, `child-login`, ADR 017) **dipanggil app** dengan JWT orang tua / anon key (§1.5–1.7). Dokumen ini adalah kontrak untuk ops dan backend; selain dua fungsi itu, app tidak memanggil satu pun dari ini secara langsung.

## 1. Edge Functions (Deno)

Sumber: `supabase/functions/{process-deletion,retention-sweep,quota-check,_shared}`. Konfigurasi: `[functions.*] enabled = true`, `verify_jwt = false` (gerbang JWT platform dimatikan karena pemanggilnya scheduler eksternal; fungsi memverifikasi sendiri). Pengecualian: `child-accounts` dan `child-login` memakai `verify_jwt = true` (§1.5–1.7).

Pemanggilan (hanya scheduler / PC tim):

```bash
curl -sS -X POST "https://<project-ref>.supabase.co/functions/v1/process-deletion" \
  -H "Authorization: Bearer <cron-secret>"
curl -sS -X POST "https://<project-ref>.supabase.co/functions/v1/retention-sweep" \
  -H "Authorization: Bearer <cron-secret>"
```

Rahasia/variabel fungsi (nama saja): `CRON_SECRET` (secret fungsi, **>= 32 karakter**), `SUPABASE_URL` dan `SUPABASE_SERVICE_ROLE_KEY` (disuntikkan platform). Kunci `service_role` **tidak** diterima sebagai kredensial pemanggil; ia hanya dipakai di dalam fungsi untuk memanggil RPC `service_*` dan Storage API.

### 1.1 Urutan pemeriksaan (sama untuk kedua fungsi; `_shared/service.ts`)

| Urutan | Kondisi | Status | Body |
|---|---|---|---|
| 1 | `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` kosong atau `CRON_SECRET` < 32 karakter | **503** | `{"error":"not_configured"}` |
| 2 | metode bukan `POST` | **405** | `{"error":"method_not_allowed"}` |
| 3 | `Authorization` != `Bearer <CRON_SECRET>` (perbandingan waktu-konstan; header hilang juga masuk sini) | **403** | `{"error":"forbidden"}` |
| 4 | lolos | 200/207/500 | lihat bawah |

Log dan respons hanya berisi **hitungan** (tanpa id, path, atau email).

### 1.2 `process-deletion` (hak hapus, FR-102)

Alur: `service_pending_deletions(p_limit=20)` mengembalikan maksimal 20 permintaan `pending`/`processing` beserta path objek `dataset_photos` milik subjek -> untuk tiap permintaan fungsi menghapus objek lewat Storage API (potongan 100 path) -> **video bukti sikat gigi** (ADR 017): `service_deletion_targets(p_request_id)` memberi path bucket `brushing_proofs` milik subjek, yang juga dihapus -> memanggil `service_execute_deletion(p_request_id, p_storage_objects_deleted)` (jumlah foto + video). RPC itu menolak selesai (`awaiting_storage_purge`) selama masih ada objek subjek di salah satu bucket, lalu menghapus baris anak (cascade, termasuk permintaan skrining, bukti, dan `child_account`) atau seluruh akun (anak, **akun anak beserta pengguna Auth-nya**, ledger, `guardian`, `user_role`, baris `auth.users`, dan entri `auth.audit_log_entries` yang memuat id/email akun), lalu menulis bukti dan `audit_log` ber-hash. Satu kegagalan (termasuk gagal membaca target video atau menghapus berkasnya) tidak menghentikan permintaan lain; permintaan itu dihitung `failed` dan eksekutor tidak dipanggil.

| Status | Body | Arti |
|---|---|---|
| 200 | `{"requests":3,"completed":2,"waiting":1,"failed":0,"objects_removed":5,"proof_objects_removed":2}` | semua diproses tanpa galat (`waiting` = RPC tidak `completed`, mis. masih ada objek) |
| 207 | sama, `failed` > 0 | sebagian permintaan gagal (diulang pada jalan berikutnya; setelah `deletion_max_attempts` (5) pg_cron menandai `failed`) |
| 500 | `{"error":"list_failed"}` | gagal membaca daftar |
| 503/405/403 | lihat 1.1 | |

Idempoten: permintaan yang sudah `completed` dilewati (`skipped`).

### 1.3 `retention-sweep` (TTL foto, foto yang ditarik, ledger)

Alur: `service_retention_candidates(p_limit=500)` -> baris `image_upload` dengan `status='uploaded' AND expires_at <= now()` atau `status='withdrawn'` -> hapus objek di `dataset_photos` -> `service_mark_images_deleted(ids)` (hanya menandai `deleted` bila objek benar-benar sudah hilang) -> **video bukti** (ADR 017): `service_expired_proofs(p_limit=200)` memberi klip kedaluwarsa (`proof_retention_days`, bawaan 7), berstatus `deleted`/`expired`, atau yang consent `d`-nya ditarik, ditambah objek yatim di bucket `brushing_proofs` (> 1 jam, tanpa baris) -> hapus objek -> `service_mark_proof_object_removed(proof_id)` (hanya bila objek benar-benar hilang; yang gagal dicoba lagi pada jalan berikut) -> `service_retention_sweep_db()` (hapus `sync_event` lebih tua dari `sync_event_retention_days`, bawaan 180; menandai permintaan skrining kedaluwarsa; menghapus baris bukti yang berkasnya sudah hilang; membuang catatan percobaan login anak > 1 hari).

| Status | Body | Arti |
|---|---|---|
| 200 | `{"candidates":4,"objects_removed":4,"images_marked_deleted":4,"proof_objects_removed":2,"proofs_marked_removed":2,"proof_purge_failed":false,"sync_event_deleted":120,"requests_expired":3,"proof_rows_deleted":2,"login_attempts_deleted":9}` | selesai |
| 207 | `sync_event_deleted` dkk. `null` | foto selesai tetapi sapu DB gagal |
| 207 | `proof_purge_failed: true` | pembersihan video gagal; sapu DB tetap berjalan, video dicoba lagi pada jalan berikut |
| 500 | `{"error":"list_failed"}` atau `{"error":"image_purge_failed","candidates":n,"removed":n}` | gagal; coba lagi pada jadwal berikut |
| 503/405/403 | lihat 1.1 | |

### 1.3b `quota-check` (kuota Supabase Free, item 5.4)

Alur: `service_quota_usage()` -> snapshot -> disaring (hanya kode & angka yang di-whitelist) -> respons. Tidak menulis apa pun.

| Status | Body | Arti |
|---|---|---|
| 200 | `{"overall_status":"ok","warn_pct":70,"critical_pct":90,"metrics":[{"metric":"db_size","unit":"mb","used":13.1,"limit":500,"pct":2.6,"status":"ok"}, ...]}` | snapshot terbaca; **juga 200 bila `critical`** (pemanggil yang memutuskan peringatan) |
| 500 | `{"error":"rpc_failed"}` atau `{"error":"bad_snapshot"}` | RPC gagal / bentuk tak valid |
| 503/405/403 | lihat 1.1 | |

Metrik: `db_size` (mb), `storage_size` (mb), `mau` (users), `auth_email_24h` (emails). `used`/`pct` bisa `null` dengan `status: "unknown"`.

### 1.4 Job di dalam database (pg_cron, bila ekstensi tersedia)

| Job | Jadwal | Isi |
|---|---|---|
| `dentisai-process-deletions` | `*/15 * * * *` | menyelesaikan permintaan hapus **tanpa** foto/video langsung di SQL; yang punya objek di `dataset_photos` atau `brushing_proofs` menunggu `process-deletion` (`failure_code='awaiting_storage_purge'`) |
| `dentisai-retention-db` | `17 3 * * *` | sapu ledger `sync_event`, kedaluwarsakan permintaan skrining dan bukti, buang baris bukti yang berkasnya sudah hilang, buang catatan percobaan login anak lama |

pg_cron tidak dapat membangunkan proyek Free yang sudah dijeda; apakah aktivitas internal mencegah penjedaan `[PERLU VERIFIKASI]`, karena itu ada ping eksternal.

### 1.5 `child-accounts` (app, orang tua mengelola akun anak — ADR 017 §2.2)

Berbeda dari fungsi di atas: **dipanggil app**, `verify_jwt = true`, `Authorization: Bearer <access token orang tua>`. Sumber: `supabase/functions/child-accounts/` (`handler.ts` logika, `ports.ts` supabase-js, `index.ts`).

Urutan pemeriksaan: `OPTIONS` → 204 (CORS) · metode bukan `POST` → 405 `method_not_allowed` · env kurang → 500 `internal` · header `Authorization` bukan `Bearer <token>` atau `my_account()` gagal/anon → 401 `unauthorized` · `account_type` bukan `guardian` (remaja, anak) → 403 `forbidden` (fungsi ini hanya untuk akun orang tua; tidak ada pemeriksaan peran lain sejak ADR 018) · body (JSON objek ≤ 4 KiB) tidak valid → 400 `invalid_body` · anak tidak terbaca lewat RLS dengan `owner_id` = pemanggil → 404 `child_not_found` (anak orang lain dan anak tak dikenal identik).

| `action` | Body | Sukses | Galat khusus |
|---|---|---|---|
| `create` | `{child_id, username, password}` | **201** `{"child_id":"<uuid>","username":"siti.kecil","created":true}` | 422 `invalid_username` (pola `^[a-z0-9][a-z0-9._-]{2,19}$` setelah trim+huruf kecil, ASCII saja, kata cadangan mis. admin/root/system/null) · 422 `weak_password` (≥ 8, kecil+besar+angka, ≤ 72 byte, tanpa karakter kontrol = `config.toml`) · 409 `child_already_has_account` · 409 `child_account_limit` (`max_child_accounts_per_guardian`, bawaan 6) · 409 `username_taken` (unik per orang tua). Bila trigger `auth.users` menolak (GoTrue hanya melapor "Database error"), fungsi memeriksa ulang: akun anak sudah ada → `child_already_has_account`, batas tercapai → `child_account_limit`, selain itu → `username_taken` (termasuk penolakan lain trigger, mis. permintaan hapus tertunda) |
| `reset_password` | `{child_id, password}` | 200 `{"ok":true}` | 422 `weak_password` · 404 `child_not_found` (belum punya akun) |
| `set_disabled` | `{child_id, disabled: bool}` | 200 `{"ok":true,"disabled":true}` | 404 `child_not_found`. Nonaktif: `child_account.disabled_at = now()` dulu, lalu ban Auth `876000h`; aktif: unban (`none`) dulu, lalu `disabled_at = null` (gagal di tengah = tetap terblokir) |
| `delete` | `{child_id}` | 200 `{"ok":true}` | 404 `child_not_found`. Menghapus pengguna Auth anak; `child_account` ikut terhapus (cascade); profil anak dan datanya tetap |

Lainnya: 500 `internal`. Bentuk galat selalu `{"error":"<kode>"}`. `create` memanggil `auth.admin.createUser({ email: "child-<child_id>@child.dentisai.invalid", password, email_confirm: true, app_metadata: { account_type: "child", guardian_id, child_id, username } })`; trigger `auth.users` membuat baris `child_account`. Respons dan log **tidak pernah** memuat kata sandi, email sintetis, atau id pengguna Auth anak.

### 1.6 `child-login` (app, login anak — ADR 017 §2.2)

Dipanggil app dengan **anon key** (`verify_jwt = true` menerimanya). Body: `{"parent_email": str, "username": str, "password": str, "captcha_token"?: str}`.

Alur: normalisasi (trim, huruf kecil) dan validasi bentuk → kunci `HMAC-SHA256(CHILD_LOGIN_HASH_PEPPER, lower(parent_email) + "|" + ip)` (hex; ip dari `cf-connecting-ip`, lalu hop pertama `x-forwarded-for`, lalu `"unknown"`) → `service_child_login_check(p_key_hash, p_max, p_window_s)` → `service_resolve_child_login(p_parent_email, p_username)` → **selalu** `POST {SUPABASE_URL}/auth/v1/token?grant_type=password` (header `apikey`; body `{email, password, gotrue_meta_security: {captcha_token}}`, field CAPTCHA dihapus bila tidak ada; email dummy `child-00000000-…@child.dentisai.invalid` bila tidak ada yang cocok) → `service_child_login_record(p_key_hash, p_ok, p_window_s)`; sukses juga mengisi `child_account.last_login_at`.

| Status | Body | Kapan |
|---|---|---|
| 200 | `{"access_token","refresh_token","token_type":"bearer","expires_in":3600,"expires_at":<unix>,"user":{"id":"<uuid>"},"child":{"username":"siti.kecil"}}` | sukses; app memanggil `supabase.auth.setSession({access_token, refresh_token})`. Tanpa email, tanpa data orang tua |
| 401 | `{"error":"invalid_credentials"}` | **seragam** untuk email orang tua tak dikenal, username tak dikenal, kata sandi salah, akun nonaktif/diban, CAPTCHA ditolak GoTrue, sesi milik pengguna lain. Dihitung sebagai gagal; dipadatkan ke ≥ 600 ms. Bila GoTrue tetap memberi sesi untuk akun nonaktif, sesi itu dicabut (`/auth/v1/logout`) |
| 400 | `{"error":"invalid_body"}` | body bukan JSON objek ≤ 8 KiB, email/username salah bentuk, kata sandi kosong/> 256 karakter, `captcha_token` bukan string (tidak dihitung) |
| 429 | `{"error":"too_many_attempts"}` + `Retry-After: <child_login_window_s>` | `child_login_max_attempts` (8) gagal dalam `child_login_window_s` (900 dtk) per kunci |
| 502 | `{"error":"upstream_unavailable"}` | GoTrue 5xx, 429 (batas laju Auth bersama per IP fungsi) atau tak terjangkau/timeout 8 dtk (tidak dihitung) |
| 500 | `{"error":"internal"}` | env/pepper kurang (pepper < 32 karakter), RPC throttle/resolve gagal (gagal-tutup) |
| 405 | `{"error":"method_not_allowed"}` | metode selain `POST`/`OPTIONS` |

### 1.7 Bersama (`child-accounts`, `child-login`)

- **CORS:** `OPTIONS` → 204; `Access-Control-Allow-Origin` hanya untuk origin di `ALLOWED_ORIGINS` (dipisah koma; bawaan `https://app.ryhnfir.dev`; `*` diabaikan, tidak pernah wildcard). App React Native tidak mengirim `Origin` dan tidak butuh CORS. Semua respons `Cache-Control: no-store`.
- **Env/secret (nama saja):** `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (disuntikkan platform); `CHILD_LOGIN_HASH_PEPPER` (secret fungsi, ≥ 32 karakter, hanya `child-login`); `ALLOWED_ORIGINS` (opsional).
- **Log:** satu baris JSON per permintaan berisi status, kode galat, dan tag alasan internal (mis. `throttle_failed`); **tidak pernah** email, username, kata sandi, token, id anak/pengguna, IP, atau kunci throttle (diuji).
- **Pengaturan (`app_setting`, dibaca service role):** `max_child_accounts_per_guardian` 6, `child_login_max_attempts` 8, `child_login_window_s` 900; nilai hilang/tidak valid → bawaan.

## 2. Workflow terjadwal (GitHub Actions)

Environment GitHub `development`. Lokasi: `.github/workflows/`.

| Workflow | Jadwal (UTC) | Memanggil | Secret |
|---|---|---|---|
| `scheduled-jobs.yml` job `process-deletion` | `17 */6 * * *` (tiap 6 jam) | `POST $SUPABASE_URL/functions/v1/process-deletion` | `SUPABASE_URL`, `CRON_SECRET` |
| `scheduled-jobs.yml` job `retention-sweep` | `43 20 * * *` (harian 03:43 WIB) | `POST $SUPABASE_URL/functions/v1/retention-sweep` | sama |
| `scheduled-jobs.yml` job `quota-check` | `7 1 * * *` (harian 08:07 WIB) | `POST $SUPABASE_URL/functions/v1/quota-check`; `warn` → anotasi `::warning`, `critical` → job gagal | sama |
| `keepalive.yml` | `17 3 */3 * *` (tiap 3 hari) | `POST <KEEPALIVE_URL>` = `.../rest/v1/rpc/keepalive` dengan anon key | `KEEPALIVE_URL`, `SUPABASE_ANON_KEY` (anon saja; kunci privileged ditolak `ping.py`) |

Perilaku `scheduled-jobs.yml`: `workflow_dispatch` punya input `job` (`both`, `process-deletion`, `retention-sweep`, `quota-check`); curl `--max-time 120 --retry 2`, `timeout-minutes: 5`; token dikirim lewat stdin (`-K -`) agar tidak muncul di daftar proses; job **sukses hanya bila HTTP 200** (207 membuat job merah agar kegagalan sebagian terlihat). Cron GitHub bisa tertunda atau dilewati, dan workflow terjadwal dimatikan GitHub setelah 60 hari tanpa aktivitas repo `[PERLU VERIFIKASI]` untuk repo privat. Pemilik harus mengisi secret `SUPABASE_URL` dan `CRON_SECRET` di GitHub (status pengisian `[PERLU VERIFIKASI]`).

## 3. Hook Send Email (Supabase Auth -> mailer)

Konfigurasi (`supabase/config.toml`):

```toml
[auth.hook.send_email]
enabled = "env(AUTH_SEND_EMAIL_HOOK_ENABLED)"   # lokal false (Mailpit); true hanya saat `config push`
uri     = "https://mailer.ryhnfir.dev/hooks/send-email"
secrets = "env(SEND_EMAIL_HOOK_SECRET)"          # format v1,whsec_<base64>; nilai sama dengan di mailer
```

Bila hook aktif, Supabase **tidak** memakai `[auth.email.smtp]`. Mailer: `../../../smtp-handler/` (Node.js 20+, Express, Nodemailer; README di repo itu). Hanya dibaca untuk dokumen ini.

### 3.1 Endpoint mailer

| Endpoint | Fungsi |
|---|---|
| `POST /hooks/send-email` | hook Supabase (butuh tanda tangan valid) |
| `GET /healthz` | `{"status":"ok","configured":true}`; `configured:false` bila ada variabel wajib yang kurang (tanpa rahasia). Tidak dibatasi laju IP |
| `GET /` | teks `DentisAI mailer is running.` |

Semua respons: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`; tanpa CORS; body maksimum 64 KB.

### 3.2 Permintaan dan tanda tangan (Standard Webhooks)

Header dari Supabase:

| Header | Isi |
|---|---|
| `webhook-id` | `^[A-Za-z0-9_:-]{1,128}$` |
| `webhook-timestamp` | detik Unix, `^\d{1,12}$`; selisih terhadap jam mailer maksimum **300 detik** (+/-) |
| `webhook-signature` | satu atau beberapa (dipisah spasi, maks 10) entri `v1,<base64>` |

Algoritma:

1. Kunci: ambil rahasia, buang awalan `v1,` lalu `whsec_`, **base64-decode** (harus >= 16 byte; kalau tidak mailer tidak terkonfigurasi -> 503).
2. Pesan tertandatangani: `"<webhook-id>.<webhook-timestamp>." + <body mentah>` (byte persis, sebelum JSON dibaca).
3. `expected = HMAC-SHA256(kunci, pesan)`; cocok bila salah satu entri `v1` ber-base64 sama dengan `expected` (perbandingan waktu-konstan; semua kandidat dievaluasi). Entri selain `v1` diabaikan.

Contoh pseudo-kode pembuat tanda tangan (untuk uji; mailer menyediakan `scripts/make-test-webhook.js`):

```js
const key = Buffer.from(secret.replace(/^v1,/, '').replace(/^whsec_/, ''), 'base64');
const sig = crypto.createHmac('sha256', key).update(`${id}.${ts}.`).update(rawBody).digest('base64');
// header: webhook-signature: v1,<sig>
```

Payload (setelah tanda tangan valid; JSON):

```json
{
  "user": { "email": "<email>", "new_email": "<email-baru, hanya email_change>" },
  "email_data": {
    "email_action_type": "signup",
    "token": "123456",
    "token_hash": "<hash>",
    "token_new": "654321",
    "token_hash_new": "<hash>",
    "redirect_to": "https://app.ryhnfir.dev/auth/callback"
  }
}
```

Validasi ketat: `user.email` (<= 254, pola konservatif) wajib; `email_action_type` salah satu dari `signup`, `invite`, `magiclink`, `recovery`, `email_change`, `reauthentication` (lainnya -> 400); `token`/`token_new` 6-10 digit; `token_hash`/`token_hash_new` `^[A-Za-z0-9_-]{1,256}$`; `redirect_to` <= 2048 karakter; `new_email` valid bila ada. Penerima **hanya** dari payload bertanda tangan.

Rencana pengiriman: `reauthentication` butuh `token` (email berisi kode 6 digit, tanpa tautan); `email_change` butuh `new_email` dan `token_hash` (dua email bila `token_hash_new` ada: alamat lama `email_change_current` dan alamat baru `email_change`); lainnya butuh `token_hash`. Tautan: `<SUPABASE_URL>/auth/v1/verify?token=<token_hash>&type=<email_action_type>[&redirect_to=<redirect>]`; `redirect_to` dibuang bila tidak lolos `ALLOWED_REDIRECT_PREFIXES` (batas segmen jalur, bukan sekadar awalan teks), sehingga Supabase memakai Site URL.

### 3.3 Respons dan urutan pemeriksaan

Bentuk galat: `{"error":{"http_code":<kode>,"message":"<teks>"}}`; sukses `200 {}`.

| Urutan | Kondisi | Status | `message` |
|---|---|---|---|
| 1 | variabel wajib kurang / rahasia tidak valid | 503 | `Service is not configured` |
| 2 | batas IP terlampaui (`RATE_LIMIT_IP_PER_HOUR`, bawaan 300/jam) | 429 + `Retry-After` | `Too many requests` |
| 3 | body > 64 KB | 413 | `Payload too large` |
| 4 | body kosong | 400 | `Invalid request` |
| 5 | header hilang/cacat, selisih waktu > 300 dtk, tanda tangan salah | 401 | `Invalid webhook signature` |
| 6 | `webhook-id` sudah pernah diproses sukses (TTL 10 menit, maks 5000) | 200 `{}` | tidak mengirim ulang |
| 7 | JSON tidak valid / payload tidak valid / rencana pengiriman gagal | 400 | `Invalid request body` |
| 8 | batas per penerima (`RATE_LIMIT_EMAIL_PER_HOUR`, bawaan 5/jam per alamat) | 429 + `Retry-After` | `Too many emails for this recipient` |
| 9 | gagal kirim SMTP atau melewati tenggat | 503 | `Failed to send email` |
| 10 | sukses | 200 `{}` | |
| - | metode selain POST di jalur hook | 405 + `Allow: POST` | `Method not allowed` |
| - | jalur lain | 404 | `Not found` |
| - | galat tak terduga | 500 | `Internal error` |

Batas laju disimpan di memori (hilang saat restart, satu instance). Untuk rencana dua email, semua penerima diperiksa dulu (semua-atau-tidak-sama-sekali).

### 3.4 Timeout

Supabase memberi hook sekitar **5 detik** (dokumentasi Supabase, diperiksa pemilik mailer 2026-10-10; `[PERLU VERIFIKASI]`). Mailer memakai batas: koneksi 3000 ms, greeting 3000 ms, socket 4000 ms, tenggat total pengiriman **4200 ms** (`SMTP_*_MS`); jangan dinaikkan banyak. Menurut README mailer, Supabase hanya mengulang 429 dan 503 (butuh `Retry-After`), dan status 400/403 dari hook dapat dilaporkan Supabase sebagai 500 kepada pengguna `[PERLU VERIFIKASI]`.

### 3.5 Variabel lingkungan mailer (nama saja)

Wajib: `SEND_EMAIL_HOOK_SECRET`, `SUPABASE_URL`, `MAIL_FROM`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`. Opsional: `SMTP_PORT` (bawaan 587; Resend 465), `SMTP_SECURE`, `SMTP_REQUIRE_TLS`, `ALLOWED_REDIRECT_PREFIXES` (disarankan; kosong = semua `redirect_to` dibuang), `PORT` (5000), `TRUST_PROXY`, `RATE_LIMIT_EMAIL_PER_HOUR`, `RATE_LIMIT_IP_PER_HOUR`, `RATE_LIMIT_WINDOW_MS`, `SMTP_CONNECTION_TIMEOUT_MS`, `SMTP_GREETING_TIMEOUT_MS`, `SMTP_SOCKET_TIMEOUT_MS`, `SMTP_SEND_DEADLINE_MS`. Di sisi Supabase CLI/`backend/.env`: `AUTH_SEND_EMAIL_HOOK_ENABLED`, `SEND_EMAIL_HOOK_SECRET`, `AUTH_SMTP_ENABLED`, `AUTH_CAPTCHA_ENABLED`, `AUTH_SITE_URL`, `AUTH_REDIRECT_URL`, `RESEND_API_KEY`, `CAPTCHA_SECRET`.

### 3.6 Templat dan privasi

Tujuh templat Indonesia (tautan atau kode saja, tanpa data anak dan tanpa klaim kesehatan): `signup`->`confirmation`, `recovery`->`recovery`, `magiclink`->`magic_link`, `invite`->`invite`, `email_change`->`email_change` (alamat baru) dan `email_change_current` (alamat lama bila "Secure email change" aktif), `reauthentication` (kode 6 digit). Templat `invite` dan `email_change_current` belum ditinjau pemilik/drg. Log mailer hanya id pesan, jenis aksi, status, jumlah, durasi, kode galat SMTP; tidak pernah email, token, atau tautan.

Hal belum terverifikasi dari kode (`[PERLU VERIFIKASI]`): field `user.new_email` pada `email_change` tidak ada di skema payload yang didokumentasikan Supabase; pemetaan `token_hash`/`token_hash_new` pada `email_change`; panjang OTP; rotasi rahasia ganda; apakah hook dan alur PKCE bekerja bersama pada proyek nyata.

## 4. Halaman statis pendukung

`web/` (Cloudflare Pages, `app.ryhnfir.dev`): `auth/callback/` meneruskan query + fragment ke `dentisai://auth/callback`; `captcha/` menjalankan Turnstile untuk WebView (CSP dan `_headers` ada di folder itu). Detail alur: [`SUPABASE_API.md`](SUPABASE_API.md) bagian 1.2-1.3.
