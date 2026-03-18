"""API server for VR voice assistant communication.

Pipeline: VR button press -> record audio -> STT -> LLM -> response to VR.
Supports both push (port 8001) and polling (GET /get_latest_response) delivery.
"""

import asyncio
import os
import queue
import time
import warnings
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import SystemStatus, VoiceRecordRequest, VoiceResponse

from services.audio_recorder import (
    AudioRecorder,
    send_text_to_vr,
    send_text_to_vr_async,
)

from services.ollama_service import (
    ConversationEngine,
    sanitize_text,
    warmup_model,
    STATE_FINISHED,
)

from services.stt_faster_whisper import transcribe_audio_file

# Suppress noisy library warnings
warnings.filterwarnings("ignore", message=".*_CallbackContext.*")
warnings.filterwarnings("ignore", category=DeprecationWarning)


logging.getLogger("cffi").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Voice Server API",
    description="API for VR <-> AI voice server communication",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global instances
# ---------------------------------------------------------------------------

engine = ConversationEngine()
recorder = AudioRecorder()

vr_response_queue: queue.Queue = queue.Queue()

latest_ai_response: Dict[str, Any] = {
    "response": "",
    "state": "waiting",
    "conversation_finished": False,
    "has_response": False,
}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

async def check_ollama_health() -> bool:
    """Lightweight check that Ollama is reachable."""
    try:
        def _check() -> bool:
            resp = requests.get("http://localhost:11434/api/tags", timeout=2.0)
            resp.raise_for_status()
            return True
        return await asyncio.to_thread(_check)
    except Exception as e:
        print(f"❌ Ollama not available: {e}")
        return False


def process_voice_to_response(audio_data: np.ndarray) -> Tuple[Optional[Dict], Optional[str]]:
    """Full pipeline: numpy audio -> STT -> LLM -> response dict."""
    try:
        print("[API] Transcribing...")
        transcription = recorder.transcribe_numpy(audio_data)
        if not transcription:
            return None, "Could not transcribe audio"

        user_text = sanitize_text(transcription)
        print(f"[API] Transcription: {user_text}")

        print("[API] Generating response...")
        ai_response, _state = engine.process_message(user_text)

        return {
            "transcription": transcription,
            "response": ai_response,
            "state": engine.state,
        }, None

    except Exception as e:
        return None, f"Error processing voice: {e}"


# ---------------------------------------------------------------------------
# Background audio processing (push-to-talk flow)
# ---------------------------------------------------------------------------

async def process_audio_background(audio_path: str) -> None:
    """Process recorded audio: STT -> LLM -> push/polling to VR."""
    global latest_ai_response

    try:
        t0 = time.perf_counter()

        print("[API] Transcribing audio...")
        transcription = await asyncio.to_thread(transcribe_audio_file, audio_path)
        t_stt = time.perf_counter()
        print(f"[API] STT: {t_stt - t0:.2f}s")

        try:
            os.unlink(audio_path)
        except OSError:
            pass

        if not transcription or len(transcription.strip()) < 2:
            print("[API] No clear text detected")
            return

        user_text = sanitize_text(transcription)
        print(f"[API] User: {user_text}")
        print("[API] LLM processing...")

        # Callback: send first complete sentence to VR early
        first_sentence_time: List[Optional[float]] = [None]

        def on_first_sentence(partial_text: str) -> None:
            first_sentence_time[0] = time.perf_counter()
            elapsed = first_sentence_time[0] - t_stt
            print(f"[API] First sentence ready in {elapsed:.2f}s: {partial_text[:50]}...")
            send_text_to_vr(partial_text, False, engine.state)

        ai_response, new_state = await asyncio.to_thread(
            engine.process_message, user_text, on_first_sentence,
        )
        t_llm = time.perf_counter()
        print(f"[API] LLM: {t_llm - t_stt:.2f}s")

        if ai_response:
            print(f"[API] Response: {ai_response}")

            conversation_finished = engine.state == STATE_FINISHED

            latest_ai_response = {
                "response": ai_response,
                "state": engine.state,
                "conversation_finished": conversation_finished,
                "has_response": True,
            }
            print(f"[API] Total pipeline: {time.perf_counter() - t0:.2f}s")

            # Push full response to VR (replaces partial)
            await send_text_to_vr_async(ai_response, conversation_finished, engine.state)

    except Exception as e:
        print(f"❌ Error in background processing: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", summary="Health check")
async def root():
    return {"message": "AI Voice Server API running", "status": "healthy", "version": "1.0.0"}


@app.get("/latest_response", summary="Get latest response for VR (queue-based)")
async def get_latest_response():
    """VR polling endpoint (thread-safe queue)."""
    try:
        response_text = vr_response_queue.get_nowait()
        print(f"[API] VR received: {response_text[:30]}...")
        return {
            "response": response_text,
            "state": "negotiating",
            "conversation_finished": False,
            "conversation_negotiation_cancel": False,
        }
    except queue.Empty:
        return {
            "response": "",
            "state": "waiting",
            "conversation_finished": False,
            "conversation_negotiation_cancel": False,
        }


