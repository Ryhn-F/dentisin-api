# Panduan pemilik: menyalakan email konfirmasi & reset kata sandi (Auth)

> Untuk **pemilik produk** (tidak perlu ahli). Rujukan: ADR 008 putaran 3, `../IMPLEMENTATION_PLAN.md` item 1.2/1.3, `../../supabase/config.toml`.
> Hasil akhir: orang tua mendaftar di app → email konfirmasi masuk **kotak masuk (bukan spam)** → ketuk tautan → app terbuka; lupa kata sandi → email reset → app terbuka.
> **Jangan** menempelkan API key, secret, atau isi `.env` ke chat, GitHub, atau dokumen. Semua nilai rahasia hanya di `backend/.env` dan dasbor penyedia.

## Urutan kerja (jangan dibalik)
`A` Resend + DNS → `B` isi `.env` → `C` kunci CAPTCHA → `D` halaman redirect → `E` push konfigurasi → `F` uji.
Alasan: bila konfigurasi dikirim (E) sebelum domain Resend terverifikasi (A), semua pendaftaran gagal karena email tidak bisa terkirim.

Perubahan **belum** dikirim ke proyek Supabase mana pun. Semua yang ada di repo ini hanya berkas konfigurasi.

---

## A. Resend: domain `auth.ryhnfir.dev`, DNS, dan API key

1. Buka <https://resend.com>, daftar (gratis). Paket Free: **3.000 email/bulan, maksimal 100 email/hari**.
2. Menu **Domains → Add Domain**. Isi nama: `auth.ryhnfir.dev` (subdomain khusus, supaya reputasi domain utama `ryhnfir.dev` terlindungi). Pilih region terdekat yang ditawarkan (mis. Tokyo/Singapura bila ada). Klik **Add**.
3. Resend menampilkan **tabel rekaman DNS**. **Salin persis dari tabel itu** (nilai berbeda untuk tiap akun). Bentuknya kira-kira (hanya contoh, *jangan* disalin dari sini):

   | Tipe | Nama (Host) | Fungsi | Nilai |
   |---|---|---|---|
   | **TXT** | `resend._domainkey.auth` | **DKIM** (tanda tangan email) | teks panjang `p=MIGfMA0...` dari Resend |
   | **MX** | `send.auth` | jalur balasan/bounce | `feedback-smtp.<region>.amazonses.com`, prioritas `10` |
   | **TXT** | `send.auth` | **SPF** (daftar pengirim sah) | `v=spf1 include:amazonses.com ~all` |

4. Tambahkan ketiganya di **tempat DNS `ryhnfir.dev` Anda dikelola** (registrar atau Cloudflare DNS; lihat di mana *nameserver* domain menunjuk). Tips:
   - Kolom **Name/Host**: sebagian panel menambahkan `.ryhnfir.dev` otomatis. Jika Anda mengetik `send.auth`, hasil akhirnya harus `send.auth.ryhnfir.dev`, **bukan** `send.auth.ryhnfir.dev.ryhnfir.dev`.
   - Di Cloudflare, rekaman email harus **DNS only** (awan abu-abu).
   - Salin nilai TXT panjang tanpa spasi/baris baru tambahan.
5. Kembali ke Resend → **Verify DNS Records**. Penyebaran DNS bisa beberapa menit sampai beberapa jam. Tunggu status **Verified** untuk semua baris.
6. **DMARC** (perlindungan agar email palsu atas nama domain Anda ditolak). Tambahkan satu rekaman lagi:

   | Tipe | Nama | Nilai |
   |---|---|---|
   | **TXT** | `_dmarc.auth` | `v=DMARC1; p=none; rua=mailto:ALAMAT-EMAIL-YANG-ANDA-BACA@contoh.com` |

   - Ganti `rua=` dengan alamat email yang benar-benar Anda baca (laporan ringkasan harian akan dikirim ke sana).
   - `p=none` = hanya memantau (cukup untuk awal). **Rencana penguatan:** setelah 2–4 minggu dan laporan menunjukkan email DentisAI lolos (SPF & DKIM *pass*), ganti menjadi `p=quarantine; pct=25`, naikkan `pct` ke 50 lalu 100 dalam beberapa minggu. `p=reject` boleh nanti. Catat tanggal tiap perubahan di `docs/regulatory/`.
