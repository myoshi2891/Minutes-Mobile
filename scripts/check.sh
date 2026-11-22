#!/bin/bash
# ローカルの検査をまとめて実行する（基本設計 dev-environment.md §6、TST-CI-01〜08）。
#
# 使い方:
#   scripts/check.sh                手順 0〜10 を順に実行する（PR の作成前）
#   scripts/check.sh --steps 0-8,10 指定した手順だけを実行する（プッシュ前のフックが使う）
#   scripts/check.sh --staged       ステージしたファイルに対する手順 1〜3 と 10（コミット前のフックが使う）。
#                                   手順 1・2・10 は作業ツリーではなくステージした内容（インデックス）を検査する
#   scripts/check.sh --require-clean 未コミットの変更（追跡していないファイルを含む）があれば、手順を始めずに失敗する。
#                                   開始時のコミットを専用の作業ツリー（リポジトリの外の一時ディレクトリ）に展開し、
#                                   そこで検査する（元の作業ツリーを検査中に書き換えて戻しても、検査する内容は変わらない）。
#                                   コミットの合格（PR の説明に貼る結果）はこの指定で得る。プッシュ前のフックも使う
#   scripts/check.sh --expect-head <SHA> 開始時の HEAD が指定したコミットでなければ、手順を始めずに失敗する
#                                   （フックが送信する参照と照合した HEAD を、検査の開始までに差し替えられないようにする）
#
# 1 つでも失敗したら、そこで止まる。終了コード: 0 = 全て通過 / 1 = 失敗 / 2 = 使い方の誤り
# 開発ツールは .tools/bin のものだけを使う（PATH にある別の版を使わない。BD-DEV-03）。
set -euo pipefail

