@echo off
setlocal
title ChatGPTProxy
cd /d "%~dp0"
echo Liberando a porta 3535 (instancias antigas)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0free-port.ps1" -Port 3535
echo.
echo Starting ChatGPTProxy on http://localhost:3535 ...
echo (Feche esta janela para parar o proxy.)
echo.
set PYTHON_PATH=python
if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe" (
    set PYTHON_PATH="%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
)
%PYTHON_PATH% app.py
echo.
echo ChatGPTProxy parou. Pressione qualquer tecla para fechar.
pause >nul
