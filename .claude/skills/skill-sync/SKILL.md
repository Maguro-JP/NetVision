---
name: skill-sync
description: Ciel のスキルをリポジトリに取り込む・更新するとき。「スキルを入れて」「最新にして」「このリポジトリでも使えるようにして」といった依頼や、スキルが入っていないリポジトリで作業を始めるとき、Ciel を更新したあとに使う。差分があるものだけを入れ替え、同じなら何もせず黙る。アーカイブ済み・fork・自分に書き込み権限が無いリポジトリには入れない。新しいスキルを作る作業には使わない。
---

# Skill Sync

Ciel のスキルと、対象リポジトリの `.claude/skills/` を突き合わせて、差分だけ入れ替える。

**同じなら何もしないし、報告もしない。** 変化がないのに知らせるのは、
定期実行にすると邪魔になるだけ。

## フックと settings.json も入れる

スキルだけ配っても、フックの登録が無いと効かないものがある（token-efficiency のフック、feedback-session の gate.sh）。
`sync.py` は `repo-bootstrap` のひな形を元に、`.claude/hooks/*.sh` の差分と、対象の `settings.json` に無い登録を
一緒に出す。登録を足すのは、そのコマンドが指すファイルが対象にあるものだけ。対象にしかない登録は消さない。
フックは次に開いたセッションから効く。

## 入れないリポジトリ

先に確認する。**該当したら何もせずに終わる。**

| # | 条件 | 理由 |
|---|---|---|
| X1 | `archived` が true（アーカイブ済み） | 書き込めない。そもそも触るべきでない |
| X2 | `disabled` が true | 同上 |
| X3 | `fork` が true | 他人のプロジェクトの派生。上流に無関係なものを入れない |
| X4 | 自分に push 権限がない | 入れられない |
| X5 | Ciel 自身 | 元なので同期しない |
| X6 | `.claude/sync-exclude.txt` に名前がある | 明示的に外したもの |

**アーカイブ済みは対象外にするだけで、警告も報告もしない。** 意図してアーカイブしたものに
毎回言及されるのは邪魔なので。一覧に出すときだけ「対象外」と添える。

除外リストは Ciel の `.claude/sync-exclude.txt` に1行1リポジトリで書く。

```
<owner>/<repo-a>
<owner>/<repo-b>
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

差分がなければ何も出さずに終わる（終了コード 0）。
差分があれば一覧を出して 1 を返す。

```
Ciel → <repo> の .claude/skills/（見るだけ）
  入っていない   auto-dev
  Ciel より古い  ci-triage
--apply で Ciel から入れる。
```

### 3. 反映する

```bash
sync.py <対象のパス> --apply
```

一部だけなら `--only auto-dev,ci-triage`。

`lessons/LESSONS.md` だけは上書きしない。そのリポジトリで起きた失敗の記録なので、
対象側の項目を全部残し、Ciel にしかない項目（見出しで比べる）を上に足す。
対象になければ Ciel のものを置く。Ciel の複製で置き換えると、記録が消える。

### 4. コミットする

```bash
cd <対象> && git add .claude/skills && git commit -m "Ciel のスキルを更新"
```

**コミットしないと、スマホや Web のセッションには反映されない。**
リモートのセッションはリポジトリを clone し直すので、手元の変更は見えない。

PR にするかは、そのリポジトリの `.claude/policy/<ユーザー名>.json` の方針に従う。
規約が人のレビューを求めているなら PR を出して止まる。

## 報告

**差分があったときだけ、1〜2行。**

```
<repo> に auto-dev と ci-triage を入れました。コミット済み、未 push です。
```

差分がなければ黙る。「最新です」も書かない。

## 定期実行に使うとき

ローカルの cron から叩く形が一番安い。判断が要らないので、
毎回セッションを起動する必要がない。

```bash
#!/bin/sh
# 週1回。差分があるときだけ動く
git -C ~/src/Ciel pull -q
for repo in ~/src/repo-a ~/src/repo-b; do
  ~/src/Ciel/.claude/skills/skill-sync/scripts/sync.py "$repo" --apply >/dev/null || true
done
```

`--apply` はコミットまではしない。コミットと push は人が確認してから行う。

## ブランチと PR の名前

配布のブランチは `claude/SYNC-<YYYYMMDD>`、PR のタイトルは `SYNC: <1行>`（work-style「名前の付け方」）。

## 原則

- **差分がなければ黙る。** 定期実行で毎回報告しない
- **アーカイブ済み・fork・権限のないものには触らない。** 警告も出さない
- **自動でコミットしない。** 何が入れ替わったか分からない状態を作らない
- 一部だけ入れたいときは `--only` を使う。全部入れるのをデフォルトにしない
- Ciel 側を書き換えない。取り込む方向だけ

## 参考

新しいリポジトリへの配り方、テンプレートリポジトリの作り方、
Routine で回すときの注意は `references/distribution.md` を読む。
