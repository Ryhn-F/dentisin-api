# Publishing a signed manifest (manual, no secrets in the repo)

This is a checklist, not a script: it uses **your own** Supabase login and never stores keys.
The signing key stays offline (ADR 008); the `service_role` key is never needed for this.
All paths are relative to `backend/`.

## 0. Before you sign (change control, ADR 002)
- The release is recorded in `../docs/regulatory/CHANGELOG.md` (model/rules/recommender
  versions, evaluation report, dentist approval). Clinical content with `PLACEHOLDER`,
  `PENDING_DRG`, `DRAFT`/`PROPOSED`/`needs_review` status or `verified: false` is refused by the
  signer; `--allow-placeholder` produces an `internal-test` manifest that production builds reject.
- Pick `--sequence` = last published sequence + 1 (read it from the currently published
  manifest: `verify_manifest.py --manifest https://…/manifest.json --pubkey … ` prints it).
  Never reuse or lower a sequence.

## 1. Sign on the offline machine
```bash
cd backend
# key on removable/encrypted media, never inside backend/ or the monorepo
uv run --project tools/manifest python tools/sign_manifest.py sign \
  --key /media/offline/dentisai-manifest-k1.pem --prompt-passphrase \
  --model-dir ml/exports/0.3.1 \
  --base-url https://<project-ref>.supabase.co/storage/v1/object/public/manifests \
  --sequence 13 --min-app-version 1.0.0 --expires-in-days 180 \
  --out-dir ../release/manifest-13       # rules/recommender default to triage/dist, recommender/dist
```
Output: `manifest-13/` = exact bucket layout (`manifest.json`, `manifest.sig`,
`model/<v>/…`, `rules/<v>/rules.json`, `recommender/<v>/recommender.json`). Only the public key
and `key_id` are printed. Keep `--out-dir` outside the repo or delete it after upload (it is
public data, but artifacts must not be committed).

## 2. Verify exactly as the app will
```bash
uv run --project tools/manifest python tools/verify_manifest.py \
  --manifest ../release/manifest-13/manifest.json --artifacts-dir ../release/manifest-13 \
  --pubkey <K1 public b64> --pubkey <K2 public b64> --production \
  --last-sequence 12 --app-version 1.0.0
```
Exit 0 and `"ok": true` required.

## 3. Upload (artifacts first, manifest last)
Upload versioned artifact paths first, then `manifest.sig` and `manifest.json` together, so an
app never sees a manifest whose artifacts are not yet there (if it does, it fails safe with
`E_ARTIFACT_FETCH` and retries).

**Dashboard:** Supabase → Storage → bucket `manifests` (public, read-only for clients) → upload
the folders from `manifest-13/` keeping the same paths → upload `manifest.sig`, then
`manifest.json` (overwrite). Do not change file contents (no "format", no added newline).

**CLI (your own login, no stored keys)** — `supabase login` uses a browser/personal access token
kept by the CLI, not in this repo. `supabase storage` commands talk to the **linked** project:
```bash
supabase login && supabase link --project-ref <project-ref>
supabase storage cp -r ../release/manifest-13/model       ss:///manifests/model       --experimental
supabase storage cp -r ../release/manifest-13/rules       ss:///manifests/rules       --experimental
supabase storage cp -r ../release/manifest-13/recommender ss:///manifests/recommender --experimental
supabase storage cp ../release/manifest-13/manifest.sig  ss:///manifests/manifest.sig  --experimental
supabase storage cp ../release/manifest-13/manifest.json ss:///manifests/manifest.json --experimental
```
[PERLU VERIFIKASI] `supabase storage` flags/overwrite behaviour differ between CLI versions
(the command has been marked experimental); check `supabase storage cp --help` and prefer the
dashboard if unsure. Set `Cache-Control` short (e.g. 60 s) for `manifest.json`/`.sig` if the
upload UI offers it; versioned artifacts may be cached long.

## 4. Verify the live bucket
```bash
uv run --project tools/manifest python tools/verify_manifest.py \
  --manifest https://<project-ref>.supabase.co/storage/v1/object/public/manifests/manifest.json \
  --fetch --production --pubkey <K1> --pubkey <K2> --last-sequence 12
```

## 5. Afterwards
- Record `sequence`, `manifest_sha256`, `key_id` in the change log.
- Unmount the key media. Never copy the private key to CI, Supabase secrets, `.env`, or chat.
- Rollback = publish a **new** manifest (higher `sequence`) that points to the previous
  artifact versions; the app refuses lower sequences by design.
