#!/usr/bin/env python3
"""fanout.py を、作った作業の列と get_session の形の JSON で確かめる。"""

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fanout  # noqa: E402

CFG = fanout.load_models()
PARENT = "claude-fable-5-2"


def task(title, paths, steps=20, detail="", **kw):
    return dict({"title": title, "paths": paths, "steps": steps, "detail": detail}, **kw)


def make(tasks, **kw):
    return fanout.plan(tasks, CFG, repo="owner/repo", branch="claude/work-1", parent_model=PARENT, **kw)


class Models(unittest.TestCase):
    def test_table_is_read_from_models_md(self):
        self.assertEqual(CFG["models"]["普通"], {"session": "claude-sonnet-5-5", "agent": "sonnet"})
        self.assertEqual(CFG["models"]["易しい"], {"session": "claude-haiku-5-5", "agent": "haiku"})
        self.assertEqual(CFG["models"]["難しい"]["session"], fanout.SAME)
        self.assertIn("設計", CFG["words"]["難しい"])

    def test_ids_are_not_written_in_the_script(self):
        src = (HERE / "fanout.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"claude-(sonnet|haiku|opus|fable)-\d")

    def test_difficulty_by_words_and_scale(self):
        self.assertEqual(fanout.difficulty(task("フックの判定を直す", ["a"]), CFG), "難しい")
        self.assertEqual(fanout.difficulty(task("README の書き直し", ["README.md"]), CFG), "普通")
        self.assertEqual(fanout.difficulty(task("スキルの一覧の更新", ["docs/USAGE.md"]), CFG), "易しい")
        self.assertEqual(fanout.difficulty(task("何かをする", ["a"]), CFG), "普通")  # 分からなければ普通
        self.assertEqual(fanout.difficulty(task("一覧の更新", ["a"], steps=45), CFG), "難しい")  # 規模が先
        three = [f".claude/skills/{n}/SKILL.md" for n in ("a", "b", "c")]
        self.assertEqual(fanout.difficulty(task("一覧の更新", three), CFG), "難しい")
        self.assertEqual(fanout.difficulty(task("設計の一覧の更新", ["a"]), CFG), "難しい")  # 複数に当たれば難しい側
        self.assertEqual(fanout.difficulty(task("設計", ["a"], difficulty="易しい"), CFG), "易しい")  # 渡された物が先

    def test_model_for(self):
        self.assertEqual(fanout.model_for("難しい", PARENT, CFG), {"session": PARENT, "agent": "fable"})
        self.assertEqual(fanout.model_for("普通", PARENT, CFG), {"session": "claude-sonnet-5-5", "agent": "sonnet"})
        self.assertEqual(fanout.model_for("易しい", PARENT, CFG), {"session": "claude-haiku-5-5", "agent": "haiku"})
        self.assertEqual(fanout.model_for("難しい", "", CFG), {"session": None, "agent": None})  # 親が分からなければ省く


class Plan(unittest.TestCase):
    def test_independent_tasks_are_split(self):
        p = make([task("README の書き直し", ["README.md"]), task("フックの判定を直す", [".claude/skills/x/hooks/a.py"])])
        self.assertTrue(p["split"])
        self.assertEqual(len(p["bundles"]), 2)
        by = {b["topic"]: b for b in p["bundles"]}
        self.assertEqual(by["README の書き直し"]["model"], "claude-sonnet-5-5")
        self.assertEqual(by["フックの判定を直す"]["model"], PARENT)
        self.assertEqual(by["README の書き直し"]["title"], "FB repo README の書き直し")
        self.assertIn("feedback-session:auto", by["README の書き直し"]["tags"])
        self.assertEqual(by["README の書き直し"]["deny"], [".claude/skills/x/hooks/a.py"])
        self.assertEqual(by["README の書き直し"]["create_session"]["outcome_branch"], "claude/work-1")

    def test_overlapping_paths_go_to_one_bundle(self):
        p = make([task("A を直す", [".claude/skills/x"]), task("B を直す", [".claude/skills/x/SKILL.md"]),
                  task("C を直す", ["docs/USAGE.md"]), task("D を直す", ["docs/USAGE.md", ".claude/skills/x/hooks"])])
        self.assertFalse(p["split"])  # D が x と USAGE をつなぐので、全部 1 つ
        self.assertIn("分けない", fanout.plan_markdown(p))
        p = make([task("A を直す", [".claude/skills/x"]), task("B を直す", [".claude/skills/x/SKILL.md"]), task("C を直す", ["docs/USAGE.md"])])
        self.assertEqual(sorted(len(b["tasks"]) for b in p["bundles"]), [1, 2])
        self.assertFalse(fanout.overlap("docs/USAGE.md", "docs/USAGE.md.bak"))
        self.assertTrue(fanout.overlap("docs", "docs/USAGE.md"))

    def test_max_bundles(self):
        tasks = [task(f"作業 {i} を作る", [f"dir{i}"], steps=10 + i) for i in range(5)]
        p = make(tasks)
        self.assertEqual(len(p["bundles"]), 3)
        self.assertEqual(sum(len(b["tasks"]) for b in p["bundles"]), 5)
        self.assertEqual(len(make(tasks, max_bundles=2)["bundles"]), 2)
        for b in p["bundles"]:  # まとめた束でも、ほかの束の場所は触らない場所に入る
            self.assertFalse(set(b["allow"]) & set(b["deny"]))

    def test_small_tasks_stay_with_parent(self):
        p = make([task("誤字", ["a.md"], steps=3), task("B を作る", ["b"]), task("C を作る", ["c"])])
        self.assertEqual([t["title"] for t in p["parent_tasks"]], ["誤字"])
        self.assertEqual(len(p["bundles"]), 2)
        p = make([task("誤字", ["a.md"], steps=3), task("B を作る", ["b"])])
        self.assertFalse(p["split"])
        self.assertEqual(len(p["parent_tasks"]), 2)
        p = make([task("一覧の更新", ["a.md"], steps=None), task("B を作る", ["b"], steps=None), task("C を作る", ["c"], steps=None)])
        self.assertEqual([t["title"] for t in p["parent_tasks"]], ["一覧の更新"])  # 手数が分からない易しい作業は 5 手と見積もる

    def test_keep_and_unknown_paths(self):
        p = make([task("README を直す", ["README.md"]), task("B を作る", ["b"]), task("C を作る", ["c"]), task("場所の無い作業を作る", [])], keep=["README.md"])
        self.assertEqual(sorted(t["why"] for t in p["parent_tasks"]), ["親が持つ場所に触る", "触る場所が分からない"])
        self.assertIn("README.md", p["bundles"][0]["deny"])

    def test_prompt_has_every_rule(self):
        p = make([task("README の書き直し", ["README.md"], detail="魅せる形にする", done="readme_check.py が 10/10"), task("B を作る", ["b"])])
        for b in p["bundles"]:
            self.assertEqual(fanout.missing_rules(b["prompt"]), [])
            self.assertIn("claude/work-1", b["prompt"])
            self.assertIn("git pull --rebase origin claude/work-1", b["prompt"])
        text = p["bundles"][0]["prompt"]
        self.assertIn("魅せる形にする", text)
        self.assertIn("readme_check.py が 10/10", text)
        self.assertIn("- b", text.split("# 触らない場所")[1])
        self.assertEqual(fanout.missing_rules("PR を作らない"), [r for r in fanout.REQUIRED if r != "PR を作らない"])

    def test_parse_task(self):
        t = fanout.parse_task("README の書き直し | ./README.md, docs/images/ | 20 手 | 魅せる形に")
        self.assertEqual(t, {"title": "README の書き直し", "paths": ["README.md", "docs/images"], "steps": 20, "detail": "魅せる形に"})
        self.assertEqual(fanout.parse_task("題だけ")["paths"], [])

    def test_from_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "reports").mkdir(parents=True)
            (root / ".claude" / "skills" / "alpha").mkdir(parents=True)
            (root / "docs" / "reports" / "deliverables.md").write_text(
                "| # | 成果物 | 完成条件 | 状態 | 残り | 更新 |\n|---|---|---|---|---|---|\n"
                "| 1 | 終わった物 | x | 完成 | 無し | 1 |\n| 2 | 仕組み（alpha） | 動く | 進行中 | `docs/reports` を直す | 2 |\n| 3 | 待ち | y | 確認待ち | z | 3 |\n", encoding="utf-8")
            (root / "docs" / "reports" / "pending.md").write_text(
                "# 判断待ち\n\n## 2026-01-01 答えの無い物\n- 決めてほしいこと: a\n\n## 2026-01-02 答えのある物\n- 答え: 進める\n\n## 2026-01-03 済んだ物\n- 答え: 進める。反映済み\n", encoding="utf-8")
            tasks = fanout.tasks_from_reports(root)
            self.assertEqual([t["title"] for t in tasks], ["仕組み（alpha）", "答えのある物"])
            self.assertEqual(tasks[0]["paths"], [".claude/skills/alpha", "docs/reports"])

    def test_cli_plan_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            j, m = Path(d) / "p.json", Path(d) / "p.md"
            r = subprocess.run([sys.executable, "-I", str(HERE / "fanout.py"), "plan", "--repo", "owner/repo", "--branch", "claude/work-1",
                                "--parent-model", PARENT, "--task", "README の書き直し | README.md | 20", "--task", "フックの判定を直す | hooks | 30",
                                "--json", str(j), "--md", str(m)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(j.read_text(encoding="utf-8"))
            self.assertEqual([b["model"] for b in data["bundles"]], ["claude-sonnet-5-5", PARENT])
            self.assertIn("```", m.read_text(encoding="utf-8"))
            self.assertNotIn("```", r.stdout)  # 会話には表だけ。プロンプトの全文はファイル
            self.assertIn("| FB repo README の書き直し | 普通 | claude-sonnet-5-5 | sonnet |", r.stdout)


class RecordAndStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("FANOUT_STATE_DIR")
        os.environ["FANOUT_STATE_DIR"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("FANOUT_STATE_DIR", None)
        else:
            os.environ["FANOUT_STATE_DIR"] = self.old
        self.tmp.cleanup()

    @staticmethod
    def sess(sid, status="SESSION_STATUS_RUNNING", bucket="SESSION_STATUS_BUCKET_WORKING", used=100, summary="reading", **kw):
        return {"ccr": dict({"id": sid, "title": f"FB repo {sid}", "session_status": status, "status_bucket": bucket, "connection_status": "connected",
                             "session_context": {"model": "m"}, "external_metadata": {"context_usage": {"used_tokens": used}, "task_summary": summary}}, **kw)}

    def test_record_writes_outside_the_repository(self):
        fanout.record("owner/repo", "s1", "FB repo A", "m", ["feedback-session:auto"], "claude/work-1")
        fanout.record("owner/repo", "s1", model="m2")  # 同じ id は 1 行のまま、渡した欄だけ変える
        f = fanout.state_file("owner/repo")
        self.assertEqual(f.parent, Path(self.tmp.name))
        self.assertEqual(f.name, "owner__repo.json")
        rows = json.loads(f.read_text(encoding="utf-8"))["sessions"]
        self.assertEqual([(r["id"], r["title"], r["model"]) for r in rows], [("s1", "FB repo A", "m2")])

    def test_status(self):
        for sid in ("run", "done", "failed", "stuck", "unseen"):
            fanout.record("owner/repo", sid, f"FB repo {sid}")
        t0 = dt.datetime(2026, 11, 14, 9, 0, tzinfo=dt.timezone.utc)
        data = [self.sess("run"), self.sess("done", status="SESSION_STATUS_IDLE", bucket="SESSION_STATUS_BUCKET_REVIEW_READY", post_turn_summary={"recent_action": "★ FB done"}),
                self.sess("failed", bucket="SESSION_STATUS_BUCKET_FAILED"), self.sess("stuck")]
        by = {r["id"]: r for r in fanout.status("owner/repo", data, t0)}
        self.assertEqual({k: v["state"] for k, v in by.items()}, {"run": "動いている", "done": "終わった", "failed": "止まった", "stuck": "動いている", "unseen": "見ていない"})
        self.assertIn("取り込む", by["done"]["action"])
        self.assertEqual(by["done"]["note"], "★ FB done")
        self.assertIn("アーカイブして引き取る", by["failed"]["action"])
        # 12 分後。run はトークンが動き、stuck は何も動いていない
        data = [self.sess("run", used=900, summary="editing"), self.sess("stuck")]
        by = {r["id"]: r for r in fanout.status("owner/repo", data, t0 + dt.timedelta(minutes=12))}
        self.assertEqual((by["run"]["state"], by["stuck"]["state"]), ("動いている", "止まった"))
        self.assertEqual(by["stuck"]["quiet_min"], 12)
        self.assertEqual(by["run"]["quiet_min"], 0)
        by = {r["id"]: r for r in fanout.status("owner/repo", [self.sess("stuck")], t0 + dt.timedelta(minutes=12), stall_min=30)}
        self.assertEqual(by["stuck"]["state"], "動いている")  # しきい値は変えられる

    def test_archived_rows_drop_out(self):
        fanout.record("owner/repo", "a", "FB repo a")
        fanout.record("owner/repo", "b", "FB repo b")
        fanout.record("owner/repo", "a", archived=True)
        now = dt.datetime(2026, 11, 14, 9, 0, tzinfo=dt.timezone.utc)
        rows = fanout.status("owner/repo", [self.sess("b", status="SESSION_STATUS_ARCHIVED")], now)
        self.assertEqual([(r["id"], r["state"]) for r in rows], [("b", "片付け済み")])
        self.assertEqual(fanout.status("owner/repo", [], now), [])
        self.assertIn("無い", fanout.status_markdown([]))

    def test_cli_status(self):
        fanout.record("owner/repo", "run", "FB repo run")
        f = Path(self.tmp.name) / "in.json"
        f.write_text(json.dumps(self.sess("run", updated_at="2026-11-14T08:47:01.685843123Z")), encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(HERE / "fanout.py"), "status", "--repo", "owner/repo", "--input", str(f), "--now", "2026-11-14T09:00:00Z"],
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("| FB repo run | 動いている |", r.stdout)
        self.assertIn("終わった 0、動いている 1、止まった 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
