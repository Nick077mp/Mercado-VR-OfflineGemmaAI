"""Text-to-Speech service using Piper TTS.

Spanish voice, 100% offline, ultra fast.
"""

import os
import re
import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from piper import PiperVoice

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PIPER_MODEL: str = "voices/es_ES-davefx-medium.onnx"
SAMPLE_RATE: int = 24000


# ---------------------------------------------------------------------------
# TextToSpeechEngine
# ---------------------------------------------------------------------------

class TextToSpeechEngine:
    """Piper TTS wrapper — natural Spanish voice without cloning."""

    # Patterns that Piper mispronounces (spelled out letter-by-letter)
    _CLEAN_RULES = [
        # "Mmm" at start -> remove
        (r"^[Mm]+[,\s]+", ""),
        # "Mmm" mid-text -> pause
        (r"\s[Mm]+[,\s]+", ", "),
        # "Hmm" similar
        (r"^[Hh]mm+[,\s]+", ""),
        (r"\s[Hh]mm+[,\s]+", ", "),
        # Prolonged interjections
        (r"\b[Aa]h{2,}\b", "Ah"),
        (r"\b[Oo]h{2,}\b", "Oh"),
        (r"\b[Ee]h{2,}\b", "Eh"),
        # Multiple punctuation
        (r"!{2,}", "!"),
        (r"\?{2,}", "?"),
        (r"\.{4,}", "..."),
        # Piper pronounces "salado" oddly
        (r"\bsalado\b", "caro"),
        (r"\bsalada\b", "cara"),
        (r"\bSalado\b", "Caro"),
        (r"\bSalada\b", "Cara"),
    ]

    def __init__(self, model_path: str = PIPER_MODEL) -> None:
        self._voice: Optional[PiperVoice] = None
        self._lock: threading.Lock = threading.Lock()
        self._model_path: str = model_path

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Initialize the Piper voice model."""
        if self._voice is not None:
            return

        try:
            print("[TTS] Loading Piper TTS...")

            if not os.path.exists(self._model_path):
                raise FileNotFoundError(f"Piper model not found: {self._model_path}")

            self._voice = PiperVoice.load(self._model_path)
            print("✅ Piper TTS loaded")

        except Exception as e:
            print(f"❌ Error loading Piper: {e}")
            import traceback
            traceback.print_exc()
            self._voice = None

    # ------------------------------------------------------------------
    # Speech generation
    # ------------------------------------------------------------------

    async def generate_speech(self, text: str, output_path: Optional[str] = None) -> Optional[str]:
        """Convert text to audio file (async-compatible)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._generate_sync, text, output_path)

    def _generate_sync(self, text: str, output_path: Optional[str] = None) -> Optional[str]:
        """Generate WAV audio from text (thread-safe)."""
        with self._lock:
            if self._voice is None:
                self.load()
                if self._voice is None:
                    return None

            try:
                clean = self._clean_text(text)

                if output_path is None:
                    temp_f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                    output_path = temp_f.name
                    temp_f.close()

                with wave.open(output_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self._voice.config.sample_rate)
                    for chunk in self._voice.synthesize(clean):
                        wf.writeframes(chunk.audio_int16_bytes)

                return output_path

            except Exception as e:
                print(f"❌ Error generating audio: {e}")
                import traceback
                traceback.print_exc()
                return None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    @staticmethod
    def play_audio(audio_path: str) -> None:
        """Play a WAV file through the default audio device."""
        try:
            audio_data, sample_rate = sf.read(audio_path)
            sd.play(audio_data, sample_rate)
            sd.wait()
        except Exception as e:
            print(f"❌ Error playing audio: {e}")

    def speak(self, text: str) -> None:
        """Generate speech and play it immediately."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                path = asyncio.run_coroutine_threadsafe(
                    self.generate_speech(text), loop,
                ).result()
            else:
                path = asyncio.run(self.generate_speech(text))
        except RuntimeError:
            path = asyncio.run(self.generate_speech(text))

        if path and os.path.exists(path):
            self.play_audio(path)
            try:
                os.unlink(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Text cleaning
    # ------------------------------------------------------------------

    @classmethod
    def _clean_text(cls, text: str) -> str:
        """Remove interjections and artefacts that Piper mispronounces."""
        if not text:
            return text

        result = text
        for pattern, replacement in cls._CLEAN_RULES:
            result = re.sub(pattern, replacement, result)

        result = re.sub(r"\s{2,}", " ", result).strip()
        return result if len(result) >= 2 else text


# ---------------------------------------------------------------------------
# Module-level singleton + backward-compatible functions
# ---------------------------------------------------------------------------

_default_engine: Optional[TextToSpeechEngine] = None


def _get_default() -> TextToSpeechEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = TextToSpeechEngine()
    return _default_engine


def load_tts_engine() -> None:
    """Load the default TTS engine."""
    _get_default().load()


async def generate_speech_async(text: str, output_path: Optional[str] = None) -> Optional[str]:
    """Generate speech using the default engine."""
    return await _get_default().generate_speech(text, output_path)


def play_audio_file(audio_path: str) -> None:
    """Play audio using the default engine."""
    TextToSpeechEngine.play_audio(audio_path)


def speak_text(text: str) -> None:
    """Generate and play speech using the default engine."""
    _get_default().speak(text)
