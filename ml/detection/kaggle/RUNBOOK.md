# RUNBOOK — Melatih kandidat detektor di Kaggle atau Google Colab (untuk pemilik produk)

> **Colab (baru, 2026-10-16):** notebook **YOLOX** juga jalan di Google Colab — lihat **§3b**. MediaPipe belum diuji di Colab.
>
> Untuk orang yang **bukan** ahli ML. Ikuti berurutan. Hasilnya **eksperimen teknis** (bake-off, item 2.3/2.5) —
> **bukan** validasi klinis, **bukan** klaim akurasi. Kelas model (ADR 014): **indikasi karies pada gigi tetap**
> (`caries_permanent`) dan **gigi sulung** (`caries_primary`); kotak = seluruh gigi, hanya penanda di layar.
>
> **Yang baru (2026-10-11):** notebook mengambil **kode** sendiri dari GitHub (pakai Secret `GITHUB_TOKEN`) dan **data**
> Ahmed 2025 sendiri dari Zenodo. Anda **tidak perlu** lagi membuat/mengunggah dataset Kaggle apa pun.

## 0. Aturan yang tidak boleh dilanggar
| Boleh | **Tidak boleh** |
|---|---|
| Notebook mengunduh dataset publik **Ahmed 2025** (Zenodo 14827784, CC BY 4.0) langsung dari Zenodo | Mengunggah **foto anak/ data lokal apa pun** ke Kaggle/Colab — bahkan "sebentar" |
| Token GitHub **read-only** untuk satu repo, disimpan sebagai **Kaggle Secret** | Menempel token/ `.env`/ kunci apa pun di sel kode, atau membagikan notebook ber-secret |
| Mengunduh kembali `download_<kandidat>.zip` (tanpa gambar) | Dataset AlphaDent (CC BY-SA), dataset "oral diseases" Kaggle, atau data yang belum hijau di `ml/data/license_gate.yaml` |

## 1. Sekali saja: akun Kaggle
1. Daftar di kaggle.com, lalu **verifikasi nomor HP** (foto profil → *Settings* → *Phone verification*).
   Tanpa ini pilihan GPU dan Internet di notebook tidak muncul.
2. Kuota GPU gratis ± **30 jam/minggu**; satu sesi maks. **12 jam** [PERLU VERIFIKASI: angka & jadwal reset tampil di
   profil → *Settings* → *Quotas*].

## 2. Sekali saja: token GitHub (±3 menit)
Notebook mengambil kode dari repo privat **`Ryhn-F/dentisin-api`**. Buat token yang **hanya bisa membaca** repo itu:
1. github.com (akun yang punya akses ke repo) → foto profil → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Isi: *Token name* `dentisai-notebook` · *Expiration* 30–90 hari · *Resource owner* pemilik repo (`Ryhn-F`) ·
   *Repository access* → **Only select repositories** → `Ryhn-F/dentisin-api` ·
   *Permissions* → *Repository permissions* → **Contents: Read-only** (Metadata: Read-only terisi otomatis). Tidak ada izin lain.
3. **Generate token** → salin (diawali `github_pat_`). Simpan di pengelola kata sandi; GitHub tidak menampilkannya lagi.
   (Bila pemilik repo adalah organisasi, admin organisasi mungkin perlu menyetujui token itu.)

> Engineer: notebook memakai cabang **`main`** (sel 0 `CODE_REF`). Perubahan pada `ml/` harus sudah di-*push* ke `main`
> sebelum pemilik produk menjalankan notebook — bila belum, sel 1 berhenti dengan pesan "Kode yang terambil lebih lama".

## 3. Jalur tercepat (ulangi untuk tiap notebook)
Ada dua notebook di `ml/detection/kaggle/` (unduh berkas `.ipynb`-nya dari GitHub/ dari engineer):

| Kandidat | Berkas | Lisensi bobot | Catatan |
|---|---|---|---|
| #1 YOLOX-Nano | `yolox_nano_kaggle.ipynb` | tidak eksplisit; **risiko diterima pemilik** (ADR 014 §5) → tercatat `owner_accepted_risk` | bisa dilanjutkan lintas sesi |
| #2 MediaPipe MobileNetV2 (QAT) | `mediapipe_ssd_kaggle.ipynb` | Apache-2.0 (terverifikasi) | Python 3.10 + TF 2.15 dipasang otomatis |

1. kaggle.com → **Create** → **New Notebook**. Di editor: menu **File** → **Import Notebook** → pilih berkas `.ipynb` → *Import*.
2. Panel kanan (ikon ⚙ / *Settings*; bila panel tertutup: *View* → *Show sidebar*):
   - **Accelerator** → **GPU T4 x2** (jangan P100).
   - **Internet** → **On**.
   - **Persistence** → *Files only* (opsional; menyimpan `/kaggle/working` antar-sesi interaktif).
   - **Environment** → *Always use latest environment*.
