# Change Log terkendali: model, aturan triase, rekomendasi

> Templat hidup (ADR 002: versioning / change control / rollback). **Setiap** artefak yang masuk `manifest.json` (model, `triage/rules.yaml`, `recommender/*.json`, `labels.json`) harus punya entri di sini **sebelum** manifes ditandatangani. Menurut ringkasan sekunder, perubahan algoritma yang memengaruhi fungsi diagnostik dapat berarti izin edar baru ([PERLU VERIFIKASI], teks resmi KMK 951/2026 belum dibaca; lihat `OPEN_LEGAL_QUESTIONS.md` Q-03) — karena itu setiap entri mencatat apakah perubahan menyentuh fungsi deteksi/triase.
> Status: **belum ada rilis**. Tidak ada entri yang boleh diisi dengan hasil yang belum dijalankan.

## Aturan
1. Versi semantik per artefak: `model` (`ml/exports/<semver>/`), `rules` (triase), `recommender`. Manifes menyebut ketiganya.
2. Entri baru ditambahkan di **atas** (terbaru dulu). Entri lama tidak diubah; koreksi = entri baru yang merujuk.
3. Persetujuan drg: nama + tanggal diisi **oleh drg/ penandatangan nyata**; sampai itu `[PENDING]` dan manifes **tidak boleh** ditandatangani untuk rilis ke pengguna. Untuk demo teknis tanpa klaim klinis: tandai `release_class: demo (no clinical claim)`.
4. Rollback: catat versi sebelumnya yang dapat dipulihkan dan hash manifes lama.
5. Nilai `PLACEHOLDER`/`null` pada `params` aturan memblokir rilis (`backend/CLAUDE.md` §3.1); entri harus menyatakan "tidak ada PLACEHOLDER" dan melampirkan hasil pemeriksaan CI.

## Templat entri (salin ke bagian "Entri")
```
### <artefak> <semver> — <YYYY-MM-DD>
- release_class:   demo (no clinical claim) | pilot (KEPK ref: ...) | public
- artefak:         model | triage rules | recommender (products/rules)
- versi sebelumnya: <semver>  (dapat di-rollback: ya/tidak; sha256 manifes lama: ...)
- apa yang berubah: <ringkas, termasuk ID aturan / kelas / ambang yang berubah>
- mengapa:          <alasan klinis/teknis; sumber, mis. laporan evaluasi, keluhan #, temuan drg>
- menyentuh fungsi deteksi/triase/indikasi?  ya | tidak  -> bila ya: tinjau status regulasi (OPEN_LEGAL_QUESTIONS Q-03) sebelum rilis publik
- laporan evaluasi: <tautan/path, set uji independen, tanggal, seed & hash split>
- model card:       <ml/exports/<semver>/MODEL_CARD.md>   labels.json sha256: ...
- data lineage:     <DATA_LINEAGE.md ID dataset/ekspor>
- risk register:    <ID risiko ditinjau/ditambah>
- hasil regresi:
    tes tabel triase: <jumlah kasus lulus/total>  (cakupan 100% diwajibkan)
    kasus graded_by_drg: <jumlah>  red-flag recall: <x/y>
    paritas Python <-> TS: <lulus/gagal>
    perbandingan metrik vs versi sebelumnya: <sens/spes per kelas, subgrup>  (tidak boleh memburuk tanpa alasan tertulis)
    parity INT8 vs float (model): <hasil ml/detection/check_int8_parity.py>
- tidak ada PLACEHOLDER pada build rilis: ya/tidak (bukti CI: ...)
- persetujuan drg:  [PENDING]  nama: [ISI]  tanggal: [ISI]
- persetujuan teknis (pemilik produk): [PENDING]
- manifes: version ..., sha256 ..., ditandatangani oleh ..., tanggal ... (Ed25519; kunci offline)
- catatan rollback / insiden terkait: ...
```

## Entri

### Gerbang rilis: basis tinjauan `review_basis` (ADR 015, rencana 3.1) — 2026-10-17
- release_class:   tidak ada rilis (perubahan alat gerbang saja; tidak ada manifes)
- artefak:         tidak ada artefak manifes yang berubah. Kode/skema gerbang: `triage/src/triage/release_check.py`, `triage/schema.json`, `triage/schemas/cases.schema.json`, `recommender/release_check.py`, `recommender/schema/rules.schema.json`, `ml/brushing/release_check.py` (baru) + `rules_loader.py`, `ml/detection/export.py` + `labels.schema.json` + `thresholds.py`
- apa yang berubah: field opsional `review_basis: literature | dentist` di `rules.yaml`, `recommender/rules.json`, `brushing_rules.yaml`, dan `thresholds.json` (→ `labels.json` `thresholds`); field opsional `expected_source` per kasus di `cases.json`. Tanpa field = `dentist` = gerbang lama, tidak berubah. `literature`: `reviewed_by` boleh anggota tim (nama tidak kosong, bukan placeholder seperti `PENDING_DRG`/`PLACEHOLDER`/`null`), setiap `source` wajib terisi tanpa `PERLU VERIFIKASI`, dan syarat ≥ 30 kasus `graded_by_drg` diganti ≥ 30 kasus dengan `expected_source`; `export.py --release` menerima titik operasi yang ditinjau anggota tim. Pemeriksaan lain (PLACEHOLDER, kontak darurat, `dist/` basi, frasa terlarang, produk BPOM, lisensi, validasi klinis) tetap
- mengapa:          ADR 015 (keputusan pemilik produk 2026-10-11): MVP tanpa tanda tangan drg, nilai klinis dari pustaka terbitan
- menyentuh fungsi deteksi/triase/indikasi?  tidak: tidak ada nilai klinis, aturan, ambang, atau teks yang berubah; `triage/dist/*`, `recommender/dist/*` dan `triage/tests/cases.json` byte-identik
- hasil regresi:    gerbang data nyata tetap GAGAL dengan penghambat yang sama seperti sebelumnya (triase 94, rekomendasi 126); gerbang sikat gigi baru juga gagal (32: `PLACEHOLDER`, belum ditinjau); `export.py --release` tetap gagal (lisensi `owner_accepted_risk`, belum ada bukti validasi klinis)
- tidak ada PLACEHOLDER pada build rilis: tidak (belum ada nilai yang diisi; `review_basis` belum dipasang di berkas data mana pun — itu rencana 3.3+)
- persetujuan drg:  tidak berlaku pada basis pustaka (ADR 015); tidak ada klaim "ditinjau/ disetujui dokter gigi"
- persetujuan teknis (pemilik produk): [PENDING]

| Artefak | Versi terbit pertama | Status |
|---|---|---|
| model | - | belum ada; bake-off belum dijalankan |
| triage rules | - | kerangka dengan `PLACEHOLDER`, belum ditinjau drg |
| recommender | - | kerangka dengan `PLACEHOLDER`, belum ditinjau drg |
| brushing rules (`ml/brushing/brushing_rules.yaml`) | 0.1.0 (prototipe, tidak masuk manifes) | semua ambang `PLACEHOLDER` kecuali target 2 menit; bukan klaim klinis |
