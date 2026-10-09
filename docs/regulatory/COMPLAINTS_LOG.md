# Daftar Keluhan & Kejadian Tidak Diinginkan

> Templat hidup (ADR 002: manajemen keluhan; ringkasan sekunder KMK 951/2026 menyebut pelaporan keluhan/ kejadian tidak diinginkan, FSCA, dan pemantauan pasca-edar — **teks resmi belum dibaca; kewajiban pasti [PERLU VERIFIKASI]**). Belum ada pengguna sehingga **belum ada entri**.
> **Privasi:** jangan mencatat nama, kontak, foto, atau data kesehatan identitas. Rujuk lewat ID pseudonim/ ID laporan; detail sensitif disimpan terpisah dan dibatasi aksesnya.

## Kanal masuk
`[ISI]` (mis. email dukungan, formulir di app tanpa data anak, laporan kader/ puskesmas, ulasan toko). Alamat/ penanggung jawab: `[ISI]`. Pengguna harus tahu cara melapor (layar Tentang) — `[ISI]`.

## Klasifikasi
| Jenis | Contoh | Penanganan |
|---|---|---|
| **K1 Keselamatan** | Red flag terlewat; hasil "hijau" lalu kondisi gawat; rekomendasi fluoride keliru | Segera: `INCIDENT_RESPONSE.md` (insiden keselamatan klinis); tinjau `RISK_REGISTER.md`; pertimbangkan rollback aturan/model dan penonaktifan `medical_modules` |
| **K2 Privasi/keamanan** | Data bocor, akses tidak sah, permintaan hapus tak terpenuhi | `INCIDENT_RESPONSE.md` |
| **K3 Kinerja/akurasi** | Banyak false positive/negative dilaporkan, perangkat tertentu gagal | Masuk pemantauan model per versi/perangkat; evaluasi ulang |
| **K4 Bahasa/UX/aksesibilitas** | Teks terbaca seperti diagnosis, instruksi tak dipahami | Perbaiki teks; lint frasa terlarang |
| **K5 Lain** | Saran, pertanyaan | Catat, jawab |

Target waktu respons per jenis: `[ISI]` (jangan menjanjikan angka sebelum ada kapasitas tim).

## Log
| ID | Tanggal terima | Kanal | Jenis (K1–K5) | Ringkasan (tanpa PII) | Versi app / model / aturan | Terkait ID risiko | Tingkat keparahan (S) | Tindakan | PIC | Tanggal tutup | Perlu CHANGELOG? | Dilaporkan ke pihak luar? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| _(belum ada)_ | | | | | | | | | | | | |

## Proses (ringkas)
1. Terima → beri ID (`C-YYYY-NNN`) → klasifikasi dalam `[ISI]`.
2. K1/K2 → eskalasi sesuai `INCIDENT_RESPONSE.md`; drg pembimbing `[PENDING]` dilibatkan untuk K1.
3. Selidiki: versi model/aturan yang dipakai (tercatat di `screening`), bisakah direproduksi dengan `triage/tests`, apakah termasuk bahaya yang sudah ada di `RISK_REGISTER.md` atau baru.
4. Tindakan korektif/pencegahan → perubahan terkendali di `CHANGELOG.md` bila menyentuh model/aturan.
5. Tutup dengan verifikasi efektivitas; balas ke pelapor tanpa membuka data pihak lain.
6. Tinjau ringkasan keluhan secara berkala `[ISI frekuensi]`; hasilnya menjadi masukan pemantauan pasca-edar dan tinjauan risiko.
7. Kewajiban pelaporan ke otoritas (Kemenkes/ BPOM/ lembaga PDP): `[PERLU VERIFIKASI]` — jangan diasumsikan; lihat `OPEN_LEGAL_QUESTIONS.md`.
