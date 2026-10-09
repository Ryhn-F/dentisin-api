"""OPTIONAL prototype: extract brushing landmark FEATURES from a camera or video file.

STATUS: UNTESTED on the author's machine (mediapipe/opencv were not installed there). Treat it as a
sketch to be run and fixed by whoever has the dependencies. It is excluded from coverage and CI.

What it does
- Reads frames with OpenCV, runs MediaPipe Face Landmarker + Hand Landmarker (Tasks API), reduces
  each frame to the scalar features in ``label_schema.json`` and writes a session JSON.
- Frames are processed in memory and discarded. NOTHING image-like is written: no video, no
  frames, no raw face mesh (face geometry can identify a child; keep it out of storage).
- Real children: only after KEPK approval and parental consent (docs/regulatory/). Use adult
  volunteers or synthetic data until then.

Not a hard dependency (ADR 008): install separately, e.g.
``pip install mediapipe opencv-python numpy``, and download ``face_landmarker.task`` and
``hand_landmarker.task`` yourself after checking their
licenses (record them in ml/data/LICENSES.md). This script downloads nothing.

Conventions (must match label_schema.json): coordinates are of the MIRRORED preview, face_scale is
face height in normalised units, yaw > 0 = head turned toward the right of the preview, pitch > 0 =
chin down. The Euler-angle extraction below is an assumption that must be checked against a
protractor/observer before the numbers are trusted.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Face Mesh indices (MediaPipe canonical topology): inner lips 13/14, forehead 10, chin 152.
UPPER_INNER_LIP, LOWER_INNER_LIP, FOREHEAD, CHIN = 13, 14, 10, 152
# Hand: wrist + the four finger MCP joints approximate the palm centre.
PALM_POINTS = (0, 5, 9, 13, 17)


def _import_optional() -> tuple[Any, Any, Any, Any]:
    try:
        import cv2
        import mediapipe as mp
        import numpy as np
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "This prototype needs optional packages: pip install mediapipe opencv-python numpy\n"
            f"({exc})"
        ) from exc
    return cv2, mp, np, (mp_python, vision)


def head_pose_deg(matrix: Any, np: Any) -> tuple[float, float]:
    """Yaw/pitch (degrees) from MediaPipe's 4x4 face transform. UNVERIFIED convention."""
    rot = np.asarray(matrix)[:3, :3]
    yaw = math.degrees(math.atan2(rot[0, 2], rot[2, 2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -float(rot[1, 2])))))
    return yaw, pitch


def extract(
    source: str, face_model: Path, hand_model: Path, max_seconds: float, out: Path, subject: str
) -> int:
    cv2, mp, np, (mp_python, vision) = _import_optional()
    face = vision.FaceLandmarker.create_from_options(
        vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(face_model)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,
        )
    )
    hand = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(hand_model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        )
    )
    capture = cv2.VideoCapture(0 if source == "camera" else source)
    frames: list[dict[str, Any]] = []
    t0: int | None = None
    while capture.isOpened():
        ok, bgr = capture.read()
        if not ok:
            break
        t_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC)) if source != "camera" else None
        if t_ms is None:
            t_ms = int(cv2.getTickCount() / cv2.getTickFrequency() * 1000)
        t0 = t_ms if t0 is None else t0
        rel_t = t_ms - t0
        if rel_t > max_seconds * 1000:
            break
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        )
        f_res = face.detect_for_video(image, t_ms)
        h_res = hand.detect_for_video(image, t_ms)
        frames.append(_features(rel_t, f_res, h_res, np))
        del bgr, image  # nothing from the image survives this iteration
    capture.release()
    session = {
        "schema_version": "0.1.0",
        "NOTE": "extractor output only: fill subject/consent/observers/segments before validation",
        "recording": {"camera": "front", "mirrored_preview": True, "extractor": "mediapipe-tasks"},
        "subject": {"pseudonym_id": subject},
        "frames": frames,
    }
    out.write_text(json.dumps(session), encoding="utf-8")
    return len(frames)


def _features(t_ms: int, f_res: Any, h_res: Any, np: Any) -> dict[str, Any]:
    feat: dict[str, Any] = {
        "t_ms": t_ms,
        "face_present": False,
        "face_conf": 0.0,
        "face_scale": None,
        "mouth_center": None,
        "head_yaw_deg": None,
        "head_pitch_deg": None,
        "hand_present": False,
        "hand_conf": 0.0,
        "hand_center": None,
    }
    if f_res.face_landmarks:
        lm = f_res.face_landmarks[0]
        feat["face_present"] = True
        feat["face_conf"] = 1.0  # the Tasks API exposes no per-face score; refine if available
        feat["face_scale"] = abs(lm[CHIN].y - lm[FOREHEAD].y)
        feat["mouth_center"] = [
            1.0 - (lm[UPPER_INNER_LIP].x + lm[LOWER_INNER_LIP].x) / 2,  # mirror x
            (lm[UPPER_INNER_LIP].y + lm[LOWER_INNER_LIP].y) / 2,
        ]
        if f_res.facial_transformation_matrixes:
            yaw, pitch = head_pose_deg(f_res.facial_transformation_matrixes[0], np)
            # Sign flips reflect the mirrored preview; verify against an observer.
            feat["head_yaw_deg"], feat["head_pitch_deg"] = -yaw, pitch
    if h_res.hand_landmarks:
        pts = h_res.hand_landmarks[0]
        feat["hand_present"] = True
        feat["hand_conf"] = float(h_res.handedness[0][0].score) if h_res.handedness else 1.0
        feat["hand_center"] = [
            1.0 - sum(pts[i].x for i in PALM_POINTS) / len(PALM_POINTS),
            sum(pts[i].y for i in PALM_POINTS) / len(PALM_POINTS),
        ]
    return feat


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument(
        "source", help="'camera' or a video file path (video is read, never stored)"
    )
    parser.add_argument("--face-model", type=Path, required=True)
    parser.add_argument("--hand-model", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=150.0)
    parser.add_argument("--subject", default="adult-volunteer-0001", help="pseudonym, never a name")
    parser.add_argument("--out", type=Path, default=Path("session_features.json"))
    args = parser.parse_args(argv)
    count = extract(
        args.source, args.face_model, args.hand_model, args.max_seconds, args.out, args.subject
    )
    print(f"wrote {count} feature frames to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
