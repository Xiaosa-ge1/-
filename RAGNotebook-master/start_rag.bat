@echo off
setlocal
title RAG NoteBook - one-click startup
rem ============================================================
rem  RAG NoteBook one-click startup
rem  - Idempotent: already-running services are skipped
rem  - Put a shortcut to this file in shell:startup to auto-start
rem  - Remove the last line (browser open) if you don't want it
rem ============================================================

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONT_DIR=%ROOT%front"
set "REDIS_DIR=E:\Redis-7.4.3-Windows-x64-msys2\Redis-7.4.3-Windows-x64-msys2"
set "REDIS_BIN=%REDIS_DIR%\redis-server.exe"

rem System32 tools (full path, immune to PATH hijack)
set "TASKLIST=%SystemRoot%\System32\tasklist.exe"
set "FIND=%SystemRoot%\System32\find.exe"
set "NETSTAT=%SystemRoot%\System32\netstat.exe"
set "FINDSTR=%SystemRoot%\System32\findstr.exe"
set "PING=%SystemRoot%\System32\ping.exe"

echo ==========================================
echo   RAG NoteBook - one-click startup
echo ==========================================

rem ---- 1) Redis ----
"%TASKLIST%" /fi "imagename eq redis-server.exe" 2>nul | "%FIND%" /i "redis-server.exe" >nul
if errorlevel 1 (
    echo [1/3] Starting Redis...
    cd /d "%REDIS_DIR%"
    start "RAG-Redis" /min "%REDIS_BIN%" redis.conf
    cd /d "%ROOT%"
) else (
    echo [1/3] Redis already running.
)

rem ---- 2) Backend (port 8000) ----
"%NETSTAT%" -ano | "%FINDSTR%" ":8000 " | "%FINDSTR%" /i "LISTENING" >nul
if errorlevel 1 (
    echo [2/3] Starting Backend uvicorn on 8000...
    start "RAG-Backend" /min cmd /c "cd /d %BACKEND_DIR% && uv run uvicorn main:app --host 127.0.0.1 --port 8000"
) else (
    echo [2/3] Backend already running.
)

rem ---- 3) Frontend (port 3000) ----
"%NETSTAT%" -ano | "%FINDSTR%" ":3000 " | "%FINDSTR%" /i "LISTENING" >nul
if errorlevel 1 (
    echo [3/3] Starting Frontend vite on 3000...
    start "RAG-Frontend" /min cmd /c "cd /d %FRONT_DIR% && npm run dev"
) else (
    echo [3/3] Frontend already running.
)

echo.
echo All services ready.
"%PING%" -n 3 127.0.0.1 >nul
start "" http://localhost:3000
exit /b 0
