@echo off
cd /d "%~dp0"
title FnOQuantAI Web Dashboard
echo Starting FnOQuantAI Dashboard at http://localhost:8001 ...
python serve_dashboard.py
pause
