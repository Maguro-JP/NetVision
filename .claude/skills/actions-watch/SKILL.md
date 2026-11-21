---
name: actions-watch
description: GitHub Actions の通知を必ず受け取り、終わる前に確認を出さず、終わったら必ず結果を書くためのもの。PR を作った直後、PR のブランチに push した直後、Actions の通知（check_suite、workflow_run）を受けたとき、「通知を受け取れ」「Actions を見て」「終わってないのに聞くな」「終わったのに見てない」と言われたときに使う。PR を作ったら subscribe_pr_activity で購読し、完了の通知が来るまでマージの ❔ を出さず、来たら ❕ で結果を 1 行書いて、通ったら PR の番号と結果を添えてマージの ❔、落ちたら ci-triage に進む。Stop フックと PreToolUse フックが順番を止め、UserPromptSubmit フックが通知のターンに何をするかを差し込む。落ちた原因の特定は ci-triage、PR の作り方とマージの手順は solo-pr-flow。
---

# GitHub Actions の通知を受け取る

ユーザーが決めたこと。GitHub Actions は必ず通知させ、通知を受け取る。終わっていないのに確認することも、終わっているのに確認しないことも無くす。

## 2 つの取り違え

| 取り違え | 何が起きるか | 守り |
|---|---|---|
| 終わっていないのに確認する | Actions が動いている最中にマージの ❔ を出し、ユーザーが「いい」と言ったあとに落ちる | マージの ❔ は、その PR の完了の通知（check_suite.completed）が来てから、PR の番号と結果を添えて出す。Stop フックが止める |
| 終わっているのに確認しない | 通知が来ても読まず、結果を書かず、ユーザーが自分で見に行く | 通知のターンは、先に ReadNotifications、❕ で結果を 1 行、通ったら ❔、落ちたら ci-triage。Stop フックが結果の行を求める |

## 手順

1. PR を作ったら、同じターンで `subscribe_pr_activity`（owner、repo、PR の番号）を呼ぶ。呼ばずにターンを終えると Stop フックが止める
2. PR のブランチに push したら、前の通知は古い。次の完了の通知を待つ（push のあとの ❔ は止まる）
3. 待つ間は sleep も polling もしない。別の仕事をするか、❕ 1 行でターンを終える（background-first）
4. 通知が来たら（`queued-remote-notifications` の知らせ、または webhook の文）、ReadNotifications で読む。bot のコメント（vercel など）は読んだだけで何も出さない
5. 完了の通知なら、`❕ Actions: PR #N は通りました（<sha の先頭 7 桁>）` を 1 行。そのあとマージの確認が要るなら `❔ <リポジトリ>: PR #N をマージしてよいですか（Actions 通過）` を 1 行
6. 落ちた通知なら、`❕ Actions: PR #N は落ちました` を 1 行書き、ci-triage で原因を特定して直し、push して次の通知を待つ。マージの ❔ は出さない
7. workflow の無いリポジトリは通知が来ない。マージの ❔ の行に「Actions は無い」と書く（フックはそれを見て通す）
8. マージしたら `unsubscribe_pr_activity` で購読を外す（solo-pr-flow の後片付け）

## フック

| フック | いつ | 何をするか | 止めるか |
|---|---|---|---|
| `hooks/ci_prompt.py` | UserPromptSubmit | 通知のターンに、何をするか（ReadNotifications、❕、❔、ci-triage、待つ）を 1 行差し込む | 止めない |
| `hooks/ci_gate.py` | Stop | PR を作ったのに購読していない、Actions が終わっていないのにマージの ❔、通知を受けたのに結果を書いていない、の 3 つを見る | 止める（足りない 1 行だけを求める） |
| `hooks/ci_merge_gate.py` | PreToolUse（merge_pull_request） | その PR の完了して通った通知が記録に無ければマージしない。通知のあとに push していても止める | 止める |

通知の読み方は `scripts/ci_state.py`（会話の記録から kind、head_sha、pr、conclusion を拾い、PR ごとの最後の状態を出す）。
`ACTIONS_WATCH_OFF=1` で 3 つとも止まる（ユーザーが自分の手で入れる）。

## 使わないとき

- 落ちた原因の特定と直し方（ci-triage）
- PR を作る前の確認、マージの手順、後片付け（solo-pr-flow。ここは順番だけを守る）
- 自分の手元のテスト（code-check、verify-before-done）

## スクリプト

| 何 | すること |
|---|---|
| `scripts/ci_state.py <transcript.jsonl> [--pr Owner/Repo#N]` | 記録から Actions の通知を一覧にし、PR ごとの最後の状態（通った・落ちた・動いている、そのあとの push の有無）を出す |
| `scripts/test_ci_state.py` | 作った記録で、読み取りと 3 つのフックの判定を確かめる |

## 他のスキルとの関係

- PR の流れ全体: `solo-pr-flow`（precheck → ❔ → 作る → 購読 → 通知 → ❔ → マージ → 後片付け）
- 落ちたとき: `ci-triage`
- 待ち方: `background-first`（sleep と polling をしない）
- 通知の文の形は GitHub の webhook（check_suite、workflow_run、check_run）。クラウドのセッションに `<wake reason="external-event">` で届く
