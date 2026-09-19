#!/usr/bin/env bash
# リポジトリに、作業の土台を一度で置く。
#
#   bootstrap.sh <リポジトリのパス>
#
# 置くもの:
#   CLAUDE.md                 無ければ置く。あれば触らない
#   .claude/settings.json     フック。無ければ置く。あれば触らない
#   .claude/hooks/lang-rule.sh  言語ルールを開始時と毎回の入力で差し込む
#   .claude/hooks/lang-check.sh 応答に韓国語が混ざったら止める
#   .claude/hooks/check.sh    編集のたびに走る検査。lint と型検査
#   .claude/hooks/test.sh     止まる前に走るテスト
#   .claude/skills/           Ciel のスキル一式
#   .github/workflows/test.yml  テストの実行方法が分かった言語だけ
#
# 検査とテストのコマンドはリポジトリから推定する。見つからなければ
# フックは何もしない中身になる。あとから hooks/*.sh を手で直せばよい。
#
# 既にあるファイルは上書きしない。置いたものと置かなかったものを最後に出す。
set -euo pipefail

dest="${1:?リポジトリのパスを指定してください}"
dest="$(cd "$dest" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
tpl="$here/../templates"
placed=(); kept=(); notes=()

[ -d "$dest/.git" ] || { echo "$dest は git リポジトリではありません" >&2; exit 1; }

# --- 検査とテストのコマンドを推定する -------------------------------
checks=(); tests=(); lang=""
if [ -f "$dest/package.json" ]; then
  lang=node
  has_script() { python3 - "$dest/package.json" "$1" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); sys.exit(0 if sys.argv[2] in d.get("scripts",{}) else 1)
PY
  }
  has_script lint      && checks+=("npm run lint")
  has_script typecheck && checks+=("npm run typecheck")
  has_script test      && tests+=("npm test")
fi
if [ -f "$dest/pyproject.toml" ] || [ -f "$dest/pytest.ini" ] || [ -d "$dest/tests" ]; then
  lang="${lang:-python}"
  if [ -f "$dest/ruff.toml" ] || grep -qs '\[tool.ruff' "$dest/pyproject.toml"; then checks+=("ruff check ."); fi
  tests+=("python3 -m pytest -q")
fi
if [ -f "$dest/go.mod" ]; then
  lang="${lang:-go}"; checks+=("go vet ./..."); tests+=("go test ./...")
fi
if [ -f "$dest/Cargo.toml" ]; then
  lang="${lang:-rust}"; checks+=("cargo check --quiet"); tests+=("cargo test --quiet")
