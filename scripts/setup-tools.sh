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
ROOT=$(cd "$(dirname "$0")/.." && pwd)
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
    swiftlint) out=$("$binary" version) || return 1 ;;
    swiftformat) out=$("$binary" --version) || return 1 ;;
    gitleaks) out=$("$binary" version) || return 1 ;;
    gh) out=$("$binary" --version) || return 1; out=${out#gh version }; out=${out%% *} ;;  # 1 行目「gh version 2.104.0 (日付)」
    *) echo "NG: 版の確認方法が未定義のツール: $name" >&2; return 1 ;;
  esac
  printf '%s\n' "${out#v}"
}

# 外部入力（版の写し）は形式を確かめてから使う
check_field() {
  local label=$1 value=$2 pattern=$3
  [[ $value =~ $pattern ]] || fail "tools/versions の${label}が不正: '$value'"
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
  # 導入記録の SHA-256 に加え、実行時の版も照合する（verify_tool と同じ確認方法。違えば取得し直す）。
  # 版の表示に失敗した導入は、表示の内容によらず導入済みとして扱わない
  if regular_binary "$target/$binary" && [ -f "$target/.archive-sha256" ] && [ "$(cat "$target/.archive-sha256")" = "$sha" ]; then
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
      curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
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
    if [ -n "$backup" ]; then rm -rf -- "${backup:?}"; fi
    PENDING_TARGET=""
    PENDING_BACKUP=""
  fi
  mkdir -p "$DEST/bin"
  # 公開するリンクの位置に実体のディレクトリがあると、ln -sfn はその中にリンクを作り、位置のリンクを更新しない。
  # 既存のシンボリックリンクは更新してよい
  if [ -d "$DEST/bin/$name" ] && [ ! -L "$DEST/bin/$name" ]; then
    fail "$DEST/bin/$name がディレクトリ（シンボリックリンクでない）。削除してから実行し直す"
  fi
  ln -sfn "../$name/$version/$binary" "$DEST/bin/$name"
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
    # 置き換えの途中で終わった場合: 新しい導入がまだ置かれていなければ退避した旧い導入を戻し、
    # 置かれていれば新しい導入を残して退避を消す
    if [ -n "$PENDING_BACKUP" ] && [ -e "$PENDING_BACKUP" ]; then
      if [ ! -e "$PENDING_TARGET" ]; then
        mv "$PENDING_BACKUP" "$PENDING_TARGET" || echo "NG: 退避した導入を戻せない（${PENDING_BACKUP}）" >&2
      else
        # 削除の失敗で終了処理を止めない（set -e でロックの解放を飛ばさない）。失敗はパスとともに記録する
        rm -rf -- "${PENDING_BACKUP:?}" || echo "NG: 退避した導入を削除できない（${PENDING_BACKUP}）" >&2
      fi
    fi
    if [ -n "$WORK" ]; then rm -rf -- "${WORK:?}" || echo "NG: 作業場所を削除できない（${WORK}）" >&2; fi
    # 自分が取得したロックだけを解放する（ロックを待っている間に終わった場合は、他の実行のロックを消さない）
    if [ -n "$LOCK_HELD" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
    # 終了コードを明示的に返す（bash 3.2 は、trap の最後のコマンドの状態でエラー終了を上書きすることがある）
    exit "$status"
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  # 作れない理由を区別する。ロックのディレクトリがあるときだけ別の導入の実行中として待ち、
  # ないのに作れない場合（書き込めない導入先など）や、ディレクトリ以外のものがある場合は待たずに止める
  waited=0
  retried=""
  until lock_error=$(mkdir "$LOCK" 2>&1); do
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
  LOCK_HELD=1
  if [ "$MODE" = install ]; then
    WORK=$(mktemp -d "$DEST/.work.XXXXXX") || fail "作業場所を作れない（${DEST}）"
  fi
fi

ARCH=$(uname -m)
seen=" "      # 写しに現れたツール名
matched=" "   # この Mac 向けの行が見つかったツール名

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
  if [ "$MODE" = install ]; then
    install_tool "$name" "$version" "$repo" "$tag" "$asset" "$binary" "$sha"
  fi
  verify_tool "$name" "$version" "$binary" "$sha"
done < "$VERSIONS"

[ "$seen" != " " ] || fail "tools/versions にツールが 1 つもない"
# 台帳に現れたツールと、検査が使う必須のツールの両方について、この Mac 向けの行が 1 つずつあること
# （重複はループの中で失敗させている）
for name in $seen $REQUIRED; do
  case "$matched" in *" $name "*) ;; *) fail "$name に $ARCH 向けの行がない" ;; esac
done
echo "OK: 開発ツールの版は tools/versions と一致"
