# Uji pemulihan backup (restore check) — DentisAI

> Untuk pemilik produk / admin. Tujuan: **membuktikan bahwa backup benar-benar bisa dipulihkan**. Backup yang belum pernah dites = belum punya backup.
> Supabase Free **tidak punya backup otomatis** (ADR 008). Satu-satunya cadangan adalah hasil `tools/backup_db.sh`.

**ATURAN EMAS: pemulihan hanya dites di database percobaan (scratch) di komputer sendiri. JANGAN PERNAH memulihkan ke database produksi untuk "mencoba".**

---

## 1. Yang dibutuhkan

| Alat | Fungsi | Cek |
|---|---|---|
| `age` | membuka enkripsi backup | `age --version` |
| `pg_restore`, `psql`, `createdb` | memulihkan ke Postgres | `pg_restore --version` (versi klien harus >= versi server Supabase) |
| Postgres lokal kosong | database percobaan | Docker: `docker run --name scratch-pg -e POSTGRES_PASSWORD=scratch -p 54329:5432 -d postgres:17` |
| **Kunci privat age** | membuka backup | disimpan **offline** (pengelola kata sandi + salinan di flash disk terpisah). Tidak pernah ada di repo, `.env`, atau CI |

Berkas backup: `dentisai-YYYYMMDDTHHMMSSZ.dump.age` (+ `.sha256`) di folder `BACKUP_OUTPUT_DIR` (di luar repo, sebaiknya disk terenkripsi).

## 2. Langkah uji pemulihan (± 15 menit, lakukan **sebulan sekali** dan setelah mengubah skema)

Ganti nama berkas sesuai backup yang diuji. Jalankan di Git Bash.

```bash
# 0. Pindah ke folder kerja sementara di DISK TERENKRIPSI (bukan di dalam repo)
cd /d/backup-dentisai          # contoh: sama dengan BACKUP_OUTPUT_DIR
F=dentisai-20261008T031700Z.dump.age

# 1. Cek integritas berkas (harus tertulis OK)
sha256sum -c "$F.sha256"

# 2. Buka enkripsi. Kunci privat diambil dari penyimpanan offline -> mis. G:\kunci\dentisai-age.txt
age --decrypt -i /g/kunci/dentisai-age.txt -o restore_check.dump "$F"

# 3. Pastikan target adalah database LOKAL (pengaman wajib; hentikan bila hasilnya bukan localhost)
TARGET="postgresql://postgres:scratch@localhost:54329/restore_check"
case "$TARGET" in
  *@localhost:*|*@127.0.0.1:*) echo "target lokal OK" ;;
  *) echo "STOP: target bukan localhost"; exit 1 ;;
esac

# 4. Buat database percobaan kosong lalu pulihkan
psql "postgresql://postgres:scratch@localhost:54329/postgres" -c "drop database if exists restore_check" -c "create database restore_check"
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$TARGET" restore_check.dump
```

> Bila `--exit-on-error` berhenti karena **role/ekstensi khusus Supabase** (mis. `supabase_auth_admin`, `pgsodium`), jalankan ulang tanpa `--exit-on-error` dan catat setiap galat. Galat yang hanya soal role/ekstensi dapat diterima; galat pada tabel `public.*` **tidak**. *[PERLU VERIFIKASI pada backup nyata pertama — skrip belum pernah dijalankan terhadap database Supabase sungguhan.]*

```bash
# 5. Pemeriksaan isi (angka harus masuk akal dibanding produksi, tanpa menampilkan data pribadi)
#    Nama tabel mengikuti PRD §9.3; sesuaikan bila migrasi di supabase/migrations berbeda.
psql "$TARGET" -Atc "select count(*) from public.guardian"
psql "$TARGET" -Atc "select count(*) from public.child_profile"
psql "$TARGET" -Atc "select count(*) from public.screening"
psql "$TARGET" -Atc "select count(*) from public.consent"
psql "$TARGET" -Atc "select count(*) from public.deletion_request"
psql "$TARGET" -Atc "select pg_size_pretty(pg_database_size(current_database()))"
# Setelah migrasi baru: pastikan RLS tetap aktif di semua tabel public (hasil harus 0 baris)
psql "$TARGET" -Atc "select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and not c.relrowsecurity"

# 6. BERSIHKAN: hapus berkas yang sudah dibuka (berisi data kesehatan anak) dan database percobaan
rm -f restore_check.dump
psql "postgresql://postgres:scratch@localhost:54329/postgres" -c "drop database restore_check"
```

### Hak hapus (UU PDP) — penting
Backup lama masih menyimpan data pengguna yang **sudah meminta dihapus**. Karena itu:
1. Simpan backup dengan batas waktu: `bash tools/backup_db.sh --keep 8` (mis. 8 backup mingguan ≈ 2 bulan). Backup yang lebih lama otomatis dihapus.
2. **Jika backup dipulihkan sungguhan**, segera jalankan ulang semua `deletion_request` yang tercatat setelah tanggal backup itu sebelum aplikasi dibuka kembali.

