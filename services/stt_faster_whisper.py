"""Speech-to-text service using Faster-Whisper with Silero VAD.

4-5x faster than original Whisper — 100% offline.
Forces CPU to keep VRAM free for VR.
"""

import os
import queue
import re
import sys
import tempfile
import threading
import time
import warnings
from contextlib import contextmanager
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
import torch
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# Offline configuration (runs at import time)
# ---------------------------------------------------------------------------

def _is_first_run() -> bool:
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    return not any(
        os.path.exists(os.path.join(cache_dir, f"models--Systran--faster-whisper-{s}"))
        for s in ["tiny", "base", "small", "medium"]
    )


if not _is_first_run():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"

os.environ["TORCH_HOME"] = os.path.expanduser("~/.cache/torch")

# Force CPU: keep VRAM free for VR
os.environ["CUDA_VISIBLE_DEVICES"] = ""

warnings.filterwarnings("ignore", category=UserWarning, module="cffi")


@contextmanager
def suppress_cffi_errors():
    """Temporarily redirect *stderr* to suppress CFFI noise."""
    old_stderr = sys.stderr
    try:
        import io
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stderr = old_stderr


# ---------------------------------------------------------------------------
# SpeechRecognizer
# ---------------------------------------------------------------------------

class SpeechRecognizer:
    """Manages Faster-Whisper and Silero VAD for offline speech recognition."""

    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1
    DTYPE: str = "int16"

    # Sensitivity defaults
    DEFAULT_SPEECH_THRESHOLD: float = 0.8
    DEFAULT_SILENCE_DURATION: float = 1.2
    MIN_RECORDING_TIME: float = 0.5
    MAX_RECORDING_TIME: float = 30.0

    # Whisper model size: "tiny" | "base" | "small" | "medium"
    WHISPER_SIZE: str = "small"

    # Word corrections for common Whisper mistakes in Spanish
    _WORD_CORRECTIONS = [
        (r"\bsite\b", "siete"),
        (r"\bSite\b", "Siete"),
        (r"\bocho\s+cero\s+cero\s+cero\b", "8000"),
        (r"\bcinco\s+cero\s+cero\s+cero\b", "5000"),
        (r"\btres\s+cero\s+cero\s+cero\b", "3000"),
        (r"\bcuatro\s+cero\s+cero\s+cero\b", "4000"),
        (r"\bseis\s+cero\s+cero\s+cero\b", "6000"),
        (r"\bnueve\s+cero\s+cero\s+cero\b", "9000"),
        (r"\bdiez\s+cero\s+cero\s+cero\b", "10000"),
        (r"\bdos\s+cero\s+cero\s+cero\b", "2000"),
        (r"\buno\s+cero\s+cero\s+cero\b", "1000"),
        (r"\bocho\s+cero\s+cero\b", "800"),
        (r"\bcinco\s+cero\s+cero\b", "500"),
        (r"\btres\s+cero\s+cero\b", "300"),
    ]

    def __init__(self) -> None:
        self._whisper: Optional[WhisperModel] = None
        self._vad = None  # torch.jit.ScriptModule
        self._speech_threshold: float = self.DEFAULT_SPEECH_THRESHOLD

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load_models(self) -> None:
        """Load both Whisper and Silero VAD models."""
        self._load_whisper()
        self._load_silero_vad()

    def _load_whisper(self) -> None:
        if self._whisper is not None:
            return

        try:
            print("[STT] Loading Faster-Whisper...")

            cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
            model_local = os.path.join(
                cache_dir,
                f"models--Systran--faster-whisper-{self.WHISPER_SIZE}",
                "snapshots",
            )

            snapshot_path = None
            if os.path.exists(model_local):
                snapshots = [
                    d for d in os.listdir(model_local)
                    if os.path.isdir(os.path.join(model_local, d))
                ]
                if snapshots:
                    snapshot_path = os.path.join(model_local, snapshots[0])

            if snapshot_path and os.path.exists(snapshot_path):
                self._whisper = WhisperModel(snapshot_path, device="cpu", compute_type="int8")
            else:
                print("[STT] First run: downloading Faster-Whisper...")
                self._whisper = WhisperModel(
                    self.WHISPER_SIZE,
                    device="cpu",
                    compute_type="int8",
                    local_files_only=False,
                )

            print("✅ Faster-Whisper loaded (offline)")

        except Exception as e:
            print(f"❌ Error loading Faster-Whisper: {e}")
            self._whisper = None

    def _load_silero_vad(self) -> None:
        if self._vad is not None:
            return

        try:
            print("[STT] Loading Silero VAD...")
            torch_cache = os.path.expanduser("~/.cache/torch/hub")
            silero_cache = os.path.join(torch_cache, "snakers4_silero-vad_master")

            if os.path.exists(silero_cache):
                sys.path.insert(0, silero_cache)
                try:
                    model_path = os.path.join(silero_cache, "files", "silero_vad.jit")
                    if os.path.exists(model_path):
                        self._vad = torch.jit.load(model_path, map_location="cpu")
                        print("✅ Silero VAD loaded (offline)")
                    else:
                        model, _utils = torch.hub.load(
                            repo_or_dir=silero_cache,
                            model="silero_vad",
                            source="local",
                            force_reload=False,
                            verbose=False,
                        )
                        self._vad = model
                        print("✅ Silero VAD loaded from cache (offline)")
                finally:
                    if silero_cache in sys.path:
                        sys.path.remove(silero_cache)
            else:
                try:
                    print("[STT] First run: downloading Silero VAD...")
                    model, _utils = torch.hub.load(
                        repo_or_dir="snakers4/silero-vad",
                        model="silero_vad",
                        force_reload=False,
                        onnx=False,
                    )
                    self._vad = model
                    print("✅ Silero VAD downloaded and cached")
                except Exception:
                    print("[STT] Could not download Silero VAD, continuing without it")
                    self._vad = None

        except Exception as e:
            print(f"[STT] Error loading Silero VAD: {e}")
            self._vad = None

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe_file(self, audio_path: str) -> str:
        """Transcribe an audio file to text."""
        self._load_whisper()
        if self._whisper is None:
            return ""

        try:
            segments, _info = self._whisper.transcribe(
                audio_path,
                language="es",
                beam_size=1,
                vad_filter=True,
            )
            transcription = " ".join(seg.text for seg in segments).strip()
            return self._clean_transcription(transcription)
        except Exception as e:
            print(f"❌ Transcription error: {e}")
            return ""

    def _clean_transcription(self, text: str) -> str:
        """Fix common Whisper mistakes in Spanish."""
        if not text:
            return text
        result = text
        for pattern, replacement in self._WORD_CORRECTIONS:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        return result.strip()

    # ------------------------------------------------------------------
    # Recording + live transcription
    # ------------------------------------------------------------------

    def record_and_transcribe(
        self,
        callback: Callable[[str, bool], None],
        device_id: Optional[int] = None,
        silence_duration: float = DEFAULT_SILENCE_DURATION,
    ) -> str:
        """Record from microphone and transcribe using Silero VAD."""
        self._load_whisper()
        self._load_silero_vad()

        if self._whisper is None:
            print("❌ Faster-Whisper not available")
            return ""

        if self._vad is None:
            print("[STT] Silero VAD not available, using basic detection")
            return self._record_basic(callback, device_id, silence_duration)

        audio_queue: queue.Queue = queue.Queue()
        is_recording = threading.Event()
        is_recording.set()

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"[STT] Audio status: {status}")
            if is_recording.is_set():
                audio_queue.put(indata.copy())

        print("[STT] Recording...")

        with suppress_cffi_errors():
            stream = sd.InputStream(
                device=device_id,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=audio_callback,
                blocksize=512,
            )
            stream.start()

        audio_buffer = []
        last_speech_time: Optional[float] = None
        speaking_detected = False

        print(f"[STT] Silero VAD: silence={silence_duration}s after voice detected")

        try:
            start_time = time.time()

            while is_recording.is_set():
                if time.time() - start_time > self.MAX_RECORDING_TIME:
                    print(f"\n[STT] Max recording time reached ({self.MAX_RECORDING_TIME}s)")
                    is_recording.clear()
                    break

                try:
                    chunk = audio_queue.get(timeout=0.1)
                    audio_buffer.append(chunk)

                    audio_float = chunk.astype(np.float32) / 32768.0
                    audio_tensor = torch.from_numpy(audio_float.flatten())
                    speech_prob = self._vad(audio_tensor, self.SAMPLE_RATE).item()

                    if speech_prob > self._speech_threshold:
                        last_speech_time = time.time()
                        speaking_detected = True
                        print(
                            f"\r  Voice: {speech_prob:.2f} | Timer: 0.0s      ",
                            end="",
                        )
                    else:
                        if last_speech_time is None:
                            print(
                                f"\r  Waiting for voice... (prob: {speech_prob:.2f})      ",
                                end="",
                            )
                        else:
                            silence_time = time.time() - last_speech_time
                            print(
                                f"\r  Silence: {silence_time:.1f}s / "
                                f"{silence_duration}s (prob: {speech_prob:.2f})      ",
                                end="",
                            )

                    if speaking_detected and last_speech_time is not None:
                        elapsed = time.time() - start_time
                        silence_time = time.time() - last_speech_time
                        if elapsed > self.MIN_RECORDING_TIME and silence_time >= silence_duration:
                            print(f"\n[STT] Silence detected ({silence_time:.1f}s)")
                            is_recording.clear()
                            break

                except queue.Empty:
                    if speaking_detected and last_speech_time is not None:
                        silence_time = time.time() - last_speech_time
                        if silence_time >= silence_duration:
                            print(f"\n[STT] Silence detected ({silence_time:.1f}s)")
                            is_recording.clear()
                            break

        except KeyboardInterrupt:
            print("\n[STT] Recording cancelled")
            is_recording.clear()
        finally:
            is_recording.clear()
            time.sleep(0.1)
            with suppress_cffi_errors():
                stream.stop()
                stream.close()
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except Exception:
                    break

        if not audio_buffer:
            return ""

        audio_data = np.concatenate(audio_buffer, axis=0)

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_path = temp_file.name
        temp_file.close()
        sf.write(temp_path, audio_data, self.SAMPLE_RATE)

        print("[STT] Transcribing...")
        transcription = self.transcribe_file(temp_path)
        os.remove(temp_path)

        return transcription

    def _record_basic(
        self,
        callback: Callable,
        device_id: Optional[int] = None,
        silence_duration: float = 3.0,
    ) -> str:
        """Basic recording fallback when Silero VAD is not available."""
        self._load_whisper()

        if self._whisper is None:
            print("❌ Faster-Whisper not available")
            return ""

        audio_queue: queue.Queue = queue.Queue()
        is_recording = threading.Event()
        is_recording.set()

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"[STT] Status: {status}")
            if is_recording.is_set():
                audio_queue.put(indata.copy())

        print("[STT] Recording (basic mode)...")
        print(f"[STT] Will stop after {silence_duration}s of silence\n")

        with suppress_cffi_errors():
            stream = sd.InputStream(
                device=device_id,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=audio_callback,
            )
            stream.start()

        audio_buffer = []
        last_sound_time = time.time()
        silence_threshold = 500
        speaking_detected = False

        try:
            start_time = time.time()

            while is_recording.is_set():
                if time.time() - start_time > self.MAX_RECORDING_TIME:
                    print(f"\n[STT] Max recording time reached ({self.MAX_RECORDING_TIME}s)")
                    break

                try:
                    chunk = audio_queue.get(timeout=0.1)
                    audio_buffer.append(chunk)

                    energy = np.abs(chunk).mean()

                    if energy > silence_threshold:
                        last_sound_time = time.time()
                        if not speaking_detected:
                            print("[STT] Voice detected, recording...")
                            speaking_detected = True
                    else:
                        if speaking_detected:
                            silence_time = time.time() - last_sound_time
                            pct = min(100, int((silence_time / silence_duration) * 100))
                            print(f"\r  Silence: {pct}%", end="")

                    if speaking_detected:
                        elapsed = time.time() - start_time
                        silence_time = time.time() - last_sound_time
                        if elapsed > self.MIN_RECORDING_TIME and silence_time >= silence_duration:
                            print("\n[STT] Silence detected, stopping...")
                            break

                except queue.Empty:
                    continue

        except KeyboardInterrupt:
            print("\n[STT] Recording cancelled")
        finally:
            is_recording.clear()
            time.sleep(0.1)
            with suppress_cffi_errors():
                stream.stop()
                stream.close()
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except Exception:
                    break

        if not audio_buffer:
            return ""

        audio_data = np.concatenate(audio_buffer, axis=0)
        duration = len(audio_data) / self.SAMPLE_RATE
        print(f"\n[STT] Recording complete: {duration:.2f}s")

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_path = temp_file.name
        temp_file.close()
        sf.write(temp_path, audio_data, self.SAMPLE_RATE)

        print("[STT] Transcribing...")
        transcription = self.transcribe_file(temp_path)
        os.remove(temp_path)

        return transcription

    # ------------------------------------------------------------------
    # Sensitivity configuration
    # ------------------------------------------------------------------

    def adjust_sensitivity(self, level: str) -> None:
        """Adjust VAD sensitivity: ``"low"`` | ``"normal"`` | ``"high"``."""
        levels = {"low": 0.85, "normal": 0.80, "high": 0.70}
        if level not in levels:
            print(f"[STT] Invalid level. Options: {list(levels.keys())}")
            return
        self._speech_threshold = levels[level]
        print(f"[STT] Sensitivity set to {level.upper()} (threshold={self._speech_threshold})")

    def get_sensitivity_info(self) -> None:
        """Print current sensitivity settings."""
        if self._speech_threshold >= 0.85:
            label = "LOW"
        elif self._speech_threshold >= 0.8:
            label = "NORMAL"
        else:
            label = "HIGH"
        print(f"[STT] Threshold: {self._speech_threshold} | Silence: "
              f"{self.DEFAULT_SILENCE_DURATION}s | Level: {label}")


