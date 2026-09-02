---
name: skill-sync
description: Ciel のスキルをリポジトリに取り込む・更新するとき。「スキルを入れて」「最新にして」「このリポジトリでも使えるようにして」といった依頼や、スキルが入っていないリポジトリで作業を始めるとき、Ciel を更新したあとに使う。差分があるものだけを入れ替え、同じなら何もせず黙る。凍結済み・fork・自分に書き込み権限が無いリポジトリには入れない。新しいスキルを作る作業には使わない。
---

# Skill Sync

Ciel のスキルと、対象リポジトリの `.claude/skills/` を突き合わせて、差分だけ入れ替える。

**同じなら何もしないし、報告もしない。** 変化が無いのに知らせるのは、
定期実行にしたときに邪魔になるだけ。

## 入れないリポジトリ

先に確認する。**該当したら何もせずに終わる。**

| # | 条件 | 理由 |
|---|---|---|
| X1 | `archived` が true（凍結済み） | 書き込めない。そもそも触るべきでない |
| X2 | `disabled` が true | 同上 |
| X3 | `fork` が true | 他人のプロジェクトの派生。上流に無関係なものを入れない |
| X4 | 自分に push 権限が無い | 入れられない |
| X5 | Ciel 自身 | 元なので同期しない |
| X6 | `.claude/sync-exclude.txt` に名前がある | 明示的に外したもの |

**凍結済みは対象外にするだけで、警告も報告もしない。** 意図して凍結したものに
毎回言及されるのは邪魔なので。一覧に出すときだけ「対象外」と添える。

除外リストは Ciel の `.claude/sync-exclude.txt` に1行1リポジトリで書く。

```
Maguro-JP/Sleep-Sound-Generator
Maguro-JP/mock-up
```

## 手順

### 1. Ciel を用意する

```bash
git clone https://github.com/Maguro-JP/Ciel.git   # 初回
git -C Ciel pull                                   # 2回目以降
```

Ciel は public なので認証は要らない。

### 2. 突き合わせる

```bash
Ciel/.claude/skills/skill-sync/scripts/sync.py <対象のパス>
```

差分が無ければ何も出さずに終わる（終了コード 0）。
差分があれば一覧を出して 1 を返す。

```
  追加 auto-dev  (入っていません)
  更新 ci-triage  (古いままです)
```

### 3. 反映する

```bash
sync.py <対象のパス> --apply
```

一部だけなら `--only auto-dev,ci-triage`。

### 4. コミットする

```bash
cd <対象> && git add .claude/skills && git commit -m "Ciel のスキルを更新"
```

**コミットしないと、スマホや Web のセッションには反映されない。**
リモートのセッションはリポジトリを clone し直すので、手元の変更は見えない。

PR にするかは、そのリポジトリの `.claude/policy/<利用者名>.json` の方針に従う。
規約が人のレビューを求めているなら PR を出して止まる。

## 報告

**差分があったときだけ、1〜2行。**

```
NetVision に auto-dev と ci-triage を入れました。コミット済み、未 push です。
```

差分が無ければ黙る。「最新です」も書かない。

## 定期実行に使うとき

ローカルの cron から叩く形が一番安い。判断が要らないので、
毎回セッションを起こす必要がない。

```bash
#!/bin/sh
# 週1回。差分があるときだけ動く
git -C ~/src/Ciel pull -q
for repo in ~/src/NetVision ~/src/AdaptiveAIStudio ~/src/DigiMon; do
  ~/src/Ciel/.claude/skills/skill-sync/scripts/sync.py "$repo" --apply >/dev/null || true
done
```

`--apply` はコミットまではしない。コミットと push は人が確認してから行う。

## 原則

- **差分が無ければ黙る。** 定期実行で毎回報告しない
- **凍結済み・fork・権限の無いものには触らない。** 警告も出さない
- **自動でコミットしない。** 何が入れ替わったか分からない状態を作らない
- 一部だけ入れたいときは `--only` を使う。全部入れるのを既定にしない
- Ciel 側を書き換えない。取り込む方向だけ

## 参考

新しいリポジトリへの配り方、テンプレートリポジトリの作り方、
Routine で回すときの注意は `references/distribution.md` を読む。
