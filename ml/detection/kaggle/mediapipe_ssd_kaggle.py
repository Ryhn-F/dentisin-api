# %% [markdown]
# # DentisAI — bake-off kandidat #2: **MediaPipe Model Maker MobileNetV2 (QAT INT8)** (Kaggle / Colab, data publik saja)
#
# Notebook ini **bukan** validasi klinis. Hasil = artefak teknis `NOT VALIDATED`. Lisensi kode & bobot MOBILENET_V2
# terverifikasi (Apache-2.0, `WEIGHT_LICENSES.md`); bila sel 5 menolak dengan *"config says license_verified=true but the
# gate has unverified ..."*, patch `license_gate.yaml` belum diterapkan — hentikan dan laporkan, jangan mengakali gerbang.
# Kelas (ADR 014): indikasi karies gigi tetap (`caries_permanent`) / sulung (`caries_primary`); kotak hanya overlay UI.
# Hanya dataset publik Ahmed 2025 (CC BY 4.0) — **jangan pernah** mengunggah foto anak/ data lokal.
# Model Maker sudah *deprecated* dan butuh Python 3.10 + TensorFlow 2.15 → dipasang otomatis di venv terpisah (sel 3).
#
# **Cara pakai (detail: `RUNBOOK.md`):** Settings → Accelerator **GPU T4 x2**, Internet **On**; Add-ons → Secrets →
# `GITHUB_TOKEN`. Lalu **Run All** dengan `SMOKE_TEST = True` (±20–30 menit); bila berhasil, ubah ke `False` dan
# *Save Version → Save & Run All (Commit)*.

# %% 0. Settings -- SATU-SATUNYA sel yang biasanya diubah
SMOKE_TEST = True           # True = uji cepat (32 foto, 1 epoch + 1 epoch QAT, seluruh rantai sampai zip unduhan). Jalankan DULU.
                            # Bila selesai tanpa error -> ubah ke False untuk pelatihan sungguhan (± 2,5-4,5 jam).
CODE_REF = "main"           # cabang/ tag/ commit repo GitHub Ryhn-F/dentisin-api yang dipakai. Biarkan "main".
EPOCHS = None               # None = nilai konfigurasi (30)
QAT_EPOCHS = None           # None = nilai konfigurasi (10). 0 = tanpa QAT -> TIDAK ada model INT8 (tidak bisa dipakai app)
BATCH_SIZE = None           # None = nilai konfigurasi (8)
TIME_BUDGET_H = 11.0        # Batas jam per sesi (Kaggle memutus di 12 jam). Model Maker TIDAK bisa melanjutkan (resume):
                            # bila waktu habis, pelatihan dihentikan dan perlu diulang dengan EPOCHS lebih kecil.
TARGET_SENSITIVITY = 0.85   # Titik operasi pada data val (teknis, bukan klinis). Biarkan.

# %% 1. Preflight + ambil kode (jangan diubah). Gagal di sini = pesan berisi langkah perbaikan.
# >>> inline: bootstrap.py
from bootstrap import (
    HOSTS_COMMON,
    HOSTS_MEDIAPIPE,
    acquire_code,
    check_nbkit_api,
    compute_env,
    default_dirs,
    preflight,
)

# <<< inline
CANDIDATE = "mediapipe_modelmaker_mobilenetv2_ssd"
import json
import sys
import time
from pathlib import Path

T0 = time.time()
ENV = compute_env()
DIRS = default_dirs(ENV)
TMP = DIRS["tmp"]
WORK = DIRS["work"] / (CANDIDATE + ("_smoke" if SMOKE_TEST else ""))  # persisted outputs: NO photos, NO image arrays
for d in (TMP, WORK):
    d.mkdir(parents=True, exist_ok=True)
REPORT = preflight(need_gpu=True, hosts=(*HOSTS_COMMON, *HOSTS_MEDIAPIPE), disk_paths={"tmp": TMP, "working": DIRS["work"]},
                   min_free_gb=8.0 if SMOKE_TEST else 12.0, env=ENV)
