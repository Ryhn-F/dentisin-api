# ml/detection — detektor karies bounding box (permisif) · DentisAI

> **Belum ada model terlatih dan belum ada klaim akurasi.** Folder ini berisi pipeline yang teruji
> pada data **sintetis**; pelatihan dijalankan tim di Kaggle/Colab (data publik) atau PC tim (data
> lokal). Semua hasil = **skrining "indikasi / perlu diperiksa"**, bukan diagnosis. Path relatif
> terhadap `backend/` (ADR 009).

## Ringkasan
| Hal | Isi |
|---|---|
| Tugas | Deteksi bounding box **indikasi karies pada gigi tetap** (`caries_permanent`, label Ahmed "D") dan **gigi sulung** (`caries_primary`, "d") — ADR 014. Kotak = seluruh gigi, **hanya overlay UI; ukuran kotak tidak pernah dipakai untuk keparahan**. Tidak ada pembeda berlubang/ belum berlubang |
| Data | Ahmed 2025 (Zenodo 14827784, **CC BY 4.0**, 6.313 foto smartphone, satu perangkat, usia 10–24 th, Sindh, Pakistan). Lihat `../data/` |
| Lisensi | Hanya kode **dan** bobot permisif. **Ultralytics/YOLOv8 (AGPL) tidak dipakai** (ADR 001/008); dicek otomatis. Gerbang: `../data/LICENSES.md` + `license_gate.yaml` |
| Kandidat bake-off | `configs/`: MediaPipe Model Maker MobileNetV2-SSD, YOLOX-Nano, EfficientDet-Lite0, RF-DETR Nano (prioritas rendah) — **data & split identik** |
| Target (hipotesis, belum terbukti) | INT8 ≤ 15 MB (gerbang di `export.py`), latensi ≤ 1,5 s di HP RAM 3–4 GB (spike S1), sens "ada indikasi karies" (gambar) ≥ 0,85 pada foto layak (PRD §3.3) |
| Pelacakan eksperimen | `ml/runs/<run_id>/run.json` (berkas lokal; opsional MLflow **lokal** `--mlflow` → `ml/runs/mlruns`). **Tanpa Azure ML** |

## Alur
```bash
cd backend/ml/detection && uv sync                 # dasar: numpy, pillow, pyyaml, jsonschema, defusedxml
# 1. data (lihat ../data/scripts/fetch_ahmed2025.md): verify_dataset → class map (drg) → convert → make_splits
# 2. latih (Kaggle/Colab/PC tim; extra opsional: uv sync --extra train / --extra rfdetr)
uv run python train.py --cfg configs/yolox_nano.yaml --dry-run --allow-unverified-license
uv run python train.py --cfg configs/yolox_nano.yaml --allow-unverified-license   # internal_experiment_only
# 3. evaluasi (prediksi JSONL {image, boxes:[{cls,x1,y1,x2,y2,score}]} dibuat dari model pada split)
uv run python eval.py --pred out/yolox_test_preds.jsonl --split test --metadata meta.csv --report out/yolox/report.json
# 4. bandingkan kandidat → tabel ADR
uv run python bake_off/compare.py --report yolox_nano=out/yolox/report.json --report ...=... --meta bake_off/measurements.yaml --out bake_off/report.md
# 5. ekspor bundle kontrak (extra export, Linux, bila perlu konversi ONNX)
uv run python export.py --cfg configs/yolox_nano.yaml --format tflite --int8 --tflite-in model_int8.tflite --out ../exports/0.1.0 --allow-unverified-license
uv run python export.py verify-bundle ../exports/0.1.0
```

