# ADR 013 — Hasil bake-off detektor karies (permisif) & model v1

<!-- Template untuk IMPLEMENTATION_PLAN item 2.7 -> ADR 013 (nomor ditetapkan koordinator; 011/012 sudah terpakai).
     Penanda komentar "compare:inputs/table/licenses/notes" diisi otomatis oleh:
       uv run python bake_off/compare.py --adr --report <cand>=<report_int8_test.json> ... \
         --meta bake_off/measurements.yaml --out bake_off/ADR-draft.md
     Bagian lain diisi tangan. Salin hasil ke ../docs/decisions/ (oleh pemilik ADR, bukan oleh agen ML). -->

- **Status:** Proposed — `YYYY-MM-DD`
- **Tanggal:** `YYYY-MM-DD`
- **Terkait:** ADR 001 (model permisif), ADR 003 (lingkup MVP, bounding box), ADR 008 (repo tertutup, Rp0),
  **ADR 014 (kelas menurut jenis gigi; ukuran kotak bukan keparahan; risiko lisensi bobot YOLOX diterima pemilik)**, PRD §3.3 / §12,
  `backend/ml/detection/WEIGHT_LICENSES.md`, `backend/ml/detection/bake_off/README.md`
- **Penyusun:** `…` · **Peninjau:** `…` (drg: `…` — wajib untuk ambang skor)

> Evaluasi teknis pada data publik — **bukan validasi klinis, bukan klaim akurasi** (PRD §12, ADR 002). Semua bundel
> `validation_status: NOT_VALIDATED`; ambang skor `set_on_val` sampai ditinjau drg.

## Konteks
ADR 001 mewajibkan bake-off ≥ 2 detektor berlisensi permisif (kode **dan** bobot) pada data & split yang sama, dengan gerbang
INT8 ≤ 15 MB dan latensi ≤ 1,5 s di HP RAM 3–4 GB (spike S1). Status lisensi bobot per kandidat: `WEIGHT_LICENSES.md`.

## Data & protokol
<!-- compare:inputs -->

- Kelas (ADR 014): `caries_permanent` (label Ahmed "D", indikasi karies pada gigi tetap) dan `caries_primary` ("d", gigi sulung);
  metrik utama = **"ada indikasi karies pada gigi"** di tingkat gambar (`any_caries`) + per kelas. Tidak ada metrik keparahan;
  kotak = seluruh gigi, hanya untuk overlay UI. Peta kelas: `ml/data/class_maps/ahmed2025.yaml` status `…`.
- Prediksi dibuat dari **model INT8 yang akan dikirim** dengan pra-proses & dekoder referensi (`predict.py`, `DECODE_SPEC.md`).
- Ambang dipilih pada `val` (`thresholds.py`, aturan: ambang tertinggi dengan sens gambar ≥ target; fallback Youden J), metrik akhir pada `test` sekali.

## Hasil utama (test, INT8)
<!-- compare:table -->

## Lisensi (ringkas dari WEIGHT_LICENSES.md)
<!-- compare:licenses -->

## Subgrup (`report.json → subgroups`)
| Kandidat | Subgrup | n gambar / n pasien | Sens karies apa pun (CI95) | Spes karies apa pun (CI95) |
|---|---|---|---|---|
| | lighting = … | | | |
| | phone_model = … | | | |

## Perangkat uji (spike S1)
| Slot | Model HP | RAM | SoC | Android | Runtime/delegate | Median latensi (ms, ≥ 30 run) |
|---|---|---|---|---|---|---|
| ram_3gb | | 3 GB | | | LiteRT CPU/XNNPACK | |
| ram_4gb | | 4 GB | | | LiteRT CPU/XNNPACK | |

## Keputusan
- Model terpilih: `…` (versi bundel `…`, sha256 `…`) · alasan: …
- Kandidat cadangan: `…`
- Yang **tidak** diputuskan di sini: ambang klinis (drg), klaim akurasi (butuh validasi lapangan, G3).

## Konsekuensi
- `ml/exports/<semver>/` dibuat dari run terpilih; `labels.json` + `DECODE_SPEC.md` menjadi kontrak app (📱 mem-port dekoder & uji paritas `decode_vectors.json`).
- MODEL_CARD wajib memuat keterbatasan: satu perangkat, usia 10–24 th (Pakistan), tanpa data anak Indonesia, kotak = seluruh gigi (bukan keparahan), tanpa pembeda berlubang/ belum berlubang.
- Risiko tersisa: lisensi bobot (lihat tabel; `owner_accepted_risk` ≠ terverifikasi), kesetaraan INT8, generalisasi perangkat/ pencahayaan senter, split pasien heuristik (`group_quality`).

<!-- compare:notes -->