# CDPATH が設定されていると、相対パスの cd が別の場所へ移り、移った先を標準出力に書いて ROOT に混ぜる。外してから解決する
unset CDPATH
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# 部分的なコミット（git commit <パス>）のフックでは、Git が一時のインデックスを GIT_INDEX_FILE で渡す。相対パスは
# 実行時の作業ディレクトリから解決されるので、ディレクトリを移る前に絶対パスにする（--staged の規則の試験は別の
# ディレクトリで git を実行するので、相対パスのままでは別のインデックスを読む）
if [ -n "${GIT_INDEX_FILE:-}" ]; then
  case "$GIT_INDEX_FILE" in /*) ;; *) GIT_INDEX_FILE="$PWD/$GIT_INDEX_FILE" ;; esac
  export GIT_INDEX_FILE
fi
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
# 1 行目は入力（未コミットの変更がなければ「commit <HEAD>」、あれば「worktree <作業ツリーのツリーのハッシュ>」）、
# 2 行目は測った結果のカバレッジの JSON（COVERAGE_JSON）の SHA-256（手順 7 と 8 の間に JSON が書き換えられていないこと）
COVERAGE_HEAD="$PACKAGE/.build/minutes-coverage-head"
# 手順 7 がその測定の JSON を写して置く場所（手順 8 はこれだけを集計する。swift test の共有の出力先は読まない）
COVERAGE_JSON="$PACKAGE/.build/minutes-coverage.json"
# 手順 7 が測る入力の写し。記録する入力の識別子が指すツリーから Packages/ を展開し、swift test はこの写しに対して実行する
# （測定中に作業ツリーが変わっても、測った内容と記録する識別子が食い違わない）。手順 8 はこの写しの Sources を数える。
# 置き場所はリポジトリ直下の .build（整形・静的解析・入れ子の設定の列挙・Git がすべて除外する。Package の下の .build は
# 静的解析などの除外が直下だけなので、写しの Swift ファイルと入れ子の設定が検査の対象になってしまう）
COVERAGE_INPUT=".build/minutes-coverage-input"
# 手順 7・8 の排他（同じ作業ツリーで並行に走る測定が、互いの写し・記録を置き換えて別の入力の結果を帰属させない）。
# 手順 7 は写しの削除から記録の公開まで、手順 8 は記録の読み取りから集計までを、このロックを持って行う
COVERAGE_LOCK=".build/minutes-coverage.lock"
COVERAGE_LOCK_HELD=""
LAST_STEP=10
STAGED=0
REQUIRE_CLEAN=0
EXPECT_HEAD=""
STEPS=""
STEPS_GIVEN=0  # --steps を指定したか（指定しなかったときだけ全手順を実行する）
STAGED_TREE=""  # --staged のとき、ステージした内容を元の相対位置で書き出す一時ディレクトリ
STAGED_GITLEAKS_CONFIG=""  # --staged のとき、インデックスにある .gitleaks.toml を書き出す一時ファイル
STAGED_DOCS_TREE=""  # --staged のとき、文書の整合の検査のためにインデックス全体を書き出す一時ディレクトリ
STAGED_SECRETS_TREE=""  # --staged のとき、秘密情報の検査のためにステージした blob をそのまま書き出す一時ディレクトリ
STAGED_INDEX_TREE=""  # --staged のとき、開始時のインデックスのツリー（終了時に同じであることを確かめる）
STAGED_TOOLS=""  # --staged のとき、開発ツールの写しと、照合に使うステージした台帳を置く一時ディレクトリ
STAGED_INDEX_COPY=""  # --staged のとき、開始時のインデックスのツリーから作った専用のインデックス（検査はこれだけを読む）
ORIGINAL_INDEX=""  # --staged のとき、本来のインデックスのファイル（終了時に開始時のツリーのままであることを確かめる）
ISOLATED_BASE=""  # --require-clean のとき、開始時のコミットを展開する専用の作業ツリーを置く一時ディレクトリ
ISOLATED_TREE=""  # その専用の作業ツリー（git worktree で登録する）
COVERAGE_SCRATCH=""  # 手順 7 の測定ごとの swift test の出力先（一時ディレクトリ）
COVERAGE_COPY=""  # 手順 8 が照合と集計に使う、カバレッジの記録の写し（一時ファイル）
TOOLS_LOCK=""  # --require-clean のとき、元の .tools の導入のロック（写している間だけ持つ）

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
    # 整数の比較の前に、桁数で上限を確かめる（桁の多い値は bash の整数の範囲を超え、比較が失敗して範囲を読み飛ばすため）
    [ ${#first} -le ${#LAST_STEP} ] && [ ${#last} -le ${#LAST_STEP} ] || usage
    if [ "$first" -gt "$last" ] || [ "$last" -gt "$LAST_STEP" ]; then usage; fi
    number=$first
    while [ "$number" -le "$last" ]; do
      out="$out $number"
      number=$((number + 1))
    done
  done
  printf '%s\n' "$out"
}

ARGUMENTS=("$@")  # --require-clean のとき、専用の作業ツリーの check.sh に同じ引数を渡す
while [ $# -gt 0 ]; do
  case "$1" in
    # 明示した --steps が空（"" や区切りだけ）なら使い方の誤りにする（空のまま下へ進むと全手順を実行してしまう）
    --steps) [ $# -ge 2 ] || usage; STEPS=$(expand_steps "$2"); [ -n "${STEPS// /}" ] || usage; STEPS_GIVEN=1; shift 2 ;;
    --staged) STAGED=1; shift ;;
    --require-clean) REQUIRE_CLEAN=1; shift ;;
    --expect-head) [ $# -ge 2 ] && [ -n "$2" ] || usage; EXPECT_HEAD=$2; shift 2 ;;
    *) usage ;;
  esac
done
if [ "$STAGED" = 1 ]; then
  # --staged はインデックスを検査する。作業ツリーの一致を求める --require-clean とは組み合わせない
  [ "$STEPS_GIVEN" = 0 ] && [ "$REQUIRE_CLEAN" = 0 ] && [ -z "$EXPECT_HEAD" ] || usage
  STEPS="1 2 3 10"
elif [ "$STEPS_GIVEN" = 0 ]; then
  STEPS=$(expand_steps "0-$LAST_STEP")
fi

# ステージした Swift ファイル（規則の試験用のファイルを除く）。
# 一覧は一時ファイルに書き出し、git の失敗を空の一覧として扱わない（プロセス置換では終了コードを確かめられない）
STAGED_SWIFT=()
if [ "$STAGED" = 1 ]; then
  # 置き換えのオブジェクト（git replace の refs/replace/）を使わず、ステージした blob そのものを書き出して検査する
  # （子のプロセスにも引き継ぐ。置き換えがなければ結果は変わらない）
  export GIT_NO_REPLACE_OBJECTS=1
  # 書き出しの前にインデックスのツリーを控え、以後の検査はそのツリーだけから作った専用のインデックスを読む
  # （GIT_INDEX_FILE で指して子のプロセスにも引き継ぐ。検査の途中に本来のインデックスが変わっても、検査する内容は
  # 控えたツリーのまま）。合格を表示する前に、本来のインデックスと専用のインデックスの両方が控えたツリーのままで
  # あることを確かめる（変わっていれば、コミットされる内容が検査した内容と違いうるので合格にしない）。
  # 未解決の衝突があればツリーを作れずに失敗する
  STAGED_INDEX_TREE=$(git write-tree) || fail "ステージした内容のツリーを作れない（未解決の衝突など）"
  ORIGINAL_INDEX=$(git rev-parse --path-format=absolute --git-path index) || fail "インデックスの場所を特定できない"
  STAGED_INDEX_COPY=$(mktemp)
  rm -f -- "$STAGED_INDEX_COPY"
  GIT_INDEX_FILE="$STAGED_INDEX_COPY" git read-tree "$STAGED_INDEX_TREE" || fail "控えたツリーから専用のインデックスを作れない"
  export GIT_INDEX_FILE="$STAGED_INDEX_COPY"
  staged_list=$(mktemp)
  # 種類の変更（T。通常のファイルとシンボリックリンクの入れ替えなど）も含める（下でインデックスのモードを確かめる）
  if ! git diff --cached --name-only --diff-filter=ACMRT -z -- '*.swift' > "$staged_list"; then
    rm -f -- "$staged_list"
    fail "ステージしたファイルの一覧を取得できない"
  fi
  while IFS= read -r -d '' file; do
    case "$file" in tools/lint-fixtures/*) continue ;; esac
    STAGED_SWIFT+=("$file")
  done < "$staged_list"
  rm -f -- "$staged_list"
  # 整形・静的解析に渡すのは、インデックスで通常のファイル（モード 100644・100755）のものだけ。シンボリックリンク（120000）
  # やサブモジュール（160000）を黙って除かず、失敗にする（追加・変更した Swift のファイルと同じ確認を、種類の変更にも行う）
  if [ ${#STAGED_SWIFT[@]} -gt 0 ]; then
    staged_modes=$(mktemp)
    if ! git --literal-pathspecs ls-files -s -z -- "${STAGED_SWIFT[@]}" > "$staged_modes"; then
      rm -f -- "$staged_modes"
      fail "ステージしたファイルのモードを取得できない"
    fi
    while IFS= read -r -d '' entry; do
      case "${entry%% *}" in
        100644 | 100755) ;;
        *)
          rm -f -- "$staged_modes"
          fail "ステージした ${entry#*$'\t'} はインデックスで通常のファイルでない（モード ${entry%% *}）"
          ;;
      esac
    done < "$staged_modes"
    rm -f -- "$staged_modes"
  fi
fi

# ステージした Swift ファイルの内容（作業ツリーではなくインデックス）を、元の相対位置のまま一時ディレクトリへ書き出す。
# 設定（.swiftformat・各階層の .swiftlint.yml）もインデックスから同じ位置へ書き出し、入れ子の設定を作業ツリーと同じに解決させる。
# 書き出すのはインデックスの blob そのもの（git cat-file blob。prepare_staged_secrets_tree と同じ）。checkout-index を通すと
# .gitattributes の smudge のフィルタ・改行の変換が掛かり、整形・静的解析がステージした内容と違うものを検査しうる
prepare_staged_tree() {
  local entries entry mode object path
  STAGED_TREE=$(mktemp -d)
  entries=$(mktemp)
  # 一覧の取得の失敗を空の一覧として扱わない
  if ! git ls-files -s -z -- ':(glob)**/.swiftformat' ':(glob,icase)**/.swiftlint.yml' ':(glob)**/.swift-version' > "$entries" \
    || ! git --literal-pathspecs ls-files -s -z -- "${STAGED_SWIFT[@]}" >> "$entries"; then
    rm -f -- "$entries"
    fail "ステージしたファイルの一覧を取得できない"
  fi
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    object=${entry#* }
    object=${object%% *}
    path=${entry#*$'\t'}
    [ "$mode" = 160000 ] && continue  # サブモジュールは blob がない
    mkdir -p "$STAGED_TREE/$(dirname "$path")"
    if ! git cat-file blob "$object" > "$STAGED_TREE/$path"; then
      rm -f -- "$entries"
      fail "ステージした ${path} の内容を読めない"
    fi
  done < "$entries"
  rm -f -- "$entries"
}

# インデックスの項目（git ls-files -s -z の出力。標準入力から読む）を、blob のまま $1 の下へ元の相対位置で書き出す。
# git cat-file blob で書くので、checkout-index と違い .gitattributes の smudge のフィルタ・改行の変換を通さない（検査する
# 内容をステージした・記録したツリーの内容と同じにする）。モードを保つ: 100755 は実行権限を付け、120000 は blob の内容を
# リンク先とするシンボリックリンクにし、160000（サブモジュール）は blob がないので書き出さない。それ以外のモードは
# 書き出せないものとして失敗する。失敗したら非 0 を返す（呼び出し側は失敗を確かめる。set -e に頼らない）
write_index_entries() {
  local dest=$1 entry mode object path target
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    object=${entry#* }
    object=${object%% *}
    path=${entry#*$'\t'}
    case "$mode" in
      160000) continue ;;
      100644 | 100755 | 120000) ;;
      *) echo "NG: インデックスの ${path} のモード ${mode} を書き出せない" >&2; return 1 ;;
    esac
    mkdir -p "$dest/$(dirname "$path")" || return 1
    rm -f -- "$dest/$path" || return 1
    if [ "$mode" = 120000 ]; then
      # 末尾の改行もリンク先の一部として保つ（$(…) は末尾の改行を落とすので、印の x を付けてから外す）
      target=$(git cat-file blob "$object" && printf x) || return 1
      ln -s -- "${target%x}" "$dest/$path" || return 1
    else
      git cat-file blob "$object" > "$dest/$path" || return 1
      if [ "$mode" = 100755 ]; then chmod +x "$dest/$path" || return 1; fi
    fi
  done
}

