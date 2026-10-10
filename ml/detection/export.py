"""Build the model contract bundle consumed by the app (backend/CLAUDE.md §1):

    ml/exports/<semver>/{model.tflite, labels.json, MODEL_CARD.md, SHA256}   (relative to backend/, ADR 009)

    python export.py --cfg configs/yolox_nano.yaml --format tflite --int8 \
        --tflite-in <converted_int8.tflite> --out ../exports/0.1.0 [--anchors anchors.json] \
        [--thresholds thresholds.json] [--run-id <id>] [--eval-report report.json] \
        [--parity-report parity.json] [--max-size-mb 15] [--allow-unverified-license] [--release]
    python export.py ... --onnx-in model.onnx --calib calib.npy   # needs: uv sync --extra export (Linux)
    python export.py verify-bundle ../exports/0.1.0

Gates: license gate (same as train.py), TFLite flatbuffer identifier, INT8 size <= max_size_mb
(MiB, default from config = 15), labels.json schema + semantic checks. ``--release`` additionally
requires verified licenses, reviewed thresholds, verified tensors and validation evidence, so it
fails today by design. Nothing here signs the manifest (tools/sign_manifest.py does that).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from canonical import CLASS_NAMES
from decode import SPEC_VERSION
from license_gate import DEFAULT_GATE_PATH, GateDecision, evaluate_gate, load_gate
from train import ConfigError, load_config

HERE = Path(__file__).resolve().parent
LABELS_SCHEMA_PATH = HERE / "labels.schema.json"
BUNDLE_FILES = ("model.tflite", "labels.json", "MODEL_CARD.md")
MIB = 1024 * 1024
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")
REQUIRED_ROLES = {
    "yolox_raw": {"raw"},
    "ssd_raw_anchors": {"raw_boxes", "raw_scores"},
    "ssd_postprocessed": {"boxes", "classes", "scores", "count"},
    "detr_raw": {"boxes", "logits"},
}
DEFAULT_POSTPROCESS = {"nms_iou_threshold": 0.45, "class_agnostic_nms": False, "max_detections": 20, "status": "technical_default"}
UI_LABEL_KEYS = {"caries_permanent": "finding.caries_permanent", "caries_primary": "finding.caries_primary"}
#: ADR 014 §3: triage reads class, confidence and the NUMBER of findings (never box geometry); both classes count.
MAY_RAISE = {"caries_permanent": True, "caries_primary": True}
#: ADR 014: classes encode the DENTITION of the tooth with a caries indication (Ahmed labels D / d).
DENTITION = {"caries_permanent": "permanent", "caries_primary": "primary"}
#: ADR 014 §2: boxes surround whole teeth; their size/area must never be used for severity.
BOX_USE = "ui_overlay_only"


class ExportError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_tflite(data: bytes) -> bool:
    """TFLite flatbuffers carry the file identifier 'TFL3' at bytes 4..8."""
    return len(data) >= 8 and data[4:8] == b"TFL3"


def check_size(size_bytes: int, max_size_mb: float) -> None:
    if size_bytes > max_size_mb * MIB:
        raise ExportError(f"model is {size_bytes / MIB:.2f} MiB > size gate {max_size_mb} MiB (ADR 001)")


def yolox_rows(w: int, h: int, strides: Sequence[int]) -> int:
    return sum((w // s) * (h // s) for s in strides)


# ------------------------------------------------------------------------------ labels.json


def build_decode(cfg: Mapping[str, Any], anchors: list[list[float]] | None) -> dict[str, Any]:
    fmt = cfg["model"]["output_format"]
    d = dict(cfg["model"].get("decode", {}))
    if fmt == "yolox_raw":
        return {"type": fmt, "strides": list(d.get("strides", [8, 16, 32])), "sigmoid_applied": bool(d.get("sigmoid_applied", True)),
                "row_order": "stride_major_then_y_then_x",
                "box_formula": "cx=(tx+gx)*s; cy=(ty+gy)*s; w=exp(tw)*s; h=exp(th)*s",
                "score_formula": "score=obj*max_c(cls_c); class=argmax_c"}
    if fmt == "ssd_raw_anchors":
        if not anchors:
            raise ExportError("ssd_raw_anchors needs --anchors (JSON list of [ycenter, xcenter, h, w])")
        return {"type": fmt, "anchors": anchors, "anchor_format": "ycenter_xcenter_h_w_normalized",
                "scales": list(d.get("scales", [10.0, 10.0, 5.0, 5.0])), "box_order": d.get("box_order", "yxhw"),
                "score_activation": d.get("score_activation", "sigmoid"), "background_class": bool(d.get("background_class", False))}
    if fmt == "ssd_postprocessed":
        return {"type": fmt, "box_format": "ymin_xmin_ymax_xmax_normalized", "class_id_offset": int(d.get("class_id_offset", 0))}
    if fmt == "detr_raw":
        return {"type": fmt, "box_format": "cx_cy_w_h_normalized", "score_activation": "sigmoid"}
    raise ExportError(f"unknown output format {fmt!r}")


def normalize_tensors(tensors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"index": int(t["index"]), "name": t.get("name"), "role": t["role"], "shape": [int(v) for v in t["shape"]],
         "dtype": t["dtype"], "quantization": t.get("quantization")}
        for t in tensors
    ]


def build_labels(
    cfg: Mapping[str, Any],
    decision: GateDecision,
    *,
    version: str,
    model_sha256: str,
    size_bytes: int,
    created_at: str,
    anchors: list[list[float]] | None = None,
    tensors: Sequence[Mapping[str, Any]] | None = None,
    tensors_verified: bool = False,
    input_quantization: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, Any] | None = None,
    distribution: str = "pilot",
    run_id: str | None = None,
    eval_report_sha256: str | None = None,
    parity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    m = cfg["model"]
    pre = m["preprocess"]
    w, h = m["input_size"]
    lo, hi = pre["pixel_range"]
    thr = dict(thresholds or {})
    attribution: list[dict[str, str]] = []
    for ds in decision.dataset_licenses:
        for lang in ("id", "en"):
            text = ds.get(f"attribution_{lang}")
            if text:
                attribution.append({"lang": lang, "text": " ".join(str(text).split())})
    model_lic = decision.model_license
    return {
        "schema_version": "2.0.0",  # 2.0.0: ADR 014 classes (dentition), box_use, licenses.weights.basis
        "model": {
            "name": "dentisai-caries-detector",
            "version": version,
            "candidate": cfg["candidate"],
            "variant": m["variant"],
            "created_at": created_at,
            "run_id": run_id,
            "eval_report_sha256": eval_report_sha256,
            "sha256": model_sha256,
            "size_bytes": size_bytes,
            "int8": bool(cfg["export"]["int8"]),
        },
        "validation_status": "NOT_VALIDATED",
        "intended_use": "screening_only",
        "distribution": distribution,
        "classes": [
            {"id": i, "name": n, "dentition": DENTITION[n], "may_raise_triage_level": MAY_RAISE[n], "ui_label_key": UI_LABEL_KEYS[n]}
            for i, n in enumerate(CLASS_NAMES)
        ],
        "input": {
            "width": int(w),
            "height": int(h),
            "channels": 3,
            "layout": "NHWC",
            "color_order": pre["color_order"],
            "dtype": pre["input_dtype"],
            "normalization": {
                "pixel_range": [lo, hi],
                "mean": list(pre["mean"]),
                "std": list(pre["std"]),
                "formula": "v = lo + p*(hi-lo)/255 for 8-bit pixel p; x = (v - mean[c]) / std[c]; "
                           "if dtype is int8/uint8: q = round(x/scale) + zero_point",
            },
            "quantization": dict(input_quantization) if input_quantization else None,
            "resize": {"method": pre["resize_method"], "align": pre["align"], "pad_value": pre["pad_value"],
                       "interpolation": pre["interpolation"]},
        },
        "output": {
            "format": m["output_format"],
            "tensors": normalize_tensors(tensors if tensors is not None else m["output_tensors"]),
            "tensors_verified": tensors_verified,
            "decode": build_decode(cfg, anchors),
            "postprocess": dict(DEFAULT_POSTPROCESS),
        },
        "thresholds": {
            "score": {c: thr.get(c) for c in CLASS_NAMES},
            "status": thr.get("status", "PLACEHOLDER") if all(thr.get(c) is not None for c in CLASS_NAMES) else "PLACEHOLDER",
            "source": thr.get("source"),
        },
        "licenses": {
            "code": {k: model_lic.get("code", {}).get(k) for k in ("license", "verified", "source")},
            "weights": {**{k: model_lic.get("weights", {}).get(k) for k in ("license", "source")},
                        # 'verified' is true ONLY for a primary-source verification; owner_accepted_risk stays false
                        "verified": decision.license_basis == "verified", "basis": decision.license_basis},
            "datasets": [{k: ds.get(k) for k in ("id", "license", "url", "doi")} for ds in decision.dataset_licenses],
            "model_artifact": "Proprietary DentisAI model (closed repo, ADR 008); third-party terms above apply.",
        },
        "attribution": attribution,
        "box_use": BOX_USE,
        "int8_parity": dict(parity) if parity else None,
        "decode_spec": {"document": "ml/detection/DECODE_SPEC.md", "version": SPEC_VERSION, "reference_impl": "ml/detection/decode.py"},
    }


def validate_labels(labels: Mapping[str, Any]) -> list[str]:
    """JSON-Schema + semantic checks. Returns problems (empty = valid)."""
    schema = json.loads(LABELS_SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = [f"schema: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                for e in jsonschema.Draft202012Validator(schema).iter_errors(labels)]
    if problems:
        return problems
    names = [c["name"] for c in labels["classes"]]
    ids = [c["id"] for c in labels["classes"]]
    if tuple(names) != CLASS_NAMES or ids != list(range(len(CLASS_NAMES))):
        problems.append(f"classes must be {list(CLASS_NAMES)} with ids 0..{len(CLASS_NAMES) - 1}")
    out = labels["output"]
    roles = [t["role"] for t in out["tensors"]]
    if set(roles) != REQUIRED_ROLES[out["format"]] or len(roles) != len(set(roles)):
        problems.append(f"output tensors roles {roles} != {sorted(REQUIRED_ROLES[out['format']])} for {out['format']}")
    if out["format"] != out["decode"]["type"]:
        problems.append("output.format != output.decode.type")
    by_role = {t["role"]: t for t in out["tensors"]}
    n_cls = len(CLASS_NAMES)
    if out["format"] == "yolox_raw" and "raw" in by_role:
        shape = by_role["raw"]["shape"]
        rows = yolox_rows(labels["input"]["width"], labels["input"]["height"], out["decode"]["strides"])
        if len(shape) != 3 or shape[1] not in (rows, -1) or shape[2] != 5 + n_cls:
            problems.append(f"yolox raw shape {shape} != [1, {rows}, {5 + n_cls}]")
    if out["format"] == "ssd_raw_anchors" and "raw_boxes" in by_role:
        n_anchor = len(out["decode"]["anchors"])
        rows = by_role["raw_boxes"]["shape"][1] if len(by_role["raw_boxes"]["shape"]) >= 2 else -1
        if rows not in (-1, n_anchor):
            problems.append(f"{n_anchor} anchors but raw_boxes has {rows} rows")
        expected_cols = n_cls + (1 if out["decode"]["background_class"] else 0)
        if "raw_scores" in by_role and by_role["raw_scores"]["shape"][-1] != expected_cols:
            problems.append(f"raw_scores last dim {by_role['raw_scores']['shape'][-1]} != {expected_cols}")
    if labels["input"]["dtype"] in ("int8", "uint8") and labels["input"]["quantization"] is None and labels["output"]["tensors_verified"]:
        problems.append("quantized input needs input.quantization")
    if any(v is None for v in labels["thresholds"]["score"].values()) and labels["thresholds"]["status"] != "PLACEHOLDER":
        problems.append("null thresholds must have status PLACEHOLDER")
    return problems


# ------------------------------------------------------------------------------- model card


def render_model_card(labels: Mapping[str, Any], *, eval_summary: Mapping[str, Any] | None = None, limitations: Sequence[str] = ()) -> str:
    m = labels["model"]
    lic = labels["licenses"]
    thr = labels["thresholds"]
    parity = labels.get("int8_parity")
    lines = [
        f"# MODEL CARD — {m['name']} {m['version']}",
        "",
        "> **⚠ NOT VALIDATED — BELUM DIVALIDASI.** Model ini belum divalidasi secara klinis pada anak Indonesia.",
        "> Hasil hanya **indikasi** untuk skrining, **bukan diagnosis**; selalu rujuk ke dokter gigi/ puskesmas.",
        f"> Distribusi: `{labels['distribution']}` · Status validasi: `{labels['validation_status']}`",
        *(["> **⚠ LISENSI BOBOT PRA-LATIH TIDAK TERVERIFIKASI — `owner_accepted_risk`.** Pemilik produk menerima risiko lisensi",
           "> implisit (Apache-2.0 tersirat) untuk kompetisi (ADR 014 §5). Wajib ditinjau ulang sebelum rilis komersial/ publik."]
          if lic["weights"].get("basis") == "owner_accepted_risk" else []),
        "",
        "## Ringkasan (Bahasa Indonesia)",
        "- **Fungsi:** menandai **indikasi karies pada gigi sulung/ tetap** (`caries_primary` / `caries_permanent`) pada foto "
        "gigi depan — hasilnya \"perlu diperiksa\", bukan diagnosis.",
        "- **Kotak** melingkupi seluruh gigi yang tampak berlubang/ bernoda; **ukuran kotak tidak menunjukkan keparahan** dan "
        "hanya dipakai untuk penanda di layar (ADR 014). Model tidak membedakan berlubang vs belum berlubang.",
        "- **Bukan untuk:** diagnosis, menilai keparahan, sela-sela gigi, bagian dalam gigi, gigi belakang yang tidak terlihat.",
        "- **Data latih:** dataset publik (lihat Lisensi); **belum ada data anak Indonesia**.",
        f"- **Ambang skor:** `{thr['status']}`" + (" — belum ditetapkan; tidak boleh dipakai untuk rilis." if thr["status"] == "PLACEHOLDER" else ""),
        "",
        "## Model details (English)",
        f"- Candidate: `{m['candidate']}` (variant `{m['variant']}`), INT8: {m.get('int8')}, size: {m.get('size_bytes', 0) / MIB:.2f} MiB",
        f"- model.tflite sha256: `{m['sha256']}`",
        f"- Training run: `{m.get('run_id')}` · Eval report sha256: `{m.get('eval_report_sha256')}`",
        f"- Created: {m['created_at']}",
        f"- Input: {labels['input']['width']}x{labels['input']['height']} {labels['input']['color_order']} "
        f"{labels['input']['dtype']}, resize `{labels['input']['resize']['method']}`",
        f"- Output: `{labels['output']['format']}` (tensors verified by interpreter: {labels['output']['tensors_verified']}); "
        f"decode per `{labels['decode_spec']['document']}` v{labels['decode_spec']['version']}",
        "",
        "## Intended use",
        "On-device screening aid for school-age children's front teeth photos taken with a flashlight, used by parents, "
        "teachers (UKS) and health cadres. Output must be phrased as 'indikasi / perlu diperiksa' with a referral CTA. "
        "Red flags come from the symptom questionnaire, never from this model.",
        "",
        "## Evaluation",
    ]
    if eval_summary:
        lines += ["Internal technical evaluation on a public-data split — **not** a clinical validation, not a claim:", "",
                  "```json", json.dumps(eval_summary, indent=1), "```"]
    else:
        lines.append("No evaluation report attached. **No performance claim of any kind is made.**")
    lines += ["", "## INT8 parity", json.dumps(parity, indent=1) if parity else "Not run.", "", "## Known limitations"]
    lines += [f"- {x}" for x in limitations] or ["- (none recorded)"]
    lines += [
        "",
        "## Licenses",
        f"- Code: {lic['code']['license']} (verified: {lic['code']['verified']}; {lic['code']['source']})",
        f"- Pretrained weights: {lic['weights']['license']} (verified: {lic['weights']['verified']}; basis: "
        f"`{lic['weights'].get('basis')}`; {lic['weights']['source']})",
        *[f"- Dataset `{d['id']}`: {d['license']} ({d.get('doi') or d.get('url')})" for d in lic["datasets"]],
        f"- This model: {lic['model_artifact']}",
        "",
        "## Attribution (show on the About screen, FR-122)",
        *[f"- [{a['lang']}] {a['text']}" for a in labels["attribution"]],
        "",
    ]
    return "\n".join(lines)


DEFAULT_LIMITATIONS = (
    "Training data from a single smartphone model, ages 10-24, Sindh (Pakistan); no Indonesian pediatric data.",
    "Boxes surround whole teeth with visible decay (Ahmed labels D/d); box size says nothing about severity and is "
    "used only for the UI overlay -- triage uses class, confidence and the number of findings (ADR 014).",
    "No cavitated vs non-cavitated distinction exists in the training data; early (non-cavitated) lesions are unreliable "
    "from smartphone photos (literature sensitivity 0-38%).",
    "Primary-dentition children under 10 are outside the training age range.",
    "Photos of front teeth only; interproximal and posterior surfaces are not assessed.",
    "Thresholds and quality-gate limits are PLACEHOLDER until set on validation data and reviewed by a dentist.",
)


# ---------------------------------------------------------------------------------- bundle


def write_sha256(bundle: Path, files: Sequence[str] = BUNDLE_FILES) -> str:
    text = "".join(f"{sha256_bytes((bundle / f).read_bytes())}  {f}\n" for f in files)
    (bundle / "SHA256").write_text(text, encoding="utf-8", newline="\n")
    return text


def verify_bundle(bundle: Path, *, max_size_mb: float = 15.0) -> list[str]:
    problems: list[str] = []
    for f in (*BUNDLE_FILES, "SHA256"):
        if not (bundle / f).is_file():
            problems.append(f"missing {f}")
    if problems:
        return problems
    for line in (bundle / "SHA256").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if name not in BUNDLE_FILES:
            problems.append(f"SHA256 lists unexpected file {name!r}")
        elif sha256_bytes((bundle / name).read_bytes()) != digest:
            problems.append(f"sha256 mismatch for {name}")
    model = (bundle / "model.tflite").read_bytes()
    if not is_tflite(model):
        problems.append("model.tflite is not a TFLite flatbuffer")
    try:
        check_size(len(model), max_size_mb)
    except ExportError as exc:
        problems.append(str(exc))
    labels = json.loads((bundle / "labels.json").read_text(encoding="utf-8"))
    problems += validate_labels(labels)
    if labels.get("model", {}).get("sha256") != sha256_bytes(model):
        problems.append("labels.json model.sha256 != sha256(model.tflite)")
    if not SEMVER.match(bundle.name) or labels.get("model", {}).get("version") != bundle.name:
        problems.append(f"bundle dir {bundle.name!r} must be the semver in labels.json")
    return problems


def release_blockers(cfg: Mapping[str, Any], decision: GateDecision, labels: Mapping[str, Any]) -> list[str]:
    b: list[str] = []
    if decision.internal_experiment_only or not decision.allowed:
        b.append("license gate not fully passed")
    if decision.license_basis == "owner_accepted_risk":
        b.append("weights licence basis is owner_accepted_risk (not verified): re-review required before commercial/public release (ADR 014 §5)")
    if labels["thresholds"]["status"] != "reviewed_by_drg":
        b.append("thresholds not reviewed by a dentist (PLACEHOLDER)")
    if not labels["output"]["tensors_verified"]:
        b.append("output tensors not verified with an interpreter")
    if not cfg["model"].get("output_format_verified", False):
        b.append("output format not verified (spike S1)")
    b.append("no clinical validation evidence (PRD §12, ADR 002); validation_status stays NOT_VALIDATED")
    return b


def convert_onnx(onnx_path: Path, work: Path, calib: Path | None) -> Path:  # pragma: no cover - optional deps
    try:
        import onnx2tf
    except ImportError as exc:
        raise ExportError("ONNX->TFLite needs optional deps: uv sync --extra export (Linux; onnx2tf + tensorflow)") from exc
    if calib is None:
        raise ExportError("INT8 conversion needs --calib (representative inputs .npy, from the TRAIN split only)")
    onnx2tf.convert(input_onnx_file_path=str(onnx_path), output_folder_path=str(work),
                    output_integer_quantized_tflite=True,
                    custom_input_op_name_np_data_path=[["images", str(calib), [[[[0.0, 0.0, 0.0]]]], [[[[1.0, 1.0, 1.0]]]]]])
    candidates = sorted(work.glob("*_full_integer_quant.tflite")) or sorted(work.glob("*_integer_quant.tflite"))
    if not candidates:
        raise ExportError(f"onnx2tf produced no integer-quantized model in {work}")
    return candidates[0]


def inspect_tflite(path: Path) -> dict[str, Any] | None:  # pragma: no cover - optional deps
    """Read real tensor details with a LiteRT interpreter; None if no interpreter is installed."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite import Interpreter
        except ImportError:
            return None
    it = Interpreter(model_path=str(path))
    it.allocate_tensors()

    def q(d: Mapping[str, Any]) -> dict[str, Any] | None:
        scale, zp = d["quantization"]
        return {"scale": float(scale), "zero_point": int(zp)} if scale else None

    inp = it.get_input_details()[0]
    outs = it.get_output_details()
    return {"input": {"shape": [int(v) for v in inp["shape"]], "dtype": str(inp["dtype"].__name__), "quantization": q(inp)},
            "outputs": [{"index": i, "name": o["name"], "shape": [int(v) for v in o["shape"]],
                         "dtype": str(o["dtype"].__name__), "quantization": q(o)} for i, o in enumerate(outs)]}


