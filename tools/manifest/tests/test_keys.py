from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import KeyMaterial
from dentisai_manifest import keys as keys_mod
from dentisai_manifest.errors import ErrorCode, ManifestError
from dentisai_manifest.keys import (
    BACKEND_ROOT,
    ENV_PASSPHRASE,
    TEST_KEY_IDS,
    b64decode_canonical,
    env_value,
    fixed_test_key,
    key_id_of,
    keygen,
    load_private_key,
    load_public_key_b64,
    location_problems,
    protected_roots,
)


def code_of(fn: object, *args: object) -> ErrorCode:
    with pytest.raises(ManifestError) as ei:
        fn(*args)  # type: ignore[operator]
    return ei.value.code


@pytest.fixture
def fake_monorepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """backend/ nested in a monorepo (parent has CLAUDE.md + app/); independent of the checkout."""
    mono = tmp_path / "mono"
    backend = mono / "backend"
    (backend / "tools" / "manifest").mkdir(parents=True)
    (mono / "app").mkdir()
    (mono / "CLAUDE.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(keys_mod, "BACKEND_ROOT", backend)
    return mono


def test_repo_root_is_the_dentisai_repo() -> None:
    assert (BACKEND_ROOT / "tools" / "manifest").is_dir()
    roots = protected_roots()
    assert roots[0] == BACKEND_ROOT
    # the parent is protected only when it looks like the monorepo (standalone repo: just backend/)
    parent_is_monorepo = any(
        (BACKEND_ROOT.parent / m).exists() for m in ("CLAUDE.md", ".git", "app")
    )
    assert roots[1:] == ([BACKEND_ROOT.parent] if parent_is_monorepo else [])


def test_protected_roots_standalone(tmp_path: Path) -> None:
    backend = tmp_path / "standalone"
    backend.mkdir()
    assert protected_roots(backend) == [backend]


def test_protected_roots_in_monorepo(fake_monorepo: Path) -> None:
    assert protected_roots() == [fake_monorepo / "backend", fake_monorepo]


def test_key_id_is_first_8_bytes_of_sha256() -> None:
    raw = bytes(range(32))
    import hashlib

    assert key_id_of(raw) == hashlib.sha256(raw).hexdigest()[:16]
    with pytest.raises(ValueError, match="32 bytes"):
        key_id_of(b"short")


def test_test_key_ids_are_known() -> None:
    assert key_id_of(fixed_test_key(0).public_key()) in TEST_KEY_IDS
    assert len(TEST_KEY_IDS) == 2


def test_load_unencrypted_pem_warns(key: KeyMaterial) -> None:
    loaded = load_private_key(key.path)
    assert loaded.key_id == key.key_id
    assert not loaded.encrypted_at_rest
    assert any("not encrypted" in w for w in loaded.warnings)


def test_load_encrypted_pem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k = Ed25519PrivateKey.generate()
    p = tmp_path / "enc.pem"
    p.write_bytes(
        k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"correct horse"),
        )
    )
    p.chmod(0o600)  # POSIX CI would otherwise warn about group/other access
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    assert code_of(load_private_key, p) is ErrorCode.E_USAGE
    assert code_of(load_private_key, p, b"wrong") is ErrorCode.E_REFUSED
    loaded = load_private_key(p, b"correct horse")
    assert loaded.encrypted_at_rest
    assert loaded.warnings == ()
    monkeypatch.setenv(ENV_PASSPHRASE, "correct horse")
    assert load_private_key(p).key_id == key_id_of(k.public_key())


def test_load_raw_base64_seed(tmp_path: Path) -> None:
    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    p = tmp_path / "seed.b64"
    p.write_text(base64.b64encode(seed).decode() + "\n")
    assert load_private_key(p).key_id == key_id_of(k.public_key())


@pytest.mark.parametrize(
    "content",
    [b"not a key", b"\xff\xfe", base64.b64encode(b"x" * 31), b"-----BEGIN garbage-----\n"],
)
def test_load_garbage_refused(tmp_path: Path, content: bytes) -> None:
    p = tmp_path / "bad"
    p.write_bytes(content)
    assert code_of(load_private_key, p) is ErrorCode.E_REFUSED


def test_load_non_ed25519_refused(tmp_path: Path) -> None:
    p = tmp_path / "ec.pem"
    p.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    assert code_of(load_private_key, p) is ErrorCode.E_REFUSED


