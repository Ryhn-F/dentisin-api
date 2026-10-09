# SBOM (Software Bill of Materials) dan inventaris dependensi

> Templat hidup (ADR 002, `backend/CLAUDE.md` §12: "pemindaian dependensi + SBOM di CI"). Belum ada SBOM yang dihasilkan; tabel di bawah diisi bertahap. Lisensi yang tidak terverifikasi ditandai `[PERLU VERIFIKASI]` — jangan menganggap lisensi dari ingatan. Repo tertutup (ADR 008): **dilarang** mengirim pustaka AGPL/GPL; `ml/data/LICENSES.md` + `ml/data/license_gate.yaml` mencatat dataset/model/dependensi baru.

## Cara membuat (CycloneDX)
Perintah di bawah adalah rujukan umum alat tersebut; **cek bantuan (`--help`) pada versi yang dipasang** karena opsi berubah antarversi `[PERLU VERIFIKASI]`.

**Python** (tiap modul punya `pyproject.toml`/`uv.lock`: `ml/brushing`, `ml/detection`, `recommender`, `triage`, `tools`, `tools/manifest`; `api/` bila ada):
```bash
pip install cyclonedx-bom
cd <modul> && uv sync
cyclonedx-py environment -o ../../sbom/<modul>.cdx.json     # SBOM dari lingkungan terpasang
# atau dari berkas kunci:  uv export --no-hashes > requirements.lock && cyclonedx-py requirements requirements.lock -o ...
pip-audit                                                      # kerentanan (alat terpisah)
```
**npm / Expo (`app/`)** — dikerjakan tim frontend, dicatat di sini agar SBOM lengkap:
```bash
npx @cyclonedx/cyclonedx-npm --output-file sbom/app.cdx.json   # dari package-lock
npm audit
```
**Deno/Edge Functions Supabase** (`supabase/functions`): daftar impor + versi `[ISI]`.

Simpan hasil di `sbom/` (buat folder saat pertama dihasilkan), **jangan** commit rahasia; SBOM tidak memuat PII. Lampirkan SBOM pada tiap rilis ke pilot dan catat hash-nya di `CHANGELOG.md`.

## Kebijakan
1. Kunci versi (lockfile) di semua modul; pembaruan dependensi = perubahan tercatat.
2. Dependensi baru → tambah baris di bawah **sebelum** merge; lisensi dicek (permisif: MIT/BSD/Apache-2.0/ISC; copyleft kuat: ditolak untuk kode terkirim).
3. Bobot model dan dataset bukan "dependensi perangkat lunak" tetapi dicatat di `DATA_LINEAGE.md` dan `MODEL_CARD`.
4. Pemindaian kerentanan dijalankan di CI dan sebelum rilis; temuan tinggi/kritis diselesaikan atau diberi alasan tertulis.
5. Modul native komunitas (VisionCamera, fast-tflite, MediaPipe wrapper): risiko pemeliharaan tinggi (PRD §14) → catat versi, tanggal rilis terakhir, dan pemilik.

## Inventaris per modul
Kolom "Lisensi" kosong/`[PERLU VERIFIKASI]` = belum dibaca dari paket. "Versi" = yang terpasang saat pengujian lokal (2026-10-09) bila tercatat.

### `ml/brushing` (prototipe sikat gigi)
| Paket | Peran | Versi saat uji | Lisensi | Catatan |
|---|---|---|---|---|
| pyyaml | runtime: memuat aturan | 6.0.3 | `[PERLU VERIFIKASI]` | |
| jsonschema | runtime: validasi pelabelan | 4.26.0 | `[PERLU VERIFIKASI]` | |
| tzdata | runtime (Windows): basis data zona waktu | 2026.5 | `[PERLU VERIFIKASI]` | marker `sys_platform == 'win32'` |
| numpy | opsional (`fast`/`prototype`) | - | `[PERLU VERIFIKASI]` | tidak dipakai scorer |
| mediapipe, opencv-python | **opsional**, hanya `prototype_mediapipe.py`, tidak diwajibkan | - | `[PERLU VERIFIKASI]` | cek lisensi paket **dan** bobot `.task` sebelum dipakai |
| pytest, pytest-cov, ruff, mypy, types-PyYAML, types-jsonschema | dev | 9.1.1, 7.1.0, 0.16.10, 2.4.0, 6.0.12.20260906, 4.26.0.20261006 | `[PERLU VERIFIKASI]` | tidak dikirim |

### `ml/detection` · `recommender` · `triage` · `tools` · `tools/manifest` · `api`
Diisi dari `pyproject.toml`/`uv.lock` masing-masing oleh pemilik modul (bukan oleh dokumen ini):
| Modul | Paket runtime | Lisensi tiap paket | Pemindaian kerentanan terakhir | Pemilik |
|---|---|---|---|---|
| ml/detection | `[ISI]` (kandidat: PyTorch/TensorFlow, MediaPipe Model Maker, YOLOX, EfficientDet-Lite — **bukan Ultralytics**) | `[ISI]` | `[ISI]` | `[ISI]` |
| recommender | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |
| triage | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |
| tools | `[ISI]` (kripto Ed25519, Azure Speech SDK/REST, dll.) | `[ISI]` | `[ISI]` | `[ISI]` |
| api (FastAPI ML/AI; belum di-deploy) | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |
| supabase/functions | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |

### `app/` (Expo / React Native)
| Paket / modul native | Versi | Lisensi | Terakhir dirilis (pemeliharaan) | Catatan |
|---|---|---|---|---|
| Expo SDK, React Native | `[ISI]` | `[ISI]` | `[ISI]` | ADR 004; upgrade 1–2×/tahun |
| VisionCamera / fast-tflite / modul MediaPipe | `[ISI]` | `[ISI]` | `[ISI]` | risiko pemeliharaan |
| SQLCipher, expo-sqlite, expo-secure-store | `[ISI]` | `[ISI]` | `[ISI]` | |
| Pustaka kripto Ed25519 (verifikasi manifes) | `[ISI]` | `[ISI]` | `[ISI]` | |

## Layanan pihak ketiga (bukan paket, tetap diinventaris)
| Layanan | Data yang lewat | Lokasi | Catatan |
|---|---|---|---|
| Supabase Free | Akun, data tersinkron, foto dataset (bila kelak) | Singapura | `OPEN_LEGAL_QUESTIONS.md` Q-01 |
| Resend Free | Alamat email orang tua + tautan | Luar negeri `[ISI]` | ADR 008 |
| Azure Speech | Tidak ada data pengguna (build-time) | Jepang | ADR 007 |
| EAS Build (Expo) | Kode app (tertutup) | `[ISI]` | ADR 008 |
| GitHub Actions (keep-alive/ CI) | Tidak boleh data kesehatan | - | ADR 008 |
