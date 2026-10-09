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
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Pending = { request_id: string; storage_paths: string[] | null };

Deno.serve(async (req: Request) => {
  const client = requireService(req);
  if (client instanceof Response) return client;

  const { data, error } = await client.rpc("service_pending_deletions", { p_limit: 20 });
  if (error) {
    console.error("process-deletion: list_failed");
    return json({ error: "list_failed" }, 500);
  }

  let completed = 0;
  let waiting = 0;
  let failed = 0;
  let objects = 0;
  for (const item of (data ?? []) as Pending[]) {
    try {
      const removed = await removeObjects(client, "dataset_photos", item.storage_paths ?? []);
      objects += removed;
      const res = await client.rpc("service_execute_deletion", {
        p_request_id: item.request_id,
        p_storage_objects_deleted: removed,
      });
      if (res.error) throw new Error("execute_failed");
      const status = (res.data as { status?: string } | null)?.status;
      if (status === "completed") completed++;
      else waiting++;
    } catch (_e) {
      failed++; // details intentionally not logged (may contain identifiers)
    }
  }

  const summary = { requests: (data ?? []).length, completed, waiting, failed, objects_removed: objects };
  console.log("process-deletion", JSON.stringify(summary));
  return json(summary, failed > 0 ? 207 : 200);
});