7. **API key**: menu **API Keys → Create API Key**.
   - Name: `supabase-auth-smtp`; **Permission: Sending access** (bukan Full access); **Domain: auth.ryhnfir.dev**.
   - Salin key (`re_...`) **saat itu juga** (hanya tampil sekali). Simpan di pengelola kata sandi dan di `.env` (langkah B).
   - Jika key bocor: hapus di Resend, buat baru, perbarui `.env` dan ulangi langkah E.

Pengirim yang dipakai aplikasi: `no-reply@auth.ryhnfir.dev`, nama tampil **DentisAI** (sudah di `config.toml`; SMTP `smtp.resend.com`, port `465`, user `resend`, password = API key).

---

## B. Isi `backend/.env`

Berkas ini rahasia, sudah di-`.gitignore`. Isi (tanpa tanda kutip):

| Variabel | Isi |
|---|---|
| `RESEND_API_KEY` | key dari langkah A.7 |
| `AUTH_SITE_URL` | `https://app.ryhnfir.dev/auth/callback` |
| `AUTH_REDIRECT_URL` | `https://app.ryhnfir.dev/auth/callback` (sama; tautan `dentisai://auth/callback` sudah tertulis tetap di `config.toml`) |
| `CAPTCHA_SECRET` | **Secret key** Turnstile (langkah C). Bukan *site key* |
| `CAPTCHA_PROVIDER` | `turnstile` (hanya catatan; penyedia kini tertulis tetap di `config.toml`) |
| `AUTH_SMTP_ENABLED` | **`false`** (lihat kotak di bawah) |
| `AUTH_CAPTCHA_ENABLED` | **`false`** (lihat kotak di bawah) |

