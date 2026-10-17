# Risk Register (gaya ISO 14971) — DentisAI

> Dokumen hidup. Versi: 0.1 (benih, 2026-10-09). **Belum ditinjau drg atau konsultan regulasi.** Kolom bertanda `[ISI]` sengaja kosong: probabilitas dan risiko sisa harus diisi pemilik risiko bersama drg pembimbing `[PENDING]`; jangan diisi dari perkiraan tanpa dasar. Severity kolom "S (usulan)" adalah usulan awal engineering untuk dikoreksi peninjau, bukan penilaian klinis.
> Asal bahaya: PRD §14 (Risiko & mitigasi), `../docs/research/05_ethics_regulation.md`, ADR 001/002/003/005/008, `backend/CLAUDE.md`. Standar rujukan (disebut ringkasan sekunder KMK 951/2026, **teks resmi belum dibaca**): ISO 14971; QMS/IEC 62304/62366-1 [PERLU VERIFIKASI relevansi].

## Skala (usulan, untuk disahkan)
| S | Arti | | P | Arti |
|---|---|---|---|---|
| S1 | Dapat diabaikan: tidak ada dampak kesehatan/privasi berarti | | P1..P5 | `[ISI]` definisi kuantitatif/kualitatif; jangan diisi sebelum ada data pilot |
| S2 | Ringan: ketidaknyamanan, kecemasan sementara, kerja ulang | | | |
| S3 | Serius: keterlambatan perawatan atau pelanggaran privasi data anak yang dapat diperbaiki | | | |
| S4 | Kritis: kerugian kesehatan serius/ permanen, atau pelanggaran privasi/regulasi besar | | | |
| S5 | Katastrofik: kematian / kerugian tak terpulihkan | | | |

Matriks keberterimaan risiko (S × P → diterima / ALARP / tidak diterima): `[ISI — disahkan pemilik risiko + drg]`.

## Register
Kolom: **Kontrol di desain** = yang sudah ada di keputusan/PRD (status implementasi tidak berarti sudah dibangun; periksa kolom Verifikasi). **Verifikasi** = bukti yang akan membuktikan kontrol bekerja; path bertanda *(rencana)* belum ada atau sedang dibangun tim lain.

