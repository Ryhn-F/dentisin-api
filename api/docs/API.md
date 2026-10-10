# DentisAI ML/AI API — Screening pipeline (`/v1/screening`)

> **Screening, not diagnosis.** Every result is an *indication that needs checking by a dentist*.
> The shipped triage and toothpaste rules are **DRAFTS** (`rules_status: "draft"`): no dentist has
> signed them off, so **no client may present this output as clinically validated**.
> The module is **off by default** (`ENABLE_SCREENING_API=false` → every route answers 404).
> Not deployed on the MVP; the app's critical path runs fully on-device (ADR 002, 005, 008, 009, 014).

Other endpoints of this service (review queue, model registry, `/v1/ml/infer`) are described in
[`../README.md`](../README.md); Supabase (auth, CRUD, `sync_events`) in
[`../../docs/api/`](../../docs/api/) when present. Machine-readable contract:
[`../openapi.json`](../openapi.json).

## Ringkasan (Bahasa Indonesia)

| Endpoint | Fungsi | Saklar | Peran | Menyimpan data? |
|---|---|---|---|---|
| `POST /v1/screening/evaluate` | Triase + rekomendasi pasta gigi dari jawaban kuesioner, nyeri, usia, dan **temuan model di HP** (`{class, confidence}` saja, tanpa kotak) | `ENABLE_SCREENING_API` | `guardian`, `kader`, `admin` | Tidak |
| `POST /v1/screening/analyze` | Pipeline QA/*second opinion* atas **foto dataset berizin** (hanya `image_upload.id`): tanda bahaya → consent → foto → quality gate → detektor → triase → rekomendasi | `ENABLE_SCREENING_API` **dan** `ENABLE_CLOUD_INFER` | `admin` | Tidak (foto hanya di memori; akses foto dicatat di `audit_log`) |
| `GET /v1/screening/rules` | Metadata: versi aturan, `rules_status`, kelas, daftar parameter + `placeholder: true/false` | `ENABLE_SCREENING_API` | semua peran aplikasi | Tidak |
| `GET /v1/screening/schema/input` · `/schema/output` | JSON Schema masukan/keluaran mesin triase (dari `triage/dist`) untuk codegen | `ENABLE_SCREENING_API` | semua peran aplikasi | Tidak |

Aturan penting untuk klien:
1. **Tanda bahaya selalu menang** dan berasal dari kuesioner, bukan model. Di `/analyze`, bila gejala saja sudah 🔴, foto **tidak diambil** sama sekali.
2. **Kotak tidak pernah masuk triase** (ADR 014). `/evaluate` menolak kunci `box` (`DETECTION_INVALID`). Kotak hanya muncul di `overlay` pada `/analyze`, khusus untuk gambar di layar.
3. **Error tidak pernah membawa level.** Status 422/409/503 dari `/v1/screening` berisi `fail_safe.action = "show_referral_screen"`: tampilkan layar rujukan ("periksakan ke dokter gigi/puskesmas"), jangan level yang lebih rendah.
4. Selama `rules_status != "approved"` atau `clinically_validated = false`: tampilkan teks `RULES_NOT_VALIDATED` dan pesan konservatif; jangan menampilkan angka ppm sebagai saran klinis.
5. API ini **stateless**: hasil tidak disimpan. Perangkat menyinkronkan hasilnya sendiri lewat RPC Supabase `sync_events`.

---

## 1. Base URLs (placeholders only — nothing is deployed)

| Environment | Base URL |
|---|---|
| Local dev | `http://127.0.0.1:8000` (`uv run --env-file ../.env uvicorn app.main:app --no-access-log`) |
| Docker (local) | `http://127.0.0.1:8000` (see §11) |
| Hosted (future, Azure Container Apps Japan) | `https://<api-host>.example` — **not provisioned** |

## 2. Authentication and roles

* `Authorization: Bearer <Supabase access token>` on every `/v1/screening` route. The API only
  **verifies** Supabase JWTs (JWKS ES256/RS256/EdDSA and/or legacy HS256 secret; `exp`, `aud=authenticated`,
  `iss=<SUPABASE_URL>/auth/v1`, UUID `sub`, `role=authenticated`). It has no login of its own.
* Roles come from `public.user_role` (default, `API_ROLE_SOURCE=table`) or `app_metadata` — never
  `user_metadata`. Anonymous Supabase users (`is_anonymous: true`) are always 403.

| Route | Roles allowed |
|---|---|
| `POST /evaluate` | `guardian`, `kader`, `admin` |
| `POST /analyze` | `admin` (dentist reviewers use admin accounts; there is no `reviewer` role) |
| `GET /rules`, `GET /schema/*` | any app role: `guardian`, `kader`, `facility_viewer`, `admin` |

## 3. Feature switches

| Setting | Default | Effect |
|---|---|---|
| `ENABLE_SCREENING_API` | `false` | Off → **404 `not_found` before authentication** for every `/v1/screening` route (indistinguishable from a missing route). |
| `ENABLE_CLOUD_INFER` | `false` | Also required by `/analyze` (it moves a consented photo into the API's memory). |
| feature flag `dataset_contribution` (Supabase `feature_flag` table) | off | `/analyze` → 409 `dataset_contribution_disabled` until KEPK approval. |

## 4. The "draft rules" contract (read this before building a client)

Every **200** response carries:

| Field | Meaning |
|---|---|
| `rules_version.triage`, `rules_version.recommender` | Exact versions of `triage/dist/rules.json` and `recommender/rules.json` used. |
| `rules_status` | `draft` \| `needs_review` \| `approved`. `approved` only when **both** `python -m triage.release_check` and `python -m recommender.release_check` pass (computed at start-up by calling the engines' own gates; if a gate cannot run it counts as failed). Otherwise `draft` if triage `status` is `DRAFT` or any recommender row is `draft`, else `needs_review`. **Today: `draft`.** |
| `clinically_validated` | `true` only if `rules_status == "approved"` (and, for `/analyze`, the quality-gate config is no longer `PLACEHOLDER`). **Today: always `false`.** |
| `disclaimer_ids` / `disclaimers` | Always `ADV_DISCLAIMER_NOT_DIAGNOSIS`; the recommender's mandatory ids (`toothpaste_not_filling`, `see_dentist_cta`) when a recommendation is present; `RULES_NOT_VALIDATED` (text = the triage rules' own `notice`) while not approved. Texts come from the rule files, never from API code. |
| `referral` | Always present: `{required: true, cta_id: "ADV_REFERRAL_CTA", cta_text, template_id}` (`template_id` = referral letter template of the final level). |
| `recommendation_status` | `ok`, `skipped_age_unknown` (age null) or `skipped_age_out_of_range` (outside the recommender's 0–216 months). Triage still runs. |

Client obligations: show the disclaimer and the referral CTA on every result; while
`clinically_validated` is false show the `RULES_NOT_VALIDATED` text and, for toothpaste, the
recommender's `status.generic_message` instead of ppm values; never cache a result as "validated".

**Errors (fail-safe).** A screening error never contains a level. 422/409/503 bodies from
`/v1/screening` add:

```json
"fail_safe": {"action": "show_referral_screen", "referral_cta_id": "ADV_REFERRAL_CTA",
              "disclaimer_ids": ["ADV_DISCLAIMER_NOT_DIAGNOSIS"], "rules_status": "draft"}
```

The app must then show the safe referral screen ("hubungi/periksakan ke puskesmas atau dokter gigi"),
**never a lower level**, and never retry with altered answers (e.g. turning "unknown" into "no").

## 5. `POST /v1/screening/evaluate`

Stateless re-computation of what the device does offline: triage (`triage.engine.evaluate`, the
Python reference) + toothpaste recommendation (`recommender.recommend`). Useful for QA, parity
checks of the TypeScript port and server-side second opinions. Same rules and params as the app
(`resolve_params(rules)` without overrides).

**Request** = the triage engine input (mirrors `triage/dist/input.schema.json` exactly; a test fails
on drift) + optional `preferences`:

| Field | Type | Notes |
|---|---|---|
| `detections` | array (≤ 100) of `{class, confidence}` | `class` ∈ `caries_permanent` (label D, permanent teeth), `caries_primary` (label d, primary teeth); `confidence` 0..1. **Exactly these two keys** — `box` or coordinates → `DETECTION_INVALID`. Empty unless `photo_status = assessed`. |
| `photo_status` | `assessed` \| `not_taken` \| `quality_failed` | Photo passed the on-device quality gate and ran through the model, or not. |
| `symptoms` | object, all 9 questions required | `airway_or_swallowing, lethargy, trismus, facial_swelling, fever_with_toothache, pus_or_fistula, dental_trauma, night_pain, white_spot`; each `yes` \| `no` \| `unknown`. "unknown" is never treated as "no". |
| `age_months` | integer 0..300 or `null` | Integral floats (`96.0`) accepted like the engine; booleans/strings rejected. |
| `pain` | integer 0..10 or `"unknown"` | |
| `preferences` | optional `{allergies: [≤ 10 codes, ≤ 64 chars], halal: bool, no_sls: bool}` | Recommender filters (FR-52). Unknown allergy codes are kept and reported. |

Example (generated, see §12):

<!-- example:evaluate_request -->
```json
{
  "detections": [
    {
      "class": "caries_primary",
      "confidence": 0.81
    },
    {
      "class": "caries_permanent",
      "confidence": 0.34
    }
  ],
  "photo_status": "assessed",
  "symptoms": {
    "airway_or_swallowing": "no",
    "lethargy": "no",
    "trismus": "no",
    "facial_swelling": "no",
    "fever_with_toothache": "no",
    "pus_or_fistula": "no",
    "dental_trauma": "no",
    "night_pain": "no",
    "white_spot": "unknown"
  },
  "age_months": 96,
  "pain": 2,
  "preferences": {
    "allergies": [
      "flavor_mint"
    ],
    "halal": true,
    "no_sls": false
  }
}
```

**Response 200** (shipped DRAFT rules; with every param still `PLACEHOLDER` the engine uses each
param's cautious `if_unset` fallback — e.g. any counted finding → 🟠 `urgent`):

<!-- example:evaluate_200 -->
```json
{
  "triage": {
    "level": "urgent",
    "reasons": [
      {
        "id": "PAIN_HIGH",
        "text": "Anak merasakan sakit gigi yang cukup berat. Perlu diperiksa dokter gigi segera."
      },
      {
        "id": "SYM_WHITE_SPOT",
        "text": "Ada bercak putih pada gigi. Ini bisa menjadi indikasi awal gigi berlubang dan perlu diperiksa dokter gigi."
      },
      {
        "id": "DET_CARIES_PERMANENT",
        "text": "Ada indikasi karies pada gigi tetap di foto. Perlu diperiksa dokter gigi."
      },
      {
        "id": "DET_CARIES_PRIMARY",
        "text": "Ada indikasi karies pada gigi sulung di foto. Perlu diperiksa dokter gigi."
      },
      {
        "id": "DET_CARIES_MANY",
        "text": "Ada indikasi karies pada beberapa gigi di foto. Perlu diperiksa dokter gigi segera."
      },
      {
        "id": "CTX_AGE_OUTSIDE_VALIDATED_RANGE",
        "text": "Pemeriksaan foto belum teruji untuk usia anak ini, atau usia belum diisi. Perlu diperiksa langsung oleh dokter gigi."
      }
    ],
    "lesions": [
      {
        "detection_index": 0,
        "class": "caries_primary",
        "dentition": "primary",
        "confidence": 0.81,
        "counted": true
      },
      {
        "detection_index": 1,
        "class": "caries_permanent",
        "dentition": "permanent",
        "confidence": 0.34,
        "counted": true
      }
    ],
    "advice_ids": [
      "ADV_ASK_MEDICATION",
      "ADV_DENTIST_SOON",
      "ADV_PHOTO_LIMITATION",
      "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "ADV_REFERRAL_CTA"
    ],
    "referral_template_id": "REF_URGENT"
  },
  "recommendation": {
    "fluoride": {
      "band_id": "school_84_216m",
      "ppm_options": [
        {
          "candidate_id": "guideline_ge1000",
          "label": "Anjuran pedoman: 1000-1450 ppm",
          "min": 1000,
          "max": 1450,
          "source": "Walsh dkk. 2019, Cochrane CD007868 (hanya abstrak; efek naik dengan konsentrasi, bermakna pada >= 1000 ppm) https://cochranelibrary.com/cdsr/doi/10.1002/14651858.CD007868.pub3/information; docs/research/03_ai_components.md §3.3; EAPD via ringkasan sekunder (docs/research/03_ai_components.md §3.3): < 1000 ppm 'dapat dipertimbangkan' tetapi bukti terbatas; awasi sikat gigi sampai >= 7 th [PERLU VERIFIKASI ke dokumen asli]"
        }
      ],
      "decision_pending": true,
      "amount": {
        "code": "pea_size",
        "text": "Pakai pasta gigi sebesar kacang polong."
      },
      "supervision": {
        "required": false,
        "until_age_months": 84,
        "text": null
      },
      "notes": [
        {
          "id": "look_for_ppm_on_pack",
          "text": "Lihat tulisan kadar fluoride (ppm) di kemasan. Anjuran yang sering dipakai sekitar 1000 ppm. Tanyakan ke dokter gigi atau puskesmas kadar yang cocok untuk usia anak."
        },
        {
          "id": "spit_dont_swallow",
          "text": "Ajari anak membuang busa pasta gigi, jangan ditelan. Anak kecil perlu dibantu orang dewasa."
        },
        {
          "id": "rinse_little",
          "text": "Setelah menyikat gigi, jangan berkumur banyak air. Cukup buang busanya."
        }
      ]
    },
    "disclaimer_ids": [
      "toothpaste_not_filling",
      "see_dentist_cta"
    ],
    "disclaimers": [
      {
        "id": "toothpaste_not_filling",
        "text": "Pasta gigi membantu, tetapi tidak menambal gigi berlubang."
      },
      {
        "id": "see_dentist_cta",
        "text": "Hasil ini bukan pengganti pemeriksaan dokter gigi. Periksakan gigi anak secara rutin ke dokter gigi atau puskesmas."
      }
    ],
    "products": [],
    "notes": [
      {
        "id": "fewer_than_two_products",
        "text": "Contoh produk yang cocok kurang dari dua. Cari kemasan dengan kadar fluoride yang disarankan dokter gigi, atau tanyakan ke apoteker atau puskesmas."
      }
    ],
    "status": {
      "needs_review": true,
      "reasons": [
        "ppm_decision_pending",
        "rules:filters:needs_review",
        "rules:message:amount_pea:needs_review",
        "rules:message:fewer_than_two_products:draft",
        "rules:message:look_for_ppm_on_pack:needs_review",
        "rules:message:rinse_little:needs_review",
        "rules:message:see_dentist_cta:draft",
        "rules:message:spit_dont_swallow:needs_review",
        "rules:message:toothpaste_not_filling:draft",
        "rules:regulatory_ceiling_ppm:needs_review",
        "rules:school_84_216m.age_months:needs_review",
        "rules:school_84_216m.amount:needs_review",
        "rules:school_84_216m.fluoride_ppm:needs_review",
        "rules:school_84_216m.supervision:needs_review",
        "rules:valid_age_months:needs_review"
      ],
      "generic_message": {
        "id": "generic_conservative",
        "text": "Pakai pasta gigi berfluoride dalam jumlah sangat sedikit dan dampingi anak saat menyikat gigi. Tanyakan kadar fluoride yang cocok untuk usia anak ke dokter gigi atau puskesmas."
      }
    },
    "rules_version": "0.1.0-draft"
  },
  "recommendation_status": "ok",
  "rules_version": {
    "triage": "0.2.0-draft",
    "recommender": "0.1.0-draft"
  },
  "rules_status": "draft",
  "clinically_validated": false,
  "disclaimer_ids": [
    "ADV_DISCLAIMER_NOT_DIAGNOSIS",
    "toothpaste_not_filling",
    "see_dentist_cta",
    "RULES_NOT_VALIDATED"
  ],
  "disclaimers": [
    {
      "id": "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "text": "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."
    },
    {
      "id": "toothpaste_not_filling",
      "text": "Pasta gigi membantu, tetapi tidak menambal gigi berlubang."
    },
    {
      "id": "see_dentist_cta",
      "text": "Hasil ini bukan pengganti pemeriksaan dokter gigi. Periksakan gigi anak secara rutin ke dokter gigi atau puskesmas."
    },
    {
      "id": "RULES_NOT_VALIDATED",
      "text": "DRAF. Semua nilai klinis dan aturan belum ditinjau dokter gigi. Tidak boleh dipakai pada anak sebelum disahkan."
    }
  ],
  "referral": {
    "required": true,
    "cta_id": "ADV_REFERRAL_CTA",
    "cta_text": "Buat surat rujukan dan bawa saat memeriksakan anak ke dokter gigi atau puskesmas.",
    "template_id": "REF_URGENT"
  },
  "request_id": "<request_id>",
  "purpose": "screening_reference"
}
```

**Response 422** — a box sent to triage (engine code, fail-safe attached):

<!-- example:evaluate_422_request -->
```json
{
  "detections": [
    {
      "class": "caries_primary",
      "confidence": 0.81,
      "box": [
        112,
        80,
        160,
        150
      ]
    }
  ],
  "photo_status": "assessed",
  "symptoms": {
    "airway_or_swallowing": "no",
    "lethargy": "no",
    "trismus": "no",
    "facial_swelling": "no",
    "fever_with_toothache": "no",
    "pus_or_fistula": "no",
    "dental_trauma": "no",
    "night_pain": "no",
    "white_spot": "no"
  },
  "age_months": 96,
  "pain": 0
}
```

<!-- example:evaluate_422 -->
```json
{
  "error": {
    "code": "DETECTION_INVALID",
    "message": "Screening input rejected by the triage engine.",
    "request_id": "<request_id>",
    "fail_safe": {
      "action": "show_referral_screen",
      "referral_cta_id": "ADV_REFERRAL_CTA",
      "disclaimer_ids": [
        "ADV_DISCLAIMER_NOT_DIAGNOSIS"
      ],
      "rules_status": "draft"
    },
    "engine": "triage"
  }
}
```

## 6. `POST /v1/screening/analyze`

QA / second-opinion pipeline on a **consented dataset photo**, referenced by `photo_ref`
(`public.image_upload.id`). The app never uploads photos to this API; photos reach the private
`dataset_photos` bucket only through the consent (c) flow (FR-25), and this endpoint reads them with
the service role like `/v1/ml/infer`. Requires `ENABLE_SCREENING_API` **and** `ENABLE_CLOUD_INFER`,
role `admin`.

Request: `{photo_ref, symptoms, age_months, pain, preferences?}` — no `detections` (they come from
the detector) and no `photo_status` (derived from the quality gate). Unknown keys (e.g. `image_b64`,
`detections`) → `UNKNOWN_FIELD`.

Stages (each reported in `stages[]` with `status` ∈ `completed|skipped|failed|unavailable`,
`duration_ms` and a small `detail`; no image data ever):

| # | Stage | Behaviour |
|---|---|---|
| a | `red_flag` | Triage on the questionnaire alone (photo `not_taken`). If that already reaches the top level (`emergency`) → **short-circuit**: `consent`, `photo_fetch`, `quality_gate`, `detector` are `skipped`, the photo is **not read** (no audit row), `outcome = red_flag_short_circuit`. Detections could never lower the level anyway (level = max over fired rules). |
| – | availability | Before any photo access: quality gate unavailable → 503 `quality_gate_unavailable`; `NullDetector` (the default — no model ships) → 503 `detector_unavailable`. **No fake detections, ever.** |
| b | `consent`, `photo_fetch` | `dataset_contribution` ON (409 `dataset_contribution_disabled`), photo exists (404 `image_upload_not_found`), status `uploaded` and not expired (409 `image_not_eligible`), active consent (c) (409 `consent_inactive`). Bytes fetched into memory only, cap 512 000 B (409 `photo_not_processable`). One `audit_log` row `screening_analyze_photo_read` (ids HMAC-hashed in the DB). |
| c | `quality_gate` | Reference `ml/detection/quality_gate.py` + `quality_gate.yaml` (all thresholds **PLACEHOLDER**; blur, brightness, teeth-visible *heuristic*). JPEG/PNG only, ≤ 24 MP; undecodable → `not_decodable`. Failed → `photo_status = quality_failed`, detector `skipped`, `outcome = quality_failed`. Needs the `vision` extra (numpy + Pillow); without it the stage is `unavailable` and the call fails closed (503). |
| d | `detector` | Pluggable `Detector` interface. Exceptions → 503 `detector_failed`; unknown class or score outside 0..1 → 503 `detector_output_invalid`. |
| e | `triage` | Engine input rebuilt as `{class, confidence}` per detection (**no geometry**). Boxes are returned only in `overlay` (`box_use: "ui_overlay_only"`, original-image pixels). |
| f | `recommendation` | As in `/evaluate`. |

Request with a red flag (photo never touched):

<!-- example:analyze_red_flag_request -->
```json
{
  "photo_ref": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "symptoms": {
    "airway_or_swallowing": "no",
    "lethargy": "no",
    "trismus": "no",
    "facial_swelling": "yes",
    "fever_with_toothache": "no",
    "pus_or_fistula": "no",
    "dental_trauma": "no",
    "night_pain": "no",
    "white_spot": "no"
  },
  "age_months": null,
  "pain": "unknown"
}
```

<!-- example:analyze_red_flag_200 -->
```json
{
  "triage": {
    "level": "emergency",
    "reasons": [
      {
        "id": "RF_FACIAL_SWELLING",
        "text": "Tanda bahaya: ada bengkak di wajah atau leher. Perlu diperiksa hari ini."
      },
      {
        "id": "PAIN_UNKNOWN",
        "text": "Tingkat sakit gigi belum diketahui. Perlu diperiksa dokter gigi."
      },
      {
        "id": "CTX_PHOTO_NOT_ASSESSED",
        "text": "Foto gigi belum bisa dinilai. Perlu diperiksa langsung oleh dokter gigi."
      }
    ],
    "lesions": [],
    "advice_ids": [
      "ADV_FASKES_TODAY",
      "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "ADV_REFERRAL_CTA"
    ],
    "referral_template_id": "REF_EMERGENCY"
  },
  "recommendation": null,
  "recommendation_status": "skipped_age_unknown",
  "rules_version": {
    "triage": "0.2.0-draft",
    "recommender": "0.1.0-draft"
  },
  "rules_status": "draft",
  "clinically_validated": false,
  "disclaimer_ids": [
    "ADV_DISCLAIMER_NOT_DIAGNOSIS",
    "RULES_NOT_VALIDATED"
  ],
  "disclaimers": [
    {
      "id": "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "text": "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."
    },
    {
      "id": "RULES_NOT_VALIDATED",
      "text": "DRAF. Semua nilai klinis dan aturan belum ditinjau dokter gigi. Tidak boleh dipakai pada anak sebelum disahkan."
    }
  ],
  "referral": {
    "required": true,
    "cta_id": "ADV_REFERRAL_CTA",
    "cta_text": "Buat surat rujukan dan bawa saat memeriksakan anak ke dokter gigi atau puskesmas.",
    "template_id": "REF_EMERGENCY"
  },
  "request_id": "<request_id>",
  "purpose": "qa_second_opinion",
  "outcome": "red_flag_short_circuit",
  "photo_status": "not_taken",
  "stages": [
    {
      "name": "red_flag",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "short_circuit": true
      }
    },
    {
      "name": "consent",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "photo_fetch",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "quality_gate",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "detector",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "triage",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "level_source": "symptoms_only"
      }
    },
    {
      "name": "recommendation",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {
        "recommendation_status": "skipped_age_unknown"
      }
    }
  ],
  "quality": null,
  "detector": null,
  "overlay": null
}
```

Full pipeline. **This example uses the TEST fake detector** (`tests/screening_support.py`) because no
model ships; the photo is the synthetic "mouth" image from the tests and passes the real reference
quality gate:

<!-- example:analyze_request -->
```json
{
  "photo_ref": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "symptoms": {
    "airway_or_swallowing": "no",
    "lethargy": "no",
    "trismus": "no",
    "facial_swelling": "no",
    "fever_with_toothache": "no",
    "pus_or_fistula": "no",
    "dental_trauma": "no",
    "night_pain": "no",
    "white_spot": "no"
  },
  "age_months": 250,
  "pain": 0
}
```

<!-- example:analyze_200 -->
```json
{
  "triage": {
    "level": "urgent",
    "reasons": [
      {
        "id": "DET_CARIES_PERMANENT",
        "text": "Ada indikasi karies pada gigi tetap di foto. Perlu diperiksa dokter gigi."
      },
      {
        "id": "DET_CARIES_PRIMARY",
        "text": "Ada indikasi karies pada gigi sulung di foto. Perlu diperiksa dokter gigi."
      },
      {
        "id": "DET_CARIES_MANY",
        "text": "Ada indikasi karies pada beberapa gigi di foto. Perlu diperiksa dokter gigi segera."
      },
      {
        "id": "CTX_AGE_OUTSIDE_VALIDATED_RANGE",
        "text": "Pemeriksaan foto belum teruji untuk usia anak ini, atau usia belum diisi. Perlu diperiksa langsung oleh dokter gigi."
      }
    ],
    "lesions": [
      {
        "detection_index": 0,
        "class": "caries_primary",
        "dentition": "primary",
        "confidence": 0.81,
        "counted": true
      },
      {
        "detection_index": 1,
        "class": "caries_permanent",
        "dentition": "permanent",
        "confidence": 0.34,
        "counted": true
      }
    ],
    "advice_ids": [
      "ADV_DENTIST_SOON",
      "ADV_PHOTO_LIMITATION",
      "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "ADV_REFERRAL_CTA"
    ],
    "referral_template_id": "REF_URGENT"
  },
  "recommendation": null,
  "recommendation_status": "skipped_age_out_of_range",
  "rules_version": {
    "triage": "0.2.0-draft",
    "recommender": "0.1.0-draft"
  },
  "rules_status": "draft",
  "clinically_validated": false,
  "disclaimer_ids": [
    "ADV_DISCLAIMER_NOT_DIAGNOSIS",
    "RULES_NOT_VALIDATED"
  ],
  "disclaimers": [
    {
      "id": "ADV_DISCLAIMER_NOT_DIAGNOSIS",
      "text": "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."
    },
    {
      "id": "RULES_NOT_VALIDATED",
      "text": "DRAF. Semua nilai klinis dan aturan belum ditinjau dokter gigi. Tidak boleh dipakai pada anak sebelum disahkan."
    }
  ],
  "referral": {
    "required": true,
    "cta_id": "ADV_REFERRAL_CTA",
    "cta_text": "Buat surat rujukan dan bawa saat memeriksakan anak ke dokter gigi atau puskesmas.",
    "template_id": "REF_URGENT"
  },
  "request_id": "<request_id>",
  "purpose": "qa_second_opinion",
  "outcome": "completed",
  "photo_status": "assessed",
  "stages": [
    {
      "name": "red_flag",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "short_circuit": false
      }
    },
    {
      "name": "consent",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "photo_fetch",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {}
    },
    {
      "name": "quality_gate",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "passed": true,
        "reasons": [],
        "config_status": "PLACEHOLDER"
      }
    },
    {
      "name": "detector",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "detections": 2
      }
    },
    {
      "name": "triage",
      "status": "completed",
      "duration_ms": 0.0,
      "detail": {
        "level_source": "symptoms_and_photo"
      }
    },
    {
      "name": "recommendation",
      "status": "skipped",
      "duration_ms": 0.0,
      "detail": {
        "recommendation_status": "skipped_age_out_of_range"
      }
    }
  ],
  "quality": {
    "passed": true,
    "reasons": [],
    "method": "heuristic_color_v1",
    "config_status": "PLACEHOLDER"
  },
  "detector": {
    "name": "fake-test-detector",
    "model_version": "0.0.0-test"
  },
  "overlay": {
    "box_use": "ui_overlay_only",
    "coordinate_space": "original_image_pixels",
    "detections": [
      {
        "detection_index": 0,
        "class": "caries_primary",
        "confidence": 0.81,
        "box": {
          "x1": 112.0,
          "y1": 80.0,
          "x2": 160.0,
          "y2": 150.0
        }
      },
      {
        "detection_index": 1,
        "class": "caries_permanent",
        "confidence": 0.34,
        "box": {
          "x1": 170.0,
          "y1": 82.0,
          "x2": 214.0,
          "y2": 148.0
        }
      }
    ]
  }
}
```

Default deployment (NullDetector) — fails closed before the photo is read:

<!-- example:analyze_503 -->
```json
{
  "error": {
    "code": "detector_unavailable",
    "message": "No detector is configured.",
    "request_id": "<request_id>",
    "fail_safe": {
      "action": "show_referral_screen",
      "referral_cta_id": "ADV_REFERRAL_CTA",
      "disclaimer_ids": [
        "ADV_DISCLAIMER_NOT_DIAGNOSIS"
      ],
      "rules_status": "draft"
    },
    "stages": [
      {
        "name": "red_flag",
        "status": "completed",
        "duration_ms": 0.0,
        "detail": {
          "short_circuit": false
        }
      },
      {
        "name": "detector",
        "status": "unavailable",
        "duration_ms": 0.0,
        "detail": {
          "detector": "null"
        }
      }
    ]
  }
}
```

Consent (c) not active for the child:

<!-- example:analyze_409 -->
```json
{
  "error": {
    "code": "consent_inactive",
    "message": "No active dataset consent (c) for this photo.",
    "request_id": "<request_id>",
    "fail_safe": {
      "action": "show_referral_screen",
      "referral_cta_id": "ADV_REFERRAL_CTA",
      "disclaimer_ids": [
        "ADV_DISCLAIMER_NOT_DIAGNOSIS"
      ],
      "rules_status": "draft"
    },
    "stages": [
      {
        "name": "red_flag",
        "status": "completed",
        "duration_ms": 0.0,
        "detail": {
          "short_circuit": false
        }
      },
      {
        "name": "consent",
        "status": "failed",
        "duration_ms": 0.0,
        "detail": {
          "code": "consent_inactive"
        }
      }
    ]
  }
}
```

## 7. `GET /v1/screening/rules`

Metadata only, so clients and QA can see what is still unvalidated. Lists every triage param, every
reviewable recommender row and the quality-gate config with `status`, `placeholder` and `reviewed`.
**Values are not exposed** (and none is invented); read the rule files for them.

<!-- example:rules_200 -->
```json
{
  "rules_version": {
    "triage": "0.2.0-draft",
    "recommender": "0.1.0-draft"
  },
  "rules_status": "draft",
  "clinically_validated": false,
  "notice": "DRAF. Semua nilai klinis dan aturan belum ditinjau dokter gigi. Tidak boleh dipakai pada anak sebelum disahkan.",
  "release_checks": {
    "triage": {
      "releasable": false,
      "blocker_count": 94,
      "marker_status": "DRAFT"
    },
    "recommender": {
      "releasable": false,
      "blocker_count": 126,
      "marker_status": null
    }
  },
  "classes": [
    {
      "id": "caries_permanent",
      "dentition": "permanent",
      "dataset_label": "D",
      "label": "indikasi karies pada gigi tetap"
    },
    {
      "id": "caries_primary",
      "dentition": "primary",
      "dataset_label": "d",
      "label": "indikasi karies pada gigi sulung"
    }
  ],
  "levels": [
    "emergency",
    "urgent",
    "scheduled",
    "routine"
  ],
  "parameters": [
    {
      "engine": "triage",
      "name": "many_lesions_n",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "permanent_min_confidence",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "primary_min_confidence",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "caries_indication_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "pain_scheduled_min_score",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "pain_urgent_min_score",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "night_pain_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "unknown_red_flag_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "white_spot_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "pain_unknown_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "validated_age_months_min",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "validated_age_months_max",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "age_out_of_range_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "triage",
      "name": "photo_not_assessed_level",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "valid_age_months",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "regulatory_ceiling_ppm",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[pre_eruption].age_months",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[pre_eruption].fluoride_ppm",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[pre_eruption].amount",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[pre_eruption].supervision",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[toddler_6_35m].age_months",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[toddler_6_35m].fluoride_ppm",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[toddler_6_35m].amount",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[toddler_6_35m].supervision",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[preschool_36_83m].age_months",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[preschool_36_83m].fluoride_ppm",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[preschool_36_83m].amount",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[preschool_36_83m].supervision",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[school_84_216m].age_months",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[school_84_216m].fluoride_ppm",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[school_84_216m].amount",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "age_bands[school_84_216m].supervision",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "filters",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "recommender",
      "name": "messages",
      "status": "needs_review",
      "placeholder": true,
      "reviewed": false
    },
    {
      "engine": "quality_gate",
      "name": "quality_gate.yaml",
      "status": "PLACEHOLDER",
      "placeholder": true,
      "reviewed": false
    }
  ],
  "quality_gate": {
    "name": "ml/detection quality_gate",
    "available": true,
    "version": null,
    "config_status": "PLACEHOLDER"
  },
  "detector": {
    "name": "fake-test-detector",
    "available": true,
    "version": "0.0.0-test",
    "config_status": null
  },
  "disclaimer_ids": [
    "ADV_DISCLAIMER_NOT_DIAGNOSIS",
    "RULES_NOT_VALIDATED"
  ]
}
```

## 8. `GET /v1/screening/schema/input` and `/schema/output`

Return `backend/triage/dist/input.schema.json` / `output.schema.json` **verbatim** (JSON Schema
2020-12, generated from `rules.yaml` by `python -m triage.export`). At start-up the API checks
`dist/rules.json`, `input.schema.json` and `output.schema.json` against `dist/SHA256SUMS`; any
mismatch → every screening route answers 503 `rules_unavailable` (fail closed). The `/evaluate`
request = input schema + `preferences`; `/evaluate`'s `triage` object + `rules_version.triage`
validates against the output schema (tested).

## 9. Errors

Body: `{"error": {"code", "message", "request_id", "details"?, "engine"?, "fail_safe"?, "stages"?}}`.
Messages are fixed strings; client input is never echoed.

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Invalid `Content-Length`. |
| 401 | `unauthorized` | No bearer token. |
| 401 | `invalid_token` / `token_expired` | Bad signature/claims / expired JWT. |
| 403 | `forbidden` | Role not allowed, or anonymous user. |
| 404 | `not_found` | `ENABLE_SCREENING_API` (or, for `/analyze`, `ENABLE_CLOUD_INFER`) off — returned before auth. |
| 404 | `image_upload_not_found` | `/analyze`: unknown `photo_ref`. |
| 409 | `dataset_contribution_disabled` · `image_not_eligible` · `consent_inactive` · `photo_not_processable` | `/analyze` gating / photo over the size cap. |
| 413 | `payload_too_large` | Body > `API_MAX_BODY_BYTES` (64 KiB). |
| 422 | **triage engine codes** (`engine: "triage"`) | `INPUT_NOT_OBJECT`, `UNKNOWN_FIELD`, `MISSING_FIELD`, `PHOTO_STATUS_INVALID`, `SYMPTOMS_INVALID`, `UNKNOWN_SYMPTOM`, `MISSING_SYMPTOM`, `INVALID_ANSWER`, `AGE_INVALID`, `PAIN_INVALID`, `DETECTIONS_INVALID`, `DETECTION_INVALID` (incl. any `box`), `UNKNOWN_CLASS`, `CONFIDENCE_INVALID`, `DETECTIONS_WITHOUT_PHOTO` — identical to `TriageInputError.code` (the engine itself classifies the rejected body). |
| 422 | `invalid_input` (`engine: "recommender"`) | Bad `preferences` (recommender's own code). |
| 422 | `too_many_detections` | More than 100 detections (API transport cap). |
| 422 | `validation_error` | Malformed JSON, bad `photo_ref`; `details[]` = location + type only. |
| 429 | `rate_limited` | Token bucket exhausted; `Retry-After` header. |
| 503 | `rules_unavailable` | Rule artefacts missing, invalid or checksum mismatch. |
| 503 | `quality_gate_unavailable` · `detector_unavailable` · `detector_failed` · `detector_output_invalid` | `/analyze` stage cannot run; `stages[]` shows where. |
| 503 | `upstream_unavailable` | Supabase unreachable. |

Examples:

<!-- example:error_401 -->
```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication required.",
    "request_id": "<request_id>"
  }
}
```

<!-- example:error_404 -->
```json
{
  "error": {
    "code": "not_found",
    "message": "Not found.",
    "request_id": "<request_id>"
  }
}
```

## 10. Limits and rate limits

* Rate limit (in-memory token bucket, per replica): `API_RATE_LIMIT_PER_MINUTE` (60) refill and
  `API_RATE_LIMIT_BURST` (20), separately **per client IP** (all routes except `/healthz`, `/readyz`)
  and **per user** (`sub`). `X-Forwarded-For` only with `API_TRUST_FORWARDED_FOR=true`.
* Body ≤ 64 KiB; detections ≤ 100; allergies ≤ 10 × 64 chars; photo ≤ 512 000 B and ≤ 24 MP.

## 11. Privacy and security notes

* **Stateless:** no screening input, result or photo is stored. Results reach Supabase only from
  the device via `sync_events` (idempotent per `event_id`).
* **No PII in logs:** one JSON access line per request with the route *template*, status and
  duration; no bodies, symptoms, levels, ids, tokens or storage paths (tests assert this, including
  that photo bytes never appear in logs or responses).
* **Photos:** `/analyze` accepts only a reference; bytes stay in memory for the request and are
  dropped; nothing is written to disk; the access is audited. Cloud processing moves a photo to the
  API's region (Japan if hosted) for the duration of the call — keep `ENABLE_CLOUD_INFER` off unless
  consent (c), KEPK approval and the legal review cover it.
* Security headers on every response (nosniff, `X-Frame-Options: DENY`, CSP `default-src 'none'`,
  `no-store`, HSTS, no `Server`). CORS closed unless `API_ALLOWED_ORIGINS` is set.
* **Docker:** the screening engines live in sibling folders, so build with the backend root as
  context: `docker build -f backend/api/Dockerfile -t dentisai-api backend` (filtered by
  `api/Dockerfile.dockerignore`; non-root uid 10001; no secrets in the image; config via `--env-file`).

## 12. Typical flows

```mermaid
sequenceDiagram
    autonumber
    participant D as Device (Expo app, offline-first)
    participant S as Supabase (Auth, Postgres+RLS, Storage)
    participant A as DentisAI API (/v1/screening, QA only)
    Note over D: Questionnaire -> red flag? -> photo -> on-device quality gate<br/>-> on-device detector -> TS triage + recommender (bundled rules)
    D->>D: Show level + disclaimer + referral CTA (works in airplane mode)
    D->>S: rpc sync_events(batch) when online (results, never photos)
    opt Consent (c) + dataset_contribution ON
        D->>S: Upload JPEG <= 500 KB to private bucket dataset_photos
    end
    Note over A: Off by default; not on the critical path
    D-->>A: POST /evaluate (optional re-check, same inputs, no photo)
    A-->>D: triage + recommendation + rules_status + disclaimers
    participant Q as QA / dentist (admin)
    Q->>A: POST /analyze {photo_ref, symptoms, ...}
    A->>A: red flags first (short-circuit)
    A->>S: check flag + consent (c), read photo (service role, memory only)
    A->>A: quality gate -> detector -> triage({class, confidence}) -> recommender
    A-->>Q: result + stages[] + overlay boxes (UI only)
```

## 13. Versioning policy

* URL major version (`/v1`). Additive changes (new optional request fields, new response fields,
  new error codes) stay in v1; removing/renaming fields or changing semantics → `/v2`, with `/v1`
  kept until the app versions using it are retired (`min_app_version` in the signed manifest).
* Rule changes do **not** change the API version: they change `rules_version.*` (and
  `rules_status` once a dentist approves). Every rule release is a controlled change (ADR 002):
  `docs/regulatory/CHANGELOG.md` + re-signed manifest.
* New detection classes or questions = triage dist change → the request models must be updated in
  the same change (the drift test fails otherwise) → minor API release noted in `openapi.json`.
* `openapi.json` is regenerated with `uv run python scripts/export_openapi.py` (a test fails when stale).

## 14. curl examples

```bash
API=http://127.0.0.1:8000
TOKEN="<Supabase access token of a guardian/kader/admin>"   # from supabase.auth.getSession()

curl -s "$API/v1/screening/rules" -H "Authorization: Bearer $TOKEN"
curl -s "$API/v1/screening/schema/input" -H "Authorization: Bearer $TOKEN" > input.schema.json

curl -s -X POST "$API/v1/screening/evaluate" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"detections":[{"class":"caries_primary","confidence":0.81}],"photo_status":"assessed",
       "symptoms":{"airway_or_swallowing":"no","lethargy":"no","trismus":"no","facial_swelling":"no",
                   "fever_with_toothache":"no","pus_or_fistula":"no","dental_trauma":"no",
                   "night_pain":"no","white_spot":"unknown"},
       "age_months":96,"pain":2,"preferences":{"allergies":[],"halal":false,"no_sls":true}}'

# admin only; needs ENABLE_SCREENING_API=true and ENABLE_CLOUD_INFER=true
curl -s -X POST "$API/v1/screening/analyze" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"photo_ref":"<image_upload uuid>","symptoms":{"airway_or_swallowing":"no","lethargy":"no",
       "trismus":"no","facial_swelling":"no","fever_with_toothache":"no","pus_or_fistula":"no",
       "dental_trauma":"no","night_pain":"no","white_spot":"no"},"age_months":96,"pain":0}'
```

## 15. How the examples and the parity claim are produced

* The JSON blocks above are written by `uv run python scripts/gen_api_examples.py` (in-process
  TestClient, test fixtures, shipped rules; only `request_id` and `duration_ms` normalised). A test
  (`tests/test_api_docs.py`) fails when they are stale.
* **Parity with the triage engine:** `tests/test_screening_parity.py` drives every case of the
  shared table `backend/triage/tests/cases.json` (the one pytest and Jest use) through
  `POST /v1/screening/evaluate` with the same test params profile and asserts identical level,
  reason ids, advice ids, referral template, lesions and error codes, and byte-equal triage output
  vs a direct `triage.evaluate` call. A hypothesis property checks "red flag + any detections →
  emergency" through HTTP.

## 16. Not verified / open

* Nothing is deployed; Supabase Storage photo fetch is exercised only against the in-memory fake
  and the existing respx tests of `SupabaseRepository`.
* No real detector exists (`NullDetector`); `/analyze` therefore answers 503 in any real deployment
  until a permissively licensed model from `ml/detection` is wired in (ADR 001/008/014).
* All clinical values are PLACEHOLDER; `rules_status` is `draft`; quality-gate thresholds are
  PLACEHOLDER (FR-21 needs ≥ 200 labelled photos).
* `risk_level` is not passed to the recommender (mapping a triage level to caries risk is a dentist
  decision that has not been made).
