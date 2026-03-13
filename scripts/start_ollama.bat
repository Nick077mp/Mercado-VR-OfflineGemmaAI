@echo off
REM ==================================================
REM Script para iniciar servidor Ollama
REM Verifica si está corriendo y espera confirmación
REM ==================================================

REM Cambiar al directorio raiz del proyecto (padre de scripts/)
cd /d "%~dp0\.."

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
REM MODO GPU HIBRIDO - Compatible con VR
REM ==========================================
REM CUDA_VISIBLE_DEVICES=0 usa la NVIDIA RTX (GPU 0)
set CUDA_VISIBLE_DEVICES=0
REM OLLAMA_NUM_GPU se controla desde ollama_service.py (perfil hibrido)
REM OLLAMA_FLASH_ATTENTION=1 optimiza uso de VRAM
set OLLAMA_FLASH_ATTENTION=1
REM OLLAMA_MAX_LOADED_MODELS=1 solo 1 modelo cargado (libera VRAM)
set OLLAMA_MAX_LOADED_MODELS=1
REM OLLAMA_KEEP_ALIVE=10m descarga modelo de VRAM tras 10 min inactivo
set OLLAMA_KEEP_ALIVE=10m

echo [*] Modo GPU Hibrido activado (compatible con VR)
echo.

REM Iniciar Ollama en segundo plano
start "Ollama Server (Hybrid Mode)" cmd /k "set CUDA_VISIBLE_DEVICES=0 && set OLLAMA_FLASH_ATTENTION=1 && set OLLAMA_MAX_LOADED_MODELS=1 && set OLLAMA_KEEP_ALIVE=10m && ollama serve"

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

:END
echo.
echo Presiona cualquier tecla para continuar...
pause >nul
