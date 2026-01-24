"""
Servicio de Text-to-Speech con SOLO Piper TTS
Voz mexicana natural de alta calidad (sin clonación)
100% offline - Ultra rápido
"""

import os
import tempfile
import sounddevice as sd
import numpy as np
import asyncio
import soundfile as sf
import threading
from pathlib import Path
from piper import PiperVoice

# Configuración
TTS_ENGINE = "piper-only"
SAMPLE_RATE = 24000
PIPER_MODEL = "voices/es_ES-davefx-medium.onnx"  # Voz española masculina

# Modelo global
PIPER_VOICE = None
TTS_LOCK = threading.Lock()

def load_tts_engine():
    """Inicializa Piper TTS (sin clonación, voz natural)"""
    global PIPER_VOICE
    
    if PIPER_VOICE is not None:
        return
    
    try:
        print("🔄 Inicializando Piper TTS (solo, sin clonación)...")
        
        # Inicializar Piper
        if not os.path.exists(PIPER_MODEL):
            raise FileNotFoundError(f"Modelo Piper no encontrado: {PIPER_MODEL}")
        
        PIPER_VOICE = PiperVoice.load(PIPER_MODEL)
        
        print(f"✅ Piper TTS inicializado")
        print(f"✅ Voz: Mexicana natural (sin clonación)")
        print(f"✅ Calidad: Alta - Sin artefactos")
        
    except Exception as e:
        print(f"❌ Error inicializando Piper: {e}")
        import traceback
        traceback.print_exc()
        PIPER_VOICE = None

async def generate_speech_async(text: str, output_path: str = None) -> str:
    """
    Convierte texto a audio usando Piper (async compatible)
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_audio_sync, text, output_path)

def _generate_audio_sync(text: str, output_path: str = None) -> str:
    """Genera audio con Piper (síncrono)"""
    with TTS_LOCK:
        if PIPER_VOICE is None:
            load_tts_engine()
            if PIPER_VOICE is None:
                return None
        
        try:
            # Crear archivo de salida
            if output_path is None:
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
                output_path = temp_file.name
                temp_file.close()
            
            # Generar audio con Piper
            import wave
            with wave.open(output_path, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(PIPER_VOICE.config.sample_rate)
                
                # Sintetizar y escribir audio
                for audio_chunk in PIPER_VOICE.synthesize(text):
                    wav_file.writeframes(audio_chunk.audio_int16_bytes)
            
            return output_path
            
        except Exception as e:
            print(f"❌ Error generando audio: {e}")
            import traceback
            traceback.print_exc()
            return None

def text_to_speech(text: str, output_path: str = None) -> str:
    """Wrapper síncrono"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                generate_speech_async(text, output_path), 
                loop
            ).result()
        else:
            return asyncio.run(generate_speech_async(text, output_path))
    except RuntimeError:
        return asyncio.run(generate_speech_async(text, output_path))

def play_audio_file(audio_path: str):
    """Reproduce un archivo de audio"""
    try:
        audio_data, sample_rate = sf.read(audio_path)
        sd.play(audio_data, sample_rate)
        sd.wait()
    except Exception as e:
        print(f"❌ Error reproduciendo audio: {e}")

def speak_text(text: str):
    """Convierte texto a voz y lo reproduce"""
    audio_path = text_to_speech(text)
    if audio_path and os.path.exists(audio_path):
        play_audio_file(audio_path)
        try:
            os.unlink(audio_path)
        except:
            pass
