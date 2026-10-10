# %% [markdown]
# # DentisAI — bake-off kandidat #1: **YOLOX-Nano** (Kaggle / Colab, data publik saja)
#
# Notebook ini **bukan** validasi klinis. Hasilnya = artefak teknis `NOT VALIDATED`,
# lisensi bobot pra-latih YOLOX **tidak eksplisit**; pemilik produk menerima risikonya untuk kompetisi (ADR 014 §5) →
# semua catatan menulis `owner_accepted_risk` (bukan "terverifikasi"). Kelas: indikasi karies gigi tetap/ sulung (ADR 014).
#
# **Aturan:** hanya dataset publik Ahmed 2025 (CC BY 4.0, atribusi wajib). **Jangan pernah** mengunggah
# foto anak/ data lokal ke Kaggle/Colab. Keluaran yang diunduh tidak boleh berisi gambar (`artifacts.py` menolaknya).
#
# Input (Kaggle → *Add Input*): (1) dataset privat kode `dentisai-ml-code` (zip dari `artifacts.py pack-code --with-processed`),
# (2) dataset privat `ahmed2025-caries` (zip Zenodo apa adanya). Opsional (3): output versi notebook sebelumnya (untuk *resume*).
# Accelerator: **GPU T4 x2** (bukan P100: build PyTorch terbaru tidak lagi mendukung Pascal [PERLU VERIFIKASI]).
# Langkah lengkap: `RUNBOOK.md`. Sel diberi nomor; jalankan berurutan.

# %% 0. Settings (the ONLY cell you normally edit)
CANDIDATE = "yolox_nano"
PRETRAINED = True            # False = random init (licence-free route, WEIGHT_LICENSES.md §3.1; needs more epochs)
EPOCHS = None                # None = config value (150); try 300 when PRETRAINED is False
BATCH_SIZE = None            # None = config value (32)
ACT = "silu"                 # "relu" if INT8 parity fails (cell 9) -- then retrain
TIME_BUDGET_H = 10.0         # stop training before the 12 h Kaggle session limit; resume next session
TARGET_SENSITIVITY = 0.85    # val operating-point rule (technical, not clinical)
VERSION_BASE = "0.1.0"
DATASET_MARKER = "Dataset/Images"                         # folder that identifies the Ahmed 2025 root [PERLU VERIFIKASI]
YOLO_IMAGES, YOLO_LABELS = "Dataset/Images", "Dataset/Annotations/Darknet_YOLO"   # only used if processed files are absent
GROUP_MODE = "exif_session"                               # only used if processed files are absent (fetch_ahmed2025.md §4)
RESUME_FROM = ["/kaggle/input/*/yolox_outputs"]          # earlier committed versions attached as input
YOLOX_COMMIT = "6ddff4824372906469a7fae2dc3206c7aa4bbaee" # keep equal to trainers/yolox_adapter.YOLOX_COMMIT

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

# %% 2. Install pinned tools (YOLOX at a pinned commit, no deps; conversion venv with Python 3.12)
nbkit.sh([sys.executable, "-m", "pip", "install", "-q", "jsonschema>=4.18", "defusedxml>=0.7.1", "pyyaml>=6", *nbkit.YOLOX_TRAIN_PKGS])
YOLOX_REPO = TMP / "YOLOX"
if not (YOLOX_REPO / "tools" / "train.py").is_file():
    nbkit.sh(["git", "clone", "-q", "https://github.com/Megvii-BaseDetection/YOLOX.git", str(YOLOX_REPO)])
nbkit.sh(["git", "-C", str(YOLOX_REPO), "checkout", "-q", YOLOX_COMMIT])
nbkit.sh([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-build-isolation", "-e", str(YOLOX_REPO)])
EXPORT_PY = nbkit.ensure_venv(TMP / "venv-export", nbkit.EXPORT_PY, nbkit.EXPORT_PKGS)
PRETRAINED_PTH = None
if PRETRAINED:  # COCO-pretrained Nano from the YOLOX release (no explicit weights licence -> internal experiment)
    PRETRAINED_PTH = TMP / "yolox_nano.pth"
    if not PRETRAINED_PTH.is_file():
        nbkit.sh(["wget", "-q", "-O", str(PRETRAINED_PTH),
                  "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.pth"])
    print("yolox_nano.pth sha256", nbkit.sha256_file(PRETRAINED_PTH))

# %% 3. Data: Ahmed 2025 root, processed annotations (canonical + patient-level splits)
DATA_ROOT = nbkit.find_dir([DIRS["inputs"]], DATASET_MARKER)
assert DATA_ROOT is not None, f"Ahmed 2025 dataset not found (looked for {DATASET_MARKER!r} under {DIRS['inputs']})"
PROCESSED = SRC / "ml" / "data" / "processed" / "ahmed2025"
info = nbkit.check_processed(PROCESSED)
if info is None:  # fallback: build them here from the reviewed class map (same commands as fetch_ahmed2025.md)
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
    "model": {"pretrained": str(PRETRAINED_PTH) if PRETRAINED_PTH else None},
    "trainer_options": {"yolox_repo": str(YOLOX_REPO), "act": ACT, "work_dir": str(TMP / "work"),
                        "output_dir": str(WORK / "yolox_outputs"), "resume_from": RESUME_FROM, "time_budget_h": TIME_BUDGET_H},
}
if EPOCHS or BATCH_SIZE:
    cfg_over["train"] = {k: v for k, v in (("epochs", EPOCHS), ("batch_size", BATCH_SIZE)) if v}
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG, cfg_over)
RUNS = WORK / "runs"
nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--dry-run", "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET)

