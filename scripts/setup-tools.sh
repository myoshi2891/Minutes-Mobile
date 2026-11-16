#!/bin/bash
# 開発ツール（gh・SwiftLint・SwiftFormat・gitleaks）を tools/versions の版で .tools/ に導入する（BD-DEV-03、SEC-SUP-01）。
#
# 使い方:
#   scripts/setup-tools.sh            導入する（導入済みで版と SHA-256 が一致するものは取得しない）
#   scripts/setup-tools.sh --verify   取得せず、導入済みの版が tools/versions と一致するかだけ確かめる（check.sh の手順 0）
# 試験用の指定（検査の自己試験が使う）: --versions <ファイル>  --dest <ディレクトリ>
#
# 終了コード: 0 = 一致 / 1 = 不一致・取得や照合の失敗 / 2 = 使い方の誤り
# 前提: curl・shasum・unzip・tar（OS 同梱）。gh は記録した版を curl で取得して .tools/ に入れ、
# ほかのツールの取得には、その版の gh（.tools/bin/gh）だけを使う（PATH や Homebrew の gh は使わない）
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSIONS="$ROOT/tools/versions"
DEST="$ROOT/.tools"
MODE=install

usage() {
  echo "使い方: $0 [--verify] [--versions <ファイル>] [--dest <ディレクトリ>]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --verify) MODE=verify; shift ;;
    --versions) [ $# -ge 2 ] || usage; VERSIONS=$2; shift 2 ;;
    --dest) [ $# -ge 2 ] || usage; DEST=$2; shift 2 ;;
    *) usage ;;
  esac
done

fail() {
  echo "NG: $*" >&2
  exit 1
}

[ -f "$VERSIONS" ] || fail "版の写しがない: $VERSIONS"

# 実行時の版を表示するコマンド。ツールを足すときはここにも足す（未定義のツールは失敗させる）
runtime_version() {
  local name=$1 binary=$2 out
  case "$name" in
    swiftlint) out=$("$binary" version) ;;
    swiftformat) out=$("$binary" --version) ;;
    gitleaks) out=$("$binary" version) ;;
    gh) out=$("$binary" --version); out=${out#gh version }; out=${out%% *} ;;  # 1 行目「gh version 2.104.0 (日付)」
    *) fail "版の確認方法が未定義のツール: $name" ;;
  esac
  printf '%s\n' "${out#v}"
}

# 外部入力（版の写し）は形式を確かめてから使う
check_field() {
  local label=$1 value=$2 pattern=$3
  [[ $value =~ $pattern ]] || fail "tools/versions の${label}が不正: '$value'"
}

verify_tool() {
  local name=$1 version=$2 binary=$3 sha=$4
  local link="$DEST/bin/$binary" stamp="$DEST/$name/$version/.archive-sha256" actual
  [ -x "$link" ] || fail "$name が導入されていない。scripts/setup-tools.sh を実行する"
  [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$sha" ] \
    || fail "$name $version の導入記録が tools/versions の SHA-256 と一致しない。scripts/setup-tools.sh を実行し直す"
  actual=$(runtime_version "$name" "$link")
  [ "$actual" = "$version" ] \
    || fail "$name の版が違う（tools/versions: $version / 実行時: ${actual}）。scripts/setup-tools.sh を実行し直す"
  echo "OK: $name $version"
}

install_tool() {
  local name=$1 version=$2 repo=$3 tag=$4 asset=$5 binary=$6 sha=$7
  local target="$DEST/$name/$version" work="$WORK/$name" actual found license
  if [ -x "$target/$binary" ] && [ -f "$target/.archive-sha256" ] && [ "$(cat "$target/.archive-sha256")" = "$sha" ]; then
    echo "導入済み: $name $version"
  else
    echo "取得: $name ${version}（$repo $tag ${asset}）"
    mkdir -p "$work/download" "$work/extract" "$work/out"
    # 標準入力は版の写しを読んでいる途中なので、取得のコマンドに渡さない
    if [ "$name" = gh ]; then
      # gh 自身は、公式のリリースの固定の URL から取得する（HTTPS だけ。リダイレクト先も HTTPS に限る）
      curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
        -o "$work/download/$asset" "https://github.com/$repo/releases/download/$tag/$asset" </dev/null \
        || fail "$name の配布ファイルを取得できない（$repo $tag ${asset}）"
    else
      "$DEST/bin/gh" release download "$tag" --repo "$repo" --pattern "$asset" --dir "$work/download" </dev/null \
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
    mkdir -p "$DEST/$name"
    rm -rf -- "${target:?}"
    mv "$work/out" "$target"
  fi
  mkdir -p "$DEST/bin"
  ln -sfn "../$name/$version/$binary" "$DEST/bin/$binary"
}

if [ "$MODE" = install ]; then
  WORK=$(mktemp -d)
  cleanup() {
    local status=$?
    rm -rf -- "${WORK:?}"
    # 終了コードを明示的に返す（bash 3.2 は、trap の最後のコマンドの状態でエラー終了を上書きすることがある）
    exit "$status"
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM
fi

ARCH=$(uname -m)
seen=" "      # 写しに現れたツール名
matched=" "   # この Mac 向けの行が見つかったツール名

while read -r name version arch repo tag asset binary sha extra; do
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
for name in $seen; do
  case "$matched" in *" $name "*) ;; *) fail "$name に $ARCH 向けの行がない" ;; esac
done
echo "OK: 開発ツールの版は tools/versions と一致"
