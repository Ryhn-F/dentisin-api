# ml/data — dataset, gerbang lisensi, skrip (tanpa data)

Path relatif terhadap `backend/` (ADR 009). **Tidak ada gambar, anotasi, atau dataset di repo.**
`backend/.gitignore` mengabaikan `ml/data/raw/`, `ml/data/processed/`, `ml/runs/`.

| Berkas | Isi |
|---|---|
| [`LICENSES.md`](LICENSES.md) | Gerbang lisensi: dataset, kandidat model (kode **dan** bobot), dependensi. Sumber + tanggal verifikasi |
| [`license_gate.yaml`](license_gate.yaml) | Versi mesin-baca; dipakai `train.py`/`export.py`; diuji sinkron dengan `LICENSES.md` |
| [`DATASET_REPORT.md`](DATASET_REPORT.md) | Laporan Ahmed 2025 dari data nyata: MD5, struktur, kelas `D`/`d`, bukti ID pasien, split, kebocoran, keterbatasan |
| [`class_maps/ahmed2025.yaml`](class_maps/ahmed2025.yaml) | Pemetaan label sumber → kelas kanonik. Sisi sumber terverifikasi (`yolo_names: [D, d]`); **ADR 014**: `D` → `caries_permanent`, `d` → `caries_primary` (keputusan pemilik produk 2026-10-10, belum ditinjau drg) |
| [`scripts/fetch_ahmed2025.md`](scripts/fetch_ahmed2025.md) | Langkah unduh manual + checksum + strategi ID pasien |
| [`scripts/verify_dataset.py`](scripts/verify_dataset.py) | (folder **atau zip di tempat**) Cek struktur, jumlah, pasangan gambar/label, berkas non-gambar (ekstensi + magic bytes), id kelas, kotak tidak valid → laporan JSON |
| [`scripts/ahmed2025_groups.py`](scripts/ahmed2025_groups.py) | Grup pasien **heuristik** Ahmed 2025 dari nama berkas (nomor rekaman, timestamp) + EXIF perangkat pilot → `groups.csv`, `split_input.jsonl` (baca zip di tempat atau folder ekstrak) |
| [`scripts/split_stats.py`](scripts/split_stats.py) | Statistik per split + cek kebocoran (grup, rekaman, pasangan duplikat) |
| [`splits/ahmed2025/`](splits/ahmed2025/) | Split yang dipakai (seed 20261008, `session_csv`) + manifest sha256, statistik, `known_issues.csv` — teks kecil, boleh di-commit |
| [`scripts/make_splits.py`](scripts/make_splits.py) | Split train/val/test **per pasien** (field/CSV/`session_csv`/regex/sesi EXIF/near-duplicate dHash), seed deterministik (urutan sha256), stratifikasi positif/negatif, `splits.json` + `split_manifest.json` (sha256, keterbatasan) |

## Aturan
- **Data publik** (`ahmed2025`) → boleh di Kaggle/Colab/PC tim.
- **Data anak lokal tidak pernah diunggah ke Kaggle/Colab/ layanan pihak ketiga** kecuali consent +
  izin etik mencakupnya; latih di PC tim/ lab kampus. `train.py` menolak dataset dengan
  `may_upload_to_third_party_compute: false` bila terdeteksi berjalan di Kaggle/Colab.
- Dataset baru = baris baru di `LICENSES.md` **dan** `license_gate.yaml` sebelum dipakai.
- Split selalu per pasien; `splits_sha256` wajib dicatat di setiap run (`train.py`) dan laporan (`eval.py`).

## Format kanonik (`canonical.jsonl`)
Satu baris per gambar: `{"image", "width", "height", "source", "patient_id", "boxes": [{"cls","x1","y1","x2","y2"}]}`;
koordinat piksel absolut gambar asli, `0 ≤ x1 < x2 ≤ width`; `cls` ∈ {`caries_permanent`, `caries_primary`} (ADR 014; sebelumnya `caries_cavitated`/`caries_noncavitated` — `../detection/canonical.py` menyusul);
`boxes: []` = negatif. Definisi: `../detection/canonical.py`.

Tes skrip data: `scripts/tests/` (dijalankan dari `../detection` lewat `uv run pytest -q`; `testpaths` mencakup folder ini).
