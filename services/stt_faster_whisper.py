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
import re

# ====================================================================
# CONFIGURACIÓN SMART OFFLINE - DESCARGA AUTOMÁTICA SI ES NECESARIO
# ====================================================================
def is_first_run():
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    # Verificar si algún modelo whisper existe en cache
    return not any(
        os.path.exists(os.path.join(cache_dir, f"models--Systran--faster-whisper-{s}"))
        for s in ["tiny", "base", "small", "medium"]
    )

if not is_first_run():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_DATASETS_OFFLINE'] = '1'

os.environ['TORCH_HOME'] = os.path.expanduser('~/.cache/torch')

# === FORZAR CPU: Liberar VRAM para VR ===
# Ocultar GPUs de PyTorch (Silero VAD, Whisper) para no consumir VRAM
os.environ['CUDA_VISIBLE_DEVICES'] = ''

# =========================
# SUPRESOR DE ERRORES CFFI
# =========================
warnings.filterwarnings("ignore", category=UserWarning, module="cffi")

@contextmanager
def suppress_cffi_errors():
    """Context manager para suprimir errores de CFFI temporalmente"""
    old_stderr = sys.stderr
    try:
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
# CONFIGURACIÓN DE SENSIBILIDAD
# ====================================================================
SPEECH_THRESHOLD = 0.8
DEFAULT_SILENCE_DURATION = 1.2
MIN_RECORDING_TIME = 0.5
MAX_RECORDING_TIME = 30

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

        torch_cache_dir = os.path.expanduser('~/.cache/torch/hub')
        silero_cache_path = os.path.join(torch_cache_dir, 'snakers4_silero-vad_master')

        if os.path.exists(silero_cache_path):
            print("📁 Usando Silero VAD desde cache local...")
            sys.path.insert(0, silero_cache_path)

            try:
                model_path = os.path.join(silero_cache_path, 'files', 'silero_vad.jit')
                if os.path.exists(model_path):
                    SILERO_VAD = torch.jit.load(model_path, map_location='cpu')
                    print("✅ Silero VAD cargado (100% OFFLINE)")
                else:
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
                if silero_cache_path in sys.path:
                    sys.path.remove(silero_cache_path)
        else:
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
            except Exception:
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

        # ============================================================
        # MODELO WHISPER: calidad vs velocidad
        # Opciones: tiny < base < small < medium
        # "small" ofrece buena precisión para español
        # ============================================================
        WHISPER_SIZE = "small"  # <<< CAMBIAR AQUÍ si quieres más velocidad ("base") o calidad ("medium")

        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        model_local_path = os.path.join(
            cache_dir,
            f"models--Systran--faster-whisper-{WHISPER_SIZE}",
            "snapshots"
        )

        # Buscar snapshot existente
        snapshot_path = None
        if os.path.exists(model_local_path):
            snapshots = [d for d in os.listdir(model_local_path) 
                        if os.path.isdir(os.path.join(model_local_path, d))]
            if snapshots:
                snapshot_path = os.path.join(model_local_path, snapshots[0])

        if snapshot_path and os.path.exists(snapshot_path):
            WHISPER_MODEL = WhisperModel(
                snapshot_path,
                device="cpu",
                compute_type="int8"
            )
        else:
            print(f"📥 Primera instalación: Descargando Faster-Whisper {WHISPER_SIZE}...")
            print("⏳ Esto tomará unos minutos, solo una vez...")
            WHISPER_MODEL = WhisperModel(
                WHISPER_SIZE,
                device="cpu",
                compute_type="int8",
                local_files_only=False
            )

        print("✅ Faster-Whisper cargado (100% OFFLINE)")

    except Exception as e:
        print(f"❌ Error cargando Faster-Whisper: {e}")
        WHISPER_MODEL = None


def clean_transcription(text: str) -> str:
    """
    Corrige errores comunes de Whisper en español.
    """
    if not text:
        return text

    result = text

    word_corrections = [
        (r'\bsite\b', 'siete'),
        (r'\bSite\b', 'Siete'),
        (r'\bocho\s+cero\s+cero\s+cero\b', '8000'),
        (r'\bcinco\s+cero\s+cero\s+cero\b', '5000'),
        (r'\btres\s+cero\s+cero\s+cero\b', '3000'),
        (r'\bcuatro\s+cero\s+cero\s+cero\b', '4000'),
        (r'\bseis\s+cero\s+cero\s+cero\b', '6000'),
        (r'\bnueve\s+cero\s+cero\s+cero\b', '9000'),
        (r'\bdiez\s+cero\s+cero\s+cero\b', '10000'),
        (r'\bdos\s+cero\s+cero\s+cero\b', '2000'),
        (r'\buno\s+cero\s+cero\s+cero\b', '1000'),
        (r'\bocho\s+cero\s+cero\b', '800'),
        (r'\bcinco\s+cero\s+cero\b', '500'),
        (r'\btres\s+cero\s+cero\b', '300'),
    ]

    for pattern, replacement in word_corrections:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result.strip()


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
            beam_size=1,
            vad_filter=True
        )

        transcription = " ".join([segment.text for segment in segments])
        transcription = transcription.strip()
        transcription = clean_transcription(transcription)

        return transcription

    except Exception as e:
        print(f"❌ Error transcribiendo: {e}")
        return ""


