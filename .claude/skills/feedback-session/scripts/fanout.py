#!/usr/bin/env python3
"""独立した作業を子のセッションに分ける計画を作り、作ったセッションを覚え、状態を表にする。

セッションを立てる手数（設定を取る、題と tag を決める、プロンプトを書く）が多いと、分けずに 1 本で進めてしまい、開発が遅いままになる。
ここで、作業の列から束・題・tag・難易度・モデル・触ってよい場所・プロンプトの全文までを 1 回で出す。
セッションを作る・見る・片付けるのは親のセッション（create_session、get_session、archive_session）。このスクリプトは外に何も送らない。

使い方:
  fanout.py plan --task "<題> | <場所>,<場所> | <手数> | <説明>" [--task ...] [--tasks 作業.json] [--from-reports]
                 [--repo owner/repo] [--branch 親のブランチ] [--parent-model ID] [--permission-mode M]
                 [--max 3] [--min-steps 10] [--keep 親が持つ場所,...] [--json 出力.json] [--md 出力.md]
  fanout.py record --repo owner/repo --session-id ID --title 題 [--model ID] [--tag T ...] [--branch B] [--archived]
  fanout.py status --repo owner/repo --input get_session の結果.json [--stall-min 10] [--now ISO] [--json]

plan の --task は「|」で区切る。場所・手数・説明は省ける。--tasks は [{"title", "paths", "steps", "detail", "difficulty", "done"}] の JSON。
--from-reports は docs/reports/deliverables.md の未完成（未着手・進行中）と、docs/reports/pending.md の答えが付いて反映していない項目を読む。
record と status は ~/.claude/state/fanout/ に書く（リポジトリには書かない。public なので ID を残せない）。FANOUT_STATE_DIR で場所を変えられる。
難易度の語とモデルの ID は references/models.md を読む。
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS_MD = HERE.parent / "references" / "models.md"
LEVELS = ("易しい", "普通", "難しい")
SAME = "親と同じ"
DEFAULT_MAX = 3        # 束の上限（私の推測。レート制限は全部のセッションで共有する）
DEFAULT_MIN_STEPS = 10  # これに満たない作業は分けない（セッションを 1 つ立てるだけで文脈が乗る）
DEFAULT_STALL_MIN = 10  # この分数、費用もトークンも動いていなければ止まったと見る（私の推測）

# 子のプロンプトに必ず入れる決まり。親が子のセッションを立てて分かったことから来ている（test_fanout.py が全部入っているかを見る）
RULES_PUSH = (
    "親のブランチ {branch} にそのまま積む。自分のブランチは作らない（リモートのブランチは消せないので、残る物を増やさない）",
    "区切りごとに小さくコミットし、`git pull --rebase origin {branch}` のあと `git push origin HEAD:{branch}` で push する。親とほかの子も同じブランチに push するので、push の前に必ず rebase する",
    "ターンを終える前に、必ずコミットして push する。作りかけを手元だけに残さない",
    "Agent（fork、subagent）は使わない。裏に出すのは長いコマンドだけにし、通知が来たら続ける",
    "コマンドはリポジトリの根で動かす。cd で移動しない",
    "コミットの末尾は Co-Authored-By の行だけにし、セッションの URL は書かない",
)
RULES_DONT = (
    "PR を作らない", "main に push しない", "マージしない", "ほかのリポジトリを clone しない",
    "セッションやルーティンを作らない・消さない・変えない",
    "ユーザーに質問しない（❔ を出さない。決められないことは「私の推測」と書いて進め、報告に書く）",
)
REPORT = ("最後に 1 つの返答で「★ FB {title}: {branch}（<HEAD 7 桁>）」と、足したコミットの一覧、変えたファイル、テストと検査の最後の行、"
          "確かめていないこと、私の推測で決めた値、親に書いてほしい記録を書いて終える。")
REQUIRED = ("rebase", "git push origin HEAD:", "区切りごと", "終える前に", "Agent（fork、subagent）は使わない", "cd で移動しない",
            "PR を作らない", "main に push しない", "マージしない", "clone しない", "セッションやルーティンを作らない",
            "ユーザーに質問しない", "★ FB ")


# ---- 表（references/models.md）----

def md_tables(text: str) -> dict:
    """見出し（## ）ごとに、最初の表の行（見出しの行と区切りの行を除く）を返す。"""
    out, head = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:].strip()
            out[head] = []
        elif head and line.startswith("|") and not re.match(r"^\|\s*:?-{2,}", line):
            out[head].append([c.strip() for c in line.strip().strip("|").split("|")])
    return {k: v[1:] for k, v in out.items() if len(v) > 1}


