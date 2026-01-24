"""
Servicio STT usando Faster-Whisper con Silero VAD
4-5x más rápido que Whisper original - 100% offline
"""

import os
import warnings
import sys
from contextlib import contextmanager
import sounddevice as sd
import numpy as np
import queue
import threading
import time
import torch
from faster_whisper import WhisperModel

# ====================================================================
# CONFIGURACIÓN SMART OFFLINE - DESCARGA AUTOMÁTICA SI ES NECESARIO
# ====================================================================
# Solo fuerza offline si los modelos ya están descargados
def is_first_run():
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    return not os.path.exists(os.path.join(cache_dir, "models--Systran--faster-whisper-small"))

if not is_first_run():
    os.environ['HF_HUB_OFFLINE'] = '1'           # Hugging Face Hub offline
    os.environ['TRANSFORMERS_OFFLINE'] = '1'     # Transformers offline  
    os.environ['HF_DATASETS_OFFLINE'] = '1'      # Datasets offline

os.environ['TORCH_HOME'] = os.path.expanduser('~/.cache/torch')  # Cache local de PyTorch
# ====================================================================

# =========================
# SUPRESOR DE ERRORES CFFI
# =========================
# Suprimir ventanas emergentes de errores CFFI en Windows
warnings.filterwarnings("ignore", category=UserWarning, module="cffi")

@contextmanager
def suppress_cffi_errors():
    """Context manager para suprimir errores de CFFI temporalmente"""
    old_stderr = sys.stderr
    try:
        # Redirigir stderr temporalmente para CFFI
        import io
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stderr = old_stderr

# Configuración
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

# ====================================================================
# CONFIGURACIÓN DE SENSIBILIDAD - AJUSTA ESTOS VALORES
# ====================================================================
# Threshold para detectar voz (0.0-1.0): Más alto = menos sensible al ruido
SPEECH_THRESHOLD = 0.8  # Valor recomendado para ambientes ruidosos: 0.75-0.85

# Duración de silencio después de detectar voz para parar (segundos)
DEFAULT_SILENCE_DURATION = 1.2  # Era 2.0, ahora más rápido

# Configuraciones adicionales para filtrar ruido
MIN_RECORDING_TIME = 0.5  # Tiempo mínimo de grabación
MAX_RECORDING_TIME = 30   # Tiempo máximo de grabación
# ====================================================================

# Modelos globales
WHISPER_MODEL = None
SILERO_VAD = None

def load_silero_vad_model():
    """Carga Silero VAD para detección precisa de voz (100% OFFLINE)"""
    global SILERO_VAD
    
    if SILERO_VAD is not None:
        return
    
    try:
        print("🔄 Cargando Silero VAD (detección de voz)...")
        
        # Verificar si ya está en cache local
        torch_cache_dir = os.path.expanduser('~/.cache/torch/hub')
        silero_cache_path = os.path.join(torch_cache_dir, 'snakers4_silero-vad_master')
        
        if os.path.exists(silero_cache_path):
            # Cargar desde cache local (100% offline)
            print("📁 Usando Silero VAD desde cache local...")
            
            # Cargar directamente desde el directorio local
            import sys
            sys.path.insert(0, silero_cache_path)
            
            try:
                # Importar y cargar el modelo directamente
                import torch
                model_path = os.path.join(silero_cache_path, 'files', 'silero_vad.jit')
                if os.path.exists(model_path):
                    SILERO_VAD = torch.jit.load(model_path, map_location='cpu')
                    print("✅ Silero VAD cargado (100% OFFLINE)")
                else:
                    # Fallback: usar torch.hub con source='local'
                    model, utils = torch.hub.load(
                        repo_or_dir=silero_cache_path,
                        model='silero_vad',
                        source='local',
                        force_reload=False,
                        verbose=False
                    )
                    SILERO_VAD = model
                    print("✅ Silero VAD cargado desde cache (100% OFFLINE)")
                    
            finally:
                # Limpiar sys.path
                if silero_cache_path in sys.path:
                    sys.path.remove(silero_cache_path)
                    
        else:
            # Primera vez: descargar Silero VAD
            try:
                print("📥 Primera instalación: Descargando Silero VAD...")
                model, utils = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    force_reload=False,
                    onnx=False
                )
                SILERO_VAD = model
                print("✅ Silero VAD descargado y cacheado")
            except:
                print("⚠️ Error descargando Silero VAD, continuando sin él")
                SILERO_VAD = None
            
    except Exception as e:
        print(f"⚠️ Error cargando Silero VAD: {e}")
        print("⚠️ Continuando sin detección avanzada de voz")
        SILERO_VAD = None

