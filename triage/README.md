# Triase DentisAI (`backend/triage/`)

> **STATUS: DRAF — BELUM DITINJAU DOKTER GIGI. TIDAK BOLEH DIPAKAI PADA ANAK.**
> Skrining, bukan diagnosis. Semua nilai klinis masih `PLACEHOLDER`; `reviewed_by` = `PENDING_DRG` di semua aturan.
> **Syarat ≥ 30 kasus uji yang dinilai drg (`graded_by_drg`) BELUM terpenuhi (NOT met): 0 dari 122 kasus. Tidak boleh ada klaim klinis apa pun.**

## Bagian 1 — Ringkasan untuk dokter gigi

Sumber kebenaran: [`rules.yaml`](rules.yaml) (bisa dibaca langsung; komentar berbahasa Indonesia).

**Cara kerja singkat**
1. Aplikasi menanyakan gejala (jawaban: *ya / tidak / tidak tahu*), skor nyeri 0–10 (atau *tidak tahu*), usia (bulan), lalu memotret gigi depan.
2. Model foto hanya memberi **kotak** (bounding box) untuk `caries_cavitated` (indikasi lubang) dan `caries_noncavitated` ("perlu perhatian").
   Ukuran lesi = luas kotak ÷ luas foto (atau ÷ luas bingkai gigi depan). Lesi "besar" bila ukuran ≥ **X**.
3. Semua aturan di bawah dicek. **Level akhir = level tertinggi** dari aturan yang terpenuhi
   (setara dengan urutan: tanda bahaya → skor/ukuran tertinggi → jumlah lesi → bawaan). Tanda bahaya selalu 🔴.
4. Setiap hasil selalu memuat: *"Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."* + ajakan membuat surat rujukan. Hasil 🟢 selalu menyarankan skrining ulang 3–6 bulan.

**Level**: 🔴 Darurat (`emergency`, ke puskesmas/IGD hari ini) · 🟠 Segera (`urgent`) · 🟡 Terjadwal (`scheduled`) · 🟢 Rutin (`routine`).

### Tabel aturan (urutan sesuai `rules.yaml`)

| ID aturan | Kapan terpenuhi | Level | Catatan |
|---|---|---|---|
| `RF_AIRWAY_OR_SWALLOWING` | "Ya": sulit menelan/bernapas, air liur menetes | 🔴 + **hubungi nomor darurat sekarang** | dari kuesioner, bukan model |
| `RF_LETHARGY` | "Ya": anak sangat lemas/terus mengantuk | 🔴 + darurat sekarang | |
| `RF_TRISMUS` | "Ya": sulit membuka mulut | 🔴 + darurat sekarang | **perlu konfirmasi**: draf riset menaruhnya di "sekarang" |
| `RF_FACIAL_SWELLING` | "Ya": bengkak wajah/leher/bawah lidah | 🔴 hari ini | |
| `RF_FEVER_WITH_TOOTHACHE` | "Ya": demam + sakit gigi | 🔴 hari ini | |
| `RF_PUS_OR_FISTULA` | "Ya": nanah/benjolan bernanah | 🔴 hari ini | |
| `RF_DENTAL_TRAUMA` | "Ya": gigi/mulut terbentur | 🔴 hari ini | |
| `RF_UNKNOWN_ANSWER` | Ada tanda bahaya dijawab "tidak tahu" | param `unknown_red_flag_level` (sementara 🔴 hari ini) | "tidak tahu" ≠ "tidak" |
| `SYM_NIGHT_PAIN` | Nyeri membangunkan anak malam hari ("ya" **atau "tidak tahu"**) | param `night_pain_level` (🔴/🟠; sementara 🔴) | |
| `PAIN_HIGH` | Skor nyeri ≥ `pain_urgent_min_score` | 🟠 | |
| `PAIN_MODERATE` | `pain_scheduled_min_score` ≤ skor < `pain_urgent_min_score` | 🟡 | |
| `PAIN_UNKNOWN` | Skor nyeri "tidak tahu" | param `pain_unknown_level` (sementara 🟠) | |
| `SYM_WHITE_SPOT` | Orang tua melihat bercak putih ("ya" atau "tidak tahu") | param `white_spot_level` (🟢/🟡; sementara 🟡) | dari kuesioner (FR-12) |
| `DET_CAVITY_LARGE` | ≥ 1 lubang terhitung dengan ukuran ≥ X | 🟠 | |
| `DET_CAVITY_SMALL` | ≥ 1 lubang terhitung dengan ukuran < X | 🟡 | |
| `DET_NONCAV_ATTENTION` | ≥ 1 temuan "perlu perhatian" terhitung | **tidak mengubah level** (hanya catatan) | FR-32 |
| `DET_CAVITY_MANY` | Jumlah lubang terhitung ≥ N | 🟠 | non-kavitas tidak dihitung |
| `CTX_AGE_OUTSIDE_VALIDATED_RANGE` | Foto dinilai, tetapi usia di luar rentang teruji atau kosong | param `age_out_of_range_level` (sementara 🟡) | model belum teruji di usia itu |
| `CTX_PHOTO_NOT_ASSESSED` | Foto tidak diambil / gagal cek kualitas | param `photo_not_assessed_level` (sementara 🟡) | agar tidak keliru menenangkan |
| `DEFAULT_ROUTINE` | Tidak ada aturan lain yang menaikkan level | 🟢 + skrining ulang 3–6 bulan | |

