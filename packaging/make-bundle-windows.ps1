# Windows 向けの「インストール不要」バンドルを作る（PowerShell 版）。
#
# packaging/make-bundle-windows.sh と同じものを作る。あちらは Git Bash が要るが、
# こちらは Windows に最初から入っている PowerShell だけで動く。
# 作業用の端末にも何も入れずに済むよう、組み込む Python を先に取ってきて、
# その Python でライブラリを集める。手元に Python が入っている必要は無い。
#
# 使い方（ネットに繋がる端末で）:
#   powershell -ExecutionPolicy Bypass -File packaging\make-bundle-windows.ps1
#
# 画面の材料（frontend\dist）が無い場合だけ Node.js が要る。
# それも避けたい場合は、GitHub の CI が作った ZIP を落としてください。
#   docs\WINDOWS_BEGINNER.md の「一番手軽な入手方法」を参照。

[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$PyVersion = "3.11.9"
)

$ErrorActionPreference = "Stop"
# 古い既定のままだと GitHub や python.org が TLS を拒む
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Repo "dist" }
$Version = Get-Date -Format "yyyyMMdd"
$Stage = Join-Path $OutDir "NetVision-$Version-windows"

function Step($message) { Write-Host "==> $message" }

function Fetch($url, $destination) {
    if (Test-Path $destination) { return }
    try {
        Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing
    } catch {
        if (Test-Path $destination) { Remove-Item $destination -Force }
        throw @"
取得に失敗しました: $url

  社内のプロキシや通信制限で届いていない可能性があります。
  別の端末でこのファイルを落として、次の場所に置いてから
  もう一度実行してください。

    $destination
"@
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# 画面。作り置きがあればそれを使い、無いときだけ Node.js で作る
$dist = Join-Path $Repo "frontend\dist"
if (Test-Path (Join-Path $dist "index.html")) {
    Step "画面は作成済みのものを使います"
} elseif (Get-Command npm -ErrorAction SilentlyContinue) {
    Step "画面をビルドします"
    Push-Location (Join-Path $Repo "frontend")
    try {
        cmd /c "npm ci" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "npm ci に失敗しました" }
        cmd /c "npm run build" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "npm run build に失敗しました" }
    } finally { Pop-Location }
} else {
    throw @"
画面の材料がありません。

  次のどちらかを選んでください。

  A. Node.js（LTS 版）を入れて、この処理をやり直す
       https://nodejs.org/
       入れたあとは PowerShell を開き直してください。

  B. 何も入れずに済ませる
       GitHub の CI が作った完成品の ZIP を落とす方法があります。
       docs\WINDOWS_BEGINNER.md の「一番手軽な入手方法」を見てください。
"@
}

Step "組み立てます"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
foreach ($dir in "python", "lib", "backend", "frontend", "docs") {
    New-Item -ItemType Directory -Force -Path (Join-Path $Stage $dir) | Out-Null
}
Copy-Item (Join-Path $Repo "backend\app") (Join-Path $Stage "backend\app") -Recurse
Copy-Item (Join-Path $Repo "backend\scripts") (Join-Path $Stage "backend\scripts") -Recurse
# 表を作る手順（マイグレーション）も持ち込む。これが無いと、初回起動で
# DB を作れずに落ちる
Copy-Item (Join-Path $Repo "backend\alembic") (Join-Path $Stage "backend\alembic") -Recurse
Copy-Item (Join-Path $Repo "backend\alembic.ini") (Join-Path $Stage "backend\alembic.ini")
Copy-Item $dist (Join-Path $Stage "frontend\dist") -Recurse
foreach ($doc in "INSTALL.md", "OPERATIONS.md", "WINDOWS_BEGINNER.md") {
    $path = Join-Path $Repo "docs\$doc"
    if (Test-Path $path) { Copy-Item $path (Join-Path $Stage "docs") }
}
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Step "インストール不要の Python を取得します（$PyVersion）"
$pyZip = Join-Path $OutDir "python-$PyVersion-embed-amd64.zip"
Fetch "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" $pyZip
Expand-Archive -Path $pyZip -DestinationPath (Join-Path $Stage "python") -Force

$abi = ($PyVersion -split '\.')[0..1] -join ''
# 組み込み版は既定で同梱ライブラリしか見ない。
# 依存を置く lib と、アプリ本体のある backend を探索先に加える。
$pth = @"
python$abi.zip
.
..\lib
..\backend
import site
"@
Set-Content -Path (Join-Path $Stage "python\python$abi._pth") -Value $pth -Encoding ASCII
$python = Join-Path $Stage "python\python.exe"

