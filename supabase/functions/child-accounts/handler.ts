// Handler logic of child-accounts (see index.ts for the contract). Pure logic: every Supabase
// access goes through the injected Caller/Service ports (ports.ts implements them with
// supabase-js), so the whole flow is unit-tested without a network.
import { jsonResponse, preflight, readJsonObject } from "../_shared/http.ts";
import {
  isAllowedNewUsername,
  isStrongPassword,
  isUuid,
  normalizeUsername,
  settingInt,
} from "../_shared/validators.ts";

export const MAX_BODY_BYTES = 4096;
export const BAN_DURATION = "876000h"; // ~100 years = disabled until a guardian re-enables
export const SETTING_MAX_ACCOUNTS = "max_child_accounts_per_guardian";
export const DEFAULT_MAX_ACCOUNTS = 6;

/** Synthetic, undeliverable Auth e-mail of a child account (RFC 2606 .invalid TLD). */
export function syntheticEmail(childId: string): string {
  return `child-${childId.toLowerCase()}@child.dentisai.invalid`;
}

/** What my_account() tells us about the caller. */
export interface Me {
  user_id: string;
  account_type: string;
  /** user_role names (my_account().roles); a kader never manages child accounts. */
  roles?: string[];
}

/** Ports bound to the CALLER's JWT (RLS applies). */
export interface Caller {
  /** null when the token is not an authenticated user (anon key, expired, revoked). */
  myAccount(): Promise<Me | null>;
  /** True only when RLS lets the caller read the child AND the caller is its owner. */
  ownsChild(childId: string, ownerId: string): Promise<boolean>;
}

export interface ChildAccountRow {
  user_id: string;
  username: string;
  disabled_at: string | null;
}

/** Normalised Auth admin error. `message` is used for classification only, never logged. */
export interface AuthFailure {
  status?: number;
  code?: string;
  message?: string;
}

export interface NewChildUser {
  email: string;
  password: string;
  email_confirm: true;
  app_metadata: {
    account_type: "child";
    guardian_id: string;
    child_id: string;
    username: string;
  };
}

/** Ports bound to the service role. DB failures throw; Auth admin failures are returned. */
export interface Service {
  settings(keys: string[]): Promise<Record<string, unknown>>;
  getChildAccount(childId: string, guardianId: string): Promise<ChildAccountRow | null>;
  countChildAccounts(guardianId: string): Promise<number>;
  usernameInUse(guardianId: string, username: string): Promise<boolean>;
  createUser(user: NewChildUser): Promise<AuthFailure | null>;
  updatePassword(userId: string, password: string): Promise<AuthFailure | null>;
  setBanned(userId: string, banned: boolean): Promise<AuthFailure | null>;
  deleteUser(userId: string): Promise<AuthFailure | null>;
  setDisabledAt(childId: string, guardianId: string, at: string | null): Promise<void>;
}

export interface Deps {
  /** False when SUPABASE_URL / anon key / service key are missing (fail closed). */
  configured: boolean;
  origins: readonly string[];
  caller(authorization: string): Caller;
  service(): Service;
  now?: () => Date;
}

type Action = "create" | "reset_password" | "set_disabled" | "delete";
const ACTIONS: readonly Action[] = ["create", "reset_password", "set_disabled", "delete"];

/** Expected outcome: a stable error code and status. `reason` is an internal log tag only. */
class Fail extends Error {
  constructor(readonly code: string, readonly status: number, readonly reason?: string) {
    super(code);
  }
}

const invalidBody = () => new Fail("invalid_body", 400);
const notFound = () => new Fail("child_not_found", 404);
const internal = (reason: string) => new Fail("internal", 500, reason);

interface Parsed {
  action: Action;
  child_id: string;
  username?: unknown;
  password?: unknown;
  disabled?: boolean;
}

function parseBody(body: Record<string, unknown> | null): Parsed {
  if (!body) throw invalidBody();
  const action = body.action as Action;
  if (!ACTIONS.includes(action) || !isUuid(body.child_id)) throw invalidBody();
  const p: Parsed = { action, child_id: body.child_id.toLowerCase() };
  if (action === "create") {
    if (typeof body.username !== "string" || typeof body.password !== "string") {
      throw invalidBody();
    }
    p.username = body.username;
    p.password = body.password;
  } else if (action === "reset_password") {
    if (typeof body.password !== "string") throw invalidBody();
    p.password = body.password;
  } else if (action === "set_disabled") {
    if (typeof body.disabled !== "boolean") throw invalidBody();
    p.disabled = body.disabled;
  }
  return p;
}

function isWeakPasswordError(e: AuthFailure): boolean {
  return e.code === "weak_password" || (e.status === 422 && /password/i.test(e.message ?? ""));
}

/**
 * createUser failures. The auth.users trigger raises on a (guardian_id, username) clash, which
 * GoTrue reports as a 500 "Database error creating new user" -> username_taken (SQL contract).
 */
function createFailure(e: AuthFailure): Fail {
  if (isWeakPasswordError(e)) return new Fail("weak_password", 422);
  if (
    e.code === "email_exists" || e.code === "user_already_exists" ||
    /already (been )?registered|already exists/i.test(e.message ?? "")
  ) {
    return new Fail("child_already_has_account", 409);
  }
  if (
    e.status === 500 && (e.code === "unexpected_failure" || /database error/i.test(e.message ?? ""))
  ) {
    return new Fail("username_taken", 409);
  }
  return internal("auth_create_failed");
}

