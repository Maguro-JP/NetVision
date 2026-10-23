#!/usr/bin/env bash
# textlint と日本語の規則集をリポジトリの外に入れる。層の組み方は tomkd555/prose-lint（MIT）に倣った。
#   ja-technical-writing（技術文書の基本）、JTF スタイル、@textlint-ja/preset-ai-writing（AI っぽい記述）、
#   公用文のかな規則（形式名詞・補助動詞・代名詞・副詞・接続詞を prh で）、い抜き・さ入れ・フィラー。
# リポジトリに node_modules を増やさないため、置き場所は $CIEL_TEXTLINT_DIR か ~/.cache/ciel-textlint。
# proofread.py は、ここに textlint があれば自動で使う。無くても proofread.py は動く（正規表現だけになる）。
set -euo pipefail
dir="${CIEL_TEXTLINT_DIR:-$HOME/.cache/ciel-textlint}"
here="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$dir"
cd "$dir"
[ -f package.json ] || npm init -y >/dev/null
npm install --silent --no-audit --no-fund \
  textlint textlint-filter-rule-comments \
  textlint-rule-preset-ja-technical-writing textlint-rule-preset-ja-spacing textlint-rule-preset-jtf-style \
  @textlint-ja/textlint-rule-preset-ai-writing \
  @textlint-ja/textlint-rule-no-filler @textlint-ja/textlint-rule-no-insert-dropping-sa @textlint-ja/textlint-rule-no-dropping-i \
  textlint-rule-ja-keishikimeishi textlint-rule-ja-hiragana-hojodoushi textlint-rule-ja-hiragana-daimeishi textlint-rule-ja-hiragana-fukushi \
  textlint-rule-prh
cp -r "$here/assets/textlint/." "$dir/"
"$dir/node_modules/.bin/textlint" --version
echo "textlint を $dir に入れた。proofread.py が自動で使う。"
