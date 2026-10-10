// retention-sweep — image TTL + withdrawn-photo purge + ledger retention, service-only.
//
//   1. service_retention_candidates() -> image_upload rows whose TTL passed
//      (expires_at <= now) or whose consent (c) was withdrawn
//   2. Storage API remove(paths)      -> objects deleted from dataset_photos
//   3. service_mark_images_deleted()  -> status 'deleted' + deleted_at (only for
//                                        rows whose object is really gone)
//   4. service_retention_sweep_db()   -> purge old sync_event ledger rows
//
// Invoke daily (scheduler / team PC only):
//   curl -X POST "$SUPABASE_URL/functions/v1/retention-sweep" \
//        -H "Authorization: Bearer $CRON_SECRET"
// Logs and responses contain counts only.
import { handle } from "./handler.ts";

Deno.serve((req: Request) => handle(req));
