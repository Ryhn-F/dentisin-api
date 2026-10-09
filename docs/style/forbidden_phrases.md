# Frasa terlarang & pengganti (UI, rujukan, chatbot)

> Draf awal untuk disahkan drg. Dipakai oleh lint UI (app) dan guardrail keluaran (backend). Pencocokan tidak peka huruf besar/kecil.

| Terlarang | Alasan | Gunakan |
|---|---|---|
| "Anda menderita …", "anak Anda menderita …" | Diagnosis | "Ada indikasi …", "Terlihat tanda yang perlu diperiksa" |
| "diagnosis", "didiagnosis", "terdiagnosis" | Skrining ≠ diagnosis | "hasil skrining", "indikasi" |
| "pasti", "dipastikan", "100%" | Overclaim | "kemungkinan", "perlu diperiksa dokter gigi" |
| "positif karies", "negatif karies" | Bahasa uji diagnostik | "ada indikasi lubang", "belum terlihat tanda yang jelas" |
| "tidak perlu ke dokter", "aman", "sehat sempurna" | False reassurance | "belum ditemukan tanda yang jelas — tetap periksa rutin 6 bulan" |
| "bebas karies" tanpa disclaimer | Overclaim | "belum terlihat lubang pada foto ini" + disclaimer |
| "obati sendiri", dosis obat untuk anak | Keselamatan | "Hubungi dokter/ puskesmas untuk obat yang sesuai" |
| "akurasi XX%" (di UI/ store) | Belum divalidasi (ADR 002) | — |
| Nama merek sebagai rekomendasi klinis | Netralitas | "Contoh produk dengan kandungan ini" (≥ 2 merek) |

Wajib hadir pada layar hasil: "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi." dan CTA rujukan.
Wajib pada rekomendasi pasta gigi: "Pasta gigi membantu, tetapi tidak menambal gigi berlubang."
