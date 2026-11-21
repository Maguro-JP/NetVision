# 検出の型

実績のあるツール（`tools.md`）があるときはツールが見る。ここの表は、ツールの無いリポジトリで `vuln_scan.py` と `info_scan.py` が
正規表現で補う型と、出力の ID の意味。元は Bandit（PyCQA、Apache-2.0）の B 番号と OWASP Top 10 の分類。

## バグやクラッシュ（crash_check.py、C 番号）

| ID | 何 | なぜ困る | 直し方 | 何で見る |
|---|---|---|---|---|
| C01 | 構文の誤り | 起動した瞬間に落ちる | その行を直す。書いたら compile か ruff で見る | compile、node --check、shellcheck か bash -n |
| C02 | 未定義の名前（F82x）、到達しない文（F7x）、比較の誤り（F63x） | その行に来たときに落ちる | 定義するか import する | ruff |
| C03 | 不具合になりやすい書き方（B。flake8-bugbear） | 条件で落ちる、黙って違う値になる | 規則の説明を見て直す | ruff |
| SC | シェルの不具合（shellcheck の番号） | 引用の抜けで分割される、未定義の変数が空になる | shellcheck の wiki の番号を見る | shellcheck |
| C05 | テストが落ちる | 動かないまま出る | 落ちたテストを直してから出す | test.sh、pytest、npm test |
| C06 | 起動の入口が `--help` で落ちる、20 秒で返らない | 使う人が最初に見るのがクラッシュ | 起動の入口の例外を直す。import の失敗なら依存を書く | 実行 |
| C07 | 画面のある入口、引数を受けない入口 | 機械で起動できない | 手で 1 回起動して主な操作を通す | 読む |

## 不具合や脆弱性（vuln_scan.py、V 番号。ツールの番号はそのまま出す）

| ID | 何 | なぜ危ない | 直し方 | 元 |
|---|---|---|---|---|
| V01 | eval、exec、new Function に文字列でない物 | 任意のコードが動く | ast.literal_eval、JSON.parse、許す値の表 | B307、B102、A03 |
| V02 | shell=True、os.system、child_process.exec に組み立てた文字列 | コマンドの注入 | 配列で渡す（shell=False、execFile、spawn） | B602、B605 |
| V03 | pickle、marshal の読み込み | 細工したデータでコードが動く | json。読むなら署名を検証 | B301、B302 |
| V04 | yaml.load に Loader 無し | 任意のオブジェクトの生成 | yaml.safe_load | B506 |
| V05 | TLS の検証を切る（verify=False、CERT_NONE、rejectUnauthorized:false、curl -k） | 中間者に読まれる | 検証を戻す。自前の CA なら CA の束を渡す | B501 |
| V06 | http:// で外に送る | 経路で読まれる | https:// | A02 |
| V07 | tempfile.mktemp | 作る前に横取りされる | mkstemp、NamedTemporaryFile | B306 |
| V08 | md5、sha1（鍵やパスワードの近く） | 衝突が作れる | パスワードは scrypt か bcrypt、他は sha256 | B303、B324 |
| V09 | random、Math.random で鍵やトークン | 予測できる | secrets、crypto.randomBytes | B311 |
| V10 | SQL を文字列で組み立てる | SQL の注入 | プレースホルダと引数 | B608、A03 |
| V11 | debug=True、host=0.0.0.0 | デバッガから任意のコード、全部の口が開く | 環境変数で切り替え、既定は False と 127.0.0.1 | B201、B104 |
| V12 | CORS がどこからでも | 他のサイトから叩ける | 許す origin を列挙 | A05 |
| V13 | autoescape=False、mark_safe、\| safe | XSS | エスケープを戻す | B701、B703 |
| V14 | innerHTML、document.write、dangerouslySetInnerHTML に変数 | DOM の XSS | textContent、DOM を組み立てる、DOMPurify | A03 |
| V15 | extractall に filter 無し | パスの外へ書く | filter="data"、展開先を検査 | B202 |
| V16 | chmod 777、umask(0) | 誰でも書ける | 0o755、0o644、鍵は 0o600 | B103 |
| V17 | except して pass、catch して空 | 不具合が黙って消える | 記録するか、型を絞る | B110 |
| V18 | 入力の検査を assert で | -O で消える | if で検査して例外 | B101 |
| V19 | git の --no-verify | フックの検査を飛ばす | フックを通す | 決まり |
| V20 | 鍵やパスワードの直書き | 漏れる | 環境変数か保管庫。push 済みならローテート | B105、B106 |
| D01、D02 | 依存の脆弱性（pip-audit、npm audit） | 知られた穴 | 版を上げる（vuln-watch の bump.py） | advisory |

## 情報の扱いや漏洩（info_scan.py、I 番号）

| ID | 何 | なぜ困る | 直し方 |
|---|---|---|---|
| I01 | .env、.pem、.key、id_rsa、credentials.json などを追跡している | 鍵がリポジトリに残る | git rm --cached、.gitignore。push 済みならローテートして履歴から消す |
| I02 | .sqlite、.db、.log、dump を追跡している | 中に個人情報が入りうる | 追跡から外す。要るなら中身を見てから |
| I03 | .gitignore に .env が無い | いつか入る | `.env`、`*.pem`、`*.key` を足す |
| I04 | print、log に鍵やパスワードの変数 | 記録と画面に残る | 出さない。要るなら長さか末尾 4 文字 |
| I05 | URL のクエリに鍵、URL にユーザー名とパスワード | 記録と履歴と referer に残る | ヘッダか本文。環境変数から読む |
| I06 | 鍵をファイルに書き出す | 置き場所が増える | 0o600、場所を README に書く、できれば保管庫 |
| I07 | 鍵のファイルを 6xx、7xx にする | 他のユーザーが読める | 0o600 |
| I08 | 外に送る先（情報） | 何を送るかを使う人が知らない | README の「情報の扱い」に、何を・どこへ送るか |
| I09 | 扱っている個人情報の種類（情報） | 保存先と期間が決まっていない | README の「情報の扱い」に、保存先・期間・暗号化 |
| I10 | クリップボード、計測の SDK | 黙って外に出る | README に書く。要らなければ外す |
| I11 | 鍵らしき物（detect-secrets、gitleaks） | 漏れている | 外して環境変数。push 済みならローテート |
| I12 | secret-leak-check と content-audit の要約 | 鍵の型、非公開の名前・費用 | それぞれの --tree の行を直す |

## 抑止

- 行末に `code-check: ignore`。理由を同じ行のコメントに 1 語で書く（例: `# code-check: ignore 固定の引数`）
- ファイルの先頭 4,000 文字以内に `code-check: ignore-file`（作り物の悪い例を置くファイル）
- テストのファイル（tests/、test_*.py、*.test.js）は型の検査を飛ばす。構文と出力の行は飛ばさない
