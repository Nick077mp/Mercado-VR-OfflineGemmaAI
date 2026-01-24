# 🎮 Configuración de Unreal Engine para Sistema de IA de Voz

## 📋 Resumen
Este documento explica cómo configurar Unreal Engine para comunicarse correctamente con el servidor Python de IA de voz.

## 🔌 Arquitectura de Comunicación

### Puertos
- **Python API Server**: `http://localhost:8000` (recibe requests de Unreal)
- **Unreal HTTP Server**: `http://localhost:8001` (recibe respuestas de Python)

### Flujo de Comunicación
```
1. Usuario presiona botón VR
   ↓
2. Unreal → POST http://localhost:8000/start_recording
   ← Python responde: {"status": "success"}
   ↓
3. Usuario suelta botón VR
   ↓
4. Unreal → POST http://localhost:8000/stop_recording
   ← Python responde inmediato: {"status": "processing"}
   ↓
5. Python procesa en background (STT + IA + TTS)
   ↓
6. Python → POST http://localhost:8001/chat
   {
     "response": "Texto de respuesta IA",
     "state": "NEGOTIATING",
     "conversation_finished": false
   }
   ↓
7. Unreal recibe y activa lip sync del avatar
```

## ⚙️ Configuración en Unreal Engine

### 1. Configurar Endpoint de Envío (Unreal → Python)
En tu Blueprint `AC_AI_httpRequest` o similar:

```cpp
// URL base del servidor Python
String ServerURL = "http://127.0.0.1:8000"

// Iniciar grabación
POST: ServerURL + "/start_recording"
Body: (vacío)
Headers: Content-Type: application/json

// Detener grabación  
POST: ServerURL + "/stop_recording"
Body: (vacío)
Headers: Content-Type: application/json
```

### 2. Configurar Servidor HTTP en Unreal (Puerto 8001)
Necesitas crear un HTTP server en Unreal que escuche en puerto 8001 para recibir las respuestas del servidor Python.

#### Opción A: Usando HTTP Server Plugin (Recomendado)
Si Unreal tiene un plugin de HTTP Server:

1. Habilitar plugin HTTP Server en Project Settings
2. Configurar puerto 8001
3. Crear endpoint `/chat` que reciba POST requests

#### Opción B: Usando Polling (Alternativa Simple)
Si no puedes crear un servidor HTTP en Unreal, usa polling:

1. **Eliminar** la necesidad de servidor en puerto 8001
2. **Modificar** api_server.py para usar un sistema de cola
3. **Crear** endpoint GET en Python que Unreal consulte cada 0.5s:

```cpp
// En Unreal, crear timer que consulte cada 0.5 segundos:
GET: http://127.0.0.1:8000/get_latest_response

Respuesta si hay texto nuevo:
{
  "response": "Texto IA",
  "state": "NEGOTIATING", 
  "has_response": true
}

Respuesta si no hay nada nuevo:
{
  "response": "",
  "has_response": false
}
```

### 3. Implementación de Botón VR

#### Blueprint: VR Button Controller

```
Event BeginPlay
  ├─> Setup HTTP Component
  └─> Initialize Variables
      ├─> IsRecording = false
      └─> ServerURL = "http://127.0.0.1:8000"

Event Tick (if using Polling)
  └─> Timer (0.5s)
      └─> GET /get_latest_response
          └─> If has_response
              └─> Activate Lip Sync
              └─> Display Subtitles

On VR Button Pressed
  └─> POST /start_recording
      ├─> On Success
      │   ├─> IsRecording = true
      │   └─> Show Recording Indicator
      └─> On Failure
          └─> Log Error

On VR Button Released
  └─> POST /stop_recording
      ├─> On Success
      │   ├─> IsRecording = false
      │   └─> Hide Recording Indicator
      │   └─> Show "Processing..." indicator
      └─> On Failure
          └─> Log Error
```

## 🔧 Modificaciones Necesarias en Python (si usas Polling)

Si decides usar polling en lugar de servidor HTTP en Unreal:

