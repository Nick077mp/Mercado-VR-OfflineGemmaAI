"""
🎮 SIMULADOR DE BOTÓN VR CON TECLA ESPACIO
============================================
Simula el comportamiento exacto del control VR usando la barra espaciadora.

Flujo (idéntico al VR):
  1. Mantener ESPACIO → POST /start_recording  (empieza a grabar)
  2. Soltar ESPACIO   → POST /stop_recording   (detiene, procesa STT→IA→TTS)
  3. Polling automático en /get_latest_response

Requisitos:
  - api_server.py corriendo en puerto 8000  (python api_server.py)
  - Ollama corriendo  (ollama serve)
  - pip install keyboard requests colorama

Uso:
  python test_spacebar_vr.py

Autor: Simulador de pruebas
"""

import keyboard
import requests
import threading
import time
import sys
import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from colorama import init, Fore, Style
    init()
except ImportError:
    # Fallback sin colores
    class Fore:
        GREEN = YELLOW = RED = CYAN = MAGENTA = WHITE = BLUE = RESET = ""
    class Style:
        BRIGHT = DIM = RESET_ALL = ""

# =========================
# CONFIGURACIÓN
# =========================
API_BASE_URL = "http://127.0.0.1:8000"
VR_LISTEN_PORT = 8001  # Puerto que simula el listener de VR
POLLING_INTERVAL = 0.5  # segundos entre cada polling
POLLING_TIMEOUT = 30    # máximo segundos esperando respuesta

# =========================
# ESTADO
# =========================
is_space_pressed = False
is_recording = False
is_waiting_response = False
polling_active = False
conversation_count = 0


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_header():
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"🎮  SIMULADOR VR – TECLA ESPACIO")
    print(f"{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}  Simula el botón del control VR para pruebas sin headset.{Style.RESET_ALL}")
    print(f"{Fore.WHITE}  Conectado a: {Fore.GREEN}{API_BASE_URL}{Style.RESET_ALL}")
    print()


def print_status():
    print(f"{Fore.YELLOW}{'─'*70}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}MANTENER ESPACIO{Style.RESET_ALL}  →  Grabar voz (como presionar botón VR)")
    print(f"  {Fore.RED}SOLTAR  ESPACIO{Style.RESET_ALL}  →  Detener y procesar (como soltar botón VR)")
    print(f"  {Fore.MAGENTA}ESC{Style.RESET_ALL}              →  Salir")
    print(f"{Fore.YELLOW}{'─'*70}{Style.RESET_ALL}")
    print()


def check_server():
    """Verificar que el servidor API esté corriendo"""
    try:
        r = requests.get(f"{API_BASE_URL}/status", timeout=3)
        if r.status_code == 200:
            data = r.json()
            print(f"  {Fore.GREEN}✅ Servidor API:{Style.RESET_ALL} Conectado")
            print(f"  {Fore.GREEN}✅ Ollama:{Style.RESET_ALL}      {'Disponible' if data.get('ollama_available') else '❌ No disponible'}")
            print(f"  {Fore.GREEN}✅ STT:{Style.RESET_ALL}         {'Listo' if data.get('stt_loaded') else '❌ No cargado'}")
            return True
        else:
            print(f"  {Fore.RED}❌ Servidor respondió con código: {r.status_code}{Style.RESET_ALL}")
            return False
    except requests.ConnectionError:
        print(f"  {Fore.RED}❌ No se pudo conectar al servidor en {API_BASE_URL}{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}💡 Inicia el servidor con: python api_server.py{Style.RESET_ALL}")
        return False
    except Exception as e:
        print(f"  {Fore.RED}❌ Error verificando servidor: {e}{Style.RESET_ALL}")
        return False


