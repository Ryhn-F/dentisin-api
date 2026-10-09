# DentisAI signed manifest — SPEC v1 (FR-101)

Status: **v1, normative.** Reference implementation: `backend/tools/manifest/src/dentisai_manifest/`
(Python). The app (Expo/TypeScript) MUST behave identically; parity is checked with the test
vectors in `vectors/` (section 10). Key words MUST/MUST NOT/SHOULD per RFC 2119.

Purpose: the app downloads `manifest.json` + `manifest.sig` from the public Supabase Storage
bucket `manifests` and uses a model/rules/recommender release **only** if the Ed25519 signature,
the manifest format/policy checks and every artifact's size + SHA-256 pass. Anything else:
reject, keep the last-good release, never apply partially.

## 1. Files

| File | Content | Max size |
|---|---|---|
| `manifest.json` | DCJ-1 canonical JSON (section 3) of the manifest object (section 2). Exactly these bytes are signed | 16 384 bytes |
| `manifest.sig` | DCJ-1 canonical JSON `{"alg":"Ed25519","key_id":"<16 hex>","sig":"<base64>"}` | 512 bytes |
| artifacts | `model.tflite`, `labels.json`, triage `rules.json`, `recommender.json` at the URLs in the manifest | per kind, section 2.3 |

Bucket object layout written by the signer (mirrors `--out-dir`):
`manifest.json`, `manifest.sig`, `model/<v>/model.tflite`, `model/<v>/labels.json`,
`rules/<v>/rules.json`, `recommender/<v>/recommender.json`. Versioned paths are immutable: a new
release uploads new paths and then replaces `manifest.json` + `manifest.sig` last.

## 2. Manifest schema v1 (closed: unknown fields are rejected)

### 2.1 Top level
| Field | Type | Rule |
|---|---|---|
| `schema_version` | int | `1`. Unknown value → `E_SCHEMA_VERSION_UNSUPPORTED`; lower than last-seen → `E_SCHEMA_DOWNGRADE` |
| `sequence` | int | 1 … 2^53−1. Monotonic per publication (rollback protection, section 5 step 11) |
| `created_at` | string | `YYYY-MM-DDTHH:MM:SSZ` (RFC 3339 subset: UTC `Z`, no fraction, no offset, valid date) |
| `expires_at` | string, **optional** | same format, strictly later than `created_at` |
| `min_app_version` | string | `MAJOR.MINOR.PATCH`, digits only, no leading zeros, each ≤ 9 digits; compared numerically |
| `signer_key_id` | string | 16 lowercase hex; MUST equal `manifest.sig` `key_id` (`E_KEY_ID_MISMATCH`) |
| `channel` | string | `"production"` or `"internal-test"` |
| `model` | object | 2.2 |
| `rules` | object | 2.2 |
| `recommender` | object | 2.2 |

### 2.2 Components
| Component | Fields (all required) |
|---|---|
| `model` | `version`, `license`, `url`, `sha256`, `size_bytes`, `labels_url`, `labels_sha256`, `labels_size_bytes` |
| `rules` | `version`, `url`, `sha256`, `size_bytes` |
| `recommender` | `version`, `url`, `sha256`, `size_bytes` |

- `version`: `^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$` (opaque label; not compared).
- `license` (model only): 1–256 printable ASCII; SPDX expression, e.g. `Apache-2.0 AND CC-BY-4.0`.
- `*url`: `^https://[a-z0-9.-]{1,253}(:[0-9]{1,5})?/[A-Za-z0-9._~%/+-]{1,1900}$`, and no empty,
  `.` or `..` path segment (so no query, fragment, userinfo, uppercase host or trailing `/`).
- `*sha256`: 64 lowercase hex of the artifact bytes.
- `*size_bytes`: int, 1 … cap.

### 2.3 Artifact caps (also enforced while downloading)
| Artifact id | Field prefix | Cap |
|---|---|---|
| `model` | `model.` | 50 MiB (52 428 800) |
| `model.labels` | `model.labels_` | 1 MiB |
| `rules` | `rules.` | 2 MiB |
| `recommender` | `recommender.` | 2 MiB |

## 3. Canonicalization: DCJ-1 (strict subset of RFC 8785 JCS)

The signature covers the **exact bytes of `manifest.json`**, and those bytes MUST be the DCJ-1
canonical form. For every accepted value, DCJ-1 output is byte-identical to RFC 8785 and to
`JSON.stringify` of an object whose keys were inserted in sorted order.

