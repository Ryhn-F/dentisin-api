from __future__ import annotations

import csv
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

import export_dataset as ed

SERVICE_KEY = "service-role-key-DO-NOT-LEAK"
URL = "https://proj.supabase.co"
OWNER_OK = "11111111-1111-1111-1111-111111111111"
OWNER_WITHDRAWN = "22222222-2222-2222-2222-222222222222"
OWNER_NONE = "33333333-3333-3333-3333-333333333333"
OWNER_REGRANT = "44444444-4444-4444-4444-444444444444"


def jpeg(tag: bytes = b"A", *, exif: bool = False) -> bytes:
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    exif_seg = b""
    if exif:
        payload = b"Exif\x00\x00" + b"II*\x00" * 3
        exif_seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return b"\xff\xd8" + app0 + exif_seg + b"\xff\xda\x00\x04\x00\x00" + tag * 50 + b"\xff\xd9"


class FakeSupabase:
    def __init__(self) -> None:
        self.flag: list[dict[str, Any]] = [{"key": "dataset_contribution", "enabled": True}]
        self.consents: list[dict[str, Any]] = [
            {"guardian_id": OWNER_OK, "kind": "c", "text_version": "v2", "withdrawn_at": None},
            {"guardian_id": OWNER_OK, "kind": "a", "text_version": "v1", "withdrawn_at": None},
            {
                "guardian_id": OWNER_WITHDRAWN,
                "kind": "c",
                "text_version": "v1",
                "withdrawn_at": "2026-09-01T00:00:00Z",
            },
            {
                "guardian_id": OWNER_REGRANT,
                "kind": "c",
                "text_version": "v1",
                "granted_at": "2026-01-01T00:00:00Z",
                "withdrawn_at": "2026-02-01T00:00:00Z",
            },
            {
                "guardian_id": OWNER_REGRANT,
                "kind": "c",
                "text_version": "v3",
                "granted_at": "2026-03-01T00:00:00Z",
                "withdrawn_at": None,
            },
        ]
        self.uploads: list[dict[str, Any]] = [
            {"guardian_id": OWNER_OK, "storage_path": f"{OWNER_OK}/p1.jpg"},
            {"guardian_id": OWNER_OK, "storage_path": f"{OWNER_OK}/p2.jpg", "withdrawn": True},
            {"guardian_id": OWNER_WITHDRAWN, "storage_path": f"{OWNER_WITHDRAWN}/p3.jpg"},
            {"guardian_id": OWNER_NONE, "storage_path": f"{OWNER_NONE}/p4.jpg"},
            {"guardian_id": OWNER_REGRANT, "storage_path": f"{OWNER_REGRANT}/p5.jpg"},
            {"guardian_id": OWNER_OK, "storage_path": "../escape.jpg"},
        ]
        self.objects: dict[str, bytes] = {
            f"{OWNER_OK}/p1.jpg": jpeg(b"1"),
            f"{OWNER_OK}/p2.jpg": jpeg(b"2"),
            f"{OWNER_WITHDRAWN}/p3.jpg": jpeg(b"3"),
            f"{OWNER_NONE}/p4.jpg": jpeg(b"4"),
            f"{OWNER_REGRANT}/p5.jpg": jpeg(b"5"),
        }
        self.fail_table: str | None = None
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["apikey"] == SERVICE_KEY
        path = request.url.path
        if path.startswith("/rest/v1/"):
            table = path.rsplit("/", 1)[1]
            if table == self.fail_table:
                return httpx.Response(500, json={"message": "secret row data"})
            rows = {
                "feature_flag": self.flag,
                "consent": self.consents,
                "image_upload": self.uploads,
            }[table]
            start = int(request.headers["Range"].split("-")[0])
            return httpx.Response(206, json=rows[start : start + ed.PAGE_SIZE])
        prefix = f"/storage/v1/object/authenticated/{ed.BUCKET}/"
        assert path.startswith(prefix)
        obj = self.objects.get(httpx.URL(path).path[len(prefix) :])
        return httpx.Response(200, content=obj) if obj is not None else httpx.Response(404)


