---
name: docs-upkeep
description: README と docs とスキルの文を、今のリポジトリに合わせて最新に保つとき。スキル・フック・スクリプトを足した・消した・名前を変えたあと、README や docs を書いたあと、PR を出す前（validate と precheck が動かす）、「README が古い」「リンクが切れている」「整理して」「最新化して」と言われたときに使う。壊れたリンク、無いファイルへの言及、スキル一覧の抜けと余り、辿れない文書を誤りとして止め、古くなる数字、予定の語、重複した段落、強調の記号を注意として出す。日本語の言い回しは ja-proofread、中身の精査は content-audit。
---

# README と docs を今のリポジトリに合わせる

文書は書いた日の状態で止まり、コードは動く。リンクが切れ、消したファイルに言及し、足したスキルが一覧に無く、
「予定」のままの文が残る。読む人はそれを今の状態だと思う。だから機械で突き合わせ、合わないものは PR の前に止める。

## 決まりの出どころ

同じ目的の公開スキルを探した（2026-10-26）。sync-docs（README と CLAUDE.md の古い節とリンクを見る）、update-project（git の差分と
`.claude/skills/*/SKILL.md` を突き合わせる）、docs-workflow（古さ・リンク切れ・版のずれを精査する）、consistency-enforcement
（README を正として他の文書と数を合わせる）があった。どれも手順の文だけで検査のコードは無く、ライセンスの表記も無い。
「言及先の実在を検査する」「一覧を正と突き合わせる」の 2 つの考えを取り、検査は `scripts/docs_check.py` に書いた。

## 検査（docs_check.py）

| 種類 | 見るもの | 扱い |
|---|---|---|
| 壊れたリンク | README、CONTRIBUTING、docs、SKILL.md と references の `[x](path)` で、相対の path が無い | 誤り |
| 無いファイルへの言及 | バッククォートの中の `dir/file.ext` が無い | 誤り |
| スキル一覧の抜けと余り | `.claude/skills/` の名前が README と docs/USAGE.md の一覧に無い。一覧にあるが無い | 誤り |
| 辿れない文書 | `docs/*.md` が README か USAGE から link されていない（reports は除く） | 誤り |
| 古くなる数字 | 「（N 件）」のような、直すたびに変わる数 | 注意 |
| 予定の語 | 「する予定」「TBD」「あとで直す」 | 注意 |
| 重複した段落 | 同じ段落が 2 つの文書にある | 注意 |
| 強調の記号 | `**` | 注意 |

`validate.sh` の「ドキュメント」の節が動かし、誤りがあれば validate が落ちる。precheck.py は validate を動かすので、PR の前に止まる。

```bash
python3 .claude/skills/docs-upkeep/scripts/docs_check.py          # リポジトリの root で
```

## 書き方の決まり

- README は、何か・公開の注意・構成・使い方・スキル一覧（全部）・文書の一覧・ライセンス、の順。説明は 1 行
- 数（件数、文字数、本数）と日付は本文に書かない。変わるたびに古くなる。記録（週報、成果物の表）だけに書く
- 「予定」を書かない。やるなら成果物の表に、やらないなら書かない
- 同じ段落を 2 か所に書かない。片方に書いて、もう片方から link する
- 文書は README か USAGE から辿れるようにする。辿れない文書は消えたのと同じ
- 強調の記号（`**`）を使わない。見出しか文で示す
- スキルを足した・消した・名前を変えたら、同じ PR で README と USAGE の一覧を直す

## 他のスキルとの関係

- 日本語の言い回し: `ja-proofread`。中身（機密、名前、不快な表現）: `content-audit`
- スキルの一覧と description の質: `skill-audit`
- 出す前の検査の 1 本化: `verify-before-done` の precheck.py
