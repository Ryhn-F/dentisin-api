# Mengambil dataset Ahmed 2025 (manual — tidak ada data di repo)

Dataset: *Annotated intraoral image dataset for dental caries detection*, Zenodo
[14827784](https://zenodo.org/records/14827784), DOI 10.5281/zenodo.14827784, **CC BY 4.0**
(lisensi diverifikasi dari halaman rekaman pada 2026-10-08; lihat `../LICENSES.md`).

> Data publik → boleh diunduh di Kaggle/Colab atau PC tim. **Jangan** pernah meng-commit gambar/anotasi;
> `backend/.gitignore` mengabaikan `ml/data/raw/` dan `ml/data/processed/`.

## 1. Unduh
Berkas di rekaman v1 (dibaca 2026-10-08):

| Berkas | Ukuran | MD5 (dari Zenodo) |
|---|---|---|
| `Dataset.zip` | 1,6 GB | `89307871ac2f08f4a8f3a7da1f18db31` |
| `Benchmarking Dataset.zip` | 1,6 GB | `852520f16faf65f4704ee06bd82561fa` |
| `Data tree.txt` | 4,1 kB | `a56d8d3964bb2352d5ebb706c7a763dc` |

```bash
# dari backend/ml/data
mkdir -p raw/ahmed2025 && cd raw/ahmed2025
curl -L -o Dataset.zip "https://zenodo.org/records/14827784/files/Dataset.zip?download=1"
curl -L -o "Data tree.txt" "https://zenodo.org/records/14827784/files/Data%20tree.txt?download=1"
md5sum Dataset.zip "Data tree.txt"        # harus sama dengan tabel di atas
sha256sum Dataset.zip > Dataset.zip.sha256  # catat; tempel nilainya di bawah
unzip -q Dataset.zip
```
SHA-256 `Dataset.zip` (diunduh & MD5 dicocokkan 2026-10-09): `4ea6f6be818702041d07409aa982ee1fb84e096bf53621475dba98e56551fc4c`.
Zenodo kadang lambat (~60–200 KB/s per koneksi, 502/504 sesekali): unduh dengan beberapa koneksi Range paralel + resume, lalu cocokkan MD5.
Ekstrak ke folder **baru** `raw/ahmed2025/extracted/` (±1,7 GB); `verify_dataset.py --zip` dan `ahmed2025_groups.py --zip` juga bisa membaca zip tanpa ekstrak.

`Benchmarking Dataset.zip` berisi split train/valid/test bawaan penulis — **terbukti bocor antar pasien** (736/1.122 rekaman
tersebar di beberapa split; lihat `../DATASET_REPORT.md` §6). Jangan dipakai; tidak perlu diunduh.

## 2–4. Struktur nyata, kelas, ID pasien, split → `../DATASET_REPORT.md`
Ringkas (diperiksa pada data 2026-10-09):
- `Dataset/Images/{retractors,no_retractors,pilot}/<view>/*.jpg` (tanpa subfolder bersarang), 6.265 JPEG; label hanya untuk 2.245 gambar.
- YOLO id 0 = `D` (gigi permanen), id 1 = `d` (gigi sulung); **tidak ada** kavitas/non-kavitas → kelas v1 (ADR 014): `D` → `caries_permanent`, `d` → `caries_primary`; keparahan tidak pernah dari ukuran kotak.
- Tidak ada ID pasien; nama berkas memuat nomor rekaman app + timestamp → grup **heuristik** (`../DATASET_REPORT.md` §4).

```bash
cd backend/ml/detection
uv run python ../data/scripts/verify_dataset.py --images ../data/raw/ahmed2025/extracted/Dataset/Images   --labels ../data/raw/ahmed2025/extracted/Dataset/Annotations/Darknet_YOLO --num-classes 2 --class-names D,d   --expected-images 6265 --orphan-labels warn --report ../data/processed/ahmed2025/verify_report.json
uv run python ../data/scripts/ahmed2025_groups.py --root ../data/raw/ahmed2025/extracted --out-dir ../data/processed/ahmed2025
uv run python ../data/scripts/make_splits.py --canonical ../data/processed/ahmed2025/split_input.jsonl   --out-dir ../data/splits/ahmed2025 --group-mode session_csv --metadata-csv ../data/processed/ahmed2025/groups.csv --seed 20261008
# konversi ke canonical.jsonl (class map sesuai ADR 014; butuh canonical.py dengan kelas caries_permanent/caries_primary):
uv run python convert_annotations.py to-canonical --in-format yolo --root ../data/raw/ahmed2025/extracted   --images Dataset/Images --labels Dataset/Annotations/Darknet_YOLO --missing-label negative --on-invalid drop   --class-map ../data/class_maps/ahmed2025.yaml --source ahmed2025 --out ../data/processed/ahmed2025/canonical.jsonl
```
Kunci gambar di `splits/ahmed2025/splits.json` = `Dataset/Images/...` (relatif ke `extracted/`), sama dengan keluaran konverter.
`splits_sha256` = `61b59b7b2a8a98908afe18b0419f457ccd0ac3dda47f3a53d28a570bab1fb625`.
