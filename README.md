# 🎙️ Waygroup Local Voice Assistant — 100% Offline

> A fully offline VR voice assistant powered by local AI. No internet required, fully private. **Waygroup Project**

Sistema de asistente de voz para VR que funciona **100% offline** usando IA local. Diseñado para simular conversaciones de compra en una plaza de mercado colombiana, con un personaje (José) que negocia precios en español.

## 🚀 Características

- ✅ **100% Offline** — No requiere conexión a internet
- 🎤 **Speech-to-Text** — Faster-Whisper (small) + Silero VAD en CPU
- 🤖 **LLM Local** — Gemma 3 4B via Ollama (modo GPU híbrido)
- 🔊 **Text-to-Speech** — Piper TTS con voces en español (.onnx)
- 🎮 **Integración VR** — API REST para Unreal Engine (push-to-talk)
- 🇨🇴 **Español colombiano** — Prompts y NLP optimizados para español
- ⚡ **Streaming** — Primera frase enviada a VR antes de completar generación

## 📋 Requisitos

- **Python 3.11+**
- **Ollama** con modelo `gemma3:4b`
- **Windows** (scripts .bat, audio con sounddevice)
- **GPU NVIDIA** recomendada (modo híbrido GPU+CPU para coexistir con VR)

## 🔧 Instalación

```bash
git clone https://github.com/tu-usuario/Waygroup-local-voice-assistant.git
cd Waygroup-local-voice-assistant

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

Verificar que Ollama esté instalado y el modelo descargado:
```bash
ollama pull gemma3:4b
```

## ▶️ Uso

### Modo VR (con Unreal Engine)

```bash
scripts\startup_all.bat
```

Esto inicia automáticamente:
- Servidor Ollama en `localhost:11434` (modo GPU híbrido)
- Servidor Python API en `localhost:8000`

Luego abre el proyecto VR en Unreal Engine. El flujo es:
1. Presionar botón VR → `POST /start_recording`
2. Hablar mientras se mantiene presionado
3. Soltar botón → `POST /stop_recording`
4. Python procesa: STT → LLM → respuesta enviada a VR

### Modo Standalone (terminal)

```bash
python voice_automation_model.py
```

Usa micrófono local con detección automática de silencio (Silero VAD).

## 📁 Estructura del Proyecto

```
Waygroup-local-voice-assistant/
├── api_server.py               # Servidor FastAPI para comunicación con VR
├── voice_automation_model.py    # Grabación + pipeline STT→LLM→TTS standalone
├── requirements.txt            # Dependencias Python
├── services/                   # Módulos de servicios
│   ├── ollama_service.py       # LLM: prompts, estados, negociación, guardrails
│   ├── stt_faster_whisper.py   # STT: Faster-Whisper + Silero VAD
│   └── tts_piper_only.py       # TTS: Piper con voces .onnx
├── scripts/                    # Scripts de inicio (Windows)
│   ├── startup_all.bat         # Inicia Ollama + Python API
│   ├── start_ollama.bat        # Solo Ollama
│   └── start_python_server.bat # Solo Python API
├── tests/                      # Simuladores y tests
│   └── test_spacebar_vr.py     # Simula botón VR con barra espaciadora
├── docs/                       # Documentación adicional
│   ├── UNREAL_SETUP.md         # Configuración de Unreal Engine
│   └── VR_BUTTON_CONTROL.md    # Endpoints de control VR
├── voices/                     # Modelos de voz Piper (.onnx)
└── AI_Push-to-talk/            # Build de Unreal Engine (Windows)
```

## 🎯 Pipeline

```
Micrófono → Faster-Whisper (STT) → Ollama/Gemma 3 (LLM) → Texto → VR (lip sync)
                                                          ↘ Piper TTS → Audio (standalone)
```

### Modo VR
1. VR presiona botón → Python graba audio del micrófono
2. Audio → Faster-Whisper (CPU) → transcripción en español
3. Transcripción → Ollama/Gemma 3 4B (GPU híbrido) → respuesta en streaming
4. Primera frase → push inmediato a VR para lip sync
5. Respuesta completa disponible via polling

### Modo Standalone
1. Micrófono con Silero VAD → detección automática de voz/silencio
2. Audio → Faster-Whisper → transcripción
3. Transcripción → Ollama → respuesta palabra por palabra
4. Cada frase → Piper TTS → reproducción de audio en tiempo real

## 🔌 API Endpoints (puerto 8000)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/start_recording` | Iniciar grabación (botón VR presionado) |
| `POST` | `/stop_recording` | Detener y procesar (botón VR soltado) |
| `GET` | `/get_latest_response` | Polling: obtener última respuesta de IA |
| `GET` | `/recording_status` | Estado actual de grabación |
| `GET` | `/status` | Health check del sistema |

### Comunicación VR

**Polling (recomendado):** Unreal consulta `GET /get_latest_response` cada 0.5s.

**Push (avanzado):** Python envía `POST http://localhost:8001/chat` directamente a Unreal (requiere HTTP server en Unreal).

Ver [docs/UNREAL_SETUP.md](docs/UNREAL_SETUP.md) para configuración completa.

## ⚙️ Configuración

### Perfil de rendimiento GPU/CPU
En `services/ollama_service.py`:
```python
PERFORMANCE_PROFILE = "gpu_hybrid"  # Opciones: "gpu_full", "gpu_hybrid", "cpu_only"
```

### Modelo de IA
En `services/ollama_service.py`:
```python
OLLAMA_MODEL = "gemma3:4b"
```

### Modelo de voz TTS
En `services/tts_piper_only.py`:
```python
PIPER_MODEL = "voices/es_ES-davefx-medium.onnx"
```

## 🐛 Solución de Problemas

| Problema | Solución |
|----------|----------|
| `ModuleNotFoundError` | `.venv\Scripts\activate` → `pip install -r requirements.txt` |
| Ollama no responde | `ollama serve` → verificar `http://localhost:11434` |
| No detecta micrófono | Ejecutar `python voice_automation_model.py` y seleccionar micrófono manualmente |
| VR no recibe respuesta | Verificar que VR haga polling a `GET /get_latest_response` |

## 👤 Autor

Nicolás — Waygroup
