#!/usr/bin/env python3
"""bugs.py（バグの帳簿の読み書き）を、一時ディレクトリの帳簿で確かめる。

帳簿が違うことを言うと、フックが「直していない致命的は無い」と通してしまう。足す・書き換える・数える、の 3 つを実際のファイルで見る。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bugs  # noqa: E402


def run(root, *args):
    r = subprocess.run([sys.executable, str(HERE / "bugs.py"), "--root", str(root), *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def text(self):
        return (self.root / bugs.FILE).read_text(encoding="utf-8")


class Judge(unittest.TestCase):
    def test_fatal_words(self):
        for s in ("保存するとクラッシュする", "起動しない", "画面が固まって操作できない", "設定のファイルが破損する", "保存したデータが消えた",
                  "API の鍵がログに漏洩している", "Traceback (most recent call last) が出て終わる", "ウィンドウが真っ白のまま"):
            self.assertEqual(bugs.judge(s), "致命的", s)

    def test_light_words(self):
        for s in ("ボタンの文言が古い", "見出しの余白が広い", "説明に誤字がある"):
            self.assertEqual(bugs.judge(s), "軽い", s)

    def test_unsure_is_heavy(self):
        # 迷ったら重い側。語で決められない物を、軽いにしない
        for s in ("検索の結果が 1 件足りない", "並べ替えが効かない"):
            self.assertEqual(bugs.judge(s), "重い", s)

    def test_failed_test_is_not_a_crash(self):
        # テストや Actions が「落ちた」は、アプリが落ちたのとは違う
        self.assertEqual(bugs.judge("main のテストが落ちている"), "重い")
        self.assertEqual(bugs.judge("Actions が落ちた"), "重い")
        self.assertEqual(bugs.judge("アプリが落ちる"), "致命的")

    def test_fatal_wins_over_light(self):
        self.assertEqual(bugs.judge("文言を変えるとクラッシュする"), "致命的")


class AddAndSet(Base):
    def test_add_creates_ledger_from_template(self):
        b = bugs.add(self.root, "保存のあとに起動しない", repro="保存して閉じ、起動する", source="ユーザーの発言")
        self.assertEqual(b["id"], "B-001")
        self.assertEqual(b["重さ"], "致命的")
        self.assertIn("# バグの帳簿", self.text())
        got = bugs.load(self.root)
        self.assertEqual(len(got), 1)  # ひな形の見本の節（コメントの中）は数えない
        self.assertEqual(got[0]["再現の手順"], "保存して閉じ、起動する")
        self.assertEqual(got[0]["状態"], "未修正")
        self.assertRegex(got[0]["見つけた日"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_ids_count_up(self):
        ids = [bugs.add(self.root, f"不具合 {i}")["id"] for i in range(3)]
        self.assertEqual(ids, ["B-001", "B-002", "B-003"])

    def test_explicit_severity_is_kept(self):
        self.assertEqual(bugs.add(self.root, "ボタンの文言が古い", severity="重い")["重さ"], "重い")

    def test_newline_in_value_stays_in_one_field(self):
        bugs.add(self.root, "起動しない", repro="1 行目\n- 状態: 修正済み")
        got = bugs.load(self.root)[0]
        self.assertEqual(got["状態"], "未修正")
        self.assertIn("1 行目", got["再現の手順"])

    def test_set_changes_the_real_section_not_the_sample(self):
        # ひな形の見本も「## B-001」で始まる。書き換えるのは、コメントの外の節
        bugs.add(self.root, "保存のあとに起動しない")
        b = bugs.set_fields(self.root, "B-001", {"状態": "修正済み", "直したコミット": "abc1234", "修正版": "v1.2.4"})
        self.assertEqual(b["状態"], "修正済み")
        self.assertEqual(b["修正版"], "v1.2.4")
        self.assertTrue(b["直した日"])
        sample = self.text().split("-->")[0]
        self.assertIn("- 状態: 未修正", sample)
        self.assertEqual(bugs.open_fatal(bugs.load(self.root)), [])

    def test_set_only_touches_that_section(self):
        bugs.add(self.root, "起動しない")
        bugs.add(self.root, "クラッシュする")
        bugs.set_fields(self.root, "B-002", {"状態": "修正中"})
        got = {b["id"]: b["状態"] for b in bugs.load(self.root)}
        self.assertEqual(got, {"B-001": "未修正", "B-002": "修正中"})

    def test_set_adds_missing_field(self):
        p = self.root / bugs.FILE
        p.parent.mkdir(parents=True)
        p.write_text("# バグの帳簿\n\n## B-001 起動しない\n- 重さ: 致命的\n- 状態: 未修正\n\n## B-002 文言\n- 重さ: 軽い\n- 状態: 未修正\n", encoding="utf-8")
        bugs.set_fields(self.root, "B-001", {"修正版": "v0.1.1"})
        got = bugs.load(self.root)
        self.assertEqual(got[0]["修正版"], "v0.1.1")
        self.assertEqual([b["id"] for b in got], ["B-001", "B-002"])

    def test_set_unknown_id(self):
        bugs.add(self.root, "起動しない")
        self.assertIsNone(bugs.set_fields(self.root, "B-009", {"状態": "修正中"}))
        self.assertIsNone(bugs.set_fields(self.root / "none", "B-001", {"状態": "修正中"}))


class OpenFatal(Base):
    def test_open_states(self):
        # (状態, 直していない致命的の数, ほかの開発より先に直す数)。確認待ちは、残りには数えるが、ほかの開発は止めない
        for state, n, block in (("未修正", 1, 1), ("修正中", 1, 1), ("確認待ち", 1, 0), ("修正済み", 0, 0), ("対象外", 0, 0), ("", 1, 1)):
            text = f"## B-001 起動しない\n- 重さ: 致命的\n- 状態: {state}\n"
            self.assertEqual(len(bugs.open_fatal(bugs.parse(text))), n, state)
            self.assertEqual(len(bugs.blocking_fatal(bugs.parse(text))), block, state)

    def test_only_fatal(self):
        text = "## B-001 文言\n- 重さ: 軽い\n- 状態: 未修正\n\n## B-002 検索\n- 重さ: 重い\n- 状態: 未修正\n"
        self.assertEqual(bugs.open_fatal(bugs.parse(text)), [])

    def test_no_ledger(self):
        self.assertEqual(bugs.load(self.root), [])


class Stats(unittest.TestCase):
    TEXT = ("## B-001 起動しない\n- 重さ: 致命的\n- 状態: 修正済み\n- 見つけた日: 2026-11-10 09:00\n- 直した日: 2026-11-10 10:35\n\n"
            "## B-002 クラッシュ\n- 重さ: 致命的\n- 状態: 未修正\n- 見つけた日: 2026-11-12 09:00\n\n"
            "## B-003 文言\n- 重さ: 軽い\n- 状態: 修正済み\n- 見つけた日: 2026-11-01 09:00\n- 直した日: 2026-11-02 09:00\n")

    def test_line(self):
        self.assertEqual(bugs.stats(bugs.parse(self.TEXT)), "バグ: 見つけた 3、直した 2、残り 1（致命的 1）、致命的を直すまで 中央 1 時間 35 分・最長 1 時間 35 分")

    def test_since(self):
        self.assertEqual(bugs.stats(bugs.parse(self.TEXT), "2026-11-09"), "バグ: 見つけた 2、直した 1、残り 1（致命的 1）、致命的を直すまで 中央 1 時間 35 分・最長 1 時間 35 分")

    def test_none(self):
        self.assertEqual(bugs.stats([]), "バグ: 見つけた 0、直した 0、残り 0（致命的 0）、致命的を直すまで 該当なし")


class Cli(Base):
    def test_flow(self):
        code, out = run(self.root, "--list")
        self.assertEqual(code, 0)
        self.assertIn("まだありません", out)
        code, out = run(self.root, "--add", "保存するとクラッシュする", "--repro", "保存を押す")
        self.assertEqual(code, 0)
        self.assertIn("B-001 を足しました（致命的）", out)
        self.assertIn("今の作業を止めて", out)
        code, out = run(self.root, "--count", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("直していない致命的: 1 件", out)
        self.assertEqual(run(self.root, "--count")[0], 0)  # --strict が無ければ、数えるだけ
        code, out = run(self.root, "--set", "B-001", "--state", "修正済み", "--commit", "abc1234")
        self.assertEqual(code, 0)
        self.assertIn("修正版の欄が空", out)
        code, out = run(self.root, "--count", "--strict")
        self.assertEqual(code, 0)
        self.assertIn("直していない致命的: 0 件", out)
        code, out = run(self.root, "--list", "--open", "--json")
        self.assertEqual(json.loads(out), [])
        code, out = run(self.root, "--stats")
        self.assertIn("見つけた 1、直した 1、残り 0", out)

    def test_errors(self):
        self.assertEqual(run(self.root, "--set", "B-001", "--state", "修正中")[0], 1)
        run(self.root, "--add", "起動しない")
        self.assertEqual(run(self.root, "--set", "B-001")[0], 2)


if __name__ == "__main__":
    unittest.main()
