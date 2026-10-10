# Kontrak outbox `sync_events`

> **Ringkasan (ID).** App menyimpan setiap perubahan data (persetujuan, profil anak, hasil skrining, sikat gigi, rujukan, metadata foto) sebagai *event* di antrean lokal (outbox), lalu mengirimnya dalam batch ke RPC `sync_events`. Server **idempoten per `event_id`**: mengirim ulang event yang sama tidak menulis dua kali. Penolakan bersifat **final** (disimpan): perbaiki penyebabnya dan kirim event baru dengan `event_id` baru. Server hanya menyimpan hash SHA-256 payload, bukan isinya. Dokumen ini diturunkan dari `supabase/migrations/20261008000500_sync_events.sql` (+ `20261010000200_ml_review_classes_by_dentition.sql` untuk `findings`).

- RPC: `public.sync_events(batch jsonb) returns jsonb`, `SECURITY INVOKER` (RLS tetap berlaku untuk setiap tulis), hanya peran `authenticated`.
- HTTP: `POST /rest/v1/rpc/sync_events`, body `{"batch": [ ...events ]}`, header `apikey` + `Authorization: Bearer <jwt>`.
- Contoh supabase-js: `const { data, error } = await supabase.rpc('sync_events', { batch })`.

## 1. Batch dan amplop event

| Aturan | Nilai |
|---|---|
| Bentuk `batch` | JSON array (bukan objek) |
| Jumlah event | 1 sampai `sync_batch_max_events` (bawaan **100**, plafon keras 500) |
| Ukuran batch | <= 262144 byte (`octet_length(batch::text)`) |
| Ukuran payload | <= 8192 byte per event (`octet_length(payload::text)`) |
| Pelanggaran | galat tingkat panggilan, HTTP 400, kode `22023`: `batch_must_be_array`, `batch_size_out_of_range`, `batch_too_large`; tanpa sesi: `not_authenticated` (`28000`) |

Amplop satu event (hanya kunci ini; kunci lain -> `schema_invalid`):

```json
{
  "event_id": "9b2f6c1e-3a4d-4e5f-8a7b-0c1d2e3f4a5b",
  "type": "screening_recorded",
  "payload": { "child_id": "...", "...": "..." },
  "occurred_at": "2026-10-11T03:12:45Z"
}
```

| Field | Wajib | Aturan |
|---|---|---|
| `event_id` | ya | UUID (format `8-4-4-4-12` hex; huruf besar diterima dan disimpan huruf kecil). Dibuat app, **unik per event**, disimpan di outbox dan dipakai ulang pada setiap pengiriman ulang |
| `type` | ya | salah satu dari 6 tipe (bagian 3); selain itu `unknown_event_type` |
| `payload` | ya | objek JSON <= 8192 byte, harus memuat `child_id` (UUID); kunci di luar daftar putih tipe itu -> `schema_invalid` (sehingga nama/teks bebas tidak bisa diselundupkan) |
| `occurred_at` | tidak | diterima tetapi **tidak divalidasi, tidak disimpan, dan tidak ikut hash idempotensi**; jangan diandalkan. Waktu kejadian ada di payload (`screened_at`, `started_at`, ...) |

Waktu di payload (`at`, `screened_at`, `started_at`, `referred_at`) divalidasi: bisa di-parse sebagai timestamp, tidak lebih dari 1 hari di masa depan (jam server), dan tidak sebelum 2020-01-01; selain itu `schema_invalid`. Kirim ISO 8601 dengan zona (`Z`).

## 2. Hasil

Respons HTTP 200: array dengan **jumlah dan urutan sama** dengan input.

```json
[ { "index": 0, "event_id": "9b2f6c1e-...", "status": "applied" },
  { "index": 1, "event_id": "4c0d1a2b-...", "status": "rejected", "code": "consent_required" } ]
```

| `status` | Arti |
|---|---|
| `applied` | sudah tertulis. Replay event identik juga mengembalikan `applied` (hasil tersimpan) |
| `rejected` | ditolak, `code` menjelaskan; **final**, kecuali `internal_error` |

`event_id` bernilai `null` bila amplop/`event_id` tidak valid (`schema_invalid`); hasil seperti itu **tidak disimpan**.

### Kode penolakan

