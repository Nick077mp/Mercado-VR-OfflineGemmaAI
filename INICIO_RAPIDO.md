# 🚀 Guía de Inicio Rápido

## ✅ Sistema Funcionando Correctamente

El sistema de IA de voz para VR está completamente operativo con las siguientes características:

- ✅ **Sin congelamiento** - Servidor siempre responsivo
- ✅ **Procesamiento asíncrono** - Background tasks
- ✅ **Auto-inicio** - Un solo comando
- ✅ **Sistema de Polling** - Fácil integración con Unreal
- ✅ **Mensajes claros** - Sin errores confusos

---

## 🎯 Iniciar el Sistema

### 1. Iniciar Servidores (Ollama + Python)

```bash
.\startup_all.bat
```

**Resultado esperado:**
```
========================================
SISTEMA INICIADO CORRECTAMENTE
========================================

Servicios corriendo:
  [*] Ollama (IA)        : http://localhost:11434
  [*] Python API         : http://localhost:8000

Unreal Engine debe:
  [*] Enviar POST a      : http://localhost:8000
  [*] Escuchar en puerto : 8001 (opcional)
```

### 2. Abrir Unreal Engine

Una vez que ambos servidores estén corriendo, iniciar Unreal Engine y el proyecto VR.

---

## 🔌 Endpoints para Unreal

### URL Base
```
http://127.0.0.1:8000
```

### Endpoints Disponibles

#### 1. Iniciar Grabación (Botón Presionado)
```
POST http://127.0.0.1:8000/start_recording
```

#### 2. Detener Grabación (Botón Soltado)
```
POST http://127.0.0.1:8000/stop_recording
```

#### 3. Obtener Respuesta IA (Polling cada 0.5s)
```
GET http://127.0.0.1:8000/get_latest_response
```

**Respuesta con texto nuevo:**
```json
{
  "response": "Buenos días, ¿en qué le puedo ayudar?",
  "state": "NEGOTIATING",
  "conversation_finished": false,
  "has_response": true
}
```

---

## 💬 Mensajes del Sistema

### Mensajes Normales (No son errores)

#### Durante Procesamiento:
```
📡 ℹ️ VR usando modo Polling (puerto 8001 no disponible - esto es normal)
```
**Significado:** Python intentó enviar por Push pero Unreal usa Polling. **Esto es completamente normal.**

#### Durante Transcripción:
```
🔄 Transcribiendo audio...
📝 TRANSCRIPCIÓN FINAL: [texto del usuario]
🤖 IA procesando...
🤖 Cliente: [respuesta de IA]
✅ TTS iniciado
✅ Respuesta guardada para polling
```

#### Cuando Unreal Consulta:
```
📡 Unreal consultó respuesta: [texto]...
```

### Mensajes de Estado:
```
🎙️ ✅ Grabación iniciada por botón VR
🎙️ ⏹️ Grabación detenida por botón VR
✅ Audio procesado: X.XX segundos
```

---

## 🧪 Prueba Rápida (Sin Unreal)

Para verificar que todo funciona correctamente:

```powershell
# 1. Health check
Invoke-WebRequest -Uri http://localhost:8000/ | Select-Object -ExpandProperty Content

# 2. Iniciar grabación
Invoke-WebRequest -Method POST -Uri http://localhost:8000/start_recording | Select-Object -ExpandProperty Content

# 3. Esperar 3 segundos (hablar)
Start-Sleep -Seconds 3

# 4. Detener grabación
Invoke-WebRequest -Method POST -Uri http://localhost:8000/stop_recording | Select-Object -ExpandProperty Content

# 5. Esperar 2 segundos y consultar respuesta
Start-Sleep -Seconds 2
Invoke-WebRequest -Method GET -Uri http://localhost:8000/get_latest_response | Select-Object -ExpandProperty Content
```

---

## 🎮 Configuración Mínima en Unreal

### Variables Blueprint:
```cpp
ServerURL: String = "http://127.0.0.1:8000"
IsRecording: Boolean = false
IsWaitingResponse: Boolean = false
```

