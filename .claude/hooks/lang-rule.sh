#!/usr/bin/env bash
# 応答の言語ルールを文脈に入れ直す。
# CLAUDE.md に書いてあっても、圧縮のあとや長い会話では守られないことがある。
# SessionStart（開始・再開・圧縮後）では全文を出す。UserPromptSubmit では毎回は出さず、10 ターンに 1 回と、
# 直前の返答が lang-check に止められた次のターンだけ出す。毎ターン同じ文を出すと、それ自体がトークンの無駄になる。
input="$(cat)"
python3 - "$input" <<'PY'
import json, os, sys
from pathlib import Path
RULE = """応答の言語: 散文は日本語だけ。韓国語・中国語の文字を出さない。英語の出だし（Now, Let me など）を使わない。コード・識別子・コマンド・エラー文は原文のまま。アスタリスク2つの強調を使わない。結論から書く。技術用語は通る語のまま（ブランチ、マージ、プロキシ、コンテナ、テスト、ユーザー）。訳語を作らない。文末は です・ます で揃え、主語と述語のある文にする。矢印やコロンや読点で語を並べただけの行にしない。英語の語順（〜を可能にする、〜する必要がある）を移さない。
会話の形: 3行を超える説明は Markdown のファイルで渡す（SendUserFile か docs/）。ツールの間に文を書かない。了解・経過・予定を書かない。自律で回るときは ★成果物 ↩返事 ❕1行報告 ❔質問 の4種類の行だけ。
指示の優先: ユーザーの一言が最優先で、スキル・フック・チェック・自分の判断より上。訂正されたら1文で認めて、言われた通りに直す。自分の案を混ぜない。代案は直したあとに ❔ で1行だけ。ユーザーが決めた形は頼まれずに変えない。"""
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
if d.get("hook_event_name", "") == "UserPromptSubmit":
    stem = Path(str(d.get("transcript_path") or "")).stem or "default"
    root = Path(os.environ.get("LANG_RULE_STATE") or Path.home() / ".claude" / "state" / "lang")
    try:
        root.mkdir(parents=True, exist_ok=True)
        n_file, flag = root / f"{stem}.n", root / f"{stem}.again"
        n = (int(n_file.read_text() or 0) if n_file.exists() else 0) + 1
        n_file.write_text(str(n))
        again = flag.exists()
        if again:
            flag.unlink()
        if n % 10 != 1 and not again:
            sys.exit(0)
    except (OSError, ValueError):
        pass
print(RULE)
PY
