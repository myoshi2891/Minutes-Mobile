# MinutesMobile 詳細設計書 ── 開発環境と検査

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | NFR-MNT-01・03・04、SEC-NET-05、SEC-DAT-04、SEC-LOG-01、SEC-PRV-01・03、SEC-SUP-01〜05、TST-CI-01〜08 |
| 関係する制約 | CON-01〜04・07〜09 |
| 対応する基本設計 | [../02-basic-design/dev-environment.md](../02-basic-design/dev-environment.md)（設計決定 BD-DEV-01〜06 を引用する） |
| 関連文書 | [../01-requirements/test-requirements.md](../01-requirements/test-requirements.md) §4、[../01-requirements/security-requirements.md](../01-requirements/security-requirements.md) §10、[../01-requirements/dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md) §3・§4 |

本書は、基本設計で決めた検査の仕組みを、スクリプト・設定ファイル・規則の粒度まで定める。**本書のコード片は実ファイルと一致している**（`scripts/verify-docs.py` が照合する。§10）。

---

## 1. 位置づけと範囲

満たす要件: NFR-MNT-04 ／ 制約: CON-09 ／ 決定: BD-DEV-02

検査の仕組みは、機能の実装より先に入れる（BD-DEV-02）。本書の内容は 2 つの作業単位に分かれる。

| 作業単位 | 内容 | 状態（2026-11-16） |
| --- | --- | --- |
| 1. 検査基盤 | 開発ツールの導入、検査スクリプト、静的解析の規則と試験、設定・依存・文書の検査、フックのファイル、`.gitignore`（§2〜§10） | **実装し、動かして確かめた**（結果は §17） |
| 2. プロジェクト設定 | `project.xcproj` の設定変更、`Packages/MinutesKit` とテストターゲットの追加、プライバシーマニフェスト、フックの有効化（§11〜§14） | **設計のみ**（次の作業単位で実施する） |

作業単位 2 が済むまで、`scripts/check.sh` の手順 1・4・7〜9 は失敗する（§4.1 の「現在の結果」）。失敗は検査が正しく働いている結果であり、検査を弱めて通すことはしない。

---

## 2. リポジトリの配置と前提

満たす要件: NFR-MNT-03、SEC-SUP-01 ／ 制約: CON-07

### 2.1 配置

```text
.githooks/                    Git のフック（pre-commit・pre-push）。scripts/setup.sh --hooks で有効にする
.gitignore
.gitleaks.toml                gitleaks の設定
.swiftformat                  SwiftFormat の設定
.swiftlint.yml                SwiftLint の設定と独自ルール
.tools/                       導入スクリプトが展開した開発ツール（Git の管理外）
scripts/
  setup.sh                    初期設定（ツールの導入。--hooks でフックも有効にする）
  setup-tools.sh              開発ツールの導入と版の照合
  check.sh                    検査の本体（手順 0〜10）
  check-project-settings.py   プロジェクト設定・Info.plist・entitlements の検査
  check-dependencies.py       依存の検査
  coverage-report.py          Package のカバレッジの集計
  find-simulator.py           テストに使うシミュレータの選択
  verify-docs.py              文書の整合の検査
  test-lint-rules.py          静的解析の規則の試験
  test-checks.py              検査スクリプトの自己試験
  xcproj.py                   project.xcproj の読み込み（検査スクリプトが共有する）
tools/
  versions                    開発ツールの版の写し（機械可読）
  dependency-allowlist.json   依存の許可リスト
  lint-fixtures/              規則の試験用の違反例・正常例
docs/04-test-records/         実機試験の記録（§15）
```

### 2.2 前提ツール

スクリプトは、OS と Xcode に同梱のものと、導入スクリプトが `.tools/` に入れる固定版のツール（`gh` を含む。§3）だけで動く。検査のために依存を増やさない（NFR-MNT-03）。

| ツール | 版（この Mac での確認値） | 入手 | 使う場所 |
| --- | --- | --- | --- |
| `/bin/bash` | 3.2.57 | macOS 同梱 | `scripts/*.sh`、フック |
| `/usr/bin/python3` | 3.9.6 | Xcode（Command Line Tools）同梱 | `scripts/*.py`。標準ライブラリだけを使う |
| `curl`・`shasum`・`unzip`・`tar`・`plutil` | — | macOS 同梱 | 導入スクリプト（`curl` は固定版の `gh` の取得だけに使う） |
| `xcodebuild`・`swift`・`xcrun simctl` | Xcode 27.2 | Xcode | 手順 6〜9 |
| `gh`（GitHub CLI） | 2.104.0（`tools/versions` で固定） | 導入スクリプトが公式のリリースの配布ファイルを取得し、SHA-256 を照合して `.tools/` に入れる（MIT） | 開発ツールの配布ファイルの取得だけ。使うのは `.tools/bin/gh` だけで、PATH や Homebrew の `gh` は使わない。版は手順 0 で照合する（§3） |

### 2.3 bash 3.2 で守ること

macOS 同梱の bash は 3.2 で、実装中に次の落とし穴を実際に踏んだ。スクリプトを直すときは同じ点を確かめる。

| 守ること | 守らないと起きること |
| --- | --- |
| シバンは `#!/bin/bash` にする。zsh で実行しない | zsh は引用符のない変数を単語に分割しないので、bash 向けの書き方が別の意味になる |
| 全角文字の直前の変数は `${name}` と書く | `$name（` と書くと、続くマルチバイト文字を変数名の一部として読み、「unbound variable」で落ちる |
| 手順の関数を `if`・`&&`・`\|\|` の中で呼ばない | 条件の中では `set -e` が効かない。途中のコマンドが失敗しても、最後のコマンドが成功すれば**通過になる** |
| EXIT の trap の中で、終了コードを明示して `exit` する | エラーで終了しても、trap の最後のコマンドの状態で上書きされ、**終了コードが 0 になる**ことがある |
| `while read` の中で呼ぶコマンドには `</dev/null` を付ける | ループが読んでいる入力を、中のコマンドが食べてしまう |
| 空の配列を展開する前に要素数を確かめる | `set -u` の下で「unbound variable」になる |
| 連想配列などの bash 4 以降の機能を使わない | 構文エラーになる |

---

## 3. 開発ツールの導入

満たす要件: SEC-SUP-01・02 ／ 制約: CON-07 ／ 決定: BD-DEV-03

### 3.1 版の写し（`tools/versions`）

台帳（[dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md) §3）の機械可読の写し。1 行が 1 つの配布ファイルで、空白で区切る。

<!-- file: tools/versions -->

```text
# 開発ツールの版の写し（機械可読）。正は docs/01-requirements/dependencies-and-costs.md §3。
# 版を変えるときは、台帳とこのファイルを同じ PR で直す（SEC-SUP-01）。
# 列（空白区切り）: 名前 版 アーキテクチャ リポジトリ タグ 配布ファイル 実行ファイル 配布ファイルのSHA-256
# アーキテクチャは any（どの Mac でも同じ配布ファイル）・arm64・x86_64 のいずれか（uname -m の値）。
# gh はほかのツールの取得に使うので、ほかのツールより前に置く（gh 自身は curl で公式のリリースから取得する）。
gh          2.104.0 arm64  cli/cli                 v2.104.0 gh_2.104.0_macOS_arm64.zip         gh          23b12706219b9892e44236f988444a24adcf29284e16b35e03ba5b863fb6eb8e
gh          2.104.0 x86_64 cli/cli                 v2.104.0 gh_2.104.0_macOS_amd64.zip         gh          a8c2a708dbae89dc7c05ba7b0beafc95bd8d09ab4a3505467b77d32ebf364dc6
swiftlint   0.65.1 any    realm/SwiftLint          0.65.1  portable_swiftlint.zip              swiftlint   c1e429b0599cf1b516f369a2d9ec04eaf0e436f3c12b637df8851fa52ff694d0
swiftformat 0.64.7 any    nicklockwood/SwiftFormat 0.64.7  swiftformat.zip                     swiftformat e82e224325cbd3403fc7b68caf6c917926dc106de3af875fca4b87e3c4634038
gitleaks    8.30.1 arm64  gitleaks/gitleaks        v8.30.1 gitleaks_8.30.1_darwin_arm64.tar.gz gitleaks    b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5
gitleaks    8.30.1 x86_64 gitleaks/gitleaks        v8.30.1 gitleaks_8.30.1_darwin_x64.tar.gz   gitleaks    dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709
```

- `gh` の行は、ほかのツールの行より前に置く（ほかのツールの取得に、先に導入・照合した `gh` を使うため。順序が違えば失敗する）。`gh` の配布ファイルはアーキテクチャごとに別で、`x86_64` の Mac には `amd64` の配布ファイルを使う。
- アーキテクチャが `any` の行はどの Mac でも使う。`arm64`・`x86_64` の行は `uname -m` と一致する Mac でだけ使う。ツールごとに、その Mac 向けの行がちょうど 1 つ必要である（0 行でも 2 行でも失敗する）。
- 各列は導入スクリプトが形式を確かめてから使う（名前・版・リポジトリ・タグ・ファイル名に使える文字を限り、SHA-256 は 16 進 64 桁）。

### 3.2 導入スクリプト（`scripts/setup-tools.sh`）

| 手順 | 内容 |
| --- | --- |
| 1 | `tools/versions` を 1 行ずつ読み、形式を確かめる |
| 2 | 版のディレクトリに実行ファイルと導入記録（配布ファイルの SHA-256）があり、記録が写しと一致すれば取得しない |
| 3 | 記録したタグの配布ファイルを一時ディレクトリへ取得する。`gh` 自身は `curl` で公式のリリースの固定の URL（`https://github.com/<リポジトリ>/releases/download/<タグ>/<配布ファイル>`。HTTPS だけ）から、ほかのツールは手順 7 まで済ませた `.tools/bin/gh` の `gh release download` で取得する |
| 4 | `shasum -a 256` の結果を写しと照合する。**一致しなければ展開せずに失敗する** |
| 5 | 一時ディレクトリに展開し、取り出した実行ファイルが記録した版を名乗ることを確かめてから（違えば既存の導入を残して失敗する）、実行ファイルと LICENSE だけを `.tools/<名前>/<版>/` へ移す（実行ファイルが最上位にない配布ファイルは、中にちょうど 1 つあるものを使う。`gh` は `gh_<版>_macOS_<arch>/bin/gh`） |
| 6 | `.tools/bin/<名前>` から版のディレクトリへシンボリックリンクを張る |
| 7 | 実行して版を表示させ、写しと一致することを確かめる |

`--verify` を付けると、取得せずに手順 2・7 の照合だけを行う（`check.sh` の手順 0）。版を表示するコマンドが失敗した場合は、表示の内容によらず不一致として扱う（導入済みとしても再利用しない）。台帳には、台帳に現れたツールに加えて、検査が使う 4 種（`gh`・`swiftlint`・`swiftformat`・`gitleaks`）の行がこの Mac 向けに 1 つずつ要る（台帳から行を消しても見落とさない）。一部だけの台帳で試験するときは `--require "<名前 …>"` で期待する集合を明示する。`gh` も同じく照合するので、取得に使う `gh` が記録した版であることを、取得のたびと手順 0 で確かめる（取得したファイルの SHA-256 の照合は、`gh` の版の固定の代わりにしない）。版の文字列だけでなく導入記録の SHA-256 も照合するので、「版は同じで中身が違う配布ファイル」に写しを差し替えた場合も、導入し直すまで失敗する。

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

