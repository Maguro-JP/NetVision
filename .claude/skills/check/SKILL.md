---
name: check
description: CI と同じ手順を手元で流して、push できる状態かを確かめる。push・PR を Ready にする前、「試験を通して」「確認して」と言われたときに使う。GitHub Actions の無料枠を使わずに、同じものを同じ順で見る。
---

# 手元で確かめる

GitHub Actions には無料枠があり、ジョブは1分未満でも1分として数えられます。
下書きのあいだは CI が走らない設定にしてあるので、その代わりにここで通します。

## 手順

```bash
./scripts/check.sh            # 全部
./scripts/check.sh backend    # backend だけ
./scripts/check.sh frontend   # frontend だけ
```

走るのは CI と同じもの、同じ順です。

- backend: `ruff check .` → `ruff format --check .` → `pytest -q`
- frontend: `npm run typecheck` → `npm test` → `npm run build`

触ったのが片側だけなら、そちら側だけで構いません。ただし push の前には
一度は全部を通してください。型の定義は両側にまたがっています。

## 落ちたとき

- 出力を捨てないこと。`>/dev/null` で黙らせるのは、中身を見たあとだけ。
- 直したら、もう一度同じコマンドを通す。試験を書き換えて通すのは、
  試験のほうが間違っているときだけ。
- 直らないものは、そのまま「直っていない」と報告する。CI に見つけてもらわない。

## 通ったあと

報告には、通した手順と数（何件通ったか）をそのまま書きます。「確認しました」
だけでは、何を見たのかが伝わりません。
