# Format pelabelan sikat gigi (landmark saja)

Tujuan: mengumpulkan data berlabel untuk kelak melatih/memvalidasi model sikat gigi (1D-CNN/GRU, v2; PRD sec.5) **tanpa pernah menyimpan video atau frame**. Berkas = fitur skalar per frame + label pengamat.

Skema mesin-baca: [`label_schema.json`](label_schema.json) (JSON Schema 2020-12). Validator: `python validate_labels.py FILE...` (skema + pemeriksaan semantik). Versi saat ini `0.1.0` — perubahan skema = versi baru + catat di `docs/regulatory/CHANGELOG.md`.

## Aturan privasi (tidak dapat ditawar)
1. **Tidak ada video, frame, piksel, atau jala wajah mentah** (468 titik wajah dapat mengidentifikasi anak = data biometrik/kesehatan anak, UU PDP Ps. 4 menurut `../docs/research/05_ethics_regulation.md`; [PERLU VERIFIKASI]). Skema memakai `additionalProperties: false` dan `privacy.contains_*` harus `false`, sehingga kolom `image`/`landmarks` ditolak. Bila kelak butuh landmark tangan mentah, itu keputusan baru + KEPK.
2. **Subjek anak hanya setelah KEPK menyetujui** dan ada persetujuan orang tua + assent anak. Validator mewajibkan untuk `subject_type: child`: `guardian_consent=true`, `assent=true`, `ethics_approval_ref` terisi, dan consent jenis `b` (data teknis terpseudonim). Sampai KEPK ada (gerbang G1, PRD 13.4): **hanya sukarelawan dewasa atau data sintetis**.
3. `pseudonym_id` acak (UUID/hash), bukan nama, inisial, NIK, atau nomor absen. Tanpa tanggal lahir (hanya `age_band`). Tanpa IMEI/serial; `device_class` kasar saja.
4. Pengamat memakai `observer_id` pseudonim (`OBS-01`). Tidak ada catatan teks bebas (bisa memuat PII).
5. Penyimpanan data lokal tim (PC tim/lab), tidak diunggah ke Kaggle/Colab/layanan pihak ketiga tanpa cakupan consent + etik (ADR 008). Hak tarik: hapus sesi berdasarkan `pseudonym_id`; model yang sudah dilatih tidak dapat "melupakan" (sampaikan jujur di consent).

## Struktur sesi
```
schema_version  "0.1.0"
session_id      UUID
subject         { pseudonym_id, subject_type: adult_volunteer|child, age_band }
consent         { consent_text_version, consent_types[a|b|c], guardian_consent, assent|null,
                  collected_at_utc, ethics_approval_ref|null }
recording       { camera:"front", mirrored_preview:true, nominal_fps, device_class,
                  extractor{name,version}, started_utc }
privacy         { contains_video:false, contains_frames:false, contains_raw_face_mesh:false }
observers[]     { observer_id: OBS-nn, role: dentist|trained_observer|other }
frames[]        fitur per frame (di bawah)
segments[]      label pengamat (di bawah)
```
Waktu absolut selalu ISO 8601 **dengan offset (simpan UTC)**; waktu relatif `t_ms` = milidetik sejak awal sesi, naik ketat.

### `frames[]` (sama dengan `Frame` di `brushing_score.py`, sehingga sesi berlabel dapat langsung diskor)
| Kolom | Arti |
|---|---|
| `t_ms` | int >= 0, naik ketat |
| `face_present`, `face_conf` | wajah terdeteksi; kepercayaan 0-1 |
| `face_scale` | tinggi wajah (satuan gambar ternormalisasi) atau `null` bila tak ada wajah; dipakai menormalkan jarak |
| `mouth_center` | `[x, y]` pusat mulut atau `null` |
| `head_yaw_deg`, `head_pitch_deg` | sudut kepala; yaw > 0 = menoleh ke kanan **layar cermin**; pitch > 0 = dagu turun |
| `hand_present`, `hand_conf` | tangan terdeteksi; kepercayaan |
| `hand_center` | `[x, y]` pusat telapak (rata-rata pergelangan + pangkal jari) atau `null` |

Koordinat: gambar ternormalisasi **pratinjau cermin** kamera depan (x ke kanan, y ke bawah; yang dilihat anak). Konsisten antara ekstraktor, skema, dan scorer. Konsistensi diperiksa validator: `face_present` => `mouth_center` & `face_scale` ada; tanpa wajah => keduanya `null`; `hand_present` <=> `hand_center` ada.

### `segments[]` (label pengamat)
`{ observer_id, start_ms, end_ms, is_brushing, sextant, uncertain? }`
- `sextant` in `upper_left|upper_front|upper_right|lower_left|lower_front|lower_right` (kiri/kanan **seperti di layar cermin**), `unknown` (pengamat tidak yakin), atau `none`.
- `is_brushing=false` **wajib** `sextant=none`; `is_brushing=true` tidak boleh `none`.
- Segmen satu pengamat tidak boleh tumpang tindih; pengamat berbeda boleh (untuk kappa antar-pengamat). Segmen harus berada dalam rentang `t_ms` frame. Interval `[start_ms, end_ms)`.

## Protokol pelabelan (usulan, belum disahkan drg) [ISI]
- Pengamat melihat anak langsung atau lewat video **yang tidak disimpan**; mencatat segmen secara real-time di alat labeling sendiri (belum ada). Video hasil rekaman untuk pelabelan = pemrosesan data wajah anak, **butuh persetujuan etik terpisah** — jangan lakukan tanpa KEPK.
- >= 2 pengamat per sesi pada subset; hitung Cohen's kappa untuk `is_brushing` dan `sextant`. Definisi sekstan tertulis oleh drg pembimbing [PENDING].
- Ukuran sampel minimum untuk kalibrasi ambang: [ISI, konsultasi biostatistik].

## Cara mendapat fitur
- `prototype_mediapipe.py` (opsional, belum diuji di mesin penulis) atau ekstraktor di app (Expo module, spike S3 ADR 004) yang menulis fitur ini langsung, tanpa menyimpan frame.
- `synthetic.py` membuat sesi sintetis untuk tes.
