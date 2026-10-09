#!/usr/bin/env bash
# backup_db.sh - encrypted logical backup of the Supabase Postgres database.
#
#   pg_dump (custom format) | age --encrypt -r <PUBLIC key>  ->  $BACKUP_OUTPUT_DIR/dentisai-<UTC>.dump.age
#
# Supabase Free has NO automatic backups (ADR 008), so this is the only one. See
# tools/restore_check.md for the restore test (do it monthly, on a scratch DB, never on prod).
#
# Usage:  bash tools/backup_db.sh [--keep N] [--schemas a,b,c] [--check-size] [--env-file FILE]
#   --keep N        after a successful backup, delete all but the newest N backups
#   --schemas LIST  schemas to dump (default: public,auth,storage)
#   --check-size    only print the database size vs the 500 MB Free limit, then exit
#   --env-file F    read missing variables from F (default: <backend>/.env; values never printed)
#
# Environment (never printed, never written to files):
#   SUPABASE_DB_URL       postgres connection string (pg_dump/psql). NOTE: it is visible in the
#                         process list while pg_dump runs - run this on a personal machine only.
#   BACKUP_AGE_RECIPIENT  age PUBLIC key (age1...). The private key is never handled here.
#   BACKUP_OUTPUT_DIR     existing directory OUTSIDE the repo (ideally an encrypted disk).
#
# Exit codes: 0 ok | 2 usage/config | 3 required tool missing | 4 unsafe output directory
#             5 dump/encryption failed | 6 output verification failed
#
# The backup contains children's health data: keep it off the repo, off public CI artifacts,
# and apply a retention limit (--keep) so deleted accounts also disappear from old backups.

set -euo pipefail
set +x
umask 077

DB_LIMIT_MB=500
WARN_MB=400
CRIT_MB=475

log() { printf 'backup_db: %s\n' "$*" >&2; }
die() { local code=$1; shift; log "ERROR: $*"; exit "$code"; }

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
BACKEND_ROOT=$(cd "$SCRIPT_DIR/.." && pwd -P)

KEEP=""
SCHEMAS="public,auth,storage"
CHECK_SIZE_ONLY=0
ENV_FILE="$BACKEND_ROOT/.env"

while [ $# -gt 0 ]; do
  case "$1" in
    --keep) [ $# -ge 2 ] || die 2 "--keep needs a number"; KEEP=$2; shift 2 ;;
    --schemas) [ $# -ge 2 ] || die 2 "--schemas needs a list"; SCHEMAS=$2; shift 2 ;;
    --check-size) CHECK_SIZE_ONLY=1; shift ;;
    --env-file) [ $# -ge 2 ] || die 2 "--env-file needs a path"; ENV_FILE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die 2 "unknown argument: $1" ;;
  esac
done

if [ -n "$KEEP" ]; then
  case "$KEEP" in
    ''|*[!0-9]*) die 2 "--keep must be a positive integer" ;;
  esac
  [ "$KEEP" -ge 1 ] || die 2 "--keep must be at least 1"
fi
case "$SCHEMAS" in
  ''|*[!A-Za-z0-9_,]*) die 2 "--schemas may only contain letters, digits, _ and commas" ;;
esac

# Read a variable from the env file only if it is not already set (value is never printed).
load_from_env_file() {
  local name=$1 line val
  if [ -n "${!name:-}" ]; then return 0; fi
  [ -f "$ENV_FILE" ] || return 0
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$ENV_FILE" | tail -n 1 || true)
  [ -n "$line" ] || return 0
  val=${line#*=}
  val=${val%$'\r'}
  case $val in
    \"*\") val=${val#\"}; val=${val%\"} ;;
    \'*\') val=${val#\'}; val=${val%\'} ;;
  esac
  if [ -n "$val" ]; then
    printf -v "$name" '%s' "$val"
    export "${name?}"
  fi
}

need_vars=(SUPABASE_DB_URL)
if [ "$CHECK_SIZE_ONLY" -eq 0 ]; then need_vars+=(BACKUP_AGE_RECIPIENT BACKUP_OUTPUT_DIR); fi
for v in "${need_vars[@]}"; do load_from_env_file "$v"; done
missing=()
for v in "${need_vars[@]}"; do
  [ -n "${!v:-}" ] || missing+=("$v")
done
[ ${#missing[@]} -eq 0 ] || die 2 "missing environment variable(s): ${missing[*]} (set them or fill the root .env)"

size_check() {
  if ! command -v psql >/dev/null 2>&1; then
    log "WARN: psql not found; skipping the database size check."
    return 0
  fi
  local bytes mb
  bytes=$(psql "$SUPABASE_DB_URL" --no-psqlrc -XAtq \
    -c "select pg_database_size(current_database())" 2>/dev/null) || {
    log "WARN: size check failed (cannot connect?)."
    return 0
  }
  if ! [[ $bytes =~ ^[0-9]+$ ]]; then
    log "WARN: size check returned an unexpected value."
    return 0
  fi
  mb=$((bytes / 1024 / 1024))
  if [ "$mb" -ge "$CRIT_MB" ]; then
    log "CRITICAL: database is ${mb} MB of the ${DB_LIMIT_MB} MB Free limit. Writes may be blocked soon; prune data or upgrade."
  elif [ "$mb" -ge "$WARN_MB" ]; then
    log "WARNING: database is ${mb} MB of the ${DB_LIMIT_MB} MB Free limit (>= ${WARN_MB} MB)."
  else
    log "database size ${mb} MB of ${DB_LIMIT_MB} MB (ok)."
  fi
}

if [ "$CHECK_SIZE_ONLY" -eq 1 ]; then
  size_check
  exit 0
fi

# --- tools -----------------------------------------------------------------------------
command -v pg_dump >/dev/null 2>&1 || die 3 "pg_dump not found (install PostgreSQL client tools; version must be >= the server's)."
command -v age >/dev/null 2>&1 || die 3 "age not found (https://github.com/FiloSottile/age). Install it first."

# --- recipient: public key only ---------------------------------------------------------
case "$BACKUP_AGE_RECIPIENT" in
  AGE-SECRET-KEY-*) die 2 "BACKUP_AGE_RECIPIENT contains a PRIVATE key. Use the public key (age1...) and rotate this key." ;;
  age1*) ;;
  *) die 2 "BACKUP_AGE_RECIPIENT must be an age public key starting with 'age1'." ;;
