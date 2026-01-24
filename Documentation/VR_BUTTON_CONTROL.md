# 🎮 Control de Botón VR - Endpoints

## Descripción
Nuevos endpoints para controlar la grabación de voz usando el botón del control VR.

## Flujo de Funcionamiento

### 1. Presionar Botón VR
```http
POST /start_recording
```

**Descripción:** Se ejecuta cuando el usuario presiona el botón del control VR.

**Respuesta exitosa:**
```json
{
  "status": "success",
  "message": "Grabación iniciada",
  "recording_active": true
}
```

**Respuesta de error:**
```json
{
  "status": "error",
  "message": "Grabación ya activa",
  "recording_active": false
}
```

### 2. Soltar Botón VR  
```http
POST /stop_recording
```

**Descripción:** Se ejecuta cuando el usuario suelta el botón del control VR. Detiene la grabación y procesa el audio en background.

**Respuesta exitosa (INMEDIATA):**
```json
{
  "status": "processing",
  "message": "Audio en proceso, respuesta será enviada a puerto 8001",
  "recording_active": false
}
```

**Nota:** La respuesta de IA se obtiene por polling (endpoint siguiente) o push (puerto 8001).

**Respuesta sin audio:**
```json
{
  "status": "warning",
  "message": "No hay audio para procesar",
  "recording_active": false
}
```

### 3. Consultar Última Respuesta (Polling)
```http
GET /get_latest_response
```

**Descripción:** Endpoint para que VR consulte la última respuesta de IA mediante polling. Llamar cada 0.5 segundos.

**Respuesta cuando HAY respuesta nueva:**
```json
{
  "response": "Buenas tardes señor, ¿en qué le puedo ayudar?",
  "state": "NEGOTIATING",
  "conversation_finished": false,
  "has_response": true
}
```

**Respuesta cuando NO hay respuesta nueva:**
```json
{
  "response": "",
  "state": "waiting",
  "conversation_finished": false,
  "has_response": false
}
```

### 4. Estado de Grabación
```http
GET /recording_status
```

**Descripción:** Consultar el estado actual de la grabación (útil para debugging).

**Respuesta:**
```json
{
  "recording_active": false,
  "microphone_id": 8,
  "sample_rate": 16000,
  "channels": 1,
  "conversation_state": "NEGOTIATING"
}
```

## Implementación en VR

### Unity C# - Ejemplo de uso

```csharp
using UnityEngine;
using UnityEngine.Networking;
using System.Collections;

public class VRButtonController : MonoBehaviour
{
    private string serverURL = "http://127.0.0.1:8000";
    private bool isRecording = false;

    void Update()
    {
        // Detectar presión del botón (ajustar según tu input)
        if (OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger))
        {
            StartRecording();
        }
        
        if (OVRInput.GetUp(OVRInput.Button.PrimaryIndexTrigger))
        {
            StopRecording();
        }
    }

    private void StartRecording()
    {
        if (!isRecording)
        {
            StartCoroutine(CallStartRecording());
        }
    }

    private void StopRecording()
    {
        if (isRecording)
        {
            StartCoroutine(CallStopRecording());
        }
    }

    private IEnumerator CallStartRecording()
    {
        UnityWebRequest request = UnityWebRequest.Post($"{serverURL}/start_recording", "");
        yield return request.SendWebRequest();

        if (request.result == UnityWebRequest.Result.Success)
        {
            isRecording = true;
            Debug.Log("🎙️ Grabación iniciada");
            // Aquí puedes mostrar indicador visual de grabación
        }
    }

    private IEnumerator CallStopRecording()
    {
        UnityWebRequest request = UnityWebRequest.Post($"{serverURL}/stop_recording", "");
        yield return request.SendWebRequest();

        if (request.result == UnityWebRequest.Result.Success)
        {
            isRecording = false;
            string responseText = request.downloadHandler.text;
            
            // Procesar respuesta JSON
            VoiceResponse response = JsonUtility.FromJson<VoiceResponse>(responseText);
            
            Debug.Log($"Transcripción: {response.transcription}");
            Debug.Log($"Respuesta IA: {response.response}");
            
            // Aquí puedes animar el lip sync del avatar
            AnimateLipSync(response.response);
        }
    }
}

[System.Serializable]
public class VoiceResponse
{
    public string transcription;
    public string response;
    public string state;
    public bool conversation_finished;
    public bool conversation_negotiation_cancel;
    public bool audio_generated;
}
```

## Ventajas del Sistema por Botón

✅ **Control preciso:** El usuario decide exactamente cuándo hablar  
✅ **Sin ruido ambiente:** No hay grabación continua  
✅ **Menor latencia:** Inicia y detiene inmediatamente  
✅ **Más natural:** Como un walkie-talkie  
✅ **Menos procesamiento:** Solo graba cuando necesario  

## Comparación con Grabación Automática

| Característica | Automática | Por Botón |
|---|---|---|
| Control de usuario | Detecta voz automáticamente | Usuario controla cuando hablar |
| Ruido ambiente | Puede captar ruido | Solo graba cuando se presiona |
| Latencia | VAD + detección silencio | Inmediata |
| Procesamiento | Continuo | Solo cuando necesario |
| Precisión | Dependiente de VAD | 100% controlada por usuario |

## Notas Técnicas

- **Sample Rate:** 16000 Hz (óptimo para STT)
- **Canales:** 1 (mono)
- **Formato:** float32 -> int16 para transcripción
- **Micrófono:** Auto-detecta VR o usa sistema
- **Thread Safety:** Variables globales protegidas
- **Cleanup:** Archivos temporales se eliminan automáticamente