@echo off
REM ==================================================
REM Script maestro para iniciar todos los servidores
REM 1. Servidor Ollama (IA)
REM 2. Servidor Python (API de voz)
REM ==================================================

echo ========================================
echo INICIO DE SISTEMA COMPLETO
echo Sistema de IA de Voz para VR
echo ========================================
echo.

REM Cambiar al directorio del script
cd /d "%~dp0"

REM ==========================================
REM PASO 1: Iniciar Ollama
REM ==========================================
echo [PASO 1/2] Iniciando servidor Ollama...
echo.

REM Verificar si Ollama ya está corriendo
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Ollama ya esta corriendo
    goto :PYTHON_SERVER
)

REM ==========================================
REM FORZAR MODO CPU - Liberar VRAM para VR
REM ==========================================
set CUDA_VISIBLE_DEVICES=
set OLLAMA_NUM_GPU=0
set OLLAMA_NUM_THREAD=6
echo [*] Modo CPU activado (GPU libre para VR)
echo [*] Hilos asignados al modelo: 6 de 8 nucleos
echo.

echo [*] Iniciando Ollama...
start "Ollama Server (CPU Mode)" cmd /k "set CUDA_VISIBLE_DEVICES= && set OLLAMA_NUM_GPU=0 && set OLLAMA_NUM_THREAD=6 && ollama serve"

REM Esperar a que Ollama esté listo
echo [*] Esperando a que Ollama este listo...
timeout /t 5 /nobreak >nul

REM Health check
set /a counter=0
:OLLAMA_WAIT
set /a counter+=1
if %counter% GTR 15 (
    echo [ERROR] Timeout esperando Ollama
    echo [!] Verifica que Ollama este instalado
    pause
    exit /b 1
)

curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Esperando Ollama... %counter%/15
    timeout /t 2 /nobreak >nul
    goto :OLLAMA_WAIT
)

echo [OK] Ollama listo en http://localhost:11434
echo.

REM ==========================================
REM WARMUP - Precargar modelo en RAM
REM ==========================================
echo [*] Precargando modelo gemma3:4b en RAM (warmup)...
echo [*] Esto puede tardar 15-20 segundos la primera vez...
curl -s -X POST http://localhost:11434/api/generate -d "{\"model\": \"gemma3:4b\", \"prompt\": \"Hola\", \"stream\": false, \"options\": {\"num_predict\": 1, \"num_ctx\": 4096, \"num_gpu\": 0, \"num_thread\": 6}, \"keep_alive\": \"30m\"}" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Modelo precargado y listo en RAM
) else (
    echo [!] Warmup fallo - la primera interaccion sera mas lenta
)
echo.

REM ==========================================
REM PASO 2: Iniciar servidor Python
REM ==========================================
:PYTHON_SERVER
echo [PASO 2/2] Iniciando servidor Python...
echo.

REM Verificar que existe el entorno virtual
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] No se encontro entorno virtual .venv
    echo.
    echo [!] Necesitas crear el entorno virtual primero:
    echo     1. python -m venv .venv
    echo     2. .venv\Scripts\activate
    echo     3. pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM Iniciar servidor Python en nueva ventana
start "Python AI Server - Puerto 8000" cmd /k "call .venv\Scripts\activate.bat && python api_server.py"

echo [OK] Servidor Python iniciado en ventana separada
echo.

REM ==========================================
REM RESUMEN
REM ==========================================
echo ========================================
echo SISTEMA INICIADO CORRECTAMENTE
echo ========================================
echo.
echo Servicios corriendo:
echo   [*] Ollama (IA)        : http://localhost:11434
echo   [*] Python API         : http://localhost:8000
echo.
echo Unreal Engine debe:
echo   [*] Enviar POST a      : http://localhost:8000
echo   [*] Escuchar en puerto : 8001
echo.
echo ========================================
echo IMPORTANTE:
echo - NO cierres las ventanas de los servidores
echo - Ahora puedes iniciar Unreal Engine
echo ========================================
echo.

pause
