"""ElevenLabs speech-to-text (voice memo -> text + language) and text-to-speech (reply voice memo)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .gemini import _load_env

API = "https://api.elevenlabs.io/v1"
STT_MODELS = ["scribe_v2", "scribe_v1"]
TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"  # premade "Sarah"; override with ELEVENLABS_VOICE_ID


def _key():
    _load_env()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Set ELEVENLABS_API_KEY in .env")
    return key


def available():
    _load_env()
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def _multipart(fields, filename, content, mime):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    parts.append(content + b"\r\n" + f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _call(request, timeout=60):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"ElevenLabs returned HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("ElevenLabs could not be reached") from None


def transcribe(audio, mime="audio/mp4"):
    """Return {"text", "language"} (ISO code such as "es") for a voice memo."""
    if len(audio) > 25_000_000:
        raise RuntimeError("Voice note is too large")
    if not audio:
        raise RuntimeError("The voice note was empty. Record for at least one second and try again.")
    content_type = (mime or "application/octet-stream").split(";", 1)[0].strip().lower()
    if content_type in ("audio/wav", "audio/x-wav") and not (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"):
        raise RuntimeError("The voice recording is not a playable WAV file. Please record it again.")
    extension = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/webm": ".webm",
                 "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3",
                 "audio/mp3": ".mp3", "audio/ogg": ".ogg"}.get(content_type, "")
    key = _key()
    last = None
    for model in STT_MODELS:
        body, ctype = _multipart({"model_id": model}, "voice-note" + extension, audio, content_type)
        request = urllib.request.Request(f"{API}/speech-to-text", body, {"xi-api-key": key, "Content-Type": ctype})
        try:
            data = json.loads(_call(request))
            return {"text": (data.get("text") or "").strip(), "language": (data.get("language_code") or "en")[:3]}
        except RuntimeError as exc:
            if "invalid_audio" in str(exc):
                raise RuntimeError("The voice recording could not be decoded. Record for at least one second and try again.") from None
            last = exc
            if "HTTP 4" not in str(exc):
                raise
    raise last


def synthesize(text, language=None):
    """Return MP3 bytes of `text` spoken in a multilingual voice."""
    key = _key()
    voice = os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    body = {"text": text[:1500], "model_id": TTS_MODEL}
    request = urllib.request.Request(f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128", json.dumps(body).encode(),
                                     {"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    return _call(request)


def to_m4a(mp3):
    """iMessage voice notes expect AAC in an .m4a container; fall back to the MP3 if ffmpeg is missing."""
    try:
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d, "in.mp3"), Path(d, "out.m4a")
            src.write_bytes(mp3)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "aac", "-b:a", "64k", str(dst)],
                           check=True, timeout=30)
            return dst.read_bytes(), "audio/mp4"
    except (OSError, subprocess.SubprocessError):
        return mp3, "audio/mpeg"
