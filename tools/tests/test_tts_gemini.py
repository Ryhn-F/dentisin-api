"""Gemini TTS provider tests. HTTP is fully mocked: no real network, no real key."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from tts import generate_audio as ga

KEY = "gemini-key-DO-NOT-LEAK-123"
MODEL = "gemini-test-tts"
VOICE = "Kore"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

GOOD_CSV = (
    "id,text,approved_by\n"
    'brush.start,"Ayo sikat gigi! Gosok pelan-pelan.",drg. Contoh Satu\n'
    'brush.rinse,"Kumur dengan air putih & selesai.",drg. Contoh Satu\n'
)


def pcm(seconds: float = 0.5, rate: int = 24000) -> bytes:
    return b"\x01\x00" * int(seconds * rate)


def ok_response(audio: bytes | None = None, mime: str = "audio/L16;codec=pcm;rate=24000") -> Any:
    data = pcm() if audio is None else audio
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
                    ]
                }
            }
        ]
    }


class FakeGemini:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.script: list[httpx.Response] = []  # consumed first; then default success

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            return self.script.pop(0)
        return httpx.Response(200, json=ok_response())


class Env:
    def __init__(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.dir = tmp_path / "audio"
        self.dir.mkdir()
        self.script = self.dir / "script.csv"
        self.script.write_text(GOOD_CSV, encoding="utf-8")
        self.fake = FakeGemini()
        self.capsys = capsys
        self.environ = {
            "GEMINI_API_KEY": KEY,
            "GEMINI_TTS_MODEL": MODEL,
            "GEMINI_TTS_VOICE": VOICE,
        }
        self.sleeps: list[float] = []

    def run(self, *args: str, under: str = "free") -> tuple[int, str]:
        client = httpx.Client(transport=httpx.MockTransport(self.fake.handler))
        argv = ["--script", str(self.script), "--generated-under", under, *args]
        code = ga.main(argv, self.environ, client, self.sleeps.append)
        captured = self.capsys.readouterr()
        return code, captured.out + captured.err

    def manifest(self) -> dict[str, Any]:
        data = json.loads((self.dir / "MANIFEST.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data


@pytest.fixture
def env(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Env:
    return Env(tmp_path, capsys)


def read_wav(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


# --- success path ------------------------------------------------------------------------


def test_gemini_is_default_and_produces_valid_wav(env: Env) -> None:
    code, out = env.run()  # no --provider: gemini is the default
    assert code == 0
    assert len(env.fake.requests) == 2
    assert read_wav(env.dir / "brush.start.wav") == (1, 2, 24000, 12000)
    assert KEY not in out


def test_request_shape(env: Env) -> None:
    env.run()
    req = env.fake.requests[1]
    assert str(req.url) == URL  # exact URL: no key, no query string
    assert req.url.query == b""
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["contents"] == [{"parts": [{"text": "Kumur dengan air putih & selesai."}]}]
    gc = body["generationConfig"]
    assert gc["responseModalities"] == ["AUDIO"]
    assert gc["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == VOICE


def test_text_is_sent_unmodified_even_with_style(env: Env) -> None:
    env.environ["GEMINI_TTS_STYLE"] = "calm and clear"
    env.run()
    body = json.loads(env.fake.requests[0].content)
    part = body["contents"][0]["parts"][0]
    assert part["text"] == "Ayo sikat gigi! Gosok pelan-pelan."
    assert part["speechMetadata"] == {"style": "calm and clear"}


def test_env_provider_selects_azure_and_flag_wins(env: Env) -> None:
    env.environ["TTS_PROVIDER"] = "azure"
    code, out = env.run("--dry-run", under="student")
    assert code == 0 and "provider azure" in out
    code, out = env.run("--dry-run", "--provider", "gemini")
    assert code == 0 and "provider gemini" in out
    env.environ["TTS_PROVIDER"] = "nope"
    assert env.run("--dry-run", "--provider", "gemini")[0] == 0
    assert env.run("--dry-run")[0] == ga.EXIT_USAGE


def test_wav_returned_by_api_is_used_as_is(env: Env) -> None:
    wav = ga.pcm_to_wav(pcm(0.3))
    env.fake.script = [
        httpx.Response(200, json=ok_response(wav, "audio/wav")),
        httpx.Response(200, json=ok_response(wav, "audio/wav")),
    ]
    assert env.run()[0] == 0
    assert (env.dir / "brush.start.wav").read_bytes() == wav


def test_snake_case_response_is_accepted() -> None:
    b64 = base64.b64encode(pcm()).decode()
    data = {"candidates": [{"content": {"parts": [{"inline_data": {"data": b64}}]}}]}
    assert ga._extract_inline_audio(data) == (b64, "")


# --- failures ----------------------------------------------------------------------------


def test_missing_key_is_usage_error_and_no_request(env: Env) -> None:
    del env.environ["GEMINI_API_KEY"]
    code, out = env.run()
    assert code == ga.EXIT_USAGE and "GEMINI_API_KEY" in out
    assert env.fake.requests == []


def test_empty_audio_fails_and_writes_nothing(env: Env) -> None:
    env.fake.script = [httpx.Response(200, json=ok_response(b""))] * 4
    code, out = env.run()
    assert code == ga.EXIT_SYNTH and "empty audio" in out
    assert not (env.dir / "MANIFEST.json").exists()
    assert not list(env.dir.glob("*.wav"))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"candidates": []},
        {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "OTHER"}]},
        {"promptFeedback": {"blockReason": "SAFETY"}},
        ok_response(b"\x01"),  # odd byte count
        ok_response(pcm(0.01)),  # far too short
        ok_response(pcm(200.0, 8000), "audio/L16;rate=8000"),  # far too long
        ok_response(b"abcd", "audio/mulaw"),
        ok_response(b"RIFFnotawav"),
    ],
)
def test_bad_responses_are_rejected(env: Env, payload: Any) -> None:
    env.fake.script = [httpx.Response(200, json=payload)] * 4
    code, _ = env.run()
    assert code == ga.EXIT_SYNTH
    assert not list(env.dir.glob("*.wav"))


def test_invalid_base64_and_json(env: Env) -> None:
    bad = ok_response()
    bad["candidates"][0]["content"]["parts"][0]["inlineData"]["data"] = "***not-base64***"
    env.fake.script = [httpx.Response(200, json=bad)] * 4
    assert env.run()[0] == ga.EXIT_SYNTH
    env.fake.script = [httpx.Response(200, content=b"<html>")] * 4
    code, out = env.run()
    assert code == ga.EXIT_SYNTH and "invalid JSON" in out


def test_429_then_success_respects_retry_after(env: Env) -> None:
    env.fake.script = [httpx.Response(429, headers={"Retry-After": "7"})]
    code, _ = env.run("--request-delay", "0")
    assert code == 0
    assert 7.0 in env.sleeps
    assert len(env.fake.requests) == 3  # 429 + success + second row


def test_429_without_retry_after_uses_exponential_backoff(env: Env) -> None:
    env.fake.script = [httpx.Response(429), httpx.Response(503)]
    assert env.run("--request-delay", "0")[0] == 0
    assert env.sleeps == [2.0, 4.0]


def test_500_exhaustion_fails_with_clear_code(env: Env) -> None:
    env.fake.script = [httpx.Response(500)] * 4
    code, out = env.run("--request-delay", "0")
    assert code == ga.EXIT_SYNTH and "HTTP 500" in out
    assert len(env.fake.requests) == ga.GEMINI_RETRIES
    assert env.sleeps == [2.0, 4.0, 8.0]
    assert not (env.dir / "MANIFEST.json").exists()


def test_400_is_not_retried_and_message_is_redacted(env: Env) -> None:
    err = {"error": {"message": f"Bad request near {KEY}"}}
    env.fake.script = [httpx.Response(400, json=err)]
    code, out = env.run()
    assert code == ga.EXIT_SYNTH and "HTTP 400" in out
    assert len(env.fake.requests) == 1
    assert KEY not in out and "***" in out


def test_network_error_is_retried_and_does_not_leak(env: Env) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError(f"boom {request.url}", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    code = ga.main(
        ["--script", str(env.script), "--generated-under", "free", "--request-delay", "0"],
        env.environ,
        client,
        env.sleeps.append,
    )
    out = env.capsys.readouterr()
    assert code == ga.EXIT_SYNTH and calls["n"] == ga.GEMINI_RETRIES
    assert "ConnectError" in out.err and KEY not in out.err + out.out


def test_request_cap_stops_run_and_keeps_finished_rows(env: Env) -> None:
    code, out = env.run("--max-requests", "1")
    assert code == ga.EXIT_SYNTH and "Request cap reached" in out
    assert len(env.fake.requests) == 1
    partial = env.manifest()
    assert partial["complete"] is False and [f["id"] for f in partial["files"]] == ["brush.start"]
    # next run only pays for the missing row
    code, _ = env.run()
    assert code == 0 and len(env.fake.requests) == 2
    assert env.manifest()["complete"] is True


def test_delay_between_requests(env: Env) -> None:
    env.run("--request-delay", "3")
    assert env.sleeps == [3.0]  # not before the first request


def test_invalid_model_or_voice_rejected(env: Env) -> None:
    env.environ["GEMINI_TTS_MODEL"] = "../evil?x=1"
    assert env.run("--dry-run")[0] == ga.EXIT_USAGE
    env.environ["GEMINI_TTS_MODEL"] = MODEL
    env.environ["GEMINI_TTS_VOICE"] = 'Ko"re'
    assert env.run("--dry-run")[0] == ga.EXIT_USAGE


def test_undocumented_voice_only_warns(env: Env) -> None:
    env.environ["GEMINI_TTS_VOICE"] = "Newvoice"
    code, out = env.run("--dry-run")
    assert code == 0 and "not in the documented list" in out


# --- validation shared with the CLI contract ---------------------------------------------


def test_unapproved_row_refused_before_any_request(env: Env) -> None:
    env.script.write_text("id,text,approved_by\na.b,Halo teman,\n", encoding="utf-8")
    code, out = env.run()
    assert code == ga.EXIT_SCRIPT and "empty approved_by" in out
    assert env.fake.requests == []


def test_forbidden_phrase_refused_before_any_request(env: Env) -> None:
    env.script.write_text(
        'id,text,approved_by\nx.1,"Gigimu aman dan pasti sehat.",drg. A\n', encoding="utf-8"
    )
    code, _ = env.run()
    assert code == ga.EXIT_SCRIPT and env.fake.requests == []


def test_dry_run_needs_no_key_and_no_network(env: Env) -> None:
    env.environ.clear()
    code, out = env.run("--dry-run")
    assert code == 0 and "no network" in out and "would generate" in out
    assert env.fake.requests == []
    assert sorted(p.name for p in env.dir.iterdir()) == ["script.csv"]


def test_generated_under_values_are_provider_specific(env: Env) -> None:
    assert env.run("--dry-run", under="student")[0] == ga.EXIT_USAGE
    assert env.run("--dry-run", under="payg")[0] == ga.EXIT_USAGE
    assert env.run("--dry-run", under="paid")[0] == 0
    assert env.run("--dry-run", "--provider", "azure", under="free")[0] == ga.EXIT_USAGE


def test_sample_script_gating_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    sample = Path(__file__).resolve().parent.parent / "tts" / "script.sample.csv"
    base = ["--script", str(sample), "--generated-under", "free", "--dry-run"]
    assert ga.main(base, {}) == ga.EXIT_SCRIPT
    assert "placeholder" in capsys.readouterr().err
    assert ga.main([*base, "--allow-sample"], {}) == 0
    assert "7 row(s)" in capsys.readouterr().err


# --- manifest and idempotence ------------------------------------------------------------


def test_manifest_fields(env: Env) -> None:
    code, out = env.run()
    assert code == 0
    m = env.manifest()
    assert m["provider"] == "gemini" and m["model"] == MODEL and m["voice"] == VOICE
    assert m["generated_under"] == "free"
    assert m["ai_voice_label_required"] is True
    assert m["complete"] is True and "region" not in m
    assert m["script_sha256"] == hashlib.sha256(env.script.read_bytes()).hexdigest()
    assert str(m["generated_at"]).endswith("Z")
    assert [f["id"] for f in m["files"]] == ["brush.start", "brush.rinse"]
    for entry in m["files"]:
        data = (env.dir / entry["file"]).read_bytes()
        assert entry["file"].endswith(".wav")
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert entry["bytes"] == len(data)
    assert not [p for p in env.dir.iterdir() if p.name.endswith(".tmp")]
    assert "ADR 012" in out and "free-tier" in out
    assert KEY not in (env.dir / "MANIFEST.json").read_text(encoding="utf-8")


def test_second_run_is_idempotent(env: Env) -> None:
    env.run()
    first = (env.dir / "MANIFEST.json").read_text(encoding="utf-8")
    calls = len(env.fake.requests)
    code, out = env.run()
    assert code == 0 and len(env.fake.requests) == calls
    assert "MANIFEST.json unchanged" in out
    assert (env.dir / "MANIFEST.json").read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    ("var", "value"),
    [("GEMINI_TTS_MODEL", "gemini-other-tts"), ("GEMINI_TTS_VOICE", "Puck")],
)
def test_changing_model_or_voice_regenerates_all(env: Env, var: str, value: str) -> None:
    env.run()
    before = len(env.fake.requests)
    env.environ[var] = value
    code, out = env.run()
    assert code == 0 and "regenerating everything" in out
    assert len(env.fake.requests) == before + 2


def test_changing_generated_under_or_style_regenerates(env: Env) -> None:
    env.run()
    before = len(env.fake.requests)
    env.run(under="paid")
    assert len(env.fake.requests) == before + 2
    env.environ["GEMINI_TTS_STYLE"] = "slow"
    env.run(under="paid")
    assert len(env.fake.requests) == before + 4


def test_changed_text_regenerates_only_that_row(env: Env) -> None:
    env.run()
    before = len(env.fake.requests)
    env.script.write_text(GOOD_CSV.replace("Gosok pelan-pelan.", "Gosok pelan saja."))
    env.run()
    assert len(env.fake.requests) == before + 1
    assert b"pelan saja" in env.fake.requests[-1].content


def test_force_regenerates(env: Env) -> None:
    env.run()
    before = len(env.fake.requests)
    env.run("--force")
    assert len(env.fake.requests) == before + 2


def test_switching_provider_regenerates_and_reports_old_files(env: Env) -> None:
    env.run()
    azure_dir_calls = len(env.fake.requests)
    # azure run in the same folder: needs its own fake transport
    azure_calls: list[httpx.Request] = []

    def azure(request: httpx.Request) -> httpx.Response:
        azure_calls.append(request)
        if request.url.path.endswith("voices/list"):
            return httpx.Response(200, json=[{"ShortName": "id-ID-GadisNeural", "Locale": "id-ID"}])
        return httpx.Response(200, content=b"ID3-audio")

    environ = {"AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "japaneast"}
    code = ga.main(
        ["--provider", "azure", "--script", str(env.script), "--generated-under", "student"],
        environ,
        httpx.Client(transport=httpx.MockTransport(azure)),
        env.sleeps.append,
    )
    err = env.capsys.readouterr().err
    assert code == 0 and "regenerating everything" in err
    assert len(env.fake.requests) == azure_dir_calls
    m = env.manifest()
    assert m["provider"] == "azure" and m["region"] == "japaneast"
    assert "brush.start.wav" in err  # old gemini files reported as stale, not deleted
    assert (env.dir / "brush.start.wav").exists()


def test_input_hash_covers_provider_model_voice() -> None:
    row = ga.Row("a", "teks", "drg")
    base = ga.Plan("gemini", "m", "Kore", "fmt", ".wav")
    hashes = {
        ga.input_hash(row, base),
        ga.input_hash(row, ga.Plan("azure", "m", "Kore", "fmt", ".wav")),
        ga.input_hash(row, ga.Plan("gemini", "m2", "Kore", "fmt", ".wav")),
        ga.input_hash(row, ga.Plan("gemini", "m", "Puck", "fmt", ".wav")),
        ga.input_hash(ga.Row("a", "teks2", "drg"), base),
    }
    assert len(hashes) == 5


def test_pcm_to_wav_roundtrip() -> None:
    data = ga.pcm_to_wav(pcm(0.1), 16000)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
