# LICENSES — gerbang lisensi ML (dataset · model · dependensi)

> Wajib menurut `backend/CLAUDE.md` §3.4, ADR 001 dan ADR 008 (repo tertutup, Rp0). Versi mesin-baca: [`license_gate.yaml`](license_gate.yaml) — dibaca `train.py` dan `export.py`; tes `test_license_gate.py` memastikan setiap id di YAML dan setiap dependensi di `pyproject.toml` tercatat di sini.
> Aturan: **"terverifikasi" hanya bila halaman rekaman/ berkas LICENSE benar-benar dibaca** (sumber + tanggal dicatat). Sisanya `[PERLU VERIFIKASI]`. Lisensi bobot pra-latih dicatat **terpisah** dari lisensi kode.

## Aturan gerbang
1. **Ultralytics/YOLOv8 (AGPL-3.0) dan pustaka AGPL/GPL tidak boleh** masuk dependensi, impor, bobot, atau build. Dicek otomatis (`forbidden_packages` + pindai impor).
2. **CC BY** → atribusi di layar *Tentang* (FR-122); teks atribusi ada di `license_gate.yaml` dan disalin ke `labels.json` → `attribution`.
3. **CC BY-SA (AlphaDent) tidak dipakai untuk model yang dikirim** (status `internal_only`).
4. **Dataset web tanpa izin (Kaggle "oral diseases") dilarang** (status `forbidden`, tidak bisa dibuka dengan flag apa pun).
5. **Data anak lokal tidak pernah diunggah ke Kaggle/Colab/ layanan pihak ketiga** kecuali consent + izin etik (KEPK) mencakupnya. Latih di PC tim/ lab kampus. `train.py` menolak dataset dengan `may_upload_to_third_party_compute: false` bila mendeteksi Kaggle/Colab.
6a. `owner_accepted_risk: true` (dengan tanggal, alasan, rujukan ADR) = pemilik produk menerima lisensi yang **belum terverifikasi** untuk lingkup tertentu; status ini terpisah dari `verified` dan tidak pernah membuatnya `true`. Gerbang `train.py`/`export.py` (pemilik `ml/detection`) perlu membedakannya; rilis komersial/publik tetap ditolak.
6. Lisensi kode **atau** bobot belum terverifikasi → `train.py`/`export.py` menolak, kecuali `--allow-unverified-license`; run/bundle ditandai **`internal_experiment_only`** dan tidak boleh didistribusikan. `export.py --release` selalu menolak bila ada yang belum terverifikasi.

## Dataset

