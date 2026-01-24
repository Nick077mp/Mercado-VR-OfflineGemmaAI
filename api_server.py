"""
API Server para comunicación con aplicación VR
==============================================
Python maneja el ciclo completo:
1. Grabar audio desde micrófono → STT → IA → TTS
2. Enviar respuesta de IA a VR para lip sync
3. VR solo RECIBE texto para animar avatar

Autor: Nicolás
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

# Importar servicios existentes
from services.ollama_service import (
    ollama_generate,
    sanitize_text,
    trim_history,
    STATE_NEGOTIATING,
    STATE_ADDITIONAL_SHOPPING,
    STATE_ACCEPTED,
    STATE_PAYMENT,
    STATE_FINISHED
)

# Importar servicios de audio como en main.py
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

# Suprimir warnings de sounddevice callback (bug conocido de la librería)
warnings.filterwarnings('ignore', message='.*_CallbackContext.*')
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Suprimir errores de CFFI callback que son benígnos
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

# Configurar CORS para permitir requests desde VR
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En localhost es seguro
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
# VARIABLES GLOBALES (UNA SOLA SESIÓN)
# =========================
conversation_history = []
conversation_state = STATE_NEGOTIATING

# Servicios de audio
tts_ready = False
audio_ready = False
selected_microphone_id = None  # ID del micrófono seleccionado

# Variable THREAD-SAFE para VR usando Queue
vr_response_queue = queue.Queue()

# Variable para polling (alternativa a servidor HTTP en Unreal)
latest_ai_response = {
    "response": "",
    "state": "waiting",
    "conversation_finished": False,
    "has_response": False
}

# =========================
# VARIABLES PARA CONTROL DE BOTÓN VR
# =========================
# Estado de grabación controlada por botón
is_recording_active = False
recording_thread = None
recording_data = []
recording_stream = None

# Configuración para grabación manual
SAMPLE_RATE = 16000
CHANNELS = 1

# Configuración VR - URL donde VR recibe texto (VR en puerto 8001)
VR_RECEIVE_URL = "http://127.0.0.1:8001/chat"  # VR RECIBE aquí en puerto DIFERENTE

async def send_text_to_vr_async(ai_response, conversation_finished=False, state="negotiating"):
    """ENVIAR respuesta de IA a VR para lip sync (versión asíncrona)"""
    try:
        # Formato igual al ChatResponse original
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
        # Esto es normal si Unreal usa polling en lugar de servidor HTTP
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
        
        def audio_callback(indata, frames, time, status):
            if status:
                print(f"⚠️ Audio status: {status}")
            if is_recording_active:
                recording_data.append(indata.copy())
        
        # Crear stream de audio
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
        # Primero marcar como inactivo para detener el callback
        is_recording_active = False
        
        # Dar un momento para que el callback termine
        import time
        time.sleep(0.1)
        
        # Detener y cerrar stream de forma segura
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
        
        # Procesar audio grabado
        print("🔍 DEBUG: Verificando datos de audio...")
        if not recording_data:
            print("❌ DEBUG: No hay datos de audio")
            return False, "No hay datos de audio", None
        
        print(f"🔍 DEBUG: Datos encontrados: {len(recording_data)} chunks")
        
        # Convertir datos de audio a numpy array
        print("🔍 DEBUG: Concatenando audio...")
        audio_data = np.concatenate(recording_data, axis=0)
        duration = len(audio_data) / SAMPLE_RATE
        
        print(f"✅ Audio procesado: {duration:.2f} segundos")
        
        # Guardar audio temporalmente para transcripción
        print("🔍 DEBUG: Creando archivo temporal...")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        temp_path = temp_file.name
        temp_file.close()
        
        # Escribir archivo WAV
        print("🔍 DEBUG: Escribiendo archivo WAV...")
        scipy.io.wavfile.write(temp_path, SAMPLE_RATE, (audio_data * 32767).astype(np.int16))
        print(f"🔍 DEBUG: Archivo guardado en: {temp_path}")
        
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
        if device['max_input_channels'] > 0:  # Solo micrófonos
            name = device['name'].lower()
            print(f"🔍 DEBUG - ID {i}: {device['name']} ({device['max_input_channels']} canales)")
            
            # Detectar VR candidatos
            if any(keyword in name for keyword in ['oculus', 'headset microphone']) and 'virtual audio device' in name:
                vr_candidates.append((i, device['name']))
                print(f"🎧 VR candidato encontrado: ID {i} - {device['name']}")
            
            # Detectar micrófono del sistema (backup)
            elif ('intel' in name and 'smart' in name) or ('varios micrófonos' in name and device['max_input_channels'] >= 4):
                system_mics.append((i, device['name'], device['max_input_channels']))
                print(f"🎤 Sistema encontrado: ID {i} - {device['name']} ({device['max_input_channels']} canales)")
    
    print(f"📊 Resumen: {len(system_mics)} micrófonos sistema, {len(vr_candidates)} VR candidatos")
    
    # 🥇 PRIORIDAD 1: Probar VR para ver si está realmente activo
    for vr_id, vr_name in vr_candidates:
        print(f"🔍 Probando VR real: ID {vr_id} - {vr_name}")
        try:
            # Test muy rápido para ver si el dispositivo responde
            import time
            start_time = time.time()
            test_data = sd.rec(frames=100, samplerate=16000, channels=1, device=vr_id, dtype='float32')
            sd.wait()  # Esperar que termine
            end_time = time.time()
            
            # Si el test se completa rápido y sin error, es VR real
            if end_time - start_time < 1:  # Test exitoso en menos de 1 segundo
                print(f"🎧✅ VR REAL DETECTADO: {vr_name} (ID: {vr_id})")
                print("🏆 Usando VR - PRIORIDAD MÁXIMA")
                return vr_id
            else:
                print(f"🎧⚠️ VR lento/problemático: {vr_name}")
                
        except Exception as e:
            print(f"🎧❌ VR fantasma: {vr_name} - {str(e)[:40]}...")
            continue
    
    # 🥈 PRIORIDAD 2: Si no hay VR real, usar micrófono del sistema
    if system_mics:
        system_mics.sort(key=lambda x: x[2], reverse=True)  # Ordenar por canales
        best_mic = system_mics[0]
        print(f"🎤✅ Usando sistema como fallback: {best_mic[1]} (ID: {best_mic[0]}, {best_mic[2]} canales)")
        return best_mic[0]
    
    # 🥉 PRIORIDAD 3: Fallback final
    print("⚠️ Usando micrófono predeterminado")
    return None

def init_audio_services():
    """Inicializar servicios TTS"""
    global tts_ready, audio_ready
    
    try:
        print("🔄 Inicializando Piper TTS (solo, sin clonación)...")
        load_tts_engine()  # Función que carga Piper
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
        # Verificar que hay audio válido
        if len(audio_data) == 0:
            print("⚠️ Audio vacío, omitiendo transcripción")
            return ""
            
        # Verificar nivel de audio (evitar transcribir silencio)
        audio_level = np.max(np.abs(audio_data))
        if audio_level < 0.001:  # Umbral muy bajo
            print("⚠️ Audio muy silencioso, omitiendo transcripción")
            return ""
        
        # Crear archivo temporal
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_path = temp_file.name
            
            # Convertir numpy a wav
            import scipy.io.wavfile
            scipy.io.wavfile.write(temp_path, sample_rate, (audio_data * 32767).astype(np.int16))
            
            # Transcribir con timeout usando threading
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
            
            # Limpiar archivo temporal
            try:
                os.unlink(temp_path)
            except:
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
            device=selected_microphone_id  # Usar micrófono seleccionado
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
    """Verificar que Ollama esté disponible"""
    try:
        # Hacer una llamada simple a Ollama
        test_response, _ = ollama_generate([], "Hola", STATE_NEGOTIATING)
        return test_response is not None
    except Exception as e:
        print(f"❌ Ollama no disponible: {e}")
        return False

def process_voice_to_response(audio_data):
    """Procesar audio completo: STT → IA → TTS"""
    try:
        global conversation_history, conversation_state
        
        # 1. STT - Speech to Text
        print("🔄 Transcribiendo...")
        transcription = transcribe_audio_numpy(audio_data)
        if not transcription:
            return None, "No se pudo transcribir el audio"
        
        user_text = sanitize_text(transcription)
        print(f"📝 Transcripción: {user_text}")
        
        # 2. IA - Generar respuesta
        print("🤖 IA respondiendo...")
        ai_response, new_state = ollama_generate(
            conversation_history,
            user_text,
            conversation_state
        )
        
        # 3. Actualizar historial
        conversation_history.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": ai_response}
        ])
        conversation_state = new_state
        
        # 4. TTS - Text to Speech
        print("🎵 Generando audio...")
        speak_text(ai_response)  # Función directa, no método
        
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
    """Endpoint básico de salud"""
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
        # Intentar obtener respuesta sin bloquear
        response_text = vr_response_queue.get_nowait()
        print(f"📡 VR RECIBE: {response_text[:30]}...")
        return {
            "response": response_text,
            "state": "negotiating", 
            "conversation_finished": False,
            "conversation_negotiation_cancel": False
        }
    except queue.Empty:
        # No hay respuesta disponible
        return {
            "response": "",
            "state": "waiting", 
            "conversation_finished": False,
            "conversation_negotiation_cancel": False
        }

@app.post("/voice_record", response_model=VoiceResponse, summary="Grabar voz y procesar")
async def voice_record(request: VoiceRecordRequest):
    """
    Grabar audio desde micrófono, transcribir con STT, procesar con IA, y generar TTS
    
    Args:
        request: VoiceRecordRequest con duración de grabación
    
    Returns:
        VoiceResponse con transcripción, respuesta de IA, y estado
    """
    if not audio_ready:
        raise HTTPException(status_code=503, detail="Servicios de audio no disponibles")
    
    try:
        # Grabar audio
        audio_data = record_audio(duration=request.duration_seconds)
        if audio_data is None:
            raise HTTPException(status_code=500, detail="Error grabando audio")
        
        # Procesar audio completo
        result, error = process_voice_to_response(audio_data)
        if error:
            raise HTTPException(status_code=500, detail=error)
        
        # Determinar si conversación terminada
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
    """
    Obtener estado actual del sistema
    """
    try:
        ollama_ok = await check_ollama_health()
        
        return SystemStatus(
            status="running",
            active_sessions=1,  # Siempre una sesión
            ollama_available=ollama_ok,
            stt_loaded=True,  # STT siempre disponible (es función)
            tts_loaded=tts_ready,
            system_info={
                "mode": "api_server_with_voice",
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
    """
    Endpoint llamado cuando VR presiona el botón del control.
    Inicia la grabación de audio.
    
    Returns:
        JSON con estado de la operación
    """
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
    """Procesar audio en background: STT + IA + TTS + envío a VR"""
    global conversation_history, conversation_state, latest_ai_response
    
    try:
        # Transcribir audio (ejecutar en thread para no bloquear)
        print("🔄 Transcribiendo audio...")
        transcription = await asyncio.to_thread(transcribe_audio_file, audio_path)
        
        # Limpiar archivo temporal
        try:
            os.unlink(audio_path)
        except:
            pass
        
        if not transcription or len(transcription.strip()) < 2:
            print("⚠️ No se detectó texto claro")
            return
        
        print(f"📝 TRANSCRIPCIÓN FINAL: {transcription}")
        
        # Procesar con IA (ejecutar en thread)
        user_text = sanitize_text(transcription)
        print(f"💬 Usuario: {user_text}")
        print("🤖 IA procesando...")
        
        ai_response, new_state = await asyncio.to_thread(
            ollama_generate,
            conversation_history,
            user_text,
            conversation_state
        )
        
        conversation_state = new_state
        
        # Actualizar historial
        if ai_response:
            conversation_history.append({
                "user": user_text,
                "assistant": ai_response
            })
            conversation_history = trim_history(conversation_history)
            
            print(f"🤖 Cliente: {ai_response}")
            
            # Generar TTS en background (fire-and-forget)
            try:
                asyncio.create_task(asyncio.to_thread(speak_text, ai_response))
                print("✅ TTS iniciado")
            except Exception as tts_e:
                print(f"⚠️ Error en TTS: {tts_e}")
            
            conversation_finished = conversation_state == STATE_FINISHED
            
            # Guardar respuesta para polling (Método 1: Unreal consulta)
            latest_ai_response = {
                "response": ai_response,
                "state": conversation_state,
                "conversation_finished": conversation_finished,
                "has_response": True
            }
            print("✅ Respuesta guardada para polling")
            
            # TAMBIÉN intentar enviar a VR puerto 8001 (Método 2: Push)
            # Si Unreal tiene servidor HTTP, recibirá por aquí
            await send_text_to_vr_async(ai_response, conversation_finished, conversation_state)
            
    except Exception as e:
        print(f"❌ Error procesando audio en background: {e}")

@app.post("/stop_recording", summary="Detener grabación y procesar por botón VR")  
async def stop_recording(background_tasks: BackgroundTasks):
    """
    Endpoint llamado cuando VR suelta el botón del control.
    Detiene la grabación y procesa el audio en background.
    
    Returns:
        Status inmediato confirmando que se inició el procesamiento
    """
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
        
        # Procesar audio en background task
        background_tasks.add_task(process_audio_background, audio_path)
        
        # Respuesta INMEDIATA para VR
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
    """
    Endpoint para consultar el estado actual de grabación.
    Útil para debugging o monitoreo.
    
    Returns:
        JSON con estado de grabación
    """
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
    """
    Endpoint para que Unreal consulte la última respuesta de IA mediante polling.
    Alternativa a tener un servidor HTTP en Unreal.
    
    Unreal debe consultar este endpoint cada 0.5 segundos.
    Cuando hay una respuesta nueva, se devuelve y se marca como leída.
    
    Returns:
        JSON con respuesta de IA o vacío si no hay nada nuevo
    """
    global latest_ai_response
    
    try:
        # Si hay respuesta nueva, devolverla
        if latest_ai_response["has_response"]:
            response = latest_ai_response.copy()
            # Marcar como leída
            latest_ai_response["has_response"] = False
            print(f"📡 Unreal consultó respuesta: {response['response'][:50]}...")
            return response
        
        # No hay respuesta nueva
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
    """Procesar transcripción del usuario y generar respuesta de IA"""
    print(f"\n💬 Usuario: {text}")
    
    if not text or len(text.strip()) < 2:
        print("⚠️ Texto muy corto, omitiendo")
        return
    
    global conversation_history, conversation_state, vr_response_queue
    
    try:
        user_text = sanitize_text(text)
        if user_text:
            print("🤖 IA procesando...")
            ai_response, new_state = ollama_generate(
                conversation_history,
                user_text,
                conversation_state
            )
            
            # Actualizar historial
            conversation_history.extend([
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": ai_response}
            ])
            conversation_state = new_state
            
            print(f"🤖 Cliente: {ai_response}")
            
            # THREAD-SAFE: Enviar respuesta a queue
            vr_response_queue.put(ai_response)
            print(f"✅ Respuesta enviada a VR via queue")
            
            # TTS
            speak_text(ai_response)
            print("✅ Respuesta completa\n")
            
    except Exception as e:
        print(f"❌ Error procesando: {e}")
        
    print("🎙️ Presiona ENTER para siguiente grabación...")

# =========================
# FUNCIÓN DE INICIO
# =========================
def start_api_server(host: str = "127.0.0.1", port: int = 8000):
    """
    Iniciar el servidor API con servicios de audio integrados
    
    Args:
        host: Dirección IP del servidor
        port: Puerto del servidor
    """
    print("=" * 70)
    print("🌐 INICIANDO API SERVER CON SERVICIOS DE VOZ INTEGRADOS")
    print("=" * 70)
    
    # Inicializar servicios de audio
    print("🔄 Precargando servicios de audio...")
    init_audio_services()
    
    # Detectar micrófono VR
    print("🔍 Detectando micrófono de gafas VR...")
    mic_device_id = auto_detect_vr_microphone()
    
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
    print("   GET  /status              - Estado del sistema")
    print("=" * 70)
    
    # ✅ MODO VR ENDPOINTS ONLY - Sin escucha automática
    print("\n🎮 MODO VR BUTTON CONTROL")
    print("📋 Esperando posts de VR en endpoints:")
    print("   POST /start_recording  <- Botón presionado")
    print("   POST /stop_recording   <- Botón soltado")
    print("🎯 Sin escucha automática activa")
    print("=" * 60)
    
    # Configurar uvicorn para usar el objeto app EXISTENTE (no reimportarlo)
    uvicorn.run(
        app,  # Usar objeto app directamente, NO string
        host=host,
        port=port,
        reload=False,
        log_level="warning",  # Solo errores importantes
        access_log=False       # No mostrar cada request
    )

if __name__ == "__main__":
    start_api_server()