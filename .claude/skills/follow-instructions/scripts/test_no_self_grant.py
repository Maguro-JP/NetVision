#!/usr/bin/env python3
"""no_self_grant.py を、作ったツール入力で確かめる。"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "no_self_grant.py"


def run(tool: str, ti: dict) -> int:
    env = dict(os.environ)
    env.pop("NO_SELF_GRANT_OFF", None)
    return subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": ti}),
                          capture_output=True, text=True, env=env).returncode


class NoSelfGrant(unittest.TestCase):
    def test_permissions_in_settings_is_blocked(self):
        self.assertEqual(run("Edit", {"file_path": "/r/.claude/settings.json", "new_string": '"permissions": {"allow": ["Bash(git:*)"]}'}), 2)
        self.assertEqual(run("Write", {"file_path": "/r/.claude/settings.local.json", "content": '{"permissions":{"allow":["Edit"]}}'}), 2)

    def test_hooks_in_settings_is_allowed(self):
        self.assertEqual(run("Edit", {"file_path": "/r/.claude/settings.json", "new_string": '"hooks": {"Stop": []}'}), 0)

    def test_policy_file_is_blocked(self):
        self.assertEqual(run("Write", {"file_path": "/r/.claude/policy/me.json", "content": '{"auto_merge": true}'}), 2)
        self.assertEqual(run("Bash", {"command": "echo '{}' > .claude/policy/me.json"}), 2)

    def test_bash_settings_permissions_is_blocked_and_hooks_write_is_not(self):
        self.assertEqual(run("Bash", {"command": "python3 -c 'import json; d={\"permissions\":{}}; open(\".claude/settings.json\",\"w\").write(json.dumps(d))'"}), 2)
        self.assertEqual(run("Bash", {"command": "python3 .claude/skills/skill-sync/scripts/sync.py /r --apply"}), 0)
        self.assertEqual(run("Bash", {"command": "cat .claude/settings.json"}), 0)

    def test_other_files_are_allowed(self):
        self.assertEqual(run("Write", {"file_path": "/r/docs/PERMISSIONS.md", "content": '"permissions" の説明'}), 0)

    def test_mentioning_the_path_in_text_is_allowed(self):
        # 文章の中に場所の名前が出るだけ（説明の編集、コミットの文）は止めない
        self.assertEqual(run("Bash", {"command": "git commit -F - <<'EOF'\n.claude/policy/ の方針はユーザーが入れる\nEOF\ngit push 2>&1 | tail -1"}), 0)
        self.assertEqual(run("Bash", {"command": "python3 - <<'EOF'\ns=s.replace('`.claude/policy/` の方針', 'x'); p.write_text(s)\nEOF\necho ok > /tmp/out.txt"}), 0)
        self.assertEqual(run("Bash", {"command": "sed -i 's/a/b/' .claude/settings.json  # \"permissions\" を直す"}), 2)
        self.assertEqual(run("Bash", {"command": "cp /tmp/x.json .claude/policy/me.json"}), 2)


if __name__ == "__main__":
    unittest.main()