# %% 5. Train (resumes automatically from latest_ckpt.pth; stops at TIME_BUDGET_H -> commit, then re-run to resume)
rc = nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET, check=False)
RUN_JSON = sorted(RUNS.glob(f"*_{CANDIDATE}_*/run.json"))[-1]
run = json.loads(RUN_JSON.read_text(encoding="utf-8"))
print(run["status"], json.dumps(run.get("results"), indent=1))
assert rc == 0, f"training failed: {run.get('error')}"
if (run.get("results") or {}).get("status") == "time_budget_reached_resume_next_session":
    raise SystemExit("Time budget reached: Save Version (commit) now, then run again with this version's output attached as input.")

# %% 6. Export ONNX (raw head: no grid decode, obj/cls as LOGITS -- the app applies the sigmoid) from the best checkpoint
from trainers.yolox_adapter import export_onnx

CKPT = Path(run["results"]["best_ckpt"])
if not CKPT.is_file():
    CKPT = Path(run["results"]["latest_ckpt"])
ONNX = TMP / "export" / f"{CANDIDATE}.onnx"
onnx_info = export_onnx(Path(run["results"]["exp_file"]), CKPT, ONNX)
print(onnx_info)
assert onnx_info["output_shape"] == [1, 3549, 7] and onnx_info["head_sigmoid_stripped"] == 6, onnx_info
assert CFG["model"]["decode"]["sigmoid_applied"] is False  # must match the stripped graph (labels.json -> app decoder)

# %% 7. Representative data (TRAIN split, INT8 calibration) + parity samples (VAL split). Kept in TMP: never exported.
CALIB = TMP / "export" / "calib_train.npy"
PARITY = TMP / "export" / "parity_val.npy"
nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "train", "--out", CALIB], cwd=DET)
nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "val", "--n", "50", "--out", PARITY], cwd=DET)

# %% 8. ONNX -> LiteRT float32 + INT8 with BOTH onnx2tf 2.6.9 backends; pick by tensor-level parity (tf_converter first).
#      Local proof (2026-10-10, tiny random graph): flatbuffer_direct INT8 converted but parity cosine 0.45 (Conv->Reshape
#      requantisation) and it rejects Sigmoid outputs; tf_converter needs TensorFlow (installed here, not tested locally).
nbkit.sh([sys.executable, "-m", "uv", "pip", "install", "--python", EXPORT_PY, *nbkit.EXPORT_TF_PKGS])
ONNX2TF = TMP / "venv-export" / "bin" / "onnx2tf"
CANDS: dict = {}
for backend in ("tf_converter", "flatbuffer_direct"):
    out_dir = TMP / "export" / f"tflite_{backend}"
    rc = nbkit.sh([ONNX2TF, "-i", ONNX, "-o", out_dir, "-b", "1", "-oiqt", "-tb", backend,
                   "-cind", "images", CALIB, "[[[[0.0,0.0,0.0]]]]", "[[[[1.0,1.0,1.0]]]]"], cwd=TMP, check=False)
    picked = nbkit.pick_onnx2tf_outputs(out_dir)
    for kind in ("int8_float_io", "int8_full"):
        if rc == 0 and picked[kind] and picked["float32"]:
            rep = WORK / "out" / f"parity_tensor_{backend}_{kind}.json"
            nbkit.sh([EXPORT_PY, "check_int8_parity.py", "--float-model", picked["float32"], "--int8-model", picked[kind],
                      "--samples", PARITY, "--report", rep], cwd=DET, check=False)
            CANDS[(backend, kind)] = (picked, json.loads(rep.read_text(encoding="utf-8")))
print({k: (v[1]["passed"], [t["cosine"] for t in v[1]["tensors"]]) for k, v in CANDS.items()})
assert CANDS, "no INT8 model from either backend -- see logs; plan B: litert-torch (WEIGHT_LICENSES.md §3.3)"
CHOSEN = next((k for k, v in CANDS.items() if v[1]["passed"]), max(CANDS, key=lambda k: CANDS[k][1]["tensors"][0]["cosine"] or 0.0))
TFL, INT8 = CANDS[CHOSEN][0], CANDS[CHOSEN][0][CHOSEN[1]]
print("chosen:", CHOSEN, INT8, round(INT8.stat().st_size / 2**20, 2), "MiB")