def build_bundle(
    cfg_path: Path,
    out: Path,
    *,
    tflite_in: Path | None = None,
    onnx_in: Path | None = None,
    calib: Path | None = None,
    anchors_path: Path | None = None,
    thresholds_path: Path | None = None,
    run_id: str | None = None,
    eval_report: Path | None = None,
    parity_report: Path | None = None,
    max_size_mb: float | None = None,
    allow_unverified_license: bool = False,
    release: bool = False,
    force: bool = False,
    gate_path: Path = DEFAULT_GATE_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg, _ = load_config(cfg_path)
    if not SEMVER.match(out.name):
        raise ExportError(f"--out must end in a semver directory (e.g. ml/exports/0.1.0), got {out.name!r}")
    if out.exists() and not force:
        raise ExportError(f"{out} exists; bundles are immutable (bump the version or pass --force for a dev build)")
    if not cfg["export"]["int8"]:
        raise ExportError("config export.int8 must be true for the shipped model")
    decision = evaluate_gate(load_gate(gate_path), candidate=cfg["candidate"], datasets=cfg["data"]["datasets"],
                             cfg_license_verified=cfg["license_verified"], allow_unverified=allow_unverified_license and not release,
                             cfg_license_note=cfg.get("license_note"))
    if not decision.allowed:
        raise ExportError("license gate refused: " + "; ".join(decision.reasons))
    for w in decision.warnings:  # owner_accepted_risk is never silent (ADR 014 §5)
        print(f"WARNING{' (RELEASE)' if release else ''}: {w}", file=sys.stderr)
    limit = float(max_size_mb if max_size_mb is not None else cfg["export"]["max_size_mb"])
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        if tflite_in is not None:
            src = tflite_in
        elif onnx_in is not None:
            src = convert_onnx(onnx_in, tmp / "convert", calib)
        else:
            raise ExportError("give --tflite-in (already converted) or --onnx-in")
        model = src.read_bytes()
        if not is_tflite(model):
            raise ExportError(f"{src} is not a TFLite flatbuffer (missing 'TFL3' identifier)")
        check_size(len(model), limit)
        details = inspect_tflite(src)
        tensors = None
        input_q = None
        if details is not None:
            roles = [t["role"] for t in cfg["model"]["output_tensors"]]
            if len(roles) != len(details["outputs"]):
                raise ExportError(f"model has {len(details['outputs'])} outputs, config expects {len(roles)}")
            tensors = [{**o, "role": r} for o, r in zip(details["outputs"], roles, strict=True)]
            input_q = details["input"]["quantization"]
        anchors = json.loads(anchors_path.read_text(encoding="utf-8")) if anchors_path else None
        thresholds = json.loads(thresholds_path.read_text(encoding="utf-8")) if thresholds_path else None
        eval_sha = sha256_bytes(eval_report.read_bytes()) if eval_report else None
        eval_summary = None
        if eval_report:
            rep = json.loads(eval_report.read_text(encoding="utf-8"))
            eval_summary = {"split": rep["inputs"]["split"], "split_sha256": rep["inputs"]["split_sha256"],
                            "map50": rep["overall"]["map50"],
                            **{f"{c}_image_level": {k: pc[c]["image_level"].get(k) for k in ("sensitivity", "specificity")}
                               for c in (*CLASS_NAMES, "any_caries") if c in (pc := rep["overall"]["per_class"])},
                            "ap50": {c: rep["overall"]["per_class"].get(c, {}).get("ap50") for c in CLASS_NAMES}}
        parity = json.loads(parity_report.read_text(encoding="utf-8")) if parity_report else None
        distribution = "release" if release else ("internal_experiment_only" if decision.internal_experiment_only else "pilot")
        labels = build_labels(
            cfg, decision, version=out.name, model_sha256=sha256_bytes(model), size_bytes=len(model),
            created_at=(now or datetime.now(UTC)).isoformat(), anchors=anchors, tensors=tensors,
            tensors_verified=details is not None, input_quantization=input_q, thresholds=thresholds,
            distribution=distribution, run_id=run_id, eval_report_sha256=eval_sha, parity=parity,
        )
        if release:
            blockers = release_blockers(cfg, decision, labels)
            if blockers:
                raise ExportError("release blocked: " + "; ".join(blockers))
        problems = validate_labels(labels)
        if problems:
            raise ExportError("labels.json invalid: " + "; ".join(problems))
        stage = tmp / out.name
        stage.mkdir()
        (stage / "model.tflite").write_bytes(model)
        (stage / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (stage / "MODEL_CARD.md").write_text(render_model_card(labels, eval_summary=eval_summary, limitations=DEFAULT_LIMITATIONS),
                                             encoding="utf-8")
        write_sha256(stage)
        problems = verify_bundle(stage, max_size_mb=limit)
        if problems:
            raise ExportError("bundle verification failed: " + "; ".join(problems))
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, out)
    return labels


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify-bundle":
        v = argparse.ArgumentParser(prog="export.py verify-bundle")
        v.add_argument("bundle", type=Path)
        v.add_argument("--max-size-mb", type=float, default=15.0)
        va = v.parse_args(argv[1:])
        problems = verify_bundle(va.bundle, max_size_mb=va.max_size_mb)
        print(json.dumps({"ok": not problems, "problems": problems}, indent=1))
        return 0 if not problems else 1
    b = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    b.add_argument("--cfg", type=Path, required=True)
    b.add_argument("--format", choices=["tflite"], default="tflite")
    b.add_argument("--int8", action="store_true", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--tflite-in", type=Path)
    b.add_argument("--onnx-in", type=Path)
    b.add_argument("--calib", type=Path)
    b.add_argument("--anchors", type=Path)
    b.add_argument("--thresholds", type=Path)
    b.add_argument("--run-id")
    b.add_argument("--eval-report", type=Path)
    b.add_argument("--parity-report", type=Path)
    b.add_argument("--max-size-mb", type=float)
    b.add_argument("--allow-unverified-license", action="store_true")
    b.add_argument("--release", action="store_true")
    b.add_argument("--force", action="store_true")
    args = b.parse_args(argv)
    try:
        labels = build_bundle(
            args.cfg, args.out, tflite_in=args.tflite_in, onnx_in=args.onnx_in, calib=args.calib,
            anchors_path=args.anchors, thresholds_path=args.thresholds, run_id=args.run_id,
            eval_report=args.eval_report, parity_report=args.parity_report, max_size_mb=args.max_size_mb,
            allow_unverified_license=args.allow_unverified_license, release=args.release, force=args.force,
        )
    except (ExportError, ConfigError) as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"bundle": str(args.out), "distribution": labels["distribution"],
                      "thresholds": labels["thresholds"]["status"], "validation_status": labels["validation_status"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
