#!/usr/bin/env python3
"""no_browser.py を、作ったツール入力で確かめる。"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "no_browser.py"


def run(tool: str, path: str, text: str) -> str:
    env = dict(os.environ)
    env.pop("DESKTOP_APP_OFF", None)
    key = "content" if tool == "Write" else "new_string"
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": {"file_path": path, key: text}}),
                       capture_output=True, text=True, env=env, check=True)
    return r.stdout


class NoBrowser(unittest.TestCase):
    def test_webbrowser_open_is_flagged(self):
        self.assertIn("ブラウザ起動", run("Edit", "/r/app/launch.py", "    webbrowser.open(url)\n"))

    def test_readme_open_url_is_flagged(self):
        self.assertIn("ブラウザ起動", run("Write", "/r/README.md", "2. 表示された `http://127.0.0.1:4173/` を開く\n"))

    def test_cli_under_app_is_flagged_but_tools_is_not(self):
        self.assertIn("CLI の入口", run("Write", "/r/app/cli.py", "p = argparse.ArgumentParser()\n"))
        self.assertEqual("", run("Write", "/r/tools/gen/demi.py", "p = argparse.ArgumentParser()\n"))

    def test_plain_edit_is_silent(self):
        self.assertEqual("", run("Edit", "/r/app/server/main.py", "def health():\n    return {'ok': True}\n"))

    def test_other_tools_are_ignored(self):
        env = dict(os.environ)
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "webbrowser.open"}}),
                           capture_output=True, text=True, env=env, check=True)
        self.assertEqual("", r.stdout)


if __name__ == "__main__":
    unittest.main()
