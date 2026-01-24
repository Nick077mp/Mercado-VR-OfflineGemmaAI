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

REM Iniciar Ollama en segundo plano
start "Ollama Server" ollama serve

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