### Eventos:

**Botón VR Presionado:**
```
POST {ServerURL}/start_recording
→ IsRecording = true
→ Mostrar indicador de grabación
```

**Botón VR Soltado:**
```
POST {ServerURL}/stop_recording
→ IsRecording = false
→ IsWaitingResponse = true
→ Mostrar "procesando..."
```

**Timer (cada 0.5s):**
```
Si IsWaitingResponse:
  GET {ServerURL}/get_latest_response
  Si has_response == true:
    → IsWaitingResponse = false
    → Activar Lip Sync
    → Mostrar subtítulos
```

---

## ⚙️ Flujo Completo

```
Usuario presiona botón VR
  ↓
Unreal → POST /start_recording
  ↓
Usuario habla (2-5 segundos)
  ↓
Usuario suelta botón VR
  ↓
Unreal → POST /stop_recording
  ↓
Python responde: "processing" (inmediato)
  ↓
Python procesa en background:
  - STT (Faster Whisper)
  - IA (Ollama)
  - TTS (Piper)
  ↓
Python guarda respuesta para polling
  ↓
Unreal consulta cada 0.5s → GET /get_latest_response
  ↓
has_response == true
  ↓
Unreal activa Lip Sync y muestra subtítulos
  ↓
✅ Completado
```

**Tiempo estimado:** 3-10 segundos desde que suelta el botón hasta que llega la respuesta.

---

## 🐛 Solución de Problemas

### Servidor no inicia
```bash
# Verificar que Ollama está instalado
ollama --version

# Verificar entorno virtual Python
.venv\Scripts\activate
python --version  # Debe ser 3.11.x
```

### "Connection refused" desde Unreal
- Verificar que `startup_all.bat` se ejecutó
- Verificar firewall de Windows (permitir Python.exe)
- Probar manualmente con PowerShell (ver pruebas arriba)

### Respuesta muy lenta
Es normal. El procesamiento incluye:
- STT: 1-3 segundos
- IA (Ollama): 2-5 segundos  
- TTS: 1-2 segundos
**Total: 4-10 segundos**

### No se detecta voz
- Verificar que el micrófono VR está funcionando
- Hablar claramente por 2-3 segundos mínimo
- Verificar que se detecta el micrófono correcto (revisar logs de Python)

---

## 📁 Estructura de Archivos

```
local-voice-assistant-feature-boton-added-http-connection/
├── startup_all.bat              ← Ejecutar esto primero
├── api_server.py                ← Servidor principal
├── INICIO_RAPIDO.md            ← Este archivo
├── CAMBIOS_REALIZADOS.md       ← Resumen de cambios técnicos
├── Documentation/
│   ├── UNREAL_SETUP.md         ← Guía detallada Unreal
│   └── VR_BUTTON_CONTROL.md    ← Documentación endpoints
└── services/                    ← Servicios (STT, IA, TTS)
```

---

## 📞 Soporte

**Documentación completa:**
- `CAMBIOS_REALIZADOS.md` - Cambios técnicos detallados
- `Documentation/UNREAL_SETUP.md` - Configuración Unreal paso a paso
- `Documentation/VR_BUTTON_CONTROL.md` - Referencia de endpoints

**Logs útiles:**
- Ventana de Python muestra todo el procesamiento
- Verificar mensajes que empiezan con 📡, 🎙️, 🔄, 🤖

---

## ✅ Checklist de Verificación

Antes de usar con Unreal, verificar:

- [ ] `startup_all.bat` ejecutado exitosamente
- [ ] Ventana "Ollama Server" abierta
- [ ] Ventana "Python AI Server" abierta y muestra "Uvicorn running"
- [ ] Prueba manual con PowerShell funciona
- [ ] Blueprints de Unreal configurados con URLs correctas
- [ ] Timer de polling implementado (0.5s)
- [ ] Lip sync conectado a respuesta de polling

---

## 🎉 ¡Listo para Usar!

Si todos los pasos anteriores funcionan, el sistema está completamente operativo.

**Disfruta tu asistente de IA en VR!** 🎮🤖
