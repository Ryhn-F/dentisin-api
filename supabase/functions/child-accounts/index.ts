// child-accounts — a GUARDIAN manages the login of one of their children (ADR 017 2.2).
//
// POST JSON, Authorization: Bearer <guardian access token> (platform verify_jwt = true).
//   { "action": "create",         "child_id": uuid, "username": str, "password": str } -> 201
//       { "child_id", "username", "created": true }
//   { "action": "reset_password", "child_id": uuid, "password": str }                  -> 200 { "ok": true }
//   { "action": "set_disabled",   "child_id": uuid, "disabled": bool }                 -> 200 { "ok": true, "disabled" }
//   { "action": "delete",         "child_id": uuid }                                   -> 200 { "ok": true }
// Errors: { "error": code } — invalid_body 400, unauthorized 401, forbidden 403 (teen/child
// callers), child_not_found 404 (also: not yours / no account), username_taken 409,
// child_already_has_account 409, child_account_limit 409, weak_password 422,
// invalid_username 422, internal 500, method_not_allowed 405.
//
// The child is a Supabase Auth user with the synthetic e-mail child-<child_id>@child.dentisai.invalid
// and server-controlled app_metadata; the auth.users trigger creates the child_account row.
// Responses and logs never contain the password, the synthetic e-mail or the auth user id.
// Env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY (injected), ALLOWED_ORIGINS.
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { parseAllowedOrigins } from "../_shared/http.ts";
import { handle } from "./handler.ts";
import { callerPort, servicePort } from "./ports.ts";

const url = Deno.env.get("SUPABASE_URL") ?? "";
const anonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const origins = parseAllowedOrigins(Deno.env.get("ALLOWED_ORIGINS"));
const authOptions = { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false };

let service: SupabaseClient | null = null;

Deno.serve((req: Request) =>
  handle(req, {
    configured: url !== "" && anonKey !== "" && serviceKey !== "",
    origins,
    caller: (authorization) =>
      callerPort(
        createClient(url, anonKey, {
          auth: authOptions,
          global: { headers: { Authorization: authorization } },
        }),
      ),
    service: () => servicePort(service ??= createClient(url, serviceKey, { auth: authOptions })),
  })
);
