#!/bin/bash
# 開発環境の初期設定: 開発ツールの導入と、Git のフックの有効化（基本設計 dev-environment.md §5.2・§6.2）。
# クローンした直後と、tools/versions が変わったときに実行する。
#
# 使い方:
#   scripts/setup.sh          開発ツールを導入する（フックは有効にしない）
#   scripts/setup.sh --hooks  開発ツールを導入し、フックを有効にする（check.sh の全手順が通るようになってから使う）
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

HOOKS=0
# 引数は 0 個か、--hooks の 1 個だけ（ツールの導入とフックの変更より前に確かめる）
[ $# -le 1 ] || { echo "使い方: $0 [--hooks]" >&2; exit 2; }
case "${1:-}" in
  "") ;;
  --hooks) HOOKS=1 ;;
  *) echo "使い方: $0 [--hooks]" >&2; exit 2 ;;
esac

scripts/setup-tools.sh
if [ "$HOOKS" = 1 ]; then
  git config core.hooksPath .githooks
  echo "OK: フックを有効にした（core.hooksPath = $(git config core.hooksPath)）"
else
  echo "OK: 開発ツールを導入した。フックは有効にしていない（有効にするときは --hooks を付ける）"
fi