def load_models(path: Path = MODELS_MD) -> dict:
    t = md_tables(path.read_text(encoding="utf-8"))
    scale = {}
    for row in t.get("規模の決まり", []):
        try:
            scale[row[0]] = int(row[1])
        except (ValueError, IndexError):
            continue
    return {
        "models": {r[0]: {"session": r[1], "agent": r[2]} for r in t.get("モデルの表", []) if len(r) >= 3},
        "words": {r[0]: [w.strip() for w in re.split(r"[、,]", r[1]) if w.strip()] for r in t.get("難易度の語", []) if len(r) >= 2},
        "hard_steps": scale.get("手数がこの数以上なら難しい", 40),
        "hard_skills": scale.get("触るスキルがこの数以上なら難しい", 3),
        "guess": {lv: scale.get(f"手数が分からないときの見積もり（{lv}）", d) for lv, d in zip(LEVELS, (5, 15, 30))},
        "alias": [(r[0], r[1]) for r in t.get("親のモデルと Agent の model", []) if len(r) >= 2],
    }


def skills_of(paths) -> set:
    return {m.group(1) for p in paths for m in [re.search(r"\.claude/skills/([^/]+)", p)] if m}


def difficulty(task: dict, cfg: dict) -> str:
    """規模を先に見て、次に語を見る。複数に当たれば難しい側。分からなければ普通。"""
    given = str(task.get("difficulty") or "")
    if given in LEVELS:
        return given
    steps = task.get("steps")
    if isinstance(steps, int) and steps >= cfg["hard_steps"]:
        return "難しい"
    if len(skills_of(task.get("paths") or [])) >= cfg["hard_skills"]:
        return "難しい"
    text = f"{task.get('title', '')} {task.get('detail', '')}"
    for lv in reversed(LEVELS):
        if any(w in text for w in cfg["words"].get(lv, [])):
            return lv
    return "普通"


def model_for(level: str, parent_model: str, cfg: dict) -> dict:
    """難易度から、create_session に渡す model と、Agent ツールに渡す model を返す。Agent が None なら model を省く。"""
    row = cfg["models"].get(level) or cfg["models"].get("普通") or {"session": SAME, "agent": SAME}
    session = parent_model if row["session"] == SAME else row["session"]
    agent = row["agent"]
    if agent == SAME:
        agent = next((a for prefix, a in cfg["alias"] if parent_model and parent_model.startswith(prefix)), None)
    return {"session": session or None, "agent": agent}


# ---- 作業の列 ----

def norm_path(p: str) -> str:
    p = p.strip().strip("`'\"")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def parse_task(spec: str) -> dict:
    parts = [x.strip() for x in spec.split("|")]
    task = {"title": parts[0], "paths": [], "steps": None, "detail": ""}
    if len(parts) > 1 and parts[1]:
        task["paths"] = [norm_path(p) for p in re.split(r"[,、\s]+", parts[1]) if norm_path(p)]
    if len(parts) > 2 and parts[2]:
        m = re.search(r"\d+", parts[2])
        task["steps"] = int(m.group(0)) if m else None
    if len(parts) > 3:
        task["detail"] = " | ".join(parts[3:]).strip()
    return task


def paths_in(text: str, root: Path) -> list:
    """文の中の `…` から、場所らしい物と、スキルの名前（.claude/skills/<名前> がある物）を拾う。"""
    out = []
    for tok in re.findall(r"`([^`\n]+)`", text) + re.findall(r"[（(、]\s*([a-z][a-z0-9-]+)(?=[、）)])", text):
        tok = tok.strip()
        if re.fullmatch(r"[a-z][a-z0-9-]+", tok) and (root / ".claude" / "skills" / tok).is_dir():
            out.append(f".claude/skills/{tok}")
        elif "/" in tok and " " not in tok and not tok.startswith(("http", "<")) and (root / norm_path(tok)).exists():
            out.append(norm_path(tok))
    return sorted(set(out))


