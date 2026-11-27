@echo off
rem Instala o Alflor Studio para este utilizador (sem privilégios de administrador).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
pause
