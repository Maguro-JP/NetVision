# 使うツール

ユーザーの決まり: Claude だけで済ませず、便利なツールを使う。検査は実績のあるツールを先に入れて使い、
自分の正規表現は無いときの補いにする。一覧は `scripts/tools.py`（`--install` で無い物を入れる）。

| ツール | 何を見る | 入れ方 | ライセンス | 使うスクリプト |
|---|---|---|---|---|
| ruff | Python の構文（E9）、未定義（F82）、不具合（B）、脆弱性の型（S。flake8-bandit の移植） | `pip install --user ruff` | MIT | crash_check、vuln_scan、code_nudge |
| bandit | Python の脆弱性の型（B 番号） | `pip install --user bandit` | Apache-2.0 | vuln_scan |
| semgrep | Python と JS/TS の脆弱性の型。規則は registry の p/security-audit と p/secrets、取れなければ同梱の `semgrep-rules.yml` | `pip install --user semgrep` | LGPL-2.1（規則の束は semgrep の registry の条件） | vuln_scan |
| shellcheck | シェルの不具合（引用の抜け、未定義、危ない形） | `pip install --user shellcheck-py` | GPL-3.0（外部のツールとして呼ぶだけで、取り込まない） | crash_check、code_nudge |
| detect-secrets | 鍵の漏洩（作業ツリー） | `pip install --user detect-secrets` | Apache-2.0 | info_scan |
| gitleaks | 鍵の漏洩（作業ツリーと履歴） | GitHub Releases の tar.gz を `~/.local/bin` に置く（tools.py --install が試す） | MIT | info_scan（`--history` で履歴も） |
| pip-audit | Python の依存の脆弱性（requirements.txt） | `pip install --user pip-audit` | Apache-2.0 | vuln_scan（依存の行。週 1 回の本番は vuln-watch） |
| npm audit | JS の依存の脆弱性（package-lock.json） | npm に同梱 | Artistic-2.0 | vuln_scan |
| node | JS の構文（`node --check`） | Node.js | MIT | crash_check、code_nudge |
| eslint | JS/TS の不具合（リポジトリに設定があるときだけ） | `npm install -g eslint`（リポジトリの node_modules を先に見る） | MIT | crash_check |
| tsc | TypeScript の型（tsconfig.json があるときだけ） | `npm install -g typescript` | Apache-2.0 | crash_check |
| pytest | Python のテスト | `pip install --user pytest` | MIT | crash_check |
| markdownlint-cli2 | README と docs の書式 | `npm install -g markdownlint-cli2` | MIT | readme-showcase の readme_check |
| markdown-link-check | README と docs のリンク切れ（外のリンクも） | `npm install -g markdown-link-check` | ISC | readme-showcase の readme_check |

## 無いときの補い

- Python の脆弱性の型: bandit も ruff も無ければ、`vuln_scan.py` の正規表現（`patterns.md` の V 番号）
- JS/TS の脆弱性の型: semgrep が無ければ、同じ正規表現
- シェルの構文: shellcheck が無ければ `bash -n`
- 鍵の漏洩: detect-secrets も gitleaks も無ければ、secret-leak-check の `scan.py`（常に併用）
- 出力の先頭の `ツール:` の行に、何で見て何が無かったかを必ず出す。無い物を黙って飛ばさない

## 入れるときの決まり

- `pip install --user` と `npm install -g` だけ。sudo は使わない。配った先で勝手に入れたくなければ `CODE_CHECK_NO_INSTALL=1`
- ツールの版は固定しない（検出の規則が更新されるため）。動かなくなったら tools.py の一覧で版を指定する
- 新しいツールを足すときは、ライセンスを docs/SOURCES.md に書き、取り込まずに呼ぶだけにする
