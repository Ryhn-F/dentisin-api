from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tts import generate_audio as ga

KEY = "azure-speech-key-DO-NOT-LEAK"
REGION = "japaneast"
VOICES_URL = f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/voices/list"
SYNTH_URL = f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/v1"

GOOD_CSV = (
    "id,text,approved_by\n"
    'brush.start,"Ayo sikat gigi! Gosok pelan-pelan.",drg. Contoh Satu\n'
    'brush.rinse,"Kumur dengan air putih & selesai.",drg. Contoh Satu\n'
)


class FakeAzure:
    def __init__(self, voices: list[dict[str, str]] | None = None) -> None:
        self.voices = (
            voices
            if voices is not None
            else [
                {"ShortName": "id-ID-GadisNeural", "Locale": "id-ID"},
                {"ShortName": "id-ID-ArdiNeural", "Locale": "id-ID"},
                {"ShortName": "en-US-JennyNeural", "Locale": "en-US"},
            ]
        )
        self.synth_bodies: list[str] = []
        self.voices_status = 200
        self.synth_statuses: list[int] = []
        self.all_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.all_requests.append(request)
        assert request.headers["Ocp-Apim-Subscription-Key"] == KEY
        if str(request.url) == VOICES_URL:
            if self.voices_status != 200:
                return httpx.Response(self.voices_status)
            return httpx.Response(200, json=self.voices)
        assert str(request.url) == SYNTH_URL
        assert request.headers["X-Microsoft-OutputFormat"] == ga.OUTPUT_FORMAT
        body = request.content.decode("utf-8")
        self.synth_bodies.append(body)
        status = self.synth_statuses.pop(0) if self.synth_statuses else 200
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, content=b"ID3-fake-audio:" + body.encode("utf-8"))


@pytest.fixture
def fake() -> FakeAzure:
    return FakeAzure()


