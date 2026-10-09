#!/usr/bin/env python3
"""Build-time TTS for child-facing instructions (ADR 010 Gemini default; ADR 007 Azure backup).

Reads a script CSV (columns: id,text,approved_by) and writes one audio file per row plus
``MANIFEST.json`` next to it (override with ``--out-dir``). The production script lives in the
app, outside BACKEND_ROOT (e.g. ``../app/assets/audio/script.csv``): pass ``--script`` or set
``TTS_SCRIPT_PATH``. Default: the bundled ``tools/tts/script.sample.csv`` (needs --allow-sample).

Providers (``--provider gemini|azure``, default gemini, or env TTS_PROVIDER)
  * gemini: Gemini API ``generateContent`` REST, WAV output (PCM 16-bit mono 24 kHz wrapped with
    the stdlib ``wave`` module). Env: GEMINI_API_KEY, GEMINI_TTS_MODEL, GEMINI_TTS_VOICE,
    optional GEMINI_TTS_STYLE. ``--generated-under free|paid``. Only the static approved script
    text is sent (ADR 010): never child data. Retry/backoff on 429/5xx, request cap and a pause
    between requests protect the free-tier quota.
  * azure: Azure Speech REST (ADR 007), MP3 output. Env: AZURE_SPEECH_KEY, AZURE_SPEECH_REGION,
    AZURE_SPEECH_VOICE. ``--generated-under student|payg``. The voices-list endpoint is queried
    first and the run FAILS CLEARLY if the voice is not offered in the region.

Rules this tool enforces (both providers)
  * No TTS at runtime: this runs on a developer PC at build time; the app only ships files.
  * Every row needs a non-empty ``approved_by`` (child-facing text must be approved).
    Placeholder approvers (CONTOH/SAMPLE/TODO/PLACEHOLDER) are refused unless --allow-sample.
  * Every text is linted with tools/lint_forbidden_phrases.py (docs/style/forbidden_phrases.md).
  * ``--generated-under`` is mandatory and recorded in MANIFEST.json together with provider,
    model and voice. Changing any of them forces a full regeneration.
  * The app must disclose the AI voice on the About screen ("suara dibuat dengan AI");
    MANIFEST.json carries ``ai_voice_label_required: true`` (and the older
    ``ai_voice_disclosure_required: true`` for compatibility).
  * Idempotent: unchanged rows (same text, provider, model, voice, format) are skipped.
    --force overrides.
  * ``--dry-run`` validates and reports without any network call or file write.
  * Keys are only sent in request headers and are never logged.

Exit codes: 0 ok, 2 usage/config, 3 script invalid (approval/lint), 4 voice unavailable (azure),
5 synthesis failed.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lint_forbidden_phrases import (  # noqa: E402
    LintConfigError,
    check_sync,
    load_rules,
    resolve_phrases_path,
    scan_text,
)
from opscommon import (  # noqa: E402
    ConfigError,
    backend_root,
    get_env,
    load_env_file,
    require_env,
)

# The production script lives in the app (outside BACKEND_ROOT): ../app/assets/audio/script.csv.
# Point to it with --script or TTS_SCRIPT_PATH; the bundled sample is the self-contained default.
DEFAULT_SCRIPT_REL = "tools/tts/script.sample.csv"
SCRIPT_ENV = "TTS_SCRIPT_PATH"
PROVIDER_ENV = "TTS_PROVIDER"
PROVIDERS = ("gemini", "azure")
DEFAULT_PROVIDER = "gemini"
GENERATED_UNDER: dict[str, tuple[str, ...]] = {
    "gemini": ("free", "paid"),
    "azure": ("student", "payg"),
}
HASH_VERSION = "2"
MAX_TEXT_CHARS = 400
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
PLACEHOLDER_APPROVERS = ("contoh", "sample", "todo", "placeholder")
AUDIO_EXTS = (".mp3", ".wav")

# --- Azure (ADR 007) ---
DEFAULT_VOICE = "id-ID-GadisNeural"
ALLOWED_VOICES = ("id-ID-GadisNeural", "id-ID-ArdiNeural")
EXPECTED_REGIONS = ("japaneast", "japanwest")
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
FILE_EXT = ".mp3"
AZURE_MODEL = "azure-speech-neural"
SSML_VERSION = "1"
REGION_RE = re.compile(r"^[a-z0-9]{3,32}$")

# --- Gemini (ADR 010) ---
# [PERLU VERIFIKASI] Shape checked against ai.google.dev (speech-generation, 2026-10-09): the
# "Generate Content API (Legacy)" page documents POST .../models/{model}:generateContent with
# header x-goog-api-key, generationConfig.responseModalities=["AUDIO"], speechConfig, response in
# candidates[0].content.parts[0].inlineData.data (base64; audio/wav default, or raw 24 kHz mono
# 16-bit PCM). The newest docs steer to the Interactions API and Google may rename the voice
# field (voiceConfig.prebuiltVoiceConfig.voiceName is used here, as in earlier docs). Model and
# voice names below were listed by the docs on that date; they change often, so the env vars
# GEMINI_TTS_MODEL / GEMINI_TTS_VOICE override them. Not tested against the live API.
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = (
    "gemini-3.8-flash-tts"  # [PERLU VERIFIKASI] older: gemini-2.5-flash-preview-tts
)
GEMINI_DEFAULT_VOICE = "Kore"  # [PERLU VERIFIKASI] pick by ear; docs list 30 prebuilt voices
GEMINI_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe",
    "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib",
    "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima",
    "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)  # fmt: skip
GEMINI_OUTPUT_FORMAT = "wav-pcm-s16le-24000hz-mono"
GEMINI_EXT = ".wav"
GEMINI_SAMPLE_RATE = 24000
GEMINI_DEFAULT_DELAY = 6.0  # seconds between requests (free-tier TTS RPM is low)
GEMINI_DEFAULT_MAX_REQUESTS = 100
GEMINI_TIMEOUT = httpx.Timeout(90.0, connect=15.0)
GEMINI_RETRIES = 4
GEMINI_RETRY_STATUS = (429, 500, 502, 503, 504)
MIN_AUDIO_SECONDS = 0.2
MAX_AUDIO_SECONDS = 180.0
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
GEMINI_VOICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SCRIPT = 3
EXIT_VOICE = 4
EXIT_SYNTH = 5


class TtsError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Row:
    id: str
    text: str
    approved_by: str


@dataclass(frozen=True)
class Plan:
    """Everything that decides what the audio sounds like (hashed per row)."""

    provider: str
    model: str
    voice: str
    output_format: str
    file_ext: str
    region: str = ""
    style: str = ""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_script(path: Path) -> tuple[list[Row], str]:
    """Parse and structurally validate the CSV. Returns rows and the file's sha256."""
    if not path.is_file():
        raise TtsError(f"Script not found: {path}", EXIT_USAGE)
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    missing = [c for c in ("id", "text", "approved_by") if c not in fields]
    if missing:
        raise TtsError(f"Script is missing column(s): {', '.join(missing)}", EXIT_SCRIPT)
    rows: list[Row] = []
    problems: list[str] = []
    seen: set[str] = set()
    for lineno, rec in enumerate(reader, start=2):
        rid = (rec.get(fields["id"]) or "").strip()
        text = " ".join((rec.get(fields["text"]) or "").split())
        approver = (rec.get(fields["approved_by"]) or "").strip()
        if not rid and not text:
            continue
        if not ID_RE.match(rid):
            problems.append(f"line {lineno}: invalid id {rid!r} (use a-z 0-9 _ . -)")
        elif rid in seen:
            problems.append(f"line {lineno}: duplicate id {rid!r}")
        seen.add(rid)
        if not text:
            problems.append(f"line {lineno} ({rid}): empty text")
        elif len(text) > MAX_TEXT_CHARS:
            problems.append(f"line {lineno} ({rid}): text longer than {MAX_TEXT_CHARS} chars")
        rows.append(Row(rid, text, approver))
    if problems:
        raise TtsError("Invalid script:\n  " + "\n  ".join(problems), EXIT_SCRIPT)
    if not rows:
        raise TtsError("Script has no rows.", EXIT_SCRIPT)
    return rows, sha256_hex(raw)