# %% 9. Dev bundle (size gate <= 15 MB, labels.json from the interpreter) + INT8 parity on 50 val images
EXPORTS = WORK / "exports"
VERSION = nbkit.next_dev_version(EXPORTS, VERSION_BASE)
BUNDLE = EXPORTS / VERSION
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", INT8, "--out", BUNDLE,
          "--run-id", run["run_id"], "--allow-unverified-license"], cwd=DET)
PARITY_JSON = WORK / "out" / "parity.json"
nbkit.sh([EXPORT_PY, "check_int8_parity.py", "--float-model", TFL["float32"], "--int8-model", BUNDLE / "model.tflite",
          "--samples", PARITY, "--labels", BUNDLE / "labels.json", "--report", PARITY_JSON], cwd=DET, check=False)
parity = json.loads(PARITY_JSON.read_text(encoding="utf-8"))
print("INT8 parity passed:", parity["passed"], parity["failures"])

# %% 10. Predictions with the INT8 bundle (exact app preprocess + decode) on val and test; float32 reference on test
OUT = WORK / "out"
common = ["--gt", P["canonical"], "--splits", P["splits"], "--images-root", DATA_ROOT]
for split in ("val", "test"):
    nbkit.sh([EXPORT_PY, "predict.py", "--model", BUNDLE / "model.tflite", "--labels", BUNDLE / "labels.json", "--split", split,
              *common, "--out", OUT / f"preds_int8_{split}.jsonl"], cwd=DET)
nbkit.sh([EXPORT_PY, "predict.py", "--model", TFL["float32"], "--labels", BUNDLE / "labels.json", "--split", "test",
          *common, "--out", OUT / "preds_float_test.jsonl"], cwd=DET)

# %% 11. Thresholds on VAL, then evaluation (val + test, patient-group bootstrap CIs)
THR = OUT / "thresholds.json"
nbkit.sh([EXPORT_PY, "thresholds.py", "--pred", OUT / "preds_int8_val.jsonl", "--gt", P["canonical"], "--splits", P["splits"],
          "--target-sensitivity", str(TARGET_SENSITIVITY), "--out", THR], cwd=DET)
for split, preds in (("val", "preds_int8_val.jsonl"), ("test", "preds_int8_test.jsonl"), ("test", "preds_float_test.jsonl")):
    tag = preds.replace("preds_", "").replace(".jsonl", "")
    nbkit.sh([EXPORT_PY, "eval.py", "--gt", P["canonical"], "--splits", P["splits"], "--split", split, "--pred", OUT / preds,
              "--metadata", META_CSV, "--thresholds", THR, "--candidate", CANDIDATE, "--model-version", VERSION,
              "--report", OUT / f"report_{tag}.json"], cwd=DET)

# %% 12. Final bundle with thresholds (status set_on_val), eval + parity evidence; verify
FINAL = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)  # bundles are immutable -> next dev number
nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--int8", "--tflite-in", INT8, "--out", FINAL,
          "--run-id", run["run_id"], "--thresholds", THR, "--eval-report", OUT / "report_int8_test.json",
          "--parity-report", PARITY_JSON, "--allow-unverified-license"], cwd=DET)
nbkit.sh([EXPORT_PY, "export.py", "verify-bundle", FINAL], cwd=DET)

# %% 13. Run record + downloadable artifacts (NO images, NO .npy, NO checkpoints)
MEAS = OUT / "measurements_partial.json"
MEAS.write_text(json.dumps({CANDIDATE: {
    "int8_size_mb": round((FINAL / "model.tflite").stat().st_size / 2**20, 3),
    "export_notes": f"onnx2tf 2.6.9 -oiqt backend={CHOSEN[0]} file={INT8.name}; act={ACT}; pretrained={PRETRAINED}; head=logits",
    "parity_passed": parity["passed"], "weight_license_clarity": "owner_accepted_risk"}}, indent=1), encoding="utf-8")
(OUT / "env.json").write_text(json.dumps(nbkit.env_record({"onnx": onnx_info, "processed": info}), indent=1), encoding="utf-8")
files = [RUN_JSON, THR, PARITY_JSON, MEAS, OUT / "env.json", *sorted(OUT.glob("report_*.json")), *sorted(OUT.glob("parity_tensor_*.json")), RUN_CFG]
nbkit.sh([EXPORT_PY, "artifacts.py", "collect", "--out-dir", WORK / f"download_{CANDIDATE}", "--bundle", FINAL,
          *[a for f in files for a in ("--file", f)]], cwd=DET)
print("Download:", WORK / f"download_{CANDIDATE}.zip", "-> backend/ml/runs/kaggle/ (see RUNBOOK §6)")