Step "依存ライブラリを集めます"
# 手元に Python を入れずに済ませるため、いま展開した組み込み版に pip を入れて使う。
# 同じ Windows・同じバージョン向けに集まるので、取り違えが起きない。
$getPip = Join-Path $OutDir "get-pip.py"
Fetch "https://bootstrap.pypa.io/get-pip.py" $getPip
& $python $getPip --quiet --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip の準備に失敗しました" }

$packages = @(
    "fastapi", "sqlalchemy", "alembic", "pydantic", "pydantic-settings", "python-dateutil",
    "openpyxl", "python-multipart", "jinja2", "uvicorn", "tzdata"
)
& $python -m pip install --quiet --target (Join-Path $Stage "lib") --no-warn-script-location @packages
if ($LASTEXITCODE -ne 0) { throw "ライブラリの取得に失敗しました" }
Get-ChildItem (Join-Path $Stage "lib") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# pip はここで役目を終える。閉域へ持ち込むものに残す理由が無いので外す
foreach ($leftover in "python\Lib", "python\Scripts") {
    $path = Join-Path $Stage $leftover
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}

Step "起動用バッチと説明書きを作ります"
# ファイル名は ASCII にしておく。日本語名は ZIP の文字コードの扱いが
# 解凍ツールごとに違い、Windows 側で化けることがあるため。
# バッチは cmd が既定で解釈する CP932、説明書きはメモ帳が確実に読める
# UTF-8 BOM 付きで、いずれも改行は CRLF にする。
$bat = @'
@echo off
rem NetVision を起動します。インストールは不要です。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\dist
echo NetVision を起動しています...
echo.
echo   画面 : http://127.0.0.1:8000
echo   停止 : このウィンドウを閉じてください
echo.
echo   Ctrl+C を押すと、その場で止まります。
echo   そのあとに出る「バッチ ジョブを終了しますか」は、この画面を残すかどうかの
echo   確認です。N を選んでも NetVision は止まったままです。
echo.
timeout /t 2 >nul
start "" http://127.0.0.1:8000
:run
"%~dp0python\python.exe" -m scripts.standalone
echo.
echo NetVision を止めました。
choice /c YN /n /m "もう一度起動しますか (Y/N) "
if errorlevel 2 goto end
echo.
echo 起動しています...
goto run
:end
'@
$crlf = ($bat -replace "`r`n", "`n") -replace "`n", "`r`n"
[IO.File]::WriteAllBytes(
    (Join-Path $Stage "NetVision.bat"),
    [Text.Encoding]::GetEncoding(932).GetBytes($crlf))

$readme = @"
NetVision （インストール不要版）
====================================

このフォルダは、パソコンに何もインストールせずに動きます。
管理者権限は不要で、レジストリや Program Files も変更しません。

■ 使い方

  1. このフォルダを、デスクトップなど書き込める場所に置く
  2. 「NetVision.bat」をダブルクリック
  3. ブラウザが開きます（開かない場合は http://127.0.0.1:8000 ）

  止めるときは、開いた黒いウィンドウを閉じてください。
  Ctrl+C でも止まります。そのあとに出る「バッチ ジョブを終了しますか」は、
  この画面を残すかどうかの確認です。N を選んでも NetVision は止まったままなので、
  続けたいときは「もう一度起動しますか」に Y と答えてください。

■ データの保存先

  C:\Users\（あなたのユーザー名）\.netvision

  このフォルダをコピーすればバックアップになります。
  アプリのフォルダを入れ替えても、データはそのまま残ります。

■ ネットワークについて

  外部への通信は一切行いません。
  自分のパソコンからのみアクセスできる設定のため、
  同じ社内ネットワークの他の人からは見えません。

■ 同梱物

  python\    インストール不要版の Python $PyVersion（python.org 配布）
  lib\       このアプリが使うライブラリ
  backend\   アプリ本体
  frontend\  画面
  docs\      導入手順書・運用手順書

版数: $Version
"@
$crlf = ($readme -replace "`r`n", "`n") -replace "`n", "`r`n"
$bom = [Text.Encoding]::UTF8.GetPreamble()
[IO.File]::WriteAllBytes(
    (Join-Path $Stage "README.txt"),
    [byte[]]($bom + [Text.Encoding]::UTF8.GetBytes($crlf)))

Step "ZIP に固めます"
$zip = Join-Path $OutDir "NetVision-$Version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $Stage -DestinationPath $zip
Remove-Item $Stage -Recurse -Force

Write-Host ""
Write-Host "できました: $zip"
Write-Host "展開して NetVision.bat をダブルクリックするだけで動きます。"
