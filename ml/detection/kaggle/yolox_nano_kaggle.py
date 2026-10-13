# %% [markdown]
# # DentisAI — bake-off kandidat #1: **YOLOX-Nano** (Kaggle / Colab, data publik saja)
#
# Notebook ini **bukan** validasi klinis. Hasilnya = artefak teknis `NOT VALIDATED`; lisensi bobot pra-latih YOLOX
# **tidak eksplisit** → pemilik produk menerima risikonya (ADR 014 §5), tercatat `owner_accepted_risk`.
# Kelas: indikasi karies gigi tetap/ sulung (ADR 014). Hanya dataset publik Ahmed 2025 (CC BY 4.0) — **jangan pernah**
# mengunggah foto anak/ data lokal. Keluaran yang diunduh tidak berisi gambar (`artifacts.py` menolaknya).
#
# **Cara pakai (detail: `RUNBOOK.md`):** Settings → Accelerator **GPU T4 x2**, Internet **On**; Add-ons → Secrets →
# `GITHUB_TOKEN` (token GitHub read-only). Lalu **Run All** dengan `SMOKE_TEST = True` (±20–30 menit). Bila selesai tanpa
# error, ubah ke `False` dan *Save Version → Save & Run All (Commit)*. Data & kode diambil otomatis (tidak perlu dataset Kaggle).

# %% 0. Settings -- SATU-SATUNYA sel yang biasanya diubah
SMOKE_TEST = True           # True = uji cepat (32 foto, 2 epoch, seluruh rantai sampai zip unduhan). Jalankan ini DULU.
                            # Bila selesai tanpa error -> ubah ke False untuk pelatihan sungguhan (± 3-6 jam).
CODE_REF = "main"           # cabang/ tag/ commit repo GitHub Ryhn-F/dentisin-api yang dipakai. Biarkan "main".
PRETRAINED = True           # True = mulai dari bobot COCO YOLOX (risiko lisensi diterima pemilik, ADR 014 §5).
                            # False = mulai dari nol (bebas isu lisensi bobot; butuh lebih banyak epoch, mis. 300).
EPOCHS = None               # None = nilai konfigurasi (150). Angka = jumlah epoch.
BATCH_SIZE = None           # None = nilai konfigurasi (32). Turunkan ke 16 bila muncul "CUDA out of memory".
ACT = "silu"                # Ganti ke "relu" HANYA bila sel 9/15 melaporkan "int8_ok = False" (INT8 tidak lolos) -> latih ulang.
TIME_BUDGET_H = 11.0        # Batas jam per sesi (Kaggle memutus di 12 jam). Pelatihan berhenti sendiri lebih awal,
                            # menyimpan checkpoint, dan dilanjutkan di sesi berikutnya (RUNBOOK §5).
TARGET_SENSITIVITY = 0.85   # Titik operasi pada data val (teknis, bukan klinis). Biarkan.

# %% 1. Preflight + ambil kode (jangan diubah). Gagal di sini = pesan berisi langkah perbaikan.
# >>> inline: bootstrap.py
from bootstrap import (
    HOSTS_COMMON,
    acquire_code,
    check_nbkit_api,
    compute_env,
    default_dirs,
    preflight,
)

# <<< inline
CANDIDATE = "yolox_nano"
import json
import os
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
REPORT = preflight(need_gpu=True, hosts=HOSTS_COMMON, disk_paths={"tmp": TMP, "working": DIRS["work"]},
                   min_free_gb=6.0 if SMOKE_TEST else 12.0, env=ENV)
CODE, CODE_INFO = acquire_code(roots=[DIRS["inputs"]], tmp=TMP, ref=CODE_REF)
DET = CODE / "ml" / "detection"
sys.path[:0] = [str(DET), str(DET / "kaggle")]
# Re-running this cell in a kernel that already imported an OLDER nbkit would silently keep the stale module
# (AttributeError: module 'nbkit' has no attribute ...): drop it so the freshly fetched code is used.
for _m in [m for m in sys.modules if m == "nbkit" or m.startswith("nbkit.")]:
    del sys.modules[_m]
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

