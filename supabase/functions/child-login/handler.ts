// Handler logic of child-login (see index.ts for the contract). Pure logic: the service-role
// RPCs go through the injected LoginService port (ports.ts) and GoTrue through deps.fetch, so
// every path (including the anti-enumeration guarantees) is unit-tested without a network.
import { jsonResponse, preflight, readJsonObject } from "../_shared/http.ts";
import { normalizeEmail, normalizeUsername, settingInt } from "../_shared/validators.ts";

export const MAX_BODY_BYTES = 8192;
export const MAX_PASSWORD_CHARS = 256;
export const MAX_CAPTCHA_CHARS = 4096;
export const SETTING_MAX_ATTEMPTS = "child_login_max_attempts";
export const SETTING_WINDOW_S = "child_login_window_s";
export const DEFAULT_MAX_ATTEMPTS = 8;
export const DEFAULT_WINDOW_S = 900;
/** Every 401 takes at least this long (masks the GoTrue bcrypt / user-lookup difference). */
export const DEFAULT_FAILURE_FLOOR_MS = 600;
export const DEFAULT_UPSTREAM_TIMEOUT_MS = 8000;
/**
 * Used when (parent e-mail, username) resolves to nothing, so GoTrue is ALWAYS called. Same
 * shape/length as a real child e-mail; the nil UUID is never a child_id.
 */
export const DUMMY_AUTH_EMAIL = "child-00000000-0000-0000-0000-000000000000@child.dentisai.invalid";

export interface Resolved {
  child_user_id: string;
  auth_email: string;
  disabled: boolean;
}

/** Service-role ports. All methods throw on failure (no identifiers in the error). */
export interface LoginService {
  settings(keys: string[]): Promise<Record<string, unknown>>;
  check(keyHash: string, max: number, windowS: number): Promise<boolean>;
  record(keyHash: string, ok: boolean, windowS: number): Promise<void>;
  resolve(parentEmail: string, username: string): Promise<Resolved | null>;
  touchLastLogin(childUserId: string): Promise<void>;
}

export interface LoginDeps {
  /** False when SUPABASE_URL / keys / CHILD_LOGIN_HASH_PEPPER (>= 32 chars) are missing. */
  configured: boolean;
  supabaseUrl: string;
  anonKey: string;
  pepper: string;
  origins: readonly string[];
  service(): LoginService;
  fetch: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  failureFloorMs?: number;
  upstreamTimeoutMs?: number;
}

const encoder = new TextEncoder();

/** HMAC-SHA256(pepper, message) as lowercase hex. */
export async function hmacHex(pepper: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

const IP_RE = /^[0-9a-f.:]{2,45}$/i;

/**
 * Client IP for the throttle key. cf-connecting-ip is preferred because Cloudflare (in front of
 * Supabase) overwrites it, whereas the first x-forwarded-for hop can be supplied by the client.
 */
export function clientIp(req: Request): string {
  const cf = req.headers.get("cf-connecting-ip")?.trim() ?? "";
  if (IP_RE.test(cf)) return cf.toLowerCase();
  const xff = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim();
  if (IP_RE.test(xff)) return xff.toLowerCase();
  return "unknown";
}

interface Input {
  email: string;
  username: string;
  password: string;
  captcha?: string;
}

function parseInput(body: Record<string, unknown> | null): Input | null {
  if (!body) return null;
  const email = normalizeEmail(body.parent_email);
  const username = normalizeUsername(body.username);
  const password = body.password;
  if (!email || !username) return null;
  if (typeof password !== "string" || password.length === 0) return null;
  if (password.length > MAX_PASSWORD_CHARS) return null;
  const captcha = body.captcha_token;
  if (captcha !== undefined && captcha !== null) {
    if (typeof captcha !== "string" || captcha.length === 0 || captcha.length > MAX_CAPTCHA_CHARS) {
      return null;
    }
    return { email, username, password, captcha };
  }
  return { email, username, password };
}

type Session = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  expires_at: number;
  user_id: string;
};

type GrantOutcome = { kind: "session"; session: Session } | { kind: "rejected" } | {
  kind: "upstream";
};

