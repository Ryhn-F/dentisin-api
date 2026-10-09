"""Shared fixtures. Every key here is ephemeral and lives in pytest's tmp dir."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dentisai_manifest.canonical import JsonValue
from dentisai_manifest.keys import key_id_of, public_b64
from dentisai_manifest.signing import BuildInputs, Signed, build_manifest, sign_manifest
from dentisai_manifest.verify import Policy, TrustStore

BASE_URL = "https://abc123.supabase.co/storage/v1/object/public/manifests"
CREATED = datetime(2026, 10, 8, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class KeyMaterial:
    private_key: Ed25519PrivateKey
    path: Path
    pem: bytes
    key_id: str
    public_b64: str

    def secrets(self) -> list[str]:
        """Every textual form of the private key that must never be printed."""
        seed = self.private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        pem_body = [
            ln for ln in self.pem.decode().splitlines() if ln and not ln.startswith("-----")
        ]
        return [
            seed.hex(),
            seed.hex().upper(),
            base64.b64encode(seed).decode(),
            base64.urlsafe_b64encode(seed).decode().rstrip("="),
            *pem_body,
        ]

    def trust(self) -> TrustStore:
        return TrustStore.from_b64([self.public_b64])


def make_key(directory: Path, name: str = "signing.pem") -> KeyMaterial:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = directory / name
    path.write_bytes(pem)
    return KeyMaterial(key, path, pem, key_id_of(key.public_key()), public_b64(key.public_key()))


@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real backend/.env during tests."""
    from dentisai_manifest import keys as keys_mod

    monkeypatch.setattr(keys_mod, "ENV_FILE", tmp_path / "no-such.env")


@pytest.fixture
def key(tmp_path: Path) -> KeyMaterial:
    d = tmp_path / "offline-keys"
    d.mkdir()
    return make_key(d)


@pytest.fixture
def other_key(tmp_path: Path) -> KeyMaterial:
    d = tmp_path / "other-keys"
    d.mkdir()
    return make_key(d)


@dataclass(frozen=True)
class Artifacts:
    model_dir: Path
    rules: Path
    recommender: Path

    def inputs(self, **over: object) -> BuildInputs:
        base: dict[str, object] = {
            "model_dir": self.model_dir,
            "rules_file": self.rules,
            "recommender_file": self.recommender,
            "base_url": BASE_URL,
            "sequence": 5,
            "min_app_version": "1.2.0",
            "created_at": CREATED,
        }
        base.update(over)
        return BuildInputs(**base)  # type: ignore[arg-type]


def write_json(path: Path, doc: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def artifacts(tmp_path: Path) -> Artifacts:
    model_dir = tmp_path / "ml" / "exports" / "0.3.1"
    model_dir.mkdir(parents=True)
    model = model_dir / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(range(256)) * 40)
    labels = write_json(
        model_dir / "labels.json",
        {
            "classes": ["caries_cavitated", "caries_noncavitated"],
            "input": {"width": 320, "height": 320},
            "license": "Apache-2.0",
            "license_verified": True,
        },
    )
    sha_model = hashlib.sha256(model.read_bytes()).hexdigest()
    sha_labels = hashlib.sha256(labels.read_bytes()).hexdigest()
    (model_dir / "SHA256").write_text(
        f"{sha_model}  model.tflite\n{sha_labels} *labels.json\n", encoding="utf-8"
    )
    (model_dir / "MODEL_CARD.md").write_text("# Model card\nTest model.\n", encoding="utf-8")
    rules = write_json(
        tmp_path / "triage" / "dist" / "rules.json",
        {"version": "2026.10.1", "rules": [{"id": "R1", "params": {"x": 1}}]},
    )
    rec = write_json(
        tmp_path / "recommender" / "dist" / "recommender.json",
        {
            "version": "1.0.0",
            "rules": [{"id": "F1"}],
            "products": [{"name": "Example", "verified": True}],
        },
    )
    return Artifacts(model_dir, rules, rec)


def build_and_sign(key: KeyMaterial, art: Artifacts, **over: object) -> Signed:
    built = build_manifest(art.inputs(**over))
    return sign_manifest(key.private_key, built.manifest, allow_placeholder=False)


def stage(out: Path, art: Artifacts, key: KeyMaterial, **over: object) -> Signed:
    """Build, sign and mirror artifacts into ``out`` like the CLI does."""
    import shutil

    built = build_manifest(art.inputs(**over))
    signed = sign_manifest(key.private_key, built.manifest, allow_placeholder=False)
    for src, rel in built.files:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (out / "manifest.json").write_bytes(signed.manifest_bytes)
    (out / "manifest.sig").write_bytes(signed.sig_bytes)
    return signed


def policy(**over: object) -> Policy:
    base: dict[str, object] = {"now": CREATED}
    base.update(over)
    return Policy(**base)  # type: ignore[arg-type]


def sample_doc(key_id: str, **over: JsonValue) -> dict[str, JsonValue]:
    def comp(name: str, extra: tuple[str, ...]) -> dict[str, JsonValue]:
        d: dict[str, JsonValue] = {
            "version": "1.0.0",
            "url": f"{BASE_URL}/{name}/1.0.0/a.bin",
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "size_bytes": 10,
        }
        for prefix in extra:
            d[f"{prefix}url"] = f"{BASE_URL}/{name}/1.0.0/{prefix}b.bin"
            d[f"{prefix}sha256"] = hashlib.sha256((name + prefix).encode()).hexdigest()
            d[f"{prefix}size_bytes"] = 20
        return d

    model = comp("model", ("labels_",))
    model["license"] = "Apache-2.0"
    doc: dict[str, JsonValue] = {
        "schema_version": 1,
        "sequence": 3,
        "created_at": "2026-10-08T00:00:00Z",
        "min_app_version": "1.0.0",
        "signer_key_id": key_id,
        "channel": "production",
        "model": model,
        "rules": comp("rules", ()),
        "recommender": comp("recommender", ()),
    }
    doc.update(over)
    return doc
