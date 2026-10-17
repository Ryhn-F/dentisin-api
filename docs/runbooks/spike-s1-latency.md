# Spike S1: latensi, memori, dan kesetaraan model di HP nyata

> Untuk **tim app + pemilik produk**. Menutup syarat "latensi" ADR 013 / gerbang G2 (rencana 2.6). Hasilnya mengisi `measurements.yaml` untuk `bake_off/compare.py` dan menentukan apakah **YOLOX-Nano float32** boleh dikirim apa adanya.
> Acuan: `docs/api/MODEL_BUNDLE.md`, ADR 013, ADR 004 (spike S1), `ml/detection/bake_off/README.md` (protokol).
> Aturan data: **jangan memakai foto anak**; pakai fixture sintetis, foto objek biasa, atau foto orang dewasa yang setuju. Tidak ada unggahan ke mana pun.

## 1. Gerbang yang diuji

| Gerbang | Nilai | Sumber |
|---|---|---|
| Latensi total per foto, HP RAM 3 GB **dan** 4 GB | ≤ 1,5 detik (median), termasuk praproses + inferensi + dekode + NMS | ADR 001 / 013 |
| Ukuran model | ≤ 15 MB (float32 = 3,48 MiB) | ADR 001 |
| Stabilitas | tidak crash/ANR; memori puncak wajar (catat angkanya) | ADR 004 |
| Kesetaraan | keluaran mentah HP ≈ fixture golden (toleransi 1e-3) | MODEL_BUNDLE §5 |

## 2. Persiapan

1. Bundel dev dari backend (`model.tflite`, `labels.json`, `SHA256`); verifikasi hash. Fixture: `docs/api/examples/detector/golden/golden_runtime.json`; dekoder: `docs/api/examples/detector/decode.ts` (+ tes lulus di Node).
2. Build **dev-build** Expo (bukan Expo Go) dengan `react-native-fast-tflite` (v3) — rencana app memakai delegasi `default` (CPU/XNNPACK) dan opsional `android-gpu` (ADR 004). `tflite` ditambahkan ke `assetExts` Metro.
3. Perangkat (isi, minimal 3 sesuai rencana 2.6):

| Slot | Model HP | RAM | SoC | Android | Catatan |
|---|---|---|---|---|---|
| ram_3gb | | 3 GB | | | kelas menengah-bawah (sasaran proyek) |
| ram_4gb | | 4 GB | | | |
| ram_6gb | | ≥ 6 GB | | | pembanding atas |

Matikan penghemat daya, biarkan HP ±5 menit pada suhu ruang, baterai ≥ 50%, layar menyala, tanpa aplikasi berat lain.

## 3. Langkah A — paritas runtime (wajib lulus dulu)

1. Buat tensor masukan dari `input.pattern` di `golden_runtime.json` (bilangan bulat; periksa `sha256_float32_le`).
2. Jalankan `model.tflite` (delegasi `default`) → tensor keluaran `[1, 3549, 7]`.
3. Bandingkan dengan `output.raw_float32_le_base64`: selisih mutlak maksimum ≤ `tolerance.raw_abs` (1e-3).
4. Dekode keluaran HP dengan `decode.ts` memakai kedua `cases[]`: jumlah deteksi sama; skor dalam `score_abs` (1e-4); kotak dalam `box_photo_px_abs` (0,5 px foto).
5. Gagal? Jangan mengukur latensi dulu. Periksa urutan kanal (BGR), layout NHWC, tipe float32, dan apakah pustaka melakukan normalisasi sendiri.
6. Ulangi dengan `android-gpu` bila dipakai: delegasi GPU boleh memberi selisih lebih besar; catat selisih maksimumnya dan putuskan toleransi bersama backend (jangan melonggarkan diam-diam).

## 4. Langkah B — pengukuran latensi

Per perangkat dan per delegasi:

- 5 pemanasan (dibuang), lalu **≥ 30 run** pada foto yang sama (resolusi kamera nyata, mis. 12 MP, sudah diputar EXIF).
- Catat tiap komponen: `preprocess_ms` (decode/resize/letterbox/tensor), `infer_ms` (model.run), `postprocess_ms` (sigmoid, NMS, pemetaan ke foto), dan `total_ms`.
- Laporkan **median** dan p90 `total_ms`; gerbang memakai median.
- Ukur juga: pemuatan model pertama kali (`load_ms`), memori puncak proses (MB), suhu/throttling bila terasa (ulangi 30 run setelah 2 menit beban).
- Uji sekali dengan resizer yang dipakai app sungguhan dan sekali dengan resize bilinear sederhana; catat selisih skor/deteksi pada foto uji (kesetaraan resize masih terbuka, MODEL_BUNDLE §10).

## 5. Langkah C — keputusan

| Hasil (median total, HP 3 GB) | Keputusan |
|---|---|
| ≤ 1,5 s | **Float32 dikirim.** Isi `measurements.yaml`, perbarui ADR 013 (bagian S1), lanjut 4.6 bundel resmi |
| 1,5–3 s | Coba float16 (`SHIP = "float16"` di notebook, 1 run ekspor tanpa melatih ulang); ukur ulang. Coba `android-gpu` |
| > 3 s atau crash/OOM | Eskalasi ke backend: perbaiki INT8 (mis. `ACT = "relu"` + latih ulang ±2 jam, atau pisahkan keluaran kotak/logit) atau kandidat cadangan MediaPipe (ADR 013) |

Kesetaraan runtime gagal di langkah A → perbaikan di app atau bundel dulu; latensi tidak berarti selama keluarannya salah.

## 6. Format hasil (`ml/detection/bake_off/measurements.yaml`)

```yaml
yolox_nano:
  precision: float32
  model_size_mb: 3.482
  latency_ms: {ram_3gb: <median total ms>, ram_4gb: <median total ms>}   # HP RAM 3 GB dan 4 GB
  devices: {ram_3gb: "<model HP>", ram_4gb: "<model HP>"}
  export_difficulty: medium
  parity_passed: null          # float: tidak berlaku
```

Lampirkan juga (ke ADR 013, bagian "Perangkat uji"): model HP, SoC, versi Android, delegasi, jumlah run, median/p90 per komponen, memori puncak, dan tanggal.
Jalankan perbandingan: `uv run python bake_off/compare.py --adr --report … --meta bake_off/measurements.yaml` (lihat `kaggle/RUNBOOK.md` §7).

## 7. Yang tidak dibuktikan oleh spike ini

Akurasi pada foto anak Indonesia, perilaku di bawah cahaya senter yang beragam, dan validasi klinis (Fase 7). S1 hanya membuktikan model berjalan benar dan cukup cepat di HP sasaran.