async function run(req: Request, deps: Deps, log: { action: string }): Promise<Response> {
  const ok = (body: unknown, status = 200) => jsonResponse(req, deps.origins, body, status);
  if (!deps.configured) throw internal("not_configured");

  // 1. Authenticate (before reading the body: anonymous callers learn nothing).
  const authorization = req.headers.get("Authorization") ?? "";
  if (!/^Bearer [^\s]+$/.test(authorization)) throw new Fail("unauthorized", 401);
  const caller = deps.caller(authorization);
  const me = await caller.myAccount();
  if (!me || !isUuid(me.user_id)) throw new Fail("unauthorized", 401);
  if (me.account_type !== "guardian" || me.roles?.includes("kader")) {
    throw new Fail("forbidden", 403);
  }
  const guardianId = me.user_id.toLowerCase();

  // 2. Body.
  const p = parseBody(await readJsonObject(req, MAX_BODY_BYTES));
  log.action = p.action;

  // 3. Ownership through RLS: unknown and someone else's child look the same.
  if (!(await caller.ownsChild(p.child_id, guardianId))) throw notFound();

  const svc = deps.service();
  switch (p.action) {
    case "create": {
      const username = normalizeUsername(p.username);
      if (!username || !isAllowedNewUsername(username)) throw new Fail("invalid_username", 422);
      if (!isStrongPassword(p.password)) throw new Fail("weak_password", 422);
      if (await svc.getChildAccount(p.child_id, guardianId)) {
        throw new Fail("child_already_has_account", 409);
      }
      const settings = await svc.settings([SETTING_MAX_ACCOUNTS]);
      const max = settingInt(settings[SETTING_MAX_ACCOUNTS], DEFAULT_MAX_ACCOUNTS, 0, 100);
      if ((await svc.countChildAccounts(guardianId)) >= max) {
        throw new Fail("child_account_limit", 409);
      }
      if (await svc.usernameInUse(guardianId, username)) throw new Fail("username_taken", 409);
      const err = await svc.createUser({
        email: syntheticEmail(p.child_id),
        password: p.password,
        email_confirm: true,
        app_metadata: {
          account_type: "child",
          guardian_id: guardianId,
          child_id: p.child_id,
          username,
        },
      });
      if (err) {
        const f = createFailure(err);
        // The trigger's error text does not reach us; re-check the two other trigger-side
        // refusals (concurrent creates) before blaming the username.
        if (f.code === "username_taken") {
          if (await svc.getChildAccount(p.child_id, guardianId)) {
            throw new Fail("child_already_has_account", 409);
          }
          if ((await svc.countChildAccounts(guardianId)) >= max) {
            throw new Fail("child_account_limit", 409);
          }
        }
        throw f;
      }
      return ok({ child_id: p.child_id, username, created: true }, 201);
    }
    case "reset_password": {
      if (!isStrongPassword(p.password)) throw new Fail("weak_password", 422);
      const acct = await svc.getChildAccount(p.child_id, guardianId);
      if (!acct) throw notFound();
      const err = await svc.updatePassword(acct.user_id, p.password);
      if (err) {
        if (isWeakPasswordError(err)) throw new Fail("weak_password", 422);
        if (err.status === 404) throw notFound();
        throw internal("auth_update_failed");
      }
      return ok({ ok: true });
    }
    case "set_disabled": {
      const acct = await svc.getChildAccount(p.child_id, guardianId);
      if (!acct) throw notFound();
      // Fail-safe ordering: disabling marks the row first (RLS blocks immediately) and then
      // bans in Auth; enabling unbans first and clears the row last.
      if (p.disabled) {
        await svc.setDisabledAt(p.child_id, guardianId, (deps.now?.() ?? new Date()).toISOString());
        if (await svc.setBanned(acct.user_id, true)) throw internal("auth_ban_failed");
      } else {
        if (await svc.setBanned(acct.user_id, false)) throw internal("auth_unban_failed");
        await svc.setDisabledAt(p.child_id, guardianId, null);
      }
      return ok({ ok: true, disabled: p.disabled });
    }
    case "delete": {
      const acct = await svc.getChildAccount(p.child_id, guardianId);
      if (!acct) throw notFound();
      const err = await svc.deleteUser(acct.user_id); // child_account cascades; profile stays
      if (err) {
        if (err.status === 404) throw notFound();
        throw internal("auth_delete_failed");
      }
      return ok({ ok: true });
    }
  }
}

export async function handle(req: Request, deps: Deps): Promise<Response> {
  if (req.method === "OPTIONS") return preflight(req, deps.origins);
  if (req.method !== "POST") {
    return jsonResponse(req, deps.origins, { error: "method_not_allowed" }, 405, {
      Allow: "POST, OPTIONS",
    });
  }
  const log = { action: "unknown" };
  try {
    const res = await run(req, deps, log);
    console.log("child-accounts", JSON.stringify({ action: log.action, status: res.status }));
    return res;
  } catch (e) {
    const f = e instanceof Fail ? e : internal("unexpected");
    // Codes only: never e-mails, usernames, passwords, tokens, ids or upstream messages.
    const line = JSON.stringify({
      action: log.action,
      status: f.status,
      code: f.code,
      ...(f.reason ? { reason: f.reason } : {}),
    });
    if (f.status >= 500) console.error("child-accounts", line);
    else console.log("child-accounts", line);
    return jsonResponse(req, deps.origins, { error: f.code }, f.status);
  }
}