CODE, CODE_INFO = acquire_code(roots=[DIRS["inputs"]], tmp=TMP, ref=CODE_REF)
DET = CODE / "ml" / "detection"
sys.path[:0] = [str(DET), str(DET / "kaggle")]
import nbkit

check_nbkit_api(nbkit.NBKIT_API)
print(json.dumps(nbkit.env_record({"code": CODE_INFO, "smoke_test": SMOKE_TEST, "work": str(WORK)}), indent=1))

# %% 2. Data Ahmed 2025 (otomatis: dataset terlampir -> atau unduh dari Zenodo) + anotasi kanonik + split pasien (dari repo)
missing = nbkit.missing_modules(nbkit.KERNEL_MODULES)  # small pure-Python pipeline deps (e.g. defusedxml) the kernel may lack
if missing:
    nbkit.sh([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *missing])
SPLIT_OF = json.loads((CODE / "ml/data/splits/ahmed2025/splits.json").read_text(encoding="utf-8"))["images"]
DATA = nbkit.prepare_ahmed(inputs=[DIRS["inputs"]], tmp=TMP, smoke=SMOKE_TEST, split_of=SPLIT_OF)
DATA_ROOT = Path(DATA["root"])
PROCESSED = TMP / ("processed_smoke" if SMOKE_TEST else "processed") / "ahmed2025"
info = nbkit.build_processed(src_ml=CODE / "ml", data_root=DATA_ROOT, processed=PROCESSED, images=DATA["images"])
print(json.dumps(info, indent=1))
assert set(info["boxes_per_class"]) <= {"caries_permanent", "caries_primary"}, info["boxes_per_class"]  # ADR 014: D / d
P = nbkit.processed_paths(PROCESSED)
META_CSV = WORK / "out" / "metadata.csv"
nbkit.metadata_from_splits(P["splits"], META_CSV)

# %% 3. Pasang: venv Model Maker (Python 3.10, TF 2.15.1 + CUDA, versi terkunci) dan venv ekspor/evaluasi (Python 3.12)
UV = nbkit.uv_command()
MM_VENV = TMP / "venv-mm"
MM_PY = nbkit.ensure_venv(MM_VENV, nbkit.MM_PY, nbkit.MM_LOCK, uv=UV)
GPUS = nbkit.ensure_tf_gpu(MM_PY, MM_VENV, need=ENV != "local")
EXPORT_PY = nbkit.ensure_venv(TMP / "venv-export", nbkit.EXPORT_PY, nbkit.EXPORT_LOCK, uv=UV)
nbkit.sh([EXPORT_PY, "-c", "import ai_edge_litert, numpy; print('litert ok, numpy', numpy.__version__)"])

# %% 4. Uji alat dalam beberapa menit: latih 1 epoch + 1 epoch QAT pada 8 foto, lalu ekspor (sebelum pelatihan panjang)
MINI = TMP / "mm_toolcheck"
print(nbkit.mini_layout(P["canonical"], P["splits"], DATA_ROOT, MINI, layout="mediapipe", n={"train": 8, "val": 4}))
t_check = time.time()
nbkit.sh([MM_PY, DET / "kaggle" / "mm_train_py310.py", "--train", MINI / "data/train", "--val", MINI / "data/val",
          "--export-dir", MINI / "exported", "--epochs", "1", "--batch-size", "4", "--qat-epochs", "1", "--cache-dir", MINI / "cache"])
assert (MINI / "exported" / "model_int8_qat.tflite").is_file(), "Model Maker toolcheck produced no QAT model -- see log above"
print(f"toolcheck OK in {time.time() - t_check:.0f}s:", sorted(p.name for p in (MINI / "exported").iterdir()))

