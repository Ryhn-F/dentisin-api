# tools/ — perkakas operasi DentisAI

Skrip untuk admin/PC tim. **Tidak ada rahasia di berkas apa pun.** Skrip membaca variabel lingkungan; bila belum disetel, `backup_db.sh`, `export_dataset.py`, dan `tts/generate_audio.py` membaca `backend/.env` (hanya variabel yang dibutuhkan; nilai tidak pernah dicetak). `.env` diisi sendiri oleh pemilik dan tidak boleh di-commit.

**Tata letak.** Folder `backend/` (BACKEND_ROOT) berdiri sendiri dan bisa menjadi repo sendiri; semua jalur di dokumen ini relatif terhadapnya dan tidak ada asumsi tentang folder induk. Folder keluaran backup/ekspor ditolak bila berada di dalam `backend/`, di dalam repo git yang membungkusnya, atau di dalam root monorepo (induk yang punya `CLAUDE.md`).

**GitHub Actions.** GitHub hanya membaca `.github/workflows/` di root repo. (a) Jika `backend/` adalah repo sendiri: pakai `backend/.github/` apa adanya. (b) Jika `backend/` subfolder monorepo: salin `ci.yml` dan `keepalive.yml` ke `.github/workflows/` di root monorepo dan ubah `env: BACKEND_DIR: ""` menjadi `"backend/"` (satu tempat per berkas; semua jalur, `hashFiles`, dan `working-directory` mengikutinya). Secret keep-alive tetap diatur di repo yang menjalankan workflow.

```bash
cd tools && uv sync && uv run pytest -q        # semua tes (stub/mock; tanpa jaringan)
uv run ruff check . && uv run mypy .
```

| Variabel | Dipakai oleh | Catatan |
|---|---|---|
| `SUPABASE_URL` | export_dataset | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | export_dataset | **Hanya PC tim.** Jangan di app, CI publik, atau log |
| `SUPABASE_DB_URL` | backup_db.sh | connection string Postgres |
| `BACKUP_AGE_RECIPIENT` | backup_db.sh | kunci **publik** age (`age1…`) |
| `BACKUP_OUTPUT_DIR` | backup_db.sh | folder yang sudah ada, **di luar repo** |
| `KEEPALIVE_URL` | keepalive/ping.py (GitHub secret) | `…/rest/v1/rpc/keepalive` |
| `SUPABASE_ANON_KEY` | keepalive/ping.py (GitHub secret) | **anon saja**; kunci service_role ditolak |
| `GEMINI_API_KEY`, `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE` (opsional `GEMINI_TTS_STYLE`) | tts/generate_audio.py (bawaan, ADR 012) | kunci dari akun Google AI Studio khusus proyek |
| `TTS_PROVIDER` | tts (opsional) | `gemini` (bawaan) atau `azure`; `--provider` menang |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_SPEECH_VOICE` | tts/generate_audio.py (cadangan, `--provider azure`) | region `japaneast`/`japanwest` |
| `DATASET_EXPORT_DIR` | export_dataset | folder ada, di luar repo, disk terenkripsi |
| `FORBIDDEN_PHRASES_PATH` | lint, tts (opsional, **variabel baru**) | menimpa lokasi daftar frasa terlarang |
| `TTS_SCRIPT_PATH` | tts (opsional, **variabel baru**) | naskah CSV, mis. `../app/assets/audio/script.csv` |

---

## manifest (owned by manifest engineer)

*(Bagian ini milik engineer manifes: `sign_manifest.py`, `verify_manifest.py`, `manifest/`. Bagian lain milik ops-tooling.)*

Manifes model/aturan bertanda tangan **Ed25519** (FR-101). Kontrak lengkap untuk engineer app (TypeScript): **[`manifest/SPEC.md`](manifest/SPEC.md)**; vektor uji paritas: `manifest/vectors/`; prosedur unggah: [`manifest/publish.md`](manifest/publish.md). Proyek uv terpisah (`manifest/pyproject.toml`, dependensi `cryptography` Apache-2.0/BSD).

```bash
cd tools/manifest && uv sync && uv run pytest --cov      # tes (kunci sementara di tmp; tanpa jaringan)
uv run ruff check src tests && uv run mypy --strict
# dari backend/:
uv run --project tools/manifest python tools/sign_manifest.py keygen --out <di LUAR repo>/k1.pem --prompt-passphrase
uv run --project tools/manifest python tools/sign_manifest.py sign --key <offline>/k1.pem --prompt-passphrase \
    --model-dir ml/exports/<semver> --sequence <n+1> --min-app-version 1.0.0 --out-dir <di luar repo>
