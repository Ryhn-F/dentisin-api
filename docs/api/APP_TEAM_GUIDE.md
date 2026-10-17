# Mulai di sini — panduan integrasi backend untuk tim app

> **Ringkasan (ID).** Satu halaman untuk engineer app (Expo/TypeScript): apa yang disediakan backend, mana yang sudah bisa dipakai hari ini, mana yang masih draf, urutan integrasi yang disarankan, dan jebakan yang sudah diketahui. Rincian ada di dokumen yang ditautkan; angka dan perilaku di sini diperiksa terhadap kode/uji backend dan terhadap proyek dev yang hidup (cek langsung 2026-10-17).
> Lingkungan: **dev saja** (belum ada produksi). Semua nilai rahasia (`anon key`, `service_role`, kunci penandatangan) tidak ada di dokumen; `service_role` **tidak pernah** masuk app.

## 1. Peta: kebutuhan app → mekanisme → dokumen → status

| Kebutuhan app | Mekanisme backend | Dokumen | Status 2026-10-17 |
|---|---|---|---|
| Daftar, masuk, reset sandi | Supabase Auth: email + sandi (≥ 8, huruf kecil+besar+angka), konfirmasi email, CAPTCHA Turnstile, tautan email → `app.ryhnfir.dev/auth/callback` → `dentisai://` | [`SUPABASE_API.md`](SUPABASE_API.md) §1 | Dev **hidup**: pendaftaran terbuka dan email wajib dikonfirmasi (`/auth/v1/settings`: `disable_signup=false`, `mailer_autoconfirm=false`). Saklar `AUTH_SMTP/CAPTCHA/SEND_EMAIL_HOOK_ENABLED` di `.env` backend bernilai `true`; apakah sudah di-push ke proyek dev `[PERLU VERIFIKASI]` (CAPTCHA aktif = `signUp` tanpa token ditolak). **Belum terbukti**: daftar → email tiba → ketuk tautan → login di HP nyata |
| Simpan hasil ke server | RPC `sync_events` (outbox idempoten) | [`SYNC_EVENTS.md`](SYNC_EVENTS.md) | **Selesai & teruji** di dev (pgTAP, HTTP, e2e) |
| **Akun v2**: pilih remaja/orang tua, orang tua membuat akun anak, login anak, streak + video bukti, permintaan skrining ke orang tua, riwayat | `signUp` + `options.data.account_type`; Edge Function `child-accounts` / `child-login`; `sync_events` tipe baru; RPC `my_account`, `list_children`, `child_history`, `brushing_streak`, `list_screening_requests`, `list_brushing_proofs` dll.; bucket `brushing_proofs` | [`ACCOUNTS_AND_FLOWS.md`](ACCOUNTS_AND_FLOWS.md) (alur, galat, daftar periksa UI), ADR 017 | Kode + uji pgTAP/Deno **selesai**; **belum di-deploy ke proyek dev** dan belum diuji e2e/HP. Butuh tinjauan hukum sebelum rilis publik (video anak) |
| Hak hapus, consent, data anak | tabel + RLS, RPC `request_deletion`, Edge Function | `SUPABASE_API.md` §2–3, [`EDGE_FUNCTIONS_AND_HOOKS.md`](EDGE_FUNCTIONS_AND_HOOKS.md) | **Selesai** di dev |
| Deteksi foto (on-device) | bundel model **YOLOX-Nano float32** + dekoder TypeScript acuan | [`MODEL_BUNDLE.md`](MODEL_BUNDLE.md), [`examples/detector/`](examples/detector/) | Bundel **dev** `0.1.0-dev.2` siap diberikan; bundel resmi menyusul. Dekoder TS lulus vektor paritas + fixture model nyata. **Latensi HP belum diukur** |
| Triase | `triage/dist/rules.json` + engine yang di-port ke TS | [`CLIENT_CONTRACTS.md`](CLIENT_CONTRACTS.md) §2 | **DRAFT**: nilai klinis `PLACEHOLDER` (diisi dari pustaka di Fase 3). Struktur masukan/keluaran stabil |
| Rekomendasi pasta gigi | `recommender/dist/recommender.json` | `CLIENT_CONTRACTS.md` §3 | **DRAFT**; `needs_review` dan produk contoh |
| Quality gate foto | rumus integer + vektor | `CLIENT_CONTRACTS.md` §6 | Algoritma stabil; **ambang masih `PLACEHOLDER`** |
| Pembaruan artefak | manifes Ed25519 + bucket publik `manifests` | `CLIENT_CONTRACTS.md` §5, [`../../tools/manifest/SPEC.md`](../../tools/manifest/SPEC.md) | Spesifikasi + vektor siap. **Belum ada manifes terbit** — pakai rilis bawaan (bundled) |
| Kontrak antar-repo | bundel berversi `dentisai-contracts` (zip + `CONTRACTS.json`) | `CLIENT_CONTRACTS.md` §7 | Pembuat sudah ada; **rilis pertama (`contracts-v0.1.0`) belum dibuat** |
| Audio TTS | berkas audio build-time (Gemini) | `../../tools/README.md` | Naskah menunggu persetujuan pemilik produk |
| FastAPI `/v1/screening` | triase+rekomendasi sisi server | [`../../api/docs/API.md`](../../api/docs/API.md) | **Bukan jalur MVP**: mati secara default, belum di-deploy. App tidak memanggilnya |

