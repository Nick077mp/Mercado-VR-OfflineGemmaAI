@echo off
REM ==================================================
REM Script para apagar todos los servidores
REM ==================================================
title Sistema IA de Voz - Apagando...
color 0E

echo.
echo  ============================================
echo    APAGANDO SISTEMA DE IA DE VOZ
echo  ============================================
echo.

REM Detener servidor Python (uvicorn)
echo  [*] Deteniendo servidor de voz (Python)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo  [OK] Servidor de voz detenido

REM Detener Ollama
echo  [*] Deteniendo servidor de IA (Ollama)...
taskkill /F /IM ollama.exe >nul 2>&1
taskkill /F /IM "ollama app.exe" >nul 2>&1
echo  [OK] Servidor de IA detenido

REM Cerrar ventanas de cmd asociadas
echo  [*] Cerrando ventanas de servidores...
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq Ollama - Servidor IA*" /NH 2^>nul ^| findstr cmd') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq Python - Servidor de Voz*" /NH 2^>nul ^| findstr cmd') do (
    taskkill /F /PID %%a >nul 2>&1
)

color 0A
echo.
echo  ============================================
echo    SISTEMA APAGADO CORRECTAMENTE
echo  ============================================
echo.
echo  Puedes cerrar esta ventana.
echo.
pause >nul