| `code` | Penyebab | Disimpan? | Tindakan app |
|---|---|---|---|
| `schema_invalid` | kunci/tipe/nilai tidak sesuai, payload > 8 KiB, `child_id` tidak valid, waktu di luar rentang, pelanggaran CHECK/NOT NULL/enum, path foto tidak cocok atau objek belum ada | ya (kecuali amplop/`event_id` tidak valid) | bug klien atau data lokal rusak: buang dari antrean, catat lokal (tanpa PII); jangan kirim ulang |
| `unknown_event_type` | `type` bukan salah satu dari 6 | ya | bug versi app; jangan kirim ulang |
| `forbidden` | bukan pemilik/anggota roster anak itu, peran kurang (mis. `paper_via_kader` tanpa peran kader), RLS menolak, referensi (mis. `screening_id` di rujukan) tidak ada/bukan milik anak itu, atau anak tidak ada pada event non-consent | ya | event tidak sah untuk akun ini; buang |
| `consent_required` | consent (b) tidak aktif (event data) atau consent (c) tidak aktif (foto) | ya | kirim `consent_changed` yang diperlukan **lebih dulu** lalu kirim data sebagai event **baru** (`event_id` baru). Bila consent ditarik, hapus event data anak itu dari outbox |
| `feature_disabled` | flag `dataset_contribution` mati (`image_upload_registered`) | ya | jangan unggah foto; buang event |
| `deletion_pending` | ada permintaan hapus `pending`/`processing` untuk anak/akun ini | ya | hentikan sinkron subjek itu |
| `entity_conflict` | id entitas sudah ada (`screening_id`, `brushing_id`, `referral_id`, `upload_id`) di bawah `event_id` lain | ya | kemungkinan sudah tersinkron; buang event (verifikasi lewat `select` bila perlu) |
| `event_id_conflict` | `event_id` sama dengan isi (`type`+`payload`) berbeda, atau `event_id` milik akun lain | tidak disimpan ulang | bug klien (payload diubah setelah kirim pertama / tabrakan id); buat `event_id` baru dengan payload final |
| `internal_error` | galat tak terduga di server | **tidak** | satu-satunya yang boleh dikirim ulang dengan `event_id` sama (setelah jeda) |

Pesan galat tidak memuat nilai data; tidak ada teks bebas.

## 3. Tipe event dan payload

Semua payload memuat `child_id` (UUID v4 yang dibuat di perangkat). Kunci wajib/opsional di bawah; nilai `null` untuk kunci opsional diperlakukan sebagai kosong. UUID entitas (`screening_id`, `brushing_id`, `referral_id`, `upload_id`) dibuat app dan disimpan huruf kecil.

### 3.1 `consent_changed`

Kunci: `child_id`, `consent_type`, `action`, `text_version`, `at`, `method`.

| Kunci | Aturan |
|---|---|
| `consent_type` | wajib; `a` (simpan lokal), `b` (kirim data teknis terpseudonim), `c` (unggah foto dataset) |
| `action` | wajib; `grant` atau `withdraw` |
| `text_version` | wajib untuk `grant`; `^[A-Za-z0-9._-]{1,32}$` (versi teks persetujuan yang ditampilkan) |
| `at` | opsional; bawaan jam server; untuk `withdraw` nilai efektif `max(at, granted_at)` |
| `method` | opsional; `in_app` (bawaan) atau `paper_via_kader` (hanya peran `kader`, kalau tidak `forbidden`) |

Perilaku: anak belum ada + `grant` -> server membuat **profil anak kosong** milik pemanggil (butuh peran `guardian` atau `kader`); anak belum ada + `withdraw` -> `applied` (tidak ada yang ditarik). `grant` saat consent jenis itu sudah aktif -> `applied` tanpa baris baru. `grant` saat ada permintaan hapus -> `deletion_pending`; **penarikan selalu diterima**. Menarik (c) langsung menandai foto anak itu `withdrawn` (lalu dihapus oleh `retention-sweep`). Setelah ditarik, consent jenis itu bisa diberikan lagi sebagai baris baru.

### 3.2 `child_upserted`

Kunci: `child_id`, `birth_year`, `birth_month`, `allergies`.
Syarat: pemanggil **pemilik** anak (`forbidden` bila bukan atau anak belum ada), tidak ada permintaan hapus, consent (b) aktif (`consent_required`).

| Kunci | Aturan |
|---|---|
| `birth_year` | integer 2005..2035 atau `null` |
| `birth_month` | integer 1..12 atau `null`; hanya bila `birth_year` ada |
| `allergies` | array maksimum 10 kode, tiap kode `^[a-z][a-z0-9_]{1,39}$` (tanpa teks bebas); gunakan kode `allergen_codes` dari bundel rekomendasi (mis. `sls`, `flavor_mint`) |

**Semantik ganti penuh:** kunci yang tidak dikirim menjadi `null` / `[]` (bukan dipertahankan). Selalu kirim ketiganya. Tidak ada kolom nama, NIK, tanggal lahir lengkap, atau alamat.