uv run --project tools/manifest python tools/verify_manifest.py --manifest <dir>/manifest.json --artifacts-dir <dir> --production
```

- **Kanonikalisasi DCJ-1** (subset RFC 8785): byte `manifest.json` = JSON kanonik (kunci urut, tanpa spasi, hanya ASCII cetak, hanya bilangan bulat aman). Tanda tangan atas byte persis; manifes yang diformat ulang **ditolak**.
- `sign`: `--rules` bawaan `triage/dist/rules.json`, `--recommender` bawaan `recommender/dist/recommender.json`; menolak bila `SHA256`/`labels.json` hilang, checksum tidak cocok, atau ada `PLACEHOLDER`/`PENDING_DRG`/`status: DRAFT|PROPOSED|needs_review`/`verified: false`. `--allow-placeholder` → `channel: internal-test` (ditolak `verify --production`).
- `keygen` menolak menulis di dalam `backend/`, monorepo induknya, atau work tree git mana pun, dan tidak menimpa berkas; hanya mencetak `key_id` + kunci publik.
- Variabel: `MANIFEST_SIGNING_KEY_PATH`, `MANIFEST_PUBLIC_KEY_B64` (boleh dipisah koma), `MANIFEST_BASE_URL` — dari lingkungan, atau `backend/.env` bila kosong (nilai tidak pernah dicetak). Passphrase hanya lewat `--prompt-passphrase` atau env `MANIFEST_SIGNING_KEY_PASSPHRASE` (tidak dibaca dari `.env`).
- Kode keluar: `0` ok · `1` I/O · `2` pemakaian · `3` tanda tangan/kunci · `4` format/skema · `5` kebijakan (kedaluwarsa, rollback, downgrade, channel, versi app) · `6` artefak · `7` penandatangan menolak. `verify` selalu mencetak satu objek JSON.

---

## contracts/ — bundel kontrak untuk repo app (item 4.3)
Satu zip berversi berisi artefak + vektor paritas yang dipakai app (daftar: `contracts/contracts.json`). Stdlib saja.
```bash
# dari backend/
python tools/contracts/build_contracts.py build                 # dari HEAD (objek git) -> build/contracts/
python tools/contracts/build_contracts.py build --worktree      # dev, ditandai -dirty, jangan dirilis
python tools/contracts/build_contracts.py verify build/contracts/dentisai-contracts-0.1.0.zip
```
Rilis: naikkan `bundle_version`, commit, `git tag contracts-v<versi> && git push origin contracts-v<versi>` → workflow `contracts-release.yml`. Build gagal bila `SHA256SUMS`/`.sha256` tidak cocok dengan berkas (jalankan ulang exporter triase/rekomendasi). Kontrak untuk app: `docs/api/CLIENT_CONTRACTS.md` §7. Tes: `tests/test_contracts.py`.

## backup_db.sh — cadangan database terenkripsi

Supabase Free **tidak punya backup otomatis**. `bash tools/backup_db.sh` menjalankan `pg_dump` (format custom) lalu langsung mengenkripsinya dengan `age` ke kunci **publik** penerima. Hasil: `BACKUP_OUTPUT_DIR/dentisai-<UTC>.dump.age` (+ `.sha256`).

```bash
bash tools/backup_db.sh --keep 8        # simpan 8 backup terbaru, hapus yang lebih lama
bash tools/backup_db.sh --check-size    # hanya cek ukuran DB vs batas 500 MB (peringatan >=400, kritis >=475)
```

Pengaman: menolak bila folder keluaran ada di dalam repo / tidak ada; menolak bila `age`/`pg_dump` tidak ada; menolak bila `BACKUP_AGE_RECIPIENT` ternyata kunci **privat**; tidak menulis data ke stdout; berkas parsial dihapus bila gagal; memverifikasi header age (dekripsi penuh butuh kunci privat yang tidak pernah disentuh skrip). Kode keluar: `0` ok · `2` konfigurasi · `3` alat kurang · `4` folder tidak aman · `5` dump/enkripsi gagal · `6` verifikasi gagal.

Catatan: URL koneksi terlihat di daftar proses selama `pg_dump` berjalan, jadi jalankan hanya di komputer pribadi. Storage tidak ikut backup (lihat pemberitahuan yang dicetak skrip). **Runbook uji pemulihan + penjadwalan: [`restore_check.md`](restore_check.md).**

## keepalive — mencegah proyek Free dijeda

`.github/workflows/keepalive.yml` berjalan tiap 3 hari (`17 3 */3 * *`, UTC) dan menjalankan `tools/keepalive/ping.py` (hanya pustaka standar Python). Skrip mengirim satu request ringan ke `KEEPALIVE_URL`; **status HTTP 2xx = sukses**, 4xx tidak diulang, 5xx/jaringan diulang 3x. URL dan kunci tidak pernah dicetak.

**Secret GitHub** (Settings → Secrets and variables → Actions):

| Secret | Isi |
|---|---|
| `KEEPALIVE_URL` | mis. `https://<ref>.supabase.co/rest/v1/rpc/keepalive` (RPC `keepalive` dibuat engineer Supabase; skrip tidak bergantung padanya, tetapi RPC yang belum ada menghasilkan 404 = gagal) |
| `SUPABASE_ANON_KEY` | kunci **anon/publishable saja**. Kunci `service_role` / `sb_secret_…` ditolak oleh `ping.py` dan ditandai oleh job `secrets-scan` |

