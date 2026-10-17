# Rencana Implementasi Backend DentisAI — per Fase

> Disusun 2026-10-09, ditulis ulang 2026-10-11 mengikuti **ADR 015** (aturan klinis berbasis pustaka tanpa tanda tangan drg; dataset tunggal Ahmed 2025). Acuan: `PRD.md` §13 (timeline 10 minggu), `CLAUDE.md` backend §15, ADR 001/005/008/009/010/014/015. Minggu 1 = 8–14 Okt 2026.
> Legenda: **[Selesai]** terbukti dijalankan · **[Ditulis]** ada kode tetapi belum terbukti di lingkungan asli · **[Terblokir]** menunggu pihak lain · 🧑 pemilik produk · ⚖ penasihat hukum · 📱 tim frontend · 🤖 dikerjakan Claude (Opus high untuk area berat, Sonnet high untuk area kecil, Opus max hanya untuk bug sulit).

## Status per 2026-10-11
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
| 2.5–2.8 ekspor INT8, spike S1, ADR 013, titik operasi | Menunggu hasil pelatihan + HP uji |
| 4.3 bundel kontrak antar-repo | **Selesai** (lokal, 2026-10-11): `tools/contracts/` (26 berkas, zip deterministik dari objek git, `verify`), workflow `contracts-release.yml`; 26 tes. Belum: tag `contracts-v0.1.0` pertama (🧑) dan konsumsi di repo app (📱) |
| 5.4 pemantauan kuota Free | **Selesai** (lokal, 2026-10-11): RPC admin/service, Edge `quota-check`, job harian; 24 pgTAP + 7 Deno. Belum: `db push` + deploy fungsi ke dev (🧑), uji alert di GitHub |
| ADR 014 (kelas D/d, tanpa keparahan dari ukuran kotak) | **Diterapkan** di ml, triase, API, DB, dokumen |
| ADR 015 (aturan berbasis pustaka, dataset tunggal) | **Diterima**; perubahan kode gerbang rilis = 3.1 (belum) |

## Batas MVP (ADR 015)
Berlaku selama belum ada drg peninjau dan persetujuan KEPK:
- **Nilai klinis bersumber pustaka.** Setiap ambang, level, red flag, dan nilai fluoride punya sitasi; bila pustaka tidak memberi angka, dipilih opsi yang lebih waspada. Tidak ada tanda tangan drg yang ditunggu.
- **Dataset: hanya Ahmed 2025**, anotasi apa adanya (tanpa pelabelan ulang/ tinjauan anotasi). DENTALAI, AlphaDent, dan data lokal tidak dipakai.
- **Klaim:** keluaran tetap "indikasi/ perlu diperiksa" + rujukan; angka kinerja hanya "pada split uji Ahmed 2025" beserta batasannya. Tidak ada klaim akurasi klinis atau "divalidasi/ disetujui dokter gigi".
- **Tanpa data & uji pada anak:** `dataset_contribution` mati; uji kegunaan dengan sukarelawan dewasa. Rilis publik fitur deteksi/ triase mengikuti ADR 002.
- **Validasi klinis** (drg, KEPK, uji lapangan, data lokal) dikerjakan di Fase 7.

## Gerbang (gate) yang mengatur urutan
| Gerbang | Isi | Target | Akibat bila belum |
|---|---|---|---|
| **G2** | Bake-off model selesai + spike S1 (ukuran/latensi di HP RAM 3–4 GB) + titik operasi dipilih di split val | minggu 3 | Tidak ada `model.tflite` maupun ambang kepercayaan untuk dirilis |
| **G4** | Semua `release_check` hijau dengan `review_basis: literature` (triase, rekomendasi, sikat gigi) | minggu 5 | Manifes produksi tidak bisa ditandatangani |
| ~~G1~~ / ~~G3~~ | Tinjauan drg / persetujuan KEPK | — | **Bukan gerbang MVP lagi (ADR 015).** Dipindah ke Fase 7; batasnya tercantum di "Batas MVP" |

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
**Keluar:** `supabase test db` hijau di Supabase CLI; konfirmasi & reset email tiba; backup + restore terbukti.

