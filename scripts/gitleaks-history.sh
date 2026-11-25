#!/bin/bash
# Git の全履歴の秘密情報の検査（TST-CI-04、SEC-SUP-03）。check.sh の手順 3 と、その自己試験（test-checks.py gitleaks）が
# 同じこのコマンドを実行する（試験した指定と手順 3 の指定が食い違わないようにする）。
#
# 使い方: scripts/gitleaks-history.sh <リポジトリ> [--report-format <形式>] [--report-path <パス>]
#   追加の引数は、自己試験が検出の結果を読むための報告の指定だけを認める（検査の方式・対象・抑止・伏せ字・終了コードを
#   変える指定を渡させない）
# 終了コード: gitleaks のもの（0 = 検出なし / 1 = 検出あり・失敗）。2 = 使い方の誤り
set -euo pipefail

# CDPATH が設定されていると、相対パスの cd が別の場所へ移り、移った先を標準出力に書いて ROOT に混ぜる。外してから解決する
unset CDPATH
# $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が付けた区切りの改行 1 つだけを外す
ROOT=$(cd "$(dirname "$0")/.." && pwd && printf x)
ROOT=${ROOT%x}
ROOT=${ROOT%$'\n'}
# 開発ツールの場所。check.sh は照合した写しを MINUTES_TOOLS_DIR で渡す（なければリポジトリの .tools）
TOOLS_DIR=${MINUTES_TOOLS_DIR:-$ROOT/.tools}
# git log に渡す指定。--log-opts は既定の指定（--all --full-history）を置き換えるので、既定の 2 つも書く。
# --diff-merges=separate でマージのコミットの差分を親ごとに検査する（マージの解決で足した行を見落とさない。-m は
# 形式が設定 log.diffMerges に左右されるので使わない）。--root で最初のコミットの差分も検査する（設定 log.showRoot=false
# でも省かない）。--no-textconv で .gitattributes の textconv を使わずに元の内容の差分を検査する（変換で鍵を隠させない）。
# --text で .gitattributes の binary（-diff）の属性のファイルも本文の差分として出す（「Binary files differ」で鍵を隠させない）。
# --no-color で設定 color.diff・color.ui=always の色づけの制御文字を差分に入れない（行が分断されて鍵の検出を逃させない）
# --no-show-signature で設定 log.showSignature=true の署名の検証を行わない（検証の失敗が標準エラーに出て、走査の失敗と取り違える）
# --no-renames で名前の変更を検出しない（数が設定 diff.renameLimit を超えると警告が標準エラーに出て、走査の失敗と取り違える）
# --no-notes で注記（git notes）を表示しない（設定 notes.displayRef などの指す参照が読めないと警告が標準エラーに出て、走査の
# 失敗と取り違える。注記のコミット自体は --all が refs/notes/ もたどるので検査する）
# --no-relative で設定 diff.relative=true のときも、引数のサブディレクトリの外の差分を除かない（除くと外の鍵を見落とす）
# --pretty=medium・--no-abbrev-commit で設定 format.pretty・log.abbrevCommit に左右されず、gitleaks が読む形式と完全なコミットの ID にする
# --src-prefix=a/・--dst-prefix=b/ で設定 diff.noprefix・diff.mnemonicPrefix・diff.srcPrefix・diff.dstPrefix に左右されない
# （gitleaks は差分の見出しの a/・b/ を前提にファイルを読む。--default-prefix は使わない）
# -O/dev/null で設定 diff.orderFile を打ち消す（指すファイルがないと git log が失敗し、読める履歴も検査できない）
GITLEAKS_LOG_OPTS="--all --full-history --diff-merges=separate --root --no-textconv --text --no-color --no-show-signature --no-renames --no-notes --no-relative --pretty=medium --no-abbrev-commit --src-prefix=a/ --dst-prefix=b/ -O/dev/null"
# 抑止の仕組みは使わない（どちらも承認の記録なしに検出を隠せるため）。--gitleaks-ignore-path に空のファイル（/dev/null）を
# 渡して実行時の場所の .gitleaksignore を読ませず、--ignore-gitleaks-allow で行の「gitleaks:allow」のコメントも無視する。
# gitleaks は検査するリポジトリの直下の .gitleaksignore を指定によらず読む（8.30.1 で確かめた）ので、それがあれば検査せずに失敗する
GITLEAKS_SUPPRESSION=(--gitleaks-ignore-path /dev/null --ignore-gitleaks-allow)

