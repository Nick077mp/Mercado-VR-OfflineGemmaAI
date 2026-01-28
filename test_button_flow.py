"""Prueba de flujo completo VR botón -> STT -> IA (Ollama customer) -> TTS.

Uso:
  1. En una terminal, ejecutar:  python api_server.py
  2. En otra terminal, ejecutar: python test_button_flow.py

El script:
  - Verifica /status
  - Simula /start_recording (como si se presionara el botón)
  - Espera unos segundos para que hables
  - Llama /stop_recording (como si se soltara el botón)
  - Hace polling a /get_latest_response para ver la respuesta

En los logs de api_server podrás verificar que se usa el modelo OLLAMA_MODEL = "customer".
"""

import time
import requests

BASE_URL = "http://127.0.0.1:8000"


def print_section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_status() -> None:
    print_section("1) Verificando /status")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        print("/status ->", data)
    except Exception as e:
        print(f"❌ Error llamando /status: {e}")


def simulate_button_flow(record_seconds: int = 5, poll_timeout: int = 20) -> None:
    print_section("2) Simulando botón VR: /start_recording")
    try:
        resp = requests.post(f"{BASE_URL}/start_recording", timeout=5)
        print("/start_recording status:", resp.status_code)
        print("/start_recording body:", resp.text)
    except Exception as e:
        print(f"❌ Error llamando /start_recording: {e}")
        return

    print(f"\n🎙️ Ahora habla al micrófono durante ~{record_seconds} segundos...")
    time.sleep(record_seconds)

    print_section("3) Simulando soltar botón VR: /stop_recording")
    try:
        resp = requests.post(f"{BASE_URL}/stop_recording", timeout=10)
        print("/stop_recording status:", resp.status_code)
        print("/stop_recording body:", resp.text)
    except Exception as e:
        print(f"❌ Error llamando /stop_recording: {e}")
        return

    print_section("4) Polling /get_latest_response (como Unreal)")
    start = time.time()
    while True:
        try:
            resp = requests.get(f"{BASE_URL}/get_latest_response", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            print("/get_latest_response ->", data)

            # Si hay respuesta no vacía, terminamos
            if data.get("response"):
                break
        except Exception as e:
            print(f"⚠️ Error en /get_latest_response: {e}")

        if time.time() - start > poll_timeout:
            print("⏰ Timeout esperando respuesta de IA")
            break

        time.sleep(1)


if __name__ == "__main__":
    check_status()
    simulate_button_flow(record_seconds=6, poll_timeout=25)
