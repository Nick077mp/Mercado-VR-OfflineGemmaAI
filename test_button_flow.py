"""Prueba de flujo completo VR botón -> STT -> IA (Ollama customer) -> TTS.

Uso:
  1. En una terminal, ejecutar:  python main.py --api-server
  2. En otra terminal, ejecutar: python test_button_flow.py

El script:
  - Verifica que el servidor esté corriendo (/status)
  - Verifica que Ollama esté disponible
  - Simula presionar botón VR (/start_recording)
  - Te da tiempo para hablar
  - Simula soltar botón VR (/stop_recording)
  - Hace polling hasta recibir respuesta de IA
  - Muestra transcripción + respuesta + estado de conversación

Puedes repetir el ciclo para simular una conversación completa.
"""

import time
import requests
import sys

BASE_URL = "http://127.0.0.1:8000"


def print_section(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def print_fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def print_info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


def check_server_running() -> bool:
    """Verificar que el servidor esté corriendo"""
    print_section("PASO 1: Verificando servidor")
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=3)
        if resp.status_code == 200:
            print_ok("Servidor corriendo correctamente")
            return True
        else:
            print_fail(f"Servidor respondió con código {resp.status_code}")
            return False
    except requests.ConnectionError:
        print_fail("No se puede conectar al servidor")
        print_info("Ejecuta primero: python main.py --api-server")
        return False
    except Exception as e:
        print_fail(f"Error: {e}")
        return False


def check_status() -> bool:
    """Verificar estado completo del sistema"""
    print_section("PASO 2: Verificando estado del sistema")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        ollama_ok = data.get("ollama_available", False)
        tts_ok = data.get("tts_loaded", False)
        stt_ok = data.get("stt_loaded", False)

        print(f"  📊 Estado general: {data.get('status', '?')}")
        print(f"  🤖 Ollama:  {'✅ Disponible' if ollama_ok else '❌ NO disponible'}")
        print(f"  🎙️ STT:     {'✅ Cargado' if stt_ok else '❌ NO cargado'}")
        print(f"  🔊 TTS:     {'✅ Cargado' if tts_ok else '❌ NO cargado'}")
        print(f"  📋 Info:    {data.get('system_info', {})}")

        if not ollama_ok:
            print_fail("Ollama no está disponible. Verifica que esté corriendo: ollama serve")
            return False

        return True

    except Exception as e:
        print_fail(f"Error consultando /status: {e}")
        return False


def check_recording_status() -> dict:
    """Verificar estado de grabación"""
    try:
        resp = requests.get(f"{BASE_URL}/recording_status", timeout=3)
        return resp.json()
    except Exception:
        return {}


def start_recording() -> bool:
    """Simular presionar botón VR"""
    print_section("PASO 3: Presionando botón VR (start_recording)")
    try:
        resp = requests.post(f"{BASE_URL}/start_recording", timeout=5)
        data = resp.json()
        status = data.get("status", "?")
        message = data.get("message", "?")
        recording = data.get("recording_active", False)

        print(f"  📡 Status: {status}")
        print(f"  💬 Mensaje: {message}")
        print(f"  🎙️ Grabando: {recording}")

        if status == "success":
            print_ok("Grabación iniciada correctamente")
            return True
        else:
            print_fail(f"No se pudo iniciar grabación: {message}")
            return False

    except Exception as e:
        print_fail(f"Error en /start_recording: {e}")
        return False


def stop_recording() -> bool:
    """Simular soltar botón VR"""
    print_section("PASO 4: Soltando botón VR (stop_recording)")
    try:
        resp = requests.post(f"{BASE_URL}/stop_recording", timeout=10)
        data = resp.json()
        status = data.get("status", "?")
        message = data.get("message", "?")

        print(f"  📡 Status: {status}")
        print(f"  💬 Mensaje: {message}")

        if status == "processing":
            print_ok("Audio enviado a procesamiento (STT → IA → TTS)")
            return True
        elif status == "error":
            print_fail(f"Error: {message}")
            return False
        elif status == "warning":
            print_info(f"Advertencia: {message}")
            return False
        else:
            print_info(f"Status inesperado: {status}")
            return False

    except Exception as e:
        print_fail(f"Error en /stop_recording: {e}")
        return False


