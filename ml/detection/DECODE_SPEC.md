# DECODE_SPEC — running `model.tflite` and decoding its outputs (v1.0.0)

Contract between `backend/ml/exports/<semver>/` and the app (`app/src/...`, TypeScript). The
reference implementation is [`decode.py`](decode.py); port parity is checked against
[`tests/decode_vectors.json`](tests/decode_vectors.json) (inputs + expected outputs, tolerance
1e-6). Every number the app needs comes from `labels.json` (schema: [`labels.schema.json`](labels.schema.json)).
Nothing here is clinical: thresholds are `null`/`PLACEHOLDER` until set on validation data and
reviewed by a dentist.

## 0. Before running
1. Verify the signed manifest, then `SHA256` of all bundle files, then `labels.model.sha256 == sha256(model.tflite)`.
2. Refuse to show results if `labels.distribution == "internal_experiment_only"` in a user-facing
   build, or if any `thresholds.score.*` is `null` outside internal/dev builds.

## 1. Preprocess (photo → input tensor)
Inputs: 8-bit RGB photo `(H, W)` after the quality gate.
1. **Resize** (`input.resize`):
   - `stretch`: resize to `input.width × input.height` (aspect ratio changes).
   - `letterbox`: `r = min(Win/W, Hin/H)`; resize to `(floor(W·r), floor(H·r))` (as YOLOX `preproc`); place at
     `align` = `top_left` (pad right/bottom; YOLOX) or `center` (`pad_x = (Win − W·r)/2`,
     `pad_y = (Hin − H·r)/2`); fill padding with `pad_value` in all channels.
   - `interpolation` names the resampling filter (bilinear default).
2. **Channel order** `input.color_order`: `RGB` or `BGR` (YOLOX was trained on OpenCV BGR).
3. **Normalise** per channel c (pixel p in 0..255, `[lo, hi] = normalization.pixel_range`):
   `v = lo + p·(hi − lo)/255`, `x = (v − mean[c]) / std[c]`.
4. **Quantise** if `input.dtype` is `int8`/`uint8`: `q = clamp(round_half_away_from_zero(x / scale) + zero_point)`
   with `input.quantization`; clamp to [0, 255] (uint8) or [−128, 127] (int8).
5. Layout `NHWC`, batch 1.

## 2. Dequantise outputs
For each tensor in `output.tensors` with non-null `quantization`: `real = scale · (q − zero_point)`.
Tensors are identified by `role`, not by index order alone (`index` is the interpreter output index).

## 3. Decode (by `output.decode.type`)
All decoders output boxes in **model-input pixels** `(x1, y1, x2, y2)`, class id (0-based, order of
`labels.classes`) and score in [0, 1].

### 3.1 `yolox_raw` (tensor role `raw`, shape `[1, N, 5 + C]`)
Row layout `[tx, ty, tw, th, obj, cls_0 … cls_{C−1}]`. `N = Σ_s (Win/s)·(Hin/s)` over `strides`.
Row order: **stride-major, then y, then x** (`for s in strides: for gy: for gx:`).
If `sigmoid_applied` is false apply `σ(z) = 1/(1+e^−z)` to `obj` and `cls_*` first.
`cx = (tx + gx)·s`, `cy = (ty + gy)·s`, `w = e^tw·s`, `h = e^th·s` →
`(cx − w/2, cy − h/2, cx + w/2, cy + h/2)`; `class = argmax_c cls_c` (first maximum wins);
`score = obj · cls_class`.

### 3.2 `ssd_raw_anchors` (roles `raw_boxes` `[1, N, 4]`, `raw_scores` `[1, N, K]`)
`anchors[i] = [yc, xc, h, w]` (normalised 0..1), `scales = [ys, xs, hs, ws]` (default 10, 10, 5, 5).
`box_order = "yxhw"` → row is `[ty, tx, th, tw]`; `"xywh"` → `[tx, ty, tw, th]`.
`yc' = ty/ys·h_a + yc_a`, `xc' = tx/xs·w_a + xc_a`, `h' = e^(th/hs)·h_a`, `w' = e^(tw/ws)·w_a`;
box = `((xc' − w'/2)·Win, (yc' − h'/2)·Hin, (xc' + w'/2)·Win, (yc' + h'/2)·Hin)`.
Scores: `score_activation` ∈ `sigmoid` | `softmax` (row-wise, max-subtracted) | `none`; if
`background_class` drop column 0 **after** activation; `class = argmax`, `score = max`.

### 3.3 `ssd_postprocessed` (roles `boxes` `[1, M, 4]`, `classes` `[1, M]`, `scores` `[1, M]`, `count` `[1]`)
Model already ran TFLite_Detection_PostProcess. Take the first `n = int(count)` rows; boxes are
`[ymin, xmin, ymax, xmax]` normalised → `(xmin·Win, ymin·Hin, xmax·Win, ymax·Hin)`;
`class = round(classes) − class_id_offset`.

### 3.4 `detr_raw` (roles `boxes` `[1, Q, 4]`, `logits` `[1, Q, C]`)
Boxes `[cx, cy, w, h]` normalised; `σ` on logits (no background column); `class = argmax`,
`score = max`; box = `((cx − w/2)·Win, (cy − h/2)·Hin, (cx + w/2)·Win, (cy + h/2)·Hin)`.

## 4. Post-process (`output.postprocess` + `thresholds`)
1. Drop detections with class id outside `[0, C)`.
2. Keep `score ≥ thresholds.score[class_name]`.
3. NMS **per class** (or all together if `class_agnostic_nms`): visit by score descending, ties by
   lower original index; keep the box, suppress every remaining box with IoU **>** `nms_iou_threshold`.
   IoU uses continuous coordinates, area = `(x2 − x1)·(y2 − y1)`, IoU = 0 if union ≤ 0.
4. Sort survivors by score descending (ties: lower original index); keep the first `max_detections`.

## 5. Map back to the photo
- `letterbox`: `x = (x_in − pad_x)/r`, `y = (y_in − pad_y)/r`.
- `stretch`: `x = x_in·W/Win`, `y = y_in·H/Hin`.
Clamp to `[0, W] × [0, H]`.

## 6. Use in triage (pointer, not part of decode)
`classes[i].may_raise_triage_level` is `false` for `caries_noncavitated` (FR-32: shown as "perlu
perhatian", never raises the level alone). Triage uses lesion **count** and **relative box area**
(`box area / photo area`) — parameters live in `triage/rules.yaml` (PLACEHOLDER until drg).
All UI wording: "indikasi / perlu diperiksa", never a diagnosis.

## 7. Open items `[PERLU VERIFIKASI]` (spike S1)
- MediaPipe Model Maker export: real output tensors (raw vs post-processed), anchor extraction
  from metadata, input dtype/normalisation.
- EfficientDet-Lite: output tensor order of TFLite_Detection_PostProcess for the TF version used.
- YOLOX via onnx2tf: whether INT8 keeps float32 I/O; NCHW→NHWC transposition of the output.
`labels.output.tensors_verified` is `true` only when shapes/dtypes were read by an interpreter at export.