# 規則の試験に使うもの（規則・試験用のファイル・試験のスクリプト）をインデックスから同じ一時ディレクトリへ書き出す。
# 試験のスクリプトは自身の位置をリポジトリ直下とみなすので、一時ディレクトリの中のスクリプトを実行する。
# 開発ツールは作業ツリーの .tools を指す（ツールは Git の管理外）
prepare_rules_tree() {
  local directory entries
  [ -n "$STAGED_TREE" ] || STAGED_TREE=$(mktemp -d)
  # blob のまま書き出す（write_index_entries）。試験のスクリプト（100755）は実行権限を保ち、下で直接実行する
  entries=$(mktemp)
  if ! git ls-files -s -z -- ':(glob,icase)**/.swiftlint.yml' tools/lint-fixtures scripts/test-lint-rules.py > "$entries" \
    || ! write_index_entries "$STAGED_TREE" < "$entries"; then
    rm -f -- "$entries"
    fail "規則の試験に使うステージした内容を書き出せない"
  fi
  rm -f -- "$entries"
  # 入れ子の設定の置き忘れの検査（テストのディレクトリがあるのに設定がない）を、インデックスのディレクトリで行う。
  # 一覧は scripts/test-lint-rules.py の TEST_CODE_DIRS と同じ
  for directory in MinutesMobileTests MinutesMobileUITests Packages/MinutesKit/Tests; do
    if [ -n "$(git ls-files -- "$directory" | head -n 1)" ]; then mkdir -p "$STAGED_TREE/$directory"; fi
  done
  # 開発ツールは、照合した写し（snapshot_staged_tools）を指す（元の .tools は検査の途中で差し替えられうる）
  [ -e "$STAGED_TREE/.tools" ] || ln -s "$STAGED_TOOLS/.tools" "$STAGED_TREE/.tools"
}

