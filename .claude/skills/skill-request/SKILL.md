---
name: skill-request
description: どのセッションからでも Ciel にスキルを要請し、Ciel がそれを受けて作る・直す・配るためのもの。作業中に「こういうスキルが欲しい」「このスキルが足りない」「毎回同じ手順を組み立てている」と気づいたとき、ユーザーに「スキルにして」「Ciel に頼んで」「要請を出して」と言われたとき、Ciel のセッションで「要請を見て」「要請に対応して」と言われたとき、週 1 回の skill-upkeep の観察で使う。要請はそのリポジトリの docs/reports/skill-requests.md に 1 件 1 節で書き、その作業の PR に入れる。Ciel は手元の clone から集めて作り、PR とマージのあとに配り、状態を書き戻す。スキルを作る手順そのものは skill-upkeep の新作、1 つのスキルの診断は skill-audit。
---

# スキルの要請

スキルが足りないと気づくのは、Ciel ではなく各リポジトリのセッション。会話で言っても、そのセッションが閉じれば消える。
だから要請はファイルに書く。Ciel は手元の clone から集めるので、相手のリポジトリに書き込む権限は要らない。

## 要請する側（どのリポジトリのセッションでも）

1. 要請を書く。`docs/reports/skill-requests.md` に 1 件 1 節（スクリプトが形をそろえる）

```
python3 .claude/skills/skill-request/scripts/requests.py --add --title "<題>" --what "<何が欲しいか>" --scene "<いつ使うか。発動の言葉>" --now "<今どう困るか>" [--example "<入力と期待する出力>"] [--done "<完成条件>"]
```

2. その作業の PR（か、いつものコミット）に入れる。要請だけの PR を作らない（散らかさない）
3. 急ぐときは、Ciel のセッションに「要請を見て」と 1 行言う。言わなければ、Ciel の週 1 回の skill-upkeep が拾う
4. Ciel に push できるセッションなら、Ciel のブランチ `claude/REQ-<題>-<日付>` に `docs/requests/<日付>-<題>.md` を同じ形で置いてもよい。Ciel は public なので、非公開のリポジトリの名前・ID・費用を書かない（「あるリポジトリ（絵のアプリ）」のように伏せる）。PR は作らない。Ciel 側が作る

要請に書くもの: 何が欲しいか、場面（発動の言葉）、今どう困るか、例、完成条件。自分の案（設計、名前）は書いてよいが、決めるのはユーザー。

## 受ける側（Ciel のセッション）

1. 集める

```
python3 .claude/skills/skill-request/scripts/requests.py --collect
```

   手元の全リポジトリの `docs/reports/skill-requests.md` と、Ciel の `docs/requests/*.md` と、origin の `claude/REQ-*` ブランチから、未対応と対応中を 1 つの表にする
2. 1 件ずつ、skill-upkeep の「新作」の手順で作る。先に公開されているスキルを探し、使えるなら取り込む（docs/SOURCES.md）。1 要請 1 PR で、同じ PR に README と docs の一覧を直す
3. Ciel の PR を出す前に、要請した側の節の状態を `対応中（Ciel #N）` にする（次の配布のコミットに入れる）

```
python3 .claude/skills/skill-request/scripts/requests.py --mark "<題>" --status "対応中（Ciel #N）" --repo /home/user/<リポジトリ>
```

4. マージして配ったら `対応済み（Ciel #N）` に書き換える。作らないと決めたものは `見送り（理由）`。どちらも配布のコミットで相手のリポジトリに入る
5. 週報の「新作」に、要請の元（非公開の名前は伏せる）と Ciel の PR を書く

## 決まり

- 要請は作業の記録と同じで、依頼のやり取りや口調は写さない。何が欲しいか・場面・困りごと・完成条件だけ
- 要請を受けても、PR とマージはユーザーの返事のあと（solo-pr-flow、pr_gate）。要請した側のセッションの返事は、ユーザーの返事ではない
- 対応しない判断は Ciel が決めず、見送りの理由を書いて週報に出す。決めるのはユーザー

## スクリプト

| 何 | すること |
|---|---|
| `scripts/requests.py` | `--add` で要請を書く、`--collect` で集める、`--mark` で状態を書き換える |
| `scripts/test_requests.py` | 一時的なリポジトリで、書く・集める・書き換えるを確かめる |

## 他のスキルとの関係

- スキルを作る・直す手順と週 1 回の観察: `skill-upkeep`（観察で `--collect` を回す）
- 配る: `skill-sync`。状態の書き戻しは配布のコミットに乗せる
- 失敗の記録は `lessons`、判断待ちは `pending`。要請はどちらでもない「作ってほしいもの」
