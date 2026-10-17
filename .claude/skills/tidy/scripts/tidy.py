#!/usr/bin/env python3
"""使い終えたリモートの枝を見つけて消す。

  tidy.py <owner/repo>            消せる枝を出すだけ（何も消さない）
  tidy.py <owner/repo> --apply    消す

消せるのは次のどちらか。どちらでもない枝は残す。
  - その枝の PR がマージ済み（squash でも可。本線に同じコミットが無くても PR の状態で判定する）
  - 本線に全部含まれている（PR を作らずに取り込んだもの、空の枝）
既定ブランチ、開いている PR の枝、PR が無く本線に無いコミットを持つ枝は消さない。

削除は API（DELETE git/refs）で行う。この環境の `git push --delete` は中継で切られることがある。
"""
import json, subprocess, sys, tempfile, urllib.parse


def gh(*args, check=True):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh api {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    repo, apply = a[0], "--apply" in a
    owner = repo.split("/")[0]
    default = json.loads(gh(f"repos/{repo}"))["default_branch"]

    branches = []
    page = 1
    while True:
        chunk = json.loads(gh(f"repos/{repo}/branches?per_page=100&page={page}"))
        branches += [b["name"] for b in chunk]
        if len(chunk) < 100:
            break
        page += 1
    branches = [b for b in branches if b != default]

    open_heads = {p["head"]["ref"] for p in json.loads(gh(f"repos/{repo}/pulls?state=open&per_page=100"))}

    # 本線に含まれるかは、コミットだけ取った clone で見る（compare API は枝名に / があると通らない）
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git", d], check=True)
        merged_local = subprocess.run(["git", "-C", d, "branch", "-r", "--merged", f"origin/{default}", "--format=%(refname:short)"],
                                      capture_output=True, text=True, check=True).stdout.split()
    contained = {b.removeprefix("origin/") for b in merged_local}

    keep, drop = [], []
    for b in branches:
        if b in open_heads:
            keep.append((b, "PR が開いている"))
            continue
        if b in contained:
            drop.append((b, "本線に含まれる"))
            continue
        q = urllib.parse.quote(f"{owner}:{b}", safe="")
        prs = json.loads(gh(f"repos/{repo}/pulls?state=closed&head={q}&per_page=5", check=False) or "[]")
        if any(p.get("merged_at") for p in prs):
            drop.append((b, f"PR #{[p['number'] for p in prs if p.get('merged_at')][0]} がマージ済み"))
        else:
            keep.append((b, "PR が無く、本線に無いコミットがある" if not prs else "PR は閉じたがマージされていない"))

    print(f"{repo}（本線 {default}）: 枝 {len(branches)} 本、消せる {len(drop)}、残す {len(keep)}")
    for b, why in drop:
        print(f"  消す  {b}  — {why}")
    for b, why in keep:
        print(f"  残す  {b}  — {why}")
    if not apply:
        if drop:
            print("消すには --apply を付ける。")
        return 0
    done, failed = 0, []
    for b, _ in drop:
        r = subprocess.run(["gh", "api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{urllib.parse.quote(b, safe='')}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            done += 1
        else:
            failed.append(b)
            if len(failed) == 1:
                print(f"  消せない: {r.stderr.strip()[:120]}")
    print(f"消した: {done}/{len(drop)}")
    if failed:
        # クラウドのセッションは、中継が削除の API も git push --delete も通さない。
        # 手元の端末で1行で消せるように、そのまま貼れる形で出す
        print("この環境からは消せない。手元の端末で次を実行するか、GitHub の画面（Branches）で消す:")
        print(f"  git push origin --delete {' '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