# ステージした（追加・変更・名前の変更・種類の変更をした）ファイルの blob を、加工せずに元の相対位置で書き出す。
# git diff・checkout-index を通さないので、.gitattributes の binary・textconv・clean/smudge のフィルタ・改行の変換の
# 影響を受けない（git cat-file blob は保存した内容をそのまま出す）。サブモジュール（160000）は blob がないので書き出さない。
# シンボリックリンク（120000）はリンク先の文字列を通常のファイルとして書く（リンクをたどらない）
prepare_staged_secrets_tree() {
  local list entries entry mode object path
  STAGED_SECRETS_TREE=$(mktemp -d)
  list=$(mktemp)
  entries=$(mktemp)
  # 一覧の取得の失敗を空の一覧として扱わない
  if ! git diff --cached --name-only --diff-filter=ACMRT -z > "$list"; then
    rm -f -- "$list" "$entries"
    fail "ステージしたファイルの一覧を取得できない"
  fi
  if [ -s "$list" ] && ! xargs -0 git --literal-pathspecs ls-files -s -z -- < "$list" > "$entries"; then
    rm -f -- "$list" "$entries"
    fail "ステージしたファイルのモードを取得できない"
  fi
  rm -f -- "$list"
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    object=${entry#* }
    object=${object%% *}
    path=${entry#*$'\t'}
    [ "$mode" = 160000 ] && continue
    # gitleaks は検査する場所の直下の .gitleaksignore を指定によらず読むので、ステージしたものも書き出さずに失敗する
    if [ "$path" = .gitleaksignore ]; then
      rm -f -- "$entries"
      fail ".gitleaksignore をステージしている。検出の抑止の記録は使わない（gitleaks が指定によらず読むため）"
    fi
    mkdir -p "$STAGED_SECRETS_TREE/$(dirname "$path")"
    if ! git cat-file blob "$object" > "$STAGED_SECRETS_TREE/$path"; then
      rm -f -- "$entries"
      fail "ステージした ${path} の内容を読めない"
    fi
  done < "$entries"
  rm -f -- "$entries"
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
    if git diff --cached --quiet -- ':(glob,icase)**/.swiftlint.yml' tools/lint-fixtures scripts/test-lint-rules.py \
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
    # 抑止の仕組み（.gitleaksignore・行の gitleaks:allow）は使わない（全履歴の検査と同じ。gitleaks-history.sh）。
    # gitleaks は検査する場所の直下の .gitleaksignore を指定によらず読むので、作業ツリーにあれば検査せずに失敗する
    if [ -e .gitleaksignore ] || [ -L .gitleaksignore ]; then
      fail ".gitleaksignore がある。検出の抑止の記録は使わない（gitleaks が指定によらず読むため）。削除してから実行し直す"
    fi
    # ステージした内容は、git の差分（gitleaks git --pre-commit --staged）ではなく、書き出した blob を gitleaks dir で検査する。
    # 8.30.1 の --staged は git diff を使い、--log-opts（履歴の検査の --no-textconv・--text）は効かないので、.gitattributes の
    # binary・textconv で鍵の行を隠せる。dir は .gitignore・.gitattributes によらず書き出したファイルの全体を検査する
    prepare_staged_secrets_tree
    (cd "$STAGED_SECRETS_TREE" && "$TOOLS/gitleaks" dir --no-banner --no-color --redact --config "$STAGED_GITLEAKS_CONFIG" \
      --gitleaks-ignore-path /dev/null --ignore-gitleaks-allow .)
  else
    # 全履歴の検査は、自己試験（test-checks.py gitleaks）と同じコマンドで行う
    scripts/gitleaks-history.sh .
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
  local changes index tree
  # git status の失敗を「変更なし」と取り違えない（$(…) を条件の中に書くと失敗が見えないので、先に取得して確かめる）
  changes=$(git status --porcelain --untracked-files=normal) || return 1
  if [ -z "$changes" ]; then
    printf 'commit %s\n' "$(git rev-parse HEAD)"
    return 0
  fi
  # 一時のインデックスに作業ツリーを写してツリーを作る（本来のインデックスとステージの内容は変えない）。
  # 識別子は測る入力（Packages/）だけでなく作業ツリーの全体を表す（測定の前後で作業ツリーのどこかが変われば記録しない）。
  # git add -A は追跡していないファイル（無視するものは除く）の blob とツリーをオブジェクトの格納庫に書き、どの参照からも
  # 届かないまま残る。これらは手元のリポジトリの中だけにあり、git gc が gc.pruneExpire（既定 2 週間）を過ぎたものを消す
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

# カバレッジのロックを取る。待たずに失敗する（別の測定が実行中。残骸なら削除して実行し直す）
take_coverage_lock() {
  mkdir -p .build
  mkdir "$COVERAGE_LOCK" 2>/dev/null \
    || fail "別のカバレッジの測定・集計が実行中（${COVERAGE_LOCK}）。実行中でなければ、そのディレクトリを削除してから実行し直す"
  COVERAGE_LOCK_HELD=1
}

# 持ち主の印を先に外してからロックを外す（copy_tools と同じ。rmdir の途中で中断されても、後始末が別の実行の取り直した
# ロックを消さない。中断でロックが残った場合は、残骸として案内のとおり削除する）
release_coverage_lock() {
  local held=$COVERAGE_LOCK_HELD
  COVERAGE_LOCK_HELD=""
  if [ -n "$held" ]; then rmdir "$COVERAGE_LOCK" 2>/dev/null || true; fi
}

# 入力の識別子が指すツリー（$1）から Packages/ を写しの場所へ書き出す。git archive は .gitattributes の export-ignore で
# 試験用のファイルなどを省きうるので使わず、一時のインデックスに読み込んで、追跡しているファイルをすべて blob のまま
# 書き出す（write_index_entries。checkout-index の smudge のフィルタ・改行の変換を通さないので、測る内容は識別子の
# ツリーの内容と一致する）。失敗したら非 0 を返す（呼び出し側は条件の中で呼ぶので、set -e に頼らない）
materialize_coverage_input() {
  local index entries status=0
  index=$(mktemp)
  entries=$(mktemp)
  rm -f -- "$index"
  GIT_INDEX_FILE="$index" git read-tree "$1" || status=1
  if [ "$status" = 0 ]; then
    GIT_INDEX_FILE="$index" git ls-files -s -z -- Packages > "$entries" || status=1
  fi
  if [ "$status" = 0 ]; then
    write_index_entries "$COVERAGE_INPUT" < "$entries" || status=1
  fi
  rm -f -- "$index" "$entries"
  return "$status"
}

# 元の作業ツリーの開発ツール（$1。Git の管理外）を、導入のロック（setup-tools.sh と同じ .install.lock）を取ってから
# 専用の作業ツリー（$2）へ写す（.githooks/pre-push と同じ手順）。共有のリンクで指すと、検査の途中に別の setup-tools.sh が
# bin のリンクや版のディレクトリを差し替え、手順 0 で照合した導入と後の手順が使う導入が食い違いうる。写しは APFS では
# clone（cp -c）、対応しないファイルシステムでは通常の複写で作る。bin のリンクは相対パスなので写しの中を指し、
# 手順 0 の照合と後の手順はすべてこの写しを使う
copy_tools() {
  local source=$1 copy=$2 waited=0 retried=0 entry
  [ -d "$source" ] || fail "開発ツールが導入されていない（${source}）。scripts/setup-tools.sh を実行してから実行し直す"
  until mkdir "$source/.install.lock" 2>/dev/null; do
    # 作れなかった直後にロックが外れた場合は、待たずに 1 回だけ取り直す。取り直しても作れずロックもなければ止める
    if [ ! -d "$source/.install.lock" ] && [ "$retried" -eq 0 ]; then
      retried=1
      continue
    fi
    if [ -d "$source/.install.lock" ]; then retried=0; fi
    if [ ! -d "$source/.install.lock" ] || [ "$waited" -ge 600 ]; then
      fail "開発ツールの導入のロック（${source}/.install.lock）を取れない。導入の実行中でなければ削除してから実行し直す"
    fi
    if [ "$waited" -eq 0 ]; then echo "待機: 開発ツールの導入・照合が実行中。終わるのを待つ" >&2; fi
    sleep 1
    waited=$((waited + 1))
  done
  TOOLS_LOCK="$source/.install.lock"
  # 写した bin のリンクが写しの中を指すのは、リンクが相対パスの場合だけ（.githooks/pre-push と同じ）。絶対パスのリンクは
  # 写しても元の導入を指すので、写す前に理由を示して止める（リンクは書き換えない）
  for entry in "$source"/bin/*; do
    if [ -L "$entry" ] && case "$(readlink -- "$entry")" in /*) true ;; *) false ;; esac; then
      fail "開発ツールのリンク（${entry}）が絶対パスを指している。scripts/setup-tools.sh で導入し直してから実行し直す"
    fi
  done
  rm -rf -- "$copy"
  mkdir "$copy"
  for entry in "$source"/* "$source"/.[!.]*; do
    if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then continue; fi  # 一致するものがないときの glob の文字列
    case "${entry##*/}" in .install.lock | .work.*) continue ;; esac  # ロックと導入の途中の作業場所は写さない
    # -P: シンボリックリンクはたどらずにリンクとして写す
    if ! cp -c -R -P -- "$entry" "$copy/" 2>/dev/null; then
      rm -rf -- "${copy:?}/${entry##*/}"
      cp -R -P -- "$entry" "$copy/" || fail "開発ツールを写せない（${entry}）"
    fi
  done
  # 持ち主の印を先に外してからロックを外す（間で中断されても、後始末が別の実行の取り直したロックを消さない。
  # 印を外した後・ロックを外す前に中断された場合はロックが残るが、導入の直列化は弱めない。残骸は案内のとおり削除する）
  local held=$TOOLS_LOCK
  TOOLS_LOCK=""
  rmdir "$held"
}

# 元の作業ツリーが sparse checkout なら、専用の作業ツリー（$1）もその指定を引き継ぎ（git worktree add が写す）、一部のファイル
# しかない状態で検査することになる。専用の作業ツリーで有効なら、そこでだけ解除して全体を展開する（.githooks/pre-push の
# full_checkout と同じ。作業ツリーごとの設定に書くので、元の作業ツリーは sparse のまま）。有効でなければ解除しない。
# 設定を読めない場合は止める。展開は改行の変換（core.autocrlf）を掛けない
full_checkout() {
  local tree=$1 sparse status=0
  # GIT_CONFIG を外して読む（設定されていると git config はそのファイルだけを読み、専用の作業ツリーの設定を見ない）
  sparse=$(env -u GIT_CONFIG git -C "$tree" config --type=bool --get core.sparseCheckout) || status=$?
  case $status in
    0) if [ "$sparse" = true ]; then git -C "$tree" -c core.autocrlf=false sparse-checkout disable; fi ;;
    1) ;;  # 設定がない（sparse でない）
    *) fail "専用の作業ツリーの sparse checkout の設定を読めない（${tree}）" ;;
  esac
}

