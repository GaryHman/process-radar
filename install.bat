@echo off
rem Process Radar installer: installs the packages from requirements.txt into your user Python
rem (pip --user, no .venv) and adds a Start Menu shortcut. Safe to run again.
setlocal
cd /d "%~dp0"
echo.
echo === Process Radar installer ===
where py >nul 2>&1
if errorlevel 1 goto :nopython
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython
echo Installing dependencies ...
py -3 -m pip install --disable-pip-version-check --user -r requirements.txt
if errorlevel 1 goto :fail
py -3 -c "import PySide6.QtWidgets, psutil" || goto :fail
echo Creating Start Menu shortcut ...
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0shortcut.ps1" -Folder Programs
echo.
echo Installed. Start "Process Radar" from the Start Menu or with run.bat.
exit /b 0
:nopython
echo Python 3.11 or newer was not found - the "py" launcher is needed.
echo Install it with:   winget install Python.Python.3.12
exit /b 1
:fail
echo Installation failed. See the messages above.
exit /b 1