"Terhitung" = keyakinan model ≥ ambang kelasnya (`cavitated_min_confidence`, `noncavitated_min_confidence`).

### Parameter yang harus ditetapkan drg (semua masih kosong)

| Parameter | Arti | Selama kosong (pilihan lebih waspada) |
|---|---|---|
| `small_lesion_rel_area` (**X**) | Batas ukuran relatif lesi "besar" | 0 → semua lubang dianggap besar |
| `many_lesions_n` (**N**) | Jumlah lubang yang dianggap "banyak" | 1 |
| `cavitated_min_confidence` | Ambang keyakinan "lubang" | 0 → semua temuan dihitung |
| `noncavitated_min_confidence` | Ambang keyakinan "perlu perhatian" | 0 |
| `pain_scheduled_min_score` | Skor nyeri minimum → 🟡 | 1 |
| `pain_urgent_min_score` | Skor nyeri minimum → 🟠 | 1 |
| `night_pain_level` | Nyeri malam: 🔴 atau 🟠 | 🔴 |
| `unknown_red_flag_level` | "Tidak tahu" pada tanda bahaya: 🔴 atau 🟠 | 🔴 |
| `white_spot_level` | Bercak putih: 🟢 atau 🟡 | 🟡 |
| `pain_unknown_level` | Nyeri "tidak tahu": 🟢/🟡/🟠 | 🟠 |
| `validated_age_months_min` / `validated_age_months_max` | Rentang usia (bulan) yang hasil fotonya sudah teruji | rentang kosong → semua usia "belum teruji" |
| `age_out_of_range_level` | Level minimum di luar rentang usia | 🟡 |
| `photo_not_assessed_level` | Level minimum tanpa foto layak | 🟡 |
| `lesion_area_reference` | Acuan ukuran: seluruh foto (`image`) atau bingkai gigi depan (`frame`) | `image` (usulan teknis) |

### Jawaban "tidak tahu" dan data hilang
- **"Tidak tahu" tidak pernah dianggap "tidak".** Tanda bahaya → `RF_UNKNOWN_ANSWER`; nyeri malam & bercak putih → diperlakukan seperti "ya"; nyeri → `PAIN_UNKNOWN`; usia kosong → `CTX_AGE_OUTSIDE_VALIDATED_RANGE`.
- **Jawaban yang hilang/tidak sah = error** (`TriageInputError`), bukan hasil. Aplikasi wajib menampilkan layar rujukan ("hubungi puskesmas") dan tidak boleh menampilkan level lebih rendah.

### Asumsi tim teknis yang perlu dikonfirmasi drg
1. `RF_TRISMUS` digolongkan "darurat sekarang" (mengikuti draf riset §3.2); brief tim menyebut hanya napas/menelan/lemas.
2. Tanda bahaya "tidak tahu" → 🔴 hari ini (bukan "sekarang"), dengan saran memeriksa ulang gejala.
3. Nyeri malam & bercak putih "tidak tahu" diperlakukan seperti "ya".
4. Skala nyeri 0–10 (wajah, mis. Wong-Baker) — [PERLU VERIFIKASI].
5. Aturan usia & foto tidak layak sebagai level minimum (agar 🟢 tidak keliru menenangkan).
6. Lubang besar **atau** banyak → 🟠; lubang kecil → 🟡; "perlu perhatian" tidak menaikkan level.
7. Nomor darurat 112/119 [PERLU VERIFIKASI]; jangka waktu "segera" (berapa hari) belum ditulis.
8. 9 pertanyaan kuesioner (FR-10 meminta ≤ 7 layar — aplikasi boleh menggabungkan beberapa pertanyaan).

