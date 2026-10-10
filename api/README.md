# DentisAI ML/AI API (`backend/api`)

## Ringkasan (Bahasa Indonesia)

Layanan FastAPI ini **hanya untuk ML dan integrasi AI** (ADR 005, ADR 009):

| Endpoint | Fungsi | Peran |
|---|---|---|
| `GET /healthz`, `GET /readyz` | cek hidup / siap (tanpa data) | publik |
| `POST /v1/ml/review-queue` | memasukkan **foto dataset berizin** ke antrean review dokter gigi (hanya **referensi** `image_upload.id`, bukan foto) | `admin` |
| `GET /v1/ml/review-queue` | daftar antrean yang masih menunggu (paginasi kursor) | `admin` (peran reviewer) |
| `POST /v1/ml/review-queue/{id}/review` | drg mengirim hasil review: keputusan + kotak (format kanonik `ml/detection/canonical.py`), ID grader **pseudonim**, catatan ≤ 500 karakter | `admin` (peran reviewer) |
| `POST /v1/ml/models` | mendaftarkan versi model/aturan dari **manifes bertanda tangan Ed25519** — server **hanya memverifikasi**, tidak pernah menandatangani | `admin` |
| `POST /v1/ml/infer` | **opsional** (mati secara default, `ENABLE_CLOUD_INFER=false` → 404): second-opinion/QA; hanya `NullDetector`, tanpa bobot model | `admin` |
| `POST /v1/screening/evaluate` | **pipeline skrining referensi** (mati secara default, `ENABLE_SCREENING_API=false` → 404): triase + rekomendasi pasta gigi dari masukan perangkat (`{class, confidence}` saja), memakai mesin referensi `../triage` & `../recommender` | `guardian`, `kader`, `admin` |
| `POST /v1/screening/analyze` | QA atas foto dataset berizin (referensi saja): tanda bahaya dulu → consent → quality gate → detektor → triase; butuh juga `ENABLE_CLOUD_INFER` | `admin` |
| `GET /v1/screening/rules`, `/schema/input`, `/schema/output` | metadata aturan (`rules_status`, parameter `placeholder`) & JSON Schema triase | semua peran |

**Dokumentasi lengkap pipeline skrining (kontrak "aturan draf", contoh JSON hasil kode, kode error, alur): [`docs/API.md`](docs/API.md).** Selama `rules_status != approved` keluaran tidak boleh ditampilkan sebagai tervalidasi klinis.

**Yang tidak dilakukan, dan alasannya:**
- **Tidak ada CRUD dan tidak ada auth sendiri.** Auth, data anak, consent, dan Storage ada di Supabase (RLS adalah pertahanan utama). API ini hanya memverifikasi JWT Supabase dan membaca peran dari tabel `public.user_role` (sumber kebenaran skema), **tidak pernah** dari `user_metadata`.
- **Tidak menerima/menyimpan foto.** Antrean dan inferensi hanya menerima `image_upload.id`; foto tetap di bucket privat `dataset_photos`. Anotasi dilakukan di PC tim (CVAT/Label Studio lokal, ADR 005).
- **Gerbang consent:** flag `dataset_contribution` harus ON (default OFF sampai KEPK menyetujui → `409 dataset_contribution_disabled`), foto berstatus `uploaded` dan belum kedaluwarsa, consent (c) aktif. Trigger database mengecek ulang di dalam INSERT (aman dari race saat consent ditarik).
- **Chatbot tetap ditunda** (ADR 008). Semua keluaran deteksi = *indikasi skrining*, bukan diagnosis.
- Tidak ada peran `reviewer` di skema Supabase; drg reviewer memakai akun `admin` (lihat `app/deps.py`).

## Developer notes (English)

```bash
cd backend/api
uv sync
uv run pytest -q                      # tests + coverage gate (>= 90 %)
uv run ruff check . && uv run ruff format --check .
uv run mypy                           # --strict via pyproject
uv run python scripts/export_openapi.py   # regenerate openapi.json (a test fails if stale)
uv run python scripts/gen_api_examples.py # regenerate the JSON examples in docs/API.md (a test fails if stale)
uv sync --extra vision                    # runtime: numpy + Pillow for the /v1/screening/analyze quality gate
uv run --env-file ../.env uvicorn app.main:app --reload --no-access-log
```

Layout: `app/{main,settings,auth,deps,errors,middleware,ratelimit,logging_setup}.py`,
`app/routers/`, `app/schemas/`, `app/services/` (business rules, manifest verifier,
detector interface), `app/repositories/` (`tables.py` = every table/column name,
`supabase.py` = PostgREST/Storage over httpx with the service_role key,
`memory.py` = in-memory fake for tests). DB objects: `../supabase/migrations/20261009000100_ml_review_queue.sql`.