verify_tool() {
  local name=$1 version=$2 binary=$3 sha=$4
  local link="$DEST/bin/$binary" stamp="$DEST/$name/$version/.archive-sha256" actual
  [ -x "$link" ] || fail "$name が導入されていない。scripts/setup-tools.sh を実行する"
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
  if [ -x "$target/$binary" ] && [ -f "$target/.archive-sha256" ] && [ "$(cat "$target/.archive-sha256")" = "$sha" ]; then
    cached=$(runtime_version "$name" "$target/$binary" 2>/dev/null) || cached=""
  fi
  if [ -n "$cached" ] && [ "$cached" = "$version" ]; then
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
    # 既存の導入を消す前に、取得したものが記録した版を名乗ることを確かめる（違えば既存の導入を残して失敗する）
    actual=$(runtime_version "$name" "$work/out/$binary") || fail "取得した $name の版を表示できない。既存の導入は残す"
    [ "$actual" = "$version" ] \
      || fail "取得した $name の版が違う（tools/versions: $version / 取得したもの: ${actual}）。既存の導入は残す"
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

展開後の配置:

```text
.tools/
  bin/swiftlint -> ../swiftlint/0.65.1/swiftlint      検査スクリプトが呼ぶのはここだけ
  swiftlint/0.65.1/{swiftlint, LICENSE, .archive-sha256}
  swiftformat/0.64.7/{swiftformat, .archive-sha256}
  gitleaks/8.30.1/{gitleaks, LICENSE, .archive-sha256}
```

### 3.3 最初の記録（2026-11-16）

| ツール | 版 | 配布ファイル | SHA-256 の照合先 | LICENSE（そのタグのファイルを直接確認） |
| --- | --- | --- | --- | --- |
| SwiftLint | 0.65.1（2026-08-21 公開） | `portable_swiftlint.zip`（arm64・x86_64 の両対応） | 自分で計算した値 = GitHub の release API が返す `digest` | MIT |
| SwiftFormat | 0.64.7（2026-11-14 公開） | `swiftformat.zip`（両対応） | 同上 | MIT |
| gitleaks | 8.30.1（2026-03-21 公開） | `gitleaks_8.30.1_darwin_arm64.tar.gz` | 同上、かつ配布の `gitleaks_8.30.1_checksums.txt` | MIT |

- 3 つとも、確認時点の最新の安定版である（プレリリースではない）。基本設計の記録（2026-11-14）から版は変わっていない。
- 基本設計では SwiftLint・SwiftFormat を「チェックサムの配布なし」としたが、GitHub の release API が各配布ファイルの `digest`（SHA-256）を返す。自分で計算した値と独立した照合先として使える。
- Intel の Mac 向けの gitleaks の行（`darwin_x64`）は、配布の `checksums.txt` と release API の値を記録した。**Intel の Mac での導入は実行して確かめていない**（§20）。

### 3.4 版を上げる手順

1 つの PR で次を同時に行う（SEC-SUP-01）。

1. 公式の releases で最新の安定版を確かめ、そのタグの LICENSE を直接読む（無償のままか・ライセンスが変わっていないか。台帳 §4）。
2. 配布ファイルを取得して SHA-256 を計算し、release API の `digest`（gitleaks は `checksums.txt` も）と一致することを確かめる。
3. 台帳 §3 と `tools/versions` を直す。
4. `scripts/verify-docs.py --update-snippets` で本書の §3.1 を書き直し、§3.3 の表を直す（`scripts/check.sh` の手順 10 は §3.1 のコード片を `tools/versions` と照合するので、全体の検査より先に直す）。
5. `scripts/setup-tools.sh` と `scripts/check.sh` を実行し、版の変更で生じた整形・規則の差分を同じ PR で直す。

---

## 4. 検査スクリプト（`scripts/check.sh`）

満たす要件: TST-CI-01〜08、SEC-SUP-03、NFR-MNT-04 ／ 決定: BD-DEV-01・04

### 4.1 手順

| 順 | 検査 | 実行するもの | 検査の自己試験 | 現在の結果（2026-11-16） |
| --- | --- | --- | --- | --- |
| 0 | 開発ツールの版 | `setup-tools.sh --verify` | `test-checks.py versions` | 通過（1 秒） |
| 1 | 整形 | `swiftformat --lint`（書き換えない） | — | **失敗**: テンプレートの `ContentView.swift` の import の並び順（`sortImports`） |
| 2 | 静的解析 | `swiftlint lint`（警告もエラー） | `test-lint-rules.py` | 通過（3 秒） |
| 3 | 秘密情報 | `gitleaks git`（全履歴。検出した値は伏せる） | `test-checks.py gitleaks` | 通過（1 秒。67 コミット） |
| 4 | プロジェクト設定 | `check-project-settings.py` | `test-checks.py settings` | **失敗**: 違反 7 件（§12 の変更で解消する） |
| 5 | 依存 | `check-dependencies.py` | `test-checks.py dependencies` | 通過（1 秒） |
| 6 | ビルド | `xcodebuild build`（機種を特定しないシミュレータ向け） | — | 通過（15 秒） |
| 7 | 単体・結合テスト | `swift test`（Package）と `xcodebuild test`（UI テストを除く） | — | **失敗**: Package とテストターゲットがない |
| 8 | カバレッジ | `coverage-report.py`（初期は警告だけ） | — | **失敗**: Package がない（一時的に置いた Package では通過した。§17.3） |
| 9 | UI テスト | `xcodebuild test`（UI テストだけ） | — | **失敗**: テストターゲットがない |
| 10 | 文書の整合 | `verify-docs.py` | `test-checks.py docs` | 通過（1 秒） |

- 手順 10 は詳細設計で足した（基本設計 §6.1 も同じ作業で直した）。文書がこのリポジトリの正であり、コード片と実ファイルの一致（NFR-MNT-04）を人の目だけに頼らないためである。
- 検査の自己試験（§17）を同じ手順の中で回す。検査スクリプトを変えて検出できなくなった場合に、その場で失敗する。
- 手順の関数は、条件の外で直接呼ぶ（§2.3）。1 つでも失敗したら止まり、失敗した手順を表示して終了コード 1 を返す。

### 4.2 実行のしかた

| 呼び方 | 範囲 | 使う場面 |
| --- | --- | --- |
| `scripts/check.sh` | 0〜10 | PR の作成前と、PR の更新後 |
| `scripts/check.sh --steps 0-8,10 --require-clean --expect-head <SHA>` | 指定した手順。未コミットの変更があるか、開始時の HEAD が指定したコミットでなければ、手順を始めずに失敗する | プッシュ前のフック |
| `scripts/check.sh --staged` | ステージしたファイルに対する 1〜3 と 10 | コミット前のフック |

`--staged` での違い:

| 手順 | `--staged` での動作 |
| --- | --- |
| 1・2 | ステージした Swift ファイルだけを見る（規則の試験用のファイルは除く）。**作業ツリーではなく、ステージした内容（インデックス）を検査する。** `git checkout-index --prefix` で、ステージした Swift ファイルと、インデックスにある設定（`.swiftformat`・各階層の `.swiftlint.yml`）を、元の相対位置のまま一時ディレクトリへ書き出し、そこを起点に実行する（入れ子の設定と、パスで指定した除外が作業ツリーと同じに解決される）。SwiftLint には `--force-exclude` を付け、明示したファイルにも除外の指定を効かせる。一時ディレクトリは終了時に消す |
| 2 | 規則・試験用のファイル・入れ子の設定・テストのディレクトリ（`MinutesMobileTests`・`MinutesMobileUITests`・`Packages/MinutesKit/Tests`。入れ子の設定の置き忘れを検査するため）をステージしたときだけ、規則の試験を回す。**規則の試験もステージした内容で行う。** 規則（各階層の `.swiftlint.yml`）・試験用のファイル（`tools/lint-fixtures/`）・試験のスクリプトをインデックスから同じ一時ディレクトリへ書き出し、その中のスクリプトを実行する（スクリプトは自身の位置をリポジトリ直下とみなす）。開発ツールは作業ツリーの `.tools` へのリンクで使い、入れ子の設定の列挙（`git ls-files`）は `GIT_DIR` と `GIT_WORK_TREE` でインデックスに対して行う |
| 3 | `gitleaks git --pre-commit --staged`（ステージした差分だけ） |
| 10 | **文書の整合もステージした内容で検査する。** `git checkout-index -a --prefix` でインデックス全体を一時ディレクトリへ書き出し、その中の `scripts/verify-docs.py` を `--root` にその一時ディレクトリを指定して実行する。文書・コード片の元の実装・検査スクリプトがすべてインデックスの内容になるので、文書と実装の両方をステージした一致する変更は通り、片方だけをステージした変更はコード片の不一致で止まる。検査スクリプトの自己試験（`test-checks.py docs`）は通常の実行だけで回す |

- ファイルの一部だけをステージした場合も、コミットされる内容（インデックス）だけを検査する。作業ツリーで直しただけでステージしていない違反は、コミットを止める。
- 違反の表示は一時ディレクトリのパスになる（末尾の相対パスは元の位置と同じ）。
- 成功すると、検査したツリーが対象のコミットと一致する場合（開始時と終了時のどちらにも未コミットの変更がなく、検査中に HEAD が変わっていない場合）だけ、そのコミットのハッシュを付けて表示する。PR の説明にはこの行を貼る（基本設計 §6.2）。
- 未コミットの変更（追跡していないファイルを含む）がある作業ツリーを検査した場合は、ハッシュを付けず「コミットの合格としては記録しない」と表示する。作業中の確認には使えるが、PR の説明に貼る結果は、変更をすべてコミットした状態で再実行して得る。
- `--require-clean` を付けた場合は、未コミットの変更（追跡していないファイルを含む）があれば、手順を始める前に終了コード 1 で失敗する。検査中に HEAD が変わった、または変更が生じた場合も、手順の後で失敗する。作業ツリーだけにある修正で合格させないためで、プッシュ前のフックが使う（§5）。`--staged`（インデックスを検査する）とは組み合わせない。
- `--expect-head <SHA>` を付けた場合は、開始時の HEAD（`START_HEAD`）を取得した直後に指定したコミットと照合し、違えば手順を始める前に終了コード 1 で失敗する。プッシュ前のフックは、送信する参照と照合した HEAD をこの引数で渡す（照合してから `check.sh` が始まるまでに HEAD が A から B に変わっても、B を A の合格として検査しない。A のままなら通る）。`--require-clean` の開始時・終了時の検査はそのまま行う。`--staged` とは組み合わせない。

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
# 手順 7 がカバレッジを測ったコミットの記録（手順 8 が集計の前に現在の HEAD と照合する）
COVERAGE_HEAD="$PACKAGE/.build/minutes-coverage-head"
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
    [[ $first =~ ^[0-9]+$ && $last =~ ^[0-9]+$ ]] || usage
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
      "$STAGED_TREE/scripts/test-lint-rules.py")
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
    "$TOOLS/gitleaks" git --no-banner --no-color --redact --config .gitleaks.toml .
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
  rm -f -- "$COVERAGE_HEAD"
  swift test --package-path "$PACKAGE" --enable-code-coverage
  git rev-parse HEAD > "$COVERAGE_HEAD"
  destination=$(test_destination)
  xcodebuild -quiet -project "$PROJECT" -scheme "$SCHEME" -destination "$destination" \
    -skip-testing:"$UI_TESTS" test
}

