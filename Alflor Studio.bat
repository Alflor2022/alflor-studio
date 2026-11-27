@echo off
rem Arranque do Alflor Studio. Na primeira vez cria o ambiente Python e instala as dependências.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo A preparar o Alflor Studio pela primeira vez...
    py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" -m ptz_studio %*
