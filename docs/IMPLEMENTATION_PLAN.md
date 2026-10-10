# Rencana Implementasi Backend DentisAI — per Fase

> Disusun 2026-10-09. Acuan: `PRD.md` §13 (timeline 10 minggu), `CLAUDE.md` backend §15 (pertanyaan terbuka), ADR 001/005/008/009/010. Minggu 1 = 8–14 Okt 2026.
> Legenda: **[Selesai]** terbukti dijalankan · **[Ditulis]** ada kode tetapi belum terbukti di lingkungan asli · **[Terblokir]** menunggu pihak lain · 🧑 pemilik produk · 🦷 drg pembimbing · ⚖ penasihat hukum · 📱 tim frontend · 🤖 dikerjakan Claude (Opus high untuk area berat, Sonnet high untuk area kecil, Opus max hanya untuk bug sulit).

## Status per 2026-10-10
| Item | Status |
|---|---|
| 1.1 pgTAP asli, 1.5 hapus data e2e, 1.6 RLS lewat HTTP | **Selesai** (lokal): 217 tes pgTAP, 88/88 HTTP, 29/29 e2e; bug audit GoTrue diperbaiki (migrasi 20261010000100) |
| 1.2 Auth dev (password 8, redirect, MFA off) | **Selesai** (push ke dev). Hook Send Email + CAPTCHA belum aktif |
| 1.3 Pages `app.ryhnfir.dev` (callback + Turnstile) | **Selesai** (200). Mailer `mailer.ryhnfir.dev` (Vercel) **terblokir**: deployment gagal build, butuh Build Logs |
| 1.4 dev/prod terpisah | **Ditunda** (prod disiapkan di akhir, keputusan pemilik) |
| 1.7 backup `age` | Menunggu pemilik |
| 1.9 Deno | **Selesai** (34 tes, CI job) |
| 2.1–2.2 dataset + split | **Selesai** (heuristik per pasien, 0 kebocoran) |
| 2.4 lisensi bobot | **Selesai**; YOLOX = `owner_accepted_risk` (ADR 014 §5) |
| 2.3 pelatihan Kaggle | **Berjalan** (dijalankan pemilik) |
| 2.5–2.7 ekspor INT8, spike S1, ADR 013 | Menunggu hasil pelatihan + HP uji |
| ADR 014 (kelas D/d, tanpa keparahan dari ukuran kotak) | **Diterapkan** di ml, triase, API, DB, dokumen |

## Gerbang (gate) yang mengatur urutan
| Gerbang | Isi | Target | Akibat bila belum |
|---|---|---|---|
| **G1** | ≥ 1 drg bersedia meninjau aturan klinis secara tertulis | akhir minggu 2 | Tanpa klaim klinis; nilai klinis `PLACEHOLDER`; modul medis & `dataset_contribution` mati; tidak ada uji pada anak (PRD §13.4) |
| **G2** | Bake-off model selesai + spike S1 (ukuran/latensi di HP RAM 3–4 GB) | minggu 3 | Tidak ada `model.tflite` untuk dirilis |
| **G3** | KEPK menyetujui | sebelum fase 6 | Foto anak tidak boleh dikumpulkan; tidak ada uji lapangan |
| **G4** | Semua `release_check` hijau (triase 99 → 0 penghambat, rekomendasi 126 → 0) | minggu 6 | Manifes produksi tidak bisa ditandatangani |

---

## Fase 0 — Fondasi **[Selesai / Ditulis]**
Tujuan: kerangka backend lengkap, aman secara desain, belum berklaim klinis.
- **[Selesai]** Repo `backend` mandiri; CI hijau; `scheduled-jobs` & `keepalive` terbukti jalan (HTTP 200).
- **[Selesai]** Database Supabase produksi-dev: 9 migrasi, 18 tabel RLS enabled+forced, bucket `manifests` & `dataset_photos`, 2 job pg_cron, `keepalive()`.
- **[Selesai]** Edge Functions `process-deletion`, `retention-sweep` ter-deploy; auth `CRON_SECRET`.
- **[Selesai, pytest]** `triage` (353 tes, 100%), `recommender` (95, 100%), `ml/detection` (170), `ml/brushing` (157), `tools` (240), `tools/manifest` (202), `api` (182).
- **[Ditulis]** Uji RLS 196 asersi hanya di Postgres sementara + shim pgTAP; belum di Supabase CLI/Docker. FastAPI belum di-deploy.
**Keluar:** semua di atas. Sudah tercapai.

