from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import check_secrets as cs


def jwt(role: str) -> str:
    def enc(obj: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc({'role': role})}.c2lnbmF0dXJlc2lnbmF0dXJl"


def write(root: Path, rel: str, content: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def scan(root: Path) -> set[tuple[str, str]]:
    return {(f.path, f.rule) for f in cs.scan_repo(root)}


def test_clean_repo(tmp_path: Path) -> None:
    write(tmp_path, ".env.example", "SUPABASE_URL=\n")
    write(tmp_path, "README.md", "hello")
    assert scan(tmp_path) == set()
    assert cs.main(["--root", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "rel",
    [
        ".env",
        ".env.local",
        "backend/.env",
        "keys/manifest_signing_v1.key",
        "certs/a.pem",
        "backups/x.sql.age",
        "backups/x.dump",
        "dataset_export/a.jpg",
        "deep/dataset_export/x/y.csv",
    ],
)
def test_forbidden_file_names(tmp_path: Path, rel: str) -> None:
    write(tmp_path, rel)
    assert [f.path for f in cs.scan_repo(tmp_path)] == [rel]
    assert cs.main(["--root", str(tmp_path)]) == 1


def test_service_role_jwt_detected_but_anon_ignored(tmp_path: Path) -> None:
    write(tmp_path, "a.ts", f"const k = '{jwt('service_role')}'\n")
    write(tmp_path, "b.ts", f"const k = '{jwt('anon')}'\n")
    assert scan(tmp_path) == {("a.ts", "service-role-jwt")}


def test_other_key_patterns(tmp_path: Path) -> None:
    write(tmp_path, "c.txt", "AGE-SECRET-KEY-1" + "Q" * 58 + "\n")
    write(tmp_path, "d.txt", "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n")
    write(tmp_path, "e.txt", "sb_secret_" + "a1b2c3d4e5f6\n")
    assert scan(tmp_path) == {
        ("c.txt", "age-private-key"),
        ("d.txt", "pem-private-key"),
        ("e.txt", "supabase-secret-key"),
    }


def test_values_are_never_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    secret = jwt("service_role")
    write(tmp_path, "leak.txt", f"line1\nkey={secret}\n")
    assert cs.main(["--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "leak.txt:2: service-role-jwt" in captured.out
    assert secret not in captured.out + captured.err


def test_skips_virtualenv_and_git_dirs(tmp_path: Path) -> None:
    write(tmp_path, ".venv/lib/x.pem")
    write(tmp_path, "node_modules/p/.env")
    assert scan(tmp_path) == set()


def test_bad_root_is_error(tmp_path: Path) -> None:
    assert cs.main(["--root", str(tmp_path / "missing")]) == 2


def test_fallback_walk_honours_gitignore_but_tracked_rules_still_apply(tmp_path: Path) -> None:
    ignore_lines = [".env", ".env.*", "!.env.example", "*.key", "dataset_export/"]
    write(tmp_path, ".gitignore", "\n".join(ignore_lines) + "\n")
    write(tmp_path, ".env", "SECRET=1")
    write(tmp_path, "a.key", "x")
    write(tmp_path, "dataset_export/a.jpg", "x")
    write(tmp_path, ".env.example", "SECRET=")
    write(tmp_path, "notes.pem", "x")  # not covered by this .gitignore -> reported
    assert scan(tmp_path) == {("notes.pem", "forbidden-file-name(*.pem)")}