## Fase 2 — Dataset & bake-off model (minggu 1–3) → G2
Tujuan: memilih detektor permisif, membuktikan ekspor LiteRT INT8, dan menetapkan titik operasi — **hanya dengan Ahmed 2025**.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 2.1 | Unduh Ahmed 2025 (CC BY 4.0), `verify_dataset.py`, pemetaan kelas D→`caries_permanent`, d→`caries_primary` (ADR 014). Satu-satunya dataset; anotasi dipakai apa adanya (ADR 015) **[Selesai]** | 🧑 + 🤖 | Opus high |
| 2.2 | `make_splits.py` per pasien (heuristik EXIF) + simpan hash split **[Selesai]** | 🤖 | Sonnet high |
| 2.3 | Latih kandidat #1 (YOLOX-Nano) & #2 (EfficientDet-Lite0 / MediaPipe Model Maker di env Py ≤ 3.10) di Kaggle/Colab — **Ahmed 2025 saja** | 🧑 jalankan | — |
| 2.4 | Verifikasi lisensi **bobot** pra-latih tiap kandidat; isi `license_gate.yaml` **[Selesai]** | 🤖 + 🧑 | Opus high |
| 2.5 | Ekspor INT8, `check_int8_parity.py`, gerbang ≤ 15 MB, tulis dekoder keluaran (`DECODE_SPEC.md`) | 🤖 | Opus high |
| 2.6 | Spike S1 di ≥ 3 HP (RAM 3/4/6 GB) bersama 📱: latensi & memori | 📱 + 🧑 | — |
| 2.7 | `bake_off/compare.py` → ADR hasil bake-off (ADR 013) | 🤖 | Sonnet high |
| 2.8 | Titik operasi: `thresholds.py` pada split **val** (target sensitivitas 0,85 = hipotesis PRD §3.3); tim memilih dan mencatat alasan → masukan untuk 3.3. Lalu `eval.py` pada split **uji** sekali → laporan per kelas + tingkat citra untuk model card | 🤖 + 🧑 | Opus high |
**Keluar (G2):** 1 model terpilih; INT8 ≤ 15 MB; latensi terukur; lisensi bobot jelas; ADR 013; `thresholds.json` + laporan uji. **Risiko:** sens karies di bawah target → turunkan klaim, pakai ukuran lebih besar. Dataset Ahmed tidak membedakan kavitas/ non-kavitas dan kotaknya melingkupi seluruh gigi → tidak ada klaim kavitas/ keparahan (ADR 014). Anotasi tidak ditinjau ulang → kesalahan label ikut terlatih; dicatat di model card.

