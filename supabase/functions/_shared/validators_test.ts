import { assert, assertEquals, assertFalse } from "jsr:@std/assert@1.0.13";
import {
  isAllowedNewUsername,
  isStrongPassword,
  isUuid,
  normalizeEmail,
  normalizeUsername,
  PASSWORD_MAX_BYTES,
  PASSWORD_MIN_LENGTH,
  PASSWORD_REQUIREMENTS,
  RESERVED_USERNAMES,
  settingInt,
} from "./validators.ts";

Deno.test("password policy mirrors supabase/config.toml [auth]", async () => {
  const toml = await Deno.readTextFile(new URL("../../config.toml", import.meta.url));
  const auth = toml.split(/^\[auth\]\s*$/m)[1].split(/^\[/m)[0];
  const min = /^minimum_password_length\s*=\s*(\d+)/m.exec(auth);
  const req = /^password_requirements\s*=\s*"([^"]*)"/m.exec(auth);
  assert(min && req, "config.toml [auth] must define both settings");
  assertEquals(Number(min[1]), PASSWORD_MIN_LENGTH);
  assertEquals(req[1], PASSWORD_REQUIREMENTS);
});

Deno.test("isStrongPassword: accepts the policy minimum", () => {
  assert(isStrongPassword("Abcdefg1"));
  assert(isStrongPassword("Gigi Bersih 2026"));
  assert(isStrongPassword("1aA" + "x".repeat(PASSWORD_MAX_BYTES - 3)));
});

Deno.test("isStrongPassword: rejects short, missing classes, too long, control chars, non-string", () => {
  const bad: unknown[] = [
    "Abcdef1", // 7
    "abcdefg1", // no upper
    "ABCDEFG1", // no lower
    "Abcdefgh", // no digit
    "1aA" + "x".repeat(PASSWORD_MAX_BYTES - 2), // 73 bytes
    "Abcdefg1\n",
    "Abc\u0000defg1",
    "ÄÖÜäöü123", // non-ASCII letters do not count as lower/upper (GoTrue uses ASCII sets)
    "",
    null,
    undefined,
    12345678,
    ["Abcdefg1"],
  ];
  for (const p of bad) assertFalse(isStrongPassword(p), JSON.stringify(p));
});

Deno.test("isStrongPassword: multi-byte characters count against the 72-byte bcrypt limit", () => {
  assertFalse(isStrongPassword("Aa1" + "é".repeat(35))); // 3 + 70 bytes
  assert(isStrongPassword("Aa1" + "é".repeat(34))); // 3 + 68 bytes
});

Deno.test("normalizeUsername: trims, lowercases, enforces the shape", () => {
  assertEquals(normalizeUsername("  Budi.Kecil_01 "), "budi.kecil_01");
  assertEquals(normalizeUsername("abc"), "abc");
  assertEquals(normalizeUsername("a".repeat(20)), "a".repeat(20));
  assertEquals(normalizeUsername("9-lives"), "9-lives");
  const bad: unknown[] = [
    "ab", // too short
    "a".repeat(21),
    ".abc", // must start with a letter/digit
    "_abc",
    "-abc",
    "ab c",
    "abc@x",
    "abc/def",
    "Kid", // Kelvin sign would fold to "kid"
    "ának",
    "",
    "   ",
    "a".repeat(65),
    null,
    42,
  ];
  for (const u of bad) assertEquals(normalizeUsername(u), null, JSON.stringify(u));
});

Deno.test("isAllowedNewUsername: reserved words are refused", () => {
  for (const r of ["admin", "root", "system", "null", "dentisai", "service_role"]) {
    assert(RESERVED_USERNAMES.has(r));
    assertFalse(isAllowedNewUsername(r));
  }
  assertFalse(isAllowedNewUsername(normalizeUsername("ADMIN")!));
  assert(isAllowedNewUsername("admin2"));
  assert(isAllowedNewUsername("budi"));
  assertFalse(isAllowedNewUsername("ab"));
});

Deno.test("normalizeEmail: trims, lowercases, conservative shape", () => {
  assertEquals(normalizeEmail("  Ibu.Siti@Example.CO.ID "), "ibu.siti@example.co.id");
  const bad: unknown[] = [
    "no-at",
    "a@b",
    "a@@b.c",
    "a b@c.d",
    "@b.cd",
    `${"a".repeat(65)}@b.cd`,
    `a@${"b".repeat(250)}.cd`,
    "a\u0001@b.cd",
    "",
    null,
    1,
  ];
  for (const e of bad) assertEquals(normalizeEmail(e), null, JSON.stringify(e));
});

Deno.test("isUuid", () => {
  assert(isUuid("5eed0000-0000-4000-8000-000000000001"));
  assert(isUuid("5EED0000-0000-4000-8000-000000000001"));
  assertFalse(isUuid("5eed0000-0000-4000-8000-00000000000"));
  assertFalse(isUuid("not-a-uuid"));
  assertFalse(isUuid(null));
});

Deno.test("settingInt: numbers, numeric strings, clamping, fallback", () => {
  assertEquals(settingInt(6, 1, 0, 100), 6);
  assertEquals(settingInt("8", 1, 0, 100), 8);
  assertEquals(settingInt(1000, 1, 0, 100), 100);
  assertEquals(settingInt(-5, 1, 0, 100), 0);
  assertEquals(settingInt(2.5, 7, 0, 100), 7);
  assertEquals(settingInt("x", 7, 0, 100), 7);
  assertEquals(settingInt(null, 7, 0, 100), 7);
  assertEquals(settingInt({ v: 1 }, 7, 0, 100), 7);
});