3. Menu **Add-ons** → **Secrets** → **Add Secret**: *Label* `GITHUB_TOKEN`, *Value* = token dari §2 → *Save*.
   Pastikan kotak/ sakelar di samping `GITHUB_TOKEN` **tercentang (attached)** untuk notebook ini.
4. **Jangan ubah apa pun** selain sel 0. Biarkan `SMOKE_TEST = True`. Klik **Run All** (▶▶).
   Uji cepat ini (±20–30 menit) membuktikan **seluruh rantai**: kode → data (hanya ±32 foto) → pemasangan → latih 1 epoch
   (YOLOX: 2 epoch) → ekspor INT8 → evaluasi → zip unduhan. Sel terakhir harus mencetak **"UJI CEPAT BERHASIL"**.
   Model uji cepat hampir tidak belajar: 0 deteksi, ambang 1.0, metrik 0 dan "INT8 tidak lolos" **wajar** di sini —
   yang diuji adalah rantainya, bukan mutu model.
   Bila berhenti dengan kotak `====` berisi pesan: ikuti isinya, atau lihat §8.
5. Ubah sel 0 menjadi `SMOKE_TEST = False` → **Save Version** (kanan atas) → **Save & Run All (Commit)** → *Save*.
   Browser boleh ditutup; Kaggle menjalankan di latar belakang.
6. Pantau: *Your Work* → notebook → *Versions* → klik versi → *Logs*.

Sel 0 (yang boleh diubah):

| Pengaturan | Default | Arti |
|---|---|---|
| `SMOKE_TEST` | `True` | `True` = uji cepat (32 foto, 1 epoch). `False` = pelatihan sungguhan |
| `CODE_REF` | `"main"` | cabang/ tag/ commit GitHub yang dipakai |
| `EPOCHS`, `BATCH_SIZE` (+ `QAT_EPOCHS` MediaPipe) | `None` | `None` = nilai konfigurasi repo (YOLOX 150/32; MediaPipe 30/8 + 10 QAT) |
| `PRETRAINED` (YOLOX) | `True` | `False` = tanpa bobot COCO (bebas isu lisensi bobot; butuh ±300 epoch) |
| `ACT` (YOLOX) | `"silu"` | `"relu"` hanya bila paritas INT8 gagal (§8) |
| `RESUME` (YOLOX) | `True` | Colab: `True` = lanjut dari checkpoint run yang sama di Google Drive; `False` = mulai dari awal (§3b) |
| `TIME_BUDGET_H` | `11.0` | jam per sesi; pelatihan berhenti lebih awal agar ekspor sempat jalan |
| `TARGET_SENSITIVITY` | `0.85` | titik operasi teknis pada data val |

### Lanjut (resume) — hanya YOLOX
Bila pelatihan belum selesai saat `TIME_BUDGET_H` tercapai, sel 6 menyimpan `latest_ckpt.pth`, mencetak "BATAS WAKTU
SESI TERCAPAI", dan sel 7–14 dilewati (versi tetap tersimpan). Lalu:
1. Tunggu versi itu selesai. Edit notebook → panel kanan **Add Input** → tab **Notebook Output Files** (*Your Work*) →
   pilih **versi sebelumnya dari notebook ini** → *Add*.
2. *Save Version* → *Save & Run All* lagi. Pelatihan melanjutkan dari epoch terakhir (dicari otomatis di input
   `.../yolox_outputs`; keluaran uji cepat `*_smoke` diabaikan).
MediaPipe Model Maker **tidak bisa** resume: bila waktu habis, ulangi dengan `EPOCHS` lebih kecil.

## 3b. Jalur Google Colab (hanya YOLOX)
Dipakai bila kuota Kaggle habis. Berkas yang sama: `yolox_nano_kaggle.ipynb` (satu notebook untuk dua platform).

1. colab.research.google.com → **File** → **Upload notebook** → pilih `yolox_nano_kaggle.ipynb`.
2. **Runtime** → **Change runtime type** → *Hardware accelerator* **T4 GPU** → *Save* (sesi dimulai ulang).
   Kuota GPU gratis Colab tidak dijamin (bisa "tidak tersedia" bila sudah banyak dipakai) [PERLU VERIFIKASI: aturan Colab berubah-ubah].
3. Ikon **kunci (Secrets)** di sidebar kiri → **Add new secret**: *Name* `GITHUB_TOKEN`, *Value* = token dari §2 →
   nyalakan sakelar **Notebook access** pada baris itu.
