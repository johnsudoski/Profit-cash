@echo off
title Profit Cash

echo.
echo  Profit Cash - Trading IA
echo  ========================
echo.

cd /d "%~dp0"

:: Tentar py launcher (mais confiavel no Windows)
where py >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
    goto :found
)

:: Tentar python direto
C:\Users\joaoe\AppData\Local\Programs\Python\Python312\python.exe --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=C:\Users\joaoe\AppData\Local\Programs\Python\Python312\python.exe
    goto :found
)

:: Tentar python no PATH (excluindo WindowsApps)
for /f "delims=" %%i in ('where python 2^>nul ^| findstr /v "WindowsApps"') do (
    set PYTHON=%%i
    goto :found
)

echo  [ERRO] Python nao encontrado.
echo  Baixe em: https://www.python.org/downloads/
pause
exit /b 1

:found
echo  Instalando dependencias...
%PYTHON% -m pip install flask flask-sock pyquotex --quiet --upgrade

echo  Iniciando... o navegador abrira automaticamente.
echo  Pressione Ctrl+C para encerrar.
echo.

%PYTHON% launcher.py
pause
