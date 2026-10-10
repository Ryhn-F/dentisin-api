# Kontrak artefak untuk app (triase, rekomendasi, model, manifes, quality gate)

> **Ringkasan (ID).** Mesin triase, rekomendasi pasta gigi, dan detektor foto berjalan **di perangkat** (offline-first). Backend hanya menyediakan artefak yang dapat dibaca dokter gigi plus kontrak masukan/keluaran yang diuji bersama. App mengunduh artefak lewat **manifes bertanda tangan Ed25519**, memverifikasinya, lalu memakainya. Semua nilai klinis masih **PLACEHOLDER / draf, belum ditinjau drg; tidak boleh dipakai pada anak**. Keluaran selalu berupa "indikasi" / "perlu diperiksa", bukan diagnosis.

Dokumen ini meringkas; sumber kebenaran ada di file yang ditautkan.

## 1. Cara artefak sampai ke app

| Artefak | Sumber di repo | Lokasi di bucket `manifests` | Pengesah |
|---|---|---|---|
| Aturan triase | `triage/dist/rules.json` | `rules/<v>/rules.json` | `triage/dist/SHA256SUMS`, manifes |
| Bundel rekomendasi | `recommender/dist/recommender.json` (+ `.sha256`) | `recommender/<v>/recommender.json` | `.sha256`, manifes |
| Model detektor | `ml/exports/<semver>/model.tflite` | `model/<v>/model.tflite` | `SHA256`, manifes |
| Label + cara dekode | `ml/exports/<semver>/labels.json` | `model/<v>/labels.json` | `labels.model.sha256`, manifes |
| Manifes | dibuat `tools/sign_manifest.py` | `manifest.json`, `manifest.sig` | tanda tangan Ed25519 |

Belum ada rilis model: `ml/exports/` kosong (hanya `.gitkeep`) dan bundel yang ada masih draf. App membawa **rilis bawaan** (bundled) sebagai lantai.

## 2. Triase (`triage/`)

Sumber kebenaran klinis: `triage/rules.yaml` (dibaca drg). App **tidak** mem-parse YAML: ia memakai `triage/dist/rules.json` (dibuat `uv run python -m triage.export`), memverifikasi SHA-256-nya terhadap `triage/dist/SHA256SUMS` dan manifes, dan mem-port `engine.py` satu-satu ke TypeScript (`resolveParams`, `checkParams`, `evaluate`). Dokumen lengkap: [`../../triage/README.md`](../../triage/README.md). Versi aturan saat ini `0.2.0-draft` (`status: DRAFT`).

Berkas `triage/dist/`:

| Berkas | Fungsi |
|---|---|
| `rules.json` | aturan, level, pertanyaan (9 id gejala), teks alasan/saran, parameter (`PLACEHOLDER`), kontak darurat |
| `input.schema.json` | JSON Schema masukan engine (untuk uji kontrak, mis. ajv) |
| `output.schema.json` | JSON Schema keluaran engine |
| `SHA256SUMS` | `sha256sum` ketiga berkas di atas |

### 2.1 Masukan (`evaluate(rules, input, params)`)

```json
{
  "detections": [ { "class": "caries_primary", "confidence": 0.82 } ],
  "photo_status": "assessed",
  "symptoms": {
    "airway_or_swallowing": "no", "lethargy": "no", "trismus": "no",
    "facial_swelling": "no", "fever_with_toothache": "no", "pus_or_fistula": "no",
    "dental_trauma": "no", "night_pain": "no", "white_spot": "no"
  },
  "age_months": 96,
  "pain": 0
}
```

