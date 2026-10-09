# Bake-off detektor permisif (ADR 001)

Tujuan: memilih **satu** detektor bounding box berlisensi permisif untuk v1 dan menulis hasilnya sebagai ADR baru.
Belum dijalankan (2026-10-09). **Bukan validasi klinis.**

## Aturan main
1. **Data & split identik** untuk semua kandidat: `canonical.jsonl` + `splits.json` yang sama
   (`compare.py` menolak laporan dengan `split_sha256`/`gt_sha256` berbeda). Split per pasien (`make_splits.py`).
2. Ambang skor dipilih pada **val**, metrik akhir pada **test**, sekali per kandidat.
3. Setiap run: `train.py` → `ml/runs/<id>/run.json` (seed, hash split, lingkungan, status gerbang lisensi).
   Run dengan lisensi belum terverifikasi = `internal_experiment_only`.
4. Latensi & ukuran diukur pada HP nyata (spike S1): ≥ 2 perangkat RAM 3 GB dan 4 GB, LiteRT CPU/XNNPACK,
   median ≥ 30 inferensi setelah 5 pemanasan, termasuk pra-proses + dekode + NMS; catat model HP & versi Android.
5. Paritas INT8: `check_int8_parity.py` pada ≥ 50 gambar val (cosine ≥ 0,98; kecocokan deteksi ≥ 0,90 — default teknis).

## Kandidat (configs/)
| Prioritas | Config | Jalur ekspor | Risiko utama |
|---|---|---|---|
| tinggi | `mediapipe_modelmaker_mobilenetv2_ssd.yaml` | QAT → `export_model()` | Model Maker *deprecated*, Python ≤ 3.10; lisensi bobot |
| tinggi | `yolox_nano.yaml` | ONNX (tanpa decode) → onnx2tf INT8, dekoder di app | Kesetaraan INT8; lisensi bobot |
| sedang | `efficientdet_lite0.yaml` | tflite-model-maker (diarsipkan) | Toolchain tua; lisensi bobot |
| rendah | `rfdetr_nano.yaml` | ONNX → onnx2tf (tak terdokumentasi) | Transformer + INT8; ukuran |

## Langkah
```bash
uv run python eval.py --pred preds_<cand>_test.jsonl --split test --metadata meta.csv --candidate <cand> --report out/<cand>/report.json
uv run python bake_off/compare.py --report <cand>=out/<cand>/report.json ... --meta bake_off/measurements.yaml --out bake_off/report.md
```
`measurements.yaml` (diisi tangan dari spike S1) — format di docstring `compare.py`.
Kerangka laporan siap-ADR: `uv run python bake_off/compare.py --template --out bake_off/report_template.md`
(dibangkitkan dari header tabel yang sama dengan `compare.py`, sehingga kolom selalu sinkron).
Salin hasil ke ADR berikutnya (`../docs/decisions/`, oleh pemilik ADR).
