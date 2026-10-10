# tools/tts — suara instruksi (build-time, ADR 012; Azure = cadangan, ADR 007)

`generate_audio.py` membuat berkas audio dari naskah anak. Penyedia bawaan: **Gemini API TTS** (`--provider gemini`). Azure Speech tetap ada sebagai cadangan (`--provider azure`). Dijalankan **di PC pengembang saat build**; aplikasi hanya membawa berkas hasilnya.

**Tidak ada panggilan TTS saat runtime. Tidak ada STT dari suara anak di v1.**

## Pernyataan risiko (ADR 012, wajib dibaca)

- Syarat tambahan Gemini API melarang penggunaan layanan sebagai bagian dari aplikasi yang ditujukan/mungkin diakses anak < 18 th dan memuat larangan saran medis **[PERLU VERIFIKASI teks terkini]**. Aplikasi ini ditujukan untuk anak dan bertema kesehatan gigi.
- Risiko ini **diterima secara sadar** oleh pemilik produk untuk pilot. **Sebelum rilis publik/komersial: minta konfirmasi tertulis dari Google, atau buat ulang semua audio dengan penyedia lain** (audio hanyalah berkas yang dapat dibuat ulang).
- Yang dikirim ke Gemini **hanya teks naskah statis yang sudah disetujui** (lolos lint frasa terlarang). **Tidak ada data anak, PII, foto, atau hasil skrining.** Jangan menaruh teks lain di naskah.
- Free tier Gemini API dapat memakai input untuk memperbaiki produk Google **[PERLU VERIFIKASI]**: aman karena naskah tidak sensitif.
- Layar **Tentang** wajib menyebut "suara dibuat dengan AI". `MANIFEST.json` membawa `ai_voice_label_required: true`.
- Pengecualian ADR 006 hanya untuk pembuatan aset build-time ini. **Chatbot/jalur runtime tetap tidak boleh memakai Gemini.**

## Langkah operator (Gemini)