fi
if [ -f "$dest/Makefile" ] && grep -qE '^test:' "$dest/Makefile" && [ ${#tests[@]} -eq 0 ]; then
  tests+=("make test")
fi

# --- CLAUDE.md ------------------------------------------------------
if [ -f "$dest/CLAUDE.md" ]; then
  kept+=("CLAUDE.md（既にある）")
  grep -q "work-style" "$dest/CLAUDE.md" || notes+=("CLAUDE.md に「作業を引き受けたら work-style を読む」が無い。手で足す")
else
  cp "$tpl/CLAUDE.md" "$dest/CLAUDE.md"; placed+=("CLAUDE.md")
fi

# --- フック ---------------------------------------------------------
mkdir -p "$dest/.claude/hooks"
write_hook() {  # $1=ファイル名 $2=見出し $3...=コマンド
  local name="$1" title="$2"; shift 2
  local f="$dest/.claude/hooks/$name"
  if [ -f "$f" ]; then kept+=(".claude/hooks/$name（既にある）"); return; fi
  {
    echo '#!/usr/bin/env bash'
    echo "# $title"
    echo '# 落ちたら exit 2 で止め、出力を Claude に返す。通れば黙る。'
    echo '# コマンドは bootstrap.sh が推定した。違っていればここを直す。'
    echo 'set -uo pipefail'
    echo 'input="$(cat)"'
    echo '# 自分が止めた直後の再実行では走らない（無限ループ防止）'
    echo 'case "$input" in *'"'"'"stop_hook_active":true'"'"'*) exit 0;; esac'
    echo 'cd "$(git rev-parse --show-toplevel)"'
    if [ $# -eq 0 ]; then
      echo '# コマンドが見つからなかったので何もしない。分かったら足す。'
      echo 'exit 0'
    else
      echo 'fail=0'
      for c in "$@"; do
        echo "out=\$($c 2>&1) || { echo \"[$c]\" >&2; echo \"\$out\" | tail -40 >&2; fail=1; }"
      done
      echo '[ $fail -eq 0 ] || exit 2'
    fi
  } > "$f"
  chmod +x "$f"; placed+=(".claude/hooks/$name（${*:-コマンド未検出}）")
}
# 言語ルールの差し込みと、応答の韓国語混入の検査。中身は固定なのでコピーする
for h in lang-rule.sh lang-check.sh skill-nudge.sh pr-nudge.sh; do
  if [ -f "$dest/.claude/hooks/$h" ]; then kept+=(".claude/hooks/$h（既にある）")
  else cp "$tpl/hooks/$h" "$dest/.claude/hooks/$h"; chmod +x "$dest/.claude/hooks/$h"; placed+=(".claude/hooks/$h"); fi
done
write_hook check.sh "編集のたびに走る検査" "${checks[@]+"${checks[@]}"}"
write_hook test.sh  "止まる前に走るテスト"  "${tests[@]+"${tests[@]}"}"

if [ -f "$dest/.claude/settings.json" ]; then
  kept+=(".claude/settings.json（既にある。フックは手で足す）")
else
  cp "$tpl/settings.json" "$dest/.claude/settings.json"; placed+=(".claude/settings.json")
fi

# --- .gitignore -----------------------------------------------------
if git -C "$dest" check-ignore -q .claude/skills 2>/dev/null; then
  notes+=(".gitignore が .claude/skills を無視している。'!.claude/' '!.claude/skills/' を足す")
fi

# --- スキル ---------------------------------------------------------
ciel="${CIEL:-}"
if [ -z "$ciel" ]; then
  ciel="$(mktemp -d)/ciel"
  git clone -q --depth 1 https://github.com/Maguro-JP/Ciel.git "$ciel"
fi
if [ -f "$ciel/.claude/skills/skill-sync/scripts/sync.py" ]; then
  python3 "$ciel/.claude/skills/skill-sync/scripts/sync.py" "$dest" --apply >/dev/null 2>&1 || true
  placed+=(".claude/skills/（$(ls "$dest/.claude/skills" | wc -l | tr -d ' ') 本）")
else
  notes+=("Ciel が見つからないのでスキルは入れていない。CIEL=/path/to/Ciel を指定する")
fi

# --- CI -------------------------------------------------------------
if [ ${#tests[@]} -gt 0 ] && [ ! -f "$dest/.github/workflows/test.yml" ]; then
  case "$lang" in
    node|python)
      mkdir -p "$dest/.github/workflows"
      sed -e "s|@@TEST@@|${tests[0]}|" -e "s|@@CHECKS@@|${checks[*]:-true}|" \
          "$tpl/ci-$lang.yml" > "$dest/.github/workflows/test.yml"
      # lockfile が無いと npm ci は失敗する
      if [ "$lang" = node ] && [ ! -f "$dest/package-lock.json" ]; then
        sed -i 's/npm ci$/npm install/' "$dest/.github/workflows/test.yml"
        notes+=("package-lock.json が無いので CI は npm install にした。lockfile を作ったら npm ci に戻す")
      fi
      placed+=(".github/workflows/test.yml（$lang）") ;;
    *) notes+=("CI は $lang 用のひな形が無いので置いていない。テストは ${tests[0]}") ;;
  esac
fi

# --- 報告 -----------------------------------------------------------
echo "置いたもの:";  for p in "${placed[@]+"${placed[@]}"}"; do echo "  $p"; done
[ ${#kept[@]} -eq 0 ]  || { echo "触らなかったもの:"; for p in "${kept[@]}";  do echo "  $p"; done; }
[ ${#notes[@]} -eq 0 ] || { echo "手でやること:";     for p in "${notes[@]}"; do echo "  $p"; done; }
echo
echo "次: git add -A && git commit してから、初めての PR の前に workspace-policy で方針を決める。"