### A. Keselamatan klinis (skrining, triase, rujukan)
| ID | Bahaya → situasi berbahaya | Kemungkinan bahaya (harm) | S (usulan) | P | Kontrol di desain | Risiko sisa | Verifikasi |
|---|---|---|---|---|---|---|---|
| R-01 | **Red flag terlewat**: gejala bengkak wajah, demam+nyeri gigi, nanah, trauma, sulit menelan/bernapas tidak diarahkan ke faskes | Keterlambatan penanganan infeksi odontogenik/ kegawatan | S4 | `[ISI]` | Red flag dari **kuesioner gejala** deterministik, dijalankan sebelum foto & sebelum fitur lain; layar darurat penuh, berfungsi offline; daftar disahkan drg (PRD FR-10/11, prinsip 2.4) | `[ISI]` | `triage/tests/cases.json` recall 100% *(rencana)*; daftar red flag disahkan drg `[PENDING]` |
| R-02 | **Red flag dikalahkan logika lain**: hasil foto "hijau" atau urutan aturan menurunkan level | False reassurance meski ada gejala gawat | S4 | `[ISI]` | Urutan evaluasi red flag → skor → jumlah lesi → default; tes properti: menambah red flag tidak pernah menurunkan level (`backend/CLAUDE.md` §3.2, §8) | `[ISI]` | Property-based test *(rencana)* di `triage/` |
| R-03 | **False negative karies berlubang** (deteksi melewatkan lesi) | Orang tua merasa aman, perawatan tertunda | S3 | `[ISI]` | Tidak ada klaim akurasi; teks "belum terlihat… tetap periksa rutin"; 🟢 tetap menyarankan skrining ulang 3–6 bulan; CTA rujukan di setiap hasil (PRD §11, FR-42); bake-off + evaluasi sens per kelas | `[ISI]` | Laporan evaluasi uji independen per versi *(rencana; belum ada)*; uji snapshot teks hasil *(rencana)* |
| R-04 | **Lesi awal (non-kavitas) tak terlihat di foto** — bukti publik: sensitivitas rendah dari foto ponsel (ADR 003, konteks) | Label hijau menyesatkan | S3 | `[ISI]` | model tidak mengklaim lesi awal/ keparahan; keparahan tidak diturunkan dari ukuran kotak (ADR 014, FR-32); batas ditampilkan "foto tidak melihat sela-sela gigi / bagian dalam" (FR-33) | `[ISI]` | Tes aturan YAML *(rencana)*; angka sensitivitas `[PERLU VERIFIKASI]` ke sumber di `02_literature.md` |
| R-05 | **Foto buruk** (buram, gelap, bibir menutupi, gigi belakang tak terlihat) lolos quality gate | Hasil tidak andal | S3 | `[ISI]` | Quality gate wajib (blur Laplacian, kecerahan, gigi terlihat); umpan balik retake; foto gigi depan dulu; kader terlatih (FR-20..22, A2) | `[ISI]` | Set uji ≥ 200 foto berlabel untuk gate *(rencana; belum ada)* |
| R-06 | **Over-triage** (false positive) | Kecemasan, beban rujukan ke puskesmas | S2 | `[ISI]` | Kebijakan "ragu → rujuk" disengaja; spesifisitas dilaporkan; bahasa menenangkan mode anak | `[ISI]` | Laporan spesifisitas/PPV per versi *(rencana)*; rate rujukan di pilot `[ISI]` |
| R-07 | **Bahasa terbaca sebagai diagnosis** (UI, PDF rujukan, audio) | Keputusan keliru; klaim medis tak sah (regulasi) | S3 | `[ISI]` | Daftar frasa terlarang + lint CI; disclaimer wajib pada layar hasil; semua keluaran "indikasi/perlu diperiksa" (prinsip 2.1, PRD §11) | `[ISI]` | Lint `../docs/style/forbidden_phrases.md` di CI *(rencana)*; uji keterbacaan 5 anak/ 10 orang tua `[ISI]` |
| R-08 | **Surat rujukan/ info faskes salah atau usang** | Rujukan ke tempat tidak tepat, tertunda | S2 | `[ISI]` | Template offline berdata minimum; daftar faskes dibundel & diberi tanggal; pilih manual tanpa GPS presisi (FR-43/44) | `[ISI]` | Uji snapshot PDF *(rencana)*; sumber data faskes `[PERLU VERIFIKASI]` (FR-44) |
| R-09 | **Nomor/ instruksi darurat tidak tepat daerah** atau layar darurat gagal tampil | Keterlambatan bantuan | S4 | `[ISI]` | Layar darurat statis offline; nomor "setempat" dikonfirmasi per wilayah pilot | `[ISI]` | Uji e2e mode pesawat *(rencana, Maestro)*; konfirmasi nomor oleh mitra `[PENDING]` |

### B. Rekomendasi (fluoride / pasta gigi)
| ID | Bahaya → situasi berbahaya | Harm | S (usulan) | P | Kontrol di desain | Risiko sisa | Verifikasi |
|---|---|---|---|---|---|---|---|
| R-10 | **Dosis/kadar fluoride salah menurut usia** (< 6 th; konflik standar SNI 16-4767-1998 vs anjuran ≥ 1000 ppm; batas 0,15% Permenkes 445/1998 `[PERLU VERIFIKASI]`) | Fluorosis gigi / tertelan berlebih atau pencegahan kurang | S3 | `[ISI]` | Parameter di JSON bertanda tinjau drg, `status: needs_review` sampai PDGI/IDAI menyelesaikan; jumlah pakai (smear/kacang polong), awasi sampai ≥ 7 th; v1 fluoride saja; nilai `PLACEHOLDER` memblokir rilis (`backend/CLAUDE.md` §3.1, §8) | `[ISI]` | CI gagal bila `null`/PLACEHOLDER pada build rilis *(rencana)*; tinjauan drg `[PENDING]` |
| R-11 | **Filter alergi/halal/SLS gagal** | Reaksi alergi / produk tak sesuai keyakinan | S3 | `[ISI]` | Filter di rule engine + uji tabel; alergi dari profil anak (FR-52, FR-03) | `[ISI]` | `recommender/tests` *(rencana)* |
| R-12 | **Bias merek / data produk salah** (nomor notifikasi BPOM, kandungan) | Rekomendasi menyesatkan, citra non-netral | S2 | `[ISI]` | Kandungan dulu; merek hanya contoh netral ≥ 2 di `products.json`, `verified`, `verified_at`, tanpa kerja sama berbayar memengaruhi logika (prinsip 2.5) | `[ISI]` | Tes "tidak ada merek di logika" *(rencana)*; verifikasi BPOM `[PERLU VERIFIKASI]` |
| R-13 | **Kesan pasta gigi menyembuhkan lubang** | Orang tua menunda ke dokter | S3 | `[ISI]` | Kalimat wajib "Pasta gigi membantu, tetapi tidak menambal gigi berlubang." (FR-52) | `[ISI]` | Uji snapshot teks *(rencana)* |

