# LICENSES — gerbang lisensi ML (dataset · model · dependensi)

> Wajib menurut `backend/CLAUDE.md` §3.4, ADR 001 dan ADR 008 (repo tertutup, Rp0). Versi mesin-baca: [`license_gate.yaml`](license_gate.yaml) — dibaca `train.py` dan `export.py`; tes `test_license_gate.py` memastikan setiap id di YAML dan setiap dependensi di `pyproject.toml` tercatat di sini.
> Aturan: **"terverifikasi" hanya bila halaman rekaman/ berkas LICENSE benar-benar dibaca** (sumber + tanggal dicatat). Sisanya `[PERLU VERIFIKASI]`. Lisensi bobot pra-latih dicatat **terpisah** dari lisensi kode.

## Aturan gerbang
1. **Ultralytics/YOLOv8 (AGPL-3.0) dan pustaka AGPL/GPL tidak boleh** masuk dependensi, impor, bobot, atau build. Dicek otomatis (`forbidden_packages` + pindai impor).
2. **CC BY** → atribusi di layar *Tentang* (FR-122); teks atribusi ada di `license_gate.yaml` dan disalin ke `labels.json` → `attribution`.
3. **CC BY-SA (AlphaDent) tidak dipakai untuk model yang dikirim** (status `internal_only`).
4. **Dataset web tanpa izin (Kaggle "oral diseases") dilarang** (status `forbidden`, tidak bisa dibuka dengan flag apa pun).
5. **Data anak lokal tidak pernah diunggah ke Kaggle/Colab/ layanan pihak ketiga** kecuali consent + izin etik (KEPK) mencakupnya. Latih di PC tim/ lab kampus. `train.py` menolak dataset dengan `may_upload_to_third_party_compute: false` bila mendeteksi Kaggle/Colab.
6. Lisensi kode **atau** bobot belum terverifikasi → `train.py`/`export.py` menolak, kecuali `--allow-unverified-license`; run/bundle ditandai **`internal_experiment_only`** dan tidak boleh didistribusikan. `export.py --release` selalu menolak bila ada yang belum terverifikasi.

## Dataset

