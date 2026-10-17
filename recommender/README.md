# Rekomendasi pasta gigi (v1: fluoride saja)

> **STATUS: DRAF. Belum ada drg yang mengesahkan satu pun nilai.** Semua baris di `rules.json` berstatus `needs_review` atau `draft`, `reviewed_by` dan `reviewed_at` masih `null`. Sampai disahkan: tidak ada klaim klinis, dan app menampilkan pesan umum yang konservatif (`status.generic_message`).
> Ini skrining, bukan diagnosis. Setiap keluaran memuat kalimat wajib "Pasta gigi membantu, tetapi tidak menambal gigi berlubang." dan anjuran periksa ke dokter gigi/puskesmas.

## Untuk dokter gigi: yang perlu diputuskan

Berkas yang dibaca: [`rules.json`](rules.json). Cara mengesahkan: ubah `status` ke `approved`, isi `reviewed_by` (nama Anda) dan `reviewed_at` (YYYY-MM-DD) pada baris yang Anda setujui. Untuk kadar ppm, isi juga `selected_candidate_id` dan ubah `decision_pending` ke `false`.

| Kelompok usia (draf) | Kadar fluoride (kandidat) | Jumlah pakai | Didampingi dewasa | Yang harus diputuskan |
|---|---|---|---|---|
| 0-5 bulan (belum ada gigi) | tidak ada | tidak pakai pasta; gusi dibersihkan kain lembap | ya | Apakah batas 6 bulan dan "belum perlu pasta" sudah benar? (asumsi tim teknis, tanpa sumber) |
| 6 bulan sampai < 3 tahun | A: 1000-1450 ppm (anjuran pedoman) **atau** B: 500-1000 ppm (SNI 16-4767-1998, lama) | olesan tipis, sebutir beras | ya, sampai 7 tahun | **Pilih A atau B** (atau rentang lain) bersama PDGI/IDAI. Apakah usia < 6 th berbeda? |
| 3 sampai 6 tahun | A atau B (sama seperti di atas) | sebesar kacang polong | ya, sampai 7 tahun | Pilihan ppm yang sama. Apakah batas usia 3 tahun sudah benar? |
| 7 tahun ke atas | A: 1000-1450 ppm | sebesar kacang polong (asumsi) | tidak wajib | Apakah SNI anak (B) berlaku juga di usia ini? Apakah jumlah sebesar kacang polong benar? |

Hal lain yang perlu disahkan:

| Butir | Nilai draf | Pertanyaan |
|---|---|---|
| Plafon fluoride | 1500 ppm (0,15%, Permenkes 445/1998) | Apakah masih berlaku di bawah Peraturan BPOM 23/2019 jo. 17/2022? Sumber sekunder, belum diverifikasi. |
| Batas usia pengawasan | 84 bulan (7 tahun) | Sesuai anjuran EAPD (ringkasan sekunder). Setuju? |
| Rentang usia valid | 0-216 bulan (0-18 tahun) | Batas kewajaran tim teknis. Di luar rentang = galat. |
| Anjuran "cari 1000 ppm di kemasan" | pesan `look_for_ppm_on_pack` | Produk anak 500 ppm sangat umum. Apakah pesan ini boleh ditampilkan? |
| Buang busa, jangan banyak berkumur | pesan `spit_dont_swallow`, `rinse_little` | Redaksi dan kelayakan untuk anak kecil. |
| Daftar alergen & filter halal/SLS | `filters` | Kode alergen cukup? Produk dengan data tidak diketahui (`null`) dibuang bila filter aktif. |
| Tingkat risiko tinggi | pesan `high_risk_see_dentist` | Di v1 risiko tidak mengubah kadar ppm, hanya menambah anjuran periksa. |

Tidak ada dosis obat dan tidak ada nama merek di `rules.json`. Kandungan lain (CPP-ACP, stannous fluoride, KNO3, xylitol, dll.) belum diverifikasi dan **tidak** ada di v1.

### Contoh produk (`products.json`)
Isinya **placeholder sintetis** ("CONTOH-A (isi manual)"), bukan produk nyata. Pemilik produk menggantinya dengan produk asli: cek nomor notifikasi di cekbpom.pom.go.id, isi `verified: true`, `verified_at`, `source` (langkah lengkap ada di `how_to_fill` dalam berkasnya). Selama masih placeholder, `release_check` gagal.

---

# Developer notes (English)

## Layout
```
recommender/
  rules.json            clinician-readable rules (all needs_review/draft today)
  products.json         example products; brands live ONLY here
  schema/*.schema.json  JSON Schema 2020-12 for both files
  recommend.py          reference implementation (pure function, 100% line+branch tested)
  release_check.py      release gate
  build_bundle.py       writes dist/recommender.json + dist/recommender.json.sha256
  tests/                cases.json (shared table), pytest runners, hypothesis properties
  dist/                 generated bundle for the app (no Python needed)
```

