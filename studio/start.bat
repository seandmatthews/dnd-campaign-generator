@echo off
rem Campaign Studio boot, stranger-proofed: find a Python, offer to install
rem whatever is missing, then start. Plain cmd.exe batch - goto flow on
rem purpose (no delayed-expansion pitfalls). Keep this file CRLF.
setlocal
cd /d "%~dp0.."

rem ---- 1. find a Python (3.10+) ------------------------------------------
set "PYCMD=python"
python --version >nul 2>&1
if not errorlevel 1 goto have_python
set "PYCMD=py -3"
py -3 --version >nul 2>&1
if not errorlevel 1 goto have_python

echo Campaign Studio needs Python 3.10 or newer, and none was found.
echo.
echo   Install it with:   winget install Python.Python.3.12
echo   or download from:  https://www.python.org/downloads/
echo.
echo In the installer, tick "Add python.exe to PATH". Then open a NEW
echo terminal window and double-click studio\start.bat again.
echo.
pause
exit /b 1

:have_python
rem ---- 2. are the studio's Python packages importable? --------------------
%PYCMD% -c "import fastapi, uvicorn, yaml" >nul 2>&1
if errorlevel 1 goto need_packages
goto boot

:need_packages
echo Some Python packages Campaign Studio needs are missing or broken.
echo Expected on a fresh machine; the install takes about a minute.
set "REPLY="
set /p REPLY=Install missing Python packages now? [Y/N] 
if /i not "%REPLY%"=="Y" goto boot

%PYCMD% -m pip install -r studio\requirements.txt
if not errorlevel 1 goto boot
echo.
echo The package install failed - read the error above, fix it, and run
echo studio\start.bat again. The full check-up lives in studio\doctor.py.
pause
exit /b 1

:boot
%PYCMD% -X utf8 studio\run.py %*
pause