| id | Dataset | Sumber | Lisensi | Terverifikasi? | Status untuk model yang dikirim |
|---|---|---|---|---|---|
| `ahmed2025` | Annotated intraoral image dataset for dental caries detection (Ahmed, Ghori, Khalid, Nooruddin, Adnan, Lal, Umer — Aga Khan University), v1, 6.313 foto, YOLO/COCO/VOC/LabelMe | [Zenodo 14827784](https://zenodo.org/records/14827784), DOI 10.5281/zenodo.14827784 | **CC BY 4.0** | **Ya** — halaman rekaman Zenodo dibaca 2026-10-08 (lisensi, penulis, berkas `Dataset.zip` 1,6 GB md5 `89307871ac2f08f4a8f3a7da1f18db31`, `Benchmarking Dataset.zip` 1,6 GB md5 `852520f16faf65f4704ee06bd82561fa`, `Data tree.txt`) | **Boleh** (dengan atribusi). Data publik → Kaggle/Colab boleh |
| `dentalai` | DENTALAI (2.495 gambar; tooth/caries/cavity/crack) | [Dataset Ninja](https://datasetninja.com/dentalai) (agregator) | CC BY 4.0 menurut agregator | **Tidak** — penerbit asli & lisensi asli `[PERLU VERIFIKASI]` | `blocked_unverified` (hanya eksperimen internal) |
| `alphadent` | AlphaDent (DSLR + cermin, 295 pasien) | [arXiv 2507.22512](https://arxiv.org/abs/2507.22512) | CC BY-SA 4.0 (menurut halaman arXiv; per berkas `[PERLU VERIFIKASI]`) | Tidak | **`internal_only` — tidak pernah untuk model yang dikirim** |
| `kaggle_oral_diseases` | Kaggle "oral diseases" (gambar dari situs web gigi) | — | Tidak jelas | — | **`forbidden`** |
| `local_children_id` | Foto anak Indonesia (masa depan) | Pengumpulan DentisAI | Data pribadi kesehatan anak (UU PDP 27/2022) | — | `blocked_unverified` sampai KEPK + consent (c); **tidak ke Kaggle/Colab** |

### Atribusi `ahmed2025` (untuk layar Tentang)
- **ID:** Model ini dilatih memakai data dari "Annotated intraoral image dataset for dental caries detection" oleh S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal dan F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, lisensi CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Perubahan: label dipetakan ulang ke kelas DentisAI dan ukuran gambar diubah.
- **EN:** Contains data from "Annotated intraoral image dataset for dental caries detection" by S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, licensed under CC BY 4.0. Changes: annotations re-mapped to DentisAI classes, images resized; the dataset was used to train a model.
- Artikel pendamping *Scientific Data* 12:1297 (2025) — metadata artikel `[PERLU VERIFIKASI]` terhadap situs jurnal.

## Kandidat model (lisensi kode **dan** bobot)

| id | Kode | Bobot pra-latih | Catatan |
|---|---|---|---|
| `mediapipe_modelmaker_mobilenetv2_ssd` | **Apache-2.0 — terverifikasi** ([LICENSE repo google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE) dibaca 2026-10-08; metadata PyPI `mediapipe-model-maker` 0.2.1.4: "Apache 2.0") | `[PERLU VERIFIKASI]` — halaman dokumentasi tidak menyebut lisensi checkpoint MobileNetV2/MultiHW-AVG | Halaman dokumentasi Model Maker berlabel **Deprecated** ("no longer actively maintained"); rilis PyPI terakhir 2024-04-22; classifier Python 3.8–3.10 → lingkungan Python 3.10 terpisah |
| `yolox_nano` | **Apache-2.0 — terverifikasi** ([LICENSE](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE), "Copyright (c) 2021-2022 Megvii Inc.", dibaca 2026-10-08) | `[PERLU VERIFIKASI]` — `yolox_nano.pth` (GitHub Releases), pra-latih COCO | PyPI `yolox` 0.3.0 terakhir 2022-04-22 → pasang dari commit git yang dipin |
| `efficientdet_lite0` | **Apache-2.0 — terverifikasi** ([google/automl LICENSE](https://github.com/google/automl/blob/master/LICENSE), "Copyright 2020 Google Research", dibaca 2026-10-08) | `[PERLU VERIFIKASI]` — halaman Kaggle Models/TF Hub tidak dapat dibaca | Jalur latih `tflite-model-maker` (diarsipkan) `[PERLU VERIFIKASI]` |
| `rfdetr_nano` | Apache-2.0 menurut README ([roboflow/rf-detr](https://github.com/roboflow/rf-detr), dibaca 2026-10-08); berkas LICENSE di `main` mengembalikan 404 → `[PERLU VERIFIKASI]` | Apache-2.0 untuk Nano/Small/Medium/Large menurut tabel README; **XL/2XL (dan menurut tabel Atto/Femto/Pico) = PML 1.0 → dilarang** | Prioritas rendah; jalur TFLite tidak terdokumentasi |
| `ultralytics_yolov8` | AGPL-3.0 (riset `03_ai_components.md` §3.1.3) | AGPL-3.0 | **`forbidden_in_product`** — tidak ada di pipeline ini |

Catatan bobot pra-latih COCO: gambar COCO berasal dari Flickr dengan lisensi beragam; anotasi COCO CC BY 4.0 — dampaknya pada bobot turunan `[PERLU VERIFIKASI]` (tinjauan hukum).

## Dependensi (backend/ml/detection/pyproject.toml)

Versi = yang di-resolve `uv.lock` pada 2026-10-09. Lisensi paket dasar & dev **dibaca dari metadata paket terpasang** (`importlib.metadata`); paket opsional tidak dipasang di mesin ini → `[PERLU VERIFIKASI]` + jalankan `pip-licenses` (termasuk dependensi transitif) di lingkungan latih sebelum dipakai.

| Paket | Versi | Grup | Lisensi | Sumber |
|---|---|---|---|---|
| `numpy` | 2.5.3 | dasar | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | metadata terpasang |
| `pillow` | 12.3.0 | dasar | MIT-CMU | metadata terpasang |
| `pyyaml` | 6.0.3 | dasar | MIT | metadata terpasang |
| `jsonschema` | 4.26.0 | dasar | MIT | metadata terpasang |
| `defusedxml` | 0.7.1 | dasar (parser VOC XML aman) | PSF License | metadata terpasang |
| `pytest` | 9.1.1 | dev | MIT | metadata terpasang |
| `pytest-cov` | 7.1.0 | dev | MIT | metadata terpasang |
| `ruff` | 0.16.10 | dev | MIT | metadata terpasang |
| `mypy` | 2.4.0 | dev | MIT | metadata terpasang |
| `types-pyyaml` | 6.0.12.20260906 | dev | Apache-2.0 | metadata terpasang |
| `types-jsonschema` | 4.26.0.20261006 | dev | Apache-2.0 | metadata terpasang |
| `types-defusedxml` | 0.7.0.20260504 | dev | Apache-2.0 | metadata terpasang |
| `torch` | 2.14.1 | extra `train` | BSD-3-Clause `[PERLU VERIFIKASI]` | — |
| `torchvision` | 0.29.1 | extra `train` | BSD-3-Clause `[PERLU VERIFIKASI]` | — |
| `pycocotools` | 2.0.11 | extra `train` | BSD-2-Clause `[PERLU VERIFIKASI]` | — |
| `rfdetr` | 1.11.2 | extra `rfdetr` | Apache-2.0 (README) `[PERLU VERIFIKASI]` + dependensi transitif | README |
| `onnx` | 1.23.2 | extra `export` (Linux) | Apache-2.0 `[PERLU VERIFIKASI]` | — |
| `onnx2tf` | 1.28.8 | extra `export` (Linux) | MIT `[PERLU VERIFIKASI]` | — |
| `tensorflow` | 2.21.0 | extra `export` (Linux) | Apache-2.0 `[PERLU VERIFIKASI]` | — |
| `ai-edge-litert` | 2.3.0 | extra `export` (Linux) | Apache-2.0 `[PERLU VERIFIKASI]` | — |
| `mlflow-skinny` | 3.17.0 | extra `tracking` (MLflow lokal) | Apache-2.0 `[PERLU VERIFIKASI]` | — |

Tidak dipasang lewat pyproject (lingkungan terpisah, lisensi `[PERLU VERIFIKASI]`): `mediapipe-model-maker` 0.2.1.4 (Apache 2.0 menurut PyPI), `tflite-model-maker`, YOLOX (git, Apache-2.0).
