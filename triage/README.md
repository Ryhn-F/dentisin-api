# Triase DentisAI (`backend/triage/`)

> **STATUS: DRAF — BELUM DITINJAU DOKTER GIGI. TIDAK BOLEH DIPAKAI PADA ANAK.**
> Hasil skrining saja. Semua nilai klinis masih `PLACEHOLDER`; `reviewed_by` = `PENDING_DRG` di semua aturan.
> **Syarat ≥ 30 kasus uji yang dinilai drg (`graded_by_drg`) BELUM terpenuhi (NOT met): 0 dari 114 kasus. Tidak boleh ada klaim klinis apa pun.**
> Versi aturan `0.2.0-draft` mengikuti **ADR 014** (kelas menurut jenis gigi; ukuran kotak tidak dipakai).

## Bagian 1 — Ringkasan untuk dokter gigi

Sumber kebenaran: [`rules.yaml`](rules.yaml) (bisa dibaca langsung; komentar berbahasa Indonesia).

**Cara kerja singkat**
1. Aplikasi menanyakan gejala (jawaban: *ya / tidak / tidak tahu*), skor nyeri 0–10 (atau *tidak tahu*), usia (bulan), lalu memotret gigi depan.
2. Model foto hanya memberi **jenis temuan + keyakinan**: `caries_permanent` (label data **D**, indikasi karies pada gigi tetap) dan `caries_primary` (label **d**, indikasi karies pada gigi sulung).
   Model **tidak** membedakan berlubang/belum berlubang (data publik tidak punya label itu).
3. **Ukuran atau posisi kotak tidak pernah dipakai** untuk menilai keparahan (ukuran gigi berbeda antar-anak; kotak melingkupi seluruh gigi). Kotak hanya untuk gambar di layar.
4. Keparahan hanya dari: tanda bahaya & gejala (kuesioner), skor nyeri, **jumlah** temuan terhitung (ambang **N**), usia, dan kelayakan foto.
5. Semua aturan dicek. **Level akhir = level tertinggi** dari aturan yang terpenuhi
   (setara urutan: tanda bahaya → gejala → temuan foto → jumlah temuan → konteks → bawaan). Tanda bahaya selalu 🔴.
6. Setiap hasil selalu memuat: *"Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."* + ajakan membuat surat rujukan. Hasil 🟢 selalu menyarankan skrining ulang 3–6 bulan.

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
| `DET_CARIES_PERMANENT` | ≥ 1 temuan terhitung "indikasi karies pada gigi tetap" (D) | param `caries_indication_level` (🟡/🟠; sementara 🟠) | level **sama** dengan gigi sulung |
| `DET_CARIES_PRIMARY` | ≥ 1 temuan terhitung "indikasi karies pada gigi sulung" (d) | param `caries_indication_level` (sama) | |
| `DET_CARIES_MANY` | Jumlah temuan terhitung (sulung + tetap) ≥ N | 🟠 | bukan ukuran kotak |
| `CTX_AGE_OUTSIDE_VALIDATED_RANGE` | Foto dinilai, tetapi usia di luar rentang teruji atau kosong | param `age_out_of_range_level` (sementara 🟡) | model belum teruji di usia itu |
| `CTX_PHOTO_NOT_ASSESSED` | Foto tidak diambil / gagal cek kualitas | param `photo_not_assessed_level` (sementara 🟡) | agar tidak keliru menenangkan |
| `DEFAULT_ROUTINE` | Tidak ada aturan lain yang menaikkan level | 🟢 + skrining ulang 3–6 bulan | |

"Terhitung" = keyakinan model ≥ ambang kelasnya (`permanent_min_confidence`, `primary_min_confidence`).

### Parameter yang harus ditetapkan drg (semua masih kosong)

| Parameter | Arti | Selama kosong (pilihan lebih waspada) |
|---|---|---|
| `many_lesions_n` (**N**) | Jumlah temuan terhitung yang dianggap "banyak" | 1 |
| `permanent_min_confidence` | Ambang keyakinan "karies gigi tetap" | 0 → semua temuan dihitung |
| `primary_min_confidence` | Ambang keyakinan "karies gigi sulung" | 0 |
| `caries_indication_level` | Level bila ada ≥ 1 indikasi karies (sulung atau tetap): 🟡 atau 🟠 | 🟠 |
| `pain_scheduled_min_score` | Skor nyeri minimum → 🟡 | 1 |
| `pain_urgent_min_score` | Skor nyeri minimum → 🟠 | 1 |
| `night_pain_level` | Nyeri malam: 🔴 atau 🟠 | 🔴 |
| `unknown_red_flag_level` | "Tidak tahu" pada tanda bahaya: 🔴 atau 🟠 | 🔴 |
| `white_spot_level` | Bercak putih: 🟢 atau 🟡 | 🟡 |
| `pain_unknown_level` | Nyeri "tidak tahu": 🟢/🟡/🟠 | 🟠 |
| `validated_age_months_min` / `validated_age_months_max` | Rentang usia (bulan) yang hasil fotonya sudah teruji | rentang kosong → semua usia "belum teruji" |
| `age_out_of_range_level` | Level minimum di luar rentang usia | 🟡 |
| `photo_not_assessed_level` | Level minimum tanpa foto layak | 🟡 |