## 2. Keputusan produk yang mengubah kode app

| ADR | Isi | Akibat di app |
|---|---|---|
| **013** | Model terpilih YOLOX-Nano **float32** (INT8 gagal) | Masukan **float32 BGR 0–255**, bukan uint8; keluaran satu tensor logit `[1,3549,7]` (bagian MODEL_BUNDLE) |
| **014** | Kelas hanya `caries_permanent` / `caries_primary`; kotak = seluruh gigi | Tidak ada "berlubang/belum berlubang"; **jangan** turunkan keparahan dari ukuran kotak; triase hanya menerima `{class, confidence}` |
| **015** | Aturan klinis bersumber pustaka (tanpa tanda tangan dokter gigi untuk MVP); dataset tunggal Ahmed 2025 | Layar Tentang/hasil memuat teks transparansi: aturan dari pedoman terbitan, model dilatih di dataset publik, **belum divalidasi klinis**. Dilarang menulis "divalidasi/disetujui dokter gigi" |
| **017** | Model akun v2: `guardian` / `teen` / `child`; anak hanya streak + minta skrining; skrining oleh orang tua; video bukti 7 hari (consent `d`) | Beranda berbeda per `my_account().account_type`; login anak lewat `child-login` (bukan `signIn`); password anak dibuat orang tua; kompres video di klien; polling untuk permintaan baru; teks consent `d` menunggu tinjauan hukum |
| **002** | Posisi regulasi | Fitur deteksi/triase di rilis publik mengikuti ADR 002; build internal boleh menyalakan modul medis lewat `feature_flag` |
| 008/009 | Repo tertutup, Rp0, FastAPI tidak di jalur kritis | Tidak ada layanan berbayar/awan di jalur utama; semua hitungan inti offline |

## 3. Urutan integrasi yang disarankan