# 作業ツリー（$1）の追跡しているファイルが、そのインデックス（チェックアウトしたコミット）の blob とバイト単位で一致することを
# 確かめる。checkout は .gitattributes の smudge のフィルタで内容を変えうる（core.autocrlf=false では filter は止まらない）。
# 内容を変えたチェックアウトを、コミットの合格として検査しない。通常のファイルは git hash-object --no-filters で、
# シンボリックリンクはリンク先の文字列で比べる。サブモジュール（160000）は blob がないので比べない。一致しなければ非 0 を返す
verify_checkout_blobs() {
  local tree=$1 entries entry mode object path actual expected status=0
  entries=$(mktemp)
  if ! git -C "$tree" ls-files -s -z > "$entries"; then rm -f -- "$entries"; return 1; fi
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    object=${entry#* }
    object=${object%% *}
    path=${entry#*$'\t'}
    case "$mode" in
      160000) continue ;;
      120000)
        # リンク先は末尾の改行も含めてバイト単位で比べる（$(…) は末尾の改行を落とすので、印の x を付けてから外す）
        actual=""
        expected=""
        if [ -L "$tree/$path" ]; then
          actual=$(readlink -n -- "$tree/$path" && printf x) && actual=${actual%x} || actual=""
          expected=$(git -C "$tree" cat-file blob "$object" && printf x) && expected=${expected%x} || expected=""
        fi
        if [ ! -L "$tree/$path" ] || [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
          echo "NG: チェックアウトした ${path} がコミットの blob と違う（シンボリックリンク）" >&2
          status=1
        fi
        ;;
      *)
        actual=""
        if [ -f "$tree/$path" ] && [ ! -L "$tree/$path" ]; then
          actual=$(git -C "$tree" hash-object --no-filters -- "$tree/$path") || actual=""
        fi
        if [ "$actual" != "$object" ]; then
          echo "NG: チェックアウトした ${path} がコミットの blob と違う（.gitattributes のフィルタなどで内容が変わった）" >&2
          status=1
        fi
        ;;
    esac
  done < "$entries"
  rm -f -- "$entries"
  return "$status"
}

# --staged の検査が使う開発ツールを固定する。元の .tools を導入のロックを取って一時ディレクトリへ写し（copy_tools）、
# 写しをステージした tools/versions と照合してから、以後の手順はすべて写しを使う（TOOLS）。コミット前のフックの照合の後に
# 別の setup-tools.sh が bin のリンクや版のディレクトリを差し替えても、照合していない導入で整形・静的解析・秘密情報を検査しない
snapshot_staged_tools() {
  STAGED_TOOLS=$(mktemp -d)
  copy_tools "$ROOT/.tools" "$STAGED_TOOLS/.tools"
  git cat-file blob :tools/versions > "$STAGED_TOOLS/versions" || fail "ステージした tools/versions を読めない"
  scripts/setup-tools.sh --verify --versions "$STAGED_TOOLS/versions" --dest "$STAGED_TOOLS/.tools" > /dev/null \
    || fail "写した開発ツールがステージした tools/versions と一致しない。ステージした台帳で導入し直してからコミットする"
  TOOLS="$STAGED_TOOLS/.tools/bin"
}

# 引き継いだ MINUTES_CHECK_ISOLATED を信じてよいか。この作業ツリーが、プッシュ前のフックか check.sh --require-clean が
# この検査のために作った専用の作業ツリー（作成時に専用の管理ディレクトリへ書いた印があり、印の作業ツリーのパスが ROOT の
# 実体のパスと一致する）なら 0 を返す。そうでなければ 1（元の作業ツリーで変数だけを引き継いでも、展開を省かせない）
isolated_checkout() {
  local private common root_real mark file text
  private=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) && private=$(cd -P "$private" && pwd) || return 1
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) && common=$(cd -P "$common" && pwd) || return 1
  root_real=$(cd -P "$ROOT" && pwd) || return 1
  [ "$(dirname "$private")" = "$common/worktrees" ] || return 1
  for mark in "minutes-pre-push-owner:MinutesMobile pre-push" "minutes-check-owner:MinutesMobile check"; do
    file="$private/${mark%%:*}"
    text="${mark#*:} ${root_real}"
    if [ -f "$file" ] && [ ! -L "$file" ] && [ "$(cat "$file")" = "$text" ]; then return 0; fi
  done
  return 1
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