4. Biarkan `SMOKE_TEST = True` → **Runtime** → **Run all**. Saat sel 1 jalan muncul pop-up Google
   *"Permit this notebook to access your Google Drive files"* → **Connect to Google Drive** → pilih akun → *Allow*
   (sel 1 menunggu klik itu). Sel terakhir harus mencetak **"UJI CEPAT BERHASIL"**.
5. Ubah sel 0 menjadi `SMOKE_TEST = False` → **Run all**. **Biarkan tab tetap terbuka** selama pelatihan.

Bedanya dengan Kaggle:
| | Kaggle | Colab |
|---|---|---|
| Tempat kerja | `/kaggle/working/outputs` (disimpan saat *Commit*) | disk VM `/content/dentisai_outputs` (**hilang** saat sesi putus) **+ cermin otomatis ke Google Drive** `MyDrive/dentisai_runs/yolox_nano/` tiap 5 menit |
| Isi cermin | — | checkpoint (`latest_ckpt.pth`, `best_ckpt.pth`), log, konfigurasi, laporan, bundel model, zip unduhan. **Tidak pernah** foto atau larik `.npy` |
| Sesi putus | Ikuti §3 "Lanjut (resume)" | Buka notebook lagi → **Run all**. Sel 5 mencetak *"sesi sebelumnya dipulihkan"* dan pelatihan lanjut dari epoch terakhir. Yang hilang paling banyak ±5 menit + epoch yang sedang berjalan |
| Hasil akhir | tab *Output* | `MyDrive/dentisai_runs/yolox_nano/download_yolox_nano.zip` (unduh dari drive.google.com atau panel *Files*), lalu §6 |
| CPU/RAM | 4 vCPU / ±29 GB | Colab gratis: 2 vCPU / ±12 GB → pelatihan **lebih lambat** (augmentasi di CPU). Waktu nyata belum diukur [PERLU VERIFIKASI] |

Aturan pemulihan: checkpoint di Drive dipakai lagi **hanya bila pengaturan run sama** (`ACT`, `EPOCHS`, `BATCH_SIZE`, `PRETRAINED`,
`SMOKE_TEST`). Bila berbeda, atau `RESUME = False`, folder lama dipindah ke `yolox_nano.prev-<waktu>` (tidak dihapus) dan
pelatihan mulai dari awal. Hapus folder `.prev-*` sendiri bila sudah yakin tidak dibutuhkan (Drive gratis hanya 15 GB, dipakai bersama Gmail/Foto).

## 4. Apa yang terjadi otomatis
| Sel | Isi |
|---|---|
| 1 | Preflight (Python, GPU, RAM, disk, Internet ke github/pypi/zenodo[/storage.googleapis]) → ambil kode: dataset kode terlampir → atau `git clone` dengan `GITHUB_TOKEN` (token tidak pernah dicetak/ disimpan di `.git/config`) |
| 2 | Data: dataset Ahmed terlampir → atau **unduh dari Zenodo** (MD5 diverifikasi, bisa dilanjut, 8 koneksi). Uji cepat hanya membaca ±32 foto lewat HTTP Range (beberapa MB). Lalu `canonical.jsonl` + **split pasien dari repo** (`ml/data/splits/ahmed2025`, sha256 dicek) |
| 3 | Pemasangan dengan versi **terkunci** (`kaggle/requirements/*.txt`); torch & numpy bawaan Kaggle tidak diganti (pip memakai keduanya sebagai *constraint*). YOLOX: modul kernel dicek dengan impor sungguhan (yang rusak/kurang dipasang ulang), checkout YOLOX commit terkunci (dipakai ulang bila sel dijalankan lagi), bobot `yolox_nano.pth` dicek sha256 |
| 4 | Cek alat (MediaPipe: latih 1 epoch + QAT pada 8 foto lalu ekspor; YOLOX: impor persis seperti `tools/train.py` + konvolusi CUDA nyata — gagal dalam menit, bukan jam) |
| 5 | Konfigurasi run + gerbang lisensi (dry run) |
| 6+ | Latih → ekspor INT8 → paritas → prediksi → ambang (val) → evaluasi (test) → bundel → zip unduhan → cek "tidak ada foto di keluaran" |
| 6 (YOLOX) | **1 GPU saja** walau T4 x2 (DDP belum pernah teruji di sini). `tools/train.py` YOLOX keluar dengan kode 0 walau *crash*; notebook memeriksa `train_log.txt` + epoch di checkpoint, jadi crash tetap terlihat. Batas waktu menghentikan pelatihan **tepat setelah checkpoint epoch berikutnya** (bukan di tengah penulisan). Keluaran stdout/stderr pelatih ditulis ke **berkas** `yolox_outputs/dentis_yolox_nano_exp/trainer_output.txt`, bukan ke layar notebook (di Kaggle, pipa stderr yang tidak terbaca membekukan pelatihan di epoch 9); sel hanya mencetak baris kemajuan tiap menit dan 40 baris terakhir berkas itu bila pelatihan gagal |
| 9 (YOLOX) | Rantai konversi: `onnxsim` → INT8 `tf_converter` (±90 dtk) → **hanya bila itu tidak menghasilkan model INT8 yang bisa dijalankan**: INT8 `flatbuffer_direct` (per-channel, lalu per-tensor; batas 7 mnt per percobaan, karena di Kaggle keduanya pernah macet 25 mnt di kalibrasi) → float16 → float32. INT8 gagal **tidak** menghentikan run: tercatat di `out/conversion.json` dan `measurements_partial.json` (`precision`, `int8_method`, `int8_ok`) dan ditampilkan dalam kotak `!!!!` |

