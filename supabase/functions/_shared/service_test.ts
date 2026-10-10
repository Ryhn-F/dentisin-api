import { assert, assertEquals, assertFalse, assertRejects } from "jsr:@std/assert@1.0.13";
import { json, removeObjects, requireService, safeEqual } from "./service.ts";
import { mockClient, withEnv } from "./testing.ts";

const SECRET = "s".repeat(40);
const ENV = {
  SUPABASE_URL: "http://127.0.0.1:54321",
  SUPABASE_SERVICE_ROLE_KEY: "service-role-key-for-tests",
  CRON_SECRET: SECRET,
};

function req(auth?: string, method = "POST"): Request {
  const headers = new Headers();
  if (auth !== undefined) headers.set("Authorization", auth);
  return new Request("http://localhost/fn", { method, headers });
}

async function expectResponse(r: unknown, status: number, error: string) {
  assert(r instanceof Response, "expected a Response");
  assertEquals(r.status, status);
  assertEquals(await r.json(), { error });
}

Deno.test("503 not_configured when CRON_SECRET is missing", async () => {
  await withEnv({ ...ENV, CRON_SECRET: undefined }, async () => {
    await expectResponse(requireService(req(`Bearer ${SECRET}`)), 503, "not_configured");
  });
});

Deno.test("503 not_configured when CRON_SECRET is shorter than 32 chars", async () => {
  const short = "x".repeat(31);
  await withEnv({ ...ENV, CRON_SECRET: short }, async () => {
    await expectResponse(requireService(req(`Bearer ${short}`)), 503, "not_configured");
  });
});

Deno.test("32-char CRON_SECRET is the accepted minimum", async () => {
  const min = "m".repeat(32);
  await withEnv({ ...ENV, CRON_SECRET: min }, () => {
    assertFalse(requireService(req(`Bearer ${min}`)) instanceof Response);
  });
});

Deno.test("503 not_configured when SUPABASE_URL or service key is missing", async () => {
  await withEnv({ ...ENV, SUPABASE_URL: undefined }, async () => {
    await expectResponse(requireService(req(`Bearer ${SECRET}`)), 503, "not_configured");
  });
  await withEnv({ ...ENV, SUPABASE_SERVICE_ROLE_KEY: undefined }, async () => {
    await expectResponse(requireService(req(`Bearer ${SECRET}`)), 503, "not_configured");
  });
});

Deno.test("fail-closed order: 503 wins over 405 and 403", async () => {
  await withEnv({ ...ENV, CRON_SECRET: undefined }, async () => {
    await expectResponse(requireService(req(undefined, "GET")), 503, "not_configured");
  });
});

Deno.test("405 for non-POST methods, even with the right secret", async () => {
  await withEnv(ENV, async () => {
    for (const m of ["GET", "PUT", "DELETE", "PATCH"]) {
      await expectResponse(requireService(req(`Bearer ${SECRET}`, m)), 405, "method_not_allowed");
    }
  });
});

Deno.test("403 for missing, wrong, anon, service-role and malformed credentials", async () => {
  await withEnv(ENV, async () => {
    const bad = [
      undefined,
      "",
      "Bearer ",
      "Bearer wrong",
      `Bearer ${"s".repeat(39)}`, // one char short
      `Bearer ${SECRET}x`, // one char long
      `Bearer ${"s".repeat(39)}t`, // last char differs
      "Bearer eyJhbGciOiJIUzI1NiJ9.anon-jwt.sig", // anon-key shaped token
      `Bearer ${ENV.SUPABASE_SERVICE_ROLE_KEY}`, // service_role key must NOT be accepted
      SECRET, // no scheme
      `bearer ${SECRET}`, // wrong case
      `Bearer  ${SECRET}`, // double space
      `Bearer ${SECRET}	.`, // trailing garbage after a tab
      `Basic ${SECRET}`,
    ];
    for (const a of bad) {
      await expectResponse(requireService(req(a)), 403, "forbidden");
    }
  });
});

Deno.test("surrounding whitespace is trimmed by the Headers API (not a bypass)", async () => {
  // fetch/Request normalise leading/trailing spaces of header values, so "Bearer <secret> " IS
  // the exact secret by the time the function sees it. Anything else must still fail (above).
  await withEnv(ENV, () => {
    assertFalse(requireService(req(`Bearer ${SECRET} `)) instanceof Response);
  });
});

Deno.test("correct secret returns a client (not a Response)", async () => {
  await withEnv(ENV, () => {
    const r = requireService(req(`Bearer ${SECRET}`));
    assertFalse(r instanceof Response);
    assertEquals(typeof (r as { rpc: unknown }).rpc, "function");
  });
});

Deno.test("403 body never echoes the presented credential", async () => {
  await withEnv(ENV, async () => {
    const r = requireService(req("Bearer leaked-token-123")) as Response;
    assertFalse((await r.text()).includes("leaked-token-123"));
  });
});

Deno.test("safeEqual: equality, prefixes, lengths, unicode, empty", () => {
  assert(safeEqual("", ""));
  assert(safeEqual("abc", "abc"));
  assert(safeEqual("kata-sandi-é", "kata-sandi-é"));
  assertFalse(safeEqual("abc", "abd"));
  assertFalse(safeEqual("abc", "ab"));
  assertFalse(safeEqual("ab", "abc"));
  assertFalse(safeEqual("abc", ""));
  assertFalse(safeEqual("", "abc"));
  assertFalse(safeEqual("a\0", "a")); // NUL padding must not equal a shorter string
  assertFalse(safeEqual("é", "e"));
});

Deno.test("json() sets status and content type", async () => {
  const r = json({ a: 1 }, 207);
  assertEquals(r.status, 207);
  assertEquals(r.headers.get("Content-Type"), "application/json");
  assertEquals(await r.json(), { a: 1 });
  assertEquals(json({}).status, 200);
});

Deno.test("removeObjects: empty list makes no storage call", async () => {
  const m = mockClient();
  assertEquals(await removeObjects(m.client, "b", []), 0);
  assertEquals(m.removeCalls.length, 0);
});

Deno.test("removeObjects: chunks of 100 and sums removed counts", async () => {
  const m = mockClient();
  const paths = Array.from({ length: 250 }, (_, i) => `p${i}`);
  assertEquals(await removeObjects(m.client, "dataset_photos", paths), 250);
  assertEquals(m.removeCalls.map((c) => c.paths.length), [100, 100, 50]);
  assert(m.removeCalls.every((c) => c.bucket === "dataset_photos"));
});

Deno.test("removeObjects: counts only objects the API reports removed", async () => {
  const m = mockClient({ remove: () => ({ data: [{ name: "a" }], error: null }) });
  assertEquals(await removeObjects(m.client, "b", ["a", "gone"]), 1);
});

Deno.test("removeObjects: storage error becomes a generic error without details", async () => {
  const m = mockClient({ remove: () => ({ data: null, error: { message: "secret/path.jpg" } }) });
  const e = await assertRejects(() => removeObjects(m.client, "b", ["secret/path.jpg"]));
  assertEquals((e as Error).message, "storage_remove_failed");
});
