import { assert, assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import { handle } from "./handler.ts";
import { captureConsole, mockClient, type MockOptions, withEnv } from "../_shared/testing.ts";

const UP_1 = "aaaaaaaa-1111-4111-8111-111111111111";
const UP_2 = "bbbbbbbb-2222-4222-8222-222222222222";
const P_1 = "user-1/child-1/one.jpg";
const P_2 = "user-1/child-1/two.jpg";
const PF_1 = "aaaaaaaa-3333-4333-8333-333333333333";
const PV_1 = "user-1/child-1/proof-1.mp4";
const PV_ORPHAN = "user-9/child-9/orphan.mp4";

const post = () => new Request("http://localhost/retention-sweep", { method: "POST" });

const candidates = () => ({
  data: [
    { upload_id: UP_1, object_path: P_1 },
    { upload_id: UP_2, object_path: P_2 },
  ],
  error: null,
});

function happyRpc(): NonNullable<MockOptions["rpc"]> {
  return {
    service_retention_candidates: candidates,
    service_mark_images_deleted: () => ({ data: 2, error: null }),
    service_expired_proofs: () => ({
      data: [
        { proof_id: PF_1, object_path: PV_1 },
        { proof_id: null, object_path: PV_ORPHAN },
      ],
      error: null,
    }),
    service_mark_proof_object_removed: () => ({ data: null, error: null }),
    service_retention_sweep_db: () => ({
      data: {
        sync_event_deleted: 7,
        screening_request_expired: 3,
        brushing_proof_rows_deleted: 1,
        child_login_attempt_deleted: 4,
      },
      error: null,
    }),
  };
}

Deno.test("default resolver is fail-closed: short secret -> 503", async () => {
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: "short" },
    async () => {
      assertEquals((await handle(post())).status, 503);
    },
  );
});

Deno.test("default resolver: GET -> 405, wrong bearer -> 403", async () => {
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: "c".repeat(40) },
    async () => {
      assertEquals((await handle(new Request("http://localhost/x"))).status, 405);
      const r = await handle(
        new Request("http://localhost/x", {
          method: "POST",
          headers: { Authorization: "Bearer anon-key" },
        }),
      );
      assertEquals(r.status, 403);
    },
  );
});

Deno.test("rejected callers get the resolver's Response untouched", async () => {
  const denied = new Response("no", { status: 403 });
  assert(await handle(post(), () => denied) === denied);
});

Deno.test("list failure -> 500 list_failed", async () => {
  const m = mockClient({
    rpc: { service_retention_candidates: () => ({ data: null, error: { message: "x" } }) },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 500);
    assertEquals(await r.json(), { error: "list_failed" });
  });
  assertEquals(m.removeCalls.length, 0);
});

Deno.test("happy path: objects removed, rows marked, ledger purged; counts only", async () => {
  const m = mockClient({ rpc: happyRpc() });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    const body = await r.json();
    assertEquals(body, {
      candidates: 2,
      objects_removed: 2,
      images_marked_deleted: 2,
      proof_objects_removed: 2,
      proofs_marked_removed: 1,
      proof_purge_failed: false,
      sync_event_deleted: 7,
      requests_expired: 3,
      proof_rows_deleted: 1,
      login_attempts_deleted: 4,
    });
    const raw = JSON.stringify(body);
    for (const s of [UP_1, UP_2, P_1, P_2, PF_1, PV_1, PV_ORPHAN]) assertFalse(raw.includes(s));
  });
  assertEquals(m.removeCalls, [
    { bucket: "dataset_photos", paths: [P_1, P_2] },
    { bucket: "brushing_proofs", paths: [PV_1, PV_ORPHAN] },
  ]);
  // only rows with an id are marked (the orphan object has none)
  const marks = m.rpcCalls.filter((c) => c.name === "service_mark_proof_object_removed");
  assertEquals(marks.map((c) => c.args), [{ p_proof: PF_1 }]);
  const mark = m.rpcCalls.find((c) => c.name === "service_mark_images_deleted");
  assertEquals(mark?.args, { p_upload_ids: [UP_1, UP_2] });
  const joined = logs.join("\n");
  for (const s of [UP_1, UP_2, P_1, P_2, PF_1, PV_1, PV_ORPHAN]) assertFalse(joined.includes(s));
});

