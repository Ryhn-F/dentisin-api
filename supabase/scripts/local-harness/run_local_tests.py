#!/usr/bin/env python3
"""Local fallback runner for the DentisAI pgTAP suite (no Docker / no Supabase CLI).

Creates (or reuses) a THROWAWAY PostgreSQL cluster, loads a minimal Supabase stub
(roles, auth, storage), a pgTAP-subset shim, all migrations, the synthetic seed,
then runs every ``backend/supabase/tests/*.test.sql`` through psql and parses the
TAP output. The canonical runner stays ``supabase test db`` (real pgTAP).

Usage (stdlib only):
    python run_local_tests.py --pg-bin "C:/Program Files/PostgreSQL/17/bin" \
        --data-dir <scratch>/pgdata --port 54329 [--stop]

Never point --data-dir at a real cluster: the database ``dentisai_harness`` is
dropped and recreated on every run. The cluster uses trust auth on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPABASE_DIR = HERE.parent.parent
DB = "dentisai_harness"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def psql(args: argparse.Namespace, db: str, *extra: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    exe = str(Path(args.pg_bin) / "psql")
    base = [exe, "-X", "-q", "-h", "127.0.0.1", "-p", str(args.port), "-U", "postgres", "-d", db,
            "-v", "ON_ERROR_STOP=1"]
    return run(base + list(extra), cwd=cwd)


def ensure_cluster(args: argparse.Namespace) -> None:
    data = Path(args.data_dir)
    bin_ = Path(args.pg_bin)
    if not (data / "PG_VERSION").exists():
        data.mkdir(parents=True, exist_ok=True)
        r = run([str(bin_ / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust",
                 "-E", "UTF8", "--locale=C"])
        if r.returncode != 0:
            sys.exit(f"initdb failed:\n{r.stderr}")
    status = run([str(bin_ / "pg_ctl"), "-D", str(data), "status"])
    if status.returncode != 0:
        # Do not capture output: the postmaster inherits the pipes on Windows and
        # subprocess would wait for EOF forever.
        rc = subprocess.call([str(bin_ / "pg_ctl"), "-D", str(data), "-l", str(data / "server.log"), "-w",
                              "-o", f"-p {args.port} -c listen_addresses=127.0.0.1", "start"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc != 0:
            sys.exit(f"pg_ctl start failed (see {data / 'server.log'})")


def stop_cluster(args: argparse.Namespace) -> None:
    subprocess.call([str(Path(args.pg_bin) / "pg_ctl"), "-D", str(args.data_dir), "-m", "fast", "-w", "stop"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_file(args: argparse.Namespace, path: Path) -> None:
    r = psql(args, DB, "-f", str(path))
    if r.returncode != 0:
        sys.exit(f"FAILED applying {path.name}:\n{r.stdout}\n{r.stderr}")
    notices = [ln for ln in r.stderr.splitlines() if "NOTICE" in ln and "DentisAI" in ln]
    for n in notices:
        print(f"    {path.name}: {n.strip()}")


def reset_db(args: argparse.Namespace) -> None:
    r = psql(args, "postgres", "-c", f"drop database if exists {DB} with (force)", "-c", f"create database {DB}")
    if r.returncode != 0:
        sys.exit(f"cannot recreate {DB}:\n{r.stderr}")
    # Cluster-wide roles survive between runs; the stub creates them only if absent.
    roles = psql(args, "postgres", "-t", "-A", "-c",
                 "select count(*) from pg_roles where rolname in ('anon','authenticated','service_role')")
    if roles.stdout.strip() == "3":
        stub = (HERE / "00_supabase_stub.sql").read_text(encoding="utf-8")
        stub = re.sub(r"^create role .*?;\s*$", "", stub, flags=re.M)
        tmp = Path(args.data_dir) / "_stub_without_roles.sql"
        tmp.write_text(stub, encoding="utf-8")
        apply_file(args, tmp)
    else:
        apply_file(args, HERE / "00_supabase_stub.sql")
    has_pgtap = psql(args, DB, "-t", "-A", "-c",
                     "select count(*) from pg_available_extensions where name = 'pgtap'").stdout.strip()
    if has_pgtap == "0":
        apply_file(args, HERE / "01_pgtap_shim.sql")
        print("  pgTAP not installed -> using local shim (01_pgtap_shim.sql)")


def run_tests(args: argparse.Namespace) -> int:
    tests_dir = SUPABASE_DIR / "tests"
    total_fail = 0
    total_ok = 0
    for f in sorted(tests_dir.glob("*.test.sql")):
        r = psql(args, DB, "-t", "-A", "-f", f.name, cwd=tests_dir)
        out = r.stdout.splitlines()
        oks = [ln for ln in out if re.match(r"^ok \d+", ln)]
        bad = [ln for ln in out if re.match(r"^not ok \d+", ln)]
        plan = next((ln for ln in out if re.match(r"^1\.\.\d+$", ln)), None)
        diag = [ln for ln in out if ln.startswith("#")]
        planned = int(plan.split("..")[1]) if plan else -1
        ran = len(oks) + len(bad)
        failed = bool(bad) or r.returncode != 0 or planned != ran or any("Looks like" in d for d in diag)
        status = "FAIL" if failed else "PASS"
        print(f"[{status}] {f.name}: {len(oks)}/{planned} ok")
        if failed:
            for ln in bad + diag:
                print(f"    {ln}")
            if r.returncode != 0:
                print("    psql error:\n      " + "\n      ".join(r.stderr.strip().splitlines()[-8:]))
            total_fail += 1
        total_ok += len(oks)
    print(f"\n{total_ok} assertions ok; {total_fail} file(s) failed")
    return 1 if total_fail else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pg-bin", default=os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\17\bin"))
    p.add_argument("--data-dir", required=True)
    p.add_argument("--port", type=int, default=54329)
    p.add_argument("--stop", action="store_true", help="stop the throwaway cluster afterwards")
    p.add_argument("--no-seed", action="store_true")
    args = p.parse_args()

    ensure_cluster(args)
    try:
        print("Resetting database ...")
        reset_db(args)
        for m in sorted((SUPABASE_DIR / "migrations").glob("*.sql")):
            print(f"  migration {m.name}")
            apply_file(args, m)
        if not args.no_seed:
            print("  seed/seed.sql")
            apply_file(args, SUPABASE_DIR / "seed" / "seed.sql")
        # Apply all migrations a second time: they must be re-runnable.
        if os.environ.get("DENTISAI_REAPPLY", "1") == "1":
            for m in sorted((SUPABASE_DIR / "migrations").glob("*.sql")):
                apply_file(args, m)
            print("  migrations re-applied (idempotency check) OK")
        return run_tests(args)
    finally:
        if args.stop:
            stop_cluster(args)
            print("cluster stopped")


if __name__ == "__main__":
    sys.exit(main())
