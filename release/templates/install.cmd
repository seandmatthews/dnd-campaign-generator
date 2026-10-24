@echo off
setlocal
rem The setup exe extracts its payload to a temp dir and runs this script
rem from there (%~dp0). Mirror the payload into the real install home,
rem preserving the campaign-data/ dir (campaigns, engine home, logs,
rem scratch) AND the friend's llm.env key file across version updates,
rem then hand off to launch.cmd there. CAMPAIGN_STUDIO_HOME overrides the
rem install home (used by the build's own smoke tests).

set "DEST=%LOCALAPPDATA%\Campaign Studio"
if defined CAMPAIGN_STUDIO_HOME set "DEST=%CAMPAIGN_STUDIO_HOME%"
set "SRC=%~dp0"

rem Never mirror over a running Studio (ticket 57): a live install holds
rem python.exe/node.exe locked, robocopy /MIR dies half-way through, and
rem the mixed old/new result is a broken install the NEXT run would
rem fast-path over. launch.cmd boots on STUDIO_PORT (default 8765) -
rem probe exactly that port before touching anything.
set "PROBE_PORT=8765"
if defined STUDIO_PORT set "PROBE_PORT=%STUDIO_PORT%"
netstat -ano | findstr /C:":%PROBE_PORT% " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo   Campaign Studio looks like it is still running ^(port %PROBE_PORT%^).
  echo   Close its console window first, then run the installer again -
  echo   updating while it is open leaves a half-copied install.
  echo [%DATE% %TIME%] refused: a studio is listening on port %PROBE_PORT% >> "%DEST%\install-log.txt"
  if not defined STUDIO_NO_BROWSER pause
  exit /b 1
)

rem Already installed at this exact version, with a mirror that COMPLETED
rem (ticket 57)? Just start it. The .install-complete stamp is a copy of
rem the payload's VERSION written only after robocopy exits below 8: a
rem mirror that died mid-copy can leave new bytes in VERSION but never a
rem matching stamp, so the next run of the same exe re-mirrors instead
rem of fast-pathing over the half-copied install.
if exist "%DEST%\VERSION" if exist "%DEST%\.install-complete" if exist "%DEST%\launch.cmd" (
  fc /b "%DEST%\VERSION" "%SRC%VERSION" >nul 2>&1
  if not errorlevel 1 (
    fc /b "%DEST%\.install-complete" "%SRC%VERSION" >nul 2>&1
    if not errorlevel 1 (
      echo [%DATE% %TIME%] fast path: same version already installed >> "%DEST%\install-log.txt"
      start "" "%DEST%\launch.cmd"
      exit /b 0
    )
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
rem The friend's llm.env must survive updates too, once it exists (ticket
rem 57). /XF takes the BARE name here - the exclusion forms that match
rem /XD's full-path trick do NOT work for files: robocopy ignores a full
rem DEST path (the payload's copy still overwrote the file in testing),
rem and a full SOURCE path stops the copy but not the /MIR extra-DELETION
rem when a keyless payload ships without an llm.env. The bare name
rem excludes llm.env at any depth, which is fine - the payload carries
rem exactly one, at its root - and a first install (no file yet) still
rem seeds it from the payload, because the exclusion only exists when an
rem installed copy does.
set "KEEP_KEY="
if exist "%DEST%\llm.env" set "KEEP_KEY=/XF llm.env"
robocopy "%SRC%." "%DEST%" /MIR /XD "%SRC%campaign-data" "%DEST%campaigns" %KEEP_KEY% /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul
echo [%DATE% %TIME%] robocopy exit %ERRORLEVEL% >> "%DEST%\install-log.txt"
if errorlevel 8 (
  echo   Some files could not be copied ^(exit %ERRORLEVEL%^).
  echo   Trying to start anyway - if the Studio does not come up, close
  echo   everything and run the installer again. This run is NOT marked
  echo   complete, so the next run of the same exe recopies everything.
) else (
  copy /y "%SRC%VERSION" "%DEST%\.install-complete" >nul
  echo [%DATE% %TIME%] mirror complete - completion stamp written >> "%DEST%\install-log.txt"
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
