import { assert, assertEquals, assertFalse, assertNotEquals } from "jsr:@std/assert@1.0.13";
import {
  clientIp,
  DUMMY_AUTH_EMAIL,
  handle,
  hmacHex,
  type LoginDeps,
  type LoginService,
  type Resolved,
} from "./handler.ts";
import { captureConsole } from "../_shared/testing.ts";

const URL_ = "http://127.0.0.1:54321";
const ANON = "anon-key-for-tests";
const PEPPER = "p".repeat(40);
const PARENT = "ibu.siti@example.com";
const KID_ID = "44444444-4444-4444-8444-444444444444";
const KID_EMAIL = "child-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa@child.dentisai.invalid";
const OFF_ID = "55555555-5555-4555-8555-555555555555";
const OFF_EMAIL = "child-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb@child.dentisai.invalid";
const PASSWORD = "Sikat2xSehari";
const IP = "203.0.113.7";
const ACCESS = "access-token-jwt-for-tests";
const REFRESH = "refresh-token-for-tests";
const CAPTCHA = "turnstile-token-for-tests";

interface AuthUser {
  id: string;
  password: string;
  banned?: boolean;
}

interface World {
  /** (parent e-mail|username) -> resolved row */
  directory: Map<string, Resolved>;
  /** GoTrue users by e-mail */
  auth: Map<string, AuthUser>;
  settings: Record<string, unknown>;
  failures: Map<string, number>; // throttle key -> failed attempts
  calls: string[];
  records: { key: string; ok: boolean; windowS: number }[];
  checks: { key: string; max: number; windowS: number }[];
  grants: { url: string; headers: Headers; body: Record<string, unknown> }[];
  logouts: string[];
  sleeps: number[];
  touched: string[];
  captchaRequired?: boolean;
  gotrueStatus?: number; // force a status (5xx, 429)
  gotrueThrows?: boolean;
  sessionUserId?: string; // override the user id GoTrue returns
  throwOn?: string;
  clock: number;
}

function world(): World {
  return {
    directory: new Map([
      [`${PARENT}|siti.kecil`, { child_user_id: KID_ID, auth_email: KID_EMAIL, disabled: false }],
      [`${PARENT}|nonaktif`, { child_user_id: OFF_ID, auth_email: OFF_EMAIL, disabled: true }],
    ]),
    auth: new Map([
      [KID_EMAIL, { id: KID_ID, password: PASSWORD }],
      [OFF_EMAIL, { id: OFF_ID, password: PASSWORD, banned: true }],
    ]),
    settings: { child_login_max_attempts: 3, child_login_window_s: 900 },
    failures: new Map(),
    calls: [],
    records: [],
    checks: [],
    grants: [],
    logouts: [],
    sleeps: [],
    touched: [],
    clock: 1_800_000_000_000,
  };
}

function gotrue(w: World): typeof fetch {
  return (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (w.gotrueThrows) return Promise.reject(new TypeError("connection refused"));
    if (url.includes("/auth/v1/logout")) {
      w.calls.push("logout");
      w.logouts.push(new Headers(init?.headers).get("Authorization") ?? "");
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    w.calls.push("grant");
    const body = JSON.parse(String(init?.body));
    w.grants.push({ url, headers: new Headers(init?.headers), body });
    w.clock += 120; // GoTrue latency
    if (w.gotrueStatus) {
      return Promise.resolve(new Response('{"msg":"down"}', { status: w.gotrueStatus }));
    }
    const reject = (desc: string) =>
      new Response(JSON.stringify({ error: "invalid_grant", error_description: desc }), {
        status: 400,
      });
    if (w.captchaRequired && body.gotrue_meta_security?.captcha_token !== CAPTCHA) {
      return Promise.resolve(reject("captcha protection: request disallowed"));
    }
    const u = w.auth.get(body.email);
    if (!u || u.password !== body.password) {
      return Promise.resolve(reject("Invalid login credentials"));
    }
    if (u.banned) return Promise.resolve(reject("User is banned"));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          access_token: ACCESS,
          refresh_token: REFRESH,
          token_type: "bearer",
          expires_in: 3600,
          expires_at: 1_800_003_600,
          user: { id: w.sessionUserId ?? u.id, email: body.email, app_metadata: { username: "x" } },
        }),
        { status: 200 },
      ),
    );
  };
}