# カバレッジの JSON の SHA-256。読めなければ失敗を返す
coverage_digest() {
  local digest
  digest=$(shasum -a 256 "$1") || return 1
  printf '%s\n' "${digest%% *}"
}

step_7() {
  local destination
  [ -f "$PACKAGE/Package.swift" ] || fail "$PACKAGE がない（中核ロジックの Package を作ってから実行する）"
  local measured report digest
  mkdir -p "$PACKAGE/.build"
  take_coverage_lock
  rm -f -- "$COVERAGE_HEAD" "$COVERAGE_JSON"
  rm -rf -- "$COVERAGE_INPUT"
  # 測る入力を固定する。入力の識別子（コミット、または作業ツリーを写したツリー）が指すツリーから Packages/ を展開し、
  # その写しを測る。展開できなければ測らない（識別子と違う内容を測った結果をコミットに帰属させない）
  measured=$(coverage_input) || fail "カバレッジを測る入力を識別できない"
  mkdir -p "$COVERAGE_INPUT"
  materialize_coverage_input "${measured#* }" || fail "カバレッジを測る入力（${measured}）を展開できない"
  # 測定ごとに専用の出力先を使う（同じ Package を対象にした別の swift test が、この測定の JSON を上書きできない）。
  # 毎回ビルドし直すので時間はかかるが、共有の出力先に残った別の測定の結果を記録しない
  COVERAGE_SCRATCH=$(mktemp -d)
  swift test --package-path "$COVERAGE_INPUT/$PACKAGE" --scratch-path "$COVERAGE_SCRATCH" --enable-code-coverage
  # 追加の安全策: 測った前後で作業ツリーが変わっていれば記録しない（写しを測るので結果は識別子と一致するが、
  # 測定中に変わった作業ツリーの合格として扱われるのを避ける）
  [ "$(coverage_input)" = "$measured" ] || fail "カバレッジの測定中に作業ツリーが変わった。手順 7 を実行し直す"
  report=$(swift test --package-path "$COVERAGE_INPUT/$PACKAGE" --scratch-path "$COVERAGE_SCRATCH" --show-codecov-path)
  [ -f "$report" ] || fail "カバレッジの記録がない（swift test の結果を確かめる）"
  # この測定の JSON を記録の場所へ写す。同じディレクトリの一時ファイルに写してから rename で置き換え、
  # 写したものからハッシュを計算する。JSON を置いてから入力とハッシュの記録を置く（記録があれば JSON もある）
  cp -- "$report" "$COVERAGE_JSON.$$"
  digest=$(coverage_digest "$COVERAGE_JSON.$$") || fail "カバレッジの記録のハッシュを計算できない"
  mv -f -- "$COVERAGE_JSON.$$" "$COVERAGE_JSON"
  printf '%s\n%s\n' "$measured" "$digest" > "$COVERAGE_HEAD.$$"
  mv -f -- "$COVERAGE_HEAD.$$" "$COVERAGE_HEAD"
  rm -rf -- "$COVERAGE_SCRATCH"
  COVERAGE_SCRATCH=""
  release_coverage_lock
  destination=$(test_destination)
  xcodebuild -quiet -project "$PROJECT" -scheme "$SCHEME" -destination "$destination" \
    -skip-testing:"$UI_TESTS" test
}

step_8() {
  # current は EXIT の trap が使う手順の番号なので、局所変数の名前に使わない（隠すと失敗した手順の表示が壊れる）
  local measured present recorded_digest
  scripts/test-checks.py coverage
  [ -f "$PACKAGE/Package.swift" ] || fail "$PACKAGE がない（中核ロジックの Package を作ってから実行する）"
  # 別の入力（別のコミット・別の作業ツリーの内容）で測った記録を集計しない
  take_coverage_lock
  if [ ! -f "$COVERAGE_HEAD" ]; then
    # 専用の作業ツリーは実行のたびに作り直すので、前の実行の測定は残らない。手順 7 と 8 を同じ実行で行う
    if [ -n "${MINUTES_CHECK_ISOLATED:-}" ]; then
      fail "カバレッジを測った入力の記録がない。専用の作業ツリーでは scripts/check.sh --require-clean --steps 7,8 のように手順 7 と 8 を一緒に実行する"
    fi
    fail "カバレッジを測った入力の記録がない。手順 7 を先に実行する"
  fi
  # 2 行を同じ開いたファイルから読む（2 回開くと、その間に記録が置き換えられたとき別の記録の行を組み合わせうる）。
  # 行がなければ空のまま続け、下の照合で失敗させる（set -e で黙って止めない）
  measured=""
  recorded_digest=""
  { IFS= read -r measured || :; IFS= read -r recorded_digest || :; } < "$COVERAGE_HEAD"
  present=$(coverage_input) || fail "カバレッジを集計する入力を識別できない"
  [ "$measured" = "$present" ] || fail "カバレッジは別の入力（${measured}）で測ったもの。手順 7 を実行し直す"
  # 照合と集計で同じバイト列を使う。記録を一時ファイルへ写し、写しのハッシュを照合してから同じ写しを集計する
  # （照合の後に記録が書き換えられても、照合していない内容を集計しない）
  [ -f "$COVERAGE_JSON" ] || fail "カバレッジの記録がない。手順 7 を先に実行する"
  COVERAGE_COPY=$(mktemp)
  cp -- "$COVERAGE_JSON" "$COVERAGE_COPY"
  # 手順 7 の後に別の測定で記録が書き換えられていれば、入力が同じでも集計しない
  [ -n "$recorded_digest" ] && [ "$(coverage_digest "$COVERAGE_COPY")" = "$recorded_digest" ] \
    || fail "カバレッジの記録が手順 7 で測ったものと違う。手順 7 を実行し直す"
  case "$measured" in
    worktree*) echo "注意: カバレッジは未コミットの変更を含む作業ツリーで測った。コミットの記録としては扱わない" >&2 ;;
  esac
  # 手順 7 が測ったのは固定した入力の写しなので、写しの Sources の行を数える
  scripts/coverage-report.py "$COVERAGE_COPY" --sources "$COVERAGE_INPUT/$PACKAGE/Sources" --target "$PACKAGE_COVERAGE_TARGET"
  release_coverage_lock
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
    # インデックスの全体（sparse checkout で作業ツリーにない skip-worktree の項目を含む）を blob のまま書き出して照合する
    # （write_index_entries。smudge のフィルタ・改行の変換を通さない。verify-docs.py は 100755 のまま実行できる）
    local entries
    entries=$(mktemp)
    if ! git ls-files -s -z > "$entries" || ! write_index_entries "$STAGED_DOCS_TREE" < "$entries"; then
      rm -f -- "$entries"
      fail "文書の整合の検査に使うステージした内容を書き出せない"
    fi
    rm -f -- "$entries"
    "$STAGED_DOCS_TREE/scripts/verify-docs.py" --root "$STAGED_DOCS_TREE"
  else
    scripts/test-checks.py docs
    scripts/verify-docs.py
  fi
}

