@echo off
rem NetVision を起動する（Windows）。閉じるとサーバも止まります。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\dist
cd backend
start "" http://127.0.0.1:8000
"%~dp0.venv\Scripts\python" -m scripts.standalone %*
