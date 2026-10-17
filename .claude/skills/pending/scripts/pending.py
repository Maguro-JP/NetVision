#!/usr/bin/env python3
"""判断待ち（docs/reports/pending.md）を、リポジトリを横断して見る・答える。

  pending.py list <owner/repo>...                答えの無い項目を出す
  pending.py list --count <owner/repo>           答えの無い項目の数だけ出す（patrol が使う）
  pending.py answer <owner/repo> <番号|見出しの一部> <答え>
                                                答えを書き、枝 claude/ANSWER-<番号>-<日付> で PR を作ってマージする

自律開発は判断待ちに答えが入るまでその件を脇に置く。答えが pending.md に入れば、次の周が取り込む。
GitHub の画面でファイルを直してもよい。この道具は、会話から答えるためのもの。
"""
import base64, datetime, json, re, subprocess, sys, tempfile
from pathlib import Path

FILE = "docs/reports/pending.md"
ANSWER = re.compile(r"^(?P<lead>\s*-?\s*)答え[:：]\s*(?P<text>.*)$")


def gh(*args, check=True):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh api {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def fetch(repo):
    out = gh(f"repos/{repo}/contents/{FILE}", check=False)
    if not out.strip() or '"content"' not in out:
        return None
    return base64.b64decode(json.loads(out)["content"]).decode("utf-8")


def sections(text):
    """'## ' ごとに分ける。(番号, 見出し, 行の並び, 答えの行番号, 答えの文) の並び。"""
    out, cur = [], None
    for i, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "lines": [], "start": i, "ans_i": None, "ans": ""}
            out.append(cur)
            continue
        if cur is None:
            continue
        cur["lines"].append(line)
        m = ANSWER.match(line)
        if m and cur["ans_i"] is None:
            cur["ans_i"] = i
            cur["ans"] = m.group("text").strip()
    for n, s in enumerate(out, 1):
        m = re.match(r"^(\d+)\.\s*(.*)$", s["title"])
        s["num"] = int(m.group(1)) if m else n
        s["name"] = m.group(2) if m else s["title"]
    return out


def pick(lines, key):
    for l in lines:
        if key in l:
            return l.strip().lstrip("- ").strip()
    return ""


def cmd_list(repos, count_only=False):
    total = 0
    for repo in repos:
        text = fetch(repo)
        if text is None:
            if not count_only:
                print(f"{repo}: pending.md が無い")
            continue
        open_items = [s for s in sections(text) if s["ans_i"] is not None and not s["ans"]]
        total += len(open_items)
        if count_only:
            continue
        if not open_items:
            print(f"{repo}: 答え待ちなし")
            continue
        print(f"{repo}: 答え待ち {len(open_items)} 件")
        for s in open_items:
            print(f"  {s['num']}. {s['name']}")
            q = pick(s["lines"], "何を決めてほしいか")
            rec = pick(s["lines"], "推す案")
            if q:
                print(f"     {q}")
            if rec:
                print(f"     {rec}")
    if count_only:
        print(total)


def cmd_answer(repo, which, answer):
    text = fetch(repo)
    if text is None:
        raise SystemExit(f"{repo}: {FILE} が無い")
    secs = sections(text)
    hit = [s for s in secs if (which.isdigit() and s["num"] == int(which)) or (not which.isdigit() and which in s["title"])]
    if len(hit) != 1:
        raise SystemExit(f"{repo}: 項目が1つに決まらない: {[s['title'] for s in hit] or '該当なし'}")
    s = hit[0]
    if s["ans_i"] is None:
        raise SystemExit(f"{repo}: 「答え:」の行が無い項目: {s['title']}")
    lines = text.splitlines()
    m = ANSWER.match(lines[s["ans_i"]])
    today = datetime.date.today().isoformat()
    stamp = f"（{today}、利用者の返答）"
    lines[s["ans_i"]] = f"{m.group('lead')}答え: {answer}{stamp}" if not s["ans"] else f"{lines[s['ans_i']]} / {answer}{stamp}"
    new = "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    default = json.loads(gh(f"repos/{repo}"))["default_branch"]
    branch = f"claude/ANSWER-{s['num']}-{today.replace('-', '')}"
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "clone", "-q", "--depth", "1", "-b", default, f"https://github.com/{repo}.git", d], check=True)
        (Path(d) / FILE).write_text(new, encoding="utf-8")
        msg = f"判断待ち {s['num']} に答えを書く: {s['name'][:40]}"
        subprocess.run(["git", "-C", d, "checkout", "-q", "-b", branch], check=True)
        subprocess.run(["git", "-C", d, "commit", "-q", "-am", msg], check=True)
        subprocess.run(["git", "-C", d, "push", "-q", "-u", "origin", branch], check=True)
    pr = json.loads(gh(f"repos/{repo}/pulls", "-X", "POST", "-f", f"title={msg}", "-f", f"head={branch}", "-f", f"base={default}",
                       "-f", "body=docs/reports/pending.md の答えだけ。次の周で取り込まれる。"))
    gh(f"repos/{repo}/pulls/{pr['number']}/merge", "-X", "PUT", "-f", "merge_method=squash")
    print(f"{repo}: 判断待ち {s['num']} に答えを書いた。PR #{pr['number']} をマージした（{default}）")
    print("次の周で取り込まれる。すぐ知らせるなら、結びつけ先のセッションに一回きりの Routine で「判断待ちに答えが入った」と送る（SKILL.md）")


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ("list", "answer"):
        print(__doc__)
        return 2
    if a[0] == "list":
        count = "--count" in a
        repos = [x for x in a[1:] if x != "--count"]
        if not repos:
            raise SystemExit("リポジトリを指定する: owner/repo ...")
        cmd_list(repos, count)
        return 0
    if len(a) < 4:
        raise SystemExit("answer <owner/repo> <番号|見出しの一部> <答え>")
    cmd_answer(a[1], a[2], " ".join(a[3:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
