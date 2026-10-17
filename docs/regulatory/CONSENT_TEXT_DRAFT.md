# Teks Persetujuan DentisAI — DRAF

> **DRAF. BELUM DITINJAU AHLI HUKUM DAN KEPK.** Dipakai untuk mendefinisikan `consent` (jenis a/b/c + `text_version`) di app dan DB (PRD FR-02, §9.3). Setiap perubahan kata = `text_version` baru; persetujuan lama tetap terikat pada versi yang dilihat penggunanya. Persetujuan harus **granular, terpisah, tidak dicentang otomatis**; (b) dan (c) **default mati**; dapat ditarik kapan saja. Teks mengacu `PRIVACY_NOTICE_DRAFT.md` (`privacy-v0.1-draft-id`).
> Pemberi persetujuan: orang tua/ wali anak (atau dewasa untuk dirinya sendiri sebagai sukarelawan). Anak < 18 th: persetujuan wali + assent anak (pendekatan konservatif, `docs/research/05_ethics_regulation.md` §5.2 — celah definisi anak [PERLU VERIFIKASI]).

## Daftar versi teks
| `text_version` | Jenis | Status | Dipakai sejak | Catatan |
|---|---|---|---|---|
| `consent-a-v0.1-draft-id` | (a) simpan lokal | DRAF | - | |
| `consent-b-v0.1-draft-id` | (b) data teknis terpseudonim | DRAF | - | |
| `consent-c-v0.1-draft-id` | (c) unggah foto | DRAF; **fitur mati sampai KEPK menyetujui** | - | `[PENDING]` KEPK |
| `consent-d-v0.1-draft-id` | (d) bukti sikat gigi video ke orang tua (ADR 017) | DRAF; **fitur di balik flag `brushing_proof_video`; menunggu tinjauan hukum (Q-15)** | - | `[PENDING]` hukum |
| `assent-child-v0.1-draft-id` | persetujuan anak (assent) | DRAF | - | Ditulis untuk kelas 4 SD; `[ISI]` uji keterbacaan 5 anak |
| `brushing-camera-notice-v0.1-draft-id` | pemberitahuan kamera sikat gigi | DRAF | - | informasi, bukan jenis consent (a/b/c) `[ISI: keputusan hukum]` |

Rekaman di DB (kolom yang disarankan): `guardian_id`, `child_id` (pseudonim), `type` (a|b|c|d), `text_version`, `granted` (bool), `granted_at`, `withdrawn_at`, `locale` = `id`. Tidak menyimpan teks lengkap berulang; teks tersimpan di repo menurut versi.

---

## (a) Simpan di ponsel — `consent-a-v0.1-draft-id`
**Judul:** Simpan data anak di ponsel ini
> Saya orang tua/ wali dari anak ini. Saya mengizinkan DentisAI menyimpan **di ponsel ini** data berikut agar riwayat dan saran dapat muncul tanpa internet: kode acak anak, tahun dan bulan lahir, alergi, jawaban gejala, hasil skrining, dan catatan sikat gigi. Data ini dienkripsi di ponsel. DentisAI **tidak meminta nama lengkap atau NIK**.
> Hasil DentisAI adalah **indikasi**, bukan diagnosis, dan tidak menggantikan pemeriksaan dokter gigi.
> Saya dapat menghapus data ini kapan saja di Pengaturan > Privasi.

Tombol: [Saya setuju] [Tidak sekarang] (jika tidak setuju: aplikasi tetap bisa dipakai dalam mode tanpa menyimpan riwayat `[ISI: konfirmasi dengan tim app]`).

## (b) Kirim data teknis tanpa identitas — `consent-b-v0.1-draft-id` (bawaan: MATI)
**Judul:** Sinkron data ke server (tanpa foto)
> Saya mengizinkan DentisAI mengirim ke server **data teknis berkode acak** tentang anak saya: tahun-bulan lahir, hasil skrining (tingkat dan tanda yang terdeteksi, tanpa gambar), tanggal, versi aturan/ model, dan catatan sikat gigi. Tujuannya: menyimpan riwayat di beberapa ponsel, rekap kelas untuk puskesmas/ sekolah (bila ikut), dan memperbaiki aplikasi.
> Server berada di **Singapura** (layanan Supabase), yaitu di luar Indonesia. Email saya diproses oleh layanan pengirim email di luar negeri hanya untuk konfirmasi dan atur ulang kata sandi.
> Saya dapat **menarik** persetujuan ini kapan saja; setelah itu data baru tidak dikirim lagi. Saya dapat meminta data saya dihapus.

