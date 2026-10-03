#!/usr/bin/env bash
# 毎回の入力で「この場面ならこのスキル」を1行差し込む。
# スキルは description が合えば読まれるはずだが、軽い作業だと素通りする。
# 場面とスキル名を毎回目の前に置いて、判断に頼る部分を減らす。
cat <<'NUDGE'
場面とスキル: PR を作った・push した→solo-pr-flow（自分のリポジトリなら CI を待ってマージまで進める）／「できました」と言う前→verify-before-done（出力を貼る。確かめていないことを書く）／CI が落ちた→ci-triage／公開・マージ前→secret-leak-check／新しいリポジトリ→repo-bootstrap／定期実行・Routine→scheduler（リポジトリを渡したセッションに結びつける）／ずっと開発→long-dev
NUDGE
