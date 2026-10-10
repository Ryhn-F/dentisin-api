// Handler logic of retention-sweep (see index.ts for the contract). Kept separate from
// the Deno.serve() call so it can be unit-tested with a mocked client.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Candidate = { upload_id: string; object_path: string };

/** Resolves the caller into a service client, or a Response to send back (injectable for tests). */
export type ClientResolver = (req: Request) => SupabaseClient | Response;

export async function handle(
  req: Request,
  resolve: ClientResolver = requireService,
): Promise<Response> {
  const client = resolve(req);
  if (client instanceof Response) return client;

  const { data, error } = await client.rpc("service_retention_candidates", {
    p_limit: 500,
  });
  if (error) {
    console.error("retention-sweep: list_failed");
    return json({ error: "list_failed" }, 500);
  }
  const rows = (data ?? []) as Candidate[];

  let removed = 0;
  let marked = 0;
  try {
    removed = await removeObjects(
      client,
      "dataset_photos",
      rows.map((r) => r.object_path),
    );
    // Rows whose object was already missing are marked as well (the RPC checks).
    const res = await client.rpc("service_mark_images_deleted", {
      p_upload_ids: rows.map((r) => r.upload_id),
    });
    if (res.error) throw new Error("mark_failed");
    marked = (res.data as number | null) ?? 0;
  } catch (_e) {
    console.error("retention-sweep: image_purge_failed");
    return json({
      error: "image_purge_failed",
      candidates: rows.length,
      removed,
    }, 500);
  }

  const db = await client.rpc("service_retention_sweep_db");
  const ledger = db.error ? null : (db.data as { sync_event_deleted?: number } | null)
    ?.sync_event_deleted ?? 0;

  const summary = {
    candidates: rows.length,
    objects_removed: removed,
    images_marked_deleted: marked,
    sync_event_deleted: ledger,
  };
  console.log("retention-sweep", JSON.stringify(summary));
  return json(summary, db.error ? 207 : 200);
}
