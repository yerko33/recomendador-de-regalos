@echo off
title RegalaCL - Servidor Local
cd /d "%~dp0"
echo Iniciando RegalaCL en http://localhost:8000 ...
start "" http://localhost:8000
python server.py
pause
