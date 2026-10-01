@echo off
setlocal
title Campaign Studio
set "ROOT=%~dp0"
if not defined STUDIO_PORT set "STUDIO_PORT=8765"
set "PATH=%ROOT%runtime\node;%ROOT%runtime\python;%PATH%"

rem First real run: leave a desktop shortcut for next time. Headless runs
rem (STUDIO_NO_BROWSER set, used by the build's smoke tests) skip this.
if defined STUDIO_NO_BROWSER goto :boot
if exist "%ROOT%runtime\.shortcut-created" goto :boot
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Campaign Studio.lnk'); $s.TargetPath='%ROOT%launch.cmd'; $s.WorkingDirectory='%ROOT%'; $s.IconLocation='%ROOT%runtime\python\python.exe,0'; $s.Save()" >nul 2>&1
type nul > "%ROOT%runtime\.shortcut-created" 2>nul

:boot
echo.
echo   Campaign Studio
echo   URL: http://127.0.0.1:%STUDIO_PORT%  (your browser will open)
echo   Close THIS window to stop the Studio.
echo.
cd /d "%ROOT%studio"
if defined STUDIO_NO_BROWSER (
  "%ROOT%runtime\python\python.exe" server.py
) else (
  "%ROOT%runtime\python\python.exe" run.py
)
echo.
if defined STUDIO_NO_BROWSER goto :done
echo Studio stopped. Press any key to close.
pause >nul
:done