Uji lokal: `KEEPALIVE_URL=… SUPABASE_ANON_KEY=… python tools/keepalive/ping.py` (kode keluar `0` ok, `1` gagal, `2` konfigurasi). Proyek dev yang juga bisa dijeda butuh workflow/secret terpisah (tambahkan entri matrix bila perlu).

**[PERLU VERIFIKASI]**
1. Apakah `pg_cron` di dalam database mencegah penjedaan? Diperkirakan **tidak** (aktivitas internal tidak dihitung dan tidak dapat membangunkan proyek yang sudah dijeda) — karena itu dipakai ping eksternal. Uji sebelum bergantung pada keep-alive internal (backend/CLAUDE.md §15 no. 7).
2. Apakah request REST/RPC dari anon key dihitung sebagai "aktivitas" oleh Supabase? Uji: biarkan satu proyek dev hanya diping > 7 hari dan pastikan tidak dijeda.
3. GitHub menonaktifkan workflow terjadwal pada repo **publik** tanpa aktivitas 60 hari; repo ini tertutup, tetapi periksa tab Actions bila ada email kegagalan.
4. Jadwal cron GitHub bisa tertunda beberapa menit; selisih terbesar antar-ping 3 hari, jauh di bawah 7 hari.

## export_dataset.py — ekspor foto berizin (khusus PC tim)

Alat admin (kunci `service_role`) untuk menarik foto dari bucket privat `dataset_photos` ke PC tim, untuk anotasi lokal (CVAT/Label Studio). **Bukan** untuk Kaggle/Colab.

```bash
uv run python export_dataset.py --i-have-ethics-approval              # dry-run (bawaan): hanya hitungan
uv run python export_dataset.py --i-have-ethics-approval --execute    # unduh
```

- Menolak jalan tanpa `--i-have-ethics-approval` **dan** flag `dataset_contribution` aktif (dibaca langsung dari tabel `feature_flag`).
- Menolak bila `DATASET_EXPORT_DIR` kosong, tidak ada, atau di dalam repo.
- Hanya foto dari pemilik dengan consent (c) **aktif dan tidak ditarik**, dan baris unggahannya sendiri tidak ditandai ditarik. Bila tidak bisa memverifikasi consent (galat API), tidak ada yang diekspor.
- Berkas disimpan sebagai `<uuid-acak>.jpg` tanpa perubahan isi; `manifest.csv`: `uuid, sha256, bytes, consent_version, exported_at` (**tanpa user id/email**). Foto yang masih memuat EXIF/XMP dilewati (`--allow-metadata` untuk memasukkannya).
- Tidak mengenkripsi apa pun: simpan di **disk terenkripsi** (BitLocker/VeraCrypt), jangan disinkronkan ke cloud.
- Keterbatasan: karena manifes sengaja tanpa user id, foto yang sudah terekspor tidak bisa dilacak bila pemiliknya menarik consent. Ekspor ulang berkala ke folder **baru** dan ganti set lama (mengikuti "keluarkan dari pelatihan berikutnya"). Egress Free 5 GB/bulan: ±2.000 foto ≈ 1 GB.
- Disarankan (belum otomatis): catat tiap ekspor massal di `audit_log` lewat SQL admin.