def check_recording_status():
    """Consultar estado actual de grabación en el servidor"""
    try:
        r = requests.get(f"{API_BASE_URL}/recording_status", timeout=2)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def start_recording():
    """Llamar POST /start_recording (equivale a presionar botón VR)"""
    global is_recording
    try:
        print(f"\n  {Fore.GREEN}🎙️  GRABANDO... (mantén ESPACIO presionado){Style.RESET_ALL}", flush=True)
        r = requests.post(f"{API_BASE_URL}/start_recording", timeout=5)
        data = r.json()

        if data.get("status") == "success":
            is_recording = True
            print(f"  {Fore.GREEN}   ✅ {data.get('message', 'Grabación iniciada')}{Style.RESET_ALL}")
        else:
            print(f"  {Fore.YELLOW}   ⚠️  {data.get('message', 'Error desconocido')}{Style.RESET_ALL}")

        return data
    except requests.ConnectionError:
        print(f"  {Fore.RED}   ❌ Sin conexión al servidor{Style.RESET_ALL}")
        return None
    except Exception as e:
        print(f"  {Fore.RED}   ❌ Error: {e}{Style.RESET_ALL}")
        return None


def stop_recording():
    """Llamar POST /stop_recording (equivale a soltar botón VR)"""
    global is_recording, is_waiting_response, conversation_count
    try:
        print(f"\n  {Fore.YELLOW}⏹️  Deteniendo grabación...{Style.RESET_ALL}", flush=True)
        r = requests.post(f"{API_BASE_URL}/stop_recording", timeout=10)
        data = r.json()
        is_recording = False

        status = data.get("status", "")
        message = data.get("message", "")

        if status == "processing":
            print(f"  {Fore.CYAN}   🔄 {message}{Style.RESET_ALL}")
            is_waiting_response = True
            conversation_count += 1
            # Iniciar polling en hilo aparte
            polling_thread = threading.Thread(target=poll_for_response, daemon=True)
            polling_thread.start()
        elif status == "warning":
            print(f"  {Fore.YELLOW}   ⚠️  {message}{Style.RESET_ALL}")
        else:
            print(f"  {Fore.RED}   ❌ {message}{Style.RESET_ALL}")

        return data
    except requests.ConnectionError:
        is_recording = False
        print(f"  {Fore.RED}   ❌ Sin conexión al servidor{Style.RESET_ALL}")
        return None
    except Exception as e:
        is_recording = False
        print(f"  {Fore.RED}   ❌ Error: {e}{Style.RESET_ALL}")
        return None