Parameter **X** (`small_lesion_rel_area`) dan acuan ukuran kotak (`lesion_area_reference`) **dihapus** (ADR 014).

### Jawaban "tidak tahu" dan data hilang
- **"Tidak tahu" tidak pernah dianggap "tidak".** Tanda bahaya → `RF_UNKNOWN_ANSWER`; nyeri malam & bercak putih → diperlakukan seperti "ya"; nyeri → `PAIN_UNKNOWN`; usia kosong → `CTX_AGE_OUTSIDE_VALIDATED_RANGE`.
- **Jawaban yang hilang/tidak sah = error** (`TriageInputError`), bukan hasil. Aplikasi wajib menampilkan layar rujukan ("hubungi puskesmas") dan tidak boleh menampilkan level lebih rendah.

### Keputusan yang perlu dikonfirmasi drg
1. **Jenis gigi dan level:** saat ini gigi sulung dan gigi tetap memakai **level yang sama** (`caries_indication_level`). Bila drg ingin berbeda (mis. gigi tetap lebih mendesak, atau gigi sulung pada anak kecil lebih mendesak), tambahkan param per jenis gigi — tim teknis tidak menebak.
2. Satu indikasi karies (tanpa info berlubang/belum): 🟡 atau 🟠? (sementara 🟠).
3. `RF_TRISMUS` digolongkan "darurat sekarang" (mengikuti draf riset §3.2).
4. Tanda bahaya "tidak tahu" → 🔴 hari ini (bukan "sekarang"), dengan saran memeriksa ulang gejala.
5. Nyeri malam & bercak putih "tidak tahu" diperlakukan seperti "ya".
6. Skala nyeri 0–10 (wajah, mis. Wong-Baker) — [PERLU VERIFIKASI].
7. Aturan usia & foto tidak layak sebagai level minimum (agar 🟢 tidak keliru menenangkan).
8. Nomor darurat 112/119 [PERLU VERIFIKASI]; jangka waktu "segera" (berapa hari) belum ditulis.
9. 9 pertanyaan kuesioner (FR-10 meminta ≤ 7 layar — aplikasi boleh menggabungkan beberapa pertanyaan).

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
  tests/cases.json           SHARED table (pytest + Jest), 114 cases (90 results + 24 typed errors)
  tests/fixtures/params_profiles.json   TEST-ONLY params (test / test_alt / unset) — not clinical
```

### Commands
```bash
cd backend/triage
uv sync
uv run pytest -q                       # --cov-fail-under=100 with branch coverage
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
  enforced by the loader). This is what the app gets today: any counted finding → 🟠.
- Tests use `tests/fixtures/params_profiles.json` (`clinical: false`) so logic is fully tested
  without pretending clinical values exist. `unset` = all null (exercises the fallbacks).
- `release_check` blocks while: status ≠ APPROVED, any param null / not `SET`, any
  `reviewed_by` null/`PENDING_DRG` or `reviewed_at` null, any source with `PERLU VERIFIKASI`,
  emergency numbers unverified, < 30 `graded_by_drg` cases, stale dist, or a forbidden phrase.
- Review basis (ADR 015): optional top-level `review_basis`. Absent or `dentist` = the gate above,
  unchanged. `literature` = `reviewed_by` may be a team member (non-empty, not a placeholder such
  as `PENDING_DRG`/`PLACEHOLDER`/`null`), every rule/param `source` must be filled (no
  `PERLU VERIFIKASI`), and the case requirement becomes ≥ 30 cases with a non-empty
  `expected_source` (no `PERLU VERIFIKASI`) instead of `graded_by_drg`. `rules.yaml` has no
  `review_basis` yet (nothing is reviewed on any basis).

### Engine contract (rules 0.2.0, ADR 014)
`evaluate(rules, input, params) -> {level, reasons[{id,text}], lesions[], advice_ids[], referral_template_id, rules_version}`

Input (`dist/input.schema.json`):
`{detections:[{class: "caries_permanent"|"caries_primary", confidence}], photo_status, symptoms:{<question id>: "yes"|"no"|"unknown"}, age_months:int|null, pain:int|"unknown"}`.

