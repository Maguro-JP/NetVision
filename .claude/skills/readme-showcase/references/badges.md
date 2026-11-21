# バッジ

バッジは「信用できる」のため。根拠のある物だけを置く。根拠とは、その値を作る物がリポジトリにあること。
置くときは改行で区切って並べ、節の見出しは付けない。

## 置けるバッジと根拠

| バッジ | 根拠 | URL の形（shields.io） |
|---|---|---|
| CI の状態 | `.github/workflows/<name>.yml` がある | `https://img.shields.io/github/actions/workflow/status/<owner>/<repo>/<name>.yml?branch=<branch>` |
| ライセンス | LICENSE のファイルがある | `https://img.shields.io/github/license/<owner>/<repo>` |
| 最新の版 | Release かタグがある | `https://img.shields.io/github/v/release/<owner>/<repo>` |
| 対応 OS | README か CI に対応 OS が書かれている | `https://img.shields.io/badge/OS-Windows%20%7C%20macOS%20%7C%20Linux-informational` |
| 言語の版 | manifest に版の下限がある（`requires-python`、`engines`） | `https://img.shields.io/badge/python-3.11%2B-blue` |
| パッケージの版 | PyPI か npm に公開している | `https://img.shields.io/pypi/v/<package>`、`https://img.shields.io/npm/v/<package>` |

## 置かないバッジ

- CI が無いのに CI の状態。ワークフローのファイルが無ければ、バッジは「unknown」のまま壊れて見える
- 公開していないパッケージの版
- ダウンロード数、スター数、コントリビューター数。数で飾らない（少ないと逆に見える）
- 外部のサービスの評価（coverage、quality）で、そのサービスを設定していない物

## 外部に出さない形

shields.io は外部への通信になる。社内や通信を抑えたい所では、静的なバッジを `docs/badges/` に置いて相対パスで参照する。

例: `![License](docs/badges/license.svg)`

静的なバッジは `https://img.shields.io/badge/<label>-<message>-<color>` を 1 回取って保存する。保存した物は値が変わらないので、CI の状態には向かない。

## 書き方

```markdown
![CI](https://img.shields.io/github/actions/workflow/status/<owner>/<repo>/ci.yml?branch=main)
![License](https://img.shields.io/github/license/<owner>/<repo>)
```

alt（`![...]` の中）は、バッジが出ないときに読まれる。「CI」「License」のように、何のバッジかを書く。
