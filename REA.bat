@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title REA - Reverse Engineer Anything

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   REA needs Python 3 on PATH.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

python rea\server.py %*
if errorlevel 1 pause
