# 検出ルール

## 目次

- [同梱ルール一覧](#同梱ルール一覧)
- [高エントロピー検出](#高エントロピー検出)
- [誤検知の抑止](#誤検知の抑止)
- [ルールを追加する](#ルールを追加する)
- [外部ツールを使う場合](#外部ツールを使う場合)

## 同梱ルール一覧

`scripts/scan.py` の `RULES` に定義。深刻度 `high` は形式が特徴的で誤検知が少ないもの。

| ルール | 形式 | 深刻度 |
|---|---|---|
| Anthropic API key | `sk-ant-api…` / `sk-ant-admin…` | high |
| OpenAI API key | `sk-…` / `sk-proj-…` | high |
| GitHub token | `ghp_` `gho_` `ghu_` `ghs_` `ghr_` | high |
| GitHub fine-grained PAT | `github_pat_…` | high |
| AWS access key | `AKIA…` / `ASIA…` | high |
| Google API key | `AIza…` | high |
| Slack token / webhook | `xoxb-` 等 / `hooks.slack.com/services/…` | high |
| Stripe secret key | `sk_live_` / `rk_live_` | high |
| SendGrid key | `SG.…….……` | high |
| 秘密鍵ブロック | `-----BEGIN … PRIVATE KEY-----` | high |
| URL 内の認証情報 | `scheme://user:pass@host` | high |
| DB 接続文字列 | `postgres://` `mysql://` `mongodb+srv://` に認証情報 | high |
| npm token | `npm_…` | high |
| Hugging Face token | `hf_…` | high |
| JWT | `eyJ….eyJ….…` | medium |

`pk_live_`（Stripe の publishable key）のような**公開前提の値は入れていない**。
入れると誤検知が増え、報告が読まれなくなる。

## 高エントロピー検出

変数名が `secret` `token` `password` `api_key` `credential` 等を含み、
値が16文字以上・エントロピー 3.6 以上のとき `medium` で報告する。

形式の決まっていない自社トークンや DB パスワードを拾うための網。
そのぶん誤検知も出るので、必ず1件ずつ確認する。

## 誤検知の抑止

優先順位の高い順に:

1. **行末のマーカー** — その行だけ無視
   ```python
   token = "ghp_example000..."  # secret-leak-check: ignore
   ```
2. **`.secretignore`** — glob を1行ずつ。コメントは `#`
   ```
   tests/fixtures/*
   docs/examples/**
   ```
3. **既定の除外** — `.example` / `.sample` 付きファイル、ロックファイル
   （`package-lock.json` `go.sum` 等）、バイナリ、`node_modules` `vendor`
   `dist` `build` `corpus` などのディレクトリ
4. **プレースホルダ判定** — `YOUR_…` `xxx` `<token>` `${VAR}` `{{ }}`
   `changeme` `dummy` `example` 等を含む値は自動で除外
5. **役割名の判定** — `user` `password` `host` `dbname` `scheme` のような
   「形式を説明するための語」が値の位置にあれば例示とみなす。
   ドキュメントで接続文字列の書式を説明する行を拾わないため

### URL の扱い

`scheme://user:pass@host` 形式は、**認証情報の部分だけ**を見て判断する。

```
postgres://user:password@host:5432/dbname          → 例示（役割名）
postgres://admin:s3cr3tP4ss@db.internal:5432/app   → 検出  # secret-leak-check: ignore
https://deploy:8Kf2mQxZ7wLp@artifacts.example.net  → 検出  # secret-leak-check: ignore
```

ホスト名やパスまで含めてエントロピーを測ると、**長いだけの例示が実物に見える**。
実際、このスキル自身のドキュメントが誤検知の1件目だった。

上の2行に付いている `# secret-leak-check: ignore` は、この抑止機能そのものの実例。
**検出される例を文書に書くと、その文書が検出される。** 抑止マーカーはこのために要る。

抑止を足す前に、**それが本当に安全な値か確認する**。

## ルールを追加する

`scripts/scan.py` の `RULES` に `(名前, 正規表現, 深刻度)` を足す。

```python
("社内APIトークン", r"acme_tok_[A-Za-z0-9]{32}", "high"),
```

追加したら**必ず両方向で試す**。

```bash
# 検出されること
printf 'k = "acme_tok_%s"\n' "$(head -c48 /dev/urandom | base64 | tr -d '/+=' | head -c32)" > /tmp/t.py

# プレースホルダが検出されないこと
printf 'k = "acme_tok_YOUR_TOKEN_HERE"\n' >> /tmp/t.py
```

**誤検知を増やすルールは、無いルールより悪い。** 報告が読み飛ばされるようになる。

## 外部ツールを使う場合

手元にあるならそちらを優先する。検出範囲が広く、更新も速い。

```bash
gitleaks detect --source . --redact
gitleaks protect --staged --redact          # コミット前

trufflehog git file://. --only-verified     # 実際に生きている鍵だけ
trufflehog filesystem . --only-verified
```

`trufflehog --only-verified` は**鍵が実際に有効かを提供元に問い合わせて確認する**ため、
誤検知がほぼ無くなる。使える場面では最も強い。

同梱の `scan.py` はこれらが無い環境のための最低限の代替であり、
外部ツールの置き換えではない。
