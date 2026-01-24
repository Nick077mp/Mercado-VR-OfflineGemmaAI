"""
🎙️ SISTEMA DE ASISTENTE DE VOZ CON IA – 100% OFFLINE
Pipeline:
Micrófono → Faster-Whisper → Ollama (local) → Piper TTS
SIN INTERNET · SIN SERVIDORES · SOLO LOCALHOST
"""

import os
import asyncio
import warnings
import sys
from contextlib import contextmanager
import sounddevice as sd
from typing import AsyncGenerator, Optional

# =========================
# IMPORTS DE SERVICIOS
# =========================
from services.stt_faster_whisper import (
    load_whisper_model,
    record_and_transcribe_streaming
)

from services.ollama_service import (
    ollama_generate,
    sanitize_text,
    trim_history,
    STATE_NEGOTIATING,
    STATE_ACCEPTED,
    STATE_FINISHED
)

from services.tts_piper_only import (
    load_tts_engine,
    generate_speech_async,
    play_audio_file
)

# =========================
# SUPRESOR DE ERRORES CFFI
# =========================
# Suprimir ventanas emergentes de errores CFFI en Windows
warnings.filterwarnings("ignore", category=UserWarning, module="cffi")

# Hook para capturar excepciones ignoradas de CFFI
import threading

# Hook para exceptions ignoradas en callbacks
def ignore_cffi_callback_errors(args):
    """Hook para capturar excepciones ignoradas en callbacks de CFFI"""
    exc_type, exc_value, exc_traceback, thread = args
    
    # Convertir a strings para búsqueda segura
    exc_str = str(exc_value) if exc_value else ""
    tb_str = str(exc_traceback) if exc_traceback else ""
    
    # Detectar errores específicos de CFFI/sounddevice
    is_cffi_error = (
        "_CallbackContext" in exc_str or
        "AttributeError: 'NoneType' object has no attribute 'out'" in exc_str or
        "AttributeError: object has no attribute 'out'" in exc_str or
        "sounddevice" in tb_str or
        "cffi" in exc_str.lower()
    )
    
    if is_cffi_error:
        # Silenciosamente ignorar errores de CFFI
        pass
    else:
        # Para otros errores, mostrar normalmente
        import traceback
        print(f"Error en thread {thread.name}: {exc_type.__name__}: {exc_value}", file=sys.__stderr__)
        if exc_traceback:
            traceback.print_tb(exc_traceback, file=sys.__stderr__)

# Instalar el hook
threading.excepthook = ignore_cffi_callback_errors

@contextmanager
def suppress_cffi_errors():
    """Context manager para suprimir errores de CFFI temporalmente"""
    old_stderr = sys.stderr
    try:
        # Redirigir stderr temporalmente a devnull para CFFI
        import io
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stderr = old_stderr

# =========================
# CONFIGURACIÓN
# =========================
SILENCE_DURATION = 1.2

# =========================
# CONTEXTO GLOBAL
# =========================
conversation_history = []
conversation_state = STATE_NEGOTIATING

# =========================
# UTILIDADES
# =========================
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def list_microphones():
    # Usar el supresor de errores CFFI durante la consulta de dispositivos
    with suppress_cffi_errors():
        devices = sd.query_devices()
    valid = []

    print("\n🎙 MICRÓFONOS DISPONIBLES:\n")
    print(f"{'ID':<5} {'Nombre':<60} {'Canales'}")
    print("-" * 80)

    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            print(f"{i:<5} {d['name']:<60} {d['max_input_channels']}")
            valid.append(i)

    print("-" * 80)
    return valid

def select_microphone(valid_ids):
    while True:
        choice = input("📌 ID del micrófono (o 'auto'): ").strip()
        if choice.lower() == "auto":
            print("✅ Usando micrófono por defecto")
            return None
        if choice.isdigit() and int(choice) in valid_ids:
            return int(choice)
        print("❌ ID inválido")

def clean_text_for_tts(text: str) -> str:
    # Mantén esto simple para no destruir el español
    for c in "()[]{}*_\"":
        text = text.replace(c, "")
    return " ".join(text.split()).strip()

def _is_sentence_end(text: str, idx: int) -> bool:
    """
    True si en text[idx] hay fin de frase, evitando cortar números 3.000.
    """
    ch = text[idx]
    if ch in "?!":
        return True
    if ch == ".":
        prev_c = text[idx - 1] if idx > 0 else ""
        next_c = text[idx + 1] if idx + 1 < len(text) else ""
        # Evitar 3.000 / 10.000
        if prev_c.isdigit() and next_c.isdigit():
            return False
        return True
    return False

