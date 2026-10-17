// child-login — a child signs in with parent e-mail + username + password (ADR 017 2.2).
//
// POST JSON, called by the app with the anon key (platform verify_jwt = true accepts it):
//   { "parent_email": str, "username": str, "password": str, "captcha_token"?: str }
// 200 { access_token, refresh_token, token_type, expires_in, expires_at, user: { id },
//       child: { username } }  -> the app calls supabase.auth.setSession(...)
// 401 { "error": "invalid_credentials" }  SAME body for unknown parent e-mail, unknown username,
//     wrong password, disabled/banned account and CAPTCHA rejected by GoTrue (anti-enumeration;
//     GoTrue is always called, with a dummy e-mail when nothing resolved; every 401 is padded to
//     a minimum duration).
// 400 invalid_body (malformed request) · 429 too_many_attempts + Retry-After (per
//     HMAC(pepper, parent e-mail | client IP), app_setting child_login_max_attempts /
//     child_login_window_s) · 502 upstream_unavailable (GoTrue 5xx/429/unreachable; not counted)
//     · 500 internal · 405 method_not_allowed.
//
// Flow: service_child_login_check -> service_resolve_child_login -> GoTrue
// POST /auth/v1/token?grant_type=password (captcha_token forwarded as gotrue_meta_security) ->
// service_child_login_record(ok) and child_account.last_login_at on success.
// Logs: status + code only (never e-mails, usernames, passwords, tokens, ids or IPs).
// Env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY (injected),
// CHILD_LOGIN_HASH_PEPPER (secret, >= 32 chars; fail closed), ALLOWED_ORIGINS.
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { parseAllowedOrigins } from "../_shared/http.ts";
import { handle } from "./handler.ts";
import { loginServicePort } from "./ports.ts";

const url = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/+$/, "");
const anonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const pepper = Deno.env.get("CHILD_LOGIN_HASH_PEPPER") ?? "";
const origins = parseAllowedOrigins(Deno.env.get("ALLOWED_ORIGINS"));

let service: SupabaseClient | null = null;

Deno.serve((req: Request) =>
  handle(req, {
    configured: url !== "" && anonKey !== "" && serviceKey !== "" && pepper.length >= 32,
    supabaseUrl: url,
    anonKey,
    pepper,
    origins,
    fetch: (input, init) => fetch(input, init),
    service: () =>
      loginServicePort(
        service ??= createClient(url, serviceKey, {
          auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
        }),
      ),
  })
);
