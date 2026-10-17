import { assertEquals, assertRejects } from "jsr:@std/assert@1.0.13";
import { loginServicePort } from "./ports.ts";
import { fakeSupabase } from "../_shared/fake_supabase.ts";

const U = "44444444-4444-4444-8444-444444444444";
const E = "child-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa@child.dentisai.invalid";

Deno.test("check/record call the service RPCs with the contract parameter names", async () => {
  const f = fakeSupabase((k) => (k === "rpc:service_child_login_check" ? { data: true } : {}));
  const s = loginServicePort(f.client);
  assertEquals(await s.check("ab12", 8, 900), true);
  await s.record("ab12", false, 900);
  assertEquals(f.log, [
    'rpc:service_child_login_check {"p_key_hash":"ab12","p_max":8,"p_window_s":900}',
    'rpc:service_child_login_record {"p_key_hash":"ab12","p_ok":false,"p_window_s":900}',
  ]);
  // non-boolean or error -> throws (the handler answers 500, never "allowed")
  await assertRejects(() =>
    loginServicePort(fakeSupabase(() => ({ data: null })).client).check("k", 1, 60)
  );
  await assertRejects(() =>
    loginServicePort(fakeSupabase(() => ({ error: { message: "x" } })).client).record("k", true, 60)
  );
});

Deno.test("resolve: one row -> Resolved; zero or many rows -> null; disabled fails closed", async () => {
  const one = fakeSupabase(() => ({
    data: [{ child_user_id: U, auth_email: E, disabled: false }],
  }));
  assertEquals(await loginServicePort(one.client).resolve("a@b.cd", "budi"), {
    child_user_id: U,
    auth_email: E,
    disabled: false,
  });
  assertEquals(one.log, [
    'rpc:service_resolve_child_login {"p_parent_email":"a@b.cd","p_username":"budi"}',
  ]);
  assertEquals(
    await loginServicePort(fakeSupabase(() => ({ data: [] })).client).resolve("a", "b"),
    null,
  );
  const two = fakeSupabase(() => ({
    data: [{ child_user_id: U, auth_email: E, disabled: false }, {
      child_user_id: U,
      auth_email: E,
      disabled: false,
    }],
  }));
  assertEquals(await loginServicePort(two.client).resolve("a", "b"), null);
  const nullDisabled = fakeSupabase(() => ({
    data: [{ child_user_id: U, auth_email: E, disabled: null }],
  }));
  assertEquals((await loginServicePort(nullDisabled.client).resolve("a", "b"))?.disabled, true);
  await assertRejects(() =>
    loginServicePort(fakeSupabase(() => ({ error: { message: "x" } })).client).resolve("a", "b")
  );
});

Deno.test("touchLastLogin updates child_account.last_login_at by user_id", async () => {
  const f = fakeSupabase(() => ({}));
  await loginServicePort(f.client, () => new Date("2026-10-17T00:00:00.000Z")).touchLastLogin(U);
  assertEquals(f.log, [
    "from:child_account",
    'update:[{"last_login_at":"2026-10-17T00:00:00.000Z"}]',
    `eq:user_id=${U}`,
  ]);
});

Deno.test("settings: rows -> map; error -> {} (defaults apply)", async () => {
  const f = fakeSupabase(() => ({ data: [{ key: "child_login_window_s", value: 600 }] }));
  assertEquals(await loginServicePort(f.client).settings(["child_login_window_s"]), {
    child_login_window_s: 600,
  });
  assertEquals(
    await loginServicePort(fakeSupabase(() => ({ error: { message: "x" } })).client).settings([]),
    {},
  );
});
