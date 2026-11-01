#!/usr/bin/env python3
"""UI の、機械で拾える検査。インテュイティブ・ユーザーフレンドリー・ストレスフリーに反する書き方を注意として出す。

使う人は説明書を読まない。画面を見てやりたいことが分かり、押せば答えが返り、間違えても戻せて、待たされず、邪魔されない、が完成の形。
その多くは人が歩いて確かめるしかない（references/checklist.md）が、書き方に出る物は機械で拾える。
止まるダイアログ、ラベルの無い入力、文字の無いボタン、消したフォーカス、曖昧なエラー文、例外の原文、画面を固める sleep、
押すと何が起きるかを言わないボタンの文言。書いている最中に知らせる方が、あとで画面ごと直すより安い。

使い方:
  ui_check.py --files <path>...   ファイルを見る。見つかれば終了コード 1
  ui_check.py --diff <base>       base からの差分で足した行だけを見る。注意として出し、終了コード 0（precheck が呼ぶ）
見るもの: .html .htm .jsx .tsx .vue .svelte .js .ts .css .scss と、tkinter・customtkinter・PySide・PyQt・pywebview・wx・kivy・flet を読み込む .py。
見ないもの: tests、node_modules、dist、build、docs、design、.claude、.github、*.min.*、*.dc.html
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

WEB_EXT = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".js", ".ts", ".mjs", ".css", ".scss"}
MARKUP_EXT = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"}
SKIP = re.compile(r"(^|/)(tests?|__tests__|spec|node_modules|dist|build|out|vendor|\.claude|\.github|docs|design|static/(?:lib|vendor))(/|$)"
                  r"|\.(?:test|spec|stories)\.[jt]sx?$|\.min\.(?:js|css)$|\.dc\.html$")
GUI_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:tkinter|customtkinter|PySide6|PySide2|PyQt[56]|webview|wx|kivy|flet|dearpygui|toga)\b", re.M)

# (どこで見るか, 正規表現, 注意)。行ごとに当てる
LINE_RULES = [
    ("web", re.compile(r"(?<![\w.$])(?:window\.)?(?:alert|confirm|prompt)\("),
     "ブラウザの止まるダイアログ（alert、confirm、prompt）。画面の中に出す。確認は戻せない操作だけにし、何が消えるかを書く"),
    ("markup", re.compile(r"<img\b(?![^>]*\balt=)[^>]*>"), "画像に alt が無い"),
    ("markup", re.compile(r"<(?:div|span)\b[^>]*\s(?:onClick|@click|on:click|v-on:click|\(click\))="),
     "div や span をクリックの対象にしている。button にする（キーボードで押せず、押せる見た目にならない）"),
    ("markup", re.compile(r"<a\b[^>]*\shref=[\"']#[\"']"), "a を button の代わりに使っている（href=\"#\"）"),
    ("markup", re.compile(r"<(?:input|select|textarea)\b(?![^>]*\btype=[\"']?(?:hidden|submit|button|reset|checkbox|radio|image))(?![^>]*\bhidden\b)(?![^>]*\b(?:aria-label|aria-labelledby|title|id)=)[^>]*>"),
     "入力にラベルが無い（label for か aria-label）。placeholder はラベルにならない"),
    ("markup", re.compile(r"<button\b(?![^>]*\b(?:aria-label|title)=)[^>]*>\s*(?:<svg|<img|<i\b|</button>)"), "文字の無いボタン（aria-label を付ける）"),
    ("markup", re.compile(r"<button\b[^>]*>\s*(?:OK|Submit|送信|実行|はい|Yes|確定)\s*<"),
     "ボタンの文言が、押すと何が起きるかを言っていない（「保存する」「書き出す」のように書く）"),
    ("web", re.compile(r"\bautoplay\b"), "自動再生。人の操作で始める"),
    ("web", re.compile(r"tabindex=[\"']?[1-9]"), "正の tabindex（Tab の順序が狂う）"),
    ("web", re.compile(r"location\.reload\("), "画面全体の再読み込み（入力が消える）。変わった所だけ更新する"),
    ("web", re.compile(r"font-size\s*:\s*(?:[0-9]|1[01])(?:\.\d+)?px\b"), "12px より小さい文字"),
    ("gui", re.compile(r"messagebox\.(?:askyesno|askokcancel|askquestion|askretrycancel)\(|QMessageBox\.(?:question|warning)\("),
     "確認のダイアログ。戻せる操作は確認せず「元に戻す」を付ける。戻せない操作だけ確認し、何が消えるかを書く"),
    ("gui", re.compile(r"\btime\.sleep\("), "画面のスレッドで sleep（画面が固まる）。after か別のスレッドにする"),
    ("gui", re.compile(r"(?<![\w.])input\("), "ターミナルの入力。画面に置く"),
    ("gui", re.compile(r"resizable\(\s*(?:False|0)\s*,\s*(?:False|0)\s*\)"), "ウィンドウの大きさを固定している"),
    ("gui", re.compile(r"\b(?:text|label)\s*=\s*[\"'](?:OK|実行|送信|はい|Submit|確定)[\"']"),
     "ボタンの文言が、押すと何が起きるかを言っていない（「保存する」「書き出す」のように書く）"),
    ("any", re.compile(r"[\"'](?:エラーが発生しました|不明なエラー(?:です)?|エラーです|エラー|Something went wrong|An error occurred|Unknown error|Error occurred|Error)\.?[\"']"),
     "何が起きて何をすればよいかが無いエラー文。起きた場所のそばに、起きたことと次にすることを書く"),
    ("any", re.compile(r"(?:alert|toast|showerror|showwarning|notify|setError|setMessage|message|critical|warning)\s*\([^)\n]*(?:\bstr\((?:e|err|ex|exc|error)\)|\b(?:e|err|error)\.message\b|String\((?:e|err|error)\)|format_exc\(\)|\{(?:e|err|exc|error)\})"),
     "例外の原文をそのまま見せている。何が起きたかと、何をすればよいかを書く"),
]
# ファイル全体で見るもの
FOCUS_OFF = re.compile(r"outline\s*:\s*(?:none|0)\b")
FOCUS_ON = re.compile(r":focus-visible")
HEAVY = re.compile(r"\b(?:requests\.(?:get|post|put|delete)|urlopen|subprocess\.(?:run|call|check_output|check_call|Popen))\(")
OFF_THREAD = re.compile(r"\bthreading\b|concurrent\.futures|\basyncio\b|QThread|\.after\(|Thread\(|run_in_executor")
LABEL_FOR = re.compile(r"<label\b[^>]*\sfor=[\"']([^\"']+)[\"']")
INPUT_ID = re.compile(r"<(?:input|select|textarea)\b(?![^>]*\btype=[\"']?(?:hidden|submit|button|reset|image))(?![^>]*\bhidden\b)(?![^>]*\b(?:aria-label|aria-labelledby|title)=)[^>]*\sid=[\"']([^\"']+)[\"']")
LABEL_OPEN = re.compile(r"<label\b")
LABEL_CLOSE = re.compile(r"</label>")


def kind_of(path: str, text: str):
    """'markup'（HTML や JSX）、'web'（JS や CSS）、'gui'（画面の Python）、None（見ない）。"""
    p = path.replace("\\", "/")
    if SKIP.search(p):
        return None
    ext = Path(p).suffix.lower()
    if ext in MARKUP_EXT:
        return "markup"
    if ext in WEB_EXT:
        return "web"
    if ext == ".py" and GUI_IMPORT.search(text):
        return "gui"
    return None


def check_text(path: str, text: str):
    """[(行番号, 抜粋, 注意)]。行番号 0 はファイル全体の注意。"""
    kind = kind_of(path, text)
    if kind is None:
        return []
    notes = []
    for no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("//", "#", "*", "/*", "<!--")):
            continue
        for where, rx, msg in LINE_RULES:
            if where == "any" or where == kind or (where == "web" and kind == "markup"):
                m = rx.search(line)
                if m:
                    notes.append((no, m.group(0)[:50], msg))  # 1 行に 2 つ（曖昧な題と例外の原文）あれば両方出す
    if kind in ("markup", "web") and FOCUS_OFF.search(text) and not FOCUS_ON.search(text):
        notes.append((0, "outline: none", "フォーカスの輪郭を消している（:focus-visible で見せる。キーボードで今どこか分からない）"))
    if kind == "markup":
        labels = set(LABEL_FOR.findall(text))
        for m in INPUT_ID.finditer(text):
            before = text[:m.start()]
            wrapped = len(LABEL_OPEN.findall(before)) > len(LABEL_CLOSE.findall(before))  # <label> で包んであれば for は要らない
            if m.group(1) not in labels and not wrapped:
                notes.append((0, f'id="{m.group(1)}"', "入力にラベルが無い（label for か aria-label）。placeholder はラベルにならない"))
    if kind == "gui" and HEAVY.search(text) and not OFF_THREAD.search(text):
        notes.append((0, HEAVY.search(text).group(0), "重い処理（HTTP、subprocess）が画面のスレッドにある恐れ。別のスレッドで動かし、進みを見せる"))
    return notes


def added_lines(base: str, path: str):
    r = subprocess.run(["git", "diff", "-U0", base, "--", path], capture_output=True, text=True)
    lines, cur = set(), None
    for l in r.stdout.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", l)
        if m:
            start, n = int(m.group(1)), int(m.group(2) or 1)
            cur = start
            lines.update(range(start, start + n))
    return lines


def changed_files(base: str):
    r = subprocess.run(["git", "diff", "--name-only", base], capture_output=True, text=True)
    s = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True)
    return [p for p in (r.stdout + s.stdout).splitlines() if p.strip() and Path(p).is_file()]


def report(found):
    for path, no, snip, msg in found:
        where = f"{path}:{no}" if no else path
        print(f"  ? {where} 「{snip}」→ {msg}")
    print(f"UI 注意 {len(found)} 件（インテュイティブ・ユーザーフレンドリー・ストレスフリーの目安。references/checklist.md）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--diff")
    a = ap.parse_args()
    found = []
    if a.diff:
        for p in changed_files(a.diff):
            try:
                text = Path(p).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            added = added_lines(a.diff, p)
            for no, snip, msg in check_text(p, text):
                if no == 0 and added or no in added:
                    found.append((p, no, snip, msg))
        report(found)
        return 0
    if a.files:
        for p in a.files:
            try:
                text = Path(p).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found += [(p, no, snip, msg) for no, snip, msg in check_text(p, text)]
        report(found)
        return 1 if found else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
