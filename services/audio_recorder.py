"""Audio recording, transcription, and VR push communication.

Encapsulates microphone management (push-to-talk and fixed-duration),
numpy-to-STT transcription, and VR response delivery (async + sync).
"""

import os
import tempfile
import threading
import time
from typing import List, Optional, Tuple

import httpx
import numpy as np
import requests
import scipy.io.wavfile
import sounddevice as sd

from services.stt_faster_whisper import transcribe_audio_file


# ---------------------------------------------------------------------------
# AudioRecorder — manages microphone recording state
# ---------------------------------------------------------------------------

class AudioRecorder:
    """Encapsulates all recording hardware state and operations."""

    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1

    def __init__(self) -> None:
        self.is_active: bool = False
        self.data: List[np.ndarray] = []
        self.stream: Optional[sd.InputStream] = None
        self.microphone_id: Optional[int] = None
        self._audio_ready: bool = False

    # ------------------------------------------------------------------
    # Microphone detection
    # ------------------------------------------------------------------

    def auto_detect_microphone(self) -> Optional[int]:
        """Auto-detect microphone: prioritize real VR > system > VR phantom."""
        devices = sd.query_devices()
        system_mics: List[Tuple[int, str, int]] = []
        vr_candidates: List[Tuple[int, str]] = []

        print("[REC] Scanning microphones...")

        for i, device in enumerate(devices):
            if device["max_input_channels"] <= 0:
                continue
            name = device["name"].lower()

            is_vr_phantom = (
                any(kw in name for kw in ["oculus", "headset microphone"])
                and "virtual audio device" in name
            )
            is_system = (
                ("intel" in name and "smart" in name)
                or ("varios micrófonos" in name and device["max_input_channels"] >= 4)
            )

            if is_vr_phantom:
                vr_candidates.append((i, device["name"]))
            elif is_system:
                system_mics.append((i, device["name"], device["max_input_channels"]))

        # Test VR candidates for real hardware
        for vr_id, vr_name in vr_candidates:
            try:
                t0 = time.time()
                sd.rec(frames=100, samplerate=16000, channels=1, device=vr_id, dtype="float32")
                sd.wait()
                if time.time() - t0 < 1:
                    print(f"[REC] VR microphone detected: {vr_name} (ID: {vr_id})")
                    return vr_id
            except Exception:
                continue

        # Fallback to system microphone
        if system_mics:
            system_mics.sort(key=lambda x: x[2], reverse=True)
            best = system_mics[0]
            print(f"[REC] Using system mic: {best[1]} (ID: {best[0]})")
            return best[0]

        print("[REC] Using default microphone")
        return None

    def init_audio(self) -> None:
        """Mark audio services as ready."""
        self._audio_ready = True
        print("✅ Audio services ready (STT)")

    @property
    def audio_ready(self) -> bool:
        return self._audio_ready

    # ------------------------------------------------------------------
    # Push-to-talk recording
    # ------------------------------------------------------------------

    def start(self) -> Tuple[bool, str]:
        """Start microphone recording (VR button pressed)."""
        if self.is_active:
            return False, "Recording already active"

        try:
            self.data = []
            self.is_active = True

            def _audio_cb(indata, frames, time_info, status):
                if status:
                    print(f"[REC] Audio status: {status}")
                if self.is_active:
                    self.data.append(indata.copy())

            self.stream = sd.InputStream(
                device=self.microphone_id,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="float32",
                callback=_audio_cb,
            )
            self.stream.start()
            print("[REC] Recording started (VR button)")
            return True, "Recording started"

        except Exception as e:
            self.is_active = False
            print(f"❌ Error starting recording: {e}")
            return False, f"Error: {e}"

    def stop(self) -> Tuple[bool, str, Optional[str]]:
        """Stop recording and save to temp WAV file.

        Returns ``(success, message, audio_path)``.
        """
        if not self.is_active:
            return False, "No active recording", None

        try:
            self.is_active = False
            time.sleep(0.1)

            if self.stream:
                try:
                    if self.stream.active:
                        self.stream.stop()
                except Exception:
                    pass
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

            print("[REC] Recording stopped (VR button)")

            if not self.data:
                print("[REC] No audio data captured")
                return False, "No audio data", None

            audio = np.concatenate(self.data, axis=0)
            duration = len(audio) / self.SAMPLE_RATE
            print(f"[REC] Audio: {duration:.2f}s, {len(self.data)} chunks")

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_path = temp_file.name
            temp_file.close()

            scipy.io.wavfile.write(
                temp_path, self.SAMPLE_RATE, (audio * 32767).astype(np.int16),
            )

            return True, f"Audio captured: {duration:.2f}s", temp_path

        except Exception as e:
            self.is_active = False
            print(f"❌ Error processing recording: {e}")
            return False, f"Error: {e}", None

    # ------------------------------------------------------------------
    # Fixed-duration recording
    # ------------------------------------------------------------------

    def record_fixed(self, duration: int = 5) -> Optional[np.ndarray]:
        """Record a fixed number of seconds."""
        try:
            print(f"[REC] Recording {duration}s...")
            audio = sd.rec(
                duration * self.SAMPLE_RATE,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="float32",
                device=self.microphone_id,
            )
            sd.wait()
            return audio.flatten()
        except Exception as e:
            print(f"❌ Error recording: {e}")
            return None

    # ------------------------------------------------------------------
    # Transcription helper
    # ------------------------------------------------------------------

    def transcribe_numpy(self, audio_data: np.ndarray, timeout: int = 15) -> str:
        """Transcribe a numpy audio array via temp file with timeout."""
        try:
            if len(audio_data) == 0:
                return ""

            if np.max(np.abs(audio_data)) < 0.001:
                print("[REC] Audio too quiet, skipping")
                return ""

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                temp_path = tf.name

            scipy.io.wavfile.write(
                temp_path, self.SAMPLE_RATE, (audio_data * 32767).astype(np.int16),
            )

            result: List[Optional[str]] = [None]
            error: List[Optional[Exception]] = [None]

            def _worker():
                try:
                    result[0] = transcribe_audio_file(temp_path)
                except Exception as exc:
                    error[0] = exc

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout)

            transcription = ""
            if t.is_alive():
                print("[REC] Transcription timeout")
            elif error[0]:
                print(f"❌ Transcription error: {error[0]}")
            else:
                transcription = result[0] or ""

            try:
                os.unlink(temp_path)
            except OSError:
                pass

            return transcription

        except Exception as e:
            print(f"❌ Error transcribing audio: {e}")
            return ""