# %% 5. Konfigurasi run + gerbang lisensi (dry run). Penolakan di sini final -- jangan mengubah gerbang dari notebook.
RUN_CFG = WORK / "cfg" / f"{CANDIDATE}.yaml"
cfg_over: dict = {
    "data": {"canonical": str(P["canonical"]), "splits": str(P["splits"]), "images_root": str(DATA_ROOT)},
    "trainer_options": {"python": str(MM_PY), "work_dir": str(TMP / ("work_smoke" if SMOKE_TEST else "work")),
                        "cache_dir": str(TMP / ("mm_cache_smoke" if SMOKE_TEST else "mm_cache"))},
}
train_over = {k: v for k, v in (("epochs", EPOCHS), ("qat_epochs", QAT_EPOCHS), ("batch_size", BATCH_SIZE)) if v is not None}
if SMOKE_TEST:
    train_over = {"epochs": 1, "qat_epochs": 1, "batch_size": 4}
if train_over:
    cfg_over["train"] = train_over
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG, cfg_over)
RUNS = WORK / "runs"
nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--dry-run", "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET)

# %% 6. Latih + QAT + ekspor (di venv Python 3.10; ±1,5-3 jam di T4; Model Maker tidak bisa resume)
TRAIN_H = nbkit.remaining_hours(TIME_BUDGET_H, T0, reserve_h=0.75)
print(f"training budget this session: {TRAIN_H} h")
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG,
                             nbkit.deep_merge(cfg_over, {"trainer_options": {"time_budget_h": TRAIN_H}}))
rc = nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET, check=False)
RUN_JSON = nbkit.latest_run(RUNS, CANDIDATE)
run = json.loads(RUN_JSON.read_text(encoding="utf-8"))
print(run["status"], json.dumps(run.get("results"), indent=1))
assert rc == 0, f"training failed: {run.get('error')} -- lihat log di atas (RUNBOOK §8)"
EXPORTED = RUN_JSON.parent / "exported"
FP32, QAT, ANCHORS = EXPORTED / "model_fp32.tflite", EXPORTED / "model_int8_qat.tflite", EXPORTED / "anchors.json"
assert QAT.is_file(), "no QAT INT8 model (QAT_EPOCHS = 0?) -- Model Maker offers no INT8 without QAT"
print({p.name: round(p.stat().st_size / 2**20, 2) for p in (FP32, QAT)}, "anchors:", len(json.loads(ANCHORS.read_text())))

# %% 7. Sampel paritas (split VAL, 50 foto; QAT tidak butuh set kalibrasi). Disimpan di TMP: tidak pernah diekspor.
PARITY = TMP / "export" / "parity_val.npy"
nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "val", "--n", "50", "--out", PARITY], cwd=DET)

# %% 8. Bundel dev (gerbang ukuran <= 15 MB; tensor/ kuantisasi dibaca interpreter) + paritas fp32 vs QAT (informatif:
#      QAT menyetel ulang bobot, jadi kecocokan lebih rendah dari PTQ murni adalah wajar)
EXPORTS = WORK / "exports"
VERSION_BASE = "0.1.0"
BUNDLE = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", QAT, "--anchors", ANCHORS,
          "--out", BUNDLE, "--run-id", run["run_id"], "--allow-unverified-license"], cwd=DET)
print(json.dumps(json.loads((BUNDLE / "labels.json").read_text())["output"]["tensors"], indent=1))
PARITY_JSON = WORK / "out" / "parity.json"
nbkit.sh([EXPORT_PY, "check_int8_parity.py", "--float-model", FP32, "--int8-model", BUNDLE / "model.tflite", "--samples", PARITY,
          "--labels", BUNDLE / "labels.json", "--report", PARITY_JSON], cwd=DET, check=False)
parity = json.loads(PARITY_JSON.read_text(encoding="utf-8")) if PARITY_JSON.is_file() else {"passed": False, "failures": ["no report"]}
print("parity passed:", parity["passed"], parity["failures"])

# %% 9. Prediksi dengan bundel INT8 (preprocess + decode app) pada val dan test; fp32 sebagai pembanding pada test
OUT = WORK / "out"
common = ["--gt", P["canonical"], "--splits", P["splits"], "--images-root", DATA_ROOT]
for split in ("val", "test"):
    nbkit.sh([EXPORT_PY, "predict.py", "--model", BUNDLE / "model.tflite", "--labels", BUNDLE / "labels.json", "--split", split,
              *common, "--out", OUT / f"preds_int8_{split}.jsonl"], cwd=DET)
