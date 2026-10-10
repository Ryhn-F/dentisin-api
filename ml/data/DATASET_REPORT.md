# DATASET_REPORT — Ahmed et al. 2025 (`ahmed2025`)

> Diperiksa 2026-10-09/10 oleh data engineer pada berkas asli (tidak ada gambar/anotasi di repo).
> Sumber: Zenodo REST API `https://zenodo.org/api/records/14827784` (dibaca 2026-10-09); artikel
> *Sci Data* 12:1297 (2025), doi:10.1038/s41597-025-05647-9, teks lengkap Europe PMC **PMC12297690**
> (artikel berlisensi CC BY-NC-ND — jangan menyalin gambar/teksnya; **dataset** CC BY 4.0).
> Angka di bawah dihitung dari data; skrip: `scripts/verify_dataset.py`, `scripts/ahmed2025_groups.py`,
> `scripts/make_splits.py`, `scripts/split_stats.py`. Artefak kecil (boleh di-commit): `splits/ahmed2025/`.

## 1. Unduhan & integritas
| Berkas | Ukuran (byte) | MD5 Zenodo = lokal | SHA-256 lokal |
|---|---|---|---|
| `Dataset.zip` | 1.576.115.314 | `89307871ac2f08f4a8f3a7da1f18db31` ✔ | `4ea6f6be818702041d07409aa982ee1fb84e096bf53621475dba98e56551fc4c` |
| `Data tree.txt` | 4.133 | `a56d8d3964bb2352d5ebb706c7a763dc` ✔ | `e546fb6b7a006fddc70600bf3a38d448d25910ea810351e2c68f9c28827600b6` |
| `Benchmarking Dataset.zip` | 1.569.536.122 | `852520f16faf65f4704ee06bd82561fa` (tidak diunduh) | — |

- Rekaman: v1 (satu-satunya versi; concept DOI 10.5281/zenodo.14827783), terbit 2025-02-07, lisensi API `cc-by-4.0`.
- `Benchmarking Dataset.zip` **tidak diunduh** (disk penuh saat itu); daftar isinya (12.969 entri, nama + CRC32) dibaca lewat HTTP Range dari central directory zip — cukup untuk analisis kebocoran §6.
- Lokasi lokal (gitignored): `raw/ahmed2025/download/` (zip), `raw/ahmed2025/extracted/Dataset/` (hasil ekstrak, 13.003 berkas). Python yang membaca data dijalankan dengan `-I`; tidak ada berkas data yang dieksekusi.

## 2. Struktur nyata vs dokumentasi
- `Dataset/Images/{retractors,no_retractors,pilot}/{Frontal,Left_Lateral,Mandibular,Maxillary_Occlusal,Right_Lateral}/*.jpg` — **tanpa** subfolder bersarang (`Frontal/Frontal/`) seperti di `Data tree.txt`.
- **6.265 JPEG** (record/artikel menyebut 6.313; benchmark berisi 6.266). retractors 2.753 · no_retractors 2.752 · pilot 760.
- Anotasi: `Darknet_YOLO`, `Labelme`, `pascal-voc` masing-masing **2.245 berkas** (bukan per gambar); `MS_coco/{train,valid,test}.json` hanya 2.227 gambar beranotasi.
- **4.038 gambar tanpa berkas label** + 65 berkas YOLO kosong → diperlakukan negatif (penulis juga memakainya sebagai gambar latar di benchmark). Positif nyata hampir hanya pada tampilan oklusal: Mandibular 494/551, Maxillary 367–374/551, sedangkan Frontal 11–15/550 dan Lateral 33–43/550 → **belum terbukti bahwa tampilan frontal/lateral dianotasi menyeluruh** `[PERLU VERIFIKASI ke penulis/drg]`.
- Cacat data: 18 berkas label pilot tanpa gambar (`known_issues.csv`); **42 baris YOLO di 16 gambar berada di luar gambar** (koordinat > 1; LabelMe sama-sama keliru → anotasi tidak sejajar dengan crop) → keluarkan gambar itu atau buang kotaknya; `verify_dataset.py` sengaja **exit 1** karena ini.
- Resolusi bervariasi (gambar sudah di-crop oleh grid app; 4.083 ukuran berbeda): lebar median 1.493 px (p5 806, p95 3.004), tinggi median 1.076 px, 0,21–12,5 MP; 13 portrait. EXIF orientation hanya pada pilot (5× orientasi 6).

## 3. Kelas (pertanyaan terbuka a)
| id YOLO | nama | arti (artikel) | kotak valid YOLO | LabelMe/VOC |
|---|---|---|---|---|
| 0 | `D` | karies gigi **permanen** | 6.188 | 6.228 |
| 1 | `d` | karies gigi **sulung** | 552 | 554 |