def split_for_tts_stream(buffer: str) -> (Optional[str], str):
    """
    Devuelve (frase_lista, resto_buffer).
    - Corta por fin de frase ? ! .
    - Si no hay, corta por longitud en espacio (suave).
    """
    if not buffer:
        return None, buffer

    # Buscar primer fin de frase
    for i in range(len(buffer)):
        if _is_sentence_end(buffer, i):
            cut_pos = i + 1
            phrase = buffer[:cut_pos].strip()
            rest = buffer[cut_pos:].strip()
            return phrase, rest

    # Corte suave por longitud si crece mucho
    if len(buffer) > 140:
        cut_pos = buffer.rfind(" ")
        if cut_pos > 0:
            phrase = buffer[:cut_pos].strip()
            rest = buffer[cut_pos:].strip()
            return phrase, rest

    return None, buffer

# =========================
# STT
# =========================
def record_with_stt(device_id):
    print("\n🎙️ GRABANDO (habla)...\n")
    last_len = 0

    def callback(text, is_final):
        nonlocal last_len
        if is_final:
            print(f"\r💬 {text}{' ' * 20}")
            last_len = 0
        else:
            if len(text) > last_len:
                print(f"\r💬 {text}", end="", flush=True)
                last_len = len(text)

    # Usar el supresor de errores CFFI durante la grabación
    with suppress_cffi_errors():
        transcript = record_and_transcribe_streaming(
            callback=callback,
            device_id=device_id,
            silence_duration=SILENCE_DURATION
        )

    print("\n\n📝 TRANSCRIPCIÓN FINAL:")
    print(transcript)
    return transcript

# =========================
# STREAMING LLM (SIMULADO, PERO LIMPIO)
# =========================
async def stream_llm_words(transcript: str) -> AsyncGenerator[str, None]:
    """
    Mantiene tu estilo (goteo por palabras), pero:
    - no emite errores crudos como voz
    - aplica sanitize/trim consistentemente
    """
    global conversation_history
    global conversation_state

    clean_transcript = sanitize_text(transcript)
    if not clean_transcript:
        return

    try:
        response, new_state = ollama_generate(
            conversation_history,
            clean_transcript,
            conversation_state
        )
        conversation_state = new_state

        if response is None:
            return

        clean_response = sanitize_text(response)

        # Guardar historial SOLO si hay respuesta válida
        conversation_history.append({
            "user": clean_transcript,
            "assistant": clean_response
        })
        conversation_history = trim_history(conversation_history)

        for word in clean_response.split():
            yield word + " "
            await asyncio.sleep(0.03)

    except Exception:
        # Error “humano” corto, sin detalle técnico
        msg = "Disculpe, tuve un problema. ¿Me lo repite por favor?"
        for word in msg.split():
            yield word + " "
            await asyncio.sleep(0.03)

# =========================
# LLM + TTS STREAMING
# =========================
async def process_with_simple_tts(transcript: str):
    buffer = ""
    first_audio = False

    print("\n🤖 IA respondiendo:\n")

    async for chunk in stream_llm_words(transcript):
        buffer += chunk
        print(chunk, end="", flush=True)

        phrase, buffer = split_for_tts_stream(buffer)
        if phrase:
            phrase = clean_text_for_tts(phrase)
            if phrase:
                if not first_audio:
                    print("\n🎵 TTS streaming iniciado...\n")
                    first_audio = True

                audio_path = await generate_speech_async(phrase)
                if audio_path:
                    await asyncio.get_event_loop().run_in_executor(
                        None, play_audio_file, audio_path
                    )
                    try:
                        os.unlink(audio_path)
                    except Exception:
                        pass

    # 🔈 Último fragmento
    tail = clean_text_for_tts(buffer)
    if tail:
        audio_path = await generate_speech_async(tail)
        if audio_path:
            await asyncio.get_event_loop().run_in_executor(
                None, play_audio_file, audio_path
            )
            try:
                os.unlink(audio_path)
            except Exception:
                pass

    print("\n\n✅ Respuesta completa\n")

# =========================
# MAIN ASYNC (evita asyncio.run repetido)
# =========================
async def main_async():
    global conversation_state

    clear_screen()
    print("=" * 70)
    print("🎙️ SISTEMA DE ASISTENTE DE VOZ CON IA - 100% OFFLINE")
    print("=" * 70)

    print("\n🔄 Precargando modelos...\n")
    load_whisper_model()
    load_tts_engine()

    print("✅ Faster-Whisper cargado")
    print("✅ Piper TTS cargado")
    print("✅ Ollama debe estar ejecutándose (ollama serve)")
    print("\n" + "=" * 70)

    valid_mics = list_microphones()
    device_id = select_microphone(valid_mics)

    while True:
        try:
            input("\n▶️ Presiona ENTER para grabar (Ctrl+C para salir)")
            transcript = record_with_stt(device_id)

            if transcript and transcript.strip():
                await process_with_simple_tts(transcript)

            if conversation_state == STATE_FINISHED:
                print("\n👋 Conversación finalizada correctamente\n")
                break

            again = input("\n¿Otra grabación? (s/n): ").lower().strip()
            if again != "s":
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")

    print("\n👋 Sistema finalizado")

def main():
    asyncio.run(main_async())

# =========================
# ENTRYPOINT
# =========================
if __name__ == "__main__":
    main()

