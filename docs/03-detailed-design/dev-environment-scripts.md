# MinutesMobile 詳細設計書 ── 開発環境（スクリプトの全文）

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版） |
| 作成日 | 2026-11-21 |
| 満たす要件 ID | TST-CI-01〜08、SEC-SUP-01 |
| 位置づけ | [dev-environment.md](dev-environment.md) の分冊。本文が 2,000 行の上限（[../README.md](../README.md) §3）を超えないよう、長いスクリプトの全文のコード片だけを置く。設計の説明・セキュリティ・テスト観点・申し送りの解決は本冊にある |

`scripts/verify-docs.py` は、対応する基本設計の名前に `-<名前>` を付けた文書（本書）を分冊として扱い、各節の冒頭の「満たす要件:」とコード片の照合だけを求める（[dev-environment.md](dev-environment.md) §10）。コード片は `scripts/verify-docs.py --update-snippets` で実ファイルから書き直す。

---

## 1. 開発ツールの導入（`scripts/setup-tools.sh`）

満たす要件: SEC-SUP-01 ／ 決定: BD-DEV-03

説明は [dev-environment.md](dev-environment.md) §3.2。

<!-- file: scripts/setup-tools.sh -->

```bash
#!/bin/bash
# 開発ツール（gh・SwiftLint・SwiftFormat・gitleaks）を tools/versions の版で .tools/ に導入する（BD-DEV-03、SEC-SUP-01）。
#
# 使い方:
#   scripts/setup-tools.sh            導入する（導入済みで版と SHA-256 が一致するものは取得しない）
#   scripts/setup-tools.sh --verify   取得せず、導入済みの版が tools/versions と一致するかだけ確かめる（check.sh の手順 0）
# 試験用の指定（検査の自己試験が使う）: --versions <ファイル>  --dest <ディレクトリ>
#   --download-limit <秒>  gh での取得の上限（1〜300。既定は 300）
#   --probe-limit <秒>  版の表示のコマンドの上限（1〜300。既定は 30）
#   --require "<名前 …>"  台帳に行が要るツールの集合（既定は検査が使う 4 種。一部だけの台帳で試験するときに明示する）
#
# 終了コード: 0 = 一致 / 1 = 不一致・取得や照合の失敗 / 2 = 使い方の誤り
# 前提: curl・shasum・unzip・tar（OS 同梱）と、Xcode 同梱の python3。gh は記録した版を curl で取得して .tools/ に入れ、
# ほかのツールの取得には、その版の gh（.tools/bin/gh）だけを使う（PATH や Homebrew の gh は使わない）
set -euo pipefail

# CDPATH が設定されていると、相対パスの cd が別の場所へ移り、移った先を標準出力に書いて ROOT に混ぜる。外してから解決する
unset CDPATH
# $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が付けた区切りの改行 1 つだけを外す
ROOT=$(cd "$(dirname "$0")/.." && pwd && printf x)
ROOT=${ROOT%x}
ROOT=${ROOT%$'\n'}
VERSIONS="$ROOT/tools/versions"
DEST="$ROOT/.tools"
MODE=install
# gh での取得の全体の上限（秒）。止まった取得でロックを持ち続けない（ロックを待つ上限の 600 秒より短くする）
DOWNLOAD_LIMIT=300
# 版の表示のコマンドの上限（秒）。止まった実行ファイルで、ロックを持ったまま待ち続けない
PROBE_LIMIT=30
# 検査（check.sh）が使うツール。台帳から行が消えても見落とさないよう、台帳の内容ではなくこの集合で必須を決める
REQUIRED="gh swiftlint swiftformat gitleaks"
# 版の確認方法（runtime_version）を持つツール。台帳に現れてよいのはこれだけ（試験は --require で一部だけを求めてよい）
SUPPORTED="gh swiftlint swiftformat gitleaks"

usage() {
  echo "使い方: $0 [--verify] [--versions <ファイル>] [--dest <ディレクトリ>]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --verify) MODE=verify; shift ;;
    --versions) [ $# -ge 2 ] || usage; VERSIONS=$2; shift 2 ;;
    # 相対パスは ./ を付ける（「-」などを cd が直前の場所（OLDPWD）やオプションとして解釈しないように。絶対パスはそのまま）
    --dest) [ $# -ge 2 ] || usage; case "$2" in /*) DEST=$2 ;; *) DEST=./$2 ;; esac; shift 2 ;;
    --download-limit) [ $# -ge 2 ] && [[ $2 =~ ^[1-9][0-9]{0,2}$ ]] && [ "$2" -le 300 ] || usage; DOWNLOAD_LIMIT=$2; shift 2 ;;
    --probe-limit) [ $# -ge 2 ] && [[ $2 =~ ^[1-9][0-9]{0,2}$ ]] && [ "$2" -le 300 ] || usage; PROBE_LIMIT=$2; shift 2 ;;
    --require) [ $# -ge 2 ] && [ -n "$2" ] || usage; REQUIRED=$2; shift 2 ;;
    *) usage ;;
  esac
done

fail() {
  echo "NG: $*" >&2
  exit 1
}

[ -f "$VERSIONS" ] || fail "版の写しがない: $VERSIONS"

# コマンドを時間の上限（秒。$1）つきで実行する。上限を過ぎたら止め、終わるのを待ってから 124 を返す（中断でも止めて待つ）。
# 起動したコマンドの子孫も止められるよう別のプロセスグループで実行し、終わったらグループごと止める（残った子孫が標準出力を
# 持ち続けると、呼び出し側の $(…) が上限を過ぎても戻らない）。bash 3.2 は前面のコマンドを待つ間に trap を動かさないので python3 で待つ
run_limited() {
  /usr/bin/python3 -I -c '
import os
import signal
import subprocess
import sys

def stop(number, frame):
    sys.exit(128 + number)

# 無視する指定（trap の空の指定）を引き継いだ中断は、無視のままにする
for number in (signal.SIGTERM, signal.SIGHUP):
    if signal.getsignal(number) != signal.SIG_IGN:
        signal.signal(number, stop)
try:
    child = subprocess.Popen(sys.argv[2:], start_new_session=True)
except OSError as error:
    print(f"NG: 起動できない: {error}", file=sys.stderr)
    sys.exit(127)
try:
    code = child.wait(timeout=int(sys.argv[1]))
except subprocess.TimeoutExpired:
    print(f"NG: {sys.argv[1]} 秒を過ぎても終わらないので止めた", file=sys.stderr)
    code = 124
finally:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # グループに何も残っていない（macOS は、終了を待たれていないものだけのグループに PermissionError を返す）
    child.wait()
sys.exit(code if code >= 0 else 128 - code)' "$@"
}

# 実行時の版を表示するコマンド。ツールを足すときはここにも足す（未定義のツールは失敗させる）。
# 呼び出し側は $(…) の中で実行するので set -e が効かない。表示のコマンドの失敗（PROBE_LIMIT を過ぎた場合を含む）は非 0 で返し、版を
# 出力しない（導入では導入済みと見なさず、照合では失敗になる）
runtime_version() {
  local name=$1 binary=$2 out
  case "$name" in
    # 標準入力は /dev/null にする（呼び出し側は版の写しを読んでいる途中で、版のコマンドに後続の行を読ませない）
    swiftlint) out=$(run_limited "$PROBE_LIMIT" "$binary" version </dev/null) || return 1 ;;
    swiftformat) out=$(run_limited "$PROBE_LIMIT" "$binary" --version </dev/null) || return 1 ;;
    gitleaks) out=$(run_limited "$PROBE_LIMIT" "$binary" version </dev/null) || return 1 ;;
    gh) out=$(run_limited "$PROBE_LIMIT" "$binary" --version </dev/null) || return 1; out=${out#gh version }; out=${out%% *} ;;  # 1 行目「gh version 2.104.0 (日付)」
    *) echo "NG: 版の確認方法が未定義のツール: $name" >&2; return 1 ;;
  esac
  printf '%s\n' "${out#v}"
}

# 外部入力（版の写し）は形式を確かめてから使う
check_field() {
  local label=$1 value=$2 pattern=$3
  [[ $value =~ $pattern ]] || fail "tools/versions の${label}が不正: '$value'"
}

# 版のディレクトリ（$DEST/<名前>/<版>）の実体が、導入先の実体の中のその位置そのものであること（<名前>・<版> のディレクトリが
# 外の共有のディレクトリなどへのリンクなら認めない。共有の実体は、導入のロックの外で差し替えられうる）。導入先自体への
# リンク（--dest のリンク）と、bin の相対リンクには関わらない
contained_install() {
  local base actual
  # 導入先のパスの末尾の改行も保つ（ROOT と同じ。actual は版の名前で終わる）
  base=$(cd -P "$DEST" 2>/dev/null && pwd && printf x) || return 1
  base=${base%x}
  base=${base%$'\n'}
  actual=$(cd -P "$DEST/$1/$2" 2>/dev/null && pwd) || return 1
  [ "$actual" = "$base/$1/$2" ]
}

# 版ディレクトリの中の実行ファイルが、シンボリックリンクではない通常のファイルで、実行できること
# （導入先の中のリンクで別の場所の実行ファイルを指させない）
regular_binary() {
  local file=$1
  [ -f "$file" ] && [ ! -L "$file" ] && [ -x "$file" ]
}

# ディレクトリ $2 の実体のパスを変数 $1 に入れる。$(…) は末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が
# 付けた区切りの改行 1 つだけを外す（名前が改行で終わる別のディレクトリを取り違えない）
real_directory() {
  local resolved_path
  resolved_path=$(cd -P -- "$2" 2>/dev/null && pwd && printf x) || return 1
  resolved_path=${resolved_path%x}
  printf -v "$1" '%s' "${resolved_path%$'\n'}"
}

verify_tool() {
  local name=$1 version=$2 binary=$3 sha=$4
  # 公開するリンクの名前はツールの名前（検査が呼ぶ名前）。リンク先は配布ファイルの中の実行ファイル（$binary）
  local link="$DEST/bin/$name" stamp="$DEST/$name/$version/.archive-sha256" actual
  [ -x "$link" ] || fail "$name が導入されていない。scripts/setup-tools.sh を実行する"
  # 実行するのは、導入記録のある版のディレクトリの中の実行ファイルに限る（リンクの先を別の導入に差し替えさせない）。
  # bash 3.2 の macOS には readlink -f がないので、リンクの先のディレクトリを cd -P で実体に解決して比べる
  # リンク先は 1 回だけ、末尾の改行も含めて読み、ディレクトリと名前の照合に同じ値を使う
  local target target_path target_dir expected_dir
  target=$(readlink -n -- "$link" && printf x) || fail "$name のリンク（${link}）の先を解決できない。scripts/setup-tools.sh を実行し直す"
  target=${target%x}
  case "$target" in /*) target_path=$target ;; *) target_path="$DEST/bin/$target" ;; esac
  real_directory target_dir "${target_path%/*}/" || fail "$name のリンク（${link}）の先を解決できない。scripts/setup-tools.sh を実行し直す"
  real_directory expected_dir "$DEST/$name/$version" \
    || fail "$name $version の導入先がない。scripts/setup-tools.sh を実行し直す"
  contained_install "$name" "$version" \
    || fail "$name $version のディレクトリの実体が導入先の中にない（リンクなど）。scripts/setup-tools.sh を実行し直す"
  [ "$target_dir" = "$expected_dir" ] && [ "${target##*/}" = "$binary" ] \
    || fail "$name のリンク（${link}）が $name $version の導入先を指していない。scripts/setup-tools.sh を実行し直す"
  regular_binary "$DEST/$name/$version/$binary" \
    || fail "$name $version の実行ファイル（$DEST/$name/$version/${binary}）が通常のファイルでない。scripts/setup-tools.sh を実行し直す"
  [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$sha" ] \
    || fail "$name $version の導入記録が tools/versions の SHA-256 と一致しない。scripts/setup-tools.sh を実行し直す"
  actual=$(runtime_version "$name" "$link") || fail "$name の版を表示できない（${link}）。scripts/setup-tools.sh を実行し直す"
  [ "$actual" = "$version" ] \
    || fail "$name の版が違う（tools/versions: $version / 実行時: ${actual}）。scripts/setup-tools.sh を実行し直す"
  echo "OK: $name $version"
}

# 展開した配布ファイル（$1/extract）の通常のファイルを、残りの引数の条件で探し、件数を FOUND_COUNT、最後のパスを FOUND_PATH に
# 入れる。NUL で区切って読む（パスの改行で件数を数え違えない）。find の失敗は、見つからないことと区別して非 0 を返す
find_paths() {
  local work=$1 path
  shift
  FOUND_COUNT=0
  FOUND_PATH=""
  find "$work/extract" "$@" -type f -print0 > "$work/found" || return 1
  while IFS= read -r -d '' path; do
    FOUND_COUNT=$((FOUND_COUNT + 1))
    FOUND_PATH=$path
  done < "$work/found"
}

install_tool() {
  local name=$1 version=$2 repo=$3 tag=$4 asset=$5 binary=$6 sha=$7
  local target="$DEST/$name/$version" work="$WORK/$name" actual found cached=""
  # ツールのディレクトリがリンクなら、リンクの先の共有の場所を書き換えずに止める
  [ ! -L "$DEST/$name" ] || fail "${DEST}/${name} がシンボリックリンク。削除してから実行し直す"
  # 導入記録の SHA-256 に加え、実行時の版も照合する（verify_tool と同じ確認方法。違えば取得し直す）。
  # 版の表示に失敗した導入は、表示の内容によらず導入済みとして扱わない
  if contained_install "$name" "$version" && regular_binary "$target/$binary" && [ -f "$target/.archive-sha256" ] && [ "$(cat "$target/.archive-sha256")" = "$sha" ]; then
    cached=$(runtime_version "$name" "$target/$binary" 2>/dev/null) || cached=""
  fi
  if [ -n "$cached" ] && [ "$cached" = "$version" ]; then
    echo "導入済み: $name $version"
  else
    echo "取得: $name ${version}（$repo $tag ${asset}）"
    # 新しい導入は、置き換えを rename だけで済ませるため導入先と同じファイルシステムの $DEST の下で組み立てる
    mkdir -p "$work/download" "$work/extract" "$work/out"
    # 標準入力は版の写しを読んでいる途中なので、取得のコマンドに渡さない
    if [ "$name" = gh ]; then
      # gh 自身は、公式のリリースの固定の URL から取得する（HTTPS だけ。リダイレクト先も HTTPS に限る）。
      # ほかのツールは、GH_HOST の設定に取得先が左右されないよう、--repo にホスト（github.com）を明示する
      # 止まった転送でロックを持ち続けないよう、接続と転送の全体に上限（秒）を付ける（ロックを待つ上限の 600 秒より短くする）
      curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 30 --max-time 300 \
        -o "$work/download/$asset" "https://github.com/$repo/releases/download/$tag/$asset" </dev/null \
        || fail "$name の配布ファイルを取得できない（$repo $tag ${asset}）"
    else
      run_limited "$DOWNLOAD_LIMIT" "$DEST/bin/gh" release download "$tag" --repo "github.com/$repo" --pattern "$asset" --dir "$work/download" </dev/null \
        || fail "$name の配布ファイルを取得できない（$repo $tag ${asset}）"
    fi
    # 標準入力から読む（パスを渡すと、改行などを含むパスで shasum が行頭に「\」を付け、値の照合に失敗する）
    actual=$(shasum -a 256 < "$work/download/$asset" | awk '{print $1}')
    # 一致しない配布ファイルは展開しない
    [ "$actual" = "$sha" ] \
      || fail "$name の SHA-256 が一致しない（tools/versions: $sha / 取得したファイル: ${actual}）。展開しない"
    case "$asset" in
      *.zip) unzip -q "$work/download/$asset" -d "$work/extract" ;;
      *.tar.gz) tar -xzf "$work/download/$asset" -C "$work/extract" ;;
      *) fail "$name の配布ファイルの形式に対応していない: $asset" ;;
    esac
    # 実行ファイルは最上位か、配布ファイルの中のどこか 1 か所（gh は gh_{版}_macOS_{arch}/bin/gh）
    found="$work/extract/$binary"
    if [ ! -f "$found" ]; then
      find_paths "$work" -name "$binary" || fail "$name の配布ファイルを調べられない"
      [ "$FOUND_COUNT" -gt 0 ] || fail "$name の配布ファイルに実行ファイル $binary がない"
      [ "$FOUND_COUNT" = 1 ] || fail "$name の配布ファイルに $binary が複数ある"
      found=$FOUND_PATH
    fi
    cp "$found" "$work/out/$binary"
    chmod 755 "$work/out/$binary"
    find_paths "$work" -maxdepth 2 -name LICENSE || fail "$name の配布ファイルを調べられない"
    if [ "$FOUND_COUNT" = 1 ]; then cp "$FOUND_PATH" "$work/out/LICENSE"; fi
    printf '%s\n' "$sha" > "$work/out/.archive-sha256"
    # 既存の導入を消す前に、取得したものが記録した版を名乗ることを確かめる（違えば既存の導入を残して失敗する）
    actual=$(runtime_version "$name" "$work/out/$binary") || fail "取得した $name の版を表示できない。既存の導入は残す"
    [ "$actual" = "$version" ] \
      || fail "取得した $name の版が違う（tools/versions: $version / 取得したもの: ${actual}）。既存の導入は残す"
    # 置き換えが終わるまで旧い導入を残す。旧い導入を退避 → 新しい導入を置く → 失敗したら退避したものを戻す → 退避を消す
    mkdir -p "$DEST/$name"
    local backup=""
    if [ -e "$target" ]; then
      backup="$DEST/$name/.$version.old.$$"
      rm -rf -- "${backup:?}"
      # 中断（INT・TERM）されても終了処理が旧い導入を戻せるよう、退避の前に場所を記録する
      PENDING_TARGET=$target
      PENDING_BACKUP=$backup
      mv "$target" "$backup" || fail "$name の既存の導入を退避できない"
    fi
    if ! mv "$work/out" "$target"; then
      if [ -n "$backup" ]; then mv "$backup" "$target" || echo "NG: 退避した $name の導入を戻せない（${backup}）" >&2; fi
      fail "$name の導入を置き換えられない。既存の導入は残す"
    fi
    # 退避した旧い導入は、公開するリンクの更新が成功するまで消さない（下の publish_link。失敗したら戻す）
  fi
  # リンクの置き換えから巻き戻しの解除までの中断は控え、解除の後で従う（公開した新しい導入を、終了処理が巻き戻さない）。
  # 置き換えに失敗した場合は、解除しないまま publish_link が止めるので、退避した旧い導入に戻る
  interrupted=""
  trap 'interrupted=1' INT TERM
  publish_link "$name" "$version" "$binary"
  # リンクを更新できたので、先に終了処理の巻き戻しを解いてから（場所の印を外してから）退避した旧い導入を消す。逆の順では、
  # 削除の失敗・中断で終了処理が、一部を消した旧い導入を公開済みの新しい導入と置き換えてしまう。PENDING_BACKUP を先に外す
  # （restore_backup はこれが空なら何もしない）。削除の失敗は場所とともに報告し、公開した導入はそのまま使う
  local retired=$PENDING_BACKUP
  PENDING_BACKUP=""
  PENDING_TARGET=""
  trap 'exit 130' INT TERM
  if [ -n "$interrupted" ]; then
    if [ -n "$retired" ]; then echo "警告: 中断したため、退避した旧い $name の導入（${retired}）が残った。新しい導入はそのまま使う。手で削除する" >&2; fi
    exit 130
  fi
  if [ -n "$retired" ]; then
    rm -rf -- "$retired" || echo "警告: 退避した旧い $name の導入を削除できない（${retired}）。新しい導入はそのまま使う。手で削除する" >&2
  fi
}

# 置き換えの途中で失敗したとき、置いた新しい導入をどけて、退避した旧い導入を元の場所に戻す（既存のリンクは同じ版の
# ディレクトリを指すので、戻せばリンクの先も元の導入になる）。退避していなければ何もしない
restore_backup() {
  [ -n "$PENDING_BACKUP" ] && [ -e "$PENDING_BACKUP" ] || return 0
  rm -rf -- "${PENDING_TARGET:?}" || { echo "NG: 置いた導入をどけられない（${PENDING_TARGET}）" >&2; return 1; }
  mv "$PENDING_BACKUP" "$PENDING_TARGET" || { echo "NG: 退避した導入を戻せない（${PENDING_BACKUP}）" >&2; return 1; }
  PENDING_TARGET=""
  PENDING_BACKUP=""
}

# 公開するリンク（$DEST/bin/<名前>）を、導入先の実行ファイルに向ける。新しいリンクを同じディレクトリの一時の名前で作り、
# rename（mv）で置き換える（途中で失敗しても既存のリンクを壊さない）。失敗したら退避した旧い導入を戻して止める
publish_link() {
  local name=$1 version=$2 binary=$3 link="$DEST/bin/$1" temporary="$DEST/bin/.$1.link.$$"
  if ! mkdir -p "$DEST/bin"; then restore_backup || true; fail "$DEST/bin を作れない。既存の導入は残す"; fi
  # 公開するリンクの位置に実体のディレクトリがあると、mv はその中にリンクを移し、位置のリンクを更新しない。
  # 既存のシンボリックリンクは更新してよい
  if [ -d "$link" ] && [ ! -L "$link" ]; then
    restore_backup || true
    fail "$link がディレクトリ（シンボリックリンクでない）。削除してから実行し直す"
  fi
  # ディレクトリを指すリンクは、mv がリンクの先のディレクトリの中へ移してしまうので、先に外す（壊れた導入の場合だけ）
  if [ -L "$link" ] && [ -d "$link" ]; then rm -f -- "$link"; fi
  rm -f -- "$temporary"
  # mv は中断を無視して実行する（置き換えた直後に止められて、失敗と取り違えられないようにする）
  if ! ln -s "../$name/$version/$binary" "$temporary" || ! (trap '' INT TERM; exec mv -f -- "$temporary" "$link"); then
    rm -f -- "$temporary"
    restore_backup || true
    fail "$name の公開するリンク（${link}）を更新できない。既存の導入とリンクは残す"
  fi
}

# 置き換えの途中の導入先と退避先（install_tool が設定し、置き換えが終わったら空に戻す）
PENDING_TARGET=""
PENDING_BACKUP=""

# 導入（install）は作業場所を導入先と同じファイルシステムに作る（置き換えの mv を rename にする）。
# 照合（--verify）は導入先を作らない（導入先がなければ照合で「導入されていない」として失敗する）
if [ "$MODE" = install ]; then mkdir -p "$DEST"; fi
if [ -d "$DEST" ]; then
  # 同じ導入先への並行の導入を防ぐ。キャッシュの判定から退避・公開・リンクの更新までをロックの中で行う。
  # 後から来た実行はロックを待ち、取得してからキャッシュを判定する（先の実行が導入したものはそのまま使う）。
  # 照合も同じロックを取り、台帳との照合を終えるまで持つ（導入の途中の、旧い導入を退避した状態などを照合しない）
  LOCK="$DEST/.install.lock"
  LOCK_HELD=""
  WORK=""
  # ロックを待つ前に終了処理を登録する（取得の直後や作業場所の作成の途中で中断されても、ロックを残さない）。
  # 解放するのは LOCK_HELD が立った後、つまり自分が取得したロックだけ
  cleanup() {
    local status=$?
    # 置き換えの途中（公開するリンクの更新が成功する前）で終わった場合: 新しい導入を置いていればどけて、退避した旧い導入を
    # 戻す（退避はリンクの更新が成功した後にだけ消す）。失敗は restore_backup がパスとともに記録し、終了処理は止めない
    restore_backup || true
    if [ -n "$WORK" ]; then rm -rf -- "${WORK:?}" || echo "NG: 作業場所を削除できない（${WORK}）" >&2; fi
    # 自分が取得したロックだけを解放する（ロックを待っている間に終わった場合は、他の実行のロックを消さない）
    if [ -n "$LOCK_HELD" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
    # 終了コードを明示的に返す（bash 3.2 は、trap の最後のコマンドの状態でエラー終了を上書きすることがある）
    exit "$status"
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  # ロックを取る。取れたら LOCK_HELD に記録して 0 を返す。結果を記録するまで、中断は控えるだけにする（check.sh の take_lock と
  # 同じ。取った直後・記録の前に終了してロックを残さない。取れなかった場合に、ほかの実行のロックを消さない）。mkdir は中断を
  # 無視して実行し、作った直後に止められて失敗と取り違えられないようにする。控えた中断には、記録の後で従う
  take_install_lock() {
    interrupted=""
    trap 'interrupted=1' INT TERM
    if lock_error=$(trap '' INT TERM; exec mkdir "$LOCK" 2>&1); then LOCK_HELD=1; fi
    trap 'exit 130' INT TERM
    if [ -n "$interrupted" ]; then exit 130; fi
    [ -n "$LOCK_HELD" ]
  }
  # 作れない理由を区別する。ロックのディレクトリがあるときだけ別の導入の実行中として待ち、
  # ないのに作れない場合（書き込めない導入先など）や、ディレクトリ以外のものがある場合は待たずに止める
  waited=0
  retried=""
  until take_install_lock; do
    if [ -d "$LOCK" ] && [ ! -L "$LOCK" ]; then
      if [ "$waited" -ge 600 ]; then
        fail "別の導入が実行中（${LOCK}）。実行中でなければ、そのディレクトリを削除してから実行し直す"
      fi
      # 待ち始めたことを 1 回だけ知らせる（利用者と自己試験が、ロックの競合を観測したことを確かめられるようにする）
      if [ "$waited" -eq 0 ]; then echo "待機: 別の導入・照合が実行中（${LOCK}）。終わるのを待つ" >&2; fi
      sleep 1
      waited=$((waited + 1))
      retried=""
    elif [ ! -e "$LOCK" ] && [ ! -L "$LOCK" ] && [ -z "$retried" ]; then
      retried=1  # 失敗の直後に先の実行がロックを解放した場合に備え、待たずに 1 回だけ作り直す
    else
      fail "ロック（${LOCK}）を作れない: ${lock_error}"
    fi
  done
  if [ "$MODE" = install ]; then
    # 公開するリンクの置き場所（bin）がリンクなら、リンクの先の共有の場所を書き換えずに、導入を変える前に止める
    # （導入先自体のリンクは認める）
    [ ! -L "$DEST/bin" ] || fail "${DEST}/bin がシンボリックリンク。削除してから実行し直す"
    WORK=$(mktemp -d "$DEST/.work.XXXXXX") || fail "作業場所を作れない（${DEST}）"
  fi
fi

ARCH=$(uname -m)
seen=" "      # 写しに現れたツール名
matched=" "   # この Mac 向けの行が見つかったツール名
# この Mac 向けの行（台帳の順）。導入・照合は、台帳の全体の検証を終えてから行う
ROWS_NAME=()
ROWS_VERSION=()
ROWS_TAG=()
ROWS_REPO=()
ROWS_ASSET=()
ROWS_BINARY=()
ROWS_SHA=()

# 台帳の全体を先に検証する（列の形式・アーキテクチャ・重複・gh の行の順序・必須のツール）。途中の行が不正な台帳で、
# 前の行のツールを導入したり公開するリンクを更新したりしない。
# 末尾に改行のない最終行も読む（read は非ゼロを返すが、値は入っている）。空の最終行は name が空なので読まない
while read -r name version arch repo tag asset binary sha extra || [ -n "${name:-}" ]; do
  case "$name" in '' | '#'*) continue ;; esac
  [ -z "${extra:-}" ] || fail "tools/versions の列が多い: $name"
  check_field "名前" "$name" '^[a-z][a-z0-9-]*$'
  # 版の確認方法のないツールは、導入・公開するリンクの更新を始める前（台帳の検証の中）で止める
  case " $SUPPORTED " in *" $name "*) ;; *) fail "tools/versions のツール $name は版の確認方法が定義されていない（対応: ${SUPPORTED}）" ;; esac
  check_field "版" "${version:-}" '^[0-9]+(\.[0-9]+)*$'
  check_field "アーキテクチャ" "${arch:-}" '^(any|arm64|x86_64)$'
  check_field "リポジトリ" "${repo:-}" '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
  check_field "タグ" "${tag:-}" '^v?[0-9]+(\.[0-9]+)*$'
  check_field "配布ファイル" "${asset:-}" '^[A-Za-z0-9_.-]+$'
  # 展開できる形式だけを認める（展開のときの判定は、二重の確認として残す）
  case "$asset" in *.zip | *.tar.gz) ;; *) fail "tools/versions の配布ファイルの形式に対応していない: $asset" ;; esac
  check_field "実行ファイル" "${binary:-}" '^[a-z][a-z0-9-]*$'
  check_field "SHA-256" "${sha:-}" '^[0-9a-f]{64}$'
  case "$seen" in *" $name "*) ;; *) seen="$seen$name " ;; esac
  if [ "$arch" != any ] && [ "$arch" != "$ARCH" ]; then continue; fi
  case "$matched" in *" $name "*) fail "tools/versions に $name の行が重複している（$ARCH 向け）" ;; esac
  # ほかのツールは、記録した版の gh で取得する。そのため gh の行を先に置き、先に導入・照合する
  if [ "$name" != gh ]; then
    case "$matched" in *" gh "*) ;; *) fail "tools/versions で、$ARCH 向けの gh の行が $name の行より前にない" ;; esac
  fi
  matched="$matched$name "
  ROWS_NAME+=("$name")
  ROWS_VERSION+=("$version")
  ROWS_REPO+=("$repo")
  ROWS_TAG+=("$tag")
  ROWS_ASSET+=("$asset")
  ROWS_BINARY+=("$binary")
  ROWS_SHA+=("$sha")
done < "$VERSIONS"

[ "$seen" != " " ] || fail "tools/versions にツールが 1 つもない"
# 台帳に現れたツールと、検査が使う必須のツールの両方について、この Mac 向けの行が 1 つずつあること
# （重複は上の検証で失敗させている）
for name in $seen $REQUIRED; do
  case "$matched" in *" $name "*) ;; *) fail "$name に $ARCH 向けの行がない" ;; esac
done

# 検証を終えた行だけを、台帳の順（gh が先）に導入・照合する。標準入力は使わない（取得のコマンドは </dev/null で実行する）
index=0
while [ "$index" -lt ${#ROWS_NAME[@]} ]; do
  if [ "$MODE" = install ]; then
    install_tool "${ROWS_NAME[$index]}" "${ROWS_VERSION[$index]}" "${ROWS_REPO[$index]}" "${ROWS_TAG[$index]}" \
      "${ROWS_ASSET[$index]}" "${ROWS_BINARY[$index]}" "${ROWS_SHA[$index]}"
  fi
  verify_tool "${ROWS_NAME[$index]}" "${ROWS_VERSION[$index]}" "${ROWS_BINARY[$index]}" "${ROWS_SHA[$index]}"
  index=$((index + 1))
done
echo "OK: 開発ツールの版は tools/versions と一致"
```