## Fase 1 — Verifikasi nyata & pengerasan fondasi (minggu 1–2)
Tujuan: mengubah semua yang bertanda **[Ditulis]** menjadi **[Selesai]**.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 1.1 | Jalankan Docker Desktop → `supabase start && supabase db reset && supabase test db` (pgTAP asli). Perbaiki selisih shim vs asli | 🧑 jalankan, 🤖 perbaiki | Opus high |
| 1.2 | Auth: `supabase config push` (SMTP Resend, CAPTCHA, redirect URL, konfirmasi email); isi `RESEND_API_KEY`, `CAPTCHA_*`, `AUTH_*` | 🧑 + 🤖 | Sonnet high |
| 1.3 | DNS Resend `auth.ryhnfir.dev` (SPF/DKIM/MX), DMARC `p=none`, halaman `app.ryhnfir.dev/auth/callback` (deep link `dentisai://`); uji kirim konfirmasi & reset | 🧑 | — |
| 1.4 | Dua proyek Supabase (dev + prod) — sekarang baru satu; pisahkan data uji dari prod | 🧑 + 🤖 | Sonnet high |
| 1.5 | Uji e2e hak hapus di Supabase lokal: akun uji → data & foto → `deletion_request` → tidak ada jejak (termasuk `auth.audit_log_entries`) | 🤖 | Opus high |
| 1.6 | Uji lanjutan auth: RLS lewat PostgREST sungguhan (anon/authenticated JWT), bukan hanya SQL | 🤖 | Opus high |
| 1.7 | Backup: pasang `age`, isi `BACKUP_*`, jalankan `tools/backup_db.sh` pertama, uji restore ke DB sementara (`restore_check.md`) | 🧑 + 🤖 | Sonnet high |
| 1.8 | Verifikasi: apakah ping REST/pg_cron mencegah penjedaan (catat di dashboard setelah 7 hari) | 🧑 | — |
| 1.9 | Type-check & uji Edge Functions dengan Deno | 🤖 | Sonnet high |
| 1.10 | Mulai paket G1: kirim outreach drg/mitra/KEPK (`docs/pitch/outreach_drg_mitra_kepk.md`) | 🧑 | — |
**Keluar:** `supabase test db` hijau di Supabase CLI; konfirmasi & reset email tiba; backup + restore terbukti; outreach terkirim.

## Fase 2 — Dataset & bake-off model (minggu 1–3) → G2
Tujuan: memilih detektor permisif dan membuktikan ekspor LiteRT INT8.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 2.1 | Unduh Ahmed 2025 (CC BY 4.0), `verify_dataset.py`, pemetaan kelas D→`caries_permanent`, d→`caries_primary` (ADR 014) **[Selesai]** | 🧑 + 🤖 | Opus high |
| 2.2 | `make_splits.py` per pasien (heuristik EXIF) + simpan hash split | 🤖 | Sonnet high |
| 2.3 | Latih kandidat #1 (YOLOX-Nano) & #2 (EfficientDet-Lite0 / MediaPipe Model Maker di env Py ≤ 3.10) di Kaggle/Colab — **data publik saja** | 🧑 jalankan | — |
| 2.4 | Verifikasi lisensi **bobot** pra-latih tiap kandidat; isi `license_gate.yaml` | 🤖 + 🧑 | Opus high |
| 2.5 | Ekspor INT8, `check_int8_parity.py`, gerbang ≤ 15 MB, tulis dekoder keluaran (`DECODE_SPEC.md`) | 🤖 | Opus high |
| 2.6 | Spike S1 di ≥ 3 HP (RAM 3/4/6 GB) bersama 📱: latensi & memori | 📱 + 🧑 | — |
| 2.7 | `bake_off/compare.py` → ADR hasil bake-off (ADR 013) | 🤖 | Sonnet high |
**Keluar (G2):** 1 model terpilih; INT8 ≤ 15 MB; latensi terukur; lisensi bobot jelas; ADR 013. **Risiko:** sens karies di bawah target → turunkan klaim, pakai ukuran lebih besar. Dataset Ahmed tidak membedakan kavitas/ non-kavitas dan kotaknya melingkupi seluruh gigi → tidak ada klaim kavitas/ keparahan (ADR 014).

