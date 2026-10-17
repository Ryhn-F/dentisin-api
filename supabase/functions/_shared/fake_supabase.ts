// Test helper (not deployed: only imported by *_test.ts). A recording fake of the supabase-js
// surface used by the ports of the app-facing functions: from(...) query chains, rpc() and
// auth.admin.*. Each call is recorded as text so tests can assert table, filters and payloads.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";

export type FakeResult = { data?: unknown; error?: unknown; count?: number | null };

export interface FakeSupabase {
  client: SupabaseClient;
  /** e.g. `from:child_account`, `select:user_id, username`, `eq:child_id=...`, `rpc:name {...}` */
  log: string[];
}

/** `respond(key)` gets "from:<table>", "rpc:<name>" or "admin:<method>" and returns the result. */
export function fakeSupabase(respond: (key: string) => FakeResult = () => ({})): FakeSupabase {
  const log: string[] = [];
  const done = (key: string) => {
    const r = respond(key);
    return { data: r.data ?? null, error: r.error ?? null, count: r.count ?? null };
  };
  const chain = (table: string) => {
    // A real Promise (awaitable) carrying the builder methods, each returning itself.
    const b = Promise.resolve().then(() => done(`from:${table}`)) as
      & Promise<unknown>
      & Record<string, unknown>;
    for (const m of ["select", "eq", "in", "update", "maybeSingle"]) {
      b[m] = (...args: unknown[]) => {
        log.push(m === "eq" ? `eq:${args[0]}=${args[1]}` : `${m}:${JSON.stringify(args)}`);
        return b;
      };
    }
    return b;
  };
  const admin = (method: string) => (...args: unknown[]) => {
    log.push(`admin:${method} ${JSON.stringify(args)}`);
    const r = done(`admin:${method}`);
    return Promise.resolve({ data: r.data, error: r.error });
  };
  const fake = {
    from(table: string) {
      log.push(`from:${table}`);
      return chain(table);
    },
    rpc(name: string, args?: unknown) {
      log.push(`rpc:${name} ${JSON.stringify(args ?? null)}`);
      return Promise.resolve(done(`rpc:${name}`));
    },
    auth: {
      admin: {
        createUser: admin("createUser"),
        updateUserById: admin("updateUserById"),
        deleteUser: admin("deleteUser"),
      },
    },
  };
  return { client: fake as unknown as SupabaseClient, log };
}
