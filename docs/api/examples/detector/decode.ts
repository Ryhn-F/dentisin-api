// Reference TypeScript port of the detector DECODE procedure for the YOLOX-Nano bundle (ADR 013, labels.json schema 2.0.0).
//
// Source of truth: ml/detection/DECODE_SPEC.md (v1.0.0) and the Python reference ml/detection/decode.py.
// Parity is checked by decode.test.ts against ml/detection/tests/decode_vectors.json (absolute tolerance 1e-6):
//
//   node --test docs/api/examples/detector/          (Node >= 22.18 runs .ts directly; erasable TypeScript syntax only)
//
// Only the `yolox_raw` family is ported (the chosen model). SSD / DETR variants stay in DECODE_SPEC for the fallback candidate.
// Copy this file into the app and keep the test next to it; do not change the maths without changing the spec.

export type Box = [number, number, number, number]; // x1, y1, x2, y2

export type Detection = { box: Box; classId: number; score: number };

export type Letterbox = { scale: number; padX: number; padY: number };

export type Decoded = { boxes: Box[]; classes: number[]; scores: number[] };

/** The parts of labels.json this decoder reads. */
export type DetectorLabels = {
  classes: { id: number; name: string }[];
  input: { width: number; height: number; resize: { method: string; align: string; pad_value: number } };
  output: {
    decode: { type: string; strides: number[]; sigmoid_applied: boolean };
    postprocess: { nms_iou_threshold: number; class_agnostic_nms: boolean; max_detections: number };
  };
  thresholds: { score: Record<string, number | null>; status: string };
};

export function sigmoid(z: number): number {
  return 1 / (1 + Math.exp(-z));
}

/** TFLite affine quantisation: real = scale * (q - zeroPoint). Not needed for the float32 bundle; kept for INT8 variants. */
export function dequantize(q: ArrayLike<number>, scale: number, zeroPoint: number): number[] {
  return Array.from(q, (v) => scale * (v - zeroPoint));
}

// ---------------------------------------------------------------------------------------------- geometry

/** Uniform scale r = min(dstW/srcW, dstH/srcH); the photo sits top-left (YOLOX) or centred. */
export function letterboxParams(srcW: number, srcH: number, dstW: number, dstH: number, align: string = "top_left"): Letterbox {
  const scale = Math.min(dstW / srcW, dstH / srcH);
  if (align === "top_left") return { scale, padX: 0, padY: 0 };
  if (align === "center") return { scale, padX: (dstW - srcW * scale) / 2, padY: (dstH - srcH * scale) / 2 };
  throw new Error(`unknown align ${align}`);
}

/** Size of the resized photo inside the canvas (floor, at least 1) and where it is placed. */
export function letterboxLayout(srcW: number, srcH: number, dstW: number, dstH: number, align: string = "top_left") {
  const lb = letterboxParams(srcW, srcH, dstW, dstH, align);
  return {
    ...lb,
    newW: Math.max(1, Math.floor(srcW * lb.scale)),
    newH: Math.max(1, Math.floor(srcH * lb.scale)),
    offsetX: Math.floor(lb.padX),
    offsetY: Math.floor(lb.padY),
  };
}

/** Model-input pixels -> photo pixels, clamped to the photo. */
export function unletterbox(box: Box, lb: Letterbox, srcW: number, srcH: number): Box {
  const cx = (v: number) => Math.min(Math.max((v - lb.padX) / lb.scale, 0), srcW);
  const cy = (v: number) => Math.min(Math.max((v - lb.padY) / lb.scale, 0), srcH);
  return [cx(box[0]), cy(box[1]), cx(box[2]), cy(box[3])];
}

/**
 * Letterboxed RGB canvas (uint8, H*W*3, pad already filled with `pad_value`) -> model input tensor (Float32, NHWC, batch 1).
 * labels.json for the YOLOX bundle: BGR order, pixel range 0..255, mean 0, std 1 -> the tensor is just the canvas with R and B swapped.
 */
