@echo off
setlocal
rem The friend-visible updater (ticket 52). Double-click this in the
rem install root (or run it from anywhere): it asks the repo's latest
rem GitHub release, compares it to this install's stamp block, downloads
rem the app payload (a few MB), hash-verifies it against the release
rem body, snapshots the current app files, then mirrors the app surface
rem over the install. It NEVER touches campaign-data/, llm.env,
rem runtime/, or dnd-mapping\tools\ - the once-shipped packs and the
rem friend's data and key stay exactly as they are. Explicit, never
rem silent: every step prints what it is doing, and any failed check
rem keeps the current tree and says so plainly. launch.cmd NEVER calls
rem this - updating is a gesture the DM chooses.
rem
rem The native-tool chain, so a friend needs nothing installed:
rem   powershell (always present)  - query the release, parse it
rem   curl.exe (Windows 10 1803+)  - download the payload
rem   certutil (always present)    - SHA256 the download
rem   Expand-Archive via powershell- unpack (the payload is a ZIP)
rem   robocopy (always present)    - snapshot + swap
rem UPDATE_API_BASE overrides the releases/latest URL - test seam only.

set "DEST=%LOCALAPPDATA%\Campaign Studio"
if defined CAMPAIGN_STUDIO_HOME set "DEST=%CAMPAIGN_STUDIO_HOME%"
set "REPO=seandmatthews/dnd-campaign-generator"
set "API=https://api.github.com/repos/%REPO%/releases/latest"
if defined UPDATE_API_BASE set "API=%UPDATE_API_BASE%"
set "ZIP=%TEMP%\cs-update-app.zip"
set "UNPACK=%TEMP%\cs-update-app"
set "META=%TEMP%\cs-update-meta.txt"
set "BACKUP=%DEST%\.update-backup"

rem -- 1. never update over a running Studio (the install.cmd probe) --
set "PROBE_PORT=8765"
if defined STUDIO_PORT set "PROBE_PORT=%STUDIO_PORT%"
netstat -ano | findstr /C:":%PROBE_PORT% " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo   Campaign Studio looks like it is still running ^(port %PROBE_PORT%^).
  echo   Close its console window first, then run update.cmd again -
  echo   updating while it is open leaves a half-copied install.
  exit /b 1
)

rem -- 2. this must be an install with a stamp block --
if not exist "%DEST%\VERSION" (
  echo.
  echo   No Campaign Studio install found at:
  echo     %DEST%
  echo   Run the full installer exe first; after that, update.cmd works.
  exit /b 1
)
set "INSTALLED="
for /f "tokens=2" %%A in ('findstr /B /C:"app-release:" "%DEST%\VERSION" 2^>nul') do set "INSTALLED=%%A"
if not defined INSTALLED (
  echo.
  echo   This install predates the updater ^(no stamp block in VERSION^).
  echo   Install the latest full exe ONE more time - after that,
  echo   update.cmd handles every update by itself.
  exit /b 1
)

echo.
echo   Campaign Studio updater
echo     install : %DEST%
for /f "usebackq delims=" %%A in ("%DEST%\VERSION") do (
  echo     build   : %%A
  goto :built
)
:built
echo     release : %INSTALLED%

rem -- 3. ask the repo what is latest (no token - the repo is public) --
echo.
echo   Checking for a newer release ...
if exist "%META%" del "%META%" >nul 2>&1
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r = Invoke-RestMethod -Uri '%API%' -TimeoutSec 30; $a = $r.assets | Where-Object { $_.name -like 'CampaignStudio-app-*' } | Select-Object -First 1; if (-not $a) { throw 'the latest release carries no CampaignStudio-app-* asset' }; if ($r.body -notmatch 'sha256: ([0-9a-f]{64})') { throw 'the release body carries no parseable sha256 line' }; Write-Output ('latest: ' + $r.tag_name); Write-Output ('asset: ' + $a.browser_download_url); Write-Output ('sha256: ' + $Matches[1])" > "%META%"
if errorlevel 1 (
  echo   Could not reach the release list at:
  echo     %API%
  echo   Nothing was changed - check your connection and try again.
  exit /b 1
)
set "LATEST="
set "ASSET_URL="
set "WANT="
set "MISSING="
for /f "tokens=1,* delims=: " %%A in ('findstr /B /C:"latest: " "%META%"') do set "LATEST=%%B"
for /f "tokens=1,* delims=: " %%A in ('findstr /B /C:"asset: " "%META%"') do set "ASSET_URL=%%B"
for /f "tokens=1,* delims=: " %%A in ('findstr /B /C:"sha256: " "%META%"') do set "WANT=%%B"
if not defined LATEST set "MISSING=1"
if not defined ASSET_URL set "MISSING=1"
if not defined WANT set "MISSING=1"
if defined MISSING (
  echo   The release answer was not readable ^(latest/asset/sha256^).
  echo   Nothing was changed.
  exit /b 1
)

