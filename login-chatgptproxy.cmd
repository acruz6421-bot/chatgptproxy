@echo off
setlocal
title ChatGPTProxy - Login
cd /d "%~dp0"

echo Limpando instancias antigas do Chromium (chatgpt_profile)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill-zombies.ps1" -ProfileName "chatgpt_profile"
echo.

echo ================================================================
echo         GESTOR DE LOGIN - CHATGPTPROXY POOL
echo ================================================================
echo.
set /p PROFILE_ID="Digite o numero da conta para logar (ex: 2, 3) ou pressione ENTER para a padrao (1): "

if "%PROFILE_ID%"=="" (
    set PROFILE_ID=1
)

echo.
echo A sessao sera salva no perfil: chatgpt_profile_%PROFILE_ID%
echo.
echo 1. Aguarde a janela do navegador abrir.
echo 2. Faca o login na sua conta do ChatGPT.
echo 3. Assim que ver a tela de chat/conversa, feche a janela ou pressione Ctrl+C aqui.
echo ================================================================
echo.

set PYTHON_PATH=python
if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe" (
    set PYTHON_PATH="%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
)
%PYTHON_PATH% login_chatgpt.py %PROFILE_ID%

echo.
echo Concluido! Pressione qualquer tecla para fechar.
pause >nul
