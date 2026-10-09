# ml/ — pipeline ML DentisAI

Path relatif terhadap `backend/` (root backend mandiri, ADR 009). Tidak ada dataset, foto, atau bobot di repo.

| Folder | Isi | Pemilik |
|---|---|---|
| [`data/`](data/README.md) | Gerbang lisensi (`LICENSES.md`, `license_gate.yaml`), peta kelas, skrip verifikasi & split per pasien | ML |
| [`detection/`](detection/README.md) | Detektor karies bounding box (permisif): konversi anotasi, dispatcher latih, eval, ekspor bundle, quality gate, bake-off | ML |
| `brushing/` | Prototipe sikat gigi (lihat README-nya) | engineer lain |
| `exports/<semver>/` | Bundle kontrak ke app: `model.tflite`, `labels.json`, `MODEL_CARD.md`, `SHA256` (dibuat `detection/export.py`; ditandatangani `tools/sign_manifest.py`) | ML |
| `runs/` | Catatan run lokal (JSON) + MLflow lokal opsional — di-gitignore | — |

## Prinsip
- **Skrining, bukan diagnosis**; tidak ada klaim akurasi sebelum validasi klinis (PRD §12, ADR 002). Setiap bundle bertanda **NOT VALIDATED**.
- **Hanya lisensi permisif** (kode **dan** bobot); Ultralytics/YOLOv8/AGPL dilarang (ADR 001/008) — ditegakkan oleh tes.
- **Data anak lokal tidak pernah ke Kaggle/Colab**; data publik boleh. Azure Student tanpa GPU → tidak ada Azure ML.
- Eksperimen dilacak di berkas lokal / MLflow lokal.
- Ambang (skor, quality gate) = `PLACEHOLDER` sampai ditetapkan pada data validasi dan ditinjau drg; rilis menolak PLACEHOLDER.

## Mulai
```bash
cd backend/ml/detection && uv sync && uv run pytest -q && uv run ruff check . ../data/scripts && uv run mypy
```
Tidak ada variabel lingkungan baru.
