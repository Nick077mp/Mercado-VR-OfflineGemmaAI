"""Standalone voice assistant — full pipeline with TTS audio output.

Pipeline: Microphone -> Faster-Whisper -> Ollama (local) -> Piper TTS
100% offline — no internet, no external servers.
"""

import asyncio
import os
import sys
import threading
import warnings
from contextlib import contextmanager
from typing import AsyncGenerator, Optional

import sounddevice as sd

from services.ollama_service import (
    ConversationEngine,
    sanitize_text,
    STATE_FINISHED,
    STATE_NEGOTIATING,
)
from services.stt_faster_whisper import SpeechRecognizer, suppress_cffi_errors
from services.tts_piper_only import TextToSpeechEngine

# Suppress CFFI noise
warnings.filterwarnings("ignore", category=UserWarning, module="cffi")


def _install_cffi_hook() -> None:
    """Silence CFFI callback exceptions in background threads."""
    def _handler(args):
        exc_str = str(args.exc_value) if args.exc_value else ""
        is_cffi = (
            "_CallbackContext" in exc_str
            or "NoneType" in exc_str
            or "sounddevice" in str(args.exc_traceback)
        )
        if not is_cffi:
            import traceback
            print(f"Error in thread {args.thread.name}: {args.exc_type.__name__}: {args.exc_value}",
                  file=sys.__stderr__)
            if args.exc_traceback:
                traceback.print_tb(args.exc_traceback, file=sys.__stderr__)

    threading.excepthook = _handler


_install_cffi_hook()


# ---------------------------------------------------------------------------
# VoiceAssistant
# ---------------------------------------------------------------------------

