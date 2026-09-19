---
name: repo-bootstrap
description: リポジトリに作業の土台を一度で置くとき。新しくリポジトリを作った直後、「初期設定して」「セットアップして」「このリポジトリでも同じ環境にして」「フックを入れて」と言われたとき、CLAUDE.md も .claude/ も無いリポジトリで作業を始めるときに使う。CLAUDE.md、フック入りの settings.json、検査とテストのフック、Ciel のスキル一式、最小の CI を置き、既にあるものには触らない。スキルの更新だけなら skill-sync、PR の方針を決めるのは workspace-policy を使う。
---

# リポジトリ初期設定

新しいリポジトリに、作業の土台を一度で置く。
置くのは6つ。既にあるものは触らず、置かなかった理由を出す。

| 置くもの | 役目 |
|---|---|
| `CLAUDE.md` | 言語ルール、work-style を最初に読む指示、完了前のルール |
| `.claude/settings.json` | フック。開始時に work-style を差し込む、編集後に検査、止まる前にテスト |
| `.claude/hooks/check.sh` | lint と型検査。落ちたら出力を返して止める |
| `.claude/hooks/test.sh` | テスト。落ちたら出力を返して止める |
| `.claude/skills/` | Ciel のスキル一式 |
| `.github/workflows/test.yml` | 最小の CI。テストの実行方法が分かった言語だけ |

## 手順

### 1. 走らせる

```bash
.claude/skills/repo-bootstrap/scripts/bootstrap.sh /path/to/repo
```

Ciel が手元にあるなら `CIEL=/path/to/Ciel` を付けると clone を省ける。

検査とテストのコマンドは、リポジトリから推定する。

| 見つけたもの | 検査 | テスト |
|---|---|---|
| `package.json` の scripts | `npm run lint`, `npm run typecheck` | `npm test` |
| `pyproject.toml`, `pytest.ini`, `tests/` | `ruff check .`（設定があれば） | `python3 -m pytest -q` |
| `go.mod` | `go vet ./...` | `go test ./...` |
| `Cargo.toml` | `cargo check` | `cargo test` |
| `Makefile` の `test:` | | `make test` |

見つからなければ、フックは何もしない中身になる。
コマンドが分かったら `.claude/hooks/*.sh` を手で直す。

### 2. 出力を読む

「置いたもの」「触らなかったもの」「手でやること」の3つに分かれて出る。
「手でやること」は必ず読む。`.gitignore` が `.claude/` を無視している、
`package-lock.json` が無い、CI のひな形が無い言語、などが出る。

### 3. フックが動くか確かめる

置いただけでは確かめたことにならない。

```bash
echo '{}' | .claude/hooks/check.sh; echo "exit=$?"
echo '{}' | .claude/hooks/test.sh;  echo "exit=$?"
```

通れば 0 で黙る。落ちれば 2 で、落ちた出力が出る。
テストが落ちるリポジトリなら、この時点で落ちるのが正しい。

### 4. コミットする

```bash
git add -A && git commit -m "作業の土台を置く"
```

`.claude/` がコミットに入っているか `git show --stat` で見る。
入っていなければ `.gitignore` が無視している。

### 5. 方針を決める

初めての PR の前に `workspace-policy` を使う。
自動マージの可否は規約を読んでから決めるもので、ここでは置かない。

## フックの動き

- SessionStart: `work-style` の SKILL.md を読み込む。セッションのたびに姿勢を入れ直す
- PostToolUse（Edit, Write）: `check.sh`。落ちたら exit 2 で、Claude に出力が戻る
- Stop: `test.sh`。落ちたら exit 2 で止まれない。通るまで直す
- `stop_hook_active` が立っていたら走らない。自分が止めた直後の無限ループを防ぐ

フックは `settings.json` に置くので、Raphael の配布対象には入っていない。
リポジトリごとに検査コマンドが違うため、一律に配ると動かないフックが入る。
このスキルで1リポジトリずつ置く。

## やらないこと

- 既にあるファイルの上書き。`CLAUDE.md` や `settings.json` があれば触らず、足りない点を出すだけ
- `.claude/policy/` を置くこと。規約を読まずに方針が決まる
- 分からない言語の CI をでっち上げること。テストコマンドが出るまで置かない
