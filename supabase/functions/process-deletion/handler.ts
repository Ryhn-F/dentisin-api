// Handler logic of process-deletion (see index.ts for the contract). Kept separate from
// the Deno.serve() call so it can be unit-tested with a mocked client.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Pending = { request_id: string; storage_paths: string[] | null };

/** Resolves the caller into a service client, or a Response to send back (injectable for tests). */
export type ClientResolver = (req: Request) => SupabaseClient | Response;

export async function handle(
  req: Request,
  resolve: ClientResolver = requireService,
): Promise<Response> {
  const client = resolve(req);
  if (client instanceof Response) return client;

  const { data, error } = await client.rpc("service_pending_deletions", {
    p_limit: 20,
  });
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
      const removed = await removeObjects(
        client,
        "dataset_photos",
        item.storage_paths ?? [],
      );
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

  const summary = {
    requests: (data ?? []).length,
    completed,
    waiting,
    failed,
    objects_removed: objects,
  };
  console.log("process-deletion", JSON.stringify(summary));
  return json(summary, failed > 0 ? 207 : 200);
}
