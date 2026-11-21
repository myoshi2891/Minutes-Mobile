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
ISOLATED_BASE=""  # --require-clean のとき、開始時のコミットを展開する専用の作業ツリーを置く一時ディレクトリ
ISOLATED_TREE=""  # その専用の作業ツリー（git worktree で登録する）
COVERAGE_SCRATCH=""  # 手順 7 の測定ごとの swift test の出力先（一時ディレクトリ）
COVERAGE_COPY=""  # 手順 8 が照合と集計に使う、カバレッジの記録の写し（一時ファイル）

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

# カバレッジのロックを取る。待たずに失敗する（別の測定が実行中。残骸なら削除して実行し直す）
take_coverage_lock() {
  mkdir -p .build
  mkdir "$COVERAGE_LOCK" 2>/dev/null \
    || fail "別のカバレッジの測定・集計が実行中（${COVERAGE_LOCK}）。実行中でなければ、そのディレクトリを削除してから実行し直す"
  COVERAGE_LOCK_HELD=1
}

release_coverage_lock() {
  if [ -n "$COVERAGE_LOCK_HELD" ]; then rmdir "$COVERAGE_LOCK" 2>/dev/null || true; fi
  COVERAGE_LOCK_HELD=""
}

# 入力の識別子が指すツリー（$1）から Packages/ を写しの場所へ書き出す。git archive は .gitattributes の export-ignore で
# 試験用のファイルなどを省きうるので使わず、一時のインデックスに読み込んで checkout-index で書き出す（追跡している
# ファイルをすべて書き出す）。失敗したら非 0 を返す（呼び出し側は条件の中で呼ぶので、set -e に頼らない）
materialize_coverage_input() {
  local index status=0
  index=$(mktemp)
  rm -f -- "$index"
  GIT_INDEX_FILE="$index" git read-tree "$1" || status=1
  if [ "$status" = 0 ]; then
    GIT_INDEX_FILE="$index" git ls-files -z -- Packages \
      | GIT_INDEX_FILE="$index" git checkout-index -z --stdin --prefix="$COVERAGE_INPUT/" || status=1
  fi
  rm -f -- "$index"
  return "$status"
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
  if [ -n "$COVERAGE_SCRATCH" ]; then remove_or_report "$COVERAGE_SCRATCH"; fi
  if [ -n "$COVERAGE_COPY" ]; then remove_or_report "$COVERAGE_COPY"; fi
  if [ -n "$ISOLATED_TREE" ]; then
    # 登録ごと消す。消せなければディレクトリを消して登録の残骸を片付ける
    git -C "$ROOT" worktree remove --force "$ISOLATED_TREE" 2>/dev/null \
      || { remove_or_report "$ISOLATED_TREE"; git -C "$ROOT" worktree prune 2>/dev/null || true; }
  fi
  if [ -n "$ISOLATED_BASE" ]; then remove_or_report "$ISOLATED_BASE"; fi
  release_coverage_lock
  if [ "$status" -eq 0 ] && [ -n "$CLEANUP_FAILED" ]; then exit 1; fi
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
# コミットの合格を求める実行は、開始時のコミットを専用の作業ツリーに展開し、その中の check.sh で検査する（元の作業ツリーを
# 検査中に書き換えて戻しても、検査する内容は変わらない）。専用の作業ツリーで実行中であることは MINUTES_CHECK_ISOLATED で
# 示す（プッシュ前のフックは自身の専用の作業ツリーで実行するので、これを設定して二重に展開しない）。置き場所は
# リポジトリの外（中に置くと、SwiftFormat などが親のディレクトリの設定ファイルも読む）
if [ "$REQUIRE_CLEAN" = 1 ] && [ -z "${MINUTES_CHECK_ISOLATED:-}" ]; then
  ISOLATED_BASE=$(mktemp -d)
  ISOLATED_TREE="$ISOLATED_BASE/tree"
  git worktree add -q --detach "$ISOLATED_TREE" "$START_HEAD"
  # 開発ツールは Git の管理外なので、元の作業ツリーの .tools を指す（.gitignore の /.tools で無視される）
  ln -s "$ROOT/.tools" "$ISOLATED_TREE/.tools"
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