@pytest.fixture
def fake() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def run(
    fake: FakeSupabase, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, str]]:
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    def _run(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        environ = {
            "SUPABASE_URL": URL,
            "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
            "DATASET_EXPORT_DIR": str(export_dir),
        }
        if env is not None:
            environ = env
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        code = ed.main(list(args), environ, client)
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    _run.export_dir = export_dir  # type: ignore[attr-defined]
    return _run


def manifest_rows(run: Callable[..., Any]) -> list[dict[str, str]]:
    with (run.export_dir / "manifest.csv").open(newline="", encoding="utf-8") as fh:  # type: ignore[attr-defined]
        return list(csv.DictReader(fh))


# --- refusals ----------------------------------------------------------------------------


def test_refuses_without_ethics_flag(run: Callable[..., tuple[int, str]]) -> None:
    code, out = run()
    assert code == ed.EXIT_REFUSED
    assert "--i-have-ethics-approval" in out


def test_refuses_when_feature_flag_off(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    fake.flag = [{"key": "dataset_contribution", "enabled": False}]
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == ed.EXIT_REFUSED
    assert "dataset_contribution" in out
    assert not any("/storage/" in str(r.url) for r in fake.requests)


def test_refuses_when_flag_row_missing(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    fake.flag = []
    assert run("--i-have-ethics-approval")[0] == ed.EXIT_REFUSED


def test_refuses_export_dir_inside_repo(run: Callable[..., tuple[int, str]]) -> None:
    inside = Path(__file__).resolve().parent  # tools/tests is inside the repo
    env = {
        "SUPABASE_URL": URL,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "DATASET_EXPORT_DIR": str(inside),
    }
    code, out = run("--i-have-ethics-approval", env=env)
    assert code == ed.EXIT_REFUSED
    assert "inside the backend/repository" in out


def test_refuses_missing_export_dir(run: Callable[..., tuple[int, str]], tmp_path: Path) -> None:
    env = {
        "SUPABASE_URL": URL,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "DATASET_EXPORT_DIR": str(tmp_path / "nope"),
    }
    code, out = run("--i-have-ethics-approval", env=env)
    assert code == ed.EXIT_REFUSED
    assert "does not exist" in out


def test_missing_env_vars_named_not_valued(run: Callable[..., tuple[int, str]]) -> None:
    code, out = run("--i-have-ethics-approval", env={"SUPABASE_URL": URL})
    assert code == ed.EXIT_USAGE
    assert "SUPABASE_SERVICE_ROLE_KEY" in out and "DATASET_EXPORT_DIR" in out


def test_unset_export_dir_refused(run: Callable[..., tuple[int, str]]) -> None:
    env = {"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY, "DATASET_EXPORT_DIR": ""}
    assert run("--i-have-ethics-approval", env=env)[0] == ed.EXIT_USAGE


def test_http_url_rejected(run: Callable[..., tuple[int, str]], tmp_path: Path) -> None:
    env = {
        "SUPABASE_URL": "http://proj.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "DATASET_EXPORT_DIR": str(tmp_path),
    }
    assert run("--i-have-ethics-approval", env=env)[0] == ed.EXIT_USAGE


def test_dry_run_and_execute_are_mutually_exclusive(run: Callable[..., tuple[int, str]]) -> None:
    with pytest.raises(SystemExit):
        run("--i-have-ethics-approval", "--dry-run", "--execute")


# --- eligibility -------------------------------------------------------------------------


def test_dry_run_is_default_and_downloads_nothing(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    code, out = run("--i-have-ethics-approval")
    assert code == 0
    assert "dry-run" in out
    assert "eligible: 2 photo(s) from 2 consenting owner(s)" in out
    assert "1 withdrawn" in out and "2 without active consent" in out
    assert "1 with invalid path" in out
    assert not any("/storage/" in str(r.url) for r in fake.requests)
    assert list(run.export_dir.iterdir()) == []  # type: ignore[attr-defined]
    assert "ENCRYPTED disk" in out


def test_execute_exports_only_consented_and_writes_manifest(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == 0
    rows = manifest_rows(run)
    assert len(rows) == 2
    assert list(rows[0].keys()) == ["uuid", "sha256", "bytes", "consent_version", "exported_at"]
    assert {r["consent_version"] for r in rows} == {"v2", "v3"}
    expected = {
        hashlib.sha256(fake.objects[f"{o}/{p}"]).hexdigest()
        for o, p in ((OWNER_OK, "p1.jpg"), (OWNER_REGRANT, "p5.jpg"))
    }
    assert {r["sha256"] for r in rows} == expected
    export_dir: Path = run.export_dir  # type: ignore[attr-defined]
    for r in rows:
        data = (export_dir / f"{r['uuid']}.jpg").read_bytes()
        assert hashlib.sha256(data).hexdigest() == r["sha256"]
        assert int(r["bytes"]) == len(data)
        assert re.fullmatch(r"[0-9a-f-]{36}", r["uuid"])
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["exported_at"])
    # no user ids anywhere in files or logs
    blob = (export_dir / "manifest.csv").read_text(encoding="utf-8") + out
    for owner in (OWNER_OK, OWNER_WITHDRAWN, OWNER_NONE, OWNER_REGRANT):
        assert owner not in blob
    assert SERVICE_KEY not in out
    assert sorted(p.name for p in export_dir.iterdir()) == sorted(
        ["manifest.csv", *(f"{r['uuid']}.jpg" for r in rows)]
    )


def test_second_execute_does_not_duplicate(run: Callable[..., tuple[int, str]]) -> None:
    run("--i-have-ethics-approval", "--execute")
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == 0
    assert "exported=0" in out and "duplicate=2" in out
    assert len(manifest_rows(run)) == 2


def test_consent_without_ordering_fails_closed_if_any_withdrawn() -> None:
    rows: list[dict[str, Any]] = [
        {"guardian_id": "o", "kind": "c", "text_version": "v1", "withdrawn_at": "2026-01-01"},
        {"guardian_id": "o", "kind": "c", "text_version": "v2", "withdrawn_at": None},
    ]
    assert ed.active_consents(rows) == {}


def test_consent_boolean_withdrawn_column_and_kind_filter() -> None:
    rows: list[dict[str, Any]] = [
        {"guardian_id": "o1", "kind": "c", "text_version": "v1", "withdrawn": True},
        {"guardian_id": "o2", "kind": "b", "text_version": "v1"},
        {"guardian_id": "o3", "kind": "c"},
    ]
    assert ed.active_consents(rows) == {"o3": "unknown"}


def test_exif_photo_skipped_unless_allowed(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    fake.objects[f"{OWNER_OK}/p1.jpg"] = jpeg(b"1", exif=True)
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == 0
    assert "metadata=1" in out and "EXIF" in out
    assert len(manifest_rows(run)) == 1


def test_allow_metadata(run: Callable[..., tuple[int, str]], fake: FakeSupabase) -> None:
    fake.objects[f"{OWNER_OK}/p1.jpg"] = jpeg(b"1", exif=True)
    run("--i-have-ethics-approval", "--execute", "--allow-metadata")
    assert len(manifest_rows(run)) == 2


def test_missing_object_and_non_jpeg_are_skipped(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    del fake.objects[f"{OWNER_OK}/p1.jpg"]
    fake.objects[f"{OWNER_REGRANT}/p5.jpg"] = b"<html>not a photo</html>"
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == 0
    assert "missing=1" in out and "invalid=1" in out and "exported=0" in out


def test_api_error_aborts_and_exports_nothing(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    fake.fail_table = "consent"
    code, out = run("--i-have-ethics-approval", "--execute")
    assert code == ed.EXIT_API
    assert "secret row data" not in out
    assert not (run.export_dir / "manifest.csv").exists()  # type: ignore[attr-defined]


def test_download_error_gives_partial_exit(
    run: Callable[..., tuple[int, str]], fake: FakeSupabase
) -> None:
    original = fake.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if "p1.jpg" in request.url.path:
            return httpx.Response(500)
        return original(request)

    fake.handler = flaky  # type: ignore[method-assign]
    code, _ = run("--i-have-ethics-approval", "--execute")
    assert code == ed.EXIT_PARTIAL


def test_pagination(fake: FakeSupabase) -> None:
    fake.uploads = [
        {"guardian_id": OWNER_OK, "storage_path": f"{OWNER_OK}/{i}.jpg"}
        for i in range(ed.PAGE_SIZE + 5)
    ]
    client = httpx.Client(
        base_url=URL, headers={"apikey": SERVICE_KEY}, transport=httpx.MockTransport(fake.handler)
    )
    assert len(ed.fetch_all(client, "image_upload", {"select": "*"})) == ed.PAGE_SIZE + 5


@pytest.mark.parametrize("bad", ["", "/abs.jpg", "a/../b.jpg", "a//b.jpg", "\\x.jpg", None, 5])
def test_unsafe_object_paths_rejected(bad: object) -> None:
    assert ed.safe_object_path(bad) is None


def test_has_embedded_metadata_edge_cases() -> None:
    assert not ed.has_embedded_metadata(b"")
    assert not ed.has_embedded_metadata(b"\xff\xd8")
    assert not ed.has_embedded_metadata(jpeg())
    assert ed.has_embedded_metadata(jpeg(exif=True))
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x/>"
    data = b"\xff\xd8" + b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp + b"\xff\xd9"
    assert ed.has_embedded_metadata(data)
    assert not ed.has_embedded_metadata(b"\xff\xd8\xff\xe1\x00")  # truncated header
