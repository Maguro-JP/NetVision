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
    [string]$PyVersion = "3.11.9",
    # GGUF の URL かパス。渡すとモデルも同梱する（既定は同梱しない）
    [string]$WithModel = ""
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
# 手引きは丸ごと持ち込む。個別に並べていると、新しく書いたものが入らない
# （実際に LOCAL_LLM.md が抜けた）
Copy-Item (Join-Path $Repo "docs\*.md") (Join-Path $Stage "docs")
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
    "openpyxl", "python-multipart", "jinja2", "uvicorn", "tzdata", "pywebview", "pythonnet"
)
& $python -m pip install --quiet --target (Join-Path $Stage "lib") --no-warn-script-location @packages
if ($LASTEXITCODE -ne 0) { throw "ライブラリの取得に失敗しました" }

# モデルをアプリの中で動かすためのライブラリ。実行ファイルは増えないので、
# 既定で同梱する。使う人がやることを「GGUF を1つ置く」だけにするため
Step "モデルを動かすライブラリを入れます"
& $python -m pip install --quiet --target (Join-Path $Stage "lib") `
    --no-warn-script-location llama-cpp-python
if ($LASTEXITCODE -ne 0) {
    Write-Host "  取得できませんでした。この一式ではモデルを使えません"
}

# モデルの実体は数 GB あるので、同梱するときだけ -WithModel に URL かパスを渡す
$models = Join-Path $Stage "models"
New-Item -ItemType Directory -Force -Path $models | Out-Null
if ($WithModel) {
    if ($WithModel -like "http*") {
        Step "モデルを取得します（数 GB あります）"
        $name = [IO.Path]::GetFileName(($WithModel -split "\?")[0])
        Fetch $WithModel (Join-Path $models $name)
    } else {
        Copy-Item $WithModel $models
    }
}
Get-ChildItem (Join-Path $Stage "lib") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 中間ファイル（.pyc）を先に作っておく。置き場所が OneDrive などの同期フォルダだと、
# 初回起動時にこれを書く処理が同期に巻き込まれて、何分も戻ってこないことがある
& $python -m compileall -q (Join-Path $Stage "backend") (Join-Path $Stage "lib") | Out-Null

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
rem NetVision を起動します。インストールは不要です。アプリのウィンドウが開きます。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\dist
rem データ（DB と資料）の置き場所。空なら C:\Users\...\.netvision
set NETVISION_DATA_DIR=
rem 中間ファイルは同梱済み。同期フォルダで固まらないよう、実行時には書かせない
set PYTHONDONTWRITEBYTECODE=1
start "" "%~dp0python\pythonw.exe" -m scripts.launcher
'@
$diag = @'
@echo off
rem 様子を見ながら起動します。アプリのウィンドウと、記録を流す黒い画面が出ます。
rem ふだんは NetVision.bat を使ってください。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\dist
set NETVISION_DATA_DIR=
set PYTHONDONTWRITEBYTECODE=1
"%~dp0python\python.exe" -m scripts.launcher
echo.
echo 終了しました。上に出ている内容を控えてください。
pause
'@
$crlf = ($bat -replace "`r`n", "`n") -replace "`n", "`r`n"
[IO.File]::WriteAllBytes(
    (Join-Path $Stage "NetVision.bat"),
    [Text.Encoding]::GetEncoding(932).GetBytes($crlf))

$diagCrlf = ($diag -replace "`r`n", "`n") -replace "`n", "`r`n"
[IO.File]::WriteAllBytes(
    (Join-Path $Stage "NetVision-console.bat"),
    [Text.Encoding]::GetEncoding(932).GetBytes($diagCrlf))

$mcp = @'
@echo off
rem NetVision を、AI から呼べる道具として立ち上げます。
rem 画面は出ません。AI の側の設定から呼ばれて動きます（docs/MCP.md）。
setlocal
cd /d "%~dp0"
set NETVISION_DATA_DIR=
set PYTHONDONTWRITEBYTECODE=1
"%~dp0python\python.exe" -m app.mcp_server
'@
$mcpCrlf = ($mcp -replace "`r`n", "`n") -replace "`n", "`r`n"
[IO.File]::WriteAllBytes(
    (Join-Path $Stage "NetVision-mcp.bat"),
    [Text.Encoding]::GetEncoding(932).GetBytes($mcpCrlf))

$readme = @"
NetVision （インストール不要版）
====================================

このフォルダは、パソコンに何もインストールせずに動きます。
管理者権限は不要で、レジストリや Program Files も変更しません。

■ 使い方

  1. このフォルダを、書き込める場所に置く
  2. 「NetVision.bat」をダブルクリック
  3. NetVision のウィンドウが開きます

  止めるときは、そのウィンドウを閉じてください。
  ウィンドウが出ないときは、Microsoft Edge WebView2 ランタイムが入っているかを確かめてください
  （Windows 11 には入っています）。

■ AI を使うとき（任意）

  C:\LLM\gguf フォルダに、モデルのファイル（.gguf）を1つ置くだけです。
  このフォルダは初めて起動したときに作られます。
  置かなければ、AI を使わない形でそのまま動きます。

  C:\LLM は、自作のアプリどうしでモデルを共有する置き場です。
  モデルは数 GB あるので、アプリごとに持たずにここへ1つだけ置きます。
  アプリのフォルダを入れ替えても、モデルはそのまま残ります。
  置き場を変えるときは、環境変数 LLM_HOME に別のフォルダを指定します。

  前に models フォルダや .netvision\models に置いたモデルも、そのまま読みます。

■ 置き場所について

  同期フォルダ（OneDrive など）やネットワークドライブでも動きます。
  Python の中間ファイルは同梱済みで、実行時には書きません。

  ただし、同期していない場所のほうが速く動きます。置ける場所があるなら
  そちらを選んでください。

  データ（DB と資料）の置き場所は、既定では次の場所です。

    C:\Users\（あなたのユーザー名）\.netvision

  ここに置けない端末では、NetVision.bat の中の

    set NETVISION_DATA_DIR=

  の行に置き場所を書いてください（例: set NETVISION_DATA_DIR=Z:\NetVision-data）。
  同期される場所に DB を置く場合は、書いている最中に同期へ持っていかれると
  壊れることがあるので、データフォルダのコピーをこまめに取ってください。

■ AI から台帳を引かせたいとき（任意）

  「NetVision-mcp.bat」は、AI の側から NetVision を道具として呼ぶための口です。
  読むだけで、台帳は書き換わりません。使い方は docs\MCP.md にあります。
  普段の利用では使いません。

■ 起動しないとき

  「NetVision-console.bat」を使ってください。アプリのウィンドウと一緒に、
  記録を流す黒い画面が出ます。起動の記録は次の場所にも残ります。

    C:\Users\（あなたのユーザー名）\.netvision\launcher.log

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
  docs\      手引き（導入・運用・AI の使い方など）

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
