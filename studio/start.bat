@echo off
cd /d "%~dp0.."
python -X utf8 studio\run.py %*
pause