## Fase 3 — Aturan klinis berbasis pustaka (minggu 3–5) → G4
Tujuan: mengganti semua `PLACEHOLDER` dengan nilai yang ditetapkan tim dari pustaka terbitan, tiap nilai bersitasi, tanpa menunggu tanda tangan drg (ADR 015). Struktur tinjauan dipertahankan agar drg dapat meninjau di Fase 7 tanpa mengubah format.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 3.1 | **Ubah gerbang rilis:** tambahkan `review_basis: literature \| dentist` di `rules.yaml`, `recommender/rules.json`, `brushing_rules.yaml`, dan `thresholds.json`. `release_check` menerima `reviewed_by` anggota tim bila basis pustaka dan `source` terisi tanpa `PERLU VERIFIKASI`; syarat `graded_by_drg` diganti ≥ 30 kasus dengan `expected_source`; `export.py --release` menerima titik operasi `reviewed_by` tim. Pemeriksaan lain tetap. Tes gerbang diperbarui; kontrak `dist/` & `cases.json` ke app tetap kompatibel (field baru opsional) | 🤖 | Opus high |
| 3.2 | **Tabel bukti** `docs/research/11_clinical_rule_sources.md`: tiap parameter, aturan, dan red flag → sumber (pedoman organisasi profesi/ pemerintah, tinjauan sistematis), ringkasan isi, tanggal akses. Tanpa angka di pustaka → nilai `if_unset` (lebih waspada), ditandai "keputusan tim (konservatif)". Mencakup 6 keputusan terbuka: trismus, jawaban "tidak tahu", skala nyeri, usia/ foto tak layak, N, nomor darurat | 🤖 menyusun, 🧑 mengesahkan | Opus high |
| 3.3 | Isi `params` triase dari 3.2 dan ambang kepercayaan dari 2.8: `value`, `status: SET`, `reviewed_by`, `reviewed_at`, `source`; perbarui header & `notice` `rules.yaml` (basis pustaka, belum divalidasi klinis) | 🤖 | Opus high |
| 3.4 | `cases.json`: ≥ 30 kasus dengan harapan **ditulis dari tabel bukti 3.2, bukan regenerasi dari kode**; mencakup kasus batas, jawaban "tidak tahu", dan kombinasi red flag; tiap kasus punya `expected_source` | 🤖 | Opus high |
| 3.5 | Rekomendasi: tetapkan ppm per usia, jumlah pakai, dan pengawasan dari pedoman terbaru yang terdokumentasi; catat konflik SNI 500–1000 ppm vs anjuran ≥ 1000 ppm (PDGI/IDAI) dan alasan pilihan; isi `products.json` nyata (nomor BPOM via cekbpom; merek hanya contoh, netral) | 🤖 + 🧑 | Sonnet high |
| 3.6 | Nilai sikat gigi (`brushing_rules.yaml`) & protokol sekstan dari pustaka | 🤖 | Sonnet high |
| 3.7 | Verifikasi nomor darurat (`contacts`) dari sumber resmi → `VERIFIED` | 🧑 | — |
| 3.8 | Sahkan `forbidden_phrases.md` (tambahkan klaim "divalidasi/ disetujui dokter gigi"); sinkronkan salinan di app | 🧑 + 📱 | — |
| 3.9 | Teks transparansi di layar Tentang & hasil: aturan disusun dari pedoman terbitan, model dilatih pada dataset publik, belum divalidasi klinis | 📱 + 🧑 | — |
| 3.10 | `release_check` triase, rekomendasi, dan sikat gigi hijau; catat `review_basis: literature` dan daftar sumber di `docs/regulatory/CHANGELOG.md` | 🤖 | Sonnet high |
**Keluar (G4):** `release_check` = 0 penghambat; setiap nilai klinis tertaut ke sumber di 3.2; change log terisi. **Ketergantungan:** 3.3 (ambang kepercayaan) menunggu 2.8; sisanya bisa mulai minggu 3.

## Fase 4 — Pipeline rilis & kontrak ke app (minggu 5–6)
Tujuan: artefak aturan/model sampai ke app secara terverifikasi dan dapat di-rollback.
| # | Pekerjaan | Pemilik | Model |
|---|---|---|---|
| 4.1 | Buat kunci Ed25519 (`keygen`) di luar repo; cadangan terenkripsi; tanam kunci publik di app (📱) | 🧑 | — |
| 4.2 | Tandatangani manifes internal-test; unggah ke bucket `manifests`; app memverifikasi (vektor SPEC §3.1 dijalankan di TS) | 🧑 + 📱 + 🤖 | Opus high |
| 4.3 | **[Selesai lokal]** **Distribusi kontrak antar-repo** (repo frontend terpisah): rilis berversi berisi `triage/dist/`, `tests/cases.json`, `recommender/dist/`, vektor manifes, `quality_gate_vectors`, `decode_vectors` — mis. GitHub Release / paket privat; app mengunduh dan menjalankan Jest paritas | 🤖 | Sonnet high |
| 4.4 | Uji paritas Python↔TS: triase, rekomendasi, quality gate, check-in sikat gigi, dekoder detektor | 📱 + 🤖 | Opus high |
| 4.5 | Prosedur rollback & rotasi kunci (SPEC §6); uji manifes dirusak ditolak di app | 🤖 + 📱 | Sonnet high |
| 4.6 | Ekspor bundel model resmi `ml/exports/<semver>` + `MODEL_CARD.md` + `SHA256` | 🤖 | Sonnet high |
| 4.7 | TTS: naskah disetujui 🧑 (lolos lint frasa terlarang) → `generate_audio.py --provider gemini` → serahkan audio ke 📱; dengar-uji dengan sukarelawan dewasa; konfirmasi syarat Gemini sebelum rilis publik (ADR 012) | 🧑 + 🤖 | Sonnet high |
**Keluar:** satu manifes produksi bertanda tangan terverifikasi end-to-end di perangkat nyata; paritas hijau.