| Field | Aturan |
|---|---|
| `detections[]` | objek dengan **tepat** `class` (`caries_permanent` \| `caries_primary`) dan `confidence` (0..1). **Kunci lain (mis. `box`) ditolak** (`DETECTION_INVALID`); app menyimpan kotak untuk overlay lalu membuangnya sebelum memanggil engine (ADR 014: keparahan tidak boleh diturunkan dari ukuran kotak) |
| `photo_status` | `assessed` \| `not_taken` \| `quality_failed`; `detections` non-kosong hanya bila `assessed` (`DETECTIONS_WITHOUT_PHOTO`) |
| `symptoms` | **semua 9 kunci wajib**; nilai `yes` \| `no` \| `unknown`. "unknown" tidak pernah dianggap "no" |
| `age_months` | integer 0..300 atau `null` (null -> `CTX_AGE_OUTSIDE_VALIDATED_RANGE`) |
| `pain` | integer 0..10 atau `"unknown"` |

Jawaban yang hilang/tidak sah melempar `TriageInputError` (bukan hasil). Kode: `INPUT_NOT_OBJECT, UNKNOWN_FIELD, MISSING_FIELD, PHOTO_STATUS_INVALID, SYMPTOMS_INVALID, UNKNOWN_SYMPTOM, MISSING_SYMPTOM, INVALID_ANSWER, AGE_INVALID, PAIN_INVALID, DETECTIONS_INVALID, DETECTION_INVALID, UNKNOWN_CLASS, CONFIDENCE_INVALID, DETECTIONS_WITHOUT_PHOTO`; parameter: `PARAM_UNKNOWN, PARAM_MISSING, PARAM_INVALID`. Pada galat, app **wajib** menampilkan layar rujukan ("hubungi puskesmas/dokter gigi") dan tidak boleh menampilkan level lebih rendah.

`params = resolveParams(rules)` tanpa override di produksi: parameter yang `null` diganti `if_unset` yang lebih waspada (mis. satu temuan sudah dianggap "banyak" selama N belum ditetapkan drg).

### 2.2 Keluaran (contoh nyata dari `engine.py` + `dist/rules.json`)

```json
{
  "level": "urgent",
  "reasons": [
    { "id": "DET_CARIES_PRIMARY", "text": "Ada indikasi karies pada gigi sulung di foto. Perlu diperiksa dokter gigi." },
    { "id": "DET_CARIES_MANY", "text": "Ada indikasi karies pada beberapa gigi di foto. Perlu diperiksa dokter gigi segera." },
    { "id": "CTX_AGE_OUTSIDE_VALIDATED_RANGE", "text": "Pemeriksaan foto belum teruji untuk usia anak ini, atau usia belum diisi. Perlu diperiksa langsung oleh dokter gigi." }
  ],
  "lesions": [ { "detection_index": 0, "class": "caries_primary", "dentition": "primary", "confidence": 0.82, "counted": true } ],
  "advice_ids": [ "ADV_DENTIST_SOON", "ADV_PHOTO_LIMITATION", "ADV_DISCLAIMER_NOT_DIAGNOSIS", "ADV_REFERRAL_CTA" ],
  "referral_template_id": "REF_URGENT",
  "rules_version": "0.2.0-draft"
}
```

- `level`: `emergency` \| `urgent` \| `scheduled` \| `routine` (merah/oranye/kuning/hijau). Level tertinggi dari semua aturan yang terpenuhi; **red flag selalu menang** dan berasal dari kuesioner gejala, bukan model.
- `advice_ids` selalu memuat `ADV_DISCLAIMER_NOT_DIAGNOSIS` dan `ADV_REFERRAL_CTA`; teks saran dibaca dari `rules.json` (`advice`), tidak ditulis di kode.
- `lesions[]` tidak memuat ukuran/area; `dentition` hanya untuk label UI ("indikasi pada gigi sulung / gigi tetap").
- Pemetaan ke server (`screening_recorded.level`, `referral_created.level`): `emergency` -> `red`, `urgent` -> `orange`, `scheduled` -> `yellow`, `routine` -> `green`.

### 2.3 Prosedur paritas (`tests/cases.json`)