# %% 3. Pasang alat: modul kernel yang kurang/ rusak (torch & numpy bawaan Kaggle TIDAK diganti), YOLOX (commit terkunci), venv ekspor Py3.12
nbkit.ensure_kernel_modules(nbkit.YOLOX_KERNEL_MODULES, tmp=TMP)  # import-checked in a fresh process; pins + torch/numpy constraints
YOLOX_COMMIT = "6ddff4824372906469a7fae2dc3206c7aa4bbaee"  # = trainers/yolox_adapter.YOLOX_COMMIT (Apache-2.0, after GPL removal)
YOLOX_REPO = TMP / "YOLOX"
print("YOLOX checkout:", nbkit.ensure_git_checkout("https://github.com/Megvii-BaseDetection/YOLOX.git", YOLOX_COMMIT, YOLOX_REPO))
# No pip install of YOLOX: it is imported from the checkout (PYTHONPATH) -> no build step, no dependency resolver.
if str(YOLOX_REPO) not in os.environ.get("PYTHONPATH", "").split(os.pathsep):  # re-running the cell must not stack entries
    os.environ["PYTHONPATH"] = os.pathsep.join([str(YOLOX_REPO), *filter(None, [os.environ.get("PYTHONPATH")])])
if str(YOLOX_REPO) not in sys.path:
    sys.path.insert(0, str(YOLOX_REPO))
# torch >= 2.6 loads with weights_only=True; YOLOX's own (trusted) checkpoints hold numpy scalars -> allow full load.
os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
UV = nbkit.uv_command()
EXPORT_PY = nbkit.ensure_venv(TMP / "venv-export", nbkit.EXPORT_PY, nbkit.EXPORT_TF_LOCK, uv=UV)  # heartbeat + 1 h timeout
nbkit.use_cpu_only(EXPORT_PY)  # its TensorFlow must not load the kernel's CUDA libraries (CUDA_VISIBLE_DEVICES=-1)
nbkit.require_imports(EXPORT_PY, list(nbkit.EXPORT_REQUIRED_MODULES))  # fail in seconds, not at cell 9
TF_OK = not nbkit.import_failures(EXPORT_PY, list(nbkit.EXPORT_OPTIONAL_MODULES))
print("TensorFlow in the export venv:", TF_OK, "" if TF_OK else "-> onnx2tf tf_converter will fail; cell 9 falls back")
PRETRAINED_PTH = None
if PRETRAINED:  # COCO-pretrained Nano from the YOLOX release (no explicit weights licence -> owner_accepted_risk), sha256-pinned
    PRETRAINED_PTH = nbkit.fetch_verified(nbkit.YOLOX_NANO_URL, TMP / "yolox_nano.pth", nbkit.YOLOX_NANO_SHA256)

# %% 4. Cek cepat alat (gagal dalam menit, bukan setelah berjam-jam): impor persis seperti YOLOX tools/train.py + GPU nyata
nbkit.sh([sys.executable, "-c", "import torch, torchvision, cv2, pycocotools.coco, loguru, thop, onnx, tqdm, tabulate, psutil; "
          "import yolox.core, yolox.exp, yolox.data, yolox.evaluators; from torch.utils.tensorboard import SummaryWriter; "
          "assert hasattr(torch.cuda.amp, 'GradScaler') and hasattr(torch.cuda.amp, 'autocast'), 'YOLOX Trainer needs torch.cuda.amp'; "
          "print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.is_available(), torch.cuda.device_count())"],
         env={"MPLBACKEND": "Agg"}, timeout=900)
nbkit.sh([EXPORT_PY, "-c", "import onnx2tf, onnxsim, ai_edge_litert, numpy; print('onnx2tf', onnx2tf.__version__, 'numpy', numpy.__version__)"],
         timeout=900)
if ENV != "local":
    nbkit.sh([sys.executable, "-c", "import torch; assert torch.cuda.is_available(), 'PyTorch tidak melihat GPU: pilih GPU T4 x2'; "
              "x = torch.nn.Conv2d(3, 8, 3).cuda()(torch.ones(1, 3, 32, 32, device='cuda')); print('CUDA conv ok', float(x.sum()))"],
             timeout=600)

# %% 5. Konfigurasi run + gerbang lisensi (dry run). Penolakan di sini final -- jangan mengubah gerbang dari notebook.
RESUME_FROM = [] if SMOKE_TEST else [str(p) for p in nbkit.find_named([DIRS["inputs"]], "yolox_outputs", want_dir=True, exclude=("_smoke",))]
print("resume candidates:", RESUME_FROM or "none")
RUN_CFG = WORK / "cfg" / f"{CANDIDATE}.yaml"
cfg_over: dict = {
    "data": {"canonical": str(P["canonical"]), "splits": str(P["splits"]), "images_root": str(DATA_ROOT)},
    "model": {"pretrained": str(PRETRAINED_PTH) if PRETRAINED_PTH else None},
    "trainer_options": {"yolox_repo": str(YOLOX_REPO), "act": ACT, "work_dir": str(TMP / ("work_smoke" if SMOKE_TEST else "work")),
                        "output_dir": str(WORK / "yolox_outputs"), "resume_from": RESUME_FROM, "devices": 1},
}
train_over = {k: v for k, v in (("epochs", EPOCHS), ("batch_size", BATCH_SIZE)) if v}
if SMOKE_TEST:  # 2 epochs: epoch 1 with mosaic, epoch 2 = YOLOX "no aug" phase (+ L1 loss), evaluation after each
    train_over = {"epochs": 2, "batch_size": 8}
    cfg_over["trainer_options"].update({"eval_interval": 1, "workers": 2, "resume": "off"})
