# 検証の置き場所と、CI・PR の出し方

自律開発の検証は手元で済ませる。GitHub Actions は、手元では確かめられないものだけに使う。
private の Actions は月の分数が有限で、全部のリポジトリが同じ枠を使う。手元で通るものを
Actions でもう一度回すと、枠だけが減って、分かることは増えない。

## 手元でやるもの

次は全部、手元（このセッション）で確かめる。CI には任せない。

| 確かめるもの | どこで |
|---|---|
| lint、整形、型検査 | `.claude/hooks/check.sh`（編集のたびに走る） |
| 単体テスト、結合テスト | `.claude/hooks/test.sh`（止まる前に走る）。周の終わりに全部 |
| ビルドが通るか | 手元でビルドする |
| 文書・設定だけの変更 | 読んで確かめる。テストは要らない |
| 画面の見た目 | 手元で起動して画像で見る |

フックが無いリポジトリは、`repo-bootstrap` で置く。フックの中身が空（コマンド未検出）なら、
そのリポジトリの検証コマンドを書き足す。手元の検証が無いまま CI に頼らない。

## CI に残してよいもの

手元に無い環境や、手元では作れないものだけ。

| 残してよい | 例 |
|---|---|
| 手元に無い OS・版 | Windows、macOS、別の Node の版 |
| 配布物の組み立て | インストーラ、署名、リリース |
| 秘密が要る結合 | 本番の API キーを使う試験（Actions の secrets にしか無いもの） |

これらも、毎回の PR ではなく、必要なときだけ回す。PR に `ci` の札を付けたとき、
`workflow_dispatch`、またはリリースのときに走る形にする。

CI を足す前に、手元でできないかを先に確かめる。できるなら CI にしない。

## 区分ごとの既定

区分は2つの軸で決まる。公開か非公開か（GitHub の `private`）は毎回 API で読み、
個人か仕事か（`kind`）は `.claude/policy/<利用者名>.json` から読む。`kind` が無ければ個人。

| | 個人（`personal`） | 仕事（`work`） |
|---|---|---|
| private | PR は1日1本（`PR=daily`）。`claude/` の枝の PR では CI を回さない | 指示が無ければ周ごとに PR。CI もリポジトリの設定どおり |
| public | 制限なし。周ごとに PR、CI も回してよい（分数は無料） | CI は回してよい。PR は1日1本 |

どの区分でも、手元でできる検証は手元でやる。public で CI が無料でも、
手元で済むものを CI 待ちにすると、周が遅くなるだけ。

方針ファイルに次があれば、表より優先する。

| キー | 意味 |
|---|---|
| `pr_window` | PR を出してよい時間。例 `"Mon-Fri 10:00-18:00 JST"`。外では作業ブランチに積むだけにし、次に時間に入ったら1本にまとめて出す |
| `pr_per_day` | 1日に出してよい PR の数 |
| `ci_on_claude_prs` | `claude/` の枝の PR で CI を回すか。区分の既定を上書きする |

利用者が「このリポジトリは仕事」「平日 10〜18 時だけ PR」と言ったら、
`workspace-policy` がその場で方針ファイルに書く。次の周から効く。

## ワークフローの形

private の個人のリポジトリでは、CI に次の条件を付ける。`claude/` の枝の PR は、
`ci` の札が無ければ回らない。本線への push でも回さない。

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled]
  workflow_dispatch:
jobs:
  test:
    if: >-
      github.event_name != 'pull_request'
      || !startsWith(github.head_ref, 'claude/')
      || contains(github.event.pull_request.labels.*.name, 'ci')
```

public のリポジトリには付けない。無料で回せる CI まで止まる。

## 定期実行の CI

private で定期実行（`schedule`）の CI を置くなら、週1回以下にする。
落ちても誰も見ないまま枠を使い続けるので、結果は日報に載せる（`dev-report` が見る）。
