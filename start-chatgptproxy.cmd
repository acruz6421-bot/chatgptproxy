@echo off
setlocal
title ChatGPTProxy
cd /d "%~dp0"
echo Liberando a porta 3500 (instancias antigas)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0free-port.ps1" -Port 3500
echo.
echo Starting ChatGPTProxy on http://localhost:3500 ...
echo (Feche esta janela para parar o proxy.)
echo.
python app.py
echo.
echo ChatGPTProxy parou. Pressione qualquer tecla para fechar.
pause >nul