Semua foto, venv, dan berkas `.npy` ada di `/tmp/dentisai` (tidak ikut tersimpan). Yang tersimpan hanya
`/kaggle/working/outputs/<kandidat>[_smoke]/` (laporan, konfigurasi, bundel `.tflite`, checkpoint YOLOX untuk resume).

## 5. Perkiraan waktu & kuota (PERKIRAAN, belum diukur di Kaggle — catat angka nyata di laporan)
| Tahap | YOLOX-Nano | MediaPipe MobileNetV2 |
|---|---|---|
| Uji cepat (`SMOKE_TEST = True`) seluruhnya | ± 20–30 mnt | ± 25–35 mnt |
| Unduh Zenodo 1,6 GB (run penuh) | 10–40 mnt [PERLU VERIFIKASI: kecepatan Zenodo→Kaggle] | sama |
| Pemasangan | 5–10 mnt | 10–15 mnt (TF 2.15 + CUDA ±3 GB) |
| Latih | 150 epoch: ± 2–5 jam | 30 epoch + 10 QAT: ± 1,5–3 jam |
| Ekspor + paritas + prediksi + evaluasi | 20–40 mnt | 20–40 mnt |
| **Total run penuh** | **± 3–6,5 jam** | **± 2,5–5 jam** |
Dua uji cepat + dua run penuh ≈ 7–12,5 jam dari kuota ±30 jam/minggu.

## 6. Yang diunduh & ditaruh di mana
1. Versi selesai → tab **Output** → unduh **`outputs/<kandidat>/download_<kandidat>.zip`** saja (sel terakhir menandainya
   "UNDUH INI"). Zip ini tanpa gambar, tanpa `.npy`, tanpa checkpoint.
2. Ekstrak ke `backend/ml/runs/kaggle/<kandidat>/<YYYYMMDD>/` (folder `ml/runs/` di-*gitignore* — **jangan commit**).
3. Isi: `RUN_SUMMARY.json` (sha256 tiap berkas), `run.json`, `report_int8_val.json`, `report_int8_test.json`,
   `report_float_test.json`, `thresholds.json`, `parity.json`, `measurements_partial.json`, `env.json` (sumber kode +
   commit, sumber data), konfigurasi run, dan bundel `0.1.0-dev.N/` (`model.tflite`, `labels.json`, `MODEL_CARD.md`, `SHA256`).
4. **Jangan** salin bundel dev ke `ml/exports/` — bundel resmi hanya lewat item 4.6 setelah ADR bake-off.
5. Hasil uji cepat (`*_smoke`) **tidak** dipakai untuk bake-off (metriknya tidak bermakna).

## 7. Melapor kembali
```bash
cd backend/ml/detection
uv run python bake_off/compare.py --adr \
  --report yolox_nano=../runs/kaggle/yolox_nano/<tgl>/download_yolox_nano/report_int8_test.json \
  --report mediapipe_modelmaker_mobilenetv2_ssd=../runs/kaggle/mediapipe_modelmaker_mobilenetv2_ssd/<tgl>/download_mediapipe_modelmaker_mobilenetv2_ssd/report_int8_test.json \
  --meta ../runs/kaggle/yolox_nano/<tgl>/download_yolox_nano/measurements_partial.json \
  --meta ../runs/kaggle/mediapipe_modelmaker_mobilenetv2_ssd/<tgl>/download_mediapipe_modelmaker_mobilenetv2_ssd/measurements_partial.json \
  --out bake_off/ADR-draft.md
```
Kirim ke tim/ Claude: `ADR-draft.md`, kedua `RUN_SUMMARY.json`, `parity.json`; bila gagal: **nomor sel + 50 baris
terakhir log** (*Versions → Logs*). Jangan kirim gambar atau tangkapan layar berisi foto. Latensi HP (spike S1) diisi
terpisah bersama tim app ke `bake_off/measurements.yaml`.