1. Buka [Google AI Studio](https://aistudio.google.com/) dengan **akun khusus proyek** (bukan akun pribadi), buat proyek baru, lalu **Get API key**. Jangan menaruh kunci di chat, repo, atau tangkapan layar.
2. Isi `backend/.env` (berkas ini tidak pernah di-commit; skrip tidak pernah mencetak nilainya):
   ```
   GEMINI_API_KEY=<kunci Anda>
   GEMINI_TTS_MODEL=<nama model TTS dari dokumentasi>
   GEMINI_TTS_VOICE=<nama suara, mis. Kore>
   ```
   Bila model/suara dikosongkan, skrip memakai bawaan `gemini-3.8-flash-tts` dan `Kore` **[PERLU VERIFIKASI]**. Daftar model dan 30 suara berubah-ubah: cek [dokumentasi speech generation](https://ai.google.dev/gemini-api/docs/speech-generation) dan dengarkan beberapa suara sebelum memilih. Opsional `GEMINI_TTS_STYLE` (mis. `tenang dan jelas`), dikirim sebagai metadata gaya, **bukan** disisipkan ke teks (teks dibacakan apa adanya).
3. Uji tanpa jaringan dan tanpa kunci:
   ```bash
   cd backend/tools
   uv run python tts/generate_audio.py --script ../../app/assets/audio/script.csv --generated-under free --dry-run
   ```
4. Build sungguhan (`free` bila kunci dari free tier, `paid` bila akun berbayar):
   ```bash
   uv run python tts/generate_audio.py --script ../../app/assets/audio/script.csv --generated-under free
   ```
   Naskah produksi ada di app (`../app/assets/audio/script.csv`, kolom `id,text,approved_by`); tunjuk dengan `--script` atau `TTS_SCRIPT_PATH`. Tanpa itu skrip memakai `tools/tts/script.sample.csv` (semua baris `CONTOH`, ditolak tanpa `--allow-sample`). Audio `.wav` dan `MANIFEST.json` ditulis di folder naskah atau `--out-dir`.
5. Dengarkan semua berkas dan pastikan pengucapan bahasa Indonesia wajar sebelum menyerahkannya ke tim app.

## Alur dan aturan (semua penyedia)

1. Setiap baris wajib punya `approved_by` (drg/penanggung jawab). Kosong = ditolak. Placeholder `CONTOH/SAMPLE/TODO/PLACEHOLDER` ditolak tanpa `--allow-sample`.
2. Teks diperiksa `tools/lint_forbidden_phrases.py` (sumber daftar: lihat `tools/README.md`). Pelanggaran = ditolak, sebelum ada panggilan jaringan.
3. `--dry-run`: memvalidasi dan melaporkan rencana; **tanpa jaringan, tanpa kunci, tanpa menulis berkas**.
4. **Idempoten:** baris yang tidak berubah dilewati. Hash masukan mencakup **provider + model + suara + format + gaya + teks asli**; berkas harus ada dan checksum cocok. `--force` membuat ulang semua. Mengubah provider/model/suara/format/`--generated-under` memaksa pembuatan ulang semua berkas.
5. Berkas audio yang tidak ada lagi di naskah (termasuk `.mp3` lama setelah pindah ke Gemini) hanya diberi peringatan, tidak dihapus.

## Gemini: perilaku teknis

- `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, kunci **hanya** di header `x-goog-api-key` (tidak pernah di URL maupun log). Badan: `contents[].parts[].text`, `generationConfig.responseModalities=["AUDIO"]`, `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`.
- Respons: `candidates[0].content.parts[0].inlineData.data` (base64). Bila berupa PCM mentah (16-bit, mono, 24 kHz) dibungkus menjadi WAV dengan modul `wave`; bila sudah WAV dipakai apa adanya. Audio kosong, rusak, atau di luar 0,2 s sampai 180 s ditolak.
- Bahasa Indonesia dikenali otomatis oleh model; tidak ada awalan "Katakan dengan jelas:" di teks karena dokumentasi menyebut teks dibacakan harfiah.
- Retry dengan backoff untuk 429/5xx/kesalahan jaringan (hormati `Retry-After`, maks. 4 percobaan), timeout 90 detik, jeda `--request-delay` (bawaan 6 detik) antar permintaan, dan batas `--max-requests` (bawaan 100, termasuk retry) per run. Bila batas tercapai atau gagal di tengah, baris yang sudah jadi dicatat di `MANIFEST.json` dengan `complete: false`; jalankan ulang nanti dan hanya baris sisa yang diminta.
- Isi permintaan tidak pernah dicatat di log.
- **Free tier punya batas permintaan per menit/hari yang ketat dan dapat berubah [PERLU VERIFIKASI].** Bila sering kena 429, naikkan `--request-delay` atau jalankan beberapa kali.

### Status verifikasi bentuk API (2026-10-09)

Diperiksa terhadap halaman dokumentasi resmi (ai.google.dev speech-generation, halaman "Generate Content API (Legacy)"), **tetapi tidak diuji ke API sungguhan** (tidak ada kunci). Hal yang masih **[PERLU VERIFIKASI]** pada run pertama:

- Dokumentasi terbaru menaruh `generateContent` sebagai "Legacy" dan mengarahkan ke Interactions API (`/v1beta/interactions`); satu cuplikan dokumentasi menulis suara sebagai `voiceConfig.voice`, sedangkan skrip memakai `voiceConfig.prebuiltVoiceConfig.voiceName` (bentuk dokumentasi sebelumnya). Bila run pertama gagal dengan `HTTP 400`, pesan galat Google ikut dicetak (kunci disamarkan): sesuaikan `GeminiTts._body`.
- Nama model bawaan (`gemini-3.8-flash-tts`) dan format WAV/PCM 24 kHz diambil dari dokumentasi; model lama seperti `gemini-2.5-flash-preview-tts` mungkin sudah tidak didukung.
- Metadata gaya (`speechMetadata.style`) hanya dikirim bila `GEMINI_TTS_STYLE` diisi.

## `--generated-under`

| Provider | Nilai | Arti |
|---|---|---|
| gemini | `free` / `paid` | free tier atau akun berbayar; dicatat untuk audit |
| azure | `student` / `payg` | Azure for Students (non-komersial; **buat ulang di PAYG sebelum rilis komersial**) / Pay-As-You-Go |

Nilai yang salah untuk provider ditolak (kode 2).

## Membuat ulang dengan penyedia lain

Audio hanya berkas. Untuk berpindah (mis. setelah Google menolak penggunaan komersial):

```bash
# isi AZURE_SPEECH_KEY / AZURE_SPEECH_REGION (japaneast|japanwest) / AZURE_SPEECH_VOICE di backend/.env
uv run python tts/generate_audio.py --provider azure --generated-under payg --script ../../app/assets/audio/script.csv
```

Atau setel `TTS_PROVIDER=azure` di `.env`. Provider berbeda memicu pembuatan ulang semua berkas (`.mp3` untuk Azure, `.wav` untuk Gemini; berkas lama tidak dihapus otomatis, hapus sendiri lalu perbarui referensi di app). Azure memeriksa daftar suara di region lebih dulu dan gagal jelas (kode 4) bila `id-ID-GadisNeural`/`id-ID-ArdiNeural` tidak tersedia.

## Keluaran: `MANIFEST.json`

`schema_version` (2), `provider`, `model`, `voice`, `region` (hanya azure), `output_format`, `generated_at`, `generated_under`, `ai_voice_label_required: true` (kunci lama `ai_voice_disclosure_required: true` dipertahankan untuk kompatibilitas), `script_sha256`, `complete`, dan `files[]` berisi `id`, `file`, `text`, `approved_by`, `input_sha256`, `sha256`, `bytes`, `generated_at`.

## Kode keluar

`0` sukses · `2` konfigurasi/argumen (termasuk kunci hilang, `--generated-under` tidak cocok dengan provider) · `3` naskah tidak valid (belum disetujui/lint/struktur) · `4` suara tidak tersedia di region (azure) · `5` sintesis gagal (termasuk batas permintaan tercapai).

## Tes

`cd backend/tools && uv run pytest -q tests/test_tts_gemini.py tests/test_tts_generate_audio.py` (HTTP sepenuhnya di-mock; tidak ada jaringan).