def _record_basic(callback, device_id=None, silence_duration=3.0):
    """
    Método básico de grabación (fallback cuando Silero VAD no está disponible)
    """
    global WHISPER_MODEL

    load_whisper_model()

    if WHISPER_MODEL is None:
        print("❌ Faster-Whisper no disponible")
        return ""

    audio_queue = queue.Queue()
    is_recording = threading.Event()
    is_recording.set()

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"⚠️ Status: {status}")
        if is_recording.is_set():
            audio_queue.put(indata.copy())

    print("🎙️  GRABANDO... (Habla cuando quieras)")
    print(f"⏹️  La grabación se detendrá automáticamente tras {silence_duration} segundos de silencio\n")

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
    silence_threshold = 500
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
    duration = len(audio_data) / SAMPLE_RATE
    print(f"\n✅ Grabación completada: {duration:.2f} segundos")

    import tempfile
    import soundfile as sf

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_path = temp_file.name
    temp_file.close()

    sf.write(temp_path, audio_data, SAMPLE_RATE)

    print("🔄 Transcribiendo...")
    transcription = transcribe_audio_file(temp_path)

    os.remove(temp_path)

    return transcription


def record_and_transcribe_streaming(callback, device_id=None, silence_duration=DEFAULT_SILENCE_DURATION):
    """
    Graba audio y transcribe en tiempo real usando Silero VAD
    """
    global WHISPER_MODEL, SILERO_VAD

    load_whisper_model()
    load_silero_vad_model()

    if WHISPER_MODEL is None:
        print("❌ Faster-Whisper no disponible")
        return ""

    if SILERO_VAD is None:
        print("⚠️ Silero VAD no disponible, usando detección básica")
        return _record_basic(callback, device_id, silence_duration)

    audio_queue = queue.Queue()
    is_recording = threading.Event()
    is_recording.set()

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"⚠️ Status: {status}")
        if is_recording.is_set():
            audio_queue.put(indata.copy())

    print("🎙️  GRABANDO...")

    with suppress_cffi_errors():
        stream = sd.InputStream(
            device=device_id,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=audio_callback,
            blocksize=512
        )
        stream.start()

    audio_buffer = []
    last_speech_time = None

    speaking_detected = False
    max_recording_time = MAX_RECORDING_TIME
    min_recording_time = MIN_RECORDING_TIME
    speech_threshold = SPEECH_THRESHOLD

    print(f"🎯 Silero VAD: silencio={silence_duration}s tras detectar voz")

    try:
        start_time = time.time()

        while is_recording.is_set():
            if time.time() - start_time > max_recording_time:
                print(f"\n⏱️ Tiempo máximo alcanzado ({max_recording_time}s)")
                is_recording.clear()
                break

            try:
                chunk = audio_queue.get(timeout=0.1)
                audio_buffer.append(chunk)

                audio_float = chunk.astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_float.flatten())

                speech_prob = SILERO_VAD(audio_tensor, SAMPLE_RATE).item()

                if speech_prob > speech_threshold:
                    last_speech_time = time.time()
                    speaking_detected = True
                    print(f"🔊 VOZ DETECTADA: {speech_prob:.2f} | Timer: 0.0s      ", end="\r")
                else:
                    if last_speech_time is None:
                        print(f"⏸️  Esperando voz... (prob: {speech_prob:.2f})      ", end="\r")
                    else:
                        silence_time = time.time() - last_speech_time
                        print(f"🔇 Silencio: {silence_time:.1f}s / {silence_duration}s (prob: {speech_prob:.2f})      ", end="\r")

                if speaking_detected and last_speech_time is not None:
                    recording_duration = time.time() - start_time
                    silence_time = time.time() - last_speech_time

                    if recording_duration > min_recording_time and silence_time >= silence_duration:
                        print(f"\n🔇 Silencio detectado ({silence_time:.1f}s)")
                        is_recording.clear()
                        break

            except queue.Empty:
                if speaking_detected and last_speech_time is not None:
                    silence_time = time.time() - last_speech_time
                    if silence_time >= silence_duration:
                        print(f"\n🔇 Silencio detectado ({silence_time:.1f}s)")
                        is_recording.clear()
                        break

    except KeyboardInterrupt:
        print("\n⏹️  Grabación cancelada")
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

    import tempfile
    import soundfile as sf

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_path = temp_file.name
    temp_file.close()

    sf.write(temp_path, audio_data, SAMPLE_RATE)

    print("🔄 Transcribiendo...")
    transcription = transcribe_audio_file(temp_path)

    os.remove(temp_path)

    return transcription


# ====================================================================
# FUNCIONES DE CONFIGURACIÓN AVANZADA
# ====================================================================
def adjust_sensitivity(sensitivity_level="normal"):
    """
    Ajusta la sensibilidad del micrófono

    sensitivity_level opciones:
    - "low": Para ambientes MUY ruidosos
    - "normal": Para ambientes moderadamente ruidosos
    - "high": Para ambientes silenciosos
    """
    global SPEECH_THRESHOLD

    if sensitivity_level == "low":
        SPEECH_THRESHOLD = 0.85
        print("🔧 Sensibilidad: BAJA (para ambientes muy ruidosos)")
    elif sensitivity_level == "normal":
        SPEECH_THRESHOLD = 0.8
        print("🔧 Sensibilidad: NORMAL (recomendado)")
    elif sensitivity_level == "high":
        SPEECH_THRESHOLD = 0.7
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

    def audio_callback(indata, frames, time_info, status):
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
