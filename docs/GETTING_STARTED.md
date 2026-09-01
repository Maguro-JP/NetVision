# 自分の端末で実データを試す

サーバーは要りません。自分のPC1台で完結します。 データも全部手元に残ります。

導入テストも、担当者1人での運用も、この形が本番採用までの道です。
サーバー構築を待っていると、現場で一度も触れないまま話が終わります。
ノートPCで実 Config を入れて「使える」と分かってから、共有用のサーバーに載せます。

> 前提：まだ認証機能がありません。そのため既定では自分の端末からしか開けない設定
> （`127.0.0.1` 待ち受け）にしてあります。実機の Config には管理IP・ACL・SNMP コミュニティ名
> などが入るので、ここは緩めないでください。

## 方法A: 1台完結モード（おすすめ / Docker 不要）

Postgres も nginx も要りません。データは SQLite ファイル1個に入るので、
そのファイルをコピーすればバックアップになります。

必要なもの: Python 3.11 以上、Node.js 22 以上

```bash
git clone https://github.com/Maguro-JP/NetVision.git
cd NetVision

# 画面をビルド（初回のみ）
cd frontend && npm ci && npm run build && cd ..

# バックエンドの用意（初回のみ）
cd backend
python3 -m venv .venv && .venv/bin/pip install -e .

# 起動
.venv/bin/python -m scripts.standalone
```

ブラウザで <http://127.0.0.1:8000> を開けば使えます。止めるのは `Ctrl+C`。

これは URL です。ファイルを開くのではありません。 アドレス欄に入れてください。
将来サーバーに置いて全員で使う場合も、変わるのはこの URL のホスト名部分だけです
（`http://サーバーのIP:8000`）。今のうちからブラウザで使っておけば、そのまま移行できます。

```
データの置き場所 : /Users/you/.netvision
DB               : /Users/you/.netvision/netvision.db
画面             : http://127.0.0.1:8000
```

| オプション | 意味 |
|---|---|
| `--data-dir <パス>` | データの置き場所を変える（既定 `~/.netvision`） |
| `--port 9000` | ポートを変える |
| `--demo` | デモデータを入れて起動する（お試し用） |

2回目以降は `cd backend && .venv/bin/python -m scripts.standalone` だけです。

### バックアップ

止めてから、データディレクトリごとコピーするだけです。

```bash
cp -r ~/.netvision ~/netvision-backup-$(date +%Y%m%d)
```

## 方法B: Docker Compose（チームで共有するとき）

複数人で同じ台帳を見る段階になったらこちら。Postgres を使います。
1人で回すなら方法Aのままで十分です。

```bash
docker compose up --build
```

画面は <http://127.0.0.1:8080>。止めるのは `Ctrl+C`、片付けは `docker compose down`。

こちらもデータは手元のボリュームに入ります。バックアップは DB とファイル領域の2系統が必要です。

```bash
docker compose exec db pg_dump -U netvision netvision > netvision.sql
docker run --rm -v netvision_documents:/d -v "$PWD:/out" alpine \
  tar czf /out/documents.tar.gz -C /d .
```

---

# 実データでの始め方

## 1. Config を集める

`show version` の出力も一緒に取ってください。 running-config だけだと型番が入っておらず、
型番が分からないと EoL も脆弱性も引けません（このツールは型番を軸に全部つないでいます）。

1ファイルにまとめて構いません。パーサは running-config と show 出力の混在を読めます。

```
! ---- core-sw-01.txt ----
show version の出力
show inventory の出力     ← あればシリアルも取れる
show cdp neighbors detail ← あれば結線が自動で引ける
running-config
```

対応ベンダーは Cisco（IOS / IOS-XE / NX-OS）、Juniper Junos、Fortinet FortiOS、
NEC（IX シリーズ / QX シリーズ）、ヤマハ（RTX / NVR / FWX）、Cisco ASA / FTD、
Palo Alto PAN-OS（`set` 形式）、Arista EOS、Aruba CX（AOS-CX）です（自動判別）。
文字コードは Shift_JIS でも UTF-8 でも構いません。

## 2. まとめて取り込む

「Configの取り込み」画面で、「台帳に無い機器を Config から登録する」にチェックを入れて
ファイルをまとめて放り込みます。これで台帳が一気に立ち上がります。

実際にやるとこうなります。

```
core-sw-01.txt         新規登録   core-sw-01    Cisco    C9500-40X   17.09.04a
edge-rt-01.txt         新規登録   edge-rt-01    Juniper  mx204       21.4R3-S5.4
fw-01.txt              新規登録   fw-01         Fortinet FGT100F     7.2.8
acc-sw-osaka-01.txt    未登録     → 型番が読み取れないため登録していません。
                                    show version の出力も一緒に取り込んでください
```

型番が読めないものは黙って登録しません。 判定できない機器が静かに増えるのを防ぐためです。
該当したファイルは `show version` を足して取り込み直してください。

## 3. トポロジーを作る

「トポロジー図」で「Config から結線を作り直す」を押すと、LLDP / CDP の情報から結線が引かれます。
足りないところは手で線を引けます（自動再推定しても手で引いた線は消えません）。

拠点・VLAN・VRF で絞り込めます。 チェックを入れると、そのセグメントを持たない機器が
薄くなるので、「どこまで伸びているか」を図で確認できます。
トランクの許可 VLAN やネイティブ VLAN が両端で食い違っている結線には ⚠ が付きます。

「図をファイルに出す（SVG）」で設計書に貼れる形で書き出せます（VLAN を1つ選んでいれば、
そのセグメントだけの図になります）。現場で使う図は「シンプル表示」にしてから
書き出してください。機器名と結線だけになります。

## 3.5 VLAN の名前と用途を書く

「VLAN 台帳」で VLAN ごとの名称・用途・管理部署を登録できます。
ここに書いた内容は Config を取り込み直しても上書きされません。

VLAN が多い場合は「実機の名前で下書きを作る」を押すと、実機の VLAN 名が台帳に入ります。
そこから用途を書き足してください（既に書いてある行は触りません）。

## 4. 保守期限・EoL を入れる

ここは Config には入っていないので、別途登録が必要です。

- 既に Excel 台帳があるなら「台帳の取り込み」でそのまま読み込めます（列名は自動対応付け）
- EoL は「リファレンス管理」で型番ごとに登録すれば、同じ型番の全機器に効きます

## 5. あとは見るだけ

ダッシュボード / 更改支援 / 最適化の指摘 が埋まってきます。

---

## 試すときに知っておいてほしいこと

- このツールは機器に触りません。 Config の取り込みはファイルのアップロード、
  設定変更は生成するだけです。SSH で繋いで自動投入する作りにはしていません
- 外部通信を一切しません。 CVE も EoL もマニュアルも、ファイルを置いて取り込む方式です
- 認証がまだありません。 1人で使う分には問題ありませんが、他の人にも使わせる段階では
  必須になります
