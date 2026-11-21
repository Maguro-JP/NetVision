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
        anchored = (RUN, 'cd "${CLAUDE_PROJECT_DIR:-.}" && ')
        tpl = json.loads((ROOT / ".claude/skills/repo-bootstrap/templates/settings.json").read_text(encoding="utf-8"))
        ours = set()
        for groups in tpl["hooks"].values():
            for g in groups:
                for h in g["hooks"]:
                    self.assertTrue(h["command"].startswith(anchored), h["command"])  # ひな形は全部
                    ours.add(sync.core(h["command"]))
        # このテストは配った先でも動く。配った先の settings.json には、そのリポジトリ自身の登録がある（触らない決まり）。
        # 見るのは、ひな形にある登録（配った物）だけ
        d = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        for groups in d["hooks"].values():
            for g in groups:
                for h in g["hooks"]:
                    if sync.core(h["command"]) in ours:
                        self.assertTrue(h["command"].startswith(anchored), h["command"])

    def test_superseded_registration_is_dropped_only_when_its_successor_exists(self):
        old = "cat .claude/skills/work-style/SKILL.md 2>/dev/null || true"
        new = 'cd "${CLAUDE_PROJECT_DIR:-.}" && awk \'1\' .claude/skills/work-style/SKILL.md 2>/dev/null || true'
        own = "cat docs/notes.md"
        cur = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": old}]},
                                           {"hooks": [{"type": "command", "command": new}, {"type": "command", "command": own}]}]}}
        self.assertEqual(sync.drop_superseded(cur), 1)
        self.assertEqual([h["command"] for g in cur["hooks"]["SessionStart"] for h in g["hooks"]], [new, own])  # 空になった組も消える
        self.assertEqual(sync.drop_superseded(cur), 0)
        alone = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": old}]}]}}
        self.assertEqual(sync.drop_superseded(alone), 0)  # 後継が無ければ残す
        self.assertEqual(len(alone["hooks"]["SessionStart"][0]["hooks"]), 1)


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
