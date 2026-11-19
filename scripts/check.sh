#!/bin/bash
# ローカルの検査をまとめて実行する（基本設計 dev-environment.md §6、TST-CI-01〜08）。
#
# 使い方:
#   scripts/check.sh                手順 0〜10 を順に実行する（PR の作成前）
#   scripts/check.sh --steps 0-8,10 指定した手順だけを実行する（プッシュ前のフックが使う）
#   scripts/check.sh --staged       ステージしたファイルに対する手順 1〜3 と 10（コミット前のフックが使う）。
#                                   手順 1・2・10 は作業ツリーではなくステージした内容（インデックス）を検査する
#   scripts/check.sh --require-clean 未コミットの変更（追跡していないファイルを含む）があれば、手順を始めずに失敗する。
#                                   検査中に HEAD が変わった・変更が生じた場合も失敗する（プッシュ前のフックが使う）
#   scripts/check.sh --expect-head <SHA> 開始時の HEAD が指定したコミットでなければ、手順を始めずに失敗する
#                                   （フックが送信する参照と照合した HEAD を、検査の開始までに差し替えられないようにする）
#
# 1 つでも失敗したら、そこで止まる。終了コード: 0 = 全て通過 / 1 = 失敗 / 2 = 使い方の誤り
# 開発ツールは .tools/bin のものだけを使う（PATH にある別の版を使わない。BD-DEV-03）。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
# xcode-select が Command Line Tools を指していても Xcode を使う
export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}

TOOLS="$ROOT/.tools/bin"
PROJECT="MinutesMobile.xcodeproj"
SCHEME="MinutesMobile"
PACKAGE="Packages/MinutesKit"
UI_TESTS="MinutesMobileUITests"
MINIMUM_OS=27.2
PACKAGE_COVERAGE_TARGET=90
# 手順 7 がカバレッジを測った入力の記録（手順 8 が集計の前に現在の入力と照合する）。
# 未コミットの変更がなければ「commit <HEAD>」、あれば「worktree <作業ツリーのツリーのハッシュ>」を書く
COVERAGE_HEAD="$PACKAGE/.build/minutes-coverage-head"
# gitleaks の履歴の検査で git log に渡す指定。--log-opts は既定の指定（--all --full-history）を置き換えるので、
# 既定の 2 つも書く。-m でマージのコミットの差分も検査する（マージの解決で足した行を見落とさない）
GITLEAKS_LOG_OPTS="--all --full-history -m"
LAST_STEP=10
STAGED=0
REQUIRE_CLEAN=0
EXPECT_HEAD=""
STEPS=""
STAGED_TREE=""  # --staged のとき、ステージした内容を元の相対位置で書き出す一時ディレクトリ
STAGED_GITLEAKS_CONFIG=""  # --staged のとき、インデックスにある .gitleaks.toml を書き出す一時ファイル
STAGED_DOCS_TREE=""  # --staged のとき、文書の整合の検査のためにインデックス全体を書き出す一時ディレクトリ

usage() {
  echo "使い方: $0 [--steps <0-10 の番号。例: 0-8,10>] [--staged] [--require-clean] [--expect-head <SHA>]" >&2
  exit 2
}

fail() {
  echo "NG: $*" >&2
  exit 1
}

expand_steps() {
  local spec=$1 part first last number out=""
  local IFS=,
  for part in $spec; do
    case "$part" in
      *-*) first=${part%-*}; last=${part#*-} ;;
      *) first=$part; last=$part ;;
    esac
    # 番号は先頭に 0 のない正規の形だけを認める（08 などは bash の算術で 8 進数として読まれ、範囲の判定を誤るため）
    [[ $first =~ ^(0|[1-9][0-9]*)$ && $last =~ ^(0|[1-9][0-9]*)$ ]] || usage
    if [ "$first" -gt "$last" ] || [ "$last" -gt "$LAST_STEP" ]; then usage; fi
    number=$first
    while [ "$number" -le "$last" ]; do
      out="$out $number"
      number=$((number + 1))
    done
  done
  printf '%s\n' "$out"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --steps) [ $# -ge 2 ] || usage; STEPS=$(expand_steps "$2"); shift 2 ;;
    --staged) STAGED=1; shift ;;
    --require-clean) REQUIRE_CLEAN=1; shift ;;
    --expect-head) [ $# -ge 2 ] && [ -n "$2" ] || usage; EXPECT_HEAD=$2; shift 2 ;;
    *) usage ;;
  esac