if train_over:
    cfg_over["train"] = train_over
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG, cfg_over)
RUNS = WORK / "runs"
nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--dry-run", "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET)

# %% 6. Latih (1 GPU; lanjut otomatis dari latest_ckpt.pth; berhenti di batas epoch sebelum batas sesi -> Save Version, lalu jalankan lagi)
TRAIN_H = nbkit.remaining_hours(TIME_BUDGET_H, T0, reserve_h=1.0)
print(f"training budget this session: {TRAIN_H} h")
CFG = nbkit.write_run_config(DET / "configs" / f"{CANDIDATE}.yaml", RUN_CFG,
                             nbkit.deep_merge(cfg_over, {"trainer_options": {"time_budget_h": TRAIN_H}}))
rc = nbkit.sh([sys.executable, "train.py", "--cfg", RUN_CFG, "--allow-unverified-license", "--runs-dir", RUNS], cwd=DET, check=False)
RUN_JSON = nbkit.latest_run(RUNS, CANDIDATE)
run = json.loads(RUN_JSON.read_text(encoding="utf-8"))
print(run["status"], json.dumps(run.get("results"), indent=1))
# YOLOX tools/train.py exits 0 even when it crashed: the adapter checks train_log.txt + the checkpoint epoch instead.
assert rc == 0, f"training failed: {run.get('error')} -- lihat log di atas (RUNBOOK §8)"
DONE = (run.get("results") or {}).get("status") != "time_budget_reached_resume_next_session"
if not DONE:
    print("=" * 78, "\nBATAS WAKTU SESI TERCAPAI -- checkpoint tersimpan di", WORK / "yolox_outputs",
          "\nSel berikutnya dilewati. Langkah: (1) tunggu versi ini selesai; (2) Edit notebook -> Add Input -> Notebook Output",
          "\n-> pilih versi ini; (3) Save & Run All lagi -> pelatihan lanjut dari epoch terakhir (RUNBOOK §5).\n" + "=" * 78)

# %% 7. Ekspor ONNX (kepala mentah: tanpa decode grid, obj/cls = LOGIT -- app menerapkan sigmoid) dari checkpoint terbaik
if DONE:
    from trainers.yolox_adapter import checkpoint_epoch, export_onnx

    CKPT = Path(run["results"]["best_ckpt"])
    if checkpoint_epoch(CKPT) is None:  # written only when val AP improved (never after a 1-2 epoch smoke run)
        CKPT = Path(run["results"]["latest_ckpt"])
    print("checkpoint:", CKPT.name, "epoch", checkpoint_epoch(CKPT))
    ONNX = TMP / "export" / f"{CANDIDATE}.onnx"
    onnx_info = export_onnx(Path(run["results"]["exp_file"]), CKPT, ONNX)
    print(onnx_info)
    assert onnx_info["output_shape"] == [1, 3549, 7] and onnx_info["head_sigmoid_stripped"] == 6, onnx_info
    assert CFG["model"]["decode"]["sigmoid_applied"] is False  # must match the stripped graph (labels.json -> app decoder)

# %% 8. Data representatif (split TRAIN, kalibrasi INT8) + sampel paritas (split VAL). Disimpan di TMP: tidak pernah diekspor.
if DONE:
    CALIB = TMP / "export" / "calib_train.npy"
    PARITY = TMP / "export" / "parity_val.npy"
    # INT8 calibration runs the model once per sample inside onnx2tf (silent, CPU): smoke = 32, full = config (200)
    nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "train", "--out", CALIB,
              *(["--n", "32"] if SMOKE_TEST else [])], cwd=DET, timeout=3600)
    nbkit.sh([EXPORT_PY, "preprocess.py", "calib", "--cfg", RUN_CFG, "--split", "val", "--n", "50", "--out", PARITY], cwd=DET, timeout=3600)