### 3.3 `screening_recorded`

Kunci: `screening_id`, `child_id`, `screened_at`, `model_version`, `rules_version`, `level`, `red_flag`, `findings`.
Syarat: akses anak (pemilik atau anggota roster kader), tidak ada permintaan hapus, consent (b) aktif.

| Kunci | Aturan |
|---|---|
| `screening_id` | wajib UUID; duplikat di bawah `event_id` lain -> `entity_conflict` |
| `screened_at` | wajib timestamp |
| `model_version`, `rules_version` | wajib; `^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}([-+][0-9A-Za-z.-]{1,32})?$` (mis. `0.1.0`, `0.2.0-draft`) |
| `level` | wajib; `red`, `orange`, `yellow`, `green` (lihat pemetaan di bawah) |
| `red_flag` | opsional boolean (bawaan `false`); konvensi app: `true` bila ada alasan `RF_*` pada keluaran triase (server tidak memverifikasi) |
| `findings` | opsional array <= 50 objek (bawaan `[]`); tiap objek hanya kunci `class`, `confidence`, `box`. `class` kode `^[a-z][a-z0-9_]{1,39}$` (v1: `caries_permanent`, `caries_primary`); `confidence` angka 0..1; `box` array 4 angka 0..1 `[x, y, w, h]` ternormalisasi, **hanya geometri overlay** |

ADR 014: `rel_area` (turunan ukuran kotak) **tidak diterima lagi** (kunci itu -> `schema_invalid`); ukuran kotak tidak boleh dipakai untuk keparahan. Tidak ada gambar, nama, atau teks bebas.

Pemetaan level triase app -> server: `emergency` -> `red`, `urgent` -> `orange`, `scheduled` -> `yellow`, `routine` -> `green` (warna di `triage/dist/rules.json` `levels[]`).

### 3.4 `brushing_recorded`

Kunci: `brushing_id`, `child_id`, `started_at`, `duration_s`, `spirit_score`, `slot`, `camera_mode`. Syarat sama seperti 3.3.

| Kunci | Aturan |
|---|---|
| `brushing_id` | wajib UUID |
| `started_at` | wajib timestamp |
| `duration_s` | wajib integer 0..1800 (pecahan -> `schema_invalid`) |
| `spirit_score` | opsional integer 0..100 atau `null` (skor semangat; bukan klaim klinis) |
| `slot` | opsional `morning`, `evening`, `other` (bawaan `other`) |
| `camera_mode` | opsional boolean (bawaan `false`); tanpa video/frame |

### 3.5 `referral_created`

Kunci: `referral_id`, `child_id`, `screening_id`, `level`, `reason_codes`, `facility_code`, `referred_at`. Syarat sama seperti 3.3.

| Kunci | Aturan |
|---|---|
| `referral_id` | wajib UUID |
| `screening_id` | opsional UUID; bila ada harus screening milik **anak yang sama** yang sudah tersimpan (kalau tidak -> `forbidden`); kirim setelah event screening berstatus `applied` |
| `level` | wajib; `red|orange|yellow|green` |
| `reason_codes` | opsional array <= 20 kode `^[a-z][a-z0-9_]{1,39}$` (mis. id alasan triase huruf kecil); tanpa teks bebas |
| `facility_code` | opsional `^[A-Z0-9][A-Z0-9-]{2,31}$` (mis. `PKM-3201-01`) |
| `referred_at` | opsional timestamp (bawaan jam server) |

PDF surat rujukan dibuat offline di app; server hanya menyimpan metadata ini.

### 3.6 `image_upload_registered`

Mendaftarkan metadata foto dataset **setelah** objek diunggah ke Storage. Kunci: `upload_id`, `child_id`, `object_path`.
Urutan cek: akses anak (`forbidden`) -> permintaan hapus (`deletion_pending`) -> flag `dataset_contribution` (`feature_disabled`) -> consent (c) (`consent_required`) -> `object_path` persis `<uid>/<child_id>/<upload_id>.jpg` atau `.jpeg` (huruf kecil) -> objek benar-benar ada di `dataset_photos` di bawah prefiks akun pemanggil (`schema_invalid` bila tidak). Server menetapkan `status='uploaded'`, `uploaded_at`, dan `expires_at = now + image_upload_ttl_days`. Aturan unggah Storage: [`SUPABASE_API.md`](SUPABASE_API.md) bagian 4. Selama KEPK belum menyetujui, flag mati dan jalur ini tidak boleh dipakai.

## 4. Idempotensi