class Env:
    def __init__(self, tmp_path: Path, fake: FakeAzure, capsys: pytest.CaptureFixture[str]) -> None:
        self.dir = tmp_path / "audio"
        self.dir.mkdir()
        self.script = self.dir / "script.csv"
        self.script.write_text(GOOD_CSV, encoding="utf-8")
        self.fake = fake
        self.capsys = capsys
        self.environ = {
            "AZURE_SPEECH_KEY": KEY,
            "AZURE_SPEECH_REGION": REGION,
            "AZURE_SPEECH_VOICE": "id-ID-GadisNeural",
        }
        self.sleeps: list[float] = []

    def run(self, *args: str, under: str = "student") -> tuple[int, str]:
        client = httpx.Client(transport=httpx.MockTransport(self.fake.handler))
        argv = [
            "--provider",
            "azure",
            "--script",
            str(self.script),
            "--generated-under",
            under,
            *args,
        ]
        code = ga.main(argv, self.environ, client, self.sleeps.append)
        captured = self.capsys.readouterr()
        return code, captured.out + captured.err

    def manifest(self) -> dict[str, object]:
        data = json.loads((self.dir / "MANIFEST.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data


@pytest.fixture
def env(tmp_path: Path, fake: FakeAzure, capsys: pytest.CaptureFixture[str]) -> Env:
    return Env(tmp_path, fake, capsys)


# --- validation --------------------------------------------------------------------------


def test_empty_approved_by_is_refused(env: Env) -> None:
    env.script.write_text("id,text,approved_by\na.b,Halo teman,\n", encoding="utf-8")
    code, out = env.run()
    assert code == ga.EXIT_SCRIPT
    assert "empty approved_by" in out and "a.b" in out
    assert env.fake.all_requests == []


def test_whitespace_only_approver_is_refused(env: Env) -> None:
    env.script.write_text("id,text,approved_by\na.b,Halo teman,   \n", encoding="utf-8")
    assert env.run()[0] == ga.EXIT_SCRIPT


@pytest.mark.parametrize("who", ["CONTOH", "sample", "TODO drg", "placeholder"])
def test_placeholder_approver_refused_unless_allowed(env: Env, who: str) -> None:
    env.script.write_text(f"id,text,approved_by\na.b,Halo teman,{who}\n", encoding="utf-8")
    code, out = env.run("--dry-run")
    assert code == ga.EXIT_SCRIPT and "placeholder" in out
    assert env.run("--dry-run", "--allow-sample")[0] == 0


def test_forbidden_phrase_in_text_is_refused(env: Env) -> None:
    env.script.write_text(
        'id,text,approved_by\nx.1,"Gigimu aman dan pasti sehat.",drg. A\n', encoding="utf-8"
    )
    code, out = env.run()
    assert code == ga.EXIT_SCRIPT
    assert "x.1" in out and "forbidden" in out
    assert env.fake.all_requests == []


def test_mandatory_disclaimer_is_allowed(env: Env) -> None:
    env.script.write_text(
        'id,text,approved_by\nr.1,"Hasil ini bukan diagnosis dan tidak menggantikan '
        'pemeriksaan dokter gigi.",drg. A\n',
        encoding="utf-8",
    )
    assert env.run("--dry-run")[0] == 0


@pytest.mark.parametrize(
    "csv_text",
    [
        "id,text\na,b\n",  # missing column
        "id,text,approved_by\n",  # no rows
        "id,text,approved_by\nA B,hai,x\n",  # bad id
        "id,text,approved_by\na,hai,x\na,lagi,x\n",  # duplicate
        "id,text,approved_by\na,,x\n",  # empty text
        "id,text,approved_by\na," + "x" * 401 + ",x\n",  # too long
    ],
)
def test_structural_errors(env: Env, csv_text: str) -> None:
    env.script.write_text(csv_text, encoding="utf-8")
    assert env.run("--dry-run")[0] == ga.EXIT_SCRIPT


def test_missing_script_is_usage_error(env: Env) -> None:
    env.script.unlink()
    assert env.run()[0] == ga.EXIT_USAGE


def test_generated_under_is_required_and_validated(env: Env) -> None:
    with pytest.raises(SystemExit):
        ga.main(["--provider", "azure", "--script", str(env.script)], env.environ)
    with pytest.raises(SystemExit):
        ga.main(["--script", str(env.script), "--generated-under", "bogus"], env.environ)
    # gemini values are not valid for azure (and vice versa)
    assert env.run(under="free")[0] == ga.EXIT_USAGE
    assert (
        ga.main(["--script", str(env.script), "--generated-under", "student"], {}) == ga.EXIT_USAGE
    )


# --- dry run -----------------------------------------------------------------------------


def test_dry_run_makes_no_network_call_and_needs_no_credentials(env: Env) -> None:
    env.environ.clear()
    code, out = env.run("--dry-run")
    assert code == 0
    assert env.fake.all_requests == []
    assert "no network" in out
    assert "would generate" in out
    assert sorted(p.name for p in env.dir.iterdir()) == ["script.csv"]


# --- voice check -------------------------------------------------------------------------


def test_voice_missing_in_region_fails_clearly(env: Env) -> None:
    env.fake.voices = [{"ShortName": "id-ID-ArdiNeural", "Locale": "id-ID"}]
    code, out = env.run()
    assert code == ga.EXIT_VOICE
    assert "id-ID-GadisNeural" in out and "NOT available" in out
    assert "id-ID-ArdiNeural" in out  # lists what exists
    assert env.fake.synth_bodies == []


def test_no_indonesian_voices_at_all(env: Env) -> None:
    env.fake.voices = [{"ShortName": "en-US-JennyNeural", "Locale": "en-US"}]
    code, out = env.run()
    assert code == ga.EXIT_VOICE and "none" in out


@pytest.mark.parametrize("status", [401, 403, 500])
def test_voices_list_http_errors(env: Env, status: int) -> None:
    env.fake.voices_status = status
    code, out = env.run()
    assert code == ga.EXIT_VOICE and str(status) in out
    assert KEY not in out


def test_voice_override_must_be_indonesian(env: Env) -> None:
    env.environ["AZURE_SPEECH_VOICE"] = "en-US-JennyNeural"
    assert env.run("--dry-run")[0] == ga.EXIT_USAGE


def test_missing_credentials_named(env: Env) -> None:
    del env.environ["AZURE_SPEECH_KEY"]
    code, out = env.run()
    assert code == ga.EXIT_USAGE and "AZURE_SPEECH_KEY" in out


def test_region_injection_rejected(env: Env) -> None:
    env.environ["AZURE_SPEECH_REGION"] = "evil.com/x"
    assert env.run()[0] == ga.EXIT_USAGE


def test_unexpected_region_only_warns(env: Env) -> None:
    env.environ["AZURE_SPEECH_REGION"] = "southeastasia"
    url_voices = "https://southeastasia.tts.speech.microsoft.com/cognitiveservices/voices/list"
    original = env.fake.handler

    def rewrite(request: httpx.Request) -> httpx.Response:
        if str(request.url) == url_voices:
            return httpx.Response(200, json=env.fake.voices)
        return httpx.Response(200, content=b"audio")

    env.fake.handler = rewrite  # type: ignore[method-assign]
    code, out = env.run()
    env.fake.handler = original  # type: ignore[method-assign]
    assert code == 0 and "not japaneast/japanwest" in out


# --- generation, manifest, idempotence ---------------------------------------------------


def test_generates_files_and_manifest(env: Env) -> None:
    code, out = env.run()
    assert code == 0
    assert "voice 'id-ID-GadisNeural' is available in 'japaneast'" in out
    assert KEY not in out
    m = env.manifest()
    assert m["voice"] == "id-ID-GadisNeural"
    assert m["provider"] == "azure" and m["ai_voice_label_required"] is True
    assert m["complete"] is True
    assert m["generated_under"] == "student"
    assert m["region"] == REGION
    assert m["ai_voice_disclosure_required"] is True
    assert m["script_sha256"] == hashlib.sha256(env.script.read_bytes()).hexdigest()
    assert str(m["generated_at"]).endswith("Z")
    files = m["files"]
    assert isinstance(files, list) and [f["id"] for f in files] == ["brush.start", "brush.rinse"]
    for entry in files:
        data = (env.dir / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert entry["bytes"] == len(data)
        assert entry["approved_by"] == "drg. Contoh Satu"
    assert not [p for p in env.dir.iterdir() if p.name.endswith(".tmp")]


def test_ssml_has_voice_and_escapes_text(env: Env) -> None:
    env.run()
    body = env.fake.synth_bodies[1]
    assert "name='id-ID-GadisNeural'" in body
    assert "air putih &amp; selesai" in body
    assert "xml:lang='id-ID'" in body


def test_second_run_is_idempotent(env: Env) -> None:
    env.run()
    first_manifest = (env.dir / "MANIFEST.json").read_text(encoding="utf-8")
    calls = len(env.fake.synth_bodies)
    code, out = env.run()
    assert code == 0
    assert len(env.fake.synth_bodies) == calls  # no new synthesis
    assert "MANIFEST.json unchanged" in out
    assert (env.dir / "MANIFEST.json").read_text(encoding="utf-8") == first_manifest


def test_changed_text_regenerates_only_that_row(env: Env) -> None:
    env.run()
    before = len(env.fake.synth_bodies)
    env.script.write_text(
        GOOD_CSV.replace("Gosok pelan-pelan.", "Gosok pelan saja."), encoding="utf-8"
    )
    env.run()
    assert len(env.fake.synth_bodies) == before + 1
    assert "pelan saja" in env.fake.synth_bodies[-1]


def test_changed_approver_updates_manifest_without_resynthesis(env: Env) -> None:
    env.run()
    before = len(env.fake.synth_bodies)
    env.script.write_text(GOOD_CSV.replace("Contoh Satu", "Contoh Dua"), encoding="utf-8")
    env.run()
    assert len(env.fake.synth_bodies) == before
    files = env.manifest()["files"]
    assert isinstance(files, list) and files[0]["approved_by"] == "drg. Contoh Dua"


def test_switching_to_payg_regenerates_everything(env: Env) -> None:
    env.run(under="student")
    before = len(env.fake.synth_bodies)
    code, out = env.run(under="payg")
    assert code == 0
    assert len(env.fake.synth_bodies) == before + 2
    assert env.manifest()["generated_under"] == "payg"
    assert "regenerating everything" in out


def test_force_regenerates(env: Env) -> None:
    env.run()
    before = len(env.fake.synth_bodies)
    env.run("--force")
    assert len(env.fake.synth_bodies) == before + 2


def test_tampered_audio_file_is_regenerated(env: Env) -> None:
    env.run()
    (env.dir / "brush.start.mp3").write_bytes(b"corrupt")
    before = len(env.fake.synth_bodies)
    env.run()
    assert len(env.fake.synth_bodies) == before + 1


def test_stale_audio_is_reported_not_deleted(env: Env) -> None:
    (env.dir / "old.mp3").write_bytes(b"x")
    code, out = env.run()
    assert code == 0 and "old.mp3" in out
    assert (env.dir / "old.mp3").exists()


def test_synthesis_retries_then_fails(env: Env) -> None:
    env.fake.synth_statuses = [503, 503, 503]
    code, out = env.run()
    assert code == ga.EXIT_SYNTH and "HTTP 503" in out
    assert env.sleeps == [2.0, 4.0]
    assert not (env.dir / "MANIFEST.json").exists()


def test_synthesis_recovers_after_429(env: Env) -> None:
    env.fake.synth_statuses = [429]
    assert env.run()[0] == 0


def test_synthesis_400_not_retried(env: Env) -> None:
    env.fake.synth_statuses = [400]
    code, _ = env.run()
    assert code == ga.EXIT_SYNTH
    assert len(env.fake.synth_bodies) == 1


def test_student_reminder_printed(env: Env) -> None:
    _, out = env.run(under="student")
    assert "non-commercial" in out
    _, out2 = env.run("--force", under="payg")
    assert "non-commercial" not in out2


def test_build_ssml_unit() -> None:
    assert ga.build_ssml("a<b", "id-ID-ArdiNeural").count("a&lt;b") == 1


def test_sample_script_dry_run_passes(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    sample = Path(__file__).resolve().parent.parent / "tts" / "script.sample.csv"
    code = ga.main(
        [
            "--provider",
            "azure",
            "--script",
            str(sample),
            "--generated-under",
            "student",
            "--dry-run",
            "--allow-sample",
        ],
        {},
    )
    assert code == 0
    assert "7 row(s)" in capsys.readouterr().err


def test_sample_script_refused_without_allow_sample(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = Path(__file__).resolve().parent.parent / "tts" / "script.sample.csv"
    code = ga.main(
        [
            "--provider",
            "azure",
            "--script",
            str(sample),
            "--generated-under",
            "student",
            "--dry-run",
        ],
        {},
    )
    assert code == ga.EXIT_SCRIPT
    capsys.readouterr()


def test_default_script_is_bundled_sample_and_env_overrides(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    # no --script, no env: bundled sample (placeholder approvers -> refused without --allow-sample)
    code = ga.main(["--provider", "azure", "--generated-under", "student", "--dry-run"], {})
    assert code == ga.EXIT_SCRIPT
    assert "placeholder" in capsys.readouterr().err
    # TTS_SCRIPT_PATH points to the app's script (outside backend/)
    code = ga.main(
        ["--provider", "azure", "--generated-under", "student", "--dry-run"],
        {"TTS_SCRIPT_PATH": str(env.script)},
    )
    assert code == 0
    assert "2 row(s)" in capsys.readouterr().err
    # --script wins over the environment
    other = env.dir / "other.csv"
    other.write_text("id,text,approved_by\nx.1,Halo,drg. A\n", encoding="utf-8")
    code = ga.main(
        [
            "--provider",
            "azure",
            "--generated-under",
            "student",
            "--dry-run",
            "--script",
            str(other),
        ],
        {"TTS_SCRIPT_PATH": str(env.script)},
    )
    assert code == 0
    assert "1 row(s)" in capsys.readouterr().err
