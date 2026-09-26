# 起きた問題と対策

## IOS の `switchport access vlan dynamic` で取り込みが落ちていた（2026-09-26 JST）

- 症状: VMPS の口を含む Config を読むと ValueError で全体が読めない。
- 原因: `configparse/cisco.py` が access vlan と native vlan の末尾を無条件に `int()` していた。
- 対策: 数字のときだけ番号を持つ。dynamic は番号空の access の口として読む。
  試験は `tests/test_configparse.py` の2件。
- 見つけ方: 見本の Config（`tests/fixtures/`、`docs/topology-samples/`）の行を
  削る・切る・語を足すなどして読み手に通し、例外を集めた（各300回）。
  このとき例外が出たのは Cisco の上記2か所だけだった。
  崩れた入力で落ちる箇所を探すのに安く効くので、読み手を足したら同じ方法で見るとよい。
