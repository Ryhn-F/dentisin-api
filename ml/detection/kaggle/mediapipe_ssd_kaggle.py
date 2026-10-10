# %% [markdown]
# # DentisAI — bake-off kandidat #2: **MediaPipe Model Maker MobileNetV2 (QAT INT8)** (Kaggle / Colab, data publik saja)
#
# Notebook ini **bukan** validasi klinis. Hasil = artefak teknis `NOT VALIDATED`.
# Lisensi kode & bobot MOBILENET_V2 terverifikasi (Apache-2.0, `WEIGHT_LICENSES.md`); **bila sel 4 menolak
# dengan "config says license_verified=true but the gate has unverified ..."**, patch `license_gate.yaml`
# (WEIGHT_LICENSES.md §4) belum diterapkan engineer data — hentikan dan laporkan, jangan mengakali gerbang.
#
# Kelas (ADR 014): indikasi karies pada gigi tetap (`caries_permanent`) / sulung (`caries_primary`); kotak hanya overlay UI.
# **Aturan:** hanya dataset publik Ahmed 2025 (CC BY 4.0). **Jangan pernah** mengunggah foto anak/ data lokal.
# Model Maker sudah *deprecated* dan butuh Python 3.10 + TensorFlow 2.15 → dipasang di venv terpisah (sel 2).
# Accelerator: **GPU T4 x2**. Langkah lengkap: `RUNBOOK.md`.

# %% 0. Settings (the ONLY cell you normally edit)
CANDIDATE = "mediapipe_modelmaker_mobilenetv2_ssd"
EPOCHS = None                # None = config value (30)
QAT_EPOCHS = None            # None = config value (10); 0 disables QAT (then NO INT8 model -> not shippable)
BATCH_SIZE = None            # None = config value (8)
TIME_BUDGET_H = 10.0
TARGET_SENSITIVITY = 0.85
VERSION_BASE = "0.1.0"
DATASET_MARKER = "Dataset/Images"                         # [PERLU VERIFIKASI] Ahmed 2025 layout
YOLO_IMAGES, YOLO_LABELS = "Dataset/Images", "Dataset/Annotations/Darknet_YOLO"
GROUP_MODE = "exif_session"

# %% 1. Locate the code, copy it to a writable place, import helpers
import collections
import json
import shutil
import sys
from pathlib import Path

assert sys.version_info >= (3, 11), f"pipeline needs Python >= 3.11, kernel has {sys.version}"
_roots = [Path("/kaggle/input"), Path("/content/drive/MyDrive/dentisai_inputs"), Path.cwd()]
_code = next((p.parents[2] for r in _roots if r.is_dir() for d in range(4)
              for p in sorted(r.glob("/".join(["*"] * d + ["ml/detection/train.py"])))), None)
assert _code is not None, "code dataset not found: attach the private dataset made by `artifacts.py pack-code`"
sys.path.insert(0, str(_code / "ml" / "detection" / "kaggle"))
import nbkit

ENV = nbkit.compute_env()
DIRS = nbkit.default_dirs(ENV)
WORK, TMP = DIRS["work"], DIRS["tmp"]
SRC = WORK / "src"
if not (SRC / "ml" / "detection" / "train.py").is_file():
    shutil.copytree(_code / "ml", SRC / "ml", dirs_exist_ok=True)
DET = SRC / "ml" / "detection"
sys.path[:0] = [str(DET), str(DET / "kaggle")]
TMP.mkdir(parents=True, exist_ok=True)
print(json.dumps(nbkit.env_record({"code": str(_code)}), indent=1))

# %% 2. Install: Model Maker venv (Python 3.10, TF 2.15.1 + CUDA wheels) and the conversion/eval venv (Python 3.12)
nbkit.sh([sys.executable, "-m", "pip", "install", "-q", "jsonschema>=4.18", "defusedxml>=0.7.1", "pyyaml>=6"])
MM_PY = nbkit.ensure_venv(TMP / "venv-mm", nbkit.MM_PY, nbkit.MM_PKGS)
nbkit.sh([MM_PY, "-c", "import tensorflow as tf, mediapipe_model_maker as m; print(tf.__version__, tf.config.list_physical_devices('GPU'))"])
EXPORT_PY = nbkit.ensure_venv(TMP / "venv-export", nbkit.EXPORT_PY, nbkit.EXPORT_PKGS)