def poll_response(timeout: int = 30) -> dict:
    """Hacer polling hasta recibir respuesta de IA"""
    print_section("PASO 5: Esperando respuesta de IA (polling)")
    print_info(f"Polling cada 1s, timeout: {timeout}s")

    start = time.time()
    dots = 0

    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{BASE_URL}/get_latest_response", timeout=5)
            data = resp.json()

            if data.get("has_response") and data.get("response"):
                elapsed = time.time() - start
                print()  # Nueva línea después de los puntos
                print_ok(f"Respuesta recibida en {elapsed:.1f}s")
                print()
                print(f"  🤖 RESPUESTA IA: {data['response']}")
                print(f"  📊 ESTADO:       {data.get('state', '?')}")
                print(f"  🏁 TERMINADO:    {data.get('conversation_finished', False)}")
                return data

        except Exception as e:
            pass

        # Mostrar progreso
        dots += 1
        print(f"  ⏳ Esperando{'.' * (dots % 4)}   ", end="\r")
        time.sleep(1)

    print()
    print_fail(f"Timeout ({timeout}s) esperando respuesta de IA")
    print_info("Verifica los logs del servidor para más detalles")
    return {}


def run_single_turn(record_seconds: int = 5) -> dict:
    """Ejecutar un turno completo de conversación"""

    # Iniciar grabación
    if not start_recording():
        return {}

    # Dar tiempo para hablar
    print()
    print("  " + "─" * 50)
    print(f"  🎙️  ¡HABLA AHORA! Tienes {record_seconds} segundos...")
    print("  " + "─" * 50)

    for i in range(record_seconds, 0, -1):
        print(f"  ⏱️  {i}s restantes...  ", end="\r")
        time.sleep(1)
    print(f"  ⏱️  ¡Tiempo!          ")

    # Detener grabación
    if not stop_recording():
        return {}

    # Esperar respuesta
    return poll_response(timeout=30)


def main():
    print()
    print("╔" + "═" * 58 + "╗")
    print("║   TEST DE FLUJO COMPLETO: BOTÓN VR → STT → IA → TTS    ║")
    print("╚" + "═" * 58 + "╝")

    # Verificaciones previas
    if not check_server_running():
        sys.exit(1)

    if not check_status():
        print_info("Continuando de todas formas...")

    # Bucle de conversación
    turn = 0
    while True:
        turn += 1
        print()
        print("╔" + "═" * 58 + "╗")
        print(f"║   TURNO {turn} DE CONVERSACIÓN                              ║")
        print("╚" + "═" * 58 + "╝")

        # Preguntar duración
        try:
            seconds_input = input("\n  ⏱️  Segundos para hablar (default=5, 'q' para salir): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if seconds_input.lower() in ('q', 'quit', 'exit', 'salir'):
            break

        try:
            record_seconds = int(seconds_input) if seconds_input else 5
        except ValueError:
            record_seconds = 5

        record_seconds = max(2, min(30, record_seconds))

        # Ejecutar turno
        result = run_single_turn(record_seconds=record_seconds)

        if result.get("conversation_finished"):
            print()
            print_section("🏁 CONVERSACIÓN TERMINADA")
            print_ok("El bot pagó y se despidió. Fin de la simulación.")
            break

        # Estado actual
        rec_status = check_recording_status()
        if rec_status:
            print(f"\n  📊 Estado conversación: {rec_status.get('conversation_state', '?')}")

    print()
    print_section("FIN DEL TEST")
    print_ok("Test completado")


if __name__ == "__main__":
    main()