- Kunci idempotensi: `event_id`. Server menghitung `sha256(type + "|" + payload::text)` (jsonb kanonik: urutan kunci dan spasi tidak berpengaruh; **angka dipertahankan seperti ditulis**, mis. `0.82` dan `0.820` berbeda). Simpan **string JSON payload persis** di outbox dan kirim byte yang sama setiap kali.
- `event_id` ada, milik pemanggil, hash sama -> hasil tersimpan dikembalikan (replay deterministik, tanpa tulis ulang).
- `event_id` ada tetapi hash beda atau milik akun lain -> `event_id_conflict` (tidak mengungkap apa pun).
- Penolakan disimpan sebagai hasil terminal (replay mengembalikan penolakan yang sama). Dikecualikan: `internal_error`, serta amplop/`event_id` tidak valid.
- Retensi buku besar `sync_event`: `sync_event_retention_days` (bawaan **180** hari). Event yang dikirim ulang setelah itu dianggap baru (biasanya berujung `entity_conflict`, artinya sudah tersinkron).
- Satu panggilan RPC = satu transaksi, tetapi setiap event dijalankan dalam sub-transaksi: satu event ditolak tidak membatalkan yang lain. Bila respons hilang (timeout/jaringan putus), kirim ulang batch yang sama.

## 5. Urutan dan ketergantungan

- Event diproses **berurutan sesuai array**, dan antar-batch menurut urutan panggilan. App menjaga FIFO per anak.
- Karena penolakan final, jangan mengirim event yang bergantung pada event lain **sebelum** yang pertama terkonfirmasi `applied`:
  1. `consent_changed` (grant a/b) untuk anak baru -> membuat profil kosong. Kirim sendirian atau di batch awal, tunggu `applied`.
  2. `child_upserted` (butuh consent b).
  3. `screening_recorded`, `brushing_recorded` (butuh consent b).
  4. `referral_created` dengan `screening_id` (butuh screening `applied`).
  5. Foto: `consent_changed` (grant c) -> unggah ke Storage -> `image_upload_registered`.
- Bila event perantara ditolak, event yang bergantung akan ikut ditolak **permanen** (mis. `consent_required`, `forbidden`); tahan di antrean lokal dan jangan kirim.
- Penarikan consent (b): server berhenti menerima data anak itu (RPC dan RLS). App harus membersihkan event data anak itu dari outbox dan berhenti menambah yang baru.
- Penghapusan akun/anak (`request_deletion`) menghentikan sinkron subjek segera.

## 6. Retry dan backoff (pedoman untuk app)

| Situasi | Tindakan |
|---|---|
| Jaringan putus, timeout, HTTP 5xx, 429 | kirim ulang **batch yang sama** (event_id dan payload tidak berubah) dengan backoff eksponensial + jitter, mis. 30 dtk, 1, 2, 4, 8 menit, plafon 1 jam; hormati `Retry-After`; coba segera saat koneksi pulih |
| HTTP 401/403 (JWT kedaluwarsa) | refresh sesi sekali lalu kirim ulang; bila gagal, jeda dan minta masuk ulang (outbox tetap utuh) |
| HTTP 400 `22023` | jangan kirim ulang tanpa perubahan: perkecil batch (`batch_size_out_of_range`, `batch_too_large`) atau perbaiki bentuk (`batch_must_be_array`) |
| Hasil `applied` | hapus dari outbox |
| `rejected` selain `internal_error` | tandai terminal (antrean "perlu perhatian"), jangan kirim ulang dengan `event_id` sama; bila penyebab bisa diperbaiki, buat event baru dengan `event_id` baru |
| `internal_error` | kirim ulang dengan `event_id` sama setelah jeda |

Ukuran batch yang dianjurkan: <= 50 event per panggilan (batas keras 100 dan 256 KiB); satu batch dalam penerbangan pada satu waktu. Jangan mencatat payload, `child_id`, atau email di log.

## 7. Yang disimpan server

| Tabel | Isi |
|---|---|
| Tabel domain (`child_profile`, `consent`, `screening`, `brushing_event`, `referral`, `image_upload`) | nilai payload yang divalidasi; `recorded_by`/`created_by` = id akun pemanggil |
| `sync_event` (buku besar) | `event_id`, `submitted_by`, `event_type`, `child_id` (null untuk penolakan), **`payload_sha256`**, `result`, `received_at`. **Payload tidak pernah disimpan** |
| `audit_log` | satu entri `sync_batch` per panggilan: `events`, `applied_or_replayed`, `rejected` (hanya hitungan; id di-hash) |

## 8. Contoh lengkap

Batch (kader/orang tua baru; flag foto mati; satu event cacat sengaja):