async function passwordGrant(deps: LoginDeps, email: string, input: Input): Promise<GrantOutcome> {
  const body: Record<string, unknown> = { email, password: input.password };
  if (input.captcha) body.gotrue_meta_security = { captcha_token: input.captcha };
  let res: Response;
  try {
    res = await deps.fetch(`${deps.supabaseUrl}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { "apikey": deps.anonKey, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: timeoutSignal(deps),
    });
  } catch {
    return { kind: "upstream" };
  }
  // 5xx = GoTrue down; 429 = the shared per-IP Auth limit of the FUNCTION's IP (ADR 017 2.2):
  // neither is the child's fault, so neither counts as a failed attempt.
  if (res.status >= 500 || res.status === 429) {
    await res.body?.cancel();
    return { kind: "upstream" };
  }
  const data = await res.json().catch(() => null) as Record<string, unknown> | null;
  if (!res.ok) return { kind: "rejected" };
  const user = data?.user as Record<string, unknown> | undefined;
  if (
    !data || typeof data.access_token !== "string" || typeof data.refresh_token !== "string" ||
    typeof user?.id !== "string"
  ) {
    return { kind: "rejected" };
  }
  const expiresIn = typeof data.expires_in === "number" ? data.expires_in : 3600;
  const nowS = Math.floor((deps.now?.() ?? Date.now()) / 1000);
  return {
    kind: "session",
    session: {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      token_type: typeof data.token_type === "string" ? data.token_type : "bearer",
      expires_in: expiresIn,
      expires_at: typeof data.expires_at === "number" ? data.expires_at : nowS + expiresIn,
      user_id: user.id,
    },
  };
}

/** Upstream timeout; upstreamTimeoutMs = 0 disables it (tests). */
function timeoutSignal(deps: LoginDeps): AbortSignal | undefined {
  const ms = deps.upstreamTimeoutMs ?? DEFAULT_UPSTREAM_TIMEOUT_MS;
  return ms > 0 ? AbortSignal.timeout(ms) : undefined;
}

/** Best effort: revoke a session we refuse to hand out (e.g. disabled but not yet banned). */
async function revoke(deps: LoginDeps, accessToken: string): Promise<void> {
  try {
    const res = await deps.fetch(`${deps.supabaseUrl}/auth/v1/logout?scope=local`, {
      method: "POST",
      headers: { "apikey": deps.anonKey, "Authorization": `Bearer ${accessToken}` },
      signal: timeoutSignal(deps),
    });
    await res.body?.cancel();
  } catch {
    // ignored: the RLS check on child_account.disabled_at still applies
  }
}

function log(status: number, code: string, reason?: string) {
  const line = JSON.stringify({ status, code, ...(reason ? { reason } : {}) });
  if (status >= 500) console.error("child-login", line);
  else console.log("child-login", line);
}

export async function handle(req: Request, deps: LoginDeps): Promise<Response> {
  const origins = deps.origins;
  if (req.method === "OPTIONS") return preflight(req, origins);
  const respond = (body: unknown, status: number, extra?: Record<string, string>) =>
    jsonResponse(req, origins, body, status, extra);
  if (req.method !== "POST") {
    return respond({ error: "method_not_allowed" }, 405, { Allow: "POST, OPTIONS" });
  }
  const started = deps.now?.() ?? Date.now();
  const fail = (code: string, status: number, reason?: string) => {
    log(status, code, reason);
    return respond({ error: code }, status);
  };

  if (!deps.configured) return fail("internal", 500, "not_configured");

  const input = parseInput(await readJsonObject(req, MAX_BODY_BYTES));
  if (!input) return fail("invalid_body", 400);

  let svc: LoginService;
  let key: string;
  let windowS = DEFAULT_WINDOW_S;
  try {
    svc = deps.service();
    key = await hmacHex(deps.pepper, `${input.email}|${clientIp(req)}`);
    const settings = await svc.settings([SETTING_MAX_ATTEMPTS, SETTING_WINDOW_S]).catch(() => ({}));
    const s = settings as Record<string, unknown>;
    const max = settingInt(s[SETTING_MAX_ATTEMPTS], DEFAULT_MAX_ATTEMPTS, 1, 1000);
    windowS = settingInt(s[SETTING_WINDOW_S], DEFAULT_WINDOW_S, 60, 86400);
    if (!(await svc.check(key, max, windowS))) {
      log(429, "too_many_attempts");
      return respond({ error: "too_many_attempts" }, 429, { "Retry-After": String(windowS) });
    }
  } catch {
    return fail("internal", 500, "throttle_failed");
  }

  let resolved: Resolved | null;
  try {
    resolved = await svc.resolve(input.email, input.username);
  } catch {
    return fail("internal", 500, "resolve_failed");
  }

  // ALWAYS call GoTrue (dummy e-mail when unresolved) so unknown users cost a comparable call.
  const outcome = await passwordGrant(deps, resolved?.auth_email ?? DUMMY_AUTH_EMAIL, input);
  if (outcome.kind === "upstream") return fail("upstream_unavailable", 502);

  const success = outcome.kind === "session" && resolved !== null && !resolved.disabled &&
    outcome.session.user_id === resolved.child_user_id;

  if (!success || outcome.kind !== "session" || !resolved) {
    if (outcome.kind === "session") await revoke(deps, outcome.session.access_token);
    try {
      await svc.record(key, false, windowS);
    } catch {
      log(401, "invalid_credentials", "record_failed");
    }
    const elapsed = (deps.now?.() ?? Date.now()) - started;
    const floor = deps.failureFloorMs ?? DEFAULT_FAILURE_FLOOR_MS;
    if (elapsed < floor) {
      await (deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms))))(floor - elapsed);
    }
    log(401, "invalid_credentials");
    return respond({ error: "invalid_credentials" }, 401);
  }

  try {
    await svc.record(key, true, windowS);
  } catch {
    log(200, "ok", "record_failed");
  }
  try {
    await svc.touchLastLogin(resolved.child_user_id);
  } catch {
    log(200, "ok", "last_login_failed");
  }
  const s = outcome.session;
  log(200, "ok");
  return respond({
    access_token: s.access_token,
    refresh_token: s.refresh_token,
    token_type: s.token_type,
    expires_in: s.expires_in,
    expires_at: s.expires_at,
    user: { id: s.user_id },
    child: { username: input.username },
  }, 200);
}
