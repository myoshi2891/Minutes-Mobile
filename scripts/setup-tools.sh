#!/bin/bash
# 開発ツール（gh・SwiftLint・SwiftFormat・gitleaks）を tools/versions の版で .tools/ に導入する（BD-DEV-03、SEC-SUP-01）。
#
# 使い方:
#   scripts/setup-tools.sh            導入する（導入済みで版と SHA-256 が一致するものは取得しない）
#   scripts/setup-tools.sh --verify   取得せず、導入済みの版が tools/versions と一致するかだけ確かめる（check.sh の手順 0）
# 試験用の指定（検査の自己試験が使う）: --versions <ファイル>  --dest <ディレクトリ>
#   --require "<名前 …>"  台帳に行が要るツールの集合（既定は検査が使う 4 種。一部だけの台帳で試験するときに明示する）
#
# 終了コード: 0 = 一致 / 1 = 不一致・取得や照合の失敗 / 2 = 使い方の誤り
# 前提: curl・shasum・unzip・tar（OS 同梱）。gh は記録した版を curl で取得して .tools/ に入れ、
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
# 検査（check.sh）が使うツール。台帳から行が消えても見落とさないよう、台帳の内容ではなくこの集合で必須を決める
REQUIRED="gh swiftlint swiftformat gitleaks"

usage() {
  echo "使い方: $0 [--verify] [--versions <ファイル>] [--dest <ディレクトリ>]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --verify) MODE=verify; shift ;;
    --versions) [ $# -ge 2 ] || usage; VERSIONS=$2; shift 2 ;;
    --dest) [ $# -ge 2 ] || usage; DEST=$2; shift 2 ;;
    --require) [ $# -ge 2 ] && [ -n "$2" ] || usage; REQUIRED=$2; shift 2 ;;
    *) usage ;;
  esac
done

fail() {
  echo "NG: $*" >&2
  exit 1
}

[ -f "$VERSIONS" ] || fail "版の写しがない: $VERSIONS"

# 実行時の版を表示するコマンド。ツールを足すときはここにも足す（未定義のツールは失敗させる）。
# 呼び出し側は $(…) の中で実行するので set -e が効かない。表示のコマンドの失敗は非 0 で返し、版を出力しない
runtime_version() {
  local name=$1 binary=$2 out
  case "$name" in
    # 標準入力は /dev/null にする（呼び出し側は版の写しを読んでいる途中で、版のコマンドに後続の行を読ませない）
    swiftlint) out=$("$binary" version </dev/null) || return 1 ;;
    swiftformat) out=$("$binary" --version </dev/null) || return 1 ;;
    gitleaks) out=$("$binary" version </dev/null) || return 1 ;;
    gh) out=$("$binary" --version </dev/null) || return 1; out=${out#gh version }; out=${out%% *} ;;  # 1 行目「gh version 2.104.0 (日付)」
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

# シンボリックリンクの先のファイルがあるディレクトリを、実体のパスで返す（リンクの先が相対パスでもよい）
# 版ディレクトリの中の実行ファイルが、シンボリックリンクではない通常のファイルで、実行できること
# （導入先の中のリンクで別の場所の実行ファイルを指させない）
regular_binary() {
  local file=$1
  [ -f "$file" ] && [ ! -L "$file" ] && [ -x "$file" ]
}

link_directory() {
  local link=$1 dest
  dest=$(readlink "$link") || return 1
  case "$dest" in /*) ;; *) dest="$(dirname "$link")/$dest" ;; esac
  (cd -P "$(dirname "$dest")" 2>/dev/null && pwd)
}

verify_tool() {
  local name=$1 version=$2 binary=$3 sha=$4
  # 公開するリンクの名前はツールの名前（検査が呼ぶ名前）。リンク先は配布ファイルの中の実行ファイル（$binary）
  local link="$DEST/bin/$name" stamp="$DEST/$name/$version/.archive-sha256" actual
  [ -x "$link" ] || fail "$name が導入されていない。scripts/setup-tools.sh を実行する"
  # 実行するのは、導入記録のある版のディレクトリの中の実行ファイルに限る（リンクの先を別の導入に差し替えさせない）。
  # bash 3.2 の macOS には readlink -f がないので、リンクの先のディレクトリを cd -P で実体に解決して比べる
  local target_dir expected_dir
  target_dir=$(link_directory "$link") || fail "$name のリンク（${link}）の先を解決できない。scripts/setup-tools.sh を実行し直す"
  expected_dir=$(cd -P "$DEST/$name/$version" 2>/dev/null && pwd) \
    || fail "$name $version の導入先がない。scripts/setup-tools.sh を実行し直す"
  contained_install "$name" "$version" \
    || fail "$name $version のディレクトリの実体が導入先の中にない（リンクなど）。scripts/setup-tools.sh を実行し直す"
  [ "$target_dir" = "$expected_dir" ] && [ "$(basename "$(readlink "$link")")" = "$binary" ] \
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

install_tool() {
  local name=$1 version=$2 repo=$3 tag=$4 asset=$5 binary=$6 sha=$7
  local target="$DEST/$name/$version" work="$WORK/$name" actual found license cached=""
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
      "$DEST/bin/gh" release download "$tag" --repo "github.com/$repo" --pattern "$asset" --dir "$work/download" </dev/null \
        || fail "$name の配布ファイルを取得できない（$repo $tag ${asset}）"
    fi
    actual=$(shasum -a 256 "$work/download/$asset" | awk '{print $1}')
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
      found=$(find "$work/extract" -type f -name "$binary")
      [ -n "$found" ] || fail "$name の配布ファイルに実行ファイル $binary がない"
      [ "$(printf '%s\n' "$found" | wc -l | tr -d ' ')" = 1 ] || fail "$name の配布ファイルに $binary が複数ある"
    fi
    cp "$found" "$work/out/$binary"
    chmod 755 "$work/out/$binary"
    license=$(find "$work/extract" -maxdepth 2 -type f -name LICENSE)
    if [ -n "$license" ] && [ "$(printf '%s\n' "$license" | wc -l | tr -d ' ')" = 1 ]; then cp "$license" "$work/out/LICENSE"; fi
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
  check_field "版" "${version:-}" '^[0-9]+(\.[0-9]+)*$'
  check_field "アーキテクチャ" "${arch:-}" '^(any|arm64|x86_64)$'
  check_field "リポジトリ" "${repo:-}" '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
  check_field "タグ" "${tag:-}" '^v?[0-9]+(\.[0-9]+)*$'
  check_field "配布ファイル" "${asset:-}" '^[A-Za-z0-9_.-]+$'
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
