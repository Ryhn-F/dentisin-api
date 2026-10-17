// Parity test: decode.ts against the Python reference vectors (ml/detection/tests/decode_vectors.json).
// Run from backend/:  node --test docs/api/examples/detector/
// In the app repo point DECODE_VECTORS at the copy shipped in the contracts bundle.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  canvasToTensor,
  dequantize,
  detect,
  decodeYoloxRaw,
  letterboxLayout,
  letterboxParams,
  postprocess,
  unletterbox,
  type Box,
  type DetectorLabels,
} from "./decode.ts";

const path = process.env.DECODE_VECTORS ?? new URL("../../../../ml/detection/tests/decode_vectors.json", import.meta.url);
const V = JSON.parse(readFileSync(path, "utf-8"));
const TOL: number = V.tolerance.abs;

function close(actual: number[], expected: number[], what: string, tol: number = TOL) {
  assert.equal(actual.length, expected.length, `${what}: length`);
  actual.forEach((a, i) => assert.ok(Math.abs(a - expected[i]!) <= tol, `${what}[${i}]: ${a} vs ${expected[i]}`));
}

function closeBoxes(actual: Box[], expected: number[][], what: string) {
  assert.equal(actual.length, expected.length, `${what}: count`);
  actual.forEach((b, i) => close(b, expected[i]!, `${what}[${i}]`));
}

test("spec version this port was written for", () => {
  assert.equal(V.spec_version, "1.0.0");
});

test("dequantize", () => {
  const d = V.quantization.dequantize;
  close(dequantize(d.q, d.scale, d.zero_point), d.expected, "dequantize");
});

test("letterbox geometry (top_left and center)", () => {
  for (const g of V.geometry) {
    const lb = letterboxParams(g.src[0], g.src[1], g.dst[0], g.dst[1], g.align);
    close([lb.scale, lb.padX, lb.padY], [g.scale, g.pad_x, g.pad_y], `params ${g.align}`);
    close(unletterbox(g.box_in, lb, g.src[0], g.src[1]), g.box_out, `unletterbox ${g.align}`);
  }
});

test("letterbox layout of a 4:3 phone photo into 416x416", () => {
  const l = letterboxLayout(4000, 3000, 416, 416);
  assert.deepEqual([l.newW, l.newH, l.offsetX, l.offsetY], [416, 312, 0, 0]);
  assert.equal(l.scale, 0.104);
});

test("yolox_raw decode matches the Python reference, then post-process (per class and agnostic)", () => {
  const v = V.vectors.find((x: { decode: { type: string } }) => x.decode.type === "yolox_raw");
  const raw: number[] = v.outputs.raw.flat();
  const [w, h] = v.input_size;
  const d = decodeYoloxRaw(raw, 2, w, h, v.decode.strides, v.decode.sigmoid_applied);
  closeBoxes(d.boxes, v.expected_decoded.boxes, "decoded boxes");
  assert.deepEqual(d.classes, v.expected_decoded.classes);
  close(d.scores, v.expected_decoded.scores, "decoded scores");

  const p = V.postprocess;
  for (const [key, agnostic] of [["expected_post", false], ["expected_post_agnostic", true]] as const) {
    const out = postprocess(d, p.score_thresholds, p.nms_iou, p.max_detections, agnostic);
    closeBoxes(out.boxes, v[key].boxes, `${key} boxes`);
    assert.deepEqual(out.classes, v[key].classes, `${key} classes`);
    close(out.scores, v[key].scores, `${key} scores`);
  }
});

test("logits path: feeding logits with sigmoid_applied=false equals feeding probabilities with sigmoid_applied=true", () => {
  const v = V.vectors.find((x: { decode: { type: string } }) => x.decode.type === "yolox_raw");
  const raw: number[][] = v.outputs.raw;
  const logit = (p: number) => Math.log(Math.min(Math.max(p, 1e-12), 1 - 1e-12) / (1 - Math.min(Math.max(p, 1e-12), 1 - 1e-12)));
  const asLogits = raw.map((r) => [r[0]!, r[1]!, r[2]!, r[3]!, ...r.slice(4).map(logit)]).flat();
  const [w, h] = v.input_size;
  const a = decodeYoloxRaw(raw.flat(), 2, w, h, v.decode.strides, true);
  const b = decodeYoloxRaw(asLogits, 2, w, h, v.decode.strides, false);
  // rows whose obj/cls are exactly 0 or 1 hit the clamp above; every other row must agree
  const same = a.scores.every((s, i) => Math.abs(s - b.scores[i]!) < 1e-6 || a.scores[i] === 0);
  assert.ok(same);
});

test("wrong tensor size is rejected", () => {
  assert.throws(() => decodeYoloxRaw(new Float32Array(10), 2, 416, 416, [8, 16, 32], false), /expected 3549 rows/);
});