## Fase 5 — Integrasi dengan app (minggu 7–8)
- 5.1 Uji kontrak `sync_events` dari klien nyata (outbox → RPC): idempoten, retry, batas ukuran, penarikan consent (b) menghentikan sinkron 🤖 Opus high + 📱.
- 5.2 Alur auth e2e (daftar → konfirmasi email → login → reset), CAPTCHA, batas 100 email/hari terpantau.
- 5.3 Hak hapus dari app → `deletion_request` → bukti; mode anak tanpa akun tidak mengirim apa pun.
- 5.4 **[Selesai lokal]** Pemantauan kuota gratis (DB 500 MB, storage 1 GB, MAU, email/hari) — `admin_quota_usage()` + Edge `quota-check` + job `scheduled-jobs.yml` (`critical` → run merah). Egress/pemanggilan fungsi tetap manual di dashboard.
- 5.5 Telemetri agregat tanpa PII; verifikasi log tidak memuat PII/foto.
- 5.6 Perbaiki kontrak: `lesions[]` (keluaran triase; `teeth[]` di `app/CLAUDE.md` usang) — selaraskan dengan 📱; kuesioner 9 pertanyaan vs batas 7 layar (FR-10) → diputuskan 🧑 + 📱 berdasarkan tabel bukti 3.2 (red flag tidak boleh dibuang).
**Keluar:** APK demo menjalankan skrining → triase → sinkron ke Supabase tanpa pelanggaran privasi.

## Fase 6 — Kesiapan rilis MVP (minggu 9)
Tujuan: MVP aman, terdokumentasi, dan jujur tentang batasannya.
| # | Pekerjaan | Pemilik |
|---|---|---|
| 6.1 | Pen-test ringan RLS/Storage/API; pemindaian dependensi; SBOM final | 🤖 Opus high |
| 6.2 | Latihan insiden & restore backup; prosedur reset password oleh admin | 🧑 + 🤖 |
| 6.3 | Model card (metrik split uji Ahmed 2025; batasan: Pakistan, 10–24 th, satu perangkat, anotasi tidak ditinjau ulang, belum divalidasi klinis), risk register (probabilitas & risiko sisa diisi 🧑), data lineage final | 🤖 Sonnet high + 🧑 |
| 6.4 | Uji kegunaan dengan sukarelawan dewasa: SUS, % foto lolos quality gate, waktu alur; tanpa anak dan tanpa unggah foto | 🧑 + 📱 |
| 6.5 | Tinjauan hukum ⚖: pertanyaan di `OPEN_LEGAL_QUESTIONS.md` (transfer Singapura, UU PDP, PP Tunas, KMK 951/2026), pemberitahuan privasi & teks consent; status rilis publik fitur deteksi/ triase per ADR 002 | ⚖ + 🧑 |
**Keluar:** APK MVP + paket dokumen kepatuhan; semua batasan diungkapkan di app dan model card.

