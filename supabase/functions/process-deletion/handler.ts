// Handler logic of process-deletion (see index.ts for the contract). Kept separate from
// the Deno.serve() call so it can be unit-tested with a mocked client.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Pending = { request_id: string; storage_paths: string[] | null };
type Targets = { proof_paths: string[] | null };

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
  let proofObjects = 0;
  for (const item of (data ?? []) as Pending[]) {
    try {
      const removed = await removeObjects(
        client,
        "dataset_photos",
        item.storage_paths ?? [],
      );
      // Brushing proof videos (ADR 017) live in their own bucket; the executor refuses to
      // complete while any object of the subject remains in either bucket.
      const targets = await client.rpc("service_deletion_targets", {
        p_request_id: item.request_id,
      });
      if (targets.error) throw new Error("targets_failed");
      const proofPaths = ((targets.data ?? []) as Targets[])
        .flatMap((t) => t.proof_paths ?? []);
      const proofRemoved = await removeObjects(client, "brushing_proofs", proofPaths);
      objects += removed;
      proofObjects += proofRemoved;
      const res = await client.rpc("service_execute_deletion", {
        p_request_id: item.request_id,
        p_storage_objects_deleted: removed + proofRemoved,
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
    proof_objects_removed: proofObjects,
  };
  console.log("process-deletion", JSON.stringify(summary));
  return json(summary, failed > 0 ? 207 : 200);
}