### C. Model & perubahan terkendali
| ID | Bahaya → situasi berbahaya | Harm | S (usulan) | P | Kontrol di desain | Risiko sisa | Verifikasi |
|---|---|---|---|---|---|---|---|
| R-14 | **Generalisasi buruk / pergeseran distribusi (drift)**: data latih publik bukan anak Indonesia (mis. satu perangkat, usia 10–24 th — `backend/CLAUDE.md` §9), gigi sulung vs permanen, cahaya senter | Kinerja nyata jauh di bawah laporan | S3 | `[ISI]` | Tidak ada klaim sebelum validasi lokal; evaluasi subgrup wajib (usia, HP, cahaya, sumber); log kinerja per versi/perangkat tanpa PII; split per pasien | `[ISI]` | MODEL_CARD subgrup *(rencana)*; pemantauan pasca-edar `[ISI]` |
| R-15 | **Bias antar subgrup** (usia, warna gusi/kulit, tipe kamera, wilayah) | Ketidakadilan akurasi | S3 | `[ISI]` | Laporan bias wajib di model card (PRD §10); data lokal ditambah (v2) | `[ISI]` | Bagian Bias di `MODEL_CARD_TEMPLATE.md` terisi per versi |
| R-16 | **Perubahan model/aturan tanpa persetujuan drg atau tanpa regresi** | Perilaku klinis tak tervalidasi; perubahan algoritma dapat berarti izin edar baru (ringkasan sekunder KMK 951/2026 `[PERLU VERIFIKASI]`) | S3 | `[ISI]` | Change control: `CHANGELOG.md`, laporan evaluasi, persetujuan drg, tes regresi; versi aturan/model tercatat di setiap `screening` (PRD §9.3) | `[ISI]` | Entri CHANGELOG lengkap sebelum manifes ditandatangani; tes tabel 100% *(rencana)* |
| R-17 | **Manifes/model/aturan dipalsukan atau korup di Storage** | Aturan klinis berbahaya terpasang di HP | S4 | `[ISI]` | Manifes Ed25519 + checksum diverifikasi app; kunci privat offline; rollback (FR-101); tes manifes rusak ditolak | `[ISI]` | `tools/verify_manifest.py` + tes *(rencana)* |
| R-18 | **Kunci penandatangan hilang/bocor** | Tidak bisa merilis perbaikan / rilis palsu | S4 | `[ISI]` | Kunci offline + cadangan terenkripsi, tidak di repo/CI; rencana rotasi `[ISI]` | `[ISI]` | Prosedur di `INCIDENT_RESPONSE.md` §4 (skenario kunci penandatangan); latihan rotasi `[ISI]` |
| R-19 | **Modul medis tampil di build publik** (flag `medical_modules` mati salah/ bocor) | Distribusi fungsi yang mungkin SaMD tanpa izin | S4 | `[ISI]` | Flag default mati; modul non-medis independen; tinjauan rilis (ADR 002) | `[ISI]` | Tes build publik: flag mati *(rencana)*; checklist rilis `[ISI]` |

