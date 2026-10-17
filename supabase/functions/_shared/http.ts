// Shared HTTP helpers for the APP-FACING Edge Functions (child-accounts, child-login):
// CORS allowlist, JSON responses that are never cached, bounded JSON body parsing.
// React Native clients send no Origin header and need no CORS; browsers (Expo web) only get
// Access-Control-Allow-Origin for origins listed in ALLOWED_ORIGINS. There is never a wildcard.

/** Used when ALLOWED_ORIGINS is unset or empty. */
export const DEFAULT_ALLOWED_ORIGINS: readonly string[] = ["https://app.ryhnfir.dev"];

const ORIGIN_RE = /^https?:\/\/[a-z0-9.-]+(:\d{1,5})?$/;

/**
 * Parses ALLOWED_ORIGINS (comma-separated). Unset/empty -> the default list. "*" and anything
 * that is not a bare scheme://host[:port] origin are ignored (never a wildcard).
 */
export function parseAllowedOrigins(raw: string | undefined): string[] {
  if (raw === undefined || raw.trim() === "") return [...DEFAULT_ALLOWED_ORIGINS];
  return raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => ORIGIN_RE.test(s));
}

/** CORS headers for this request: the allow-* headers only when the Origin is allowlisted. */
export function corsHeaders(req: Request, origins: readonly string[]): Record<string, string> {
  const headers: Record<string, string> = { "Vary": "Origin" };
  const origin = req.headers.get("Origin");
  if (origin && origins.includes(origin.toLowerCase())) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Methods"] = "POST, OPTIONS";
    headers["Access-Control-Allow-Headers"] = "authorization, x-client-info, apikey, content-type";
    headers["Access-Control-Max-Age"] = "600";
  }
  return headers;
}

/** JSON response with CORS headers and `Cache-Control: no-store` (sessions must never be cached). */
export function jsonResponse(
  req: Request,
  origins: readonly string[],
  body: unknown,
  status: number,
  extra: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(req, origins),
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...extra,
    },
  });
}

/** Answer to an OPTIONS preflight (204; allow-* headers only for allowlisted origins). */
export function preflight(req: Request, origins: readonly string[]): Response {
  return new Response(null, { status: 204, headers: corsHeaders(req, origins) });
}

const encoder = new TextEncoder();

/**
 * Reads the body as a JSON object of at most maxBytes (UTF-8). Returns null for an oversized,
 * empty, non-JSON or non-object body (arrays and primitives are rejected).
 */
export async function readJsonObject(
  req: Request,
  maxBytes: number,
): Promise<Record<string, unknown> | null> {
  const declared = Number(req.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(declared) && declared > maxBytes) return null;
  let text: string;
  try {
    text = await req.text();
  } catch {
    return null;
  }
  if (text === "" || encoder.encode(text).length > maxBytes) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}