1. Salin/rujuk `triage/dist/rules.json`, `triage/tests/cases.json` (`format_version: 2`, 114 kasus: 90 hasil + 24 galat bertipe), dan `triage/tests/fixtures/params_profiles.json` (profil `test`, `test_alt`, `unset`; **khusus uji**, bukan nilai klinis, jangan masuk bundel app).
2. Jalankan Jest di `app/__tests__/triage.parity.test.ts` (contoh lengkap di `triage/README.md`): untuk tiap kasus, buang `params_profile` dari `input`, panggil `evaluate(rules, input, resolveParams(rules, fixtures.profiles[profile].values))`.
3. Jika `expected.error`, harapkan lemparan dengan `code === expected.error_code`. Jika tidak, bandingkan `level`, `reasons_ids`, `advice_ids`, `referral_template_id` (urutan **diperhitungkan**), dan bila ada `lesions` (`detection_index`, `dentition`, `counted`).
4. Target: seluruh 114 kasus lulus dengan cakupan garis dan cabang penuh pada engine TS.
5. Setiap perubahan `rules.yaml`: `uv run python -m triage.export`, lalu perbarui bundel app dan tanda tangan manifes. `release_check` sengaja **gagal** sampai drg mengesahkan (status bukan `APPROVED`, parameter `null`, `reviewed_by = PENDING_DRG`, 0 dari >= 30 kasus `graded_by_drg`).

## 3. Rekomendasi pasta gigi (`recommender/`)

Dokumen lengkap: [`../../recommender/README.md`](../../recommender/README.md). v1 hanya fluoride (kadar ppm, jumlah pakai, pendampingan per usia); merek hanya di `products.json` dan netral. Status: semua baris `needs_review`/`draft`, `release_ready: false` (`release_issue_count` 126 pada bundel saat ini).

Bundel untuk app: `recommender/dist/recommender.json` (dibuat `uv run python -m recommender.build_bundle`; `recommender.json.sha256` = SHA-256 berkas itu).

```
{ bundle_format: 1, rules_version, rules_sha256, products_version, products_sha256,
  release_ready, release_issue_count, rules: {...}, products: [...] }
```

Port `recommend` ke TypeScript (`app/src/domain`) membaca `bundle.rules` dan `bundle.products`.

Masukan: `recommend(age_months, allergies = [], prefs = null, risk_level = null)`

| Argumen | Aturan |
|---|---|
| `age_months` | integer 0..216 (bukan boolean/pecahan/string); selain itu `InvalidAgeError` (`code: "invalid_age"`) |
| `allergies` | kode dari `rules.filters.allergen_codes`: `flavor_mint`, `cinnamon`, `fruit_flavor`, `propylene_glycol`, `sls`; di-trim, huruf kecil, unik; kode tak dikenal menambah alasan `unknown_allergy:<kode>` dan catatan `allergy_unverified`. Pakai kode yang sama untuk `child_upserted.allergies` (server menerima pola `^[a-z][a-z0-9_]{1,39}$`) |
| `prefs` | `{ halal?: boolean, no_sls?: boolean }` (alergi `sls` memaksa `no_sls`) |
| `risk_level` | `low` \| `moderate` \| `high` \| `null`; nilai lain -> `InvalidInputError` (`code: "invalid_input"`) |

Keluaran (ringkas; `source` dan teks panjang dipotong):

```json
{
  "fluoride": {
    "band_id": "school_84_216m",
    "ppm_options": [ { "candidate_id": "guideline_ge1000", "label": "Anjuran pedoman: 1000-1450 ppm", "min": 1000, "max": 1450, "source": "..." } ],
    "decision_pending": true,
    "amount": { "code": "pea_size", "text": "Pakai pasta gigi sebesar kacang polong." },
    "supervision": { "required": false, "until_age_months": 84, "text": null },
    "notes": [ { "id": "look_for_ppm_on_pack", "text": "..." }, { "id": "spit_dont_swallow", "text": "..." }, { "id": "rinse_little", "text": "..." } ]
  },
  "disclaimer_ids": [ "toothpaste_not_filling", "see_dentist_cta" ],
  "disclaimers": [ { "id": "toothpaste_not_filling", "text": "Pasta gigi membantu, tetapi tidak menambal gigi berlubang." }, { "id": "see_dentist_cta", "text": "..." } ],
  "products": [],
  "notes": [ { "id": "fewer_than_two_products", "text": "..." } ],
  "status": { "needs_review": true, "reasons": [ "ppm_decision_pending", "..." ],
              "generic_message": { "id": "generic_conservative", "text": "..." } },
  "rules_version": "0.1.0-draft"
}
```