> **Mengapa dua saklar `false`?** Supabase CLI ikut membaca `backend/.env`. Tanpa saklar, `supabase start` di laptop akan memakai API key Resend asli dan **mengirim email sungguhan** saat pendaftaran uji. Dengan `false`: email lokal masuk ke Mailpit (<http://127.0.0.1:54324>) dan CAPTCHA mati di lokal. Kedua saklar **wajib ada**; bila kosong/hilang, CLI berhenti dengan galat `CliConfigParseError` (sengaja: lebih baik gagal daripada mengirim email asli). Nilai `true` **hanya** diketik di baris perintah saat push (langkah E), tidak disimpan di `.env`.

---

## C. Kunci CAPTCHA (rekomendasi: Cloudflare Turnstile)

Pilih **Turnstile**: gratis tanpa batas, tanpa kartu kredit, jarang menampilkan teka-teki ke pengguna. (hCaptcha juga gratis, tetapi lebih sering menampilkan tantangan gambar dan akun perlu verifikasi tambahan.)

1. Daftar/masuk <https://dash.cloudflare.com> → **Turnstile → Add widget**.
2. Widget name: `DentisAI auth`. **Hostname**: `app.ryhnfir.dev` (alamat tempat widget ditampilkan). Mode: **Managed**. Create.
3. Anda mendapat **dua** nilai, jangan tertukar:
   - **Site key** — *publik*, dipakai di app/halaman widget (diberikan ke tim frontend).
   - **Secret key** — *rahasia*, masuk `CAPTCHA_SECRET` di `.env` (dan dipakai Supabase untuk memeriksa token).
4. Catatan untuk tim frontend: app React Native menampilkan widget lewat **WebView** (halaman kecil di domain yang diizinkan) lalu mengirim token ke Supabase (`captchaToken`) pada daftar, masuk, dan reset kata sandi. Tanpa ini, setelah CAPTCHA diaktifkan di server semua pendaftaran ditolak. Keputusan: di mana halaman widget di-hosting (usul: `https://app.ryhnfir.dev/captcha/`).
5. Uji tanpa risiko: Cloudflare menyediakan kunci uji resmi (secret `1x0000000000000000000000000000000AA` selalu lolos). Pakai hanya di lokal, lihat bagian "Pengembangan lokal".

Untuk hCaptcha: ubah `provider = "hcaptcha"` di `[auth.captcha]` pada `config.toml` dan pakai kunci hCaptcha.

---

## D. Halaman redirect `https://app.ryhnfir.dev/auth/callback`

Tautan di email membuka halaman web ini; halaman meneruskan *kode sekali pakai* ke app lewat deep link `dentisai://auth/callback`. Berkasnya sudah disediakan: [`web/auth/callback/index.html`](../../web/auth/callback/index.html) (dan `web/captcha/` untuk Turnstile; lihat `web/README.md` untuk deploy Cloudflare Pages) (tanpa pelacak, tanpa permintaan keluar, menghapus kode dari bilah alamat, ada pesan cadangan berbahasa Indonesia).

Struktur yang harus dipublikasikan (satu berkas saja):
```
auth/callback/index.html     <- salin dari web/auth/callback/index.html
```

**Opsi 1 (paling mudah): Cloudflare Pages, unggah langsung, tanpa Git.**
1. Buat folder kosong di komputer, mis. `callback-site/auth/callback/` dan salin `index.html` ke dalamnya.
2. Cloudflare → **Workers & Pages → Create → Pages → Upload assets**. Beri nama proyek `dentisai-callback`, unggah folder `callback-site` (yang berisi folder `auth`), **Deploy**.
3. Proyek → **Custom domains → Set up a custom domain** → `app.ryhnfir.dev`. Ikuti petunjuk (bila DNS `ryhnfir.dev` ada di Cloudflare, rekaman dibuat otomatis; bila di tempat lain, tambah `CNAME app → dentisai-callback.pages.dev`).

**Opsi 2: GitHub Pages** (repositori **publik** diperlukan pada paket gratis; berkas ini tidak berisi rahasia).
1. Repositori baru **terpisah** `dentisai-auth-callback` (jangan repo backend). Letakkan berkas di `auth/callback/index.html`. Settings → Pages → Branch `main` / root.
2. Pages → Custom domain `app.ryhnfir.dev`, centang **Enforce HTTPS**. Di DNS: `CNAME app → <username-github>.github.io`.

Cek: buka `https://app.ryhnfir.dev/auth/callback?code=uji#x=1` di HP yang sudah terpasang app; app harus terbuka (atau tombol "Buka aplikasi DentisAI" muncul). Di komputer akan tampil pesan cadangan. Pada GitHub Pages, alamat tanpa garis miring akhir diarahkan otomatis ke `/auth/callback/` dengan query tetap terbawa; ini normal.

Opsional nanti: Android App Links (`/.well-known/assetlinks.json`) supaya tautan membuka app langsung tanpa halaman perantara.

---

## E. Mengirim konfigurasi ke Supabase (`config push`) — SETELAH A–D selesai

> **Peringatan.** `supabase config push` **menimpa** pengaturan Auth (dan bagian lain) di dasbor Supabase dengan isi `config.toml`. Perubahan yang pernah Anda klik manual di dasbor akan hilang. Perintah ini **mengubah proyek produksi**. Selalu baca ringkasan perbedaan (*diff*) yang ditampilkan dan jawab `n` bila ada yang tidak Anda kenali.

Dari folder `backend/` (Git Bash):
```bash
npx supabase@2.120.0 login                      # sekali saja; atau pakai SUPABASE_ACCESS_TOKEN di .env
AUTH_SMTP_ENABLED=true AUTH_CAPTCHA_ENABLED=true \
  npx supabase@2.120.0 config push --project-ref <SUPABASE_PROJECT_REF>
```
PowerShell: `$env:AUTH_SMTP_ENABLED="true"; $env:AUTH_CAPTCHA_ENABLED="true"; npx supabase@2.120.0 config push --project-ref <ref>` lalu `Remove-Item Env:AUTH_SMTP_ENABLED, Env:AUTH_CAPTCHA_ENABLED` setelahnya.
(Nilai lain — `RESEND_API_KEY`, `CAPTCHA_SECRET`, `AUTH_SITE_URL`, `AUTH_REDIRECT_URL` — dibaca dari `backend/.env`.)

**Periksa diff sebelum menjawab `y`:**
- `site_url` dan `additional_redirect_urls` berisi alamat asli (`https://app.ryhnfir.dev/auth/callback`, `dentisai://auth/callback`) — **bukan** teks `env(AUTH_SITE_URL)` dan bukan kosong. Jika masih `env(...)`, batalkan dan hubungi tim backend.
- SMTP: host `smtp.resend.com`, port `465`, user `resend`, pengirim `no-reply@auth.ryhnfir.dev`, nama `DentisAI`. Password tidak ditampilkan (wajar).
- CAPTCHA aktif, provider `turnstile`.
- Konfirmasi email aktif; panjang kata sandi minimum 10; `anonymous sign-ins` mati; 3 jenis MFA mati.
- Rate limit email `30`/jam.

Setelah `config push`: di dasbor **Authentication → Providers → Email** dan **Settings → SMTP**, pastikan tampil custom SMTP aktif. Tidak perlu `db push` (skema tidak berubah).

---

## F. Daftar uji (centang satu per satu)

Lakukan setelah E, dengan alamat Gmail pribadi (bukan alamat `.invalid`):
- [ ] Daftar akun baru di app (atau memanggil API) → **tidak ada galat CAPTCHA**.
- [ ] Email "Konfirmasi email akun DentisAI" tiba **di Kotak Masuk** (bukan Spam) dalam < 1 menit; pengirim `DentisAI <no-reply@auth.ryhnfir.dev>`.
- [ ] Gmail → titik tiga → **Tampilkan asli** (*Show original*): SPF = **PASS**, DKIM = **PASS**, DMARC = **PASS**.
- [ ] Ketuk tautan di HP → halaman "Membuka aplikasi DentisAI" → app terbuka dan akun terkonfirmasi.
- [ ] Masuk dengan email + kata sandi (kata sandi < 10 karakter atau tanpa huruf besar/kecil/angka harus ditolak).
- [ ] "Lupa kata sandi" → email "Atur ulang kata sandi DentisAI" tiba → tautan membuka app → kata sandi baru berhasil, masuk dengan kata sandi baru.
- [ ] Tautan yang sudah dipakai/kedaluwarsa (1 jam) menampilkan pesan galat yang ramah di app, bukan layar kosong.
- [ ] Resend → **Emails**: setiap pengiriman berstatus *Delivered*.
- [ ] Isi email hanya tautan; tidak ada nama/foto/data anak.

**Batas harian.** Resend Free hanya mengirim **100 email/hari** (3.000/bulan). Setiap pendaftaran = 1 email, setiap reset = 1 email. Supabase dibatasi 30 email/jam (`email_sent`) dan 1 email/60 detik per pengguna. Bila hari pendaftaran massal (mis. satu sekolah) diperkirakan > 100 orang: jadwalkan pendaftaran bertahap, atau siapkan cadangan (Brevo ±300/hari, atau tier berbayar Resend). Bila batas habis, pengguna melihat galat kirim email; catat di `docs/regulatory/` bila kejadian.

---

## Pengembangan lokal (untuk tim, tanpa email asli)
- Dengan `AUTH_SMTP_ENABLED=false` dan `AUTH_CAPTCHA_ENABLED=false` di `.env` (lihat B), `supabase start` memakai Mailpit: lihat email di <http://127.0.0.1:54324>.
- Uji CAPTCHA lokal dengan kunci uji Cloudflare (hanya di baris perintah, jangan di `.env`):
  `AUTH_CAPTCHA_ENABLED=true CAPTCHA_SECRET=1x0000000000000000000000000000000AA npx supabase@2.120.0 start`

## Keputusan yang masih menunggu pemilik
1. **CAPTCHA**: setuju Turnstile (rekomendasi) atau hCaptcha? Siapa membuat halaman widget untuk WebView?
2. **Anonymous sign-in**: dibiarkan **mati** (mode anak tidak butuh akun; menghemat MAU & permukaan serangan). Ubah hanya bila ingin akun tamu yang dinaikkan ke email.
3. **Kata sandi**: minimal 10 karakter + huruf besar, kecil, angka. Terlalu berat untuk sebagian orang tua? Alternatif: minimal 8 (`minimum_password_length = 8`).
4. **Batas email 30/jam** dan jadwal naik DMARC (`none` → `quarantine`).
5. **Dua proyek** (dev & prod): sekarang push mengubah satu-satunya proyek; item 1.4 menyarankan memisahkannya sebelum pengguna nyata.

## Setiap `config push` ke dev/prod: set tiga saklar secara inline
`backend/.env` menyimpan `false` untuk ketiganya supaya `supabase start` lokal memakai Mailpit. Saat **push**, atur inline, jika tidak hook akan **mati** karena config dianggap `false`:

```bash
export AUTH_SMTP_ENABLED=false AUTH_CAPTCHA_ENABLED=false AUTH_SEND_EMAIL_HOOK_ENABLED=true
npx supabase config push --project-ref <ref>      # periksa diff, jawab y
```

- `AUTH_SEND_EMAIL_HOOK_ENABLED=true`: email dikirim oleh mailer (`https://mailer.ryhnfir.dev/hooks/send-email`, repo `smtp-handler`); `AUTH_SMTP_ENABLED` tetap `false` karena SMTP bawaan Supabase tidak dipakai.
- `AUTH_CAPTCHA_ENABLED=true` hanya setelah app mengirim token Turnstile (halaman `web/captcha/`).
- Mailer di Vercel harus `configured:true` (`/healthz`) dan `SEND_EMAIL_HOOK_SECRET`-nya sama dengan yang di Supabase.