function deps(w: World, configured = true): LoginDeps {
  const maybeThrow = (n: string) => {
    if (w.throwOn === n) throw new Error(`boom ${PARENT}`);
  };
  const service: LoginService = {
    settings() {
      w.calls.push("settings");
      return Promise.resolve(w.settings);
    },
    check(key, max, windowS) {
      w.calls.push("check");
      maybeThrow("check");
      w.checks.push({ key, max, windowS });
      return Promise.resolve((w.failures.get(key) ?? 0) < max);
    },
    record(key, ok, windowS) {
      w.calls.push(`record:${ok}`);
      maybeThrow("record");
      w.records.push({ key, ok, windowS });
      if (!ok) w.failures.set(key, (w.failures.get(key) ?? 0) + 1);
      return Promise.resolve();
    },
    resolve(email, username) {
      w.calls.push("resolve");
      maybeThrow("resolve");
      return Promise.resolve(w.directory.get(`${email}|${username}`) ?? null);
    },
    touchLastLogin(id) {
      w.calls.push("touch");
      maybeThrow("touch");
      w.touched.push(id);
      return Promise.resolve();
    },
  };
  return {
    configured,
    supabaseUrl: URL_,
    anonKey: ANON,
    pepper: PEPPER,
    origins: ["https://app.ryhnfir.dev"],
    service: () => service,
    fetch: gotrue(w),
    sleep: (ms) => {
      w.sleeps.push(ms);
      w.clock += ms;
      return Promise.resolve();
    },
    now: () => w.clock,
    upstreamTimeoutMs: 0,
  };
}

function post(body: unknown, headers: Record<string, string> = { "x-forwarded-for": IP }): Request {
  return new Request("http://localhost/child-login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${ANON}`, ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const login = (extra: Record<string, unknown> = {}) => ({
  parent_email: PARENT,
  username: "siti.kecil",
  password: PASSWORD,
  ...extra,
});

async function call(w: World, req: Request, configured = true) {
  let res: Response | undefined;
  const logs = await captureConsole(async () => {
    res = await handle(req, deps(w, configured));
  });
  const text = await res!.text();
  return { status: res!.status, body: text ? JSON.parse(text) : null, res: res!, text, logs };
}

// ---------------------------------------------------------------- transport

Deno.test("OPTIONS preflight and 405 for other methods", async () => {
  const w = world();
  const pre = await handle(
    new Request("http://localhost/x", {
      method: "OPTIONS",
      headers: { Origin: "https://app.ryhnfir.dev" },
    }),
    deps(w),
  );
  assertEquals(pre.status, 204);
  assertEquals(pre.headers.get("Access-Control-Allow-Origin"), "https://app.ryhnfir.dev");
  const get = await handle(new Request("http://localhost/x"), deps(w));
  assertEquals(get.status, 405);
  assertEquals(await get.json(), { error: "method_not_allowed" });
  assertEquals(w.calls, []);
});

Deno.test("not configured -> 500 internal, nothing called", async () => {
  const w = world();
  const r = await call(w, post(login()), false);
  assertEquals([r.status, r.body], [500, { error: "internal" }]);
  assertEquals(w.calls, []);
});

Deno.test("400 invalid_body for malformed requests (not counted, GoTrue not called)", async () => {
  const bodies: unknown[] = [
    "nope",
    "[]",
    {},
    login({ parent_email: "not-an-email" }),
    login({ parent_email: `${"a".repeat(300)}@x.io` }),
    login({ parent_email: 5 }),
    login({ username: "ab" }),
    login({ username: "has space" }),
    login({ username: null }),
    login({ password: "" }),
    login({ password: 12345678 }),
    login({ password: "A1a".repeat(100) }),
    login({ captcha_token: 5 }),
    login({ captcha_token: "" }),
    login({ captcha_token: "x".repeat(5000) }),
  ];
  for (const b of bodies) {
    const w = world();
    const r = await call(w, post(b));
    assertEquals([r.status, r.body], [400, { error: "invalid_body" }], JSON.stringify(b));
    assertEquals(w.calls, []);
  }
});

// ---------------------------------------------------------------- success