# %% 3. Data: Ahmed 2025 root, processed annotations (canonical + patient-level splits)
DATA_ROOT = nbkit.find_dir([DIRS["inputs"]], DATASET_MARKER)
assert DATA_ROOT is not None, f"Ahmed 2025 dataset not found (looked for {DATASET_MARKER!r} under {DIRS['inputs']})"
PROCESSED = SRC / "ml" / "data" / "processed" / "ahmed2025"
info = nbkit.check_processed(PROCESSED)
if info is None:
    probs = nbkit.class_map_problems(SRC / "ml" / "data" / "class_maps" / "ahmed2025.yaml")
    print("class map:", probs or "ok")
    assert not any("null" in p for p in probs), "fill ml/data/class_maps/ahmed2025.yaml first (data engineer + drg)"
    nbkit.sh([sys.executable, "convert_annotations.py", "to-canonical", "--in-format", "yolo", "--root", DATA_ROOT,
              "--images", YOLO_IMAGES, "--labels", YOLO_LABELS, "--class-map", SRC / "ml/data/class_maps/ahmed2025.yaml",
              "--source", "ahmed2025", "--missing-label", "negative", "--on-invalid", "drop",
              "--out", PROCESSED / "canonical.jsonl"], cwd=DET)
    nbkit.sh([sys.executable, SRC / "ml/data/scripts/make_splits.py", "--canonical", PROCESSED / "canonical.jsonl",
              "--out-dir", PROCESSED / "splits", "--group-mode", GROUP_MODE, "--images-root", DATA_ROOT,
              "--merge-near-duplicates", "--seed", "20261008"], cwd=DET)
    info = nbkit.check_processed(PROCESSED)
print(json.dumps(info, indent=1))
# Sanity check of the D/d decision (ADR 014): D -> caries_permanent, d -> caries_primary.
_cls = collections.Counter(b["cls"] for line in (PROCESSED / "canonical.jsonl").read_text(encoding="utf-8").splitlines()
                           for b in json.loads(line).get("boxes", []))
print("boxes per class:", dict(_cls))
assert set(_cls) <= {"caries_permanent", "caries_primary"}, f"unexpected classes: {set(_cls)}"
P = nbkit.processed_paths(PROCESSED)
META_CSV = WORK / "out" / "metadata.csv"
nbkit.metadata_from_splits(P["splits"], META_CSV)

# %% 4. Run config + licence gate (dry run). A refusal here is final -- do not edit the gate from a notebook.
RUN_CFG = WORK / "cfg" / f"{CANDIDATE}.yaml"
cfg_over: dict = {
    "data": {"canonical": str(P["canonical"]), "splits": str(P["splits"]), "images_root": str(DATA_ROOT)},
    "trainer_options": {"python": str(MM_PY), "work_dir": str(TMP / "work"), "cache_dir": str(TMP / "mm_cache"),
                        "time_budget_h": TIME_BUDGET_H},
}
train_over = {k: v for k, v in (("epochs", EPOCHS), ("qat_epochs", QAT_EPOCHS), ("batch_size", BATCH_SIZE)) if v is not None}
if train_over:
    cfg_over["train"] = train_over
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG, cfg_over)
RUNS = WORK / "runs"
nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--dry-run", "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET)

# %% 5. Train + QAT + export (inside the Python 3.10 venv; one session ~1.5-3 h on T4, no resume support in Model Maker)
rc = nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET, check=False)
RUN_JSON = sorted(RUNS.glob(f"*_{CANDIDATE}_*/run.json"))[-1]
run = json.loads(RUN_JSON.read_text(encoding="utf-8"))
print(run["status"], json.dumps(run.get("results"), indent=1))
assert rc == 0, f"training failed: {run.get('error')}"
EXPORTED = RUN_JSON.parent / "exported"
FP32, QAT, ANCHORS = EXPORTED / "model_fp32.tflite", EXPORTED / "model_int8_qat.tflite", EXPORTED / "anchors.json"
assert QAT.is_file(), "no QAT INT8 model (QAT_EPOCHS = 0?) -- Model Maker offers no INT8 without QAT"
print({p.name: round(p.stat().st_size / 2**20, 2) for p in (FP32, QAT)}, "anchors:", len(json.loads(ANCHORS.read_text())))

