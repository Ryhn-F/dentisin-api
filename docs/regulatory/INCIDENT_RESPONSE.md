# Prosedur Insiden (keamanan, privasi, keselamatan klinis)

> Templat hidup; **belum pernah dilatih/diuji**. Kontak dan peran `[ISI]` — jangan mengarang nama. Tenggat notifikasi kebocoran menurut UU PDP dan peraturan turunannya adalah **[PERLU VERIFIKASI]**; tidak diasumsikan di sini (lihat `OPEN_LEGAL_QUESTIONS.md` Q-02). Sampai terverifikasi, gunakan **target internal konservatif** (usulan, bukan kewajiban hukum): eskalasi ke pemilik produk secepat mungkin dan target keputusan notifikasi < 24 jam sejak insiden terkonfirmasi.

## 1. Peran dan kontak
| Peran | Nama | Kontak (di luar sistem yang terdampak) |
|---|---|---|
| Pemimpin insiden (pemilik produk) | `[ISI]` | `[ISI]` |
| Teknis backend/keamanan | `[ISI]` | `[ISI]` |
| Klinis (drg pembimbing) | `[PENDING]` | `[PENDING]` |
| Hukum / pejabat PDP (bila ditunjuk) | `[PENDING]` | `[PENDING]` |
| Mitra puskesmas/sekolah & KEPK (bila data uji terdampak) | `[PENDING]` | `[PENDING]` |
| Penyedia: Supabase, Resend, EAS, Azure | akun/ dasbor `[ISI]` | halaman dukungan/ keamanan vendor `[ISI]` |

Simpan daftar ini juga **offline** (bukan hanya di layanan yang bisa mati/bocor).

## 2. Tingkat keparahan (usulan)
| Tingkat | Contoh | Respons |
|---|---|---|
| **SEV1** | Data anak/ kesehatan terakses pihak tak berwenang; kunci `service_role`/penandatangan bocor; red flag terbukti terlewat pada pengguna nyata; manifes palsu terpasang | Segera, semua peran, hentikan penyebab |
| **SEV2** | RLS salah konfigurasi tanpa bukti akses; permintaan hapus gagal; foto terunggah tanpa consent (c)/ KEPK; dependensi rentan dieksploitasi | Hari yang sama |
| **SEV3** | Anomali tanpa dampak data; kegagalan keep-alive/backup; email auth tertunda | Dalam jadwal kerja |

## 3. Langkah umum
1. **Deteksi & catat**: siapa/kapan/apa; salin bukti (log tanpa PII). Buka entri di `COMPLAINTS_LOG.md` (jenis K1/K2) bila berasal dari laporan.
2. **Bendung**: hentikan penyebab sebelum menyelidiki (cabut kunci, matikan flag, cabut akses, tarik manifes). Jangan menghapus bukti.
3. **Nilai**: data apa (kategori: kesehatan, anak, foto, email), berapa subjek, apakah pseudonim, apakah dapat diidentifikasi, apakah terenkripsi, apakah ada kemungkinan bahaya bagi anak.
4. **Putuskan notifikasi**: ke subjek data (orang tua/wali), otoritas/ lembaga PDP, mitra, KEPK — berdasarkan hasil verifikasi hukum. Tenggat: **[PERLU VERIFIKASI]**. Catat siapa memutuskan dan alasannya. Isi notifikasi: apa terjadi, data apa, dampak mungkin, langkah yang diambil, kontak `[ISI]`; tanpa menyalahkan, tanpa data tambahan anak.
5. **Pulihkan**: perbaikan akar masalah, pulihkan dari cadangan terenkripsi bila perlu, verifikasi dengan tes (mis. `supabase test db`).
6. **Tinjau pasca-insiden** (≤ `[ISI]` hari): akar masalah, kontrol yang gagal, pembaruan `RISK_REGISTER.md`, `CHANGELOG.md` bila aturan/model berubah, tindakan pencegahan, latihan ulang.

## 4. Skenario khusus
| Skenario | Langkah awal |
|---|---|
| **Kunci Supabase `service_role` atau API key (Resend, dsb.) bocor** | Rotasi segera di dasbor; periksa log akses; pindai repo/ CI/ artefak untuk salinan; evaluasi apakah data terakses (SEV1 bila ya); perbarui rahasia di secrets, bukan repo. Pastikan `service_role` tidak ada di app |
| **RLS salah / data pengguna A terbaca B** | Matikan akses terdampak (nonaktifkan kebijakan/ tabel dari API), perbaiki lewat migrasi, tambah uji RLS, selidiki log siapa membaca apa (`audit_log`) |
| **Foto anak terunggah tanpa consent (c) atau sebelum KEPK** | Matikan `dataset_contribution`; kunci bucket `dataset_photos`; hapus/ karantina objek, catat bukti di `deletion_request`; tentukan apakah masuk pelatihan (`DATA_LINEAGE.md` §6); laporkan ke KEPK bila sudah ada |
| **Kunci penandatangan manifes hilang/ bocor** | Bocor: anggap manifes baru tak tepercaya; terbitkan kunci baru, rilis app dengan kunci publik baru (kunci publik di-embed — butuh update app), cabut manifes lama; hilang: gunakan cadangan terenkripsi, bila tak ada → rotasi seperti bocor. Tidak ada rilis aturan sampai pulih |
| **Model/aturan salah terpasang (regresi klinis)** | Rollback ke versi sebelumnya di manifes (FR-101); matikan `medical_modules` bila perlu; entri `CHANGELOG.md`; kabari drg |
| **Red flag terlewat (laporan pengguna)** | SEV1 klinis: selamatkan masukan yang direproduksi (tanpa PII), reproduksi di `triage/tests`, tambah kasus uji, perbaiki aturan, persetujuan drg `[PENDING]`, kabari pelapor untuk segera ke faskes bila masih relevan |
| **HP kader hilang/ dicuri** | Data lokal terenkripsi (SQLCipher); minta pengguna mengganti kata sandi akun; cabut sesi; catat; nilai apakah data anak tereksposisi |
| **Proyek Supabase dijeda / data hilang** | Pulihkan dari `pg_dump` terenkripsi terbaru; app tetap jalan offline; kabari mitra; periksa keep-alive |
| **Email konfirmasi/ reset bermasalah (Resend)** | Batas 100/hari; gunakan prosedur reset oleh admin; cadangan SMTP sesuai ADR 008; jangan kirim data anak lewat email |
| **Dependensi rentan / rantai pasok** | Regenerasi SBOM, tentukan terdampak, patch/ kunci versi, rilis perbaikan |

## 5. Latihan dan kesiapan
- Latihan meja minimal sekali sebelum uji lapangan `[ISI jadwal]`; catat hasil di bawah.
- Pen-test sebelum uji lapangan (`backend/CLAUDE.md` §12) `[ISI]`.

| Tanggal | Jenis (insiden nyata / latihan) | Ringkasan | Pelajaran | Tindak lanjut |
|---|---|---|---|---|
| _(belum ada)_ | | | | |
