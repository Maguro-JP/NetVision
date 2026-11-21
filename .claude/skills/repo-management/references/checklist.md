# 段ごとの判定と直し方

`scripts/repo_check.py` が機械で出す判定の基準。人が見るときも同じ基準で見る。

| 段 | OK | 注意 | NG | 直し方 |
|---|---|---|---|---|
| 土台 | CLAUDE.md があり、`.claude/settings.json` に hooks がある | settings.json はあるが hooks が無い | CLAUDE.md か settings.json が無い | repo-bootstrap の `bootstrap.sh`。既にある物には触らない |
| 方針 | `.claude/policy/*.json` がある | 無い | ― | workspace-policy で自動マージの可否、同時に開く PR の本数、人のレビューの要否を決めて記録する |
| README | readme_check.py の 10 項目に NG が無い | NG がある（点数が 10 未満） | README.md が無い | readme-showcase。雛形から書き、NG を直して 10/10 にする。LICENSE の無い物は先にライセンスを決める |
| 文書 | docs_check.py の誤り 0、注意 0 | 注意がある（古くなる数、予定の語、重複、強調の記号） | 誤りがある（壊れたリンク、無いファイル、一覧の抜け、辿れない文書） | docs-upkeep。スキルやスクリプトを足した・消した PR で一覧とリンクを直す |
| 鍵 | 作業ツリーに検出なし | ― | 検出あり | secret-leak-check。先にローテートし、次に消す。公開の前は `--history` で履歴も見る |
| 公開の精査 | 重大 0、注意 0 | 注意がある | 重大がある | content-audit。名前・メール・ID・費用・内部の場所を伏せる。スクリーンショットに写った物も見る |
| 脆弱性 | 棚卸しができている | inventory.py が無い（vuln-watch が入っていない） | ― | vuln-watch を入れ、週 1 回のルーティンに載せる。悪用済みと重大は更新のブランチを用意して ❔ |
| 整理整頓 | 余計なファイルが追跡されていない、要る `.gitignore` の行がある、デフォルトブランチに入ったリモートブランチが無い | どれかがある | ― | tidy。余計なファイルは消して `.gitignore` に足す。入ったブランチは消す。月に 1 回は `tidy_report.py` |
| 出し方 | 最後の precheck が通っている | 記録が無い、または落ちている | ― | solo-pr-flow。PR の前に `precheck.py` を通し、❔ で返事を待つ |

## 10 バグやクラッシュ（code-check の crash_check.py）

| 状態 | 基準 |
|---|---|
| OK | 構文の誤りと未定義が 0、テストが通る、起動の入口が `--help` で落ちない |
| 注意 | 画面のある入口や引数を受けない入口（手で起動して確かめる）、不具合になりやすい書き方（ruff の B） |
| NG | 構文の誤り、未定義の名前、テストが落ちる、起動で Traceback |

直し方: 重大の行を直す。テストは落ちた物を直してから出す。

## 11 不具合や脆弱性（code-check の vuln_scan.py）

| 状態 | 基準 |
|---|---|
| OK | bandit・semgrep・ruff の S と正規表現の重大が 0、依存に high と critical が無い |
| 注意 | 0.0.0.0、CORS、except して pass、assert の検査、http://、依存の moderate |
| NG | 注入（shell、SQL、eval）、pickle、TLS の検証を切る、弱い乱数で鍵、debug=True、誰でも書ける権限、直書きの鍵 |

直し方: code-check の `.claude/skills/code-check/references/patterns.md` の同じ番号。直せない固定の文字列は行末に `code-check: ignore 理由`。

## 12 情報の扱いや漏洩（code-check の info_scan.py）

| 状態 | 基準 |
|---|---|
| OK | 追跡してはいけないファイルが無い、鍵を出力していない、URL に鍵が無い、detect-secrets と gitleaks が 0 |
| 注意 | .gitignore に .env が無い、db や log を追跡、鍵の書き出し、クリップボードや計測の SDK、文書の中の例 |
| NG | .env や .pem を追跡、鍵の出力、URL の鍵、鍵のファイルを他のユーザーが読める、鍵らしき物 |

直し方: 追跡から外して .gitignore へ。push 済みならローテートして履歴から消す。外に送る先と扱う個人情報は README の「情報の扱い」に書く。

## 公開の前に足すこと

作業ツリーだけでは足りない。private を public にする前は、次も見る。

- 鍵: `scan.py --history`（全コミット）。見つかったら、消しても戻らないので先にローテート
- 公開の精査: `audit.py --history` と `audit.py --github <owner>/<repo>`（Issue、PR の本文、履歴の著者）
- README: スクリーンショットに、名前・メール・鍵・内部の場所が写っていないか
- ライセンス: LICENSE が無いまま公開しない

## 月に 1 回

tidy の月初の回と skill-upkeep の月初の回で `repo_check.py --md` を回し、表を月報の「整理整頓」の節に入れる。
注意のまま残す段は、理由を 1 行で書く。
