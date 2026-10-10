# web/ — situs statis untuk Cloudflare Pages (`app.ryhnfir.dev`)

Isi: `auth/callback/` (tautan email → membuka app lewat `dentisai://`), `captcha/` (widget Cloudflare Turnstile untuk WebView).

## Deploy (Cloudflare Pages, gratis)
1. Cloudflare Dashboard → Workers & Pages → Create → Pages → **Direct Upload** (atau hubungkan repo ini; Build command kosong, Output directory `web`).
2. Upload folder `web/`. Beri nama proyek `dentisai-app`.
3. Custom domains → tambahkan `app.ryhnfir.dev` (jika DNS `ryhnfir.dev` ada di Cloudflare, otomatis; jika tidak, tambah CNAME ke `dentisai-app.pages.dev`).
4. Uji: buka `https://app.ryhnfir.dev/auth/callback/` (harus mencoba membuka app) dan `https://app.ryhnfir.dev/captcha/?sitekey=1x00000000000000000000AA` (kunci uji Turnstile: selalu lolos).

## Turnstile
Cloudflare Dashboard → Turnstile → Add widget → hostname `app.ryhnfir.dev` (+ `localhost` untuk uji) → Managed. Salin **Site key** (publik; dipakai app di URL `?sitekey=`) dan **Secret key** → isi `CAPTCHA_SECRET` di `backend/.env`, lalu push config dengan `AUTH_CAPTCHA_ENABLED=true`. JANGAN aktifkan CAPTCHA di server sebelum app memakai widget ini, atau pendaftaran gagal.
