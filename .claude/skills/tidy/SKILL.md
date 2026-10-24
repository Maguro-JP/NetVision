---
name: tidy
description: 使い終えたリモートのブランチと、実行されたあとの一回きりの Routine を掃除するとき。「ブランチを整理して」「ブランチが多い」「消していいブランチは?」「Routine の一覧がごちゃごちゃ」と言われたとき、週報を書くとき、squash マージのあとに作業ブランチが残っているときに使う。マージ済みか main ブランチに含まれるブランチだけを消し、開いている PR のブランチと未マージの変更があるブランチは残す。main ブランチや他人のブランチを消す作業には使わない。
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

## やらないこと

- デフォルトブランチ、`release/` のブランチ、他人のブランチを消すこと
- 未マージの変更があるブランチを「古いから」で消すこと。残してユーザーに聞く
- 見ずに `--apply` を付けること

## 他のスキルとの関係

- マージと後片付けの判断: `solo-pr-flow`
- Routine の作り方: `scheduler`
- 横断の見回り: `patrol`