# 手順の関数は条件（if・&&・||）の中で呼ばない。条件の中では set -e が効かず、
# 途中のコマンドが失敗しても最後のコマンドが成功すれば通過になってしまう。失敗の表示は EXIT の trap で行う。
current=""
# 後始末の削除。失敗は場所とともに報告し、set -e で後始末を止めない（後続の削除とロックの解放を飛ばさない）。
# 失敗したら CLEANUP_FAILED を立て、終了コードを失敗にする
CLEANUP_FAILED=""
remove_or_report() {
  rm -rf -- "$1" || { echo "NG: 一時の場所を削除できない（${1}）" >&2; CLEANUP_FAILED=1; }
}
on_exit() {
  local status=$?
  if [ -n "$STAGED_TREE" ]; then remove_or_report "$STAGED_TREE"; fi
  if [ -n "$STAGED_GITLEAKS_CONFIG" ]; then remove_or_report "$STAGED_GITLEAKS_CONFIG"; fi
  if [ -n "$STAGED_DOCS_TREE" ]; then remove_or_report "$STAGED_DOCS_TREE"; fi
  if [ -n "$STAGED_SECRETS_TREE" ]; then remove_or_report "$STAGED_SECRETS_TREE"; fi
  if [ -n "$STAGED_TOOLS" ]; then remove_or_report "$STAGED_TOOLS"; fi
  if [ -n "$STAGED_INDEX_COPY" ]; then remove_or_report "$STAGED_INDEX_COPY"; fi
  if [ -n "$COVERAGE_SCRATCH" ]; then remove_or_report "$COVERAGE_SCRATCH"; fi
  if [ -n "$COVERAGE_COPY" ]; then remove_or_report "$COVERAGE_COPY"; fi
  if [ -n "$TOOLS_LOCK" ]; then rmdir "$TOOLS_LOCK" 2>/dev/null || true; fi
  # この実行で作った専用の作業ツリーだけを登録ごと消す。リポジトリ全体の git worktree prune は実行しない（ほかの作業ツリーの
  # 登録を消さない）。消せなければ場所を報告して失敗にし、置き場所も残す（中身を確かめて git worktree remove で消せるように）
  isolated_removed=1
  # 作成の前に失敗した場合（ディレクトリがない）は消すものがない
  if [ -n "$ISOLATED_TREE" ] && { [ -e "$ISOLATED_TREE" ] || [ -L "$ISOLATED_TREE" ]; } \
    && ! git -C "$ROOT" worktree remove --force "$ISOLATED_TREE"; then
    echo "NG: 専用の作業ツリーを削除できない（${ISOLATED_TREE}）。git worktree remove --force でその作業ツリーを削除する" >&2
    CLEANUP_FAILED=1
    isolated_removed=""
  fi
  if [ -n "$ISOLATED_BASE" ] && [ -n "$isolated_removed" ]; then remove_or_report "$ISOLATED_BASE"; fi
  release_coverage_lock
  if [ "$status" -eq 0 ] && [ -n "$CLEANUP_FAILED" ]; then exit 1; fi
  # 成功も明示的に exit する（bash 3.2 は、trap の最後のコマンドの状態で終了コードを上書きすることがある）
  if [ "$status" -eq 0 ]; then exit 0; fi
  if [ -n "$current" ]; then echo "NG: 手順 ${current}（$(title "$current")）で失敗" >&2; fi
  # 明示的に終了コードを返す（bash 3.2 は、trap の最後のコマンドの状態でエラー終了を上書きすることがある）
  if [ "$status" -eq 2 ] && [ -z "$current" ]; then exit 2; fi
  exit 1
}
trap on_exit EXIT
trap 'exit 130' INT TERM
if [ "$STAGED" = 1 ]; then snapshot_staged_tools; fi
if [ "$STAGED" = 1 ] && [ ${#STAGED_SWIFT[@]} -gt 0 ]; then prepare_staged_tree; fi
# 通常の実行は作業ツリーを検査する。開始時と終了時の HEAD と未コミットの変更を控え、
# （未追跡のファイルは status.showUntrackedFiles の設定によらず数える）
# 検査したツリーがコミットと一致する場合だけ、そのコミットの合格として表示する
START_HEAD=""
START_CHANGES=""
if [ "$STAGED" = 0 ]; then
  # コミットの合格を求める実行は、置き換えのオブジェクト（git replace）を使わず、元のコミットのオブジェクトで開始時の
  # 照合・専用の作業ツリーの展開・sparse checkout の解除・入れ子の check.sh の検査を行う（子のプロセスにも引き継ぐ）
  if [ "$REQUIRE_CLEAN" = 1 ]; then export GIT_NO_REPLACE_OBJECTS=1; fi
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
# コミットの合格を求める実行は、開始時のコミットを専用の作業ツリーに展開し、その中の check.sh で検査する（元の作業ツリーを
# 検査中に書き換えて戻しても、検査する内容は変わらない）。専用の作業ツリーで実行中であることは MINUTES_CHECK_ISOLATED で
# 示す（プッシュ前のフックは自身の専用の作業ツリーで実行するので、これを設定して二重に展開しない）。置き場所は
# リポジトリの外（中に置くと、SwiftFormat などが親のディレクトリの設定ファイルも読む）
# 引き継いだ MINUTES_CHECK_ISOLATED は、この作業ツリーがこの検査の専用の作業ツリーである場合だけ認める（元の作業ツリーで
# 変数を引き継いだ直接の --require-clean が、展開を省いて専用の作業ツリーで検査したと表示しない）
if [ "$REQUIRE_CLEAN" = 1 ] && [ -n "${MINUTES_CHECK_ISOLATED:-}" ] && ! isolated_checkout; then
  echo "注意: 引き継いだ MINUTES_CHECK_ISOLATED を無視する（この作業ツリーはこの検査の専用の作業ツリーではない）。開始時のコミットを専用の作業ツリーに展開する" >&2
  unset MINUTES_CHECK_ISOLATED
fi
if [ "$REQUIRE_CLEAN" = 1 ] && [ -z "${MINUTES_CHECK_ISOLATED:-}" ]; then
  # 一時の置き場所は TMPDIR（なければ /tmp）の下に明示的に作る（mktemp -d の既定は TMPDIR を使わない環境がある）
  temporary_root=${TMPDIR:-/tmp}
  ISOLATED_BASE=$(mktemp -d "${temporary_root%/}/minutes-check.XXXXXX")
  # 一時の置き場所も、プッシュ前のフックと同じ判定（scripts/worktree-location.py）で確かめてから作業ツリーを作る。
  # TMPDIR が作業ツリーの中や、SwiftFormat の設定（.swiftformat・.swift-version）のある場所の下を指すと、専用の作業ツリーの
  # 検査に別の設定が混ざる。作業ツリーの外で、設定を継がない一時ディレクトリは認める
  # 判定の結果のパスは NUL で終わる記録で受け取る（$(…) はパスの末尾の改行を落とすので使わない）
  location_status=0
  location_out=$(mktemp)
  "$ROOT/scripts/worktree-location.py" check "$ISOLATED_BASE" "$ROOT" > "$location_out" || location_status=$?
  checked_base=""
  if [ "$location_status" = 0 ] && ! IFS= read -r -d '' checked_base < "$location_out"; then location_status=2; fi
  rm -f -- "$location_out"
  case $location_status in
    0) ISOLATED_BASE=$checked_base ;;
    1) fail "専用の作業ツリーの一時の置き場所（${ISOLATED_BASE}）を使えない。TMPDIR で作業ツリーの外の、SwiftFormat の設定のない場所を指定する" ;;
    *) fail "専用の作業ツリーの一時の置き場所（${ISOLATED_BASE}）が作業ツリーの中かを判定できない" ;;
  esac
  ISOLATED_TREE="$ISOLATED_BASE/tree"
  # 改行の変換（利用者の core.autocrlf の設定）を掛けずに展開する（scripts/check.sh などを LF のまま実行する）
  git -c core.autocrlf=false worktree add -q --detach "$ISOLATED_TREE" "$START_HEAD"
  # 作った作業ツリーの専用の管理ディレクトリに印を書く（入れ子の check.sh が、引き継いだ MINUTES_CHECK_ISOLATED を
  # この作業ツリーのものと確かめる。isolated_checkout）
  isolated_private=$(git -C "$ISOLATED_TREE" rev-parse --path-format=absolute --git-dir) \
    && isolated_private=$(cd -P "$isolated_private" && pwd) && isolated_root=$(cd -P "$ISOLATED_TREE" && pwd) \
    || fail "専用の作業ツリー（${ISOLATED_TREE}）の管理ディレクトリを特定できない"
  printf '%s\n' "MinutesMobile check ${isolated_root}" > "$isolated_private/minutes-check-owner"
  full_checkout "$ISOLATED_TREE"
  # 展開した内容がコミットの blob と同じであること（smudge のフィルタで変わった内容をコミットの合格として検査しない）
  verify_checkout_blobs "$ISOLATED_TREE" \
    || fail "専用の作業ツリーの内容がコミット ${START_HEAD} の blob と違う（.gitattributes の smudge のフィルタなど）。コミットの合格にしない"
  # 開発ツールは Git の管理外（.gitignore の /.tools で無視される）。導入のロックを取って写し、手順 0 の照合と
  # 後の手順はすべてこの写しを使う（検査の途中の setup-tools.sh による差し替えの影響を受けない）
  copy_tools "$ROOT/.tools" "$ISOLATED_TREE/.tools"
  echo "== 開始時のコミット ${START_HEAD} を専用の作業ツリー（${ISOLATED_TREE}）に展開して検査する"
  MINUTES_CHECK_ISOLATED=1 "$ISOLATED_TREE/scripts/check.sh" "${ARGUMENTS[@]}"
  exit 0
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
  # 検査が読んだ専用のインデックスと、コミットされる本来のインデックスの両方が、控えたツリーのままであること
  end_copy_tree=$(git write-tree) || fail "終了時の専用のインデックスのツリーを作れない"
  end_index_tree=$(GIT_INDEX_FILE="$ORIGINAL_INDEX" git write-tree) || fail "終了時のステージした内容のツリーを作れない"
  if [ "$end_copy_tree" != "$STAGED_INDEX_TREE" ] || [ "$end_index_tree" != "$STAGED_INDEX_TREE" ]; then
    fail "検査中にステージした内容が変わった（開始時 ${STAGED_INDEX_TREE} / 終了時 ${end_index_tree}・検査したもの ${end_copy_tree}）。検査した内容と違うので合格にしない"
  fi
  echo "OK: コミット前の検査を通過"
