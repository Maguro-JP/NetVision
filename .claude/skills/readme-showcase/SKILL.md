---
name: readme-showcase
description: README を魅せる形で書く・直すとき。「README を書いて」「README を魅せる形に」「README を良くして」「リポジトリの顔を作って」「公開する」「リリースする」と言われたとき、新しいリポジトリを作ったとき、private を public にする前、Release を出す前に使う。一目で分かる・動かしたくなる・信用できるの 3 つの柱で、根拠のあるバッジとスクリーンショット、5 手以内のクイックスタートを置き、リポジトリから読み取れる事実だけを書く。`readme_check.py` が 10 項目で点数を出し、出す前に secret-leak-check と content-audit と docs-upkeep を通す。README を今の中身に合わせて保つだけなら docs-upkeep、docs の本文や API リファレンスには使わない。
---

# 魅せる README

ユーザーが決めたこと。README は魅せる形で書く。README はリポジトリの顔で、初めて開いた人が 10 秒で「何をする物か」「自分に要るか」「どう動かすか」を掴める物が完成の形。
中身が動くだけでは未完成で、素っ気ない README は、使われない・信用されない・直されないを招く。

## 3 つの柱

| 柱 | 何 | 具体的に |
|---|---|---|
| 一目で分かる | 開いた瞬間に、何をする物かが分かる | タイトルの直下に 1 文（120 文字以内）。スクリーンショットか GIF か図を上に置く。特長は 3〜6 個の箇条書き |
| 動かしたくなる | 読んだ人が今すぐ試せる | クイックスタートは 5 手以内で、コピーして動くコマンド。前提（OS、版、鍵）を先に書く |
| 信用できる | 書いてあることが本当で、保たれている | バッジはリポジトリに根拠のある物だけ（CI、ライセンス、版、対応 OS）。ライセンスを最後に明記。無い物は書かない |

## 節の順

| 順 | 節 | 入れる物 | 無いときは |
|---|---|---|---|
| 1 | タイトル | リポジトリの名前と同じ。ロゴがあれば中央に | 省かない |
| 2 | 1 文 | 何を・誰のために・どうする物か。120 文字以内で、自分の行に | 省かない |
| 3 | バッジ | CI の状態、ライセンス、最新の版、対応 OS。改行で区切り、節の見出しは付けない | 根拠が無ければ置かない |
| 4 | スクリーンショット | スクリーンショット、GIF、図（mermaid でもよい）。ライブラリなら使う所のコードでもよい | 書き出す前に `scripts/shot.py` で撮る |
| 5 | 特長 | 3〜6 個。機能の名前ではなく、使う人に何が起きるかを書く | 省かない |
| 6 | クイックスタート | 5 手以内。前提 → 入れる → 動かす → 見える物。番号付きでコマンドは code block | 省かない |
| 7 | インストール | 前提（版、OS、鍵）と手順。code block。更新の仕方があれば書く | クイックスタートに含めてよい |
| 8 | 使い方 | よく使う操作を code block で。CLI があれば CLI の例、import できるなら import の例 | 省かない |
| 9 | 設定 | 環境変数と設定ファイルの表（名前、既定、何に効くか） | 設定が無ければ省く |
| 10 | 困ったとき | よくある失敗と直し方。問い合わせ先（Issue） | 無ければ省く |
| 11 | 開発 | テストの流し方、貢献の受け方（PR を受けるか、決まり） | 無ければ省く |
| 12 | ライセンス | ライセンスの名前と LICENSE へのリンク。必ず最後の節 | 省かない |

節ごとの中身と例は `references/structure.md`、バッジの URL の形は `references/badges.md`、雛形は `assets/README.template.md`。

## 根拠の決まり

- リポジトリから読み取れる事実だけを書く。manifest（package.json、pyproject.toml、go.mod）、entrypoint、CI の設定、LICENSE、既存の README、画面が根拠
- 根拠の無い物は書かない。無いバッジ、無い install コマンド、無い API、無い環境変数、無いベンチマーク、無いスクリーンショットを作らない。足りなければ省く
- 書くか迷ったら省く。省くより作る方が信用を削る
- ユーザーが言っていない値（名前、対象、文言）を決めたら「私の推測」と書き、直したあとに ❔ で 1 行聞く
- 既存の README に良い文があれば残す。間違い、古い、重複だけ直す

## 手順

1. 根拠を集める。manifest、entrypoint、`.github/workflows`、LICENSE、既存の README、設定の雛形（`.env.example`）、画面。ここで集まらない物は書かない
2. 型を決める。CLI、ライブラリ、サービス、アプリ（ウィンドウ）、文書のどれか。型で要る節が変わる（CLI は使い方に CLI の例、ライブラリは import の例、アプリはスクリーンショットと起動の手順）
3. 書く。`assets/README.template.md` から始め、節の順で埋める。文は ja-proofread の決まり（です・ます、助詞のある文、`**` を使わない）
4. スクリーンショットを撮る。画面があるなら `scripts/shot.py <URL か HTML> docs/images/screenshot.png` で撮り、README の上に置く。画面が無ければ図か、使う所のコード
5. 検査する。`scripts/readme_check.py` で 10 項目を見る。NG を直し、点数を 10/10 にする。注意は目安
6. 公開前の連携。README と画像を、secret-leak-check の `scan.py --tree`、content-audit の `audit.py --tree`（非公開の名前、メール、ID、費用、スクリーンショットに写った物）、ja-proofread の `proofread.py`、docs-upkeep の `docs_check.py`（壊れたリンク、無いファイル）に通す。precheck が README の差分に点数を注意で出す
7. 出す。★ で README の場所と点数を 1 行。PR とマージは solo-pr-flow（❔ への返事のあと）

## スクリプト

| 何 | すること |
|---|---|
| `scripts/readme_check.py [README か root] [--strict] [--json] [--diff <base>]` | 10 項目（タイトル、1 文、バッジ、スクリーンショット、クイックスタート、インストール、使い方、ライセンス、リンク、仕上げ）を OK・NG・注意で出し、`点数 N/10` を 1 行で出す。`--strict` は NG があれば終了コード 1。`--diff <base>` は README が差分にあるときだけ見て、注意として出す（precheck が呼ぶ） |
| `scripts/shot.py <URL か HTML> <out.png> [--width 1280] [--height 800]` | スクリーンショットを Playwright で撮る（倍率 2）。Playwright が無ければ、撮り方を 1 行出して終了コード 2 |
| `scripts/test_readme_check.py` | 作った中身で判定を確かめる |

## 使わないとき

- docs の本文（設計、手順書、API リファレンス）。README は入口で、長い物は docs に置いて README から辿る
- README を今の中身に合わせるだけ（スキルを足した・消した、リンク切れ）。それは docs-upkeep
- 見た目の個性（色、ロゴの図案）。それは frontend-design

## 他のスキルとの関係

- 今の中身に合わせて保つ: `docs-upkeep`（docs_check.py が壊れたリンクと一覧の抜けを止める）
- 公開前の精査: `secret-leak-check`（鍵）、`content-audit`（名前、ID、費用、スクリーンショットに写った物）
- 画面そのもの: `ui-design`。起動の手順の形（ウィンドウで開く、CLI を見せない）: `desktop-app`
- リポジトリ全体の管理の 9 段の 1 つ: `repo-management`
- 取り込み元は docs/SOURCES.md（readme-generator-skill の根拠の決まり、standard-readme の節の決まり、Best-README-Template の並び）
