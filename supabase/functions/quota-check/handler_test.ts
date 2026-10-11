import { assert, assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import { handle, sanitize } from "./handler.ts";
import { captureConsole, mockClient, withEnv } from "../_shared/testing.ts";

const post = () => new Request("http://localhost/quota-check", { method: "POST" });

const snapshot = (overall = "warn") => ({
  checked_at: "2026-10-11T00:00:00Z",
  warn_pct: 70,
  critical_pct: 90,
  overall_status: overall,
  metrics: [
    { metric: "db_size", unit: "mb", used: 360.2, limit: 500, pct: 72, status: "warn" },
    { metric: "storage_size", unit: "mb", used: 12.5, limit: 1024, pct: 1.2, status: "ok" },
    { metric: "mau", unit: "users", used: 3, limit: 50000, pct: 0, status: "ok" },
    {
      metric: "auth_email_24h",
      unit: "emails",
      used: null,
      limit: 100,
      pct: null,
      status: "unknown",
    },
  ],
});

Deno.test("default resolver is fail-closed: short secret -> 503, wrong bearer -> 403", async () => {
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: "short" },
    async () => assertEquals((await handle(post())).status, 503),
  );
  await withEnv(
    { SUPABASE_URL: "http://x", SUPABASE_SERVICE_ROLE_KEY: "k", CRON_SECRET: "c".repeat(40) },
    async () => {
      const r = await handle(
        new Request("http://localhost/x", {
          method: "POST",
          headers: { Authorization: "Bearer x" },
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

Deno.test("happy path: 200 with the sanitized snapshot; logs numbers only", async () => {
  const m = mockClient({ rpc: { service_quota_usage: () => ({ data: snapshot(), error: null }) } });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    const body = await r.json();
    assertEquals(body.overall_status, "warn");
    assertEquals(body.metrics.length, 4);
    assertEquals(body.metrics[0], snapshot().metrics[0]);
    assertEquals(body.metrics[3].used, null);
    assertFalse("checked_at" in body);
  });
  assertEquals(m.rpcCalls, [{ name: "service_quota_usage", args: undefined }]);
  assertEquals(logs.length, 1);
  assert(logs[0].includes('"db_size":72'));
});

Deno.test("critical snapshot is still 200 (the workflow decides how to alert)", async () => {
  const m = mockClient({
    rpc: { service_quota_usage: () => ({ data: snapshot("critical"), error: null }) },
  });
  await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 200);
    assertEquals((await r.json()).overall_status, "critical");
  });
});

Deno.test("rpc error -> 500 rpc_failed without echoing the error", async () => {
  const m = mockClient({
    rpc: { service_quota_usage: () => ({ data: null, error: { message: "secret detail" } }) },
  });
  const logs = await captureConsole(async () => {
    const r = await handle(post(), () => m.client);
    assertEquals(r.status, 500);
    assertEquals(await r.json(), { error: "rpc_failed" });
  });
  assertFalse(logs.join("\n").includes("secret detail"));
});

Deno.test("malformed snapshot -> 500 bad_snapshot", async () => {
  for (
    const data of [null, "x", { overall_status: "fine", metrics: [] }, { overall_status: "ok" }]
  ) {
    const m = mockClient({ rpc: { service_quota_usage: () => ({ data, error: null }) } });
    await captureConsole(async () => {
      const r = await handle(post(), () => m.client);
      assertEquals(r.status, 500);
      assertEquals((await r.json()).error, "bad_snapshot");
    });
  }
});

Deno.test("sanitize drops unknown fields and rejects free-text codes", () => {
  const raw = snapshot();
  (raw.metrics[0] as Record<string, unknown>).owner = "aaaaaaaa-1111-4111-8111-111111111111";
  (raw as Record<string, unknown>).email = "a@b.test";
  const s = sanitize(raw);
  assert(s);
  const text = JSON.stringify(s);
  assertFalse(text.includes("aaaaaaaa") || text.includes("@"));
  const bad = snapshot();
  bad.metrics[1].metric = "Storage Size <b>";
  assertEquals(sanitize(bad), null);
  const nan = snapshot();
  (nan.metrics[0] as Record<string, unknown>).used = "360";
  assertEquals(sanitize(nan)?.metrics[0].used, null);
});