- **Box geometry is not an engine input.** Each detection must have exactly `class` and
  `confidence`; any other key (e.g. `box`) raises `DETECTION_INVALID`. The app keeps boxes for the
  overlay and strips them before calling the engine. The old `reference_frame` field is rejected
  (`UNKNOWN_FIELD`); old classes `caries_cavitated`/`caries_noncavitated` raise `UNKNOWN_CLASS`.
- Each lesion in the output: `{detection_index, class, dentition: "primary"|"permanent", confidence, counted}`
  (no `rel_area`, no `size`). Show dentition in the UI as "indikasi pada gigi sulung / gigi tetap".

Algorithm (see `engine.py` docstring): validate params → validate input (order: fields,
photo_status, symptoms, age, pain, detections, detections-need-photo) → assess detections
(counted iff `confidence >= threshold of its class`) → walk rules in file order using the fixed
condition vocabulary (`symptom_answer`, `any_symptom_answer`, `pain_score_at_least`,
`pain_score_in_range`, `pain_unknown`, `detection_count_at_least` (class id or `any`),
`age_outside_validated_range`, `photo_not_assessed`, `no_higher_level_rule_fired`) → level = max
rank of fired rules (each has a fixed `level` or a `level_param`) → advice = advice of fired rules
at the final level (file order), then level advice, then `photo_assessed_advice` (assessed photo
only), then `always_advice`, de-duplicated (first wins). Numbers: bool is not a number; integral
floats (96.0) are accepted as integers (JS parity).

Error codes (`TriageInputError.code`): `INPUT_NOT_OBJECT, UNKNOWN_FIELD, MISSING_FIELD,
PHOTO_STATUS_INVALID, SYMPTOMS_INVALID, UNKNOWN_SYMPTOM, MISSING_SYMPTOM, INVALID_ANSWER,
AGE_INVALID, PAIN_INVALID, DETECTIONS_INVALID, DETECTION_INVALID, UNKNOWN_CLASS,
CONFIDENCE_INVALID, DETECTIONS_WITHOUT_PHOTO`; params: `PARAM_UNKNOWN, PARAM_MISSING, PARAM_INVALID`.
(Removed in 0.2.0: `BOX_INVALID`, `REFERENCE_FRAME_INVALID`, `REFERENCE_FRAME_REQUIRED`.)

### How the TS runtime must consume the rules
1. Bundle `dist/rules.json` (never parse YAML in the app) into `app/assets/rules/`; verify its
   SHA-256 against `dist/SHA256SUMS` (and later the signed manifest) before use.
2. Port `engine.py` 1:1 (`resolveParams`, `checkParams`, `evaluate`); read every threshold,
   list and text from `rules.json`. Throw an error carrying the same `code` strings.
3. Use `dist/input.schema.json` / `dist/output.schema.json` (e.g. with ajv) for contract tests.
4. The app passes `resolveParams(rules)` (no overrides) in production.
5. Parity: run `tests/cases.json` in Jest, with full line + branch coverage on the TS engine.

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
      expect(out.lesions.map(({ detection_index, dentition, counted }) =>
        ({ detection_index, dentition, counted }))).toEqual(c.expected.lesions);
    }
  });
});
```

### Shared case format (`tests/cases.json`, `format_version: 2`)
`{id, description, tags[], input:{detections[{class, confidence}], photo_status, symptoms{}, age_months, pain, params_profile}, expected:{level, reasons_ids[], advice_ids[], referral_template_id, lesions?[{detection_index, dentition, counted}]} | {error:"TriageInputError", error_code}, expected_source?, graded_by_drg:false}`.
`expected_source` (optional, ADR 015) = citation for the expected result; no case has one yet.
`reasons_ids` and `advice_ids` are compared **in order**. Strip `params_profile` before calling the
engine. All expectations were authored by engineers; `graded_by_drg` is false for every case, so the
≥ 30 dentist-graded cases requirement is **NOT met**.

### Tests
- `test_cases_table.py` — the shared table; every rule id, advice id, level and profile is covered;
  every expected id exists; valid inputs match `dist/input.schema.json`.
- `test_properties.py` (hypothesis) — a red flag never lowers the level and always yields 🔴; answers
  monotone `no ≤ unknown ≤ yes`; monotone in detection **count**, confidence and pain; dentition
  (primary vs permanent) never changes the level; any box/geometry key is rejected so box size can
  never affect the result; unknown age / missing photo never lower; outputs always carry
  disclaimer + CTA (+ re-screen for 🟢), are deterministic and leave the input untouched; missing or
  invalid answers raise.
- `test_rules_file.py` — schema + invariants, no invented reviewers, PLACEHOLDERs, no geometry
  params, shared dentition level, forbidden-phrase lint over **every** string in rules.yaml,
  screening wording, dist freshness, README coverage.
- `test_loader.py`, `test_engine_units.py`, `test_export.py`, `test_release_check.py`, `test_forbidden.py`.