## 8. Masalah umum (gejala → penyebab → tindakan)
| Gejala (sel) | Penyebab | Tindakan |
|---|---|---|
| 1: `KODE TIDAK DITEMUKAN ... GITHUB_TOKEN` | Secret belum dibuat atau belum dicentang untuk notebook ini | §3 langkah 3; jalankan ulang dari sel 0 |
| 1: `Token ... ditolak GitHub` | Token salah/ kedaluwarsa | Buat token baru (§2), ganti *Value* secret |
| 1: `Repo Ryhn-F/dentisin-api tidak terlihat` | Token tidak memilih repo itu / tanpa *Contents: Read* | Edit token di GitHub: tambah repo + Contents Read-only |
| 1: `CODE_REF = ... tidak ada di repo` | Nama cabang/ tag salah di sel 0 | Kembalikan ke `"main"` |
| 1: `Kode yang terambil lebih lama dari notebook` | Perubahan terbaru belum di-push ke GitHub `main` | Minta engineer push; atau `CODE_REF` ke cabang yang benar |
| 1: `Internet MATI` | Settings → Internet Off, atau HP belum diverifikasi | Internet → On; verifikasi HP (§1) |
| 1: `GPU MATI` / `P100` | Accelerator None/ P100, atau kuota GPU habis | Accelerator → GPU T4 x2; cek kuota |
| 1: `Ruang disk kurang` | Sisa run lama di `/kaggle/working` | *Run → Restart & clear*; hapus `outputs/` lama (setelah diunduh) |
| 1 (Colab): `Ruang disk kurang: drive (...)` | Google Drive penuh (15 GB gratis dipakai bersama Gmail/Foto) | Kosongkan Drive (hapus folder `yolox_nano.prev-*` lama, Sampah), jalankan ulang sel 1 |
| 1 (Colab): `Google Drive TIDAK terpasang` / `BERHENTI: pelatihan sungguhan di Colab butuh Google Drive` | Pop-up Drive ditolak/ ditutup | Jalankan ulang sel 1, pilih *Connect to Google Drive* → *Allow* |
| 1 (Colab): `KODE TIDAK DITEMUKAN` | Secret Colab belum dibuat atau *Notebook access* mati | §3b langkah 3 |
| 1 (Colab): `GPU MATI` | Runtime masih CPU, atau kuota GPU gratis hari ini habis | §3b langkah 2; bila T4 tidak tersedia coba lagi nanti atau pakai Kaggle |
| 5 (Colab): `PERINGATAN Drive: ... BELUM tersimpan di Drive` | Menulis ke Drive gagal (penuh/ terputus) | Kosongkan Drive; checkpoint tetap aman di VM selama sesi hidup, dan disalin lagi pada putaran berikutnya |
| 5 (Colab): `folder lama dipindah ke ...prev-...` | Pengaturan berbeda dari run yang ada di Drive (atau `RESUME = False`) | Normal. Pelatihan mulai dari awal; hapus folder `.prev-*` bila tak dibutuhkan |
| 3 (Colab): tombol **RESTART SESSION** muncul setelah pemasangan | Colab minta kernel dimuat ulang setelah pip mengganti paket | Klik *Restart session* (bukan *Delete runtime*), lalu **Run all** lagi dari sel 1; pemasangan yang sudah ada dipakai ulang |
| 2: `download failed after ... attempts` / `no download URL works` | Zenodo sedang lambat/ gangguan (502/504) | Tunggu 10–30 mnt, jalankan ulang sel 2 (unduhan melanjutkan bagian yang sudah ada dalam sesi yang sama). Alternatif: §9 |
| 2: `MD5 mismatch` | Unduhan rusak | Jalankan ulang sel 2 (berkas rusak sudah dihapus) |
| 2: `canonical.jsonl and splits.json disagree` | Dataset terlampir bukan `Dataset.zip` v1 yang utuh | Lepas dataset itu (biarkan notebook mengunduh sendiri) |
| 2: `committed ... damaged` / `fill ml/data/class_maps` | Berkas split/ peta kelas di repo berubah | Laporkan ke engineer data; jangan diakali |
| 3: `could not create a Python 3.10 environment` (MediaPipe) | uv gagal mengunduh Python dari github.com | Cek Internet; jalankan ulang sel 3; bila tetap gagal laporkan log |
| 3: `TensorFlow ... sees NO GPU` (MediaPipe) | TF 2.15 tidak menemukan pustaka CUDA | Pastikan GPU T4; laporkan 50 baris log sel 3. **Jangan** latih 30 epoch di CPU |
| 4: `toolcheck produced no QAT model` / error Model Maker | Paket/ format data tidak cocok | Hentikan, laporkan log sel 4 (ini tujuan sel 4: gagal dalam menit) |
| 4: `PyTorch tidak melihat GPU` (YOLOX) | GPU tidak aktif | Accelerator → GPU T4 x2 |
| 3 (YOLOX): `kernel modules missing/broken -> installing pinned versions` | Kernel Kaggle tidak punya / punya versi rusak (mis. pycocotools untuk numpy lama) | Normal; dipasang otomatis dengan pin. Bila lalu `modules still not importable after pip install`: laporkan 50 baris log sel 3 |
| 3 (YOLOX): pip `conflicting dependencies` menyebut `torch==...` / `numpy==...` | Paket meminta torch/numpy lain; notebook **sengaja menolak** mengganti torch/numpy Kaggle | Jangan memasang ulang torch. Laporkan log ke engineer (pin perlu disesuaikan) |
| 3: `Paket di venv tidak bisa di-import (...)` | Versi terkunci di `requirements/*.txt` cocok di atas kertas tetapi rusak saat dipakai | Laporkan log; engineer memperbaiki pin (contoh lama: setuptools ≥ 81 tanpa `pkg_resources`) |
| 3: baris `... 120s elapsed \| venv-export: 850 MB \| free disk ...` berulang | Pemasangan besar (TensorFlow) sedang berjalan; itu tanda masih hidup | Tunggu. Bila `command timed out after 3600s`: cek Internet, jalankan ulang sel 3 (unduhan dilanjutkan dari cache) |
| 3: `shallow fetch of the pinned commit failed -> full clone` | GitHub menolak fetch per-commit | Normal; notebook mengkloning penuh lalu checkout commit yang sama |
| 3: `yolox_nano.pth: sha256 ... != expected` | Unduhan bobot rusak/berubah | Jalankan ulang sel 3 (berkas rusak sudah dihapus); bila berulang, laporkan |
| 5: `config says license_verified=true but the gate has unverified ...` | Patch `license_gate.yaml` (WEIGHT_LICENSES.md §4) belum diterapkan | Laporkan ke engineer data. **Jangan** mengedit gerbang dari notebook |
| 6: `CUDA out of memory` | Batch terlalu besar | Sel 0: `BATCH_SIZE = 16` |
| 6: `training failed: ...TimeoutExpired` (MediaPipe) | Waktu sesi habis (tidak bisa resume) | Kurangi `EPOCHS` (mis. 20) dan jalankan lagi |
| 6: `BATAS WAKTU SESI TERCAPAI` (YOLOX) | Normal untuk run panjang | Ikuti §3 "Lanjut (resume)" |
| 6 (YOLOX): `YOLOX training crashed (tools/train.py exits with code 0 on errors ...)` | Crash di dalam YOLOX yang dulu tersembunyi (exit 0) | Laporkan pesan itu + 50 baris log. Jangan mengulang run penuh sebelum diperbaiki |
| 6 (YOLOX): `YOLOX stopped after epoch X of Y without ...` | Proses mati tanpa pesan (RAM/ GPU penuh, dihentikan) | `BATCH_SIZE = 16`, jalankan lagi (melanjutkan dari checkpoint bila bukan uji cepat) |
| 6 (YOLOX): `NOTE: trainer_options.devices=2 ignored -> 1 GPU` | Disengaja: DDP 2 GPU belum teruji, bisa macet (NCCL) | Tidak perlu tindakan |
| 6 (YOLOX): `resume checkpoint ... is unreadable` | Checkpoint di input versi lama rusak | Lepas input versi itu (atau pilih versi sebelumnya) |
| 6 (YOLOX): `YOLOX wrote nothing to train_log.txt for 10 min ... a hang` | Pelatihan macet (bukan lambat): biasanya DataLoader worker, GPU, atau evaluasi yang tidak kembali. Sebelumnya terlihat sebagai ±10 jam tanpa keluaran | Sebelum menghentikan, notebook mencetak laporan: tumpukan Python semua thread + `nvidia-smi` + status tiap proses (D = menunggu I/O, Z = worker mati). Laporan yang sama tersimpan di `yolox_outputs/dentis_yolox_nano_exp/stall_report.txt` dan `stacks.txt` (ikut terunduh/ ikut tercermin ke Drive). Kirim berkas itu + 20 baris output sel sebelum pesan tadi + `train_log.txt` |
| 6 (YOLOX): pelatihan membeku di epoch ±9 tanpa error; laporan macet menunjukkan thread utama di `loguru ... emit` → `multiprocessing ... _send` dan proses berstatus `anon_pipe_write` | Pipa stderr ke notebook penuh dan tidak dibaca (Kaggle 2026-10-17): sink stderr loguru memblokir, antreannya penuh, loop latihan berhenti | Sudah diperbaiki: keluaran pelatih ke berkas `trainer_output.txt`. Bila masih terjadi, kirim `stall_report.txt` |
| 6 (YOLOX): 10 menit pertama sangat lambat (`iter_time` ±6 dtk per 10 iterasi, jeda ±55 dtk berulang) | cuDNN *autotune* (`cudnn.benchmark`, dinyalakan YOLOX) berjalan ulang untuk TIAP ukuran masukan baru; pelatihan multi-skala memakai 11 ukuran (320–640), jadi 11 jeda (Kaggle 2026-10-17) | Sudah dimatikan di Exp (`trainer_options.cudnn_benchmark`, default `False`); setelah kode terbaru, 150 epoch tidak lagi berhenti ±10 menit di awal |
| 7: `file named ... not found` / `instances_*.json` tidak ada | Tata letak data tidak cocok dengan YOLOX (seharusnya tidak terjadi; dicek lokal) | Laporkan log sel 6–7 |
| 8–13: `ValueError: ... matplotlib_inline` atau versi numpy aneh di venv | Variabel lingkungan kernel bocor ke venv (sudah dicegah) | Laporkan log; jangan mengubah variabel lingkungan sendiri |
| 9 (YOLOX): kotak `!!!!` "model INT8 TIDAK lolos" | Semua INT8 gagal/menyimpang; run tetap lanjut dengan INT8 terbaik atau float16/float32 (`int8_ok: false`) | Biarkan selesai. Uji cepat: wajar. Run penuh: putuskan — run ulang dengan `ACT = "relu"` (sel 0) atau terima sebagai catatan bake-off |
| 9 (YOLOX): `Konversi ONNX -> TFLite gagal total` | Tidak ada satu pun TFLite (bahkan float32) | Laporkan `out/conversion.json` + log sel 9; rencana B `litert-torch` (WEIGHT_LICENSES.md §3.3) |
| 9/10: `GERBANG UKURAN: model ... MiB > 15 MiB` | Model terlalu besar untuk ADR 001 | Uji cepat: hanya dilaporkan. Run penuh: ekspor ditolak → laporkan (tidak diharapkan untuk Nano, ±1–4 MB) |
| 10: `parity (int8 vs float32) passed: False` | INT8 menyimpang dari float (SiLU) | Biarkan selesai (tetap dilaporkan), lalu run ulang dengan `ACT = "relu"` |
| 12 (uji cepat): ambang `1.0`, sensitivitas 0, `n_pred_boxes` 0 | Model 2 epoch belum mendeteksi apa pun | Normal untuk uji cepat |
| Sel mana pun dijalankan ulang | Aman: venv, checkout YOLOX, bobot, data dipakai ulang; laporan konversi lama dihapus dulu | Bila ragu: *Run → Restart & clear* lalu Run All |
| `no kernel image is available` | GPU P100 | Ganti ke T4 x2 |
| Sesi interaktif mati saat browser ditutup | Run interaktif butuh browser terbuka | Untuk run penuh pakai *Save & Run All (Commit)* |