Aturan tampilan: bila `status.needs_review` true (selalu, saat ini), UI menampilkan `status.generic_message` secara konservatif dan **tidak** menyajikan ppm sebagai anjuran klinis. Setiap hasil non-galat memuat `toothpaste_not_filling` di `disclaimer_ids`. `products` diurutkan menurut `id` (perbandingan code-unit); catatan menurut urutan aturan; `reasons` terurut. Produk saat ini placeholder sintetis (`verified: false`).

Paritas: `recommender/tests/cases.json` (`format_version: 1`, 50 kasus; himpunan produk `product_sets`: `fixture`, `none`, `single`, `too_strong`, `shipped` = `bundle.products`). Per kasus: input = `{...input_defaults, ...case.input}`; `expect.error` -> harapkan lemparan dengan `code` itu; selain itu cocokkan hanya kunci yang ada di `expect` (semantik di `expect_keys` dalam berkas). Jangan menambah kasus JSON bernilai `12.0` (Python dan JS membacanya berbeda).

## 4. Model detektor: `labels.json` v2 dan dekode

Kontrak: [`../../ml/detection/DECODE_SPEC.md`](../../ml/detection/DECODE_SPEC.md) (versi 1.0.0), skema [`../../ml/detection/labels.schema.json`](../../ml/detection/labels.schema.json) (`schema_version: "2.0.0"`), implementasi acuan `decode.py`. Bundel `ml/exports/<semver>/`: `model.tflite`, `labels.json`, `MODEL_CARD.md`, `SHA256`.

Hal yang mengikat (ADR 014):

- Kelas **hanya** `caries_permanent` (id 0, `dentition: permanent`, label dataset **D**) dan `caries_primary` (id 1, `dentition: primary`, label **d**). Tidak ada kelas berlubang/belum berlubang. Teks UI: "indikasi karies pada gigi tetap/sulung, perlu diperiksa".
- `box_use = "ui_overlay_only"`: kotak melingkupi **seluruh gigi**; digambar sebagai overlay saja. **Jangan pernah menurunkan keparahan dari ukuran/luas kotak.** Triase hanya membaca kelas, skor, dan **jumlah** temuan.
- `intended_use = "screening_only"`. Skor ambang (`thresholds.score.*`) `null` selama `status: PLACEHOLDER`.

Struktur ringkas (contoh ini sah menurut `labels.schema.json`; nilai hanya ilustrasi, bukan hasil model nyata):

