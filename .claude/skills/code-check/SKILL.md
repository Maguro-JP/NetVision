---
name: code-check
description: 作った物のバグやクラッシュ・不具合や脆弱性・情報の扱いや漏洩を、実績のあるツールで機械で確認するとき。「ちゃんと確認して」「バグが無いか見て」「クラッシュする」「脆弱性は?」「情報が漏れてないか」「問題が多発してる」「便利なツールを使え」と言われたとき、コードを書いたあと、完了と言う前、PR の前、公開の前、月に 1 回の整理整頓で使う。ruff・bandit・semgrep・shellcheck・detect-secrets・gitleaks・pip-audit・npm audit・eslint・tsc・pytest を先に入れて使い（tools.py --install）、無い物だけ自分の正規表現で補う。書き込みのたびにフックが重大を差し込み、precheck が重大で止め、repo-management の段で表にする。依存の CVE の週 1 回の見張りは vuln-watch、鍵の型は secret-leak-check、非公開の名前や費用は content-audit、UI は ui-design。
---

# 作った物をちゃんと確認する

ユーザーが決めたこと。Claude が作る物には問題が多い。だから、作った物はバグやクラッシュ・不具合や脆弱性・情報の扱いや漏洩を
機械で確認してから出す。確認は Claude の目ではなく、実績のあるツールで行う。Claude だけで済ませない。

## 3 つの観点

| 観点 | 何を見る | スクリプト | 先に使うツール | 無いときの補い |
|---|---|---|---|---|
| バグやクラッシュ | 構文、未定義の名前、不具合になりやすい書き方、テスト、`--help` での起動 | `scripts/crash_check.py` | ruff、node --check、shellcheck、eslint、tsc、pytest、npm test | compile、bash -n |
| 不具合や脆弱性 | 自分のコードの穴（注入、eval、pickle、TLS、弱い乱数、SQL、XSS、権限、直書きの鍵）と依存の脆弱性 | `scripts/vuln_scan.py` | bandit、semgrep、ruff の S、pip-audit、npm audit | 正規表現（`references/patterns.md` の V 番号） |
| 情報の扱いや漏洩 | 追跡してはいけないファイル、鍵の出力、URL の鍵、鍵の書き出し、鍵の権限、外に送る先、個人情報、計測の SDK | `scripts/info_scan.py` | detect-secrets、gitleaks、secret-leak-check、content-audit | 正規表現（I 番号） |

入口は `scripts/code_check.py`。3 本を回して 1 つの表にし、`コードの確認: 重大 n / 注意 n / 情報 n` を出す。重大があれば終了コード 1。

## いつ動くか（3 か所。どれか 1 つでは抜ける）

| いつ | 何が | 何を | 止めるか |
|---|---|---|---|
| 書き込みのたび | `hooks/code_nudge.py`（PostToolUse Edit、Write、MultiEdit） | そのファイルの構文と重大の型だけ。1 秒以内 | 止めない。重大だけ 1 行差し込む |
| PR の前・マージの前 | verify-before-done の precheck.py | 差分のファイルで 3 本 | 重大で NG。pr_gate.py が PR と main への push を止める |
| 月に 1 回、公開の前 | repo-management の repo_check.py | 木全体で 3 本（段 10〜12） | 表にする。公開の前は `--history` で履歴の鍵も |

## 手順

1. ツールを揃える。`python3 .claude/skills/code-check/scripts/tools.py` で何があるかを見て、無ければ `--install`。出力の先頭の `ツール:` の行に、何で見て何が無かったかが出る。無い物を黙って飛ばさない
2. 書いたら回す。`python3 .claude/skills/code-check/scripts/code_check.py` を根で動かす（差分だけなら `--diff origin/main`）。長いので裏で動かし、出力はファイルへ（background-first）
3. 重大は直してから出す。直せない物は `code-check: ignore` を理由つきで行末に書き、❕ 疑い に 1 行
4. 注意は直すか、直さない理由を ❕ 疑い に 1 行。情報（外に送る先、扱っている個人情報）は README の「情報の扱い」に書く
5. 完了と言うときは「❕ 疑い」に `コードの確認: 重大 0 / 注意 n / 情報 n` を写す。回していなければ「回していない」と書く

## 使わないとき

- 依存の CVE を週 1 回見張る（vuln-watch。ここは pip-audit と npm audit の行を出すだけ）
- 鍵の有無そのもの（secret-leak-check。ここはその要約を取り込む）、非公開の名前・メール・費用（content-audit）
- UI の使いやすさ（ui-design）、文章（ja-proofread）、設計の良し悪しやコードの読みやすさ（レビュー）

## スクリプトとフック

| 何 | すること |
|---|---|
| `scripts/code_check.py [root] [--diff base] [--md OUT] [--json] [--no-tests] [--no-smoke] [--history]` | 3 本を回して 1 つの表。重大で 1 |
| `scripts/crash_check.py` | 構文、未定義、テスト、起動。`--no-tests`（precheck は別にテストを流す）、`--no-smoke` |
| `scripts/vuln_scan.py` | 自分のコードの穴と依存。`--regex` でツールがあっても正規表現を全部動かす |
| `scripts/info_scan.py` | 情報の扱いと漏洩。`--history` で gitleaks が履歴も見る |
| `scripts/tools.py [--install] [--json]` | ツールの有無と導入。`CODE_CHECK_NO_INSTALL=1` で入れない |
| `hooks/code_nudge.py` | 書き込みのたびに 1 ファイルを見て重大を 1 行。`CODE_CHECK_OFF=1` で止める |
| `scripts/test_crash_check.py`、`test_vuln_scan.py`、`test_info_scan.py` | 作ったリポジトリで確かめる（フックも） |
| `references/patterns.md` | 型の一覧（C、V、I、D の番号と直し方）と抑止の書き方 |
| `references/tools.md` | ツールの一覧、入れ方、ライセンス、無いときの補い |
| `references/semgrep-rules.yml` | semgrep の registry が取れないときの同梱の規則 |

## 他のスキルとの関係

- 完了の前の決まり: `verify-before-done`（動かしてから完了と言う。precheck がここを呼ぶ）
- リポジトリ全体の 12 段: `repo-management`（段 10〜12 がこの 3 本）
- Claude Code 組み込みの `/security-review` があれば、PR の差分で併用する（読んで判断する側。ここは機械で見る側）
- 取り込み元は docs/SOURCES.md（Bandit の検出の一覧、OWASP Top 10 の分類。ツールは取り込まずに呼ぶ）