export function canvasToTensor(
  rgb: ArrayLike<number>,
  opts: { colorOrder: "RGB" | "BGR"; pixelRange: [number, number]; mean: number[]; std: number[] },
): Float32Array {
  const n = rgb.length / 3;
  const out = new Float32Array(rgb.length);
  const [lo, hi] = opts.pixelRange;
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 3; c++) {
      const src = opts.colorOrder === "BGR" ? 2 - c : c; // output channel c takes this input channel
      const v = lo + (rgb[i * 3 + src]! * (hi - lo)) / 255;
      out[i * 3 + c] = (v - opts.mean[c]!) / opts.std[c]!;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- decoding

/** Grid cell (gx, gy) and stride of every output row, in YOLOX order: stride-major, then y, then x. */
export function yoloxGrid(inputW: number, inputH: number, strides: number[]) {
  const gx: number[] = [];
  const gy: number[] = [];
  const st: number[] = [];
  for (const s of strides) {
    const hs = Math.floor(inputH / s);
    const ws = Math.floor(inputW / s);
    for (let y = 0; y < hs; y++) {
      for (let x = 0; x < ws; x++) {
        gx.push(x);
        gy.push(y);
        st.push(s);
      }
    }
  }
  return { gx, gy, st };
}

/**
 * Raw YOLOX head output `[1, N, 5 + C]` (flattened) -> boxes in MODEL-INPUT pixels. Row = [tx, ty, tw, th, obj, cls_0 .. cls_C-1].
 * The ADR 013 bundle emits LOGITS (`sigmoid_applied: false`): sigmoid is applied here to obj and cls.
 * class = argmax over cls (first maximum wins), score = obj * cls[class].
 */
export function decodeYoloxRaw(
  raw: ArrayLike<number>,
  numClasses: number,
  inputW: number,
  inputH: number,
  strides: number[],
  sigmoidApplied: boolean,
): Decoded {
  const width = 5 + numClasses;
  const { gx, gy, st } = yoloxGrid(inputW, inputH, strides);
  if (raw.length !== gx.length * width) {
    throw new Error(`expected ${gx.length} rows of ${width} values for ${inputW}x${inputH}, got ${raw.length / width}`);
  }
  const boxes: Box[] = [];
  const classes: number[] = [];
  const scores: number[] = [];
  for (let i = 0; i < gx.length; i++) {
    const o = i * width;
    const act = (v: number) => (sigmoidApplied ? v : sigmoid(v));
    const obj = act(raw[o + 4]!);
    let best = 0;
    let bestV = act(raw[o + 5]!);
    for (let c = 1; c < numClasses; c++) {
      const v = act(raw[o + 5 + c]!);
      if (v > bestV) {
        best = c;
        bestV = v;
      }
    }
    const s = st[i]!;
    const cx = (raw[o]! + gx[i]!) * s;
    const cy = (raw[o + 1]! + gy[i]!) * s;
    const w = Math.exp(raw[o + 2]!) * s;
    const h = Math.exp(raw[o + 3]!) * s;
    boxes.push([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]);
    classes.push(best);
    scores.push(obj * bestV);
  }
  return { boxes, classes, scores };
}

function iou(a: Box, b: Box): number {
  const ix = Math.max(Math.min(a[2], b[2]) - Math.max(a[0], b[0]), 0);
  const iy = Math.max(Math.min(a[3], b[3]) - Math.max(a[1], b[1]), 0);
  const inter = ix * iy;
  const union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
  return union > 0 ? inter / union : 0;
}

/** Greedy hard NMS. Visit by score descending (ties: lower index); suppress every remaining box with IoU > threshold. */
export function nms(boxes: Box[], scores: number[], iouThreshold: number): number[] {
  const order = scores.map((_, i) => i).sort((i, j) => scores[j]! - scores[i]! || i - j);
  const suppressed = new Array<boolean>(scores.length).fill(false);
  const keep: number[] = [];
  for (const i of order) {
    if (suppressed[i]) continue;
    keep.push(i);
    for (let j = 0; j < scores.length; j++) {
      if (!suppressed[j] && j !== i && iou(boxes[i]!, boxes[j]!) > iouThreshold) suppressed[j] = true;
    }
  }
  return keep;
}

/**
 * 1) drop invalid class ids, 2) keep score >= threshold[class], 3) NMS per class (or class-agnostic),
 * 4) sort by score descending (ties: lower original index), 5) truncate to `maxDetections`.
 */
export function postprocess(
  d: Decoded,
  scoreThresholds: number[],
  nmsIou: number,
  maxDetections: number,
  classAgnostic: boolean = false,
): Decoded {
  const numClasses = scoreThresholds.length;
  const idx: number[] = [];
  for (let i = 0; i < d.scores.length; i++) {
    const c = d.classes[i]!;
    if (c >= 0 && c < numClasses && d.scores[i]! >= scoreThresholds[c]!) idx.push(i);
  }
  const groups = classAgnostic ? [idx] : Array.from({ length: numClasses }, (_, c) => idx.filter((i) => d.classes[i] === c));
  const kept: number[] = [];
  for (const g of groups) {
    if (g.length === 0) continue;
    const k = nms(g.map((i) => d.boxes[i]!), g.map((i) => d.scores[i]!), nmsIou);
    for (const j of k) kept.push(g[j]!);
  }
  kept.sort((i, j) => d.scores[j]! - d.scores[i]! || i - j);
  const sel = kept.slice(0, maxDetections);
  return { boxes: sel.map((i) => d.boxes[i]!), classes: sel.map((i) => d.classes[i]!), scores: sel.map((i) => d.scores[i]!) };
}

/**
 * The whole post-inference chain for the ADR 013 bundle: raw tensor -> detections in PHOTO pixels.
 * Throws if the bundle is not allowed in this build (see DECODE_SPEC section 0): thresholds still null/PLACEHOLDER, or an unknown decoder.
 * `allowPlaceholderThresholds` is for internal/dev builds only.
 */
export function detect(
  labels: DetectorLabels,
  raw: ArrayLike<number>,
  photoW: number,
  photoH: number,
  opts: { allowPlaceholderThresholds?: boolean } = {},
): Detection[] {
  const { input, output, classes } = labels;
  if (output.decode.type !== "yolox_raw") throw new Error(`unsupported decoder ${output.decode.type}`);
  const thr = classes.map((c) => labels.thresholds.score[c.name]);
  if (thr.some((t) => t === null || t === undefined)) {
    if (!opts.allowPlaceholderThresholds) throw new Error("score thresholds are not set; refuse to show results in this build");
  }
  const thresholds = thr.map((t) => t ?? 0);
  const decoded = decodeYoloxRaw(raw, classes.length, input.width, input.height, output.decode.strides, output.decode.sigmoid_applied);
  const pp = output.postprocess;
  const kept = postprocess(decoded, thresholds, pp.nms_iou_threshold, pp.max_detections, pp.class_agnostic_nms);
  const lb = letterboxParams(photoW, photoH, input.width, input.height, input.resize.align);
  return kept.boxes.map((b, i) => ({ box: unletterbox(b, lb, photoW, photoH), classId: kept.classes[i]!, score: kept.scores[i]! }));
}
