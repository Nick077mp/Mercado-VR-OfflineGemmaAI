# 🎙️ Waygroup Local Voice Assistant - 100% Offline

> An offline AI assistant that responds to prompts using a custom-trained cloned voice. No internet required, fully private, and personalized. **Waygroup Project**

Sistema completo de asistente de voz que funciona **totalmente offline** sin necesidad de internet.

## 🚀 Características

- ✅ **100% Offline** - No requiere conexión a internet
- 🎤 **Reconocimiento de voz** - Faster-Whisper con Silero VAD
- 🤖 **Inteligencia Artificial** - Mistral via Ollama
- 🔊 **Síntesis de voz** - Piper TTS (voces españolas de calidad)
- ⚡ **Ultra rápido** - Audio generado en <0.2 segundos
- 🇨🇴 **Español colombiano** - Optimizado para español
- 🔧 **CFFI Error Suppression** - Sin ventanas emergentes en Windows

## 📋 Requisitos Previos

1. **Python 3.11.9**
2. **Ollama** instalado con modelo Mistral
3. **Windows** (para pyttsx3)

## 🔧 Instalación

### 1. Clonar el repositorio
```bash
git clone <tu-repositorio>
cd ai_voice_server
```

### 2. Crear entorno virtual
```bash
python -m venv venv
.\venv\Scripts\activate
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Verificar Ollama
Asegúrate de que Ollama esté corriendo:
```bash
ollama run mistral
```

## ▶️ Uso

### Opción 1: Con Unreal Engine (Sistema Completo VR)

1. **Iniciar servidores:**
   ```bash
   # Ejecutar script de auto-inicio
   startup_all.bat
   ```
   Esto iniciará:
   - Servidor Ollama (puerto 11434)
   - Servidor Python API (puerto 8000)

2. **Abrir Unreal Engine:**
   - Esperar a que ambos servidores estén corriendo
   - Iniciar proyecto VR
   - El sistema estará listo para recibir comandos de voz

3. **Usar en VR:**
   - Presionar botón del control VR para iniciar grabación
   - Hablar mientras se mantiene presionado
   - Soltar botón para procesar
   - La IA responderá automáticamente

### Opción 2: Modo Standalone (Solo Terminal)

Para usar sin Unreal Engine:
```bash
python main.py
```

## 📁 Estructura del Proyecto

```
ai_voice_server/
├── main.py                    # Punto de entrada standalone
├── api_server.py              # Servidor API para Unreal Engine
├── voice_recorder.py          # Lógica de grabación
├── startup_all.bat            # Script de auto-inicio (Windows)
├── start_ollama.bat           # Iniciar servidor Ollama
├── start_python_server.bat    # Iniciar servidor Python
├── services/                  # Módulos de servicios
│   ├── stt_faster_whisper.py # Reconocimiento de voz (STT)
│   ├── ollama_service.py     # IA con Ollama (LLM)
│   ├── tts_piper_only.py     # Síntesis de voz (TTS)
│   └── audio_service.py      # Manejo de audio
├── Documentation/             # Documentación
│   ├── VR_BUTTON_CONTROL.md  # Control por botón VR
│   └── UNREAL_SETUP.md       # Configuración Unreal
├── requirements.txt          # Dependencias Python
└── README.md                 # Este archivo
```

## 🎯 Flujo de Funcionamiento

### Modo VR (con Unreal Engine)
1. **Botón presionado** → Inicia grabación de audio
2. **Usuario habla** → Audio se captura en tiempo real
3. **Botón soltado** → Detiene grabación y envía a servidor
4. **Transcripción** → Faster Whisper convierte voz a texto
5. **Procesamiento IA** → Ollama genera respuesta inteligente
6. **Síntesis de voz** → Piper TTS genera audio
7. **Envío a Unreal** → Texto enviado para lip sync del avatar
8. **Reproducción** → Audio se reproduce automáticamente

### Modo Standalone
1. **Grabación** → El usuario habla por el micrófono
2. **Transcripción** → Faster Whisper convierte voz a texto
3. **Procesamiento** → Ollama genera respuesta inteligente
4. **Síntesis** → Piper TTS convierte respuesta a audio
5. **Reproducción** → El audio se reproduce automáticamente

## 🔌 Endpoints de API (para Unreal Engine)

### Servidor Python (puerto 8000)
- `POST /start_recording` - Iniciar grabación (botón presionado)
- `POST /stop_recording` - Detener y procesar (botón soltado)
- `GET /get_latest_response` - Consultar última respuesta (polling)
- `GET /recording_status` - Estado de grabación
- `GET /status` - Estado del sistema
- `GET /` - Health check

### Comunicación con Unreal
**Método 1: Polling (Recomendado)**
- Unreal consulta `GET /get_latest_response` cada 0.5 segundos
- Cuando hay respuesta nueva, se devuelve y se marca como leída

**Método 2: Push (Avanzado)**
- Python envía `POST http://localhost:8001/chat` a Unreal
- Requiere servidor HTTP en Unreal escuchando puerto 8001

Ver `Documentation/UNREAL_SETUP.md` para detalles completos.

## ⚠️ Configuración

### Cambiar velocidad de voz
Edita `services/tts_service.py`:
```python
TTS_ENGINE_OBJ.setProperty('rate', 180)  # 150-200 normal, 200+ rápido
```

### Cambiar modelo de IA
Edita `services/ollama_service.py`:
```python
MODEL_NAME = "mistral"  # o "llama2", "codellama", etc.
```

## 🐛 Solución de Problemas

### Error: "ModuleNotFoundError"
```bash
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Ollama no responde
```bash
ollama serve
ollama run mistral
```

### No detecta micrófono
Ejecuta y selecciona el micrófono correcto cuando el sistema pregunte.

## 📝 Licencia

Proyecto personal - Uso libre

## 👤 Autor

Nicolás - Programador

---
**Nota**: Este sistema está optimizado para Windows y ahora incluye supresión de errores CFFI para mejor compatibilidad.
