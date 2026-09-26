---
name: scheduler
description: 定期実行（Routine）を作る・見る・止めるとき。「毎日回して」「毎週月曜に」「毎月1日に」「定期実行して」「スケジュールを止めて」「いつ動くようにした?」「Routine が動かない」と言われたとき、dev-report や long-dev や auto-dev を時刻で起こしたいときに使う。リポジトリを渡してセッションを作り、その session_id に Routine を結びつけ、1回起こしてリポジトリ側の書き込みで確かめる、を守る。どのセッションからでも作れる。cron は UTC で分は 0、間隔指定（*/N）は使わず時刻を列挙する。1時間以内の繰り返しは loop の仕事で、このスキルは使わない。
---

# スケジューリング

時刻で起こすものは Routine（cron）で作る。1時間以内の繰り返しは `loop`。
Routine の1回の起動は1セッション分のトークンを使う。回数は必要最小限にする。

## 作り方（結論）

セッションの中で使える Routine のツール（`create_trigger`）には、リポジトリを渡す引数が無い。
公式の作成画面（claude.ai/code/routines、デスクトップの Routines、CLI の `/schedule`）には
リポジトリの欄があるが、クラウドセッションの中からその画面は使えない。
だから、次の順で作る。リポジトリを持つセッションを先に作り、Routine をそれに結びつける。

1. `create_session` に `source_url`（対象リポジトリの URL）と `permission_mode: auto` を渡してセッションを作る。返答の `session_context.sources` にそのリポジトリが入っていることを見る
2. `create_trigger` に `persistent_session_id`（1 の session_id）、cron、プロンプトを渡す。`create_new_session_on_fire` は使わない
3. `fire_trigger` で1回起こす
4. `scripts/check.sh <owner/repo> <ブランチの一部>` でリポジトリ側にブランチかコミットが増えるのを待つ。増えたら「動く」。増えなければ Routine を無効にして原因を見る

この順で 2026-09-24 に Daikenja で確かめた。起こしてから約30秒で `claude/routine-check` に
コミットが入った。作った側のセッション（Ciel を開いていた）とは別のリポジトリでも通る。

```
create_session(title, source_url="https://github.com/<owner>/<repo>", permission_mode="auto")
create_trigger(name, prompt, cron_expression, persistent_session_id=<session_id>, initiation="human_request")
fire_trigger(trigger_id)
scripts/check.sh <owner>/<repo> <branch-part>
```

`connectors` 引数はこの組織では使えない（エラーになる）。渡さない。

## なぜこうするか

| 起こし方 | 起きた環境 | 結果 |
|---|---|---|
| `create_new_session_on_fire` | `sources: []`、`mcp_servers: []` | リポジトリ無し。clone も push もできず、承認待ちか無言で終わる。status は緑のまま |
| `persistent_session_id` にリポジトリ無しのセッション | 作った側のリポジトリ | 対象と違うリポジトリで動く |
| `persistent_session_id` にリポジトリを渡して作ったセッション | 対象リポジトリ、auto | clone、変更、commit、push、PR 作成、マージまで通った（AdaptiveAIStudio #33、起こしてから5分） |
| 同上を約20時間放置したあと、定刻（cron）で起こす | 対象リポジトリ、auto | 09:04 JST に起きて日報の PR #39 を作りマージし、10:03 JST に long-dev が再開した（2026-09-25） |

結びつけ先のセッションをアーカイブすると、Routine は止まらず、次に起きるときに黙って
新しい空のセッション（リポジトリ無し）に結びつけ直される。仕事はせず、status は緑のまま。
2026-09-24 に Daikenja で確かめた。だから結びつけ先のセッションは消してはいけない。
セッションの寿命に依存したくないなら、画面（claude.ai/code/routines）からリポジトリを
選んで作る。それが公式の形で、毎回新しい clone で始まる。

status が緑でも仕事をしたとは限らない。このアカウントの「動いているように見えた」Routine
（DigiMon、RuleCrawler）は、9時間走って1つもコミットを残していなかった。
動いたかはリポジトリ側で見る。

## 決まり

| 決まり | 理由 |
|---|---|
| cron は UTC で書く。JST から 9 時間引く | Routine は UTC で評価される。日付をまたぐなら曜日と日もずらす |
| 分は必ず 0 | 切りの悪い時刻に起こさない。`*/N` はこの環境では作成時刻に固定されるので使わない |
| 複数回なら時刻を列挙する | `0 0,6,12 * * *` と書く。`0 */6 * * *` は使わない |
| プロンプトにリポジトリ名、スキル名、引数を必ず入れる | 圧縮で前の文脈が薄れる。プロンプトだけで何をするか分かるようにする |
| 名前は「スキル名 引数（リポジトリ）」にする | 一覧で何が動くか分かる。例: `long-dev（AdaptiveAIStudio）` |
| 1リポジトリに1セッション | long-dev と dev-report の Routine は同じセッションに結びつける。同じリポジトリで2つのセッションが同時に push しない |
| 消さずに無効にする | 再開が楽で、履歴も残る。ただしリポジトリ無しで作った古いものは、動く見込みが無いので無効にしてから作り直す |