## Fase 7 — Validasi klinis & perluasan (pasca-MVP, setelah minggu 10)
Tujuan: membawa produk dari bukti teknis ke bukti klinis dan memperluas cakupan.
- 7.1 **Drg peninjau** (paket outreach `docs/pitch/outreach_drg_mitra_kepk.md`): tinjau tabel bukti 3.2 dan semua nilai; terbitkan ulang aturan dengan `review_basis: dentist`; ≥ 30 kasus `graded_by_drg`; tinjau titik operasi model.
- 7.2 **KEPK:** protokol, consent (a/b/c) & assent anak, lembar informasi; setelah disetujui baru `dataset_contribution` boleh dinyalakan; retensi foto (365 hari placeholder) ditetapkan KEPK.
- 7.3 **Uji lapangan:** sensitivitas/spesifisitas vs drg, % foto diagnostik, under-triage, SUS pada pengguna sasaran.
- 7.4 Data lokal ≥ 2 anotator (κ), kelas tambahan, latih ulang di PC tim/ lab kampus; ML sikat gigi dari landmark.
- 7.5 Deploy FastAPI (`backend/api`) ke Azure Container Apps (Jepang) bila diperlukan; peran `reviewer`, URL foto bertanda tangan jangka pendek, auto-batal item bila foto ditarik.
- 7.6 Dasbor fasilitas (agregat, `facility_viewer`), rekap kelas PDF/CSV.
- 7.7 Chatbot (v2) hanya dengan keputusan anggaran/penyedia baru — Gemini terlarang di jalur runtime (ADR 006/010).
- 7.8 Migrasi ke tier berbayar/ regenerasi audio TTS di akun berbayar dan tinjau ulang lisensi bobot YOLOX (ADR 014 §5) sebelum rilis komersial.

---

## Jalur kritis & yang bisa jalan paralel
```
Fase 2 bake-off ──► G2 ──┬──► 3.3 ambang ──┐
                         └──► 4.6 bundel model ───────┐
Fase 3 pustaka (3.1–3.10) ─────────────────┴──► G4 ───┴──► 4.2 manifes ──┐
Fase 1 verifikasi · 4.3 kontrak antar-repo ──────────────────────────────┴──► Fase 5 ──► Fase 6 (rilis MVP) ──► Fase 7 (validasi klinis)
```
Jalur terpanjang = **Fase 2 → G2 → 3.3 (ambang kepercayaan) → G4 → 4.2**. Tabel bukti (3.2), kasus uji (3.4), rekomendasi (3.5), sikat gigi (3.6), dan semua pekerjaan Fase 1, 4.3, 4.7, dan 5.x berjalan paralel tanpa menunggu pihak luar.

## Risiko utama & mitigasi
| Risiko | Mitigasi |
|---|---|
| Aturan berbasis pustaka tidak sesuai praktik klinis lokal | Sitasi per nilai; opsi lebih waspada bila pustaka diam; red flag selalu menang; keluaran "indikasi" + rujukan; teks transparansi (3.9); validasi drg di Fase 7 |
| Model hanya dari Ahmed 2025 (Pakistan, 10–24 th, satu perangkat, label tidak ditinjau ulang) → generalisasi ke anak Indonesia/ HP lain tidak diketahui | Quality gate foto; metrik dilaporkan hanya "pada split uji Ahmed 2025"; batasan di model card & layar Tentang; data lokal di Fase 7 |
| Klaim berlebihan di materi atau UI | Lint frasa terlarang (termasuk "divalidasi/ disetujui dokter gigi"); CHANGELOG mencatat `review_basis: literature` |
| Model permisif kurang akurat / ekspor INT8 gagal | Bake-off ≥ 2 kandidat; turunkan klaim; ukuran model lebih besar |
| Kuota/penjedaan Supabase Free | Keep-alive terbukti 200; backup manual terjadwal; pantau kuota (5.4); rencana tier berbayar |
| Syarat Gemini untuk aplikasi anak (ADR 012) | Konfirmasi tertulis Google atau ganti penyedia — audio bisa dibuat ulang |
| Dua repo terpisah menyebabkan kontrak melenceng | Fase 4.3: rilis artefak berversi + tes paritas wajib di kedua sisi |
| Kebocoran rahasia (`.env`, service_role, kunci penandatangan) | `.gitignore`, `check_secrets.py` di CI, kunci offline, `CRON_SECRET` terpisah dari service_role |

## Pekerjaan yang belum punya pemilik
- Siapa yang menjalankan pelatihan di Kaggle/Colab (2.3) dan memilih titik operasi bersama 🤖 (2.8)?
- Siapa yang mengesahkan tabel bukti (3.2) di sisi tim — 🧑 atau anggota tim dengan latar kesehatan?
- Daftar HP uji untuk spike S1 (≥ 3 perangkat)?
- Siapa ⚖ (6.5)? Drg peninjau dan kontak KEPK baru dibutuhkan di Fase 7.