### D. Privasi, data anak, keamanan
| ID | Bahaya → situasi berbahaya | Harm | S (usulan) | P | Kontrol di desain | Risiko sisa | Verifikasi |
|---|---|---|---|---|---|---|---|
| R-20 | **Kebocoran data kesehatan/ anak di server** (RLS salah, kunci `service_role` bocor) | Pelanggaran UU PDP; pengungkapan data anak | S4 | `[ISI]` | RLS di semua tabel + uji otomatis; `service_role` tidak di app; pseudonim UUID; tanpa nama lengkap/NIK; `audit_log`; pemindaian rahasia | `[ISI]` | `supabase test db` *(rencana)*; pen-test sebelum uji lapangan `[ISI]` |
| R-21 | **Data di perangkat terbuka** (HP hilang/ dipinjam) | Pengungkapan riwayat skrining anak | S3 | `[ISI]` | SQLCipher, kunci di Keystore; foto tidak dipertahankan kecuali perlu | `[ISI]` | Spike S5 (DB terenkripsi) *(rencana)* |
| R-22 | **PII/foto masuk log, analitik, atau email** | Kebocoran sekunder | S3 | `[ISI]` | Log tanpa PII/foto; tanpa Application Insights/SDK iklan; email hanya tautan | `[ISI]` | Pemeriksaan log di tinjauan kode; uji "tidak ada PII" *(rencana)* |
| R-23 | **Foto anak terunggah tanpa consent (c) atau sebelum KEPK** (flag `dataset_contribution` hidup salah) | Pemrosesan tanpa dasar sah; masalah etik | S4 | `[ISI]` | Flag default mati sampai KEPK; unggah opt-in (c) terpisah, default mati; RLS bucket (hanya tulis prefiks milik sendiri); JPEG ≤ 500 KB, EXIF dihapus (FR-25) | `[ISI]` | Uji RLS bucket *(rencana)*; keputusan KEPK `[PENDING]` |
| R-24 | **Persetujuan tidak sah** (bukan wali, anak sendiri, versi teks tak tercatat) | Dasar pemrosesan batal | S3 | `[ISI]` | Persetujuan orang tua/wali granular a/b/c + assent anak; versi teks & waktu tercatat; tarik persetujuan menghentikan sinkron (FR-02) | `[ISI]` | Uji otomatis consent *(rencana)*; tinjauan hukum teks `[PENDING]` |
| R-25 | **Hak hapus/ tarik tidak dijalankan**; ekspektasi keliru bahwa model "melupakan" | Pelanggaran hak subjek; kepercayaan | S3 | `[ISI]` | Job hapus + bukti di `deletion_request`; `withdrawn` mengeluarkan dari pelatihan berikutnya; pengungkapan jujur bahwa model terlatih tidak bisa melupakan (di consent (c)) | `[ISI]` | Uji penghapusan *(rencana)*; kebijakan data pada model yang sudah dilatih `[ISI — keputusan hukum/etik]` |
| R-26 | **Transfer lintas negara** (Supabase Singapura; Resend untuk email; Azure Jepang hanya TTS build-time/ FastAPI tahap 1) tanpa dasar hukum jelas | Pelanggaran UU PDP / aturan turunan | S3 | `[ISI]` | Minimisasi, pseudonim, foto tidak default, email hanya tautan; pemberitahuan privasi menyebutnya; tinjauan hukum | `[ISI]` | `OPEN_LEGAL_QUESTIONS.md` Q-01 |
| R-27 | **Video/frame wajah anak tersimpan oleh modul sikat gigi** *(dilonggarkan oleh ADR 017 hanya untuk video bukti ke orang tua; lihat R-37)* | Data biometrik/anak tersimpan tanpa perlu | S4 | `[ISI]` | Frame diproses di memori & dibuang; format pelabelan hanya landmark; tanpa jala wajah mentah (FR-64; `ml/brushing/`) | `[ISI]` | Tes "tidak ada berkas frame di storage" *(rencana, app)*; skema pelabelan menolak kolom gambar (`ml/brushing/tests/test_labels.py`) |
| R-28 | **Kerentanan dependensi / rantai pasok** (npm, pip, modul native komunitas) | Kompromi aplikasi | S3 | `[ISI]` | Kunci versi; pemindaian dependensi + SBOM di CI | `[ISI]` | `SBOM.md`; `npm audit`/`pip-audit` di CI *(rencana)* |
| R-29 | **Kontaminasi lisensi** (AGPL YOLOv8 ke repo tertutup; CC BY-SA AlphaDent ke model terkirim; bobot berlisensi tak jelas) | Larangan distribusi, sengketa | S3 | `[ISI]` | Model permisif (ADR 001/008); gerbang lisensi `ml/data/LICENSES.md` | `[ISI]` | Tinjauan `LICENSES.md` per rilis; `DATA_LINEAGE.md` |