## 9. Opsional: lampirkan data/ kode sebagai dataset Kaggle (lebih cepat, tidak wajib)
- **Data**: Kaggle → *Datasets* → *New Dataset* → unggah `Dataset.zip` asli dari https://zenodo.org/records/14827784
  (atau gunakan opsi *Link/ Remote URL* bila tersedia) → judul `ahmed2025-caries` → **Private** → *License* CC BY 4.0 →
  *Description*: tempel atribusi di bawah. Lalu di notebook: *Add Input* → dataset itu. Notebook memakainya otomatis
  (folder `Dataset/Images` atau `Dataset.zip` dengan ukuran 1.576.115.314 byte), tanpa unduhan Zenodo.
  > Contains data from "Annotated intraoral image dataset for dental caries detection" by S. M. F. Ahmed, H. Ghori,
  > A. Khalid, A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784,
  > licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Uploaded unchanged as a private copy.
- **Kode** (tanpa token GitHub): engineer menjalankan
  `cd backend/ml/detection && uv run python artifacts.py pack-code --out ../../../dentisai-ml-code.zip`, Anda mengunggahnya
  sebagai dataset **Private** `dentisai-ml-code` dan menambahkannya lewat *Add Input*. Dataset kode terlampir **selalu
  didahulukan** daripada GitHub — lepas dataset lama bila kode di GitHub lebih baru.