test("detect(): the whole chain with a labels.json excerpt of the ADR 013 bundle", () => {
  const labels: DetectorLabels = {
    classes: [{ id: 0, name: "caries_permanent" }, { id: 1, name: "caries_primary" }],
    input: { width: 416, height: 416, resize: { method: "letterbox", align: "top_left", pad_value: 114 } },
    output: {
      decode: { type: "yolox_raw", strides: [8, 16, 32], sigmoid_applied: false },
      postprocess: { nms_iou_threshold: 0.45, class_agnostic_nms: false, max_detections: 20 },
    },
    thresholds: { score: { caries_permanent: 0.5914, caries_primary: 0.0096 }, status: "set_on_val" },
  };
  const rows = 52 * 52 + 26 * 26 + 13 * 13; // 3549
  const raw = new Float32Array(rows * 7);
  for (let i = 0; i < rows; i++) {
    raw.set([0, 0, 0, 0, -9, -9, -9], i * 7); // empty cell: everything near 0
  }
  // one strong primary-tooth finding at stride 8, cell (gx=10, gy=20): centre (80..88, 160..168) -> box 8x8 px at the cell
  const strong = 20 * 52 + 10;
  raw.set([0, 0, Math.log(3), Math.log(3), 4, -4, 4], strong * 7); // w = h = 3 * 8 = 24 px
  const out = detect(labels, raw, 4000, 3000);
  assert.equal(out.length, 1);
  assert.equal(out[0]!.classId, 1);
  const s = 1 / (1 + Math.exp(-4));
  assert.ok(Math.abs(out[0]!.score - s * s) < 1e-6);
  const r = 416 / 4000; // letterbox scale; box in input px: x1 = 80 - 12 = 68, y1 = 160 - 12 = 148, x2 = 92, y2 = 172
  close(out[0]!.box, [68 / r, 148 / r, 92 / r, 172 / r], "photo box", 1e-3); // Float32 tensor: ~1e-6 relative
  // placeholder thresholds are refused unless the build is internal
  const unset: DetectorLabels = { ...labels, thresholds: { score: { caries_permanent: null, caries_primary: null }, status: "PLACEHOLDER" } };
  assert.throws(() => detect(unset, raw, 4000, 3000), /thresholds are not set/);
  assert.doesNotThrow(() => detect(unset, raw, 4000, 3000, { allowPlaceholderThresholds: true }));
  assert.throws(() => detect({ ...labels, output: { ...labels.output, decode: { type: "ssd_raw_anchors", strides: [], sigmoid_applied: false } } }, raw, 4000, 3000), /unsupported decoder/);
});

test("golden fixture: the REAL model's raw output decodes to the stored detections", () => {
  const gpath = process.env.GOLDEN_RUNTIME ?? new URL("./golden/golden_runtime.json", import.meta.url);
  const G = JSON.parse(readFileSync(gpath, "utf-8"));
  const buf = Buffer.from(G.output.raw_float32_le_base64, "base64");
  const raw = new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4); // little-endian host (ARM64/x86)
  assert.deepEqual(G.output.shape, [1, 3549, 7]);
  assert.equal(raw.length, 3549 * 7);
  const spec = G.decode;
  const tol: number = G.tolerance.decode_from_fixture_raw_abs;
  const [inW, inH] = spec.input_size;
  const [photoW, photoH] = spec.photo_size;
  const decoded = decodeYoloxRaw(raw, spec.class_names.length, inW, inH, spec.strides, spec.sigmoid_applied);
  const lb = letterboxParams(photoW, photoH, inW, inH, spec.resize_align);
  for (const c of G.cases) {
    const thr = spec.class_names.map((n: string) => c.thresholds[n]);
    const kept = postprocess(decoded, thr, spec.nms_iou_threshold, spec.max_detections, spec.class_agnostic_nms);
    assert.equal(kept.scores.length, c.expected.length, `${c.id}: detection count`);
    kept.scores.forEach((score, i) => {
      const e = c.expected[i];
      assert.equal(kept.classes[i], e.class_id, `${c.id}[${i}] class`);
      assert.ok(Math.abs(score - e.score) <= tol, `${c.id}[${i}] score ${score} vs ${e.score}`);
      close(unletterbox(kept.boxes[i]!, lb, photoW, photoH), e.box_photo_px, `${c.id}[${i}] box`, tol);
    });
  }
});

test("canvasToTensor: BGR, 0..255, mean 0 / std 1 just swaps R and B", () => {
  const rgb = [10, 20, 30, 40, 50, 60];
  const t = canvasToTensor(rgb, { colorOrder: "BGR", pixelRange: [0, 255], mean: [0, 0, 0], std: [1, 1, 1] });
  assert.deepEqual(Array.from(t), [30, 20, 10, 60, 50, 40]);
  const n = canvasToTensor(rgb, { colorOrder: "RGB", pixelRange: [-1, 1], mean: [0.5, 0, 0], std: [0.5, 1, 1] });
  assert.ok(Math.abs(n[0]! - ((-1 + (10 * 2) / 255 - 0.5) / 0.5)) < 1e-6);
});