def check_approvals(rows: Sequence[Row], allow_sample: bool) -> None:
    unapproved = [r.id for r in rows if not r.approved_by]
    if unapproved:
        raise TtsError(
            "Refusing: empty approved_by for child-facing text in row(s): "
            + ", ".join(unapproved)
            + ". A dentist/reviewer must approve each line.",
            EXIT_SCRIPT,
        )
    if not allow_sample:
        placeholders = [
            r.id
            for r in rows
            if any(r.approved_by.lower().startswith(p) for p in PLACEHOLDER_APPROVERS)
        ]
        if placeholders:
            raise TtsError(
                "Refusing: placeholder approved_by (CONTOH/SAMPLE/TODO) in row(s): "
                + ", ".join(placeholders)
                + ". Use --allow-sample only for dry runs of the sample script.",
                EXIT_SCRIPT,
            )


def lint_rows(rows: Sequence[Row], phrases_file: Path) -> None:
    try:
        rules, _skipped = load_rules(phrases_file)
    except LintConfigError as exc:
        raise TtsError(f"Cannot lint texts: {exc}", EXIT_USAGE) from exc
    problems: list[str] = []
    for row in rows:
        for hit in scan_text(row.text, rules, row.id):
            problems.append(f'{row.id}: "{hit.matched}" is forbidden ({hit.rule_id}). {hit.reason}')
    if problems:
        raise TtsError(
            "Texts violate docs/style/forbidden_phrases.md:\n  " + "\n  ".join(problems),
            EXIT_SCRIPT,
        )