**Asumsi skema** (dari PRD §9.3 / backend/CLAUDE.md §6; ubah di blok `SCHEMA` pada kepala berkas bila migrasi berbeda): `feature_flag(key, enabled)`; `consent(guardian_id, kind 'a'|'b'|'c', text_version, granted_at?, withdrawn_at? | withdrawn?)`; `image_upload(guardian_id, storage_path, withdrawn_at? | withdrawn?)`; bucket `dataset_photos`. Tanpa `granted_at`, pemilik dengan **satu saja** baris (c) yang ditarik dikeluarkan (gagal-aman).

## tts/ — suara instruksi

Lihat [`tts/README.md`](tts/README.md). Ringkas: `uv run python tts/generate_audio.py --generated-under free|paid [--provider gemini|azure] [--dry-run] [--script ../app/assets/audio/script.csv]` (Gemini bawaan, ADR 012; `azure` memakai `student|payg`); isi `GEMINI_API_KEY`/`GEMINI_TTS_MODEL`/`GEMINI_TTS_VOICE` di `backend/.env` (dikosongkan di `.env.example`, jangan di-commit); naskah produksi ada di app (di luar `backend/`) sehingga jalurnya lewat `--script` atau `TTS_SCRIPT_PATH` (bawaan: `tts/script.sample.csv`, perlu `--allow-sample`); hanya teks naskah statis yang disetujui yang dikirim, tanpa data anak; **risiko syarat Google (aplikasi anak/medis) belum dikonfirmasi: minta konfirmasi tertulis atau ganti provider sebelum rilis publik/komersial**; tanpa TTS saat runtime; label "suara dibuat dengan AI" wajib di layar Tentang.

## lint_forbidden_phrases.py — frasa terlarang

Sumber daftar (urutan): env `FORBIDDEN_PHRASES_PATH` → salinan bersama `backend/../docs/style/forbidden_phrases.md` bila ada → salinan vendor `backend/docs/style/forbidden_phrases.md`. **Bila salinan bersama dan vendor sama-sama ada tetapi berbeda, lint gagal (kode 3)**; `--check-sync` hanya menjalankan pemeriksaan ini (dipakai juga di CI). Tabel di-parse langsung ( frasa dalam tanda kutip, `…` = ekor bebas, `XX` = angka, huruf besar/kecil diabaikan, batas kata sehingga `keamanan`/`amankan` tidak terkena `aman`).

```bash
python tools/lint_forbidden_phrases.py                       # triage/, recommender/, supabase/seed, tools/tts
python tools/lint_forbidden_phrases.py --check-sync          # salinan bersama vs vendor
python tools/lint_forbidden_phrases.py path/a.json app/src --ext json,ts --json
python tools/lint_forbidden_phrases.py --list-rules
```

- Bawaan: `*.json *.yaml *.yml *.csv *.sql *.txt`; folder `docs/` (termasuk `docs/regulatory`, yang mengutip frasa terlarang) dan `*.md` dilewati kecuali dipilih eksplisit. Path bawaan yang belum ada dilewati. Keluar `1` bila ada pelanggaran (`file:baris:kolom`), `2` bila galat konfigurasi.
- `bebas karies` boleh bila ada "bukan diagnosis…/tidak menggantikan pemeriksaan dokter gigi" dalam ±2 baris. Negasi aman dikecualikan secara heuristik: `bukan diagnosis`, `belum/tidak pasti`, `belum/tidak [dapat] dipastikan`, `tidak/kurang/belum aman`.
- Satu baris bisa dikecualikan dengan komentar `forbidden-phrases:ignore`.
- **Tidak dapat dimekanisasi** (dicetak setiap run): dosis obat untuk anak, netralitas merek (perlu cek `recommender/products.json`), keberadaan teks wajib (disclaimer hasil & pasta gigi), bentuk berimbuhan/sinonim di luar tabel, frasa yang terpecah antar-baris.

## check_secrets.py + CI

`python tools/check_secrets.py --root .` gagal bila `.env`/`.env.*` (kecuali `.env.example`), `*.pem`, `*.key`, `*.age`, `*.dump`, `manifest_signing*`, atau `dataset_export/` ter-track, atau ada kunci privat age/PEM, `sb_secret_…`, atau JWT berperan `service_role` di berkas teks. Nilai tidak pernah dicetak.

`.github/workflows/ci.yml`: `secrets-scan`, `forbidden-phrases`, `shell` (bash -n + shellcheck), dan `python` (matrix `tools`, `tools/manifest`, `triage`, `recommender`, `ml/detection`; tiap modul dijalankan hanya bila `pyproject.toml`-nya ada; ruff + mypy hanya untuk `tools`).
