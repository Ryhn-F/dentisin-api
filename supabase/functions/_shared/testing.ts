// Test helpers (not deployed: only imported by *_test.ts). Mocks the tiny part of the
// Supabase client surface that the service-only functions use: rpc() and storage.remove().
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";

export type Result = { data: unknown; error: unknown };
export type RpcHandler = (args: Record<string, unknown> | undefined) => Result;

export interface MockOptions {
  /** Per-RPC-name handlers; unknown names fail the test loudly. */
  rpc?: Record<string, RpcHandler>;
  /** Called for each storage remove(); default removes every path. */
  remove?: (bucket: string, paths: string[]) => Result;
}

export interface MockClient {
  client: SupabaseClient;
  rpcCalls: { name: string; args: Record<string, unknown> | undefined }[];
  removeCalls: { bucket: string; paths: string[] }[];
}

export function mockClient(opts: MockOptions = {}): MockClient {
  const rpcCalls: MockClient["rpcCalls"] = [];
  const removeCalls: MockClient["removeCalls"] = [];
  const fake = {
    rpc(name: string, args?: Record<string, unknown>) {
      rpcCalls.push({ name, args });
      const h = opts.rpc?.[name];
      if (!h) throw new Error(`unexpected rpc ${name}`);
      return Promise.resolve(h(args));
    },
    storage: {
      from(bucket: string) {
        return {
          remove(paths: string[]) {
            removeCalls.push({ bucket, paths });
            const r = opts.remove?.(bucket, paths) ??
              { data: paths.map((p) => ({ name: p })), error: null };
            return Promise.resolve(r);
          },
        };
      },
    },
  };
  return { client: fake as unknown as SupabaseClient, rpcCalls, removeCalls };
}

/** Runs fn with the given env vars set (undefined = unset) and restores them afterwards. */
export async function withEnv(
  vars: Record<string, string | undefined>,
  fn: () => void | Promise<void>,
): Promise<void> {
  const saved: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(vars)) {
    saved[k] = Deno.env.get(k);
    if (v === undefined) Deno.env.delete(k);
    else Deno.env.set(k, v);
  }
  try {
    await fn();
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) Deno.env.delete(k);
      else Deno.env.set(k, v);
    }
  }
}

/**
 * Captures console.log/error/warn/info/debug output while fn runs (to assert that no
 * identifiers are logged).
 */
export async function captureConsole(
  fn: () => Promise<void>,
): Promise<string[]> {
  const lines: string[] = [];
  const methods = ["log", "error", "warn", "info", "debug"] as const;
  const saved = methods.map((m) => console[m]);
  for (const m of methods) {
    console[m] = (...a: unknown[]) => void lines.push(a.map(String).join(" "));
  }
  try {
    await fn();
  } finally {
    methods.forEach((m, i) => (console[m] = saved[i]));
  }
  return lines;
}
