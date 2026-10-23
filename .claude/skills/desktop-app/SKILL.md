---
name: desktop-app
description: アプリを作る・直すとき、起動の仕方や機能の入口を決めるとき。「アプリにして」「ブラウザじゃなくて」「CLI じゃなくて GUI で」「ウィンドウで開いて」「Setup.exe」「デスクトップアプリ」と言われたとき、README の起動手順を書くときに使う。開発は CLI ではなく GUI、起動はブラウザではなくアプリのウィンドウで行う。ウェブの画面は pywebview や Tauri、Electron で包み、`webbrowser.open` と「URL を開いて」を残さない。フックがブラウザ起動と CLI の入口を見つける。ライブラリや内部のエンジンには使わない。
---

# アプリはウィンドウで開き、機能は画面に置く

ユーザーが決めたこと。開発は CLI ではなく GUI で行い、起動はブラウザではなくアプリケーションのウィンドウで開く。
「サーバーを立ててブラウザで URL を開く」も「コマンドを打って使う」も、ユーザーにとっては未完成のアプリ。
この決まりに反する案（CLI の方が早い、ブラウザで十分）は出さない。言われた形で作る。

## 1. 入口はウィンドウ

- 起動（デスクトップのアイコン、`Setup.exe`、`start.sh`）で OS のウィンドウが開き、そこに画面が出る
- サーバーが要るなら裏で起動し、ウィンドウが閉じたら止める。URL は人に見せない。「http://127.0.0.1 を開いてください」と README に書かない
- `webbrowser.open`、`xdg-open http`、`start http`、`open http` を起動の経路に使わない
- 準備（部品やモデルの取得）の進みも、ターミナルではなくウィンドウに出す

## 2. CLI を作らない

- ユーザーが触る機能は全部画面に置く。新しい機能を `argparse` や `click` の入口で足さない
- 内部のエンジン（描く、測る、変換する）が Python のモジュールや関数であるのはよい。それを呼ぶのは画面のサーバーで、人ではない
- すでにある CLI は、画面に同じ機能が入った時点で README の使い方から消す。開発者向けのツールとして残すのはよいが、使い方の先頭に置かない
- テストや CI が CLI を使うのはよい。人に「コマンドを打て」と言わない、が線

## 3. 包み方の選び方

今あるものに合わせる。全部作り直さない。

| 今あるもの | 包み方 | 一言 |
|---|---|---|
| Python のサーバー + ウェブの画面（Vite、React） | pywebview（Python から OS のウェブビューのウィンドウを開く） | 既存のサーバーと画面をそのまま使える。Windows は WebView2、macOS は WebKit、Linux は WebKitGTK |
| Node や TypeScript の画面だけ | Tauri（軽い）か Electron（実績） | Python のサーバーが要るなら、子プロセスで起動して待つ |
| Python だけで画面も無い | PySide6（Qt）か Tkinter | 画面を一から作る。小さいツールなら Tkinter で足りる |
| C# や .NET | WPF か WinUI 3 | |
| Rust | Tauri か egui | |

pywebview の最小の形。サーバーを裏で起動し、応答が返ったらウィンドウを開き、閉じたら止める。

```python
import threading, webview
threading.Thread(target=run_server, daemon=True).start()   # uvicorn などを裏で
wait_until_http_ok(url)                                     # 応答が返るまで待つ
webview.create_window("アプリ名", url, width=1280, height=800)
webview.start()                                             # 閉じるまで止まる。戻ったらサーバーを止める
```

## 4. 配布

- Windows はインストーラー（NSIS や Inno Setup）でデスクトップとスタートメニューにアイコンを置く。起動でウィンドウが開く
- WebView2 が無い Windows では、インストーラーが Evergreen Bootstrapper を入れるか、起動時に案内を出す
- macOS は `.app`、Linux は AppImage か `.desktop` ファイル。どれも「起動すると窓が開く」が完成の形
- 初回の重い準備（モデルの取得）も、ウィンドウの中で進みを見せる。ターミナルを見せない

## 5. 完成条件の書き方

finish-first の表に書く完成条件は、起動の操作と、開くものを書く。

- 「`sh app/start.sh` と `Setup.exe` から起動すると、ブラウザではなくアプリのウィンドウが開いて画面が表示される」
- 「`grep -rn webbrowser app/` が 0 件。README の使い方に `http://` を開く手順が無い」
- 「画面から <機能> ができる。コマンドを打つ手順が README の使い方に無い」

## 6. フックが見るもの

`hooks/no_browser.py`（PostToolUse、Edit と Write）が、書いた中身にブラウザ起動（`webbrowser.open`、`xdg-open http`、
「URL を開いてください」）と、`app/` や `src/` の下の新しい CLI の入口（`argparse.ArgumentParser(`、`@click.command`、`typer.Typer(`）
があれば1行で知らせる。`tests/`、`tools/`、`scripts/` の下は見ない。`DESKTOP_APP_OFF=1` で止まる。

## 7. 他のスキルとの関係

- 完成条件と1つずつ完成させること: `finish-first`
- ユーザーの決めたことに反抗しない: `follow-instructions`
- リポジトリごとの具体（どのサーバー、どの画面、どのインストーラー）は、そのリポジトリのスキルに書く
