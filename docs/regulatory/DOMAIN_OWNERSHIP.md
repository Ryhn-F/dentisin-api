# Kepemilikan domain `ryhnfir.dev`

> Mengikuti ADR 008 (putaran 3, baris "Kepemilikan"): domain **dimiliki pribadi oleh pemilik produk**; catat kepemilikan, kontak registrar, rencana pengalihan, dan salinan pengaturan DNS bila tim/ badan hukum berubah. Nilai yang tidak diketahui repo = `[ISI]`. **Jangan menaruh kata sandi, kode otorisasi/ EPP, API key, atau token di berkas ini.**

## Fakta yang diketahui dari repo
| Hal | Isi | Sumber |
|---|---|---|
| Domain | `ryhnfir.dev` | ADR 008 |
| Pemilik | Pemilik produk, **secara pribadi** (bukan badan hukum/ tim) | ADR 008 |
| Subdomain pengirim email auth | `auth.ryhnfir.dev` (pengirim `no-reply@auth.ryhnfir.dev`, nama "DentisAI"); penyedia Resend Free | ADR 008; `backend/CLAUDE.md` §6 |
| Halaman redirect konfirmasi/ reset | `https://app.ryhnfir.dev/auth/callback` (host statis gratis; membuka app lewat `dentisai://`) | `backend/CLAUDE.md` §6 |
| Biaya | Rp0 selama memakai domain yang sudah dimiliki; **perpanjangan tahunan ditanggung pemilik** | ADR 008 |
| Ketergantungan | Email konfirmasi/ reset akun (jalur auth) dan halaman callback bergantung pada domain ini | ADR 008 |

## Dicatat oleh pemilik `[ISI]`
| Hal | Isi |
|---|---|
| Registrar | `[ISI]` |
| Nama pemegang terdaftar (registrant) — bila WHOIS privacy aktif, catat di sini, jangan di repo publik | `[ISI]` |
| Email kontak registrar / pemulihan akun | `[ISI]` (sebaiknya alamat yang bukan di `ryhnfir.dev` itu sendiri, agar pemulihan tetap mungkin bila domain bermasalah) |
| Tanggal kedaluwarsa & perpanjangan otomatis? | `[ISI]` |
| Kunci registrar / 2FA di akun registrar | `[ISI]` (aktifkan; simpan kode pemulihan di pengelola kata sandi, bukan repo) |
| Penyedia DNS (nameserver) | `[ISI]` |
| Pihak yang punya akses darurat (orang kedua tepercaya) | `[ISI]` |

## Salinan DNS (isi setelah ada; **perbarui setiap perubahan**)
Salin dari dasbor DNS/ Resend; jangan mengarang nilai. Berisi nama/ tipe/ nilai (nilai DKIM publik boleh; tidak ada rahasia).
| Nama | Tipe | Nilai | TTL | Tujuan | Terakhir diverifikasi |
|---|---|---|---|---|---|
| `auth` (SPF/MX/bounce untuk Resend) | `[ISI]` | `[ISI]` | `[ISI]` | Verifikasi domain Resend | `[ISI]` |
| `resend._domainkey.auth` (DKIM) | TXT/CNAME `[ISI]` | `[ISI]` | `[ISI]` | DKIM | `[ISI]` |
| `_dmarc.auth` | TXT | `[ISI]` (mulai `p=none`, naik ke `quarantine` setelah laporan bersih — ADR 008) | `[ISI]` | DMARC | `[ISI]` |
| `app` | CNAME/A `[ISI]` | `[ISI]` | `[ISI]` | Halaman callback statis | `[ISI]` |
| `_acme-challenge`/ verifikasi hosting | `[ISI]` | `[ISI]` | `[ISI]` | Sertifikat TLS/ hosting | `[ISI]` |

Lokasi cadangan salinan DNS (di luar repo publik): `[ISI]`.

## Rencana pengalihan (bila tim/ badan hukum berubah)
Pemicu: pembentukan badan hukum/ yayasan; pemilik produk berganti; mitra mensyaratkan aset di bawah organisasi; akhir masa lomba/ pilot.
1. **Putuskan pemilik baru** (nama entitas) dan tulis di ADR baru; hindari beberapa orang memegang kunci akun yang sama.
2. **Catat keadaan saat ini**: ekspor seluruh zona DNS; simpan tangkapan layar pengaturan registrar & Resend (tanpa rahasia).
3. **Kurangi risiko sebelum pindah**: turunkan TTL; pastikan perpanjangan aktif; verifikasi akses pemulihan registrar.
4. **Pindah**: transfer akun/ domain ke pemilik baru lewat fitur registrar (kode transfer disampaikan lewat kanal aman, tidak lewat repo/ chat terbuka). Jangan membiarkan domain tidak aktif (email auth berhenti).
5. **Setelah pindah**: periksa DNS identik, SPF/DKIM/DMARC lulus di Resend, kirim uji email konfirmasi/ reset, uji halaman `app.ryhnfir.dev/auth/callback` dan deep link; perbarui `Site URL`/ `Redirect URLs` Supabase bila domain berubah.
6. **Dokumentasi**: perbarui berkas ini, `PRIVACY_NOTICE_DRAFT.md` (identitas pengelola data), `CONSENT_TEXT_DRAFT.md`, dan ADR.
7. Bila domain **diganti** (bukan dipindah): ubah pengirim SMTP, Redirect URLs Supabase, deep link/ App Links `/.well-known/assetlinks.json`, dan rilis app baru bila ada domain yang tertanam; pengguna lama tetap perlu tautan konfirmasi/ reset yang valid.

## Risiko
Domain dimiliki pribadi → titik kegagalan tunggal (kehilangan akses akun = auth email tidak berfungsi). Tercatat di `RISK_REGISTER.md` R-30 (operasional) — tambahkan risiko khusus domain bila dianggap material `[ISI]`.