def input_hash(row: Row, plan: Plan) -> str:
    """Hash of everything that affects the audio. The raw script text is hashed unmodified."""
    payload = "\n".join(
        (
            HASH_VERSION,
            plan.provider,
            plan.model,
            plan.voice,
            plan.output_format,
            plan.style,
            row.text,
        )
    )
    return sha256_hex(payload.encode("utf-8"))


# --------------------------------------------------------------------------- Azure (ADR 007)


def build_ssml(text: str, voice: str) -> str:
    return (
        "<speak version='1.0' xml:lang='id-ID' xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice xml:lang='id-ID' name='{voice}'>{escape(text)}</voice></speak>"
    )


def _headers(key: str) -> dict[str, str]:
    return {"Ocp-Apim-Subscription-Key": key, "User-Agent": "dentisai-tts-build"}


def verify_voice(client: httpx.Client, region: str, key: str, voice: str) -> list[str]:
    """Fail unless ``voice`` is offered in ``region``. Returns all id-ID voices found."""
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    try:
        response = client.get(url, headers=_headers(key))
    except httpx.HTTPError as exc:
        raise TtsError(f"Voices list request failed ({type(exc).__name__}).", EXIT_VOICE) from exc
    if response.status_code in (401, 403):
        raise TtsError(
            f"Voices list returned HTTP {response.status_code}: the key does not match region "
            f"'{region}' (keys are region-bound) or is invalid.",
            EXIT_VOICE,
        )
    if response.status_code != 200:
        raise TtsError(f"Voices list returned HTTP {response.status_code}.", EXIT_VOICE)
    try:
        voices: list[dict[str, Any]] = response.json()
    except ValueError as exc:
        raise TtsError("Voices list returned invalid JSON.", EXIT_VOICE) from exc
    indo = sorted(
        str(v.get("ShortName")) for v in voices if str(v.get("Locale", "")).lower() == "id-id"
    )
    if voice not in {str(v.get("ShortName")) for v in voices}:
        available = ", ".join(indo) if indo else "none"
        raise TtsError(
            f"Voice '{voice}' is NOT available in region '{region}'. id-ID voices there: "
            f"{available}. Create the Speech resource in a region that offers it, or pick "
            "another id-ID voice.",
            EXIT_VOICE,
        )
    return indo