def load_whisper_model():
    """Carga el modelo Faster-Whisper desde cache local (100% OFFLINE)"""
    global WHISPER_MODEL
    
    if WHISPER_MODEL is not None:
        return
    
    try:
        print("🔄 Cargando Faster-Whisper...")
        
        # RUTA LOCAL del modelo (100% OFFLINE - Sin conexión a internet)
        import os
        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        model_local_path = os.path.join(
            cache_dir, 
            "models--Systran--faster-whisper-small",
            "snapshots",
            "536b0662742c02347bc0e980a01041f333bce120"
        )
        
        # Verificar que el modelo existe localmente
        if not os.path.exists(model_local_path):
            print("📥 Primera instalación: Descargando Faster-Whisper...")
            print("⏳ Esto tomará unos minutos, solo una vez...")
            # Primera instalación: descargar modelo automáticamente
            WHISPER_MODEL = WhisperModel(
                "small",
                device="cpu", 
                compute_type="int8",
                local_files_only=False  # Permite descarga automática
            )
        else:
            # Usar ruta local completa (recomendado)
            WHISPER_MODEL = WhisperModel(
                model_local_path,
                device="cpu",
                compute_type="int8"
            )
        
        print("✅ Faster-Whisper cargado (100% OFFLINE)")
        
    except Exception as e:
        print(f"❌ Error cargando Faster-Whisper: {e}")
        WHISPER_MODEL = None

def transcribe_audio_file(audio_path: str) -> str:
    """Transcribe un archivo de audio"""
    global WHISPER_MODEL
    
    load_whisper_model()
    
    if WHISPER_MODEL is None:
        return ""
    
    try:
        segments, info = WHISPER_MODEL.transcribe(
            audio_path,
            language="es",
            beam_size=1,  # Beam 1 = 2x más rápido (era 5)
            vad_filter=True  # Voice Activity Detection para mejor precisión
        )
        
        # Concatenar todos los segmentos
        transcription = " ".join([segment.text for segment in segments])
        return transcription.strip()
        
    except Exception as e:
        print(f"❌ Error transcribiendo: {e}")
        return ""

