// supabase-js implementations of the child-accounts ports (handler.ts). Thin on purpose: the
// decisions live in handler.ts. Errors carry no identifiers (generic messages only).
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import type { AuthFailure, Caller, ChildAccountRow, Me, NewChildUser, Service } from "./handler.ts";
import { BAN_DURATION } from "./handler.ts";

type PgError = { code?: string; message?: string } | null;

/**
 * PostgREST/Postgres codes meaning "this token is not an authenticated user": my_account()
 * raises 28000 `not_authenticated` for the anon key; 42501 = not granted; PGRST30x = bad JWT.
 */
const NOT_AUTHENTICATED = new Set(["28000", "42501", "PGRST301", "PGRST302"]);

export function callerPort(client: SupabaseClient): Caller {
  return {
    async myAccount(): Promise<Me | null> {
      const { data, error } = await client.rpc("my_account");
      if (error) {
        if (NOT_AUTHENTICATED.has((error as PgError)?.code ?? "")) return null;
        throw new Error("my_account_failed");
      }
      const d = data as Record<string, unknown> | null;
      if (!d || typeof d.user_id !== "string" || typeof d.account_type !== "string") return null;
      return { user_id: d.user_id, account_type: d.account_type };
    },
    async ownsChild(childId: string, ownerId: string): Promise<boolean> {
      const { data, error } = await client
        .from("child_profile")
        .select("id")
        .eq("id", childId)
        .eq("owner_id", ownerId)
        .maybeSingle();
      if (error) throw new Error("child_lookup_failed");
      return data !== null;
    },
  };
}

function authFailure(e: unknown): AuthFailure {
  const x = (e ?? {}) as { status?: unknown; code?: unknown; message?: unknown };
  return {
    status: typeof x.status === "number" ? x.status : undefined,
    code: typeof x.code === "string" ? x.code : undefined,
    message: typeof x.message === "string" ? x.message : undefined,
  };
}

export function servicePort(client: SupabaseClient): Service {
  return {
    async settings(keys: string[]): Promise<Record<string, unknown>> {
      const { data, error } = await client.from("app_setting").select("key, value").in(
        "key",
        keys,
      );
      if (error) return {}; // defaults apply
      return Object.fromEntries(
        ((data ?? []) as { key: string; value: unknown }[]).map((r) => [r.key, r.value]),
      );
    },
    async getChildAccount(childId: string, guardianId: string): Promise<ChildAccountRow | null> {
      const { data, error } = await client
        .from("child_account")
        .select("user_id, username, disabled_at")
        .eq("child_id", childId)
        .eq("guardian_id", guardianId)
        .maybeSingle();
      if (error) throw new Error("child_account_lookup_failed");
      return (data as ChildAccountRow | null) ?? null;
    },
    async countChildAccounts(guardianId: string): Promise<number> {
      const { count, error } = await client
        .from("child_account")
        .select("child_id", { count: "exact", head: true })
        .eq("guardian_id", guardianId);
      if (error || count === null) throw new Error("child_account_count_failed");
      return count;
    },
    async usernameInUse(guardianId: string, username: string): Promise<boolean> {
      const { count, error } = await client
        .from("child_account")
        .select("child_id", { count: "exact", head: true })
        .eq("guardian_id", guardianId)
        .eq("username", username);
      if (error || count === null) throw new Error("username_lookup_failed");
      return count > 0;
    },
    async createUser(user: NewChildUser): Promise<AuthFailure | null> {
      const { error } = await client.auth.admin.createUser(user);
      return error ? authFailure(error) : null;
    },
    async updatePassword(userId: string, password: string): Promise<AuthFailure | null> {
      const { error } = await client.auth.admin.updateUserById(userId, { password });
      return error ? authFailure(error) : null;
    },
    async setBanned(userId: string, banned: boolean): Promise<AuthFailure | null> {
      const { error } = await client.auth.admin.updateUserById(userId, {
        ban_duration: banned ? BAN_DURATION : "none",
      });
      return error ? authFailure(error) : null;
    },
    async deleteUser(userId: string): Promise<AuthFailure | null> {
      const { error } = await client.auth.admin.deleteUser(userId);
      return error ? authFailure(error) : null;
    },
    async setDisabledAt(childId: string, guardianId: string, at: string | null): Promise<void> {
      const { error } = await client
        .from("child_account")
        .update({ disabled_at: at })
        .eq("child_id", childId)
        .eq("guardian_id", guardianId);
      if (error) throw new Error("child_account_update_failed");
    },
  };
}
