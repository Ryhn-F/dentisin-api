# docs/regulatory — dokumentasi kepatuhan "regulatable-by-design"

> Dasar: **ADR 002** (status Proposed) dan `backend/CLAUDE.md` §3 butir 9. Dokumen-dokumen ini adalah **templat hidup**: diisi dengan apa yang sudah diketahui dari repo, sisanya sengaja dibiarkan kosong (`[ISI]`). Tidak ada fakta, tanggal, hasil uji, nama peninjau, atau nomor pasal yang boleh dikarang. Tanda yang dipakai: `[PERLU VERIFIKASI]` (klaim belum dicek ke sumber primer), `[ISI]` (data belum ada), `[PENDING]` (menunggu orang tertentu, mis. drg pembimbing).

> **Konvensi path:** semua path dalam dokumen ini relatif terhadap `backend/` (BACKEND_ROOT). Dokumen bersama di root repo ditulis `../docs/...` (PRD, ADR, riset, gaya). Berkas frasa terlarang: env `FORBIDDEN_PHRASES_PATH`, jika tidak ada `../docs/style/forbidden_phrases.md`, jika tidak ada salinan `docs/style/forbidden_phrases.md`.

## Status saat ini (per 2026-10-09)
| Hal | Status |
|---|---|
| Klaim medis | **Tidak ada.** Hanya bahasa "indikasi / perlu diperiksa"; tanpa angka akurasi sebelum validasi (ADR 002 butir 2; `../docs/style/forbidden_phrases.md`) |
| Posisi | **Prototipe riset/pilot** di bawah persetujuan etik (KEPK) + MoU mitra. Keduanya **belum ada** (gerbang G1, PRD §13.4). Tanpa G1: demo teknis, tanpa uji pada anak |
| Flag `medical_modules` | **Default MATI** pada build publik (deteksi foto, triase, rekomendasi berbasis temuan). Modul non-medis (streak sikat gigi, edukasi) tidak bergantung pada flag. Flag `dataset_contribution` juga **default mati** sampai KEPK menyetujui |
| KMK HK.01.07/MENKES/951/2026 | **Teks resminya BELUM dibaca** oleh tim; semua yang dikatakan tentangnya berasal dari ringkasan sekunder (`../docs/research/05_ethics_regulation.md` §5.1). Kelas risiko, ambang sampel, dan pengecualian aplikasi edukasi **tidak diketahui**. Lihat `OPEN_LEGAL_QUESTIONS.md` |
| Drg pembimbing / penandatangan klinis | **Belum ada** → semua persetujuan klinis `[PENDING]` |
| Pendapat hukum tertulis | **Belum ada** (ADR 002 butir 4; simpan di `../docs/decisions/evidence/` — folder dibuat pemilik saat berkas ada) |

## Indeks
| Berkas | Fungsi | Diperbarui oleh / kapan |
|---|---|---|
| [`RISK_REGISTER.md`](RISK_REGISTER.md) | Manajemen risiko gaya ISO 14971: bahaya, kontrol di desain, risiko sisa, tautan verifikasi | Setiap fitur/perubahan aturan/model; tinjau berkala [ISI] |
| [`CHANGELOG.md`](CHANGELOG.md) | Log perubahan terkendali model / aturan triase / rekomendasi (versi, alasan, laporan evaluasi, persetujuan drg, hasil regresi) | **Sebelum** rilis tiap artefak yang masuk manifes |
| [`MODEL_CARD_TEMPLATE.md`](MODEL_CARD_TEMPLATE.md) | Templat `ml/exports/<semver>/MODEL_CARD.md` (selaras dengan `labels.json`) | Disalin per versi model |
| [`DATA_LINEAGE.md`](DATA_LINEAGE.md) | Asal-usul data: sumber, lisensi, transformasi, split, model turunan, penarikan | Setiap dataset/ekspor/pelatihan |
| [`COMPLAINTS_LOG.md`](COMPLAINTS_LOG.md) | Daftar keluhan & kejadian tidak diinginkan | Setiap laporan masuk |
| [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) | Prosedur insiden keamanan/privasi/keselamatan klinis | Setelah tiap latihan/insiden |
| [`PRIVACY_NOTICE_DRAFT.md`](PRIVACY_NOTICE_DRAFT.md) | **DRAF** pemberitahuan privasi untuk orang tua (Indonesia sederhana) | Butuh tinjauan hukum |
| [`CONSENT_TEXT_DRAFT.md`](CONSENT_TEXT_DRAFT.md) | **DRAF** teks persetujuan (a)/(b)/(c) + assent anak + `text_version` | Butuh tinjauan hukum + KEPK |
| [`SBOM.md`](SBOM.md) | Cara membuat SBOM dan tabel dependensi per modul | Setiap perubahan dependensi |
| [`DOMAIN_OWNERSHIP.md`](DOMAIN_OWNERSHIP.md) | Kepemilikan `ryhnfir.dev` (ADR 008) dan rencana pengalihan | Bila tim/badan hukum berubah |
| [`OPEN_LEGAL_QUESTIONS.md`](OPEN_LEGAL_QUESTIONS.md) | Pertanyaan hukum/regulasi terbuka beserta pemilik | Tiap ada jawaban |

## Cara menjaga dokumen tetap mutakhir
1. **Definisi selesai** (`backend/CLAUDE.md` §13): perubahan tidak selesai bila dokumen terkait belum diperbarui. Satu PR/perubahan yang menyentuh model, aturan, consent, skema data, atau dependensi **wajib** menyentuh berkas di sini yang relevan.
2. Pemicu cepat:
   - `triage/rules.yaml`, `recommender/*.json`, model/`labels.json` berubah → `CHANGELOG.md` + (model) `MODEL_CARD` baru + tinjau `RISK_REGISTER.md`.
   - Dataset baru/ekspor → `DATA_LINEAGE.md` (+ `ml/data/LICENSES.md`).
   - Teks consent/pemberitahuan berubah → naikkan `text_version`, catat di sini, perbarui `consent` di app/DB.
   - Dependensi berubah → regenerasi SBOM.
   - Laporan pengguna → `COMPLAINTS_LOG.md`; bila terkait keselamatan/privasi → `INCIDENT_RESPONSE.md`.
3. Setiap klaim angka/regulasi punya sumber di `../docs/research/` atau ditandai `[PERLU VERIFIKASI]`.
4. Tinjau ulang ADR 002 setelah teks resmi KMK 951/2026 dibaca (ADR 002 butir 5); catat hasilnya di `OPEN_LEGAL_QUESTIONS.md`.
5. Jangan menaruh PII, foto, atau rahasia di sini. Identitas pengaduan memakai ID pseudonim.

## Yang sengaja belum ada
Sertifikasi (ISO 13485 dsb.) tidak dikejar pada tahap lomba/pilot (ADR 002 butir 1: adopsi bertahap). Dokumen ini hanya menyiapkan *bukti kerja* agar registrasi kelak murah, bukan menyatakan bahwa DentisAI sudah patuh.
