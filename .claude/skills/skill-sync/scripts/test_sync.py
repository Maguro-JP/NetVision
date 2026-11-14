#!/usr/bin/env python3
"""sync.py のフックの登録の扱いと、起動役（.claude/hooks/run.sh）を確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
import sync  # noqa: E402

RUN = '"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/run.sh" '


class Registration(unittest.TestCase):
    def test_core_ignores_how_the_hook_is_started(self):
        new = RUN + ".claude/skills/x/hooks/y.py --flag"
        self.assertEqual(sync.core(new), ".claude/skills/x/hooks/y.py --flag")
        self.assertEqual(sync.core("python3 .claude/skills/x/hooks/y.py --flag"), sync.core(new))
        self.assertEqual(sync.core(".claude/hooks/lang-rule.sh"), sync.core(RUN + ".claude/hooks/lang-rule.sh"))
        self.assertNotEqual(sync.core("python3 .claude/skills/x/hooks/z.py"), sync.core(new))

    def test_reanchor_replaces_old_form_without_duplicating(self):
        tpl = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": RUN + ".claude/skills/x/hooks/y.py"}]}]}}
        cur = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 .claude/skills/x/hooks/y.py"},
                                                {"type": "command", "command": "python3 .claude/own/hook.py"}]}]}}
        self.assertEqual(sync.reanchor(cur, tpl), 1)
        cmds = [h["command"] for h in cur["hooks"]["Stop"][0]["hooks"]]
        self.assertEqual(cmds, [RUN + ".claude/skills/x/hooks/y.py", "python3 .claude/own/hook.py"])  # そのリポジトリ自身のフックには触らない
        self.assertEqual(sync.reanchor(cur, tpl), 0)

    def test_every_registration_goes_through_the_launcher(self):
        for p in (ROOT / ".claude/settings.json", ROOT / ".claude/skills/repo-bootstrap/templates/settings.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            for groups in d["hooks"].values():
                for g in groups:
                    for h in g["hooks"]:
                        self.assertTrue(h["command"].startswith((RUN, 'cd "${CLAUDE_PROJECT_DIR:-.}" && ')), h["command"])


class Launcher(unittest.TestCase):
    def run_from(self, cwd, *args, project=None):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project or ROOT)
        return subprocess.run([str(ROOT / ".claude/hooks/run.sh"), *args], cwd=cwd, input="{}", capture_output=True, text=True, env=env, timeout=30)

    def test_runs_from_another_directory(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "proj"
            (proj / ".claude").mkdir(parents=True)
            (proj / ".claude" / "h.py").write_text("import os\nprint(os.getcwd())\n", encoding="utf-8")
            other = Path(d) / "other"
            other.mkdir()
            r = self.run_from(other, ".claude/h.py", project=proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()).resolve(), proj.resolve())  # 根に戻ってから動かす

    def test_missing_hook_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_from(d, ".claude/skills/none/hooks/gone.py")
            self.assertEqual(r.returncode, 0)  # 2 を返すと全部のツールが止まる

    def test_exit_code_passes_through(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / ".claude").mkdir()
            (proj / ".claude" / "stop.py").write_text("import sys\nprint('x', file=sys.stderr)\nsys.exit(2)\n", encoding="utf-8")
            r = self.run_from(d, ".claude/stop.py", project=proj)
            self.assertEqual(r.returncode, 2)  # 止める判定はそのまま伝える


if __name__ == "__main__":
    unittest.main()