Value space (anything else → `E_NOT_CANONICAL`):
1. Whole file: bytes 0x20–0x7E only (printable ASCII). So: no BOM, no newline/tab, no non-ASCII.
2. Object keys: `^[a-z][a-z0-9_]{0,63}$`, unique, emitted in ascending byte order
   (equals UTF-16 code-unit order for ASCII). This also excludes `__proto__`.
3. Strings: characters U+0020–U+007E only. Exactly two escapes exist: `"` → `\"`, `\` → `\\`.
   Any other escape in the input (`\/`, `\n`, `A`, `\ud800`, …) is non-canonical.
4. Numbers: integers only, −(2^53−1) … 2^53−1, shortest decimal (`0`, `-5`, `42`). Rejected:
   `1.0`, `0.5`, `1e2`, `-0`, leading zeros, `NaN`, `Infinity`, 2^53 and beyond.
5. `true`, `false`, `null`, arrays.
6. No insignificant whitespace; separators are exactly `,` and `:`. No trailing newline.
7. Nesting depth ≤ 16 (top-level value is depth 1).
8. Duplicate keys are rejected.

**Rule for re-serialized manifests (tested):** a semantically equal but differently serialized
`manifest.json` (reordered keys, pretty-printed, trailing newline) does **not** verify with the
original signature (`E_SIGNATURE`), and if someone signs those bytes directly it is rejected as
`E_NOT_CANONICAL`. Re-canonicalizing it reproduces the original bytes, which verify again.

### 3.1 TypeScript reference (illustrative, **not executed in this repo**; vectors are the contract)
```ts
const KEY = /^[a-z][a-z0-9_]{0,63}$/;
function canon(v: unknown, depth = 1): string {
  if (depth > 16) throw new Error("E_NOT_CANONICAL");
  if (v === null || v === true || v === false) return String(v);
  if (typeof v === "number") {
    if (!Number.isSafeInteger(v) || Object.is(v, -0)) throw new Error("E_NOT_CANONICAL");
    return String(v);
  }
  if (typeof v === "string") {
    for (let i = 0; i < v.length; i++) {
      const c = v.charCodeAt(i);
      if (c < 0x20 || c > 0x7e) throw new Error("E_NOT_CANONICAL"); // also rejects surrogates
    }
    return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }
  if (Array.isArray(v)) return "[" + v.map((x) => canon(x, depth + 1)).join(",") + "]";
  if (typeof v === "object") {
    const keys = Object.keys(v as object);
    for (const k of keys) if (!KEY.test(k)) throw new Error("E_NOT_CANONICAL");
    keys.sort(); // ASCII-only keys: default sort == code-unit order
    return "{" + keys.map((k) => `"${k}":` + canon((v as any)[k], depth + 1)).join(",") + "}";
  }
  throw new Error("E_NOT_CANONICAL");
}
// parseCanonical(raw: Uint8Array): every byte 0x20..0x7E, then
// v = JSON.parse(ascii(raw)); require bytesEqual(ascii(canon(v)), raw).
```
Why the explicit checks: `JSON.parse` silently accepts duplicates (last wins), floats and
`"\ud800"`. Duplicates, `1.0`, `1e2`, `-0`, whitespace and order are caught by the byte
comparison; `0.5`, `2^53` and lone-surrogate escapes re-serialize identically, so `canon` MUST
reject them itself. All of these have vectors in `vectors/canonicalization.json`.

## 4. Keys and `manifest.sig`

- Algorithm: Ed25519 (RFC 8032), pure, no context/prehash. Signed message = raw `manifest.json` bytes.
- Verification MUST be strict RFC 8032: reject non-canonical `S` (S ≥ L). With
  `@noble/ed25519`/`@noble/curves` use `{ zip215: false }`. Vector "non-canonical signature
  scalar S+L" must be rejected.
- `key_id` = lowercase hex of the first 8 bytes of SHA-256(raw 32-byte public key).
- `manifest.sig` = DCJ-1 canonical `{"alg":"Ed25519","key_id":…,"sig":…}`, exactly those three
  keys; `sig` = standard base64 **with padding** (RFC 4648 §4, 88 chars) of the 64-byte signature
  and must round-trip (non-canonical base64 → `E_SIG_FORMAT`).
- Public keys are distributed as standard base64 of the raw 32 bytes.

## 5. Verification algorithm (order is normative; first failure wins)

Inputs: raw manifest bytes, raw sig bytes, trusted keys `{key_id → pubkey}` embedded in the app,
revoked `key_id`s, known TEST key ids, policy state `{now, production, last_sequence,
last_manifest_sha256, last_schema_version, app_version}`.

1. Size caps: manifest ≤ 16 384, sig ≤ 512 bytes; when downloading, stop reading at cap+1 → `E_TOO_LARGE`.
2. Parse `manifest.sig` (section 4) → `E_SIG_FORMAT`.
3. Key selection: `key_id` revoked → `E_KEY_REVOKED`; production and `key_id` ∈ TEST ids → `E_KEY_TEST_ONLY`; not trusted → `E_KEY_UNKNOWN`.
4. Ed25519 verify over the **raw received bytes, before any JSON parsing** → `E_SIGNATURE`.
5. DCJ-1 canonical check (section 3) → `E_NOT_CANONICAL`.
6. `schema_version` < `last_schema_version` → `E_SCHEMA_DOWNGRADE`.
7. Schema (section 2): unsupported version → `E_SCHEMA_VERSION_UNSUPPORTED`; else `E_SCHEMA`.
8. `signer_key_id` ≠ sig `key_id` → `E_KEY_ID_MISMATCH`.
9. Production only: `channel` ≠ `production` → `E_CHANNEL`; any string containing `PLACEHOLDER` → `E_PLACEHOLDER`.
10. `expires_at` present and `now ≥ expires_at` → `E_EXPIRED`.
11. Rollback: `sequence < last_sequence` → `E_ROLLBACK`; `sequence == last_sequence` and
    SHA-256(manifest bytes) ≠ `last_manifest_sha256` → `E_ROLLBACK` (equivocation). Equal
    sequence + equal bytes = no-op (nothing new to apply).
12. `app_version < min_app_version` (numeric tuple compare) → `E_APP_TOO_OLD` (keep last-good; prompt app update).
13. Artifacts, for each in section 2.3 order: download over https only (redirects to non-https
    fail), abort when `Content-Length` > `size_bytes`, read at most `size_bytes + 1` bytes;
    size ≠ `size_bytes` → `E_ARTIFACT_SIZE`; SHA-256 ≠ `sha256` → `E_ARTIFACT_HASH`;
    network/timeout → `E_ARTIFACT_FETCH`; (CLI local mirror) missing → `E_ARTIFACT_MISSING`.

`created_at` is not compared with the device clock (cheap phones often have wrong clocks); a wrong
clock can only cause `E_EXPIRED`, which fails safe (last-good stays active).

### 5.1 Applying (app)
- Download all artifacts into a staging dir; verify **all**; only then atomically switch the
  "active release" pointer (single SQLite transaction / rename) and persist
  `{last_sequence, last_manifest_sha256, last_schema_version, manifest bytes, sig bytes}`.
- Any failure: delete staging, keep the last-good release (or the bundled one), log the error
  code only (no PII), retry later with backoff. Never apply a subset of artifacts.
- Re-verify stored artifacts' SHA-256 at load time (cheap, catches local corruption).
- The app ships a **bundled** release + its manifest; it is the floor (`last_sequence` starts at
  the bundled manifest's `sequence`).

## 6. Keys, rotation, revocation (private keys are offline; ADR 008: no Key Vault)

- Generate with `sign_manifest.py keygen --out <path outside backend/ and the monorepo>`
  (refuses inside either tree or any git work tree, refuses to overwrite, file mode 0600 on
  POSIX; prints only `key_id` + public key). Encrypt with `--prompt-passphrase`.
- Generate **two** keys at setup: primary K1 and backup K2, stored on separate offline media /
  password manager, each with an encrypted backup. The app embeds **both** public keys from day 1.
- What the app embeds (build-time constant, change-controlled): `TRUSTED_KEYS {key_id → base64}`,
  `REVOKED_KEY_IDS`, `TEST_KEY_IDS = ["965ba6b27d5ca7a7","e12ef5f599e59801"]` (always rejected
  in production builds), `SUPPORTED_SCHEMA_VERSIONS = [1]`, the caps in section 2.3, the bucket
  base URL, and the bundled release (artifacts + manifest + sig).
- Lost K1: sign with K2; no app update needed. Rotate in a new K3 via the next app release.
- Compromised K1: sign with K2 and ship an app release with K1 in `REVOKED_KEY_IDS`. On startup,
  if the active manifest's signer is revoked, discard it, fall back to the newest stored
  manifest signed by a non-revoked key (else the bundled release) and reset `last_sequence` to
  that manifest's sequence — otherwise an attacker could have burned `sequence` up to 2^53−1.
  Until the app update lands, a K1 holder can publish manifests the app accepts: treat key
  compromise as an incident (ADR 002 change log, UU PDP assessment).
- Revocation is only via app update in v1 (no online revocation list), by design: an offline key
  cannot sign a fresh revocation list quickly, and an unsigned list is an attack surface.

## 7. Signer rules (`sign_manifest.py`)

### 7.1 Inputs (defaults relative to `backend/`)
`--model-dir ml/exports/<semver>` (required; needs `model.tflite`, `labels.json`, `SHA256` whose
entries must match; `labels.json` `model.version`, if present, must equal the dir name),
`--rules` (default `triage/dist/rules.json`), `--recommender` (default
`recommender/dist/recommender.json`). A `SHA256SUMS` next to rules/recommender (written by their
exporters) must agree with the file. Versions come from the JSON top-level `version` or
`--rules-version/--recommender-version`. Model license: `--model-license`, else
`labels.json` `license` (string) or `licenses.{code,weights,datasets[]}.license` joined with ` AND `.

### 7.2 Placeholder / unreviewed gate
Refuse (exit 7) when any artifact text contains `PLACEHOLDER` or `PENDING_DRG`, or a JSON
artifact has `license_verified: false`, `verified: false`, or `status` ∈ {`needs_review`,
`DRAFT`, `PROPOSED`, `PLACEHOLDER`}. `--allow-placeholder` signs anyway but stamps
`channel: "internal-test"`, which production verifiers reject (`E_CHANNEL`). The public TEST key
is refused for production signing.

## 8. Error codes and CLI exit codes

| Exit | Codes |
|---|---|
| 0 | OK |
| 1 | `E_IO` |
| 2 | `E_USAGE` (also argparse errors) |
| 3 | `E_SIG_FORMAT`, `E_KEY_UNKNOWN`, `E_KEY_REVOKED`, `E_KEY_TEST_ONLY`, `E_SIGNATURE` |
| 4 | `E_TOO_LARGE`, `E_NOT_CANONICAL`, `E_SCHEMA`, `E_SCHEMA_VERSION_UNSUPPORTED`, `E_KEY_ID_MISMATCH` |
| 5 | `E_SCHEMA_DOWNGRADE`, `E_EXPIRED`, `E_ROLLBACK`, `E_APP_TOO_OLD`, `E_CHANNEL`, `E_PLACEHOLDER` |
| 6 | `E_ARTIFACT_MISSING`, `E_ARTIFACT_SIZE`, `E_ARTIFACT_HASH`, `E_ARTIFACT_FETCH` |
| 7 | `E_REFUSED` (signer only) |

`verify_manifest.py` prints one JSON object: `{ok, exit_code, error:{code,message}|null,
manifest:{summary}|null, manifest_sha256, artifacts:[{name,url,source,ok,code,message,
expected_sha256,actual_sha256,expected_size,actual_size}]}`.

## 9. Configuration
Env vars (CLI flags override; if unset, the same names are read from `backend/.env`; values are
never printed): `MANIFEST_SIGNING_KEY_PATH`, `MANIFEST_PUBLIC_KEY_B64` (comma-separated list
allowed), `MANIFEST_BASE_URL`. `MANIFEST_SIGNING_KEY_PASSPHRASE` is read from the environment
only (never from `.env`); prefer `--prompt-passphrase`.

## 10. Test vectors (`vectors/`, TEST-ONLY keys)

Keys: seed = SHA-256 of a public ASCII label (in each file), so they are **not secret** and
MUST NOT be trusted by production builds. Signing key `key_id 965ba6b27d5ca7a7`, public
`X9Inwg6D9nOwQZiwlebpFeuSGf7y003JF4UbArectsE=`; second key `e12ef5f599e59801`.

| File | Content |
|---|---|
| `manifest_v1_valid_01_minimal.json` | production, no expiry |
| `manifest_v1_valid_02_internal_test_expiring.json` | internal-test, `expires_at`, PLACEHOLDER license |
| `manifest_v1_valid_03_escapes_and_semver.json` | `\"`/`\\` escapes, `min_app_version 10.2.3`, sequence 2^53−1 |
| `manifest_v1_invalid.json` | 17 rejected manifests/sigs with the expected code |
| `canonicalization.json` | `canonicalize` input→output and `parse_canonical` bytes→OK/`E_NOT_CANONICAL` |

Each valid vector has `manifest_object_unsorted` (canonicalize it → `canonical_hex`),
`manifest_json` (exact file bytes), `manifest_sig`, `signature_hex`, and `cases` (policy →
expected `OK`/code). Regenerate: `PYTHONPATH=src uv run python -m dentisai_manifest.vectors`;
`tests/test_vectors.py` fails on drift. Because production policy always rejects TEST keys, the
production-only checks (`E_CHANNEL`, `E_PLACEHOLDER`) are exercised in TS with ephemeral keys.
