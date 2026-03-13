@echo off
REM ==================================================
REM Script para iniciar servidor Python de IA de Voz
REM Activa entorno virtual y ejecuta api_server.py
REM ==================================================

echo ========================================
echo Iniciando Servidor Python de IA
echo ========================================

REM Cambiar al directorio raiz del proyecto (padre de scripts/)
cd /d "%~dp0\.."

REM Detectar entorno virtual automaticamente
set VENV_DIR=

REM 1. Si ya hay un entorno activo, usarlo directamente
if defined VIRTUAL_ENV (
    echo [OK] Entorno virtual ya activo: %VIRTUAL_ENV%
    set VENV_DIR=%VIRTUAL_ENV%
    goto :VENV_READY
)

REM 2. Buscar carpetas comunes de entorno virtual
for %%D in (.venv venv env .env) do (
    if exist "%%D\Scripts\activate.bat" (
        set VENV_DIR=%%D
        goto :VENV_ACTIVATE
    )
)

REM 3. No se encontro ningun entorno virtual
echo [ERROR] No se encontro entorno virtual
echo [!] Ejecuta: python -m venv .venv
echo [!] Luego: .venv\Scripts\activate
echo [!] Y: pip install -r requirements.txt
pause
exit /b 1

:VENV_ACTIVATE
echo [*] Activando entorno virtual: %VENV_DIR%
call %VENV_DIR%\Scripts\activate.bat

:VENV_READY

REM Verificar que httpx está instalado
python -c "import httpx" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [!] Instalando dependencia httpx...
    pip install httpx
)

REM Verificar que FastAPI está instalado
python -c "import fastapi" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] FastAPI no esta instalado
    echo [!] Ejecuta: pip install -r requirements.txt
    pause
    exit /b 1
)

echo [OK] Entorno virtual activado
echo [*] Iniciando servidor Python en puerto 8000...
echo.
echo ========================================
echo IMPORTANTE: 
echo - Python servidor: puerto 8000
echo - Unreal Engine debe escuchar: puerto 8001
echo ========================================
echo.

REM Iniciar servidor Python
python api_server.py

REM Si el servidor se detiene, mantener ventana abierta
echo.
echo [!] Servidor detenido
pause
