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
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Candidate = { upload_id: string; object_path: string };

Deno.serve(async (req: Request) => {
  const client = requireService(req);
  if (client instanceof Response) return client;

  const { data, error } = await client.rpc("service_retention_candidates", { p_limit: 500 });
  if (error) {
    console.error("retention-sweep: list_failed");
    return json({ error: "list_failed" }, 500);
  }
  const rows = (data ?? []) as Candidate[];

  let removed = 0;
  let marked = 0;
  try {
    removed = await removeObjects(client, "dataset_photos", rows.map((r) => r.object_path));
    // Rows whose object was already missing are marked as well (the RPC checks).
    const res = await client.rpc("service_mark_images_deleted", {
      p_upload_ids: rows.map((r) => r.upload_id),
    });
    if (res.error) throw new Error("mark_failed");
    marked = (res.data as number | null) ?? 0;
  } catch (_e) {
    console.error("retention-sweep: image_purge_failed");
    return json({ error: "image_purge_failed", candidates: rows.length, removed }, 500);
  }

  const db = await client.rpc("service_retention_sweep_db");
  const ledger = db.error ? null : (db.data as { sync_event_deleted?: number } | null)?.sync_event_deleted ?? 0;

  const summary = { candidates: rows.length, objects_removed: removed, images_marked_deleted: marked, sync_event_deleted: ledger };
  console.log("retention-sweep", JSON.stringify(summary));
  return json(summary, db.error ? 207 : 200);
});