# %% 6. Parity samples (VAL split, 50 images; QAT needs no calibration set). Kept in TMP: never exported.
PARITY = TMP / "export" / "parity_val.npy"
nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "val", "--n", "50", "--out", PARITY], cwd=DET)

# %% 7. Dev bundle (size gate <= 15 MB; tensors/quantisation read by the interpreter) + parity fp32 vs QAT (informational:
#      QAT fine-tunes the weights, so lower agreement than a pure PTQ comparison is expected)
EXPORTS = WORK / "exports"
BUNDLE = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", QAT, "--anchors", ANCHORS,
          "--out", BUNDLE, "--run-id", run["run_id"], "--allow-unverified-license"], cwd=DET)
print(json.dumps(json.loads((BUNDLE / "labels.json").read_text())["output"]["tensors"], indent=1))
PARITY_JSON = WORK / "out" / "parity.json"
nbkit.sh([EXPORT_PY, "check_int8_parity.py", "--float-model", FP32, "--int8-model", BUNDLE / "model.tflite", "--samples", PARITY,
          "--labels", BUNDLE / "labels.json", "--report", PARITY_JSON], cwd=DET, check=False)
parity = json.loads(PARITY_JSON.read_text(encoding="utf-8"))
print("parity passed:", parity["passed"], parity["failures"])

# %% 8. Predictions with the INT8 bundle (app preprocess + decode) on val and test; fp32 reference on test
OUT = WORK / "out"
common = ["--gt", P["canonical"], "--splits", P["splits"], "--images-root", DATA_ROOT]
for split in ("val", "test"):
    nbkit.sh([EXPORT_PY, "predict.py", "--model", BUNDLE / "model.tflite", "--labels", BUNDLE / "labels.json", "--split", split,
              *common, "--out", OUT / f"preds_int8_{split}.jsonl"], cwd=DET)
nbkit.sh([EXPORT_PY, "predict.py", "--model", FP32, "--labels", BUNDLE / "labels.json", "--split", "test",
          *common, "--out", OUT / "preds_float_test.jsonl"], cwd=DET, check=False)  # fp32 I/O differs from uint8: best effort

# %% 9. Thresholds on VAL, then evaluation (val + test, patient-group bootstrap CIs)
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

# %% 10. Final bundle with thresholds + evidence; verify
FINAL = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", QAT, "--anchors", ANCHORS,
          "--out", FINAL, "--run-id", run["run_id"], "--thresholds", THR, "--eval-report", OUT / "report_int8_test.json",
          "--parity-report", PARITY_JSON, "--allow-unverified-license"], cwd=DET)
nbkit.sh([EXPORT_PY, "export.py", "verify-bundle", FINAL], cwd=DET)

# %% 11. Run record + downloadable artifacts (NO images, NO .npy, NO checkpoints)
MEAS = OUT / "measurements_partial.json"
MEAS.write_text(json.dumps({CANDIDATE: {
    "int8_size_mb": round((FINAL / "model.tflite").stat().st_size / 2**20, 3),
    "export_notes": "Model Maker 0.2.1.4 QAT export_model (uint8 I/O, raw boxes+scores, anchors.json)",
    "parity_passed": parity["passed"], "weight_license_clarity": "clear"}}, indent=1), encoding="utf-8")
(OUT / "env.json").write_text(json.dumps(nbkit.env_record({"processed": info}), indent=1), encoding="utf-8")
files = [RUN_JSON, THR, PARITY_JSON, MEAS, OUT / "env.json", *sorted(OUT.glob("report_*.json")), RUN_CFG,
         EXPORTED / "mm_result.json", EXPORTED / "metadata.json"]
nbkit.sh([EXPORT_PY, "artifacts.py", "collect", "--out-dir", WORK / f"download_{CANDIDATE}", "--bundle", FINAL,
          *[a for f in files if f.is_file() for a in ("--file", f)]], cwd=DET)
print("Download:", WORK / f"download_{CANDIDATE}.zip", "-> backend/ml/runs/kaggle/ (see RUNBOOK §6)")
