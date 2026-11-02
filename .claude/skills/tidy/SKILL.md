---
name: tidy
description: リポジトリを散らかさないとき、月に 1 回の整理整頓（long-dev や skill-upkeep の Routine の月初の回、「整理して」と言われたとき）、使い終えたリモートのブランチと、実行されたあとの一回きりの Routine を掃除するとき。「散らかさないで」「PR が多い」「ブランチが多い」と言われたとき、PR を分けるか迷ったとき、「ブランチを整理して」「ブランチが多い」「消していいブランチは?」「Routine の一覧がごちゃごちゃ」と言われたとき、週報を書くとき、squash マージのあとに作業ブランチが残っているときに使う。マージ済みか main ブランチに含まれるブランチだけを消し、開いている PR のブランチと未マージの変更があるブランチは残す。main ブランチや他人のブランチを消す作業には使わない。
---

# 掃除

squash マージは作業ブランチを残す。自律開発は毎日ブランチを作るので、放っておくと数十本になる。
1回きりの Routine も、実行されたあと一覧に残る。どちらも溜まると、生きているものが見えなくなる。

## ブランチ

```
.claude/skills/tidy/scripts/tidy.py <owner/repo>            見るだけ
.claude/skills/tidy/scripts/tidy.py <owner/repo> --apply    消す
```

消すのは次のどちらかだけ。

| ブランチ | 判定 |
|---|---|
| そのブランチの PR がマージ済み | 消す。squash でも PR の状態で分かる |
| main ブランチに全部含まれている | 消す |
| PR が開いている | 残す |
| PR がなく、main ブランチにないコミットがある | 残す。誰かの作業中かもしれない |
| デフォルトブランチ | 触らない |

先に見るだけで走らせ、一覧を見てから `--apply`。

クラウドのセッションからは消せない。プロキシが `git push --delete` も削除の API も通さない
（2026-10-15 に確かめた。`Write access to this GitHub API path is not permitted through this proxy`）。
そのときは、手元の端末で貼って実行できる `git push origin --delete ...` の1行を出すので、
それをユーザーに渡す。手元の端末（CLI やデスクトップ）のセッションなら `--apply` で消える。

リポジトリの「マージ後にブランチを自動で消す」（Settings → General → Automatically delete head branches）を
有効にすると、以後は溜まらない。設定はユーザーが GitHub の画面で行う。

## 一回きりの Routine

`send_later` と `run_once_at` の Routine は、実行されたあとも一覧に残る（`ended_reason: run_once_fired`）。

1. `list_triggers` に `include_completed: true` を付けて出す
2. `ended_reason` が `run_once_fired` のものを `delete_trigger` で消す
3. 有効なものと、これから実行される1回きりのものは残す

## 終わったセッション

並列で立てたセッションは、終わっても一覧に残る。残すほど一覧が読めなくなり、紐づいたルーティンの行き先も分からなくなる。

1. `list_sessions` で、自分（か自分の親）が立てたもの（`parent_session_id` がある、tag が `feedback-session:auto` か `ciel:`、tag `config:meta-mcp-own-entry`）を選ぶ
2. 終わっているもの（IDLE か COMPLETED で、紐づく有効なルーティンが無く、3 日以上動いていない。PR がマージ済みか close）を `archive_session` で消す（アーカイブ。`unarchive_session` で戻せる）。`get_session` の `post_turn_summary` と `list_triggers` の `persistent_session_id` で見る
3. 人が開いたセッション（`origin` が ios・web・desktop で、上の目印が無い）、`feedback-session:keep` の付いたもの、答え待ちで止まっているもの（BLOCKED）は残す。残したものは週報に 1 行で書く
4. その週に立てたものは、終わった回の中で消す（feedback-session の 5）。週 1 回の skill-upkeep で session-watch の judge.py を回して消す。ツールの権限で消せなかったものは、名前を週報に書く。人には聞かない

## 散らかさない（予防）

掃除より先に、散らかさない。1 日に表の直しだけの PR を 4 本出し、配布ブランチを -2 から -7 まで増やした日があった。

| 場所 | 決まり |
|---|---|
| PR | 変更のまとまりごとに 1 本。表（deliverables.md）・週報・lessons の更新は、その変更と同じ PR に入れる。表だけの PR を作らない |
| コミット | 出したあとに「番号の直し」「権限の直し」のコミットを足さない。precheck を通し、確認を取ってから出す（solo-pr-flow「出す前の確認」）。main へは squash の 1 コミット |
| ブランチ | 配布ブランチは 1 日 1 本（`claude/SYNC-<YYYYMMDD>`）に積む。`-2` `-3` と増やさない。マージ後は消す。消せない環境（GitHub のプロキシが削除を止める）では、ユーザーがリポジトリの設定で「マージ後に自動で消す」を入れる。残っている一覧は週報に書く |
| Actions | PR を作るたび・push するたびに走る。小さな変更はまとめて 1 回にし、確認を取ってから push する |
| Release とタグ | 頼まれたときだけ作る。確認ゲート（pr_gate.py）が止める |
| ディレクトリとファイル | 一時ファイルと作業用のスクリプトはリポジトリに置かない（セッションの scratchpad に置く）。生成物は決まった場所（`docs/reports/weekly/`、`docs/reports/deliverables.md`、`docs/reports/pending.md`）だけ。同じ内容の文書を 2 か所に書かない |
| 履歴 | force push は自分の作業ブランチだけ。main と他人のブランチにはしない |
| テキスト | CLAUDE.md の「ユーザーの決めたこと」は 1 決まり 1 行。長い説明はスキルに置く。会話は 3 行まで。長いログは貼らず、最後の行と場所を書く |

## 月に 1 回の整理整頓

長く回すと、予防だけでは散らかる。月の最初の実行（1 日の回。平日だけの Routine なら最初の平日）に、Routine の中で行う。
「整理して」と言われたときも同じ手順。結果は月報（dev-report monthly）に「整理整頓」の節として書き、報告と同じ PR に入れる。

1. `python3 .claude/skills/tidy/scripts/tidy_report.py` を動かす。消せるリモートブランチ、残すブランチ、余計なファイル、
   大きなファイル、空のディレクトリ、文書の検査（docs-upkeep）、古い判断待ち、古い PR と Issue を Markdown で出す
2. 消す。消せるリモートブランチは上の「ブランチ」の手順で（消せない環境では一覧を月報に書き、ユーザーが消す）。
   実行済みの一回きりのルーティンは上の「一回きりの Routine」の手順で。終わったセッションは上の「終わったセッション」の手順で。余計なファイルは消してコミットし、`.gitignore` に足す
3. 直す。docs-upkeep の誤りを直す。30 日より古い判断待ちは、戻せる判断なら決めて進め、そうでなければ月報に「まだ待っている」と 1 行で書く
4. 残す。開いている PR と Issue、人が作ったブランチは消さない。古いものは月報に一覧で書くだけ
5. 月報に「整理整頓」の節を書く。消したもの、直したもの、残したものと理由。報告の PR に入れ、整理整頓だけの PR を作らない

## やらないこと

- デフォルトブランチ、`release/` のブランチ、他人のブランチを消すこと
- 未マージの変更があるブランチを「古いから」で消すこと。残してユーザーに聞く
- 見ずに `--apply` を付けること

## 他のスキルとの関係

- マージと後片付けの判断: `solo-pr-flow`
- Routine の作り方: `scheduler`
- 横断の見回り: `patrol`