Deno.test("success: 200 with the session only, attempt recorded ok, last_login_at touched", async () => {
  const w = world();
  const r = await call(
    w,
    post(login({ parent_email: "  IBU.Siti@Example.com ", username: " Siti.Kecil " })),
  );
  assertEquals(r.status, 200);
  assertEquals(r.body, {
    access_token: ACCESS,
    refresh_token: REFRESH,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: 1_800_003_600,
    user: { id: KID_ID },
    child: { username: "siti.kecil" },
  });
  for (const s of [PARENT, KID_EMAIL, "@"]) assertFalse(r.text.includes(s), s);
  assertEquals(r.res.headers.get("Cache-Control"), "no-store");
  assertEquals(w.calls, ["settings", "check", "resolve", "grant", "record:true", "touch"]);
  assertEquals(w.touched, [KID_ID]);
  assertEquals(w.sleeps, []); // success is not padded
  const g = w.grants[0];
  assertEquals(g.url, `${URL_}/auth/v1/token?grant_type=password`);
  assertEquals(g.headers.get("apikey"), ANON);
  assertEquals(g.body, { email: KID_EMAIL, password: PASSWORD }); // no captcha field when absent
});

Deno.test("expires_at is derived from expires_in when GoTrue omits it", async () => {
  const w = world();
  const base = gotrue(w);
  const d = deps(w);
  d.fetch = async (i, init) => {
    const res = await base(i, init);
    const j = await res.json();
    delete j.expires_at;
    return new Response(JSON.stringify(j), { status: res.status });
  };
  const res = await handle(post(login()), d);
  const body = await res.json();
  assertEquals(body.expires_at, Math.floor(w.clock / 1000) + 3600);
});

Deno.test("captcha_token is forwarded to GoTrue as gotrue_meta_security", async () => {
  const w = world();
  w.captchaRequired = true;
  const r = await call(w, post(login({ captcha_token: CAPTCHA })));
  assertEquals(r.status, 200);
  assertEquals(w.grants[0].body.gotrue_meta_security, { captcha_token: CAPTCHA });
});

Deno.test("captcha rejected by GoTrue -> the uniform 401 (counted)", async () => {
  const w = world();
  w.captchaRequired = true;
  const r = await call(w, post(login({ captcha_token: "wrong" })));
  assertEquals([r.status, r.body], [401, { error: "invalid_credentials" }]);
  assertEquals(w.records.map((x) => x.ok), [false]);
});

// ---------------------------------------------------------------- anti-enumeration

Deno.test("no enumeration: unknown parent / unknown username / wrong password / disabled look identical", async () => {
  const cases: Record<string, Record<string, unknown>> = {
    unknown_parent: login({ parent_email: "orang.lain@example.com" }),
    unknown_username: login({ username: "tidak.ada" }),
    wrong_password: login({ password: "Salah123x" }),
    disabled_banned: login({ username: "nonaktif" }),
  };
  const seen: { status: number; text: string; calls: string[]; sleeps: number[]; hdr: string }[] =
    [];
  for (const [name, body] of Object.entries(cases)) {
    const w = world();
    const r = await call(w, post(body));
    assertEquals([r.status, r.body], [401, { error: "invalid_credentials" }], name);
    assertEquals(w.grants.length, 1, `${name}: GoTrue must always be called`);
    if (name === "unknown_parent" || name === "unknown_username") {
      assertEquals(w.grants[0].body.email, DUMMY_AUTH_EMAIL);
      assertEquals(String(w.grants[0].body.email).length, KID_EMAIL.length);
    }
    assertEquals(w.grants[0].body.password, body.password, "the supplied password is used");
    const hdr = [...r.res.headers.entries()].map(([k, v]) => `${k}:${v}`).sort().join("|");
    seen.push({ status: r.status, text: r.text, calls: w.calls, sleeps: w.sleeps, hdr });
  }
  for (const s of seen.slice(1)) {
    assertEquals(s.text, seen[0].text);
    assertEquals(s.hdr, seen[0].hdr);
    assertEquals(s.calls, ["settings", "check", "resolve", "grant", "record:false"]);
    assertEquals(s.sleeps, seen[0].sleeps); // padded to the same floor
  }
  assertEquals(seen[0].sleeps, [600 - 120]);
});

Deno.test("disabled account whose Auth ban is not applied yet: session is revoked, uniform 401", async () => {
  const w = world();
  w.auth.get(OFF_EMAIL)!.banned = false; // GoTrue would issue a session
  const r = await call(w, post(login({ username: "nonaktif" })));
  assertEquals([r.status, r.body], [401, { error: "invalid_credentials" }]);
  assertEquals(w.logouts, [`Bearer ${ACCESS}`]);
  assertFalse(r.text.includes(ACCESS));
  assertEquals(w.records.map((x) => x.ok), [false]);
  assertEquals(w.touched, []);
});

