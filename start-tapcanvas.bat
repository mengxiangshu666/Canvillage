@echo off
rem ASCII-only wrapper: the PowerShell launcher handles the Unicode product path.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-tapcanvas.ps1" %*
exit /b %errorlevel%
