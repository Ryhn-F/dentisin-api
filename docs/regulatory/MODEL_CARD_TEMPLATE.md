# MODEL CARD — <nama model> <semver>

> Templat. Salin ke `ml/exports/<semver>/MODEL_CARD.md` dan isi per versi. Harus **selaras dengan `labels.json`** dari versi yang sama (skema: `ml/detection/labels.schema.json`; field `labels.json`: `schema_version, model, validation_status, intended_use, distribution, classes, input, output, thresholds, licenses, attribution, int8_parity, decode_spec`). Bila model card dan `labels.json` bertentangan, rilis diblokir.
> **Aturan isi:** tidak ada angka kinerja sebelum evaluasi pada set uji independen benar-benar dijalankan. Kolom kosong = `[ISI]`. Tidak ada klaim "akurasi" untuk publik (ADR 002; `../docs/style/forbidden_phrases.md`). Bahasa: ringkas, Indonesia (dibaca drg); istilah teknis Inggris boleh.

## 1. Identitas
| Field | Isi |
|---|---|
| Nama / versi (semver) | `[ISI]` (sama dengan `labels.json: model`) |
| Tanggal build | `[ISI]` |
| Rilis | demo (no clinical claim) / pilot (KEPK `[ISI]`) / publik — sesuai `CHANGELOG.md` |
| `validation_status` (dari labels.json) | `[ISI]` (mis. unvalidated) |
| SHA256 `model.tflite` / `labels.json` | `[ISI]` |
| Penanggung jawab teknis / penandatangan klinis | `[ISI]` / `[PENDING]` drg |
| Entri CHANGELOG | `[ISI]` |

## 2. Tujuan penggunaan (intended use)
- Alat bantu **skrining** (indikasi, bukan diagnosis) pada foto gigi depan anak usia sekolah oleh orang tua (atau remaja untuk dirinya sendiri), dengan rujukan ke dokter gigi. Bounding box untuk: `caries_permanent`, `caries_primary` (ADR 014; kotak hanya untuk overlay, bukan ukuran keparahan) `[sesuaikan dengan labels.json: classes]`.
- Bukan untuk: diagnosis, keputusan terapi, rontgen, rongga mulut dewasa `[PERLU VERIFIKASI cakupan usia data]`, kelainan non-karies, abses/fistula (bukan target model v1).
- Pengguna: orang tua/ wali dan remaja (untuk dirinya sendiri); kader/ guru UKS tidak lagi menjadi pengguna (ADR 018). Lingkungan: HP Android kelas menengah-bawah, offline, senter.
- Tidak boleh dipakai di luar `intended_use` pada `labels.json`.

## 3. Detail model
| Field | Isi |
|---|---|
| Arsitektur / kandidat bake-off | `[ISI]` (kandidat: MediaPipe Model Maker, YOLOX-Nano, EfficientDet-Lite; ADR 001) |
| Format & kuantisasi | LiteRT INT8 `[ISI]`; ukuran `[ISI]` MB (target ≤ 15 MB) |
| Input | ukuran, urutan kanal, normalisasi — salin dari `labels.json: input` |
| Output & cara dekode | salin ringkas dari `labels.json: output / decode_spec` (format tensor, NMS, koordinat) |
| Ambang | dari `labels.json: thresholds`; sumber penetapan ambang: `[ISI]` |
| Parity INT8 vs float | `labels.json: int8_parity` → `[ISI]` |
| Latensi pada HP uji | tabel perangkat (RAM 3/4/6 GB): `[ISI]` — diukur di spike S1, bukan perkiraan |

## 4. Data
Rujuk `DATA_LINEAGE.md` (ID dataset) dan `ml/data/LICENSES.md` / `ml/data/license_gate.yaml`.
| Dataset | ID lineage | Lisensi (kode/data) | Dipakai untuk | Jumlah gambar/box | Catatan |
|---|---|---|---|---|---|
| `[ISI]` | `[ISI]` | `[ISI]` | latih / validasi / uji | `[ISI]` | atribusi CC BY tercantum di layar Tentang |

- Split: **per pasien/anak** (atau heuristik pengelompokan yang dicatat sebagai keterbatasan); seed `[ISI]`; hash split `[ISI]`; set uji **independen** tidak dipakai untuk memilih ambang/ model: `[ya/tidak, ISI]`.
- Anotasi: sumber label `[ISI]`; anotator (jumlah, kualifikasi) `[ISI]`; kappa antar-anotator per kelas `[ISI]` (rencana: ≥ 2 drg + adjudikasi).
- Data anak lokal: dipakai? `[ya/tidak]`; bila ya, KEPK `[ISI]`, consent (c) `[ISI]`, dilatih hanya di PC tim/lab (bukan Kaggle/Colab).
- Pra-pemrosesan & augmentasi (cahaya senter, white balance, blur gerak, bibir menutupi): `[ISI]`.
- Data hilang / label noise yang diketahui: `[ISI]`.

