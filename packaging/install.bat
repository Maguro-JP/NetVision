@echo off
rem 閉域環境での導入（Windows）。ネットには一切繋ぎません。
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo python が見つかりません。3.11 以上を先に入れてください。
  pause & exit /b 1
)

echo ==^> 仮想環境を作ります
python -m venv .venv
if errorlevel 1 ( pause & exit /b 1 )

echo ==^> 同梱の wheel から入れます（ネット未使用）
.venv\Scripts\pip install --quiet --no-index --find-links wheels netvision-backend
if errorlevel 1 ( pause & exit /b 1 )

echo.
echo 導入できました。start.bat をダブルクリックすると起動します。
pause