step_8() {
  local report measured
  [ -f "$PACKAGE/Package.swift" ] || fail "$PACKAGE がない（中核ロジックの Package を作ってから実行する）"
  report=$(swift test --package-path "$PACKAGE" --show-codecov-path)
  [ -f "$report" ] || fail "カバレッジの記録がない。手順 7 を先に実行する"
  # 別のコミットで測った記録を集計しない
  [ -f "$COVERAGE_HEAD" ] || fail "カバレッジを測ったコミットの記録がない。手順 7 を先に実行する"
  measured=$(cat "$COVERAGE_HEAD")
  [ "$measured" = "$(git rev-parse HEAD)" ] \
    || fail "カバレッジは別のコミット（${measured}）で測ったもの。手順 7 を実行し直す"
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
```

### 4.3 シミュレータの選択（`scripts/find-simulator.py`）

テストに使う機種名を決め打ちにしない。導入済みのランタイムと機種は Mac ごとに違い、最低 OS（iOS 27.2。CON-01）より古いランタイムの機種を指定するとビルドが失敗する。

- 実例（2026-11-16、この Mac）: 「iPhone 17」は iOS 27.0 のランタイムにしかなく、`name=iPhone 17` の指定は「destination が見つからない」で失敗した。iOS 27.2 のシミュレータは「iPhone Duo」1 台だけだった。
- 規則: `xcrun simctl list -j` の結果から、最低 OS 以上のランタイムにある iPhone のうち、ランタイムの版が最も低いもの、同じ版の中では名前の順で最初のものを選ぶ（同じ環境では毎回同じ結果になる）。条件を満たすものがなければ失敗する。
- ビルド（手順 6）は `generic/platform=iOS Simulator` を使い、特定の機種に依存しない。
- 環境変数 `MINUTES_SIMULATOR` に `xcodebuild` の destination を入れると、選択を上書きできる。

### 4.4 カバレッジ（`scripts/coverage-report.py`）

- Package: `swift test --enable-code-coverage` が出す JSON のうち、`Packages/MinutesKit/Sources/` 配下のファイルだけを数える。合計にはテストコードと自動生成のファイルが混ざるためである（実測で、合計 59%・本体 50% と食い違った）。目標は 90%（TST-CI-07）で、初期は警告だけを出す。
- アプリ側（全体 70%）の集計は、アプリのテストターゲットを足す作業で実装する。それまで手順 8 は「未実装」の警告を出す。
- 手順 7 は、カバレッジを測ったコミット（`git rev-parse HEAD`）を `Packages/MinutesKit/.build/minutes-coverage-head` に記録する。手順 8 は集計の前にこれを現在の HEAD と照合し、記録がない・違うコミットで測った場合は失敗する（古い記録を集計して合格にしない）。
- 未コミットの変更の判定（`git status --porcelain`）には `--untracked-files=normal` を付け、`status.showUntrackedFiles` の設定によらず追跡していないファイルを数える。

---

## 5. Git のフックと初期設定

満たす要件: TST-CI-03・04、SEC-SUP-03 ／ 決定: BD-DEV-01

<!-- file: .githooks/pre-commit -->

```bash
#!/bin/bash
# コミット前の検査: ステージしたファイルに対する手順 1〜3 と 10（基本設計 dev-environment.md §6.2）。
# 有効にする: scripts/setup.sh --hooks（git config core.hooksPath .githooks）。--no-verify で飛ばさない。
set -euo pipefail
exec "$(git rev-parse --show-toplevel)/scripts/check.sh" --staged
```

<!-- file: .githooks/pre-push -->

```bash
#!/bin/bash
# プッシュ前の検査: 手順 0〜8 と 10（基本設計 dev-environment.md §6.2）。UI テスト（手順 9）は PR の作成前に手動で行う。
# 有効にする: scripts/setup.sh --hooks（git config core.hooksPath .githooks）。--no-verify で飛ばさない。
# 検査するのは HEAD のコミットだけなので、送信するコミットが HEAD と同じで、未コミットの変更がないことを求める
# 照合した HEAD は --expect-head で渡し、check.sh の開始までに HEAD が変わっていれば失敗させる
set -euo pipefail
head=$(git rev-parse HEAD)
updates=0  # 削除以外の参照の送信の数
# 標準入力: 送信する参照ごとに「<ローカルの参照> <ローカルの SHA> <リモートの参照> <リモートの SHA>」
while read -r local_ref local_sha remote_ref remote_sha; do
  # 削除の送信（ローカルの SHA がすべて 0）は検査するコミットがない
  if [ -z "${local_sha//0/}" ]; then continue; fi
  updates=$((updates + 1))
  # 注釈付きのタグはタグのオブジェクトを指すので、コミットまでたどってから比べる
  commit=$(git rev-parse --verify -q "${local_sha}^{commit}") || {
    echo "NG: 送信する ${local_ref} (${local_sha}) がコミットを指していない" >&2
    exit 1
  }
  if [ "$commit" != "$head" ]; then
    echo "NG: 送信する ${local_ref} (${commit}) が HEAD (${head}) と違う。そのコミットをチェックアウトしてから push する" >&2
    exit 1
  fi
done
# 削除だけの送信は、検査するコミットがないので検査を起動しない
if [ "$updates" -eq 0 ]; then exit 0; fi
exec "$(git rev-parse --show-toplevel)/scripts/check.sh" --steps 0-8,10 --require-clean --expect-head "$head" </dev/null
```

<!-- file: scripts/setup.sh -->

```bash
#!/bin/bash
# 開発環境の初期設定: 開発ツールの導入と、Git のフックの有効化（基本設計 dev-environment.md §5.2・§6.2）。
# クローンした直後と、tools/versions が変わったときに実行する。
#
# 使い方:
#   scripts/setup.sh          開発ツールを導入する（フックは有効にしない）
#   scripts/setup.sh --hooks  開発ツールを導入し、フックを有効にする（check.sh の全手順が通るようになってから使う）
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

HOOKS=0
case "${1:-}" in
  "") ;;
  --hooks) HOOKS=1 ;;
  *) echo "使い方: $0 [--hooks]" >&2; exit 2 ;;
esac

scripts/setup-tools.sh
if [ "$HOOKS" = 1 ]; then
  git config core.hooksPath .githooks
  echo "OK: フックを有効にした（core.hooksPath = $(git config core.hooksPath)）"
else
  echo "OK: 開発ツールを導入した。フックは有効にしていない（有効にするときは --hooks を付ける）"
