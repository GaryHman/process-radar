@echo off
rem Removes the Process Radar shortcuts on THIS PC (Start Menu, Startup, Windows desktop).
rem Does not delete the app folder or %APPDATA%\ProcessRadar (settings + log).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0shortcut.ps1" -Folder Programs -Remove
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0shortcut.ps1" -Folder Startup -Remove
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0shortcut.ps1" -Folder Desktop -Remove
pause