## Fase 3 — Aturan klinis (minggu 3–6) → G1 → G4 **[Terblokir 🦷]**
Tujuan: mengganti semua `PLACEHOLDER` dengan nilai yang ditandatangani drg.
| # | Pekerjaan | Pemilik |
|---|---|---|
| 3.1 | Sesi tinjauan 🦷 atas `triage/README.md` (tabel Indonesia) — 6 keputusan: trismus, jawaban "tidak tahu", skala nyeri, usia/foto tak layak, X/N, nomor darurat | 🦷 + 🤖 menyiapkan lembar tinjauan |
| 3.2 | Isi `params` (X, N, ambang kepercayaan), `reviewed_by`, `reviewed_at`; perbarui `cases.json` dengan **harapan dari 🦷, bukan regenerasi dari kode** | 🤖 (Opus high) |
| 3.3 | ≥ 30 kasus `graded_by_drg: true`; kasus batas hasil penilaian 🦷 | 🦷 |
| 3.4 | Rekomendasi: putuskan konflik SNI 500–1000 ppm vs ≥ 1000 ppm (PDGI/IDAI), pita usia, jumlah pakai; isi `products.json` nyata (nomor BPOM via cekbpom) | 🦷 + 🧑 |
| 3.5 | Sahkan `forbidden_phrases.md`; sinkronkan salinan di repo frontend | 🦷 + 🧑 |
| 3.6 | Sahkan nilai sikat gigi (`brushing_rules.yaml`) & protokol sekstan | 🦷 |
| 3.7 | `release_check` triase & rekomendasi hijau; catat di `docs/regulatory/CHANGELOG.md` | 🤖 (Sonnet high) |
**Keluar (G4):** `release_check` = 0 penghambat; change log terisi. **Tanpa G1:** fase ini berhenti; produk tetap demo teknis tanpa klaim klinis.

## Fase 4 — Pipeline rilis & kontrak ke app (minggu 5–6)
Tujuan: artefak aturan/model sampai ke app secara terverifikasi dan dapat di-rollback.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 4.1 | Buat kunci Ed25519 (`keygen`) di luar repo; cadangan terenkripsi; tanam kunci publik di app (📱) | 🧑 | — |
| 4.2 | Tandatangani manifes internal-test; unggah ke bucket `manifests`; app memverifikasi (vektor SPEC §3.1 dijalankan di TS) | 🧑 + 📱 + 🤖 | Opus high |
| 4.3 | **Distribusi kontrak antar-repo** (repo frontend terpisah): rilis berversi berisi `triage/dist/`, `tests/cases.json`, `recommender/dist/`, vektor manifes, `quality_gate_vectors`, `decode_vectors` — mis. GitHub Release / paket privat; app mengunduh dan menjalankan Jest paritas | 🤖 | Sonnet high |
| 4.4 | Uji paritas Python↔TS: triase, rekomendasi, quality gate, check-in sikat gigi, dekoder detektor | 📱 + 🤖 | Opus high |
| 4.5 | Prosedur rollback & rotasi kunci (SPEC §6); uji manifes dirusak ditolak di app | 🤖 + 📱 | Sonnet high |
| 4.6 | Ekspor bundel model resmi `ml/exports/<semver>` + `MODEL_CARD.md` + `SHA256` | 🤖 | Sonnet high |
| 4.7 | TTS: naskah disetujui → `generate_audio.py --provider gemini` → serahkan audio ke 📱; dengar-uji 5 anak; konfirmasi syarat Gemini sebelum rilis publik (ADR 012) | 🧑 + 🤖 | Sonnet high |
**Keluar:** satu manifes produksi bertanda tangan terverifikasi end-to-end di perangkat nyata; paritas hijau.

## Fase 5 — Integrasi dengan app (minggu 7–8)
- 5.1 Uji kontrak `sync_events` dari klien nyata (outbox → RPC): idempoten, retry, batas ukuran, penarikan consent (b) menghentikan sinkron 🤖 Opus high + 📱.
- 5.2 Alur auth e2e (daftar → konfirmasi email → login → reset), CAPTCHA, batas 100 email/hari terpantau.
- 5.3 Hak hapus dari app → `deletion_request` → bukti; mode anak tanpa akun tidak mengirim apa pun.
- 5.4 Pemantauan kuota gratis (DB 500 MB, storage 1 GB, MAU, email/hari) — view admin + peringatan.
- 5.5 Telemetri agregat tanpa PII; verifikasi log tidak memuat PII/foto.
- 5.6 Perbaiki kontrak: kuesioner 9 pertanyaan vs batas 7 layar (FR-10), `lesions[]` (keluaran triase; `teeth[]` di `app/CLAUDE.md` usang) — selaraskan dengan 📱; kuesioner 9 vs 7 layar → 🦷.
**Keluar:** APK demo menjalankan skrining → triase → sinkron ke Supabase tanpa pelanggaran privasi.