def tasks_from_reports(root: Path) -> list:
    tasks = []
    deliverables = root / "docs" / "reports" / "deliverables.md"
    if deliverables.is_file():
        for line in deliverables.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not line.startswith("|") or len(cells) < 5 or not cells[0].isdigit():
                continue
            if cells[3] not in ("未着手", "進行中"):
                continue
            tasks.append({"title": cells[1][:40], "detail": f"完成条件: {cells[2]} 残り: {cells[4]}", "done": cells[2],
                          "paths": paths_in(cells[1] + cells[4], root), "steps": None, "source": "deliverables.md"})
    pending = root / "docs" / "reports" / "pending.md"
    if pending.is_file():
        for sec in re.split(r"(?m)^## ", pending.read_text(encoding="utf-8"))[1:]:
            title, _, body = sec.partition("\n")
            if "答え" not in body or "反映済み" in body:
                continue  # 答えの無い物はユーザーの返事待ち。反映済みは終わっている
            tasks.append({"title": re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", title).strip()[:40], "detail": " ".join(body.split())[:400],
                          "paths": paths_in(body, root), "steps": None, "source": "pending.md"})
    return tasks


def overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:30] if len(s) >= 3 else "t-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def group(tasks: list) -> list:
    """触る場所が重なる作業を同じ束にする（重なりをたどって、つながる物は全部 1 つ）。"""
    parent = list(range(len(tasks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(tasks):
        for j in range(i + 1, len(tasks)):
            if any(overlap(p, q) for p in a["paths"] for q in tasks[j]["paths"]):
                parent[find(j)] = find(i)
    bundles = {}
    for i, t in enumerate(tasks):
        bundles.setdefault(find(i), []).append(t)
    return list(bundles.values())


def plan(tasks: list, cfg: dict, *, repo: str, branch: str, parent_model: str, permission_mode: str = "",
         max_bundles: int = DEFAULT_MAX, min_steps: int = DEFAULT_MIN_STEPS, keep=()) -> dict:
    keep = [norm_path(k) for k in keep if norm_path(k)]
    parent_tasks, work = [], []
    for t in tasks:
        t = dict(t, paths=[norm_path(p) for p in t.get("paths") or [] if norm_path(p)])
        t["difficulty"] = difficulty(t, cfg)
        if not isinstance(t.get("steps"), int):
            t["steps"], t["steps_guessed"] = cfg["guess"][t["difficulty"]], True
        if t["steps"] < min_steps:
            parent_tasks.append(dict(t, why=f"{min_steps} 手に満たない"))
        elif not t["paths"]:
            parent_tasks.append(dict(t, why="触る場所が分からない"))
        elif any(overlap(p, k) for p in t["paths"] for k in keep):
            parent_tasks.append(dict(t, why="親が持つ場所に触る"))
        else:
            work.append(t)
    groups = group(work)
    while len(groups) > max(1, max_bundles):  # 上限を超えたら、手数の少ない束どうしを 1 本にまとめる（その子が順に進める）
        groups.sort(key=lambda g: sum(t["steps"] for t in g))
        groups[0:2] = [groups[0] + groups[1]]
    out = {"repo": repo, "branch": branch, "parent_model": parent_model, "split": len(groups) >= 2,
           "parent_tasks": parent_tasks, "bundles": []}
    if not out["split"]:
        n = len(groups)
        out["reason"] = f"独立した {min_steps} 手以上の作業が {n} つ" if n else f"{min_steps} 手以上で場所の分かる作業が無い"
        out["parent_tasks"] += [dict(t, why="分ける相手が無い") for g in groups for t in g]
        return out
    repo_name = repo.split("/")[-1]
    all_paths = [sorted({p for t in g for p in t["paths"]}) for g in groups]
    for i, g in enumerate(groups):
        level = max((t["difficulty"] for t in g), key=LEVELS.index)
        title = g[0]["title"] if len(g) == 1 else f"{g[0]['title']} ほか {len(g) - 1} 件"
        model = model_for(level, parent_model, cfg)
        deny = sorted({p for j, ps in enumerate(all_paths) if j != i for p in ps} | set(keep))
        b = {"title": f"FB {repo_name} {title}", "topic": title, "tags": ["feedback-session:auto", f"ciel:fanout:{slug(g[0].get('slug') or title)}"],
             "difficulty": level, "model": model["session"], "agent_model": model["agent"],
             "steps": sum(t["steps"] for t in g), "allow": all_paths[i], "deny": deny, "tasks": g}
        b["prompt"] = prompt(b, repo=repo, branch=branch)
        b["create_session"] = {"title": b["title"], "source_url": f"https://github.com/{repo}", "source_revision": branch,
                               "outcome_branch": branch, "model": b["model"], "permission_mode": permission_mode or "<親の permission_mode>",
                               "tags": b["tags"], "prompt": b["prompt"]}
        out["bundles"].append(b)
    return out


def prompt(b: dict, *, repo: str, branch: str) -> str:
    lines = [f"{repo} の作業を、親のセッションから分けた 1 本です（題: {b['topic']}。難易度: {b['difficulty']}）。"
             "CLAUDE.md と .claude/skills/work-style/SKILL.md があれば、読んでから始めてください。", "", "# やること"]
    for n, t in enumerate(b["tasks"], 1):
        lines.append(f"{n}. {t['title']}" + (f": {t['detail']}" if t.get("detail") else ""))
    done = [t["done"] for t in b["tasks"] if t.get("done")]
    lines += ["", "# 完成条件", "。".join(done) if done else "変えた物のテストと検査が通り、親のブランチに push されている。",
              "", "# 触ってよい場所"] + [f"- {p}" for p in b["allow"]]
    lines += ["", "# 触らない場所（親とほかの子が持つ）"] + ([f"- {p}" for p in b["deny"]] or ["- 無し"])
    lines += ["足したい記録（教訓、README の行、週報の行）と、触らない場所の直しは、最後の報告に書いてください。親が書きます。",
              "", "# ブランチと push（大事）"] + [f"- {r.format(branch=branch)}" for r in RULES_PUSH]
    lines += ["", "# やらないこと", "。".join(RULES_DONT) + "。",
              "", "# 検査と終わり", "変えたスクリプトのテストと、リポジトリの検査（あれば ./scripts/validate.sh）を通す。長いコマンドは run_in_background で、出力はファイルへ。sleep や polling をしない。",
              "週の上限に当たりそうなら、途中でもコミットして push し、報告して止まる。", REPORT.format(title=b["topic"], branch=branch)]
    return "\n".join(lines)


def missing_rules(text: str) -> list:
    return [r for r in REQUIRED if r not in text]


def plan_markdown(p: dict) -> str:
    if not p["split"]:
        out = [f"❕ 並列: 分けない（{p['reason']}）"]
    else:
        out = [f"# 並列の計画（{p['repo']}。束 {len(p['bundles'])} 本。親のブランチ {p['branch']}）", "",
               "| 題 | 難易度 | create_session の model | Agent の model | 手数 | 触ってよい場所 |", "|---|---|---|---|---|---|"]
        for b in p["bundles"]:
            out.append(f"| {b['title']} | {b['difficulty']} | {b['model'] or '親と同じ'} | {b['agent_model'] or '省く（親と同じ）'} | {b['steps']} | {'、'.join(b['allow'])} |")
        out += ["", "立て方: 束ごとに create_session（title、source_url、source_revision、outcome_branch、model、permission_mode、tags、prompt は JSON の create_session の欄）を呼び、"
                "`fanout.py record` で覚え、3〜5 分後に 1 回 get_session で見て `fanout.py status` に渡す。effort は渡さない（欄を使うのはユーザーが言ったときだけ）。"]
        for b in p["bundles"]:
            out += ["", f"## {b['title']}", f"tags: {', '.join(b['tags'])}", "", "```", b["prompt"], "```"]
    if p["parent_tasks"]:
        out += ["", "## 親が持つ作業", ""] + [f"- {t['title']}（{t['why']}。難易度: {t['difficulty']}）" for t in p["parent_tasks"]]
    return "\n".join(out) + "\n"


# ---- record と status ----

def state_file(repo: str) -> Path:
    root = Path(os.environ.get("FANOUT_STATE_DIR") or Path.home() / ".claude" / "state" / "fanout")
    return root / (re.sub(r"[^A-Za-z0-9._-]+", "__", repo).strip("_") + ".json")


def load_state(repo: str) -> dict:
    try:
        return json.loads(state_file(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"repo": repo, "sessions": []}


def save_state(repo: str, st: dict) -> None:
    f = state_file(repo)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def record(repo: str, session_id: str, title: str = "", model: str = "", tags=(), branch: str = "", archived: bool = False, now: str = "") -> dict:
    st = load_state(repo)
    row = next((s for s in st["sessions"] if s.get("id") == session_id), None)
    if row is None:
        row = {"id": session_id, "created": now or iso(utcnow())}
        st["sessions"].append(row)
    for k, v in (("title", title), ("model", model), ("tags", list(tags)), ("branch", branch)):
        if v:
            row[k] = v
    if archived:
        row["archived"] = True
    save_state(repo, st)
    return row


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s.strip().replace("Z", "+00:00")))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def find_cost(obj):
    """get_session の結果のどこかにある費用の数（キーに cost を含む数）。無ければ None。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "cost" in str(k).lower() and isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            r = find_cost(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_cost(v)
            if r is not None:
                return r
    return None


def sessions_in(data) -> dict:
    """get_session の結果（1 件、列、{"data": [...]}）を、id から中身への表にする。"""
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    out = {}
    for item in data if isinstance(data, list) else [data]:
        c = item.get("ccr", item) if isinstance(item, dict) else {}
        if isinstance(c, dict) and c.get("id"):
            out[c["id"]] = c
    return out


def classify(c: dict, seen: dict, now: dt.datetime, stall_min: int) -> dict:
    """終わった・動いている・止まった と、次の処置を返す。seen は前に見たときの値（無ければ空）。"""
    status, bucket = str(c.get("session_status", "")).upper(), str(c.get("status_bucket", "")).upper()
    meta = c.get("external_metadata") or {}
    used = (meta.get("context_usage") or {}).get("used_tokens")
    mark = [used, find_cost(c), meta.get("task_summary") or c.get("task_summary"), c.get("updated_at")]
    since = seen.get("since") if seen.get("mark") == mark else iso(now)
    quiet = max(0, int((now - parse_time(since)).total_seconds() // 60))
    summary = c.get("post_turn_summary") or {}
    note = str(summary.get("recent_action") or summary.get("needs_action") or meta.get("task_summary") or "")[:60]
    res = {"mark": mark, "since": since, "quiet_min": quiet, "note": note, "used_tokens": used}
    if "ARCHIVED" in status:
        return dict(res, state="片付け済み", action="無し")
    if "FAILED" in bucket or c.get("connection_status") == "disconnected":
        return dict(res, state="止まった", action="アーカイブして引き取る（子のコミットがあれば取り込み、残りは親が進める）")
    if "BLOCKED" in bucket:
        return dict(res, state="止まった", action="アーカイブして引き取る（子は確認待ちで止まっている。子に質問させない）")
    if any(x in bucket for x in ("REVIEW_READY", "COMPLETED")) or "IDLE" in status:
        return dict(res, state="終わった", action="取り込む（親のブランチを fetch して子のコミットと差分を読み、検査を通し、この回に archive_session。コミットが無ければ引き取る）")
    if quiet >= stall_min:
        return dict(res, state="止まった", action=f"アーカイブして引き取る（{quiet} 分、費用もトークンも動いていない）")
    return dict(res, state="動いている", action="待つ（予約は新しく重ねず、1 本を update_trigger で入れ直す）")


def status(repo: str, data, now: dt.datetime, stall_min: int = DEFAULT_STALL_MIN) -> list:
    st = load_state(repo)
    got = sessions_in(data)
    rows = []
    for s in st["sessions"]:
        if s.get("archived"):
            continue
        c = got.get(s.get("id"))
        if c is None:
            rows.append({"id": s.get("id"), "title": s.get("title", ""), "model": s.get("model", ""), "state": "見ていない",
                         "action": "get_session で取って渡す", "note": "", "quiet_min": None})
            continue
        r = classify(c, s.get("seen") or {}, now, stall_min)
        s["seen"] = {"mark": r["mark"], "since": r["since"]}
        if r["state"] == "片付け済み":
            s["archived"] = True
        rows.append({"id": s["id"], "title": s.get("title") or c.get("title", ""),
                     "model": (c.get("session_context") or {}).get("model") or s.get("model", ""), **{k: r[k] for k in ("state", "action", "note", "quiet_min")}})
    save_state(repo, st)
    return rows


def status_markdown(rows: list) -> str:
    if not rows:
        return "覚えている子のセッションは無い（fanout.py record で覚える）\n"
    out = ["| 題 | 状態 | モデル | 動きの無い分 | 最後の報告 | 次の処置 | id |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        quiet = "" if r["quiet_min"] is None else str(r["quiet_min"])
        out.append(f"| {r['title']} | {r['state']} | {r['model']} | {quiet} | {r['note']} | {r['action']} | {r['id']} |")
    count = {s: sum(1 for r in rows if r["state"] == s) for s in ("終わった", "動いている", "止まった")}
    out.append("")
    out.append("、".join(f"{k} {v}" for k, v in count.items()))
    return "\n".join(out) + "\n"


# ---- 入口 ----

def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def guess_repo() -> str:
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", git("remote", "get-url", "origin"))
    return m.group(1) if m else "<owner>/<repo>"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--task", action="append", default=[])
    p.add_argument("--tasks")
    p.add_argument("--from-reports", action="store_true")
    p.add_argument("--root", default=".")
    p.add_argument("--repo")
    p.add_argument("--branch")
    p.add_argument("--parent-model", default="")
    p.add_argument("--permission-mode", default="")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--min-steps", type=int, default=DEFAULT_MIN_STEPS)
    p.add_argument("--keep", default="")
    p.add_argument("--json")
    p.add_argument("--md")
    r = sub.add_parser("record")
    r.add_argument("--repo", required=True)
    r.add_argument("--session-id", required=True)
    r.add_argument("--title", default="")
    r.add_argument("--model", default="")
    r.add_argument("--tag", action="append", default=[])
    r.add_argument("--branch", default="")
    r.add_argument("--archived", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--repo", required=True)
    s.add_argument("--input", required=True, help="get_session の結果を写した JSON（- は標準入力）")
    s.add_argument("--stall-min", type=int, default=DEFAULT_STALL_MIN)
    s.add_argument("--now")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        tasks = [parse_task(t) for t in a.task]
        if a.tasks:
            tasks += json.loads(Path(a.tasks).read_text(encoding="utf-8"))
        if a.from_reports:
            tasks += tasks_from_reports(Path(a.root))
        if not tasks:
            print("作業が無い。--task か --tasks か --from-reports で渡す", file=sys.stderr)
            return 1
        res = plan(tasks, load_models(), repo=a.repo or guess_repo(), branch=a.branch or git("branch", "--show-current") or "<親のブランチ>",
                   parent_model=a.parent_model, permission_mode=a.permission_mode, max_bundles=a.max, min_steps=a.min_steps,
                   keep=[k for k in re.split(r"[,、]", a.keep) if k.strip()])
        bad = [b["title"] for b in res["bundles"] if missing_rules(b["prompt"])]
        if bad:
            print(f"プロンプトに決まりが欠けている: {bad}", file=sys.stderr)
            return 1
        md = plan_markdown(res)
        if a.json:
            Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if a.md:
            Path(a.md).write_text(md, encoding="utf-8")
        print(md if not (a.json or a.md) else md.split("\n## ")[0].rstrip() + (f"\n\nプロンプトの全文: {a.md or a.json}" if res["split"] else ""))
        return 0
    if a.cmd == "record":
        row = record(a.repo, a.session_id, a.title, a.model, a.tag, a.branch, a.archived)
        print(f"覚えた: {row.get('title') or row['id']}（{state_file(a.repo)}）")
        return 0
    raw = sys.stdin.read() if a.input == "-" else Path(a.input).read_text(encoding="utf-8")
    rows = status(a.repo, json.loads(raw), parse_time(a.now) if a.now else utcnow(), a.stall_min)
    print(json.dumps(rows, ensure_ascii=False, indent=1) if a.json else status_markdown(rows), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
