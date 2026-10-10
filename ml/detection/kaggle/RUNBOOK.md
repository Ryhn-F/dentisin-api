# RUNBOOK — Melatih kandidat detektor di Kaggle (untuk pemilik produk)

> Untuk orang yang **bukan** ahli ML. Ikuti berurutan. Semua path relatif terhadap `backend/`.
> Hasilnya adalah **eksperimen teknis** (bake-off, item 2.3/2.5) — **bukan** validasi klinis, **bukan** klaim akurasi.
> Kelas model (ADR 014): **indikasi karies pada gigi tetap** (`caries_permanent`) dan **gigi sulung** (`caries_primary`);
> kotak = seluruh gigi, hanya penanda di layar — ukuran kotak **tidak** menunjukkan keparahan.

## 0. Aturan yang tidak boleh dilanggar
| Boleh diunggah ke Kaggle/Colab | **Tidak boleh** diunggah |
|---|---|
| Dataset publik **Ahmed 2025** (Zenodo 14827784, CC BY 4.0) — sebagai dataset **Private** dengan atribusi | **Foto anak/ data lokal apa pun** (puskesmas, sekolah, keluarga, foto uji tim) — bahkan "sebentar" |
| Kode pipeline (`artifacts.py pack-code`), anotasi teks hasil olahan (`canonical.jsonl`, `splits.json`) | Berkas `.env`, kunci Supabase/ penandatangan, token apa pun |
| | Dataset AlphaDent (CC BY-SA), dataset "oral diseases" Kaggle, atau data lain yang belum hijau di `ml/data/license_gate.yaml` |

Yang **diunduh kembali** hanya zip `download_<kandidat>.zip` (tanpa gambar — skrip menolak gambar/ `.npy`/ checkpoint).

## 1. Sekali saja: akun Kaggle
1. Daftar di kaggle.com, lalu **verifikasi nomor HP** (Settings → Phone verification). Tanpa ini GPU dan Internet di notebook tidak aktif.
2. Kuota GPU gratis ± **30 jam/minggu** (reset tiap Sabtu pagi WIB [PERLU VERIFIKASI]); satu sesi maks. **12 jam**. Lihat sisa kuota di profil → *Settings/Quota*.

## 2. Siapkan berkas di PC (sekali per versi kode)
Butuh hasil engineer data: `ml/data/processed/ahmed2025/canonical.jsonl` + `splits/` (lihat `ml/data/scripts/fetch_ahmed2025.md`).
```bash
cd backend/ml/detection
uv run python kaggle/make_ipynb.py                      # memastikan .ipynb sinkron dengan .py
uv run python artifacts.py pack-code --with-processed --out ../../../dentisai-ml-code.zip
```
Hasil: `dentisai-ml-code.zip` (± < 5 MB; hanya kode + anotasi teks, **tanpa gambar**). Catat `code_sha256` yang dicetak.

## 3. Unggah dua dataset **Private** ke Kaggle
**a. Dataset Ahmed 2025** (sekali saja)
1. Unduh zip asli dari https://zenodo.org/records/14827784 (jangan diubah isinya).
2. Kaggle → *Datasets* → *New Dataset* → unggah zip → judul **`ahmed2025-caries`** → **Visibility: Private**.
3. *License*: pilih **CC BY 4.0**. Di *Description* tempel atribusi (wajib, CC BY):
   > Contains data from "Annotated intraoral image dataset for dental caries detection" by S. M. F. Ahmed, H. Ghori, A. Khalid,
   > A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, licensed under
   > CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Uploaded unchanged as a private copy for model training.
4. Jangan jadikan Public (kita tidak ingin menjadi pendistribusi ulang tanpa perlu).

**b. Dataset kode** (setiap kali kode berubah → *New Version*)
- *New Dataset* → unggah `dentisai-ml-code.zip` → judul **`dentisai-ml-code`** → **Private**. Kaggle mengekstrak zip otomatis.

## 4. Buat & jalankan notebook
Ada dua notebook (satu per kandidat), di `ml/detection/kaggle/`:
| Kandidat | Berkas | Lisensi bobot | Catatan |
|---|---|---|---|
| #1 YOLOX-Nano | `yolox_nano_kaggle.ipynb` | tidak eksplisit; **risiko diterima pemilik** (ADR 014 §5) → tercatat `owner_accepted_risk`, bukan "terverifikasi" | butuh `onnx2tf` untuk INT8 |
| #2 MediaPipe MobileNetV2 (QAT) | `mediapipe_ssd_kaggle.ipynb` | Apache-2.0 (terverifikasi) | butuh Python 3.10 (dipasang otomatis) |