def poll_for_response():
    """Polling en /get_latest_response hasta recibir respuesta de IA"""
    global is_waiting_response

    print(f"  {Fore.CYAN}   ⏳ Esperando respuesta de IA (polling cada {POLLING_INTERVAL}s)...{Style.RESET_ALL}", flush=True)

    start_time = time.time()
    dots = 0

    while is_waiting_response and (time.time() - start_time) < POLLING_TIMEOUT:
        try:
            r = requests.get(f"{API_BASE_URL}/get_latest_response", timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get("has_response"):
                    response_text = data.get("response", "")
                    state = data.get("state", "")
                    finished = data.get("conversation_finished", False)

                    elapsed = time.time() - start_time
                    print(f"\n  {Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
                    print(f"  {Fore.GREEN}🤖 RESPUESTA IA ({elapsed:.1f}s):{Style.RESET_ALL}")
                    print(f"  {Fore.WHITE}{response_text}{Style.RESET_ALL}")
                    print(f"  {Style.DIM}   Estado: {state} | Finalizada: {finished}{Style.RESET_ALL}")
                    print(f"  {Fore.GREEN}{'─'*60}{Style.RESET_ALL}")

                    if finished:
                        print(f"\n  {Fore.MAGENTA}👋 Conversación finalizada por la IA{Style.RESET_ALL}")

                    is_waiting_response = False
                    print(f"\n  {Fore.CYAN}🎯 Listo para siguiente interacción (ESPACIO para grabar){Style.RESET_ALL}\n")
                    return
        except Exception:
            pass

        # Animación de espera
        dots = (dots + 1) % 4
        print(f"\r  {Fore.CYAN}   ⏳ Procesando{'.' * dots}{' ' * (3-dots)}{Style.RESET_ALL}", end="", flush=True)
        time.sleep(POLLING_INTERVAL)

    if is_waiting_response:
        print(f"\n  {Fore.RED}   ⏰ Timeout esperando respuesta ({POLLING_TIMEOUT}s){Style.RESET_ALL}")
        is_waiting_response = False


def on_space_press(event):
    """Callback cuando se PRESIONA la barra espaciadora"""
    global is_space_pressed
    if event.name == "space" and not is_space_pressed and not is_waiting_response:
        is_space_pressed = True
        start_recording()


def on_space_release(event):
    """Callback cuando se SUELTA la barra espaciadora"""
    global is_space_pressed
    if event.name == "space" and is_space_pressed:
        is_space_pressed = False
        stop_recording()


def main():
    global is_waiting_response

    clear_screen()
    print_header()

    # Verificar conexión al servidor
    print(f"  {Fore.CYAN}🔍 Verificando servicios...{Style.RESET_ALL}")
    if not check_server():
        print(f"\n  {Fore.RED}❌ No se puede continuar sin el servidor API.{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}💡 Ejecuta primero:{Style.RESET_ALL}")
        print(f"     1. {Fore.WHITE}ollama serve{Style.RESET_ALL}")
        print(f"     2. {Fore.WHITE}python api_server.py{Style.RESET_ALL}")
        print(f"     3. {Fore.WHITE}python test_spacebar_vr.py{Style.RESET_ALL}")
        input(f"\n  Presiona ENTER para salir...")
        sys.exit(1)

    # Iniciar servidor simulado de VR (puerto 8001)
    start_vr_listener()

    print()
    print_status()

    print(f"  {Fore.GREEN}🎯 Sistema listo. Mantén ESPACIO para hablar.{Style.RESET_ALL}\n")

    # Registrar eventos de teclado
    keyboard.on_press_key("space", on_space_press)
    keyboard.on_release_key("space", on_space_release)

    try:
        # Esperar hasta que se presione ESC
        keyboard.wait("esc")
    except KeyboardInterrupt:
        pass

    print(f"\n  {Fore.CYAN}👋 Simulador VR finalizado. Interacciones: {conversation_count}{Style.RESET_ALL}\n")


# =========================
# SIMULADOR DE LISTENER VR (puerto 8001)
# =========================
class VRChatHandler(BaseHTTPRequestHandler):
    """Simula el endpoint /chat de la app VR en puerto 8001"""

    def do_POST(self):
        if self.path == "/chat":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                response_text = data.get("response", "")
                state = data.get("state", "")
                finished = data.get("conversation_finished", False)
                print(f"\n  {Fore.BLUE}📡 [VR PUSH] Recibido en puerto 8001:{Style.RESET_ALL}")
                print(f"  {Fore.WHITE}   🤖 {response_text}{Style.RESET_ALL}")
                print(f"  {Style.DIM}   Estado: {state} | Finalizada: {finished}{Style.RESET_ALL}")
            except Exception as e:
                print(f"  {Fore.RED}   ❌ Error parseando push: {e}{Style.RESET_ALL}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Silenciar logs HTTP


def start_vr_listener():
    """Inicia un mini servidor HTTP en puerto 8001 simulando el listener de VR"""
    try:
        server = HTTPServer(('127.0.0.1', VR_LISTEN_PORT), VRChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"  {Fore.BLUE}📡 Listener VR simulado activo en puerto {VR_LISTEN_PORT}{Style.RESET_ALL}")
    except OSError as e:
        print(f"  {Fore.YELLOW}⚠️  No se pudo iniciar listener en {VR_LISTEN_PORT}: {e}{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}   (puede que la app VR real ya esté usando ese puerto){Style.RESET_ALL}")


if __name__ == "__main__":
    main()
