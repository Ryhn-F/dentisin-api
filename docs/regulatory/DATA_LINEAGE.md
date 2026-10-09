# Data Lineage

> Templat hidup (ADR 002: "data lineage"). Mencatat asal-usul setiap data yang menyentuh model/aturan: sumber → lisensi/izin → transformasi → split → model turunan. Sumber lisensi mesin-baca: `ml/data/license_gate.yaml`; versi manusia: `ml/data/LICENSES.md` (jaga sinkron). **Jangan menaruh dataset, foto, atau PII di sini; hanya metadata dan hash.** Belum ada pelatihan yang dijalankan per 2026-10-09.

## 1. Registri dataset
| ID | Nama / sumber (URL, versi) | Lisensi | Diverifikasi? (sumber + tanggal) | Subjek | Dasar izin (consent/KEPK) | Boleh ke Kaggle/Colab? | Dipakai untuk | Status |
|---|---|---|---|---|---|---|---|---|
| `ahmed2025` | Annotated intraoral image dataset for dental caries detection (Zenodo, DOI di `license_gate.yaml`) | CC BY 4.0 (lihat gerbang lisensi) | Menurut `license_gate.yaml`: ya, halaman Zenodo dibaca 2026-10-08 — **verifikasi ulang oleh pemilik** | Pasien klinik (asal Pakistan menurut `backend/CLAUDE.md` §9), foto smartphone, bbox | Data publik; izin etik asal dataset `[PERLU VERIFIKASI]` | Ya (data publik) | Kandidat latih/validasi/uji (belum ada pelatihan) | `allowed` (gerbang) — belum diunduh/dipakai `[ISI]` |
| `dentalai` | DENTALAI (daftar Dataset Ninja) | CC BY per daftar saja | **Belum** (asal & lisensi publisher belum dicek) | `[ISI]` | `[ISI]` | - | - | `blocked_unverified` — jangan dipakai |
| `alphadent` | AlphaDent | CC BY-SA | - | - | - | - | **Tidak dipakai** untuk model terkirim (share-alike; ADR 008) | `forbidden` |
| `local_children_v1` | Foto gigi anak lokal (rencana) | Hak milik proyek, sesuai consent | - | Anak sekolah | KEPK `[ISI]`; consent (c) `[ISI]`; assent `[ISI]` | **Tidak** (hanya PC tim/ lab) | Evaluasi lokal / latih | **Belum ada** (tidak ada KEPK/ mitra) |

Tambahkan baris per dataset baru; ID baru tidak boleh dipakai ulang. Dataset web tanpa izin (mis. Kaggle "oral diseases") dilarang.

## 2. Transformasi dan turunan
Satu baris per langkah yang mengubah data (konversi anotasi, pemetaan kelas, resize, kompresi, de-identifikasi, augmentasi).
| ID turunan | Dari | Langkah (skrip + commit/versi) | Parameter / seed | Hash keluaran | Tanggal | Oleh |
|---|---|---|---|---|---|---|
| `[ISI]` | `ahmed2025` | `ml/detection/convert_annotations.py` `[versi]` | peta kelas `ml/data/class_maps/` `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` |

## 3. Split
| ID split | Dataset | Unit pemisahan (per pasien/ per gambar) | Rasio | Seed | Hash daftar ID | Keterbatasan yang diketahui |
|---|---|---|---|---|---|---|
| `[ISI]` | `ahmed2025` | per pasien bila ID pasien tersedia; bila tidak, heuristik pengelompokan dicatat | `[ISI]` | `[ISI]` | `[ISI]` (skrip `ml/data/scripts/make_splits.py`) | Apakah dataset menyediakan ID pasien: `[PERLU VERIFIKASI]` |

Set uji independen: dikunci, tidak dipakai memilih model/ambang; hash `[ISI]`.

## 4. Model turunan
| Versi model | Data latih (ID split/ turunan) | Data validasi | Data uji | Run pelatihan (config, commit, tempat: Kaggle/Colab/PC tim) | Model card |
|---|---|---|---|---|---|
| `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `ml/exports/<semver>/MODEL_CARD.md` |

## 5. Data anak lokal (bila kelak ada)
- Setiap anak: ID pseudonim (UUID); **tidak ada** nama lengkap/NIK; lokasi foto: bucket privat `dataset_photos` atau PC tim (terenkripsi).
- Pengambilan massal hanya admin dengan `service_role` dari PC tim; log pengambilan (siapa, kapan, berapa) `[ISI]` di `audit_log`.
- Anotasi lokal (CVAT/Label Studio) di PC tim; tidak diunggah ke layanan pihak ketiga.
- Kuota Supabase Free 1 GB; ekspor berkala terenkripsi karena tanpa backup otomatis (ADR 008).

## 6. Penarikan persetujuan dan penghapusan
| Tanggal | ID pseudonim (atau rentang) | Permintaan | Foto ditandai `withdrawn` | Dikeluarkan dari split/ pelatihan berikutnya | Model yang sudah memakai data | Bukti penghapusan |
|---|---|---|---|---|---|---|
| `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | `[ISI]` | Versi `[ISI]` — model terlatih tidak dapat melupakan; keputusan (latih ulang atau tidak): `[ISI, hukum/etik]` | `[ISI]` |

## 7. Pemeriksaan sebelum rilis model
- [ ] Setiap dataset yang dipakai ada di §1 dengan lisensi **terverifikasi**
- [ ] Tidak ada dataset `blocked_unverified`/`forbidden`
- [ ] Split & hash tercatat; set uji independen terkunci
- [ ] Atribusi CC BY masuk layar Tentang dan `labels.json: attribution`
- [ ] Data anak lokal: KEPK + consent (c) dirujuk, tidak ada yang keluar dari PC tim
