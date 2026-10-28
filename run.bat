@echo off
rem Process Radar launcher: finds a Python that has PySide6 + psutil and starts the app with
rem pythonw.exe (no console window). Never uses a .venv (the folder is synced between PCs).
rem If Process Radar is already running, the new copy only brings the open window to the front.
rem
rem Launcher messages go to %APPDATA%\ProcessRadar\launcher.log - deliberately NOT radar.log: the
rem running app keeps radar.log open, and cmd cannot redirect into a file another process has open.
rem (Before 23 Oct 2026 that made every Python look broken whenever the app was already running.)
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "APP=%~dp0process_radar.py"
set "LOGDIR=%APPDATA%\ProcessRadar"
if defined PR_DATA_DIR set "LOGDIR=%PR_DATA_DIR%"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" 2>nul
set "LOG=%LOGDIR%\launcher.log"
set "PYW="
set "TRIED=|"

call :try "C:\Python314\python.exe"
if not defined PYW for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do call :try "%%P"
if not defined PYW for /f "usebackq delims=" %%P in (`where python 2^>nul`) do call :try "%%P"
if not defined PYW for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%D\python.exe"

rem PR_LAUNCH_DRYRUN=1 (used by the tests): only report which Python would be used, start nothing
if defined PR_LAUNCH_DRYRUN (
  if defined PYW (echo PYTHON=!PYW!) else (echo PYTHON=none)
  echo TRIED=!TRIED!
  exit /b 0
)
if defined PYW (
  call :log INFO starting "!PYW!" "!APP!"
  start "" "!PYW!" "!APP!" %*
  exit /b 0
)

call :log ERROR no Python with PySide6 + psutil found. Checked: !TRIED!
set "PR_TRIED=!TRIED!"
set "PR_LOG=%LOG%"
powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; $n=[Environment]::NewLine; $t=(($env:PR_TRIED -split '\|') | Where-Object { $_ } | ForEach-Object { '    ' + $_ }) -join $n; if (-not $t) { $t='    (no Python installation was found)' }; [void][System.Windows.MessageBox]::Show('Process Radar needs Python 3 with the PySide6 and psutil packages, but none of the Python installations on this PC has them.' + $n + $n + 'Checked:' + $n + $t + $n + $n + 'To fix this, open a terminal and run:' + $n + '    py -m pip install --user psutil PySide6-Essentials' + $n + $n + 'Details: ' + $env:PR_LOG, 'Process Radar', 'OK', 'Warning')"
exit /b 1

:try
if defined PYW exit /b 0
set "CAND=%~f1"
if not exist "!CAND!" exit /b 0
if not "!CAND:\WindowsApps\=!"=="!CAND!" exit /b 0
if not "!CAND:\.venv\=!"=="!CAND!" exit /b 0
rem the same python.exe is usually found 2-3 times (fixed path, py -3, where python): test it once
if not "!TRIED:|%~f1|=!"=="!TRIED!" exit /b 0
set "TRIED=!TRIED!!CAND!|"
"!CAND!" -c "import PySide6.QtWidgets, psutil" >nul 2>nul && goto :try_ok
call :log WARN "!CAND!" cannot import PySide6/psutil - skipped
exit /b 0
:try_ok
set "W=%~dp1pythonw.exe"
if exist "!W!" (set "PYW=!W!") else (set "PYW=!CAND!")
exit /b 0

:log
>>"%LOG%" echo %date% %time% %* 2>nul
exit /b 0