def test_load_missing_and_oversized(tmp_path: Path) -> None:
    assert code_of(load_private_key, tmp_path / "nope") is ErrorCode.E_IO
    big = tmp_path / "big"
    big.write_bytes(b"A" * 5000)
    assert code_of(load_private_key, big) is ErrorCode.E_REFUSED


def test_location_problems(tmp_path: Path) -> None:
    assert location_problems(BACKEND_ROOT / "tools" / "x.pem")
    assert location_problems(BACKEND_ROOT)
    assert location_problems(tmp_path / "k.pem") == []
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    probs = location_problems(tmp_path / "proj" / "sub" / "k.pem")
    assert any("git work tree" in p for p in probs)


def test_load_warns_about_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    k = Ed25519PrivateKey.generate()
    p = tmp_path / "k.pem"
    p.write_bytes(
        k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    assert any("git work tree" in w for w in load_private_key(p).warnings)
    monkeypatch.setattr(keys_mod, "BACKEND_ROOT", tmp_path)
    assert any("repository" in w for w in load_private_key(p).warnings)


def test_keygen_refuses_inside_repo() -> None:
    target = BACKEND_ROOT / "tools" / "manifest" / "SHOULD_NOT_EXIST_key.pem"
    assert code_of(keygen, target, None) is ErrorCode.E_REFUSED
    assert not target.exists()


def test_keygen_refuses_in_git_tree_overwrite_and_missing_dir(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert code_of(keygen, tmp_path / "repo" / "k.pem", None) is ErrorCode.E_REFUSED
    existing = tmp_path / "exists.pem"
    existing.write_text("keep me")
    assert code_of(keygen, existing, None) is ErrorCode.E_REFUSED
    assert existing.read_text() == "keep me"
    assert code_of(keygen, tmp_path / "missing" / "k.pem", None) is ErrorCode.E_IO


def test_keygen_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", boom)
    assert code_of(keygen, tmp_path / "k.pem", None) is ErrorCode.E_IO


def test_keygen_writes_loadable_key(tmp_path: Path) -> None:
    kid, pub = keygen(tmp_path / "k.pem", b"pass phrase")
    loaded = load_private_key(tmp_path / "k.pem", b"pass phrase")
    assert loaded.key_id == kid
    assert load_public_key_b64(pub).key_id == kid
    assert b"ENCRYPTED" in (tmp_path / "k.pem").read_bytes()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("é", "valid base64"),
        ("!!!!", "valid base64"),
        ("AAB=", "canonical"),  # non-zero padding bits
        (base64.b64encode(b"x" * 31).decode(), "32 bytes"),
    ],
)
def test_b64decode_canonical(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        b64decode_canonical(text, 32, "k")


def test_location_problems_monorepo_outside_backend(fake_monorepo: Path) -> None:
    assert location_problems(fake_monorepo / "app" / "k.pem")  # monorepo, outside backend/
    assert location_problems(fake_monorepo / "k.pem")


def test_keygen_refuses_in_monorepo_outside_backend(fake_monorepo: Path) -> None:
    target = fake_monorepo / "docs" / "SHOULD_NOT_EXIST_key.pem"
    assert code_of(keygen, target, None) is ErrorCode.E_REFUSED
    assert not target.exists()


def test_protected_roots_standalone_backend(tmp_path: Path) -> None:
    standalone = tmp_path / "backend"
    standalone.mkdir()
    assert protected_roots(standalone) == [standalone]
    (tmp_path / "CLAUDE.md").write_text("x")
    assert protected_roots(standalone) == [standalone, tmp_path]


def test_env_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nMANIFEST_X=\nexport MANIFEST_Y = 'quoted value'\nMANIFEST_Z=plain\nJUNK\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MANIFEST_Z", raising=False)
    assert env_value("MANIFEST_X", env) is None
    assert env_value("MANIFEST_Y", env) == "quoted value"
    assert env_value("MANIFEST_Z", env) == "plain"
    assert env_value("MANIFEST_MISSING", env) is None
    assert env_value("MANIFEST_Z", tmp_path / "absent.env") is None
    monkeypatch.setenv("MANIFEST_Z", "from-environ")
    assert env_value("MANIFEST_Z", env) == "from-environ"
