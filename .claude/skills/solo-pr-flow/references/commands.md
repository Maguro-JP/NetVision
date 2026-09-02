# コマンド対応表

環境によって使える道具が違う。手元にある方を使う。

## 目次

- [停止条件の判定材料をどう取るか](#停止条件の判定材料をどう取るか)
- [PR の状態の取得](#pr-の状態の取得)
- [マージ](#マージ)
- [後片付け](#後片付け)
- [判定に使う値](#判定に使う値)
- [自動マージ機能を使う場合](#自動マージ機能を使う場合)
- [追加した停止条件の判定材料](#追加した停止条件の判定材料)
- [acknowledged の書き方](#acknowledged-の書き方)

## 停止条件の判定材料をどう取るか

リポジトリ側の判定材料は**1〜3回の呼び出しでまとめて**取る。1条件ずつ往復しない。

### リポジトリのメタデータ（B1, B2, C1〜C3, D1〜D3 をまとめて満たす）

MCP なら `search_repositories` に `repo:<owner>/<name>` を渡し、
**`minimal_output: false`** にする。true だと必要なフィールドが落ちる。

CLI なら:

```bash
gh repo view <owner>/<repo> --json \
  isArchived,isFork,isDisabled,stargazerCount,forkCount,watchers,owner,viewerPermission,defaultBranchRef
```

取れるフィールドと対応する停止条件:

| フィールド | 停止条件 |
|---|---|
| `permissions.admin` / `viewerPermission` | B1（admin でなければ停止） |
| `owner.type` == `"Organization"` | B2 |
| `stargazers_count` >= 2 | C1 |
| `forks_count` >= 1 | C2 |
| `watchers_count` >= 2 | C3 |
| `archived` | D1 |
| `fork` | D2 |
| `disabled` | D3 |
| `default_branch` | E3 の判定に使う |

### コラボレーター（B3）

```bash
gh api repos/<owner>/<repo>/collaborators --jq 'length'
```

MCP なら `list_repository_collaborators`。**2件以上なら停止。**
権限不足で 403 が返ることがある。**その場合は停止条件 A（情報が取れない）に該当する。**

### コミット著者（B4）

```bash
gh api "repos/<owner>/<repo>/commits?per_page=100" --jq '[.[].author.login] | unique'
```

MCP なら `list_commits` に `perPage: 100`, `fields: ["sha","author"]`。
bot（`login` が `*[bot]`、`github-actions` など）は除いて数える。**2人以上なら停止。**

### CODEOWNERS（B5）

次の3か所を見る。1つでも存在すれば停止。

```
.github/CODEOWNERS
CODEOWNERS
docs/CODEOWNERS
```

ローカルに clone があるならファイルの存在を見るだけでよい。

## PR の状態の取得

| 欲しい情報 | GitHub CLI | MCP (github) |
|---|---|---|
| PR 詳細 | `gh pr view <n> --json state,isDraft,mergeable,mergeStateStatus,baseRefName` | `pull_request_read` method=`get` |
| チェック状況 | `gh pr checks <n>` | `pull_request_read` method=`get_check_runs` |
| レビュー | `gh pr view <n> --json reviews` | `pull_request_read` method=`get_reviews` |
| 変更ファイル | `gh pr diff <n> --name-only` | `pull_request_read` method=`get_files` |
| PR の作成者 | `gh pr view <n> --json author` | `pull_request_read` method=`get` の `user.login` |

一度に必要な分をまとめて取る。1項目ずつ往復しない。

## マージ

```bash
gh pr merge <n> --squash --delete-branch
gh pr merge <n> --merge  --delete-branch
gh pr merge <n> --rebase --delete-branch
```

MCP なら `merge_pull_request`（`merge_method`: `squash` / `merge` / `rebase`）。
ブランチ削除は別途 `delete_file` ではなく git 側で行う:

```bash
git push origin --delete <branch>
```

## 後片付け

```bash
git checkout <default-branch>
git pull origin <default-branch>
git branch -d <branch>          # -D は使わない（未マージを消してしまう）
```

既定ブランチ名の取得:

```bash
git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'
```

## 判定に使う値

### mergeable_state / mergeStateStatus

| 値 | 意味 | 自動マージ |
|---|---|---|
| `clean` | 問題なし | ✅ |
| `blocked` | 必須チェック未完了、レビュー未承認など | ❌ 原因を特定する |
| `behind` | base に遅れている | base を取り込んで push |
| `dirty` | コンフリクト | 解決してから |
| `unstable` | 非必須チェックが失敗 | 内容を見て判断 |
| `unknown` | GitHub が計算中 | 少し置いて取り直す |

### チェックランの status / conclusion

`status` が `completed` になって初めて `conclusion` を見る。
`queued` / `in_progress` の間は判定を保留し、**完了通知を待つ**。

`conclusion` の扱い:

| 値 | 扱い |
|---|---|
| `success` | 合格 |
| `skipped` / `neutral` | 合格扱いでよい |
| `failure` / `timed_out` | 不合格。直す |
| `cancelled` | 再実行するか、原因を確認 |
| `action_required` | 人の操作が必要。尋ねる |

## 自動マージ機能を使う場合

CI 完了を待たずに予約したいときは GitHub 側の auto-merge を使う手もある。

```bash
gh pr merge <n> --squash --auto --delete-branch
```

ただし**リポジトリ設定で auto-merge が有効**かつ**ブランチ保護ルールが必要**。
個人リポジトリでは無効なことが多いので、使えなければ通常のマージに切り替える。

## 追加した停止条件の判定材料

### B6: 自分以外が作った issue / PR

```bash
gh issue list --state open --json author --jq '[.[].author.login] | unique'
gh pr    list --state open --json author --jq '[.[].author.login] | unique'
```

MCP なら `list_issues` / `list_pull_requests` の作成者を見る。
自分以外の login が出たら停止。

### C4: リリース / タグ

```bash
gh release list --limit 1
gh api repos/<owner>/<repo>/tags --jq 'length'
```

MCP なら `list_releases` / `list_tags`。1件でもあれば停止。

### C5, C6, D4: リポジトリのフラグ

`search_repositories`（`minimal_output: false`）の
`has_pages` / `has_discussions` / `is_template` を見る。
リポジトリのメタデータ取得と同じ1回の呼び出しで済む。

### F 系: 差分の内容

変更ファイル一覧と差分本体から判定する。

```bash
gh pr diff <n> --name-only     # F1, F3, F4, F5, F6, F7
gh pr diff <n>                 # F2（秘密情報の走査）
gh pr view <n> --json additions,deletions,changedFiles   # F8
```

| 条件 | 見るもの |
|---|---|
| F1 | パスが `protected_paths` の要素で始まるか |
| status | `added` / `modified` / `removed` の区別。`gh pr diff --name-status`、MCP は `get_files` の `status` |
| F2 | 追加行に `sk-ant-` `ghp_` `AKIA` `-----BEGIN .* PRIVATE KEY-----` 等 |
| F3 | `LICENSE` / `LICENSE.*` / `COPYING` |
| F4 | `.github/` 配下（`protected_paths` に含まれない分） |
| F5 | `package.json` `package-lock.json` `yarn.lock` `pnpm-lock.yaml` `requirements.txt` `poetry.lock` `Cargo.lock` `go.mod` `go.sum` `Gemfile.lock` |
| F6 | `Dockerfile` `.npmrc` `pyproject.toml` の publish 設定、`vercel.json` `netlify.toml` 等のデプロイ設定 |
| F7 | `migrations/` `migrate/` 配下、`*.sql` |
| F8 | `changedFiles >= 100` または `deletions >= 1000` |

リポジトリに検証スクリプトがあるなら、F2 はそれを再利用する
（Ciel なら `./scripts/validate.sh` の secret scan）。

## acknowledged の書き方

停止条件を「今後は許容する」と決めたときだけ `.claude/policy/<利用者名>.json` に追記する。**観測値を必ず一緒に記録する。**

```json
{
  "condition": "C1",
  "observed": { "stargazers_count": 1 },
  "note": "自分の自己Star",
  "decided_at": "2026-08-11"
}
```

次回の判定では `observed` と現在値を比べる。

- `stargazers_count` が 1 のまま → 許容が有効。止まらない
- 2 になった → **許容は失効**。再び停止して報告する

一覧型（コミット著者など）は配列で記録し、記録に無い人物が現れたら失効:

```json
{
  "condition": "B4",
  "observed": { "authors": ["Maguro-JP", "dependabot[bot]"] },
  "note": "bot のコミットのみ",
  "decided_at": "2026-08-11"
}
```

F 系は記録しても次回また止まる。差分は毎回別物なので、前回の判断を根拠にできない。

## F1 の bootstrap 判定

「既定ブランチに workflow が1つも無い」ことを確認してから適用する。
**PR ブランチではなく既定ブランチを見る。**

```bash
gh api "repos/<owner>/<repo>/contents/.github/workflows?ref=<default-branch>" --jq 'length'
```

404 または 0 件なら bootstrap の条件を満たす。1件でもあれば通常どおり F1 で停止する。

MCP なら `get_file_contents` に `.github/workflows` と既定ブランチの ref を渡す。
**取得に失敗したら停止条件 A に該当**。bootstrap を適用してはいけない。

## draft の扱い

`draft_is_default: true` は「この利用者は常に draft で PR を作る」という宣言。
このとき draft は「未完成」の signal ではないので E1 では停止せず、マージ前に draft を外す。

```bash
gh pr ready <n>
```

MCP なら `update_pull_request` で `draft: false`。

`draft_is_default: false`（既定）なら draft は従来どおり停止条件。
