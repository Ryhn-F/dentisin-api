"""CLI behaviour, exit codes, JSON output and private-key leak checks."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from conftest import BASE_URL, Artifacts, KeyMaterial, make_key, write_json
from dentisai_manifest import cli_sign, cli_verify
from dentisai_manifest.keys import (
    BACKEND_ROOT,
    ENV_PASSPHRASE,
    ENV_PUBLIC_KEY_B64,
    ENV_SIGNING_KEY_PATH,
    fixed_test_key,
)

TOOLS = BACKEND_ROOT / "tools"


def run_sign(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any], str]:
    rc = cli_sign.main([str(a) for a in argv])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def run_verify(
    capsys: pytest.CaptureFixture[str], *argv: str, opener: Any = None
) -> tuple[int, dict[str, Any], str]:
    rc = cli_verify.main([str(a) for a in argv], opener)
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def assert_no_leak(key: KeyMaterial, *outputs: str) -> None:
    blob = "\n".join(outputs)
    for secret in key.secrets():
        assert secret not in blob


def build_args(art: Artifacts, key: KeyMaterial, out: Path, *extra: str) -> list[str]:
    return [
        "sign",
        "--key", str(key.path),
        "--model-dir", str(art.model_dir),
        "--rules", str(art.rules),
        "--recommender", str(art.recommender),
        "--base-url", BASE_URL,
        "--sequence", "12",
        "--min-app-version", "1.0.0",
        "--created-at", "2026-10-08T00:00:00Z",
        "--out-dir", str(out),
        *extra,
    ]  # fmt: skip


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (ENV_SIGNING_KEY_PATH, ENV_PUBLIC_KEY_B64, ENV_PASSPHRASE, cli_sign.ENV_BASE_URL):
        monkeypatch.delenv(var, raising=False)


# --- keygen ---------------------------------------------------------------------------


def test_keygen_prints_only_public_material(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "k.pem"
    rc, out, err = run_sign(capsys, "keygen", "--out", str(target))
    assert rc == 0
    assert set(out) == {"ok", "key_id", "public_key_b64", "private_key_path", "encrypted"}
    assert out["encrypted"] is False
    assert "UNENCRYPTED" in err
    from dentisai_manifest.keys import load_private_key

    loaded = load_private_key(target)
    km = KeyMaterial(loaded.private_key, target, target.read_bytes(), loaded.key_id, "")
    assert out["key_id"] == loaded.key_id
    assert_no_leak(km, json.dumps(out), err)


def test_keygen_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inside = BACKEND_ROOT / "tools" / "manifest" / "SHOULD_NOT_EXIST.pem"
    rc, out, _ = run_sign(capsys, "keygen", "--out", str(inside))
    assert rc == 7
    assert out["error"]["code"] == "E_REFUSED"
    assert not inside.exists()
    existing = tmp_path / "k.pem"
    existing.write_text("x")
    rc, out, _ = run_sign(capsys, "keygen", "--out", str(existing))
    assert (rc, out["error"]["code"]) == (7, "E_REFUSED")
    assert existing.read_text() == "x"


def test_keygen_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["s3cret-pass", "s3cret-pass", "a", "b", "", ""])
    monkeypatch.setattr(getpass, "getpass", lambda _p="": next(answers))
    rc, out, err = run_sign(
        capsys, "keygen", "--out", str(tmp_path / "a.pem"), "--prompt-passphrase"
    )
    assert rc == 0 and out["encrypted"] is True
    assert "s3cret-pass" not in json.dumps(out) + err
    rc, out, _ = run_sign(capsys, "keygen", "--out", str(tmp_path / "b.pem"), "--prompt-passphrase")
    assert (rc, out["error"]["code"]) == (2, "E_USAGE")
    rc, out, _ = run_sign(capsys, "keygen", "--out", str(tmp_path / "c.pem"), "--prompt-passphrase")
    assert (rc, out["error"]["code"]) == (2, "E_USAGE")
    monkeypatch.setenv(ENV_PASSPHRASE, "from-env")
    rc, out, _ = run_sign(capsys, "keygen", "--out", str(tmp_path / "d.pem"))
    assert rc == 0 and out["encrypted"] is True


# --- sign (build mode) + verify ---------------------------------------------------------


def test_build_sign_verify_end_to_end(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"
    rc, out, err = run_sign(capsys, *build_args(artifacts, key, out_dir))
    assert rc == 0, out
    assert out["channel"] == "production"
    assert out["key_id"] == key.key_id
    assert len(out["staged_artifacts"]) == 4
    assert "not encrypted" in err
    assert_no_leak(key, json.dumps(out), err)

    m = out_dir / "manifest.json"
    rc, rep, verr = run_verify(
        capsys, "--manifest", str(m), "--pubkey", key.public_b64, "--production",
        "--artifacts-dir", str(out_dir), "--now", "2026-10-09T00:00:00Z",
        "--app-version", "1.0.0", "--last-sequence", "11", "--last-schema-version", "1",
    )  # fmt: skip
    assert rc == 0, rep
    assert rep["ok"] is True and rep["error"] is None
    assert rep["manifest"]["sequence"] == 12
    assert [a["ok"] for a in rep["artifacts"]] == [True] * 4
    assert_no_leak(key, json.dumps(rep), verr)

    # refusing to overwrite outputs without --force
    rc, out, _ = run_sign(capsys, *build_args(artifacts, key, out_dir))
    assert (rc, out["error"]["code"]) == (7, "E_REFUSED")
    rc, out, _ = run_sign(capsys, *build_args(artifacts, key, out_dir), "--force")
    assert rc == 0

    # tamper with an artifact after signing -> exit 6
    model = out_dir / "model/0.3.1/model.tflite"
    model.write_bytes(model.read_bytes()[:-1] + b"\x00")
    rc, rep, _ = run_verify(
        capsys, "--manifest", str(m), "--pubkey", key.public_b64, "--artifacts-dir", str(out_dir)
    )
    assert rc == 6
    assert rep["error"]["code"] in ("E_ARTIFACT_HASH", "E_ARTIFACT_SIZE")
    # rollback -> exit 5
    rc, rep, _ = run_verify(
        capsys, "--manifest", str(m), "--pubkey", key.public_b64, "--last-sequence", "13"
    )
    assert (rc, rep["error"]["code"]) == (5, "E_ROLLBACK")


def test_placeholder_requires_flag_and_is_rejected_in_production(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(artifacts.rules, {"version": "1.0.0", "params": {"x": None}, "n": "PLACEHOLDER"})
    rc, out, err = run_sign(capsys, *build_args(artifacts, key, tmp_path / "o1"))
    assert (rc, out["error"]["code"]) == (7, "E_REFUSED")
    assert "placeholder/unverified" in err
    assert not (tmp_path / "o1" / "manifest.sig").exists()

    out_dir = tmp_path / "o2"
    rc, out, err = run_sign(capsys, *build_args(artifacts, key, out_dir), "--allow-placeholder")
    assert rc == 0 and out["channel"] == "internal-test"
    assert "production verifiers will reject" in err
    m = str(out_dir / "manifest.json")
    rc, rep, _ = run_verify(capsys, "--manifest", m, "--pubkey", key.public_b64)
    assert rc == 0
    rc, rep, _ = run_verify(capsys, "--manifest", m, "--pubkey", key.public_b64, "--production")
    assert (rc, rep["error"]["code"]) == (5, "E_CHANNEL")


def test_sign_existing_manifest(
    tmp_path: Path, key: KeyMaterial, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from conftest import sample_doc

    doc = sample_doc(key.key_id, expires_at="2027-01-01T00:00:00Z")
    src = tmp_path / "manifest.in.json"
    src.write_text(json.dumps(doc, indent=4), encoding="utf-8")  # pretty, unsorted input
    monkeypatch.setenv(ENV_SIGNING_KEY_PATH, str(key.path))
    out_dir = tmp_path / "out"
    # no "sign" subcommand: backwards compatible invocation from backend/CLAUDE.md
    rc, out, err = run_sign(capsys, "--manifest", str(src), "--out-dir", str(out_dir))
    assert rc == 0, out
    assert out["staged_artifacts"] == []
    raw = (out_dir / "manifest.json").read_bytes()
    assert b"\n" not in raw and b" " not in raw
    assert_no_leak(key, json.dumps(out), err)
    monkeypatch.setenv(ENV_PUBLIC_KEY_B64, f"{key.public_b64}, ")
    rc, rep, _ = run_verify(capsys, "--manifest", str(out_dir / "manifest.json"))
    assert rc == 0, rep


@pytest.mark.parametrize(
    ("text", "code", "rc"),
    [
        ('{"a":1,"a":2}', "E_NOT_CANONICAL", 4),
        ('{"sequence":1.5}', "E_NOT_CANONICAL", 4),
        ("[1]", "E_SCHEMA", 4),
    ],
)
def test_sign_existing_rejects_bad_json(
    tmp_path: Path, key: KeyMaterial, capsys: pytest.CaptureFixture[str],
    text: str, code: str, rc: int,
) -> None:  # fmt: skip
    src = tmp_path / "m.json"
    src.write_text(text, encoding="utf-8")
    got_rc, out, _ = run_sign(capsys, "--key", str(key.path), "--manifest", str(src),
                              "--out-dir", str(tmp_path / "o"))  # fmt: skip
    assert (got_rc, out["error"]["code"]) == (rc, code)


def test_sign_usage_errors(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    o = str(tmp_path / "o")
    cases: list[tuple[list[str], int, str]] = [
        (["sign", "--manifest", "x.json", "--out-dir", o], 2, "E_USAGE"),  # no key
        (["sign", "--key", str(key.path), "--out-dir", o], 2, "E_USAGE"),  # nothing to do
        (["sign", "--key", str(key.path), "--manifest", str(tmp_path / "missing.json"),
          "--out-dir", o], 1, "E_IO"),
        (["sign", "--key", str(key.path), "--manifest", "m.json", "--rules", "r.json",
          "--out-dir", o], 2, "E_USAGE"),
        ([*build_args(artifacts, key, tmp_path / "o"), "--min-app-version", "1.0"], 2,
         "E_USAGE"),
        ([*build_args(artifacts, key, tmp_path / "o"), "--sequence", "0"], 2, "E_USAGE"),
        ([*build_args(artifacts, key, tmp_path / "o"), "--created-at", "yesterday"], 2,
         "E_USAGE"),
        ([*build_args(artifacts, key, tmp_path / "o"), "--key", str(tmp_path / "nokey")], 1,
         "E_IO"),
    ]  # fmt: skip
    for argv, rc, code in cases:
        got_rc, out, _ = run_sign(capsys, *argv)
        assert (got_rc, out["error"]["code"]) == (rc, code), argv


def test_sign_expiry_options(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _, _ = run_sign(
        capsys, *build_args(artifacts, key, tmp_path / "a"), "--expires-in-days", "30"
    )
    assert rc == 0
    doc = json.loads((tmp_path / "a" / "manifest.json").read_bytes())
    assert doc["expires_at"] == "2026-11-07T00:00:00Z"
    rc, _, _ = run_sign(
        capsys, *build_args(artifacts, key, tmp_path / "b"), "--expires-at", "2026-12-01T00:00:00Z"
    )
    assert rc == 0
    m = str(tmp_path / "b" / "manifest.json")
    rc, rep, _ = run_verify(
        capsys, "--manifest", m, "--pubkey", key.public_b64, "--now", "2026-12-01T00:00:00Z"
    )
    assert (rc, rep["error"]["code"]) == (5, "E_EXPIRED")


def test_sign_with_base_url_env_and_default_created_at(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setenv(cli_sign.ENV_BASE_URL, BASE_URL)
    argv = [a for a in build_args(artifacts, key, tmp_path / "o")]
    i = argv.index("--base-url")
    del argv[i : i + 2]
    i = argv.index("--created-at")
    del argv[i : i + 2]
    rc, out, _ = run_sign(capsys, *argv)
    assert rc == 0, out
    rc, out, _ = run_sign(
        capsys, *argv[:-2], "--out-dir", str(tmp_path / "p"), "--rules-version", "3.0.0"
    )
    assert rc == 0, out
    monkeypatch.delenv(cli_sign.ENV_BASE_URL)
    rc, out, _ = run_sign(capsys, *argv[:-2], "--out-dir", str(tmp_path / "q"))
    assert (rc, out["error"]["code"]) == (2, "E_USAGE")
    assert "--base-url" in out["error"]["message"]


def test_sign_refuses_test_key_for_production(
    tmp_path: Path, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    from cryptography.hazmat.primitives import serialization

    from dentisai_manifest.keys import key_id_of

    tk = fixed_test_key()
    p = tmp_path / "test.pem"
    p.write_bytes(tk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))  # fmt: skip
    km = KeyMaterial(tk, p, p.read_bytes(), key_id_of(tk.public_key()), "")
    rc, out, err = run_sign(capsys, *build_args(artifacts, km, tmp_path / "o"))
    assert (rc, out["error"]["code"]) == (7, "E_REFUSED")
    rc, out, err = run_sign(
        capsys, *build_args(artifacts, km, tmp_path / "o"), "--allow-placeholder"
    )
    assert rc == 0 and out["channel"] == "internal-test"
    assert_no_leak(km, json.dumps(out), err)


def test_sign_encrypted_key_prompt_and_wrong_passphrase(
    tmp_path: Path, artifacts: Artifacts, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from dentisai_manifest.keys import keygen, load_private_key

    kp = tmp_path / "keys"
    kp.mkdir()
    keygen(kp / "enc.pem", b"right-pass")
    loaded = load_private_key(kp / "enc.pem", b"right-pass")
    km = KeyMaterial(loaded.private_key, kp / "enc.pem", (kp / "enc.pem").read_bytes(),
                     loaded.key_id, "")  # fmt: skip
    answers = iter(["wrong-pass", "right-pass"])
    monkeypatch.setattr(getpass, "getpass", lambda _p="": next(answers))
    rc, out, err = run_sign(
        capsys, *build_args(artifacts, km, tmp_path / "o"), "--prompt-passphrase"
    )
    assert (rc, out["error"]["code"]) == (7, "E_REFUSED")
    assert "wrong-pass" not in json.dumps(out) + err
    assert_no_leak(km, json.dumps(out), err)
    rc, out, err = run_sign(
        capsys, *build_args(artifacts, km, tmp_path / "o"), "--prompt-passphrase"
    )
    assert rc == 0, out
    assert "not encrypted" not in err
    assert_no_leak(km, json.dumps(out), err, "right-pass")


def test_sign_os_error_reported(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    rc, out, _ = run_sign(capsys, *build_args(artifacts, key, blocker / "sub"))
    assert (rc, out["error"]["code"]) == (1, "E_IO")


# --- verify CLI ------------------------------------------------------------------------


def _signed_dir(tmp_path: Path, key: KeyMaterial, artifacts: Artifacts,
                capsys: pytest.CaptureFixture[str]) -> Path:  # fmt: skip
    out_dir = tmp_path / "pub"
    rc, _, _ = run_sign(capsys, *build_args(artifacts, key, out_dir))
    assert rc == 0
    return out_dir


def test_verify_usage_and_io_errors(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _signed_dir(tmp_path, key, artifacts, capsys)
    m = str(d / "manifest.json")
    cases: list[tuple[list[str], int, str]] = [
        (["--manifest", m], 2, "E_USAGE"),  # no trusted key
        (["--manifest", m, "--pubkey", "bad"], 2, "E_USAGE"),
        (["--manifest", m, "--pubkey", key.public_b64, "--revoked", "nothex"], 2, "E_USAGE"),
        (["--manifest", m, "--pubkey", key.public_b64, "--now", "tomorrow"], 2, "E_USAGE"),
        (["--manifest", m, "--pubkey", key.public_b64, "--app-version", "v1"], 2, "E_USAGE"),
        (["--manifest", str(tmp_path / "nope.json"), "--pubkey", key.public_b64], 1, "E_IO"),
        (["--manifest", m, "--pubkey", key.public_b64, "--revoked", key.key_id], 3,
         "E_KEY_REVOKED"),
        (["--manifest", m, "--pubkey", key.public_b64, "--app-version", "0.9.0"], 5,
         "E_APP_TOO_OLD"),
        (["--manifest", m, "--pubkey", key.public_b64, "--last-schema-version", "2"], 5,
         "E_SCHEMA_DOWNGRADE"),
    ]  # fmt: skip
    for argv, rc, code in cases:
        got, rep, _ = run_verify(capsys, *argv)
        assert (got, rep["error"]["code"]) == (rc, code), argv
        assert rep["ok"] is False and rep["exit_code"] == rc


def test_verify_wrong_key_and_tamper_exit_codes(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _signed_dir(tmp_path, key, artifacts, capsys)
    other = make_key(tmp_path, "other.pem")
    m = d / "manifest.json"
    rc, rep, _ = run_verify(capsys, "--manifest", str(m), "--pubkey", other.public_b64)
    assert (rc, rep["error"]["code"]) == (3, "E_KEY_UNKNOWN")
    raw = bytearray(m.read_bytes())
    raw[5] ^= 1
    m.write_bytes(bytes(raw))
    rc, rep, _ = run_verify(capsys, "--manifest", str(m), "--pubkey", key.public_b64)
    assert (rc, rep["error"]["code"]) == (3, "E_SIGNATURE")
    m.write_bytes(m.read_bytes() + b"\n")
    rc, rep, _ = run_verify(capsys, "--manifest", str(m), "--sig", str(d / "manifest.sig"),
                            "--pubkey", key.public_b64)  # fmt: skip
    assert rc == 3


def test_verify_over_https_with_fake_opener(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_build_artifacts import FakeResp

    d = _signed_dir(tmp_path, key, artifacts, capsys)

    def opener(req: urllib.request.Request, timeout: float) -> FakeResp:
        rel = req.full_url[len(BASE_URL) + 1 :]
        return FakeResp((d / rel).read_bytes(), req.full_url)

    url = f"{BASE_URL}/manifest.json"
    rc, rep, _ = run_verify(capsys, "--manifest", url, "--pubkey", key.public_b64, "--fetch",
                            "--timeout", "3", opener=opener)  # fmt: skip
    assert rc == 0, rep
    assert len(rep["artifacts"]) == 4
    rc, rep, _ = run_verify(capsys, "--manifest", "http://abc.supabase.co/manifest.json",
                            "--pubkey", key.public_b64, opener=opener)  # fmt: skip
    assert rc == 6


# --- thin entrypoints ------------------------------------------------------------------


@pytest.mark.parametrize("script", ["sign_manifest.py", "verify_manifest.py"])
def test_entrypoint_wrappers(script: str) -> None:
    proc = subprocess.run(
        [sys.executable, str(TOOLS / script), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout


def test_entrypoint_verify_vector_via_subprocess(tmp_path: Path) -> None:
    vec = json.loads((BACKEND_ROOT / "tools/manifest/vectors/manifest_v1_valid_01_minimal.json")
                     .read_text(encoding="utf-8"))  # fmt: skip
    (tmp_path / "manifest.json").write_text(vec["manifest_json"], encoding="ascii", newline="")
    (tmp_path / "manifest.sig").write_text(vec["manifest_sig"], encoding="ascii", newline="")
    base = [sys.executable, str(TOOLS / "verify_manifest.py"), "--manifest",
            str(tmp_path / "manifest.json"), "--pubkey", vec["signing_key"]["public_key_b64"],
            "--now", "2026-10-08T01:00:00Z"]  # fmt: skip
    proc = subprocess.run(base, capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stdout
    proc = subprocess.run([*base, "--production"], capture_output=True, text=True, check=False,
                          timeout=60)  # fmt: skip
    assert proc.returncode == 3
    assert json.loads(proc.stdout)["error"]["code"] == "E_KEY_TEST_ONLY"


def test_sign_large_input_and_build_warnings(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    big = tmp_path / "big.json"
    big.write_text('{"a":"' + "x" * 70_000 + '"}', encoding="utf-8")
    rc, out, _ = run_sign(capsys, "--key", str(key.path), "--manifest", str(big),
                          "--out-dir", str(tmp_path / "o"))  # fmt: skip
    assert (rc, out["error"]["code"]) == (4, "E_TOO_LARGE")
    (artifacts.model_dir / "MODEL_CARD.md").unlink()
    rc, out, err = run_sign(capsys, *build_args(artifacts, key, tmp_path / "p"))
    assert rc == 0, out
    assert "MODEL_CARD.md missing" in err


def test_env_file_fallback_and_default_paths(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """MANIFEST_* come from backend/.env when unset; rules/recommender default to backend/ dist."""
    from dentisai_manifest import keys as keys_mod
    from dentisai_manifest import signing

    env = tmp_path / "backend.env"
    env.write_text(
        f"MANIFEST_SIGNING_KEY_PATH={key.path}\nMANIFEST_PUBLIC_KEY_B64={key.public_b64}\n"
        f"MANIFEST_BASE_URL={BASE_URL}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(keys_mod, "ENV_FILE", env)
    monkeypatch.setattr(signing, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(cli_sign, "DEFAULT_RULES_FILE", artifacts.rules)
    monkeypatch.setattr(cli_sign, "DEFAULT_RECOMMENDER_FILE", artifacts.recommender)
    out_dir = tmp_path / "out"
    rc, out, err = run_sign(
        capsys, "--model-dir", str(artifacts.model_dir), "--sequence", "3",
        "--min-app-version", "1.0.0", "--out-dir", str(out_dir),
    )  # fmt: skip
    assert rc == 0, out
    assert_no_leak(key, json.dumps(out), err)
    rc, rep, _ = run_verify(capsys, "--manifest", str(out_dir / "manifest.json"),
                            "--artifacts-dir", str(out_dir))  # fmt: skip
    assert rc == 0, rep