```json
{ "batch": [
  { "event_id": "11111111-1111-4111-8111-111111111101", "type": "consent_changed",
    "payload": { "child_id": "c0c0c0c0-0000-4000-8000-000000000001", "consent_type": "b",
                 "action": "grant", "text_version": "v1", "at": "2026-10-11T03:00:00Z" } },
  { "event_id": "11111111-1111-4111-8111-111111111102", "type": "child_upserted",
    "payload": { "child_id": "c0c0c0c0-0000-4000-8000-000000000001", "birth_year": 2016,
                 "birth_month": 7, "allergies": ["sls"] } },
  { "event_id": "11111111-1111-4111-8111-111111111103", "type": "screening_recorded",
    "payload": { "screening_id": "55555555-5555-4555-8555-555555555501",
                 "child_id": "c0c0c0c0-0000-4000-8000-000000000001",
                 "screened_at": "2026-10-11T03:12:45Z", "model_version": "0.1.0",
                 "rules_version": "0.2.0-draft", "level": "orange", "red_flag": false,
                 "findings": [ { "class": "caries_primary", "confidence": 0.82,
                                 "box": [0.31, 0.42, 0.08, 0.1] } ] } },
  { "event_id": "11111111-1111-4111-8111-111111111104", "type": "brushing_recorded",
    "payload": { "brushing_id": "66666666-6666-4666-8666-666666666601",
                 "child_id": "c0c0c0c0-0000-4000-8000-000000000001",
                 "started_at": "2026-10-11T00:30:00Z", "duration_s": 120, "spirit_score": 77,
                 "slot": "morning", "camera_mode": true } },
  { "event_id": "11111111-1111-4111-8111-111111111105", "type": "referral_created",
    "payload": { "referral_id": "77777777-7777-4777-8777-777777777701",
                 "child_id": "c0c0c0c0-0000-4000-8000-000000000001",
                 "screening_id": "55555555-5555-4555-8555-555555555501", "level": "orange",
                 "reason_codes": ["det_caries_primary"], "referred_at": "2026-10-11T03:15:00Z" } },
  { "event_id": "11111111-1111-4111-8111-111111111106", "type": "screening_recorded",
    "payload": { "screening_id": "55555555-5555-4555-8555-555555555502",
                 "child_id": "c0c0c0c0-0000-4000-8000-000000000001",
                 "screened_at": "2026-10-11T03:20:00Z", "model_version": "0.1.0",
                 "rules_version": "0.2.0-draft", "level": "purple" } },
  { "event_id": "11111111-1111-4111-8111-111111111107", "type": "image_upload_registered",
    "payload": { "upload_id": "88888888-8888-4888-8888-888888888801",
                 "child_id": "c0c0c0c0-0000-4000-8000-000000000001",
                 "object_path": "<uid>/c0c0c0c0-0000-4000-8000-000000000001/88888888-8888-4888-8888-888888888801.jpg" } }
] }
```

Respons HTTP 200 (urutan sama dengan input):

```json
[
  { "index": 0, "event_id": "11111111-1111-4111-8111-111111111101", "status": "applied" },
  { "index": 1, "event_id": "11111111-1111-4111-8111-111111111102", "status": "applied" },
  { "index": 2, "event_id": "11111111-1111-4111-8111-111111111103", "status": "applied" },
  { "index": 3, "event_id": "11111111-1111-4111-8111-111111111104", "status": "applied" },
  { "index": 4, "event_id": "11111111-1111-4111-8111-111111111105", "status": "applied" },
  { "index": 5, "event_id": "11111111-1111-4111-8111-111111111106", "status": "rejected", "code": "schema_invalid" },
  { "index": 6, "event_id": "11111111-1111-4111-8111-111111111107", "status": "rejected", "code": "feature_disabled" }
]
```

Penjelasan: event 0 membuat profil kosong dan consent (b); 1-4 diterima karena consent (b) sudah aktif pada event 0 di batch yang sama; 5 ditolak (`purple` bukan level) -> final, jadi event pengganti memakai `event_id` baru; 6 ditolak karena flag `dataset_contribution` mati. Mengirim ulang batch yang sama mengembalikan array yang identik (tanpa duplikasi baris) dan tidak mengubah apa pun.

Catatan verifikasi: kontrak ini diuji oleh `supabase/tests/03_sync_events.test.sql` dan `04_consent.test.sql` (pgTAP) serta `supabase/scripts/rls_http_check.py`; contoh di atas ditulis tangan mengikuti kode dan belum dijalankan terhadap stack lokal `[PERLU VERIFIKASI]`.