if "%INSTALLED%"=="%LATEST%" (
  echo   Already at %LATEST% - nothing to do.
  exit /b 0
)
echo     latest : %LATEST%

rem -- 4. the explicit gesture: no keystroke, no update --
choice /C YN /N /M "   Update to %LATEST% now? [Y/N] "
if errorlevel 2 (
  echo   Kept current. Run update.cmd again any time.
  exit /b 0
)

rem -- 5. download + hash-verify before anything on the install is
rem       touched --
echo   Downloading %LATEST% ...
if exist "%ZIP%" del "%ZIP%" >nul 2>&1
curl -fSL --max-time 600 -o "%ZIP%" "%ASSET_URL%"
if errorlevel 1 (
  echo   The download failed. Nothing was changed - the current install
  echo   is exactly as it was. Try again later.
  exit /b 1
)
set "GOT="
for /f "skip=1 delims=" %%A in ('certutil -hashfile "%ZIP%" SHA256') do (
  if not defined GOT set "GOT=%%A"
)
if not defined GOT (
  echo   Could not hash the download. Nothing was changed.
  del "%ZIP%" >nul 2>&1
  exit /b 1
)
set "GOT=%GOT: =%"
if /I not "%GOT%"=="%WANT%" (
  echo   The downloaded file does not match the release's SHA256.
  echo     expected: %WANT%
  echo     got     : %GOT%
  echo   Deleting it. Nothing was changed - this is worth reporting.
  del "%ZIP%" >nul 2>&1
  exit /b 1
)
echo   Hash verified.

rem -- 6. snapshot the current app files (the resolve-apply precedent:
rem       a failed swap must leave the previous tree findable) --
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
mkdir "%BACKUP%" 2>nul
echo   Snapshotting the current app files ...
for %%D in ("studio" "agentic-simulator" "verification" "avtt-integration" "docs") do (
  if exist "%DEST%\%%~D" (
    robocopy "%DEST%\%%~D" "%BACKUP%\%%~D" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
      echo   Could not snapshot %%~D - nothing was changed.
      exit /b 1
    )
  )
)
if exist "%DEST%\dnd-mapping" (
  robocopy "%DEST%\dnd-mapping" "%BACKUP%\dnd-mapping" /E /XD "%DEST%\dnd-mapping\tools" /R:2 /W:2 /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo   Could not snapshot dnd-mapping - nothing was changed.
    exit /b 1
  )
)
for %%F in (VERSION launch.cmd install.cmd update.cmd export.cmd README.txt llm.env.example) do (
  if exist "%DEST%\%%F" copy /y "%DEST%\%%F" "%BACKUP%\%%F" >nul
)

rem -- 7. unpack + mirror the app surface. /XD with FULL paths excludes
rem       exactly the once-shipped packs and the data dir (a bare name
rem       would exclude same-named dirs at ANY depth - the install.cmd
rem       lesson). campaign-data is excluded on BOTH sides: the payload
rem       normally carries only an empty seed, and the dest-side exclude
rem       is what guarantees the friend's campaigns survive even a
rem       payload that carries no seed at all (found by the updater's
rem       own end-to-end test). /XF keeps the friend's key file and the
rem       installer's log. Everything else on the dest side that the
rem       payload does not carry is DELETED - that is the /MIR
rem       contract, and the snapshot answers it.
echo   Swapping in %LATEST% ...
if exist "%UNPACK%" rmdir /s /q "%UNPACK%"
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%UNPACK%' -Force"
if errorlevel 1 (
  echo   Could not unpack the payload. Nothing was changed.
  exit /b 1
)
robocopy "%UNPACK%" "%DEST%" /MIR /XD "%UNPACK%\campaign-data" "%DEST%\campaign-data" "%DEST%\campaigns" "%DEST%\runtime" "%DEST%\dnd-mapping\tools" "%BACKUP%" /XF llm.env install-log.txt /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul
echo [%DATE% %TIME%] update robocopy exit %ERRORLEVEL% (target %LATEST%) >> "%DEST%\install-log.txt"
if errorlevel 8 (
  echo   The swap did not finish cleanly ^(robocopy exit %ERRORLEVEL%^).
  echo   Your previous app files are kept in:
  echo     %BACKUP%
  echo   Close every Studio window and run update.cmd again - a clean
  echo   run rebuilds the snapshot and swaps again.
  exit /b 1
)

rem -- 8. stamp completion like install.cmd does, clean up, report --
copy /y "%DEST%\VERSION" "%DEST%\.install-complete" >nul
rmdir /s /q "%BACKUP%"
del "%ZIP%" >nul 2>&1
rmdir /s /q "%UNPACK%"
del "%META%" >nul 2>&1
echo [%DATE% %TIME%] update complete - %LATEST% >> "%DEST%\install-log.txt"
echo.
echo   Updated to %LATEST%. Start Campaign Studio from the desktop
echo   shortcut - your campaigns and key were never touched.
exit /b 0