| id | Dataset | Sumber | Lisensi | Terverifikasi? | Status untuk model yang dikirim |
|---|---|---|---|---|---|
| `ahmed2025` | Annotated intraoral image dataset for dental caries detection (Ahmed, Ghori, Khalid, Nooruddin, Adnan, Lal, Umer — Aga Khan University), v1 (2025-02-07). Nyata: **6.265 JPEG** (record menyebut 6.313), label YOLO/VOC/LabelMe/COCO kelas `D`/`d` — lihat [`DATASET_REPORT.md`](DATASET_REPORT.md) | [Zenodo 14827784](https://zenodo.org/records/14827784), DOI 10.5281/zenodo.14827784 (concept 10.5281/zenodo.14827783) | **CC BY 4.0** | **Ya** — halaman rekaman (2026-10-08) + Zenodo REST API `license.id = cc-by-4.0` (2026-10-09). `Dataset.zip` 1.576.115.314 B, MD5 `89307871ac2f08f4a8f3a7da1f18db31` **dicocokkan lokal**, SHA-256 `4ea6f6be818702041d07409aa982ee1fb84e096bf53621475dba98e56551fc4c`; `Data tree.txt` MD5 `a56d8d3964bb2352d5ebb706c7a763dc` dicocokkan; `Benchmarking Dataset.zip` MD5 `852520f16faf65f4704ee06bd82561fa` (tidak diunduh) | **Boleh** (dengan atribusi). Data publik → Kaggle/Colab boleh. Split penulis bocor antar pasien → pakai `splits/ahmed2025/` |
| `dentalai` | DENTALAI (2.495 gambar; tooth/caries/cavity/crack) | [Dataset Ninja](https://datasetninja.com/dentalai) (agregator) | CC BY 4.0 menurut agregator | **Tidak** — penerbit asli & lisensi asli `[PERLU VERIFIKASI]` | `blocked_unverified` (hanya eksperimen internal) |
| `alphadent` | AlphaDent (DSLR + cermin, 295 pasien) | [arXiv 2507.22512](https://arxiv.org/abs/2507.22512) | CC BY-SA 4.0 (menurut halaman arXiv; per berkas `[PERLU VERIFIKASI]`) | Tidak | **`internal_only` — tidak pernah untuk model yang dikirim** |
| `kaggle_oral_diseases` | Kaggle "oral diseases" (gambar dari situs web gigi) | — | Tidak jelas | — | **`forbidden`** |
| `local_children_id` | Foto anak Indonesia (masa depan) | Pengumpulan DentisAI | Data pribadi kesehatan anak (UU PDP 27/2022) | — | `blocked_unverified` sampai KEPK + consent (c); **tidak ke Kaggle/Colab** |

### Atribusi `ahmed2025` (untuk layar Tentang)
- **ID:** Model ini dilatih memakai data dari "Annotated intraoral image dataset for dental caries detection" (v1, 2025) oleh S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal dan F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, lisensi CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Perubahan: label dipetakan ulang ke kelas DentisAI dan ukuran gambar diubah.
- **EN:** Contains data from "Annotated intraoral image dataset for dental caries detection" (v1, 2025) by S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, licensed under CC BY 4.0. Changes: annotations re-mapped to DentisAI classes, images resized; the dataset was used to train a model.
- Artikel pendamping (terverifikasi 2026-10-09 via Europe PMC PMC12297690): Ahmed SMF, Ghori MH, Khalid A, Nooruddin A, Adnan N, Lal A, Umer F. *Annotated intraoral image dataset for dental caries detection.* Sci Data 12, 1297 (2025), doi:10.1038/s41597-025-05647-9, PMID 40715095. **Artikelnya berlisensi CC BY-NC-ND 4.0** (berbeda dari dataset): jangan menyalin gambar/teks artikel ke materi produk/pitch; kutip saja.
- Atribusi memakai daftar *creators* Zenodo (nama di Zenodo: "Ghori, Huzaifa"; di artikel: "Muhammad Huzaifa Ghori").

## Kandidat model (lisensi kode **dan** bobot)

| id | Kode | Bobot pra-latih | Catatan |
|---|---|---|---|
| `mediapipe_modelmaker_mobilenetv2_ssd` | **Apache-2.0 — terverifikasi** ([LICENSE repo google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE) dibaca 2026-10-08; metadata PyPI `mediapipe-model-maker` 0.2.1.4: "Apache 2.0") | **Apache-2.0 — terverifikasi, HANYA varian `MOBILENET_V2` (i256)**: [`model_spec.py`](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/model_maker/python/vision/object_detector/model_spec.py) mengunduh `mobilenetv2_ssd_i256_ckpt.tar.gz` dari `tf_model_garden/vision/qat/mobilenetv2_ssd_coco/`; checkpoint yang sama diterbitkan [README QAT TF Model Garden](https://github.com/tensorflow/models/blob/master/official/projects/qat/vision/README.md) (bagian *License*: Apache 2.0; dilatih pada COCO dari nol). Dibaca 2026-10-09 (ML engineer), dicek ulang 2026-10-10 (data engineer). Varian i320/MultiAVG **tidak** terverifikasi | Model Maker **Deprecated** ("no longer actively maintained"); rilis PyPI terakhir 2024-04-22; Python ≤ 3.10 → env terpisah. INT8 hanya via QAT (README QAT: PTQ INT8 mAP 0,04) |
| `yolox_nano` | **Apache-2.0 — terverifikasi** ([LICENSE](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE), "Copyright (c) 2021-2022 Megvii Inc.", dibaca 2026-10-08) | **Tidak terverifikasi — tidak ada lisensi eksplisit**: `yolox_nano.pth` = aset rilis 0.1.1rc0 repo Apache-2.0 tanpa syarat terpisah; pertanyaan ke maintainer [YOLOX#1865](https://github.com/Megvii-BaseDetection/YOLOX/issues/1865) (2026-03-31) masih terbuka, 0 komentar (dicek 2026-10-10). "Tersirat Apache-2.0" ≠ terverifikasi. **Risiko diterima pemilik produk** (`owner_accepted_risk: true`, 2026-10-10, ADR 014 §5): untuk keperluan kompetisi, Apache-2.0 implisit diterima — **bukan** verifikasi; wajib ditinjau ulang (konfirmasi tertulis Megvii atau latih dari inisialisasi acak) sebelum rilis komersial/publik | Pin commit `6ddff4824372906469a7fae2dc3206c7aa4bbaee`; tag < 0.2.0 memuat kode GPL-3.0 YOLOv5 ([#765](https://github.com/Megvii-BaseDetection/YOLOX/issues/765)) → jangan pakai PyPI `yolox` 0.3.0. Bundel YOLOX: boleh untuk kompetisi atas dasar risiko yang diterima pemilik; rilis komersial/publik tetap terblokir sampai terverifikasi |
| `efficientdet_lite0` | **Apache-2.0 — terverifikasi** ([google/automl LICENSE](https://github.com/google/automl/blob/master/LICENSE), "Copyright 2020 Google Research", dibaca 2026-10-08) | **Apache-2.0 — terverifikasi**: Kaggle Models API `GET /api/v1/models/tensorflow/efficientdet/get` → instans `lite0-feature-vector`, `lite0-detection` (TF2), `lite0-detection-default/-metadata`, `lite0-int8` (TFLite) `licenseName: Apache 2.0` (dibaca 2026-10-09, dicek ulang 2026-10-10) | Lisensi OK; masalahnya toolchain: `tflite-model-maker` deprecated (rilis terakhir 2024-01-23) → cadangan saja |
| `rfdetr_nano` | **Apache-2.0 — terverifikasi** ([LICENSE](https://github.com/roboflow/rf-detr/blob/develop/LICENSE), branch default `develop`; dibaca 2026-10-09, dicek ulang 2026-10-10) | **Apache-2.0 — terverifikasi untuk RF-DETR-N** (tabel [README](https://github.com/roboflow/rf-detr/blob/develop/README.md)); Atto/Femto/Pico/XL/2XL = **PML 1.0 → dilarang** | **DIKELUARKAN karena ukuran**: 30,5 juta parameter (README) → ±30 MB walau INT8 > gerbang 15 MB; jalur TFLite tidak terdokumentasi |
| `ultralytics_yolov8` | AGPL-3.0 (riset `03_ai_components.md` §3.1.3) | AGPL-3.0 | **`forbidden_in_product`** — tidak ada di pipeline ini |

Catatan bobot pra-latih COCO ([terms of use](https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/termsofuse.htm)): anotasi CC BY 4.0 (COCO Consortium); hak cipta gambar tidak dimiliki Consortium (ikut syarat Flickr). Semua kandidat dipra-latih pada COCO oleh penerbitnya; kita tidak mendistribusikan gambar COCO. Dampak pada bobot turunan = pertanyaan hukum terbuka (Q-W2 di `../detection/WEIGHT_LICENSES.md` §5), dicatat, tidak memblokir. Detail & bukti per kandidat: [`../detection/WEIGHT_LICENSES.md`](../detection/WEIGHT_LICENSES.md).

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
| `pycocotools` | 2.0.11 | extra `train` | FreeBSD (BSD-2-Clause) | PyPI JSON (2026-10-10) |
| `rfdetr` | 1.11.2 | extra `rfdetr` | Apache-2.0 (LICENSE repo `develop`) + dependensi transitif `[PERLU VERIFIKASI]` | LICENSE dibaca 2026-10-10 |
| `onnx` | 1.23.2 | extra `export` (Linux) | Apache-2.0 | PyPI JSON `license_expression` (2026-10-10) |
| `onnx2tf` | 1.28.8 | extra `export` (Linux) | MIT (PyPI 2.6.9; versi 1.28.8 belum dicek terpisah) | PyPI JSON (2026-10-10) |
| `tensorflow` | 2.21.0 | extra `export` (Linux) | Apache 2.0 | PyPI JSON (2026-10-10) |
| `ai-edge-litert` | 2.3.0 | extra `export` (Linux) | Apache-2.0 (classifier "Apache Software License") | PyPI JSON (2026-10-10) |
| `mlflow-skinny` | 3.17.0 | extra `tracking` (MLflow lokal) | Apache-2.0 `[PERLU VERIFIKASI]` | — |

### Alat khusus waktu latih/ekspor (notebook Kaggle/Colab; **tidak dikirim** di app)
Versi = yang dipin notebook (`../detection/WEIGHT_LICENSES.md` §4). Lisensi dibaca dari metadata PyPI JSON (rilis terbaru) pada 2026-10-10; bila versi pin berbeda dari rilis terbaru, lisensi diasumsikan sama `[PERLU VERIFIKASI per versi]`.

| Paket | Versi pin | Lisensi (PyPI) | Catatan |
|---|---|---|---|
| `onnx2tf` | 2.6.9 | MIT | konversi ONNX → TFLite |
| `onnxruntime` | 1.26.0 | MIT | uji kesetaraan ONNX |
| `onnxsim` | 0.6.5 | MIT AND (Apache-2.0 OR BSD-2-Clause) | — |
| `onnx` (notebook) | 1.20.1 | Apache-2.0 | — |
| `ai-edge-litert` (notebook) | 2.1.2 | Apache-2.0 (classifier) | interpreter TFLite |
| `opencv-python` | 4.13.0.92 | Apache 2.0 | — |
| `mediapipe-model-maker` | 0.2.1.4 | Apache 2.0 | env Python 3.10 terpisah |
| `tensorflow` (env Model Maker) | 2.15.1 | Apache 2.0 | — |
| `loguru` | 0.7.3 | MIT (classifier) | dependensi latih YOLOX |
| `thop` | — | MIT | dependensi latih YOLOX |
| `tabulate` | — | MIT | dependensi latih YOLOX |
| `pycocotools` (notebook) | 2.0.11 | FreeBSD | — |
| `ninja` | 1.13.0 | Apache-2.0 | — |
| `tensorboard` | — | Apache 2.0 | — |
| `torch` (kernel Kaggle/Colab terpasang) | — | BSD-3-style (`[PERLU VERIFIKASI]` ekspresi lengkap) | tidak dipasang oleh kita |
| YOLOX (git commit `6ddff48…`) | — | Apache-2.0 (LICENSE repo) | dipasang `--no-deps` |

Juga tidak lewat pyproject: `tflite-model-maker` 0.4.3 (Apache 2.0 menurut PyPI; deprecated) — hanya bila jalur cadangan EfficientDet dipakai.
