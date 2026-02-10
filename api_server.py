"""
API Server para comunicación con aplicación VR
==============================================
Python maneja el ciclo completo:
1. VR presiona botón → Graba audio → STT → IA → TTS
2. Enviar respuesta de IA a VR para lip sync
3. VR solo RECIBE texto para animar avatar

Autor: Nicolás
MEJORADO CON: Validación de totales, eliminación de repeticiones, conversación fluida
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uvicorn
import asyncio
import json
import threading
import datetime
import requests
import queue
import time
import httpx

# Importar servicios existentes - MEJORADO
from services.ollama_service import (
    ollama_generate,
    PriceTracker,  # NUEVO
    extract_price_and_product,  # NUEVO
    detect_seller_total,  # NUEVO
    sanitize_text,
    trim_history,
    STATE_NEGOTIATING,
    STATE_BUILDING_ORDER,
    STATE_READY_TO_PAY,
    STATE_PAYMENT,
    STATE_FINISHED,
)

# Importar servicios de audio
from services.stt_faster_whisper import record_and_transcribe_streaming, transcribe_audio_file
from services.tts_piper_only import speak_text, load_tts_engine
import sounddevice as sd
import numpy as np
import io
import wave
import tempfile
import os
import scipy.io.wavfile
import warnings
import sys

# Suprimir warnings
warnings.filterwarnings('ignore', message='.*_CallbackContext.*')
warnings.filterwarnings('ignore', category=DeprecationWarning)

import logging
logging.getLogger('cffi').setLevel(logging.ERROR)

# =========================
# CONFIGURACIÓN API
# =========================
app = FastAPI(
    title="AI Voice Server API",
    description="API para comunicación entre aplicación VR y servidor de IA de voz",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# MODELOS PYDANTIC
# =========================
class VoiceRecordRequest(BaseModel):
    duration_seconds: int = 5

class VoiceResponse(BaseModel):
    transcription: str
    response: str
    state: str
    conversation_finished: bool
    conversation_negotiation_cancel: bool
    audio_generated: bool

class SystemStatus(BaseModel):
    status: str
    active_sessions: int
    ollama_available: bool
    stt_loaded: bool
    tts_loaded: bool
    system_info: Dict[str, str]

# =========================
# VARIABLES GLOBALES
# =========================
conversation_history = []
conversation_state = STATE_NEGOTIATING
price_tracker = PriceTracker()  # NUEVO - Instancia global del tracker

tts_ready = False
audio_ready = False
selected_microphone_id = None

vr_response_queue = queue.Queue()

latest_ai_response = {
    "response": "",
    "state": "waiting",
    "conversation_finished": False,
    "has_response": False
}

# =========================
# VARIABLES PARA CONTROL DE BOTÓN VR
# =========================
is_recording_active = False
recording_thread = None
recording_data = []
recording_stream = None

SAMPLE_RATE = 16000
CHANNELS = 1

VR_RECEIVE_URL = "http://127.0.0.1:8001/chat"


async def send_text_to_vr_async(ai_response, conversation_finished=False, state="negotiating"):
    """ENVIAR respuesta de IA a VR para lip sync (versión asíncrona)"""
    try:
        data = {
            "response": ai_response,
            "state": state,
            "conversation_finished": conversation_finished,
            "conversation_negotiation_cancel": False
        }
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(VR_RECEIVE_URL, json=data)
            if response.status_code == 200:
                print("📡 ✅ Texto enviado a VR exitosamente (modo Push)")
            else:
                print(f"📡 ⚠️ VR respondió con código: {response.status_code}")
    except httpx.ConnectError:
        print("📡 ℹ️ VR usando modo Polling (puerto 8001 no disponible - esto es normal)")
    except Exception as e:
        print(f"📡 ⚠️ Error conectando a VR: {type(e).__name__}")


def send_text_to_vr(ai_response, conversation_finished=False, state="negotiating"):
    """ENVIAR respuesta de IA a VR para lip sync (versión síncrona)"""
    try:
        data = {
            "response": ai_response,
            "state": state,
            "conversation_finished": conversation_finished,
            "conversation_negotiation_cancel": False
        }
        response = requests.post(VR_RECEIVE_URL, json=data, timeout=2.0)
        if response.status_code == 200:
            print("📡 ✅ Texto enviado a VR exitosamente (modo Push)")
        else:
            print(f"📡 ⚠️ VR respondió con código: {response.status_code}")
    except requests.ConnectionError:
        print("📡 ℹ️ VR usando modo Polling (puerto 8001 no disponible - esto es normal)")
    except Exception as e:
        print(f"📡 ⚠️ Error conectando a VR: {type(e).__name__}")


# =========================
# FUNCIONES PARA CONTROL DE BOTÓN VR
# =========================
def start_manual_recording():
    """Iniciar grabación controlada por botón VR"""
    global is_recording_active, recording_data, recording_stream

    if is_recording_active:
        return False, "Grabación ya activa"

    try:
        recording_data = []
        is_recording_active = True

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"⚠️ Audio status: {status}")
            if is_recording_active:
                recording_data.append(indata.copy())

        recording_stream = sd.InputStream(
            device=selected_microphone_id,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='float32',
            callback=audio_callback
        )

        recording_stream.start()
        print("🎙️ ✅ Grabación iniciada por botón VR")
        return True, "Grabación iniciada"

    except Exception as e:
        is_recording_active = False
        print(f"❌ Error iniciando grabación: {e}")
        return False, f"Error: {str(e)}"


def stop_manual_recording():
    """Detener grabación y procesar audio"""
    global is_recording_active, recording_data, recording_stream

    if not is_recording_active:
        return False, "No hay grabación activa", None

    try:
        is_recording_active = False
        time.sleep(0.1)

        if recording_stream:
            try:
                if recording_stream.active:
                    recording_stream.stop()
            except Exception:
                pass
            try:
                recording_stream.close()
            except Exception:
                pass
            recording_stream = None

        print("🎙️ ⏹️ Grabación detenida por botón VR")

        if not recording_data:
            print("❌ No hay datos de audio")
            return False, "No hay datos de audio", None

        print(f"🔍 Datos encontrados: {len(recording_data)} chunks")

        audio_data = np.concatenate(recording_data, axis=0)
        duration = len(audio_data) / SAMPLE_RATE
        print(f"✅ Audio procesado: {duration:.2f} segundos")

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        temp_path = temp_file.name
        temp_file.close()

        scipy.io.wavfile.write(temp_path, SAMPLE_RATE, (audio_data * 32767).astype(np.int16))
        print(f"🔍 Archivo guardado en: {temp_path}")

        return True, f"Audio procesado: {duration:.2f}s", temp_path

    except Exception as e:
        is_recording_active = False
        print(f"❌ Error procesando grabación: {e}")
        return False, f"Error: {str(e)}", None


def auto_detect_vr_microphone():
    """Auto-detectar micrófono: PRIORIDAD VR real > Sistema > VR fantasma"""
    devices = sd.query_devices()

    system_mics = []
    vr_candidates = []

    print("🔍 Analizando micrófonos disponibles...")

    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            name = device['name'].lower()
            print(f"🔍 DEBUG - ID {i}: {device['name']} ({device['max_input_channels']} canales)")

            if any(keyword in name for keyword in ['oculus', 'headset microphone']) and 'virtual audio device' in name:
                vr_candidates.append((i, device['name']))
                print(f"🎧 VR candidato encontrado: ID {i} - {device['name']}")
            elif ('intel' in name and 'smart' in name) or ('varios micrófonos' in name and device['max_input_channels'] >= 4):
                system_mics.append((i, device['name'], device['max_input_channels']))
                print(f"🎤 Sistema encontrado: ID {i} - {device['name']} ({device['max_input_channels']} canales)")

    print(f"📊 Resumen: {len(system_mics)} micrófonos sistema, {len(vr_candidates)} VR candidatos")

    for vr_id, vr_name in vr_candidates:
        print(f"🔍 Probando VR real: ID {vr_id} - {vr_name}")
        try:
            start_time = time.time()
            test_data = sd.rec(frames=100, samplerate=16000, channels=1, device=vr_id, dtype='float32')
            sd.wait()
            end_time = time.time()
            if end_time - start_time < 1:
                print(f"🎧✅ VR REAL DETECTADO: {vr_name} (ID: {vr_id})")
                return vr_id
            else:
                print(f"🎧⚠️ VR lento/problemático: {vr_name}")
        except Exception as e:
            print(f"🎧❌ VR fantasma: {vr_name} - {str(e)[:40]}...")
            continue

    if system_mics:
        system_mics.sort(key=lambda x: x[2], reverse=True)
        best_mic = system_mics[0]
        print(f"🎤✅ Usando sistema como fallback: {best_mic[1]} (ID: {best_mic[0]}, {best_mic[2]} canales)")
        return best_mic[0]

    print("⚠️ Usando micrófono predeterminado")
    return None


def init_audio_services():
    """Inicializar servicios TTS"""
    global tts_ready, audio_ready

    try:
        print("🔄 Inicializando Piper TTS (solo, sin clonación)...")
        load_tts_engine()
        tts_ready = True
        print("✅ Piper TTS inicializado")
        print("✅ Voz: Mexicana natural (sin clonación)")
        print("✅ Calidad: Alta - Sin artefactos")
        audio_ready = True
    except Exception as e:
        print(f"❌ Error inicializando servicios de audio: {e}")
        tts_ready = False
        audio_ready = False


def transcribe_audio_numpy(audio_data, sample_rate=16000, timeout=15):
    """Transcribir audio desde numpy array usando archivo temporal con timeout"""
    try:
        if len(audio_data) == 0:
            print("⚠️ Audio vacío, omitiendo transcripción")
            return ""

        audio_level = np.max(np.abs(audio_data))
        if audio_level < 0.001:
            print("⚠️ Audio muy silencioso, omitiendo transcripción")
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_path = temp_file.name

            scipy.io.wavfile.write(temp_path, sample_rate, (audio_data * 32767).astype(np.int16))

            result = [None]
            error = [None]

            def transcribe_worker():
                try:
                    result[0] = transcribe_audio_file(temp_path)
                except Exception as e:
                    error[0] = e

            worker_thread = threading.Thread(target=transcribe_worker)
            worker_thread.daemon = True
            worker_thread.start()
            worker_thread.join(timeout)

            if worker_thread.is_alive():
                print("⚠️ Transcripción timeout, omitiendo")
                transcription = ""
            elif error[0]:
                print(f"❌ Error en transcripción: {error[0]}")
                transcription = ""
            else:
                transcription = result[0] or ""

            try:
                os.unlink(temp_path)
            except Exception:
                pass

            return transcription

    except Exception as e:
        print(f"❌ Error transcribiendo audio: {e}")
        return ""


def record_audio(duration=5, sample_rate=16000):
    """Grabar audio desde micrófono usando el ID seleccionado"""
    global selected_microphone_id

    try:
        print(f"🎙️ Grabando por {duration} segundos...")
        audio_data = sd.rec(
            duration * sample_rate,
            samplerate=sample_rate,
            channels=1,
            dtype='float32',
            device=selected_microphone_id
        )
        sd.wait()
        return audio_data.flatten()
    except Exception as e:
        print(f"❌ Error grabando audio: {e}")
        return None


# =========================
# UTILIDADES
# =========================
async def check_ollama_health() -> bool:
    """Verificar que Ollama esté disponible (chequeo ligero)."""
    try:
        base_url = "http://localhost:11434"

        def _check():
            resp = requests.get(f"{base_url}/api/tags", timeout=2.0)
            resp.raise_for_status()
            return True

        return await asyncio.to_thread(_check)
    except Exception as e:
        print(f"❌ Ollama no disponible: {e}")
        return False


def process_voice_to_response(audio_data):
    """Procesar audio completo: STT → IA → TTS"""
    try:
        global conversation_history, conversation_state, price_tracker

        print("🔄 Transcribiendo...")
        transcription = transcribe_audio_numpy(audio_data)
        if not transcription:
            return None, "No se pudo transcribir el audio"

        user_text = sanitize_text(transcription)
        print(f"📝 Transcripción: {user_text}")

        # NUEVO: Detectar y registrar productos del vendedor
        product_price = extract_price_and_product(user_text)
        if product_price:
            product_name, price = product_price
            price_tracker.add_product(product_name, 1, price)
            print(f"📦 Producto registrado: {product_name} × 1 @ {price} COP")

        print("🤖 IA respondiendo...")
        # NUEVO: Pasar price_tracker a ollama_generate
        ai_response, new_state = ollama_generate(
            conversation_history,
            user_text,
            conversation_state,
            price_tracker=price_tracker
        )

        conversation_history.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": ai_response}
        ])
        conversation_state = new_state

        print("🎵 Generando audio...")
        speak_text(ai_response)

        print(f"✅ Ciclo completo: {user_text} → {ai_response}")

        return {
            "transcription": transcription,
            "response": ai_response,
            "state": new_state
        }, None

    except Exception as e:
        return None, f"Error procesando voz: {str(e)}"


# =========================
# ENDPOINTS
# =========================
@app.get("/", summary="Health Check")
async def root():
    return {
        "message": "AI Voice Server API está corriendo",
        "status": "healthy",
        "version": "1.0.0"
    }


@app.get("/latest_response", summary="Obtener última respuesta para VR")
async def get_latest_response():
    """VR consulta - THREAD-SAFE"""
    global vr_response_queue

    try:
        response_text = vr_response_queue.get_nowait()
        print(f"📡 VR RECIBE: {response_text[:30]}...")
        return {
            "response": response_text,
            "state": "negotiating",
            "conversation_finished": False,
            "conversation_negotiation_cancel": False
        }
    except queue.Empty:
        return {
            "response": "",
            "state": "waiting",
            "conversation_finished": False,
            "conversation_negotiation_cancel": False
        }


@app.post("/voice_record", response_model=VoiceResponse, summary="Grabar voz y procesar")
async def voice_record(request: VoiceRecordRequest):
    if not audio_ready:
        raise HTTPException(status_code=503, detail="Servicios de audio no disponibles")

    try:
        audio_data = record_audio(duration=request.duration_seconds)
        if audio_data is None:
            raise HTTPException(status_code=500, detail="Error grabando audio")

        result, error = process_voice_to_response(audio_data)
        if error:
            raise HTTPException(status_code=500, detail=error)

        conversation_finished = "pago por qr" in result["response"].lower() or "qr" in result["response"].lower()

        return VoiceResponse(
            transcription=result["transcription"],
            response=result["response"],
            state=result["state"],
            conversation_finished=conversation_finished,
            conversation_negotiation_cancel=False,
            audio_generated=True
        )

    except Exception as e:
        print(f"❌ Error en /voice_record: {e}")
        raise HTTPException(status_code=500, detail=f"Error procesando voz: {str(e)}")


@app.get("/status", response_model=SystemStatus, summary="Estado del sistema")
async def get_status():
    try:
        ollama_ok = await check_ollama_health()

        return SystemStatus(
            status="running",
            active_sessions=1,
            ollama_available=ollama_ok,
            stt_loaded=True,
            tts_loaded=tts_ready,
            system_info={
                "mode": "api_server_vr_button_control",
                "sessions_count": "1",
                "ollama_status": "available" if ollama_ok else "unavailable",
                "audio_services": "ready" if audio_ready else "not_ready"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo status: {str(e)}")


# =========================
# ENDPOINTS PARA CONTROL DE BOTÓN VR
# =========================
@app.post("/start_recording", summary="Iniciar grabación por botón VR")
async def start_recording():
    try:
        success, message = start_manual_recording()

        if success:
            return {
                "status": "success",
                "message": message,
                "recording_active": True
            }
        else:
            return {
                "status": "error",
                "message": message,
                "recording_active": False
            }

    except Exception as e:
        print(f"❌ Error en /start_recording: {e}")
        raise HTTPException(status_code=500, detail=f"Error iniciando grabación: {str(e)}")


async def process_audio_background(audio_path: str):
    """Procesar audio en background: STT + IA + TTS + envío a VR - MEJORADO"""
    global conversation_history, conversation_state, latest_ai_response, price_tracker

    try:
        print("🔄 Transcribiendo audio...")
        transcription = await asyncio.to_thread(transcribe_audio_file, audio_path)

        try:
            os.unlink(audio_path)
        except Exception:
            pass

        if not transcription or len(transcription.strip()) < 2:
            print("⚠️ No se detectó texto claro")
            return

        print(f"📝 TRANSCRIPCIÓN FINAL: {transcription}")

        user_text = sanitize_text(transcription)
        print(f"💬 Usuario: {user_text}")
        print("🤖 IA procesando...")

        # NUEVO: Detectar y registrar productos del vendedor
        product_price = extract_price_and_product(user_text)
        if product_price:
            product_name, price = product_price
            price_tracker.add_product(product_name, 1, price)
            print(f"📦 Producto registrado: {product_name} × 1 @ {price} COP")

        # NUEVO: Pasar price_tracker a ollama_generate
        ai_response, new_state = await asyncio.to_thread(
            ollama_generate,
            conversation_history,
            user_text,
            conversation_state,
            price_tracker
        )

        conversation_state = new_state

        if ai_response:
            conversation_history.extend([
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": ai_response}
            ])
            conversation_history = trim_history(conversation_history)

            print(f"🤖 Cliente: {ai_response}")

            try:
                asyncio.create_task(asyncio.to_thread(speak_text, ai_response))
                print("✅ TTS iniciado")
            except Exception as tts_e:
                print(f"⚠️ Error en TTS: {tts_e}")

            conversation_finished = conversation_state == STATE_FINISHED

            latest_ai_response = {
                "response": ai_response,
                "state": conversation_state,
                "conversation_finished": conversation_finished,
                "has_response": True
            }
            print("✅ Respuesta guardada para polling")

            await send_text_to_vr_async(ai_response, conversation_finished, conversation_state)

    except Exception as e:
        print(f"❌ Error procesando audio en background: {e}")


@app.post("/stop_recording", summary="Detener grabación y procesar por botón VR")
async def stop_recording(background_tasks: BackgroundTasks):
    try:
        success, message, audio_path = stop_manual_recording()

        if not success:
            return {
                "status": "error",
                "message": message,
                "recording_active": False
            }

        if not audio_path:
            return {
                "status": "warning",
                "message": "No hay audio para procesar",
                "recording_active": False
            }

        background_tasks.add_task(process_audio_background, audio_path)

        return {
            "status": "processing",
            "message": "Audio en proceso, respuesta será enviada a puerto 8001",
            "recording_active": False
        }

    except Exception as e:
        print(f"❌ Error en /stop_recording: {e}")
        raise HTTPException(status_code=500, detail=f"Error procesando grabación: {str(e)}")


@app.get("/recording_status", summary="Estado actual de grabación")
async def recording_status():
    try:
        return {
            "recording_active": is_recording_active,
            "microphone_id": selected_microphone_id,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "conversation_state": conversation_state
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado: {str(e)}")


@app.get("/get_latest_response", summary="Obtener última respuesta (polling para Unreal)")
async def get_latest_response_polling():
    global latest_ai_response

    try:
        if latest_ai_response["has_response"]:
            response = latest_ai_response.copy()
            latest_ai_response["has_response"] = False
            print(f"📡 Unreal consultó respuesta: {response['response'][:50]}...")
            return response

        return {
            "response": "",
            "state": "waiting",
            "conversation_finished": False,
            "has_response": False
        }
    except Exception as e:
        print(f"❌ Error en /get_latest_response: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo respuesta: {str(e)}")


# =========================
# FUNCIÓN CALLBACK PARA PROCESAMIENTO DE VOZ
# =========================
def callback_function(text):
    """Procesar transcripción del usuario y generar respuesta de IA - MEJORADO"""
    print(f"\n💬 Usuario: {text}")

    if not text or len(text.strip()) < 2:
        print("⚠️ Texto muy corto, omitiendo")
        return

    global conversation_history, conversation_state, vr_response_queue, price_tracker

    try:
        user_text = sanitize_text(text)
        if user_text:
            # NUEVO: Detectar y registrar productos del vendedor
            product_price = extract_price_and_product(user_text)
            if product_price:
                product_name, price = product_price
                price_tracker.add_product(product_name, 1, price)
                print(f"📦 Producto registrado: {product_name} × 1 @ {price} COP")

            print("🤖 IA procesando...")
            # NUEVO: Pasar price_tracker a ollama_generate
            ai_response, new_state = ollama_generate(
                conversation_history,
                user_text,
                conversation_state,
                price_tracker=price_tracker
            )

            conversation_history.extend([
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": ai_response}
            ])
            conversation_state = new_state

            print(f"🤖 Cliente: {ai_response}")

            vr_response_queue.put(ai_response)
            print(f"✅ Respuesta enviada a VR via queue")

            speak_text(ai_response)
            print("✅ Respuesta completa\n")

    except Exception as e:
        print(f"❌ Error procesando: {e}")

    print("🎙️ Presiona ENTER para siguiente grabación...")


# =========================
# FUNCIÓN DE INICIO
# =========================
def start_api_server(host: str = "127.0.0.1", port: int = 8000):
    global selected_microphone_id

    print("=" * 70)
    print("🌐 INICIANDO API SERVER CON SERVICIOS DE VOZ INTEGRADOS")
    print("=" * 70)

    # Inicializar servicios de audio
    print("🔄 Precargando servicios de audio...")
    init_audio_services()

    # Detectar micrófono VR
    print("🔍 Detectando micrófono de gafas VR...")
    selected_microphone_id = auto_detect_vr_microphone()

    if audio_ready:
        print("✅ Servicios de audio listos")
    else:
        print("⚠️ Servicios de audio no disponibles - Solo modo texto")

    print(f"🔗 URL: http://{host}:{port}")
    print(f"📚 Documentación: http://{host}:{port}/docs")
    print(f"🔄 Swagger UI: http://{host}:{port}/redoc")
    print("=" * 70)
    print("📋 ENDPOINTS DISPONIBLES:")
    print("   GET  /latest_response      - VR consulta última respuesta")
    print("   POST /voice_record        - Grabar voz y procesar")
    print("   POST /start_recording     - VR presiona botón (iniciar grabación)")
    print("   POST /stop_recording      - VR suelta botón (detener grabación)")
    print("   GET  /recording_status    - Estado actual de grabación")
    print("   GET  /get_latest_response - Polling para Unreal")
    print("   GET  /status              - Estado del sistema")
    print("=" * 70)

    print("\n🎮 MODO VR BUTTON CONTROL")
    print("📋 Esperando posts de VR en endpoints:")
    print("   POST /start_recording  <- Botón presionado")
    print("   POST /stop_recording   <- Botón soltado")
    print("🎯 Sin escucha automática - Solo responde a botón VR")
    print("=" * 60)

    print("\n✨ MEJORAS INTEGRADAS:")
    print("   ✅ Validación de totales (detección de estafas)")
    print("   ✅ Eliminación de repeticiones de saludos")
    print("   ✅ Conversación más fluida y natural")
    print("   ✅ Mejor gestión de contexto")
    print("=" * 60)

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="warning",
        access_log=False
    )


if __name__ == "__main__":
    start_api_server()