### Security decisions
- **JWT:** asymmetric Supabase signing keys via JWKS (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, ES256/RS256/EdDSA, cached, unknown-`kid` refetch throttled to 1/30 s) **and/or** legacy HS256 (`SUPABASE_JWT_SECRET`). `alg` allow-list is checked before key lookup (`none` and HS256-without-secret rejected → no key confusion); `exp` required; `aud=authenticated`; `iss=<SUPABASE_URL>/auth/v1`; `sub` must be a UUID; Postgres `role` claim must be `authenticated`; anonymous users never pass role checks.
- **Roles:** `API_ROLE_SOURCE=table` (default, `public.user_role`) or `app_metadata` (only if a custom access-token hook is added). `user_metadata` is never read.
- **service_role key** only in the repository layer, only in headers; legacy JWT keys also go into `Authorization`, new `sb_secret_*` keys only into `apikey`.
- **Errors:** `{"error": {"code", "message", "request_id"}}`; validation errors never echo input; unhandled exceptions → generic 500 without traceback (also not in logs).
- **Logs:** JSON lines, allow-listed fields only, route *templates* (never raw paths/queries), redaction of bearer tokens/JWTs/Supabase keys/storage paths/UUIDs; uvicorn access log disabled; httpx URL logging silenced. Audit trail goes to `public.audit_log` via `service_ml_audit` (ids HMAC-hashed in the DB).
- **Limits (defaults):** body ≤ 64 KiB (`API_MAX_BODY_BYTES`, 413); token bucket 60 req/min, burst 20 per client IP and separately per user (`API_RATE_LIMIT_PER_MINUTE`, `API_RATE_LIMIT_BURST`; in-memory, per replica; `/healthz` and `/readyz` exempt); `X-Forwarded-For` only honoured with `API_TRUST_FORWARDED_FOR=true` (set it behind a trusted ingress, otherwise every client shares the ingress IP bucket). Manifest ≤ 16 KiB, signature ≤ 512 B, ≤ 100 boxes, notes ≤ 500 chars, pagination ≤ 100.
- **Headers:** nosniff, `X-Frame-Options: DENY`, CSP `default-src 'none'`, `no-store`, HSTS, `Referrer-Policy: no-referrer`; no `Server` header. CORS closed unless `API_ALLOWED_ORIGINS` is set. `/docs` and `/openapi.json` are off unless `API_DOCS_ENABLED=true`.
- **Manifest verification** re-implements the `dentisai_manifest` verifier contract (DCJ-1 canonical JSON, Ed25519 verify-before-parse, closed schema v1, rollback against the highest registered `sequence`, test keys rejected when `API_ENV=production`). The tooling package is not importable (`tool.uv.package = false`, still in progress), so parity is enforced by running every vector in `../tools/manifest/vectors/` (`tests/test_manifest_vectors.py`). Trusted keys: `MANIFEST_PUBLIC_KEY_B64` (comma-separated), revocations: `MANIFEST_REVOKED_KEY_IDS`.

### Environment variables
Shared (`backend/.env.example`): `SUPABASE_URL`, `SUPABASE_ANON_KEY` (optional, sent to JWKS), `SUPABASE_SERVICE_ROLE_KEY`, **`SUPABASE_JWT_SECRET`** (new), **`ENABLE_CLOUD_INFER`** (new, default `false`), **`ENABLE_SCREENING_API`** (new, default `false`), **`API_ALLOWED_ORIGINS`** (new, empty), `MANIFEST_PUBLIC_KEY_B64`.
API-only, with safe defaults (not in `.env.example`): `API_ENV` (`development`|`production`), `API_JWKS_ENABLED` (true), `API_JWKS_CACHE_SECONDS` (600), `API_JWT_LEEWAY_SECONDS` (30), `API_ROLE_SOURCE` (`table`), `API_RATE_LIMIT_PER_MINUTE` (60), `API_RATE_LIMIT_BURST` (20), `API_MAX_BODY_BYTES` (65536), `API_TRUST_FORWARDED_FOR` (false), `API_DOCS_ENABLED` (false), `API_LOG_LEVEL` (INFO), `API_UPSTREAM_TIMEOUT_SECONDS` (10), `MANIFEST_REVOKED_KEY_IDS` (empty).

### Docker
`docker build -f backend/api/Dockerfile -t dentisai-api backend` — the build context is the **backend root** because the screening pipeline depends on the sibling reference engines (uv path dependencies `../triage`, `../recommender`, plus `ml/detection/quality_gate.py`). Multi-stage, `python:3.12-slim`, uv-locked deps (`--extra vision`), runs as uid 10001, no secrets in the image (`api/Dockerfile.dockerignore`, BuildKit, admits only the files the image needs; `.env*` never). Configuration is passed at run time (`--env-file`).

### Screening pipeline (`/v1/screening`, ADR 002 option 3, ADR 014)
Reuses `triage.engine.evaluate` and `recommender.recommend` as libraries (editable uv path dependencies) — no clinical logic in this service. Rule artefacts are checksum-verified at start-up (`triage/dist/SHA256SUMS`); `rules_status` comes from the engines' own release gates. Parity with the shared table `triage/tests/cases.json` is proven through HTTP (`tests/test_screening_parity.py`); request models are drift-checked against `triage/dist/input.schema.json` (`tests/test_screening_schema_drift.py`). Details: [`docs/API.md`](docs/API.md).

### Azure Container Apps (Japan) — deployment NOTES only (nothing is deployed; not on the MVP critical path)
- Region: Japan East/West (the owner's Azure for Students region). **Check first** that the Student subscription may create Container Apps there and the remaining credit (12 months, non-commercial, no GPU) — [PERLU VERIFIKASI].
- Consumption plan, `min-replicas 0` (scale to zero) to stay within the free grant (180k vCPU-s, 360k GiB-s, 2M requests/month per subscription [S]); expect cold starts. In-memory rate limits are per replica.
- Secrets (`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`) as Container Apps secrets / Key Vault references — never in the image or repo. Set `API_ENV=production`, `API_TRUST_FORWARDED_FOR=true` (ingress is the only path in), HTTPS-only ingress, probes on `/healthz` (liveness) and `/readyz` (readiness).
- **Data residency:** Supabase is in Singapore; calling it from Japan is an additional cross-border transfer. Only consented, pseudonymised references flow by default; `ENABLE_CLOUD_INFER=true` would move **photos** to Japan for the duration of a request — keep it off unless consent (c), KEPK approval and the legal review cover it (backend/CLAUDE.md §10, PRD §10).
- When the credit ends the API simply stops; the app does not depend on it (manifests are static files in Supabase Storage).
