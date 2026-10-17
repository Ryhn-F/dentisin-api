# Pemberitahuan Privasi DentisAI (untuk orang tua/wali) — DRAF

> **DRAF. BELUM DITINJAU AHLI HUKUM.** Wajib ditinjau konsultan hukum (UU PDP No. 27/2022, PP Tunas, transfer lintas negara) dan KEPK sebelum dipakai. Nomor pasal sengaja tidak dicantumkan karena belum diverifikasi (`OPEN_LEGAL_QUESTIONS.md`). Isian `[ISI]` harus diisi tim; jangan dikarang. Bahasa dibuat sederhana; versi untuk anak ada di `CONSENT_TEXT_DRAFT.md`.
> `notice_version`: `privacy-v0.1-draft-id` (naikkan setiap perubahan dan catat di sini).

---

## DentisAI dan data Anda: ringkasan
- DentisAI adalah aplikasi **skrining**, bukan alat diagnosis. Hasilnya berupa **indikasi** yang perlu diperiksa dokter gigi. Hasil ini tidak menggantikan pemeriksaan dokter gigi.
- Aplikasi bekerja **di ponsel Anda**. Foto gigi diperiksa di ponsel dan **tidak dikirim ke mana pun** kecuali Anda sendiri memilih (lihat "Foto untuk pengembangan" di bawah, yang saat ini **belum tersedia**).
- Kami menyimpan sesedikit mungkin data tentang anak. Kami tidak meminta nama lengkap atau NIK anak.

## 1. Data apa yang kami simpan
| Data | Untuk apa | Di mana |
|---|---|---|
| Alamat email dan kata sandi Anda (orang tua/ kader) | Membuat dan masuk ke akun, atur ulang kata sandi | Server (layanan Supabase, Singapura); kata sandi disimpan dalam bentuk terenkripsi (hash) oleh layanan |
| Profil anak: **kode acak** (bukan nama), **tahun dan bulan lahir**, alergi | Menyesuaikan saran (mis. kadar fluoride menurut usia, bebas bahan yang menyebabkan alergi) | Di ponsel; ke server hanya bila Anda mengizinkan (b) |
| Jawaban gejala dan hasil skrining (tingkat, tanda yang terdeteksi, tanggal, versi aturan/ model) | Menampilkan hasil, riwayat, dan surat rujukan | Di ponsel (terenkripsi); ke server hanya bila diizinkan (b). **Tanpa gambar** |
| Catatan sikat gigi: tanggal, durasi, "skor semangat" | Streak dan laporan | Di ponsel; ke server hanya bila diizinkan (b) |
| Catatan persetujuan: jenis, versi teks, waktu | Bukti persetujuan dan penarikannya | Ponsel dan server |
| Catatan teknis tanpa identitas (mis. versi aplikasi, jenis kesalahan) | Memperbaiki aplikasi | Server, tanpa nama/ foto/ data anak yang dapat dikenali `[ISI: verifikasi isi log]` |

**Kamera saat menyikat gigi:** kamera depan dipakai untuk mengenali wajah dan gerakan tangan saat menyikat. **Video dan gambar tidak disimpan dan tidak dikirim**; gambar dibuang segera setelah diproses. **Satu pengecualian, hanya atas persetujuan orang tua (consent d, ADR 017):** pada akun anak, video singkat saat menyikat gigi dapat dikirim ke server privat di Singapura agar orang tua dapat menontonnya, dan dihapus otomatis paling lama 7 hari; tidak dipakai untuk melatih model dan tidak dilihat siapa pun selain orang tua anak itu `[ISI: tinjauan hukum Q-15]`.

**Yang tidak kami lakukan:** tidak ada iklan, tidak ada pelacak pihak ketiga, tidak ada profil perilaku anak, tidak ada lokasi tepat dikirim ke server `[ISI: konfirmasi dengan tim app]`.

## 2. Foto untuk pengembangan (pilihan (c)) — saat ini BELUM TERSEDIA
Fitur mengunggah foto gigi anak agar dipakai melatih dan mengevaluasi model **mati** dan baru boleh dihidupkan setelah komite etik penelitian (KEPK) menyetujui `[PENDING]`. Bila kelak tersedia:
- Hanya jika Anda memilih secara terpisah; **bawaan: mati**.
- Foto dikompres, data lokasi/ EXIF dihapus, nama berkas acak, disimpan di server privat (Supabase, **Singapura**).
- Hanya tim yang berwenang yang dapat mengaksesnya `[ISI: daftar peran]`; dipakai untuk melatih dan menguji model; lama simpan `[ISI]`.
- Anda dapat **menarik persetujuan** kapan saja: foto ditandai ditarik dan tidak dipakai pada pelatihan berikutnya. **Perlu Anda ketahui dengan jujur:** model yang sudah dilatih dengan foto itu **tidak dapat "melupakan"** apa yang sudah dipelajarinya. Keputusan apakah model dilatih ulang: `[ISI]`.

