#!/usr/bin/env python3
"""ui_check.py と ui_nudge.py を、作った中身で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ui_check import check_text, kind_of  # noqa: E402

HOOK = HERE.parent / "hooks" / "ui_nudge.py"


def msgs(path, text):
    return [m for _, _, m in check_text(path, text)]


class Check(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(kind_of("app/index.html", ""), "markup")
        self.assertEqual(kind_of("src/App.tsx", ""), "markup")
        self.assertEqual(kind_of("src/main.ts", ""), "web")
        self.assertEqual(kind_of("app/gui.py", "import tkinter as tk\n"), "gui")
        self.assertIsNone(kind_of("app/engine.py", "import numpy\n"))
        self.assertIsNone(kind_of("tests/index.html", ""))
        self.assertIsNone(kind_of("design/Main.dc.html", ""))
        self.assertIsNone(kind_of("src/App.test.tsx", ""))
        self.assertIsNone(kind_of("docs/index.html", ""))

    def test_markup_rules(self):
        html = """<img src="a.png">
<div onClick={go}>x</div>
<a href="#" onclick="f()">x</a>
<input type="text" placeholder="名前">
<button><svg/></button>
<button>OK</button>
<video autoplay>
<p tabindex="3">
<button aria-label="閉じる"><svg/></button>
<button>保存する</button>
<img src="b.png" alt="図">
<label for="q">検索</label><input id="q">
<label>年 <select id="y"></select></label>
<input id="nolabel">
"""
        got = msgs("app/index.html", html)
        for key in ("alt が無い", "div や span", "a を button", "入力にラベルが無い", "文字の無いボタン", "何が起きるかを言っていない", "自動再生", "正の tabindex"):
            self.assertTrue(any(key in m for m in got), key)
        self.assertEqual(sum("入力にラベルが無い" in m for m in got), 2)  # placeholder だけのものと id="nolabel"。label for と label で包んだものは数えない
        self.assertEqual(len(got), 9)

    def test_web_rules_and_focus(self):
        css = "button:focus { outline: none; }\n"
        self.assertTrue(any("フォーカスの輪郭" in m for m in msgs("src/a.css", css)))
        self.assertEqual(msgs("src/a.css", css + "button:focus-visible { outline: 2px solid; }\n"), [])
        js = "confirm('消しますか');\nalert(err.message);\nlocation.reload();\nconst s = { fontSize: 10 };\n"
        got = msgs("src/a.js", js)
        self.assertTrue(any("止まるダイアログ" in m for m in got))
        self.assertTrue(any("再読み込み" in m for m in got))
        self.assertEqual(msgs("src/a.js", "showPrompt();\n// alert(x)\n"), [])

    def test_error_text_rules(self):
        self.assertTrue(any("エラー文" in m for m in msgs("src/a.ts", 'setError("エラーが発生しました");\n')))
        self.assertTrue(any("例外の原文" in m for m in msgs("src/a.ts", "toast(String(e));\n")))
        self.assertEqual(msgs("src/a.ts", 'setError("保存できませんでした。ディスクの空きを確かめて、もう一度保存してください");\n'), [])

    def test_gui_rules(self):
        py = ("import tkinter as tk\nfrom tkinter import messagebox\nimport subprocess\n"
              "messagebox.askyesno('確認', '本当によいですか')\ntime.sleep(1)\nroot.resizable(False, False)\n"
              "tk.Button(root, text='OK')\nmessagebox.showerror('エラー', str(e))\nsubprocess.run(['x'])\n")
        got = msgs("app/gui.py", py)
        for key in ("確認のダイアログ", "sleep", "大きさを固定", "何が起きるかを言っていない", "例外の原文", "重い処理"):
            self.assertTrue(any(key in m for m in got), key)
        self.assertFalse(any("重い処理" in m for m in msgs("app/gui.py", py + "import threading\n")))
        self.assertEqual(msgs("app/gui.py", "import tkinter as tk\ntk.Button(root, text='保存する', command=save)\n"), [])

    def test_hook(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app" / "index.html"
            p.parent.mkdir()
            p.write_text("<button>OK</button>\n<button>保存する</button>\n", encoding="utf-8")
            env = dict(os.environ)
            env.pop("UI_DESIGN_OFF", None)

            def run(tool, key, text, path=str(p), extra=None):
                r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": {"file_path": path, key: text}}),
                                   capture_output=True, text=True, env=extra or env, check=True)
                return r.stdout

            self.assertIn("何が起きるかを言っていない", run("Edit", "new_string", "<button>OK</button>"))
            self.assertEqual("", run("Edit", "new_string", "<button>保存する</button>"))
            self.assertEqual("", run("Bash", "command", "cat x"))
            self.assertEqual("", run("Edit", "new_string", "<button>OK</button>", extra=dict(env, UI_DESIGN_OFF="1")))
            self.assertEqual("", run("Write", "content", "<button>OK</button>", path=str(Path(d) / "docs" / "a.html")))


if __name__ == "__main__":
    unittest.main()
