# 機械で拾う形

`scripts/fake_scan.py` が拾う形の表。ID は出力の先頭に出る。重さは 2 つで、重大は直すか返答に 1 行書くまで出さない物、注意は数を見て減らす物。
Python は ast で、JavaScript と TypeScript は文字列とコメントを落としてから形で見る。文字列の中の語（正規表現、説明の文）は拾わない。

## A-1 置き場のままの実装

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| P01 | 中身が `pass` だけ・`...` だけ・docstring だけの関数。JS は中身の無い関数 | 注意 | abstract、overload、Protocol や ABC の中、`__init__` などの特別な名前、`noop` |
| P02 | `raise NotImplementedError` だけの関数。JS は not implemented を投げるだけの関数 | 注意 | abstract の中 |
| P03 | 引数を使わずに、固定の値（数、文字列、固定の配列や辞書）を返すだけの関数 | 注意 | 引数の無い関数、property、テストのファイル |
| P04 | 名前が check、validate、verify、is_、has_ で始まるのに、中身が無いか、いつも True を返す関数（偽の検査） | 重大 | 中身のある検査 |
| P05 | コメントの中の置き場の語。`TODO`、`FIXME`、`XXX`、`HACK`（大文字のマーク。小文字は `todo:` の形だけ）、仮実装、仮の値、（仮）、ダミー、dummy、placeholder、stub、とりあえず、ひとまず、未実装、for now、in a real implementation、本番では、mock data、サンプルデータ | 注意 | テストのファイル、文字列の中 |
| P06 | 省略の置き場。`... rest of the code`、`... existing code`、（中略）、以下省略、残りは同じ、のコメントで本物のコードを置き換えた物 | 重大 | テストのファイル |

## A-2 握りつぶし

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| S01 | `except:`、`except Exception:`、`except BaseException:` の中身が `pass` か `...` だけ。`suppress(Exception)` | 重大 | 中身のある扱い（記録、投げ直し） |
| S02 | 型を絞った except（`except OSError:` など）の中身が `pass` だけで、理由のコメントが無い | 注意 | 同じ行か中身の行に理由のコメントがある物 |
| S03 | 中身の無い `catch`、`.catch(() => {})`、`.catch(() => null)` | 重大 | 中か同じ行に、コメントで理由が書いてある物。失敗を扱う `.catch` |
| S04 | 新しく足した `\|\| true`（シェル、ワークフロー、Makefile、Dockerfile、package.json） | 注意 | 前からある行（差分のときだけ数える） |
| S05 | 新しく足した `2>/dev/null` | 注意 | 前からある行（差分のときだけ数える） |

## A-3 偽のデータと振り

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| D01 | sleep して（待ってから）固定の値を返すだけの関数 | 注意 | 待ったあとに本物の処理を呼ぶ関数 |
| D02 | 条件なしに「成功しました」「保存しました」を出して、固定の値を返すだけの関数 | 注意 | 結果を見てから出す関数 |
| D03 | テストのときだけ別の道を通る分岐。`PYTEST_CURRENT_TEST`、`"pytest" in sys.modules`、`process.env.JEST_WORKER_ID`、`process.env.VITEST` | 重大 | テストのファイル、文字列の中 |
| D04 | `NODE_ENV === 'test'` の分岐 | 注意 | テストのファイル |

## A-4 テストの見せかけ

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| T01 | assert（expect）の無いテスト | 注意 | `pytest.raises`、`assert_called` など、確かめる呼び出しのあるテスト |
| T02 | 中身の無いテスト。いつも通る assert（`assert True`、`assertTrue(True)`、`x == x`、`expect(true).toBe(true)`） | 重大 | |
| T03 | 条件なしの `skip`・`xfail`・`only`・`todo`。`@pytest.mark.skip`、`@unittest.skip`、条件の外の `self.skipTest()`、`pytestmark` の skip、`it.skip`、`it.only`、`xit`、`fit`、`it.todo` | 重大 | `skipif`、`skipIf`、`skipUnless`、`if` の中の `skipTest`、`importorskip`、条件を渡す `test.skip(条件, 理由)` |
| T04 | テストのファイルを消した。テストの関数を消した | 重大 | 別のファイルに移した物（消した行の半分以上が別の所に足されている）、名前を変えただけの物 |
| T05 | assert を消した（2 行以上の減り、または全部） | 重大 | 同じ数の assert に置き換えた物 |
| T06 | テストの対象そのものをモックで置き換えた疑い。`test_<名前>` の中の `patch("….<名前>")`、`<対象>.test.ts` の中の `jest.mock("./<対象>")` | 注意 | 対象の外の物（通信、時計、ファイル）の置き換え |
| T07 | スナップショットの書き換え（`__snapshots__`、`.snap`） | 注意 | |
| T08 | 期待値の書き換え（同じ形の assert で、値だけが変わった） | 注意 | |