# ---------------------------------------------------------------------------
# VR communication helpers
# ---------------------------------------------------------------------------

VR_RECEIVE_URL: str = "http://127.0.0.1:8001/chat"


async def send_text_to_vr_async(
    text: str,
    conversation_finished: bool = False,
    state: str = "negotiating",
) -> None:
    """Push response to VR (async). Falls back silently to polling mode."""
    try:
        data = {
            "response": text,
            "state": state,
            "conversation_finished": conversation_finished,
            "conversation_negotiation_cancel": False,
        }
        print(f"[VR] Push JSON: {data}")
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(VR_RECEIVE_URL, json=data)
            if response.status_code == 200:
                print("[VR] Text pushed to VR successfully")
            else:
                print(f"[VR] VR responded with code: {response.status_code}")
    except httpx.ConnectError:
        pass  # Normal: VR using polling mode
    except Exception as e:
        print(f"[VR] Push error: {type(e).__name__}")


def send_text_to_vr(
    text: str,
    conversation_finished: bool = False,
    state: str = "negotiating",
) -> None:
    """Push response to VR (sync). Falls back silently to polling mode."""
    try:
        data = {
            "response": text,
            "state": state,
            "conversation_finished": conversation_finished,
            "conversation_negotiation_cancel": False,
        }
        print(f"[VR] Push JSON (sync): {data}")
        response = requests.post(VR_RECEIVE_URL, json=data, timeout=2.0)
        if response.status_code == 200:
            print("[VR] Text pushed to VR successfully")
        else:
            print(f"[VR] VR responded with code: {response.status_code}")
    except requests.ConnectionError:
        pass  # Normal: VR using polling mode
    except Exception as e:
        print(f"[VR] Push error: {type(e).__name__}")
