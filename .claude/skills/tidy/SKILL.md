---
name: tidy
description: 使い終えたリモートの枝と、起きたあとの一回きりの Routine を掃除するとき。「ブランチを整理して」「枝が多い」「消していいブランチは?」「Routine の一覧がごちゃごちゃ」と言われたとき、週報を書くとき、squash マージのあとに作業枝が残っているときに使う。マージ済みか本線に含まれる枝だけを消し、開いている PR の枝と未マージの変更がある枝は残す。本線や他人の枝を消す作業には使わない。
---

# 掃除

squash マージは作業枝を残す。自律開発は毎日枝を作るので、放っておくと数十本になる。
一回きりの Routine も、起きたあと一覧に残る。どちらも溜まると、生きているものが見えなくなる。

## 枝

```
.claude/skills/tidy/scripts/tidy.py <owner/repo>            見るだけ
.claude/skills/tidy/scripts/tidy.py <owner/repo> --apply    消す
```

消すのは次のどちらかだけ。

| 枝 | 判定 |
|---|---|
| その枝の PR がマージ済み | 消す。squash でも PR の状態で分かる |
| 本線に全部含まれている | 消す |
| PR が開いている | 残す |
| PR が無く、本線に無いコミットがある | 残す。誰かの作業中かもしれない |
| 既定ブランチ | 触らない |

先に見るだけで走らせ、一覧を見てから `--apply`。

クラウドのセッションからは消せない。中継が `git push --delete` も削除の API も通さない
（2026-10-15 に確かめた。`Write access to this GitHub API path is not permitted through this proxy`）。
そのときは、手元の端末で貼って実行できる `git push origin --delete ...` の1行を出すので、
それを利用者に渡す。手元の端末（CLI やデスクトップ）のセッションなら `--apply` で消える。

リポジトリの「マージ後に枝を自動で消す」（Settings → General → Automatically delete head branches）を
有効にすると、以後は溜まらない。設定は利用者が GitHub の画面で行う。

## 一回きりの Routine

`send_later` と `run_once_at` の Routine は、起きたあとも一覧に残る（`ended_reason: run_once_fired`）。

1. `list_triggers` に `include_completed: true` を付けて出す
2. `ended_reason` が `run_once_fired` のものを `delete_trigger` で消す
3. 有効なものと、これから起きる一回きりのものは残す

## やらないこと

- 既定ブランチ、`release/` の枝、他人の枝を消すこと
- 未マージの変更がある枝を「古いから」で消すこと。残して利用者に聞く
- 見ずに `--apply` を付けること

## 他のスキルとの関係

- マージと後片付けの判断: `solo-pr-flow`
- Routine の作り方: `scheduler`
- 横断の見回り: `patrol`
