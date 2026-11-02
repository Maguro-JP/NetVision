---
name: vuln-watch
description: 脆弱性を見張るとき。「脆弱性を調べて」「CVE が出た」「依存は安全か」「更新が遅れていないか」「セキュリティのニュースを見て」と言われたとき、週 1 回のルーティンで実行されたとき、依存を足した・版を上げたあと、公開の前に使う。手元の全リポジトリの依存とツールを棚卸しし、OSV と CISA の悪用済み一覧と JVN のニュースに突き合わせ、新しく出たものを周知し、直す版のある物は更新の PR を用意する。鍵の混入は secret-leak-check、自分のコードの穴は security-review、中身の精査は content-audit。
---

# 脆弱性の見張り

脆弱性は、ニュースを見ていなくても自分の依存に出る。気づかずに更新が遅れると、公開しているアプリと CI がそのまま穴になる。
人が毎日ニュースを追うのは続かないので、機械で週に 1 回、この 7 日に動いたリポジトリを見て、新しく出たものだけを目立たせ、直す版がある物は更新まで進める。

## 手順

### 1. 棚卸し

```
python3 .claude/skills/vuln-watch/scripts/inventory.py /home/user
```

手元の全リポジトリの依存とツールを一覧にする。package-lock.json / package.json、requirements*.txt、pyproject.toml、
poetry.lock、Pipfile.lock、go.mod、Cargo.lock、Dockerfile の FROM、.github/workflows の `uses:`。
lock がある所は lock を正とする。版が決まっていないもの（`>=`、`^`、浮いたタグ）は調べきれないので、報告で別に数える。

### 2. 調査

```
python3 .claude/skills/vuln-watch/scripts/scan.py --roots /home/user --out <報告.md> --json <結果.json> --mark-seen
```

| 出どころ | 何に使うか |
|---|---|
| OSV（osv.dev） | 部品と版ごとの脆弱性。GitHub Advisory の中身も入っている。重さは GitHub の判定か CVSS 3 の基本値 |
| CISA KEV | 実際に悪用されている脆弱性。自分の依存に当たれば「悪用済み」として最上位 |
| JVN（RSS） | 日本語の注意喚起。依存の名前と `references/watch.md` の見張る語に当たるものをニュースに |

GitHub Advisory の一覧 API は、このセッションの GitHub の経路（リポジトリ単位）では使えないので OSV で代える。
同じ CVE の別名（GHSA と PYSEC）は 1 つにまとめる。GitHub Actions は `v4` のような浮いたタグだと版が決まらないので、
所見にはせず「浮いたタグ」として固定を勧める。`--mark-seen` を付けると、次の回から新しいものだけに ★ が付く。
取ったものは `~/.cache/vuln-watch` に写しを置く（OSV の脆弱性は 7 日、一覧は 20 時間）。`--offline` で写しだけで動く。

### 3. 周知

- 会話には 1 行: `★ 脆弱性: 重大 N / 高 N / 中 N / 低 N、悪用済み N、新規 N、ニュース N`。新規が 0 なら ❕ で 1 行
- 新規があれば報告のファイルを `SendUserFile` で渡す。毎日同じ報告を送らない
- 悪用済みか重大が新しく出たときは、`PushNotification` で 1 行送る（部品、リポジトリ、直す版）。ユーザーが離れていても届くように
- 週の報告（skill-upkeep の土曜）で、その時点の報告を `docs/reports/security.md` に写して PR に入れる。毎日のコミットはしない（散らかさない）

### 4. 改善

悪用済み・重大・高で直す版があるものは、その日のうちに更新のブランチを用意する。

```
python3 .claude/skills/vuln-watch/scripts/bump.py <リポジトリ> <種類> <部品> <直す版>
```

- ブランチは `claude/SEC-<YYYYMMDD>`（日付は日本時間）。1 リポジトリ 1 本に、その日の分をまとめる
- そのリポジトリのテストの台本（repo-bootstrap が置く test.sh や、そのリポジトリの check.sh）を通し、precheck を通してから PR にする
- 大きな版上げ（0.x から 1.x、主番号が変わる）は、依存している側（fastapi と starlette など）の対応版も見る。動かないなら PR にせず、報告に「直す版はあるが動かない。理由」と書く
- 直す版が「まだ無い」ものは、ID のページで回避策を見る。無ければ使う所を外すか別の部品に替える案を報告に書く
- 開発用の依存（dev）は、CI と手元でしか動かないので、本番の依存より後でよい。ただし CI で動く物（vitest、vite）は CI のランナーで動くので直す
- PR を作る前とマージの前はユーザーの確認が要る（solo-pr-flow「出す前の確認」）。悪用済みと重大は、その日のうちに ❔ を 1 行出す。自律で回っていて返事が無いときは pending.md に「PR 待ち」

### 5. 見張る語を育てる

依存の一覧に出ない物（実行環境、OS、アプリの入れ物、開発の道具）は `references/watch.md` に書く。新しい道具を使い始めたら足す。

## 週 1 回のルーティン

週に 1 回（月曜の朝）、そのたびに新しいセッションで 1〜4 を回す。対象は、この 7 日に push のあったリポジトリだけ（list_repos の pushed_at）。
毎日は回さない（トークンの無駄）。ルーティンのプロンプトには、やらないこと（PR を作らない、マージしない、main に push しない、全リポジトリを clone しない）を書く。
見回りのセッションが全リポジトリを clone して PR を作りマージした失敗がある（LESSONS.md）。手元の clone が古いと調べる版がずれるので、最初に各リポジトリの
デフォルトブランチを fetch する。1 回の実行は、調査 20 秒と報告の読み書きだけで済むようにする（スクリプトに任せ、
セッションは結果を読んで 3 行で返す）。

## スクリプト

| 何 | すること |
|---|---|
| `scripts/inventory.py` | 棚卸し。`--json` で 1 行 1 件 |
| `scripts/scan.py` | 調査と報告。`--roots` `--days` `--out` `--json` `--mark-seen` `--offline` `--strict`（重大か悪用済みがあれば終了コード 1）。`VULN_WATCH_CACHE` と `VULN_WATCH_STATE` で置き場を変えられる |
| `scripts/bump.py` | 直す版に書き換える（PyPI、npm、GitHub Actions） |
| `scripts/test_vuln_watch.py` | 写しのデータで、棚卸し・判定・報告・書き換えを確かめる。ネットワークは使わない |

## 他のスキルとの関係

- 鍵や ID の混入は `secret-leak-check`。自分の書いたコードの穴は `security-review`。人に見せてよいかは `content-audit`
- 更新の PR の出し方と確認は `solo-pr-flow`。CI が落ちたら `ci-triage`
- 定期実行の作り方は `scheduler`。週の報告への写しは `skill-upkeep`
