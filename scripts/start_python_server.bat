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

REM Verificar si existe el entorno virtual
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] No se encontro el entorno virtual .venv
    echo [!] Ejecuta: python -m venv .venv
    echo [!] Luego: .venv\Scripts\activate
    echo [!] Y: pip install -r requirements.txt
    pause
    exit /b 1
)

REM Activar entorno virtual
echo [*] Activando entorno virtual...
call .venv\Scripts\activate.bat

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