Deno.test("no candidates still sweeps the ledger and returns 200", async () => {
  const m = mockClient({
    rpc: {
      ...happyRpc(),
      service_retention_candidates: () => ({ data: [], error: null }),
      service_mark_images_deleted: () => ({ data: 0, error: null }),
      service_expired_proofs: () => ({ data: [], error: null }),
    },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    assertEquals((await r.json()).candidates, 0);
  });
  assertEquals(m.removeCalls.length, 0);
});

Deno.test("storage failure -> 500 image_purge_failed, rows are NOT marked deleted", async () => {
  const m = mockClient({
    rpc: happyRpc(),
    remove: () => ({ data: null, error: { message: `nope ${P_1}` } }),
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 500);
    const b = await r.json();
    assertEquals(b.error, "image_purge_failed");
    assertEquals(b.candidates, 2);
    assertFalse(JSON.stringify(b).includes(P_1));
  });
  assertFalse(m.rpcCalls.some((c) => c.name === "service_mark_images_deleted"));
  assertFalse(m.rpcCalls.some((c) => c.name === "service_retention_sweep_db"));
  assertFalse(logs.join("\n").includes(P_1));
});

Deno.test("mark RPC error -> 500 image_purge_failed (ledger sweep skipped)", async () => {
  const m = mockClient({
    rpc: { ...happyRpc(), service_mark_images_deleted: () => ({ data: null, error: { m: 1 } }) },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 500);
    assertEquals((await r.json()).error, "image_purge_failed");
  });
  assertFalse(m.rpcCalls.some((c) => c.name === "service_retention_sweep_db"));
});

Deno.test("ledger sweep error -> 207 with sync_event_deleted null", async () => {
  const m = mockClient({
    rpc: { ...happyRpc(), service_retention_sweep_db: () => ({ data: null, error: { m: 1 } }) },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    const b = await r.json();
    assertEquals(b.sync_event_deleted, null);
    assertEquals(b.requests_expired, null);
    assertEquals(b.images_marked_deleted, 2);
  });
});

Deno.test("proof list error -> 207 proof_purge_failed, the DB sweep still runs", async () => {
  const m = mockClient({
    rpc: { ...happyRpc(), service_expired_proofs: () => ({ data: null, error: { m: 1 } }) },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    const b = await r.json();
    assertEquals(b.proof_purge_failed, true);
    assertEquals(b.proof_objects_removed, 0);
    assertEquals(b.sync_event_deleted, 7);
  });
  assert(m.rpcCalls.some((c) => c.name === "service_retention_sweep_db"));
});

Deno.test("proof storage failure -> 207, rows NOT marked removed, path not logged", async () => {
  const m = mockClient({
    rpc: happyRpc(),
    remove: (bucket, paths) =>
      bucket === "brushing_proofs"
        ? { data: null, error: { message: `nope ${PV_1}` } }
        : { data: paths.map((p) => ({ name: p })), error: null },
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    assertEquals((await r.json()).proof_purge_failed, true);
  });
  assertFalse(m.rpcCalls.some((c) => c.name === "service_mark_proof_object_removed"));
  assertFalse(logs.join("\n").includes(PV_1));
});

Deno.test("proof mark RPC error -> 207 proof_purge_failed", async () => {
  const m = mockClient({
    rpc: {
      ...happyRpc(),
      service_mark_proof_object_removed: () => ({ data: null, error: { m: 1 } }),
    },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    assertEquals((await r.json()).proof_purge_failed, true);
  });
});
