// supabase-js (service role) implementation of the child-login LoginService port.
// Thin on purpose: the decisions live in handler.ts. Errors carry no identifiers.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import type { LoginService, Resolved } from "./handler.ts";

export function loginServicePort(client: SupabaseClient, now = () => new Date()): LoginService {
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
    async check(keyHash: string, max: number, windowS: number): Promise<boolean> {
      const { data, error } = await client.rpc("service_child_login_check", {
        p_key_hash: keyHash,
        p_max: max,
        p_window_s: windowS,
      });
      if (error || typeof data !== "boolean") throw new Error("check_failed");
      return data;
    },
    async record(keyHash: string, ok: boolean, windowS: number): Promise<void> {
      const { error } = await client.rpc("service_child_login_record", {
        p_key_hash: keyHash,
        p_ok: ok,
        p_window_s: windowS,
      });
      if (error) throw new Error("record_failed");
    },
    async resolve(parentEmail: string, username: string): Promise<Resolved | null> {
      const { data, error } = await client.rpc("service_resolve_child_login", {
        p_parent_email: parentEmail,
        p_username: username,
      });
      if (error) throw new Error("resolve_failed");
      const rows = (Array.isArray(data) ? data : data ? [data] : []) as Record<string, unknown>[];
      if (rows.length !== 1) return null; // 0 = unknown; > 1 would be a broken invariant
      const r = rows[0];
      if (typeof r.child_user_id !== "string" || typeof r.auth_email !== "string") return null;
      return {
        child_user_id: r.child_user_id,
        auth_email: r.auth_email,
        disabled: r.disabled !== false, // fail closed: anything but an explicit false = disabled
      };
    },
    async touchLastLogin(childUserId: string): Promise<void> {
      const { error } = await client
        .from("child_account")
        .update({ last_login_at: now().toISOString() })
        .eq("user_id", childUserId);
      if (error) throw new Error("last_login_failed");
    },
  };
}
