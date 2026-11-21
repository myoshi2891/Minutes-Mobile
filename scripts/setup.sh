#!/bin/bash
# 開発環境の初期設定: 開発ツールの導入と、Git のフックの有効化（基本設計 dev-environment.md §5.2・§6.2）。
# クローンした直後と、tools/versions が変わったときに実行する。
#
# 使い方:
#   scripts/setup.sh          開発ツールを導入する（フックは有効にしない）
#   scripts/setup.sh --hooks  開発ツールを導入し、フックを有効にする（check.sh の全手順が通るようになってから使う）
set -euo pipefail

# CDPATH が設定されていると、相対パスの cd が別の場所へ移り、移った先を標準出力に書いて ROOT に混ぜる。外してから解決する
unset CDPATH
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

HOOKS=0
# 引数は 0 個か、--hooks の 1 個だけ（ツールの導入とフックの変更より前に確かめる）。
# 引数が 1 個あれば空文字列でも引数として扱い、--hooks との完全一致を求める
usage() { echo "使い方: $0 [--hooks]" >&2; exit 2; }
[ $# -le 1 ] || usage
if [ $# -eq 1 ]; then
  [ "$1" = --hooks ] || usage
  HOOKS=1
fi

"$ROOT/scripts/setup-tools.sh"
if [ "$HOOKS" = 1 ]; then
  # フックのファイルが揃っていて実行できることを、設定を書く前に確かめる（ないフックを指す設定で有効にしたと表示しない）
  for hook in .githooks/pre-commit .githooks/pre-push; do
    if [ ! -f "$hook" ] || [ ! -x "$hook" ]; then
      echo "NG: フック ${hook} がない、または実行できない。フックを有効にしない" >&2
      exit 1
    fi
  done
  # 環境変数 GIT_CONFIG は git config の読み書きを別のファイルへ向ける（設定したまま書くと、リポジトリの設定を
  # 変えずに読み直しも同じファイルから行い、誤って成功する）。書き込みと読み直しの両方で外し、書き込みは --local に限る
  env -u GIT_CONFIG git config --local core.hooksPath .githooks
  # 実際に効く値を読み直す（より優先される設定（-c・GIT_CONFIG_* の環境変数・worktree の設定など）が
  # 別の値を指していれば、書き込んでもフックは有効にならない）。--local を付けず、worktree の設定も反映させる
  effective=$(env -u GIT_CONFIG git config --get core.hooksPath || true)
  if [ "$effective" != .githooks ]; then
    echo "NG: core.hooksPath を .githooks に設定したが、実際に効く値は「${effective}」。" \
      "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
    exit 1
  fi
  echo "OK: フックを有効にした（core.hooksPath = ${effective}）"
else
  echo "OK: 開発ツールを導入した。フックは有効にしていない（有効にするときは --hooks を付ける）"
fi
