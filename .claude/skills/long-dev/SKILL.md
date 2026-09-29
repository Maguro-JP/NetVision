---
name: long-dev
description: 終わりの無い自律開発を定期実行に載せるとき。「ずっと開発を続けて」「長期で回して」「放っておいても良くなるようにして」「継続的に改善して」と言われたとき、/long-dev を打ったときに使う。リポジトリごとに Routine を1つ作り、毎朝その日の報告（日報・週報・月報）を書かせてから auto-dev を起こす。L= と E= は auto-dev にそのまま渡す。自分では開発の周を回さない。周の中身は auto-dev、報告は dev-report、Routine の作り方は scheduler。単発の作業や、今だけ回したいときは auto-dev を直接使う。
---

# 長期開発

long-dev は、auto-dev と報告を毎日起こす Routine を作るスキル。自分では周を回さない。

| | auto-dev | long-dev |
|---|---|---|
| 役目 | 開発の周を回す | auto-dev と報告を毎日起こす Routine を作る |
| 日報・週報・月報 | 作らない | 毎朝の Routine で書かせる |
| L= と E= | 自分で使う | auto-dev にそのまま渡す |

周の間隔（`L=`）と1日に回す長さ（`E=`）で、リポジトリごとに1日に使うトークンを決められる。
長期開発の中身を auto-dev と分けていると、止まる条件や間隔の扱いが二重になり、片方だけ直して食い違う。

## 引数

```
/long-dev L=1h E=8h 優先: 取り込みの精度
```

| 引数 | 既定 | 意味 |
|---|---|---|
| `L=` | `auto` | auto-dev の周の間隔。`auto` は間を空けずに回す |
| `E=` | `23h` | 1日に回す長さ。翌朝の Routine の前に止まる |
| 残り | なし | auto-dev に渡す優先の指示 |

## 作るもの

リポジトリごとに Routine を1つだけ作る。4つに分けると一覧が埋まって見づらく、
報告と開発の順番もずれる。

- 名前: `long-dev（<リポジトリ>）`
- 時刻: 毎日 09:00 JST（cron `0 0 * * *`）
- 結びつけ先: そのリポジトリを渡したセッション。作り方は `scheduler`

プロンプトの形。

```
<owner/repo> の long-dev。今日の日付（JST）で次を順にやる。
1. 報告: 1日なら dev-report monthly、月曜なら dev-report weekly、それ以外は dev-report daily
2. 開発: auto-dev L=<L> E=<E> <優先の指示>
回っている auto-dev があれば、報告だけ書いてそのまま続ける（新しい指示として扱わない）。
```

## 手順

1. Routine の一覧を見る。同じリポジトリの long-dev があれば、作らずにそれを使う
   - 古い形（long-dev と dev-report を別々に4つ）があれば、新しい1つを作ってから古いものを消す
2. 結びつけ先のセッションを用意する。そのリポジトリを開いて回しているセッションがあればそれを使う。無ければ `scheduler` の手順で作る
3. Routine を作る
4. 定刻を待たずに、その場で1回起こす（`fire_trigger`）。作っただけで止まると、次の 09:00 JST まで何もしない
5. リポジトリにコミットか報告が増えたのを見てから、「動いた」と言う

## 周の中身

auto-dev のまま。止まる条件、判断待ち（`docs/reports/pending.md`）、会話に出すもの、
周の間を空けないことは、すべて auto-dev に書いてある。

long-dev の Routine から起きた auto-dev は、`references/principles.md`（プロダクトを
時間とともに良くする原則）を判断の土台に足す。記録は周ごとに `docs/knowledge/` に書く。

## 他のスキルとの関係

- 周ごとの作業: `auto-dev`。原則は `references/principles.md`
- 日報・週報・月報: `dev-report`
- 定期実行の設定: `scheduler`
- PR のマージと後片付け: `solo-pr-flow`