# ---------------------------------------------------------------------------
# Module-level singleton + backward-compatible functions
# ---------------------------------------------------------------------------

_default_recognizer: Optional[SpeechRecognizer] = None


def _get_default() -> SpeechRecognizer:
    global _default_recognizer
    if _default_recognizer is None:
        _default_recognizer = SpeechRecognizer()
    return _default_recognizer


def load_whisper_model() -> None:
    """Load the default Whisper model."""
    _get_default()._load_whisper()


def load_silero_vad_model() -> None:
    """Load the default Silero VAD model."""
    _get_default()._load_silero_vad()


def transcribe_audio_file(audio_path: str) -> str:
    """Transcribe a file using the default recognizer."""
    return _get_default().transcribe_file(audio_path)


def record_and_transcribe_streaming(
    callback: Callable,
    device_id: Optional[int] = None,
    silence_duration: float = SpeechRecognizer.DEFAULT_SILENCE_DURATION,
) -> str:
    """Record and transcribe using the default recognizer."""
    return _get_default().record_and_transcribe(callback, device_id, silence_duration)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("[STT] Testing Faster-Whisper...")
    recognizer = SpeechRecognizer()
    recognizer.load_models()

    print("\nPress Ctrl+C to stop")
    print("Speak now...\n")

    def _print_cb(text: str, is_final: bool = False) -> None:
        print(f"  {text}")

    result = recognizer.record_and_transcribe(_print_cb)
    print(f"\n✅ Final transcription: {result}")