## 10. Yang BELUM terbukti (jujur)
- Belum pernah dijalankan di Kaggle sungguhan (tidak ada GPU/ akun Kaggle di PC tim).
- **Disimulasikan di PC (2026-10-11, `kaggle/local_simulate.py`)**: `.ipynb` hasil generator dieksekusi sel demi sel pada
  tata letak Kaggle palsu — preflight, `git clone` lewat jalur token (secret palsu; dicek token tidak bocor ke log/ `.git`),
  dataset kode terlampir, data lewat HTTP Range dari server lokal yang menyajikan `Dataset.zip` ASLI, zip terlampir,
  folder terlampir (run penuh: 6.265 foto, sha256 split cocok dengan repo), konversi, split, konfigurasi + gerbang
  lisensi, dan tata letak COCO MediaPipe/ YOLOX dicek terhadap aturan pustaka (MediaPipe membaca `images/` + `labels.json`
  dan **membuang foto tanpa kotak**).
- **Docker Linux (python:3.11, CPU, torch 2.14.1)**: notebook YOLOX sel 0–8 berjalan sungguhan: venv ekspor Python 3.12
  (uv mengunduh CPython) dari berkas terkunci, YOLOX dari commit terkunci, bobot `yolox_nano.pth` dimuat, 1 iterasi latih
  CPU pada tata letak nyata (pengganti `tools/train.py` yang hanya-CUDA), ekspor ONNX (3549×7, 6 sigmoid dibuang),
  kalibrasi/paritas `.npy`. Sel 9 (onnx2tf) gagal di simulasi: tanpa TensorFlow (`tf_converter` tak bisa jalan) dan
  `onnxsim` tak ditemukan di PATH → INT8 `flatbuffer_direct` menolak op `EQUAL`. PATH sudah diperbaiki; sel 9–15
  kini terbukti di simulasi Windows (butir berikut), belum di Linux/ Kaggle.
