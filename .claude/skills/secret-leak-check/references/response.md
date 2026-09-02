# 漏洩が確定したときの手順

**原則: 鍵を無効化するのが最優先。履歴の掃除は最後。**

commit を消しても漏れは戻らない。fork、他人のクローン、CI のログ、
GitHub のイベント API、検索エンジンのキャッシュ、スクレイパーの収集物に残る。
公開リポジトリに push された鍵は**数分で自動収集される**前提で動く。

## 1. 無効化・ローテート（今すぐ）

| 種類 | 場所 |
|---|---|
| Anthropic API key | Console → API keys → 該当キーを削除 → 新規発行 |
| OpenAI API key | Dashboard → API keys → Revoke |
| GitHub PAT | Settings → Developer settings → Tokens → Delete |
| GitHub App / OAuth | App 設定 → client secret を再生成 |
| AWS アクセスキー | IAM → 該当ユーザー → キーを無効化 → 削除 → 新規作成 |
| GCP サービスアカウント鍵 | IAM → サービスアカウント → 鍵を削除 |
| Slack token | App 管理画面 → Revoke |
| Stripe | Dashboard → API keys → Roll key |
| DB のパスワード | DB 側で変更し、接続先すべてを更新 |
| SSH 秘密鍵 | 公開鍵を各サーバから削除 → 鍵ペアを作り直す |

**削除ではなく無効化を先に。** 新しい鍵に差し替えてから古い鍵を消すと、
差し替え漏れがあっても止まらずに済む。

## 2. 使われた形跡を確認する

ローテートの後、被害の有無を見る。

- 提供元の監査ログ・使用状況（Anthropic Console、AWS CloudTrail、GitHub の
  security log、Stripe のイベント）
- 身に覚えのない時刻・IP・地域からのアクセス
- 課金の急増
- リポジトリへの想定外の push、Actions の実行

**痕跡があれば、そこから先はインシデント対応。** 影響範囲の特定を優先し、
履歴の掃除は後回しでよい。

## 3. 履歴から除去する

ここまで終えてから着手する。**これは再発防止と見た目の掃除であって、漏洩の解消ではない。**

### 未 push の場合

```bash
# 直前のコミットだけなら
git rm --cached path/to/secret
git commit --amend --no-edit
```

### push 済みの場合

`git-filter-repo` を使う（`filter-branch` は使わない）。

```bash
pip install git-filter-repo
git filter-repo --invert-paths --path path/to/secret

# 特定の文字列だけ消すなら
echo 'literal:sk-ant-xxxxx==>REMOVED' > /tmp/expr.txt
git filter-repo --replace-text /tmp/expr.txt
```

その後:

```bash
git push --force origin --all
git push --force origin --tags
```

**注意点**

- 全コミットの SHA が変わる。他のクローンは作り直しが必要
- open な PR は壊れる。先に閉じるか、書き換え後に作り直す
- **fork には効かない。** fork 側の履歴は残る。GitHub サポートに削除を依頼する
- 保護ブランチなら force push を一時的に許可する必要がある
- 実行前に必ずバックアップを取る（`git clone --mirror`）

### GitHub 側の後始末

- fork が存在するなら削除を依頼する
- キャッシュされた view（`https://github.com/<owner>/<repo>/commit/<sha>`）が
  残ることがある。サポートに連絡
- Actions のログにも出力されている可能性がある。該当 run を削除する

## 4. 再発を防ぐ

1. `.gitignore` に `.env` 等を追加（すでにあるか確認）
2. 値は環境変数か secret manager に移す
3. CI に走査を入れる

```yaml
- name: Secret scan
  run: skills/secret-leak-check/scripts/scan.py --diff origin/main..HEAD
```

4. pre-commit フックを置く

```bash
#!/bin/sh
exec skills/secret-leak-check/scripts/scan.py --diff
```

## やってはいけないこと

- **ローテートより先に履歴を消す。** 消している間も鍵は生きている
- 「private だから大丈夫」と判断する。public 化・fork・退職者のクローンで漏れる
- 報告や issue に鍵の値をそのまま書く。新しい漏洩経路になる
- force push の前にバックアップを取らない
