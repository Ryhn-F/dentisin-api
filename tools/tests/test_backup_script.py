"""Behavioural tests for tools/backup_db.sh using stub pg_dump/age executables.

These verify the script's control flow (guards, exit codes, file handling, retention,
no-secret-output). They do NOT prove that real pg_dump/age work against a real database.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from opscommon import backend_root as repo_root

SCRIPT = repo_root() / "tools" / "backup_db.sh"
DB_URL = "postgresql://postgres:s3cr3t-password@db.example.supabase.co:5432/postgres"
RECIPIENT = "age1" + "q" * 58


def _find_bash() -> str | None:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates += [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
    for cand in candidates:
        if not cand:
            continue
        try:
            out = subprocess.run(
                [cand, "-c", "echo $BASH_VERSION; command -v head tr wc mv sed grep date"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return cand
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="a working bash is required")

PG_DUMP_OK = '#!/usr/bin/env bash\nhead -c 400 /dev/zero | tr "\\0" "x"\n'
PG_DUMP_FAIL = "#!/usr/bin/env bash\necho 'connection refused' >&2\nexit 1\n"
AGE_OK = (
    "#!/usr/bin/env bash\n"
    "out=''\n"
    'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; *) shift;; esac; done\n'
    'printf "age-encryption.org/v1\\n" > "$out"\n'
    'cat >> "$out"\n'
)
AGE_NO_HEADER = (
    "#!/usr/bin/env bash\n"
    "out=''\n"
    'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; *) shift;; esac; done\n'
    'cat > "$out"\n'
)


def write_exe(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.stubs = tmp_path / "stubs"
        self.stubs.mkdir()
        self.out = tmp_path / "backups"
        self.out.mkdir()
        write_exe(self.stubs / "pg_dump", PG_DUMP_OK)
        write_exe(self.stubs / "age", AGE_OK)
        self.env = {
            "SUPABASE_DB_URL": DB_URL,
            "BACKUP_AGE_RECIPIENT": RECIPIENT,
            "BACKUP_OUTPUT_DIR": str(self.out),
        }
        self.empty_env_file = tmp_path / "empty.env"
        self.empty_env_file.write_text("", encoding="utf-8")

    def run(
        self, *args: str, env: dict[str, str] | None = None, hide: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        full = {
            k: v
            for k, v in os.environ.items()
            if k not in {"SUPABASE_DB_URL", "BACKUP_AGE_RECIPIENT", "BACKUP_OUTPUT_DIR"}
        }
        path_parts = [str(self.stubs)] + full.get("PATH", "").split(os.pathsep)
        full["PATH"] = os.pathsep.join(path_parts)
        full.update(self.env if env is None else env)
        return subprocess.run(
            [BASH, str(SCRIPT), "--env-file", str(self.empty_env_file), *args],
            capture_output=True,
            text=True,
            env=full,
            timeout=60,
            check=False,
        )

    def backups(self) -> list[str]:
        return sorted(p.name for p in self.out.iterdir())


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def assert_no_secrets(proc: subprocess.CompletedProcess[str]) -> None:
    combined = proc.stdout + proc.stderr
    assert "s3cr3t-password" not in combined
    assert DB_URL not in combined


def test_success_writes_encrypted_file_and_checksum(rig: Rig) -> None:
    proc = rig.run()
    assert proc.returncode == 0, proc.stderr
    names = rig.backups()
    assert len(names) == 2
    dump = next(n for n in names if n.endswith(".dump.age"))
    assert dump.startswith("dentisai-") and f"{dump}.sha256" in names
    assert (rig.out / dump).read_bytes().startswith(b"age-encryption.org/v1\n")
    assert not any(n.endswith(".partial") for n in names)
    assert proc.stdout == ""  # nothing on stdout, ever
    assert "dataset_photos" in proc.stderr  # storage notice
    assert_no_secrets(proc)


def test_missing_variables_exit_2(rig: Rig) -> None:
    proc = rig.run(env={})
    assert proc.returncode == 2
    assert "SUPABASE_DB_URL" in proc.stderr and "BACKUP_OUTPUT_DIR" in proc.stderr


def test_private_key_as_recipient_is_refused(rig: Rig) -> None:
    rig.env["BACKUP_AGE_RECIPIENT"] = "AGE-SECRET-KEY-1" + "Q" * 58
    proc = rig.run()
    assert proc.returncode == 2
    assert "PRIVATE" in proc.stderr
    assert "QQQQ" not in proc.stderr
    assert rig.backups() == []


def test_garbage_recipient_is_refused(rig: Rig) -> None:
    rig.env["BACKUP_AGE_RECIPIENT"] = "not-a-key"
    assert rig.run().returncode == 2


def test_output_inside_repo_is_refused(rig: Rig) -> None:
    rig.env["BACKUP_OUTPUT_DIR"] = str(repo_root() / "tools" / "tests")
    proc = rig.run()
    assert proc.returncode == 4
    assert "inside the backend/repository" in proc.stderr
    assert not list((repo_root() / "tools" / "tests").glob("dentisai-*"))


def test_output_equal_to_repo_root_is_refused(rig: Rig) -> None:
    rig.env["BACKUP_OUTPUT_DIR"] = str(repo_root())
    assert rig.run().returncode == 4


def test_missing_output_dir_is_refused(rig: Rig, tmp_path: Path) -> None:
    rig.env["BACKUP_OUTPUT_DIR"] = str(tmp_path / "does-not-exist")
    assert rig.run().returncode == 4


def test_missing_age_exit_3(rig: Rig) -> None:
    if shutil.which("age"):
        pytest.skip("a real age is installed on PATH")
    (rig.stubs / "age").unlink()
    assert rig.run().returncode == 3


def test_missing_pg_dump_exit_3(rig: Rig) -> None:
    if shutil.which("pg_dump"):
        pytest.skip("a real pg_dump is installed on PATH")
    (rig.stubs / "pg_dump").unlink()
    assert rig.run().returncode == 3


def test_pg_dump_failure_leaves_no_file(rig: Rig) -> None:
    write_exe(rig.stubs / "pg_dump", PG_DUMP_FAIL)
    proc = rig.run()
    assert proc.returncode == 5
    assert rig.backups() == []
    assert_no_secrets(proc)


def test_output_without_age_header_fails_verification(rig: Rig) -> None:
    write_exe(rig.stubs / "age", AGE_NO_HEADER)
    proc = rig.run()
    assert proc.returncode == 6
    assert rig.backups() == []


def test_retention_keeps_newest_n(rig: Rig) -> None:
    for stamp in ("20240101T000000Z", "20240102T000000Z", "20240103T000000Z"):
        (rig.out / f"dentisai-{stamp}.dump.age").write_bytes(b"old")
        (rig.out / f"dentisai-{stamp}.dump.age.sha256").write_text("x", encoding="utf-8")
    (rig.out / "unrelated.txt").write_text("keep me", encoding="utf-8")
    proc = rig.run("--keep", "2")
    assert proc.returncode == 0, proc.stderr
    dumps = [n for n in rig.backups() if n.endswith(".dump.age")]
    assert len(dumps) == 2
    assert "dentisai-20240101T000000Z.dump.age" not in dumps
    assert "dentisai-20240101T000000Z.dump.age.sha256" not in rig.backups()
    assert "dentisai-20240103T000000Z.dump.age" in dumps  # newest old backup survives
    assert "dentisai-20240102T000000Z.dump.age" not in dumps
    assert "unrelated.txt" in rig.backups()


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_invalid_keep_rejected(rig: Rig, bad: str) -> None:
    assert rig.run("--keep", bad).returncode == 2


def test_invalid_schemas_rejected(rig: Rig) -> None:
    assert rig.run("--schemas", "public;drop table x").returncode == 2
    assert rig.run("--schemas", ",").returncode == 2


def test_unknown_argument(rig: Rig) -> None:
    assert rig.run("--bogus").returncode == 2


def test_check_size_only_does_not_need_age_or_output(rig: Rig) -> None:
    proc = rig.run("--check-size", env={"SUPABASE_DB_URL": DB_URL})
    assert proc.returncode == 0
    assert rig.backups() == []
    assert_no_secrets(proc)


def test_size_warning_levels(rig: Rig) -> None:
    write_exe(rig.stubs / "psql", f"#!/usr/bin/env bash\necho {450 * 1024 * 1024}\n")
    proc = rig.run("--check-size", env={"SUPABASE_DB_URL": DB_URL})
    assert "WARNING: database is 450 MB" in proc.stderr
    write_exe(rig.stubs / "psql", f"#!/usr/bin/env bash\necho {490 * 1024 * 1024}\n")
    assert "CRITICAL" in rig.run("--check-size", env={"SUPABASE_DB_URL": DB_URL}).stderr
    write_exe(rig.stubs / "psql", f"#!/usr/bin/env bash\necho {10 * 1024 * 1024}\n")
    assert "(ok)" in rig.run("--check-size", env={"SUPABASE_DB_URL": DB_URL}).stderr


def test_env_file_is_used_when_variables_unset(rig: Rig, tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        f'SUPABASE_DB_URL="{DB_URL}"\nBACKUP_AGE_RECIPIENT={RECIPIENT}\r\n'
        f"BACKUP_OUTPUT_DIR={rig.out}\n",
        encoding="utf-8",
    )
    rig.empty_env_file = env_file
    proc = rig.run(env={})
    assert proc.returncode == 0, proc.stderr
    assert_no_secrets(proc)


def test_output_in_monorepo_parent_is_refused(rig: Rig) -> None:
    parent = repo_root().parent  # monorepo root holding CLAUDE.md, when backend/ is a subfolder
    if not (parent / "CLAUDE.md").is_file():
        pytest.skip("backend/ is not inside a monorepo here")
    rig.env["BACKUP_OUTPUT_DIR"] = str(parent / "app")
    assert rig.run().returncode == 4
