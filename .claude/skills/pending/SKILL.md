---
name: pending
description: 自律開発が脇に置いた判断待ち（docs/reports/pending.md）を、リポジトリを横断して見る・答えるとき。「判断待ちは?」「何を決めればいい?」「決めてほしいことある?」「判断待ち 6 は A で」「答えを入れて」と言われたとき、日報や patrol で答え待ちの件数を見たときに使う。答えの無い項目だけを一覧にし、答えは pending.md に書いて PR でマージし、紐づけ先のセッションに知らせる。項目を新しく作る側（auto-dev が脇に置くとき）には使わない。
---

# 判断待ち

自律開発は、人の判断が要る件を `docs/reports/pending.md` に書いて脇に置き、別の作業を進める。
答えが入るまでその件は止まったまま。答えが溜まると、長く回しても進まない。
このスキルは、答え待ちをまとめて見て、会話から答えるためのもの。

## 見る

```
.claude/skills/pending/scripts/pending.py list <owner/repo> <owner/repo> ...
```

答えのない項目だけを、番号・見出し・何を決めてほしいか・推す案で出す。
対象のリポジトリは、long-dev の Routine が付いているもの（`list_triggers` の名前から取る）。

## 答える

```
.claude/skills/pending/scripts/pending.py answer <owner/repo> <番号> <答え>
```

- `docs/reports/pending.md` のその項目の「答え:」に、答えと日付を書く
- ブランチ `claude/ANSWER-<番号>-<日付>` で PR を作り、squash でマージする。文書だけなので CI は走らない（走る設定のリポジトリでは `.claude/` と `docs/` を paths-ignore にしてある）
- 答えはユーザーの言葉のまま書く。選択肢の記号だけなら「A」でよい。言い換えない

マージしたあと、紐づけ先のセッションに知らせる。知らせなくても翌朝のサイクルで取り込まれるが、
すぐ進めたいときは1回きりの Routine で送る（`scheduler`）。

```
create_trigger(name="判断待ちの答え（<repo>）", run_once_at=<数分後>, persistent_session_id=<long-dev のセッション>,
  prompt="docs/reports/pending.md の判断待ち <番号> に答えが入った。git fetch origin <デフォルトブランチ> して取り込み、その件を進める。")
```

送った1回きりの Routine は、実行されたあと一覧に残る。次に見たときに消す（`scheduler` の「掃除する」）。

## 決まり

- 答えを書くのはユーザーが言ったときだけ。推す案を勝手に答えにしない
- 答えを書いた項目は消さない。「取り込み済み」は自律開発側が付ける
- 番号で指定できないリポジトリ（見出しに番号がない）は、見出しの一部で指定する

## 他のスキルとの関係

- 項目を作る側（脇に置く条件）: `auto-dev`
- 横断の見回り（答え待ちの件数も出す）: `patrol`
- セッションへの知らせ方: `scheduler`