# %% 9. ONNX -> LiteRT dengan rantai cadangan: onnxsim -> INT8 tf_converter -> INT8 flatbuffer_direct (per-channel, lalu
#      per-tensor) -> float16 -> float32. INT8 gagal TIDAK menghentikan run: dicatat (int8_ok) dan ditampilkan mencolok.
if DONE:
    CHAIN = nbkit.tflite_chain(onnx=ONNX, calib=CALIB, samples=PARITY, out_root=TMP / "export", report_dir=WORK / "out",
                               export_py=EXPORT_PY, det=DET)
    MODEL, TFL32, PRECISION, INT8_OK = Path(CHAIN["model"]), Path(CHAIN["float32"]), CHAIN["precision"], CHAIN["int8_ok"]
    SIZE = nbkit.size_gate(MODEL.stat().st_size, float(CFG["export"]["max_size_mb"]))
    SIZE_GATE = "report" if SMOKE_TEST else "enforce"  # smoke: the size gate is reported, never fatal
    print(json.dumps({k: CHAIN.get(k) for k in ("precision", "int8_method", "int8_ok", "onnx_simplified", "conversion_parity",
                                                 "model_size_mb")}, indent=1), "\nsize gate:", SIZE)
    if not INT8_OK:
        nbkit.banner([f"PERHATIAN: model INT8 TIDAK lolos (presisi dipakai: {PRECISION}; metode: {CHAIN['int8_method']}).",
                      "Run tetap dilanjutkan sampai zip unduhan; measurements_partial.json mencatat int8_ok = false.",
                      "Pemilik produk memutuskan: run ulang dengan ACT = 'relu' (sel 0) atau terima. Detail: out/conversion.json"])
    if (CHAIN.get("conversion_parity") or {}).get("passed") is False:
        nbkit.banner(["PERHATIAN: TFLite float32 berbeda dari graf ONNX (paritas konversi gagal) -- lihat out/conversion.json."])
    if not SIZE["ok"]:
        nbkit.banner([f"GERBANG UKURAN: model {SIZE['size_mb']} MiB > {SIZE['max_mb']} MiB (ADR 001)."
                      + (" Uji cepat: hanya dilaporkan." if SMOKE_TEST else "")])

# %% 10. Bundel dev (gerbang ukuran <= 15 MB, labels.json dari interpreter) + paritas model terpilih vs float32 pada 50 foto val
if DONE:
    EXPORTS = WORK / "exports"
    VERSION_BASE = "0.1.0"
    VERSION = nbkit.next_dev_version(EXPORTS, VERSION_BASE)
    BUNDLE = EXPORTS / VERSION
    nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--precision", PRECISION, "--size-gate", SIZE_GATE,
              "--tflite-in", MODEL, "--out", BUNDLE, "--run-id", run["run_id"], "--allow-unverified-license"], cwd=DET)
    PARITY_JSON = WORK / "out" / "parity.json"
    PARITY_JSON.unlink(missing_ok=True)
    nbkit.sh([EXPORT_PY, "check_int8_parity.py", "--float-model", TFL32, "--int8-model", BUNDLE / "model.tflite",
              "--samples", PARITY, "--labels", BUNDLE / "labels.json", "--report", PARITY_JSON], cwd=DET, check=False, timeout=1800)
    parity = json.loads(PARITY_JSON.read_text(encoding="utf-8")) if PARITY_JSON.is_file() else {"passed": False, "failures": ["no report"]}
    print(f"parity ({PRECISION} vs float32) passed:", parity["passed"], parity["failures"])

# %% 11. Prediksi dengan bundel (preprocess + decode persis app) pada val dan test; float32 sebagai pembanding pada test
if DONE:
    OUT = WORK / "out"
    common = ["--gt", P["canonical"], "--splits", P["splits"], "--images-root", DATA_ROOT]
    for split in ("val", "test"):
        nbkit.sh([EXPORT_PY, "predict.py", "--model", BUNDLE / "model.tflite", "--labels", BUNDLE / "labels.json", "--split", split,
                  *common, "--out", OUT / f"preds_int8_{split}.jsonl"], cwd=DET, timeout=7200)
    nbkit.sh([EXPORT_PY, "predict.py", "--model", TFL32, "--labels", BUNDLE / "labels.json", "--split", "test",
              *common, "--out", OUT / "preds_float_test.jsonl"], cwd=DET, timeout=7200)