## (c) Unggah foto untuk pengembangan — `consent-c-v0.1-draft-id` (bawaan: MATI; fitur belum aktif)
**Judul:** Bantu mengembangkan DentisAI dengan foto gigi (pilihan)
> **Catatan:** Pilihan ini hanya muncul bila komite etik penelitian telah menyetujui `[PENDING]`. Nomor persetujuan etik: `[ISI]`.
> Saya mengizinkan foto gigi depan anak saya diunggah ke **server privat di Singapura** untuk melatih dan menguji model komputer pendeteksi gigi berlubang. Foto dikompres; data lokasi dan EXIF dihapus; berkas diberi nama acak; **wajah tidak diminta tampil**.
> **Siapa yang dapat mengakses:** `[ISI: peran, mis. tim peneliti dan drg penilai]`. **Lama simpan:** `[ISI]`.
> Saya boleh **menarik** persetujuan kapan saja. Foto saya akan ditandai ditarik dan **tidak dipakai pada pelatihan berikutnya**. Saya memahami bahwa **model yang sudah dilatih dengan foto itu tidak dapat "melupakan"** apa yang telah dipelajarinya `[ISI: kebijakan latih ulang]`.
> Menolak pilihan ini **tidak** mengurangi fitur aplikasi yang lain.

## (d) Video bukti sikat gigi untuk orang tua — `consent-d-v0.1-draft-id` (bawaan: MATI; hanya untuk anak yang punya akun anak)
**Judul:** Orang tua boleh menonton video sikat gigi anak
> Saya orang tua/ wali dari anak ini. Saya mengizinkan akun anak saya merekam **video singkat saat menyikat gigi** (menampilkan mulut dan mungkin wajah anak) dan mengirimnya ke server **privat di Singapura** agar **saya** dapat menontonnya sebagai bukti anak menyikat gigi.
> **Siapa yang dapat melihat:** hanya akun saya. Video **tidak** dipakai untuk melatih model, tidak dibagikan ke guru/ kader/ fasilitas kesehatan/ admin, dan tidak dianalisis otomatis.
> **Lama simpan:** dihapus otomatis paling lama **7 hari** setelah direkam; saya dapat menghapusnya lebih cepat kapan saja.
> Saya dapat **menarik** persetujuan ini kapan saja: video yang ada langsung disembunyikan dan dihapus dari server, dan video baru tidak dapat dikirim. Menolak pilihan ini **tidak** mengurangi fitur lain; anak tetap bisa menyikat gigi dan mencatat streak tanpa video.
> `[ISI: teks final menunggu tinjauan hukum Q-15/Q-18 dan KEPK bila diteliti]`

## Assent anak — `assent-child-v0.1-draft-id` (bahasa sederhana, mode anak)
> Halo! Aplikasi ini membantu melihat gigimu dengan foto dan kamera. Hasilnya bukan dari dokter. Kalau ada yang perlu dilihat, kamu akan diajak ke puskesmas atau dokter gigi.
> Kamu boleh bilang "tidak mau" kapan saja, dan kamu tidak akan dimarahi.
> Mau ikut?  [Mau]  [Tidak mau]

(Jangan menampilkan gambar menakutkan; nada menenangkan. Uji keterbacaan: `[ISI]`.)

## Pemberitahuan kamera sikat gigi — `brushing-camera-notice-v0.1-draft-id`
> Saat menyikat gigi, kamera depan melihat wajah dan tangan supaya hitungan 2 menit berjalan hanya saat kamu menyikat. **Video dan foto tidak disimpan dan tidak dikirim** (kecuali pada akun anak yang orang tuanya mengaktifkan consent (d): lihat teks (d) dan layar perekaman yang menyatakannya jelas sebelum merekam). Kalau kamera tidak mau dipakai, ada timer dengan musik tanpa kamera.

## Penarikan persetujuan (UI)
- Pengaturan > Privasi: tiap jenis dapat dimatikan sendiri; konfirmasi: "Data baru tidak akan dikirim. Mau sekaligus menghapus data yang sudah dikirim?"
- Penarikan (b) menghentikan sinkron; penarikan (c) menandai foto `withdrawn`; keduanya mencatat `withdrawn_at` dan memicu tinjauan `DATA_LINEAGE.md` §6 untuk (c).
- Hapus akun/ data anak membuat `deletion_request` dan bukti.

## Mode kader/ sekolah
Persetujuan orang tua **per murid, tercatat sebelum foto diambil** (PRD FR-80). Surat izin sekolah/ Dinkes: `[ISI]`. Lembar informasi + formulir kertas untuk orang tua tanpa email aktif: `[ISI]` — format mengikuti arahan KEPK.

## Daftar tinjauan
- [ ] Ahli hukum: kecukupan persetujuan wali, penarikan, transfer lintas negara, kategori data spesifik
- [ ] KEPK: teks (c) dan assent
- [ ] drg pembimbing: kalimat tentang hasil/ rujukan sesuai `../docs/style/forbidden_phrases.md`
- [ ] Uji keterbacaan orang tua dan anak
- [ ] Nomor `text_version` sinkron di app, DB, dan dokumen ini