@app.post("/voice_record", response_model=VoiceResponse, summary="Record and process voice")
async def voice_record(request: VoiceRecordRequest):
    if not recorder.audio_ready:
        raise HTTPException(status_code=503, detail="Audio services not available")

    try:
        audio_data = recorder.record_fixed(duration=request.duration_seconds)
        if audio_data is None:
            raise HTTPException(status_code=500, detail="Recording error")

        result, error = process_voice_to_response(audio_data)
        if error:
            raise HTTPException(status_code=500, detail=error)

        finished = "qr" in result["response"].lower()
        return VoiceResponse(
            transcription=result["transcription"],
            response=result["response"],
            state=result["state"],
            conversation_finished=finished,
            conversation_negotiation_cancel=False,
            audio_generated=True,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /voice_record: {e}")
        raise HTTPException(status_code=500, detail=f"Voice processing error: {e}")


@app.get("/status", response_model=SystemStatus, summary="System status")
async def get_status():
    try:
        ollama_ok = await check_ollama_health()
        return SystemStatus(
            status="running",
            active_sessions=1,
            ollama_available=ollama_ok,
            stt_loaded=True,
            system_info={
                "mode": "api_server_vr_button_control",
                "sessions_count": "1",
                "ollama_status": "available" if ollama_ok else "unavailable",
                "audio_services": "ready" if recorder.audio_ready else "not_ready",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status error: {e}")


# -- Push-to-talk VR endpoints --

@app.post("/start_recording", summary="Start recording (VR button pressed)")
async def start_recording():
    try:
        success, message = recorder.start()
        return {
            "status": "success" if success else "error",
            "message": message,
            "recording_active": success,
        }
    except Exception as e:
        print(f"❌ Error in /start_recording: {e}")
        raise HTTPException(status_code=500, detail=f"Start recording error: {e}")


@app.post("/stop_recording", summary="Stop recording and process (VR button released)")
async def stop_recording(background_tasks: BackgroundTasks):
    try:
        success, message, audio_path = recorder.stop()

        if not success:
            return {"status": "error", "message": message, "recording_active": False}

        if not audio_path:
            return {"status": "warning", "message": "No audio to process", "recording_active": False}

        background_tasks.add_task(process_audio_background, audio_path)

        return {
            "status": "processing",
            "message": "Audio being processed, response via polling or push",
            "recording_active": False,
        }

    except Exception as e:
        print(f"❌ Error in /stop_recording: {e}")
        raise HTTPException(status_code=500, detail=f"Stop recording error: {e}")


@app.get("/recording_status", summary="Current recording status")
async def recording_status():
    try:
        return {
            "recording_active": recorder.is_active,
            "microphone_id": recorder.microphone_id,
            "sample_rate": recorder.SAMPLE_RATE,
            "channels": recorder.CHANNELS,
            "conversation_state": engine.state,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status error: {e}")


@app.get("/get_latest_response", summary="Get latest response (polling for Unreal)")
async def get_latest_response_polling():
    global latest_ai_response
    try:
        if latest_ai_response["has_response"]:
            response = latest_ai_response.copy()
            latest_ai_response["has_response"] = False
            print(f"[API] Unreal polled response: {response['response'][:50]}...")
            return response

        return {
            "response": "",
            "state": "waiting",
            "conversation_finished": False,
            "has_response": False,
        }
    except Exception as e:
        print(f"❌ Error in /get_latest_response: {e}")
        raise HTTPException(status_code=500, detail=f"Polling error: {e}")


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_api_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    print("=" * 70)
    print("  AI VOICE SERVER — VR BUTTON CONTROL MODE")
    print("=" * 70)

    # Initialize audio
    print("[API] Initializing audio services...")
    recorder.init_audio()

    # Detect microphone
    print("[API] Detecting VR microphone...")
    recorder.microphone_id = recorder.auto_detect_microphone()

    # Warmup: pre-load LLM into GPU VRAM for instant first response
    print("[API] Warming up LLM model...")
    warmup_model()

    print(f"\n  URL: http://{host}:{port}")
    print(f"  Docs: http://{host}:{port}/docs")
    print("=" * 70)
    print("  Endpoints:")
    print("   GET  /                     - Health check")
    print("   GET  /latest_response      - VR queue polling")
    print("   POST /voice_record         - Record and process voice")
    print("   POST /start_recording      - VR button pressed")
    print("   POST /stop_recording       - VR button released")
    print("   GET  /recording_status     - Recording state")
    print("   GET  /get_latest_response  - Polling for Unreal")
    print("   GET  /status               - System status")
    print("=" * 70)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    start_api_server()