class VoiceAssistant:
    """Interactive voice assistant: record -> STT -> LLM -> TTS loop."""

    SILENCE_DURATION: float = 1.2

    def __init__(self) -> None:
        self.engine: ConversationEngine = ConversationEngine()
        self.stt: SpeechRecognizer = SpeechRecognizer()
        self.tts: TextToSpeechEngine = TextToSpeechEngine()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the interactive voice assistant."""
        self._clear_screen()
        print("=" * 70)
        print("  VOICE ASSISTANT — 100% OFFLINE")
        print("=" * 70)

        print("\n[Init] Loading models...\n")
        self.stt.load_models()
        self.tts.load()
        print("✅ Faster-Whisper loaded")
        print("✅ Piper TTS loaded")
        print("✅ Ollama must be running (ollama serve)")
        print("\n" + "=" * 70)

        valid_mics = self._list_microphones()
        device_id = self._select_microphone(valid_mics)

        while True:
            try:
                input("\n  Press ENTER to record (Ctrl+C to exit)")
                transcript = self._record(device_id)

                if transcript and transcript.strip():
                    await self._process_with_tts(transcript)

                if self.engine.state == STATE_FINISHED:
                    print("\n  Conversation finished.\n")
                    break

                again = input("\n  Another recording? (y/n): ").lower().strip()
                if again != "y" and again != "s":
                    break

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")

        print("\n  System stopped.")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _record(self, device_id: Optional[int]) -> str:
        """Record audio and return transcription."""
        print("\n[REC] Recording (speak now)...\n")
        last_len = 0

        def callback(text: str, is_final: bool = False) -> None:
            nonlocal last_len
            if is_final:
                print(f"\r  {text}{' ' * 20}")
                last_len = 0
            else:
                if len(text) > last_len:
                    print(f"\r  {text}", end="", flush=True)
                    last_len = len(text)

        with suppress_cffi_errors():
            transcript = self.stt.record_and_transcribe(
                callback=callback,
                device_id=device_id,
                silence_duration=self.SILENCE_DURATION,
            )

        print(f"\n\n[STT] Final transcription: {transcript}")
        return transcript

    # ------------------------------------------------------------------
    # LLM streaming
    # ------------------------------------------------------------------

    async def _stream_response(self, transcript: str) -> AsyncGenerator[str, None]:
        """Get LLM response and yield word by word for TTS streaming."""
        clean = sanitize_text(transcript)
        if not clean:
            return

        try:
            response, _state = self.engine.process_message(clean)

            if response is None:
                return

            clean_response = sanitize_text(response)
            for word in clean_response.split():
                yield word + " "
                await asyncio.sleep(0.03)

        except Exception:
            msg = "Disculpe, tuve un problema. ¿Me lo repite por favor?"
            for word in msg.split():
                yield word + " "
                await asyncio.sleep(0.03)

    # ------------------------------------------------------------------
    # TTS streaming
    # ------------------------------------------------------------------

    async def _process_with_tts(self, transcript: str) -> None:
        """Stream LLM response through TTS with sentence-level chunking."""
        buffer = ""
        first_audio = False

        print("\n[LLM] Responding:\n")

        async for chunk in self._stream_response(transcript):
            buffer += chunk
            print(chunk, end="", flush=True)

            phrase, buffer = self._split_for_tts(buffer)
            if phrase:
                phrase = self._clean_text_for_tts(phrase)
                if phrase:
                    if not first_audio:
                        print("\n[TTS] Streaming started...\n")
                        first_audio = True

                    audio_path = await self.tts.generate_speech(phrase)
                    if audio_path:
                        await asyncio.get_event_loop().run_in_executor(
                            None, self.tts.play_audio, audio_path,
                        )
                        try:
                            os.unlink(audio_path)
                        except OSError:
                            pass

        # Flush remaining buffer
        tail = self._clean_text_for_tts(buffer)
        if tail:
            audio_path = await self.tts.generate_speech(tail)
            if audio_path:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.tts.play_audio, audio_path,
                )
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass

        print("\n\n✅ Response complete\n")

    # ------------------------------------------------------------------
    # Text utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text_for_tts(text: str) -> str:
        """Strip markdown-like artefacts for cleaner TTS input."""
        for ch in "()[]{}*_\"":
            text = text.replace(ch, "")
        return " ".join(text.split()).strip()

    @staticmethod
    def _is_sentence_end(text: str, idx: int) -> bool:
        ch = text[idx]
        if ch in "?!":
            return True
        if ch == ".":
            prev_c = text[idx - 1] if idx > 0 else ""
            next_c = text[idx + 1] if idx + 1 < len(text) else ""
            if prev_c.isdigit() and next_c.isdigit():
                return False
            return True
        return False

    @staticmethod
    def _split_for_tts(buffer: str) -> tuple:
        """Split buffer into ``(ready_phrase, remaining_buffer)``."""
        if not buffer:
            return None, buffer

        for i in range(len(buffer)):
            if VoiceAssistant._is_sentence_end(buffer, i):
                cut = i + 1
                phrase = buffer[:cut].strip()
                rest = buffer[cut:].strip()
                return phrase, rest

        # Soft cut by length if buffer grows too long
        if len(buffer) > 140:
            cut = buffer.rfind(" ")
            if cut > 0:
                return buffer[:cut].strip(), buffer[cut:].strip()

        return None, buffer

    # ------------------------------------------------------------------
    # Microphone selection
    # ------------------------------------------------------------------

    @staticmethod
    def _list_microphones() -> list:
        with suppress_cffi_errors():
            devices = sd.query_devices()

        valid = []
        print("\n  AVAILABLE MICROPHONES:\n")
        print(f"  {'ID':<5} {'Name':<60} {'Channels'}")
        print("  " + "-" * 78)

        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                print(f"  {i:<5} {d['name']:<60} {d['max_input_channels']}")
                valid.append(i)

        print("  " + "-" * 78)
        return valid

    @staticmethod
    def _select_microphone(valid_ids: list) -> Optional[int]:
        while True:
            choice = input("  Microphone ID (or 'auto'): ").strip()
            if choice.lower() == "auto":
                print("  Using default microphone")
                return None
            if choice.isdigit() and int(choice) in valid_ids:
                return int(choice)
            print("  Invalid ID")

    @staticmethod
    def _clear_screen() -> None:
        os.system("cls" if os.name == "nt" else "clear")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    assistant = VoiceAssistant()
    asyncio.run(assistant.run())


if __name__ == "__main__":
    main()
