"""Local audio transcription via faster-whisper.

Used to turn meeting recordings (or any supported audio file) into text
so they can flow through the normal document indexing pipeline.

The Whisper model is heavy to load (hundreds of MB) and slow to initialize,
so it is lazy-loaded on first use and kept in-process for subsequent calls.
"""

from pathlib import Path
from typing import Dict, List, Optional
import logging
import threading

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac", ".mp4", ".mpeg", ".mpga"}


class TranscriptionResult:
    """Transcript plus segment-level timing from Whisper."""

    def __init__(self, text: str, segments: List[Dict], language: str, duration: float):
        self.text = text
        self.segments = segments
        self.language = language
        self.duration = duration


class AudioTranscriber:
    """Lazy-loaded faster-whisper wrapper.

    One instance is expected per process. The model is loaded on first
    transcribe() call; subsequent calls reuse it.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._load_lock = threading.Lock()

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise RuntimeError(
                    "faster-whisper is not installed. Run `pip install faster-whisper` "
                    "or `pip install -r requirements.txt`."
                ) from e

            logger.info(
                f"Loading Whisper model '{self.model_size}' "
                f"(device={self.device}, compute_type={self.compute_type})"
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("Whisper model loaded")

    def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        """Transcribe an audio file to text with segment timestamps.

        Args:
            audio_path: Path to audio file (mp3/wav/m4a/webm/ogg/flac/mp4).
            language: Optional language hint (e.g. "en"). None = auto-detect.
            vad_filter: Drop silent gaps via voice-activity detection.
        """
        self._ensure_loaded()

        logger.info(f"Transcribing audio: {audio_path.name}")
        segments_gen, info = self._model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=vad_filter,
            beam_size=5,
        )

        segments: List[Dict] = []
        text_parts: List[str] = []
        for seg in segments_gen:
            segments.append(
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                }
            )
            text_parts.append(seg.text.strip())

        full_text = "\n".join(t for t in text_parts if t)

        logger.info(
            f"Transcribed {audio_path.name}: {len(segments)} segments, "
            f"{len(full_text)} chars, language={info.language}, duration={info.duration:.1f}s"
        )

        return TranscriptionResult(
            text=full_text,
            segments=segments,
            language=info.language,
            duration=info.duration,
        )


_transcriber_instance: Optional[AudioTranscriber] = None
_instance_lock = threading.Lock()


def get_transcriber(
    model_size: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
) -> AudioTranscriber:
    """Return a process-wide AudioTranscriber, creating it on first call."""
    global _transcriber_instance
    if _transcriber_instance is not None:
        return _transcriber_instance
    with _instance_lock:
        if _transcriber_instance is None:
            _transcriber_instance = AudioTranscriber(
                model_size=model_size,
                device=device,
                compute_type=compute_type,
            )
        return _transcriber_instance


def is_audio_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in AUDIO_EXTENSIONS


def format_transcript_with_timestamps(result: TranscriptionResult) -> str:
    """Render a transcript with [mm:ss] prefixes per segment.

    Useful when the transcript is indexed as a document — the timestamps
    give retrieved passages a natural anchor back into the original audio.
    """
    lines: List[str] = []
    for seg in result.segments:
        mm = int(seg["start"] // 60)
        ss = int(seg["start"] % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg['text']}")
    return "\n".join(lines)
