# チェックの読み方

## 目次

- [状態を取る](#状態を取る)
- [status と conclusion](#status-と-conclusion)
- [cancelled の扱い](#cancelled-の扱い)
- [ログを取って落ちたステップを特定する](#ログを取って落ちたステップを特定する)
- [チェックの性質を見分ける](#チェックの性質を見分ける)
- [基のブランチで再現するか確かめる](#基のブランチで再現するか確かめる)
- [再実行](#再実行)

## 状態を取る

| 欲しいもの | GitHub CLI | MCP (github) |
|---|---|---|
| PR の現在の head | `gh pr view <n> --json headRefOid` | `pull_request_read` method=`get` の `head.sha` |
| チェック一覧 | `gh pr checks <n>` | `pull_request_read` method=`get_check_runs` |
| ワークフローの実行 | `gh run list --branch <枝>` | `actions_list` method=`list_workflow_runs` |
| 実行の中のジョブ | `gh run view <run-id> --json jobs` | `actions_list` method=`list_workflow_jobs` |
| ジョブのログ | `gh run view --job <job-id> --log-failed` | `get_job_logs` |

`gh run view --log-failed` は**失敗したステップのログだけ**を出す。
全体のログより先にこちらを試す。

## status と conclusion

`status` が `completed` になって初めて `conclusion` を見る。

| status | 意味 |
|---|---|
| `queued` | 実行待ち |
| `in_progress` | 実行中 |
| `completed` | 終了。`conclusion` を見る |
| `waiting` | 承認待ちなど |

| conclusion | 扱い |
|---|---|
| `success` | 合格 |
| `skipped` | 条件に当たらず実行されていない。合格として扱う |
| `neutral` | 合否を主張していない。合格として扱う |
| `cancelled` | 中断。失敗ではない |
| `failure` | 不合格 |
| `timed_out` | 時間切れ。不合格として扱うが、再実行で通ることがある |
| `action_required` | 人の操作が要る。承認待ちや手動の入力 |
| `stale` | 古い。現在の結果を見る |

## cancelled の扱い

`concurrency` に `cancel-in-progress: true` が設定されていると、
新しいコミットを push した時点で走っていた実行が `cancelled` になる。

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

この設定があるリポジトリでは、`cancelled` は日常的に出る。
**これを失敗として報告すると、直っているものを落ちていると言うことになる。**

`cancelled` を見たときの確認。

```bash
gh run list --workflow <ファイル名> --branch <枝> --limit 5
```

同じワークフローのより新しい実行があれば、そちらが本当の結果。
無ければ、手動での中断や実行時間の上限など別の原因なので調べる。

## ログを取って落ちたステップを特定する

```bash
# 失敗したジョブだけ
gh run view <run-id> --json jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | {name, databaseId}'

# そのジョブの、失敗したステップのログだけ
gh run view --job <job-id> --log-failed
```

MCP なら `get_job_logs`。失敗したジョブに絞る指定があるならそれを使う。

ログから拾う手がかり。

```
##[error]        Actions が付けるエラー行
Error:           多くのツールの共通形式
FAILED / FAIL    テストランナー
exit code        終了コード
Traceback        Python
error TSxxxx     TypeScript
```

**全部読まない。** 失敗したステップの周辺だけを見る。
ログが数千行あることは珍しくない。

報告にはログを貼らず、原因を示す行だけを引く。

## チェックの性質を見分ける

ワークフローの発火条件を見る。

```bash
grep -A5 '^on:' .github/workflows/*.yml
```

| 発火条件 | PR での扱い |
|---|---|
| `pull_request` | PR で走る。結果を待つ |
| `push` （枝の除外に注意） | PR とは別に走ることがある |
| `workflow_dispatch` のみ | 手動。PR では走らない。待たない |
| `release` のみ | 公開時のみ。待たない |

規約を検査するワークフローの見分け方。

- ファイル名や `name` に `policy`、`規約`、`rule` が入っている
- 中身が PR の base や枝、他の PR の状態を見ている
- コードをチェックアウトせず、`github-script` や API だけで完結している

3つ目が確実な手がかりになる。**コードを見ていないなら、コードの検査ではない。**

## 基のブランチで再現するか確かめる

自分の差分が原因でない失敗を直そうとしない。

```bash
gh run list --branch main --workflow <ファイル名> --limit 3
```

基のブランチでも同じチェックが落ちているなら、その PR の問題ではない。
1行で報告して、回復を待つ。

```
CI が <チェック名> で落ちていますが、main でも同じ状態です。
こちらの差分が原因ではないので、回復してから再実行します。
```

## 再実行

再実行で直るのは、外部の一時的な不調が原因のときだけ。

```bash
gh run rerun <run-id> --failed
```

**2回続けて同じところで落ちるなら、再実行では直らない。** 原因を調べる。

再実行を繰り返すのは、時間と実行枠を使うだけで何も分からない。