### Agregar endpoint en api_server.py:
```python
# Variable global para última respuesta
latest_ai_response = {
    "response": "",
    "state": "waiting",
    "has_response": False
}

@app.get("/get_latest_response")
async def get_latest_response():
    """Endpoint para que Unreal consulte última respuesta"""
    global latest_ai_response
    
    # Si hay respuesta, devolverla y limpiar
    if latest_ai_response["has_response"]:
        response = latest_ai_response.copy()
        latest_ai_response["has_response"] = False  # Marcar como leída
        return response
    
    # No hay respuesta nueva
    return {
        "response": "",
        "state": "waiting",
        "has_response": False
    }

# Modificar process_audio_background para actualizar latest_ai_response:
async def process_audio_background(audio_path: str):
    global latest_ai_response
    
    # ... [código existente de STT + IA] ...
    
    if ai_response:
        # Guardar respuesta para que Unreal la consulte
        latest_ai_response = {
            "response": ai_response,
            "state": conversation_state,
            "conversation_finished": conversation_state == STATE_FINISHED,
            "has_response": True
        }
```

## 🚀 Inicio del Sistema

### Opción 1: Usando Scripts de Auto-inicio (Recomendado)
1. Ejecutar `startup_all.bat` ANTES de abrir Unreal
2. Esperar confirmación de que ambos servidores están corriendo
3. Abrir Unreal Engine
4. Iniciar el proyecto VR

### Opción 2: Inicio desde Unreal (Avanzado)
Configurar Blueprint que se ejecute al iniciar el nivel:

```cpp
Event BeginPlay
  └─> Execute Console Command
      └─> Command: [Ruta completa]\startup_all.bat
      └─> Wait 10 seconds
      └─> Verify servers with health checks
```

## 🧪 Pruebas

### Test 1: Verificar Conectividad Python
```bash
# En PowerShell o CMD
curl http://localhost:8000/
# Debe responder: {"message": "AI Voice Server API está corriendo"}
```

### Test 2: Verificar Endpoints de Grabación
```bash
# Iniciar grabación
curl -X POST http://localhost:8000/start_recording
# Debe responder: {"status": "success", "recording_active": true}

# Detener grabación (después de 2-3 segundos)
curl -X POST http://localhost:8000/stop_recording
# Debe responder: {"status": "processing", "recording_active": false}
```

### Test 3: Verificar Polling (si usas esa opción)
```bash
curl http://localhost:8000/get_latest_response
# Debe responder: {"response": "", "has_response": false}
# (o con datos si hay respuesta pendiente)
```

## 🐛 Solución de Problemas

### Problema: Unreal no puede conectarse a Python
**Solución:**
1. Verificar que `startup_all.bat` se ejecutó correctamente
2. Verificar firewall de Windows (permitir Python y Unreal)
3. Probar manualmente con curl los endpoints

### Problema: Python no envía respuesta a Unreal
**Opciones:**
1. Verificar que Unreal está escuchando en puerto 8001
2. Alternativamente, cambiar a sistema de polling (ver sección anterior)
3. Revisar logs de Python para ver errores de conexión

### Problema: Grabación se congela
**Solución:** 
✅ Ya corregido en la nueva versión con BackgroundTasks
- El procesamiento ahora es asíncrono
- La respuesta HTTP es inmediata
- El procesamiento continúa en background

## 📚 Referencias

- Documentación FastAPI: https://fastapi.tiangolo.com/
- Unreal HTTP: https://docs.unrealengine.com/en-US/API/Runtime/HTTP/
- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md

## ✅ Checklist de Implementación

- [ ] Scripts de inicio creados y probados
- [ ] Configurado botón VR para POST a /start_recording y /stop_recording
- [ ] Implementado sistema de recepción (HTTP Server puerto 8001 O Polling)
- [ ] Probados todos los endpoints manualmente con curl
- [ ] Integrado lip sync con respuesta de IA
- [ ] Verificado que no se congela el sistema
- [ ] Documentación leída y entendida