def _record_basic(callback, device_id=None, silence_duration=3.0):
    """
    Método básico de grabación (fallback cuando Silero VAD no está disponible)
    Usa detección de silencio por energía/volumen
    """
    global WHISPER_MODEL
    
    load_whisper_model()
    
    if WHISPER_MODEL is None:
        print("❌ Faster-Whisper no disponible")
        return ""
    
    audio_queue = queue.Queue()
    is_recording = threading.Event()
    is_recording.set()
    
    def audio_callback(indata, frames, time, status):
        """Callback para capturar audio"""
        if status:
            print(f"⚠️ Status: {status}")
        if is_recording.is_set():
            audio_queue.put(indata.copy())
    
    print("🎙️  GRABANDO... (Habla cuando quieras)")
    print(f"⏹️  La grabación se detendrá automáticamente tras {silence_duration} segundos de silencio\n")
    
    # Usar supresor de errores CFFI para el stream
    with suppress_cffi_errors():
        stream = sd.InputStream(
            device=device_id,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=audio_callback
        )
        
        stream.start()
    
    audio_buffer = []
    last_sound_time = time.time()
    silence_threshold = 500  # Umbral de energía para detectar silencio
    max_recording_time = 30
    min_recording_time = 0.5
    speaking_detected = False
    
    try:
        start_time = time.time()
        
        while is_recording.is_set():
            if time.time() - start_time > max_recording_time:
                print(f"\n⏱️ Tiempo máximo alcanzado ({max_recording_time}s)")
                break
            
            try:
                chunk = audio_queue.get(timeout=0.1)
                audio_buffer.append(chunk)
                
                # Calcular energía del chunk
                energy = np.abs(chunk).mean()
                
                if energy > silence_threshold:
                    last_sound_time = time.time()
                    if not speaking_detected:
                        print("🗣️  Voz detectada, grabando...")
                        speaking_detected = True
                else:
                    if speaking_detected:
                        silence_time = time.time() - last_sound_time
                        silence_percent = min(100, int((silence_time / silence_duration) * 100))
                        print(f"🔇 Silencio: {silence_percent}%", end="\r")
                
                # Verificar si terminamos
                if speaking_detected:
                    recording_duration = time.time() - start_time
                    silence_time = time.time() - last_sound_time
                    
                    if recording_duration > min_recording_time and silence_time >= silence_duration:
                        print(f"\n🔇 Silencio detectado, finalizando grabación...")
                        break
                        
            except queue.Empty:
                continue
                    
    except KeyboardInterrupt:
        print("\n⏹️  Grabación cancelada")
    finally:
        is_recording.clear()
        time.sleep(0.1)
        # Usar supresor para evitar errores al cerrar el stream
        with suppress_cffi_errors():
            stream.stop()
            stream.close()
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except:
                break
    
    if not audio_buffer:
        return ""
    
    audio_data = np.concatenate(audio_buffer, axis=0)
    duration = len(audio_data) / SAMPLE_RATE
    print(f"\n✅ Grabación completada: {duration:.2f} segundos")
    
    # Guardar y transcribir
    import tempfile
    import soundfile as sf
    
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_path = temp_file.name
    temp_file.close()
    
    sf.write(temp_path, audio_data, SAMPLE_RATE)
    
    print("🔄 Transcribiendo...")
    transcription = transcribe_audio_file(temp_path)
    
    import os
    os.remove(temp_path)
    
    return transcription

