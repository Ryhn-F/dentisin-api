// Handler logic of retention-sweep (see index.ts for the contract). Kept separate from
// the Deno.serve() call so it can be unit-tested with a mocked client.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { json, removeObjects, requireService } from "../_shared/service.ts";

type Candidate = { upload_id: string; object_path: string };
type ExpiredProof = { proof_id: string | null; object_path: string };
type DbSweep = {
  sync_event_deleted?: number;
  screening_request_expired?: number;
  brushing_proof_expired?: number;
  brushing_proof_rows_deleted?: number;
  child_login_attempt_deleted?: number;
};

const PROOF_BATCH = 200;

/** Resolves the caller into a service client, or a Response to send back (injectable for tests). */
export type ClientResolver = (req: Request) => SupabaseClient | Response;

/**
 * Removes expired / deleted / consent-less brushing proof videos (ADR 017) and records that
 * they are gone. Orphan objects (proof_id null) are removed too. A row is only marked once
 * its object is really gone (the RPC re-checks), so a failed removal is retried next run.
 */
async function purgeProofs(
  client: SupabaseClient,
): Promise<{ removed: number; marked: number }> {
  const list = await client.rpc("service_expired_proofs", { p_limit: PROOF_BATCH });
  if (list.error) throw new Error("proof_list_failed");
  const rows = (list.data ?? []) as ExpiredProof[];
  const removed = await removeObjects(
    client,
    "brushing_proofs",
    rows.map((r) => r.object_path),
  );
  let marked = 0;
  for (const row of rows) {
    if (!row.proof_id) continue;
    const res = await client.rpc("service_mark_proof_object_removed", {
      p_proof: row.proof_id,
    });
    if (res.error) throw new Error("proof_mark_failed");
    marked++;
  }
  return { removed, marked };
}

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

  // Proof videos: a failure must not block the DB sweep (it only deletes rows whose
  // object is already gone), so it is reported as 207 instead of aborting.
  let proofsRemoved = 0;
  let proofsMarked = 0;
  let proofFailed = false;
  try {
    const p = await purgeProofs(client);
    proofsRemoved = p.removed;
    proofsMarked = p.marked;
  } catch (_e) {
    proofFailed = true;
    console.error("retention-sweep: proof_purge_failed");
  }

  const db = await client.rpc("service_retention_sweep_db");
  const sweep = db.error ? null : ((db.data ?? {}) as DbSweep);

  const summary = {
    candidates: rows.length,
    objects_removed: removed,
    images_marked_deleted: marked,
    proof_objects_removed: proofsRemoved,
    proofs_marked_removed: proofsMarked,
    proof_purge_failed: proofFailed,
    sync_event_deleted: sweep ? sweep.sync_event_deleted ?? 0 : null,
    requests_expired: sweep ? sweep.screening_request_expired ?? 0 : null,
    proof_rows_deleted: sweep ? sweep.brushing_proof_rows_deleted ?? 0 : null,
    login_attempts_deleted: sweep ? sweep.child_login_attempt_deleted ?? 0 : null,
  };
  console.log("retention-sweep", JSON.stringify(summary));
  return json(summary, db.error || proofFailed ? 207 : 200);
}