```json
{
  "schema_version": "2.0.0",
  "model": { "name": "dentisai-caries-detector", "version": "0.1.0", "candidate": "yolox_nano", "variant": "int8",
             "created_at": "2026-10-11T00:00:00Z", "sha256": "<sha256 model.tflite>", "size_bytes": 1234567, "int8": true },
  "validation_status": "NOT_VALIDATED",
  "intended_use": "screening_only",
  "distribution": "internal_experiment_only",
  "box_use": "ui_overlay_only",
  "classes": [
    { "id": 0, "name": "caries_permanent", "dentition": "permanent", "may_raise_triage_level": true, "ui_label_key": "finding.caries_permanent" },
    { "id": 1, "name": "caries_primary",   "dentition": "primary",   "may_raise_triage_level": true, "ui_label_key": "finding.caries_primary" }
  ],
  "input": { "width": 416, "height": 416, "channels": 3, "layout": "NHWC", "color_order": "BGR", "dtype": "float32",
             "normalization": { "pixel_range": [0, 255], "mean": [0, 0, 0], "std": [1, 1, 1], "formula": "x = (v - mean) / std" },
             "quantization": null,
             "resize": { "method": "letterbox", "align": "top_left", "pad_value": 114, "interpolation": "bilinear" } },
  "output": { "format": "yolox_raw",
              "tensors": [ { "index": 0, "name": null, "role": "raw", "shape": [1, 3549, 7], "dtype": "float32", "quantization": null } ],
              "tensors_verified": false,
              "decode": { "type": "yolox_raw", "strides": [8, 16, 32], "sigmoid_applied": false,
                          "row_order": "stride_major_then_y_then_x",
                          "box_formula": "cx=(tx+gx)*s, cy=(ty+gy)*s, w=exp(tw)*s, h=exp(th)*s", "score_formula": "obj*cls" },
              "postprocess": { "nms_iou_threshold": 0.45, "class_agnostic_nms": false, "max_detections": 20, "status": "technical_default" } },
  "thresholds": { "score": { "caries_permanent": null, "caries_primary": null }, "status": "PLACEHOLDER", "source": null },
  "licenses": { "code": { "license": "Apache-2.0", "verified": false, "basis": "unverified", "source": null },
                "weights": { "license": null, "verified": false, "basis": "unverified", "source": null },
                "datasets": [ { "id": "ahmed_2025", "license": "CC-BY-4.0" } ], "model_artifact": "see MODEL_CARD.md" },
  "attribution": [ { "lang": "id", "text": "Atribusi dataset sesuai lisensi." } ],
  "decode_spec": { "document": "ml/detection/DECODE_SPEC.md", "version": "1.0.0", "reference_impl": "ml/detection/decode.py" }
}
```

Langkah app (ringkas; rinci di DECODE_SPEC):

1. Sebelum memakai: verifikasi manifes, lalu `SHA256` semua berkas bundel, lalu `labels.model.sha256 == sha256(model.tflite)`.
2. **Tolak** menampilkan hasil bila `distribution == "internal_experiment_only"` pada build pengguna, atau bila `thresholds.score.*` `null` di luar build internal/dev. Rilis (`distribution: release`) mensyaratkan `validation_status: VALIDATED`, ambang `reviewed_by_drg`, lisensi terverifikasi, dan `tensors_verified: true`.
3. Praproses: resize (`letterbox`/`stretch`, `align`, `pad_value`), urutan kanal, normalisasi `x = (lo + p*(hi-lo)/255 - mean)/std`, kuantisasi bila dtype `int8`/`uint8`; layout NHWC, batch 1.
4. Dequantisasi keluaran (`real = scale * (q - zero_point)`), identifikasi tensor lewat `role`.
5. Dekode menurut `output.decode.type` (`yolox_raw`, `ssd_raw_anchors`, `ssd_postprocessed`, `detr_raw`), saring skor per kelas, NMS (per kelas atau agnostik), urut skor, batasi `max_detections`.
6. Petakan kembali ke foto (`letterbox`: `(x - pad)/r`; `stretch`: skala) dan klem ke batas gambar.
7. Kirim `{class, confidence}` ke engine triase; simpan `box` hanya untuk overlay dan untuk `findings[].box` (ternormalisasi `[x, y, w, h]`) pada event sinkron.

Vektor paritas dekode: `ml/detection/tests/decode_vectors.json` (`spec_version 1.0.0`; toleransi absolut 1e-6; bagian `vectors`, `geometry`, `quantization`, `postprocess`).

Item terbuka `[PERLU VERIFIKASI]` (spike S1): bentuk tensor keluaran nyata untuk tiap kandidat, paritas INT8 model sebenarnya, ambang skor (belum ada, menunggu data validasi dan drg).

## 5. Manifes bertanda tangan

Kontrak normatif (format, kanonikalisasi DCJ-1, kunci, algoritma verifikasi 13 langkah, kode galat, vektor uji): **[`../../tools/manifest/SPEC.md`](../../tools/manifest/SPEC.md)**; prosedur publikasi: [`../../tools/manifest/publish.md`](../../tools/manifest/publish.md). Tidak diduplikasi di sini. Ringkasan alur untuk app:

