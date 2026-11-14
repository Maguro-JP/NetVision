#!/usr/bin/env python3
"""README に置くスクリーンショットを撮る。URL か HTML のファイルを開き、倍率 2 の PNG で保存する。

使い方: shot.py <URL か HTML のファイル> <out.png> [--width 1280] [--height 800] [--full]
Playwright（python の playwright と chromium）が無ければ、撮り方を 1 行出して終了コード 2。
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--full", action="store_true", help="ページ全体を撮る")
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright が無い。pip install playwright && playwright install chromium のあとに撮るか、アプリの画面を手で撮って docs/images/ に置く")
        return 2
    target = a.target
    p = Path(target)
    if p.exists():
        target = p.resolve().as_uri()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=2).new_page()
        page.goto(target, wait_until="networkidle", timeout=30000)
        page.screenshot(path=str(out), full_page=a.full)
        browser.close()
    print(f"スクリーンショット: {out}（{a.width}x{a.height}、倍率 2）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