JST と UTC の対応。

| JST | UTC |
|---|---|
| 09:00 | `0 0` |
| 10:00 | `0 1` |
| 12:00 | `0 3` |
| 21:00 | `0 12` |
| 00:00 | `0 15`（前日） |

## 手順

### 作る

1. 何を、どの周期で、何時（JST）に起こすかを1行で確かめる
2. 一覧を見て、同じ仕事の Routine が無いか見る。あれば `persistent_session_id` の先を `get_session` で見て、`session_context.sources` に対象リポジトリがあるかで判定する。あるならそのまま使う。無い（`create_new_session_on_fire` のもの、別のリポジトリのセッション）なら無効にして作り直す
   利用者がそのリポジトリを開いて long-dev を回しているセッションがあれば、新しく作らずそれに結びつける。1リポジトリに1セッション
3. cron に直す。UTC、分は 0、列挙
4. プロンプトを書く。対象リポジトリ（owner/repo）、スキル名、引数、止まる条件
5. 「作り方」の 1〜4 をそのまま行う。4 の結果（ブランチかコミット）を貼る
6. 一覧を見て、cron と次回の時刻が意図どおりか確かめる

### 見る

一覧を出し、名前・cron・有効か・次回・前回の結果・結びつけ先を並べる。
前回が失敗なら、その Routine は仕事をしていない。原因を見る。
前回が成功でも「起きた」という意味しかない。仕事ができたかはリポジトリ側で見る。

### 止める

無効にする。消さない。

### 掃除する

`send_later` の1回きりの確認は、起きたあとも Routine の一覧に残る（`ended_reason: run_once_fired`）。
1時間おきの確認を1日続けると24件残る。2026-09-24 に数えたら 218 件中 195 件がこれだった。
一覧を見るときに `run_once_fired` のものと、無効にした作り直し前のものは消す。
消す前に、有効な Routine と、これから起きる1回きりのものは残す。

### 起きた側でやること

Routine から起きたセッションは、最初に `git remote -v` を出す。
対象リポジトリが無ければ、作業に入らず1行で止まる。

```
このセッションに <owner/repo> が無い。Routine の結びつけ先が違う。scheduler で作り直す
```

30分かけてから同じことを言わない。

## 定型

| 名前 | cron（UTC） | プロンプト |
|---|---|---|
| `long-dev` | `0 1 * * *` | <owner/repo> で long-dev。止まっていれば再開、回っていればそのまま続ける |
| `dev-report daily` | `0 0 * * *` | <owner/repo> で dev-report daily を実行する。日報を書く。開発はしない |
| `dev-report weekly` | `0 0 * * 1` | <owner/repo> で dev-report weekly を実行する。週報と来週の計画。PR を整理する |
| `dev-report monthly` | `0 0 1 * *` | <owner/repo> で dev-report monthly を実行する。月報と今月の週ごとの計画 |
| `auto-dev 朝夕` | `0 0,9 * * *` | <owner/repo> で auto-dev E=1h を実行する。優先: <指示> |

long-dev と報告の3つは、同じセッションに結びつけて4つ揃えて作る。
最初の1回は `dev-report daily` を起こして確かめる。long-dev を起こすと23時間走る。

Raphael の配布は Actions の schedule で、Routine ではない。土曜 09:00 JST。

## 費用

| 周期 | 月の起動回数 |
|---|---|
| daily（報告） | 約30。報告だけなので短い |
| long-dev | 約30。1回が翌朝まで続く長いセッション |
| weekly | 4〜5 |
| monthly | 1 |
| 朝夕 | 約60 |

変更不要の日は調査だけで終わるので軽いが、ゼロではない。
Actions は PR を作った日だけ走る。private リポジトリは分数が課金される。

## やらないこと

- `create_new_session_on_fire` で作ること。空の環境になる
- 1時間以内の繰り返しを Routine にすること。`loop` を使う
- 「とりあえず毎時」で作ること。回数は仕事の周期に合わせる
- 同じ仕事の Routine を二重に作ること。作る前に一覧を見る
- 起きたあとの1回きりの確認を消さずに溜めること
- 「動く」と言う前にリポジトリ側を見ないこと

## 他のスキルとの関係

- 日報・週報・月報: `dev-report`
- 終わりの無い自律開発: `long-dev`
- 周ごとの作業: `auto-dev`
- 1時間以内の繰り返し: `loop`
