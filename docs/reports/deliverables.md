# 成果物

1つずつ完成させる。上から順に、状態が完成でない最初の行が「今の成果物」。
完成条件は、動かして確かめられる文で書く（コマンド名、テスト名、画面の操作と出る結果のどれかを含める）。
状態は 未着手・進行中・確認待ち・完成 の4つ。残りは、次に何をすれば完成に近づくかを1行で。

| # | 成果物 | 完成条件 | 状態 | 残り | 更新 |
|---|---|---|---|---|---|
| 1 | NetVision をアプリのウィンドウで開く（ブラウザを使わない） | `uv run pytest tests/test_launcher.py tests/test_packaging.py` が通る（ウィンドウに渡す URL がサーバーのもので、閉じるとサーバーが止まり、ブラウザを開かない）。`grep -rn webbrowser backend/scripts backend/app` が 0 件。README と docs の起動手順に URL を開く手順が無い。Windows の実機で NetVision.bat からウィンドウが開く | 完成 | 無し。Windows の実機試験（.github/workflows/windows-smoke.yml、run 40256864539、windows-latest）で、Setup.exe を入れ、スタートメニューのショートカットと NetVision.bat の両方からウィンドウが開いて概況の画面が出て、ブラウザは開かず、閉じると起動役とサーバーが止まった | 2026-10-26 |
| 2 | 機器の読み手の「未確認」を、公式資料に届くものから無くす | `grep -rn "未確認" backend/app/services/configparse --include=*.py` に出る行が、どれも「資料に届かない」理由（届かない資料の名前）を同じ行か直前の行に書いてある。`./scripts/check.sh` が通る（仮） | 完成 | 無し。grep に出る「未確認」の行は、どれも同じ行か直前の行に「資料に届かない」と届かなかった資料の名前がある（2026-10-31 JST に確かめた）。./scripts/check.sh も通った | 2026-10-31 |
| 3 | Junos の論理システムを、論理システムごとの機器として取り込む | 論理システムを2つ持つ Junos の Config を1ファイル取り込むと、台帳に本体と論理システム2つの計3台が載り、それぞれの口と経路が出る試験が `uv run pytest` で通る（仮） | 進行中 | decisions.md の「決めていなかったこと」3つにデフォルトを決めた。次は `configs.ingest_many` で `juniper.split_logical_systems` を呼び、論理システムごとの塊を足す。完成条件の試験を先に書く | 2026-10-31 |
