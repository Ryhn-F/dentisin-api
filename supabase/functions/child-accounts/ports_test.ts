import { assertEquals, assertRejects } from "jsr:@std/assert@1.0.13";
import { callerPort, servicePort } from "./ports.ts";
import { fakeSupabase } from "../_shared/fake_supabase.ts";

const G = "11111111-1111-4111-8111-111111111111";
const C = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const U = "44444444-4444-4444-8444-444444444444";

Deno.test("callerPort.myAccount: parses my_account(); not-authenticated codes -> null; else throws", async () => {
  const ok = fakeSupabase(() => ({
    data: { user_id: G, account_type: "guardian", roles: ["guardian", "kader", 7], extra: 1 },
  }));
  assertEquals(await callerPort(ok.client).myAccount(), {
    user_id: G,
    account_type: "guardian",
    roles: ["guardian", "kader"],
  });
  assertEquals(ok.log, ["rpc:my_account null"]);
  for (const code of ["28000", "42501", "PGRST301"]) {
    const denied = fakeSupabase(() => ({ error: { code, message: "permission denied" } }));
    assertEquals(await callerPort(denied.client).myAccount(), null);
  }
  assertEquals(await callerPort(fakeSupabase(() => ({ data: null })).client).myAccount(), null);
  const broken = fakeSupabase(() => ({ error: { code: "XX000", message: "boom" } }));
  await assertRejects(() => callerPort(broken.client).myAccount());
});

Deno.test("callerPort.ownsChild filters by id AND owner_id through RLS", async () => {
  const f = fakeSupabase(() => ({ data: { id: C } }));
  assertEquals(await callerPort(f.client).ownsChild(C, G), true);
  assertEquals(f.log, [
    "from:child_profile",
    'select:["id"]',
    `eq:id=${C}`,
    `eq:owner_id=${G}`,
    "maybeSingle:[]",
  ]);
  assertEquals(
    await callerPort(fakeSupabase(() => ({ data: null })).client).ownsChild(C, G),
    false,
  );
  await assertRejects(() =>
    callerPort(fakeSupabase(() => ({ error: { message: "x" } })).client).ownsChild(C, G)
  );
});

Deno.test("servicePort: settings, account lookups and counts", async () => {
  const f = fakeSupabase((k) =>
    k === "from:app_setting"
      ? { data: [{ key: "max_child_accounts_per_guardian", value: 4 }] }
      : { data: { user_id: U, username: "budi", disabled_at: null }, count: 2 }
  );
  const s = servicePort(f.client);
  assertEquals(await s.settings(["max_child_accounts_per_guardian"]), {
    max_child_accounts_per_guardian: 4,
  });
  assertEquals(await s.getChildAccount(C, G), { user_id: U, username: "budi", disabled_at: null });
  assertEquals(await s.countChildAccounts(G), 2);
  assertEquals(await s.usernameInUse(G, "budi"), true);
  const text = f.log.join("\n");
  for (
    const expected of [
      "from:child_account",
      `eq:child_id=${C}`,
      `eq:guardian_id=${G}`,
      "eq:username=budi",
      'select:["child_id",{"count":"exact","head":true}]',
    ]
  ) assertEquals(text.includes(expected), true, expected);
  // settings errors fall back to defaults instead of failing the request
  assertEquals(
    await servicePort(fakeSupabase(() => ({ error: { message: "x" } })).client).settings(["a"]),
    {},
  );
  await assertRejects(() =>
    servicePort(fakeSupabase(() => ({ error: { message: "x" } })).client).countChildAccounts(G)
  );
});

Deno.test("servicePort: Auth admin calls and error normalisation", async () => {
  const f = fakeSupabase(() => ({}));
  const s = servicePort(f.client);
  assertEquals(await s.updatePassword(U, "GigiKuat99"), null);
  assertEquals(await s.setBanned(U, true), null);
  assertEquals(await s.setBanned(U, false), null);
  assertEquals(await s.deleteUser(U), null);
  await s.setDisabledAt(C, G, null);
  assertEquals(f.log.slice(0, 4), [
    `admin:updateUserById ["${U}",{"password":"GigiKuat99"}]`,
    `admin:updateUserById ["${U}",{"ban_duration":"876000h"}]`,
    `admin:updateUserById ["${U}",{"ban_duration":"none"}]`,
    `admin:deleteUser ["${U}"]`,
  ]);
  assertEquals(f.log.slice(4), [
    "from:child_account",
    'update:[{"disabled_at":null}]',
    `eq:child_id=${C}`,
    `eq:guardian_id=${G}`,
  ]);
  const err = fakeSupabase(() => ({ error: { status: 422, code: "weak_password", message: "m" } }));
  assertEquals(
    await servicePort(err.client).createUser({
      email: "e",
      password: "p",
      email_confirm: true,
      app_metadata: { account_type: "child", guardian_id: G, child_id: C, username: "u" },
    }),
    { status: 422, code: "weak_password", message: "m" },
  );
});
