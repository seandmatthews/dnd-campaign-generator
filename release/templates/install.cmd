@echo off
setlocal
rem The setup exe extracts its payload to a temp dir and runs this script
rem from there (%~dp0). Mirror the payload into the real install home,
rem preserving the campaign-data/ dir (campaigns, engine home, logs, scratch)
rem across version updates, then hand off to launch.cmd there.
rem CAMPAIGN_STUDIO_HOME overrides the install home (used by the build's own
rem smoke tests).

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
rem Upgrade from a pre-campaign-data install (ticket 35): carry the old
rem campaigns\ into the new data home ONCE, visibly, BEFORE the mirror -
rem /MIR would otherwise delete it (it is not in the payload anymore).
if exist "%DEST%\campaigns" if not exist "%DEST%\campaign-data\campaigns" (
  echo   Moving your campaigns into %DEST%\campaign-data ...
  if not exist "%DEST%\campaign-data" mkdir "%DEST%\campaign-data"
  move "%DEST%\campaigns" "%DEST%\campaign-data\campaigns" >nul
  if errorlevel 1 (
    echo   Could not move the old campaigns\ dir ^(a Studio window may be
    echo   running^). It is left in place; close the Studio and run
    echo     python studio\doctor.py --migrate-data-root
    echo   from the install dir to finish the move.
  )
  echo [%DATE% %TIME%] legacy campaigns\ migration attempted >> "%DEST%\install-log.txt"
)
rem /XD with the FULL source path excludes exactly the top-level data dir
rem (a bare "campaign-data" name would exclude same-named dirs at ANY depth,
rem so a nested campaign-data/ would silently stop mirroring), and it still
rem keeps /MIR from ever touching the installed data dir. The old
rem campaigns\ dest path stays excluded too: if the move above could not
rem run, the mirror must not destroy it.
robocopy "%SRC%." "%DEST%" /MIR /XD "%SRC%campaign-data" "%DEST%campaigns" /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul
echo [%DATE% %TIME%] robocopy exit %ERRORLEVEL% >> "%DEST%\install-log.txt"
if errorlevel 8 (
  echo   Some files could not be copied ^(exit %ERRORLEVEL%^).
  echo   Trying to start anyway - if the Studio does not come up, close
  echo   everything and run the installer again.
)
rem the mirror never touches the data dir: seed it on first install only.
if not exist "%DEST%\campaign-data" robocopy "%SRC%campaign-data" "%DEST%\campaign-data" /E /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul

if not exist "%DEST%\launch.cmd" (
  echo   Install incomplete: launch.cmd missing from "%DEST%".
  if not defined STUDIO_NO_BROWSER pause
  exit /b 1
)
echo [%DATE% %TIME%] handing off to launch.cmd >> "%DEST%\install-log.txt"
start "" "%DEST%\launch.cmd"
exit /b 0
