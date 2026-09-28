@echo off
cd /d "%~dp0"
python launch_platform.py
if errorlevel 1 pause
