#!/usr/bin/env python3
"""emotion.py を、作った一言と transcript で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
from emotion import classify, repeated  # noqa: E402

HOOK = HERE.parent / "hooks" / "emotion.py"


def run(prompt, transcript=None, state=None, off=False):
    env = dict(os.environ)
    env.pop("USER_EMOTION_OFF", None)
    if state:
        env["USER_EMOTION_STATE"] = state
    if off:
        env["USER_EMOTION_OFF"] = "1"
    d = {"prompt": prompt, "session_id": "s"}
    if transcript:
        d["transcript_path"] = transcript
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(d, ensure_ascii=False), capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


class Emotion(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("マージしろって 何度も言わせんなよ")[0], "いらだち")
        self.assertEqual(classify("無能なんだから 常に間違ってると疑って")[0], "怒り")  # content-audit: ignore（検出語の例）
        self.assertEqual(classify("まだ? いつ終わるの")[0], "急かし")
        self.assertEqual(classify("また同じこと言わせるのか")[0], "いらだち")  # 強い方が先
        self.assertEqual(classify("結局直ってない")[0], "失望")
        self.assertEqual(classify("任せる")[0], "承認")
        self.assertEqual(classify("README の一覧を直して")[0], "")

    def test_repeated(self):
        self.assertEqual(repeated("マージしろって 何度も言わせんなよ", ["Ciel の PR をマージして進めて", "UI を直して"], cue=True), 2)
        self.assertEqual(repeated("マージしろって 何度も言わせんなよ", ["Ciel の PR をマージして進めて"], cue=False), 1)  # 1 語の重なりは、いらだちが無ければ数えない
        self.assertEqual(repeated("PR #94 をマージしろ", ["PR #94 をマージして配って"]), 2)  # 2 語以上の重なり
        self.assertEqual(repeated("Ciel の README を直して", ["Ciel の PR をマージして"]), 1)  # Ciel と PR は数えない
        self.assertEqual(repeated("週報を書いて", ["画面を直して"]), 1)

    def test_hook_output_and_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run("だからしていいから進めろって言ってんじゃん トークン無駄にすんな", state=d)
            self.assertEqual(code, 0)
            self.assertIn("感情: いらだち", out)
            self.assertIn("聞き返さない", out)
            self.assertNotIn("お怒り", out)
            rows = (Path(d) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(rows[-1])["emotion"], "いらだち")
            self.assertEqual(run("README の一覧を直して", state=d)[1], "")  # 感情が無ければ黙る
            self.assertEqual(run("しろよ", state=d, off=True)[1], "")
            self.assertEqual(run("<task-notification>x</task-notification>", state=d)[1], "")
            t = Path(d) / "t.jsonl"
            rows = [{"type": "user", "message": {"content": "Ciel の PR #94 をマージして配って"}},
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": "❔ マージしてよいですか"}]}},
                    {"type": "user", "message": {"content": "Stop hook feedback: x"}},
                    {"type": "user", "message": {"content": "PR #94 をマージしろって言ってんじゃん"}}]
            t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
            out = run("PR #94 をマージしろって言ってんじゃん", transcript=str(t), state=d)[1]
            self.assertIn("同じ指示が", out)


if __name__ == "__main__":
    unittest.main()