def record_and_transcribe_streaming(callback, device_id=None, silence_duration=DEFAULT_SILENCE_DURATION):
    """
    Graba audio y transcribe en tiempo real usando Silero VAD
    
    Args:
        callback: Función a llamar con transcripción parcial
        device_id: ID del dispositivo de audio (None = predeterminado)
        silence_duration: Segundos de silencio para detener
    """
    global WHISPER_MODEL, SILERO_VAD
    
    load_whisper_model()
    load_silero_vad_model()
    
    if WHISPER_MODEL is None:
        print("❌ Faster-Whisper no disponible")
        return ""
    
    if SILERO_VAD is None:
        print("⚠️ Silero VAD no disponible, usando detección básica")
        # Fallback a detección básica si falla VAD
        return _record_basic(callback, device_id, silence_duration)
    
    # ⚠️ CRÍTICO: Crear queue y event FRESCOS cada vez
    audio_queue = queue.Queue()
    is_recording = threading.Event()
    is_recording.set()  # Activar grabación
    
    def audio_callback(indata, frames, time, status):
        """Callback para capturar audio"""
        if status:
            print(f"⚠️ Status: {status}")
        if is_recording.is_set():
            audio_queue.put(indata.copy())
    
    # Iniciar grabación
    print("🎙️  GRABANDO...")
    
    # Usar supresor de errores CFFI para el stream
    with suppress_cffi_errors():
        stream = sd.InputStream(
            device=device_id,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=audio_callback,
            blocksize=512  # Silero VAD requiere 512 samples para 16kHz
        )
        
        stream.start()
    
    # Buffer para acumular audio
    audio_buffer = []
    last_speech_time = None  # Última vez que se detectó voz
    
    # Silero VAD - Detección de voz con IA
    speaking_detected = False
    max_recording_time = MAX_RECORDING_TIME
    min_recording_time = MIN_RECORDING_TIME
    speech_threshold = SPEECH_THRESHOLD  # Usar configuración global
    
    print(f"🎯 Silero VAD: silencio={silence_duration}s tras detectar voz")
    
    try:
        start_time = time.time()
        
        while is_recording.is_set():
            # Timeout de seguridad
            if time.time() - start_time > max_recording_time:
                print(f"\n⏱️ Tiempo máximo alcanzado ({max_recording_time}s)")
                is_recording.clear()
                break
                
            try:
                # Obtener audio del queue
                chunk = audio_queue.get(timeout=0.1)
                audio_buffer.append(chunk)
                
                # Convertir chunk a tensor para Silero VAD
                audio_float = chunk.astype(np.float32) / 32768.0  # int16 → float32 [-1, 1]
                audio_tensor = torch.from_numpy(audio_float.flatten())
                
                # Silero VAD: obtener probabilidad de voz (0.0 - 1.0)
                speech_prob = SILERO_VAD(audio_tensor, SAMPLE_RATE).item()
                
                # Umbral configurable para filtrar ruido
                if speech_prob > speech_threshold:
                    last_speech_time = time.time()
                    speaking_detected = True
                    print(f"🔊 VOZ DETECTADA: {speech_prob:.2f} | Timer: 0.0s      ", end="\r")
                else:
                    # No hay voz
                    if last_speech_time is None:
                        print(f"⏸️  Esperando voz... (prob: {speech_prob:.2f})      ", end="\r")
                    else:
                        silence_time = time.time() - last_speech_time
                        print(f"🔇 Silencio: {silence_time:.1f}s / {silence_duration}s (prob: {speech_prob:.2f})      ", end="\r")
                
                # Verificar si terminamos
                if speaking_detected and last_speech_time is not None:
                    recording_duration = time.time() - start_time
                    silence_time = time.time() - last_speech_time
                    
                    if recording_duration > min_recording_time and silence_time >= silence_duration:
                        print(f"\n🔇 Silencio detectado ({silence_time:.1f}s)")
                        is_recording.clear()
                        break
                    
            except queue.Empty:
                # Queue vacío: verificar si terminamos por silencio
                if speaking_detected and last_speech_time is not None:
                    silence_time = time.time() - last_speech_time
                    if silence_time >= silence_duration:
                        print(f"\n🔇 Silencio detectado ({silence_time:.1f}s)")
                        is_recording.clear()
                        break
                # Continuar esperando más audio
                pass
                    
    except KeyboardInterrupt:
        print("\n⏹️  Grabación cancelada")
        is_recording.clear()
    finally:
        # ⚠️ CRÍTICO: Cerrar stream completamente
        is_recording.clear()  # Detener callback
        time.sleep(0.1)  # Dar tiempo a que termine el callback
        # Usar supresor para evitar errores al cerrar el stream
        with suppress_cffi_errors():
            stream.stop()
            stream.close()
        # Vaciar queue residual
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except:
                break
    
    # Convertir buffer a numpy array
    if not audio_buffer:
        return ""
    
    audio_data = np.concatenate(audio_buffer, axis=0)
    
    # Guardar temporalmente para transcribir
    import tempfile
    import soundfile as sf
    
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_path = temp_file.name
    temp_file.close()
    
    sf.write(temp_path, audio_data, SAMPLE_RATE)
    
    # Transcribir
    print("🔄 Transcribiendo...")
    transcription = transcribe_audio_file(temp_path)
    
    # Limpiar
    import os
    os.remove(temp_path)
    
    return transcription