# %% 12. Ambang pada VAL, lalu evaluasi (val + test, CI bootstrap per kelompok pasien). Model uji cepat boleh 0 deteksi.
if DONE:
    THR = OUT / "thresholds.json"
    nbkit.sh([EXPORT_PY, "thresholds.py", "--pred", OUT / "preds_int8_val.jsonl", "--gt", P["canonical"], "--splits", P["splits"],
              "--target-sensitivity", str(TARGET_SENSITIVITY), "--out", THR], cwd=DET)
    for split, preds in (("val", "preds_int8_val.jsonl"), ("test", "preds_int8_test.jsonl"), ("test", "preds_float_test.jsonl")):
        tag = preds.replace("preds_", "").replace(".jsonl", "")
        nbkit.sh([EXPORT_PY, "eval.py", "--gt", P["canonical"], "--splits", P["splits"], "--split", split, "--pred", OUT / preds,
                  "--metadata", META_CSV, "--thresholds", THR, "--candidate", CANDIDATE, "--model-version", VERSION,
                  "--report", OUT / f"report_{tag}.json"], cwd=DET, timeout=3600)

# %% 13. Bundel final dengan ambang (status set_on_val) + bukti evaluasi & paritas; verifikasi
if DONE:
    FINAL = EXPORTS / nbkit.next_dev_version(EXPORTS, VERSION_BASE)  # bundles are immutable -> next dev number
    nbkit.sh([EXPORT_PY, "export.py", "--cfg", RUN_CFG, "--format", "tflite", "--precision", PRECISION, "--size-gate", SIZE_GATE,
              "--tflite-in", MODEL, "--out", FINAL, "--run-id", run["run_id"], "--thresholds", THR,
              "--eval-report", OUT / "report_int8_test.json", "--parity-report", PARITY_JSON, "--allow-unverified-license"], cwd=DET)
    nbkit.sh([EXPORT_PY, "export.py", "verify-bundle", FINAL, "--size-gate", SIZE_GATE], cwd=DET)

# %% 14. Catatan run + zip unduhan (TANPA gambar, TANPA .npy, TANPA checkpoint)
if DONE:
    MEAS = OUT / "measurements_partial.json"
    size_mb = round((FINAL / "model.tflite").stat().st_size / 2**20, 3)
    MEAS.write_text(json.dumps({CANDIDATE: {
        "int8_size_mb": size_mb if PRECISION == "int8" else None, "model_size_mb": size_mb, "precision": PRECISION,
        "int8_method": CHAIN["int8_method"], "int8_ok": INT8_OK, "size_gate_ok": SIZE["ok"],
        "conversion_parity_passed": (CHAIN.get("conversion_parity") or {}).get("passed"),
        "export_notes": f"onnx2tf 2.6.9 chain: {CHAIN['int8_method']}; precision={PRECISION}; act={ACT}; pretrained={PRETRAINED}; head=logits",
        "parity_passed": parity["passed"] if PRECISION == "int8" else None, "weight_license_clarity": "owner_accepted_risk",
        "smoke_test": SMOKE_TEST}}, indent=1), encoding="utf-8")
    (OUT / "env.json").write_text(json.dumps(nbkit.env_record({"onnx": onnx_info, "processed": info, "code": CODE_INFO,
                                                               "data_source": DATA["source"], "smoke_test": SMOKE_TEST,
                                                               "tf_in_export_venv": TF_OK}), indent=1), encoding="utf-8")
    files = [RUN_JSON, THR, MEAS, OUT / "env.json", OUT / "conversion.json", *sorted(OUT.glob("report_*.json")),
             *sorted(OUT.glob("parity*.json")), RUN_CFG]
    nbkit.sh([EXPORT_PY, "artifacts.py", "collect", "--out-dir", WORK / f"download_{CANDIDATE}", "--bundle", FINAL,
              *[a for f in files if f.is_file() for a in ("--file", f)]], cwd=DET)

# %% 15. Pemeriksaan akhir: tidak ada foto di keluaran + daftar berkas yang perlu diunduh
bad = nbkit.forbidden_outputs(DIRS["work"])
assert not bad, f"photos/arrays found in the persisted outputs (must never happen): {bad[:5]}"
print("\n".join(nbkit.describe_outputs(WORK)))
print(f"\nSelesai dalam {(time.time() - T0) / 3600:.2f} jam. SMOKE_TEST = {SMOKE_TEST}.")
if DONE and not (INT8_OK and SIZE["ok"]):
    nbkit.banner([f"RINGKASAN: presisi {PRECISION}, int8_ok = {INT8_OK}, gerbang ukuran ok = {SIZE['ok']} -- lihat out/conversion.json."])
if SMOKE_TEST:
    print("UJI CEPAT BERHASIL. Ubah SMOKE_TEST = False di sel 0, lalu Save Version -> Save & Run All (Commit).")
elif DONE:
    print(f"Unduh {WORK / f'download_{CANDIDATE}.zip'} (tab Output) -> backend/ml/runs/kaggle/ (RUNBOOK §6).")
