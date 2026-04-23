"""Local AI captioning for images, video, and audio.

Probes Ollama for a vision-language model and faster-whisper/whisper for
speech-to-text.  All functions fail silently — callers receive None and fall
back to binary embedding.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import config

logger = logging.getLogger(__name__)

# ── Capability detection ──────────────────────────────────────────────────────

_VLM_KEYWORDS = ("llava", "moondream", "bakllava", "minicpm-v")
_MIN_FREE_RAM_BYTES = 8 * 1024 ** 3  # 8 GB


class CaptionError(Exception):
    """Raised when captioning fails; caller catches and returns None."""


@dataclass
class Capabilities:
    vlm_available: bool = False
    vlm_model: str = ""
    stt_available: bool = False
    stt_backend: str = ""  # "faster_whisper" | "whisper"


_capabilities: Capabilities | None = None


def detect_capabilities() -> Capabilities:
    """Probe local AI backends once and cache the result."""
    global _capabilities
    if _capabilities is not None:
        return _capabilities

    caps = Capabilities()

    # ── VLM via Ollama ────────────────────────────────────────────────────────
    try:
        import httpx
        resp = httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            for m in models:
                name: str = m.get("name", "").lower()
                if any(kw in name for kw in _VLM_KEYWORDS):
                    caps.vlm_available = True
                    caps.vlm_model = m.get("name", "")
                    break
    except Exception:
        pass

    # ── STT via faster-whisper or whisper ─────────────────────────────────────
    try:
        import faster_whisper  # noqa: F401
        caps.stt_available = True
        caps.stt_backend = "faster_whisper"
    except ImportError:
        try:
            import whisper  # noqa: F401
            caps.stt_available = True
            caps.stt_backend = "whisper"
        except ImportError:
            pass

    _capabilities = caps
    return caps


def _check_memory() -> None:
    """Raise CaptionError if < 8 GB RAM is available."""
    mem = psutil.virtual_memory()
    if mem.available < _MIN_FREE_RAM_BYTES:
        raise CaptionError(
            f"insufficient memory for captioning: "
            f"{mem.available // (1024**2)} MB available, need 8192 MB"
        )


# ── Image captioning ──────────────────────────────────────────────────────────


def caption_image(path: str | Path) -> str:
    """Send an image to Ollama VLM and return a text description."""
    caps = detect_capabilities()
    if not caps.vlm_available:
        raise CaptionError("no VLM available")
    _check_memory()

    path = Path(path)
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    import httpx
    payload = {
        "model": caps.vlm_model,
        "prompt": (
            "Describe this image in detail. "
            "Include people, objects, locations, activities, colors, "
            "and any text visible."
        ),
        "images": [img_b64],
        "stream": False,
    }
    resp = httpx.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload, timeout=60.0)
    resp.raise_for_status()
    caption = resp.json().get("response", "").strip()
    if not caption:
        raise CaptionError("VLM returned empty response")
    return caption


# ── Video captioning ──────────────────────────────────────────────────────────


def _extract_frame_png(path: Path) -> bytes:
    """Extract first frame at t=1s as PNG bytes via ffmpeg."""
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", "1",
            "-i", str(path),
            "-frames:v", "1",
            "-f", "image2",
            "-vcodec", "png",
            "pipe:1",
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout:
        raise CaptionError(f"ffmpeg frame extraction failed: {result.stderr[:200]}")
    return result.stdout


def caption_video(path: str | Path) -> str:
    """Caption a video by extracting a frame and sending it to the VLM."""
    caps = detect_capabilities()
    if not caps.vlm_available:
        raise CaptionError("no VLM available")
    _check_memory()

    path = Path(path)
    frame_bytes = _extract_frame_png(path)
    img_b64 = base64.b64encode(frame_bytes).decode()

    import httpx
    payload = {
        "model": caps.vlm_model,
        "prompt": (
            "Describe this image in detail. "
            "Include people, objects, locations, activities, colors, "
            "and any text visible."
        ),
        "images": [img_b64],
        "stream": False,
    }
    resp = httpx.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload, timeout=60.0)
    resp.raise_for_status()
    caption = resp.json().get("response", "").strip()
    if not caption:
        raise CaptionError("VLM returned empty response for video frame")
    return caption


# ── Audio / video transcription ───────────────────────────────────────────────


def _extract_audio_wav(path: Path) -> Path:
    """Extract audio track to a temp WAV file.  Returns temp path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(path),
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            tmp.name,
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise CaptionError(f"ffmpeg audio extraction failed: {result.stderr[:200]}")
    return Path(tmp.name)


def transcribe_audio(path: str | Path) -> str:
    """Transcribe an audio file using faster-whisper or whisper."""
    caps = detect_capabilities()
    if not caps.stt_available:
        raise CaptionError("no STT backend available")
    _check_memory()

    path = Path(path)

    if caps.stt_backend == "faster_whisper":
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="auto")
        segments, _ = model.transcribe(str(path))
        text = " ".join(s.text for s in segments).strip()
    else:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(str(path))
        text = result.get("text", "").strip()

    if not text:
        raise CaptionError("STT returned empty transcription")
    return text


# ── Unified entry point ───────────────────────────────────────────────────────


def caption_file(path: str | Path) -> str | None:
    """
    Return a text description/transcription for any supported file.

    Returns None if captioning is unavailable or fails — caller falls back to
    binary embedding.  Never raises.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    # Image
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}:
        try:
            return caption_image(path)
        except Exception as e:
            logger.debug("image captioning failed for %s: %s", path, e)
            return None

    # Audio
    if suffix in {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}:
        try:
            return transcribe_audio(path)
        except Exception as e:
            logger.debug("audio transcription failed for %s: %s", path, e)
            return None

    # Video — try visual caption AND audio transcription, combine if both succeed
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        visual: str | None = None
        transcript: str | None = None

        try:
            visual = caption_video(path)
        except Exception as e:
            logger.debug("video visual caption failed for %s: %s", path, e)

        try:
            # Extract audio track first for video files
            audio_path = _extract_audio_wav(path)
            try:
                transcript = transcribe_audio(audio_path)
            finally:
                audio_path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("video audio transcription failed for %s: %s", path, e)

        if visual and transcript:
            return f"[Visual] {visual}\n[Audio] {transcript}"
        return visual or transcript

    return None