## 5. Kinerja (set uji independen)
> Hanya diisi dari laporan evaluasi yang dijalankan (`ml/detection/eval.py` → `report.json`, skema `ml/detection/report.schema.json`). Tautan: `[ISI]`.

| Kelas | Sens | Spes | PPV | NPV | mAP50 | n positif | n negatif | CI |
|---|---|---|---|---|---|---|---|---|
| caries_permanent (gigi tetap) | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |
| caries_primary (gigi sulung; data latih hanya ±8% kotak) | `[ISI]` | | | | | | | |
| ada indikasi karies (tingkat citra) | `[ISI]` | | | | | | | |

Confusion matrix: `[ISI]`. Validasi lokal di Indonesia (mis. dibandingkan pemeriksaan drg): `[ISI — belum ada]`.

## 6. Bias dan subgrup
Wajib dilaporkan (sel kosong = belum dievaluasi, **bukan** "tidak ada perbedaan"):
| Subgrup | Sens | Spes | n | Catatan |
|---|---|---|---|---|
| Gigi sulung vs permanen | `[ISI]` | | | |
| Tipe/ kelas HP & kamera | `[ISI]` | | | |
| Kondisi cahaya (senter/ ruangan) | `[ISI]` | | | |
| Sumber dataset | `[ISI]` | | | |
| Wilayah (mis. Jawa vs Indonesia timur) | `[ISI]` | | | |
| Warna gusi/ kulit | `[ISI]` | | | |

Keterbatasan representasi data latih (mis. satu perangkat, rentang usia, negara asal): `[ISI]`.

## 7. Batasan dan mode kegagalan yang diketahui
- Dataset tidak membedakan lesi berlubang/ tidak berlubang dan tidak menilai keparahan; lesi awal sulit dilihat pada foto ponsel; foto tidak melihat sela gigi/ bagian dalam (ditampilkan di layar hasil, FR-33).
- Kegagalan pada foto buram, gelap, bibir menutupi, gigi belakang (quality gate mengurangi tetapi tidak menghilangkan).
- Hasil hanya "indikasi"; false negative tetap mungkin → CTA rujukan dan skrining ulang selalu ditampilkan.
- Batasan lain dari evaluasi: `[ISI]`. Risiko terkait: lihat `RISK_REGISTER.md` R-03..R-06, R-14, R-15.

## 8. Etika, privasi, keamanan
- Data pelatihan anak: consent/assent & KEPK `[ISI]`; de-identifikasi (tanpa wajah, EXIF dihapus) `[ISI]`.
- Model dikirim lewat manifes Ed25519 + checksum; versi tercatat pada tiap `screening`.
- Model terlatih **tidak dapat "melupakan"** data yang telah dipakai; kebijakan bila ada penarikan persetujuan: `[ISI]`.

## 9. Lisensi (kode, bobot, data) — harus sama dengan `labels.json: licenses/attribution`
| Komponen | Lisensi | Terverifikasi (sumber, tanggal) |
|---|---|---|
| Kode pelatihan/ekspor | `[ISI]` | `[ISI]` |
| Bobot awal (pretrained) | `[ISI]` | `[ISI]` |
| Bobot akhir | `[ISI]` | `[ISI]` |
| Dataset | `[ISI]` | `[ISI]` |
- Ultralytics/YOLOv8 (AGPL) **tidak** dipakai di produk; CC BY-SA tidak dipakai untuk model terkirim (ADR 001/008).

## 10. Pemantauan pasca-rilis
Metrik yang dipantau (agregat, tanpa PII): `[ISI]`; frekuensi tinjau `[ISI]`; pemicu rollback `[ISI]`; rute keluhan → `COMPLAINTS_LOG.md`.

## 11. Persetujuan
| Peran | Nama | Tanggal | Tanda tangan |
|---|---|---|---|
| Drg pembimbing | `[PENDING]` | `[ISI]` | `[ISI]` |
| Pemilik produk | `[PENDING]` | `[ISI]` | `[ISI]` |

### Daftar periksa selaras `labels.json`
- [ ] Kelas, ukuran input, normalisasi sama dengan §3
- [ ] Ambang sama dengan §3
- [ ] Lisensi & atribusi sama dengan §9
- [ ] `validation_status` sama dengan §1 dan tidak ada klaim akurasi di UI
- [ ] SHA256 di `SHA256` cocok dengan manifes
