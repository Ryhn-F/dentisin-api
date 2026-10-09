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
      weight_license_clarity: unclear              # clear | unclear | unknown

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
        cav = ov["per_class"]["caries_cavitated"]
        non = ov["per_class"]["caries_noncavitated"]
        m = meta.get(cand, {})
        lat = m.get("latency_ms") or {}
        rows.append([
            f"`{cand}`",
            metric_cell(cav["image_level"], "sensitivity"),
            metric_cell(cav["image_level"], "specificity"),
            metric_cell(cav["box_level"], "sensitivity"),
            metric_cell(non["image_level"], "sensitivity"),
            fmt_ratio(ov.get("map50")) + fmt_ci(ov.get("map50_ci95")),
            "belum diukur" if m.get("int8_size_mb") is None else f"{float(m['int8_size_mb']):.1f}",
            *("belum diukur" if lat.get(d) is None else f"{float(lat[d]):.0f}" for d in DEVICES),
            str(m.get("export_difficulty", "belum dinilai")),
            str(m.get("weight_license_clarity", "unknown")),
            gate_cell(m),
        ])
    return rows


HEADER = [
    "Kandidat", "Sens kavitas (gambar, CI95)", "Spes kavitas (gambar, CI95)", "Sens kavitas (box IoU≥0,5)",
    "Sens non-kavitas (gambar)", "mAP50 (CI95)", "INT8 MB (≤15)", "Latensi HP 3 GB (ms)", "Latensi HP 4 GB (ms, ≤1500)",
    "Kemudahan ekspor", "Kejelasan lisensi bobot", "Gerbang ukuran+latensi",
]


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
        "| Kandidat | Subgrup | n gambar / n pasien | Sens kavitas (CI95) | Spes kavitas (CI95) |",
        "|---|---|---|---|---|",
        "| | dentition = primary | | | |",
        "| | dentition = permanent | | | |",
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="append")
    p.add_argument("--meta", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--template", action="store_true", help="print/write the empty ADR report template")
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
        meta = (yaml.safe_load(args.meta.read_text(encoding="utf-8")) or {}) if args.meta else {}
        md = render_markdown(reports, meta)
    except (CompareError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