- Pemetaan id dibuktikan dengan mencocokkan setiap baris YOLO dengan kotak LabelMe pada berkas yang sama: 6.228/6.228 `0→D`, 552/552 `1→d` (pusat ≤ 3 px). Tidak ada `classes.txt`.
- **Id kategori COCO berbeda per berkas** (`test.json`: 1=D, 2=d; `train/valid.json`: 1=d, 2=D) → selalu petakan berdasarkan **nama**.
- LabelMe saja: `M` ×44, `m` ×1, 2 bentuk `line` — tidak didokumentasikan, tidak diekspor → `__drop__`.
- Kotak mengelilingi **seluruh gigi** yang tampak berlubang ("tooth with visible decay"), bukan lesinya: area kotak/foto 1–4 % (5.736 kotak), 4–16 % (975). ⇒ "ukuran relatif box" mengukur **ukuran gigi**, bukan luas lesi — **keparahan tidak boleh disimpulkan dari ukuran kotak** (ADR 014 §2; kotak hanya untuk overlay UI).
- **Jawaban (a): TIDAK membedakan kavitas vs non-kavitas.** Label hanya membedakan gigi permanen/sulung. Artikel sendiri menyebut lesi non-kavitas sulit dibedakan dari fluorosis pada foto, sehingga tidak jelas apakah lesi non-kavitas dianotasi. ⇒ Tidak ada data latih untuk kavitas/non-kavitas. **Keputusan (ADR 014, pemilik produk 2026-10-10):** kelas v1 mengikuti label — `D` → `caries_permanent`, `d` → `caries_primary` (`class_maps/ahmed2025.yaml`, `status: decided`; belum ditinjau drg, tanpa klaim klinis); `M`/`m` → `__drop__`. Kelas `caries_cavitated`/`caries_noncavitated` dihapus dari v1. Catatan ketidakseimbangan: `caries_primary` hanya 552 kotak (8 %).

## 4. ID pasien (pertanyaan terbuka b)
**Tidak ada field ID pasien**, tetapi nama berkas memberi sinyal kuat:
- Studi utama: `anonymous_<AAA>-<BBB>-<NNN>-<RR>_<epoch ms>_<View>_View.jpg` (pemisah `-` atau `_`). `AAA-BBB-NNN` = nomor **rekaman app**; `RR` = `01` retractors / `00` no_retractors (100 % konsisten); 5 tampilan satu rekaman berbagi **satu** timestamp. 1.122 rekaman; 1.078 punya 5 gambar.
- Satu rekaman **tidak pernah** muncul di kedua subset, padahal artikel: tiap anak difoto 5× dengan dan 5× tanpa retraktor. Urutan waktu membuktikan pasangannya: retractors `NNN` → no_retractors `NNN+1` (935/1.081 transisi selisih nomor 1), jeda median 528 s (p95 997 s), anak berikutnya median 2.044 s kemudian.
- Pilot: `anonymous-<view>View-<epoch ms>.jpg` (timestamp per gambar), EXIF Make/Model **ada** (Samsung **SM-A235F** 345, **SM-N985F** 230, **SM-A045F** 185 — bertentangan dengan klaim "satu perangkat A23"); studi utama EXIF-nya dihapus. Waktu EXIF = nama berkas + 5 jam (PKT).
- **Pilot ↔ utama tumpang tindih:** 2024-10-02 rekaman `003-103-*` dan gambar pilot SM-A235F diambil berselang menit; 65 gambar pilot adalah salinan hampir identik gambar utama (dHash 256-bit jarak 0, korelasi 32×32 > 0,95). Tanpa penggabungan, 20 pasangan duplikat jatuh di split berbeda.
- ⇒ ID pasien **dapat diturunkan secara heuristik** (`group_quality: heuristic_session`), bukan pasti. Aturan (`ahmed2025_groups.py`, sengaja longgar: menggabungkan dua anak tidak berbahaya, memisah satu anak = bocor): rekaman berurutan digabung bila jeda ≤ 600 s, atau retractors→no_retractors ≤ 1.800 s; pilot per perangkat EXIF, jeda > 900 s = sesi baru; gambar pilot ≤ 900 s dari rekaman utama → digabung.
- Hasil: **657 grup** (utama 560 ≈ 581 anak menurut artikel; pilot 97); ukuran 10 gambar ×557, 5 ×68, ≥ 11 ×14 (anak tergabung).

## 5. Split (seed 20261008, rasio 0,7/0,15/0,15, stratifikasi ada/tidak kotak per grup)
`splits_sha256` = **`61b59b7b2a8a98908afe18b0419f457ccd0ac3dda47f3a53d28a570bab1fb625`** (deterministik; dijalankan ulang = hash sama). Input `split_input.jsonl` sha256 `a1c15a6b…b11b`, `groups.csv` sha256 `dbe8cced…87a4`.