usage() {
  echo "使い方: $0 <リポジトリ> [--report-format json|csv|junit|sarif] [--report-path <パス>]" >&2
  exit 2
}
[ $# -ge 1 ] && [ -n "$1" ] || usage
# リポジトリは一度だけ実体の絶対パスに解決し、以後の判定と gitleaks の検査の両方に同じパスを渡す（相対パス・「-」で始まる
# 名前・途中で差し替えられたシンボリックリンクで、判定したものと別のリポジトリを検査しない）。「.」は現在の作業ツリーになる
# 相対パスは ./ を付けてから cd する（「-」という名前のディレクトリを、cd が直前の場所（OLDPWD）と解釈しないように）
case "$1" in /*) requested=$1 ;; *) requested=./$1 ;; esac
# $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が付けた区切りの改行 1 つだけを外す
# （名前が改行で終わるリポジトリを、改行のない名前の別のリポジトリと取り違えない）
repository=$(cd -P -- "$requested" 2>/dev/null && pwd && printf x) || {
  echo "NG: リポジトリ ${1} を解決できない" >&2
  exit 1
}
repository=${repository%x}
repository=${repository%$'\n'}
shift
# 追加の引数は報告の指定（--report-format・--report-path。値は次の引数）だけを、それぞれ 1 回まで認める。
# ほかの指定（--no-git・--log-opts・--exit-code・--no-redact・--baseline-path・--config など）は、検査の方式や対象を変え、
# 検出を隠し、伏せ字を外し、終了コードを変えうるので、gitleaks に渡さずに使い方の誤りにする
REPORT=()
format_given=""
path_given=""
while [ $# -gt 0 ]; do
  # 値がない・空・「-」で始まる（次の指定を値として取り込む）ものは認めない
  [ $# -ge 2 ] && [ -n "$2" ] && [ "${2#-}" = "$2" ] || usage
  case "$1" in
    --report-format)
      [ -z "$format_given" ] || usage
      case "$2" in json | csv | junit | sarif) ;; *) usage ;; esac
      format_given=1
      ;;
    --report-path)
      [ -z "$path_given" ] || usage
      path_given=1
      ;;
    *) usage ;;
  esac
  REPORT+=("$1" "$2")
  shift 2
done
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
# 旧来の grafts（info/grafts）は GIT_NO_REPLACE_OBJECTS では外れず、コミットの親を付け替えて履歴の一部を隠せる
# （git 2.55.0 で確かめた。GIT_GRAFT_FILE は上で外したので、読まれるのは共通のディレクトリの info/grafts だけ）。
# コメントと空行だけのファイルでも、git が非推奨の案内を標準エラーに出し、gitleaks（8.30.1）が履歴を読まずに
# 「no leaks found」で通すので、ファイルがあれば内容によらず検査せずに失敗する
grafts=$(git -C "$repository" rev-parse --path-format=absolute --git-path info/grafts)
if [ -e "$grafts" ] || [ -L "$grafts" ]; then
  echo "NG: ${grafts} がある。grafts はコミットの親を付け替えるので全履歴を検査できない。git replace --convert-graft-file で置き換えのオブジェクトに変えるか削除してから検査し直す" >&2
  exit 1
fi
if [ -e "$repository/.gitleaksignore" ] || [ -L "$repository/.gitleaksignore" ]; then
  echo "NG: ${repository}/.gitleaksignore がある。検出の抑止の記録は使わない（gitleaks が指定によらず読むため）。削除してから検査し直す" >&2
  exit 1
fi
# gitleaks（8.30.1）は git log の失敗（壊れた・欠けたオブジェクトなど）を終了コードに反映せず、読めた分だけで「no leaks found」
# として通しうる（標準エラーに何か書かれた場合も、そこで走査をやめて通す）。gitleaks は git を PATH から起動するので、実際の git を
# 呼んで git log の終了コードと標準エラーの有無を記録する代わりの git を、gitleaks にだけ PATH の先頭で渡す。走査が最後まで
# 終わった記録（終了コード 0・標準エラーなし）がなければ、検出なしでも失敗にする
real_git=$(command -v git)
case "$real_git" in /*) ;; *) echo "NG: git の場所を特定できない（${real_git}）" >&2; exit 1 ;; esac
shim=$(mktemp -d)
trap 'status=$?; rm -rf -- "$shim" || true; exit "$status"' EXIT
trap 'exit 130' INT TERM
cat > "$shim/git" <<'SHIM'
#!/bin/bash
# gitleaks が走査に使う「git … log …」だけを記録する（ほかの git の呼び出しはそのまま渡す）
is_log=""
for argument in "$@"; do if [ "$argument" = log ]; then is_log=1; fi; done
if [ -z "$is_log" ]; then exec "$MINUTES_REAL_GIT" "$@"; fi
echo "started" >> "$MINUTES_GIT_RECORD"
"$MINUTES_REAL_GIT" "$@" 2> "$MINUTES_GIT_RECORD.stderr"
status=$?
cat "$MINUTES_GIT_RECORD.stderr" >&2
if [ "$status" = 0 ] && [ ! -s "$MINUTES_GIT_RECORD.stderr" ]; then echo "complete" >> "$MINUTES_GIT_RECORD"; fi
exit "$status"
SHIM
chmod +x "$shim/git"
result=0
# --redact: 検出した値そのものを出力に残さない
PATH="$shim:$PATH" MINUTES_REAL_GIT="$real_git" MINUTES_GIT_RECORD="$shim/record" "$TOOLS_DIR/bin/gitleaks" git --no-banner --no-color --redact --config "$ROOT/.gitleaks.toml" \
  "${GITLEAKS_SUPPRESSION[@]}" --log-opts="$GITLEAKS_LOG_OPTS" ${REPORT[@]+"${REPORT[@]}"} "$repository" || result=$?
# git log は 1 回だけ起動され、最後まで終わっていること（起動の記録がない・途中で終わった・標準エラーに書いたものは失敗）
if [ "$(cat "$shim/record" 2>/dev/null)" != $'started\ncomplete' ]; then
  echo "NG: gitleaks の走査中の git log が最後まで終わらなかった（失敗・中断・標準エラーへの出力）。全履歴を検査できていない。壊れた・欠けたオブジェクトなどを git fsck で確かめてから検査し直す" >&2
  if [ "$result" -eq 0 ]; then result=1; fi
fi
exit "$result"