- **YOLOX, simulasi Windows CPU (2026-10-12/13, `local_simulate.py --level deep --export-python ... --rerun ...`)**: notebook
  `.ipynb` hasil generator berjalan **sel 0–15 sampai "UJI CEPAT BERHASIL"** (±9 menit) lalu seluruh sel dijalankan ulang
  di kernel yang sama tanpa error. Yang nyata: checkout YOLOX commit terkunci, bobot `yolox_nano.pth` (sha256), pelatihan
  2 epoch dengan `tools/train.py` + `Trainer` YOLOX **asli** (hanya panggilan CUDA ditukar ke CPU; mosaic di epoch 1,
  no-aug di epoch 2, evaluasi COCO tiap epoch), ekspor ONNX, `onnxsim`, onnx2tf 2.6.9 (TF 2.21) semua attempt, paritas,
  prediksi, ambang, evaluasi, bundel, zip. Hasil konversi: `tf_converter` INT8 jadi tetapi **paritas cosine 0,962 < 0,98**
  (SiLU, model 2 epoch); `flatbuffer_direct` INT8 gagal (*quantized_dimension* pada depthwise conv; per-tensor: bias INT32
  ≠ INT64) → rantai memilih INT8 terbaik dan menandai `int8_ok: false`. Juga terbukti nyata: pycocotools 2.0.10 +
  YOLOX → `KeyError: 'info'` saat evaluasi **dengan exit code 0** (kini ditangkap; Exp memperbaikinya), dan batas waktu
  menghentikan pelatihan setelah checkpoint epoch lalu sesi berikutnya melanjutkan sampai selesai.
- Instalasi venv Model Maker (TF 2.15.1 + CUDA 12.2) dan pelatihan **GPU** (YOLOX `tools/train.py` di CUDA/ fp16,
  Model Maker) belum berjalan di mana pun; versi terkunci diselesaikan oleh `uv pip compile` untuk Linux x86_64.
- Venv ekspor Linux dari berkas terkunci belum dipasang di Linux sejak itu (di Windows dipakai pin tingkat-atas yang sama).
- Kecepatan unduh Zenodo dari Kaggle, ketersediaan uv/ Python 3.10 di image Kaggle saat ini, dan kuota Kaggle [PERLU VERIFIKASI pada run pertama].

## 11. Colab (cadangan)
Runtime → *Change runtime type* → T4 GPU. Simpan token di Colab *Secrets* (ikon kunci) bernama `GITHUB_TOKEN` dan beri
akses notebook. Keluaran ke `MyDrive/dentisai_runs/` (perlu `from google.colab import drive; drive.mount('/content/drive')`
di sel baru sebelum sel 1). Sesi Colab gratis bisa putus kapan saja. Aturan data sama persis dengan Kaggle.
