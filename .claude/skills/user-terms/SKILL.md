---
name: user-terms
description: 略語・訳語・造語を、ユーザーが使った語に直すとき。「略語を使うな」「その言い方は使っていない」「俺の言い方に合わせろ」「訳語を作るな」と言われたとき、会話を出す前、README・docs・記録を書いたあと、ユーザーの発言に新しい語が出たときに使う。対応表（references/user-terms.md）のユーザーの語を正とし、Stop フックが会話の中の違う言い方を差し戻し、precheck が文書の差分を注意として出す。ユーザーの発言から語を数えて表を育てる。開発者の間で通る語の既定は ja-proofread の glossary.md で、重なったらこちらが先。
---

# ユーザーの語に合わせる

ユーザーは自分の語で読む。私が「プルリク」「リポ」「Routine」「片付け」「JST」と書くと、
読む側が自分の語に言い換える手間を負う。略語も訳語も造語も、ユーザーが使った語に直す。
正は `references/user-terms.md` の左の列で、ユーザーの発言と CLAUDE.md の「ユーザーの決めたこと」から取る。
私が良いと思う語は正にしない。

## 手順

### 1. 会話を出す前

表の「直す言い方」を使っていないか見る。Stop フック `hooks/terms_check.py` が、止まる直前の返答を表と突き合わせ、
見つかれば「「プルリク」→「PR」」の形で差し戻す。指された語を左の語に直して出し直す。
コード、引用（「…」）、URL、パス、`<...>` の中は見ないので、コマンドやキーはバッククォートに入れて書く。

### 2. 文書を書いたあと

```
python3 .claude/skills/user-terms/scripts/terms.py --diff origin/main
```

差分で足した行（`.md` と `.txt`）の違う言い方を、ファイルと行で出す。precheck が同じものを動かし、注意として数える。
止める理由にはしないが、その文書を直すときに一緒に直す。1 つのファイルを全部見るなら `--files <path>`。

### 3. ユーザーの発言に、表に無い語が出たとき

`hooks/terms_learn.py`（UserPromptSubmit）が、発言のカタカナ語・英数字の語・漢字の連なりを数えて
`~/.claude/state/user-terms/terms.json` に残す。何も差し込まない。

```
python3 .claude/skills/user-terms/hooks/terms_learn.py --candidates
```

2 回以上使われていて表に無い語を、回数の順に出す。自分がその語を別の言い方で書いていれば、表に行を足す。
週 1 回の skill-upkeep で見る。ユーザーに「その言い方は使っていない」と言われたら、その場で行を足す（follow-instructions）。

## 表の書き方

| 列 | 書くこと |
|---|---|
| ユーザーの語 | 正。ユーザーの発言か CLAUDE.md にある語。略語に対応する語が無ければ、省略しない形か日本語 |
| 直す言い方 | 「、」で区切る。英数字は単語の境界で（大文字小文字は区別しない）、カタカナはカタカナの境界で当てる。正規表現も書けるが「|」は表の区切りなので書かない |
| 出どころ | 発言、CLAUDE.md、略語 のどれか |

ユーザーの語を別の行の直す言い方に書かない。`scripts/terms.py --table` が矛盾を数える。
表そのものと ja-proofread の `glossary.md` は検査の対象にしない。1 行だけ外すなら `<!-- user-terms: ignore -->`。

## フック

| フック | いつ | すること |
|---|---|---|
| `hooks/terms_check.py` | Stop | この返答の文を表と突き合わせ、違う言い方があれば差し戻す。数を `~/.claude/state/user-terms/metrics.jsonl` に残す |
| `hooks/terms_learn.py` | UserPromptSubmit | 発言の語を数える。通知、フックの差し戻し、長い貼り付けは数えない |

`USER_TERMS_OFF=1` で止まる。保持先は `USER_TERMS_STATE`。登録は `repo-bootstrap/templates/settings.json` にあり、skill-sync が配る。
テストは `scripts/test_terms.py`。

## 他のスキルとの関係

- 開発者の間で通る語の既定（枝→ブランチ、門→ゲート）: `.claude/skills/ja-proofread/references/glossary.md`。ユーザーが別の語を使っていれば、こちらの表が先（docs/GUIDELINES.md の 10）
- 訂正されたときの直し方: `follow-instructions`。同じ訂正が 2 回目なら CLAUDE.md の「ユーザーの決めたこと」にも足す
- 表を育てる周期と、数の読み方: `skill-upkeep`
- 文書の一覧と言及先: `docs-upkeep`。中身の精査: `content-audit`
