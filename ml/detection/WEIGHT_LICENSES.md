# WEIGHT_LICENSES — pretrained weights of the bake-off candidates (item 2.4)

> Read 2026-10-09 by the ML engineer (Claude) from the primary sources linked below (GitHub
> repositories via the GitHub API / raw files, Kaggle Models API, PyPI JSON, official docs).
> Summaries are paraphrased, not quoted. **Not legal advice** — the open questions in §5 go to the
> product owner / legal adviser (⚖). Machine-readable status lives in `../data/license_gate.yaml`
> (owned by the data engineer) — the patch to apply is in §4. Configs carry `license_verified: true`
> **only** where both code and weights licences were read from a primary source.

## 1. Verdicts

| Candidate (config) | Code licence | Pretrained weights licence | Verdict | `license_verified` |
|---|---|---|---|---|
| `yolox_nano` | Apache-2.0 — [LICENSE](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE) | **No explicit licence.** `yolox_nano.pth` is a release asset ([0.1.1rc0](https://github.com/Megvii-BaseDetection/YOLOX/releases/tag/0.1.1rc0)) of the Apache-2.0 repo, with no separate terms; an explicit question to the maintainers ([#1865](https://github.com/Megvii-BaseDetection/YOLOX/issues/1865), 2026-03-31) is unanswered | **Not verified.** ADR 014 §5 (2026-10-10): the owner **accepts the risk** ("implied Apache-2.0", competition use) → config `license_verified: true` + `license_note: owner_accepted_risk`, recorded as basis `owner_accepted_risk` (never "verified"); re-review before commercial/public release | `true` |
| `mediapipe_modelmaker_mobilenetv2_ssd` (variant `MOBILENET_V2` only) | Apache-2.0 — [mediapipe LICENSE](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE); PyPI `mediapipe-model-maker` 0.2.1.4 "Apache 2.0" | **Apache-2.0** — Model Maker downloads `mobilenetv2_ssd_i256_ckpt.tar.gz` from `storage.googleapis.com/tf_model_garden/vision/qat/mobilenetv2_ssd_coco/` ([model_spec.py](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/model_maker/python/vision/object_detector/model_spec.py)); that exact checkpoint is published by the TF Model Garden QAT project whose README ends with an Apache-2.0 licence section ([README](https://github.com/tensorflow/models/blob/master/official/projects/qat/vision/README.md)); trained on COCO **from scratch** (README) → no ImageNet dependency | **Usable** (residual: COCO image provenance, §2) | `true` |
| same, variants `MOBILENET_V2_I320`, `MOBILENET_MULTI_AVG(_I384)` | idem | Checkpoints (`mobilenetv2_ssd_i320_ckpt`, `mobilenetv3.5_ssd_i256/i384_ckpt`) are **not listed** in the QAT README | Not verified — do not switch variant without re-checking | (adapter refuses) |
| `efficientdet_lite0` | Apache-2.0 — [google/automl LICENSE](https://github.com/google/automl/blob/master/LICENSE); tflite-model-maker in [tensorflow/examples](https://github.com/tensorflow/examples) (Apache-2.0); PyPI `tflite-model-maker` 0.4.3 "Apache 2.0" | **Apache-2.0** — tflite-model-maker loads `tfhub.dev/tensorflow/efficientdet/lite0/feature-vector/1` ([object_detector_spec.py](https://github.com/tensorflow/examples/blob/master/tensorflow_examples/lite/model_maker/core/task/model_spec/object_detector_spec.py)); Kaggle Models API for `tensorflow/efficientdet` lists every variation incl. `lite0-feature-vector`, `lite0-detection` and `TfLite/lite0-int8` with licence "Apache 2.0" ([model page](https://www.kaggle.com/models/tensorflow/efficientdet), API `GET /api/v1/models/tensorflow/efficientdet/get`) | **Licence OK; toolchain is the problem** (tflite-model-maker deprecated, last release 2024-01-23, old Python/TF) → fallback only | `true` |
| `rfdetr_nano` | Apache-2.0 — [LICENSE](https://github.com/roboflow/rf-detr/blob/develop/LICENSE) (default branch `develop`) | README model table: **RF-DETR-N = Apache 2.0**; Atto/Femto/Pico/XL/2XL = PML 1.0 (never usable) ([README](https://github.com/roboflow/rf-detr/blob/develop/README.md)) | **Licence OK but EXCLUDED: 30.5 M parameters (README) ⇒ ~30 MB even at INT8 > 15 MB size gate**; no documented TFLite path | `true` |
| MediaPipe Tasks pretrained `ssd_mobilenet_v2.tflite` / `efficientdet_lite0.tflite` (inference models on the [Object Detector page](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector)) | — | No model-specific licence on the page (only site-wide CC BY 4.0 text / Apache-2.0 code samples), no model card link | Not used (we fine-tune; we never ship these files) | — |
| Ultralytics YOLOv5/v8/11 | AGPL-3.0 | AGPL-3.0 | **Forbidden** (ADR 001/008) | — |

## 2. Residual risk common to all COCO-pretrained weights
COCO [terms of use](https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/termsofuse.htm) (read 2026-10-09): annotations CC BY 4.0
(COCO Consortium); the Consortium does **not** own the image copyrights, image use must follow the Flickr
terms, and users take responsibility for image use. All four candidates were pretrained on COCO by their
publishers; we never redistribute COCO images. Whether weights trained on such images carry obligations is
an open legal question shared by the whole industry → recorded, not blocking (⚖ question Q-W2 below).
Credit line suggestion for the About screen: "Model initialised from weights pretrained on COCO (cocodataset.org, annotations CC BY 4.0)".

## 3. Candidate notes

### 3.1 YOLOX-Nano
- Pin YOLOX to commit `6ddff4824372906469a7fae2dc3206c7aa4bbaee` (main, 2025-06-08; `trainers/yolox_adapter.YOLOX_COMMIT`).
  Releases ≤ 0.1.1rc0 contained augmentation code copied from GPL-3.0 YOLOv5
  ([#765](https://github.com/Megvii-BaseDetection/YOLOX/issues/765), maintainer acknowledged; fixed by PR #781 before 0.2.0).
  Do **not** use PyPI `yolox` 0.3.0 wheels or tags < 0.2.0. Note: the published `yolox_nano.pth` (Aug 2021) was trained
  with that older code — training code licences normally do not attach to model outputs, but list it for ⚖ (Q-W1).
- YOLOX `requirements.txt` pins `onnx-simplifier==0.4.10`; the notebook installs YOLOX with `--no-deps` and only the needed
  training-time packages (loguru, tabulate, pycocotools, thop, ninja, psutil, tensorboard — none GPL; not shipped).
- **Licence-free fallback:** `PRETRAINED = False` in the notebook (YOLOX-Nano from random init, longer schedule). Then only
  the Apache-2.0 code is involved; expect lower accuracy. Gate entry proposal `yolox_nano_scratch` in §4.
- INT8 risk: SiLU + depthwise convs + large SPP max-pools quantise poorly (onnx2tf README §"accuracy of the INT8 quantized model
  degrades"; the community fix is ReLU — `trainer_options.act: relu`). Decide by `check_int8_parity.py`, not by assumption.
- **Local conversion experiment (2026-10-10, Windows, onnx2tf 2.6.9 default `flatbuffer_direct`, no TensorFlow; tiny RANDOM
  graph with the YOLOX raw-head I/O, 20 synthetic images):** (1) a head ending in `Sigmoid` fails INT8 export
  ("LOGISTIC output scale must be 1/256") → the YOLOX export now strips the head obj/cls Sigmoid (raw logits,
  `sigmoid_applied: false`; SiLU's Sigmoid would hit the same check → `act: relu` or the `tf_converter` backend);
  (2) without Sigmoid the INT8 files are produced, float32 TFLite matches ONNX (max err 2e-5), **but INT8 parity is poor
  (cosine 0.45; Conv→Reshape scale mismatch 0.017 vs 0.241 copied without requantisation)** → the notebook converts with
  `-tb tf_converter` (TensorFlow 2.21 in the export venv) AND `flatbuffer_direct` and keeps the first variant that passes parity.
  Bundle/labels/parity/predict/thresholds/eval/artifact steps all ran on the real `.tflite` files.

### 3.2 MediaPipe Model Maker (MobileNetV2, i256)
- Model Maker docs: "still available, but no longer actively maintained" (2026-10-09); no replacement named. MediaPipe *Tasks*
  `ObjectDetector` is inference-only, so Model Maker is the only Google-supported fine-tuning route for this family.
- Python ≤ 3.10 (classifiers), `tensorflow<2.16`, `tf-models-official<2.16` → separate Python 3.10 env in the notebook.
- INT8 only via **QAT** (Model Maker offers PTQ only for float16). The QAT README reports, for this exact model on COCO:
  FP32 mAP 23.3, PTQ INT8 0.04, QAT INT8 21.7 — i.e. post-training INT8 of this architecture collapses; QAT is mandatory.
- Export (read from source, not yet from a real export): raw encoded boxes + sigmoid scores incl. background column, no NMS,
  anchors in metadata, uint8 I/O → `ssd_raw_anchors` with scales 1/1/1/1 (configs updated accordingly).

### 3.3 Alternatives considered for the "Model Maker is deprecated" problem
| Route | Code / weights | Why not primary |
|---|---|---|
| EfficientDet-Lite0 via tflite-model-maker | Apache / Apache (verified) | Deprecated toolchain; Python 3.9-era deps (tflite-support, tensorflow-addons EOL) — highest install-failure risk. Fallback #3 |
| TF Object Detection API (SSD MobileNet V2 FPNLite) | Apache-2.0 code; TF2 model zoo weights [PERLU VERIFIKASI — not read] | TF1/TF2 OD API is in maintenance mode; protobuf/compile steps fragile on current Kaggle images |
| torchvision `ssdlite320_mobilenet_v3_large` | BSD-3 code; torchvision states pretrained weights may carry their own dataset-derived terms [PERLU VERIFIKASI — not read] | Hard-swish/SE blocks + PyTorch→TFLite path = same conversion risk as YOLOX without its accuracy |
| `litert-torch` (ex `ai-edge-torch`, PyPI 0.9.4, Apache-2.0) as PyTorch→LiteRT converter for YOLOX | Apache-2.0 | Requires `torch<2.14`, pulls transformers/jax; viable **plan B for YOLOX conversion** if onnx2tf fails |

## 4. Patch for `../data/license_gate.yaml` (to be applied by the data engineer)
`train.py`/`export.py` **hard-refuse** a config that says `license_verified: true` while the gate still has
`verified: false` — so until this patch lands, the MediaPipe notebook stops at the licence cell. Apply:
```yaml
  mediapipe_modelmaker_mobilenetv2_ssd:
    weights:
      license: "Apache-2.0"
      verified: true
      source: "MOBILENET_V2 i256 only: ckpt URL in mediapipe model_maker object_detector/model_spec.py; checkpoint published by https://github.com/tensorflow/models/blob/master/official/projects/qat/vision/README.md (License: Apache 2.0; trained on COCO from scratch) (read 2026-10-09)"
  efficientdet_lite0:
    weights:
      license: "Apache-2.0"
      verified: true
      source: "Kaggle Models API tensorflow/efficientdet: variations lite0-feature-vector / lite0-detection / TfLite lite0-int8 licenseName 'Apache 2.0' (read 2026-10-09)"
  rfdetr_nano:
    code: {license: "Apache-2.0", verified: true, source: "https://github.com/roboflow/rf-detr/blob/develop/LICENSE (read 2026-10-09)"}
    weights: {license: "Apache-2.0", verified: true, source: "rf-detr README model table: RF-DETR-N Apache 2.0 (read 2026-10-09)"}
    notes: "Excluded by size: 30.5 M params -> INT8 > 15 MB gate."
  yolox_nano:
    weights:
      license: "Apache-2.0 (implied, not stated)"
      verified: false                    # stays false: this is NOT a verification
      basis: owner_accepted_risk         # ADR 014 §5 -- license_gate.py recognises basis / license_note / owner_accepted_risk: true
      source: "No explicit weights licence; release asset of Apache-2.0 repo; maintainer question YOLOX#1865 (2026-03-31) unanswered (read 2026-10-09); owner accepted risk 2026-10-10 (ADR 014 §5)"
  # optional, if the owner wants the licence-free YOLOX route (random init, PRETRAINED=False):
  # yolox_nano_scratch: {code: {license: Apache-2.0, verified: true, source: ...}, weights: {license: "none (random init)", verified: true, source: "no pretrained weights used"}, status: candidate}
```
`LICENSES.md` (also the data engineer's) should gain the same rows plus the **training-time-only** tools used by the notebooks
(not shipped): onnx2tf 2.6.9 (MIT), onnxruntime 1.26.0 (MIT), onnxsim 0.6.5 (MIT AND (Apache-2.0 OR BSD-2-Clause)), onnx 1.20.1
(Apache-2.0), ai-edge-litert 2.1.2 (Apache-2.0 classifier), opencv-python 4.13.0.92 (Apache 2.0), mediapipe-model-maker
0.2.1.4 (Apache 2.0), tensorflow 2.15.1 (Apache 2.0), YOLOX training deps loguru 0.7.3 / thop (MIT), tabulate (MIT [PERLU VERIFIKASI]),
pycocotools 2.0.11 (FreeBSD), ninja 1.13.0 (Apache/BSD), tensorboard (Apache-2.0 [PERLU VERIFIKASI]) — from PyPI JSON 2026-10-09.
PyTorch on Kaggle/Colab is the pre-installed kernel build (BSD-3-style, PyPI lists Apache-2.0 AND BSD-3-Clause AND … for 2.14.1).

## 5. Questions for the owner / ⚖
- **Q-W1** ~~Accept YOLOX-Nano weights as "implied Apache-2.0"?~~ **Decided 2026-10-10 (ADR 014 §5): owner accepts the risk for
  competition use** → basis `owner_accepted_risk`; still open for any commercial/public release (written confirmation or random init).
- **Q-W2** Acceptable to ship a model *initialised* from COCO-pretrained weights given COCO's image terms (§2)?
- **Q-W3** Attribution text for Apache-2.0 weights (NOTICE) on the About screen together with the CC BY 4.0 dataset credit.