esac

# --- output directory: must exist and lie outside the repository ------------------------
normalize_dir() {
  local p=$1
  if command -v cygpath >/dev/null 2>&1; then p=$(cygpath -u "$p"); fi
  ( cd "$p" 2>/dev/null && pwd -P )
}
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

OUT_DIR=$(normalize_dir "$BACKUP_OUTPUT_DIR") || die 4 "BACKUP_OUTPUT_DIR does not exist or is not a directory (create it first)."
CMP_OUT=$OUT_DIR
IS_WINDOWS_SHELL=0
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) IS_WINDOWS_SHELL=1; CMP_OUT=$(lower "$OUT_DIR") ;;
esac

# Protected roots: BACKEND_ROOT, the enclosing git work tree (if any), and a monorepo root
# (parent holding CLAUDE.md). Backups of children's health data must never land in any of them.
protected=("$BACKEND_ROOT")
probe=$(dirname "$BACKEND_ROOT")
while [ "$probe" != "/" ] && [ "$probe" != "." ]; do
  if [ -e "$probe/.git" ]; then protected+=("$probe"); break; fi
  next=$(dirname "$probe")
  [ "$next" != "$probe" ] || break
  probe=$next
done
if [ -f "$(dirname "$BACKEND_ROOT")/CLAUDE.md" ]; then protected+=("$(dirname "$BACKEND_ROOT")"); fi
for p in "${protected[@]}"; do
  cmp_p=$p
  if [ "$IS_WINDOWS_SHELL" -eq 1 ]; then cmp_p=$(lower "$p"); fi
  if [ "$CMP_OUT" = "$cmp_p" ] || [[ "$CMP_OUT/" == "$cmp_p/"* ]]; then
    die 4 "BACKUP_OUTPUT_DIR is inside the backend/repository tree. Choose a directory outside it."
  fi
done
[ -w "$OUT_DIR" ] || die 4 "BACKUP_OUTPUT_DIR is not writable."

size_check

# --- dump + encrypt ---------------------------------------------------------------------
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FINAL="$OUT_DIR/dentisai-$STAMP.dump.age"
PARTIAL="$FINAL.partial"
trap 'rm -f -- "$PARTIAL"' EXIT

schema_args=()
IFS=',' read -r -a schema_list <<< "$SCHEMAS"
for s in "${schema_list[@]}"; do
  if [ -n "$s" ]; then schema_args+=("--schema=$s"); fi
done

[ ${#schema_args[@]} -gt 0 ] || die 2 "--schemas is empty"
log "dumping schemas: $SCHEMAS (output is encrypted before it touches disk)"
if ! pg_dump --dbname="$SUPABASE_DB_URL" --format=custom --no-owner --no-privileges \
      "${schema_args[@]}" | age --encrypt -r "$BACKUP_AGE_RECIPIENT" -o "$PARTIAL"; then
  die 5 "pg_dump or age failed; no backup was written."
fi

# --- verify the output (cannot fully decrypt: we never hold the private key) -------------
[ -s "$PARTIAL" ] || die 6 "encrypted output is empty."
header=$(head -c 21 "$PARTIAL" | tr -d '\0')
[ "$header" = "age-encryption.org/v1" ] || die 6 "output does not start with an age header."
bytes=$(wc -c < "$PARTIAL" | tr -d ' ')
[ "$bytes" -gt 200 ] || die 6 "encrypted output is suspiciously small (${bytes} bytes)."

mv -- "$PARTIAL" "$FINAL"

if command -v sha256sum >/dev/null 2>&1; then
  ( cd "$OUT_DIR" && sha256sum -- "$(basename "$FINAL")" > "$(basename "$FINAL").sha256" )
elif command -v shasum >/dev/null 2>&1; then
  ( cd "$OUT_DIR" && shasum -a 256 -- "$(basename "$FINAL")" > "$(basename "$FINAL").sha256" )
else
  log "WARN: no sha256 tool found; checksum file not written."
fi
log "OK: wrote $(basename "$FINAL") (${bytes} bytes). Header verified; full decrypt requires the private key - run the restore test (tools/restore_check.md)."

# --- retention ----------------------------------------------------------------------------
if [ -n "$KEEP" ]; then
  shopt -s nullglob
  backups=("$OUT_DIR"/dentisai-*.dump.age)
  total=${#backups[@]}
  if [ "$total" -gt "$KEEP" ]; then
    remove=$((total - KEEP))
    for ((i = 0; i < remove; i++)); do
      old=${backups[$i]}
      if [ "$old" = "$FINAL" ]; then continue; fi
      rm -f -- "$old" "$old.sha256"
      log "pruned $(basename "$old")"
    done
  fi
  shopt -u nullglob
fi

log "NOTICE: pg_dump does NOT include Storage files, only their metadata rows (storage.objects)."
log "NOTICE: bucket 'manifests' is reproducible from ml/exports. Bucket 'dataset_photos' (children's photos) is NOT in this backup; export it separately with tools/export_dataset.py (needs ethics approval) and keep it on an encrypted disk."