### E. Operasional, desain untuk anak, lain-lain
| ID | Bahaya → situasi berbahaya | Harm | S (usulan) | P | Kontrol di desain | Risiko sisa | Verifikasi |
|---|---|---|---|---|---|---|---|
| R-30 | **Supabase Free dijeda / tanpa backup / kuota habis** | Data pilot hilang; sinkron gagal; pengguna tak bisa daftar | S2 | `[ISI]` | App offline-first (jalur kritis tidak bergantung server); keep-alive terjadwal; `pg_dump` terenkripsi; SMTP kustom; rate limit | `[ISI]` | `tools/backup_db.sh` + uji pulih *(rencana)*; pertanyaan pg_cron `backend/CLAUDE.md` §15.7 |
| R-31 | **Sinkron outbox ganda/ konflik** → riwayat salah | Data anak tertukar/ ganda | S2 | `[ISI]` | `event_id` idempoten, append-only; retry/backoff (FR-100) | `[ISI]` | Tes idempotensi `sync_events` *(rencana)* |
| R-32 | **Salah anak** pada mode kader/kelas (daftar pseudonim tertukar) | Hasil/rujukan salah ke keluarga lain; kebocoran | S3 | `[ISI]` | Roster pseudonim; persetujuan per murid sebelum foto; RLS kader hanya kelasnya | `[ISI]` | Uji RLS kader *(rencana)*; alur konfirmasi anak `[ISI]` |
| R-33 | **Gamifikasi sikat gigi menimbulkan rasa aman semu atau tekanan/ curang** | Anggapan "gigi pasti sehat"; stres anak | S2 | `[ISI]` | Hanya "skor semangat", tanpa klaim klinis/akurasi; anti-curang (variasi gerak); streak tidak patah oleh hari berjalan; tanpa iklan/profiling, maskot orisinal (PP Tunas) | `[ISI]` | Tes perilaku `ml/brushing/tests`; tinjauan UX anak `[ISI]` |
| R-34 | **Instruksi tidak dipahami** (literasi rendah, audio, a11y) | Pemakaian salah (mis. jarak senter) atau abai gejala | S2 | `[ISI]` | Bahasa kelas 4 SD, ikon+teks, audio ber-label AI, TalkBack, area sentuh 48dp (FR-120/121) | `[ISI]` | Uji 10 orang tua/ kader (FR-20); uji dengar 5 anak (A9) |
| R-35 | **Kegagalan komponen native kamera/ML** (modul komunitas tak terawat) atau performa rendah di HP kelas bawah | Fitur foto/sikat tak jalan; hasil lambat | S2 | `[ISI]` | Spike S1–S5; fallback timer tanpa kamera (FR-61); kuesioner red flag tetap jalan tanpa kamera | `[ISI]` | Hasil spike `[ISI]` |
| R-36 | **Dipasarkan/diklaim di luar tujuan** (akurasi, "diagnosis") atau dianggap SaMD tanpa izin | Penegakan regulasi; penarikan | S4 | `[ISI]` | ADR 002: pilot di bawah etik, klaim sebatas indikasi, daftar frasa terlarang, opini hukum tertulis | `[ISI]` | `OPEN_LEGAL_QUESTIONS.md` Q-03; pendapat hukum `[PENDING]` |
| R-37 | **Video bukti sikat gigi anak bocor/ disalahgunakan** (ADR 017) | Video mulut/wajah anak terbuka bagi pihak tak berwenang atau tersimpan lebih lama dari janji | S4 | `[ISI]` | Bucket privat; unggah hanya oleh akun anak ke path terdaftar; baca hanya pemilik (signed URL <= 5 menit); tanpa akses kader/ fasilitas/ admin; consent `d` + flag `brushing_proof_video`; retensi 7 hari + sapu harian; hapus seketika oleh orang tua; tidak untuk pelatihan | `[ISI]` | pgTAP `supabase/tests/12_child_rls.test.sql` dan `06_storage.test.sql`; uji `retention-sweep`/`process-deletion`; tinjauan hukum Q-15/Q-18 `[PENDING]` |
| R-38 | **Akun anak diambil alih / kredensial anak bocor atau username berisi nama asli** (ADR 017) | Pihak lain mencatat sikat gigi palsu, mengirim permintaan skrining, atau mengunggah video atas nama anak; identitas anak terungkap | S3 | `[ISI]` | Hak akun anak sempit di RLS; password dibuat orang tua + kebijakan sandi; throttle login per hash(email orang tua + ip) + CAPTCHA; respons gagal seragam; nonaktifkan seketika oleh orang tua; UI mengingatkan nama panggilan | `[ISI]` | pgTAP `12_child_rls`; uji Deno `child-login`; uji HTTP nyata setelah deploy `[PENDING]` |

## Tinjauan risiko
| Tanggal | Peninjau | Peran | Hasil | Tindak lanjut |
|---|---|---|---|---|
| `[ISI]` | `[PENDING]` drg pembimbing | Klinis | `[ISI]` | `[ISI]` |
| `[ISI]` | `[ISI]` | Hukum/regulasi | `[ISI]` | `[ISI]` |

## Aturan penggunaan
- Bahaya baru dari keluhan/insiden/uji lapangan → tambah baris (ID berlanjut, jangan menggeser ID).
- Mengubah S/P/risiko sisa → catat tanggal dan peninjau di tabel tinjauan; jangan menimpa tanpa jejak.
- Tidak ada kontrol yang dihitung "efektif" sampai kolom Verifikasi menunjuk bukti yang benar-benar ada.
