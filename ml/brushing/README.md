# ml/brushing — prototipe sikat gigi (Fase 4)

Prototipe **rule-based** yang bekerja pada aliran *fitur landmark* (bukan gambar). Lingkup mengikuti ADR 003 butir 4 dan PRD FR-60..64:

| Dihitung | Cara | Bukan |
|---|---|---|
| **Kehadiran** | wajah terdeteksi **dan** tangan/sikat bergerak dekat mulut | bukan identifikasi orang |
| **Durasi aktif** | akumulasi waktu frame "aktif"; timer 2 menit selesai bila akumulasi >= 120 s | bukan durasi dinding |
| **Anti-curang** | variasi gerak: varians posisi tangan terhadap mulut dalam jendela geser, jumlah balik arah, batas kecepatan | tangan diam memegang sikat **tidak** dihitung |
| **Cakupan kasar** | 6 sekstan dari posisi tangan relatif mulut + yaw/pitch kepala; sekstan "tercakup" bila >= `min_sextant_ms` | **bukan** penilaian teknik/klinis |
| **Skor semangat** | 0-100 = bobot (durasi aktif, kelincahan gerak, cakupan) | bukan skor kebersihan gigi |
| **Jendela check-in** | pagi 06.00–09.00 (setelah sarapan) / malam 20.00–23.00 (sebelum tidur), satu sesi per jendela per hari, satu jendela cukup untuk streak (`streak_requires: any`), zona waktu Asia/Jakarta (WITA/WIT dapat dipilih), disimpan UTC (FR-63) | tidak memeriksa jam perangkat yang dimanipulasi |

## Batasan jujur (wajib dibaca sebelum memakai angka apa pun)
- **Bukti kamera depan lemah**: untuk wilayah sikat, literatur kuat hanya untuk sensor IMU; kamera depan layak untuk kehadiran/durasi/gamifikasi (`../docs/research/03_ai_components.md` sec. 3.4). Sikat sering menutupi mulut dan tangan.
- **Semua ambang di `brushing_rules.yaml` berstatus `PLACEHOLDER`** (kecuali target 2 menit = konsensus). Belum ada validasi terhadap pengamat, apalagi drg. `Rules.placeholder_params()` mendaftar yang belum divalidasi; gerbang rilis `release_check.py` menolak rilis selama masih ada (juga bila `reviewed_by`/`reviewed_at` kosong atau ada tanda `PERLU VERIFIKASI`). Basis tinjauan `review_basis` (opsional, ADR 015): `literature` = peninjau boleh anggota tim, tiap parameter wajib punya `source` (sitasi); `dentist` (bawaan bila kosong) = disahkan drg.
- Peta sekstan (arah kiri/kanan, atas/bawah) adalah **heuristik tidak teruji**. Tampilkan hanya sebagai panduan permainan.
- **Tidak ada klaim klinis dan tidak ada angka akurasi.** UI hanya boleh menyebut "skor semangat" (ADR 002/003; `../docs/style/forbidden_phrases.md`).
- Tidak ada video/frame yang disimpan: `BrushingScorer` hanya menyimpan jendela geser `(t, x, y)` (dibatasi `window_ms`), dan frame kamera dibuang setelah fitur diekstrak (FR-64). App perlu tes sendiri bahwa tidak ada berkas frame di storage.
- Jam perangkat dipercaya apa adanya; validasi sisi server saat sinkron belum dibuat.

## Isi
| Berkas | Fungsi |
|---|---|
| `brushing_rules.yaml` | **Semua** ambang (nilai + status + rasional). Kode tidak punya default angka |
| `rules_loader.py` | Memuat/memvalidasi aturan; `placeholder_params()` |
| `release_check.py` | Gerbang rilis aturan (`python release_check.py`; exit 1 = belum boleh rilis; gagal hari ini, memang begitu) |
| `brushing_score.py` | `Frame`, `BrushingScorer` (streaming), `score_session`; Python murni, mudah di-port ke TS |
| `checkin.py` | Jendela check-in + streak (fungsi murni; UTC ms masuk, zona waktu IANA) |
| `synthetic.py` | Generator aliran sintetis: wajah diam, sikat aktif, tangan melambai jauh, tangan diam, wajah hilang, geser satu arah, goyang cepat, sapuan 6 sekstan |
| `label_format.md`, `label_schema.json`, `validate_labels.py` | Format pelabelan **hanya landmark** + validator |
| `generate_cases.py` -> `tests/cases.json` | Kasus bersama untuk **paritas Python <-> TypeScript** (golden dari referensi ini) |
| `prototype_mediapipe.py` | Opsional, **belum diuji** di mesin penulis; ekstrak fitur dari kamera/berkas video tanpa menyimpan frame |

## Menjalankan
```bash
cd backend/ml/brushing
python -m venv .venv && . .venv/Scripts/activate      # atau .venv/bin/activate
pip install -e ".[dev]"
pytest                    # termasuk cakupan >= 95% (gagal bila di bawah)
ruff check . && ruff format --check .
mypy
python generate_cases.py  # hanya bila generator/stream sintetis berubah; tinjau diff cases.json
python validate_labels.py session.json
```

## Kontrak paritas TypeScript
Port TS (`../app/src/domain`) memuat `brushing_rules.yaml` yang sama (atau JSON hasil konversi, dengan tes yang membuktikan kesetaraannya), lalu menjalankan `tests/cases.json`: `session_cases[].frames` -> hasil harus cocok dengan `golden` (toleransi `float_tolerance`); `checkin_cases` dan `streak_cases` ditulis tangan (bukan hasil kode) dan harus sama persis. Waktu = integer milidetik; jangan memakai `Date` lokal perangkat untuk menentukan jendela tanpa zona waktu eksplisit.

## Dataset, lisensi, metrik
- Dataset: **belum ada**. Data uji = sintetis (dibuat kode ini; tanpa lisensi pihak ketiga). Data nyata hanya setelah KEPK + persetujuan orang tua (lihat `label_format.md`, `docs/regulatory/`).
- Lisensi: kode proyek tertutup (ADR 008). Dependensi: `pyyaml`, `jsonschema` (+ `tzdata` di Windows); semuanya perlu dicatat di `docs/regulatory/SBOM.md` dan `ml/data/LICENSES.md` [PERLU VERIFIKASI lisensi tiap versi]. MediaPipe **bukan** dependensi wajib; lisensi paket dan bobot model `.task` harus dicek sebelum dipakai.
- Metrik: hanya tes perilaku sintetis (hasil nyata pada saat penulisan: lihat laporan akhir/CI). **Belum ada** metrik terhadap pengamat; rencana validasi: bandingkan `is_brushing`/sekstan hasil skor dengan label pengamat (kappa antar-pengamat dulu), Fase 6.
