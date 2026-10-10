// process-deletion — right-to-delete executor (FR-102), service-only.
//
// For each pending/processing deletion_request:
//   1. service_pending_deletions()  -> request id + dataset_photos object paths
//   2. Storage API remove(paths)    -> photos really gone (SQL cannot delete blobs)
//   3. service_execute_deletion()   -> deletes/cascades DB rows (and auth user for
//                                      account scope), writes proof + audit_log
// The pg_cron job `dentisai-process-deletions` completes requests that have no
// photos on its own; this function is needed when photos exist.
//
// Invoke (scheduler / team PC only, never from the app):
//   curl -X POST "$SUPABASE_URL/functions/v1/process-deletion" \
//        -H "Authorization: Bearer $CRON_SECRET"
// Logs and responses contain counts only (no ids, paths or e-mails).
import { handle } from "./handler.ts";

Deno.serve((req: Request) => handle(req));