---

## Part 2 — Developer notes (English)

### Layout
```
backend/triage/
  rules.yaml                 single source of truth (dentist-readable)
  schema.json                JSON Schema for rules.yaml / dist/rules.json
  schemas/cases.schema.json  JSON Schema for tests/cases.json
  dist/                      GENERATED: rules.json, input.schema.json, output.schema.json, SHA256SUMS
  src/triage/
    engine.py                pure engine: resolve_params, check_params, evaluate (port 1:1 to TS)
    errors.py                TriageInputError / TriageParamsError / RulesValidationError (+ .code)
    loader.py                YAML/JSON loading + schema + semantic invariants (CI only)
    forbidden.py             forbidden-phrase lint parsed from docs/style/forbidden_phrases.md
    export.py                python -m triage.export [--check]
    release_check.py         python -m triage.release_check (exit 1 = not releasable)
    paths.py                 file locations
  tests/cases.json           SHARED table (pytest + Jest), 122 cases (97 results + 25 typed errors)
  tests/fixtures/params_profiles.json   TEST-ONLY params (test / test_alt / unset) — not clinical
```

### Commands
```bash
cd backend/triage
uv sync
uv run pytest -q                       # 100% line+branch coverage enforced
uv run ruff check . && uv run mypy     # mypy --strict (configured in pyproject)
uv run python -m triage.export         # regenerate dist/ after editing rules.yaml
uv run python -m triage.export --check # CI: fail if dist/ is stale
uv run python -m triage.release_check  # CI release job: FAILS today (by design)
```
The forbidden-phrase list is read from env `FORBIDDEN_PHRASES_PATH` if set, else the shared
original `../../docs/style/forbidden_phrases.md` (repo root) if present, else the vendored copy
`backend/docs/style/forbidden_phrases.md`. A test fails if the vendored copy drifts from the shared one.

### Params: dev vs release
- `rules.yaml` params are `null` + `PLACEHOLDER`. `resolve_params(rules)` substitutes each param's
  `if_unset` (always the more cautious choice; for level params it must be the highest allowed level —
  enforced by the loader). This is what the app gets today.
- Tests use `tests/fixtures/params_profiles.json` (`clinical: false`) so logic is fully tested
  without pretending clinical values exist. `unset` = all null (exercises the fallbacks).
- `release_check` blocks while: status ≠ APPROVED, any param null / not `SET`, any
  `reviewed_by` null/`PENDING_DRG` or `reviewed_at` null, any source with `PERLU VERIFIKASI`,
  emergency numbers unverified, < 30 `graded_by_drg` cases, stale dist, or a forbidden phrase.

### Engine contract
`evaluate(rules, input, params) -> {level, reasons[{id,text}], lesions[], advice_ids[], referral_template_id, rules_version}`

Input (`dist/input.schema.json`): `{detections:[{class, confidence, box:{x_min,y_min,x_max,y_max}}], photo_status, reference_frame|null, symptoms:{<question id>: "yes"|"no"|"unknown"}, age_months:int|null, pain:int|"unknown"}` — boxes normalised to [0,1].
Each lesion: `{detection_index, class, confidence, rel_area, counted, size: "small"|"large"|null}`.

Algorithm (see `engine.py` docstring): validate params → validate input (order: fields,
photo_status, symptoms, age, pain, detections, reference_frame, frame-required) → assess lesions
(`rel_area = box area / (1 or frame area)`; counted iff `confidence >= threshold`; `large` iff
`rel_area >= X`) → walk rules in file order using the fixed condition vocabulary
(`symptom_answer`, `any_symptom_answer`, `pain_score_at_least`, `pain_score_in_range`,
`pain_unknown`, `lesion_count_at_least`, `age_outside_validated_range`, `photo_not_assessed`,
`no_higher_level_rule_fired`) → level = max rank of fired `raise` rules → advice = advice of fired
raise rules at the final level + fired info rules (file order), then level advice, then
`photo_assessed_advice` (assessed photo only), then `always_advice`, de-duplicated (first wins).
Comparisons are plain IEEE-754 doubles (`>=`); no rounding. Numbers: bool is not a number;
integral floats (96.0) are accepted as integers (JS parity).

