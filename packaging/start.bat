@echo off
rem NetVision をアプリのウィンドウで起動する（Windows）。ウィンドウを閉じるとサーバーも止まります。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\dist
cd backend
"%~dp0.venv\Scripts\python" -m scripts.launcher %*
