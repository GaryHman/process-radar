@echo off
rem Process Radar launcher: finds a Python that has PySide6 + psutil and starts the app with
rem pythonw.exe (no console window). Never uses a .venv (the folder is synced between PCs).
rem Everything is logged to %APPDATA%\ProcessRadar\radar.log; on failure a message box explains why.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "APP=%~dp0process_radar.py"
set "LOGDIR=%APPDATA%\ProcessRadar"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\radar.log"
set "PYW="
set "TRIED="

for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do call :try "%%P"
if not defined PYW for /f "usebackq delims=" %%P in (`where python 2^>nul`) do call :try "%%P"
if not defined PYW for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%D\python.exe"

if defined PYW (
  >>"%LOG%" echo %date% %time% INFO run.bat: starting "!PYW!" "%APP%"
  start "Process Radar" "!PYW!" "%APP%" %*
  exit /b 0
)

>>"%LOG%" echo %date% %time% ERROR run.bat: no Python with PySide6 + psutil found. Tried:!TRIED!
powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; $n=[Environment]::NewLine; [void][System.Windows.MessageBox]::Show('Process Radar could not start: no Python with PySide6 and psutil was found.'+$n+$n+'Tried: !TRIED!'+$n+$n+'Fix: open a terminal and run'+$n+'    py -m pip install --user psutil PySide6-Essentials'+$n+$n+'Log: %LOG%','Process Radar','OK','Error')"
exit /b 1

:try
if defined PYW exit /b 0
set "CAND=%~1"
if not exist "%CAND%" exit /b 0
echo %CAND% | find /i "\WindowsApps\" >nul && exit /b 0
echo %CAND% | find /i "\.venv\" >nul && exit /b 0
set "TRIED=!TRIED! %CAND%"
"%CAND%" -c "import PySide6.QtWidgets, psutil" >nul 2>>"%LOG%"
if errorlevel 1 (
  >>"%LOG%" echo %date% %time% WARN run.bat: "!CAND!" cannot import PySide6/psutil - skipped
  exit /b 0
)
set "W=%~dp1pythonw.exe"
if exist "!W!" (set "PYW=!W!") else (set "PYW=!CAND!")
exit /b 0