### Catatan hasil uji (isi tiap kali, tanpa data pribadi)
```
Tanggal uji : 2026-__-__        Berkas: dentisai-__________.dump.age
Checksum OK : ya/tidak           Pemulihan berhasil: ya/tidak (galat yang diabaikan: ...)
Jumlah baris: guardian=__ child_profile=__ screening=__ consent=__
Diuji oleh  : ________           Catatan: ...
```

## 3. Cakupan backup (apa yang TIDAK ikut)
- `pg_dump` hanya menyimpan **database** (skema `public`, `auth`, `storage` secara bawaan). **Berkas di Storage tidak ikut**, hanya baris metadatanya (`storage.objects`).
- Bucket `manifests`: dapat dibuat ulang dari `ml/exports/` dan `manifest.json` (ditandatangani ulang dengan kunci offline).
- Bucket `dataset_photos` (foto anak, hanya bila `dataset_contribution` aktif setelah KEPK menyetujui): **tidak** ada di backup ini. Ambil terpisah dengan `tools/export_dataset.py` (perlu `--i-have-ethics-approval`), simpan di disk terenkripsi.
- Pengaturan Auth (SMTP, URL redirect, CAPTCHA) ada di dashboard, bukan di dump: catat di dokumen terpisah tanpa rahasia.

## 4. Batas 500 MB (Supabase Free)
Cek ukuran kapan saja:
```bash
bash tools/backup_db.sh --check-size        # peringatan >= 400 MB, KRITIS >= 475 MB
```
atau di SQL Editor Supabase:
```sql
select pg_size_pretty(pg_database_size(current_database())) as total;
select relname, pg_size_pretty(pg_total_relation_size(relid)) as ukuran
from pg_catalog.pg_statio_user_tables
order by pg_total_relation_size(relid) desc limit 10;
```
Bila mendekati batas: hapus/anonimkan data lama sesuai kebijakan retensi (Edge Function/pg_cron), jangan menyimpan gambar di database, lalu rencanakan tier berbayar. *[PERLU VERIFIKASI: apakah angka dashboard Supabase memakai metrik yang sama dengan `pg_database_size`.]*

## 5. Penjadwalan

Frekuensi yang disarankan: **mingguan** + **sebelum setiap migrasi** (`supabase db push`). Komputer harus menyala dan terhubung internet.

### Windows (Task Scheduler)
Jalankan sekali di PowerShell (sesuaikan jalur repo; skrip membaca `backend/.env` sendiri, jadi tidak perlu menyetel variabel lingkungan):
```powershell
schtasks /Create /TN "DentisAI Backup" /SC WEEKLY /D SUN /ST 02:00 /F `
  /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc \"cd /d/Projects/dentisin/backend && bash tools/backup_db.sh --keep 8 >> /d/backup-dentisai/backup.log 2>&1\""
```
Lalu di aplikasi Task Scheduler: *Properties → Settings → "Run task as soon as possible after a scheduled start is missed"* dicentang, dan jalankan sebagai akun Anda sendiri. Berkas log hanya berisi baris status (tanpa kata sandi dan tanpa data), tetapi tetap simpan di luar repo.

### Linux / macOS (cron)
```cron
0 2 * * 0  cd /home/USER/dentisin/backend && bash tools/backup_db.sh --keep 8 >> /home/USER/dentisai-backup.log 2>&1
```

### Pengingat bulanan
Tambahkan kalender "Uji pemulihan DentisAI" tiap tanggal 1: jalankan bagian 2 di atas. Jika uji gagal, anggap **tidak ada backup** dan perbaiki segera.

## 6. Pemecahan masalah

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| `exit 2` "missing environment variable" | `.env` belum diisi | Isi `SUPABASE_DB_URL`, `BACKUP_AGE_RECIPIENT`, `BACKUP_OUTPUT_DIR` |
| `exit 3` | `age` atau `pg_dump` belum terpasang | Pasang; versi `pg_dump` harus >= versi server |
| `exit 4` | folder backup di dalam repo / tidak ada | Pilih folder di luar repo (disk terenkripsi) dan buat dulu |
| `exit 5` + "connection" | URL salah, kata sandi salah, atau jaringan tanpa IPv6 | Koneksi langsung `db.<ref>.supabase.co` **hanya IPv6** pada paket Free; bila jaringan Anda IPv4, pakai *connection pooler* (mode session, port 5432) dari dashboard *[PERLU VERIFIKASI]* |
| `exit 6` | keluaran bukan berkas age yang valid | Jangan dipakai; periksa versi `age`, ulangi |
| Proyek "paused" | keep-alive gagal | Lihat bagian *keepalive* di `tools/README.md`; pulihkan proyek dari dashboard |
