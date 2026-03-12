@echo off
REM ==================================================
REM Script para iniciar servidor Ollama
REM Verifica si está corriendo y espera confirmación
REM ==================================================

echo ========================================
echo Iniciando Servidor Ollama
echo ========================================

REM Verificar si Ollama ya está corriendo
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Ollama ya esta corriendo
    echo ========================================
    goto :END
)

echo [*] Ollama no detectado, iniciando...
echo.

REM ==========================================
REM FORZAR MODO CPU - Liberar VRAM para VR
REM ==========================================
REM CUDA_VISIBLE_DEVICES vacio oculta todas las GPUs de Ollama
set CUDA_VISIBLE_DEVICES=
REM OLLAMA_NUM_GPU=0 indica 0 capas en GPU (todo en CPU)
set OLLAMA_NUM_GPU=0

echo [*] Modo CPU activado (GPU libre para VR)
echo.

REM Iniciar Ollama en segundo plano
start "Ollama Server (CPU Mode)" cmd /k "set CUDA_VISIBLE_DEVICES= && set OLLAMA_NUM_GPU=0 && ollama serve"

REM Esperar a que Ollama esté listo
echo [*] Esperando a que Ollama este listo...
timeout /t 3 /nobreak >nul

REM Health check (intentar hasta 10 veces)
set /a counter=0
:WAIT_LOOP
set /a counter+=1
if %counter% GTR 10 (
    echo [ERROR] Timeout esperando Ollama
    echo [!] Verifica que Ollama este instalado correctamente
    pause
    exit /b 1
)

curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Intento %counter%/10...
    timeout /t 2 /nobreak >nul
    goto :WAIT_LOOP
)

echo [OK] Ollama servidor esta corriendo!
echo [OK] Disponible en: http://localhost:11434
echo ========================================
echo.

REM ==========================================
REM WARMUP - Precargar modelo en RAM
REM ==========================================
echo [*] Precargando modelo gemma3:4b en RAM (warmup)...
echo [*] Esto puede tardar 15-20 segundos la primera vez...
curl -s -X POST http://localhost:11434/api/generate -d "{\"model\": \"gemma3:4b\", \"prompt\": \"Hola\", \"stream\": false, \"options\": {\"num_predict\": 1, \"num_ctx\": 4096, \"num_gpu\": 0}, \"keep_alive\": \"30m\"}" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Modelo precargado y listo en RAM
) else (
    echo [!] Warmup fallo - la primera interaccion sera mas lenta
)
echo ========================================

:END
echo.
echo Presiona cualquier tecla para continuar...
pause >nul