Error codes (`TriageInputError.code`): `INPUT_NOT_OBJECT, UNKNOWN_FIELD, MISSING_FIELD,
PHOTO_STATUS_INVALID, SYMPTOMS_INVALID, UNKNOWN_SYMPTOM, MISSING_SYMPTOM, INVALID_ANSWER,
AGE_INVALID, PAIN_INVALID, DETECTIONS_INVALID, DETECTION_INVALID, UNKNOWN_CLASS,
CONFIDENCE_INVALID, BOX_INVALID, DETECTIONS_WITHOUT_PHOTO, REFERENCE_FRAME_INVALID,
REFERENCE_FRAME_REQUIRED`; params: `PARAM_UNKNOWN, PARAM_MISSING, PARAM_INVALID`.

### How the TS runtime must consume the rules
1. Bundle `dist/rules.json` (never parse YAML in the app) into `app/assets/rules/`; verify its
   SHA-256 against `dist/SHA256SUMS` (and later the signed manifest) before use.
2. Port `engine.py` 1:1 (`resolveParams`, `checkParams`, `evaluate`); read every threshold,
   list and text from `rules.json`. Throw an error carrying the same `code` strings.
3. Use `dist/input.schema.json` / `dist/output.schema.json` (e.g. with ajv) for contract tests.
4. The app passes `resolveParams(rules)` (no overrides) in production.
5. Parity: run `tests/cases.json` in Jest, 100% coverage on the TS engine.

### Sample Jest test (for `app/__tests__/triage.parity.test.ts`; do not copy test params into the app bundle)
```ts
import rules from '../../backend/triage/dist/rules.json';
import table from '../../backend/triage/tests/cases.json';
import fixtures from '../../backend/triage/tests/fixtures/params_profiles.json';
import { evaluate, resolveParams } from '../src/domain/triage';

describe.each(table.cases.map((c) => [c.id, c] as const))('%s', (_id, c: any) => {
  const { params_profile, ...input } = c.input;
  const params = resolveParams(rules, (fixtures.profiles as any)[params_profile].values);
  it(c.description, () => {
    if (c.expected.error) {
      expect(() => evaluate(rules, input, params)).toThrow(
        expect.objectContaining({ code: c.expected.error_code }));
      return;
    }
    const out = evaluate(rules, input, params);
    expect(out.level).toBe(c.expected.level);
    expect(out.reasons.map((r) => r.id)).toEqual(c.expected.reasons_ids);
    expect(out.advice_ids).toEqual(c.expected.advice_ids);
    expect(out.referral_template_id).toBe(c.expected.referral_template_id);
    if (c.expected.lesions) {
      expect(out.lesions.map(({ detection_index, counted, size }) =>
        ({ detection_index, counted, size }))).toEqual(c.expected.lesions);
    }
  });
});
```

### Shared case format (`tests/cases.json`)
`{id, description, tags[], input:{detections[], photo_status, reference_frame, symptoms{}, age_months, pain, params_profile}, expected:{level, reasons_ids[], advice_ids[], referral_template_id, lesions?} | {error:"TriageInputError", error_code}, graded_by_drg:false}`.
`reasons_ids` and `advice_ids` are compared **in order**. Strip `params_profile` before calling the
engine. All expectations were authored by engineers; `graded_by_drg` is false for every case, so the
≥ 30 dentist-graded cases requirement is **NOT met**.

### Tests
- `test_cases_table.py` — the shared table; every rule id, advice id, level and profile is covered;
  every expected id exists; valid inputs match `dist/input.schema.json`.
- `test_properties.py` (hypothesis) — a red flag never lowers the level and always yields 🔴; answers
  monotone `no ≤ unknown ≤ yes`; monotone in lesion count, size, confidence and pain; non-cavitated
  detections never change the level; unknown age / missing photo never lower; outputs always carry
  disclaimer + CTA (+ re-screen for 🟢), are deterministic and leave the input untouched; missing or
  invalid answers raise.
- `test_rules_file.py` — schema + invariants, no invented reviewers, PLACEHOLDERs, forbidden-phrase
  lint over **every** string in rules.yaml, screening wording, dist freshness, README coverage.
- `test_loader.py`, `test_engine_units.py`, `test_export.py`, `test_release_check.py`, `test_forbidden.py`.