# ====================================================================
# FUNCIONES DE CONFIGURACIÓN AVANZADA
# ====================================================================
def adjust_sensitivity(sensitivity_level="normal"):
    """
    Ajusta la sensibilidad del micrófono
    
    sensitivity_level opciones:
    - "low": Para ambientes MUY ruidosos (oficinas, calles)
    - "normal": Para ambientes moderadamente ruidosos  
    - "high": Para ambientes silenciosos (casa, oficina callada)
    """
    global SPEECH_THRESHOLD
    
    if sensitivity_level == "low":
        SPEECH_THRESHOLD = 0.85  # Muy poco sensible
        print("🔧 Sensibilidad: BAJA (para ambientes muy ruidosos)")
    elif sensitivity_level == "normal":
        SPEECH_THRESHOLD = 0.8   # Sensibilidad normal
        print("🔧 Sensibilidad: NORMAL (recomendado)")
    elif sensitivity_level == "high":
        SPEECH_THRESHOLD = 0.7   # Más sensible
        print("🔧 Sensibilidad: ALTA (para ambientes silenciosos)")
    else:
        print("❌ Opciones: 'low', 'normal', 'high'")

def get_sensitivity_info():
    """Muestra información actual de sensibilidad"""
    print(f"🎛️ Configuración actual:")
    print(f"   - Threshold de voz: {SPEECH_THRESHOLD}")
    print(f"   - Silencio para parar: {DEFAULT_SILENCE_DURATION}s")
    
    if SPEECH_THRESHOLD >= 0.85:
        level = "BAJA (poco sensible)"
    elif SPEECH_THRESHOLD >= 0.8:
        level = "NORMAL"
    else:
        level = "ALTA (muy sensible)"
    
    print(f"   - Nivel: {level}")

def test_microphone_sensitivity(device_id=None):
    """Prueba el micrófono para verificar la sensibilidad"""
    print("🎤 Prueba de sensibilidad del micrófono...")
    print("   Habla durante 5 segundos y verás los niveles de detección")
    
    load_silero_vad_model()
    if SILERO_VAD is None:
        print("❌ No se puede probar sin Silero VAD")
        return
    
    audio_queue = queue.Queue()
    is_recording = threading.Event()
    is_recording.set()
    
    def audio_callback(indata, frames, time, status):
        if is_recording.is_set():
            audio_queue.put(indata.copy())
    
    stream = sd.InputStream(
        device=device_id,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=audio_callback
    )
    
    stream.start()
    print(f"\n🎯 Threshold actual: {SPEECH_THRESHOLD}")
    print("🔊 = Voz detectada | 🔇 = Silencio/ruido")
    
    start_time = time.time()
    try:
        while time.time() - start_time < 5.0:
            try:
                chunk = audio_queue.get(timeout=0.1)
                audio_float = chunk.astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_float.flatten())
                speech_prob = SILERO_VAD(audio_tensor, SAMPLE_RATE).item()
                
                if speech_prob > SPEECH_THRESHOLD:
                    print(f"🔊 VOZ: {speech_prob:.3f}", end="  ")
                else:
                    print(f"🔇 {speech_prob:.3f}", end="  ")
                    
            except queue.Empty:
                print(".", end="")
                
    except KeyboardInterrupt:
        pass
    finally:
        is_recording.clear()
        stream.stop()
        stream.close()
    
    print(f"\n✅ Prueba completada")
    print(f"ℹ️  Si escuchas mucho '🔇' cuando hablas, usa: adjust_sensitivity('high')")
    print(f"ℹ️  Si escuchas mucho '🔊' con ruido de fondo, usa: adjust_sensitivity('low')")

if __name__ == "__main__":
    print("🎤 Probando Faster-Whisper...")
    load_whisper_model()
    
    print("\n▶️  Presiona Ctrl+C para detener")
    print("🎙️  Habla ahora...\n")
    
    def print_callback(text):
        print(f"💬 {text}")
    
    text = record_and_transcribe_streaming(print_callback)
    print(f"\n✅ Transcripción final: {text}")