done
if [ "$STAGED" = 1 ]; then
  # --staged はインデックスを検査する。作業ツリーの一致を求める --require-clean とは組み合わせない
  [ -z "$STEPS" ] && [ "$REQUIRE_CLEAN" = 0 ] && [ -z "$EXPECT_HEAD" ] || usage
  STEPS="1 2 3 10"
elif [ -z "$STEPS" ]; then
  STEPS=$(expand_steps "0-$LAST_STEP")
fi

# ステージした Swift ファイル（規則の試験用のファイルを除く）。
# 一覧は一時ファイルに書き出し、git の失敗を空の一覧として扱わない（プロセス置換では終了コードを確かめられない）
STAGED_SWIFT=()
if [ "$STAGED" = 1 ]; then
  staged_list=$(mktemp)
  if ! git diff --cached --name-only --diff-filter=ACMR -z -- '*.swift' > "$staged_list"; then
    rm -f -- "$staged_list"
    fail "ステージしたファイルの一覧を取得できない"
  fi
  while IFS= read -r -d '' file; do
    case "$file" in tools/lint-fixtures/*) continue ;; esac
    STAGED_SWIFT+=("$file")
  done < "$staged_list"
  rm -f -- "$staged_list"
fi

# ステージした Swift ファイルの内容（作業ツリーではなくインデックス）を、元の相対位置のまま一時ディレクトリへ書き出す。
# 設定（.swiftformat・各階層の .swiftlint.yml）もインデックスから同じ位置へ書き出し、入れ子の設定を作業ツリーと同じに解決させる
prepare_staged_tree() {
  STAGED_TREE=$(mktemp -d)
  {
    git ls-files -z -- ':(glob)**/.swiftformat' ':(glob)**/.swiftlint.yml' ':(glob)**/.swift-version'
    printf '%s\0' "${STAGED_SWIFT[@]}"
  } | git checkout-index -z --stdin --prefix="$STAGED_TREE/"
}

# 規則の試験に使うもの（規則・試験用のファイル・試験のスクリプト）をインデックスから同じ一時ディレクトリへ書き出す。
# 試験のスクリプトは自身の位置をリポジトリ直下とみなすので、一時ディレクトリの中のスクリプトを実行する。
# 開発ツールは作業ツリーの .tools を指す（ツールは Git の管理外）
prepare_rules_tree() {
  local directory
  [ -n "$STAGED_TREE" ] || STAGED_TREE=$(mktemp -d)
  git ls-files -z -- ':(glob)**/.swiftlint.yml' tools/lint-fixtures scripts/test-lint-rules.py \
    | git checkout-index -z -f --stdin --prefix="$STAGED_TREE/"
  # 入れ子の設定の置き忘れの検査（テストのディレクトリがあるのに設定がない）を、インデックスのディレクトリで行う。
  # 一覧は scripts/test-lint-rules.py の TEST_CODE_DIRS と同じ
  for directory in MinutesMobileTests MinutesMobileUITests Packages/MinutesKit/Tests; do
    if [ -n "$(git ls-files -- "$directory" | head -n 1)" ]; then mkdir -p "$STAGED_TREE/$directory"; fi
  done
  [ -e "$STAGED_TREE/.tools" ] || ln -s "$ROOT/.tools" "$STAGED_TREE/.tools"
}

title() {
  case "$1" in
    0) echo "開発ツールの版" ;;
    1) echo "整形（SwiftFormat）" ;;
    2) echo "静的解析（SwiftLint）" ;;
    3) echo "秘密情報の検出（gitleaks）" ;;
    4) echo "プロジェクト設定と Info.plist" ;;
    5) echo "依存" ;;
    6) echo "ビルド" ;;
    7) echo "単体・結合テスト" ;;
    8) echo "カバレッジ" ;;
    9) echo "UI テスト" ;;
    10) echo "文書の整合" ;;
  esac
}

