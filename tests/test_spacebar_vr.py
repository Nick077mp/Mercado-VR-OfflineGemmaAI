"""VR button simulator — test the API server with the spacebar.

Simulates the exact VR controller behavior:
  1. Hold SPACE   -> POST /start_recording  (start recording)
  2. Release SPACE -> POST /stop_recording   (stop, process STT->LLM)
  3. Auto-poll /get_latest_response for the AI answer

Requirements:
  - api_server.py running on port 8000
  - Ollama running (ollama serve)
  - pip install keyboard requests colorama
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import keyboard
import requests

try:
    from colorama import Fore, Style, init
    init()
except ImportError:
    class Fore:
        GREEN = YELLOW = RED = CYAN = MAGENTA = WHITE = BLUE = RESET = ""
    class Style:
        BRIGHT = DIM = RESET_ALL = ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL: str = "http://127.0.0.1:8000"
VR_LISTEN_PORT: int = 8001
POLLING_INTERVAL: float = 0.5
POLLING_TIMEOUT: int = 30


# ---------------------------------------------------------------------------
# VRSimulator
# ---------------------------------------------------------------------------

class VRSimulator:
    """Simulates the VR push-to-talk flow using the spacebar."""

    def __init__(
        self,
        api_url: str = API_BASE_URL,
        vr_port: int = VR_LISTEN_PORT,
    ) -> None:
        self._api_url: str = api_url
        self._vr_port: int = vr_port
        self._space_pressed: bool = False
        self._is_recording: bool = False
        self._waiting_response: bool = False
        self._conversation_count: int = 0

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._clear_screen()
        self._print_header()

        print(f"  {Fore.CYAN}Checking services...{Style.RESET_ALL}")
        if not self._check_server():
            print(f"\n  {Fore.RED}Cannot continue without API server.{Style.RESET_ALL}")
            print(f"  {Fore.YELLOW}Run these first:{Style.RESET_ALL}")
            print(f"     1. {Fore.WHITE}ollama serve{Style.RESET_ALL}")
            print(f"     2. {Fore.WHITE}python api_server.py{Style.RESET_ALL}")
            print(f"     3. {Fore.WHITE}python tests/test_spacebar_vr.py{Style.RESET_ALL}")
            input("\n  Press ENTER to exit...")
            sys.exit(1)

        self._start_vr_listener()
        print()
        self._print_controls()
        print(f"  {Fore.GREEN}Ready. Hold SPACE to record.{Style.RESET_ALL}\n")

        keyboard.on_press_key("space", self._on_space_press)
        keyboard.on_release_key("space", self._on_space_release)

        try:
            keyboard.wait("esc")
        except KeyboardInterrupt:
            pass

        print(f"\n  {Fore.CYAN}Simulator finished. Interactions: "
              f"{self._conversation_count}{Style.RESET_ALL}\n")

    # ------------------------------------------------------------------
    # Server checks
    # ------------------------------------------------------------------

    def _check_server(self) -> bool:
        try:
            r = requests.get(f"{self._api_url}/status", timeout=3)
            if r.status_code == 200:
                data = r.json()
                print(f"  {Fore.GREEN}✅ API Server:{Style.RESET_ALL} Connected")
                print(f"  {Fore.GREEN}✅ Ollama:{Style.RESET_ALL}      "
                      f"{'Available' if data.get('ollama_available') else 'Not available'}")
                print(f"  {Fore.GREEN}✅ STT:{Style.RESET_ALL}         "
                      f"{'Ready' if data.get('stt_loaded') else 'Not loaded'}")
                return True
            print(f"  {Fore.RED}Server responded with code: {r.status_code}{Style.RESET_ALL}")
            return False
        except requests.ConnectionError:
            print(f"  {Fore.RED}Cannot connect to {self._api_url}{Style.RESET_ALL}")
            return False
        except Exception as e:
            print(f"  {Fore.RED}Server check error: {e}{Style.RESET_ALL}")
            return False

    # ------------------------------------------------------------------
    # Recording control
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        try:
            print(f"\n  {Fore.GREEN}[REC] Recording... (hold SPACE){Style.RESET_ALL}", flush=True)
            r = requests.post(f"{self._api_url}/start_recording", timeout=5)
            data = r.json()
            if data.get("status") == "success":
                self._is_recording = True
                print(f"  {Fore.GREEN}   ✅ {data.get('message', 'Started')}{Style.RESET_ALL}")
            else:
                print(f"  {Fore.YELLOW}   {data.get('message', 'Unknown error')}{Style.RESET_ALL}")
        except requests.ConnectionError:
            print(f"  {Fore.RED}   No server connection{Style.RESET_ALL}")
        except Exception as e:
            print(f"  {Fore.RED}   Error: {e}{Style.RESET_ALL}")

    def _stop_recording(self) -> None:
        try:
            print(f"\n  {Fore.YELLOW}[REC] Stopping...{Style.RESET_ALL}", flush=True)
            r = requests.post(f"{self._api_url}/stop_recording", timeout=10)
            data = r.json()
            self._is_recording = False

            status = data.get("status", "")
            message = data.get("message", "")

            if status == "processing":
                print(f"  {Fore.CYAN}   {message}{Style.RESET_ALL}")
                self._waiting_response = True
                self._conversation_count += 1
                t = threading.Thread(target=self._poll_for_response, daemon=True)
                t.start()
            elif status == "warning":
                print(f"  {Fore.YELLOW}   {message}{Style.RESET_ALL}")
            else:
                print(f"  {Fore.RED}   {message}{Style.RESET_ALL}")

        except requests.ConnectionError:
            self._is_recording = False
            print(f"  {Fore.RED}   No server connection{Style.RESET_ALL}")
        except Exception as e:
            self._is_recording = False
            print(f"  {Fore.RED}   Error: {e}{Style.RESET_ALL}")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_for_response(self) -> None:
        print(f"  {Fore.CYAN}   Waiting for AI response "
              f"(polling every {POLLING_INTERVAL}s)...{Style.RESET_ALL}", flush=True)

        start = time.time()
        dots = 0

        while self._waiting_response and (time.time() - start) < POLLING_TIMEOUT:
            try:
                r = requests.get(f"{self._api_url}/get_latest_response", timeout=3)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("has_response"):
                        elapsed = time.time() - start
                        resp = data.get("response", "")
                        state = data.get("state", "")
                        finished = data.get("conversation_finished", False)

                        print(f"\n  {Fore.GREEN}{'─' * 60}{Style.RESET_ALL}")
                        print(f"  {Fore.GREEN}AI RESPONSE ({elapsed:.1f}s):{Style.RESET_ALL}")
                        print(f"  {Fore.WHITE}{resp}{Style.RESET_ALL}")
                        print(f"  {Style.DIM}   State: {state} | Finished: {finished}{Style.RESET_ALL}")
                        print(f"  {Fore.GREEN}{'─' * 60}{Style.RESET_ALL}")

                        if finished:
                            print(f"\n  {Fore.MAGENTA}Conversation ended by AI{Style.RESET_ALL}")

                        self._waiting_response = False
                        print(f"\n  {Fore.CYAN}Ready (SPACE to record){Style.RESET_ALL}\n")
                        return
            except Exception:
                pass

            dots = (dots + 1) % 4
            print(f"\r  {Fore.CYAN}   Processing{'.' * dots}{' ' * (3 - dots)}{Style.RESET_ALL}",
                  end="", flush=True)
            time.sleep(POLLING_INTERVAL)

        if self._waiting_response:
            print(f"\n  {Fore.RED}   Timeout ({POLLING_TIMEOUT}s){Style.RESET_ALL}")
            self._waiting_response = False

    # ------------------------------------------------------------------
    # Keyboard callbacks
    # ------------------------------------------------------------------

    def _on_space_press(self, event) -> None:
        if event.name == "space" and not self._space_pressed and not self._waiting_response:
            self._space_pressed = True
            self._start_recording()

    def _on_space_release(self, event) -> None:
        if event.name == "space" and self._space_pressed:
            self._space_pressed = False
            self._stop_recording()

    # ------------------------------------------------------------------
    # VR listener (simulated port 8001)
    # ------------------------------------------------------------------

    def _start_vr_listener(self) -> None:
        """Start a mini HTTP server on port 8001 simulating VR's /chat endpoint."""
        try:
            handler = _make_vr_handler()
            server = HTTPServer(("127.0.0.1", self._vr_port), handler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            print(f"  {Fore.BLUE}VR listener active on port {self._vr_port}{Style.RESET_ALL}")
        except OSError as e:
            print(f"  {Fore.YELLOW}Could not start listener on {self._vr_port}: {e}{Style.RESET_ALL}")

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_screen() -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def _print_header(self) -> None:
        print(f"\n{Fore.CYAN}{'=' * 70}")
        print("  VR SIMULATOR — SPACEBAR")
        print(f"{'=' * 70}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}  Simulates VR controller for testing without headset.{Style.RESET_ALL}")
        print(f"{Fore.WHITE}  Connected to: {Fore.GREEN}{self._api_url}{Style.RESET_ALL}")
        print()

    @staticmethod
    def _print_controls() -> None:
        print(f"{Fore.YELLOW}{'─' * 70}{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}HOLD SPACE{Style.RESET_ALL}   ->  Record voice (like VR button)")
        print(f"  {Fore.RED}RELEASE SPACE{Style.RESET_ALL}  ->  Stop and process")
        print(f"  {Fore.MAGENTA}ESC{Style.RESET_ALL}            ->  Exit")
        print(f"{Fore.YELLOW}{'─' * 70}{Style.RESET_ALL}")


# ---------------------------------------------------------------------------
# VR chat handler (port 8001 mock)
# ---------------------------------------------------------------------------

def _make_vr_handler():
    """Create a request handler class for the simulated VR server."""

    class _VRChatHandler(BaseHTTPRequestHandler):

        def do_POST(self):
            if self.path == "/chat":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    resp = data.get("response", "")
                    state = data.get("state", "")
                    finished = data.get("conversation_finished", False)
                    print(f"\n  {Fore.BLUE}[VR PUSH] Received on port 8001:{Style.RESET_ALL}")
                    print(f"  {Fore.WHITE}   {resp}{Style.RESET_ALL}")
                    print(f"  {Style.DIM}   State: {state} | Finished: {finished}{Style.RESET_ALL}")
                except Exception as e:
                    print(f"  {Fore.RED}   Parse error: {e}{Style.RESET_ALL}")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            pass

    return _VRChatHandler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    simulator = VRSimulator()
    simulator.run()
