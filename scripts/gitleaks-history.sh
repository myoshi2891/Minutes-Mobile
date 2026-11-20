#!/bin/bash
# Git の全履歴の秘密情報の検査（TST-CI-04、SEC-SUP-03）。check.sh の手順 3 と、その自己試験（test-checks.py gitleaks）が
# 同じこのコマンドを実行する（試験した指定と手順 3 の指定が食い違わないようにする）。
#
# 使い方: scripts/gitleaks-history.sh <リポジトリ> [gitleaks に渡す追加の引数…]
# 終了コード: gitleaks のもの（0 = 検出なし / 1 = 検出あり・失敗）。2 = 使い方の誤り
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
# git log に渡す指定。--log-opts は既定の指定（--all --full-history）を置き換えるので、既定の 2 つも書く。
# --diff-merges=separate でマージのコミットの差分を親ごとに検査する（マージの解決で足した行を見落とさない。-m は
# 形式が設定 log.diffMerges に左右されるので使わない）。--root で最初のコミットの差分も検査する（設定 log.showRoot=false
# でも省かない）
GITLEAKS_LOG_OPTS="--all --full-history --diff-merges=separate --root"

[ $# -ge 1 ] && [ -n "$1" ] || { echo "使い方: $0 <リポジトリ> [gitleaks の追加の引数…]" >&2; exit 2; }
repository=$1
shift
# 浅いクローンは、省いた履歴を検査しないまま通過しうる。全履歴があるときだけ検査する
# （判定できない場合も通さない。$(…) の失敗は set -e で終了する）
shallow=$(git -C "$repository" rev-parse --is-shallow-repository)
if [ "$shallow" != false ]; then
  echo "NG: ${repository} は浅いクローンで、履歴の一部がない。git fetch --unshallow で全履歴を取得してから検査し直す" >&2
  exit 1
fi
# --redact: 検出した値そのものを出力に残さない
exec "$ROOT/.tools/bin/gitleaks" git --no-banner --no-color --redact --config "$ROOT/.gitleaks.toml" \
  --log-opts="$GITLEAKS_LOG_OPTS" "$@" "$repository"
