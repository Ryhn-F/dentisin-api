# DentisAI Backend

Backend untuk **DentisAI**, aplikasi mobile **skrining** (bukan diagnosis) karies dan kelainan rongga mulut untuk orang tua, remaja, dan akun anak buatan orang tua. Folder ini berdiri sendiri (ADR 009): semua yang bukan UI ada di sini. Aplikasi Expo ada di repo/folder `app/`, dokumen produk bersama (PRD, ADR, riset) di `docs/` pada root monorepo.

> **Status: dev saja, aturan masih draf.** Belum ada produksi, belum ada drg yang mengesahkan aturan triase/rekomendasi, dan model belum divalidasi klinis (`NOT_VALIDATED`). Semua keluaran berbunyi "indikasi / perlu diperiksa" dan tidak boleh dipakai pada anak sebelum ditinjau drg dan KEPK.

## Arsitektur

```
App (Expo)
 ├── Supabase (Free, Singapore)  ← auth, CRUD, consent, sync, Storage       [supabase/]
 │     Postgres + RLS · RPC · Storage · Edge Functions · pg_cron
 └── FastAPI ML/AI               ← detektor foto, triase, rekomendasi        [api/]
       memverifikasi JWT Supabase; tanpa CRUD dan tanpa auth sendiri
```

Pembagian tugas:

- **Supabase** menangani auth (email + password), data profil anak, consent, riwayat, `sync_events`, video bukti sikat gigi, hak hapus, dan retensi. RLS adalah pertahanan utama.
- **FastAPI** hanya untuk ML dan integrasi AI. Rute utama untuk app adalah `POST /v1/screening/photo` (ADR 020): foto yang lolos quality gate di HP dikirim bersama jawaban kuesioner, lalu server menjalankan tanda bahaya → quality gate → detektor YOLOX-Nano → triase → rekomendasi. Foto hanya di memori selama permintaan. Hasil server adalah hasil yang ditampilkan.
- **Aturan klinis** (`triage/`, `recommender/`, `ml/brushing/`) berupa tabel YAML/JSON yang bisa dibaca drg, dengan mesin referensi Python dan kasus uji tabel yang sama dengan runtime TypeScript di app. FastAPI memakai mesin referensi ini sebagai library; tidak ada logika klinis di dalam service.
- **Model** (`ml/`) dilatih di luar repo (Kaggle/Colab untuk data publik, PC tim untuk data lokal). Bundel `ml/exports/0.1.0-dev.2` berisi YOLOX-Nano float32 dengan dua kelas: `caries_permanent` dan `caries_primary` (ADR 013, 014). Kotak hanya untuk overlay; ukuran kotak tidak pernah masuk triase.
- **Chatbot ditunda** (ADR 008). Tidak ada layanan berbayar di jalur kritis.

Prinsip yang selalu berlaku: skrining bukan diagnosis, red flag dari kuesioner selalu menang, foto rongga mulut adalah data kesehatan anak (minimisasi, tanpa PII di log), dan tidak ada Ultralytics/YOLOv8 atau pustaka AGPL/GPL di kode yang dikirim (ADR 001, 008).

## Peta folder

| Folder | Isi | Dokumen |
|---|---|---|
| [`supabase/`](supabase/README.md) | Migrasi, RLS, uji pgTAP, seed sintetis, template email, Edge Functions (`child-accounts`, `child-login`, `process-deletion`, `retention-sweep`, `quota-check`) | [`supabase/README.md`](supabase/README.md) |
| [`api/`](api/README.md) | FastAPI ML/AI: `/v1/screening/*`, `/v1/ml/*`, health | [`api/README.md`](api/README.md), [`api/docs/API.md`](api/docs/API.md) |
| [`triage/`](triage/README.md) | `rules.yaml`, skema, mesin referensi, `dist/` (aturan + JSON Schema bertanda checksum), 114 kasus uji | [`triage/README.md`](triage/README.md) |
| [`recommender/`](recommender/README.md) | Rekomendasi pasta gigi berbasis kandungan (fluoride saja di v1), bundel `dist/recommender.json` | [`recommender/README.md`](recommender/README.md) |
| [`ml/`](ml/README.md) | `data/` (gerbang lisensi, skrip), `detection/` (konversi, latih, eval, ekspor, quality gate, bake-off), `brushing/` (aturan sikat gigi dan check-in), `exports/` (bundel model) | [`ml/README.md`](ml/README.md) |
| [`tools/`](tools/README.md) | Penandatanganan dan verifikasi manifes Ed25519, bundel kontrak untuk app, backup terenkripsi, ekspor dataset, keep-alive, TTS build-time, lint frasa terlarang, scan rahasia | [`tools/README.md`](tools/README.md) |
| [`web/`](web/README.md) | Situs statis `app.ryhnfir.dev`: callback tautan email dan widget CAPTCHA Turnstile | [`web/README.md`](web/README.md) |
| [`docs/`](docs/api/README.md) | Kontrak API untuk tim app (`docs/api/`), artefak kepatuhan (`docs/regulatory/`), runbook, gaya bahasa | [`docs/api/README.md`](docs/api/README.md) |
| `.github/workflows/` | `ci`, `keepalive`, `scheduled-jobs`, `contracts-release` | lihat di bawah |

