@echo off
rem Atalho do projeto: usa o Python do .venv sozinho, de qualquer pasta.
rem   cinedata web            (dentro da pasta do projeto)
rem   cinedata-agent\cinedata web   (da pasta de cima)
setlocal
chcp 65001 >nul
set "RAIZ=%~dp0"
set "PY=%RAIZ%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Ambiente virtual nao encontrado. Criando o .venv e instalando as dependencias...
    python -m venv "%RAIZ%.venv" || (echo Instale o Python 3.11+ e tente de novo. & exit /b 1)
    "%PY%" -m pip install -r "%RAIZ%requirements.txt" || exit /b 1
)

set PYTHONIOENCODING=utf-8
"%PY%" "%RAIZ%cinedata.py" %*
exit /b %ERRORLEVEL%