1. Unduh `manifest.json` (<= 16 384 B) dan `manifest.sig` (<= 512 B) dari `https://<project-ref>.supabase.co/storage/v1/object/public/manifests/` (hanya https).
2. Verifikasi **byte mentah** dengan Ed25519 (strict RFC 8032, mis. `@noble/ed25519` `zip215: false`) memakai kunci tepercaya yang di-embed (dua kunci: K1 utama, K2 cadangan; `key_id` = 8 byte pertama SHA-256 kunci publik, hex). Kunci uji (`965ba6b27d5ca7a7`, `e12ef5f599e59801`) ditolak di build produksi.
3. Periksa kanonikalisasi, skema tertutup, `signer_key_id`, `channel` (`production` di build produksi), tidak ada `PLACEHOLDER`, `expires_at`, anti-rollback (`sequence` >= terakhir; sama + hash beda ditolak), `min_app_version`.
4. Unduh artefak (model, labels, rules, recommender) ke staging; cek ukuran dan SHA-256 masing-masing.
5. Hanya bila **semua** lolos: tukar penunjuk "rilis aktif" secara atomik dan simpan `{sequence, hash manifes, schema_version, bytes manifes dan sig}`. Gagal di mana pun: pertahankan rilis terakhir yang baik (atau rilis bawaan), catat hanya kode galat, ulangi nanti dengan backoff. Jangan pernah menerapkan sebagian.

Selama `channel = internal-test` atau artefak masih draf, manifes produksi tidak dapat ditandatangani (penanda tangan menolak `PLACEHOLDER`, `PENDING_DRG`, `needs_review`, `verified: false`). Verifikasi lokal untuk uji paritas: `tools/verify_manifest.py` dan vektor di `tools/manifest/vectors/`.

## 6. Quality gate foto

Implementasi acuan Python: [`../../ml/detection/quality_gate.py`](../../ml/detection/quality_gate.py); konfigurasi: [`../../ml/detection/quality_gate.yaml`](../../ml/detection/quality_gate.yaml) (versi 1, **`status: PLACEHOLDER`**: seluruh ambang tebakan teknis sampai ada >= 200 foto berlabel; build rilis harus menolak status ini). App mem-port-nya.

Alur (integer sedapat mungkin, deterministik):

1. Citra kerja: `k = max(1, ceil(max(H, W) / 320))`; potong sisa kanan/bawah agar kelipatan `k`; rata-rata blok per kanal dengan pembulatan integer `(sum + k*k // 2) // (k*k)`.
2. Luma `Y = (77 R + 150 G + 29 B + 128) >> 8`.
3. Blur: varians Laplacian 3x3 `[[0,1,0],[1,-4,1],[0,1,0]]` pada area valid (tanpa padding; 0.0 bila < 3x3).
4. Kecerahan: rata-rata luma, fraksi piksel `Y <= dark_level` dan `Y >= bright_level`.
5. Gigi terlihat: **heuristik** warna pada ROI tengah (bukan detektor gigi); dapat diganti estimator terlatih.

Keluaran: `{ passed, reasons[], metrics{}, config_status }` (`config_status` = `PLACEHOLDER` selama ambang belum ditetapkan), dengan kode alasan stabil dan berurutan: `too_dark`, `too_bright`, `blurry`, `teeth_not_visible` (app memetakan ke pesan Indonesia). Gagal -> `photo_status = quality_failed` di masukan triase (tidak ada deteksi).

Vektor paritas: `ml/detection/tests/quality_gate_vectors.json` (versi 1, 12 vektor; toleransi abs/rel 1e-9). Tiap vektor: `id`, `gen` (generator parametris berbasis integer: `uniform`, `checker`, `hgradient`, `mouth`, `mouth_soft`, opsional `blur`), `expected.passed`, `expected.reasons`, `expected.metrics` (`laplacian_var`, `mean_luma`, `dark_fraction`, `bright_fraction`, `tooth_fraction`, `tissue_fraction`, ...). Konfigurasi yang dipakai disimpan di dalam berkas vektor agar perubahan ambang tidak mengubah vektor secara diam-diam. Regenerasi: `python quality_gate_vectors.py --write` (dari `ml/detection/`).