Deno.test("GoTrue session for a different user than resolved -> revoked, 401", async () => {
  const w = world();
  w.sessionUserId = "99999999-9999-4999-8999-999999999999";
  const r = await call(w, post(login()));
  assertEquals([r.status, r.body], [401, { error: "invalid_credentials" }]);
  assertEquals(w.logouts.length, 1);
});

Deno.test("failure floor: no extra sleep when the attempt already took longer", async () => {
  const w = world();
  const d = deps(w);
  const base = d.fetch;
  d.fetch = async (i, init) => {
    w.clock += 1000;
    return await base(i, init);
  };
  await captureConsole(async () => {
    const r = await handle(post(login({ password: "Salah123x" })), d);
    assertEquals(r.status, 401);
  });
  assertEquals(w.sleeps, []);
});

// ---------------------------------------------------------------- throttling

Deno.test("throttle key = HMAC-SHA256(pepper, lower(email) | client ip)", async () => {
  const w = world();
  await call(w, post(login({ parent_email: "IBU.SITI@EXAMPLE.COM" })));
  const expected = await hmacHex(PEPPER, `${PARENT}|${IP}`);
  assertEquals(w.checks[0].key, expected);
  assertEquals(w.records[0].key, expected);
  assert(/^[0-9a-f]{64}$/.test(expected));
  assertFalse(expected.includes(PARENT));
});

Deno.test("hmacHex matches the RFC test vector", async () => {
  assertEquals(
    await hmacHex("key", "The quick brown fox jumps over the lazy dog"),
    "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
  );
});

Deno.test("429 too_many_attempts + Retry-After after max failures; no resolve, no GoTrue", async () => {
  const w = world();
  for (let i = 0; i < 3; i++) {
    assertEquals((await call(w, post(login({ password: "Salah123x" })))).status, 401);
  }
  w.calls = [];
  const grants = w.grants.length;
  const r = await call(w, post(login())); // even the right password is refused now
  assertEquals([r.status, r.body], [429, { error: "too_many_attempts" }]);
  assertEquals(r.res.headers.get("Retry-After"), "900");
  assertEquals(w.calls, ["settings", "check"]);
  assertEquals(w.grants.length, grants);
  assertEquals(w.checks.at(-1), { key: w.records[0].key, max: 3, windowS: 900 });
});

Deno.test("throttle is per (parent e-mail, ip): another ip or e-mail is not blocked", async () => {
  const w = world();
  for (let i = 0; i < 3; i++) await call(w, post(login({ password: "Salah123x" })));
  assertEquals((await call(w, post(login(), { "x-forwarded-for": "198.51.100.1" }))).status, 200);
  assertEquals(
    (await call(w, post(login({ parent_email: "lain@example.com" })))).status,
    401, // not 429
  );
});

Deno.test("throttle settings come from app_setting (window echoed in Retry-After)", async () => {
  const w = world();
  w.settings = { child_login_max_attempts: 1, child_login_window_s: "120" };
  await call(w, post(login({ password: "Salah123x" })));
  const r = await call(w, post(login()));
  assertEquals(r.status, 429);
  assertEquals(r.res.headers.get("Retry-After"), "120");
  assertEquals(w.records[0].windowS, 120);
});

Deno.test("defaults (8 / 900 s) when settings are missing", async () => {
  const w = world();
  w.settings = {};
  await call(w, post(login()));
  assertEquals(w.checks[0].max, 8);
  assertEquals(w.checks[0].windowS, 900);
});

Deno.test("clientIp: cf-connecting-ip, then first x-forwarded-for hop, then unknown", () => {
  const r = (h: Record<string, string>) => new Request("http://x", { headers: h });
  assertEquals(
    clientIp(r({ "cf-connecting-ip": "198.51.100.9", "x-forwarded-for": IP })),
    "198.51.100.9",
  );
  assertEquals(clientIp(r({ "x-forwarded-for": `${IP}, 10.0.0.1` })), IP);
  assertEquals(clientIp(r({ "x-forwarded-for": "2001:DB8::1" })), "2001:db8::1");
  assertEquals(clientIp(r({ "x-forwarded-for": "<script>" })), "unknown");
  assertEquals(clientIp(r({})), "unknown");
  assertNotEquals(clientIp(r({ "cf-connecting-ip": "junk value" })), "junk value");
});

