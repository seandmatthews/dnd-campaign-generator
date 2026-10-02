@echo off
setlocal
rem The setup exe extracts its payload to a temp dir and runs this script
rem from there (%~dp0). Mirror the payload into the real install home,
rem preserving the campaigns/ data across version updates, then hand off
rem to launch.cmd there. CAMPAIGN_STUDIO_HOME overrides the install home
rem (used by the build's own smoke tests).

set "DEST=%LOCALAPPDATA%\Campaign Studio"
if defined CAMPAIGN_STUDIO_HOME set "DEST=%CAMPAIGN_STUDIO_HOME%"
set "SRC=%~dp0"

rem Already installed at this exact version? Just start it.
if exist "%DEST%\VERSION" if exist "%DEST%\launch.cmd" (
  fc /b "%DEST%\VERSION" "%SRC%VERSION" >nul 2>&1
  if not errorlevel 1 (
    echo [%DATE% %TIME%] fast path: same version already installed >> "%DEST%\install-log.txt"
    start "" "%DEST%\launch.cmd"
    exit /b 0
  )
)

echo.
echo   Installing Campaign Studio into:
echo     %DEST%
echo   Copying the program files - once per version, please wait...
echo.
rem /XD with the FULL source path excludes exactly the top-level data dir
rem (a bare "campaigns" name would exclude same-named dirs at ANY depth,
rem so a future nested campaigns/ would silently stop mirroring), and it
rem still keeps /MIR from ever touching the installed data dir.
robocopy "%SRC%." "%DEST%" /MIR /XD "%SRC%campaigns" /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul
echo [%DATE% %TIME%] robocopy exit %ERRORLEVEL% >> "%DEST%\install-log.txt"
if errorlevel 8 (
  echo   Some files could not be copied ^(exit %ERRORLEVEL%^).
  echo   Trying to start anyway - if the Studio does not come up, close
  echo   everything and run the installer again.
)
rem the mirror never touches the data dir: seed it on first install only.
if not exist "%DEST%\campaigns" robocopy "%SRC%campaigns" "%DEST%\campaigns" /E /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul

if not exist "%DEST%\launch.cmd" (
  echo   Install incomplete: launch.cmd missing from "%DEST%".
  if not defined STUDIO_NO_BROWSER pause
  exit /b 1
)
echo [%DATE% %TIME%] handing off to launch.cmd >> "%DEST%\install-log.txt"
start "" "%DEST%\launch.cmd"
exit /b 0