1. **Klien Supabase** (env publik: `SUPABASE_URL`, `SUPABASE_ANON_KEY`; sesi di penyimpanan aman). Kode: `SUPABASE_API.md` §1.4. Uji cepat tanpa kode: bagian 6.
2. **Auth** + CAPTCHA (WebView Turnstile, `SUPABASE_API.md` §1.3) + penanganan deep link (§1.2). Uji pada proyek dev dengan akun baru; ini satu-satunya langkah yang butuh email sungguhan. Tambahkan layar **pilih peran** saat daftar (`account_type`) dan routing beranda dari `my_account()` ([`ACCOUNTS_AND_FLOWS.md`](ACCOUNTS_AND_FLOWS.md)).
3. **Outbox → `sync_events`**: skema event di `SYNC_EVENTS.md`; kirim ≤ 50 event per panggilan, satu batch dalam penerbangan, `event_id` tetap saat retry.
   - **Akun v2** (setelah 2–3 jalan): alur orang tua (tambah anak → `child-accounts`), login anak (`child-login`), beranda anak (streak, minta skrining), video bukti (kompres, unggah), beranda orang tua (`list_children`, permintaan, pemutar bukti), riwayat. Urutan dan kontrak di `ACCOUNTS_AND_FLOWS.md` §2 dan §10. Fitur ini baru bisa diuji nyata setelah backend men-deploy migrasi `20261017*` dan kedua Edge Function ke proyek dev.
4. **Model**: terima bundel dev dari backend → verifikasi hash → jalankan `golden_runtime.json` di HP (keluaran mentah harus cocok) → pasang `decode.ts` + tesnya → jalankan foto uji milik tim sendiri. Jangan memakai foto anak.
5. **Triase + rekomendasi + quality gate**: port engine ke TS; uji paritas dengan `cases.json` / vektor (bundel kontrak). Jalankan dengan parameter uji sampai Fase 3 selesai; keluaran tetap berlabel DRAFT.
6. **Consent, hak hapus, mode anak tanpa akun** (offline penuh, tidak mengirim apa pun).
7. **Manifes bertanda tangan** (verifikasi 13 langkah) setelah kunci penandatangan dibuat (rencana 4.1/4.2). Sebelum itu, selalu jalankan rilis bawaan.

## 4. Jebakan yang sudah diketahui

| Gejala | Penyebab / tindakan |
|---|---|
| `GET …/manifests/manifest.json` memberi **HTTP 400** dengan isi `{"statusCode":"404","error":"not_found",…}` | Belum ada manifes terbit. Supabase Storage memakai 400 untuk objek yang tidak ada. Perlakukan **semua** status bukan-200 sebagai "tidak ada pembaruan" dan pakai rilis bawaan |
| Login anak selalu `401 invalid_credentials` | Pesan sengaja seragam. Periksa: email **orang tua** (bukan email anak), username huruf kecil, password yang dibuat orang tua untuk anak itu, akun anak tidak dinonaktifkan, Turnstile terkirim. Banyak percobaan → `429` + `Retry-After` |
| Bukti video: `consent_required` / `forbidden` | Orang tua belum memberi consent `b` **dan** `d`, atau akun anak dinonaktifkan; cek `my_account().child_account.proof_allowed` |
| `signUp` berhasil tetapi `session` kosong | Konfirmasi email aktif: sesi baru ada setelah pengguna membuka tautan. `signInWithPassword` sebelum itu gagal (`email_not_confirmed`) |
| Baca tabel tanpa login → HTTP 401, kode `42501` | Wajar: anon hanya boleh Storage `manifests`, Auth, dan RPC `keepalive`. Semua tabel butuh JWT pengguna |
| Pendaftaran gagal `captcha_failed` | CAPTCHA aktif di server: setiap `signUp` / `signIn` / `resetPasswordForEmail` wajib membawa `captchaToken` sekali pakai |
| 429 | Batas email (1 per 60 detik per pengguna; 30/jam proyek) atau laju Auth; backoff + hormati `Retry-After` (`SUPABASE_API.md` §5) |
| `rejected` pada hasil `sync_events` | Terminal kecuali `internal_error`; jangan kirim ulang dengan `event_id` sama (`SYNC_EVENTS.md` §6) |
| Hasil model tampak "kosong" pada foto uji sintetis | Wajar: ambang `caries_permanent` 0,59. Model tidak dilatih pada pola sintetis; pakai fixture golden hanya untuk uji aritmetika |
| Dokumen lama menyebut model INT8 / `uint8` | Usang sejak ADR 013; ikuti `MODEL_BUNDLE.md` |

## 5. Aturan yang tidak boleh dilanggar app

