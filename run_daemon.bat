@echo off
cd /d "%~dp0"
title FnOQuantAI Daemon
python main.py daemon
pause
