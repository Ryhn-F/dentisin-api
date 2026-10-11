// Handler logic of quota-check (see index.ts for the contract). Kept separate from
// the Deno.serve() call so it can be unit-tested with a mocked client.
import type { SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { json, requireService } from "../_shared/service.ts";

/** Resolves the caller into a service client, or a Response to send back (injectable for tests). */
export type ClientResolver = (req: Request) => SupabaseClient | Response;

const STATUSES = ["ok", "warn", "critical", "unknown"] as const;
type Status = typeof STATUSES[number];

export interface Metric {
  metric: string;
  unit: string;
  used: number | null;
  limit: number | null;
  pct: number | null;
  status: Status;
}

export interface Snapshot {
  overall_status: Status;
  warn_pct: number;
  critical_pct: number;
  metrics: Metric[];
}

const isStatus = (v: unknown): v is Status => STATUSES.includes(v as Status);
const isCode = (v: unknown): v is string =>
  typeof v === "string" && /^[a-z][a-z0-9_]{0,39}$/.test(v);
const numOrNull = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/**
 * Copies only the whitelisted, numeric/code fields of the RPC result, so nothing
 * unexpected (identifiers, free text) can ever reach the response or the logs.
 * Returns null when the shape is not a valid snapshot.
 */
export function sanitize(raw: unknown): Snapshot | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!isStatus(r.overall_status) || !Array.isArray(r.metrics)) return null;
  const metrics: Metric[] = [];
  for (const m of r.metrics as Record<string, unknown>[]) {
    if (!m || !isCode(m.metric) || !isCode(m.unit) || !isStatus(m.status)) return null;
    metrics.push({
      metric: m.metric,
      unit: m.unit,
      used: numOrNull(m.used),
      limit: numOrNull(m.limit),
      pct: numOrNull(m.pct),
      status: m.status,
    });
  }
  return {
    overall_status: r.overall_status,
    warn_pct: numOrNull(r.warn_pct) ?? 0,
    critical_pct: numOrNull(r.critical_pct) ?? 0,
    metrics,
  };
}

export async function handle(
  req: Request,
  resolve: ClientResolver = requireService,
): Promise<Response> {
  const client = resolve(req);
  if (client instanceof Response) return client;

  const { data, error } = await client.rpc("service_quota_usage");
  if (error) {
    console.error("quota-check: rpc_failed");
    return json({ error: "rpc_failed" }, 500);
  }
  const snap = sanitize(data);
  if (!snap) {
    console.error("quota-check: bad_snapshot");
    return json({ error: "bad_snapshot" }, 500);
  }
  console.log(
    "quota-check",
    JSON.stringify({
      overall_status: snap.overall_status,
      pct: Object.fromEntries(snap.metrics.map((m) => [m.metric, m.pct])),
    }),
  );
  return json(snap, 200);
}
