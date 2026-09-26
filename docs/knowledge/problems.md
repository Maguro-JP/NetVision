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

## EXOS で切れたマスクの行により取り込みが落ちていた（2026-09-26 JST）

- 症状: `configure vlan X ipaddress 10.0.0.5 255.255.255.` のような切れた行で ValueError。
- 原因: `configparse/exos.py` のマスクの正規表現が `[\d.]+` で、何でも拾っていた。
- 対策: 4つ組か2桁までの長さだけを拾う。切れた行は読まない。
- 同じ方法を、試験に埋め込まれた 336 本の Config（25 ベンダー）にも各150回かけた。
  例外が出たのはこの1か所だけで、直したあとは 0 件。
