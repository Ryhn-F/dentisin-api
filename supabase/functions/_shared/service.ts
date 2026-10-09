// Shared helpers for service-only Edge Functions (process-deletion, retention-sweep).
// These functions are invoked by a scheduler with a dedicated CRON_SECRET (NOT the
// service_role key: least privilege, and the key injected into the runtime may differ
// from the one in the dashboard) and must never be callable from the app. They log COUNTS only — never ids, paths or e-mails.
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";

const encoder = new TextEncoder();

/** Constant-time string comparison. */
function safeEqual(a: string, b: string): boolean {
  const x = encoder.encode(a);
  const y = encoder.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/**
 * Fail-closed check that the caller presented CRON_SECRET (>= 32 chars) as Bearer token.
 * Returns a service client, or a Response to send back.
 */
export function requireService(req: Request): SupabaseClient | Response {
  const url = Deno.env.get("SUPABASE_URL") ?? "";
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const cron = Deno.env.get("CRON_SECRET") ?? "";
  if (!url || !key || cron.length < 32) {
    return json({ error: "not_configured" }, 503);
  }
  if (req.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }
  const auth = req.headers.get("Authorization") ?? "";
  if (!safeEqual(auth, `Bearer ${cron}`)) {
    return json({ error: "forbidden" }, 403);
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Remove objects from a bucket in chunks; returns how many were removed. */
export async function removeObjects(
  client: SupabaseClient,
  bucket: string,
  paths: string[],
): Promise<number> {
  let removed = 0;
  for (let i = 0; i < paths.length; i += 100) {
    const chunk = paths.slice(i, i + 100);
    const { data, error } = await client.storage.from(bucket).remove(chunk);
    if (error) throw new Error("storage_remove_failed");
    removed += data?.length ?? 0;
  }
  return removed;
}