fi
```

- プッシュ前のフックは、削除の送信（ローカルの SHA がすべて 0）を照合の対象から外す。削除だけの送信は検査するコミットがないので、`check.sh` を起動せずに終了コード 0 で終える。削除と通常の更新が混ざる場合は、通常の更新だけを HEAD と照合し、従来どおり `check.sh` を実行する。
- **有効にする時期: 作業単位 2 の最後。** プッシュ前のフックは手順 0〜8 を回すので、プロジェクト設定とテストターゲットが揃う前に有効にすると、push できなくなる。作業単位 1 ではフックのファイルを置くだけにし、複製したリポジトリで動作を確かめた（§17）。
- フックを飛ばす指定（`--no-verify`）は使わない。クラウドの CI がない間は、PR の説明に貼った検査結果（対象のコミットのハッシュ付き）が統合の条件になる（基本設計 §6.2）。
- コミット前の検査の所要時間は、Swift ファイル 2 件の現状で約 1 秒である。遅くなると飛ばされるので、コードが増えたら測り直す（§17）。

---

## 6. 静的解析の規則

満たす要件: SEC-LOG-01、SEC-NET-05、SEC-SUP-01・02、TST-CI-03 ／ 決定: BD-DEV-05

### 6.1 基本設計の表との対応

基本設計 §7 の 14 行を、SwiftLint の組み込みの規則 3 つと独自ルール 19 個で実現する。

| 基本設計 §7 の行 | 規則 | 例外を認める場所 |
| --- | --- | --- |
| `print(`・`NSLog(`・`debugPrint(` | `no_print_logging` | なし |
| `Logger(`・`Logger.init(` の直接の利用 | `no_direct_logger`（`os_log(` も含む） | ログの型 |
| 通信 API・WebSocket・CFStream | `no_network_api` | なし |
| `import Network`・`import CFNetwork` | `no_network_import` | なし |
| BSD ソケット（`socket(`・`connect(`。`Darwin.` で修飾した呼び出しを含む。空白を挟むものも含め、ほかの型のメンバーの呼び出し `x.connect(`・`x . connect(` は除く） | `no_bsd_socket` | なし |
| `SFSpeechRecognizer` | `no_legacy_speech_recognizer` | なし |
| `PrivateCloudComputeLanguageModel` | `no_private_cloud_model` | なし |
| `try!`・`as!`・強制アンラップ | 組み込みの `force_try`・`force_cast`・`force_unwrapping` | テストコード（§6.4） |
| 空の `catch` | `no_empty_catch`（本体がコメントだけの場合も含む。ブロックコメントは 1 段の入れ子まで読む。正規表現の規則なので任意の深さの入れ子は数えられない） | なし |
| `UserDefaults` の直接の利用 | `no_direct_user_defaults` | 設定の型 |
| `.documentDirectory`・`NSTemporaryDirectory`・ディレクトリの直接の作成 | `no_documents_directory`（`.documentDirectory`）・`no_direct_directory_access`（一時ディレクトリとディレクトリの作成） | `no_documents_directory` はなし（保存ルートの型の中でも Documents を使わせない）。`no_direct_directory_access` は保存ルートの型、テスト用の一時ディレクトリの型 |
| `UIPasteboard`、`.textSelection(` | `no_direct_pasteboard` | コピーの型 |
| `ShareLink` | `no_share_link` | なし |
| `WKWebView`・`import WebKit` | `no_webkit` | なし |
| `CoreSpotlight`・`NSUserActivity` | `no_spotlight_user_activity` | なし |
| `Date()`・`Date.now`・`UUID()`・`Task.sleep`・乱数 | `no_direct_clock`・`no_direct_uuid`・`no_direct_sleep_timer`・`no_direct_random` | 注入用の本番実装（UUID はテスト用の一時ディレクトリの型も） |

詳細設計で、基本設計の文言より広げた点:

| 規則 | 足したもの | 理由 |
| --- | --- | --- |
| `no_direct_user_defaults` | `@AppStorage` | SwiftUI から `UserDefaults` に直接書く経路になる（SEC-DAT-07） |
| `no_documents_directory` | `URL.documentsDirectory` | 同じ場所を指す新しい書き方がある |
| `no_direct_directory_access` | `temporaryDirectory` | 同じ場所を指す新しい書き方がある |
| `no_direct_clock` | `ContinuousClock`・`SuspendingClock`・`CFAbsoluteTimeGetCurrent`・`DispatchTime.now`・`systemUptime` など | 単調時計も注入の対象である（TST-GEN-01） |
| `no_direct_sleep_timer` | `Thread.sleep`・`sleep(`・`Timer.scheduledTimer`・`asyncAfter` など | タイマーも注入の対象である（TST-GEN-01） |
| `no_spotlight_user_activity` | `.userActivity(`・`.onContinueUserActivity(` | SwiftUI から `NSUserActivity` を提供する経路になる（SEC-PLT-02） |

### 6.2 設定（`.swiftlint.yml`）

- 検査はリポジトリ直下から `--config` を付けずに実行する。付けると入れ子の設定（§6.4）が無効になる（SwiftLint 0.65.1 の README で確認）。
- 独自ルールの `match_kinds` に、コメントと文字列を除いた種類を指定する。コードとして現れた場合だけ検出し、説明のコメントに書いた名前を誤検出しない。
- 正規表現は YAML の単一引用符で書く（バックスラッシュをそのまま書ける）。

<!-- file: .swiftlint.yml -->

```yaml
# SwiftLint の設定（security-requirements.md §10.1、基本設計 dev-environment.md §7）。
# 規則を足す・変えるときは tools/lint-fixtures/<規則名>/ の違反例と正常例も同じ作業で直す（BD-DEV-05）。
# 検査はリポジトリ直下から --config を付けずに実行する（付けると、テストコード用の入れ子の設定が無効になる）。

# 警告もエラーとして扱う（TST-CI-03）
strict: true

excluded:
  - .tools
  - .build
  - build
  - DerivedData
  - tools/lint-fixtures

opt_in_rules:
  - force_unwrapping

# SEC-INP-03。テストコードでは入れ子の設定（tools/lint-fixtures/_test-code/.swiftlint.yml の写し）で外す
force_try: error
force_cast: error
# 既定では静的な文字列を渡した URL(string:)・UIImage(named:) などの強制アンラップを見逃すので、例外の一覧を空にする
force_unwrapping:
  severity: error
  ignored_literal_argument_functions: []

# match_kinds はコメントと文字列の中の一致を除くために指定する（コードとして現れた場合だけ検出する）
custom_rules:
  no_print_logging:
    name: "print / NSLog / debugPrint の禁止"
    regex: '\b(print|debugPrint|NSLog)\s*\('
    match_kinds: &code
      - identifier
      - typeidentifier
      - keyword
      - attribute.id
      - attribute.builtin
      - argument
      - parameter
      - number
    message: "ログはログの型を経由する（SEC-LOG-01）"
    severity: error

  no_direct_logger:
    name: "Logger の直接の利用の禁止"
    # 型の注釈を付けた省略形（let logger: Logger = .init(subsystem:category:)）も生成として検出する
    regex: '\bos_log\s*\(|\bLogger\s*(?:\.\s*init\s*)?\(|:\s*(?:os\s*\.\s*)?Logger\s*[?!]?\s*=\s*\.\s*init\s*\('
    excluded:
      - '.*/Logging/AppLogger\.swift'
    match_kinds: *code
    message: "Logger はログの型の中だけで使う（BD-ARC-06、SEC-LOG-03）"
    severity: error

  no_network_api:
    name: "通信 API の禁止"
    regex: '\b(URLSession\w*|URLRequest|NSURLConnection|NW(Connection|Listener|Browser|PathMonitor|ProtocolWebSocket)\w*|CFStreamCreatePairWith\w+|CFSocket\w*)\b'
    match_kinds: *code
    message: "アプリのコードは通信 API を持たない（SEC-NET-01、BD-ARC-05）"
    severity: error

  no_network_import:
    name: "通信フレームワークの import の禁止"
    regex: '\bimport\s+(\w+\s+)?(Network|CFNetwork)\b'
    match_kinds: *code
    message: "Network / CFNetwork を import しない（SEC-NET-01）"
    severity: error

  # 「.」の後に空白を挟むメンバーの呼び出し（store . connect()）は、有界の後読みで除く（ICU は可変長の後読みを持たない）
  no_bsd_socket:
    name: "BSD ソケットの禁止"
    regex: '(?:\bDarwin\s*\.\s*|(?<![.\w])(?<!\.\s{1,16})(?<!func\s))(socket|connect)\s*\('
    match_kinds: *code
    message: "ソケットを直接使わない（SEC-NET-01）"
    severity: error

  no_legacy_speech_recognizer:
    name: "SFSpeechRecognizer の禁止"
    regex: '\bSFSpeechRecognizer\b'
    match_kinds: *code
    message: "旧来の音声認識は使わない。SpeechAnalyzer を使う（SEC-NET-01、BD-STT-10）"
    severity: error

  no_private_cloud_model:
    name: "端末外のモデルの禁止"
    regex: '\bPrivateCloudComputeLanguageModel\b'
    match_kinds: *code
    message: "オンデバイスのモデルだけを使う（SEC-NET-02）"
    severity: error

  # 条件（where 句など）と本体（コメントと空白だけ）の判定は先読みに置き、一致範囲をキーワードの「catch」に限る。
  # 一致範囲がキーワードだけになるので、コメントや文字列の中の「catch {}」を検出せず、
  # 条件に文字列を含む空の catch も見逃さない（一致範囲に文字列の種別が入ると match_kinds で外れるため）。
  # 条件は複数行にわたってよい。条件の中の文字列リテラル・行コメント・ブロックコメントは読み飛ばす
  # （中の「{」「{}」を本体の始まりや空の本体と取り違えない）。
  # ブロックコメントは 1 段の入れ子まで読む（/* 外 /* 内 */ 外 */ を 1 つのコメントとして扱う）。外側を閉じる「*/」の後に
  # 実行文がある catch を空と見なさない。正規表現では任意の深さの入れ子を数えられないので、2 段以上の入れ子は読み違えうる
  # 行コメントは条件側・本体側とも行末まで後戻りせずに読み飛ばす（*+）。コメント内の「{」「}」を本体の始まりや終わりと取り違えない
  no_empty_catch:
    name: "空の catch の禁止"
    regex: '\bcatch\b(?=(?:[^{"/]|/(?![/*])|//[^\n]*+|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|"(?:[^"\\\n]|\\.)*")*\{\s*(?://[^\n]*+\s*|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/\s*)*\})'
    match_kinds: *code
    message: "エラーを握りつぶさない（SEC-LOG-04）"
    severity: error

  no_direct_user_defaults:
    name: "UserDefaults の直接の利用の禁止"
    regex: '\b(UserDefaults|NSUserDefaults|AppStorage)\b'
    excluded:
      - '.*/Settings/SettingsStore\.swift'
    match_kinds: *code
    message: "UserDefaults は設定の型の中だけで使う（SEC-DAT-07）"
    severity: error

  # Documents は保存ルートの型の中でも使わない（保存先は Application Support に限る）。例外の場所を持たない
  no_documents_directory:
    name: "Documents ディレクトリの禁止"
    regex: '\.documentDirectory\b|\bdocumentsDirectory\b'
    match_kinds: *code
    message: "保存先は Application Support に限る（SEC-DAT-04）"
    severity: error

  no_direct_directory_access:
    name: "一時ディレクトリの直接の指定とディレクトリの直接の作成の禁止"
    regex: '\bNSTemporaryDirectory\b|\btemporaryDirectory\b|\bcreateDirectory\s*\('
    excluded:
      - '.*/Storage/StorageRoot\.swift'
      - '.*/TestSupport/TemporaryDirectory\.swift'
    match_kinds: *code
    message: "ディレクトリは保存ルートの型を経由して作る（SEC-DAT-01・03）"
    severity: error

  no_direct_pasteboard:
    name: "UIPasteboard と textSelection の直接の利用の禁止"
    regex: '\bUIPasteboard\b|\.textSelection\s*\('
    excluded:
      - '.*/Clipboard/ClipboardWriter\.swift'
    match_kinds: *code
    message: "コピーはコピーの型を経由する（SEC-PLT-01）"
    severity: error

  no_share_link:
    name: "ShareLink の禁止"
    regex: '\bShareLink\b'
    match_kinds: *code
    message: "共有は完了を受け取れる部品で行う（SEC-DAT-06）"
    severity: error

  no_webkit:
    name: "WebView の禁止"
    regex: '\bWKWebView\b|\bimport\s+(\w+\s+)?WebKit\b'
    match_kinds: *code
    message: "WebView を使わない（SEC-PLT-05）"
    severity: error

  no_spotlight_user_activity:
    name: "Spotlight と NSUserActivity の禁止"
    regex: '\bimport\s+(\w+\s+)?CoreSpotlight\b|\bCSSearchable\w+|\bNSUserActivity\b|\.userActivity\s*\(|\.onContinueUserActivity\s*\('
    match_kinds: *code
    message: "Spotlight・Siri にデータを渡さない（SEC-PLT-02）"
    severity: error

  no_direct_clock:
    name: "時計の直接の利用の禁止"
    # Date()・Date.init()・Date(timeIntervalSinceNow:) は現在時刻を読む。Date(timeIntervalSince1970:) などは認める
    regex: '\bDate\s*(?:\.\s*init\s*)?\(\s*(?:\)|timeIntervalSinceNow\s*:)|\bDate\.now\b|\bCFAbsoluteTimeGetCurrent\b|\b(ContinuousClock|SuspendingClock)\b|\bDispatchTime\.now\b|\bmach_(absolute|continuous)_time\b|\bsystemUptime\b'
    excluded:
      - '.*/SystemDependencies/.*\.swift'
    match_kinds: *code
    message: "時刻は注入した時計から得る（TST-GEN-01・07）"
    severity: error

  no_direct_uuid:
    name: "UUID の直接の生成の禁止"
    regex: '\bUUID\s*\(\s*\)|\bUUID\s*\.\s*init\s*\(\s*\)'
    excluded:
      - '.*/SystemDependencies/.*\.swift'
      - '.*/TestSupport/TemporaryDirectory\.swift'
    match_kinds: *code
    message: "UUID は注入した生成器から得る（TST-GEN-04・07）"
    severity: error

  no_direct_sleep_timer:
    name: "待機とタイマーの直接の利用の禁止"
    regex: '\bTask\.sleep\b|\bThread\.sleep\b|(?<![.\w])(?<!\.\s{1,16})u?sleep\s*\(|\bDarwin\s*\.\s*u?sleep\s*\(|\bTimer\.(scheduledTimer|publish)\b|\bmakeTimerSource\b|\.asyncAfter\s*\('
    excluded:
      - '.*/SystemDependencies/.*\.swift'
    match_kinds: *code
    message: "待機とタイマーは注入した型を経由する。実時間を待つテストは書かない（TST-GEN-01）"
    severity: error

  no_direct_random:
    name: "乱数の直接の利用の禁止"
    regex: '\.random\s*\(|\.randomElement\s*\(|\.shuffled\s*\(|\.shuffle\s*\(|\bSystemRandomNumberGenerator\b|\barc4random\w*\b|\bSecRandomCopyBytes\b'
    excluded:
      - '.*/SystemDependencies/.*\.swift'
    match_kinds: *code
    message: "乱数は注入した型から得る（TST-GEN-07）"
    severity: error
```

### 6.3 例外を認める場所（仮のパス）

例外は、規則ごとの `excluded`（パスの正規表現）で指定する。**型とファイルの名前は architecture の詳細設計で確定する。** 下のパスは仮のもので、確定したら、同じ作業で `.swiftlint.yml` と `tools/lint-fixtures/<規則名>/allowed/` を直す。

| 例外の場所 | 仮のパス | 対象の規則 |
| --- | --- | --- |
| ログの型 | `…/Logging/AppLogger.swift` | `no_direct_logger` |
| 設定の型 | `…/Settings/SettingsStore.swift` | `no_direct_user_defaults` |
| 保存ルートの型 | `…/Storage/StorageRoot.swift` | `no_direct_directory_access` |
| コピーの型 | `…/Clipboard/ClipboardWriter.swift` | `no_direct_pasteboard` |
| 注入用の本番実装 | `…/SystemDependencies/` 配下 | `no_direct_clock`・`no_direct_uuid`・`no_direct_sleep_timer`・`no_direct_random` |
| テスト用の一時ディレクトリの型 | `…/TestSupport/TemporaryDirectory.swift` | `no_direct_directory_access`・`no_direct_uuid`（テストごとに一意の一時ディレクトリを作るため。TST-GEN-02） |

テストコードにも独自ルールを適用する。テストで `Task.sleep`・`Date()`・乱数を使わせないためである（TST-GEN-01・07）。

### 6.4 テストコードで外す規則

`try!`・`as!`・強制アンラップは、テストコードでだけ認める（SEC-INP-03 はテストコード以外が対象）。組み込みの規則はパスごとの除外を持たないので、テストのディレクトリに入れ子の設定を置いて外す。

<!-- file: tools/lint-fixtures/_test-code/.swiftlint.yml -->

```yaml
# テストコードでだけ外す規則（SEC-INP-03 の例外）。この内容を変えるときは
# tools/lint-fixtures/_test-code/.swiftlint.yml と各テストのディレクトリの写しを同じ作業で直す。
disabled_rules:
  - force_try
  - force_cast
  - force_unwrapping
```

- 同じ内容を `MinutesMobileTests/`・`MinutesMobileUITests/`・`Packages/MinutesKit/Tests/` に `.swiftlint.yml` として置く（作業単位 2）。
- 規則の試験が、①これらのディレクトリにあって設定がない、②内容が上と 1 バイトでも違う、③これ以外の場所に入れ子の設定がある、のいずれも失敗にする。入れ子の設定は規則を外す抜け道になるためである。

### 6.5 規則の試験（`scripts/test-lint-rules.py`）

規則ごとに `tools/lint-fixtures/<規則名>/` を置く（BD-DEV-05）。

| ファイル | 内容 | 判定 |
| --- | --- | --- |
| `violation.swift` | 検出される例。検出されるはずの行の末尾に `// expect: <規則名>` を書く | 印の行の集合と、検出された行の集合が一致する |
| `ok.swift` | 検出されない例（コメント・文字列の中の名前、似た名前の別の API など） | 検出が 0 件 |
| `allowed/<例外のパス>` | 例外の場所に置いた違反 | 検出が 0 件 |
| `violation/<パス>`（任意） | 例外の場所に似たパスに置いた違反。例外を持たない規則で、パスによる抜け道がないことを確かめる（例: `no_documents_directory/violation/Storage/StorageRoot.swift`） | 印の行の集合と、検出された行の集合が一致する |

- 「1 件以上検出する」ではなく行の集合の一致で判定する。規則が弱くなった場合（3 つのうち 1 つしか検出しないなど）を見逃さないためである。
- 試験のない規則は作れない。規則に試験用のディレクトリがない、例外のある規則に `allowed/` がない、規則のないディレクトリが残っている、のいずれも失敗にする。
- 試験用のファイルは通常の解析と整形から除く（`.swiftlint.yml` の `excluded`、`.swiftformat` の `--exclude`）。試験では、ファイルを明示して渡すことで除外を越える。

### 6.6 規則で検出できないもの

正規表現による規則には限界がある。次は PR のチェックリスト（security-requirements.md §10.2）と、自動テスト（通信ゼロ。TST-SEC-01）で補う。

| 検出できない例 | 補う手段 |
| --- | --- |
| 型を省いた書き方（`let now: Date = .now`） | レビュー。注入した時計を使っていないテストは、時刻を固定できず不安定になって見つかる |
| 文字列から組み立てた呼び出し（`NSClassFromString("URLSession")`） | 通信ゼロの自動テスト（TST-SEC-01）、実機の確認（TST-SEC-02） |
| ログに D1〜D3 を入れていないか | 型付きのログ（BD-ARC-06）、TST-SEC-07 |
| 例外の場所の中での誤用 | レビュー（例外の場所を変える PR は、規則の変更として扱う） |

---

## 7. 整形と秘密情報の検出

満たす要件: TST-CI-03・04、SEC-SUP-03

<!-- file: .swiftformat -->

```text
# SwiftFormat の設定（TST-CI-03）。検査は --lint で行い、ファイルを書き換えない。
--swift-version 6.4
--language-mode 6
--exclude .tools,.build,build,DerivedData,tools/lint-fixtures
```

<!-- file: .gitleaks.toml -->

```toml
# gitleaks の設定（TST-CI-04、SEC-SUP-03）。既定の規則をそのまま使う。
# 検出を除外する指定（allowlist）は置かない。必要になったら理由を PR に書いてレビューを受ける。
[extend]
useDefault = true
```

- SwiftFormat は `--lint` で確認だけを行い、検査の中でファイルを書き換えない。直すときは開発者が `.tools/bin/swiftformat .` を実行する。
- gitleaks は既定の規則をそのまま使い、除外の指定を置かない。`--redact` を付け、検出した値そのものを出力に残さない（出力を PR に貼っても漏れない）。
- gitleaks の全体の検査はコミット済みの履歴を対象にする。未コミットの変更は、コミット前のフック（ステージした差分）が受け持つ。

---

## 8. プロジェクト設定の検査（`scripts/check-project-settings.py`）

満たす要件: TST-CI-06、SEC-NET-05、SEC-DAT-04、SEC-PRV-01 ／ 制約: CON-01・03・04

### 8.1 読むもの

| 対象 | 読み方 |
| --- | --- |
| `MinutesMobile.xcodeproj/project.xcproj` | JSON 形式だが末尾カンマを含む。文字列の外にある、閉じ括弧の直前のカンマだけを除いてから標準の JSON パーサーで読む（`scripts/xcproj.py`） |
| ビルド設定の `INFOPLIST_KEY_…` | 自動生成の Info.plist に入る項目。接頭辞を外して Info.plist の規則を当てる |
| `…Info.plist`・`….entitlements` | リポジトリ内のすべて（成果物と導入したツールのディレクトリを除く）。`plistlib` で読む |
| ビルド設定の `INFOPLIST_FILE`・`CODE_SIGN_ENTITLEMENTS` | 各ターゲット・各構成で効く値が指すファイル（名前や拡張子にかかわらず、Info.plist または entitlements として検査する）。`$(SRCROOT)`・`$(PROJECT_DIR)`・`$(TARGET_NAME)` を展開し、相対パスは `.xcodeproj` のあるディレクトリから解決する。ほかの変数を含む・ファイルがない・読めない場合は、読めないものとして失敗する |

- 条件付きの設定（`SWIFT_VERSION[config=Debug]` など）は、構成（`configurations` の各要素）ごとに効く値を求めて検査する。効く値は、ターゲットの構成の条件が一致する書き方 → ターゲットの条件のない書き方 → プロジェクトの構成の条件が一致する書き方 → プロジェクトの条件のない書き方、の順で最初に見つかったもの。ターゲットにある書き方だけを見ると、ターゲットに Debug だけ、プロジェクトに Release だけがある場合に Release の値を見落とすためである。構成以外の条件（`sdk=`・`arch=`）の書き方は、そのビルドで効きうるのですべて検査する。SDK を指定して判定する場合（下の利用目的）は、`sdk=` の条件を版つきの SDK 名（`xcrun --sdk <SDK> --show-sdk-version` から作る `iphoneos27.2` など。Xcode が照合する名前）と照合し、空の条件はどの SDK にも当たらないとする。版つきの名前を取得できない場合は、条件を無視せず読めないもの（終了コード 2）とする。どの段にも値がない構成は「設定されていない」とする。
- 空でない値が要る鍵（マイクの利用目的）は、ビルド設定の値ではなく、**各構成で最終的に生成される Info.plist** で判定する。`GENERATE_INFOPLIST_FILE` が `YES` の構成では `INFOPLIST_KEY_…` と `INFOPLIST_FILE` の参照先の両方を、`NO`（設定がない場合も `NO`）の構成では参照先だけを見る（`NO` では `INFOPLIST_KEY_…` が使われないため）。判定は構成と SDK（`iphoneos`・`iphonesimulator`）の組ごとに行い、`sdk=` の条件付きの書き方は条件に合う SDK でだけ効くとする。ビルド設定の値が一部の SDK の条件付きだけにある場合（例: `INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphonesimulator*]` だけ）、条件に合わない SDK では参照先の Info.plist が鍵を持たなければ欠落とする（`resolve` が返す「決まらない組」を捨てずに使う）。`GENERATE_INFOPLIST_FILE` が `arch=` などの条件付きだけで決まらない組では、生成しない場合も起こりうるとして参照先の Info.plist も求める。`INFOPLIST_FILE` の参照先が条件付きだけで決まらない組では、参照先で補ったことにしない（別の arch の参照先で補わない）。どこにも鍵がない組は欠落として報告する（例: 構成 Debug（iphoneos））。どちらが優先されるかに頼らないよう、効きうる値は**すべて**空でないこと（条件のない値に説明文があっても、Release の条件付きの値や参照先の値が空なら違反）。
- **読めないものを合格にしない。** 形式が違う・壊れているファイルは、終了コード 2 で失敗する。

### 8.2 規則

<!-- excerpt: scripts/check-project-settings.py -->

```python
# すべてのターゲットで満たす値: 設定名 → (許す値, 根拠)
REQUIRED_ALL_TARGETS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "TARGETED_DEVICE_FAMILY": (("1",), "CON-03"),
    "SUPPORTED_PLATFORMS": (("iphoneos iphonesimulator", "iphonesimulator iphoneos"), "CON-03"),
    "SWIFT_VERSION": (("6.0", "6"), "CON-04"),
    "SWIFT_TREAT_WARNINGS_AS_ERRORS": (("YES",), "TST-CI-01"),
    "IPHONEOS_DEPLOYMENT_TARGET": (("27.2",), "CON-01"),
}
# アプリのターゲットだけで満たす値
REQUIRED_APPLICATION: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": (("UIInterfaceOrientationPortrait",), "BD-UI-10"),
}
# アプリのターゲットで、各構成で最終的に生成される Info.plist に空でない文字列が要る鍵
REQUIRED_PLIST_VALUES_APPLICATION = {"NSMicrophoneUsageDescription": "SEC-PRV-01"}
# あってはならないビルド設定
FORBIDDEN_BUILD_SETTINGS = {"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad": "BD-UI-10"}

# Info.plist: あるだけで違反になる鍵
FORBIDDEN_PLIST_KEYS = {
    "NSAppTransportSecurity": "SEC-NET-05",
    "CFBundleURLTypes": "SEC-PLT-04",
    "NSUserActivityTypes": "SEC-PLT-02",
}
FORBIDDEN_PLIST_KEY_PREFIXES = {"NSAllows": "SEC-NET-05", "NSException": "SEC-NET-05"}
# Info.plist: 有効にすると違反になる鍵
FORBIDDEN_WHEN_ENABLED = {
    "UIFileSharingEnabled": "SEC-DAT-04",
    "LSSupportsOpeningDocumentsInPlace": "SEC-DAT-04",
    "UISupportsDocumentBrowser": "SEC-DAT-04",
}
# 利用目的の文言は、使う権限のものだけを置く（TST-SEC-03）
ALLOWED_USAGE_DESCRIPTIONS = {"NSMicrophoneUsageDescription"}
ALLOWED_BACKGROUND_MODES = {"audio", "processing"}
# 拡張はウィジェット（Live Activity）だけを認める。共有拡張などは置かない（SEC-PLT-04）
ALLOWED_EXTENSION_POINTS = {"com.apple.widgetkit-extension"}
# 利用目的の文言を SDK ごとに確かめるときの SDK（CON-03 の SUPPORTED_PLATFORMS と同じ）
SDKS = ("iphoneos", "iphonesimulator")

# entitlements: あってはならない鍵（Personal Team で使えない、または使わない capability）
FORBIDDEN_ENTITLEMENTS = {
    "aps-environment": "CON-02（Push 通知）",
    "com.apple.developer.associated-domains": "SEC-PLT-04",
    "com.apple.developer.siri": "SEC-PLT-02",
    "com.apple.developer.usernotifications.time-sensitive": "CON-02",
}
FORBIDDEN_ENTITLEMENT_PREFIXES = {
    "com.apple.developer.icloud": "SEC-NET-01（iCloud）",
    "com.apple.developer.ubiquity": "SEC-NET-01（iCloud）",
}
```

- 利用目的の文言は `NSMicrophoneUsageDescription` だけを認める。音声認識の利用目的は置かない（§14.2）。
- Bundle Identifier が仮の値（`devplaceholder`）のままの場合は、失敗ではなく警告にする。確定はオーナーの判断で、最初の実録音の前に行う（CON-09、BD-DEV-06）。
- 共有拡張かどうかは、Info.plist の拡張の種類で判定する。ウィジェット（Live Activity）だけを認める。

---

## 9. 依存の許可リスト

満たす要件: TST-CI-05、SEC-SUP-01・02、NFR-MNT-03 ／ 制約: CON-07 ／ 決定: UD-05

### 9.1 許可リスト（`tools/dependency-allowlist.json`）

<!-- file: tools/dependency-allowlist.json -->

```json
{
  "formatVersion": 1,
  "allowedLicenses": ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"],
  "approvedPackages": []
}
```

MVP では承認済みの依存は 0 件である（BD-ARC-07）。依存を足すときは `approvedPackages` に次の項目を持つ要素を足す。

| 項目 | 内容 |
| --- | --- |
| `identity` | パッケージの名前（`Package.resolved` の `identity`） |
| `url` | リポジトリの URL。正規化では末尾の `.git`・スラッシュと、scheme とホストの大文字小文字だけをそろえ、パスの大文字小文字は区別する |
| `version` | 承認した版（この版だけが通る） |
| `license` | SPDX の識別子 |
| `approvedOn` | 承認日（`YYYY-MM-DD`） |
| `ledger` | 台帳の該当箇所 |
| `ownerDecision` | `allowedLicenses` にないライセンスの場合に必須。オーナーの決定（`UD-nn`） |

ローカルのバイナリ（`.binaryTarget(path:)`）を承認する要素は、`url` の代わりに `path`（リポジトリの起点からの相対パス。`Package.swift` のディレクトリと宣言の `path:` をつないで正規化したものと照合する）を持ち、ほかの項目（`identity`・`version`・`license`・`approvedOn`・`ledger`、必要なら `ownerDecision`）は同じく必須とする。`url` と `path` の両方を持つ・どちらも持たない要素は、読めないものとして終了コード 2 で失敗する。

### 9.2 検査（`scripts/check-dependencies.py`）

| 対象 | 規則 |
| --- | --- |
| `Package.resolved`（すべて） | 各依存の名前と版が、承認済みの要素と一致する。名前と版が一致しても、取得元（`location`。古い形式は `repositoryURL`）を正規化して承認済みの URL と比べ、違う・ない場合は認めない。版で固定していない依存（ブランチ・コミットの指定）は認めない |
| `Package.swift`（すべて。版別の manifest `Package@swift-<版>.swift` も同じ検査にかける） | 先にコメント（`//`・入れ子にできる `/* */`）を空白に置き換える（文字列リテラルの中の `//` は残す。閉じていないブロックコメント・文字列リテラル・補間は読めないものとして失敗する。末尾まで文字列と見なすと後ろの宣言を隠すため）。宣言と `url:`・`path:`・`id:` などのキーワードは、さらに文字列リテラルの本文を空白に置き換えた内容から探し（文字列の中の `.package(` を宣言と見なさない）、取得元と版はその宣言の引数の中だけで調べる。`url:` で指す依存が、承認済みの URL と一致する。ローカルの Package（`.package(path:)`）は認める。ローカルのバイナリ（`.binaryTarget(path:)`）は、`path` の一致する承認済みの要素（§9.1）がある場合だけ認める。レジストリの依存（`id:`）は認めない。**`.package(…)`・`.binaryTarget(…)` の宣言をすべて列挙し、取得元が文字列リテラルだけの `url:` か `path:` でないもの（変数・連結・補間・その他の式）は、取得元を判定できないものとして認めない**。`url:` の依存は、版の指定（`from:`・`exact:`・`.upToNextMajor`・`.upToNextMinor`・`..<`・`...`）を、**同じディレクトリの** `Package.resolved` の確定版と照合する。確定版がない、ブランチ・コミットの指定、版の指定を文字列リテラルから判定できない、確定版が宣言の版の指定を満たさない（古い承認済みの確定版が残った場合を含む）、確定版が承認した版でない、のいずれも認めない |
| `project.xcproj` | リモートの URL の形をした文字列が、承認済みの URL と一致する。承認済みの URL は、**同じ `.xcodeproj` の中の** `Package.resolved` に同じ取得元の確定版がある。要求条件（版の指定）は書き方が未確認（§20 の断定しない事項）なので、確認して検査を実装するまでは判定できないものとして認めない |

- 許可リスト自体も検査する。`allowedLicenses` にないライセンスで `ownerDecision` がない要素、承認日のない要素があれば、終了コード 2 で失敗する。
- 追加の手順は台帳 §4 の 7 手順による。許可リストへの追加は、台帳 §3 への追記と**同じ PR で**行い、オーナーが PR のレビューで承認する（TST-CI-05）。費用が発生しうるもの・GPL 系・独自のライセンスは、オーナーの確認が記録されるまで足さない。

---

## 10. 文書の整合の検査（`scripts/verify-docs.py`）

満たす要件: NFR-MNT-04 ／ 決定: UD-06

| 検査 | 内容 |
| --- | --- |
| ID の定義 | 定義文書の表から ID を集める。定義の重複を失敗にする |
| ID の参照 | すべての文書（`docs/`・`README.md`・`CLAUDE.md`）が参照する ID が定義されている。`SEC-SUP-01〜05`・`NFR-MNT-01・03`・`SEC-NET-03 / 04`（スラッシュ区切り。「/」の前後の空白は 1 つまで）・`FR-REC 群` のような省略した書き方も展開して調べる。範囲（〜）の要素数が同じ接頭辞の定義済みの ID の数を超える書き方は展開せず、不正として報告する（巨大な範囲の展開を防ぐ） |
| フェーズ | 定義文書の FR / NFR / SEC の行は、フェーズの列を持ち、値が MVP・P2・P3 のいずれかで始まる。列がない・読めない行は行番号つきで失敗する |
| MVP の網羅 | フェーズが MVP の FR / NFR / SEC が、基本設計の各文書（索引の README.md を除く）の「満たす要件」（節の冒頭の「満たす要件:」と文書の冒頭の表の「\| 満たす要件 ID \|」の行だけ。「満たす要件の例」などの説明文は数えない）の、「／」より前（決定・関連などの参照を除く）のどこかに現れる。索引は「SEC 全般」や群の表記でまとめて挙げるため、判定に含めると欠落を隠す |
| 書式 | 相対リンクの先がある。コードフェンスに言語の指定がある。1 文書が 2,000 行未満である。フェンスは開始行の文字（バッククォートかチルダ）と長さを覚え、同じ文字が開始以上の長さで並び、その後が空白だけの行でだけ閉じる（長いフェンスの中の短いフェンスで切り替わらない）。開始・終了とも字下げは空白 0〜3 個に限り、4 個以上の字下げの行はコードの本文として扱う（CommonMark）。この判定は ID の走査・節の区切り・コード片の照合と書き直しで共通に使う |
| 節の構成 | 基本設計・詳細設計（配下のディレクトリの文書を含む）の各節の冒頭に「満たす要件:」がある（「制約:」だけの行は認めない。制約は「／」の後に書く）。「満たす要件:」の後（「／」の前まで）には、定義済みの要件 ID（FR・NFR・SEC・TST・CON・INV）、要件の群、「SEC 全般」、満たす要件がないことを示す「—」のいずれかがある。各文書に「セキュリティ」節と「テスト観点」節がある。詳細設計には「申し送り」の節と「要件 ID ↔ テスト」の節もあり、後者には先頭の列に定義済みの要件 ID を持ち、ほかの列（検査・試験）が空でない対応表の行が 1 行以上ある |
| 申し送りの解決 | 対応する基本設計（詳細設計からの相対パスが同じ文書）の「詳細設計への申し送り」の各項目が、詳細設計の「申し送り」の節の表の先頭の列に、項目名（前後の空白と末尾の「。」を除く）で一度ずつ現れる。欠落・重複・基本設計にない行を失敗にする（行数だけを比べると、重複した行が欠落を埋めて合格になるため） |
| コード片 | フェンスの外にある印の直後のコード片が実ファイルと一致する（下）。フェンスの中の印は説明用の例として、照合にも書き直しにも使わない |
| 確認日 | 確認日から 3 か月を過ぎた記述のある文書を警告する（失敗にはしない） |
| 読むもの | `--root` の下に `docs/` と定義文書（要件・SEC・TST・COST・`docs/README.md`）がなければ、何も調べずに通らないよう終了コード 2 で失敗する |

コード片の印:

| 印 | 意味 |
| --- | --- |
| `<!-- file: パス -->` | 直後のコード片が、そのファイルの全文と一致する |
| `<!-- excerpt: パス -->` | 直後のコード片が、そのファイルの連続した一部と一致する |

- 実ファイルを変えたら、`scripts/verify-docs.py --update-snippets` で全文のコード片を書き直す。一部のコード片は手で直す。どちらも、食い違いが残れば手順 10 が失敗する。
- 印のないコード片（`Package.swift`・`PrivacyInfo.xcprivacy` など、まだ作っていないファイル）は照合しない。ファイルを作る作業で印を付ける。

---

## 11. Package とターゲットの構成

満たす要件: NFR-MNT-01、TST-CI-01・02・07・08 ／ 制約: CON-04 ／ 決定: BD-DEV-04

### 11.1 `Packages/MinutesKit/Package.swift`（作業単位 2 で作成）

中核ロジックの Package（BD-ARC-02）。下の内容を一時ディレクトリに置き、Swift 6.4.2 でビルドとテストが通ること、未使用の値の警告がエラーになることを確かめた（2026-11-16）。

```swift
// swift-tools-version: 6.4
import PackageDescription

let package = Package(
    name: "MinutesKit",
    platforms: [
        .iOS("27.2"),
        .macOS("27.2"),
    ],
    products: [
        .library(name: "MinutesKit", targets: ["MinutesKit"]),
    ],
    targets: [
        .target(
            name: "MinutesKit",
            swiftSettings: [.treatAllWarnings(as: .error)]
        ),
        .testTarget(
            name: "MinutesKitTests",
            dependencies: ["MinutesKit"],
            swiftSettings: [.treatAllWarnings(as: .error)]
        ),
    ],
    swiftLanguageModes: [.v6]
)
```

| 項目 | 設定 | 理由 |
| --- | --- | --- |
| プラットフォーム | iOS 27.2（CON-01）と macOS 27.2 | Mac 上の `swift test` で回すため（BD-DEV-04）。macOS は開発機の版に合わせた |
| 言語モード | Swift 6 | CON-04 |
| 警告 | エラーとして扱う | TST-CI-01 |
| 外部の依存 | なし | BD-ARC-07 |
| ターゲット | 当面は `MinutesKit` と `MinutesKitTests` の 2 つ | Package の中の分割（層ごとのターゲット）は architecture の詳細設計で確定する |

### 11.2 ターゲット

| ターゲット | 追加する時期 | 入れ子の設定（§6.4） |
| --- | --- | --- |
| `Packages/MinutesKit`（`MinutesKit`・`MinutesKitTests`） | 作業単位 2 | `Packages/MinutesKit/Tests/.swiftlint.yml` |
| `MinutesMobileTests`（Swift Testing） | 作業単位 2 | `MinutesMobileTests/.swiftlint.yml` |
| `MinutesMobileUITests`（XCUITest） | 作業単位 2 | `MinutesMobileUITests/.swiftlint.yml` |
| `MinutesMobileWidgets`（Live Activity） | ui の実装時 | — |

- アプリのスキームのテストに `MinutesKitTests` も入れ、同じテストをシミュレータでも実行する（BD-DEV-04）。
- テストは並列で実行する。順序と共有の状態に依存したテストを早く見つけるためである（基本設計 §4）。

---

## 12. プロジェクト設定の変更（作業単位 2 で実施）

満たす要件: NFR-MNT-01、SEC-NET-05、SEC-DAT-04、SEC-PRV-01 ／ 制約: CON-01〜04・09 ／ 決定: BD-DEV-06

設定の名前と値は、Xcode 27.2 の定義ファイル（`Swift.xcspec` など）で確かめた。

### 12.1 `project.xcproj` のターゲット `MinutesMobile`

| 設定 | 現在 | 変更後 | 根拠 |
| --- | --- | --- | --- |
| `TARGETED_DEVICE_FAMILY` | `1,2,7` | `1` | CON-03 |
| `SUPPORTED_PLATFORMS` | `iphoneos iphonesimulator macosx xros xrsimulator` | `iphoneos iphonesimulator` | CON-03 |
| `SWIFT_VERSION` | `5.0` | `6.0` | CON-04 |
| `SWIFT_TREAT_WARNINGS_AS_ERRORS` | なし | `YES` | TST-CI-01 |
| `INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone` | 縦と横 | `UIInterfaceOrientationPortrait` | BD-UI-10 |
| `INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad` | 全方向 | 削除 | BD-UI-10 |
| `INFOPLIST_KEY_NSMicrophoneUsageDescription` | なし | 基本設計 §3.2 の文言 | SEC-PRV-01 |
| `PRODUCT_BUNDLE_IDENTIFIER` | 仮の値 | オーナーが決めた値 | CON-09、BD-DEV-06 |

- `SWIFT_DEFAULT_ACTOR_ISOLATION`（`MainActor`）と `SWIFT_APPROACHABLE_CONCURRENCY` はそのままにする（基本設計 §3.1）。
- macOS・visionOS 向けの設定（`LD_RUNPATH_SEARCH_PATHS[sdk=macosx*]`・`ENABLE_APP_SANDBOX`・各 OS の deployment target など）は害がないので、削除は任意とする。
- Bundle Identifier が決まっていなくても、ほかの変更は先に進められる（検査は警告にとどめる。§8.2）。**決まるまで、実機で実際の会議を録音しない。**

### 12.2 Info.plist のファイルに書くもの

`UIBackgroundModes` と継続処理タスクの識別子（`BGTaskSchedulerPermittedIdentifiers`）は、`INFOPLIST_KEY_…` のビルド設定として用意されていない（Xcode 27.2 の定義ファイルで確認）。Info.plist のファイルを作り、自動生成の内容と合わせる。

| 鍵 | 値 | 根拠 |
| --- | --- | --- |
| `UIBackgroundModes` | `audio`（継続処理タスクに必要なら `processing` も） | FR-REC-05 |
| `BGTaskSchedulerPermittedIdentifiers` | 継続処理タスクの識別子（transcription の詳細設計で確定） | NFR-PWR-03 |

### 12.3 変更の後に通るようになる検査

| 手順 | 条件 |
| --- | --- |
| 1 | `ContentView.swift` の import を並べ替える（テンプレートのコードを置き換える時点で解消する） |
| 4 | §12.1 の変更（Bundle Identifier を除く） |
| 7・9 | §11 の Package とテストターゲット |

すべて通ったら、`scripts/setup.sh --hooks` でフックを有効にする（§5）。

---

## 13. 差し替え用の起動の引数

満たす要件: TST-CI-08、TST-GEN-04 ／ 決定: BD-DEV-04

UI テストは、録音・音声認識・空き容量・保存ルートを差し替えた構成でアプリを起動する（基本設計 §4）。引数の名前は仮のもので、ui と architecture の詳細設計で確定する。

| 引数 | 値 | 差し替えるもの |
| --- | --- | --- |
| `-uiTestMode` | `1` | 差し替え全体を有効にする。これがなければ、下の引数はすべて無視する |
| `-uiTestStorageRoot` | 一時ディレクトリのパス | 保存ルート（テストごとに一意。TST-GEN-02） |
| `-uiTestConsent` | `none`・`accepted` | 同意確認の状態 |
| `-uiTestMicrophone` | `granted`・`denied`・`undetermined` | マイクの権限（TST-INT-06） |
| `-uiTestAudioInput` | `sine`・`silence`・`none` | 録音の入力（合成した音声。TST-GEN-08） |
| `-uiTestSpeechEngine` | `fake`・`unavailable` | 音声認識エンジン |
| `-uiTestFreeSpace` | バイト数 | 空き容量 |

本番のビルドで無効にする方法:

- 引数を読むコードを `#if DEBUG` の中に置き、Release のビルドに含めない。
- 引数から構成を決める処理は純粋関数（引数の一覧・ビルドの種類 → 構成）にし、「Release では、引数があっても本番の構成になる」ことを単体テストで確かめる（基本設計 §13）。
- 引数の値は外部入力として検証する。保存ルートは、アプリの一時領域の下にあるパスだけを受け付ける。値が不正な場合は起動を止める（差し替えたつもりで本番の保存先に書くことを防ぐ）。

---

## 14. プライバシーマニフェストと権限

満たす要件: SEC-PRV-01・03

### 14.1 `MinutesMobile/PrivacyInfo.xcprivacy`（作業単位 2 で作成）

理由コードを Apple の一覧と照合して確定した（2026-11-16）。鍵の名前は Xcode 27.2 内の定義と一致することを確かめた。

| 申告する API の種類 | 理由コード | Apple の説明（要旨） | 使う場面 |
| --- | --- | --- | --- |
| ファイルの日時（`NSPrivacyAccessedAPICategoryFileTimestamp`） | `C617.1` | アプリのコンテナ内のファイルの日時・大きさなどのメタデータを読む | 自分のファイルの確認 |
| ディスクの空き容量（`NSPrivacyAccessedAPICategoryDiskSpace`） | `E174.1` | 書き込めるだけの空きがあるかを確かめる。空き容量に応じた動作が利用者に見える | 空き容量の 3 段階の縮退 |
| `UserDefaults`（`NSPrivacyAccessedAPICategoryUserDefaults`） | `CA92.1` | アプリ自身だけが読み書きする情報 | アプリの設定 |
| システムの起動時間（`NSPrivacyAccessedAPICategorySystemBootTime`） | `35F9.1` | アプリ内の事象の間の経過時間を測る | 単調時計 |

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSPrivacyTracking</key>
    <false/>
    <key>NSPrivacyTrackingDomains</key>
    <array/>
    <key>NSPrivacyCollectedDataTypes</key>
    <array/>
    <key>NSPrivacyAccessedAPITypes</key>
    <array>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategoryFileTimestamp</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>C617.1</string>
            </array>
        </dict>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategoryDiskSpace</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>E174.1</string>
            </array>
        </dict>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>CA92.1</string>
            </array>
        </dict>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategorySystemBootTime</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>35F9.1</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
```

- 申告は、実際に使う API に合わせる。Apple の一覧でシステムの起動時間の対象になっているのは `ProcessInfo.systemUptime` と `mach_absolute_time()` である。単調時計の実装がこれらを使わない場合は、`35F9.1` の項目を外す（architecture の詳細設計で実装を決めた時点で確定する）。
- 実装が進んで、理由の申告が必要な API を新しく使うことになったら、同じ作業でこのファイルと本節を直す。

### 14.2 音声認識の利用目的

**`NSSpeechRecognitionUsageDescription` は置かない。**

- Apple の文書「Asking permission to use speech recognition」は、許可の手続きを「`SFSpeechRecognizer` を使う音声認識にだけ当てはまる」とし、`SpeechAnalyzer` の文字起こしのモジュールは利用者の音声を Apple のサーバーへ送らないと説明している（確認日 2026-11-16）。
- iOS 27.2 SDK で、この鍵を要求しているのは `SFSpeechRecognizer.requestAuthorization` だけである。`SpeechAnalyzer` 側のインターフェースに、許可に関する API はない。
- `SFSpeechRecognizer` は使わない（BD-STT-10。静的解析で禁止）。使わない権限の文言は置かない（TST-SEC-03）ので、設定の検査はこの鍵を違反として扱う。
- 実機で文字起こしを初めて動かすとき（TST-STT-07）に、許可の確認が出ないこと・この鍵がなくても落ちないことを確かめる。結果が違った場合は、本節・設定の検査・基本設計 §3.2 を同じ作業で改める。

---

## 15. 実機試験の記録

満たす要件: SEC-SUP-04・05 ／ 決定: UD-06

- 置き場所: [../04-test-records/](../04-test-records/README.md)。試験 1 回を 1 行で記録する。
- 様式は test-requirements.md §3.7 のとおり（日付・ビルドのコミット・端末機種・OS 版・試験 ID・結果・観測値・不合格の理由）。
- 録音データそのものは置かない。置くのは件数・結果・ハッシュだけである（SEC-SUP-05）。`.gitignore` が音声ファイルを除外する。
- 「検査が通った」ことと「実機試験が済んだ」ことは別に記録する（test-requirements.md §6）。

---

## 16. セキュリティ

満たす要件: SEC-SUP-01〜05、SEC-NET-01・05、SEC-DAT-04 ／ 制約: CON-07 ／ 脅威: T-03・10・11 ／ 決定: UD-05

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| 開発ツールのすり替え | 版と配布ファイルの SHA-256 を記録し、一致しなければ展開しない。検査は `.tools/bin` だけを使い、PATH にある別の版を使わない | SEC-SUP-01、T-10 |
| 配布ファイルの展開 | 取得したファイルは、照合が済むまで展開しない。展開は空の一時ディレクトリで行い、決めた名前の実行ファイルと LICENSE だけを取り出す | SEC-SUP-01 |
| 外部入力の検証 | `tools/versions`・許可リスト・`project.xcproj`・Info.plist は形式を確かめてから使う。読めないものは合格にしない | SEC-SUP-01・02 |
| 依存の混入 | 許可リストにない依存、版で固定していない依存を失敗にする | SEC-SUP-02、T-10 |
| 秘密情報 | コミット前と全体の検査で gitleaks。検出した値は出力に残さない | SEC-SUP-03、T-11 |
| 録音データの混入 | `.gitignore` と運用（§15） | SEC-SUP-04・05 |
| 意図しない通信 | 静的解析（§6）と通信ゼロの自動テスト（TST-SEC-01） | SEC-NET-01、T-03 |
| 設定の逸脱 | 設定の検査（§8） | SEC-NET-05、SEC-DAT-04 |
| 検査の迂回 | 検査の自己試験を検査の中で回す。入れ子の設定は決めた場所と内容だけを認める。検査の対象外にするディレクトリを作らない（自己試験の入力は実行時に一時ディレクトリへ作る） | TST-CI-05 |

**実装・レビュー時の注意点:**

- 検査スクリプト・規則・許可リストを変える PR は、検査を弱めていないかを必ず確かめる。規則の正規表現を変えたら、違反例と正常例も同じ PR で直す。
- 検査が「何も調べずに通る」状態を作らない。新しい検査には、失敗するはずの入力で失敗することを確かめる自己試験を付ける。
- 自己試験が通っただけでは足りない。検査の側をわざと壊して、自己試験が失敗することを確かめる（§17.2）。壊した後は必ず元に戻し、元に戻した状態で通ることも確かめる。
- 偽の鍵は、試験のときに実行時に組み立てる。リポジトリに文字列として置かない（gitleaks が検出する。除外の指定も増やさない）。
- 開発ツールを更新するときは、ライセンスが変わっていないかも確かめる。
- `gh` は配布ファイルの取得にだけ使い、`tools/versions` で版を固定する。取得したファイルの SHA-256 の照合は `gh` の版の固定の代わりにならないので、`gh` もほかのツールと同じく版と SHA-256 を記録し、`.tools/bin/gh` の実行時の版を照合する。

---

## 17. テスト観点

満たす要件: TST-CI-03・05・06、TST-SEC-03、SEC-SUP-01 ／ 決定: BD-DEV-05

### 17.1 検査の自己試験

| 対象 | 試験 | 件数 | 方法 |
| --- | --- | --- | --- |
| 独自ルールと入れ子の設定 | `scripts/test-lint-rules.py` | 22 規則・58 件 | 違反例の印の行と検出行が一致する。正常例と例外の場所では検出しない。入れ子の設定の試験用のファイル（`_test-code/Sample.swift`）は、通常の設定（`--config` で入れ子の設定を無効にする）では組み込みの 3 規則のすべてに違反することも確かめる（違反のないファイルで「外れる」を確かめても証拠にならない）。規則名の行は末尾のコメントを許し、規則名として読めない行は失敗にする |
| 設定の検査 | `scripts/test-checks.py settings` | 81 件 | 合格する設定で通り、項目ごとの違反で失敗する。マイクの利用目的は生成の有無・SDK の条件・参照先の Info.plist の組み合わせで、entitlements は `CODE_SIGN_ENTITLEMENTS` の参照先（拡張子が違うものを含む）で確かめる。読めない設定・参照先がない場合は終了コード 2 |
| 依存の検査 | `scripts/test-checks.py dependencies` | 73 件 | 許可リストにない依存・版の不一致・取得元の不一致・版で固定していない依存・取得元を判定できない宣言・対象外のライセンスで失敗する。コメントだけの宣言（行・ブロック・入れ子）は通り、閉じていないブロックコメントは終了コード 2。補間の中の文字列で宣言を隠せない |
| 版の照合 | `scripts/test-checks.py versions` | 11 件 | 版・SHA-256・形式・行の不足（必須のツールの行の欠落を含む）・`gh` の行の順序・版の表示の失敗で失敗する |
| 秘密情報の検出 | `scripts/test-checks.py gitleaks` | 2 件 | 実行時に組み立てた偽の鍵を検出する |
| 文書の検査 | `scripts/test-checks.py docs` | 59 件 | 未定義の ID・切れたリンク・言語のないコードフェンス・閉じていないフェンス・食い違うコード片・ID のない「満たす要件:」・申し送りの欠落と重複と余分な行・入れ子の設計書の不備などで失敗する。長いフェンス・チルダのフェンスで誤って切り替わらない。フェンスの中の印は照合にも書き直しにも使わない。「／」の後の関連の参照だけでは網羅にならない。存在しない root・定義文書の欠落は終了コード 2 |

自己試験の入力は、試験スクリプトの中で組み立てて一時ディレクトリに置く。リポジトリに違反例の設定ファイルを置くと、本物の検査がそれを避けるための除外が要り、抜け道になるためである。

### 17.2 自己試験が効いていることの確認（2026-11-16）

検査の側をわざと壊し（突然変異）、自己試験が失敗することを確かめた。

| 壊したもの | 件数 | 結果 |
| --- | --- | --- |
| 独自ルール（規則の削除、正規表現の弱体化、例外の削除、`match_kinds` の削除、関数宣言の除外の削除、コメントだけの `catch` の除外、入れ子の設定の変更、opt-in の削除） | 8 | すべて失敗した |
| 設定の検査の表（禁止・必須の一覧を 1 行ずつ消す、許す値を広げる） | 25 | すべて失敗した |
| 検査スクリプトの処理（条件付きの設定を見ない、文字列の中のカンマを区別しない、依存の照合を無効にする、導入記録の SHA-256 を照合しない など） | 7 | すべて失敗した |
| `check.sh`（手順の先頭に失敗するコマンドを入れる、手順の外で未定義の変数を使う） | 2 | どちらも終了コード 1 |
| 2026-11-16 の追加分（`check-dependencies.py`・`check-project-settings.py`・`verify-docs.py`・`setup-tools.sh` を変更前の版に戻す。`setup-tools.sh` の `gh` の行の順序の照合だけを外す、実行時の版の比較だけを外す） | 6 | 追加した試験のうち、それぞれ検出できなかった入力の試験が失敗した |
| 2026-11-18 の追加分（`no_documents_directory` に保存ルートの型の除外を戻す） | 1 | 失敗した（例外の場所がないのに `allowed/` がない、`violation/Storage/StorageRoot.swift` の検出行が不一致） |
| 2026-11-18 のレビュー対応の追加分（`no_empty_catch` の入れ子のコメントの読み取り・`no_bsd_socket` と `no_direct_sleep_timer` の空白を挟むメンバーの除外・組み込み規則の Sample の違反の確認・規則名の行の読み取り。設定の検査の arch の条件付きの参照先と生成の指定・版つきの SDK 名・開き括弧の直後のカンマ。依存の検査の閉じていない文字列と補間・版別の manifest・起点の確認。版の照合の必須のツール・版の表示の失敗。文書の検査のフェンスの字下げ（開始・終了）・対応表の行・広すぎる範囲・フェーズ・網羅の欄） | 23 | 試験を足した後は 22 件が失敗した。版つきの SDK 名を照合しない変更（既存の試験は SDK を指定しない経路を通っていた）と開始のフェンスの字下げを外す変更は、試験を足すまで生き残った。空の SDK の条件を除く分岐を外す変更は、`fnmatch` が空の条件に当たらず結果が同じ（等価な変更）なので生き残った |
| `check.sh` を変更前の版に戻し、`print` を含む内容をステージして作業ツリーだけを直す | 1 | 変更前は手順 2 が通過し、変更後は失敗した（作業ツリーではなくインデックスを検査している） |
| 文書の検査（未定義の ID を報告しない、範囲の省略を展開しない、MVP の網羅を調べない、コード片の不一致を報告しない など） | 15 | すべて失敗した |
| 2026-11-17 の追加分（`no_empty_catch` の条件の文字列の読み飛ばしを外す。依存のコメントの除去を呼ばない・文字列を扱わない・入れ子を数えない・補間を追わない・補間の中の文字列を読み飛ばさない。設定の未設定の生成を有効と見なす・生成しない構成でビルド設定を数える・ビルド設定や参照先の Info.plist を見ない・`CODE_SIGN_ENTITLEMENTS` を見ない。文書の申し送りの重複や余分な行を見ない・照合や書き直しでフェンスを無視する・入れ子の文書を見ない） | 16 | すべて失敗した |

この確認で見つけて直した不具合:

| 不具合 | 見つけた方法 |
| --- | --- |
| `NSAppTransportSecurity` の規則に対応する試験がなかった（別の規則が先に検出していた） | 表を 1 行ずつ消す突然変異が 1 件生き残った |
| 壊れた Info.plist で、終了コード 2 ではなく例外で落ちた（`plistlib` は `ExpatError` を投げる） | 「読めない Info.plist」の試験を足した |
| `check.sh` で手順の関数を `if` の中で呼んでおり、途中の失敗が通過になった | 手順の先頭に失敗するコマンドを入れた |
| `check.sh` がエラーで終了しても終了コードが 0 だった（EXIT の trap） | 同上の確認の途中で、実際に 0 が返った |
| `no_empty_catch` の一致範囲をキーワードに限ったとき、条件の文字列の中の `{` を本体の始まりと取り違えた（2026-11-17） | 条件に `"{}"` を含む空でない `catch` を正常例に足した |
| `no_empty_catch` が行コメントを後戻りして途中で切り、コメントの中の `{`・`}` を本体の始まり・終わりと取り違えた（2026-11-18） | `catch // {}` と `catch { // }` の空でない `catch` を正常例に足した。行コメントを後戻りしない（`*+`）ようにした |
| `no_direct_logger` が `Logger.init(…)` を検出しなかった（2026-11-18） | 違反例に足した |
| マイクの利用目的が `sdk=iphonesimulator*` の条件付きだけでも合格した（実機用の欠落を見落とした。2026-11-18） | 構成と SDK の組ごとに判定する試験を足した |
| `check.sh --staged` の規則の試験が作業ツリーの規則を読み、ステージした内容を試験していなかった（2026-11-18） | 古い規則をステージし、作業ツリーだけを直して手順 2 が失敗することを確かめた |
| 依存の検査のコメントの除去で、補間の中の文字列で外側の文字列が閉じたと見なし、偽のコメントで後ろの宣言を隠せた（2026-11-17） | 補間の中に `"/*"` を置く試験を足した。補間の中の文字列を読み飛ばさない突然変異が、括弧を含む文字列の試験を足すまで生き残った |
| `force_unwrapping` の既定の例外（`URL(string:)`・`UIImage(named:)` などに静的な文字列を渡した場合）で、テストコード以外の強制アンラップを見逃していた（2026-11-18） | 例外の一覧を空にし、違反例に `UIImage(named:)`・`URL(string:)` を足した |
| 依存の検査で、補間の中のコメントの `)`・`"` を補間や文字列の終わりと取り違えうる。行コメントの終わりを LF だけで判定していた（2026-11-18） | 補間の中のコメントを先に読み飛ばし、行コメントは CR・LF・CRLF で終わるようにした。補間の中のコメントと改行の 3 種の試験を足した（ファイルの読み込みは改行を LF にそろえるので、CR の不具合は単体の関数でだけ起きていた） |
| 設定の検査が除外するディレクトリの名前を全階層で照合し、下位の `build` などにある Info.plist を見落としうる。除外の配下も列挙していた（2026-11-18） | 除外を root 直下に限り、走査の前に枝刈りするようにした。下位の `Sources/build/Info.plist` を検出する試験を足した |
| `check.sh` の `test_destination` が、シミュレータの選択に失敗しても空の id で destination を出力していた（`$(…)` の中では `set -e` が効かない。2026-11-18） | 失敗を `return 1` で返すようにした |
| `check.sh --staged` の文書の整合が作業ツリーを読み、片方だけをステージした文書・実装の変更を通しえた（2026-11-18） | インデックス全体を書き出して照合するようにした |
| プッシュ前の検査が作業ツリーを検査し、未コミットの変更があっても警告だけで終了コード 0 を返した。HEAD 以外の参照を送る場合も、送るコミットを検査していなかった（2026-11-18） | `check.sh` に `--require-clean` を足し、プッシュ前のフックで使うようにした。フックは標準入力の送信する参照を読み、ローカルの SHA をコミットまでたどって（注釈付きのタグを含む）HEAD と違えば失敗する。複製したリポジトリで、変更なしは通過、追跡していないファイルあり・HEAD 以外の送信（HEAD の 1 つ前を指す注釈付きのタグを含む）は終了コード 1、HEAD を指す注釈付きのタグは比較を通ることを確かめた |
| 設定の検査の除外を root 直下に限ったため、下位の `DerivedData`・`.build`・`xcuserdata`（Package の `.build` など）の Info.plist を検査の対象にしていた（2026-11-18） | 成果物・個人の設定に固有の名前（`.build`・`DerivedData`・`xcuserdata`）はどの階層でも除外し、一般的な名前（`build`）と `.git`・`.tools` は root 直下だけで除外するようにした。下位の 3 種を検査しない試験を足し、全階層の除外を外すと失敗することを確かめた |
| 文書の検査がスラッシュ区切りの省略（`SEC-NET-03 / 04`）を展開せず、未定義の ID を見逃しえた。節の冒頭の「制約:」だけの行を「満たす要件:」の代わりに認めていた（2026-11-18） | スラッシュ区切りを並び（・）と同じに展開し、「満たす要件:」だけを必須にした。それぞれの試験を足し、変更前に戻すと失敗することを確かめた |

### 17.3 実行して確かめたこと（2026-11-16）

| 項目 | 結果 |
| --- | --- |
| 開発ツールの導入 | 3 つとも導入でき、版が一致した（約 7 秒）。再実行では取得しない。導入前の `--verify` は失敗する |
| SHA-256 が違う配布ファイル | 写しの値を書き換えて実際に取得した。展開せずに終了コード 1 で失敗し、一時ディレクトリは残らなかった |
| 設定の検査（実際のプロジェクト） | 違反 7 件で失敗した（対象デバイス・対応プラットフォーム・言語モード・警告・画面の向き 2 件・マイクの利用目的）。Bundle Identifier は警告 |
| フック（複製したリポジトリ） | `print` を含む Swift ファイル、偽の鍵を含むファイル、規則を弱めた `.swiftlint.yml` のコミットが止まった。違反のない変更はコミットできた |
| 手順 7・8（一時的に置いた Package） | `swift test` が通り、カバレッジを集計して目標未満の警告を出した |
| 検査の自己試験 | 76 件（設定 37・依存 12・版 6・秘密情報 2・文書 19）と、規則の試験 21 規則・55 件が通った。件数は規則や検査を足すと増える |
| コミット前の検査の所要時間 | 約 1 秒（Swift ファイル 2 件。規則の試験が回る場合は約 2 秒） |

**設計・実装時の注意点:**

- 検査の所要時間を記録する。コミット前の検査が遅くなると、飛ばされるようになる。
- テストが並列・順不同で通ることを、最初のテストを書いた時点から保つ。
- 検査を足したら、①失敗するはずの入力の自己試験、②検査を壊す確認、の両方を行う。

---

## 18. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

| 要件 | 検査・試験 | 実行する場所 | 状態 |
| --- | --- | --- | --- |
| TST-CI-01 | `check.sh` 手順 4（言語モードと警告の設定）・手順 6（ビルド） | プッシュ前 | 手順 6 は通過。手順 4 は作業単位 2 の後 |
| TST-CI-02 | `check.sh` 手順 7 | プッシュ前 | 作業単位 2 の後 |
| TST-CI-03 | `check.sh` 手順 1・2、`test-lint-rules.py` | コミット前・プッシュ前 | 手順 2 は通過。手順 1 は作業単位 2 の後 |
| TST-CI-04 | `check.sh` 手順 3、`test-checks.py gitleaks` | コミット前・プッシュ前 | 通過 |
| TST-CI-05 | `check.sh` 手順 5、`test-checks.py dependencies` | プッシュ前 | 通過 |
| TST-CI-06、TST-SEC-03 | `check.sh` 手順 4、`test-checks.py settings` | プッシュ前 | 作業単位 2 の後（自己試験は通過） |
| TST-CI-07 | `check.sh` 手順 8、`coverage-report.py` | プッシュ前 | Package の集計は確認済み。アプリ側は未実装 |
| TST-CI-08 | `check.sh` 手順 9 | PR の作成前 | 作業単位 2 の後 |
| SEC-SUP-01 | `check.sh` 手順 0、`test-checks.py versions` | プッシュ前 | 通過 |
| SEC-SUP-02 | `check.sh` 手順 5 | プッシュ前 | 通過 |
| SEC-SUP-03 | `check.sh` 手順 3 | コミット前・プッシュ前 | 通過 |
| SEC-SUP-04・05 | `.gitignore`、実機試験の記録の様式（§15） | レビュー | 設定済み |
| SEC-NET-05、SEC-DAT-04、SEC-PRV-01 | `check-project-settings.py` の規則（§8.2） | プッシュ前 | 自己試験は通過 |
| SEC-LOG-01 | 規則 `no_print_logging`・`no_direct_logger` | コミット前・プッシュ前 | 通過 |
| SEC-PRV-03 | `PrivacyInfo.xcprivacy`（§14.1） | レビュー | 作業単位 2 で作成 |
| NFR-MNT-04 | `check.sh` 手順 10、`test-checks.py docs` | コミット前・プッシュ前 | 通過 |
| NFR-MNT-01 | `check.sh` 手順 4・6、`Package.swift` の言語モード | プッシュ前 | 作業単位 2 の後 |
| NFR-MNT-03 | `check.sh` 手順 5（依存 0 件） | プッシュ前 | 通過 |

---

## 19. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [dev-environment.md](../02-basic-design/dev-environment.md) §14 の 7 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| 検査スクリプトとフックの中身、独自ルールの正規表現と試験用のファイル | 実装した。全文を本書に載せた | §4・§5・§6 |
| 開発ツールの導入スクリプト（`scripts/setup-tools.sh`）と、台帳の機械可読の写し（`tools/versions`）の置き場所と形式（§5.2） | `scripts/setup-tools.sh`、`tools/versions`（空白区切りの行形式） | §3 |
| 依存の許可リストの置き場所と形式（TST-CI-05） | `tools/dependency-allowlist.json` | §9 |
| Package の構成（プラットフォームの指定、ターゲット） | `Packages/MinutesKit/Package.swift` の内容を確定し、ビルドして確かめた。ファイルの作成は作業単位 2 | §11 |
| 差し替え用の起動の引数の一覧 | 7 つの引数と、本番のビルドで無効にする方法（名前は仮） | §13 |
| 実機試験の記録の置き場所（案: `docs/04-test-records/`）と様式 | `docs/04-test-records/` | §15 |
| 音声認識の利用目的の文言の要否、プライバシーマニフェストの理由コード | 利用目的は置かない。理由コードは 4 つとも Apple の一覧と照合して確定した | §14 |

---

## 20. 断定しない事項・確かめていない事項

満たす要件: SEC-SUP-01、SEC-PRV-03、TST-CI-02・07・08 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| 手順 7 のシミュレータでのテスト、手順 9、アプリ側のカバレッジ | テストターゲットがないので実行していない。コマンドは書いたが、通ることは確かめていない | 作業単位 2 |
| Intel の Mac での開発ツールの導入 | 行と SHA-256 は記録したが、実行していない | Intel の Mac を使うことになったとき |
| `Package.resolved` の形式と、`project.xcproj` でのパッケージの参照の書き方 | 依存が 0 件で実物がない。一般に知られた形式で検査を書いた。形式が違えば「読めない」で失敗する。`project.xcproj` の要求条件は書き方が分からないので、承認済みの依存があれば判定できないとして失敗させている | 最初の依存を足すとき（MVP では足さない） |
| 例外を認める場所のパス、起動の引数の名前 | 仮のもの | architecture・ui の詳細設計 |
| Xcode が書き出す `SWIFT_VERSION` の値 | 定義ファイルでは `6.0`。検査は `6.0` と `6` を認める | 作業単位 2（Xcode の画面で設定して確かめる） |
| 音声認識の許可が要らないこと | Apple の文書と SDK で確かめた。実機では確かめていない | TST-STT-07 |
| システムの起動時間の理由コード（`35F9.1`）の要否 | 単調時計の実装による | architecture の詳細設計 |
| 独自ルールが違反をすべて検出すること | 正規表現の限界がある（§6.6） | 規則の試験、PR のチェックリスト |
| フックが毎回実行されること | フックは飛ばせる。作業単位 1 では有効にしていない | 作業単位 2 の最後に有効にする。PR の説明の検査結果を統合の条件にする |

---

## 21. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- [GitHub: realm/SwiftLint releases](https://github.com/realm/SwiftLint/releases)（0.65.1。配布ファイルの `digest`、タグ 0.65.1 の `LICENSE` と `README.md`〈独自ルール・入れ子の設定〉）
- [GitHub: nicklockwood/SwiftFormat releases](https://github.com/nicklockwood/SwiftFormat/releases)（0.64.7。配布ファイルの `digest`、タグ 0.64.7 の `LICENSE.md`）
- [GitHub: gitleaks/gitleaks releases](https://github.com/gitleaks/gitleaks/releases)（v8.30.1。`gitleaks_8.30.1_checksums.txt`、タグ v8.30.1 の `LICENSE`）
- 各ツールのヘルプ（`swiftlint lint --help`・`swiftformat --help`・`gitleaks git --help`。導入した版で確認）
- Xcode 27.2 の定義ファイル（`Swift.xcspec`: `SWIFT_VERSION`・`SWIFT_TREAT_WARNINGS_AS_ERRORS` ほか。`INFOPLIST_KEY_…` の一覧）、`xcrun simctl list`
- iOS 27.2 SDK の `Speech.framework`（`SFSpeechRecognizer.h`、Swift インターフェース）
- [Apple Developer: Describing use of required reason API](https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api)、[NSPrivacyAccessedAPIType](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacyaccessedapitypes/nsprivacyaccessedapitype)（理由コードの一覧）
- [Apple Developer: Asking permission to use speech recognition](https://developer.apple.com/documentation/speech/asking-permission-to-use-speech-recognition)、[SpeechAnalyzer](https://developer.apple.com/documentation/speech/speechanalyzer)