def synthesize(
    client: httpx.Client,
    region: str,
    key: str,
    voice: str,
    text: str,
    *,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        **_headers(key),
        "Content-Type": "application/ssml+xml; charset=utf-8",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
    }
    body = build_ssml(text, voice).encode("utf-8")
    last = "no response"
    for attempt in range(1, retries + 1):
        try:
            response = client.post(url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        else:
            if response.status_code == 200 and response.content:
                return response.content
            last = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt < retries:
            sleep(2.0 * attempt)
    raise TtsError(f"Synthesis failed ({last}).", EXIT_SYNTH)


# --------------------------------------------------------------------------- Gemini (ADR 010)


def pcm_to_wav(pcm: bytes, rate: int = GEMINI_SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit signed little-endian mono PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _mime_rate(mime: str) -> int:
    match = re.search(r"rate=(\d{4,6})", mime)
    return int(match.group(1)) if match else GEMINI_SAMPLE_RATE


def decode_gemini_audio(b64: str, mime: str) -> bytes:
    """Base64 audio from Gemini -> playable WAV bytes, or TtsError if it fails sanity checks."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TtsError("Gemini returned audio that is not valid base64.", EXIT_SYNTH) from exc
    if not raw:
        raise TtsError("Gemini returned empty audio.", EXIT_SYNTH)
    lowered = mime.lower()
    if "mulaw" in lowered or "alaw" in lowered:
        raise TtsError(f"Unsupported audio type from Gemini: {mime[:60]!r}.", EXIT_SYNTH)
    if raw[:4] == b"RIFF":  # unary default per docs is a complete WAV already
        try:
            with wave.open(io.BytesIO(raw), "rb") as wav:
                seconds = wav.getnframes() / float(wav.getframerate() or 1)
        except (wave.Error, EOFError) as exc:
            raise TtsError("Gemini returned a malformed WAV.", EXIT_SYNTH) from exc
        wav_bytes = raw
    else:
        if len(raw) % 2:
            raise TtsError("Gemini PCM has an odd byte count (not 16-bit).", EXIT_SYNTH)
        rate = _mime_rate(mime)
        seconds = len(raw) / 2.0 / rate
        wav_bytes = pcm_to_wav(raw, rate)
    if not MIN_AUDIO_SECONDS <= seconds <= MAX_AUDIO_SECONDS:
        raise TtsError(
            f"Gemini audio length {seconds:.2f}s is outside {MIN_AUDIO_SECONDS}-"
            f"{MAX_AUDIO_SECONDS:.0f}s; refusing to ship it.",
            EXIT_SYNTH,
        )
    return wav_bytes


def _extract_inline_audio(data: Any) -> tuple[str, str]:
    """Return (base64, mime) from a generateContent response or raise a short TtsError."""
    if not isinstance(data, dict):
        raise TtsError("Gemini response is not a JSON object.", EXIT_SYNTH)
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        feedback = data.get("promptFeedback")
        reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        raise TtsError(f"Gemini returned no candidates (blockReason={reason}).", EXIT_SYNTH)
    cand = candidates[0]
    content = cand.get("content") if isinstance(cand, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    for part in parts if isinstance(parts, list) else []:
        inline = (
            part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
        )
        if isinstance(inline, dict) and isinstance(inline.get("data"), str):
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            return str(inline["data"]), str(mime)
    finish = cand.get("finishReason") if isinstance(cand, dict) else None
    raise TtsError(f"Gemini response has no audio part (finishReason={finish}).", EXIT_SYNTH)


class GeminiTts:
    """Minimal Gemini generateContent TTS client with pacing, retry and a request cap.

    The API key travels only in the ``x-goog-api-key`` header. Request bodies are never logged.
    """

    def __init__(
        self,
        client: httpx.Client,
        key: str,
        model: str,
        voice: str,
        *,
        style: str = "",
        retries: int = GEMINI_RETRIES,
        delay: float = GEMINI_DEFAULT_DELAY,
        max_requests: int = GEMINI_DEFAULT_MAX_REQUESTS,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = lambda m: None,
    ) -> None:
        self._client = client
        self._key = key
        self._model = model
        self._voice = voice
        self._style = style
        self._retries = retries
        self._delay = delay
        self._max = max_requests
        self._sleep = sleep
        self._log = log
        self.requests = 0

    @property
    def url(self) -> str:
        return f"{GEMINI_BASE}/models/{self._model}:generateContent"

    def _body(self, text: str) -> dict[str, Any]:
        part: dict[str, Any] = {"text": text}  # raw script text; never modified
        if self._style:
            # Style goes in metadata, not in the text (docs: text is a verbatim transcript).
            part["speechMetadata"] = {"style": self._style}
        return {
            "contents": [{"parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
                },
            },
        }

    def _redact(self, message: str) -> str:
        return message.replace(self._key, "***")[:200]

    def _error_detail(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return ""
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else None
        return f": {self._redact(msg)}" if isinstance(msg, str) else ""

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            header = response.headers.get("Retry-After", "").strip()
            try:
                return max(0.0, min(120.0, float(header)))
            except ValueError:
                pass
        return min(60.0, 2.0 * float(2 ** (attempt - 1)))

    def synthesize(self, text: str) -> bytes:
        if self.requests > 0 and self._delay > 0:
            self._sleep(self._delay)
        headers = {
            "x-goog-api-key": self._key,
            "Content-Type": "application/json",
            "User-Agent": "dentisai-tts-build",
        }
        payload = json.dumps(self._body(text), ensure_ascii=False).encode("utf-8")
        last = "no response"
        for attempt in range(1, self._retries + 1):
            if self.requests >= self._max:
                raise TtsError(
                    f"Request cap reached ({self._max} per run, --max-requests); stopping to "
                    "protect quota. Re-run later: finished rows are skipped.",
                    EXIT_SYNTH,
                )
            self.requests += 1
            response: httpx.Response | None = None
            try:
                response = self._client.post(self.url, headers=headers, content=payload)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    try:
                        b64, mime = _extract_inline_audio(response.json())
                        return decode_gemini_audio(b64, mime)
                    except ValueError:
                        last = "invalid JSON"
                    except TtsError as exc:
                        last = str(exc)
                else:
                    last = f"HTTP {response.status_code}{self._error_detail(response)}"
                    if response.status_code not in GEMINI_RETRY_STATUS:
                        break
            if attempt < self._retries:
                wait = self._backoff(attempt, response)
                self._log(f"attempt {attempt} failed ({last[:80]}); retrying in {wait:.0f}s")
                self._sleep(wait)
        raise TtsError(f"Synthesis failed ({last}).", EXIT_SYNTH)


# --------------------------------------------------------------------------- build


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _file_matches(entry: Mapping[str, Any], out_dir: Path, want_hash: str) -> bool:
    audio = out_dir / str(entry.get("file", ""))
    if entry.get("input_sha256") != want_hash or not audio.is_file():
        return False
    return sha256_hex(audio.read_bytes()) == entry.get("sha256")


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    tmp_manifest = path.with_name(".MANIFEST.json.tmp")
    tmp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp_manifest, path)


def run_build(
    rows: Sequence[Row],
    *,
    script_sha: str,
    out_dir: Path,
    plan: Plan,
    generated_under: str,
    synth: Callable[[str], bytes] | None,
    force: bool,
    dry_run: bool,
    log: Callable[[str], None] = lambda m: print(f"generate_audio: {m}", file=sys.stderr),
) -> dict[str, int]:
    manifest_path = out_dir / "MANIFEST.json"
    old = load_manifest(manifest_path)
    old_files = {str(e.get("id")): e for e in old.get("files", []) if isinstance(e, dict)}
    mode_changed = bool(old) and (
        old.get("generated_under") != generated_under
        or old.get("provider", "azure") != plan.provider
        or old.get("model", AZURE_MODEL) != plan.model
        or old.get("voice") != plan.voice
        or old.get("output_format") != plan.output_format
    )
    if mode_changed:
        log("provider/model/voice/format/generated_under changed: regenerating everything.")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries: list[dict[str, Any]] = []
    stats = {"generated": 0, "skipped": 0, "chars": 0}
    failure: TtsError | None = None
    for row in rows:
        want = input_hash(row, plan)
        previous = old_files.get(row.id)
        unchanged = (
            not force
            and not mode_changed
            and previous is not None
            and _file_matches(previous, out_dir, want)
        )
        if unchanged and previous is not None:
            stats["skipped"] += 1
            entries.append({**previous, "text": row.text, "approved_by": row.approved_by})
            log(f"{row.id}: unchanged, skipped")
            continue
        if dry_run:
            stats["generated"] += 1
            stats["chars"] += len(row.text)
            log(f"{row.id}: would generate ({len(row.text)} chars)")
            continue
        assert synth is not None
        try:
            audio = synth(row.text)
        except TtsError as exc:
            failure = exc
            break
        stats["generated"] += 1
        stats["chars"] += len(row.text)
        file_name = f"{row.id}{plan.file_ext}"
        tmp = out_dir / f".{file_name}.tmp"
        tmp.write_bytes(audio)
        os.replace(tmp, out_dir / file_name)
        entries.append(
            {
                "id": row.id,
                "file": file_name,
                "text": row.text,
                "approved_by": row.approved_by,
                "input_sha256": want,
                "sha256": sha256_hex(audio),
                "bytes": len(audio),
                "generated_at": now,
            }
        )
        log(f"{row.id}: generated ({len(audio)} bytes)")
    if dry_run:
        return stats

    nothing_new = stats["generated"] == 0 and bool(old) and failure is None
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "provider": plan.provider,
        "model": plan.model,
        "voice": plan.voice,
    }
    if plan.region:
        manifest["region"] = plan.region
    manifest.update(
        {
            "output_format": plan.output_format,
            "generated_at": old.get("generated_at", now) if nothing_new else now,
            "generated_under": generated_under,
            "ai_voice_label_required": True,
            "ai_voice_disclosure_required": True,  # legacy key (schema 1), kept for the app team
            "script_sha256": script_sha,
            "complete": failure is None,
            "files": entries,
        }
    )
    if failure is not None:
        # Keep finished audio on record so the next run does not pay for it twice.
        if stats["generated"] > 0:
            _write_manifest(manifest_path, manifest)
            log("run incomplete: partial MANIFEST.json written (complete=false)")
        raise failure
    if manifest != old:
        _write_manifest(manifest_path, manifest)
        log("MANIFEST.json written")
    else:
        log("MANIFEST.json unchanged")
    known = {f"{r.id}{plan.file_ext}" for r in rows}
    stale = sorted(
        p.name for ext in AUDIO_EXTS for p in out_dir.glob(f"*{ext}") if p.name not in known
    )
    if stale:
        log("warning: audio files not in the script (delete if obsolete): " + ", ".join(stale))
    return stats


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate instruction audio at build time (Gemini default, ADR 010; "
        "Azure Speech backup, ADR 007).",
        epilog="No TTS at runtime. Only static approved script text is sent. Get Google's written "
        "confirmation (or switch provider) before a public/commercial release. "
        "The app must say the voice is AI-generated.",
    )
    p.add_argument(
        "--script", help=f"CSV (default: ${SCRIPT_ENV}, else <backend>/{DEFAULT_SCRIPT_REL})"
    )
    p.add_argument("--out-dir", help="Output folder (default: the script's folder)")
    p.add_argument(
        "--provider",
        choices=PROVIDERS,
        help=f"TTS provider (default: ${PROVIDER_ENV}, else {DEFAULT_PROVIDER}).",
    )
    p.add_argument(
        "--generated-under",
        required=True,
        choices=("free", "paid", "student", "payg"),
        help="Billing mode used for this build, recorded in MANIFEST.json: free|paid for "
        "gemini, student|payg for azure.",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate only; no network, no writes.")
    p.add_argument("--force", action="store_true", help="Regenerate even if unchanged.")
    p.add_argument("--allow-sample", action="store_true", help="Accept CONTOH/SAMPLE approvers.")
    p.add_argument("--phrases", help="Forbidden-phrases markdown (default: docs/style/...)")
    p.add_argument(
        "--request-delay",
        type=float,
        default=GEMINI_DEFAULT_DELAY,
        help="gemini: seconds to wait between requests (free-tier rate limits).",
    )
    p.add_argument(
        "--max-requests",
        type=int,
        default=GEMINI_DEFAULT_MAX_REQUESTS,
        help="gemini: maximum HTTP requests (retries included) in one run.",
    )
    return p


def _azure_plan(
    environ: Mapping[str, str], dry_run: bool, log: Callable[[str], None]
) -> tuple[Plan, str | None]:
    voice = get_env("AZURE_SPEECH_VOICE", environ) or DEFAULT_VOICE
    if not voice.startswith("id-ID-"):
        raise TtsError(f"AZURE_SPEECH_VOICE must be an id-ID voice, got '{voice}'.", EXIT_USAGE)
    if voice not in ALLOWED_VOICES:
        log(f"warning: '{voice}' is not one of {', '.join(ALLOWED_VOICES)} (ADR 007).")
    key: str | None = None
    region = get_env("AZURE_SPEECH_REGION", environ) or ""
    if not dry_run:
        env = require_env(("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"), environ)
        key, region = env["AZURE_SPEECH_KEY"], env["AZURE_SPEECH_REGION"].lower()
    if region and not REGION_RE.match(region):
        raise TtsError("AZURE_SPEECH_REGION is not a valid region name.", EXIT_USAGE)
    if region and region not in EXPECTED_REGIONS:
        log(f"warning: region '{region}' is not {'/'.join(EXPECTED_REGIONS)} (ADR 007).")
    plan = Plan("azure", AZURE_MODEL, voice, OUTPUT_FORMAT, FILE_EXT, region=region)
    return plan, key


def _gemini_plan(
    environ: Mapping[str, str], dry_run: bool, log: Callable[[str], None]
) -> tuple[Plan, str | None]:
    model = get_env("GEMINI_TTS_MODEL", environ) or GEMINI_DEFAULT_MODEL
    voice = get_env("GEMINI_TTS_VOICE", environ) or GEMINI_DEFAULT_VOICE
    style = get_env("GEMINI_TTS_STYLE", environ) or ""
    if not MODEL_RE.match(model):
        raise TtsError("GEMINI_TTS_MODEL is not a valid model id.", EXIT_USAGE)
    if not GEMINI_VOICE_RE.match(voice):
        raise TtsError("GEMINI_TTS_VOICE is not a valid voice name.", EXIT_USAGE)
    if voice not in GEMINI_VOICES:
        log(f"warning: voice '{voice}' is not in the documented list of 30 prebuilt voices.")
    if len(style) > 200:
        raise TtsError("GEMINI_TTS_STYLE is longer than 200 chars.", EXIT_USAGE)
    key = None if dry_run else require_env(("GEMINI_API_KEY",), environ)["GEMINI_API_KEY"]
    return Plan("gemini", model, voice, GEMINI_OUTPUT_FORMAT, GEMINI_EXT, style=style), key


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    if environ is None:
        live = os.environ
        load_env_file(backend_root() / ".env", live)
        environ = live

    def log(msg: str) -> None:
        print(f"generate_audio: {msg}", file=sys.stderr)

    root = backend_root()
    script = (
        Path(args.script)
        if args.script
        else Path(get_env(SCRIPT_ENV, environ) or root / DEFAULT_SCRIPT_REL)
    )
    out_dir = Path(args.out_dir) if args.out_dir else script.parent
    phrases = Path(args.phrases) if args.phrases else resolve_phrases_path(root, environ)
    owns_client = False
    try:
        provider = (args.provider or get_env(PROVIDER_ENV, environ) or DEFAULT_PROVIDER).lower()
        if provider not in PROVIDERS:
            raise TtsError(f"Unknown provider '{provider}' (use gemini or azure).", EXIT_USAGE)
        if args.generated_under not in GENERATED_UNDER[provider]:
            raise TtsError(
                f"--generated-under {args.generated_under} is not valid for provider "
                f"{provider}; use {' or '.join(GENERATED_UNDER[provider])}.",
                EXIT_USAGE,
            )
        if args.request_delay < 0 or args.max_requests < 1:
            raise TtsError("--request-delay must be >= 0 and --max-requests >= 1.", EXIT_USAGE)
        sync_error = check_sync(root)
        if sync_error:
            raise TtsError(sync_error, EXIT_SCRIPT)
        rows, script_sha = read_script(script)
        check_approvals(rows, args.allow_sample)
        lint_rows(rows, phrases)
        if provider == "gemini":
            plan, key = _gemini_plan(environ, args.dry_run, log)
        else:
            plan, key = _azure_plan(environ, args.dry_run, log)
        total_chars = sum(len(r.text) for r in rows)
        log(
            f"{len(rows)} row(s), {total_chars} characters, provider {plan.provider}, "
            f"model {plan.model}, voice {plan.voice}"
        )

        synth: Callable[[str], bytes] | None = None
        if args.dry_run:
            log("dry-run: skipping provider checks and synthesis (no network).")
        else:
            assert key is not None
            owns_client = client is None
            if provider == "gemini":
                client = client or httpx.Client(timeout=GEMINI_TIMEOUT)
                gemini = GeminiTts(
                    client,
                    key,
                    plan.model,
                    plan.voice,
                    style=plan.style,
                    delay=args.request_delay,
                    max_requests=args.max_requests,
                    sleep=sleep,
                    log=log,
                )
                synth = gemini.synthesize
            else:
                client = client or httpx.Client(timeout=60.0)
                indo = verify_voice(client, plan.region, key, plan.voice)
                log(
                    f"OK: voice '{plan.voice}' is available in '{plan.region}' "
                    f"({len(indo)} id-ID voices)."
                )
                azure_client, azure_key = client, key

                def synth(text: str) -> bytes:
                    return synthesize(
                        azure_client, plan.region, azure_key, plan.voice, text, sleep=sleep
                    )

            out_dir.mkdir(parents=True, exist_ok=True)
        stats = run_build(
            rows,
            script_sha=script_sha,
            out_dir=out_dir,
            plan=plan,
            generated_under=args.generated_under,
            synth=synth,
            force=args.force,
            dry_run=args.dry_run,
        )
        log(
            f"summary: generated={stats['generated']} skipped={stats['skipped']} "
            f"characters~{stats['chars']}"
        )
        if provider == "gemini":
            log(
                "reminder (ADR 010): Gemini terms on under-18 apps / medical advice are "
                "unconfirmed; get Google's written confirmation or switch provider before a "
                "public/commercial release. The About screen must say the voice is AI-made."
            )
            if args.generated_under == "free":
                log("note: free-tier inputs may be used by Google; only static script text sent.")
        elif args.generated_under == "student":
            log(
                "reminder: student credits are non-commercial; regenerate with --generated-under "
                "payg before any commercial release."
            )
        return EXIT_OK
    except ConfigError as exc:
        log(str(exc))
        return EXIT_USAGE
    except TtsError as exc:
        log(f"error: {exc}")
        return exc.code
    finally:
        if owns_client and client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
