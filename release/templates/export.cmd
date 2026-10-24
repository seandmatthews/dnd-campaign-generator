@echo off
setlocal
title Campaign Studio - Export a campaign
set "ROOT=%~dp0"
rem The friend backup path (ticket 58; README.txt "Backing up"): runs the
rem export on the BUNDLED python -- no python on PATH is needed -- against
rem the same data root launch.cmd uses, so it always sees the installed
rem campaigns. Headless runs (STUDIO_NO_BROWSER set, the build's smoke
rem tests) skip the closing pause and propagate the exit code.
if not defined CAMPAIGN_DATA_ROOT set "CAMPAIGN_DATA_ROOT=%ROOT%campaign-data"
cd /d "%ROOT%studio"
echo.
echo   Campaign Studio export
echo   Writes one campaign as a zip you can copy somewhere safe.
echo   (Run it with no arguments to list your campaigns.)
echo.
"%ROOT%runtime\python\python.exe" export_campaign.py %*
set "RC=%ERRORLEVEL%"
echo.
if defined STUDIO_NO_BROWSER exit /b %RC%
echo Finished. Press any key to close.
pause >nul
exit /b %RC%
