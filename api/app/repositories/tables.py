"""Every Supabase table / column / RPC name the API touches, in ONE place.

Source of truth: backend/supabase/migrations/*.sql (2026100800xx by the
Supabase engineer; 20261009000100_ml_review_queue.sql for the ml_* tables).
"""

from __future__ import annotations

from typing import Final

# --- existing schema (read-only for the API) ------------------------------------
USER_ROLE: Final = "user_role"
USER_ROLE_USER_ID: Final = "user_id"
USER_ROLE_ROLE: Final = "role"

FEATURE_FLAG: Final = "feature_flag"
FEATURE_FLAG_KEY: Final = "key"
FEATURE_FLAG_ENABLED: Final = "enabled"
FLAG_DATASET_CONTRIBUTION: Final = "dataset_contribution"

IMAGE_UPLOAD: Final = "image_upload"
IMAGE_UPLOAD_ID: Final = "id"
IMAGE_UPLOAD_CHILD_ID: Final = "child_id"
IMAGE_UPLOAD_STATUS: Final = "status"
IMAGE_UPLOAD_EXPIRES_AT: Final = "expires_at"
IMAGE_UPLOAD_OBJECT_PATH: Final = "object_path"
IMAGE_UPLOAD_BUCKET: Final = "dataset_photos"
IMAGE_STATUS_UPLOADED: Final = "uploaded"

CONSENT: Final = "consent"
CONSENT_CHILD_ID: Final = "child_id"
CONSENT_TYPE: Final = "consent_type"
CONSENT_WITHDRAWN_AT: Final = "withdrawn_at"
CONSENT_TYPE_DATASET: Final = "c"

# --- ml_* tables (20261009000100_ml_review_queue.sql) ----------------------------
REVIEW_QUEUE: Final = "ml_review_queue"
RQ_ID: Final = "id"
RQ_IMAGE_UPLOAD_ID: Final = "image_upload_id"
RQ_STATUS: Final = "status"
RQ_REQUIRED_REVIEWS: Final = "required_reviews"
RQ_REVIEW_COUNT: Final = "review_count"
RQ_ENQUEUED_BY: Final = "enqueued_by"
RQ_ENQUEUED_AT: Final = "enqueued_at"
RQ_COMPLETED_AT: Final = "completed_at"
RQ_STATUS_PENDING: Final = "pending"
RQ_STATUS_COMPLETED: Final = "completed"

REVIEW_RESULT: Final = "ml_review_result"
RR_ID: Final = "id"
RR_QUEUE_ID: Final = "queue_id"
RR_REVIEWER_ID: Final = "reviewer_id"
RR_GRADER: Final = "grader_pseudonym"
RR_DECISION: Final = "decision"
RR_IMAGE_WIDTH: Final = "image_width"
RR_IMAGE_HEIGHT: Final = "image_height"
RR_BOXES: Final = "boxes"
RR_NOTES: Final = "notes"
RR_CREATED_AT: Final = "created_at"

MODEL_REGISTRY: Final = "ml_model_registry"
MR_ID: Final = "id"
MR_MANIFEST_SHA256: Final = "manifest_sha256"
MR_SEQUENCE: Final = "sequence"
MR_SCHEMA_VERSION: Final = "schema_version"
MR_CHANNEL: Final = "channel"
MR_SIGNER_KEY_ID: Final = "signer_key_id"
MR_MIN_APP_VERSION: Final = "min_app_version"
MR_MODEL_VERSION: Final = "model_version"
MR_MODEL_SHA256: Final = "model_sha256"
MR_MODEL_LICENSE: Final = "model_license"
MR_RULES_VERSION: Final = "rules_version"
MR_RECOMMENDER_VERSION: Final = "recommender_version"
MR_MANIFEST_CREATED_AT: Final = "manifest_created_at"
MR_EXPIRES_AT: Final = "expires_at"
MR_MANIFEST_CANONICAL: Final = "manifest_canonical"
MR_MANIFEST_SIG: Final = "manifest_sig"
MR_REGISTERED_BY: Final = "registered_by"
MR_REGISTERED_AT: Final = "registered_at"

# --- RPCs ---------------------------------------------------------------------------
RPC_ML_AUDIT: Final = "service_ml_audit"

# DB error codes raised by the ml_* guard triggers (message text).
DB_ERR_NOT_ELIGIBLE: Final = "ml_review_not_eligible"
DB_ERR_QUEUE_CLOSED: Final = "ml_review_queue_closed"