## Commands
```bash
cd backend/recommender
uv sync
uv run pytest -q                        # enforces 100% line+branch coverage of recommend.py
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict
uv run python -m recommender.release_check   # EXPECTED to fail (exit 1) until a dentist signs off
uv run python -m recommender.build_bundle    # regenerate dist/ after editing rules/products
uv run python -m recommender.build_bundle --check
```
`test_dist_bundle_is_up_to_date_with_the_sources` fails when `dist/` is stale; rebuild it.

Review basis (ADR 015): optional top-level `review_basis` in `rules.json`. Absent or `dentist` = the original gate (`reviewed_by` not null, i.e. a dentist signs). `literature` = `reviewed_by` may be a team member (non-empty, not a placeholder such as `PENDING_DRG`/`PLACEHOLDER`/`null`) and every `source` (review rows and ppm candidates) must be a filled citation without `PERLU VERIFIKASI`. All other checks are the same. The shipped file has no `review_basis` yet (nothing is reviewed).
The forbidden-phrase lint reads `FORBIDDEN_PHRASES_PATH`, else `../docs/style/forbidden_phrases.md`, else the vendored `backend/docs/style/forbidden_phrases.md`.

## API
`recommend(age_months, allergies=[], prefs=None, risk_level=None, *, rules=None, products=None)`

* `age_months`: integer 0..216 (`rules.valid_age_months`); anything else raises `InvalidAgeError` (`code: "invalid_age"`). Booleans, fractions and strings are invalid.
* `allergies`: list of codes (`rules.filters.allergen_codes`); trimmed, lower-cased, de-duplicated. Unknown codes add reason `unknown_allergy:<code>` and the note `allergy_unverified`.
* `prefs`: `{halal?: bool, no_sls?: bool}`. `risk_level`: `low|moderate|high|null`. Bad values raise `InvalidInputError` (`code: "invalid_input"`).
* Output: `fluoride{band_id, ppm_options[{candidate_id,label,min,max,source}], decision_pending, amount{code,text}, supervision{required,until_age_months,text}, notes[{id,text}]}`, `disclaimer_ids`, `disclaimers`, `products[]` (sorted by `id`), `notes[]` (product notes, e.g. `fewer_than_two_products`), `status{needs_review, reasons[], generic_message}`, `rules_version`.
* `status.needs_review` is true whenever any row used is not `approved`, a ppm decision is pending, or a returned product is unverified. The data is still returned; the UI must then show `status.generic_message` conservatively and must not present ppm values as clinical advice.
* Product filters: allergy drops products that list the code OR whose `allergens` is `null`; `halal` requires `halal_certified === true`; `no_sls` requires `contains_sls === false`. An `sls` allergy implies `no_sls`. Fewer than `min_products` (2) matches: return what exists plus `fewer_than_two_products`; nothing is invented.
* When `selected_candidate_id` is set, only that candidate's range is returned; otherwise all candidates are returned (the conflict stays visible). Ranges are capped at `regulatory_ceiling_ppm`.

## Consuming `tests/cases.json` from TypeScript (Jest, `app/src/domain`)
1. Load `dist/recommender.json`; pass `bundle.rules` and `bundle.products` (or a case's catalogue) to the port of `recommend`.
2. For each entry in `cases`, build input as `{...input_defaults, ...case.input}`; the catalogue is `product_sets[case.product_set]` (`"shipped"` = `bundle.products`).
3. If `expect.error` is set, assert the call throws an error whose `code` equals it (`invalid_age` / `invalid_input`). Age validation is "typeof number, Number.isInteger, in range" (booleans, strings, null, fractions are invalid). Do not add JSON cases like `12.0`: Python and JS read it differently.
4. Otherwise assert only the keys present in `expect` (semantics are listed in `expect_keys` inside the file). `ppm_options` is a list of `[min, max]`; `product_ids` is exact and ordered; `*_include` / `*_exclude` are set checks.
5. Every non-error result must contain `toothpaste_not_filling` in `disclaimer_ids`.
6. Ordering must be reproduced: products sorted by `id` using code-unit string comparison; notes in the order given by the rules; `reasons` sorted.

The expectations are literal values for the DRAFT rules. When a dentist approves rules or changes the bands or ppm, update `cases.json` deliberately (not by snapshot), and bump `rules_version`.

## Rules for editing
* No thresholds in code: change `rules.json`, then the cases, then rebuild `dist/`.
* Never fill `reviewed_by` / `reviewed_at` on a dentist's behalf; `release_check` must pass only after real sign-off.
* Brands only in `products.json`. Do not add real BPOM numbers without checking cekbpom.pom.go.id.
* Output strings must avoid `docs/style/forbidden_phrases.md` phrases (substring match, so avoid words like "halaman", "pastikan", "keamanan" in strings the lint covers).
* Every change to rules or the bundle is a controlled change (ADR 002): record it in `docs/regulatory/CHANGELOG.md` and re-sign the manifest with `tools/sign_manifest.py`.
