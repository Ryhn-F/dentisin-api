// quota-check — Supabase Free quota snapshot for the scheduled alert, service-only.
//
//   service_quota_usage() -> db_size, storage_size, mau, auth_email_24h with
//                            ok / warn / critical / unknown per metric
//
// Invoke daily (scheduler / team PC only):
//   curl -X POST "$SUPABASE_URL/functions/v1/quota-check" \
//        -H "Authorization: Bearer $CRON_SECRET"
// Always 200 when the snapshot was read; the caller (scheduled-jobs.yml) decides
// how to alert from `overall_status`. Logs and responses contain numbers only.
import { handle } from "./handler.ts";

Deno.serve((req: Request) => handle(req));