## 3. Ke mana data dikirim (termasuk ke luar negeri)
| Pihak | Data | Lokasi | Catatan |
|---|---|---|---|
| **Supabase** (penyimpanan akun dan data tersinkron) | Akun, data yang Anda izinkan | **Singapura** | Data kesehatan/ anak minimal dan berkode acak. Dasar hukum transfer lintas negara: `[PERLU VERIFIKASI]` |
| **Resend** (pengirim email konfirmasi dan atur ulang kata sandi) | Alamat email Anda dan tautan | Di luar negeri `[ISI lokasi]` | Isi email hanya tautan, tanpa data anak |
| Penyedia suara petunjuk (Azure Speech) | **Tidak ada data pengguna.** Suara dibuat sebelum aplikasi dirilis | - | Suara dibuat dengan AI dan diberi label |
Kami tidak menjual data dan tidak membagikannya untuk iklan.

## 4. Dasar dan tujuan
Kami memproses data atas **persetujuan Anda** sebagai orang tua/ wali (pilihan a/b/c bersifat terpisah dan dapat ditarik). Tujuan: menjalankan skrining dan saran, menyimpan riwayat, rujukan ke dokter gigi, serta (bila disetujui dan KEPK menyetujui) mengembangkan model. Dasar hukum rinci: `[PERLU VERIFIKASI oleh ahli hukum]`.

## 5. Hak Anda
Anda berhak meminta: melihat data, memperbaiki, **menghapus**, dan **menarik persetujuan**, sesuai UU PDP `[PERLU VERIFIKASI: ruang lingkup dan tenggat menurut peraturan]`. Cara: lewat menu Pengaturan > Privasi di aplikasi (hapus data anak / hapus akun) atau hubungi `[ISI kontak]`. Permintaan hapus diproses oleh sistem dan kami mencatat bukti penghapusannya `[ISI: batas waktu pemrosesan]`. Penarikan persetujuan (b) menghentikan pengiriman data baru ke server.

## 6. Berapa lama data disimpan
| Data | Lama |
|---|---|
| Data di ponsel | Sampai Anda menghapusnya atau aplikasi dihapus |
| Akun dan data tersinkron di server | `[ISI]` atau sampai Anda minta hapus |
| Foto pengembangan (bila ada) | `[ISI]` |
| Catatan persetujuan dan penghapusan | `[ISI]` (bukti kepatuhan) |
| Cadangan terenkripsi | `[ISI]` |

## 7. Keamanan
Data di ponsel dienkripsi; koneksi memakai TLS; akses server dibatasi per pengguna; log tidak memuat nama atau foto. Tidak ada sistem yang 100% aman; bila terjadi kebocoran yang berisiko bagi Anda, kami akan memberi tahu sesuai ketentuan `[PERLU VERIFIKASI tenggat]` (`INCIDENT_RESPONSE.md`).

## 8. Anak
Akun dimiliki orang tua/ wali atau kader. Mode anak berjalan tanpa akun dan tanpa data pribadi. Untuk penggunaan di sekolah/ posyandu, persetujuan orang tua dicatat sebelum foto diambil.

## 9. Perubahan dan kontak
Bila pemberitahuan ini berubah, Anda akan melihat versi baru dan, bila perlu, diminta persetujuan ulang. Kontak pengelola data / pejabat PDP: `[ISI]`. Pengaduan: `[ISI]`; Anda juga dapat mengadu ke otoritas pelindungan data sesuai hukum `[PERLU VERIFIKASI nama lembaga]`.

---
### Catatan untuk peninjau hukum (bukan bagian dari teks untuk pengguna)
1. Verifikasi: kategori data spesifik (kesehatan, anak, biometrik) dan perlakuan wajah (kamera sikat gigi tanpa penyimpanan).
2. Transfer Singapura (Supabase) dan Resend: dasar hukum, perlu perjanjian pemrosesan data?
3. Pejabat PDP wajib? Penilaian dampak? (data spesifik skala besar `[PERLU VERIFIKASI]`)
4. Retensi dan pernyataan "model tidak dapat melupakan": apakah cukup, atau latih ulang diwajibkan?
5. Apakah bahasa di bagian 4 (dasar persetujuan) sesuai dengan definisi persetujuan anak/ wali (Putusan MK 151/PUU-XXII/2024 `[PERLU VERIFIKASI]`)?
6. Tanggal efektif dan kewajiban PP Tunas untuk aplikasi kesehatan/ edukasi.