| Berkas | Fungsi |
|---|---|
| `canonical.py` | Format internal kanonik + validasi kotak (dalam batas, tidak degenerate) |
| `convert_annotations.py` | YOLO/COCO/VOC (VOC via `defusedxml`) → kanonik; penulis tata letak MediaPipe COCO, YOLOX COCO, Roboflow COCO (RF-DETR), VOC (EfficientDet). Peta kelas wajib eksplisit; `null` = menolak |
| `train.py` + `trainers/` | Dispatcher tipis: validasi config (`config.schema.json`), gerbang lisensi, hash data + split, catatan run. Adaptor diimpor malas; **adaptor tidak pernah dijalankan di repo ini** (tanpa GPU) |
| `eval.py` + `report.schema.json` | Metrik numpy murni (definisi di docstring) |
| `export.py` + `labels.schema.json` | Bundle `model.tflite, labels.json, MODEL_CARD.md, SHA256`; gerbang ukuran; validator; `verify-bundle` |
| `decode.py` + `DECODE_SPEC.md` + `tests/decode_vectors.json` | Spesifikasi dekode yang di-port app (TS), vektor paritas |
| `check_int8_parity.py` | Paritas float vs INT8 (tensor: max/mean abs error, cosine; deteksi: kecocokan IoU ≥ 0,5) untuk callable apa pun |
| `quality_gate.py` + `quality_gate.yaml` + `tests/quality_gate_vectors.json` | Gerbang kualitas foto (blur, kecerahan, heuristik gigi terlihat), ambang PLACEHOLDER |
| `bake_off/` | README, template laporan, `compare.py` (`--adr` mengisi `ADR-013-template.md`) |
| `preprocess.py` | Pra-proses referensi (DECODE_SPEC §1) + set kalibrasi INT8/ sampel paritas (`calib`, split train/val) |
| `predict.py` | Jalankan `.tflite`/`.onnx` pada split → JSONL prediksi untuk `eval.py` (pra-proses + dekode yang sama dengan app) |
| `thresholds.py` | Ambang skor per kelas dipilih di **val** (status `set_on_val`, tetap butuh tinjauan drg untuk rilis) |
| `artifacts.py` | `collect` (zip unduhan notebook; menolak gambar/`.npy`/checkpoint) · `pack-code` (zip kode untuk dataset privat Kaggle) |
| `WEIGHT_LICENSES.md` | Verifikasi lisensi **bobot** pra-latih tiap kandidat (sumber primer, 2026-10-09) + patch `license_gate.yaml` |
| `kaggle/` | Notebook Kaggle/Colab (`*_kaggle.py` sumber, `.ipynb` dibangkitkan `make_ipynb.py`), `nbkit.py`, `mm_train_py310.py`, **`RUNBOOK.md`** |

## Metrik (`eval.py`)
Per kelas (gigi tetap dan gigi sulung **terpisah**) + `any_caries` ("ada indikasi karies pada gigi"): tingkat **gambar** (sens, spes, PPV, NPV, matriks 2×2)
dan tingkat **kotak** IoU ≥ 0,5 (sens, PPV; spes/NPV tidak terdefinisi → `null`), AP50 (interpolasi semua titik) dan
mAP50 (AP50 per kelas tetap/ sulung), matriks kebingungan kotak multi-kelas, subgrup dari CSV metadata (`phone_model`,
`lighting`, `source`; **tanpa subgrup ukuran kotak**), **CI 95% bootstrap atas pasien** (fallback gambar + peringatan bila `patient_id` tidak ada).
Ambang skor = PLACEHOLDER sampai ditetapkan pada split val (bukan test) dan ditinjau drg.

## KETERBATASAN YANG DIKETAHUI
1. **Satu perangkat** pada data latih → generalisasi ke HP lain (kamera, ISP, senter) tidak diketahui.
2. **Usia 10–24 th, Sindh (Pakistan)** → anak SD Indonesia (7–12 th, banyak gigi sulung) di luar distribusi; **tidak ada data anak Indonesia**.
3. **Hanya kotak** (seluruh gigi bertanda karies), tanpa kelas `tooth` → triase memakai kelas, skor, dan **jumlah** temuan; ukuran/ luas kotak tidak pernah dipakai (ADR 014).
4. Dataset **tidak membedakan berlubang vs belum berlubang** (label hanya D/d = jenis gigi; ADR 014); lesi awal tidak andal dari foto smartphone (literatur sens 0–38%). Tidak ada klaim keparahan.
5. **ID pasien tidak terdokumentasi** di dataset → split per pasien mungkin heuristik (lihat `fetch_ahmed2025.md` §4) → metrik bisa optimistis.
6. Foto data latih kemungkinan berkualitas "klinis" (retraktor, dokter) — foto orang tua/kader lebih buruk.
7. Hanya gigi depan/ permukaan terlihat; sela-sela dan gigi belakang tidak dinilai.
8. Heuristik "gigi terlihat" bukan detektor gigi (kertas putih + objek merah bisa lolos). Semua ambang quality gate PLACEHOLDER (FR-21: ≥ 200 foto berlabel).
9. Format keluaran tiap kandidat (`output_format_verified: false`) belum dibaca dari model nyata. Lisensi bobot: lihat `WEIGHT_LICENSES.md`
   (MediaPipe MobileNetV2 & EfficientDet-Lite0 Apache-2.0 terverifikasi; **YOLOX-Nano tidak eksplisit**; RF-DETR-N gugur karena ukuran).
10. Adaptor pelatihan ditulis dari API/kode sumber tetapi **belum pernah dijalankan**; MediaPipe Model Maker sudah *deprecated* (Python 3.10).
11. Konversi onnx2tf 2.6.9 hanya terbukti pada graf acak kecil: backend bawaan `flatbuffer_direct` memberi paritas INT8 buruk → notebook
    mencoba `tf_converter` juga dan memilih berdasarkan paritas.

## Tes
`uv sync && uv run pytest -q` (cakupan ≥ 90% ditegakkan; termasuk `../data/scripts`), `uv run ruff check . ../data/scripts`, `uv run mypy`.
Semua data uji sintetis (gambar numpy/Pillow kecil, anotasi buatan) — tidak ada foto pasien.
