import { assert, assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import { handle } from "./handler.ts";
import { captureConsole, mockClient, withEnv } from "../_shared/testing.ts";

const REQ_A = "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const REQ_B = "22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const PATH_A = "user-1/child-1/photo-a.jpg";

const post = () => new Request("http://localhost/process-deletion", { method: "POST" });

Deno.test("default resolver is fail-closed: no secret configured -> 503", async () => {
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: undefined },
    async () => {
      const r = await handle(post());
      assertEquals(r.status, 503);
    },
  );
});

Deno.test("default resolver rejects a wrong bearer with 403 and does not touch the DB", async () => {
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: "c".repeat(40) },
    async () => {
      const r = await handle(
        new Request("http://localhost/x", {
          method: "POST",
          headers: { Authorization: "Bearer nope" },
        }),
      );
      assertEquals(r.status, 403);
    },
  );
});

Deno.test("rejected callers get the resolver's Response untouched", async () => {
  const denied = new Response("no", { status: 403 });
  const r = await handle(post(), () => denied);
  assert(r === denied);
});

Deno.test("list failure -> 500 list_failed, nothing else is called", async () => {
  const m = mockClient({
    rpc: { service_pending_deletions: () => ({ data: null, error: { message: "boom" } }) },
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 500);
    assertEquals(await r.json(), { error: "list_failed" });
  });
  assertEquals(m.rpcCalls.map((c) => c.name), ["service_pending_deletions"]);
  assertFalse(logs.join("\n").includes("boom"));
});

Deno.test("no pending requests -> 200 with zero counts", async () => {
  const m = mockClient({ rpc: { service_pending_deletions: () => ({ data: [], error: null }) } });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    assertEquals(await r.json(), {
      requests: 0,
      completed: 0,
      waiting: 0,
      failed: 0,
      objects_removed: 0,
    });
  });
  assertEquals(m.removeCalls.length, 0);
  assertEquals(logs.length, 1);
});

Deno.test("photos removed first, then the executor RPC runs with the removed count", async () => {
  const order: string[] = [];
  const m = mockClient({
    rpc: {
      service_pending_deletions: () => ({
        data: [
          { request_id: REQ_A, storage_paths: [PATH_A, "u/c/b.jpg"] },
          { request_id: REQ_B, storage_paths: null },
        ],
        error: null,
      }),
      service_execute_deletion: (a) => {
        order.push(`execute:${a?.p_request_id}:${a?.p_storage_objects_deleted}`);
        return { data: { status: "completed" }, error: null };
      },
    },
    remove: (_b, paths) => {
      order.push(`remove:${paths.length}`);
      return { data: paths.map((p) => ({ name: p })), error: null };
    },
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    const body = await r.json();
    assertEquals(body, {
      requests: 2,
      completed: 2,
      waiting: 0,
      failed: 0,
      objects_removed: 2,
    });
    // counts only: no ids or paths in the response
    const raw = JSON.stringify(body);
    for (const secret of [REQ_A, REQ_B, PATH_A]) assertFalse(raw.includes(secret));
  });
  assertEquals(order, [`remove:2`, `execute:${REQ_A}:2`, `execute:${REQ_B}:0`]);
  assertEquals(m.removeCalls.every((c) => c.bucket === "dataset_photos"), true);
  const joined = logs.join("\n");
  for (const secret of [REQ_A, REQ_B, PATH_A]) assertFalse(joined.includes(secret));
});

Deno.test("executor returning a non-completed status counts as waiting", async () => {
  const m = mockClient({
    rpc: {
      service_pending_deletions: () => ({
        data: [{ request_id: REQ_A, storage_paths: [] }],
        error: null,
      }),
      service_execute_deletion: () => ({
        data: { status: "awaiting_storage_purge" },
        error: null,
      }),
    },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    const b = await r.json();
    assertEquals([b.completed, b.waiting, b.failed], [0, 1, 0]);
  });
});

Deno.test("storage failure -> request counted failed, 207, executor NOT called for it", async () => {
  const m = mockClient({
    rpc: {
      service_pending_deletions: () => ({
        data: [
          { request_id: REQ_A, storage_paths: [PATH_A] },
          { request_id: REQ_B, storage_paths: [] },
        ],
        error: null,
      }),
      service_execute_deletion: () => ({ data: { status: "completed" }, error: null }),
    },
    remove: (_b, paths) =>
      paths.includes(PATH_A)
        ? { data: null, error: { message: `cannot delete ${PATH_A}` } }
        : { data: [], error: null },
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    const b = await r.json();
    assertEquals([b.requests, b.completed, b.failed], [2, 1, 1]);
  });
  const executed = m.rpcCalls.filter((c) => c.name === "service_execute_deletion");
  assertEquals(executed.length, 1);
  assertEquals(executed[0].args?.p_request_id, REQ_B);
  assertFalse(logs.join("\n").includes(PATH_A));
});

Deno.test("executor RPC error -> counted failed (207)", async () => {
  const m = mockClient({
    rpc: {
      service_pending_deletions: () => ({
        data: [{ request_id: REQ_A, storage_paths: [] }],
        error: null,
      }),
      service_execute_deletion: () => ({ data: null, error: { message: "db" } }),
    },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 207);
    assertEquals((await r.json()).failed, 1);
  });
});