1. Keluaran selalu "**indikasi / perlu diperiksa**" + rujukan; tidak ada diagnosis dan tidak ada angka akurasi.
2. **Red flag menang** atas model, chatbot, dan logika lain; berasal dari kuesioner gejala.
3. Foto anak tidak diunggah kecuali consent (c) **dan** flag `dataset_contribution` hidup (tetap mati sampai ada persetujuan etik); mode anak tanpa akun tidak mengirim apa pun.
4. `service_role`, kunci penandatangan, dan rahasia lain tidak boleh ada di app/CI publik. App hanya memegang `anon key`.
5. Log tanpa PII/foto/`child_id`/email.
6. Teks UI kelas 4 SD untuk mode anak; kode dan komentar Inggris.

## 6. Uji cepat proyek dev (tanpa kode)

Ganti placeholder; jangan menempel nilai asli ke chat/repo.

```bash
# 1. Proyek hidup + RPC anon (jawab: 1)
curl -sS -X POST "https://<project-ref>.supabase.co/rest/v1/rpc/keepalive" \
  -H "apikey: <anon-key>" -H "Authorization: Bearer <anon-key>" -H "Content-Type: application/json" -d '{}'

# 2. Konfigurasi Auth publik (signup terbuka, konfirmasi email wajib)
curl -sS "https://<project-ref>.supabase.co/auth/v1/settings" -H "apikey: <anon-key>"

# 3. Manifes (sekarang: HTTP 400 NoSuchKey = belum terbit)
curl -sS -i "https://<project-ref>.supabase.co/storage/v1/object/public/manifests/manifest.json" | head -1
```

Uji model dan dekoder (dari `backend/`, Node ≥ 22.18):

```bash
node --test "docs/api/examples/detector/*.test.ts"      # 10 uji: vektor paritas + fixture model nyata
```

## 7. Cara melapor dan meminta perubahan kontrak

- Perubahan kontrak (migrasi, aturan, model) dicatat di `../regulatory/CHANGELOG.md` dan ADR; perubahan dokumen ini di [`CHANGELOG.md`](CHANGELOG.md).
- Kontrak berubah **hanya aditif** dalam satu versi mayor (field baru opsional). Bila app butuh field/perilaku baru, tulis kasus uji paritas dulu, lalu minta perubahan; jangan menambal di sisi app.
- Temuan yang tidak jelas ditandai `[PERLU VERIFIKASI]` di dokumen terkait; laporkan hasil uji nyata (HP, versi Android, log tanpa PII) agar bisa ditutup.

## 8. Yang belum ada (jangan menunggu di sisi app)

| Item | Pemilik | Dampak |
|---|---|---|
| Deploy akun v2 ke proyek dev (`db push`, rahasia `CHILD_LOGIN_HASH_PEPPER`, `functions deploy`) | pemilik produk + backend | Sampai dilakukan, login anak/bukti video belum bisa dicoba dari app |
| Tinjauan hukum video anak/remaja mendaftar sendiri (`OPEN_LEGAL_QUESTIONS.md`) | pemilik produk | Menentukan apakah fitur video boleh dihidupkan di rilis publik |
| Latensi/memori di HP (S1) — protokol: [`../runbooks/spike-s1-latency.md`](../runbooks/spike-s1-latency.md) | tim app + pemilik produk | Menentukan model final dan presisi (float32/float16) |
| Nilai klinis dari pustaka (Fase 3) | backend | Triase/rekomendasi tetap DRAFT sampai `release_check` hijau |
| Kunci penandatangan + manifes pertama (4.1/4.2) | pemilik produk + backend | Pembaruan artefak lewat manifes belum bisa dipakai |
| Rilis bundel kontrak `contracts-v0.1.0` | pemilik produk (tag) | App belum dapat mengunduh kontrak berversi; sementara minta salinan ke backend |
| Deploy FastAPI | — (pasca-MVP) | Tidak dibutuhkan jalur MVP |
