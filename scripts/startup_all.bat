@echo off
REM ==================================================
REM Script maestro para iniciar todos los servidores
REM 1. Servidor Ollama (IA)
REM 2. Servidor Python (API de voz)
REM ==================================================
title Sistema IA de Voz - Iniciando...
color 0A

echo.
echo  ============================================
echo    SISTEMA DE IA DE VOZ PARA VR
echo    Iniciando servidores automaticamente...
echo  ============================================
echo.

REM Cambiar al directorio raiz del proyecto (padre de scripts/)
cd /d "%~dp0\.."

REM ==========================================
REM VERIFICACION DE PREREQUISITOS
REM ==========================================
echo  [VERIFICANDO] Revisando requisitos del sistema...
echo.

REM -- Verificar curl --
where curl >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] No se encontro "curl" en el sistema.
    echo          Se requiere Windows 10 version 1803 o superior.
    goto :ERROR_EXIT
)

REM -- Verificar Ollama instalado --
where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] Ollama no esta instalado.
    echo.
    echo          Descargalo de: https://ollama.com/download
    echo          Instala, reinicia tu PC, y ejecuta este script de nuevo.
    goto :ERROR_EXIT
)
echo  [OK] Ollama instalado

REM -- Verificar Python instalado --
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] Python no esta instalado.
    echo.
    echo          Descargalo de: https://www.python.org/downloads/
    echo          Marca "Add Python to PATH" al instalar.
    goto :ERROR_EXIT
)
echo  [OK] Python instalado

REM -- Verificar entorno virtual --
set "VENV_DIR="
if defined VIRTUAL_ENV (
    set "VENV_DIR=%VIRTUAL_ENV%"
    goto :VENV_OK
)
for %%D in (.venv venv env .env) do (
    if exist "%%D\Scripts\activate.bat" (
        set "VENV_DIR=%%D"
        goto :VENV_OK
    )
)
echo  [ERROR] No se encontro el entorno virtual de Python.
echo.
echo          Contacta al equipo de soporte tecnico.
echo          (Referencia: falta carpeta venv con dependencias)
goto :ERROR_EXIT

:VENV_OK
echo  [OK] Entorno virtual: %VENV_DIR%
echo.

REM ==========================================
REM PASO 1: Iniciar Ollama
REM ==========================================
echo  [PASO 1/2] Iniciando servidor de IA (Ollama)...

REM Verificar si Ollama ya esta corriendo
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo  [OK] Ollama ya estaba activo
    goto :PYTHON_SERVER
)

REM Modo GPU hibrido compatible con VR
start "Ollama - Servidor IA (NO CERRAR)" /min cmd /k "title Ollama - Servidor IA (NO CERRAR) && set CUDA_VISIBLE_DEVICES=0 && set OLLAMA_FLASH_ATTENTION=1 && set OLLAMA_MAX_LOADED_MODELS=1 && set OLLAMA_KEEP_ALIVE=10m && ollama serve"

echo  [*] Esperando a que Ollama este listo...
timeout /t 5 /nobreak >nul

set /a counter=0
:OLLAMA_WAIT
set /a counter+=1
if %counter% GTR 20 (
    echo.
    echo  [ERROR] Ollama no responde despues de 40 segundos.
    echo          Puede que otro programa este usando el puerto 11434.
    echo          Reinicia tu PC e intenta de nuevo.
    goto :ERROR_EXIT
)

curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [*] Conectando con Ollama... %counter%/20
    timeout /t 2 /nobreak >nul
    goto :OLLAMA_WAIT
)

echo  [OK] Ollama listo
echo.

REM ==========================================
REM PASO 2: Iniciar servidor Python
REM ==========================================
:PYTHON_SERVER
echo  [PASO 2/2] Iniciando servidor de voz (Python)...

REM Verificar si ya esta corriendo
curl -s http://localhost:8000/ >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo  [OK] Servidor de voz ya estaba activo
    goto :ALL_READY
)

REM Iniciar en ventana separada minimizada
start "Python - Servidor de Voz (NO CERRAR)" /min cmd /k "title Python - Servidor de Voz (NO CERRAR) && cd /d %CD% && call %VENV_DIR%\Scripts\activate.bat && python api_server.py"

echo  [*] Esperando a que el servidor de voz este listo...
timeout /t 5 /nobreak >nul

set /a pycounter=0
:PYTHON_WAIT
set /a pycounter+=1
if %pycounter% GTR 15 (
    echo.
    echo  [ERROR] El servidor de voz no responde.
    echo          Puede que falten dependencias de Python.
    echo          Contacta al equipo de soporte tecnico.
    goto :ERROR_EXIT
)

curl -s http://localhost:8000/ >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [*] Iniciando servidor de voz... %pycounter%/15
    timeout /t 2 /nobreak >nul
    goto :PYTHON_WAIT
)

echo  [OK] Servidor de voz listo
echo.

REM ==========================================
REM TODO LISTO
REM ==========================================
:ALL_READY
title Sistema IA de Voz - ACTIVO
color 0A
echo.
echo  ============================================
echo       SISTEMA LISTO - TODO FUNCIONANDO
echo  ============================================
echo.
echo    Servidor IA   : ACTIVO
echo    Servidor Voz  : ACTIVO
echo.
echo  ============================================
echo    Ya puedes abrir la aplicacion de VR.
echo.
echo    IMPORTANTE:
echo    - NO cierres esta ventana
echo    - NO cierres las ventanas minimizadas
echo    - Para apagar todo, ejecuta: apagar_sistema.bat
echo  ============================================
echo.
echo  Presiona cualquier tecla para cerrar SOLO esta ventana.
echo  (Los servidores seguiran funcionando en segundo plano)
pause >nul
exit /b 0

REM ==========================================
REM SALIDA POR ERROR
REM ==========================================
:ERROR_EXIT
title Sistema IA de Voz - ERROR
color 0C
echo.
echo  ============================================
echo    El sistema no pudo iniciarse.
echo    Revisa los mensajes de arriba.
echo  ============================================
echo.
pause
exit /b 1