## Fitur saat ini

- **Akun v2** (ADR 017, 018): peran `guardian` (orang tua dan remaja 15-19 tahun), `child` (akun anak buatan orang tua, login lewat Edge Function), dan `admin`. Peran kader, mode kelas, dan rekap fasilitas sudah dihapus.
- **Skrining**: kuesioner red flag, triase, rekomendasi pasta gigi, surat rujukan (template di app), dan skrining foto lewat detektor server.
- **Sikat gigi**: aturan check-in pagi 06-09 dan malam 20-23 (zona waktu Asia/Jakarta), streak jalan dengan satu jendela, video bukti 7 hari yang hanya dapat ditonton pemilik profil.
- **Sinkronisasi**: RPC `sync_events` idempoten per `event_id`.
- **Privasi**: hak hapus nyata (`deletion_request`, `process-deletion`), retensi foto, `audit_log` untuk akses data sensitif, `dataset_contribution` default mati sampai KEPK menyetujui.
- **Distribusi aturan/model**: manifes bertanda tangan Ed25519 (kunci privat offline), bundel kontrak berversi untuk uji paritas di app (`contracts-v*`).
- **Operasi Free plan**: keep-alive terjadwal, cron hak hapus/retensi/pemantauan kuota, backup `pg_dump` terenkripsi `age`.

## Mulai cepat

Prasyarat: Python 3.12 dengan [`uv`](https://docs.astral.sh/uv/), Docker Desktop untuk Supabase lokal, Deno untuk Edge Functions, Node 22.18+ untuk tes dekoder TypeScript.

```bash
cp .env.example .env              # isi sendiri; .env tidak boleh di-commit

# Supabase lokal + uji RLS
npx supabase@2.120.0 start && npx supabase db reset && npx supabase test db

# Aturan dan mesin referensi
cd triage && uv sync && uv run pytest -q
cd ../recommender && uv sync && uv run pytest -q

# FastAPI ML/AI
cd api && uv sync --extra vision
uv run pytest -q                  # cakupan minimal 90%
uv run --env-file ../.env uvicorn app.main:app --reload --no-access-log

# Edge Functions
cd supabase/functions && deno task ci
```

Detail tiap modul ada di README masing-masing. Di Windows lokal, runtime LiteRT (`--extra detector`) tidak tersedia, sehingga detektor tidak termuat dan rute foto menjawab 503 `detector_unavailable`. Image Docker memuat runtime dan bundel model:

```bash
docker build -f api/Dockerfile -t dentisai-api .
docker run --rm -p 8000:8000 --env-file .env dentisai-api
```

## Konfigurasi

Semua variabel ada di [`.env.example`](.env.example). Yang menentukan perilaku FastAPI:

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `ENABLE_SCREENING_API` | `false` | Menghidupkan `/v1/screening/*` (mati = 404) |
| `ENABLE_SCREENING_PHOTO` | `false` | Menghidupkan `POST /v1/screening/photo`; butuh `ENABLE_SCREENING_API` juga |
| `DETECTOR_MODEL_DIR` | kosong | Folder bundel `{model.tflite, labels.json}`, mis. `ml/exports/0.1.0-dev.2`; kosong atau gagal dimuat = 503 |
| `ENABLE_CLOUD_INFER` | `false` | Menghidupkan `/v1/ml/infer` dan `/v1/screening/analyze` (QA atas foto dataset berizin) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` | kosong | Akses Supabase dan verifikasi JWT |

Kunci `service_role` hanya untuk server dan PC tim, tidak pernah di app atau CI publik.

## CI dan rilis

- **`ci`**: scan rahasia, lint frasa terlarang, shellcheck, pytest untuk `tools`, `tools/manifest`, `triage`, `recommender`, `ml/detection`, `ml/brushing`, dan `api`, tes Edge Functions (Deno), serta paritas dekoder TypeScript.
- **`keepalive`** dan **`scheduled-jobs`**: menjaga proyek Free tidak dijeda dan menjalankan `process-deletion`, `retention-sweep`, `quota-check`. Rahasia ada di GitHub Environment `development`.
- **`contracts-release`**: tag `contracts-v<versi>` membangun dan menerbitkan bundel kontrak untuk repo app.

## Hal yang belum selesai

- Aturan triase dan rekomendasi belum disahkan drg (0 dari 114 kasus `graded_by_drg`); semua nilai klinis masih draf atau `PLACEHOLDER`.
- Drg pembimbing, mitra puskesmas/sekolah, dan KEPK belum ada, jadi tidak ada klaim klinis dan tidak ada uji pada anak.
- Model `0.1.0-dev.2` belum divalidasi pada anak Indonesia, dan lisensi bobot pra-latih diterima sebagai risiko oleh pemilik produk (ADR 013, 014).
- FastAPI belum di-deploy (target Azure; ADR 020 menyebut App Service, catatan di `api/README.md` menyebut Container Apps). Region dan transfer lintas negara (UU PDP) masih `[PERLU VERIFIKASI]`, dan jalur foto membuat fitur ini bergantung pada Azure.
- Spike S1 (latensi dan memori model di HP) belum selesai; bila selesai, deteksi dapat kembali on-device.