// ---------------------------------------------------------------- upstream and internal failures

Deno.test("GoTrue 5xx / 429 / unreachable -> 502 upstream_unavailable, not counted", async () => {
  for (
    const setup of [(w: World) => (w.gotrueStatus = 503), (w: World) => (w.gotrueStatus = 500), (
      w: World,
    ) => (w.gotrueStatus = 429), (w: World) => (w.gotrueThrows = true)]
  ) {
    const w = world();
    setup(w);
    const r = await call(w, post(login()));
    assertEquals([r.status, r.body], [502, { error: "upstream_unavailable" }]);
    assertEquals(w.records, []);
    assertEquals(w.sleeps, []);
  }
});

Deno.test("throttle check or resolve failure -> 500 internal, GoTrue not called", async () => {
  for (const where of ["check", "resolve"]) {
    const w = world();
    w.throwOn = where;
    const r = await call(w, post(login()));
    assertEquals([r.status, r.body], [500, { error: "internal" }]);
    assertEquals(w.grants.length, 0);
  }
});

Deno.test("record / touch failures do not change the answer", async () => {
  const w = world();
  w.throwOn = "record";
  assertEquals((await call(w, post(login({ password: "Salah123x" })))).status, 401);
  assertEquals((await call(w, post(login()))).status, 200);
  const w2 = world();
  w2.throwOn = "touch";
  assertEquals((await call(w2, post(login()))).status, 200);
});

Deno.test("CORS: allowlisted origin echoed on 401; other origins get nothing", async () => {
  const a = await call(
    world(),
    post(login({ password: "Salah123x" }), { Origin: "https://app.ryhnfir.dev" }),
  );
  assertEquals(a.res.headers.get("Access-Control-Allow-Origin"), "https://app.ryhnfir.dev");
  const b = await call(world(), post(login(), { Origin: "https://evil.example" }));
  assertEquals(b.res.headers.get("Access-Control-Allow-Origin"), null);
});

// ---------------------------------------------------------------- logging hygiene

Deno.test("logging hygiene: no e-mail, username, password, token, id, ip or key reaches console.*", async () => {
  const scenarios: [(w: World) => void, Request][] = [
    [() => {}, post(login({ captcha_token: CAPTCHA }))],
    [() => {}, post(login({ password: "Salah123x" }))],
    [() => {}, post(login({ username: "tidak.ada" }))],
    [() => {}, post(login({ username: "nonaktif" }))],
    [(w) => (w.auth.get(OFF_EMAIL)!.banned = false), post(login({ username: "nonaktif" }))],
    [(w) => (w.gotrueStatus = 503), post(login())],
    [(w) => (w.throwOn = "resolve"), post(login())],
    [(w) => (w.throwOn = "record"), post(login())],
    [(w) => (w.throwOn = "touch"), post(login())],
    [(w) => (w.settings = { child_login_max_attempts: 1 }), post(login({ password: "x1Ax" }))],
    [() => {}, post(login({ parent_email: "bad" }))],
  ];
  const all: string[] = [];
  const keys: string[] = [];
  for (const [setup, req] of scenarios) {
    const w = world();
    setup(w);
    const r = await call(w, req);
    all.push(...r.logs);
    keys.push(...w.records.map((x) => x.key));
  }
  // an extra throttled request so the 429 path is covered too
  const w = world();
  w.settings = { child_login_max_attempts: 1 };
  await call(w, post(login({ password: "x1Ax" })));
  all.push(...(await call(w, post(login()))).logs);
  const joined = all.join("\n");
  const sensitive = [
    PARENT,
    "siti.kecil",
    "nonaktif",
    "tidak.ada",
    PASSWORD,
    "Salah123x",
    ACCESS,
    REFRESH,
    CAPTCHA,
    KID_ID,
    KID_EMAIL,
    OFF_ID,
    OFF_EMAIL,
    IP,
    PEPPER,
    ANON,
    ...keys,
  ];
  for (const s of sensitive) assertFalse(joined.includes(s), `leaked: ${s}`);
  assert(joined.includes('"code":"too_many_attempts"'));
  for (const line of all) assert(/^child-login \{/.test(line), line);
});
