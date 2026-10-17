# Bundel model detektor (ADR 013): YOLOX-Nano float32

> **Ringkasan (ID).** Detektor foto berjalan **di HP** (offline-first). Model terpilih bake-off adalah **YOLOX-Nano, TFLite float32** (3,5 MB), bukan INT8. Dokumen ini adalah panduan lengkap untuk tim app: isi bundel, cara menjalankan model, cara membaca keluarannya, kode TypeScript acuan yang sudah diuji terhadap implementasi Python, dan batasan yang harus tampil di UI.
> Status: bundel **dev** `0.1.0-dev.2` (`distribution: pilot`, `validation_status: NOT_VALIDATED`). Tanggal: 2026-10-17. Keputusan: [`../../../docs/decisions/013-detector-bake-off-yolox-nano.md`](../../../docs/decisions/013-detector-bake-off-yolox-nano.md). Hasil di sini **bukan klaim akurasi klinis**.

Kontrak yang mengikat tetap: [`../../ml/detection/DECODE_SPEC.md`](../../ml/detection/DECODE_SPEC.md) (v1.0.0), [`../../ml/detection/labels.schema.json`](../../ml/detection/labels.schema.json) (`schema_version 2.0.0`), implementasi acuan [`../../ml/detection/decode.py`](../../ml/detection/decode.py). Dokumen ini tidak menggantikannya; ia menunjukkan cara memakainya untuk model yang benar-benar dipilih.

## 1. Apa yang berubah dari dokumen sebelumnya

| Sebelum (draf) | Sekarang (ADR 013) |
|---|---|
| Contoh `labels.json` bervarian `int8`, `int8: true` | Model terpilih **float32**: `int8: false`. INT8 YOLOX gagal (mAP50 0,83 turun ke 0,27; kecocokan deteksi vs float 0,149) sehingga tidak dikirim |
| Ambang skor `null` / `PLACEHOLDER` | `thresholds.status = "set_on_val"`: `caries_permanent` 0,5914 · `caries_primary` 0,0096. Belum ditinjau dokter gigi |
| `tensors_verified: false` | `true` (bentuk dan tipe dibaca interpreter saat ekspor) |
| Dekoder hanya kalimat di spesifikasi | Ada port TypeScript yang lulus vektor paritas: [`examples/detector/`](examples/detector/) |

`ml/exports/` masih kosong: bundel resmi dibuat lewat item rencana 4.6 setelah syarat ADR 013 terpenuhi. Sampai itu, bundel dev diberikan oleh backend (bukan dari git).

## 2. Isi bundel

| Berkas | Fungsi |
|---|---|
| `model.tflite` | 3.651.644 byte (3,48 MiB), sha256 `3aa743f2…8bfb6` |
| `labels.json` | **sumber semua angka** yang dipakai app: kelas, ukuran input, praproses, bentuk keluaran, cara dekode, NMS, ambang, lisensi, atribusi |
| `MODEL_CARD.md` | batasan dan evaluasi (Indonesia + English) |
| `SHA256` | `sha256sum` ketiga berkas |

Sebelum dipakai (berurutan): verifikasi manifes bertanda tangan → `SHA256` semua berkas → `labels.model.sha256 == sha256(model.tflite)`. Tolak seluruh bundel bila salah satu gagal. Jangan memuat sebagian.

## 3. Spesifikasi masukan dan keluaran (dari `labels.json` bundel dev)

| Hal | Nilai |
|---|---|
| Kelas | `0 = caries_permanent` (gigi tetap, label dataset "D") · `1 = caries_primary` (gigi sulung, "d"). Tidak ada kelas lain; tidak ada berlubang/belum berlubang |
| Masukan | `[1, 416, 416, 3]`, **float32**, NHWC, urutan kanal **BGR**, nilai piksel **0–255** (tanpa normalisasi: mean 0, std 1) |
| Ukuran/resize | `letterbox`: skala seragam `r = min(416/W, 416/H)`, foto ditaruh **kiri-atas**, sisa kanvas diisi **114** di semua kanal, `interpolation: bilinear` |
| Keluaran | satu tensor `[1, 3549, 7]` float32 (`PartitionedCall:0`), `role: raw`. Baris = `[tx, ty, tw, th, obj, cls_permanent, cls_primary]` |
| Nilai keluaran | `obj` dan `cls_*` adalah **logit** (`sigmoid_applied: false`): terapkan sigmoid sendiri |
| Urutan baris | stride-major (8, 16, 32), lalu y, lalu x. 52×52 + 26×26 + 13×13 = 3549 |
| Dekode | `cx=(tx+gx)·s`, `cy=(ty+gy)·s`, `w=exp(tw)·s`, `h=exp(th)·s`; `kelas = argmax(cls)`, `skor = obj·cls[kelas]` |
| Pasca-proses | saring `skor >= ambang[kelas]` → NMS per kelas, IoU > 0,45 menekan → urut skor → maks. 20 deteksi (`status: technical_default`) |
| Kotak | `box_use: ui_overlay_only` — lihat bagian 7 |

