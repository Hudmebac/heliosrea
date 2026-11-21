@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Helios launcher

set "URL=http://127.0.0.1:4700/"
set "PY="

rem --- find Python ---------------------------------------------------------
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo.
  echo   Helios needs Python 3 on this PC.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

rem --- already running? ----------------------------------------------------
netstat -ano | findstr ":4700" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo   Helios is already running.
  goto :open
)

rem --- build the galaxy if it does not exist yet ---------------------------
if not exist "viewer\galaxy.json" (
  echo   Building your knowledge galaxy...
  %PY% build.py
  if errorlevel 1 (
    echo   Build failed - see the message above.
    if /i not "%~1"=="nobrowser" pause
    exit /b 1
  )
)

rem --- wake the free local brain -------------------------------------------
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto :brainup
where ollama >nul 2>nul
if errorlevel 1 goto :brainup
echo   Waking Ollama...
start "Ollama" /min ollama serve
ping -n 3 127.0.0.1 >nul
:brainup

rem --- start the server and keep watching the notes ------------------------
echo   Starting Helios on port 4700...
start "Helios server" /min cmd /c "%PY% -u server.py > server.log 2>&1"
start "Helios notes watcher" /min cmd /c "%PY% -u build.py --watch 60 > watch.log 2>&1"

set /a tries=0
:wait
set /a tries+=1
netstat -ano | findstr ":4700" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto :open
if %tries% GEQ 20 (
  echo.
  echo   Helios did not come up. Open server.log in this folder for the error.
  echo.
  if /i not "%~1"=="nobrowser" pause
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :wait

:open
echo   Galaxy ready at %URL%
if /i "%~1"=="nobrowser" exit /b 0
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
  start "" "%CHROME%" "%URL%"
) else (
  start "" "%URL%"
)
exit /b 0