nbkit.sh([EXPORT_PY, "predict.py", "--model", FP32, "--labels", BUNDLE / "labels.json", "--split", "test",
          *common, "--out", OUT / "preds_float_test.jsonl"], cwd=DET, check=False)  # fp32 I/O differs from uint8: best effort

# %% 10. Ambang pada VAL, lalu evaluasi (val + test, CI bootstrap per kelompok pasien)
THR = OUT / "thresholds.json"
nbkit.sh([EXPORT_PY, "thresholds.py", "--pred", OUT / "preds_int8_val.jsonl", "--gt", P["canonical"], "--splits", P["splits"],
          "--target-sensitivity", str(TARGET_SENSITIVITY), "--out", THR], cwd=DET)
for split, preds in (("val", "preds_int8_val.jsonl"), ("test", "preds_int8_test.jsonl"), ("test", "preds_float_test.jsonl")):
    if not (OUT / preds).is_file():
        continue
    tag = preds.replace("preds_", "").replace(".jsonl", "")
    nbkit.sh([EXPORT_PY, "eval.py", "--gt", P["canonical"], "--splits", P["splits"], "--split", split, "--pred", OUT / preds,
              "--metadata", META_CSV, "--thresholds", THR, "--candidate", CANDIDATE, "--model-version", BUNDLE.name,
              "--report", OUT / f"report_{tag}.json"], cwd=DET)

# %% 11. Bundel final dengan ambang + bukti; verifikasi
FINAL = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", QAT, "--anchors", ANCHORS,
          "--out", FINAL, "--run-id", run["run_id"], "--thresholds", THR, "--eval-report", OUT / "report_int8_test.json",
          "--parity-report", PARITY_JSON, "--allow-unverified-license"], cwd=DET)
nbkit.sh([EXPORT_PY, "export.py", "verify-bundle", FINAL], cwd=DET)

# %% 12. Catatan run + zip unduhan (TANPA gambar, TANPA .npy, TANPA checkpoint)
MEAS = OUT / "measurements_partial.json"
MEAS.write_text(json.dumps({CANDIDATE: {
    "int8_size_mb": round((FINAL / "model.tflite").stat().st_size / 2**20, 3),
    "export_notes": "Model Maker 0.2.1.4 QAT export_model (uint8 I/O, raw boxes+scores, anchors.json)",
    "parity_passed": parity["passed"], "weight_license_clarity": "clear", "smoke_test": SMOKE_TEST}}, indent=1), encoding="utf-8")
(OUT / "env.json").write_text(json.dumps(nbkit.env_record({"processed": info, "code": CODE_INFO, "data_source": DATA["source"],
                                                           "smoke_test": SMOKE_TEST, "tf_gpus": GPUS}), indent=1), encoding="utf-8")
files = [RUN_JSON, THR, PARITY_JSON, MEAS, OUT / "env.json", *sorted(OUT.glob("report_*.json")), RUN_CFG,
         EXPORTED / "mm_result.json", EXPORTED / "metadata.json"]
nbkit.sh([EXPORT_PY, "artifacts.py", "collect", "--out-dir", WORK / f"download_{CANDIDATE}", "--bundle", FINAL,
          *[a for f in files if f.is_file() for a in ("--file", f)]], cwd=DET)

# %% 13. Pemeriksaan akhir: tidak ada foto di keluaran + daftar berkas yang perlu diunduh
bad = nbkit.forbidden_outputs(DIRS["work"])
assert not bad, f"photos/arrays found in the persisted outputs (must never happen): {bad[:5]}"
print("\n".join(nbkit.describe_outputs(WORK)))
print(f"\nSelesai dalam {(time.time() - T0) / 3600:.2f} jam. SMOKE_TEST = {SMOKE_TEST}.")
if SMOKE_TEST:
    print("UJI CEPAT BERHASIL. Ubah SMOKE_TEST = False di sel 0, lalu Save Version -> Save & Run All (Commit).")
else:
    print(f"Unduh {WORK / f'download_{CANDIDATE}.zip'} (tab Output) -> backend/ml/runs/kaggle/ (RUNBOOK §6).")