## 4. Alur di app, langkah demi langkah

```
foto (JPEG, EXIF diputar) ──► quality gate ──► letterbox 416 ──► tensor BGR float32 ──► model.tflite ──► raw [1,3549,7]
                                                                                                          │
   overlay (kotak di foto) ◄── kotak foto (px) ◄── NMS + ambang + sigmoid ◄──────────────────────────────┘
   triase ◄── [{ class, confidence }]  (kotak TIDAK dikirim ke triase)
```

1. **Gerbang bundel** (bagian 2) dan gerbang build: `labels.distribution == "internal_experiment_only"` atau ambang `null` → jangan tampilkan hasil di build pengguna. Bundel dev ini `pilot`; build pengguna tetap mengikuti ADR 002.
2. **Quality gate** ([`CLIENT_CONTRACTS.md` §6](CLIENT_CONTRACTS.md)): gagal → `photo_status = quality_failed`, jangan jalankan model.
3. **Dekode foto** dan terapkan orientasi EXIF sebelum mengukur `W`/`H`. Semua koordinat akhir ada pada foto yang sudah diputar.
4. **Letterbox**: `letterboxLayout(W, H, 416, 416)` memberi `newW`, `newH` (`floor`, minimal 1). Ubah ukuran foto ke `newW × newH` dengan filter bilinear, taruh di kiri-atas kanvas 416×416 yang diisi 114.
5. **Tensor**: `canvasToTensor(kanvasRGB, { colorOrder: "BGR", pixelRange: [0, 255], mean: [0,0,0], std: [1,1,1] })`. Ambil semua nilai dari `labels.json`, jangan di-hardcode.
6. **Inferensi** LiteRT (CPU/XNNPACK) → `Float32Array(3549 × 7)`.
7. **Dekode**: `detect(labels, raw, W, H)` mengembalikan `[{ box: [x1,y1,x2,y2] px foto, classId, score }]`.
8. Kirim `{ class, confidence }` ke mesin triase; simpan `box` hanya untuk overlay dan `findings[].box` (ternormalisasi) pada event sinkron.

Kode TypeScript acuan (salin ke app, simpan tesnya di sebelahnya):

```ts
import { detect, letterboxLayout, canvasToTensor, type DetectorLabels } from './decode';

const labels: DetectorLabels = JSON.parse(labelsJsonText);        // sudah diverifikasi (bagian 2)
const L = letterboxLayout(photoW, photoH, labels.input.width, labels.input.height, labels.input.resize.align);
// ... ubah ukuran foto ke L.newW x L.newH, taruh di (L.offsetX, L.offsetY) pada kanvas 416x416 berisi 114 -> canvasRGB
const input = canvasToTensor(canvasRGB, { colorOrder: 'BGR', pixelRange: [0, 255], mean: [0, 0, 0], std: [1, 1, 1] });
const raw = await model.run(input);                               // Float32Array(3549 * 7)
const findings = detect(labels, raw, photoW, photoH);              // melempar bila ambang null / dekoder tak didukung
const forTriage = findings.map((f) => ({ class: labels.classes[f.classId].name, confidence: f.score })); // tanpa box
```

`detect(..., { allowPlaceholderThresholds: true })` hanya untuk build internal/dev.

## 5. Bukti bahwa port TypeScript benar

Berkas di [`examples/detector/`](examples/detector/):

| Berkas | Isi |
|---|---|
| `decode.ts` | `sigmoid`, `dequantize`, `letterboxParams/Layout`, `unletterbox`, `canvasToTensor`, `decodeYoloxRaw`, `nms`, `postprocess`, `detect` (hanya keluarga `yolox_raw`) |
| `decode.test.ts` | 10 uji (`node:test`): vektor paritas Python, geometri letterbox, jalur logit, alur penuh `detect`, dan fixture golden di bawah |
| `golden/golden_runtime.json` | **keluaran mentah model nyata** pada masukan sintetis + deteksi yang diharapkan |

Jalankan dari `backend/` (Node ≥ 22.18; tanpa dependensi):

```bash
node --test "docs/api/examples/detector/*.test.ts"
# di repo app: DECODE_VECTORS=<salinan decode_vectors.json> GOLDEN_RUNTIME=<salinan golden_runtime.json> node --test ...
```

Dua tingkat bukti:

1. **Dekoder (deterministik):** kode TS mendekode tensor mentah yang tersimpan di fixture dan harus sama dengan keluaran `decode.py` (toleransi 1e-6) untuk dua kasus (ambang bundel; ambang diturunkan sampai 14 deteksi lolos agar NMS dan pemetaan ke foto teruji). Ini sudah lulus di backend.
2. **Runtime di HP (spike S1):** buat tensor masukan dari rumus di `golden_runtime.json → input.pattern` (hanya bilangan bulat, identik di semua bahasa; `input.sha256_float32_le` untuk memeriksa), jalankan `model.tflite` di HP, lalu bandingkan keluaran dengan `output.raw_float32_le_base64` dalam toleransi `tolerance.raw_abs = 1e-3`. Lolos berarti binding LiteRT di app memberi makan tensor dengan benar (urutan kanal, layout, tipe) dan kernel HP setara. **Belum dijalankan di HP.**

