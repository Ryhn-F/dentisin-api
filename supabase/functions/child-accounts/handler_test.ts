import { assert, assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import {
  type AuthFailure,
  type Caller,
  type ChildAccountRow,
  type Deps,
  handle,
  type Me,
  type NewChildUser,
  type Service,
  syntheticEmail,
} from "./handler.ts";
import { captureConsole } from "../_shared/testing.ts";

const G1 = "11111111-1111-4111-8111-111111111111"; // guardian (caller)
const G2 = "22222222-2222-4222-8222-222222222222"; // another guardian
const TEEN = "33333333-3333-4333-8333-333333333333";
const KID_USER = "44444444-4444-4444-8444-444444444444"; // a child's auth user
const CHILD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"; // G1's child, no account yet
const CHILD_ACC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"; // G1's child WITH an account
const OTHER_CHILD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"; // G2's child
const UNKNOWN_CHILD = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const ACC_USER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"; // auth user of CHILD_ACC
const PASSWORD = "Sikat2xSehari";
const NEW_PASSWORD = "GigiKuat99";
const TOKEN_G1 = "Bearer token-guardian-one";

const TOKENS: Record<string, Me | null> = {
  [TOKEN_G1]: { user_id: G1, account_type: "guardian" },
  "Bearer token-guardian-two": { user_id: G2, account_type: "guardian" },
  "Bearer token-teen": { user_id: TEEN, account_type: "teen" },
  "Bearer token-kader": { user_id: G1, account_type: "guardian", roles: ["guardian", "kader"] },
  "Bearer token-child": { user_id: KID_USER, account_type: "child" },
  "Bearer token-anon": null,
};

type Account = ChildAccountRow & { guardian_id: string };

interface World {
  profiles: Map<string, string>; // child_id -> owner_id
  accounts: Map<string, Account>; // child_id -> account
  settings: Record<string, unknown>;
  calls: string[];
  created: NewChildUser[];
  failCreate?: AuthFailure;
  failUpdate?: AuthFailure;
  failBan?: AuthFailure;
  failDelete?: AuthFailure;
  throwOn?: string;
}

function world(): World {
  return {
    profiles: new Map([[CHILD, G1], [CHILD_ACC, G1], [OTHER_CHILD, G2]]),
    accounts: new Map([[CHILD_ACC, {
      user_id: ACC_USER,
      username: "budi",
      disabled_at: null,
      guardian_id: G1,
    }]]),
    settings: { max_child_accounts_per_guardian: 6 },
    calls: [],
    created: [],
  };
}

function deps(w: World, configured = true): Deps {
  const maybeThrow = (name: string) => {
    if (w.throwOn === name) throw new Error(`db down ${CHILD} ${PASSWORD}`);
  };
  const caller = (authorization: string): Caller => ({
    myAccount() {
      w.calls.push("myAccount");
      maybeThrow("myAccount");
      return Promise.resolve(TOKENS[authorization] ?? null);
    },
    ownsChild(childId, ownerId) {
      w.calls.push("ownsChild");
      return Promise.resolve(w.profiles.get(childId) === ownerId);
    },
  });
  const findAccount = (childId: string, guardianId: string) => {
    const a = w.accounts.get(childId);
    return a && a.guardian_id === guardianId ? a : null;
  };
  const byUser = (userId: string) =>
    [...w.accounts.entries()].find(([, a]) => a.user_id === userId);
  const service: Service = {
    settings(keys) {
      w.calls.push(`settings:${keys.join(",")}`);
      return Promise.resolve(w.settings);
    },
    getChildAccount(childId, guardianId) {
      w.calls.push("getChildAccount");
      maybeThrow("getChildAccount");
      const a = findAccount(childId, guardianId);
      return Promise.resolve(
        a ? { user_id: a.user_id, username: a.username, disabled_at: a.disabled_at } : null,
      );
    },
    countChildAccounts(guardianId) {
      w.calls.push("countChildAccounts");
      return Promise.resolve(
        [...w.accounts.values()].filter((a) => a.guardian_id === guardianId)
          .length,
      );
    },
    usernameInUse(guardianId, username) {
      w.calls.push("usernameInUse");
      return Promise.resolve(
        [...w.accounts.values()].some((a) =>
          a.guardian_id === guardianId && a.username === username
        ),
      );
    },
    createUser(user) {
      w.calls.push("createUser");
      w.created.push(user);
      if (w.failCreate) return Promise.resolve(w.failCreate);
      // what the auth.users trigger does
      w.accounts.set(user.app_metadata.child_id, {
        user_id: KID_USER,
        username: user.app_metadata.username,
        disabled_at: null,
        guardian_id: user.app_metadata.guardian_id,
      });
      return Promise.resolve(null);
    },
    updatePassword(userId) {
      w.calls.push(`updatePassword:${userId === ACC_USER}`);
      return Promise.resolve(w.failUpdate ?? null);
    },
    setBanned(userId, banned) {
      w.calls.push(`setBanned:${banned}:${userId === ACC_USER}`);
      return Promise.resolve(w.failBan ?? null);
    },
    deleteUser(userId) {
      w.calls.push(`deleteUser:${userId === ACC_USER}`);
      if (w.failDelete) return Promise.resolve(w.failDelete);
      const hit = byUser(userId);
      if (hit) w.accounts.delete(hit[0]);
      return Promise.resolve(null);
    },
    setDisabledAt(childId, guardianId, at) {
      w.calls.push(`setDisabledAt:${at === null ? "null" : at}`);
      const a = findAccount(childId, guardianId);
      if (a) a.disabled_at = at;
      return Promise.resolve();
    },
  };
  return {
    configured,
    origins: ["https://app.ryhnfir.dev"],
    caller,
    service: () => service,
    now: () => new Date("2026-10-17T03:00:00.000Z"),
  };
}

function post(body: unknown, auth: string | null = TOKEN_G1, origin?: string): Request {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (auth !== null) headers.set("Authorization", auth);
  if (origin) headers.set("Origin", origin);
  return new Request("http://localhost/child-accounts", {
    method: "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

async function call(w: World, req: Request, configured = true) {
  let res: Response | undefined;
  const logs = await captureConsole(async () => {
    res = await handle(req, deps(w, configured));
  });
  const text = await res!.text();
  return { status: res!.status, body: text ? JSON.parse(text) : null, res: res!, logs };
}

const create = (extra: Record<string, unknown> = {}) => ({
  action: "create",
  child_id: CHILD,
  username: "Siti.Kecil",
  password: PASSWORD,
  ...extra,
});

// ---------------------------------------------------------------- transport / CORS

Deno.test("OPTIONS preflight: 204 with CORS for the allowed origin only", async () => {
  const w = world();
  const ok = await handle(
    new Request("http://localhost/x", {
      method: "OPTIONS",
      headers: { Origin: "https://app.ryhnfir.dev" },
    }),
    deps(w),
  );
  assertEquals(ok.status, 204);
  assertEquals(ok.headers.get("Access-Control-Allow-Origin"), "https://app.ryhnfir.dev");
  const bad = await handle(
    new Request("http://localhost/x", {
      method: "OPTIONS",
      headers: { Origin: "https://evil.io" },
    }),
    deps(w),
  );
  assertEquals(bad.headers.get("Access-Control-Allow-Origin"), null);
  assertEquals(w.calls, []);
});

Deno.test("CORS header on normal responses for allowed origin; none for others", async () => {
  const a = await call(world(), post(create(), TOKEN_G1, "https://app.ryhnfir.dev"));
  assertEquals(a.res.headers.get("Access-Control-Allow-Origin"), "https://app.ryhnfir.dev");
  assertEquals(a.res.headers.get("Cache-Control"), "no-store");
  const b = await call(world(), post(create(), TOKEN_G1, "https://evil.io"));
  assertEquals(b.res.headers.get("Access-Control-Allow-Origin"), null);
});

Deno.test("non-POST -> 405 method_not_allowed", async () => {
  for (const method of ["GET", "PUT", "DELETE", "PATCH"]) {
    const w = world();
    const r = await handle(new Request("http://localhost/x", { method }), deps(w));
    assertEquals(r.status, 405);
    assertEquals(await r.json(), { error: "method_not_allowed" });
    assertEquals(w.calls, []);
  }
});

Deno.test("not configured -> 500 internal (fail closed, nothing called)", async () => {
  const w = world();
  const r = await call(w, post(create()), false);
  assertEquals([r.status, r.body], [500, { error: "internal" }]);
  assertEquals(w.calls, []);
});

// ---------------------------------------------------------------- authentication / authorization

Deno.test("401 unauthorized: missing, malformed or non-user token", async () => {
  for (
    const auth of [null, "", "Bearer ", "Basic abc", "token-guardian-one", "Bearer token-anon"]
  ) {
    const r = await call(world(), post(create(), auth));
    assertEquals([r.status, r.body], [401, { error: "unauthorized" }], String(auth));
  }
});

Deno.test("403 forbidden for teen, child and kader accounts (before the body is read)", async () => {
  for (const auth of ["Bearer token-teen", "Bearer token-child", "Bearer token-kader"]) {
    const w = world();
    const r = await call(w, post("not even json", auth));
    assertEquals([r.status, r.body], [403, { error: "forbidden" }]);
    assertEquals(w.calls, ["myAccount"]);
  }
});

Deno.test("404 child_not_found: another guardian's child and an unknown child look identical", async () => {
  const actions = [
    create({ child_id: OTHER_CHILD }),
    { action: "reset_password", child_id: OTHER_CHILD, password: NEW_PASSWORD },
    { action: "set_disabled", child_id: OTHER_CHILD, disabled: true },
    { action: "delete", child_id: OTHER_CHILD },
  ];
  for (const body of actions) {
    const w1 = world();
    const theirs = await call(w1, post(body));
    const w2 = world();
    const unknown = await call(w2, post({ ...body, child_id: UNKNOWN_CHILD }));
    assertEquals([theirs.status, theirs.body], [404, { error: "child_not_found" }]);
    assertEquals([unknown.status, unknown.body], [theirs.status, theirs.body]);
    assertEquals(w1.calls, ["myAccount", "ownsChild"]); // no service call at all
    assertEquals(w2.calls, w1.calls);
  }
});

Deno.test("the other guardian can manage THEIR child (ownership is per caller)", async () => {
  const w = world();
  const r = await call(w, post(create({ child_id: OTHER_CHILD }), "Bearer token-guardian-two"));
  assertEquals(r.status, 201);
  assertEquals(w.created[0].app_metadata.guardian_id, G2);
});

// ---------------------------------------------------------------- body validation

Deno.test("400 invalid_body for malformed bodies", async () => {
  const bodies: unknown[] = [
    "not json",
    "[]",
    {},
    { action: "create" },
    { action: "explode", child_id: CHILD },
    { action: "delete", child_id: "not-a-uuid" },
    { action: "create", child_id: CHILD, username: "budi" }, // no password
    { action: "create", child_id: CHILD, password: PASSWORD }, // no username
    { action: "create", child_id: CHILD, username: 5, password: PASSWORD },
    { action: "reset_password", child_id: CHILD },
    { action: "set_disabled", child_id: CHILD, disabled: "true" },
    { action: "set_disabled", child_id: CHILD },
    JSON.stringify({ action: "delete", child_id: CHILD, pad: "x".repeat(5000) }),
  ];
  for (const b of bodies) {
    const r = await call(world(), post(b));
    assertEquals([r.status, r.body], [400, { error: "invalid_body" }], JSON.stringify(b));
  }
});

// ---------------------------------------------------------------- create

Deno.test("create: 201, synthetic e-mail + server-controlled app_metadata, nothing secret returned", async () => {
  const w = world();
  const r = await call(w, post(create()));
  assertEquals(r.status, 201);
  assertEquals(r.body, { child_id: CHILD, username: "siti.kecil", created: true });
  assertEquals(w.created, [{
    email: `child-${CHILD}@child.dentisai.invalid`,
    password: PASSWORD,
    email_confirm: true,
    app_metadata: {
      account_type: "child",
      guardian_id: G1,
      child_id: CHILD,
      username: "siti.kecil",
    },
  }]);
  const raw = JSON.stringify(r.body);
  for (const s of [PASSWORD, syntheticEmail(CHILD), KID_USER]) assertFalse(raw.includes(s));
  assertEquals(w.calls, [
    "myAccount",
    "ownsChild",
    "getChildAccount",
    "settings:max_child_accounts_per_guardian",
    "countChildAccounts",
    "usernameInUse",
    "createUser",
  ]);
});

Deno.test("create: upper-case child_id is normalised", async () => {
  const w = world();
  const r = await call(w, post(create({ child_id: CHILD.toUpperCase() })));
  assertEquals(r.status, 201);
  assertEquals(r.body.child_id, CHILD);
  assertEquals(w.created[0].email, `child-${CHILD}@child.dentisai.invalid`);
});

Deno.test("create: 422 invalid_username (shape and reserved words)", async () => {
  for (const username of ["ab", "has space", "x".repeat(21), "Admin", "root", "_lead", "ának"]) {
    const w = world();
    const r = await call(w, post(create({ username })));
    assertEquals([r.status, r.body], [422, { error: "invalid_username" }], username);
    assertEquals(w.created.length, 0);
  }
});

Deno.test("create: 422 weak_password", async () => {
  for (const password of ["short1A", "alllower123", "ALLUPPER123", "NoDigitsHere", ""]) {
    const w = world();
    const r = await call(w, post(create({ password })));
    assertEquals([r.status, r.body], [422, { error: "weak_password" }], password);
    assertEquals(w.created.length, 0);
  }
});

Deno.test("create: 409 child_already_has_account", async () => {
  const w = world();
  const r = await call(w, post(create({ child_id: CHILD_ACC })));
  assertEquals([r.status, r.body], [409, { error: "child_already_has_account" }]);
  assertEquals(w.created.length, 0);
});

Deno.test("create: 409 child_account_limit at the app_setting value (number or string)", async () => {
  for (const setting of [1, "1"]) {
    const w = world();
    w.settings = { max_child_accounts_per_guardian: setting };
    const r = await call(w, post(create()));
    assertEquals([r.status, r.body], [409, { error: "child_account_limit" }]);
    assertEquals(w.created.length, 0);
  }
});

Deno.test("create: limit defaults to 6 when the setting is missing or invalid", async () => {
  for (const settings of [{}, { max_child_accounts_per_guardian: "lots" }]) {
    const w = world();
    w.settings = settings;
    for (let i = 0; i < 4; i++) { // + CHILD_ACC = 5 existing accounts
      const id = `f${i}ffffff-ffff-4fff-8fff-ffffffffffff`;
      w.profiles.set(id, G1);
      w.accounts.set(id, { user_id: id, username: `kid${i}`, disabled_at: null, guardian_id: G1 });
    }
    assertEquals((await call(w, post(create()))).status, 201); // 6th account
    const id7 = "f9ffffff-ffff-4fff-8fff-ffffffffffff";
    w.profiles.set(id7, G1);
    const r = await call(w, post(create({ child_id: id7, username: "ketujuh" })));
    assertEquals([r.status, r.body], [409, { error: "child_account_limit" }]);
  }
});

Deno.test("create: 409 username_taken when the guardian already uses it (any case)", async () => {
  const w = world();
  const r = await call(w, post(create({ username: "BUDI" })));
  assertEquals([r.status, r.body], [409, { error: "username_taken" }]);
  assertEquals(w.created.length, 0);
});

Deno.test("create: the same username is fine for a different guardian", async () => {
  const w = world();
  const r = await call(
    w,
    post(create({ child_id: OTHER_CHILD, username: "budi" }), "Bearer token-guardian-two"),
  );
  assertEquals(r.status, 201);
});

Deno.test("create: trigger uniqueness clash (race) -> 409 username_taken", async () => {
  const w = world();
  w.failCreate = {
    status: 500,
    code: "unexpected_failure",
    message: "Database error creating new user",
  };
  const r = await call(w, post(create()));
  assertEquals([r.status, r.body], [409, { error: "username_taken" }]);
});

Deno.test("create: trigger refusal re-checked -> limit or existing account wins over username", async () => {
  const dbError = { status: 500, code: "unexpected_failure", message: "Database error" };
  // concurrent create filled the last slot
  const w = world();
  w.settings = { max_child_accounts_per_guardian: 2 };
  w.failCreate = dbError;
  const d = deps(w);
  const svc = d.service();
  const origCreate = svc.createUser.bind(svc);
  svc.createUser = async (u) => {
    const id = "f8ffffff-ffff-4fff-8fff-ffffffffffff";
    w.accounts.set(id, { user_id: id, username: "lain", disabled_at: null, guardian_id: G1 });
    return await origCreate(u);
  };
  let r: Response | undefined;
  await captureConsole(async () => {
    r = await handle(post(create()), d);
  });
  assertEquals([r!.status, await r!.json()], [409, { error: "child_account_limit" }]);
  // concurrent create for the same child
  const w2 = world();
  w2.failCreate = dbError;
  const d2 = deps(w2);
  const svc2 = d2.service();
  const orig2 = svc2.createUser.bind(svc2);
  svc2.createUser = async (u) => {
    w2.accounts.set(CHILD, {
      user_id: KID_USER,
      username: "x.y",
      disabled_at: null,
      guardian_id: G1,
    });
    return await orig2(u);
  };
  await captureConsole(async () => {
    r = await handle(post(create()), d2);
  });
  assertEquals([r!.status, await r!.json()], [409, { error: "child_already_has_account" }]);
});

Deno.test("create: Auth e-mail clash -> 409 child_already_has_account", async () => {
  for (
    const f of [
      { status: 422, code: "email_exists", message: "x" },
      { status: 422, message: "A user with this email address has already been registered" },
    ]
  ) {
    const w = world();
    w.failCreate = f;
    const r = await call(w, post(create()));
    assertEquals([r.status, r.body], [409, { error: "child_already_has_account" }]);
  }
});

Deno.test("create: GoTrue weak_password -> 422; anything else -> 500 internal", async () => {
  const w = world();
  w.failCreate = { status: 422, code: "weak_password", message: "Password should contain..." };
  assertEquals((await call(w, post(create()))).body, { error: "weak_password" });
  for (const f of [{ status: 0, message: "fetch failed" }, { status: 503 }, { status: 400 }]) {
    const w2 = world();
    w2.failCreate = f;
    const r = await call(w2, post(create()));
    assertEquals([r.status, r.body], [500, { error: "internal" }], JSON.stringify(f));
  }
});

// ---------------------------------------------------------------- reset_password

Deno.test("reset_password: 200 and the child's auth user gets the new password", async () => {
  const w = world();
  const r = await call(
    w,
    post({ action: "reset_password", child_id: CHILD_ACC, password: NEW_PASSWORD }),
  );
  assertEquals([r.status, r.body], [200, { ok: true }]);
  assertEquals(w.calls.slice(-2), ["getChildAccount", "updatePassword:true"]);
});

Deno.test("reset_password: 422 weak_password, 404 without account, 500 on Auth error", async () => {
  const weak = await call(
    world(),
    post({ action: "reset_password", child_id: CHILD_ACC, password: "weak" }),
  );
  assertEquals([weak.status, weak.body], [422, { error: "weak_password" }]);
  const none = await call(
    world(),
    post({ action: "reset_password", child_id: CHILD, password: NEW_PASSWORD }),
  );
  assertEquals([none.status, none.body], [404, { error: "child_not_found" }]);
  const w = world();
  w.failUpdate = { status: 503 };
  const err = await call(
    w,
    post({ action: "reset_password", child_id: CHILD_ACC, password: NEW_PASSWORD }),
  );
  assertEquals([err.status, err.body], [500, { error: "internal" }]);
  const w2 = world();
  w2.failUpdate = { status: 422, code: "weak_password" };
  const weak2 = await call(
    w2,
    post({ action: "reset_password", child_id: CHILD_ACC, password: NEW_PASSWORD }),
  );
  assertEquals(weak2.status, 422);
  const w3 = world();
  w3.failUpdate = { status: 404, code: "user_not_found" };
  const gone = await call(
    w3,
    post({ action: "reset_password", child_id: CHILD_ACC, password: NEW_PASSWORD }),
  );
  assertEquals([gone.status, gone.body], [404, { error: "child_not_found" }]);
});

// ---------------------------------------------------------------- set_disabled

Deno.test("set_disabled true: row first (RLS), then Auth ban", async () => {
  const w = world();
  const r = await call(w, post({ action: "set_disabled", child_id: CHILD_ACC, disabled: true }));
  assertEquals([r.status, r.body], [200, { ok: true, disabled: true }]);
  assertEquals(w.calls.slice(-2), [
    "setDisabledAt:2026-10-17T03:00:00.000Z",
    "setBanned:true:true",
  ]);
  assertEquals(w.accounts.get(CHILD_ACC)!.disabled_at, "2026-10-17T03:00:00.000Z");
});

Deno.test("set_disabled false: Auth unban first, then the row is cleared", async () => {
  const w = world();
  w.accounts.get(CHILD_ACC)!.disabled_at = "2026-10-01T00:00:00.000Z";
  const r = await call(w, post({ action: "set_disabled", child_id: CHILD_ACC, disabled: false }));
  assertEquals([r.status, r.body], [200, { ok: true, disabled: false }]);
  assertEquals(w.calls.slice(-2), ["setBanned:false:true", "setDisabledAt:null"]);
  assertEquals(w.accounts.get(CHILD_ACC)!.disabled_at, null);
});

Deno.test("set_disabled: 404 without account; Auth failure -> 500 and fail-safe state", async () => {
  const none = await call(
    world(),
    post({ action: "set_disabled", child_id: CHILD, disabled: true }),
  );
  assertEquals([none.status, none.body], [404, { error: "child_not_found" }]);

  const w = world();
  w.failBan = { status: 503 };
  const on = await call(w, post({ action: "set_disabled", child_id: CHILD_ACC, disabled: true }));
  assertEquals([on.status, on.body], [500, { error: "internal" }]);
  assert(w.accounts.get(CHILD_ACC)!.disabled_at !== null); // still blocked by RLS

  const w2 = world();
  w2.accounts.get(CHILD_ACC)!.disabled_at = "2026-10-01T00:00:00.000Z";
  w2.failBan = { status: 503 };
  const off = await call(
    w2,
    post({ action: "set_disabled", child_id: CHILD_ACC, disabled: false }),
  );
  assertEquals(off.status, 500);
  assertEquals(w2.accounts.get(CHILD_ACC)!.disabled_at, "2026-10-01T00:00:00.000Z"); // unchanged
});

// ---------------------------------------------------------------- delete

Deno.test("delete: 200, deletes the child's Auth user (account row cascades, profile stays)", async () => {
  const w = world();
  const r = await call(w, post({ action: "delete", child_id: CHILD_ACC }));
  assertEquals([r.status, r.body], [200, { ok: true }]);
  assertEquals(w.calls.slice(-1), ["deleteUser:true"]);
  assertFalse(w.accounts.has(CHILD_ACC));
  assert(w.profiles.has(CHILD_ACC));
});

Deno.test("delete: 404 without account or when Auth says not found; 500 otherwise", async () => {
  const none = await call(world(), post({ action: "delete", child_id: CHILD }));
  assertEquals([none.status, none.body], [404, { error: "child_not_found" }]);
  const w = world();
  w.failDelete = { status: 404, code: "user_not_found" };
  assertEquals((await call(w, post({ action: "delete", child_id: CHILD_ACC }))).status, 404);
  const w2 = world();
  w2.failDelete = { status: 500 };
  const r = await call(w2, post({ action: "delete", child_id: CHILD_ACC }));
  assertEquals([r.status, r.body], [500, { error: "internal" }]);
});

// ---------------------------------------------------------------- failures and logging

Deno.test("thrown DB/transport errors -> 500 internal without details", async () => {
  for (const where of ["myAccount", "getChildAccount"]) {
    const w = world();
    w.throwOn = where;
    const r = await call(w, post({ action: "delete", child_id: CHILD_ACC }));
    assertEquals([r.status, r.body], [500, { error: "internal" }]);
    assertFalse(r.logs.join("\n").includes("db down"));
  }
});

Deno.test("logging hygiene: no password, username, e-mail, token or id reaches console.*", async () => {
  const sensitive = [
    PASSWORD,
    NEW_PASSWORD,
    "siti.kecil",
    "Siti.Kecil",
    "budi",
    syntheticEmail(CHILD),
    "token-guardian-one",
    "token-teen",
    G1,
    G2,
    CHILD,
    CHILD_ACC,
    OTHER_CHILD,
    KID_USER,
    ACC_USER,
  ];
  const scenarios: [() => World, Request][] = [
    [world, post(create())],
    [world, post(create({ username: "budi" }))],
    [world, post(create({ child_id: OTHER_CHILD }))],
    [world, post(create({ password: "weak" }))],
    [
      () => ({ ...world(), failCreate: { status: 500, message: `Database error ${CHILD}` } }),
      post(create()),
    ],
    [
      () => ({ ...world(), failCreate: { status: 503, message: `upstream ${PASSWORD}` } }),
      post(create()),
    ],
    [world, post({ action: "reset_password", child_id: CHILD_ACC, password: NEW_PASSWORD })],
    [world, post({ action: "set_disabled", child_id: CHILD_ACC, disabled: true })],
    [world, post({ action: "delete", child_id: CHILD_ACC })],
    [
      () => ({ ...world(), throwOn: "getChildAccount" }),
      post({ action: "delete", child_id: CHILD_ACC }),
    ],
    [world, post(create(), "Bearer token-teen")],
    [world, post(create(), "Bearer token-anon")],
  ];
  const all: string[] = [];
  for (const [mk, req] of scenarios) all.push(...(await call(mk(), req)).logs);
  assert(all.length >= scenarios.length, "every request logs one line");
  const joined = all.join("\n");
  for (const s of sensitive) assertFalse(joined.includes(s), `leaked: ${s}`);
  for (const line of all) assert(/^child-accounts \{/.test(line), line);
});