step_0() {
  scripts/setup-tools.sh --verify
  scripts/test-checks.py versions
}

step_1() {
  if [ "$STAGED" = 1 ]; then
    if [ ${#STAGED_SWIFT[@]} -eq 0 ]; then echo "ステージした Swift ファイルはない"; return 0; fi
    (cd "$STAGED_TREE" && "$TOOLS/swiftformat" --lint --cache ignore "${STAGED_SWIFT[@]}")
  else
    "$TOOLS/swiftformat" --lint --cache ignore .
  fi
}

step_2() {
  # --config を付けない（付けると、テストコード用の入れ子の設定が無効になる）
  if [ "$STAGED" = 1 ]; then
    if [ ${#STAGED_SWIFT[@]} -gt 0 ]; then
      (cd "$STAGED_TREE" && "$TOOLS/swiftlint" lint --quiet --no-cache --force-exclude "${STAGED_SWIFT[@]}")
    else
      echo "ステージした Swift ファイルはない"
    fi
    # 規則・試験用のファイル・入れ子の設定・テストのディレクトリ（入れ子の設定の置き忘れを検査する）を変えたときだけ、
    # 規則の試験を回す。テストのディレクトリの一覧は prepare_rules_tree と scripts/test-lint-rules.py の TEST_CODE_DIRS と同じ
    if git diff --cached --quiet -- ':(glob)**/.swiftlint.yml' tools/lint-fixtures scripts/test-lint-rules.py \
      MinutesMobileTests MinutesMobileUITests Packages/MinutesKit/Tests; then return 0; fi
    # 作業ツリーではなくステージした内容で試験する。入れ子の設定の列挙（git ls-files）もインデックスに対して行う
    prepare_rules_tree
    (cd "$STAGED_TREE" && GIT_DIR="$(git -C "$ROOT" rev-parse --absolute-git-dir)" GIT_WORK_TREE="$STAGED_TREE" \
      "$STAGED_TREE/scripts/test-lint-rules.py" --staged)
    return 0
  else
    "$TOOLS/swiftlint" lint --quiet --no-cache
  fi
  scripts/test-lint-rules.py
}

step_3() {
  # --redact: 検出した値そのものを出力に残さない
  if [ "$STAGED" = 1 ]; then
    # 設定も作業ツリーではなくインデックスのものを使う（ステージしていない設定の変更で検査の範囲を変えない）。
    # 書き出しの失敗は set -e で手順の失敗になる（条件の中で実行しない）
    STAGED_GITLEAKS_CONFIG=$(mktemp)
    git cat-file blob :.gitleaks.toml > "$STAGED_GITLEAKS_CONFIG"
    "$TOOLS/gitleaks" git --no-banner --no-color --redact --config "$STAGED_GITLEAKS_CONFIG" --pre-commit --staged .
  else
    "$TOOLS/gitleaks" git --no-banner --no-color --redact --config .gitleaks.toml --log-opts="$GITLEAKS_LOG_OPTS" .
    scripts/test-checks.py gitleaks
  fi
}

step_4() {
  scripts/test-checks.py settings
  scripts/check-project-settings.py
}

step_5() {
  scripts/test-checks.py dependencies
  scripts/check-dependencies.py
}

# カバレッジを測った入力の識別子。未コミットの変更がなければ HEAD のコミット、あれば作業ツリー全体（追跡していない
# ファイルを含む。無視するファイルは除く）のツリーのハッシュ。作業ツリーの記録はコミットの合格の記録と区別する
coverage_input() {
  local index tree
  if [ -z "$(git status --porcelain --untracked-files=normal)" ]; then
    printf 'commit %s\n' "$(git rev-parse HEAD)"
    return 0
  fi
  # 一時のインデックスに作業ツリーを写してツリーを作る（本来のインデックスとステージの内容は変えない）
  index=$(mktemp)
  rm -f -- "$index"
  if ! GIT_INDEX_FILE="$index" git read-tree HEAD || ! GIT_INDEX_FILE="$index" git add -A \
    || ! tree=$(GIT_INDEX_FILE="$index" git write-tree); then
    rm -f -- "$index"
    return 1
  fi
  rm -f -- "$index"
  printf 'worktree %s\n' "$tree"
}

# テストに使うシミュレータ。機種名は決め打ちにせず、最低 OS 以上の iPhone を選ぶ（MINUTES_SIMULATOR で指定もできる）
test_destination() {
  local udid
  if [ -n "${MINUTES_SIMULATOR:-}" ]; then printf '%s\n' "$MINUTES_SIMULATOR"; return 0; fi
  # 呼び出し側は $(…) の中で実行するので set -e が効かない。取得の失敗は明示的に返し、空の id を出力しない
  udid=$(scripts/find-simulator.py --minimum-os "$MINIMUM_OS") || return 1
  [ -n "$udid" ] || return 1
  printf 'platform=iOS Simulator,id=%s\n' "$udid"
}

step_6() {
  # 警告をエラーにする指定はプロジェクト設定にあり、手順 4 が確かめる。ビルドは機種を特定しない
  xcodebuild -quiet -project "$PROJECT" -scheme "$SCHEME" -destination 'generic/platform=iOS Simulator' build
}

step_7() {
  local destination
  [ -f "$PACKAGE/Package.swift" ] || fail "$PACKAGE がない（中核ロジックの Package を作ってから実行する）"
  local measured
  rm -f -- "$COVERAGE_HEAD"
  # 測る前の入力を控え、測った後も同じ場合だけ記録する（測定中に変わった入力の結果をどちらにも帰属させない）
  measured=$(coverage_input) || fail "カバレッジを測る入力を識別できない"
  swift test --package-path "$PACKAGE" --enable-code-coverage
  [ "$(coverage_input)" = "$measured" ] || fail "カバレッジの測定中に作業ツリーが変わった。手順 7 を実行し直す"
  printf '%s\n' "$measured" > "$COVERAGE_HEAD"
  destination=$(test_destination)
  xcodebuild -quiet -project "$PROJECT" -scheme "$SCHEME" -destination "$destination" \
    -skip-testing:"$UI_TESTS" test
}

step_8() {
  local report measured current
  scripts/test-checks.py coverage
  [ -f "$PACKAGE/Package.swift" ] || fail "$PACKAGE がない（中核ロジックの Package を作ってから実行する）"
  report=$(swift test --package-path "$PACKAGE" --show-codecov-path)
  [ -f "$report" ] || fail "カバレッジの記録がない。手順 7 を先に実行する"
  # 別の入力（別のコミット・別の作業ツリーの内容）で測った記録を集計しない
  [ -f "$COVERAGE_HEAD" ] || fail "カバレッジを測った入力の記録がない。手順 7 を先に実行する"
  measured=$(cat "$COVERAGE_HEAD")
  current=$(coverage_input) || fail "カバレッジを集計する入力を識別できない"
  [ "$measured" = "$current" ] || fail "カバレッジは別の入力（${measured}）で測ったもの。手順 7 を実行し直す"
  case "$measured" in
    worktree*) echo "注意: カバレッジは未コミットの変更を含む作業ツリーで測った。コミットの記録としては扱わない" >&2 ;;
  esac
  scripts/coverage-report.py "$report" --sources "$PACKAGE/Sources" --target "$PACKAGE_COVERAGE_TARGET"
  echo "警告: アプリ側（全体 70%）の集計は、アプリのテストターゲットを追加する作業で実装する（TST-CI-07）"
}

step_9() {
  local destination
  destination=$(test_destination)
  xcodebuild -quiet -project "$PROJECT" -scheme "$SCHEME" -destination "$destination" \
    -only-testing:"$UI_TESTS" test
}

step_10() {
  if [ "$STAGED" = 1 ]; then
    # 文書・コード片の元の実装・検査スクリプトのすべてを、作業ツリーではなくインデックスの内容で照合する。
    # 文書と実装の両方をステージした一致する変更は通り、片方だけをステージした変更はコード片の不一致で止まる
    STAGED_DOCS_TREE=$(mktemp -d)
    git checkout-index -a --prefix="$STAGED_DOCS_TREE/"
    "$STAGED_DOCS_TREE/scripts/verify-docs.py" --root "$STAGED_DOCS_TREE"
  else
    scripts/test-checks.py docs
    scripts/verify-docs.py
  fi
}

# 手順の関数は条件（if・&&・||）の中で呼ばない。条件の中では set -e が効かず、
# 途中のコマンドが失敗しても最後のコマンドが成功すれば通過になってしまう。失敗の表示は EXIT の trap で行う。
current=""
on_exit() {
  local status=$?
  if [ -n "$STAGED_TREE" ]; then rm -rf -- "$STAGED_TREE"; fi
  if [ -n "$STAGED_GITLEAKS_CONFIG" ]; then rm -f -- "$STAGED_GITLEAKS_CONFIG"; fi
  if [ -n "$STAGED_DOCS_TREE" ]; then rm -rf -- "$STAGED_DOCS_TREE"; fi
  if [ "$status" -eq 0 ]; then return 0; fi
  if [ -n "$current" ]; then echo "NG: 手順 ${current}（$(title "$current")）で失敗" >&2; fi
  # 明示的に終了コードを返す（bash 3.2 は、trap の最後のコマンドの状態でエラー終了を上書きすることがある）
  if [ "$status" -eq 2 ] && [ -z "$current" ]; then exit 2; fi
  exit 1
}
trap on_exit EXIT
trap 'exit 130' INT TERM
if [ "$STAGED" = 1 ] && [ ${#STAGED_SWIFT[@]} -gt 0 ]; then prepare_staged_tree; fi
# 通常の実行は作業ツリーを検査する。開始時と終了時の HEAD と未コミットの変更を控え、
# （未追跡のファイルは status.showUntrackedFiles の設定によらず数える）
# 検査したツリーがコミットと一致する場合だけ、そのコミットの合格として表示する
START_HEAD=""
START_CHANGES=""
if [ "$STAGED" = 0 ]; then
  START_HEAD=$(git rev-parse HEAD)
  # 呼び出し元が照合した HEAD と、これから検査する HEAD が同じであること（A → A は通し、A → B は止める）
  if [ -n "$EXPECT_HEAD" ]; then
    expected=$(git rev-parse --verify -q "${EXPECT_HEAD}^{commit}") || fail "--expect-head の ${EXPECT_HEAD} がコミットを指していない"
    if [ "$START_HEAD" != "$expected" ]; then
      fail "開始時の HEAD (${START_HEAD}) が期待したコミット (${expected}) と違う。送信するコミットをチェックアウトしてから push する"
    fi
  fi
  START_CHANGES=$(git status --porcelain --untracked-files=normal)
  # 作業ツリーだけにある修正で合格させない。長い手順を回す前に止める
  if [ "$REQUIRE_CLEAN" = 1 ] && [ -n "$START_CHANGES" ]; then
    fail "未コミットの変更がある（追跡していないファイルを含む）。コミットするか退避してから再実行する（検査するのは HEAD のコミットだけ）"
  fi
fi

for step in $STEPS; do
  echo "== 手順 $step: $(title "$step")"
  started=$SECONDS
  current=$step
  "step_$step"
  current=""
  echo "-- 手順 $step: OK（$((SECONDS - started)) 秒）"
done

if [ "$STAGED" = 1 ]; then
  echo "OK: コミット前の検査を通過"
else
  end_head=$(git rev-parse HEAD)
  end_changes=$(git status --porcelain --untracked-files=normal)
  if [ -n "$START_CHANGES" ] || [ -n "$end_changes" ] || [ "$START_HEAD" != "$end_head" ]; then
    # 未コミットの変更を含む作業ツリーを検査した（または検査中にコミットが変わった）。コミットの合格にはしない
    if [ "$REQUIRE_CLEAN" = 1 ]; then
      fail "検査中に HEAD が変わった、または未コミットの変更が生じた。検査したツリーがコミットと一致しないので合格にしない"
    fi
    echo "OK: 手順$STEPS を通過（未コミットの変更あり。コミットの合格としては記録しない）"
    echo "注意: PR の説明に貼る結果は、変更をすべてコミットした状態で再実行して得る" >&2
  else
    echo "OK: 手順$STEPS を通過（コミット ${end_head}、未コミットの変更なし）"
  fi
fi