Fixture dibuat dengan `ml/detection/make_golden.py` dari bundel nyata (memerlukan runtime LiteRT); isinya bukan foto dan bukan hasil klinis. Hasil deteksinya (skor ≈ 0,001) tidak bermakna selain sebagai uji aritmetika. Bundel baru → buat ulang fixture; tes CI (`tests/test_golden.py`) menolak fixture yang tidak konsisten.

## 6. Ambang skor

| Kelas | Ambang (`labels.json → thresholds.score`) | Cara dipilih |
|---|---|---|
| `caries_permanent` | 0,5914 | pada split `val`: ambang tertinggi dengan sensitivitas tingkat gambar ≥ 0,85 |
| `caries_primary` | **0,0096** | idem; nilai ini sangat rendah karena kelas sulung jarang (39 gambar positif di test) |

Status `set_on_val`: **teknis, belum ditinjau dokter gigi**. Baca ambang dari `labels.json`; jangan menyalin angka ke kode. Bundel resmi dapat mengubahnya tanpa mengubah app.

## 7. Yang harus tampil / tidak boleh di UI (ADR 014, 002, 013)

- Teks: **"indikasi karies pada gigi tetap / gigi sulung — perlu diperiksa"** + ajakan ke dokter gigi atau faskes. Bukan diagnosis; tidak ada "Anda menderita".
- **Kotak melingkupi seluruh gigi.** Gambar hanya sebagai penanda; **jangan menyimpulkan keparahan dari ukuran/luas kotak**. Triase hanya membaca kelas, skor, dan **jumlah** temuan.
- Tidak ada angka akurasi di app ("90% akurat" dilarang). Kinerja di bawah hanya untuk keputusan desain, bukan untuk ditampilkan.
- Layar Tentang wajib memuat atribusi CC BY dari `labels.json → attribution` (Indonesia dan English) dan pernyataan: model dilatih pada dataset publik, **belum divalidasi klinis**, hanya satu perangkat (Pakistan, usia 10–24 th).
- Red flag dari kuesioner menang atas hasil model apa pun.

## 8. Kinerja terukur (informasi desain, bukan klaim)

Split uji Ahmed 2025 (940 foto, 96 pasien), ambang `set_on_val`. Dari ADR 013:

| | Sens (gambar) | Spes (gambar) | PPV | AP50 |
|---|---|---|---|---|
| Ada indikasi karies | 0,88 (0,83–0,92) | 0,93 (0,91–0,95) | 0,88 | 0,91 |
| Gigi tetap | 0,85 | 0,96 | 0,92 | 0,91 |
| Gigi sulung | 0,87 (0,68–1,00) | 0,90 | **0,28** | 0,72 |

Implikasi desain: untuk gigi sulung, sekitar 7 dari 10 tanda adalah alarm palsu dan datanya kecil (39 gambar positif) — rancang teks hasil yang berhati-hati dan jangan memberi bobot visual lebih pada temuan sulung dibanding tetap. Dua limitasi data: tidak ada anak Indonesia, tidak ada analisis per-HP/pencahayaan (kolom metadata tidak ada).

## 9. Ukuran, memori, latensi

| Hal | Nilai |
|---|---|
| Berkas | 3,48 MiB (gerbang ADR 001: ≤ 15 MB, lolos) |
| Parameter / komputasi | 0,90 juta / ≈ 1,08 GFLOP pada 416×416 (dari ringkasan model YOLOX) |
| Latensi di HP RAM 3 GB dan 4 GB | **belum diukur** (spike S1; protokol dan format hasil: [`../runbooks/spike-s1-latency.md`](../runbooks/spike-s1-latency.md)). Gerbang: ≤ 1,5 s termasuk praproses, dekode, NMS |

Rencana bila S1 gagal (tanpa mengubah kontrak app bila memungkinkan): (a) float16 — bobot separuh ukuran, masukan/keluaran tetap float32 `[PERLU VERIFIKASI pada model nyata]`; (b) memperbaiki INT8; (c) kandidat cadangan MediaPipe. Lihat ADR 013, "Syarat yang belum terpenuhi".

## 10. Hal yang belum diketahui

- Latensi dan memori di HP (S1).
- Kesetaraan resize di HP: evaluasi di backend memakai PIL `BILINEAR` (mengecilkan dengan anti-aliasing), pelatihan memakai OpenCV `INTER_LINEAR` pada salinan 832 px. Penurunan ukuran foto 12 MP ke 416 dengan filter berbeda dapat menggeser skor; pilih penurunan yang tidak menghasilkan aliasing dan laporkan selisihnya pada uji S1 `[PERLU VERIFIKASI]`.
- Perilaku di pencahayaan senter/HP lain (data satu perangkat).
