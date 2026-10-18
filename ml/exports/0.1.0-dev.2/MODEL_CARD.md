# MODEL CARD — dentisai-caries-detector 0.1.0-dev.2

> **⚠ NOT VALIDATED — BELUM DIVALIDASI.** Model ini belum divalidasi secara klinis pada anak Indonesia.
> Hasil hanya **indikasi** untuk skrining, **bukan diagnosis**; selalu rujuk ke dokter gigi/ puskesmas.
> Distribusi: `pilot` · Status validasi: `NOT_VALIDATED`
> **⚠ LISENSI BOBOT PRA-LATIH TIDAK TERVERIFIKASI — `owner_accepted_risk`.** Pemilik produk menerima risiko lisensi
> implisit (Apache-2.0 tersirat) untuk kompetisi (ADR 014 §5). Wajib ditinjau ulang sebelum rilis komersial/ publik.

## Ringkasan (Bahasa Indonesia)
- **Fungsi:** menandai **indikasi karies pada gigi sulung/ tetap** (`caries_primary` / `caries_permanent`) pada foto gigi depan — hasilnya "perlu diperiksa", bukan diagnosis.
- **Kotak** melingkupi seluruh gigi yang tampak berlubang/ bernoda; **ukuran kotak tidak menunjukkan keparahan** dan hanya dipakai untuk penanda di layar (ADR 014). Model tidak membedakan berlubang vs belum berlubang.
- **Bukan untuk:** diagnosis, menilai keparahan, sela-sela gigi, bagian dalam gigi, gigi belakang yang tidak terlihat.
- **Data latih:** dataset publik (lihat Lisensi); **belum ada data anak Indonesia**.
- **Ambang skor:** `set_on_val`

## Model details (English)
- Candidate: `yolox_nano` (variant `yolox_nano`), INT8: False, size: 3.48 MiB
- model.tflite sha256: `3aa743f2325ce773b11a8ddf5e8e9a10e816ce0ff2a9946fb7ab666917d8bfb6`
- Training run: `20261017T110527Z_yolox_nano_137a1889` · Eval report sha256: `bfff52db8b712683e1a5fbade93d24882dce335a4c15e5737fdb4351986cef65`
- Created: 2026-10-17T12:52:14.251902+00:00
- Input: 416x416 BGR float32, resize `letterbox`
- Output: `yolox_raw` (tensors verified by interpreter: True); decode per `ml/detection/DECODE_SPEC.md` v1.0.0

## Intended use
On-device screening aid for school-age children's front teeth photos taken with a flashlight, used by parents, teachers (UKS) and health cadres. Output must be phrased as 'indikasi / perlu diperiksa' with a referral CTA. Red flags come from the symptom questionnaire, never from this model.

## Evaluation
Internal technical evaluation on a public-data split — **not** a clinical validation, not a claim:

```json
{
 "split": "test",
 "split_sha256": "61b59b7b2a8a98908afe18b0419f457ccd0ac3dda47f3a53d28a570bab1fb625",
 "map50": 0.815139511917745,
 "caries_permanent_image_level": {
  "sensitivity": 0.8520900321543409,
  "specificity": 0.9618441971383148
 },
 "caries_primary_image_level": {
  "sensitivity": 0.8717948717948718,
  "specificity": 0.902330743618202
 },
 "any_caries_image_level": {
  "sensitivity": 0.8795180722891566,
  "specificity": 0.9325657894736842
 },
 "ap50": {
  "caries_permanent": 0.9137712647184048,
  "caries_primary": 0.7165077591170853
 }
}
```

## INT8 parity
{
 "n_samples": 50,
 "tensors": [
  {
   "index": 0,
   "max_abs_err": 0.0,
   "mean_abs_err": 0.0,
   "cosine": 0.9999999999999998
  }
 ],
 "detection_agreement": 1.0,
 "mean_score_abs_diff": 0.0,
 "n_float_detections": 54,
 "min_cosine": 0.98,
 "min_agreement": 0.9,
 "passed": true,
 "failures": []
}

## Known limitations
- Training data from a single smartphone model, ages 10-24, Sindh (Pakistan); no Indonesian pediatric data.
- Boxes surround whole teeth with visible decay (Ahmed labels D/d); box size says nothing about severity and is used only for the UI overlay -- triage uses class, confidence and the number of findings (ADR 014).
- No cavitated vs non-cavitated distinction exists in the training data; early (non-cavitated) lesions are unreliable from smartphone photos (literature sensitivity 0-38%).
- Primary-dentition children under 10 are outside the training age range.
- Photos of front teeth only; interproximal and posterior surfaces are not assessed.
- Thresholds and quality-gate limits are PLACEHOLDER until set on validation data and reviewed by a dentist.

## Licenses
- Code: Apache-2.0 (verified: True; https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE (read 2026-10-08; 'Copyright (c) 2021-2022 Megvii Inc.'))
- Pretrained weights: None (verified: False; basis: `owner_accepted_risk`; yolox_nano.pth = release asset (0.1.1rc0) of the Apache-2.0 repo, no separate terms; maintainer question https://github.com/Megvii-BaseDetection/YOLOX/issues/1865 (2026-03-31) open with 0 comments (re-checked 2026-10-10))
- Dataset `ahmed2025`: CC-BY-4.0 (10.5281/zenodo.14827784)
- This model: Proprietary DentisAI model (closed repo, ADR 008); third-party terms above apply.

## Attribution (show on the About screen, FR-122)
- [id] Model ini dilatih memakai data dari "Annotated intraoral image dataset for dental caries detection" (v1, 2025) oleh S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal dan F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, lisensi CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Perubahan: label dipetakan ulang ke kelas DentisAI dan ukuran gambar diubah.
- [en] Contains data from "Annotated intraoral image dataset for dental caries detection" (v1, 2025) by S. M. F. Ahmed, H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, doi:10.5281/zenodo.14827784, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Changes: annotations re-mapped to DentisAI classes, images resized; the dataset was used to train a model.
