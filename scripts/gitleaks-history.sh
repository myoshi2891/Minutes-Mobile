#!/bin/bash
# Git の全履歴の秘密情報の検査（TST-CI-04、SEC-SUP-03）。check.sh の手順 3 と、その自己試験（test-checks.py gitleaks）が
# 同じこのコマンドを実行する（試験した指定と手順 3 の指定が食い違わないようにする）。
#
# 使い方: scripts/gitleaks-history.sh <リポジトリ> [gitleaks に渡す追加の引数…]
# 終了コード: gitleaks のもの（0 = 検出なし / 1 = 検出あり・失敗）。2 = 使い方の誤り
set -euo pipefail

# CDPATH が設定されていると、相対パスの cd が別の場所へ移り、移った先を標準出力に書いて ROOT に混ぜる。外してから解決する
unset CDPATH
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# git log に渡す指定。--log-opts は既定の指定（--all --full-history）を置き換えるので、既定の 2 つも書く。
# --diff-merges=separate でマージのコミットの差分を親ごとに検査する（マージの解決で足した行を見落とさない。-m は
# 形式が設定 log.diffMerges に左右されるので使わない）。--root で最初のコミットの差分も検査する（設定 log.showRoot=false
# でも省かない）。--no-textconv で .gitattributes の textconv を使わずに元の内容の差分を検査する（変換で鍵を隠させない）。
# --text で .gitattributes の binary（-diff）の属性のファイルも本文の差分として出す（「Binary files differ」で鍵を隠させない）
GITLEAKS_LOG_OPTS="--all --full-history --diff-merges=separate --root --no-textconv --text"
# 抑止の仕組みは使わない（どちらも承認の記録なしに検出を隠せるため）。--gitleaks-ignore-path に空のファイル（/dev/null）を
# 渡して実行時の場所の .gitleaksignore を読ませず、--ignore-gitleaks-allow で行の「gitleaks:allow」のコメントも無視する。
# gitleaks は検査するリポジトリの直下の .gitleaksignore を指定によらず読む（8.30.1 で確かめた）ので、それがあれば検査せずに失敗する
GITLEAKS_SUPPRESSION=(--gitleaks-ignore-path /dev/null --ignore-gitleaks-allow)

[ $# -ge 1 ] && [ -n "$1" ] || { echo "使い方: $0 <リポジトリ> [gitleaks の追加の引数…]" >&2; exit 2; }
repository=$1
shift
# 外から引き継いだリポジトリを指す変数（フックが設定する GIT_DIR など）を外す。残すと、浅いクローンの判定と gitleaks の
# git log が引数のリポジトリではなく、その変数が指すリポジトリを読む
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR \
  GIT_NAMESPACE GIT_PREFIX GIT_SHALLOW_FILE GIT_GRAFT_FILE GIT_REPLACE_REF_BASE GIT_NO_REPLACE_OBJECTS
# 置き換えのオブジェクト（git replace の refs/replace/）を使わず、元のオブジェクトの履歴を検査する（置き換えで鍵を含む
# blob・コミットを別の内容に見せて隠させない）。外した後に明示的に設定する
export GIT_NO_REPLACE_OBJECTS=1
# 浅いクローンは、省いた履歴を検査しないまま通過しうる。全履歴があるときだけ検査する
# （判定できない場合も通さない。$(…) の失敗は set -e で終了する）
shallow=$(git -C "$repository" rev-parse --is-shallow-repository)
if [ "$shallow" != false ]; then
  echo "NG: ${repository} は浅いクローンで、履歴の一部がない。git fetch --unshallow で全履歴を取得してから検査し直す" >&2
  exit 1
fi
if [ -e "$repository/.gitleaksignore" ] || [ -L "$repository/.gitleaksignore" ]; then
  echo "NG: ${repository}/.gitleaksignore がある。検出の抑止の記録は使わない（gitleaks が指定によらず読むため）。削除してから検査し直す" >&2
  exit 1
fi
# --redact: 検出した値そのものを出力に残さない
exec "$ROOT/.tools/bin/gitleaks" git --no-banner --no-color --redact --config "$ROOT/.gitleaks.toml" \
  "${GITLEAKS_SUPPRESSION[@]}" --log-opts="$GITLEAKS_LOG_OPTS" "$@" "$repository"
