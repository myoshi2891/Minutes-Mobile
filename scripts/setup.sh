#!/bin/bash
# 開発環境の初期設定: 開発ツールの導入と、Git のフックの有効化（基本設計 dev-environment.md §5.2・§6.2）。
# クローンした直後と、tools/versions が変わったときに実行する。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

scripts/setup-tools.sh
git config core.hooksPath .githooks
echo "OK: フックを有効にした（core.hooksPath = $(git config core.hooksPath)）"
