---
name: repo-management
description: リポジトリを正しく管理するとき。「リポジトリを正しく管理して」「リポジトリ管理」「このリポジトリは大丈夫?」「公開していい?」「整えて」と言われたとき、新しいリポジトリ、private を public にする前、月に 1 回の整理整頓で使う。土台・方針・README・文書・鍵・公開の精査・脆弱性・整理整頓・出し方の 12 段を各スキルの検査で見て、`repo_check.py` が 1 つの表にする。1 つの段の作業そのもの（README を書く、鍵を消す）はその段のスキル、コードのレビューには使わない。
---

# リポジトリを正しく管理する

ユーザーが決めたこと。リポジトリは 12 段で正しく管理し、1 つの表で見る。
段は別々のスキルが持つ。このスキルは入口で、どの段が揃っていてどの段が欠けているかを一目で見せ、欠けた段をそのスキルに渡す。
段の検査を自分で持たないのは、決まりを 2 か所に書くと食い違うため。

## 12 段

| 段 | 揃っているとは | スキルと検査 | いつ動く |
|---|---|---|---|
| 1 土台 | CLAUDE.md、フック入りの `.claude/settings.json`、最小の CI がある | repo-bootstrap（`repo-bootstrap/scripts/bootstrap.sh`） | 新しいリポジトリ、スキルを初めて入れるとき |
| 2 方針 | 自動マージの可否・同時に開く PR の本数・人のレビューの要否が `.claude/policy/<ユーザー名>.json` にある | workspace-policy | 初めて PR を作る前 |
| 3 README | 魅せる README（10 項目が 10/10、根拠のあるバッジ、スクリーンショット、5 手以内のクイックスタート、ライセンスが最後） | readme-showcase（`readme-showcase/scripts/readme_check.py`） | README を書いたあと、公開の前。precheck が差分に注意で出す |
| 4 文書 | README と docs が今のリポジトリと合っている（壊れたリンク、無いファイル、一覧の抜けが 0） | docs-upkeep（`docs-upkeep/scripts/docs_check.py`） | validate と precheck |
| 5 鍵 | 作業ツリーと履歴に鍵が無い。見つけたらローテートが先 | secret-leak-check（`secret-leak-check/scripts/scan.py` の `--tree`、公開の前は `--history`） | PR の前、公開の前 |
| 6 公開の精査 | 非公開の名前、メール、ID、費用、内部の場所、不快な表現が無い。スクリーンショットに写った物も | content-audit（`content-audit/scripts/audit.py` の `--tree`） | PR の前、記録を書いたあと、公開の前 |
| 7 依存の脆弱性 | 依存の棚卸しがあり、週 1 回 OSV・CISA KEV・JVN と突き合わせている | vuln-watch（`vuln-watch/scripts/inventory.py`、`vuln-watch/scripts/scan.py`） | 週 1 回のルーティン |
| 8 整理整頓 | 余計なファイルが追跡されていない、`.gitignore` がある、入ったブランチと実行済みの一回きりのルーティンが残っていない | tidy（`tidy/scripts/tidy_report.py`） | 月に 1 回、squash マージのあと |
| 9 出し方 | PR の前に precheck を通し、❔ への返事のあとに作る・マージする | solo-pr-flow（verify-before-done の `precheck.py`、pr_gate.py） | PR の前、マージの前 |
| 10 バグやクラッシュ | 構文と未定義が無く、テストが通り、`--help` で起動して落ちない | code-check（`code-check/scripts/crash_check.py`。ruff、node --check、shellcheck、eslint、tsc、pytest） | 書き込みのたび（フック）、precheck、月に 1 回 |
| 11 不具合や脆弱性 | 自分のコードに注入・eval・pickle・TLS・弱い乱数・SQL・XSS・権限・直書きの鍵が無く、依存の脆弱性が無い | code-check（`code-check/scripts/vuln_scan.py`。bandit、semgrep、ruff の S、pip-audit、npm audit） | 同上 |
| 12 情報の扱いや漏洩 | 追跡してはいけないファイルが無く、鍵を出力せず、URL に鍵が無く、外に送る先と扱う個人情報が README に書いてある | code-check（`code-check/scripts/info_scan.py`。detect-secrets、gitleaks、secret-leak-check、content-audit） | 同上。公開の前は `--history` |

## 入口: repo_check.py

```bash
python3 .claude/skills/repo-management/scripts/repo_check.py [root] [--md docs/reports/repo-check.md] [--json]
```

12 段を順に見て、1 つの表（段、状態、根拠、直し方）と最後の 1 行（`リポジトリ管理: OK n / 注意 n / NG n`）を出す。

| 状態 | 意味 | どうする |
|---|---|---|
| OK | 揃っている | 何もしない |
| 注意 | 欠けているが、出す前に止める物ではない（方針が無い、README の点数が低い、入ったブランチが残っている、precheck の記録が無い） | 直し方の列のスキルで直す。月報に表を書く |
| NG | 揃っていない（土台が無い、README が無い、文書の誤り、鍵を検出、重大な表現、code-check の重大） | 出す前に直す。鍵と code-check の NG は終了コード 1 |

検査のスクリプトは、そのリポジトリの `.claude/skills/` を先に見て、無ければ Ciel の物を使う（`CIEL_ROOT`、既定は Ciel の clone の場所）。
スキルが入っていない段は「注意」になり、直し方は skill-sync で入れること。

## 手順

1. `repo_check.py` を動かし、表を読む
2. NG の段を、その段のスキルで直す。鍵は先にローテート
3. 注意の段は、直すか、月報に「まだ」と理由を 1 行で書く
4. 公開の前は、鍵を `--history` で、公開の精査を `--history` と `--github` でも見る（作業ツリーだけでは足りない）。情報の扱いも `info_scan.py --history`
5. 月に 1 回の整理整頓（tidy の月初の回、skill-upkeep の月初の回）で `--md` を付けて回し、月報に表を書く。報告の PR に入れる
6. 出すときは ★ で表の場所と最後の 1 行

## 使わないとき

- 1 つの段の作業そのもの。README を書くのは readme-showcase、鍵を消すのは secret-leak-check、ブランチを消すのは tidy
- コードのレビュー、テストの中身、設計の良し悪し
- 自律開発の状態を見る（それは patrol。patrol はコミット・PR・Actions・判断待ちの表で、こちらは管理の 12 段の表）

## スクリプト

| 何 | すること |
|---|---|
| `scripts/repo_check.py [root] [--md OUT] [--json]` | 12 段を 1 つの表にする。鍵と code-check の NG で終了コード 1 |
| `scripts/test_repo_check.py` | 作ったリポジトリで、全部欠けた場合と揃った場合を確かめる |
| `references/checklist.md` | 段ごとの判定（OK・注意・NG）の基準と直し方 |

## 他のスキルとの関係

- 12 段の各スキルがそれぞれの決まりを持つ。ここは入口と表だけ
- 全体の状態を見る: `patrol`（自律開発の表）。整理整頓そのもの: `tidy`
- スキルを入れる・更新する: `skill-sync`、`repo-bootstrap`