## A-5 配線されていない物

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| U01 | 定義しただけで、リポジトリのどこからも呼ばれていない関数（いちばん外側の関数だけ） | 注意 | デコレータの付いた関数、`__all__` にある名前、`main` などの入口の名前、テスト、メソッド |
| U02 | ハンドラの無いボタン（`<button>` に onClick も type="submit" も無い） | 注意 | form の中のボタン、Trigger や Link に包まれたボタン、`{...props}` を受けるボタン、HTML で id や class のあるボタン |
| U03 | 中身の無いハンドラ（`onClick={() => {}}`、`onclick=""`） | 注意 | |
| U04 | 新しいファイルが、ほかのどのファイルからも読まれていない | 注意 | 入口のファイル（main、index、app、cli、設定）、実行できるスクリプト、テスト、ルートになる場所（pages、app、routes） |
| U05 | 登録されていないルートの束（Blueprint、APIRouter、express.Router） | 注意 | 同じファイルか別のファイルで登録している物 |
| U06 | 足した設定のキー（`config*.json`、`settings*.yml`、`.env.example` など）を、コードのどこも読んでいない | 注意 | 前からあるキー、ハイフンのあるキー、`.claude/` の中の設定（差分のときだけ数える） |

## 検査を弱める形

| ID | 形 | 重さ | 拾わない物 |
|---|---|---|---|
| W01 | `git commit --no-verify`、`git push --no-verify` | 重大 | |
| W02 | ワークフローの `continue-on-error: true` | 重大 | |
| W03 | 検査をまるごと黙らせるコメント。`# type: ignore`、`# noqa`、`# ruff: noqa`、`# pylint: disable=all`、`@ts-ignore`、`@ts-nocheck`、規則の無い `eslint-disable` | 重大 | 規則を名指しした物（W13） |
| W13 | 規則を名指しして黙らせるコメント。`# noqa: E402`、`# type: ignore[attr-defined]`、`eslint-disable-next-line no-console`、`@ts-expect-error`、`# pragma: no cover`、`# nosec` | 注意 | 前からある行（差分のときだけ数える） |
| W04 | しきい値を緩めた（`fail_under`、`coverageThreshold`、`--max-warnings` の数） | 重大 | |
| W05 | フックの登録を消した（settings.json の command、pre-commit の id）。CI やフックのファイルを消した。ワークフローから検査の段を消した | 重大 | 並べ替えただけの登録 |
| W06 | 検査の失敗を握りつぶす（`pytest \|\| true`、`npm test \|\| echo`、`; exit 0`）。何も確かめない台本（`"test": "echo …"`） | 重大 | 検査ではないコマンドの `\|\| true`（S04） |
| W07 | 型の検査を緩める設定（tsconfig の `"strict": false`、mypy の `ignore_errors = true`） | 重大 | 前からある行 |
| W08 | テストを設定で外す（`--ignore`、`--deselect`、`-k "not …"` を addopts に足す） | 重大 | 前からある行 |

## 抑止

- 行の末尾に `no-fake: ignore（理由）`。関数や except の形は、始めの行から終わりの行のどこにあってもよい
- ファイルの先頭 4,000 文字以内に `no-fake: ignore-file（理由）`（悪い見本を置くファイル）
- 消した物は行が残らないので、コミットメッセージに `no-fake: ignore <パスか名前>（理由）` を 1 行書く
- 見ない場所: `node_modules`、`dist`、`build`、`vendor`、`fixtures`、`testdata`、`__mocks__`、`__snapshots__`、このスキルと code-check の `references/`
- 配った先のリポジトリの `.claude/skills/` の中（スキルの元のリポジトリのコピー。元かどうかは、リポジトリの根の scripts に、スキルを検査する `validate.sh` があるかで見分ける）

## 偽の当たりを減らすための決まり

- 差分では、足した行だけを見る。消した行にも同じ文がある行は、動かしただけなので足した行に数えない
- `git merge-base` で分かれ目を取り、base が進んだ分を自分の変更に数えない
- 規則を名指しした抑止、型を絞った except、条件つきの skip は、理由が読めるので重大にしない
- テストのファイルでは、置き場の語と固定の値を見ない（stub や dummy は、テストでは普通の語）
- 新しい形を足すときは、テストに「拾う見本」と「拾わない見本」を組で足し、このリポジトリ全体と実際の会話の記録に当てて、当たった数と断片を見る
