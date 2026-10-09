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
SHA-256 `Dataset.zip` (diisi oleh orang pertama yang mengunduh): `PLACEHOLDER — [PERLU VERIFIKASI]`

`Benchmarking Dataset.zip` berisi split train/valid/test bawaan penulis. **Jangan** dipakai untuk
klaim kita kecuali terbukti terpisah per pasien; boleh dilaporkan terpisah sebagai pembanding.

## 2. Struktur yang diharapkan (dari `Data tree.txt`)
```
Dataset/
  Images/{no_retractors,pilot,retractors}/{Frontal,Left_Lateral,Mandibular,Maxillary_Occlusal,Right_Lateral}/...
         (no_retractors punya subfolder bersarang, mis. Frontal/Frontal/)
  Annotations/Darknet_YOLO/{no_retractors,pilot,retractors}/<view>/*.txt
  Annotations/{Labelme,pascal-voc}/... · Annotations/MS_coco/
```
Folder menunjukkan **5 pandangan per anak** (frontal, lateral kiri/kanan, rahang bawah, oklusal atas)
→ satu pasien punya beberapa foto → split wajib per pasien.

## 3. Verifikasi, petakan kelas, split
```bash
cd backend/ml/detection
# (a) cek struktur, pasangan gambar/label, berkas non-gambar, id kelas
uv run python ../data/scripts/verify_dataset.py --images ../data/raw/ahmed2025/Dataset/Images \
  --labels ../data/raw/ahmed2025/Dataset/Annotations/Darknet_YOLO --num-classes <n> \
  --expected-images 6313 --report ../data/processed/ahmed2025/verify_report.json
# (b) baca nama kelas asli (COCO categories / classes.txt), isi ../data/class_maps/ahmed2025.yaml
#     bersama drg pembimbing (pemetaan kavitas vs non-kavitas = keputusan klinis)
# (c) konversi ke format kanonik
uv run python convert_annotations.py to-canonical --in-format yolo --root ../data/raw/ahmed2025 \
  --images Dataset/Images --labels Dataset/Annotations/Darknet_YOLO \
  --class-map ../data/class_maps/ahmed2025.yaml --source ahmed2025 \
  --out ../data/processed/ahmed2025/canonical.jsonl
# (d) split per pasien — lihat bagian 4 untuk memilih --group-mode
uv run python ../data/scripts/make_splits.py --canonical ../data/processed/ahmed2025/canonical.jsonl \
  --out-dir ../data/processed/ahmed2025/splits --group-mode <mode> --images-root ../data/raw/ahmed2025 \
  --merge-near-duplicates --seed 20261008
```

## 4. ID pasien — keterbatasan yang harus dicatat
Halaman Zenodo dan `Data tree.txt` **tidak menyebut ID pasien** (hanya folder; nama berkas belum
dilihat). Urutan pilihan:
1. Nama berkas berisi ID anak (mis. `P012_frontal.jpg`) → `--group-mode regex --patient-regex "(?P<patient>...)"`.
2. Metadata/ artikel *Scientific Data* menyediakan tabel ID → CSV → `--group-mode metadata_csv`.
3. Tidak ada keduanya → `--group-mode exif_session` (waktu EXIF, celah default 120 s per folder;
   satu perangkat dipakai, sehingga sesi ≈ satu anak) **+** `--merge-near-duplicates`.
   Catat sebagai keterbatasan (manifest menulis `group_quality: heuristic_session`).
4. Terakhir: `--group-mode image` = **tanpa perlindungan pasien**; hasilnya tidak boleh dipakai untuk klaim.
Hasil pilihan ini + `splits_sha256` dicatat di laporan bake-off.