Langkah (ulangi untuk tiap notebook):
1. Kaggle → *Code* → *New Notebook* → *File* → *Import Notebook* → pilih `.ipynb`.
2. Panel kanan → *Add Input* → cari dataset privat **`ahmed2025-caries`** dan **`dentisai-ml-code`** → *Add*.
3. *Settings* → *Accelerator* → **GPU T4 x2** (hindari P100) · *Internet* → **On** · *Language* Python.
4. Hanya **sel 0** yang boleh diubah (mis. `EPOCHS`). Biarkan default untuk run pertama.
5. Jalankan uji cepat dulu: *Run All* secara interaktif sampai **sel 4** selesai (pemasangan + gerbang lisensi, ± 10–25 menit).
   Bila sel 4 menolak — lihat §8. Bila lolos, hentikan sesi (*Stop session*) agar kuota tidak terbuang.
6. Jalankan sungguhan di latar belakang: **Save Version** → *Save & Run All (Commit)*. Browser boleh ditutup.
7. Pantau di *Your Work* → notebook → *Versions* → log.

### Resume (hanya YOLOX) bila sesi berhenti
Sel 5 berhenti sendiri setelah `TIME_BUDGET_H` (10 jam) dan menyimpan `latest_ckpt.pth`. Lalu:
1. Buka versi yang selesai → *Output* tersimpan.
2. Edit notebook → *Add Input* → tab *Notebook Output Files* → pilih **versi sebelumnya dari notebook ini**.
3. *Save Version* → *Save & Run All* lagi. Pelatihan melanjutkan dari epoch terakhir (dicari di `/kaggle/input/*/yolox_outputs`).

## 5. Perkiraan waktu & kuota (PERKIRAAN, belum diukur — catat angka nyata di laporan)
| Tahap | YOLOX-Nano | MediaPipe MobileNetV2 |
|---|---|---|
| Pemasangan (sel 2) | 10–15 mnt | 15–25 mnt (TF 2.15 + CUDA) |
| Salin+perkecil gambar | 10–20 mnt | 10–20 mnt |
| Latih | 150 epoch: ± 2–5 jam | 30 epoch + 10 QAT: ± 1,5–3 jam |
| Ekspor + paritas + prediksi + evaluasi | 20–40 mnt | 20–40 mnt |
| **Total per run** | **± 3–6 jam** | **± 2,5–4,5 jam** |
Dua kandidat ≈ 6–11 jam dari kuota 30 jam/minggu → masih ada sisa untuk satu run ulang (mis. YOLOX `ACT="relu"`).

## 6. Yang diunduh & ditaruh di mana
1. Versi selesai → *Output* → unduh **`download_<kandidat>.zip`** saja (jangan unduh `src/`, `runs/`, `yolox_outputs/` kecuali diminta).
2. Ekstrak ke `backend/ml/runs/kaggle/<kandidat>/<YYYYMMDD>/` (folder `ml/runs/` di-*gitignore* — **jangan commit**).
3. Isi zip: `RUN_SUMMARY.json` (sha256 tiap berkas), `run.json`, `report_int8_val.json`, `report_int8_test.json`,
   `report_float_test.json`, `thresholds.json`, `parity.json`, `measurements_partial.json`, `env.json`, konfigurasi run,
   dan bundel `0.1.0-dev.N/` (`model.tflite`, `labels.json`, `MODEL_CARD.md`, `SHA256`).
4. **Jangan** salin bundel dev ke `ml/exports/` — bundel resmi hanya lewat item 4.6 setelah ADR bake-off.
5. Checkpoint (`best_ckpt.pth`) hanya diunduh bila diminta; simpan di luar repo.

## 7. Melapor kembali
1. Gabungkan hasil:
   ```bash
   cd backend/ml/detection
   uv run python bake_off/compare.py --adr \
     --report yolox_nano=../runs/kaggle/yolox_nano/<tgl>/download_yolox_nano/report_int8_test.json \
     --report mediapipe_modelmaker_mobilenetv2_ssd=../runs/kaggle/mediapipe_modelmaker_mobilenetv2_ssd/<tgl>/download_mediapipe_modelmaker_mobilenetv2_ssd/report_int8_test.json \
     --meta ../runs/kaggle/yolox_nano/<tgl>/download_yolox_nano/measurements_partial.json \
     --meta ../runs/kaggle/mediapipe_modelmaker_mobilenetv2_ssd/<tgl>/download_mediapipe_modelmaker_mobilenetv2_ssd/measurements_partial.json \
     --out bake_off/ADR-draft.md
   ```
   (`compare.py` menolak bila kedua laporan memakai split berbeda — itu disengaja.)
