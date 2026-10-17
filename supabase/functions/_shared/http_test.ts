import { assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import {
  corsHeaders,
  DEFAULT_ALLOWED_ORIGINS,
  jsonResponse,
  parseAllowedOrigins,
  preflight,
  readJsonObject,
} from "./http.ts";

const req = (origin?: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  if (origin !== undefined) headers.set("Origin", origin);
  return new Request("http://localhost/fn", { method: "POST", ...init, headers });
};

Deno.test("parseAllowedOrigins: default, list, wildcard and junk ignored", () => {
  assertEquals(parseAllowedOrigins(undefined), [...DEFAULT_ALLOWED_ORIGINS]);
  assertEquals(parseAllowedOrigins("  "), [...DEFAULT_ALLOWED_ORIGINS]);
  assertEquals(
    parseAllowedOrigins(
      " https://App.Example.com , http://localhost:8081,*,javascript:x,https://a/b",
    ),
    ["https://app.example.com", "http://localhost:8081"],
  );
  assertEquals(parseAllowedOrigins("*"), []); // explicit wildcard = nobody, never everybody
});

Deno.test("corsHeaders: only allowlisted origins get Access-Control-Allow-Origin", () => {
  const origins = parseAllowedOrigins(undefined);
  const ok = corsHeaders(req("https://app.ryhnfir.dev"), origins);
  assertEquals(ok["Access-Control-Allow-Origin"], "https://app.ryhnfir.dev");
  assertEquals(ok["Vary"], "Origin");
  for (const o of ["https://evil.example", "null", "https://app.ryhnfir.dev.evil.example"]) {
    assertFalse("Access-Control-Allow-Origin" in corsHeaders(req(o), origins), o);
  }
  // React Native: no Origin header, nothing needed
  assertFalse("Access-Control-Allow-Origin" in corsHeaders(req(), origins));
});

Deno.test("preflight: 204, allow headers for allowed origin, none otherwise", () => {
  const origins = ["http://localhost:8081"];
  const ok = preflight(req("http://localhost:8081", { method: "OPTIONS" }), origins);
  assertEquals(ok.status, 204);
  assertEquals(ok.headers.get("Access-Control-Allow-Origin"), "http://localhost:8081");
  assertEquals(ok.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
  const no = preflight(req("https://evil.example", { method: "OPTIONS" }), origins);
  assertEquals(no.status, 204);
  assertEquals(no.headers.get("Access-Control-Allow-Origin"), null);
});

Deno.test("jsonResponse: JSON, no-store, CORS and extra headers", async () => {
  const r = jsonResponse(req("https://app.ryhnfir.dev"), DEFAULT_ALLOWED_ORIGINS, { a: 1 }, 429, {
    "Retry-After": "900",
  });
  assertEquals(r.status, 429);
  assertEquals(r.headers.get("Content-Type"), "application/json");
  assertEquals(r.headers.get("Cache-Control"), "no-store");
  assertEquals(r.headers.get("Retry-After"), "900");
  assertEquals(r.headers.get("Access-Control-Allow-Origin"), "https://app.ryhnfir.dev");
  assertEquals(await r.json(), { a: 1 });
});

Deno.test("readJsonObject: objects only, size bounded", async () => {
  const body = (b: string) => req(undefined, { body: b });
  assertEquals(await readJsonObject(body('{"a":1}'), 100), { a: 1 });
  for (const b of ["", "not json", "[1,2]", "null", "1", '"s"']) {
    assertEquals(await readJsonObject(body(b), 100), null, b);
  }
  assertEquals(await readJsonObject(body(`{"a":"${"x".repeat(200)}"}`), 100), null);
  // multi-byte characters count as bytes
  assertEquals(await readJsonObject(body(`{"a":"${"é".repeat(60)}"}`), 100), null);
});