else
  end_head=$(git rev-parse HEAD)
  end_changes=$(git status --porcelain --untracked-files=normal)
  if [ "$REQUIRE_CLEAN" = 1 ]; then
    # 専用の作業ツリーでの検査。展開したコミットから変わっていなければ、そのコミットの合格とする
    if [ -n "$START_CHANGES" ] || [ -n "$end_changes" ] || [ "$START_HEAD" != "$end_head" ]; then
      fail "検査中に HEAD が変わった、または未コミットの変更が生じた。検査したツリーがコミットと一致しないので合格にしない"
    fi
    echo "OK: 手順$STEPS を通過（コミット ${end_head} を専用の作業ツリーで検査）"
  else
    # 通常の実行は作業ツリーを検査する。検査中に書き換えて戻した内容も検査しうるので、変更の有無によらず
    # コミットの合格にはしない
    if [ -n "$START_CHANGES" ] || [ -n "$end_changes" ] || [ "$START_HEAD" != "$end_head" ]; then
      echo "OK: 手順$STEPS を通過（作業ツリーを検査。未コミットの変更あり。コミットの合格としては記録しない）"
    else
      echo "OK: 手順$STEPS を通過（作業ツリーを検査。コミットの合格としては記録しない）"
    fi
    echo "注意: PR の説明に貼る結果（コミットの合格）は scripts/check.sh --require-clean で得る（開始時のコミットを専用の作業ツリーで検査する）" >&2
  fi
fi