2. Kirim ke tim/ Claude: `ADR-draft.md`, kedua `RUN_SUMMARY.json`, `parity.json`, dan bila gagal: **nomor sel + 50 baris terakhir log**
   (Versions → Logs). Jangan kirim gambar atau tangkapan layar berisi foto.
3. Latensi HP (spike S1) diisi terpisah bersama tim app ke `bake_off/measurements.yaml`.

## 8. Masalah umum
| Gejala | Arti | Tindakan |
|---|---|---|
| Sel 4: `config says license_verified=true but the gate has unverified ...` | Patch `license_gate.yaml` (WEIGHT_LICENSES.md §4) belum diterapkan | Minta engineer data menerapkan patch → *pack-code* ulang → *New Version* dataset kode. **Jangan** mengedit gerbang di notebook |
| Sel 3: `fill ml/data/class_maps/ahmed2025.yaml first` | Pemetaan kelas belum diputuskan | Tunggu engineer data + drg |
| Sel 3: `splits.json sha256 ... != split_manifest.json` | Berkas split rusak/ tidak cocok | *pack-code* ulang dari PC |
| Sel 2 MediaPipe: GPU list kosong `[]` | TF 2.15 tidak melihat GPU | Pastikan T4; laporkan log; jangan jalankan 30 epoch di CPU |
| Sel 8 YOLOX: satu backend gagal / paritas buruk | Wajar: `flatbuffer_direct` terbukti bermasalah pada graf uji kecil | Sel 8 otomatis memilih backend yang lolos paritas (`tf_converter` dulu). Bila keduanya gagal: laporkan log; rencana B `litert-torch` (WEIGHT_LICENSES.md §3.3) |
| Sel 9: `INT8 parity passed: False` | INT8 menyimpang dari float (SiLU) | Lanjutkan sampai selesai (tetap dilaporkan), lalu run ulang dengan `ACT = "relu"` |
| `No space left on device` | `/kaggle/working` > 20 GB | Jangan menyalin gambar ke `/kaggle/working`; notebook memakai `/tmp/dentisai` |
| Error PyTorch `no kernel image` | GPU P100 | Ganti ke T4 x2 |

## 9. Yang BELUM terbukti (jujur)
- Tidak satu pun notebook pernah dijalankan end-to-end di Kaggle (tidak ada GPU di PC tim). Pelatihan YOLOX/Model Maker belum pernah berjalan.
- **Sudah terbukti di PC (2026-10-10, data sintetis, model acak kecil):** ONNX → `onnx2tf 2.6.9` → `.tflite` INT8, lalu bundel
  (`labels.json` dari interpreter), `verify-bundle`, paritas, prediksi, ambang val, evaluasi, dan zip unduhan (gambar/`.npy` ditolak).
  Temuan: backend bawaan `flatbuffer_direct` menghasilkan INT8 dengan paritas buruk dan menolak Sigmoid → sel 8 mencoba dua backend.
- Backend `tf_converter` (butuh TensorFlow) belum dicoba di mana pun.
- Format keluaran MediaPipe dibaca dari **kode sumber** Model Maker, belum dari model nyata; `labels.json` mengambil bentuk tensor dari interpreter saat ekspor.
- Kuota/ batas Kaggle dan ketersediaan Python 3.10/3.12 via `uv` di image Kaggle saat ini [PERLU VERIFIKASI pada run pertama].

## 10. Colab (cadangan)
Taruh kedua zip di Google Drive `MyDrive/dentisai_inputs/` (diekstrak), jalankan notebook yang sama (Runtime → T4 GPU),
*mount* Drive dulu (`from google.colab import drive; drive.mount('/content/drive')`). Keluaran ke `MyDrive/dentisai_runs/`.
Sesi Colab gratis bisa putus kapan saja — YOLOX akan *resume* dari `MyDrive/dentisai_runs/yolox_outputs` bila `RESUME_FROM`
diubah ke path itu. Aturan data sama persis dengan Kaggle.
