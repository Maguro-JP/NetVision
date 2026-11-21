# 調べる順と、記憶で書かない物

## 調べる順

近い所から順に当たる。前の段で分かれば、次へ進まない。

| 順 | 見る所 | ツール | 分かること |
|---|---|---|---|
| 1 | 手元のコードと出力 | Grep、Read、動かした出力、`git log`、`git diff` | このリポジトリで実際にどうなっているか |
| 2 | 入っている版と、その使い方 | `--help`、`--version`、`pip show`、`npm view`、lockfile、入っているソース | この環境で使えるフラグと API |
| 3 | 公式の文書 | Context7（`resolve-library-id` のあと `query-docs`）、公式のサイトを WebFetch | 今の仕様、変更履歴、移行の手順 |
| 4 | 検索 | WebSearch、Exa | 文書に無い事例、公開されている実装 |

調べる前に `knowledge.py --find <語>` で、前に調べた知識を見る。調べたら research-to-tech の形（`docs/knowledge/tech/`）で残す。

## 記憶で書かない物

出典: [houseofmvps/ultraship](https://github.com/houseofmvps/ultraship) の `staying-current`（MIT License, Copyright (c) 2026 Kailesk Khumar）と、
[Soju06/knowpatch](https://github.com/Soju06/knowpatch)（MIT License, Copyright (c) 2026 Soju06）。要点だけを日本語で写した。

学習の知識は、数年前で止まっている。そのあとに、ライブラリは壊れる変更を出し、値段は変わり、モデルの名前は替わり、API は廃止される。
次の物は、書く前に一次情報で確かめ、確かめた版か日付を書く。

| 書く物 | 見る所 |
|---|---|
| ライブラリの API（import、関数の名前、引数） | 入っている版のソースか、その版の公式の文書（Context7） |
| パッケージの版、互換、廃止 | lockfile と manifest。新しい版は `npm view <名前> version`、`pip index versions <名前>`、`cargo search <名前> --limit 1` |
| CLI のフラグ、サブコマンド | `<コマンド> --help`、`<コマンド> <サブコマンド> --help`、`--version` |
| 設定ファイルの形（キーの名前、置き場所） | 公式の文書。同じリポジトリの、動いている設定 |
| GitHub Actions の `uses:` とその版 | そのリポジトリの Release とタグ |
| モデルの ID、名前、文脈の長さ | 提供元の公式の一覧（Claude は `claude-api` のスキル） |
| 値段、上限、無料枠 | 提供元の公式のページ。見た日を書く |
| 「最新」「今の」「推奨」 | 変更履歴と Release。今日の日付と比べる |
| 公開されているスキルやリポジトリの名前と中身 | そのリポジトリの中身と LICENSE を開いて読む |
| Claude Code のフックの仕様（入力の欄、終了コード、差し込みの形） | 公式の文書と、このリポジトリの動いているフック |

## 決まり

- 版は覚えない。版はパッケージの管理のツールに聞く。変わった所（名前の変更、壊れる変更）は文書に聞く
- 「最新の版」と書く前に、今日の日付と、その版の出た日を見る
- プロジェクトは古い版を固定していることがある。最新を前提にせず、lockfile を先に読む
- 記憶と一次情報が違ったら、記憶を捨てる。違いは `docs/knowledge/tech/` に残す（次の回の記憶も同じ所で古い）
- 調べられなかったら、そう書く。`❕ 疑い: <何>は調べられない（理由）`。もっともらしい間違いより、分からないと書く方がよい

## 調べられないとき

| 起きたこと | すること |
|---|---|
| コマンドが入っていない | 別のツールで同じ事実を取る（`pip show` が無ければ `python3 -c "import importlib.metadata as m; print(m.version('<名前>'))"`） |
| ネットワークに届かない | 手元の入っている版とソースで分かる所まで書き、届かなかった相手を書く |
| 全部だめ | `❕ 疑い` に、何を・なぜ調べられなかったかを書く。推測の語で埋めない |
