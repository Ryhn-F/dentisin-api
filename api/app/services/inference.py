"""Optional cloud QA/second-opinion inference (ENABLE_CLOUD_INFER, default off).

The photo is read from the private bucket by reference (image_upload.id),
only after the same consent/flag gating as the review queue, is held in memory
for the duration of the call and is never stored or logged by the API.
"""

from __future__ import annotations

from ..auth import Principal
from ..repositories import tables as t
from ..repositories.base import MlRepository
from .detector import Detection, Detector
from .review_queue import eligible_image

# dataset_photos bucket hard limit (backend/supabase/migrations/..._storage.sql)
MAX_PHOTO_BYTES = 512_000


async def infer(
    repo: MlRepository, detector: Detector, principal: Principal, image_upload_id: str
) -> list[Detection]:
    image = await eligible_image(repo, image_upload_id)
    photo = await repo.fetch_photo(image.object_path, MAX_PHOTO_BYTES)
    detections = await detector.detect(photo)
    del photo
    await repo.audit(
        principal.user_id,
        "ml_cloud_infer",
        t.IMAGE_UPLOAD,
        image.id,
        {"detector": detector.name, "detections": len(detections)},
    )
    return detections