---

## 2. 検査スクリプト（`scripts/check.sh`）

満たす要件: TST-CI-01〜08

説明は [dev-environment.md](dev-environment.md) §4。

<!-- file: scripts/check.sh -->

```bash
#!/bin/bash
# ローカルの検査をまとめて実行する（基本設計 dev-environment.md §6、TST-CI-01〜08）。
#
# 使い方:
#   scripts/check.sh                手順 0〜10 を順に実行する（PR の作成前）
#   scripts/check.sh --steps 0-8,10 指定した手順だけを実行する（プッシュ前のフックが使う）
#   scripts/check.sh --staged       ステージしたファイルに対する手順 1〜3 と 10（コミット前のフックが使う）。
#                                   手順 1・2・10 は作業ツリーではなくステージした内容（インデックス）を検査する
#   check.sh --root <リポジトリ> --staged コミット前のフックが、ステージした check.sh を一時ファイルに書き出して実行するときに使う。
#                                   最初の引数に置き、検査するリポジトリの起点を絶対パスで渡す（--staged 以外とは組み合わせない）
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
# パス指定の解釈を変える変数（git --literal-pathspecs commit などのフックに引き継がれる）を外す。残すと '*.swift' などの指定が
# 別の意味になり、ステージしたファイルを 1 つも選ばずに合格しうる。集めた名前での照会は --literal-pathspecs を付ける
unset GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS
# --root は、このスクリプトの置き場所によらず検査するリポジトリの起点を示す（書き出したステージした版を実行するとき）
ROOT_GIVEN=0
if [ "${1:-}" = --root ]; then
  case "${2:-}" in /*) ;; *) echo "使い方: --root には絶対パスを渡す" >&2; exit 2 ;; esac
  # $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が付けた区切りの改行 1 つだけを外す
  ROOT=$(cd -P -- "$2" && pwd && printf x) && ROOT=${ROOT%x} && ROOT=${ROOT%$'\n'} || exit 1
  ROOT_GIVEN=1
  shift 2
else
  ROOT=$(cd "$(dirname "$0")/.." && pwd && printf x) && ROOT=${ROOT%x} && ROOT=${ROOT%$'\n'} || exit 1
fi
# 開発ツールの写しの場所は、この実行が照合した写しだけを子に渡す（snapshot_run_tools）。引き継いだ値は使わない
unset MINUTES_TOOLS_DIR
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
# 2 行目は測った結果のカバレッジの JSON（COVERAGE_JSON）の SHA-256（手順 7 と 8 の間に JSON が書き換えられていないこと）、
# 3 行目から末尾までは測った Sources の実体のパス（JSON のパスの起点。測った後に作業ツリーを移しても、測ったものを数える）
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
STAGED_HEAD=""  # --staged のとき、開始時の HEAD のコミット（HEAD がまだないなら空。終了時に同じであることを確かめる）
STAGED_BASE=""  # --staged のとき、ステージしたファイルを選ぶ基点（開始時の HEAD。HEAD がまだないなら空のツリー）
STAGED_TOOLS=""  # --staged のとき、開発ツールの写しと、照合に使うステージした台帳を置く一時ディレクトリ
RUN_TOOLS=""  # 通常の実行が手順 1〜3 を含むとき、開発ツールの写しを置く一時ディレクトリ
STAGED_INDEX_COPY=""  # --staged のとき、開始時のインデックスのツリーから作った専用のインデックス（検査はこれだけを読む）
ORIGINAL_INDEX=""  # --staged のとき、本来のインデックスのファイル（終了時に開始時のツリーのままであることを確かめる）
ISOLATED_BASE=""  # --require-clean のとき、開始時のコミットを展開する専用の作業ツリーを置く一時ディレクトリ
ISOLATED_TREE=""  # その専用の作業ツリー（git worktree で登録する）
ISOLATED_COMMON=""  # その専用の作業ツリーを登録したリポジトリの共通のディレクトリ（後始末で登録ごと消す）
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
  # 「,」で分けるだけで、ファイル名の展開はしない（「[0-9]」などを実行時の場所のファイルの名前に置き換えて、番号として読まない）。
  # 呼び出しは $(…) の中なので、set -f は呼び出し側に残らない
  set -f
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
if [ "$ROOT_GIVEN" = 1 ]; then
  [ "$STAGED" = 1 ] || usage
  # 起点でないディレクトリ（下の階層・リポジトリの外）を検査しない
  # （パスの末尾の改行は上と同じく保つ。cd に失敗した場合も一致させない）
  toplevel=$(git rev-parse --show-toplevel && printf x) && toplevel=${toplevel%x} && toplevel=${toplevel%$'\n'} || fail "git の作業ツリーを特定できない"
  selected=$(cd -P -- "$toplevel" && pwd && printf x) && selected=${selected%x} && selected=${selected%$'\n'} || fail "作業ツリー ${toplevel} を解決できない"
  [ "$selected" = "$ROOT" ] || fail "--root（${ROOT}）がリポジトリの起点でない"
fi
if [ "$STAGED" = 1 ]; then
  # --staged はインデックスを検査する。作業ツリーの一致を求める --require-clean とは組み合わせない
  [ "$STEPS_GIVEN" = 0 ] && [ "$REQUIRE_CLEAN" = 0 ] && [ -z "$EXPECT_HEAD" ] || usage
  STEPS="1 2 3 10"
elif [ "$STEPS_GIVEN" = 0 ]; then
  STEPS=$(expand_steps "0-$LAST_STEP")
fi

# 終了処理は、一時のファイル（--staged の専用のインデックスなど）を作る前に登録する（手順を始める前に止めた場合も残さない）
current=""  # 実行中の手順の番号（手順の外では空。on_exit が失敗した手順の表示に使う）
# 持ち主の印を先に外してからロックを外す（rmdir の途中で中断されても、後始末が別の実行の取り直した
# ロックを消さない。中断でロックが残った場合は、残骸として案内のとおり削除する）
release_coverage_lock() {
  local held=$COVERAGE_LOCK_HELD
  COVERAGE_LOCK_HELD=""
  if [ -n "$held" ]; then rmdir "$COVERAGE_LOCK" 2>/dev/null || true; fi
}
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
  if [ -n "$RUN_TOOLS" ]; then remove_or_report "$RUN_TOOLS"; fi
  if [ -n "$STAGED_INDEX_COPY" ]; then remove_or_report "$STAGED_INDEX_COPY"; fi
  if [ -n "$COVERAGE_SCRATCH" ]; then remove_or_report "$COVERAGE_SCRATCH"; fi
  if [ -n "$COVERAGE_COPY" ]; then remove_or_report "$COVERAGE_COPY"; fi
  if [ -n "$TOOLS_LOCK" ]; then rmdir "$TOOLS_LOCK" 2>/dev/null || true; fi
  # この実行で作った専用の作業ツリーだけを登録ごと消す。リポジトリ全体の git worktree prune は実行しない（ほかの作業ツリーの
  # 登録を消さない）。消せなければ場所を報告して失敗にし、置き場所も残す（中身を確かめて git worktree remove で消せるように）
  isolated_removed=1
  # 作成の前に失敗した場合（ディレクトリがない）は消すものがない
  if [ -n "$ISOLATED_TREE" ] && { [ -e "$ISOLATED_TREE" ] || [ -L "$ISOLATED_TREE" ]; } \
    && ! git --git-dir="$ISOLATED_COMMON" worktree remove --force "$ISOLATED_TREE"; then
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
  # ステージしたファイルを選ぶ基点を開始時に 1 回だけ決め、3 か所の git diff --cached で同じ基点を使う（検査の途中に HEAD が
  # 変わっても、選ぶファイルの集合を変えない）。HEAD がまだない（最初のコミットの前）場合は空のツリーを基点にし、
  # ステージしたファイルをすべて選ぶ。HEAD が読めない理由がまだないこと以外（壊れた参照など）なら止める
  if STAGED_HEAD=$(git rev-parse --verify -q HEAD); then
    STAGED_BASE=$STAGED_HEAD
  elif git symbolic-ref -q HEAD > /dev/null && ! git rev-parse --verify -q "$(git symbolic-ref -q HEAD)" > /dev/null; then
    STAGED_HEAD=""
    STAGED_BASE=$(git hash-object -t tree /dev/null) || fail "空のツリーを作れない"
  else
    fail "開始時の HEAD を読めない（まだないのでもない）"
  fi
  # $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と git が付けた区切りの改行 1 つだけを外す
  # （git_path と同じ。GIT_INDEX_FILE があれば、呼び出し側の代わりのインデックスの場所になる）
  ORIGINAL_INDEX=$(git rev-parse --path-format=absolute --git-path index && printf x) || fail "インデックスの場所を特定できない"
  ORIGINAL_INDEX=${ORIGINAL_INDEX%x}
  ORIGINAL_INDEX=${ORIGINAL_INDEX%$'\n'}
  STAGED_INDEX_COPY=$(mktemp)
  rm -f -- "$STAGED_INDEX_COPY"
  GIT_INDEX_FILE="$STAGED_INDEX_COPY" git read-tree "$STAGED_INDEX_TREE" || fail "控えたツリーから専用のインデックスを作れない"
  export GIT_INDEX_FILE="$STAGED_INDEX_COPY"
  staged_list=$(mktemp)
  # 種類の変更（T。通常のファイルとシンボリックリンクの入れ替えなど）も含める（下でインデックスのモードを確かめる）
  if ! git diff --cached --name-only --diff-filter=ACMRT -z "$STAGED_BASE" -- '*.swift' > "$staged_list"; then
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

# $1 の下に、追跡しているパス $2 の親のディレクトリを作る（$(dirname …) はディレクトリの名前の末尾の改行を落とすので使わない）
make_parent() {
  case "$2" in
    */*) mkdir -p -- "$1/${2%/*}" ;;
    *) mkdir -p -- "$1" ;;
  esac
}

# ステージした内容を書き出す一時ディレクトリ（STAGED_TREE）を作る。作った直後、ファイルを書く前に、--require-clean の専用の
# 作業ツリーと同じ判定（scripts/worktree-location.py check）で置き場所を確かめる。TMPDIR が作業ツリーの中や、SwiftFormat の
# 設定（.swiftformat・.swift-version）のある場所の下を指すと、書き出した内容の整形・静的解析に別の設定が混ざるため。
# 認めない・判定できない場合は止める。認めた場所では、書き出した内容の設定（インデックスの .swiftformat など）をそのまま使う。
# 判定の結果のパスは NUL で終わる記録で受け取る（$(…) はパスの末尾の改行を落とすので使わない）
make_staged_tree() {
  local location_out location_script location_status=0 checked="" temporary_root=${TMPDIR:-/tmp}
  # 一時の置き場所は TMPDIR（なければ /tmp）の下に明示的に作る（--require-clean と同じ。mktemp -d の既定は TMPDIR を使わない
  # 環境がある）。判定で止めた場合も、終了処理が消せるように先に控える
  STAGED_TREE=$(mktemp -d "${temporary_root%/}/minutes-staged.XXXXXX")
  # 判定に使うスクリプトもステージしたものを書き出して実行する（ステージしていない作業ツリーの変更で判定を変えない）
  location_script=$(mktemp)
  if ! git cat-file blob :scripts/worktree-location.py > "$location_script"; then
    rm -f -- "$location_script"
    fail "ステージした scripts/worktree-location.py を読めない"
  fi
  location_out=$(mktemp)
  # -I: 書き出した場所（一時ディレクトリ）に置かれた同名のモジュール（subprocess.py など）を読み込ませない
  /usr/bin/python3 -I "$location_script" check "$STAGED_TREE" "$ROOT" > "$location_out" || location_status=$?
  if [ "$location_status" = 0 ] && ! IFS= read -r -d '' checked < "$location_out"; then location_status=2; fi
  rm -f -- "$location_out" "$location_script"
  case $location_status in
    0) STAGED_TREE=$checked ;;
    1) fail "ステージした内容の一時の置き場所（${STAGED_TREE}）を使えない。TMPDIR で作業ツリーの外の、SwiftFormat の設定のない場所を指定する" ;;
    *) fail "ステージした内容の一時の置き場所（${STAGED_TREE}）が作業ツリーの中かを判定できない" ;;
  esac
}

# インデックスの整形・静的解析の設定がシンボリックリンクなら、ツールを実行する前に止める（prepare_staged_tree はリンク先の文字列を
# 設定の内容として書き出し、prepare_rules_tree はリンクのまま書き出すので、どちらもステージした設定で検査できない）。
# ステージした Swift ファイルがなくても、規則の試験が設定を使うので確かめる
reject_staged_config_links() {
  local entries entry path
  entries=$(mktemp)
  if ! git ls-files -s -z -- ':(glob)**/.swiftformat' ':(glob,icase)**/.swiftlint.yml' ':(glob)**/.swift-version' > "$entries"; then
    rm -f -- "$entries"
    fail "ステージした設定の一覧を取得できない"
  fi
  while IFS= read -r -d '' entry; do
    if [ "${entry%% *}" != 120000 ]; then continue; fi
    path=${entry#*$'\t'}
    rm -f -- "$entries"
    fail "${path} はインデックスでシンボリックリンク。整形・静的解析の設定は通常のファイルでステージする"
  done < "$entries"
  rm -f -- "$entries"
}

# ステージした Swift ファイルの内容（作業ツリーではなくインデックス）を、元の相対位置のまま一時ディレクトリへ書き出す。
# 設定（.swiftformat・各階層の .swiftlint.yml）もインデックスから同じ位置へ書き出し、入れ子の設定を作業ツリーと同じに解決させる。
# 書き出すのはインデックスの blob そのもの（git cat-file blob。prepare_staged_secrets_tree と同じ）。checkout-index を通すと
# .gitattributes の smudge のフィルタ・改行の変換が掛かり、整形・静的解析がステージした内容と違うものを検査しうる
prepare_staged_tree() {
  local entries entry mode object path
  make_staged_tree
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
    make_parent "$STAGED_TREE" "$path"
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
    make_parent "$dest" "$path" || return 1
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
# 開発ツールは照合した写し（$STAGED_TOOLS/.tools）を指す（ツールは Git の管理外）
prepare_rules_tree() {
  local directory entries
  [ -n "$STAGED_TREE" ] || make_staged_tree
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
  if ! git diff --cached --name-only --diff-filter=ACMRT -z "$STAGED_BASE" > "$list"; then
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
    make_parent "$STAGED_SECRETS_TREE" "$path"
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
  verify_run_tools
  scripts/test-checks.py versions
}

step_1() {
  if [ "$STAGED" = 1 ]; then
    if [ ${#STAGED_SWIFT[@]} -eq 0 ]; then echo "ステージした Swift ファイルはない"; return 0; fi
    # ./ を付けて渡す（「-」で始まる名前を指定として解釈させない。手順 2 も同じ）
    (cd "$STAGED_TREE" && "$TOOLS/swiftformat" --lint --cache ignore "${STAGED_SWIFT[@]/#/./}")
  else
    "$TOOLS/swiftformat" --lint --cache ignore .
  fi
}

step_2() {
  # --config を付けない（付けると、テストコード用の入れ子の設定が無効になる）
  if [ "$STAGED" = 1 ]; then
    if [ ${#STAGED_SWIFT[@]} -gt 0 ]; then
      (cd "$STAGED_TREE" && "$TOOLS/swiftlint" lint --quiet --no-cache --force-exclude "${STAGED_SWIFT[@]/#/./}")
    else
      echo "ステージした Swift ファイルはない"
    fi
    # 規則・試験用のファイル・入れ子の設定・テストのディレクトリ（入れ子の設定の置き忘れを検査する）を変えたときだけ、
    # 規則の試験を回す。テストのディレクトリの一覧は prepare_rules_tree と scripts/test-lint-rules.py の TEST_CODE_DIRS と同じ
    # 終了コードは 0 = 変更なし / 1 = 変更あり。それ以外は照会の失敗なので、変更があると見なさずに止める
    local lint_changed=0
    git diff --cached --quiet "$STAGED_BASE" -- ':(glob,icase)**/.swiftlint.yml' tools/lint-fixtures scripts/test-lint-rules.py \
      MinutesMobileTests MinutesMobileUITests Packages/MinutesKit/Tests || lint_changed=$?
    case $lint_changed in
      0) return 0 ;;
      1) ;;
      *) fail "規則の試験に関わるステージした変更の有無を調べられない（git diff の終了コード ${lint_changed}）" ;;
    esac
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
  # 専用の作業ツリーの検査（上の isolated_checkout で確かめたもの）では、参照先の plist を、検査するコミットで追跡したものに限る。
  # 環境変数ではなく引数で渡す（自己試験の子のプロセスに引き継がせない）
  if [ "$REQUIRE_CLEAN" = 1 ] && [ -n "${MINUTES_CHECK_ISOLATED:-}" ]; then
    scripts/check-project-settings.py --tracked-only
  else
    scripts/check-project-settings.py
  fi
}

step_5() {
  scripts/test-checks.py dependencies
  scripts/check-dependencies.py
}

# カバレッジを測った入力の識別子。未コミットの変更がなければ HEAD のコミット、あれば作業ツリー全体（追跡していない
# ファイルを含む。無視するファイルは除く）のツリーのハッシュ。作業ツリーの記録はコミットの合格の記録と区別する
coverage_input() {
  local changes index staged tree
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
  # 起点は HEAD ではなく現在のインデックス（git add -f でステージした無視するファイルも測る入力に含める。追跡していない
  # 無視するファイルは git add -A が除く）。未解決の衝突があればツリーを作れず、識別できないものとして失敗する
  if ! staged=$(git write-tree) || ! GIT_INDEX_FILE="$index" git read-tree "$staged" || ! GIT_INDEX_FILE="$index" git add -A \
    || ! tree=$(GIT_INDEX_FILE="$index" git write-tree); then
    rm -f -- "$index"
    return 1
  fi
  rm -f -- "$index"
  printf 'worktree %s\n' "$tree"
}

# 手順 7・8 が書く・消すカバレッジの場所（記録・JSON・測る入力の写し・ロック）と、その祖先（起点まで）のそれぞれが、リンクを
# 解決しても現在の作業ツリーの中にあることを確かめる。.build・$PACKAGE/.build がシンボリックリンクで外を指すと、ロックの作成・
# 写しの rm -rf・記録の置き換えがリンクの先（作業ツリーの外）に及ぶ。満たさない・調べられない場合は非 0 を返す
verify_coverage_paths() {
  /usr/bin/python3 -I - "$ROOT" "$COVERAGE_HEAD" "$COVERAGE_JSON" "$COVERAGE_INPUT" "$COVERAGE_LOCK" <<'PYTHON'
import os
import sys

try:
    root = os.path.realpath(sys.argv[1])
    bad = []
    for relative in sys.argv[2:]:
        path = os.path.join(root, relative)
        # 対象そのものと、起点までの祖先のそれぞれ（ないものは、あるところまでを解決した値）
        while path != root:
            real = os.path.realpath(path)
            if real != root and not real.startswith(root + os.sep):
                bad.append(f"{os.path.relpath(path, root)} → {real}")
                break
            path = os.path.dirname(path)
except (OSError, RuntimeError, ValueError) as error:  # 循環するリンクは RuntimeError（Python 3.12 まで）
    print(f"NG: カバレッジの場所を解決できない: {error!r}", file=sys.stderr)
    sys.exit(2)
for item in bad:
    print(f"NG: カバレッジの場所が作業ツリーの外を指す: {item}", file=sys.stderr)
sys.exit(1 if bad else 0)
PYTHON
}

# カバレッジのロックを取る。待たずに失敗する（別の測定が実行中。残骸なら削除して実行し直す）。取得の途中の中断は take_lock が控える
# 手順 7・8 の共通の入口なので、ロックを取る前（ファイルを作る・消す前）にカバレッジの場所を確かめる
take_coverage_lock() {
  verify_coverage_paths \
    || fail "カバレッジの場所（.build・${PACKAGE}/.build）がシンボリックリンクなどで作業ツリーの外を指す・確かめられない。中身を確かめて直してから実行し直す"
  mkdir -p .build
  take_lock "$COVERAGE_LOCK" COVERAGE_LOCK_HELD \
    || fail "別のカバレッジの測定・集計が実行中（${COVERAGE_LOCK}）。実行中でなければ、そのディレクトリを削除してから実行し直す"
}

# 入力の識別子が指すツリー（$1）から Packages/ を写しの場所へ書き出す。git archive は .gitattributes の export-ignore で
# 試験用のファイルなどを省きうるので使わず、一時のインデックスに読み込んで、追跡しているファイルをすべて blob のまま
# 書き出す（write_index_entries。checkout-index の smudge のフィルタ・改行の変換を通さないので、測る内容は識別子の
# ツリーの内容と一致する）。ローカルの Package とローカルのバイナリは check-dependencies.py が Packages/ の下（字面・実体）に
# 限るので、Packages/ だけを写せば swift test が承認済みのものを解決できる。
# 失敗したら非 0 を返す（呼び出し側は条件の中で呼ぶので、set -e に頼らない）
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

# 書き出した写し（$1）のシンボリックリンクが、すべて写しの中を指すことを確かめる。写しの外を指すリンクを swift test がたどると、
# 記録する入力の識別子に含まれない内容を測ってしまう。リンク先は相対パスに限り、「..」やほかのリンクを経由する途中も写しの中に
# あることを求める（写しの中の相対リンクは認める）。満たさない・調べられない場合は非 0 を返す
verify_coverage_links() {
  /usr/bin/python3 -I - "$1" <<'PYTHON'
import os
import sys

root = os.path.realpath(sys.argv[1])


def inside(path):
    return path == root or path.startswith(root + os.sep)


def contained(link):
    target = os.readlink(link)
    if os.path.isabs(target):
        return False
    # 途中のリンクが外を経由するかは、写しの中のすべてのリンクをこの関数で調べるので、要素ごとの実体だけを見ればよい
    current = os.path.realpath(os.path.dirname(link))
    for part in target.split(os.sep):
        current = os.path.realpath(os.path.join(current, part))
        if not inside(current):
            return False
    return True


def failed(error):
    raise error


bad = []
try:
    for directory, names, files in os.walk(root, onerror=failed):
        for name in names + files:
            path = os.path.join(directory, name)
            if os.path.islink(path) and not contained(path):
                bad.append(path)
except OSError as error:
    print(f"NG: カバレッジの入力の写しを調べられない: {error}", file=sys.stderr)
    sys.exit(1)
for path in bad:
    print(f"NG: カバレッジの入力のシンボリックリンク {os.path.relpath(path, root)!r} が写しの外を指す", file=sys.stderr)
sys.exit(1 if bad else 0)
PYTHON
}

# ロック（ディレクトリ $1）を取り、取れたら変数 $2 に場所を記録して 0 を返す。結果を記録するまで、中断は控えるだけにする
# （.githooks/pre-push と同じ。取った直後・記録の前に終了してロックを残さない。取れなかった場合に、ほかの実行のロックを消さない）。mkdir は中断を無視して
# 実行し、作った直後に止められて失敗と取り違えられないようにする。控えた中断には、記録の後で従う
take_lock() {
  local taken=""
  interrupted=""
  trap 'interrupted=1' INT TERM
  if (trap '' INT TERM; exec mkdir "$1") 2>/dev/null; then
    printf -v "$2" '%s' "$1"
    taken=1
  fi
  trap 'exit 130' INT TERM
  if [ -n "$interrupted" ]; then exit 130; fi
  [ -n "$taken" ]
}

# 元の作業ツリーの開発ツール（$1。Git の管理外）を、導入のロック（setup-tools.sh と同じ .install.lock）を取ってから
# 専用の作業ツリー（$2）へ写す（.githooks/pre-push と同じ手順）。共有のリンクで指すと、検査の途中に別の setup-tools.sh が
# bin のリンクや版のディレクトリを差し替え、手順 0 で照合した導入と後の手順が使う導入が食い違いうる。写しは APFS では
# clone（cp -c）、対応しないファイルシステムでは通常の複写で作る。bin のリンクは相対パスなので写しの中を指し、
# 手順 0 の照合と後の手順はすべてこの写しを使う
copy_tools() {
  local source=$1 copy=$2 waited=0 retried=0 entry
  [ -d "$source" ] || fail "開発ツールが導入されていない（${source}）。scripts/setup-tools.sh を実行してから実行し直す"
  until take_lock "$source/.install.lock" TOOLS_LOCK; do
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
  # ロックを外して持ち主の印を外すまで、中断は控えるだけにする（.githooks/pre-push と同じ。間で終了してロックを残さず、
  # 後始末が別の実行の取り直したロックを消さない）。外せなければ印を残して失敗し、控えた中断には印を外した後で従う
  local released=1
  interrupted=""
  trap 'interrupted=1' INT TERM
  if (trap '' INT TERM; exec rmdir "$TOOLS_LOCK"); then TOOLS_LOCK=""; else released=""; fi
  trap 'exit 130' INT TERM
  if [ -n "$interrupted" ]; then exit 130; fi
  [ -n "$released" ] || fail "開発ツールの導入のロック（${TOOLS_LOCK}）を外せない"
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
  # 照合に使う setup-tools.sh もステージしたものを書き出して実行する（ステージしていない作業ツリーの変更で照合を変えない）
  git cat-file blob :scripts/setup-tools.sh > "$STAGED_TOOLS/setup-tools.sh" || fail "ステージした scripts/setup-tools.sh を読めない"
  /bin/bash "$STAGED_TOOLS/setup-tools.sh" --verify --versions "$STAGED_TOOLS/versions" --dest "$STAGED_TOOLS/.tools" > /dev/null \
    || fail "写した開発ツールがステージした tools/versions と一致しない。ステージした台帳で導入し直してからコミットする"
  TOOLS="$STAGED_TOOLS/.tools/bin"
}

# 通常の実行（--staged 以外）の手順 1〜3 が使う開発ツールを固定する。元の .tools を導入のロックを取って一時ディレクトリへ写し、
# 以後の手順はすべて写しを使う（照合の後に別の setup-tools.sh が元の導入を差し替えても、照合していない導入で検査しない）。
# 手順 2・3 が呼ぶ test-lint-rules.py・gitleaks-history.sh・test-checks.py には MINUTES_TOOLS_DIR で写しの場所を渡す
snapshot_run_tools() {
  RUN_TOOLS=$(mktemp -d)
  copy_tools "$ROOT/.tools" "$RUN_TOOLS/.tools"
  TOOLS="$RUN_TOOLS/.tools/bin"
  export MINUTES_TOOLS_DIR="$RUN_TOOLS/.tools"
}

# 使う開発ツールを、この作業ツリーの tools/versions と照合する（写しがあれば写しを、なければ元の .tools を照合する）
verify_run_tools() {
  if [ -n "$RUN_TOOLS" ]; then
    scripts/setup-tools.sh --verify --dest "$RUN_TOOLS/.tools"
  else
    scripts/setup-tools.sh --verify
  fi
}

# ディレクトリ $2 の実体のパスを変数 $1 に入れる。$(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と pwd が
# 付けた区切りの改行 1 つだけを外す（名前が改行で終わるディレクトリを取り違えない）
real_directory() {
  local resolved_path
  resolved_path=$(cd -P -- "$2" 2>/dev/null && pwd && printf x) || return 1
  resolved_path=${resolved_path%x}
  printf -v "$1" '%s' "${resolved_path%$'\n'}"
}
# 作業ツリー $2 の git rev-parse --path-format=absolute $3 のパスを変数 $1 に入れる（末尾の改行の扱いは real_directory と同じ）
git_path() {
  local printed_path
  printed_path=$(git -C "$2" rev-parse --path-format=absolute "$3" 2>/dev/null && printf x) || return 1
  printed_path=${printed_path%x}
  printf -v "$1" '%s' "${printed_path%$'\n'}"
}

# 引き継いだ MINUTES_CHECK_ISOLATED を信じてよいか。この作業ツリーが、プッシュ前のフックか check.sh --require-clean が
# この検査のために作った専用の作業ツリー（作成時に専用の管理ディレクトリへ書いた印があり、印の作業ツリーのパスが ROOT の
# 実体のパスと一致する）なら 0 を返す。そうでなければ 1（元の作業ツリーで変数だけを引き継いでも、展開を省かせない）
isolated_checkout() {
  local private common root_real mark file text
  git_path private . --git-dir && real_directory private "$private" || return 1
  git_path common . --git-common-dir && real_directory common "$common" || return 1
  real_directory root_real "$ROOT" || return 1
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
  local measured report digest sources
  local resolution=()
  take_coverage_lock
  mkdir -p "$PACKAGE/.build"
  rm -f -- "$COVERAGE_HEAD" "$COVERAGE_JSON"
  rm -rf -- "$COVERAGE_INPUT"
  # 測る入力を固定する。入力の識別子（コミット、または作業ツリーを写したツリー）が指すツリーから Packages/ を展開し、
  # その写しを測る。展開できなければ測らない（識別子と違う内容を測った結果をコミットに帰属させない）
  measured=$(coverage_input) || fail "カバレッジを測る入力を識別できない"
  mkdir -p "$COVERAGE_INPUT"
  materialize_coverage_input "${measured#* }" || fail "カバレッジを測る入力（${measured}）を展開できない"
  real_directory sources "$COVERAGE_INPUT/$PACKAGE" || fail "カバレッジを測る入力の場所を特定できない"
  verify_coverage_links "$COVERAGE_INPUT" || fail "カバレッジを測る入力（${measured}）のシンボリックリンクを確かめられない。測らない"
  # 測定ごとに専用の出力先を使う（同じ Package を対象にした別の swift test が、この測定の JSON を上書きできない）。
  # 毎回ビルドし直すので時間はかかるが、共有の出力先に残った別の測定の結果を記録しない
  COVERAGE_SCRATCH=$(mktemp -d)
  # 確定版の記録（Package.resolved）があれば、その版だけを使わせる（古い記録を swift test に書き直させて測らない。合わなければ
  # 記録を書く前に失敗する）。依存がなく記録もない場合は、これまでどおりの指定で実行する
  if [ -e "$COVERAGE_INPUT/$PACKAGE/Package.resolved" ]; then resolution=(--force-resolved-versions); fi
  swift test --package-path "$COVERAGE_INPUT/$PACKAGE" --scratch-path "$COVERAGE_SCRATCH" ${resolution[@]+"${resolution[@]}"} --enable-code-coverage
  # 追加の安全策: 測った前後で作業ツリーが変わっていれば記録しない（写しを測るので結果は識別子と一致するが、
  # 測定中に変わった作業ツリーの合格として扱われるのを避ける）
  [ "$(coverage_input)" = "$measured" ] || fail "カバレッジの測定中に作業ツリーが変わった。手順 7 を実行し直す"
  report=$(swift test --package-path "$COVERAGE_INPUT/$PACKAGE" --scratch-path "$COVERAGE_SCRATCH" ${resolution[@]+"${resolution[@]}"} --show-codecov-path)
  [ -f "$report" ] || fail "カバレッジの記録がない（swift test の結果を確かめる）"
  # この測定の JSON を記録の場所へ写す。同じディレクトリの一時ファイルに写してから rename で置き換え、
  # 写したものからハッシュを計算する。JSON を置いてから入力とハッシュの記録を置く（記録があれば JSON もある）
  cp -- "$report" "$COVERAGE_JSON.$$"
  digest=$(coverage_digest "$COVERAGE_JSON.$$") || fail "カバレッジの記録のハッシュを計算できない"
  mv -f -- "$COVERAGE_JSON.$$" "$COVERAGE_JSON"
  printf '%s\n%s\n%s\n' "$measured" "$digest" "$sources/Sources" > "$COVERAGE_HEAD.$$"
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
  local measured present recorded_digest recorded_sources
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
  # 記録のすべてを同じ開いたファイルから読む（2 回開くと、その間に記録が置き換えられたとき別の記録の行を組み合わせうる）。
  # 行がなければ空のまま続け、下の照合で失敗させる（set -e で黙って止めない）
  measured=""
  recorded_digest=""
  recorded_sources=""
  { IFS= read -r measured || :; IFS= read -r recorded_digest || :; IFS= read -r -d '' recorded_sources || :; } < "$COVERAGE_HEAD"
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
  # 末尾の改行 1 つだけを外す（パスに含まれる改行は保つ）。前の形式の記録（パスがない）は使わない
  recorded_sources=${recorded_sources%$'\n'}
  [ -n "$recorded_sources" ] || fail "カバレッジの記録に測った場所がない。手順 7 を実行し直す"
  case "$measured" in
    worktree*) echo "注意: カバレッジは未コミットの変更を含む作業ツリーで測った。コミットの記録としては扱わない" >&2 ;;
  esac
  # 手順 7 が測ったのは固定した入力の写しなので、測ったときの写しの Sources の行を数える（現在の場所から求め直さない）
  scripts/coverage-report.py "$COVERAGE_COPY" --measured-sources "$recorded_sources" --target "$PACKAGE_COVERAGE_TARGET"
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
if [ "$STAGED" = 1 ]; then reject_staged_config_links; fi
if [ "$STAGED" = 1 ]; then snapshot_staged_tools; fi
if [ "$STAGED" = 1 ] && [ ${#STAGED_SWIFT[@]} -gt 0 ]; then prepare_staged_tree; fi
# 通常の実行は作業ツリーを検査する。開始時と終了時の HEAD と未コミットの変更を控え、
# （未追跡のファイルは status.showUntrackedFiles の設定によらず数える）
# 検査したツリーがコミットと一致する場合だけ、そのコミットの合格として表示する
START_HEAD=""
START_CHANGES=""
if [ "$STAGED" = 0 ]; then
  # 置き換えのオブジェクト（git replace）を使わず、元のオブジェクトで開始時の照合・専用の作業ツリーの展開・sparse checkout の
  # 解除・入れ子の check.sh の検査・カバレッジの入力の識別と書き出し（手順 7・8。--require-clean の有無によらない）を行う
  # （子のプロセスにも引き継ぐ。置き換えがなければ結果は変わらない）
  export GIT_NO_REPLACE_OBJECTS=1
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
  # 呼び出し側のインデックス（GIT_INDEX_FILE）は、上の開始時の照合だけに使う。残すと、専用の作業ツリーの作成・展開・照合と
  # 入れ子の check.sh の git が、専用の作業ツリーではなく呼び出し側のインデックスを読み書きする
  unset GIT_INDEX_FILE
  # 一時の置き場所は TMPDIR（なければ /tmp）の下に明示的に作る（mktemp -d の既定は TMPDIR を使わない環境がある）
  temporary_root=${TMPDIR:-/tmp}
  ISOLATED_BASE=$(mktemp -d "${temporary_root%/}/minutes-check.XXXXXX")
  # 一時の置き場所も、プッシュ前のフックと同じ判定（scripts/worktree-location.py）で確かめてから作業ツリーを作る。
  # TMPDIR が作業ツリーの中や、SwiftFormat の設定（.swiftformat・.swift-version）のある場所の下を指すと、専用の作業ツリーの
  # 検査に別の設定が混ざる。作業ツリーの外で、設定を継がない一時ディレクトリは認める
  # 判定の結果のパスは NUL で終わる記録で受け取る（$(…) はパスの末尾の改行を落とすので使わない）
  location_status=0
  # 判定に使うスクリプトは開始時のコミットから書き出して実行する（元の作業ツリーが sparse checkout で scripts/ の一部が
  # なくても判定でき、途中の書き換えで判定が変わらない）
  location_script=$(mktemp)
  if ! git cat-file blob "$START_HEAD:scripts/worktree-location.py" > "$location_script"; then
    rm -f -- "$location_script"
    fail "開始時のコミットの scripts/worktree-location.py を読めない"
  fi
  location_out=$(mktemp)
  # -I: 書き出した場所（一時ディレクトリ）に置かれた同名のモジュール（subprocess.py など）を読み込ませない
  /usr/bin/python3 -I "$location_script" check "$ISOLATED_BASE" "$ROOT" > "$location_out" || location_status=$?
  checked_base=""
  if [ "$location_status" = 0 ] && ! IFS= read -r -d '' checked_base < "$location_out"; then location_status=2; fi
  rm -f -- "$location_out" "$location_script"
  case $location_status in
    0) ISOLATED_BASE=$checked_base ;;
    1) fail "専用の作業ツリーの一時の置き場所（${ISOLATED_BASE}）を使えない。TMPDIR で作業ツリーの外の、SwiftFormat の設定のない場所を指定する" ;;
    *) fail "専用の作業ツリーの一時の置き場所（${ISOLATED_BASE}）が作業ツリーの中かを判定できない" ;;
  esac
  git_path ISOLATED_COMMON "$ROOT" --git-common-dir || fail "共通のディレクトリを特定できない"
  ISOLATED_TREE="$ISOLATED_BASE/tree"
  # 改行の変換（利用者の core.autocrlf の設定）を掛けずに展開する（scripts/check.sh などを LF のまま実行する）
  # 利用者のフック（post-checkout）は、内部の展開では動かさない（.githooks/pre-push と同じ）
  git -c core.hooksPath=/dev/null -c core.autocrlf=false worktree add -q --detach "$ISOLATED_TREE" "$START_HEAD"
  # 引き継いだ GIT_DIR・GIT_WORK_TREE（元のリポジトリを選ぶのに使った）を、展開した後に外す。残すと、以後の git と入れ子の
  # check.sh が、専用の作業ツリーではなく元の作業ツリーを読み書きする
  unset GIT_DIR GIT_WORK_TREE
  # 作った作業ツリーの専用の管理ディレクトリに印を書く（入れ子の check.sh が、引き継いだ MINUTES_CHECK_ISOLATED を
  # この作業ツリーのものと確かめる。isolated_checkout）
  git_path isolated_private "$ISOLATED_TREE" --git-dir \
    && real_directory isolated_private "$isolated_private" && real_directory isolated_root "$ISOLATED_TREE" \
    || fail "専用の作業ツリー（${ISOLATED_TREE}）の管理ディレクトリを特定できない"
  printf '%s\n' "MinutesMobile check ${isolated_root}" > "$isolated_private/minutes-check-owner"
  full_checkout "$ISOLATED_TREE"
  # 展開した内容がコミットの blob と同じであること（smudge のフィルタで変わった内容をコミットの合格として検査しない）
  verify_checkout_blobs "$ISOLATED_TREE" \
    || fail "専用の作業ツリーの内容がコミット ${START_HEAD} の blob と違う（.gitattributes の smudge のフィルタなど）。コミットの合格にしない"
  # 開発ツールは Git の管理外（.gitignore の /.tools で無視される）。導入のロックを取って写し、手順 0 の照合と
  # 後の手順はすべてこの写しを使う（検査の途中の setup-tools.sh による差し替えの影響を受けない）
  copy_tools "$ROOT/.tools" "$ISOLATED_TREE/.tools"
  # 入れ子の check.sh も同じ開始時のコミットを確かめる。呼び出し側の --expect-head（参照の名前などで、指すコミットが変わりうる）
  # より後に置き、後の指定を優先させる
  echo "== 開始時のコミット ${START_HEAD} を専用の作業ツリー（${ISOLATED_TREE}）に展開して検査する"
  MINUTES_CHECK_ISOLATED=1 "$ISOLATED_TREE/scripts/check.sh" "${ARGUMENTS[@]}" --expect-head "$START_HEAD"
  exit 0
fi

# 固定した開発ツールを使う手順（1・2・3）があれば、手順を始める前に開発ツールを写し、実行の全体で写しだけを使う。
# 固定した開発ツールを使う手順（1・2・3）を、照合する手順 0 より先に（または手順 0 なしで）実行する場合も、使う導入がコミットした台帳（この作業ツリーの
# tools/versions。専用の作業ツリーでは展開したコミットのもの、そこへ写した .tools）と一致することを先に確かめる（照合して
# いない導入の結果で合格にしない）。手順 0 の自己試験（test-checks.py versions）は求めない。--staged は snapshot_staged_tools が
# ステージした台帳と照合済み
if [ "$STAGED" = 0 ]; then
  uses_tools=""
  verified_first=""  # 開発ツールを使う最初の手順より前に手順 0 がある（--steps 1,0 のような後の手順 0 は数えない）
  for step in $STEPS; do
    case "$step" in
      0) if [ -z "$uses_tools" ]; then verified_first=1; fi ;;
      1 | 2 | 3) uses_tools=1 ;;
    esac
  done
  if [ -n "$uses_tools" ]; then snapshot_run_tools; fi
  if [ -n "$uses_tools" ] && [ -z "$verified_first" ]; then
    echo "== 開発ツールの照合（手順 1〜3 が使う導入を tools/versions と照合する）"
    verify_run_tools
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
  # 検査が読んだ専用のインデックスと、コミットされる本来のインデックスの両方が、控えたツリーのままであること
  end_copy_tree=$(git write-tree) || fail "終了時の専用のインデックスのツリーを作れない"
  end_index_tree=$(GIT_INDEX_FILE="$ORIGINAL_INDEX" git write-tree) || fail "終了時のステージした内容のツリーを作れない"
  if [ "$end_copy_tree" != "$STAGED_INDEX_TREE" ] || [ "$end_index_tree" != "$STAGED_INDEX_TREE" ]; then
    fail "検査中にステージした内容が変わった（開始時 ${STAGED_INDEX_TREE} / 終了時 ${end_index_tree}・検査したもの ${end_copy_tree}）。検査した内容と違うので合格にしない"
  fi
  # ステージしたファイルを選んだ基点（開始時の HEAD）が終了時も同じであること（変わっていれば、コミットされる差分と
  # 検査したファイルの集合が違いうるので合格にしない）。HEAD がまだなかった場合は、終了時もまだないこと
  end_staged_head=$(git rev-parse --verify -q HEAD) || end_staged_head=""
  if [ "$end_staged_head" != "$STAGED_HEAD" ]; then
    fail "検査中に HEAD が変わった（開始時 ${STAGED_HEAD:-まだない} / 終了時 ${end_staged_head:-まだない}）。検査したファイルの集合と違いうるので合格にしない"
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
```
