@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m compbot stats
if errorlevel 1 pause
