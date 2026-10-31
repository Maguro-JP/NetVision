# アプリケーション版にそろえるための整理の案（2026-11-01 JST）

ユーザーの指示「今後全てアプリケーション版で開発する。昔のゴミは整理し、使う部分は使えるようにして残す。実装するかは判断待ち」を受けた案です。
まだ何も消していません。判断待ち 11（pending.md）の答えが付いてから、この表のとおりに1本の PR で行います。

## 消す候補（アプリケーション版では使わないもの）

| もの | 何のためにあったか | 消す理由 |
|---|---|---|
| `vercel.json`、`api/index.py`、`requirements.txt` | PR ごとにブラウザで画面を見る Vercel のプレビュー | アプリのウィンドウで確かめる形になり、ブラウザで開く入口が残る（desktop-app の決まりに反する） |
| `docs/PREVIEW.md`、README と NEXT_STEPS.md の Vercel の節 | 上のプレビューの説明 | 同上 |
| `backend/tests/test_preview_requirements.py` と `backend/app/bootstrap.py` の Vercel 向けの分岐 | プレビューの依存とデモの起こし方 | 同上 |
| `backend/build/` | 古いビルドの写し（判断待ち 1） | 使っていない。git に入っているだけ |
| `packaging/make-bundle.sh`、`packaging/install.sh`・`install.bat` | Linux / 閉域向けの wheel の一式 | Windows の Setup.exe と ZIP に寄せる。Linux はサーバーの形（下）で使う |
| `packaging/make-bundle-windows.ps1` | Git Bash が無い端末で ZIP を作る PowerShell 版 | GitHub の Windows bundle で作れるようになった。sh 版と二重に直す手間が続いている |

## 残して使えるようにするもの

| もの | 使い道 |
|---|---|
| `backend/`、`frontend/` | アプリの中身（画面は pywebview のウィンドウの中で動く） |
| `backend/scripts/launcher.py`、`packaging/windows/`、`make-setup-windows.sh`、`make-bundle-windows.sh` | アプリケーション版の起動と配布（Setup.exe と ZIP） |
| `.github/workflows/windows-smoke.yml`、`bundle.yml`、`ci.yml` | Windows の実機試験、毎月の配布物、CI |
| `backend/scripts/standalone.py`、`packaging/netvision.service`、`docker-compose.yml`、`frontend/Dockerfile`・`nginx.conf` | サーバーで複数人で使う形（PostgreSQL。docs/DATA_LOCATIONS.md）。DB の置き方で決めた拡張の道として残す |
| `packaging/start.sh`・`start.bat` | 手元の開発でアプリのウィンドウを開く入口 |
| `tools/build_coastline.py` | 構成図の海岸線の作り直し |

## 消すときに一緒に直すもの

- README・docs のリンクと一覧（docs-upkeep の docs_check.py で確かめる）
- `test_packaging.py` の PowerShell 版と sh 版を比べる試験（PowerShell 版を消すなら外す）
- Vercel のプロジェクト側の設定（GitHub との連携を外す）は、ユーザーの Vercel の画面で行う（こちらからは触らない）