| split | gambar | grup | gambar positif | kotak D / d | retr / no_retr / pilot | cacat label |
|---|---|---|---|---|---|---|
| train | 4.387 | 460 | 1.499 (34,2 %) | 4.221 / 426 | 1.932 / 1.937 / 518 | 7 |
| val | 938 | 101 | 317 (33,8 %) | 887 / 57 | 412 / 405 / 121 | 3 |
| test | 940 | 96 | 332 (35,3 %) | 1.026 / 69 | 409 / 410 / 121 | 6 |

Berkas: `splits/ahmed2025/{splits.json, split_manifest.json, split_stats.json, groups_summary.json, verify_report.json, known_issues.csv}`. `splits.json` bebas pemetaan kelas (hanya kunci `Dataset/Images/...`); `convert_annotations.py export --splits` dapat langsung memakainya.

## 6. Pemeriksaan kebocoran
| Cek | Hasil |
|---|---|
| Grup heuristik di > 1 split | **0** |
| Rekaman app (`AAA-BBB-NNN`) di > 1 split | **0** |
| Duplikat byte identik (CRC32+ukuran) | 0 pasangan di seluruh dataset |
| Duplikat hampir identik ketat (dHash-256 ≤ 10) lintas split | **0** dari 65 |
| dHash-64 ≤ 4 (1.060 pasangan) | 946 pasangan lintas-hari semuanya korelasi < 0,9 → positif palsu (tampilan intraoral mirip), tidak digabung |
| **Split benchmark penulis** (`Benchmarking Dataset.zip`) | split per gambar: **736/1.122 rekaman dan 569/657 grup** tersebar di train/valid/test → metrik penulis (mAP50 0,841 YOLOv8s) **optimistis, jangan dipakai sebagai pembanding setara** |

Catatan privasi: satu gambar train di `Benchmarking Dataset.zip` bernama berkas berisi nama orang (tidak ada di `Dataset.zip`) → jangan pakai benchmark zip; laporkan ke penulis bila relevan.

## 7. Keterbatasan (wajib dikutip di model card / laporan bake-off)
1. **Populasi:** Mithi, Sindh, Pakistan; usia **10–24 th** (remaja & dewasa muda), sekolah negeri; **tidak ada anak < 10 th dan tidak ada data anak Indonesia** → performa pada gigi sulung anak SD Indonesia tidak diketahui (hanya 552 kotak `d`).
2. **Perangkat & cahaya:** artikel: satu HP (Samsung A23) + lampu unit dental, pasien di dental chair, setelah scaling & pengeringan saliva — jauh dari senter + HP murah di sekolah. Pilot nyatanya 3 model Samsung; studi utama tanpa EXIF (perangkat tak bisa diverifikasi).
3. **Label:** tingkat gigi (bukan lesi), tanpa kavitas/non-kavitas, tanpa ICDAS, tanpa fluorosis; κ = 0,89 antar dua annotator (10 % data, menurut artikel); frontal/lateral jarang berlabel (§2).
4. **ID pasien heuristik** (§4) dan 65 duplikat pilot↔utama (`known_issues.csv`: buang salinan pilot dari evaluasi).
5. Jumlah gambar ≠ yang diklaim (6.265 vs 6.313); 18 label yatim; 16 gambar dengan kotak di luar gambar.
6. Metrik benchmark penulis bocor antar pasien (§6).

## 8. Reproduksi
```bash
cd backend/ml/detection   # UV_CACHE_DIR di D: (C: penuh)
uv run python ../data/scripts/verify_dataset.py --images ../data/raw/ahmed2025/extracted/Dataset/Images \
  --labels ../data/raw/ahmed2025/extracted/Dataset/Annotations/Darknet_YOLO --num-classes 2 --class-names D,d \
  --expected-images 6265 --orphan-labels warn --report ../data/processed/ahmed2025/verify_report.json   # exit 1: 42 baris cacat (diketahui)
uv run python ../data/scripts/ahmed2025_groups.py --root ../data/raw/ahmed2025/extracted --out-dir ../data/processed/ahmed2025
uv run python ../data/scripts/make_splits.py --canonical ../data/processed/ahmed2025/split_input.jsonl \
  --out-dir ../data/splits/ahmed2025 --group-mode session_csv --metadata-csv ../data/processed/ahmed2025/groups.csv --seed 20261008
uv run python ../data/scripts/split_stats.py --splits ../data/splits/ahmed2025/splits.json \
  --groups-csv ../data/processed/ahmed2025/groups.csv --split-input ../data/processed/ahmed2025/split_input.jsonl \
  --pairs ../data/processed/ahmed2025/dup_pairs_strict.json --out ../data/splits/ahmed2025/split_stats.json
```
`dup_pairs_strict.json` dibuat dengan skrip analisis sekali-pakai (dHash 256-bit ≤ 10) — belum menjadi skrip repo.