## Fase 6 — Kesiapan uji lapangan (minggu 9) → G3
| # | Pekerjaan | Pemilik |
|---|---|---|
| 6.1 | KEPK: protokol, consent (a/b/c) & assent anak, lembar informasi; setelah disetujui baru `dataset_contribution` boleh dinyalakan; retensi foto (365 hari placeholder) ditetapkan KEPK | 🧑 + 🦷 |
| 6.2 | Tinjauan hukum ⚖: 14 pertanyaan di `OPEN_LEGAL_QUESTIONS.md` (transfer Singapura, UU PDP, PP Tunas, KMK 951/2026), pemberitahuan privasi & teks consent | ⚖ |
| 6.3 | Pen-test ringan RLS/Storage/API; pemindaian dependensi; SBOM final | 🤖 Opus high |
| 6.4 | Latihan insiden & restore backup; prosedur reset password oleh admin | 🧑 + 🤖 |
| 6.5 | Model card, risk register (probabilitas & risiko sisa diisi 🦷/🧑), data lineage final | 🤖 Sonnet high + 🦷 |
| 6.6 | Uji lapangan kecil: sensitivitas/spesifisitas vs drg, % foto diagnostik, under-triage, SUS | 🦷 + 📱 |
**Keluar (G3):** persetujuan KEPK + hasil uji; laporan evaluasi.

## Fase 7 — Pasca-pilot / v1.1 (setelah minggu 10)
- Deploy FastAPI (`backend/api`) ke Azure Container Apps (Jepang) bila diperlukan; peran `reviewer`, URL foto bertanda tangan jangka pendek, auto-batal item bila foto ditarik.
- Dasbor fasilitas (agregat, `facility_viewer`), rekap kelas PDF/CSV.
- Kelas tambahan & data lokal ≥ 2 anotator (κ); ML sikat gigi dari landmark.
- Chatbot (v2) hanya dengan keputusan anggaran/penyedia baru — Gemini terlarang di jalur runtime (ADR 006/010).
- Migrasi ke tier berbayar/ regenerasi audio TTS di akun berbayar sebelum rilis komersial.

---

## Jalur kritis & yang bisa jalan paralel
```
Fase 1 (verifikasi) ─┬─────────────────────────────┐
Fase 2 (bake-off) ───┼─► G2 ──► 4.6 bundel model ──┤
G1 (drg) ► Fase 3 ───┴─► G4 ──► 4.2 manifes ───────┼─► Fase 5 ─► Fase 6 (G3) ─► Fase 7
                                 4.3 kontrak antar-repo ┘
```
Jalur terpanjang = **G1 → Fase 3 → G4**. Semua pekerjaan 🤖 di Fase 1, 2, 4.3, 4.7, dan 5.x dapat berjalan tanpa drg.

## Risiko utama & mitigasi
| Risiko | Mitigasi |
|---|---|
| Tidak ada drg (G1 gagal) | Demo teknis tanpa klaim klinis; nilai tetap `PLACEHOLDER`; `release_check` mencegah rilis tak sengaja |
| Model permisif kurang akurat / ekspor INT8 gagal | Bake-off ≥ 2 kandidat; turunkan klaim; ukuran model lebih besar |
| Kuota/penjedaan Supabase Free | Keep-alive terbukti 200; backup manual terjadwal; pantau kuota (5.4); rencana tier berbayar |
| Syarat Gemini untuk aplikasi anak (ADR 012) | Konfirmasi tertulis Google atau ganti penyedia — audio bisa dibuat ulang |
| Dua repo terpisah menyebabkan kontrak melenceng | Fase 4.3: rilis artefak berversi + tes paritas wajib di kedua sisi |
| Kebocoran rahasia (`.env`, service_role, kunci penandatangan) | `.gitignore`, `check_secrets.py` di CI, kunci offline, `CRON_SECRET` terpisah dari service_role |

## Pekerjaan yang belum punya pemilik
- Siapa yang menjalankan pelatihan di Kaggle/Colab dan mengunduh dataset (2.1, 2.3)?
- Siapa 🦷, ⚖, dan kontak KEPK? (belum ada)
- Daftar HP uji untuk spike S1 (≥ 3 perangkat)?
