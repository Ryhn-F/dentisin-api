// Input validators shared by child-accounts and child-login (ADR 017 section 2.2).
// The password policy MIRRORS supabase/config.toml [auth] (minimum_password_length,
// password_requirements); validators_test.ts reads config.toml and fails when they drift.

/** config.toml [auth] minimum_password_length. */
export const PASSWORD_MIN_LENGTH = 8;
/** config.toml [auth] password_requirements: >= 1 lowercase, >= 1 uppercase, >= 1 digit (ASCII). */
export const PASSWORD_REQUIREMENTS = "lower_upper_letters_digits";
/** bcrypt (GoTrue) only uses the first 72 bytes; longer passwords are refused, not truncated. */
export const PASSWORD_MAX_BYTES = 72;

const encoder = new TextEncoder();
// deno-lint-ignore no-control-regex
const CONTROL_RE = /[\u0000-\u001f\u007f]/;

/** True when pw satisfies the project password policy (same rules GoTrue enforces). */
export function isStrongPassword(pw: unknown): pw is string {
  if (typeof pw !== "string") return false;
  if ([...pw].length < PASSWORD_MIN_LENGTH) return false;
  if (encoder.encode(pw).length > PASSWORD_MAX_BYTES) return false;
  if (CONTROL_RE.test(pw)) return false;
  return /[a-z]/.test(pw) && /[A-Z]/.test(pw) && /[0-9]/.test(pw);
}

/** Child usernames: nickname handle, unique per guardian (ADR 017 2.2). */
export const USERNAME_RE = /^[a-z0-9][a-z0-9._-]{2,19}$/;

/** Handles that could be mistaken for staff/system accounts. Checked on create only. */
export const RESERVED_USERNAMES: ReadonlySet<string> = new Set([
  "admin",
  "administrator",
  "root",
  "system",
  "sys",
  "null",
  "nil",
  "none",
  "undefined",
  "support",
  "help",
  "info",
  "dentisai",
  "dentis",
  "service",
  "service_role",
  "anon",
  "anonymous",
  "authenticated",
  "guardian",
  "parent",
  "child",
  "teen",
  "kader",
  "staff",
  "moderator",
  "official",
  "test",
]);

/**
 * Trims and lowercases a username and checks its SHAPE. Non-ASCII input is rejected before
 * lowercasing (e.g. the Kelvin sign would otherwise fold to "k"). Returns null when invalid.
 */
export function normalizeUsername(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 64) return null;
  const trimmed = raw.trim();
  if (!/^[\x21-\x7e]+$/.test(trimmed)) return null;
  const u = trimmed.toLowerCase();
  return USERNAME_RE.test(u) ? u : null;
}

/** Shape AND not reserved: what a guardian may create. */
export function isAllowedNewUsername(username: string): boolean {
  return USERNAME_RE.test(username) && !RESERVED_USERNAMES.has(username);
}

/** Trims/lowercases an e-mail and checks a conservative shape (<= 254, local part <= 64). */
export function normalizeEmail(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 320) return null;
  const e = raw.trim().toLowerCase();
  if (e.length < 3 || e.length > 254) return null;
  const m = /^([^\s@]+)@([^\s@]+\.[^\s@]+)$/.exec(e);
  if (!m || m[1].length > 64 || CONTROL_RE.test(e)) return null;
  return e;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(v: unknown): v is string {
  return typeof v === "string" && UUID_RE.test(v);
}

/** Integer setting from app_setting.value (jsonb number or numeric string), clamped; else def. */
export function settingInt(raw: unknown, def: number, min: number, max: number): number {
  let n: number;
  if (typeof raw === "number") n = raw;
  else if (typeof raw === "string" && /^\s*-?\d+\s*$/.test(raw)) n = Number(raw);
  else return def;
  if (!Number.isInteger(n)) return def;
  return Math.min(max, Math.max(min, n));
}
