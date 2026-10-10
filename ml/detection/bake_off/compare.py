"""Merge eval.py reports (+ hand-measured spike data) into the ADR-ready bake-off table.

    python bake_off/compare.py --report yolox_nano=out/yolox/report.json \
        --report mediapipe_modelmaker_mobilenetv2_ssd=out/mm/report.json \
        --meta bake_off/measurements.yaml --out bake_off/report.md

``--meta`` YAML, per candidate (all optional; missing -> "belum diukur"):

    yolox_nano:
      int8_size_mb: 3.9
      latency_ms: {ram_3gb: 820, ram_4gb: 610}     # spike S1, median of >= 30 runs, CPU/XNNPACK
      devices: {ram_3gb: "<model>", ram_4gb: "<model>"}
      export_difficulty: medium                    # low | medium | high
      export_notes: "onnx2tf needed decoder outside the graph"
      weight_license_clarity: unclear              # clear | unclear | unknown (default: WEIGHT_LICENSES.md verdict)
      parity_passed: true                          # from check_int8_parity.py (notebook measurements_partial.json)

``--meta`` may be repeated (YAML or JSON files; later files override earlier ones per candidate).

All reports must share the same split file hash and GT hash (same data & split for all candidates);
otherwise the comparison is refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ADR_TEMPLATE = HERE / "ADR-013-template.md"
SIZE_GATE_MB = 15.0
LATENCY_GATE_MS = 1500.0
DEVICES = ("ram_3gb", "ram_4gb")


class CompareError(ValueError):
    pass


def fmt_ratio(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def fmt_ci(ci: Mapping[str, Any] | None) -> str:
    if not ci or ci.get("lo") is None or ci.get("hi") is None:
        return ""
    return f" ({ci['lo']:.2f}–{ci['hi']:.2f})"


def metric_cell(block: Mapping[str, Any], name: str) -> str:
    ci = (block.get("ci95") or {}).get(name)
    return fmt_ratio(block.get(name)) + fmt_ci(ci)


def check_same_data(reports: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, str | None, str | None]:
    keys = {(r["inputs"].get("split_sha256"), r["inputs"].get("gt_sha256"), r["inputs"].get("split")) for r in reports.values()}
    if len(keys) != 1:
        raise CompareError(f"reports use different data/splits: {sorted(map(str, keys))}")
    split_sha, gt_sha, split = next(iter(keys))
    if split_sha is None:
        raise CompareError("reports have no split_sha256 (evaluate with --split and --splits)")
    return split_sha, gt_sha, split


def gate_cell(meta: Mapping[str, Any]) -> str:
    size = meta.get("int8_size_mb")
    lat = meta.get("latency_ms") or {}
    if size is None or any(lat.get(d) is None for d in DEVICES):
        return "belum diukur"
    ok = size <= SIZE_GATE_MB and all(float(lat[d]) <= LATENCY_GATE_MS for d in DEVICES)
    return "lolos" if ok else "gagal"


def build_rows(reports: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Mapping[str, Any]]) -> list[list[str]]:
    rows = []
    for cand in sorted(reports):
        ov = reports[cand]["overall"]
        pc = ov["per_class"]
        perm, prim, anyc = pc["caries_permanent"], pc["caries_primary"], pc["any_caries"]
        m = meta.get(cand, {})
        lat = m.get("latency_ms") or {}
        rows.append([
            f"`{cand}`",
            metric_cell(anyc["image_level"], "sensitivity"),
            metric_cell(anyc["image_level"], "specificity"),
            metric_cell(perm["image_level"], "sensitivity"),
            metric_cell(perm["image_level"], "specificity"),
            metric_cell(prim["image_level"], "sensitivity"),
            metric_cell(prim["image_level"], "specificity"),
            fmt_ratio(perm.get("ap50")),
            fmt_ratio(prim.get("ap50")),
            fmt_ratio(ov.get("map50")) + fmt_ci(ov.get("map50_ci95")),
            "belum diukur" if m.get("int8_size_mb") is None else f"{float(m['int8_size_mb']):.1f}",
            *("belum diukur" if lat.get(d) is None else f"{float(lat[d]):.0f}" for d in DEVICES),
            str(m.get("export_difficulty", "belum dinilai")),
            parity_cell(m),
            str(m.get("weight_license_clarity", LICENSES.get(cand, {}).get("clarity", "unknown"))),
            gate_cell(m),
        ])
    return rows


HEADER = [
    "Kandidat", "Sens karies apa pun (gambar, CI95)", "Spes karies apa pun (gambar, CI95)",
    "Sens gigi tetap (gambar, CI95)", "Spes gigi tetap (gambar, CI95)", "Sens gigi sulung (gambar, CI95)", "Spes gigi sulung (gambar, CI95)",
    "AP50 tetap", "AP50 sulung", "mAP50 (CI95)", "INT8 MB (≤15)", "Latensi HP 3 GB (ms)", "Latensi HP 4 GB (ms, ≤1500)",
    "Kemudahan ekspor", "Paritas INT8", "Kejelasan lisensi bobot", "Gerbang ukuran+latensi",
]

#: Verdicts from WEIGHT_LICENSES.md (read 2026-10-09). Keep in sync with that file (test checks the candidate ids).
LICENSES: dict[str, dict[str, str]] = {
    "yolox_nano": {"code": "Apache-2.0", "weights": "tidak eksplisit (aset rilis repo Apache-2.0; YOLOX#1865 belum dijawab)",
                   "clarity": "owner_accepted_risk",
                   "verdict": "risiko diterima pemilik untuk kompetisi (ADR 014 §5) — BUKAN terverifikasi; tinjau ulang sebelum rilis komersial/ publik"},
    "mediapipe_modelmaker_mobilenetv2_ssd": {"code": "Apache-2.0", "weights": "Apache-2.0 (TF Model Garden QAT, MOBILENET_V2 i256)",
                                             "clarity": "clear", "verdict": "boleh dipakai (sisa risiko: gambar COCO)"},
    "efficientdet_lite0": {"code": "Apache-2.0", "weights": "Apache-2.0 (Kaggle Models tensorflow/efficientdet)",
                           "clarity": "clear", "verdict": "lisensi OK; toolchain usang (cadangan)"},
    "rfdetr_nano": {"code": "Apache-2.0", "weights": "Apache-2.0 (README RF-DETR-N)", "clarity": "clear",
                    "verdict": "gugur: 30,5 jt parameter > gerbang 15 MB"},
}


def parity_cell(meta: Mapping[str, Any]) -> str:
    v = meta.get("parity_passed")
    return "belum diukur" if v is None else ("lulus" if v else "gagal")


def render_markdown(reports: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Mapping[str, Any]]) -> str:
    split_sha, gt_sha, split = check_same_data(reports)
    thr_status = {r["settings"]["score_threshold_status"] for r in reports.values()}
    units = {r["counts"]["bootstrap_unit"] for r in reports.values()}
    lines = [
        "# Hasil bake-off detektor (draf untuk ADR)",
        "",
        "> Evaluasi teknis pada data publik — **bukan validasi klinis, bukan klaim akurasi** (PRD §12).",
        "",
        f"- Split: `{split}` · splits.json sha256 `{split_sha}` · GT sha256 `{gt_sha}`",
        f"- Status ambang skor: {', '.join(sorted(thr_status))} · Unit bootstrap: {', '.join(sorted(units))}",
        "",
        "| " + " | ".join(HEADER) + " |",
        "|" + "---|" * len(HEADER),
    ]
    lines += ["| " + " | ".join(r) + " |" for r in build_rows(reports, meta)]
    notes = [f"- `{c}`: {meta[c]['export_notes']}" for c in sorted(meta) if c in reports and meta[c].get("export_notes")]
    if notes:
        lines += ["", "Catatan ekspor:", *notes]
    warn = [f"- `{c}`: {w}" for c in sorted(reports) for w in reports[c].get("warnings", [])]
    if warn:
        lines += ["", "Peringatan dari laporan evaluasi:", *warn]
    return "\n".join(lines) + "\n"


def parse_report_args(items: Sequence[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for it in items:
        name, sep, path = it.partition("=")
        if not sep or not name or not path:
            raise CompareError(f"--report expects candidate=path, got {it!r}")
        if name in out:
            raise CompareError(f"duplicate candidate {name!r}")
        out[name] = Path(path)
    return out


CANDIDATES = ("mediapipe_modelmaker_mobilenetv2_ssd", "yolox_nano", "efficientdet_lite0", "rfdetr_nano")


def render_template() -> str:
    """ADR-ready report skeleton (``--template``); the table header is shared with render_markdown."""
    empty = "| " + " | ".join([""] * (len(HEADER) - 1)) + " |"
    return "\n".join([
        "# Laporan bake-off detektor — TEMPLATE (siap ADR)",
        "",
        "> Evaluasi teknis pada data publik — **bukan validasi klinis, bukan klaim akurasi** (PRD §12, ADR 002).",
        "> Tabel diisi otomatis oleh `bake_off/compare.py`; bagian lain diisi tangan.",
        "",
        "- Tanggal: `YYYY-MM-DD` · Penyusun: `…`",
        "- Dataset: `ahmed2025` (CC BY 4.0) · `canonical.jsonl` sha256 `…`",
        "- Split: `test` · `splits.json` sha256 `…` · `group_quality`: `patient | patient_derived | heuristic_session | none`",
        "- Seed: `20261008` · Unit bootstrap: `patient` · B = 1000 · Ambang skor dipilih pada `val` (`set_on_val`)",
        "",
        "## Tabel utama",
        "",
        "| " + " | ".join(HEADER) + " |",
        "|" + "---|" * len(HEADER),
        *[f"| `{c}` {empty}" for c in CANDIDATES],
        "",
        "## Subgrup (`report.json → subgroups`)",
        "| Kandidat | Subgrup | n gambar / n pasien | Sens karies apa pun (CI95) | Spes karies apa pun (CI95) |",
        "|---|---|---|---|---|",
        "| | lighting = flashlight | | | |",
        "| | phone_model = … | | | |",
        "",
        "## Paritas INT8 (`check_int8_parity.py`)",
        "| Kandidat | n sampel | cosine min | kecocokan deteksi | lulus? |",
        "|---|---|---|---|---|",
        "",
        "## Perangkat uji (spike S1)",
        "| Slot | Model HP | RAM | SoC | Android | Runtime/delegate |",
        "|---|---|---|---|---|---|",
        "| ram_3gb | | 3 GB | | | LiteRT CPU/XNNPACK |",
        "| ram_4gb | | 4 GB | | | LiteRT CPU/XNNPACK |",
        "",
        "## Keputusan yang diusulkan",
        "- Model terpilih: `…` · alasan: …",
        "- Risiko tersisa (lisensi bobot, kesetaraan INT8, generalisasi perangkat): …",
        "- Keterbatasan wajib di MODEL_CARD: satu perangkat, usia 10–24 th (Pakistan), tanpa data anak Indonesia, hanya kotak, lesi awal tidak andal.",
        "",
    ])


def render_licenses(candidates: Sequence[str]) -> str:
    lines = ["| Kandidat | Kode | Bobot pra-latih | Putusan |", "|---|---|---|---|"]
    for c in candidates:
        lic = LICENSES.get(c, {})
        lines.append(f"| `{c}` | {lic.get('code', '?')} | {lic.get('weights', '?')} | {lic.get('verdict', 'belum diverifikasi')} |")
    return "\n".join(lines)


def render_adr(reports: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Mapping[str, Any]], template: str) -> str:
    """Fill the ADR template markers. Every marker must be present (the template and this code stay in sync)."""
    split_sha, gt_sha, split = check_same_data(reports)
    md = render_markdown(reports, meta)
    table = md[md.index("| " + HEADER[0]):].split("\n\n", 1)
    inputs = "\n".join([
        f"- Split evaluasi: `{split}` · `splits.json` sha256 `{split_sha}` · GT (`canonical.jsonl`) sha256 `{gt_sha}`",
        f"- Status ambang: {', '.join(sorted({r['settings']['score_threshold_status'] for r in reports.values()}))} · "
        f"unit bootstrap: {', '.join(sorted({r['counts']['bootstrap_unit'] for r in reports.values()}))}",
        "- Kandidat dievaluasi: " + ", ".join(f"`{c}`" for c in sorted(reports)),
    ])
    fills = {"inputs": inputs, "table": table[0].rstrip(), "licenses": render_licenses(sorted(reports)),
             "notes": table[1].strip() if len(table) > 1 else ""}
    out = template
    for key, value in fills.items():
        marker = f"<!-- compare:{key} -->"
        if marker not in out:
            raise CompareError(f"ADR template lacks marker {marker}")
        out = out.replace(marker, value)
    return out


def load_meta(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for cand, values in data.items():
            merged.setdefault(str(cand), {}).update(values or {})
    return merged


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="append")
    p.add_argument("--meta", type=Path, action="append", default=[])
    p.add_argument("--out", type=Path)
    p.add_argument("--template", action="store_true", help="print/write the empty ADR report template")
    p.add_argument("--adr", action="store_true", help="fill the ADR template (bake_off/ADR-013-template.md) instead of the bare table")
    p.add_argument("--adr-template", type=Path, default=ADR_TEMPLATE)
    args = p.parse_args(argv)
    if args.template:
        text = render_template()
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(text)
        return 0
    if not args.report:
        print("error: --report is required (or use --template)", file=sys.stderr)
        return 2
    try:
        paths = parse_report_args(args.report)
        reports = {k: json.loads(v.read_text(encoding="utf-8")) for k, v in paths.items()}
        meta = load_meta(args.meta)
        md = render_adr(reports, meta, args.adr_template.read_text(encoding="utf-8")) if args.adr else render_markdown(reports, meta)
    except (CompareError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
