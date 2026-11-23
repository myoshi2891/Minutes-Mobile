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
# $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が付けた区切りの改行 1 つだけを外す
ROOT=$(cd "$(dirname "$0")/.." && pwd && printf x)
ROOT=${ROOT%x}
ROOT=${ROOT%$'\n'}
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
  # 外から引き継いだリポジトリの選択の変数（GIT_DIR・GIT_WORK_TREE など）を外し、git には ROOT からリポジトリを探させる
  # （変数が別のリポジトリを指していても、その設定を書き換えない）。設定の書き込みは --local に限る
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR \
    GIT_NAMESPACE GIT_PREFIX GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM
  # フックのファイルが揃っていて実行できることを、設定を書く前に確かめる（ないフックを指す設定で有効にしたと表示しない）
  for hook in .githooks/pre-commit .githooks/pre-push; do
    if [ ! -f "$hook" ] || [ ! -x "$hook" ]; then
      echo "NG: フック ${hook} がない、または実行できない。フックを有効にしない" >&2
      exit 1
    fi
  done
  # 環境変数 GIT_CONFIG は git config の読み書きを別のファイルへ向ける（設定したまま書くと、リポジトリの設定を
  # 変えずに読み直しも同じファイルから行い、誤って成功する）。書き込みと読み直しの両方で外し、書き込みは --local に限る
  # git が選ぶ作業ツリー（GIT_DIR・GIT_WORK_TREE などの変数で別のリポジトリを指しうる）が、このスクリプトのある
  # リポジトリ（ROOT）であること。通常のクローンとリンクした作業ツリーの両方で、リンクを解決した作業ツリーのパスで比べる
  # （.git の配置を仮定しない）。違えば設定を変えない
  toplevel=$(env -u GIT_CONFIG git -C "$ROOT" rev-parse --show-toplevel && printf x) || { echo "NG: git の作業ツリーを特定できない。フックを有効にしない" >&2; exit 1; }
  toplevel=${toplevel%x}
  toplevel=${toplevel%$'\n'}
  selected=$(cd -P "$toplevel" 2>/dev/null && pwd && printf x) || { echo "NG: 作業ツリー ${toplevel} を解決できない。フックを有効にしない" >&2; exit 1; }
  expected=$(cd -P "$ROOT" && pwd && printf x)  # 3 つとも末尾の改行を保って比べる（selected と expected は印を付けたまま）
  if [ "$selected" != "$expected" ]; then
    echo "NG: git が選ぶ作業ツリー（${toplevel}）がこのリポジトリ（${ROOT}）と違う。GIT_DIR・GIT_WORK_TREE などの変数を外してから実行し直す。フックを有効にしない" >&2
    exit 1
  fi
  # 設定を読む。鍵がない（終了コード 1）は空として続け、それ以外の失敗は「鍵がない」と取り違えずに報告して失敗を返す
  read_config() {
    local value status=0
    value=$(env -u GIT_CONFIG git -C "$ROOT" config "$@") || status=$?
    case $status in
      0) printf '%s\n' "$value" ;;
      1) ;;
      *)
        echo "NG: git の設定を読み取れない（git config $*。終了コード ${status}）。共有の設定を変えずに止める" >&2
        return 1
        ;;
    esac
  }
  # 共有の設定（--local）より優先される設定（作業ツリーの設定・-c・GIT_CONFIG_* の環境変数）が別の値を指していれば、
  # 共有の値を書いても有効にならない。共有の値を変える前に止める
  current=$(read_config --show-scope --get core.hooksPath) || exit 1
  case "${current%%$'\t'*}" in
    worktree | command)
      if [ "${current#*$'\t'}" != .githooks ]; then
        echo "NG: より優先される設定（${current%%$'\t'*}）の core.hooksPath が「${current#*$'\t'}」を指している。共有の設定を変えずに止める。" \
          "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
        exit 1
      fi
      ;;
  esac
  # 共有の設定のファイルが取り込む設定（include.path と、今の作業ツリーで条件の当たる includeIf.*.path。onbranch を含む）の
  # core.hooksPath は、取り込みの位置で読まれるので、書き込む値より後に読まれて上書きしうる。取り込みからだけ得られる値
  # （取り込みを読んだ一覧から、共有のファイル自身の値を除いたもの）に .githooks 以外があれば、共有の値を書かずに止める
  with_includes=$(read_config --local --includes --get-all core.hooksPath) || exit 1
  own=$(read_config --local --no-includes --get-all core.hooksPath) || exit 1
  included=$(comm -23 <(printf '%s\n' "$with_includes" | LC_ALL=C sort) <(printf '%s\n' "$own" | LC_ALL=C sort))
  while IFS= read -r value; do
    if [ -n "$value" ] && [ "$value" != .githooks ]; then
      echo "NG: 共有の設定が取り込む設定（include・includeIf）の core.hooksPath が「${value}」を指している。共有の設定を変えずに止める。" \
        "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
      exit 1
    fi
  done <<< "$included"
  env -u GIT_CONFIG git -C "$ROOT" config --local core.hooksPath .githooks
  # 実際に効く値を読み直す（より優先される設定（-c・GIT_CONFIG_* の環境変数・worktree の設定など）が
  # 別の値を指していれば、書き込んでもフックは有効にならない）。--local を付けず、worktree の設定も反映させる
  effective=$(env -u GIT_CONFIG git -C "$ROOT" config --get core.hooksPath || true)
  if [ "$effective" != .githooks ]; then
    echo "NG: core.hooksPath を .githooks に設定したが、実際に効く値は「${effective}」。" \
      "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
    exit 1
  fi
  echo "OK: フックを有効にした（core.hooksPath = ${effective}）"
else
  echo "OK: 開発ツールを導入した。フックは有効にしていない（有効にするときは --hooks を付ける）"
fi
