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
  gitleaks-history.sh         全履歴の秘密情報の検査（手順 3 と自己試験が同じコマンドを使う）
  check-project-settings.py   プロジェクト設定・Info.plist・entitlements の検査
  check-dependencies.py       依存の検査
  coverage-report.py          Package のカバレッジの集計
  find-simulator.py           テストに使うシミュレータの選択
  worktree-location.py        専用の作業ツリーの置き場所の判定と登録の照合・下の作業ツリーの有無（プッシュ前のフックと --require-clean が共用する）
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
| 1 | `tools/versions` の全体を先に検証する（各行の列の形式・アーキテクチャ・この Mac 向けの行の重複・`gh` の行が先にあること・必須のツールの行）。検証を終えるまで、どのツールも取得・導入せず、公開するリンクも更新しない（途中の行が不正な台帳で、前の行のツールだけを導入しない）。手順 2 以降は、検証を終えた行を台帳の順に行う |
| 2 | 版のディレクトリに実行ファイルと導入記録（配布ファイルの SHA-256）があり、記録が写しと一致すれば取得しない。版のディレクトリの実体が導入先の中のその位置にない（`.tools/<名前>`・`.tools/<名前>/<版>` が外へのリンク）ものは導入済みと扱わず、照合（`--verify`）でも失敗にする（`bin` の相対リンクは認める）。`.tools/<名前>` がリンクなら、リンクの先を書き換えずに止める |
| 3 | 記録したタグの配布ファイルを一時ディレクトリへ取得する。`gh` 自身は `curl` で公式のリリースの固定の URL（`https://github.com/<リポジトリ>/releases/download/<タグ>/<配布ファイル>`。HTTPS だけ。最初の引数の `-q` で `.curlrc` を読ませず、利用者の設定に転送を左右させない。接続に 30 秒、転送の全体に 300 秒の上限を付け、止まった転送は取得の失敗として終える）から、ほかのツールは手順 7 まで済ませた `.tools/bin/gh` の `gh release download` で取得する（全体に 300 秒の上限を付け、過ぎたら `gh` を止めて終わるのを待ち、取得の失敗として終える。SHA-256 の照合は変わらない。試験では `--download-limit <秒>`（1〜300）で短くする） |
| 4 | `shasum -a 256` の結果を写しと照合する。**一致しなければ展開せずに失敗する** |
| 5 | 一時ディレクトリに展開し、取り出した実行ファイルが記録した版を名乗ることを確かめてから（違えば既存の導入を残して失敗する）、実行ファイルと LICENSE だけを `.tools/<名前>/<版>/` へ移す（実行ファイルが最上位にない配布ファイルは、中にちょうど 1 つあるものを使う。`gh` は `gh_<版>_macOS_<arch>/bin/gh`） |
| 6 | `.tools/bin/<名前>` から版のディレクトリへシンボリックリンクを張る（同じディレクトリの一時の名前でリンクを作り、`mv` で置き換える）。手順 5 で同じ版の既存の導入を退避した場合は、リンクの更新が成功するまで退避を消さず、更新に失敗したら置いた導入をどけて退避を戻し、既存のリンクと導入を残して失敗する（中断の終了処理も同じく戻す）。リンクの置き換えから巻き戻しを解くまでの中断（INT・TERM）は控え、解いた後で従う（公開した新しい導入を巻き戻さない。置き換えに失敗した場合は解かずに戻す）。更新に成功したら、終了処理の巻き戻しを解いてから退避を消す。退避の削除に失敗しても巻き戻さず、場所を警告して新しい導入を使う |
| 7 | 実行して版を表示させ、写しと一致することを確かめる |

**初回の導入と GitHub の認証:** 手順 3 の `gh release download` は GitHub の認証を要する（`gh` 自身の取得は `curl` なので要らない）。認証のない環境で初めて実行すると、`gh` の導入までは済み、ほかのツールの取得で「配布ファイルを取得できない」として止まる。その場合は導入した `.tools/bin/gh auth login` で認証してから、`scripts/setup-tools.sh` を実行し直す（導入済みの `gh` は取得し直さない）。開発機の `gh` で認証済みの環境（認証の情報を共有する）や、環境変数 `GH_TOKEN` を設定した環境では、この手順は要らない。

`--verify` を付けると、取得せずに手順 2・7 の照合だけを行う（`check.sh` の手順 0）。版を表示するコマンドが失敗した場合は、表示の内容によらず不一致として扱う（導入済みとしても再利用しない）。版を表示するコマンドには 30 秒の上限（試験用に `--probe-limit` で変えられる）を付け、過ぎたらその子孫ごと止めて、失敗と同じに扱う（導入では取得し直し、`--verify` では失敗する。`gh` での取得の上限も子孫ごと止める）。台帳には、台帳に現れたツールに加えて、検査が使う 4 種（`gh`・`swiftlint`・`swiftformat`・`gitleaks`）の行がこの Mac 向けに 1 つずつ要る（台帳から行を消しても見落とさない）。一部だけの台帳で試験するときは `--require "<名前 …>"` で期待する集合を明示する。`gh` も同じく照合するので、取得に使う `gh` が記録した版であることを、取得のたびと手順 0 で確かめる（取得したファイルの SHA-256 の照合は、`gh` の版の固定の代わりにしない）。版の文字列だけでなく導入記録の SHA-256 も照合するので、「版は同じで中身が違う配布ファイル」に写しを差し替えた場合も、導入し直すまで失敗する。

導入（`--verify` なし）は、同じ導入先への並行の導入を `<導入先>/.install.lock` のディレクトリで排他する。ロックを作れない場合は理由を区別し、ロックのディレクトリがあるときだけ別の導入の実行中として待つ（最大 600 秒）。ないのに作れない場合（書き込めない導入先など）は、作れなかった直後に解放された場合に備えて 1 回だけ作り直し、それでも作れなければ `mkdir` のエラーを示して止める。ロックの位置にディレクトリ以外のもの（ファイル・シンボリックリンク）がある場合も待たずに止める（待っても解消しないものを 600 秒待たない）。ロックを作ってから取得を記録するまでの中断（INT・TERM）は控え、記録の後で従う（`check.sh` の `take_lock` と同じ。自分のロックを残さず、ほかの実行のロックを消さない）。導入では、ロックを取った後・導入を変える前に、`<導入先>/bin` がシンボリックリンクなら止める（リンクの先を書き換えない。導入先自体のリンクは認める）。

全文は [dev-environment-scripts.md](dev-environment-scripts.md) §1 にある（コード片が長いため分冊に置く）。

展開後の配置:

```text
.tools/
  bin/gh -> ../gh/2.104.0/gh                          ほかのツールの取得に使う
  bin/swiftlint -> ../swiftlint/0.65.1/swiftlint      検査スクリプトが呼ぶのは bin/ のリンクだけ
  bin/swiftformat -> ../swiftformat/0.64.7/swiftformat
  bin/gitleaks -> ../gitleaks/8.30.1/gitleaks
  gh/2.104.0/{gh, LICENSE, .archive-sha256}
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
| 3 | 秘密情報 | `gitleaks-history.sh`（`gitleaks git` で全履歴。検出した値は伏せる） | `test-checks.py gitleaks` | 通過（1 秒。67 コミット） |
| 4 | プロジェクト設定 | `check-project-settings.py` | `test-checks.py settings` | **失敗**: 違反 7 件（§12 の変更で解消する） |
| 5 | 依存 | `check-dependencies.py` | `test-checks.py dependencies` | 通過（1 秒） |
| 6 | ビルド | `xcodebuild build`（機種を特定しないシミュレータ向け） | — | 通過（15 秒） |
| 7 | 単体・結合テスト | `swift test`（Package）と `xcodebuild test`（UI テストを除く） | — | **失敗**: Package とテストターゲットがない |
| 8 | カバレッジ | `coverage-report.py`（初期は警告だけ） | `test-checks.py coverage` | **失敗**: Package がない（一時的に置いた Package では通過した。§17.3） |
| 9 | UI テスト | `xcodebuild test`（UI テストだけ） | — | **失敗**: テストターゲットがない |
| 10 | 文書の整合 | `verify-docs.py` | `test-checks.py docs` | 通過（1 秒） |

- 手順 10 は詳細設計で足した（基本設計 §6.1 も同じ作業で直した）。文書がこのリポジトリの正であり、コード片と実ファイルの一致（NFR-MNT-04）を人の目だけに頼らないためである。
- 検査の自己試験（§17）を同じ手順の中で回す。検査スクリプトを変えて検出できなくなった場合に、その場で失敗する。
- 手順の関数は、条件の外で直接呼ぶ（§2.3）。1 つでも失敗したら止まり、失敗した手順を表示して終了コード 1 を返す。

### 4.2 実行のしかた

| 呼び方 | 範囲 | 使う場面 |
| --- | --- | --- |
| `scripts/check.sh` | 0〜10 | 作業中の確認（作業ツリーを検査する。コミットの合格にはしない） |
| `scripts/check.sh --require-clean` | 0〜10。開始時のコミットを専用の作業ツリーに展開して検査する | PR の作成前と、PR の更新後（結果を PR の説明に貼る） |
| `scripts/check.sh --steps 0-8,10 --require-clean --expect-head <SHA>` | 指定した手順。未コミットの変更があるか、開始時の HEAD が指定したコミットでなければ、手順を始めずに失敗する | プッシュ前のフック |
| `scripts/check.sh --staged` | ステージしたファイルに対する 1〜3 と 10 | コミット前のフック |

`--staged` での違い:

| 手順 | `--staged` での動作 |
| --- | --- |
| 1・2 | ステージした Swift ファイルだけを見る（規則の試験用のファイルは除く）。**作業ツリーではなく、ステージした内容（インデックス）を検査する。** ステージした Swift ファイルと、インデックスにある設定（`.swiftformat`・各階層の `.swiftlint.yml`）の blob そのもの（`git cat-file blob`。手順 3 と同じく `checkout-index` を通さず、`.gitattributes` の smudge のフィルタ・改行の変換を掛けない）を、元の相対位置のまま一時ディレクトリへ書き出し、そこを起点に実行する（入れ子の設定と、パスで指定した除外が作業ツリーと同じに解決される。検査する内容はステージした内容とバイト単位で同じ）。SwiftLint には `--force-exclude` を付け、明示したファイルにも除外の指定を効かせる。一時ディレクトリは終了時に消す |
| 2 | 規則・試験用のファイル・入れ子の設定・テストのディレクトリ（`MinutesMobileTests`・`MinutesMobileUITests`・`Packages/MinutesKit/Tests`。入れ子の設定の置き忘れを検査するため）をステージしたときだけ、規則の試験を回す。**規則の試験もステージした内容で行う。** 規則（各階層の `.swiftlint.yml`）・試験用のファイル（`tools/lint-fixtures/`）・試験のスクリプトをインデックスから同じ一時ディレクトリへ blob のまま（`git ls-files -s` の項目ごとに `git cat-file blob`。`checkout-index` を通さず、smudge のフィルタ・改行の変換を掛けない。モード 100755 の実行権限を保ち、120000 はリンクとして作り、160000 は書き出さない。共通の関数 `write_index_entries`）書き出し、その中のスクリプトを実行する（スクリプトは自身の位置をリポジトリ直下とみなす）。開発ツールは照合した写し（`STAGED_TOOLS/.tools`。作業ツリーの `.tools` ではない）へのリンクで使い、入れ子の設定の列挙（`git ls-files`）は `GIT_DIR` と `GIT_WORK_TREE` でインデックスに対して行う |
| 3 | ステージした（追加・変更・名前の変更・種類の変更をした）ファイルの blob を `git cat-file blob` で加工せずに一時ディレクトリへ書き出し、`gitleaks dir` で検査する（ファイルの全体を見る）。8.30.1 の `gitleaks git --pre-commit --staged` は `git diff` を使い、`--log-opts` が効かないので、`.gitattributes` の `binary`・textconv で鍵の行を隠せるため使わない（8.30.1 で確かめた）。`.gitleaksignore` をステージしていれば検査せずに失敗する。一時ディレクトリは終了時に消す |
| 10 | **文書の整合もステージした内容で検査する。** インデックス全体（sparse checkout の skip-worktree の項目を含む）を、手順 2 と同じ `write_index_entries` で blob のまま一時ディレクトリへ書き出し（smudge のフィルタ・改行の変換を掛けない。モードを保つので `verify-docs.py` をそのまま実行できる）、その中の `scripts/verify-docs.py` を（通常の実行できるファイルで、それ自身も置き場所の `scripts` もシンボリックリンクでないことを確かめてから。リンクなら書き出した場所の外のスクリプトをステージした内容として実行しうるので、実行せずに失敗する） `--root` にその一時ディレクトリを指定して実行する。文書・コード片の元の実装・検査スクリプトがすべてインデックスの内容になるので、文書と実装の両方をステージした一致する変更は通り、片方だけをステージした変更はコード片の不一致で止まる。検査スクリプトの自己試験（`test-checks.py docs`）は通常の実行だけで回す |

- ファイルの一部だけをステージした場合も、コミットされる内容（インデックス）だけを検査する。作業ツリーで直しただけでステージしていない違反は、コミットを止める。
- 違反の表示は一時ディレクトリのパスになる（末尾の相対パスは元の位置と同じ）。
- 通常の実行（`--require-clean` なし）は作業ツリーを検査し、未コミットの変更の有無によらず「コミットの合格としては記録しない」と表示する。検査中に作業ツリーを書き換えて戻すと、開始時と終了時の状態が同じでも、コミットにない内容を検査しうるためである。
- `--require-clean` を付けた場合は、未コミットの変更（追跡していないファイルを含む）があれば、手順を始める前に終了コード 1 で失敗する。なければ、開始時のコミットをリポジトリの外の一時ディレクトリ（`TMPDIR`、なければ `/tmp` の下に `mktemp -d` で明示的に作る。既定の `mktemp -d` が `TMPDIR` を使わない環境があるため）に `git worktree add --detach` で展開し（一時の置き場所は、作業ツリーを作る前にプッシュ前のフックと同じ判定（`scripts/worktree-location.py check`。開始時のコミットから書き出して実行するので、元の作業ツリーが sparse checkout でこのファイルを持たなくてもよい）で確かめ、作業ツリーの中か、自身・祖先に `.swiftformat`・`.swift-version` がある場所なら失敗する。作業ツリーの外の、設定を継がない一時ディレクトリは認める。開発ツールは、元の作業ツリーの `.tools` を共有のリンクで指さず、プッシュ前のフックと同じく導入のロック（`.install.lock`）を取ってから専用の作業ツリーの `.tools` に写す。手順 0 の照合と後の手順はすべてこの写しを使い、検査の途中に別の `setup-tools.sh` が元の `.tools` を差し替えても、照合した導入と使う導入が食い違わない）、その中の `check.sh` に同じ引数を渡して検査する（元の作業ツリーを検査中に書き換えて戻しても、検査する内容は変わらない）。展開はプッシュ前のフックと同じく `-c core.autocrlf=false` で行い（LF のまま実行する）、元の作業ツリーが sparse checkout なら専用の作業ツリーでだけ `sparse-checkout disable` を実行して全体を展開する（`full_checkout`。§5 と同じ扱い。`GIT_CONFIG` を外して設定を読む。通常の実行の作業ツリーは変えない）。`--staged` でない実行は（`--require-clean` の有無によらず）、開始時の HEAD を読む前に `GIT_NO_REPLACE_OBJECTS=1` を設定し、照合・展開・入れ子の `check.sh`・手順 7・8 のカバレッジの入力の識別と書き出しのすべてを置き換えのオブジェクト（`git replace`）なしの元のコミットで行う。プッシュ前のフックも、参照の照合の前に同じ設定をする。展開の後に、専用の作業ツリーの追跡しているファイルがコミットの blob とバイト単位で一致することを確かめ（`verify_checkout_blobs`。通常のファイルは `git hash-object --no-filters`、シンボリックリンクはリンク先の文字列で比べる）、違えば手順を始めずに失敗する。`core.autocrlf=false` では `.gitattributes` の smudge のフィルタは止まらず、clean のフィルタで打ち消されると `git status` では変更が見えないためである。プッシュ前のフックも、専用の作業ツリーの再利用・作り直しのどちらの後でも同じ関数で確かめる。専用の作業ツリーで検査中に HEAD が変わった、または変更が生じた場合は、手順の後で失敗する。成功すると、そのコミットのハッシュを付けて表示する。PR の説明にはこの行を貼る（基本設計 §6.2）。専用の作業ツリーは終了時に、この実行で作ったものだけを `git worktree remove --force` で登録ごと消す。リポジトリ全体の `git worktree prune` は実行しない（ほかの作業ツリーの登録を外さない）。消せなければ場所を表示して失敗にし、置き場所も残す。専用の作業ツリーで実行中であることは環境変数 `MINUTES_CHECK_ISOLATED` で示す。ただし `--require-clean` は、引き継いだこの変数を、現在の作業ツリーがこの検査の専用の作業ツリーである場合だけ認める（`isolated_checkout`。作業ツリーの専用の管理ディレクトリに、作成時に書いた印 `minutes-check-owner`（`check.sh` が作ったもの）か `minutes-pre-push-owner`（プッシュ前のフックが作ったもの）があり、印の作業ツリーのパスが自身の実体のパスと一致すること）。そうでなければ変数を無視して開始時のコミットを専用の作業ツリーに展開する（元の作業ツリーで変数を引き継いだ直接の実行が、展開を省いて専用の作業ツリーで検査したと表示しない）。プッシュ前のフックは自身の専用の作業ツリー（§5）で実行するのでこれを設定して二重に展開しない（ビルドの成果物を再利用する）。`--staged`（インデックスを検査する）とは組み合わせない。
- `check.sh` は、パス指定の解釈を変える変数（`GIT_LITERAL_PATHSPECS`・`GIT_GLOB_PATHSPECS`・`GIT_NOGLOB_PATHSPECS`・`GIT_ICASE_PATHSPECS`。`git --literal-pathspecs commit` などがフックに引き継ぐ）を開始時に外す（残すと `*.swift` が別の意味になり、ステージしたファイルを選ばずに合格しうる）。手順 2 の規則の変更の有無の照会（`git diff --cached --quiet`）は、終了コード 0・1 以外を変更ありと見なさずに失敗にする。集めた名前での照会は `--literal-pathspecs` を付ける。
- `--staged` は、ステージしたファイルを選ぶ基点（`STAGED_BASE`）を開始時に 1 回だけ決め、3 か所の `git diff --cached`（整形・静的解析に渡す Swift のパス、秘密情報の検査に書き出すファイル、規則の試験を回すかの判定）で同じ基点を使う。基点は開始時の HEAD で、HEAD がまだない（最初のコミットの前）場合は空のツリーとし、ステージしたファイルをすべて選ぶ。HEAD が読めない理由がまだないこと以外なら止める。合格を表示する前に、HEAD が開始時と同じ（まだなかった場合は、まだない）ことを確かめ、変わっていれば終了コード 1（検査したファイルの集合とコミットされる差分が違いうるため）。
- `--staged` がステージした内容を書き出す一時ディレクトリ（`STAGED_TREE`。`make_staged_tree`）は、`TMPDIR`（なければ `/tmp`）の下に明示的に作り、ファイルを書く前に `--require-clean` と同じ判定（`worktree-location.py check`。ステージした `scripts/worktree-location.py` を書き出して実行する）で置き場所を確かめる。作業ツリーの中か、自身・祖先に `.swiftformat`・`.swift-version` がある場所なら、書き出さずに終了コード 1。インデックスでシンボリックリンクの設定（`.swiftformat`・`.swiftlint.yml`・`.swift-version`）があれば、ツールを実行する前に場所を表示して失敗する。
- 手順 1〜3（固定した開発ツールを使う手順）を、手順 0 なしで、または手順 0 より先に（`--steps 1,0` など）実行する場合（`--staged` を除く）は、手順を始める前に `setup-tools.sh --verify` で、使う導入をその作業ツリーの `tools/versions`（`--require-clean` では展開したコミットの台帳と、そこへ写した `.tools`）と照合する。手順 0 の自己試験（`test-checks.py versions`）は求めない。
- 手順 1〜3 を含む実行（`--staged` を除く）は、手順を始める前に `.tools` を導入のロックを取って一時ディレクトリへ写し（`snapshot_run_tools`。`copy_tools` を使う）、上の照合と手順 0 の照合は写しに対して行い（`--dest`）、実行の全体で写しだけを使う（照合の後に別の `setup-tools.sh` が元の `.tools` を差し替えても、照合していない導入で検査しない）。`test-lint-rules.py`・`gitleaks-history.sh`・`test-checks.py` には環境変数 `MINUTES_TOOLS_DIR` で写しの場所を渡す。`check.sh` は引き継いだ `MINUTES_TOOLS_DIR` を使わない（入口で外す）。写しは終了時に消す。手順 1〜3 を含まない実行は写しを作らない。
- `--require-clean` は、開始時の照合の後・専用の作業ツリーを作る前に `GIT_INDEX_FILE` を外し（呼び出し側のインデックスを専用の作業ツリーの git に使わせない）、`git worktree add` の後に `GIT_DIR`・`GIT_WORK_TREE` も外し（以後の git と入れ子の `check.sh` に元の作業ツリーを読み書きさせない。後始末の登録の削除は、先に控えた共通のディレクトリで行う）、入れ子の `check.sh` には引数の最後に `--expect-head <開始時のコミット>` を付ける（呼び出し側の `--expect-head` が指すものが変わっても、開始時のコミットだけを認める）。
- 起点のパスの末尾の改行は、`check.sh`（`--root` の有無によらず）・コミット前のフック・導入し直す手順・`setup.sh`・`setup-tools.sh`（導入先を含む）・`gitleaks-history.sh`（引数を含む）のどれでも保つ。ステージした内容を書き出す親のディレクトリも `dirname` を使わずに作り、名前の末尾の改行を保つ（`$(…)` は末尾の改行をすべて落とすので、印の文字を付けて受け取り、印と区切りの改行 1 つだけを外す）。
- `--staged` が整形・静的解析に渡すのは、追加・変更・名前の変更・種類の変更（`--diff-filter=ACMRT`）でステージした Swift のパスのうち、インデックスで通常のファイル（モード `100644`・`100755`）のものだけ。シンボリックリンク・サブモジュールの Swift のパスは、黙って除かずに失敗にする。
- `--expect-head <SHA>` を付けた場合は、開始時の HEAD（`START_HEAD`）を取得した直後に指定したコミットと照合し、違えば手順を始める前に終了コード 1 で失敗する。プッシュ前のフックは、送信する参照と照合した HEAD をこの引数で渡す（照合してから `check.sh` が始まるまでに HEAD が A から B に変わっても、B を A の合格として検査しない。A のままなら通る）。`--require-clean` の開始時・終了時の検査はそのまま行う。`--staged` とは組み合わせない。

全文は [dev-environment-scripts.md](dev-environment-scripts.md) §2 にある（コード片が長いため分冊に置く）。

### 4.3 シミュレータの選択（`scripts/find-simulator.py`）

テストに使う機種名を決め打ちにしない。導入済みのランタイムと機種は Mac ごとに違い、最低 OS（iOS 27.2。CON-01）より古いランタイムの機種を指定するとビルドが失敗する。

- 実例（2026-11-16、この Mac）: 「iPhone 17」は iOS 27.0 のランタイムにしかなく、`name=iPhone 17` の指定は「destination が見つからない」で失敗した。iOS 27.2 のシミュレータは「iPhone Duo」1 台だけだった。
- 規則: `xcrun simctl list -j` の結果から、最低 OS 以上のランタイムにある iPhone のうち、ランタイムの版が最も低いもの、同じ版の中では名前の順で最初のものを選ぶ（同じ環境では毎回同じ結果になる）。条件を満たすものがなければ失敗する（終了コード 1）。一覧の形が違う場合（`runtimes` と `devices` の各項目が辞書でない、機種の識別子 `deviceTypeIdentifier` が文字列でないなど）は、候補なしと取り違えず、取得の失敗（終了コード 2）とする（「iPhone」を含む配列を iPhone と取り違えない）。重複した鍵のある一覧と、使える iOS のランタイムの識別子の重複も同じく取得の失敗とする（後の版で前の版を隠さない）。UDID は UUID の形（8-4-4-4-12 桁の 16 進数。大文字小文字は区別しない）であることを求め、空でなくても UUID でない値は取得の失敗とする（destination の `id=` に渡さない）。版の各要素は ASCII の数字だけを認め、符号・空白・全角数字などを含む版は取得の失敗とする。
- `xcrun` が失敗したら、その標準エラーを続けて標準エラーに示す（終了コード 2。標準出力には UDID 以外を出さない）。
- `DEVELOPER_DIR` がない・空なら、`check.sh` と同じ既定（`/Applications/Xcode.app/Contents/Developer`）で `simctl` を実行する（単独の実行でも同じ Xcode を使う。利用者の指定は変えない）。
- ビルド（手順 6）は `generic/platform=iOS Simulator` を使い、特定の機種に依存しない。
- 一覧の形が違う場合（`devices` が辞書でないなど）は、取得の失敗として終了コード 2 で止める。`devices` が空の辞書なら、候補なしとして終了コード 1 にする。 深い入れ子の一覧（`json` の読み込みが `RecursionError` になるもの）も取得の失敗（終了コード 2）とする。
- 環境変数 `MINUTES_SIMULATOR` に `xcodebuild` の destination を入れると、選択を上書きできる。

### 4.4 カバレッジ（`scripts/coverage-report.py`）

- Package: `swift test --enable-code-coverage` が出す JSON のうち、`Packages/MinutesKit/Sources/` 配下のファイルだけを数える。合計にはテストコードと自動生成のファイルが混ざるためである（実測で、合計 59%・本体 50% と食い違った）。目標は 90%（TST-CI-07）で、初期は警告だけを出す。
- アプリ側（全体 70%）の集計は、アプリのテストターゲットを足す作業で実装する。それまで手順 8 は「未実装」の警告を出す。
- 手順 7・8 は `.build/minutes-coverage.lock` のロックを持って行う（手順 7 は写しの削除から記録の公開まで、手順 8 は記録の読み取りから集計まで。同じ作業ツリーで並行に走る測定が互いの写しと記録を置き換えないようにする。取得は `take_lock` で行い、取得と持ち主の記録の間の中断を控える。ロックがあれば待たずに失敗する。解放は持ち主の印を先に外してから `rmdir` する。解放の途中で中断されてもロックが残るだけで、終了の trap が別の実行の取り直したロックを消さない。残ったロックは案内のとおり削除する）。手順 7 は、測る入力を固定する。入力の識別子（下の `commit <HEAD>` / `worktree <ツリーのハッシュ>`）が指すツリーを一時のインデックスに読み込み、`Packages/` を手順 2 と同じ `write_index_entries` で blob のまま（smudge のフィルタ・改行の変換を掛けず、測る内容を識別子のツリーの内容と一致させる）リポジトリ直下の `.build/minutes-coverage-input` に書き出し（`git archive` は `export-ignore` で試験用のファイルを省きうるので使わない。書き出せなければ測らない。直下の `.build` は整形・静的解析・入れ子の設定の列挙・Git がすべて除外する）、その写しを対象に、測定ごとに専用の出力先（`swift test --scratch-path` に渡す一時ディレクトリ）で `swift test --enable-code-coverage` を実行し（手順 8 は写しの `Sources` の行を数える）、その出力先の JSON（`--show-codecov-path` も同じ `--scratch-path` で求める）だけを `Packages/MinutesKit/.build/minutes-coverage.json` に写す。同じ Package を対象にした別の `swift test` が共有の出力先（`.build`）の JSON を上書きしても、手順 7 の記録に混ざらない（毎回ビルドし直すので時間はかかる）。写しは同じディレクトリの一時ファイルから rename で置き、写しを置いてから入力とハッシュの記録を置く。`--scratch-path` を渡すと `--show-codecov-path` もその出力先の中の JSON を返し、Package の `.build` には何も書かないことは、2026-11-20 に一時的に置いた Package で確かめた。
- 手順 7 は、カバレッジを測った入力を `Packages/MinutesKit/.build/minutes-coverage-head` に記録する。未コミットの変更がなければ `commit <HEAD>`、あれば `worktree <ツリーのハッシュ>`（一時のインデックスに作業ツリーを写して `git write-tree` で作る。本来のインデックスは変えない。一覧とモードは `git add -A` で決め、通常のファイルの blob は作業ツリーのバイト列そのもの（`git hash-object -w --no-filters`。`.gitattributes` の clean のフィルタ・改行の変換を通さない）で書き直す（`raw_worktree_blobs`）。測る写しはこのツリーの blob をそのまま書き出すので、識別した内容と測る内容が同じ blob になり、clean のフィルタで作業ツリーと違う内容を測らない。コミットを測る場合は従来どおりコミットの blob を使う。識別子は測る入力の `Packages/` だけでなく作業ツリーの全体を表し、測定の前後でどこかが変われば記録しない。`git add -A` が書いた追跡していないファイル（無視するものは除く）の blob とツリーは、どの参照からも届かないまま手元のオブジェクトの格納庫に残り、`git gc` が `gc.pruneExpire`（既定 2 週間）を過ぎたものを消す。送信はされない）とし、未コミットの測定にコミットの SHA を付けない。測定の前後で入力が変わった場合は記録しない。手順 8 は集計の前に現在の入力と照合し、記録がない・違う入力で測った場合は失敗する（古い記録を集計して合格にしない）。`worktree` の記録はコミットの記録として扱わない旨を表示する。 あわせて、測った結果のカバレッジの JSON の SHA-256 を 2 行目に、測った `Sources` の実体のパスを 3 行目から末尾までに記録し、手順 8 は入力に加えてこの SHA-256 も照合する（手順 7 の後に JSON が書き換えられた場合に、手順 7 の測定として集計しない）。手順 8 は記録の JSON を一時ファイルへ写し、写しの SHA-256 を照合してから**同じ写し**を `coverage-report.py` に渡す（照合の後に記録が書き換えられても、照合していない内容を集計しない）。写しは EXIT の trap で消す。照合に失敗した場合・記録の JSON がない場合は集計せずに失敗する。この照合は、2026-11-20 に一時的に置いた Package で手順 7（`swift test` の部分）と手順 8 を通し、記録の JSON を書き換えると手順 8 が失敗し、消すと「手順 7 を先に実行する」で失敗することを確かめた（Package を作る作業単位 2 で本物の Package でも確かめる）。
- `coverage-report.py` は、数える対象かを記録したパスで決める（現在のファイルシステムのシンボリックリンクをたどらない）。`swift test` の記録は Package の起点を実体のパスに解決し、起点の中のパスはそのまま書く（2026-11-22 に一時的に置いた Package で、`/tmp` を経由して指定しても `/private/tmp` の起点になり、`Sources` の中のシンボリックリンクのファイルはリンクのパスのまま記録されることを確かめた）。そこで `--sources` だけを実体のパスにし、記録したパスは字面のまま正規化して比べる。手順 8 は `--sources` の代わりに、手順 7 が記録した実体のパスを `--measured-sources` で渡し、現在の場所から求め直さない（測った後に作業ツリーを移しても、測った行を数える）。記録したパスが絶対パスでなければ終了コード 2。
- `coverage-report.py` は、各ファイルの行数（`count`・`covered`）が整数（真偽値を除く）で `0 <= covered <= count` であることを確かめ、不正な記録は終了コード 2 で失敗する（手順 8 は `test-checks.py coverage` で自己試験してから集計する）。数える対象の同じファイルの記録が重複していても（綴りは正規化して比べる）、合計せずに終了コード 2 で失敗する。重複した鍵のある記録も、後の値を採用せずに終了コード 2 で失敗する（`xcproj.py` の `reject_duplicate_keys`）。
- `--staged` は、手順を始める前に開発ツールを固定する（`snapshot_staged_tools`）。元の `.tools` を導入のロックを取って一時ディレクトリへ写し（`--require-clean` と同じ `copy_tools`）、写しをステージした `tools/versions` と、ステージした `scripts/setup-tools.sh`（作業ツリーのものではなく、インデックスの blob を一時ファイルに書き出したもの）の `--verify --versions … --dest …` で照合してから、整形・静的解析・秘密情報・規則の試験はすべて写し（`TOOLS`）を使う。コミット前のフックの照合の後に別の `setup-tools.sh` が元の `.tools` の bin のリンクや版のディレクトリを差し替えても、照合していない導入で検査しない。写しは終了時に消す。
- `--staged` は、ステージした内容を書き出す前にインデックスのツリー（`git write-tree`）を控え、そのツリーだけから専用のインデックス（一時ファイル）を作って `GIT_INDEX_FILE` で指し、以後の書き出し・整形・静的解析・秘密情報・文書の整合はすべてこの専用のインデックスを読む（検査の途中に本来のインデックスが変わっても、検査する内容は控えたツリーのまま）。合格を表示する前に、専用のインデックスと本来のインデックスの両方が控えたツリーのままであることを確かめる。あわせて `GIT_NO_REPLACE_OBJECTS=1` を設定し、置き換えのオブジェクトを使わずにステージした blob を読む。手順ごとにインデックスから別々に書き出すので、検査の間にステージした内容が変わると、手順ごとに違う内容を検査しうるため（変わっていれば終了コード 1。未解決の衝突でツリーを作れなければ失敗する）。
- `--staged` は、整形と静的解析に渡すパスに `./` を付ける（「-」で始まる名前を指定として解釈させない）。プッシュ前のフックと `isolated_checkout` は、共通のディレクトリ・管理ディレクトリのパスの末尾の改行も保って比べる（`real_directory`・`git_path`）。
- インデックスのシンボリックリンク（120000）を書き出すとき（`write_index_entries`）と、専用の作業ツリーの展開を照合するとき（`verify_checkout_blobs`。`.githooks/pre-push` も同じ）は、リンク先を末尾の改行も含めてバイト単位で扱う（`$(…)` は末尾の改行を落とすので、印の文字を付けてから外し、`readlink` には `-n` を付ける）。
- `--require-clean` とプッシュ前のフックが開発ツール（`.tools`）を写す前に、`bin` のリンクが絶対パスを指していれば理由を示して止める（写したリンクが写しの中を指すのは相対パスの場合だけ。絶対パスのリンクは元の導入を指したまま手順 0 の照合に失敗し、原因が分からないため。リンクは書き換えない）。
- 作業ツリーの識別子の一時のインデックスは、HEAD ではなく現在のインデックスのツリー（`git write-tree`）を起点にする（`git add -f` でステージした無視するファイルを測る入力から落とさない）。追跡していない無視するファイルは含めない。未解決の衝突があれば識別できないものとして失敗する。
- 測る写しに `Package.resolved` があれば、`swift test` に `--force-resolved-versions` を渡す（古い確定版を書き直させて測らず、記録を書く前に失敗させる）。なければ渡さない。
- 未コミットの変更の判定（`git status --porcelain`）には `--untracked-files=normal` を付け、`status.showUntrackedFiles` の設定によらず追跡していないファイルを数える。`coverage_input` は `git status` の出力を先に変数へ取得し、`git status` が失敗したら入力を確定せずに失敗を返す（`$(…)` を条件の中に書くと、失敗を「変更なし」と取り違えて HEAD の記録にするため）。
- 手順 7 は、測る入力の写しを書き出した後・`swift test` の前に、写しの中のシンボリックリンクがすべて写しの中を指すことを確かめる（`verify_coverage_links`。絶対パス、写しの外、外を経由して中に戻るリンクは測らずに失敗する。写しの中の相対リンクは認める）。外を指すリンクをたどると、記録する入力の識別子に含まれない内容を測ってしまうためである。
- `coverage-report.py` は、行数の比と目標（10 進数）を分数のまま正確に比べ、表示する % も同じ値から目標との大小が変わらない桁数まで出す（浮動小数点の丸めで、目標のすぐ下を届いたと、目標ちょうどを届かないと判定しない）。

---

## 5. Git のフックと初期設定

満たす要件: TST-CI-03・04、SEC-SUP-03 ／ 決定: BD-DEV-01

コミット前のフックは、ステージした `tools/versions` とステージした `scripts/setup-tools.sh` を一時ファイルに書き出して照合し（作業ツリーの `setup-tools.sh` の、ステージしていない変更で照合の結果を変えない）、合わなければ、ステージした台帳と `setup-tools.sh` の両方を（フックが見ているインデックスから控えた blob の ID で。部分的なコミットの一時のインデックスはフックの後に消えるため）一時ファイルに書き出し、`--versions`・`--dest` を付けて実行する手順を示す（作業ツリーのものを使わず、作業ツリーとステージした内容を変えない。導入の終了コードを返し、一時ファイルを消す。bash と zsh のどちらに貼っても動く）。検査の間の導入の差し替えには、`check.sh --staged` が写して照合した導入を使うことで備える（照合の後に元の導入が差し替えられても、検査に使う導入は変わらない。写しが台帳と合わなければ `check.sh` が失敗する）。そのため、検査の後に共有の導入をもう一度照合することはしない。フックは入口で `GIT_NO_REPLACE_OBJECTS=1` を設定し、置き換えのオブジェクト（`git replace`）を使わずにステージした台帳と blob を読む（`check.sh --staged` にも引き継ぐ）。終了の trap は入口の終了の状態を控え、一時ファイルの削除の後に明示的に返す。

続けて、ステージした `scripts/check.sh` を一時ファイルに書き出し、`--root <リポジトリの起点> --staged` を付けて実行する（ステージしていない作業ツリーの変更で検査を変えない）。`--root` は最初の引数に置く絶対パスで、`--staged` とだけ組み合わせられ、リポジトリの起点でなければ失敗する。`check.sh` は `--root` の場所を起点とするので、Git の操作・`GIT_INDEX_FILE`（部分的なコミットの一時のインデックスを含む）・`.tools` は元のリポジトリのままで、一時ファイルの場所に移らない。`--staged` が呼ぶ補助のスクリプトも、すべて控えたインデックスから書き出したものを使う（`worktree-location.py`・`setup-tools.sh`・`test-lint-rules.py`・`verify-docs.py`）。フックのファイル自身は Git が作業ツリーのものを起動するので、この対象に含まない。

<!-- file: .githooks/pre-commit -->

```bash
#!/bin/bash
# コミット前の検査: ステージしたファイルに対する手順 1〜3 と 10（基本設計 dev-environment.md §6.2）。
# 有効にする: scripts/setup.sh --hooks（git config core.hooksPath .githooks）。--no-verify で飛ばさない。
set -euo pipefail
# 置き換えのオブジェクト（git replace の refs/replace/）を使わず、ステージした blob そのものを読む（台帳の書き出しと、
# check.sh --staged の各手順の書き出し。子のプロセスにも引き継ぐ）
export GIT_NO_REPLACE_OBJECTS=1
# $(…) はパスの末尾の改行もすべて落とすので、印の x を付けて受け取り、x と git が付けた区切りの改行 1 つだけを外す（pre-push と同じ）
root=$(git rev-parse --show-toplevel && printf x) && root=${root%x} && root=${root%$'\n'} || exit 1
# 導入済みの開発ツールが、ステージした tools/versions（コミットする台帳）と一致することを先に確かめる。作業ツリーの台帳や
# 古い導入のまま、コミットする台帳と違う版のツールで整形・静的解析・秘密情報を検査しない
versions=$(mktemp)
# 終了の状態を入口で控え、一時ファイルの削除の後に明示的に返す（削除の失敗で検査の結果を上書きしない）
trap 'status=$?; rm -f -- "$versions" || true; exit "$status"' EXIT
# 読むのは、このフックが見ているインデックス（部分的なコミット git commit <パス> では一時のもの）の blob。ID を控え、案内する
# 導入し直す手順でも同じ ID を使う（一時のインデックスはフックの後に消え、通常のインデックスのパスは別の内容を指しうる）
if ! versions_blob=$(git rev-parse --verify -q :tools/versions) || ! git cat-file blob "$versions_blob" > "$versions"; then
  echo "NG: ステージした tools/versions を読めない" >&2
  exit 1
fi
# 照合に使う setup-tools.sh も、作業ツリーではなくステージしたもの（コミットする内容）を一時ファイルに書き出して実行する
# （ステージしていない作業ツリーの変更で照合の結果を変えない）。--versions・--dest を渡すので、置き場所に依存しない
setup=$(mktemp)
trap 'status=$?; rm -f -- "$versions" "$setup" || true; exit "$status"' EXIT
if ! setup_blob=$(git rev-parse --verify -q :scripts/setup-tools.sh) || ! git cat-file blob "$setup_blob" > "$setup"; then
  echo "NG: ステージした scripts/setup-tools.sh を読めない" >&2
  exit 1
fi
# 導入し直す手順は、ステージした台帳と setup-tools.sh の両方を一時ファイルに書き出し、書き出したものを --versions・--dest を
# 付けて実行する（作業ツリーのものは使わず、作業ツリーとステージした内容は変えない）。一時ファイルを消した後も、導入（または
# 書き出し）の終了コードを返す（削除の成否で導入の結果を変えない。利用者のシェルが set -e を引き継いでいても後始末まで進むよう、
# 導入は if の条件で実行する）。zsh では status が読み取り専用なので result に控える。
# 起点のパスは上と同じく末尾の改行を保つ（%?x は、印の x とその前の区切りの改行 1 文字を外す。引用符の中に書けるのでこの形にする）
reinstall='(export GIT_NO_REPLACE_OBJECTS=1; root=$(git rev-parse --show-toplevel && printf x) || exit 1; root=${root%?x}; versions=$(mktemp) || exit 1; setup=$(mktemp) || { rm -f -- "$versions"; exit 1; }; if git cat-file blob '"$versions_blob"' > "$versions" && git cat-file blob '"$setup_blob"' > "$setup" && /bin/bash "$setup" --versions "$versions" --dest "$root/.tools"; then result=0; else result=$?; fi; rm -f -- "$versions" "$setup" || true; exit "$result")'
if ! /bin/bash "$setup" --verify --versions "$versions" --dest "$root/.tools"; then
  echo "NG: 導入済みの開発ツールがステージした tools/versions と違う。ステージした台帳で導入し直してからコミットする: ${reinstall}" >&2
  exit 1
fi
# check.sh --staged は、導入のロックを取って開発ツールを写し、写しをステージした台帳と照合してから、検査のすべてを写しで
# 行う（照合の後に元の導入が差し替えられても、検査に使う導入は変わらない）。上の照合は、導入し直す手順を示すために先に行う
# check.sh 自身もステージしたものを書き出して実行する（ステージしていない作業ツリーの変更で検査を変えない）。--root で元の
# リポジトリを示すので、Git の操作・GIT_INDEX_FILE・.tools は元のリポジトリのまま。check.sh が呼ぶほかのスクリプト
# （worktree-location.py・setup-tools.sh・test-lint-rules.py・verify-docs.py）は、check.sh がステージした内容から書き出して使う
check=$(mktemp)
trap 'status=$?; rm -f -- "$versions" "$setup" "$check" || true; exit "$status"' EXIT
if ! git cat-file blob :scripts/check.sh > "$check"; then
  echo "NG: ステージした scripts/check.sh を読めない" >&2
  exit 1
fi
/bin/bash "$check" --root "$root" --staged
```

<!-- file: .githooks/pre-push -->

```bash
#!/bin/bash
# プッシュ前の検査: 手順 0〜8 と 10（基本設計 dev-environment.md §6.2）。UI テスト（手順 9）は PR の作成前に手動で行う。
# 有効にする: scripts/setup.sh --hooks（git config core.hooksPath .githooks）。--no-verify で飛ばさない。
# 送信するコミットが HEAD と同じで、未コミットの変更がないことを求める。
# 検査は、送信するコミットだけをチェックアウトした専用の作業ツリーで行う。元の作業ツリーで検査中にブランチを
# 切り替えても（切り替えて戻しても）、すべての手順が同じスナップショットを読む。専用の作業ツリーはリポジトリの外
# （<ホーム>/Library/Caches/MinutesMobile/pre-push/<リポジトリごとの値>。MINUTES_PRE_PUSH_DIR で変えられる）に置く
# （リポジトリの中に置くと、SwiftFormat などが親のディレクトリの設定ファイルも読み、元の作業ツリーの設定が混ざる）
set -euo pipefail
# 置き換えのオブジェクト（git replace の refs/replace/）を使わず、元のコミットのオブジェクトで照合・展開・検査する
# （参照の照合・HEAD の確認から専用の作業ツリーの展開、check.sh の各手順まで。子のプロセスにも引き継ぐ）
export GIT_NO_REPLACE_OBJECTS=1
# HEAD は削除以外の参照の行を受け取ってから解決する（削除だけの送信では解決しない。HEAD がまだない場合にも失敗させない）。
# 最初に解決した値を、後続の照合・専用の作業ツリーのチェックアウト・--expect-head に使う
head=""
updates=0  # 削除以外の参照の送信の数
# 標準入力: 送信する参照ごとに「<ローカルの参照> <ローカルの SHA> <リモートの参照> <リモートの SHA>」
while read -r local_ref local_sha remote_ref remote_sha; do
  # 削除の送信（ローカルの SHA がすべて 0）は検査するコミットがない
  if [ -z "${local_sha//0/}" ]; then continue; fi
  updates=$((updates + 1))
  if [ -z "$head" ]; then head=$(git rev-parse --verify HEAD); fi
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
# 元の作業ツリーに未コミットの変更（追跡していないファイルを含む）があれば、送信するコミットと手元が食い違うので止める
# 状態を取得できない場合（git status の失敗）は、変更がないと見なさずに止める
status_output=$(git status --porcelain --untracked-files=normal) || {
  echo "NG: 作業ツリーの状態を取得できない（git status が失敗した）" >&2
  exit 1
}
if [ -n "$status_output" ]; then
  echo "NG: 未コミットの変更がある（追跡していないファイルを含む）。コミットするか退避してから push する" >&2
  exit 1
fi

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
# 実体のパス（シンボリックリンクを解決したもの）と共通のディレクトリ。&& の並びの途中の失敗では set -e が働かないので、明示的に止める
git_path toplevel . --show-toplevel && real_directory source_root "$toplevel" && git_path common_dir . --git-common-dir \
  || { echo "NG: 作業ツリーの起点・共通のディレクトリを特定できない" >&2; exit 1; }
base=${MINUTES_PRE_PUSH_DIR:-${HOME}/Library/Caches/MinutesMobile/pre-push}
# 置き場所の判定（実体のパスへの解決・SwiftFormat の設定のある祖先の拒否・元のリポジトリと登録されたすべての作業ツリーの
# 中の拒否）は scripts/worktree-location.py が行う（check.sh --require-clean と共用する）。中に置くと、SwiftFormat などが
# 親のディレクトリの設定ファイルも読み、その作業ツリーの設定が混ざるため。拒否してから作る（中にディレクトリを残さない）。
# 判定できない場合（作業ツリーの一覧の行を読めないなど）は認めずに止める。
# スクリプトは、送信するコミット（最初に解決した HEAD）から 1 回だけ書き出し、3 つの操作（check・registered・nested）に同じものを使う
# （元の作業ツリーが sparse checkout で scripts/ がなくても判定でき、途中の書き換えで判定が変わらない）
location_script=$(mktemp)
trap 'status=$?; rm -f -- "$location_script"; exit "$status"' EXIT
git cat-file blob "$head:scripts/worktree-location.py" > "$location_script" \
  || { echo "NG: 送信するコミットの scripts/worktree-location.py を読めない" >&2; exit 1; }
# -I: 書き出した場所（一時ディレクトリ）に置かれた同名のモジュール（subprocess.py など）を読み込ませない
worktree_location() { /usr/bin/python3 -I "$location_script" "$@"; }
# 判定の結果のパスは NUL で終わる記録で受け取り、変数 resolved_base に入れる（$(…) はパスの末尾の改行を落とすので使わない）
check_location() {
  local location_status=0 location_out
  location_out=$(mktemp)
  worktree_location check "$1" "$source_root" > "$location_out" || location_status=$?
  resolved_base=""
  if [ "$location_status" = 0 ] && ! IFS= read -r -d '' resolved_base < "$location_out"; then location_status=2; fi
  rm -f -- "$location_out"
  case $location_status in
    0) ;;
    1) exit 1 ;;  # 理由は worktree-location.py が標準エラーに書いた
    *)
      echo "NG: 専用の作業ツリーの置き場所を判定できない（${MINUTES_PRE_PUSH_DIR:-既定の場所}）" >&2
      exit 1
      ;;
  esac
}
check_location "$base"
base=$resolved_base
mkdir -p "$base"
# 作った後にも判定し直す（最初の判定と作るまでの間に、置き場所を含む作業ツリーが登録された・置き場所の途中がリンクに
# 変わった場合に、そのまま使わない）。同じ実体のパスに解決されることも求める
check_location "$base"
if [ "$resolved_base" != "$base" ]; then
  echo "NG: 専用の作業ツリーの置き場所（${base}）が、作った後に別の場所（${resolved_base}）に解決される。やり直す" >&2
  exit 1
fi
key=$(printf '%s' "$common_dir" | shasum -a 256)
snapshot="$base/${key%% *}"
lock="$snapshot.lock"
# フックの実行中に Git が設定する変数を外す（残すと、専用の作業ツリーでの git が元のリポジトリの作業ツリーを操作する）
# オブジェクトの置き場所・共通のディレクトリの変数も外す（別の場所のオブジェクトを、送信するコミットとして検査しない）
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR

lock_held=""   # 作業ツリーのロックを自分が取った（後始末は自分のロックだけを消す）
tools_lock=""  # 元の .tools の導入のロック（写している間だけ持つ）
# 後始末。持ち主の印を先に外してからロックを外す（間で中断されても、別の実行の取り直したロックを消さない）
release_locks() {
  local held_tools=$tools_lock held=$lock_held
  tools_lock=""
  lock_held=""
  if [ -n "$held_tools" ]; then rmdir "$held_tools" 2>/dev/null || true; fi
  if [ -n "$held" ]; then rmdir "$held" 2>/dev/null || true; fi
}
# 専用の作業ツリーを合わせる間だけ移しておく場所（推測できない名前の一時のディレクトリ。中の tree が専用の作業ツリー）
staging=""
# 戻す途中で元の場所にほかのものができると、専用の作業ツリーがその中（${snapshot}/tree）に移っていることがある。そこに作業ツリーらしき
# ものがあるときだけ、可能性として知らせる（この専用の作業ツリーだとは確かめていないので、動かさず、消さない）
nested_hint() {
  if [ -e "$snapshot/tree/.git" ]; then printf '%s' "（専用の作業ツリーが ${snapshot}/tree に移っている可能性がある。そこも確かめる）"; fi
}
# 移しておいた専用の作業ツリーを元の場所に戻す。元の場所に別のもの（移している間に登録された作業ツリーなど）があれば、どちらも
# 変えずに失敗する（次の検査が、元の場所が空いていれば戻す）
restore_staging() {
  local held=$staging
  if [ -z "$held" ]; then return 0; fi
  staging=""
  if [ ! -e "$held/tree" ]; then rmdir "$held" 2>/dev/null || true; return 0; fi
  if [ -e "$snapshot" ] || [ -L "$snapshot" ]; then
    echo "NG: 専用の作業ツリーの置き場所（${snapshot}）に、検査の間に別のものができた。中身を確かめて外す（作業ツリーなら git worktree move か remove）か削除してから push し直す（元の専用の作業ツリーは ${held}/tree に残した）: $(ls -A -- "$snapshot" 2>/dev/null | sed 's|$|/|' | tr '\n' ' ')" >&2
    return 1
  fi
  # 移す途中の強制終了で、登録されたパスと実際の場所が食い違っている場合だけ、登録を直してから戻す。移動は中断を無視して実行する
  if ! worktree_location registered "$source_root" "$held/tree"; then git -C "$held/tree" worktree repair > /dev/null || true; fi
  (trap '' INT TERM; exec git -C "$source_root" worktree move "$held/tree" "$snapshot") \
    || { echo "NG: 専用の作業ツリー（${held}/tree）を元の場所（${snapshot}）に戻せない$(nested_hint)" >&2; return 1; }
  rmdir "$held" 2>/dev/null || true
  # 確かめてから移すまでの間に元の場所ができると、git worktree move はその中に移す（上書きはしない）。検査せずに止める
  if [ ! -e "$snapshot/.git" ] || [ -e "$snapshot/tree/.git" ]; then
    echo "NG: 専用の作業ツリーを元の場所（${snapshot}）に戻す間に、そこに別のものができた。中身を確かめて外してから push し直す$(nested_hint)" >&2
    return 1
  fi
}
# 終了の状態を入口で控え、後始末の後に明示的に返す（後始末のコマンドの状態で検査の失敗を上書きしない）
trap 'status=$?; restore_staging || true; release_locks; rm -f -- "$location_script"; exit "$status"' EXIT
# ロック（ディレクトリ $1）を取り、取れたら変数 $2 に場所を記録して 0 を返す。結果を記録するまで、中断は控えるだけにする
# （取った直後・記録の前に終了してロックを残さない。取れなかった場合に、ほかの実行のロックを消さない）。mkdir は中断を無視して
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
# 同時に 2 つの push が同じ作業ツリーを使わないようにする
if ! take_lock "$lock" lock_held; then
  echo "NG: 別の push の検査が実行中（${lock}）。実行中でなければ、そのディレクトリを削除してから push し直す" >&2
  exit 1
fi

# このフックが作った印。作業ツリーの専用の管理ディレクトリ（<共通のディレクトリ>/worktrees/<名前>。作業ツリーの中ではないので
# checkout・clean で消えない）に、このフックの名前と作業ツリーの実体のパスを書く。置き場所と共通のディレクトリが合うだけでは、
# このフックが作った作業ツリーであることの証拠にならない（利用者が同じ場所に git worktree add した作業ツリーなど）
owner_mark="minutes-pre-push-owner"
owner_text="MinutesMobile pre-push ${snapshot}"
# $1 が、起点がその場所で、共通のディレクトリが元のリポジトリのもので、専用の管理ディレクトリにこのフックの印がある作業ツリーか
owned_tree() {
  local top common source_common private
  git_path top "$1" --show-toplevel && real_directory top "$top" \
    && git_path common "$1" --git-common-dir && real_directory common "$common" \
    && real_directory source_common "$common_dir" \
    && [ "$top" = "$1" ] && [ "$common" = "$source_common" ] \
    && git_path private "$1" --git-dir && real_directory private "$private" && [ "$(dirname "$private")" = "$source_common/worktrees" ] \
    && [ -f "$private/$owner_mark" ] && [ ! -L "$private/$owner_mark" ] \
    && [ "$(cat "$private/$owner_mark")" = "$owner_text" ]
}
# 前の検査が、専用の作業ツリーを移したまま終わった（強制終了・元の場所に別のものができた）場合は、先に元の場所へ戻す。元の場所に
# 何かがあれば（空のディレクトリを含む）、このフックのものと確かめられないので消さず、restore_staging が戻さずに止める
for held in "$snapshot".work.*; do
  if [ ! -d "$held" ] || [ -L "$held" ]; then continue; fi
  if [ -e "$held/tree" ] && ! owned_tree "$held/tree"; then
    echo "NG: ${held}/tree がこの検査の作業ツリーと確かめられない。中身を確かめて外してから push し直す" >&2
    exit 1
  fi
  staging=$held
  restore_staging
done

# 専用の作業ツリーを送信するコミットに合わせる。作業ツリーは残し、無視するファイル（ビルドの成果物）を次の検査で再利用する
# 一覧の取得と照合を分ける（取得に失敗したまま「登録がない」と見なし、専用の作業ツリーを消して作り直さない）。
# 照合は記録の worktree の項目（パスの全体）との完全な一致。終了コード: 0 = 登録あり / 1 = 登録なし / 2 = 取得できない
registration=0
worktree_location registered "$source_root" "$snapshot" || registration=$?
case $registration in
  0 | 1) ;;
  *)
    echo "NG: 作業ツリーの一覧を取得できない（git worktree list が失敗した）" >&2
    exit 1
    ;;
esac
# 再利用するのは、登録があり、ディレクトリの git が専用の作業ツリーそのもの（作業ツリーの起点がこの場所で、
# 共通のディレクトリが元のリポジトリのもの）を指し、専用の管理ディレクトリにこのフックの印がある場合だけ。
# .git のファイルを失ったなどで別のリポジトリや親のディレクトリを指す場合・印がない場合に、そちらを checkout・clean しない
# （その場合は下で止める）
owned=0
if [ "$registration" -eq 0 ] && [ -d "$snapshot" ]; then
  if owned_tree "$snapshot"; then owned=1; fi
fi
# 元の作業ツリーが sparse checkout なら、専用の作業ツリーもその指定を引き継ぎ（git worktree add が写す）、一部のファイル
# しかない状態で検査することになる。専用の作業ツリーで sparse checkout を有効にしていれば、そこでだけ解除して全体を
# 展開する（作業ツリーごとの設定に書くので、元の作業ツリーは sparse のまま）。有効でなければ解除しない（解除の操作は
# sparse でないリポジトリにも作業ツリーごとの設定の拡張を書き足すため）。設定を読めない場合は止める
full_checkout() {
  local tree=$1 sparse status=0
  # GIT_CONFIG を外して読む（設定されていると git config はそのファイルだけを読み、専用の作業ツリーの設定を見ない）
  sparse=$(env -u GIT_CONFIG git -C "$tree" config --type=bool --get core.sparseCheckout) || status=$?
  case $status in
    0) if [ "$sparse" = true ]; then git -C "$tree" -c core.autocrlf=false sparse-checkout disable; fi ;;
    1) ;;  # 設定がない（sparse でない）
    *)
      echo "NG: 専用の作業ツリーの sparse checkout の設定を読めない（${tree}）" >&2
      exit 1
      ;;
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
# 改行の変換（利用者の core.autocrlf の設定）を掛けずに展開する（scripts/check.sh などを LF のまま実行する）
# 利用者のフック（post-checkout）は、内部の展開では動かさない（失敗・書き換えで検査を左右させない。利用者の git には関わらない）
if [ "$owned" -eq 1 ]; then
  # 専用の作業ツリーの下に登録された別の作業ツリーがあれば、checkout・clean で中身を失わせうるので、変更せずに止める
  nested=0
  worktree_location nested "$source_root" "$snapshot" || nested=$?
  case $nested in
    0) ;;
    1) exit 1 ;;  # 理由とパスは worktree-location.py が標準エラーに書いた
    *)
      echo "NG: 専用の作業ツリー（${snapshot}）の下に別の作業ツリーがあるかを判定できない" >&2
      exit 1
      ;;
  esac
  # 上の判定の後に利用者が登録した作業ツリー（git worktree add はこのフックのロックを見ないので止められない）を壊さないよう、
  # 専用の作業ツリーを推測できない名前の場所へ rename で移し、そこで確かめ、合わせてから、元の場所へ戻す。移した後に元の場所の下へ
  # 登録された作業ツリーは、合わせる作業ツリーとは別の場所になる（checkout・clean・rm が及ばない）。移すまでに登録されたものは
  # 一緒に移り（中身は変わらない）、下の .git の検査で止めて元の場所へ戻す。検査する場所とビルドの成果物は変わらない
  staging=$(mktemp -d "$snapshot.work.XXXXXX")
  (trap '' INT TERM; exec git -C "$source_root" worktree move "$snapshot" "$staging/tree") \
    || { echo "NG: 専用の作業ツリー（${snapshot}）を移せない" >&2; exit 1; }
  # 中に別の作業ツリー・リポジトリ（.git を持つもの）があれば、何も変えずに止める。残すビルドの成果物（.build）の中の .git の
  # ディレクトリ（取得した依存の複製）は認めるが、ディレクトリでない .git（登録された作業ツリーが持つファイル）は認めない
  inner=$(cd "$staging/tree" && find . -name .git ! -path ./.git \
    \( ! -type d -o ! -path './.build/*' ! -path './Packages/MinutesKit/.build/*' \) -print) \
    || { echo "NG: 専用の作業ツリー（${snapshot}）の中に別の作業ツリーがあるかを調べられない" >&2; exit 1; }
  if [ -n "$inner" ]; then
    echo "NG: 専用の作業ツリー（${snapshot}）に、別の作業ツリーかリポジトリがある。中身を確かめて外す（作業ツリーなら git worktree move か remove）か削除してから push し直す: ${inner}" >&2
    exit 1
  fi
  git -C "$staging/tree" -c core.hooksPath=/dev/null -c core.autocrlf=false checkout -q --detach --force "$head"
  full_checkout "$staging/tree"
  # 追跡していないファイルを、無視するもの（docs/ の下書きなど）も含めて消し、送信するコミットと一致させる
  # （無視するファイルが残ると、文書の整合の検査などが送信しないファイルを読む）。残すのは check.sh が使うビルドの成果物の場所
  # （直下の /.build と Package の /Packages/MinutesKit/.build）だけ。ほかの階層の .build（docs/.build など）は消す
  # -f は 1 回だけ付け、.git を持つディレクトリを消さない
  kept=(-e /.build -e /Packages/MinutesKit/.build)
  git -C "$staging/tree" clean -q -f -d -x "${kept[@]}"
  # -n: 消さずに、残っているもの（-f を 2 回付けると消えるもの）を調べる
  remaining=$(git -C "$staging/tree" clean -n -f -f -d -x "${kept[@]}") \
    || { echo "NG: 専用の作業ツリー（${snapshot}）に追跡していないものが残っているかを調べられない" >&2; exit 1; }
  if [ -n "$remaining" ]; then
    echo "NG: 専用の作業ツリー（${snapshot}）に、別の作業ツリーかリポジトリがある。中身を確かめて外す（作業ツリーなら git worktree move か remove）か削除してから push し直す: ${remaining}" >&2
    exit 1
  fi
  # 残したビルドの成果物の場所には、中断した前回の検査のカバレッジのロック（check.sh の手順 7・8 が直下の .build に作る）が
  # 残りうる（強制終了では check.sh の後始末が動かない）。この専用の作業ツリーで検査するのは作業ツリーのロック（$lock）を
  # 持つ実行だけなので、ロックを取った今、残っているものは持ち主のいない残骸として消す（ビルドの成果物は消さない）
  # .build がシンボリックリンクなら、リンクの先（作業ツリーの外の場所）の中を消しうるので、消さずに止める
  # Package の .build も、リンクなら check.sh がカバレッジの記録をリンクの先に書くので、同じく止める
  for build in "$staging/tree/.build" "$staging/tree/Packages/MinutesKit/.build"; do
    if [ -L "$build" ]; then
      echo "NG: 専用の作業ツリーの ${build#"$staging/tree/"} がシンボリックリンク。中身を確かめて削除してから push し直す" >&2
      exit 1
    fi
  done
  rm -rf -- "$staging/tree/.build/minutes-coverage.lock"
  restore_staging
else
  # 専用の作業ツリーとして確かめられなかったもの（登録のないディレクトリ・別のリポジトリを指すもの・このフックの印のない
  # 作業ツリーなど）が置き場所にあれば、変更せずに止める（中身が何かを確かめられないものを checkout・clean・rm -rf しない）。
  # 登録も置き場所もないとき（初回）だけ作る
  if [ -e "$snapshot" ] || [ -L "$snapshot" ]; then
    echo "NG: 専用の作業ツリーの置き場所（${snapshot}）に、この検査の作業ツリーと確かめられないもの（このフックの印のない作業ツリーを含む）がある。中身を確かめて削除する（作業ツリーなら git worktree remove）か、MINUTES_PRE_PUSH_DIR で別の場所を指定してから push し直す$(nested_hint)" >&2
    exit 1
  fi
  # 登録だけが残っている場合は、上書きせずに止める（作業ツリーを別の場所へ移した・外した場所に置いた場合に、その登録を奪わない）
  if [ "$registration" -eq 0 ]; then
    echo "NG: 専用の作業ツリーの置き場所（${snapshot}）は、ディレクトリがないのに作業ツリーとして登録されている。移したものなら git worktree repair で登録を直し、不要なら git worktree remove で登録を外してから push し直す" >&2
    exit 1
  fi
  # --force は付けない（上の照合の後に登録された場合も、git が拒否する）
  # --no-checkout: 先に登録だけを行い、印を書いてから展開する（時間のかかる展開の途中で中断・失敗しても印が残り、次の検査が
  # このフックの作業ツリーとして展開し直せる）
  # 登録してから、作った作業ツリーの専用の管理ディレクトリにこのフックの印を書く（次の検査で再利用してよいかの判定に使う）まで、
  # 中断を無視する（間で終わって、印のない作業ツリーを残さない。このシェルの trap は、サブシェルの終了を待ってから動くので、
  # 控えた中断にはその後で従う）。登録に失敗したら、既存のものに印を書かずに止める
  # 印を書けなかった場合は、この実行が作った登録だと確かめられたときだけ外す（印のない作業ツリーを残すと、次の検査は持ち主を
  # 確かめられずに止まる）。確かめる: 管理ディレクトリが登録の置き場所の直下にあり、add の前にはなかった名前で、その gitdir の
  # 記録が置き場所の .git を指すこと。確かめられない・外せない場合は外さずに、場所と直し方を示して止める
  created=0
  (
    trap '' INT TERM
    real_directory source_common "$common_dir" || exit 2
    before=" "
    for entry in "$source_common/worktrees"/*; do
      if [ -e "$entry" ] || [ -L "$entry" ]; then before="$before${entry##*/} "; fi
    done
    git -C "$source_root" worktree add -q --no-checkout --detach "$snapshot" "$head" || exit 1
    git_path private "$snapshot" --git-dir && real_directory private "$private" || exit 2
    if printf '%s\n' "$owner_text" > "$private/$owner_mark"; then exit 0; fi
    name=${private##*/}
    if [ "${private%/*}" != "$source_common/worktrees" ] || case "$before" in *" $name "*) true ;; *) false ;; esac \
      || [ ! -f "$private/gitdir" ] || [ -L "$private/gitdir" ]; then
      exit 5
    fi
    # gitdir の記録は相対パスのこともある（worktree.useRelativePaths）。相対なら記録のあるディレクトリ（管理ディレクトリ）から
    # 解決し、実体のパスにしてから置き場所の .git と比べる
    record=$(cat "$private/gitdir") || exit 5
    case $record in
      /*) ;;
      *) record=$private/$record ;;
    esac
    if [ "${record##*/}" != .git ] || ! real_directory recorded_tree "${record%/*}" \
      || [ "$recorded_tree/.git" != "$snapshot/.git" ]; then
      exit 5
    fi
    git -C "$source_root" worktree remove --force "$snapshot" || exit 4
    exit 3
  ) || created=$?
  recovery="中身を確かめてから git worktree remove --force で外す（ディレクトリだけがなければ git worktree prune）か、MINUTES_PRE_PUSH_DIR で別の場所を指定してから push し直す"
  case $created in
    0) ;;
    1) echo "NG: 専用の作業ツリー（${snapshot}）を作れない" >&2; exit 1 ;;
    2) echo "NG: 作った専用の作業ツリー（${snapshot}）の管理ディレクトリを特定できない。印のない作業ツリーが残った可能性がある。${recovery}" >&2; exit 1 ;;
    3) echo "NG: 作った専用の作業ツリー（${snapshot}）に印を書けない（作った登録は外した）。管理ディレクトリ（${common_dir}/worktrees）に書き込めるかを確かめてから push し直す" >&2; exit 1 ;;
    4) echo "NG: 作った専用の作業ツリー（${snapshot}）に印を書けず、登録も外せない。${recovery}" >&2; exit 1 ;;
    *) echo "NG: 作った専用の作業ツリー（${snapshot}）に印を書けず、この検査が作った登録だと確かめられないので外さない。${recovery}" >&2; exit 1 ;;
  esac
  git -C "$snapshot" -c core.hooksPath=/dev/null -c core.autocrlf=false checkout -q --detach --force "$head"
  full_checkout "$snapshot"
fi
# 再利用・作り直しのどちらでも、展開した内容が送信するコミットの blob と同じであること（core.autocrlf=false では
# smudge のフィルタは止まらない。変わった内容を送信するコミットの合格として検査しない）
if ! verify_checkout_blobs "$snapshot"; then
  echo "NG: 専用の作業ツリーの内容が送信するコミット（${head}）の blob と違う（.gitattributes の smudge のフィルタなど）。検査しない" >&2
  exit 1
fi
# 開発ツールは Git の管理外（.gitignore の /.tools で無視される）。元の作業ツリーの .tools を共有のリンクで指すと、検査の途中に
# 別の setup.sh が bin のリンクや版のディレクトリを差し替え、手順 0 で照合した導入と後の手順が使う導入が食い違いうる。
# そこで、導入のロック（setup-tools.sh と同じ .install.lock）を取ってから元の .tools を専用の作業ツリーへ写し、ロックを外す。
# 写しは APFS では複製の領域を使わない clone（cp -c）、対応しないファイルシステムでは通常の複写で作る。bin のリンクは
# 相対パスなので写しの中を指す。以後の手順（手順 0 の照合を含む）はすべてこの写しを使い、元の .tools の変更の影響を受けない
tools_source="$source_root/.tools"
tools_copy="$snapshot/.tools"
if [ ! -d "$tools_source" ]; then
  echo "NG: 開発ツールが導入されていない（${tools_source}）。scripts/setup-tools.sh を実行してから push し直す" >&2
  exit 1
fi
waited=0
retried=0  # ロックが消えていたときに、直ちに取り直した（1 回だけ）
until take_lock "$tools_source/.install.lock" tools_lock; do
  # mkdir の失敗の後にロックが外れた（別の導入が終わった）場合は、待たずに 1 回だけ取り直す。
  # 取り直しても失敗し、ロックもない場合は、ロック以外の理由（書き込めないなど）として止める
  if [ ! -d "$tools_source/.install.lock" ] && [ "$retried" -eq 0 ]; then
    retried=1
    continue
  fi
  if [ -d "$tools_source/.install.lock" ]; then retried=0; fi
  if [ ! -d "$tools_source/.install.lock" ] || [ "$waited" -ge 600 ]; then
    echo "NG: 開発ツールの導入のロック（${tools_source}/.install.lock）を取れない。導入の実行中でなければ削除してから push し直す" >&2
    exit 1
  fi
  if [ "$waited" -eq 0 ]; then echo "待機: 開発ツールの導入・照合が実行中。終わるのを待つ" >&2; fi
  sleep 1
  waited=$((waited + 1))
done
# 写した bin のリンクが写しの中を指すのは、リンクが相対パスの場合だけ。絶対パスのリンクは写しても元の導入を指し、手順 0 の
# 照合が写しの導入先と合わずに失敗する（検査は弱まらないが原因が分からない）。写す前に、理由を示して止める（リンクは書き換えない）
for link in "$tools_source"/bin/*; do
  if [ -L "$link" ] && case "$(readlink -- "$link")" in /*) true ;; *) false ;; esac; then
    echo "NG: 開発ツールのリンク（${link}）が絶対パスを指している。scripts/setup-tools.sh で導入し直してから push し直す" >&2
    exit 1
  fi
done
# 前の写しは、上で専用の作業ツリーを合わせるときに消してある（ここで消すと、後から登録された作業ツリーを消しうる）
mkdir "$tools_copy"
for entry in "$tools_source"/* "$tools_source"/.[!.]*; do
  if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then continue; fi  # 一致するものがないときの glob の文字列
  case "${entry##*/}" in .install.lock | .work.*) continue ;; esac  # ロックと導入の途中の作業場所は写さない
  # -P: シンボリックリンクはたどらずにリンクとして写す
  if ! cp -c -R -P -- "$entry" "$tools_copy/" 2>/dev/null; then
    rm -rf -- "${tools_copy:?}/${entry##*/}"
    cp -R -P -- "$entry" "$tools_copy/" || { echo "NG: 開発ツールを写せない（${entry}）" >&2; exit 1; }
  fi
done
# ロックを外して持ち主の印（tools_lock）を外すまで、中断は控えるだけにする（take_lock と同じ。間で終了してロックを残さず、
# 終了の trap が別の導入の取り直したロックを消さない）。外せなければ印を残して失敗し、控えた中断には印を外した後で従う
interrupted=""
released=1
trap 'interrupted=1' INT TERM
if (trap '' INT TERM; exec rmdir "$tools_lock"); then tools_lock=""; else released=""; fi
trap 'exit 130' INT TERM
if [ -n "$interrupted" ]; then exit 130; fi
if [ -z "$released" ]; then echo "NG: 開発ツールの導入のロック（${tools_lock}）を外せない" >&2; exit 1; fi

# 起動する check.sh は、専用の作業ツリーの中の通常の実行できるファイルであること（check.sh の手順 10 の verify-docs.py と同じ）。
# scripts かその中の check.sh がシンボリックリンクなら、verify_checkout_blobs（リンク先の文字列の照合）は通っても、作業ツリーの外を実行しうる
if [ -L "$snapshot/scripts" ] || [ ! -d "$snapshot/scripts" ] || [ -L "$snapshot/scripts/check.sh" ] \
  || [ ! -f "$snapshot/scripts/check.sh" ] || [ ! -x "$snapshot/scripts/check.sh" ]; then
  echo "NG: 送信するコミットの scripts/check.sh が通常の実行できるファイルでない（シンボリックリンク・実行権限なしなど）" >&2
  exit 1
fi
# 専用の作業ツリーは送信するコミットに合わせてあり、開発ツールも写した導入に固定してあるので、check.sh に改めて展開させない（ビルドの成果物を再利用する）
MINUTES_CHECK_ISOLATED=1 "$snapshot/scripts/check.sh" --steps 0-8,10 --require-clean --expect-head "$head" </dev/null
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
  # 設定を読む。鍵がない（終了コード 1）は空として続け、それ以外の失敗は「鍵がない」と取り違えずに報告して失敗を返す。
  # 値の末尾の改行も区別する（$(…) は落とす）ため、NUL 区切り（-z）で読み、記録ごとに 1 行の「=値」にして返す。
  # 値の中の改行は制御文字（RS。8 進数 036）に置き換える（「.githooks＋改行」も空の値も、「=.githooks」と一致しない）
  read_config() {
    local value status=0
    value=$(env -u GIT_CONFIG git -C "$ROOT" config -z "$@" | LC_ALL=C tr '\n\000' '\036\n' | LC_ALL=C sed 's/^/=/') || status=$?
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
  # 共有の値を書いても有効にならない。共有の値を変える前に止める（1 行目が設定の段、2 行目が値）
  current=$(read_config --show-scope --get core.hooksPath) || exit 1
  scope=${current%%$'\n'*}
  scope=${scope#=}
  case "$scope" in
    worktree | command)
      if [ "${current#*$'\n'}" != =.githooks ]; then
        echo "NG: より優先される設定（${scope}）の core.hooksPath が「${current#*$'\n'=}」を指している。共有の設定を変えずに止める。" \
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
    if [ -n "$value" ] && [ "$value" != =.githooks ]; then
      echo "NG: 共有の設定が取り込む設定（include・includeIf）の core.hooksPath が「${value#=}」を指している。共有の設定を変えずに止める。" \
        "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
      exit 1
    fi
  done <<< "$included"
  # 共有の設定のファイル自身の値（取り込みを除く）が既に .githooks の 1 件だけなら書かない。複数の行がある場合、git config の
  # 1 つの値の書き込みは「複数の値を 1 つの値で上書きできない」で失敗するので、--replace-all ですべての行を明示的に置き換える
  if [ "$own" != =.githooks ]; then
    env -u GIT_CONFIG git -C "$ROOT" config --local --replace-all core.hooksPath .githooks
  fi
  # 実際に効く値を読み直す（より優先される設定（-c・GIT_CONFIG_* の環境変数・worktree の設定など）が
  # 別の値を指していれば、書き込んでもフックは有効にならない）。--local を付けず、worktree の設定も反映させる
  effective=$(read_config --get core.hooksPath) || exit 1
  if [ "$effective" != =.githooks ]; then
    echo "NG: core.hooksPath を .githooks に設定したが、実際に効く値は「${effective#=}」。" \
      "設定の出所を env -u GIT_CONFIG git config --show-origin --get-all core.hooksPath で確かめる" >&2
    exit 1
  fi
  echo "OK: フックを有効にした（core.hooksPath = ${effective#=}）"
else
  echo "OK: 開発ツールを導入した。フックは有効にしていない（有効にするときは --hooks を付ける）"
fi
```

- プッシュ前のフックは、削除の送信（ローカルの SHA がすべて 0）を照合の対象から外す。削除だけの送信は検査するコミットがないので、`check.sh` を起動せずに終了コード 0 で終える。削除と通常の更新が混ざる場合は、通常の更新だけを HEAD と照合し、従来どおり `check.sh` を実行する。
- プッシュ前のフックの 2 つのロック（作業ツリー・導入の `.install.lock`）と `check.sh` の導入のロック（`copy_tools`）は `take_lock` で取り、取得の結果を記録するまで INT・TERM を控えるだけにする（終了の trap は先に置く）（取った直後の中断でロックを残さず、取れなかった場合にほかの実行のロックを消さない）。
- `setup.sh --hooks` は、`core.hooksPath` を NUL 区切り（`git config -z`）で読み、末尾の改行や空の値も区別して、ちょうど `.githooks` でない上書き（優先される設定・取り込む設定）があれば共有の設定を書かずに止める。書いた後の読み直しも同じ読み方で確かめ、読み取りの失敗では止める。
- プッシュ前のフックは、送信するコミットをチェックアウトした**専用の作業ツリー**（`git worktree add --detach`）で `check.sh` を実行し、すべての手順が同じスナップショットを読むようにする（元の作業ツリーで検査中にブランチを切り替えて戻しても、検査の対象が変わらない。`--expect-head` の照合だけでは、開始と終了の HEAD が同じなら途中の切り替えを検出できない）。専用の作業ツリーはリポジトリの外（`<ホーム>/Library/Caches/MinutesMobile/pre-push/<git の共通ディレクトリのパスの SHA-256>`。環境変数 `MINUTES_PRE_PUSH_DIR` で変えられる）に置き、次の push で再利用する（登録があり、ディレクトリの git の作業ツリーの起点がその場所で共通のディレクトリが元のリポジトリのものであり、さらにその作業ツリーの専用の管理ディレクトリ（`<共通のディレクトリ>/worktrees/<名前>`。作業ツリーの中ではないので checkout・clean で消えない）に、フックが作成の時に書いた印（`minutes-pre-push-owner`。フックの名前と作業ツリーの実体のパス）があると確かめた場合だけ、推測できない名前の場所へ移したうえで、`checkout --detach --force` と `clean -fdx -e /.build -e /Packages/MinutesKit/.build` で送信するコミットに合わせる。ただし、その下に別の作業ツリーが登録されていれば、checkout・clean で中身を失わせうるので、変更せずに場所を表示して失敗する（`worktree-location.py nested`。パスの文字列ではなくファイルシステム上の同一性で判定し、登録されたパスの祖先と、リンクを解決したパスの祖先の両方を見る）。登録だけが残りディレクトリがない場合は、登録を上書きせず、場所を表示して失敗する（別の場所へ移した・外した場所に置いた作業ツリーの登録を奪わない。`worktree add` に `--force` を付けない）。確かめられないもの（登録のないディレクトリ・別のリポジトリを指すもの・印のない作業ツリーなど。置き場所と共通のディレクトリが合うだけでは、フックが作った作業ツリーの証拠にならない）が置き場所にあれば、checkout・clean・`rm -rf` をせず、場所を表示して失敗する（中身を確かめて消すか、`MINUTES_PRE_PUSH_DIR` で別の場所を指定する）。置き場所にも登録にも何もないとき（初回）だけ作る。作るときは `worktree add --no-checkout` で登録だけを行い（登録から印を書くまでの中断（INT・TERM）は控え、書いた後で従う。登録に失敗したら、先にある登録に印を書かずに止める）、印を書いてから `checkout` で展開する（展開の途中で中断しても印が残り、次の push で展開し直せる）。印を書けなかった場合は、この実行が作った登録だと確かめられたとき（管理ディレクトリが `<共通のディレクトリ>/worktrees` の直下にあり、`worktree add` の前にはなかった名前で、その `gitdir` の記録が置き場所の `.git` を指す）だけ、同じ中断を控えた区間で `git worktree remove --force` で登録を外して失敗する（印のない作業ツリーを残さない。次の push は初回として作り直せる）。確かめられない・外せない・管理ディレクトリを特定できない場合は外さず、置き場所と直し方（中身を確かめて `git worktree remove --force` か `git worktree prune`、または `MINUTES_PRE_PUSH_DIR`）を示して失敗する（持ち主の分からない作業ツリーを再利用しない規則は変えない）。置き場所の判定（`worktree-location.py check`。作業ツリーの一覧を祖先の判定の前と後に取り、違えば判定の間に登録が変わったとして認めない）は、置き場所を `mkdir -p` で作る前と作った後の 2 回行い、2 回目も同じ実体のパスに解決されることを求める（判定と作成の間に置き場所を含む作業ツリーが登録された・途中がリンクに変わった場合に使わない）。展開（`checkout`・`sparse-checkout disable`）は `-c core.autocrlf=false` で行い、利用者の改行の変換の設定によらず `scripts/check.sh` などを LF のまま実行する。`git worktree add` はフックのロックを見ないので、下の作業ツリーの判定の後の登録は止められず、判定を足しても展開までの間が残る。そこで、専用の作業ツリーを `git worktree move` で推測できない名前の場所（`<作業ツリー>.work.XXXXXX/tree`。`mktemp -d`）へ rename で移し、そこで中の `.git` を調べ（残す 2 つの `.build` の中では、依存の複製が持つ `.git` のディレクトリは認め、登録された作業ツリーが持つ `.git` のファイルは認めない）、展開・`clean`・前の `.tools` の削除を行ってから、元の場所へ戻す。移すまでに登録された作業ツリーは中身を変えずに一緒に移り、`.git` の検査で見つけて、何も変えずに元の場所へ戻して失敗する。移した後に元の場所の下へ登録されたものは別の場所になり、展開・`clean` が及ばない（戻せないので、どちらも変えずに場所を表示して失敗する。次の push は、元の場所が空いていれば印を確かめて先に戻す。強制終了で移したまま終わった場合も同じで、登録されたパスが古ければ `git worktree repair` で直す）。検査する場所は変わらないので、ビルドの成果物とパスごとの DerivedData を再利用できる。`.tools` の写しの場所にほかのものができていれば、消さずに失敗する。`clean` の `-f` は 1 回だけ付け、`.git` を持つディレクトリを残す。続けて `clean -n -ffdx`（消さずに一覧だけを出す）で残ったもの（追跡していない複製など）があれば、検査せずに場所を表示して失敗する。元の作業ツリーが sparse checkout なら `git worktree add` がその指定を写すので、専用の作業ツリーで `core.sparseCheckout` が有効な場合だけ、そこで `sparse-checkout disable` を実行して全体を展開する（設定は環境変数 `GIT_CONFIG` を外して読む。設定されていると `git config` はそのファイルだけを読み、有効なのに展開しない。作業ツリーごとの設定に書くので元の作業ツリーは sparse のまま。有効でない場合に実行すると、sparse でないリポジトリにも作業ツリーごとの設定の拡張を書き足すので実行しない）。無視するファイル（`docs/` の下書き・`docs/.build` など）も消して文書の整合の検査に読ませず、`check.sh` が使う直下と Package の `.build` だけを残す（`.tools` は push のたびに写し直す）。残した直下の `.build` に、中断した前回の検査のカバレッジのロック（`.build/minutes-coverage.lock`。強制終了では `check.sh` の後始末が動かない）が残っていれば、作業ツリーのロックを取った後に持ち主のいない残骸として消す（`.build` がシンボリックリンクなら、リンクの先の中を消しうるので消さずに場所を表示して失敗する。Package の `.build` がリンクの場合も、カバレッジの記録をリンクの先に書くので同じく失敗する。この作業ツリーで検査するのは作業ツリーのロックを持つ実行だけなので、ほかの実行のロックを消すことはない。ビルドの成果物は消さない）。置き場所の判定と作業ツリーの登録の照合は `scripts/worktree-location.py`（`check`・`registered`・`nested`。`check.sh --require-clean` と共用する）が行う。プッシュ前のフックは、送信するコミットから 1 回だけ一時ファイルに書き出し、3 つの操作すべてに同じものを使う（元の作業ツリーが sparse checkout で `scripts/` がなくても判定できる）。作業ツリーの一覧は `git worktree list --porcelain -z` で取得し（git は、列挙できない登録を省いて成功するので、共通のディレクトリの `worktrees/` を列挙できることも確かめ、できなければ終了コード 2。ないのは認める）、NUL で区切った記録の `worktree` の項目（パスの全体）を比べる（改行を含むパスを分けない）。一覧を取得できなければ（git の失敗・形の違う出力）、作業ツリーを消さずに失敗する）。置き場所は作る前に実体のパスへ解決し、元のリポジトリと登録されたすべての作業ツリー（`git worktree list`）の中（リンクを経由する場合を含む）なら作らずに失敗する。中かどうかはパスの文字列ではなく、置き場所の既存の祖先（自身を含む）のどれかが作業ツリーとファイルシステム上で同じディレクトリ（デバイスと inode が一致）かで判定する（大文字小文字を区別しないボリュームの綴りの違いを見逃さず、区別するボリュームの別のディレクトリは別物として認める。判定できなければ失敗する。一覧を取得できなければ比べずに認めることはせず失敗する。どの作業ツリーの中でもない場所は認める）。リポジトリの中（`.git` の下など）に置くと、SwiftFormat が親のディレクトリの `.swiftformat` も読み、元の作業ツリーの設定が混ざるため置かない（複製したリポジトリで確かめた）。同じ理由で、置き場所（自身を含む）かその祖先のどれかに `.swiftformat`・`.swift-version` があれば失敗する（SwiftFormat は読む範囲を止める指定を持たない。専用の作業ツリーの中の設定はリポジトリの設定としてそのまま読む）。開発ツールは、元の作業ツリーの `.tools` を共有のリンクで指さず、導入のロック（`setup-tools.sh` と同じ `.install.lock`）を取ってから専用の作業ツリーの `.tools` に写し（APFS では `cp -c` の clone、対応しなければ通常の複写。ロックと導入の途中の作業場所は写さない）、ロックを外す（`rmdir` と持ち主の印の解除が終わるまで中断を控える。外せなければ印を残して失敗し、終了の後始末がもう一度外す。`check.sh` の `copy_tools` も同じ）。bin のリンクは相対パスなので写しの中を指し、手順 0 の照合と後の手順はすべてこの写しを使う（検査の途中に別の `setup.sh` が元の `.tools` の bin のリンクや版のディレクトリを差し替えても、照合した導入と使う導入が食い違わない）。ロックが取れなければ待ち（上限 600 秒）、待ち始めたことを表示する。作れなかった直後にロックが外れていた場合は、待たずに 1 回だけ取り直し、それでも作れずロックもなければ止める（`setup-tools.sh` と同じ扱い）。同時の push はロック（`<作業ツリー>.lock` のディレクトリ）で止める。終了の trap は入口で終了の状態を控え、ロックの解放の後にその状態で明示的に終了する（後始末のコマンドの状態で検査の失敗を上書きしない）。登録が残ったままディレクトリを消された場合は、上のとおり登録を上書きせずに場所を表示して失敗する（`worktree add --force` で作り直さない）。利用者は、別の場所へ移したものなら `git worktree repair` で登録を直し、不要なら `git worktree remove` で登録を外してから push し直す（`git worktree prune` はほかの作業ツリーの登録も外すので使わない）。フックが設定する `GIT_DIR` などの変数は外してから専用の作業ツリーを操作する。送信するコミットが HEAD と同じで、元の作業ツリーに未コミットの変更がないことは、従来どおり求める。
- **有効にする時期: 作業単位 2 の最後。** プッシュ前のフックは手順 0〜8 を回すので、プロジェクト設定とテストターゲットが揃う前に有効にすると、push できなくなる。作業単位 1 ではフックのファイルを置くだけにし、複製したリポジトリで動作を確かめた（§17）。
- フックを飛ばす指定（`--no-verify`）は使わない。クラウドの CI がない間は、PR の説明に貼った検査結果（対象のコミットのハッシュ付き）が統合の条件になる（基本設計 §6.2）。
- コミット前の検査の所要時間は、Swift ファイル 2 件の現状で約 1 秒である。遅くなると飛ばされるので、コードが増えたら測り直す（§17）。

---

## 6. 静的解析の規則

満たす要件: SEC-LOG-01、SEC-NET-05、SEC-SUP-01・02、TST-CI-03 ／ 決定: BD-DEV-05

### 6.1 基本設計の表との対応

基本設計 §7 の 14 行を、SwiftLint の組み込みの規則 3 つと独自ルール 20 個（`.swiftlint.yml` の `custom_rules`。`no_url_content_loading` を含む）で実現する。基本設計の表にない規則として、`no_url_content_loading` を足す（下の「基本設計の文言より広げた点」）。

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
| `UIPasteboard` | `no_direct_pasteboard` | コピーの型 |
| `.textSelection(` | `no_text_selection` | なし |
| `ShareLink` | `no_share_link` | なし |
| `WKWebView`・`import WebKit` | `no_webkit` | なし |
| `CoreSpotlight`・`NSUserActivity` | `no_spotlight_user_activity` | なし |
| `Date()`・`Date.now`・`UUID()`・`Task.sleep`・乱数 | `no_direct_clock`・`no_direct_uuid`・`no_direct_sleep_timer`・`no_direct_random` | 注入用の本番実装（UUID はテスト用の一時ディレクトリの型も） |

詳細設計で、基本設計の文言より広げた点:

| 規則 | 足したもの | 理由 |
| --- | --- | --- |
| `no_direct_user_defaults` | `@AppStorage` | SwiftUI から `UserDefaults` に直接書く経路になる（SEC-DAT-07） |
| `no_documents_directory` | `URL.documentsDirectory`・省略形の `.documentsDirectory`（メンバーの参照だけ。同じ名前の局所変数は認める） | 同じ場所を指す新しい書き方がある |
| `no_direct_directory_access` | メンバーの参照の `.temporaryDirectory` | 同じ場所を指す新しい書き方がある（同じ名前の局所変数は認める） |
| `no_direct_clock` | `ContinuousClock`・`SuspendingClock`・`CFAbsoluteTimeGetCurrent`・`DispatchTime.now`・`ProcessInfo.processInfo.systemUptime` など。型のプロパティの `Date.timeIntervalSinceReferenceDate`（保存した日時のインスタンスのプロパティは認める）。型の注釈を付けた省略形（`let recordedAt: Date = .now`・`: Date = .init()`） | 単調時計も注入の対象である（TST-GEN-01）。省略形は型の名前を書かずに現在時刻を読めるので、`Date.now` の検出だけでは見逃す（型の注釈のない `.now` は別の型のものとして認める） |
| `no_direct_uuid` | 型の注釈を付けた省略形（`let id: UUID = .init()`） | `UUID()` の検出だけでは型の名前を書かない生成を見逃す。引数のある `.init(uuidString:)`・`.init(uuid:)` は認める |
| `no_direct_sleep_timer` | `Thread.sleep`・`sleep(`・`Timer.scheduledTimer`・Timer の初期化子（`Timer(timeInterval:…)`・`Timer.init(…)`・`: Timer = .init(…)`）・`asyncAfter` など | タイマーも注入の対象である（TST-GEN-01） |
| `no_spotlight_user_activity` | `.userActivity(`・`.onContinueUserActivity(` | SwiftUI から `NSUserActivity` を提供する経路になる（SEC-PLT-02） |
| `no_url_content_loading`（新しい規則。例外の場所なし） | URL を受け取る読み込みの初期化子（`NSData(contentsOf:)`・`Data(contentsOf:)`・`String(contentsOf:)`・`NSDictionary(contentsOf:)`・`XMLParser(contentsOf:)` など。型の注釈つきの `.init(contentsOf:)` を含む。`NSAttributedString`・`NSMutableAttributedString` の `url:` の初期化子も同じ） | `https://` の URL を渡すと通信する経路になり、`no_network_api` では検出できない（SEC-NET-01、TST-SEC-01）。ローカルのファイルはパスを受け取る API（`contentsOfFile:`・`FileManager.contents(atPath:)`・`FileHandle`）で読む。`append(contentsOf:)` などのコレクションの操作は対象外 |

### 6.2 設定（`.swiftlint.yml`）

- 検査はリポジトリ直下から `--config` を付けずに実行する。付けると入れ子の設定（§6.4）が無効になる（SwiftLint 0.65.1 の README で確認）。
- 独自ルールの `match_kinds` に、コメントと文字列を除いた種類を指定する。コードとして現れた場合だけ検出し、説明のコメントに書いた名前を誤検出しない。
- 例外の場所（`excluded`）が 1 つのファイルの規則は、末尾を `\z` で固定する（`AppLogger.swift.swift` のような接尾辞の付いた名前を除外しない。SwiftLint は部分一致で照合する）。`excluded` の値は字下げ 6 の並びで書く（同じ行の値は `test-lint-rules.py` が失敗にする）。
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
      - '.*/Logging/AppLogger\.swift\z'
    match_kinds: *code
    message: "Logger はログの型の中だけで使う（BD-ARC-06、SEC-LOG-03）"
    severity: error

  no_network_api:
    name: "通信 API の禁止"
    regex: '\b(URLSession\w*|URLRequest|NSURLConnection|NW(Connection|Listener|Browser|PathMonitor|ProtocolWebSocket)\w*|CFStreamCreatePairWith\w+|CFSocket\w*|getStreamsToHost\w*)\b'
    match_kinds: *code
    message: "アプリのコードは通信 API を持たない（SEC-NET-01、BD-ARC-05）"
    severity: error

  no_network_import:
    name: "通信フレームワークの import の禁止"
    regex: '\bimport\s+(\w+\s+)?(Network|CFNetwork)\b'
    match_kinds: *code
    message: "Network / CFNetwork を import しない（SEC-NET-01）"
    severity: error

  # URL を受け取る読み込みの初期化子は、https:// などのリモートの URL も読めるので使わない（通信の経路になる）。
  # ローカルのファイルは、パスを受け取る API（contentsOfFile:・FileManager.contents(atPath:)・FileHandle）で読む。
  # append(contentsOf:) などコレクションの操作は対象外にするため、型の名前を付けた呼び出しと、型の注釈つきの .init だけを検出する
  # NSAttributedString・NSMutableAttributedString は url: の初期化子もリモートの URL を読める（string: などは認める）
  no_url_content_loading:
    name: "URL を受け取る読み込みの禁止"
    regex: '\b(?:NS(?:Mutable)?(?:Data|String|AttributedString|Dictionary|Array)|Data|String|AttributedString|XMLParser|NSXMLParser)\s*(?:\.\s*init\s*)?\(\s*contentsOf\s*:|:\s*(?:Foundation\s*\.\s*)?(?:NS(?:Mutable)?(?:Data|String|AttributedString|Dictionary|Array)|Data|String|AttributedString|XMLParser|NSXMLParser)\s*[?!]?\s*=\s*(?:try\s*[?!]?\s*)?\.\s*init\s*\(\s*contentsOf\s*:|\bNS(?:Mutable)?AttributedString\s*(?:\.\s*init\s*)?\(\s*url\s*:|:\s*(?:Foundation\s*\.\s*)?NS(?:Mutable)?AttributedString\s*[?!]?\s*=\s*(?:try\s*[?!]?\s*)?\.\s*init\s*\(\s*url\s*:'
    match_kinds: *code
    message: "URL を受け取る読み込みはリモートの URL も読める。ローカルのファイルはパスを受け取る API で読む（SEC-NET-01）"
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
  # 行コメントは条件側・本体側とも行末（CR か LF。CR だけのファイルを含む）まで後戻りせずに読み飛ばす（*+）。コメント内の「{」「}」を本体の始まりや終わりと取り違えない
  # 条件の中の丸括弧・角括弧の組は 2 段の入れ子まで、中の文字列ごと読み飛ばす（引数のクロージャー「f(where: { … })」の
  # 「{」「}」を本体と取り違えない。括弧なしの後置クロージャーは Swift が条件の中で認めない）。3 段以上の入れ子は検出しない
  # 括弧の中のブロックコメント（1 段の入れ子まで）と行コメントは 1 つの単位として読み飛ばす（コメントの中の「)」「"」で
  # 括弧が早く閉じた・文字列が始まったと取り違えない）
  # 拡張区切りの文字列（#"…"#・##"…"##・#"""…"""# など。「#」の数は後方参照でそろえる）も 1 つの単位として読み飛ばす。
  # 文字列を始める「#」は 1 文字として読まない（後戻りで「"」からふつうの文字列と読み直させない）。補間の中の文字列は読まない
  # ふつうの複数行の文字列（"""…"""）も 1 つの単位として読み飛ばす
  no_empty_catch:
    name: "空の catch の禁止"
    regex: '\bcatch\b(?=(?:[^{"/(\[#]|[(\[](?:[^()\[\]"/#]|/(?![/*])|//[^\r\n]*+|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\1))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\1))*+")\1|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*"|[(\[](?:[^()\[\]"/#]|/(?![/*])|//[^\r\n]*+|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\2))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\2))*+")\2|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*")*[)\]])*[)\]]|/(?![/*])|//[^\r\n]*+|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\3))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\3))*+")\3|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*")*\{\s*(?://[^\r\n]*+\s*|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/\s*)*\})'
    match_kinds: *code
    message: "エラーを握りつぶさない（SEC-LOG-04）"
    severity: error

  no_direct_user_defaults:
    name: "UserDefaults の直接の利用の禁止"
    regex: '\b(UserDefaults|NSUserDefaults|AppStorage)\b'
    excluded:
      - '.*/Settings/SettingsStore\.swift\z'
    match_kinds: *code
    message: "UserDefaults は設定の型の中だけで使う（SEC-DAT-07）"
    severity: error

  # Documents は保存ルートの型の中でも使わない（保存先は Application Support に限る）。例外の場所を持たない
  # documentsDirectory は API の参照（URL.documentsDirectory・省略形の .documentsDirectory）だけを検出し、同じ名前の局所変数は認める
  no_documents_directory:
    name: "Documents ディレクトリの禁止"
    regex: '\.\s*documentDirectory\b|\.\s*`?documentsDirectory\b'
    match_kinds: *code
    message: "保存先は Application Support に限る（SEC-DAT-04）"
    severity: error

  # temporaryDirectory は API の参照（FileManager.default.temporaryDirectory・URL.temporaryDirectory・省略形の .temporaryDirectory）
  # だけを検出し、同じ名前の局所変数は認める
  no_direct_directory_access:
    name: "一時ディレクトリの直接の指定とディレクトリの直接の作成の禁止"
    regex: '\bNSTemporaryDirectory\b|\.\s*`?temporaryDirectory\b|\bcreateDirectory\s*\('
    excluded:
      - '.*/Storage/StorageRoot\.swift\z'
      - '.*/TestSupport/TemporaryDirectory\.swift\z'
    match_kinds: *code
    message: "ディレクトリは保存ルートの型を経由して作る（SEC-DAT-01・03）"
    severity: error

  no_direct_pasteboard:
    name: "UIPasteboard の直接の利用の禁止"
    regex: '\bUIPasteboard\b'
    excluded:
      - '.*/Clipboard/ClipboardWriter\.swift\z'
    match_kinds: *code
    message: "コピーはコピーの型を経由する（SEC-PLT-01）"
    severity: error

  # コピーの型（ClipboardWriter.swift）でも使わない（no_direct_pasteboard と違い、例外の場所を持たない）
  no_text_selection:
    name: "textSelection の禁止"
    regex: '\.\s*textSelection\s*\('
    match_kinds: *code
    message: "文字列の選択からの標準のコピーは使わない。コピーはコピーの型を経由する（SEC-PLT-01）"
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
    # Date()・Date.init()・Date(timeIntervalSinceNow:) と、プロパティの .timeIntervalSinceNow（deadline.timeIntervalSinceNow）は
    # 現在時刻を読む。Date(timeIntervalSince1970:)・deadline.timeIntervalSince(now) などは認める
    # 型のプロパティの Date.timeIntervalSinceReferenceDate も現在時刻を読む（保存した日時のインスタンスのプロパティ
    # fixedDate.timeIntervalSinceReferenceDate は認める）
    # 起動からの時間は ProcessInfo.processInfo.systemUptime の呼び出しで検出する（systemUptime という名前の変数・プロパティは認める）
    # 型の注釈を付けた省略形（let recordedAt: Date = .now・: Date = .init()）も現在時刻の読み取りとして検出する。
    # 型の注釈のない .now（clock.now など別の型のもの）と、: Date = .init(timeIntervalSince1970:) は認める
    regex: '\bDate\s*(?:\.\s*init\s*)?\(\s*(?:\)|timeIntervalSinceNow\s*:)|:\s*(?:Foundation\s*\.\s*)?Date\s*[?!]?\s*=\s*\.\s*(?:now\b|init\s*\(\s*(?:\)|timeIntervalSinceNow\s*:))|\.\s*timeIntervalSinceNow\b|\bDate\s*\.\s*now\b|\bDate\s*\.\s*timeIntervalSinceReferenceDate\b|\bCFAbsoluteTimeGetCurrent\b|\b(ContinuousClock|SuspendingClock)\b|\bDispatchTime\s*\.\s*now\b|\bmach_(absolute|continuous)_time\b|\.\s*processInfo\s*\.\s*systemUptime\b'
    excluded:
      - '.*/SystemDependencies/.*\.swift'
    match_kinds: *code
    message: "時刻は注入した時計から得る（TST-GEN-01・07）"
    severity: error

  no_direct_uuid:
    name: "UUID の直接の生成の禁止"
    # 型の注釈を付けた省略形（let id: UUID = .init()）も生成として検出する。引数のある .init(uuidString:)・.init(uuid:) は認める
    regex: '\bUUID\s*\(\s*\)|\bUUID\s*\.\s*init\s*\(\s*\)|:\s*(?:Foundation\s*\.\s*)?UUID\s*[?!]?\s*=\s*\.\s*init\s*\(\s*\)'
    excluded:
      - '.*/SystemDependencies/.*\.swift'
      - '.*/TestSupport/TemporaryDirectory\.swift\z'
    match_kinds: *code
    message: "UUID は注入した生成器から得る（TST-GEN-04・07）"
    severity: error

  # 裸の sleep( はメソッドの宣言（func sleep(）を除く（no_bsd_socket と同じ後読み）
  # Task.sleep は型引数つきの呼び出し（Task<Never, Never>.sleep）も検出する（型引数は 1 段の入れ子まで読む）
  # Thread.sleep・Timer.scheduledTimer・Timer.publish も「.」の前後の空白（Thread . sleep）を Swift と同じく許して検出する
  # Timer の初期化子（Timer(timeInterval:…)・Timer(fire:…)・Timer.init(…)・型の注釈つきの : Timer = .init(…)）も、作った後に
  # RunLoop に足すと実時間で動くので検出する。名前の一部が Timer の型（ManualTimer(…)・TimerScheduler(…)）は認める
  no_direct_sleep_timer:
    name: "待機とタイマーの直接の利用の禁止"
    regex: '\bTask\s*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?\s*\.\s*sleep\b|\bThread\s*\.\s*sleep\b|(?<![.\w])(?<!\.\s{1,16})(?<!func\s)u?sleep\s*\(|\bDarwin\s*\.\s*u?sleep\s*\(|\bTimer\s*\.\s*(scheduledTimer|publish)\b|\bTimer\s*(?:\.\s*init\s*)?\(|:\s*(?:Foundation\s*\.\s*)?Timer\s*[?!]?\s*=\s*\.\s*init\s*\(|\bmakeTimerSource\b|\.asyncAfter\s*\('
    excluded:
      - '.*/SystemDependencies/.*\.swift'
    match_kinds: *code
    message: "待機とタイマーは注入した型を経由する。実時間を待つテストは書かない（TST-GEN-01）"
    severity: error

  # 「.」と名前の間の空白（Int . random(in:)）は Swift と同じく許す
  # random・randomElement・shuffled・shuffle は、注入した生成器を渡す呼び出し（using: の引数）を認め、既定の生成器を使う
  # 呼び出しだけを検出する。using: は呼び出しの最上位の引数のラベル（「(」の直後か、最上位の「,」の直後）だけを認める。
  # 引数の中の括弧（丸括弧・角括弧・波括弧）は 3 段の入れ子まで読み、入れ子の中の using:（辞書のリテラル [using: 1] や
  # 入れ子の呼び出しの引数）は認めない（それより深い入れ子のある呼び出しは、最上位に using: があっても検出する側に倒れる）
  # 引数の中のブロックコメントと行コメントは 1 つの単位として読み飛ばす（コメントの中の「(」「)」で括弧の対を取り違えて
  # 後ろの using: を見落とさず、コメントの中の「using:」を引数と見なさない）。文字列リテラル（1 行の "…"）も 1 つの単位として
  # 読み飛ばす（文字列の中の「(」「)」で括弧の対を取り違えず、文字列の中の「using:」を引数と見なさない）
  # ブロックコメントは 1 段の入れ子まで読む（/* 外 /* 内 */ 外 */ を 1 つのコメントとして扱う。no_empty_catch と同じ）
  # 行コメントは行末（CR か LF。CR だけのファイルを含む）で終わる（no_empty_catch と同じ）
  # 引数のラベル using と「:」の間のコメント（ブロックコメント・行コメント）は空白として扱う
  # 拡張区切りの文字列（#"…"#・##"…"##・#"""…"""# など。「#」の数は後方参照でそろえる）も 1 つの単位として読み飛ばす。
  # 文字列を始める「#」は 1 文字として読まない（後戻りで「"」からふつうの文字列と読み直させない）。補間の中の文字列は読まない
  # ふつうの複数行の文字列（"""…"""）も 1 つの単位として読み飛ばす
  no_direct_random:
    name: "乱数の直接の利用の禁止"
    regex: '\.\s*(?:random|randomElement|shuffled|shuffle)\s*\((?!(?:\s|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+)*\busing(?:\s|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+)*:|(?:/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\1))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\1))*+")\1|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*"|[^()\[\]{}/"#]|/(?![/*])|[(\[{](?:/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\2))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\2))*+")\2|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*"|[^()\[\]{}/"#]|/(?![/*])|[(\[{](?:/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\3))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\3))*+")\3|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*"|[^()\[\]{}/"#]|/(?![/*])|[(\[{](?:/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+|(#+)(?:"""[ \t]*\r?\n(?:[^"]|"(?!""\4))*+"""|"(?!""[ \t]*\r?\n)(?:[^"\r\n]|"(?!\4))*+")\4|#(?!#*")|"""[ \t]*\r?\n(?:[^"\\]|\\[\s\S]|"(?!""))*+"""|"(?:[^"\\\n]|\\.)*"|[^()\[\]{}/"#]|/(?![/*]))*[)\]}])*[)\]}])*[)\]}])*,(?:\s|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+)*\busing(?:\s|/\*(?:[^*/]|\*(?!/)|/(?!\*)|/\*(?:[^*]|\*(?!/))*\*/)*\*/|//[^\r\n]*+)*:)|\bSystemRandomNumberGenerator\b|\barc4random\w*\b|\bSecRandomCopyBytes\b'
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
- 規則の試験が、①これらのディレクトリにあって設定がない、②内容が上と 1 バイトでも違う、③これ以外の場所に入れ子の設定がある、④`disabled_rules` に組み込みの 3 規則以外の規則がある、⑤`disabled_rules` 以外の最上位の鍵（`custom_rules` での規則の上書き・`only_rules`・`excluded` など）がある、⑥`disabled_rules` が字下げした「- 規則名」の並び以外の形（流れの形 `[a, b]` など）で書かれている、のいずれも失敗にする。入れ子の設定は規則を外す抜け道になるためである。加えて、入れ子の設定の下でも独自の規則が効いていること（試験用のファイル `_test-code/Sample.swift` の `print(` の行で `no_print_logging` が検出されること）を確かめる。 `custom_rules` の各規則の属性（`excluded` など）は規則名の直下の階層で判定し、この検査と対照の設定（その規則の `excluded` だけを除いたもの）の作成が字下げ 4 の属性と字下げ 6 の `excluded` の項目だけを読むので、ほかの字下げで書いた属性・`excluded` の項目・属性の階層と違う字下げの `excluded` は、YAML として正しくても読めない設定として失敗にする（`excluded` を読み落として、例外のない規則と取り違えない）。

### 6.5 規則の試験（`scripts/test-lint-rules.py`）

規則ごとに `tools/lint-fixtures/<規則名>/` を置く（BD-DEV-05）。

| ファイル | 内容 | 判定 |
| --- | --- | --- |
| `violation.swift` | 検出される例。検出されるはずの行の末尾に `// expect: <規則名>` を書く | 印の行の集合と、検出された行の集合が一致する |
| `ok.swift` | 検出されない例（コメント・文字列の中の名前、似た名前の別の API など） | 検出が 0 件 |
| `allowed/<例外のパス>` | 例外の場所に置いた違反 | 検出が 0 件。対照として、その規則の `excluded` だけを除いた設定（一時ディレクトリに書き、`--config` で渡す）で、同じパスのまま解析すると、規則が 1 件以上検出する（パスを変えないので、ほかの例外の場所や `TMPDIR` の置き場所に左右されない。本来の試験は元の設定で行う。違反を含まない内容で例外の試験を通さない） |
| `violation/<パス>`（任意） | 例外の場所に似たパスに置いた違反。例外を持たない規則で、パスによる抜け道がないことを確かめる（例: `no_documents_directory/violation/Storage/StorageRoot.swift`） | 印の行の集合と、検出された行の集合が一致する |

- 「1 件以上検出する」ではなく行の集合の一致で判定する。規則が弱くなった場合（3 つのうち 1 つしか検出しないなど）を見逃さないためである。
- `excluded` の値は、字下げ 6 の 1 行の「- 値」の並びだけを認める（流れの形・複数行の値・次の行の文字列・ブロックの文字列は、対照の設定で正しく除けない）。`custom_rules` に読めない書き方があれば、SwiftLint を実行する前に終了コード 1 で止める。
- SwiftLint の検出は、試験で見ているファイルと規則の組だけを読む。その組の検出に行番号がなければ、印の行と比べられないものとして失敗にする。
- 最上位にほかの設定への参照（`parent_config`・`child_config`）があれば、試験の前に終了コード 2 で止める（参照の相対パスは設定の場所から解決されるので、一時ディレクトリに置く対照の設定では同じものを読めない）。
- 試験のない規則は作れない。規則に試験用のディレクトリがない、例外のある規則に `allowed/` がない、規則のないディレクトリが残っている、のいずれも失敗にする。
- 試験用のファイルは通常の解析と整形から除く（`.swiftlint.yml` の `excluded`、`.swiftformat` の `--exclude`）。試験では、ファイルを明示して渡すことで除外を越える。

### 6.6 規則で検出できないもの

正規表現による規則には限界がある。次は PR のチェックリスト（security-requirements.md §10.2）と、自動テスト（通信ゼロ。TST-SEC-01）で補う。

| 検出できない例 | 補う手段 |
| --- | --- |
| 使う側で型を省いた書き方（`Date` 型の引数に渡す `schedule(at: .now)` など） | レビュー。注入した時計を使っていないテストは、時刻を固定できず不安定になって見つかる |
| 文字列から組み立てた呼び出し（`NSClassFromString("URLSession")`） | 通信ゼロの自動テスト（TST-SEC-01）、実機の確認（TST-SEC-02） |
| ログに D1〜D3 を入れていないか | 型付きのログ（BD-ARC-06）、TST-SEC-07 |
| 例外の場所の中での誤用 | レビュー（例外の場所を変える PR は、規則の変更として扱う） |
| `no_empty_catch` の条件で 3 段以上に入れ子にした括弧（`where f(g(h({ … })))` など）。条件の丸括弧・角括弧は 2 段の入れ子まで読み飛ばし、それより深い入れ子のある `catch` は検出しない | レビュー。PR のチェックリストの「エラーを握りつぶしていないか」 |
| `no_empty_catch`・`no_direct_random` の条件・引数の、補間の中に文字列を含む文字列（`"\(f("a"))"`・`#"\#(f(#"a"#))"#`）。補間を解釈しないので、文字列の終わりを取り違えうる | レビュー |

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
- gitleaks の全体の検査はコミット済みの履歴を対象にする。未コミットの変更は、コミット前のフック（ステージした内容）が受け持つ。
- 履歴の検査には `--log-opts="--all --full-history --diff-merges=separate --root --no-textconv --text --no-color --no-show-signature --no-renames --no-notes --no-relative --pretty=tformat:commit%x20%H%nAuthor:%x20%an%x20<%ae>%nDate:%x20%aI%n --no-abbrev-commit --src-prefix=a/ --dst-prefix=b/ -O/dev/null"` を渡す（日時は固定の形式（厳密な ISO 8601 の `%aI`）で出す。`%ad` は設定 `log.date`（`relative` など）で形式が変わり、gitleaks が履歴を読めなくなりうるため。コミットのメッセージは出さない。`--redact` は報告の `Message` を伏せず、鍵を書いたメッセージが報告に残る（8.30.1 で確かめた）ため。gitleaks はメッセージを検査しないので検出は変わらず、コミット・作者・日時・ファイル・行は報告に残る。`-O/dev/null` は、設定 `diff.orderFile` の指すファイルがなくても `git log` を失敗させず、読める履歴を検査する）（接頭辞は、設定 `diff.noprefix`・`diff.mnemonicPrefix`・`diff.srcPrefix`・`diff.dstPrefix` に左右されず、gitleaks がファイルの名前を正しく読むように明示する）（`scripts/gitleaks-history.sh` の `GITLEAKS_LOG_OPTS`）。`gitleaks-history.sh` が gitleaks に渡す追加の引数は、自己試験が結果を読むための報告の指定（`--report-format`・`--report-path`。それぞれ 1 回まで）だけとし、ほかの指定は渡さずに終了コード 2 で止める（検査の方式・対象・抑止・伏せ字・終了コードを呼び出し側から変えさせない）。gitleaks（8.30.1）は git log の失敗（欠けたオブジェクトなど）を終了コードに反映しないので、欠けた blob のある一時リポジトリでは「no leaks found」で通る。`git log` が失敗・中断した場合と、標準エラーに出力した場合（gitleaks はそこで走査をやめる）も、8.30.1 は「no leaks found」・終了コード 0 で通す（2026-11-23 に代わりの git で確かめた）。そこで、gitleaks にだけ、実際の git を呼んで `git log` の終了コードと標準エラーの有無を記録する代わりの git を `PATH` の先頭で渡し、最後まで終わった記録がなければ検出なしでも終了コード 1 にする（同じ全履歴の `git log` を先に別に実行することはしない。走査は 1 回だけ）。既定の `git log` はマージのコミットの差分を出さず、マージの解決で足した行を見落とす（`-m` は形式が設定 `log.diffMerges` に左右されるので使わない）。`--root` は設定 `log.showRoot=false` でも最初のコミットの差分を出す。`--no-textconv` は `.gitattributes` の差分の変換（textconv）を使わず、元の内容の差分を出す（変換で鍵の行を消させない）。`--text` は `.gitattributes` で `binary`（`-diff`）にしたファイルも本文の差分として出す（「Binary files differ」で鍵の行を隠させない）。`--no-color` は設定 `color.diff`・`color.ui` が `always` でも差分に色の制御文字を入れない（行が分断されて鍵を見落とさない。8.30.1 で、付けないと検出しないことを確かめた）。`--no-show-signature` は設定 `log.showSignature=true` でも署名を検証しない（検証の失敗が標準エラーに出て、走査の失敗と取り違えない。標準エラーの確認は残す）。`--no-renames` は名前の変更を検出しない（数が設定 `diff.renameLimit` を超えると警告が標準エラーに出て、同じく取り違える。名前を変えたファイルの全体が差分に出る）。`--no-notes` は注記（git notes）を表示しない（設定 `notes.displayRef` の指す参照がないと警告が標準エラーに出て、同じく取り違える。注記のコミット自体は `--all` がたどるので検査する）。`--no-relative` は、設定 `diff.relative=true` で引数がサブディレクトリのときも、その外の差分を除かない。**検出の抑止の仕組みは使わない。** 全履歴の検査とコミット前の検査の両方で、`--gitleaks-ignore-path /dev/null`（実行時の場所の `.gitleaksignore` を読ませない）と `--ignore-gitleaks-allow`（行の `gitleaks:allow` のコメントを無視する）を渡す。gitleaks は検査する場所の直下の `.gitleaksignore` を指定によらず読む（8.30.1 で確かめた）ので、検査する場所に `.gitleaksignore` があれば検査せずに失敗する（承認の記録なしに検出を隠せるため）。`gitleaks-history.sh` は、外から引き継いだリポジトリを指す変数（`GIT_DIR`・`GIT_WORK_TREE` など）を外し、`GIT_NO_REPLACE_OBJECTS=1` を設定して置き換えのオブジェクト（`git replace`）を使わずに元のオブジェクトを読ませてから、浅いクローンの判定と検査を行う。旧来の grafts（`git rev-parse --git-path info/grafts` の位置）は `GIT_NO_REPLACE_OBJECTS` では外れず、コミットの親を付け替えて履歴の一部を隠せる（git 2.55.0 で確かめた）。コメントと空行だけのファイルでも git が非推奨の案内を標準エラーに出し、gitleaks 8.30.1 は「stderr is not empty」で履歴を読まないまま「no leaks found」・終了コード 0 で通す（同じく確かめた）ので、grafts のファイルがあれば内容によらず検査せずに失敗する。`--log-opts` は既定の指定を置き換えるので、既定の `--all --full-history` も書く。手順 3 と自己試験は同じ `scripts/gitleaks-history.sh` を実行する（指定の文字列があることだけを確かめると、手順 3 が `--log-opts` を渡さなくなっても自己試験が通るため）。自己試験は、このコマンドで通常のコミットとマージのコミットだけで足した行の両方が検出され、対照として `gitleaks` を指定なしで直接実行するとマージの行を見落とすことを確かめる。`check.sh` の手順 3 がこのコマンドを呼んでいることも確かめる。

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

- 条件付きの設定（`SWIFT_VERSION[config=Debug]` など）は、構成（`configurations` の各要素）ごと・SDK（`iphoneos`・`iphonesimulator`）ごとに効く値を求めて検査する。必須の設定は、どれかの構成・SDK で値が決まらない・許す値でなければ違反とする（SDK の条件付きの書き方だけで、片方の SDK の値を欠くことを見逃さない）。効く値は、ターゲットの構成の条件が一致する書き方 → ターゲットの条件のない書き方 → プロジェクトの構成の条件が一致する書き方 → プロジェクトの条件のない書き方、の順で最初に見つかったもの。ターゲットにある書き方だけを見ると、ターゲットに Debug だけ、プロジェクトに Release だけがある場合に Release の値を見落とすためである。構成以外の条件（`sdk=`・`arch=`）の書き方は、そのビルドで効きうるのですべて検査する。利用目的・向き・参照先の plist は、`arch=` の書き方があれば arch ごと（よく使う値・書き方に現れる値・どれとも一致しない値を代表とする）にも分けて判定し、`arch=` の条件は一致する arch にだけ効くとする。SDK を指定して判定する場合（下の利用目的）は、`sdk=` の条件を版つきの SDK 名（`xcrun --sdk <SDK> --show-sdk-version` から作る `iphoneos27.2` など。Xcode が照合する名前）と照合し、空の条件はどの SDK にも当たらないとする。版つきの名前を取得できない場合は、条件を無視せず読めないもの（終了コード 2）とする。どの段にも値がない構成は「設定されていない」とする。
- 空でない値が要る鍵（マイクの利用目的）は、ビルド設定の値ではなく、**各構成で最終的に生成される Info.plist** で判定する。`GENERATE_INFOPLIST_FILE` が `YES` の構成では `INFOPLIST_KEY_…` と `INFOPLIST_FILE` の参照先の両方を、`NO`（設定がない場合も `NO`）の構成では参照先だけを見る（`NO` では `INFOPLIST_KEY_…` が使われないため）。判定は構成と SDK（`iphoneos`・`iphonesimulator`）の組ごとに行い、`sdk=` の条件付きの書き方は条件に合う SDK でだけ効くとする。ビルド設定の値が一部の SDK の条件付きだけにある場合（例: `INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphonesimulator*]` だけ）、条件に合わない SDK では参照先の Info.plist が鍵を持たなければ欠落とする（`resolve` が返す「決まらない組」を捨てずに使う）。`GENERATE_INFOPLIST_FILE` が決まらない組（`arch=` の条件付きだけのときの、条件の外の arch など）では、生成しない場合も起こりうるとして参照先の Info.plist も求める。`INFOPLIST_FILE` の参照先が条件付きだけで決まらない組では、参照先で補ったことにしない（別の arch の参照先で補わない）。どこにも鍵がない組は欠落として報告する（例: 構成 Debug（iphoneos））。どちらが優先されるかに頼らないよう、効きうる値は**すべて**空でないこと（条件のない値に説明文があっても、Release の条件付きの値や参照先の値が空なら違反）。
- すべてのターゲットで満たす値は、変数を実際にビルドする構成・SDK・arch の組ごとに展開してから許す値と比べる。展開できない値は違反とする。
- 空でない値が要る鍵で、効きうる候補（ビルド設定の値・参照先の plist の値）のうち 1 つでも変数を展開できないものがあれば、ほかの候補で補えても違反とする（展開できない候補を、別の候補の合格で見逃さない）。 参照先の plist の値に変数がある場合は、向きの検査と同じく、ビルドがその構成・SDK で参照先の plist の変数を展開する（`INFOPLIST_EXPAND_BUILD_SETTINGS` が YES、または設定がない）と決められるときだけ展開後の値で判定し、NO の構成・決められない構成では違反とする（ビルドの結果に変数の文字列がそのまま残りうる）。変数のない値は、展開の設定によらず空でなければ認める。
- 有効にすると違反になる鍵（`UIFileSharingEnabled` など）の値が変数（`$(NAME)`）を含む場合は、その値を使うターゲットの各構成・各 SDK（`iphoneos`・`iphonesimulator`）のビルド設定で展開してから判定する。プロジェクトの設定はすべてのターゲット、`INFOPLIST_FILE` の参照先はそれを参照するターゲットの、参照する構成・SDK のビルド設定だけで展開する（一部の構成だけが参照する plist を、参照しない構成の値で判定しない）。ビルドが参照先の plist の変数を展開しない構成（`INFOPLIST_EXPAND_BUILD_SETTINGS` が NO）・展開するかを決められない構成では、展開できないものとして報告する（リテラルの値はそのまま判定する）。専用の作業ツリーの検査では、`check.sh` の手順 4 が `--tracked-only` を渡し、`INFOPLIST_FILE`・`CODE_SIGN_ENTITLEMENTS` の参照先と、名前で見つけた `Info.plist`・`.entitlements` の実体（リンクは解決する）が起点の中の、HEAD のコミットで追跡した通常のファイルであることを求める（送信するコミットにないファイルの内容で合格させない。満たさなければ終了コード 2）。通常の実行では求めない。`GENERATE_INFOPLIST_FILE` の値も同じく構成・SDK ごとに展開してから生成の有無を判定し、展開できなければ生成する・しないの両方の経路を検査する。1 つでも有効な値に展開されれば違反、展開できなければ（どのターゲットも参照しない plist の値を含む）無効とみなさず違反として報告する。iPhone のビルドで使われうる修飾子つきの鍵（`UIFileSharingEnabled~iphone`・`UIFileSharingEnabled-iphoneos`・`UIFileSharingEnabled-iphoneos~iphone` など）も、元の鍵と同じ規則で判定する（修飾子で禁止を迂回させない）。
- 画面の向き（BD-UI-10）も、構成と SDK の組ごとの Info.plist の生成の有無で判定する。生成する組では `INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone` か使われる plist の向きの指定のどちらかで縦だけに決まること（plist が縦だけを持てば、ビルド設定がなくても違反にしない）、生成しない組では使われる plist に縦だけの指定があること、両方がありうる組では両方を求める。効きうるビルド設定の値と plist の指定は、どれも縦だけであること。
- **読めないものを合格にしない。** 形式が違う・壊れているファイルは、終了コード 2 で失敗する。入れ子が 100 段を超える・循環する plist も同じ。同じ辞書の中に重複した鍵のある plist（XML・バイナリ形式）も同じ（後の値が前の値を検査から隠すため）。

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
# アプリのターゲットで、Info.plist を生成する構成の向きのビルド設定（BD-UI-10）。生成するかは構成・SDK ごとに決まるので、
# 一律の必須の設定にせず check_plist_orientation で生成の有無に合わせて確かめる
ORIENTATION_SETTING = "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone"
ORIENTATION_SETTING_VALUES = ("UIInterfaceOrientationPortrait",)
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
- マイクの利用目的は、ビルド設定の元の値（文字列化の前）が文字列であることを確かめる。plist とビルド設定の値の変数（`$(NAME)`）は構成・SDK ごとに展開してから空かを判定し、展開できない値（設定のない変数・1 つの値に決まらない変数・修飾子つきの参照）は欠落として扱う。
- ビルド設定の値の変数は、その書き方の条件（構成・SDK・arch）に合うビルドだけで展開する（`INFOPLIST_KEY_…[arch=arm64]` の参照する `SHARE[arch=arm64]` を、ほかの arch で展開できないとしない）。
- 必須の設定（`REQUIRED_ALL_TARGETS`）の判定・必要な値の欠落の判定と変数の展開は、構成・SDK ごとに実際にビルドする arch（`ARCHS` から `EXCLUDED_ARCHS` を除いたもの。`ARCHS` がなければ Xcode の既定の `iphoneos` = `arm64`、`iphonesimulator` = `arm64 x86_64`。2026-11-25 に Xcode 27.2 の `xcodebuild -showBuildSettings` で確かめた）だけで行う。`ONLY_ACTIVE_ARCH` は見ない。決められない値（展開できない・空など）の SDK は、当たりうる arch をすべて判定する。禁止する鍵を探す参照先は、ビルドしない arch のものも含める。
- 空の条件付きの `INFOPLIST_FILE`（`INFOPLIST_FILE[arch=arm64] = ""` など）は、その条件のビルドにだけ参照先がないことを表す。その条件のビルドは条件のない参照先で補わず（生成する構成ならビルド設定の値で判定する）、ほかのビルドは条件のない参照先で判定する。
- 画面の向き（BD-UI-10）は、`GENERATE_INFOPLIST_FILE` が `NO` になりうる構成では、使われる plist に `UISupportedInterfaceOrientations~iphone`（または `UISupportedInterfaceOrientations`）が `[UIInterfaceOrientationPortrait]` であることを求める。どの構成でも、参照先の plist にある向きの指定は縦だけであること。
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
  "allowedLicenses": ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD"],
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

ローカルのバイナリ（`.binaryTarget(path:)`）を承認する要素は、`url` の代わりに `path`（リポジトリの起点からの相対パス。`Package.swift` のディレクトリと宣言の `path:` をつないで正規化したものと照合する。前後の空白は除かず、前後に空白のある `path:` は照合せずに拒否する。依存のファイルの走査で読めないディレクトリがあれば終了コード 2。名前が合うのに通常のファイルとして確かめられないもの（壊れたシンボリックリンク・同じ名前のディレクトリなど）も黙って除かず、終了コード 2）と `sha256`（承認したときの内容のハッシュ。ファイルはその内容の SHA-256、ディレクトリは配下の項目を相対パスの順に「種別・相対パス・内容の SHA-256（シンボリックリンクはたどらずにリンクの先の文字列）」として連ねた SHA-256。ビルドはリンクをたどるので、成果物の外を指すリンク（絶対パス、またはたどった先が成果物の外）は読めないものとして終了コード 2 で失敗する。成果物の中に読めないディレクトリがあれば、欠けた内容からハッシュを作らず、読めないものとして終了コード 2 で失敗する（配下の走査は失敗を伝える `os.walk` で行い、失敗を黙って読み飛ばす `rglob` を使わない）。配下のパスを UTF-8 にできない場合（UTF-8 でない名前を許すファイルシステム）も、記録の形を決められないので、そのパスを示して終了コード 2 で失敗する（APFS は UTF-8 でない名前を作れないので、この Mac では試験できない）。`check-dependencies.py` の `content_digest`）を持ち、ほかの項目（`identity`・`version`・`license`・`approvedOn`・`ledger`、必要なら `ownerDecision`）は同じく必須とする。リモートのバイナリ（`.binaryTarget(url:checksum:)`）を承認する要素は、`url` に加えて `checksum`（承認した配布物の SHA-256）を持つ。`sha256`・`checksum` は 64 桁の小文字の 16 進数とし、形式が違えば終了コード 2。`url` と `path` の両方を持つ・どちらも持たない要素は、読めないものとして終了コード 2 で失敗する。`checksum` を持つ要素はリモートのバイナリの配布物だけを承認し、Package の依存（`.package(url:)` の宣言と `Package.resolved` の確定版）は承認しない（版の照合の索引に入れない。同じ URL の Package を承認する要素は、`checksum` を持つ要素と同じ URL では持てない）。許可リストの JSON に重複した鍵があれば（後の値が前の値を隠す）、読めないものとして終了コード 2 で失敗する。

### 9.2 検査（`scripts/check-dependencies.py`）

| 対象 | 規則 |
| --- | --- |
| `Package.resolved`（すべて） | 各依存の名前・版・取得元の組が、承認済みの要素と一致する（承認は取得元ごとに持ち、同じ名前と版を別の取得元で承認しても上書きしない）。名前と版が一致しても、取得元（`location`。古い形式は `repositoryURL`）を正規化して承認済みの URL と比べ、違う・ない場合は認めない。版で固定していない依存（ブランチ・コミットの指定）は認めない。形式の版（`version`）は整数の 1〜3 だけを読み、`pins` の位置（1 は `object` の中、2・3 は最上位）が合わなければ終了コード 2。同じファイルの中で名前（大文字小文字を区別しない `identity`。古い形式は `repositoryURL` の最後の要素）が重複すれば終了コード 2（承認の照合にも同じ名前を使い、古い形式の表示名 `package` は使わない。SwiftPM も拒否する。2026-11-25 に Xcode 27.2 の `swift package resolve` で確かめた。別のファイルの同じ名前は認める） |
| `Package.swift`（すべて。版別の manifest `Package@swift-<版>.swift` も同じ検査にかける） | 先にコメント（`//`・入れ子にできる `/* */`）を空白に置き換える（文字列リテラルの中の `//` は残す。閉じていないブロックコメント・文字列リテラル・補間は読めないものとして失敗する。末尾まで文字列と見なすと後ろの宣言を隠すため）。宣言と `url:`・`path:`・`id:` などのキーワード（ラベルと「:」の間の空白・コメントを除いた跡を許す）は、さらに文字列リテラルの本文を空白に置き換えた内容から探し（文字列の中の `.package(` を宣言と見なさない）、取得元と版はその宣言の引数の中だけで調べる。`url:` で指す依存が、承認済みの URL と一致する。ローカルの Package（`.package(path:)`）は、参照元の `Package.swift` のディレクトリから解決した参照先が起点の中の `Packages/` の下にあり（リンクを解決した実体も。`check.sh` の手順 7 がカバレッジを測る写しは `Packages/` だけを展開するので、その外の Package は写しの中で解決できない）、検査で除外するディレクトリの中でなく、`Package.swift` を持つ場合だけ認める（起点の中の manifest はすべて検査するので、参照先の未承認の依存・バイナリも検出される。起点の外・manifest のない参照先は中を検査できないので認めない）。宣言の識別子はバッククォートで囲んだもの（``.`package`(…)``）も同じに扱い、呼び出しの形でないメンバーの参照（`Package.Dependency.package` を値として渡すなど）は宣言を判定できないものとして認めない。ローカルのバイナリ（`.binaryTarget(path:)`）は、宣言と承認の `path` が起点の中を指し（承認の `path` が絶対パス・起点の外なら許可リストを読めないものとして終了コード 2。宣言が起点の外なら違反。リンクを解決した実体が起点の外なら内容のハッシュを読まずに違反）、ローカルの Package と同じく `Packages/` の下にあり（字面のパスと、リンクを解決した実体の両方。手順 7 の写しは `Packages/` だけを展開するので、外のバイナリは `swift test` が解決できない）、`path` の一致する承認済みの要素（§9.1）があり、現在の内容のハッシュがその `sha256` と一致する場合だけ認める（内容が変われば再承認を求める。成果物がなければ終了コード 2）。リモートのバイナリ（`.binaryTarget(url:)`）は、URL の一致に加えて、宣言の `checksum:`（文字列リテラル）が承認した `checksum` と一致する場合だけ認める（同じ URL の配布物の差し替えを通さない）。レジストリの依存（`id:`）は認めない。**`.package(…)`・`.binaryTarget(…)` の宣言をすべて列挙し、取得元が文字列リテラルだけの `url:` か `path:` でないもの（変数・連結・補間・その他の式）は、取得元を判定できないものとして認めない**。`url:` の依存は、版の指定（`from:`・`exact:`・`.upToNextMajor`・`.upToNextMinor`・`..<`・`...`）を、**同じディレクトリの** `Package.resolved` の確定版と照合する。版の文字列リテラルは、後ろが「,」「)」か引数の末尾の場合だけ版と見なす（範囲の下限は、前が「(」「,」「:」か引数の先頭の場合だけ）。`"1.0.0" + x` のように式の一部になっているものは、版の指定を判定できないものとして失敗する。確定版がない、ブランチ・コミットの指定、版の指定を文字列リテラルから判定できない、確定版が宣言の版の指定を満たさない（古い承認済みの確定版が残った場合を含む）、確定版が承認した版でない、のいずれも認めない。ローカルの Package の `Package.swift` がシンボリックリンクなら、カバレッジの測定の写しと同じ規則（`check.sh` の `verify_coverage_links`）で、`Packages/` の中を指す相対パスだけを認める 間接の依存は manifest ごとに宣言を持ち、ツールの版（接尾辞と tools-version の境目）ごとに選ばれる manifest の組でたどる（別の版別の manifest の辺を組み合わせない）。どれかの組でリモートの依存に届けば、起点の確定版を求める。選び方は 2026-11-25 に Swift 6.4.2 の `swift package dump-package` で確かめた（完全に一致する接尾辞、なければ tools-version が `Package.swift` より新しい最大の下位の版、それ以外は `Package.swift`。`Package.swift` の tools-version がツールより新しければ、最大の下位の版を選び、それも読めなければ次の版へは移らない。tools-version は行頭の「//」で最初に一致した行を読み、6.0 以降なら先頭の行でなくてもよく、5.x は先頭の行だけ。候補の接尾辞は [ToolsVersion.swift](https://raw.githubusercontent.com/swiftlang/swift-package-manager/main/Sources/PackageModel/ToolsVersion.swift)）。 |
| `project.xcproj` | リモートの URL の形をした文字列が、承認済みの URL と一致する。承認済みの URL は、**同じ `.xcodeproj` の中の** `Package.resolved` に同じ取得元の確定版がある。要求条件（版の指定）は書き方が未確認（§20 の断定しない事項）なので、確認して検査を実装するまでは判定できないものとして認めない |

- ローカルの Package の参照先の要素（途中を含む）がシンボリックリンクなら、解決する前に、manifest のリンクと同じ規則（相対パスだけで、途中も `Packages/` の中）を満たすことを求める（解決した先が中でも、測定の写しの中で解決できないものを認めない）。
- ローカルのバイナリの途中のディレクトリと成果物の中のリンクは、ローカルの Package と同じく、測定の写しの中を指す相対パスだけを認める（手順 7 の `verify_coverage_links` と同じ規則）。
- ローカルの Package（`.package(path:)`）は間接のものまでたどり、その先のリモートの依存を、ビルドの起点になる各 Package 自身の `Package.resolved` と照合する（SwiftPM は起点の確定版だけを使う）。ローカルの Package 自身の確定版も、単独の起点として照合する。
- 許可リスト自体も検査する。`allowedLicenses` にないライセンスで `ownerDecision` がない要素、承認日のない要素があれば、終了コード 2 で失敗する。
- 追加の手順は台帳 §4 の 7 手順による。許可リストへの追加は、台帳 §3 への追記と**同じ PR で**行い、オーナーが PR のレビューで承認する（TST-CI-05）。費用が発生しうるもの・GPL 系・独自のライセンスは、オーナーの確認が記録されるまで足さない。

---

## 10. 文書の整合の検査（`scripts/verify-docs.py`）

満たす要件: NFR-MNT-04 ／ 決定: UD-06

| 検査 | 内容 |
| --- | --- |
| ID の定義 | 定義文書の表から ID を集める（データ行の先頭の列を、列の区切りの前後の空白を除いてから判定する。空白のない表も、行頭・行末の `\|` を省いた表の行も読む。表は区切りの行を起点に、直前の行を見出しとして判定する）。設計決定（`BD-…`）は、基本設計の各文書の §1（`architecture.md` だけは §3。`docs/README.md` §3）の表の行だけを定義とする（ほかの節の表の先頭の列の BD は定義にしない）。定義の重複を失敗にする |
| ID の参照 | すべての文書（`docs/`・`README.md`・`CLAUDE.md`）が参照する ID が定義されている。`SEC-SUP-01〜05`・`NFR-MNT-01・03`・`SEC-NET-03 / 04`（スラッシュ区切り。「/」の前後の空白は 1 つまで）・`FR-REC 群` のような省略した書き方も展開して調べる。範囲（〜）の要素数が同じ接頭辞の定義済みの ID の数を超える書き方は展開せず、不正として報告する（巨大な範囲の展開を防ぐ） |
| フェーズ | 定義文書の FR / NFR / SEC の行は、フェーズの列を持ち、値が MVP・P2・P3 のいずれかで始まる。列がない・読めない行は行番号つきで失敗する |
| MASVS 分類 | SEC の行は、MASVS の列を持ち、値が空でない（コメントだけの値は空とする）。列がない・空の行は行番号つきで失敗する |
| MVP の網羅 | フェーズが MVP の FR / NFR / SEC が、基本設計の各文書（索引の README.md を除く）の「満たす要件」（節・小節の見出し（`##`・`###`）の直後の最初の本文行にある「満たす要件:」と、文書の冒頭（最初の `##`・`###` より前）の表（見出しの行と区切りの行を持つもの）の、先頭の列が「満たす要件 ID」のデータ行だけ。表として成り立たない単独の「\| 満たす要件 ID \|」の行は数えない。節の後半にある同名の行や「満たす要件の例」などの説明文は数えない）の、「／」より前（決定・関連などの参照を除く）のどこかに現れる。同じ行で閉じる HTML のコメント（`<!-- … -->`）の中の ID は表示されないので数えない（節の冒頭の「満たす要件:」の要件 ID・「—」の判定でも同じ）。索引は「SEC 全般」や群の表記でまとめて挙げるため、判定に含めると欠落を隠す |
| 書式 | 相対リンクの先がある（インラインのコードの中のリンクの書き方は確かめない。リンク先を山括弧で囲んだ書き方は、山括弧を除いて解決する。山括弧のないリンク先の括弧は対をとって読む（`design(v2).md` の「)」をリンクの終わりとしない）。リンク先の後ろのタイトル（`"…"`・`'…'`・`(…)`。リンク先もタイトルも、同じ段落の次の行にあってもよい。診断の行はリンクの始まりの行）は除いてリンク先だけを解決する）。参照形式のリンク（`[文][ラベル]` と省略形の `[ラベル][]`）は、インラインのリンクのリンク先とタイトルの中を除いて探し、フェンスの外の定義（`[ラベル]: リンク先`。ラベルは大文字小文字と連続した空白を区別しない）を通してリンク先を求め、同じく確かめる。定義は段落の始まりの行（定義の続きを含む）だけを採り、段落の文の続きにある定義の形の行は採らない。リンク先の後に完全なタイトル以外の文字が続く行も採らない。引用の中のコードフェンスの本文のリンクは確かめない。リンクの中にリンクは入らないので、画像でないリンクが成立したら、それを囲む画像でない「[」はリンクの始まりにしない（画像を含むリンクは、画像とリンクの両方を確かめる）。空行などで段落が切れた後の空白 4 個以上（タブは 4 桁）の行は字下げしたコードブロックとして、中のリンクの書き方を確かめない（段落の続きの行は確かめる。箇条書きの中では項目の本文の位置から数えるので、項目の中の段落・入れ子の項目は確かめる）。引用の中のフェンスにも言語の指定を求める（指定だけを確かめる。引用が終わればフェンスも終わる）。HTML のコメントで始まる行は全体が HTML のブロックなので、閉じた後ろのリンクの書き方も確かめない（コード片の印の照合は続ける）。短縮形（`[ラベル]`）も同じく確かめる（定義の行・エスケープした「[」・脚注は使用として数えない）。定義のないラベルはリンクとして扱わない。表は、見出しの行の直後に区切りの行（`\| --- \|`）があり、見出しと区切りの行の列の数が同じものだけを表として数える（列はエスケープした `\|` で区切らない。新しいブロックの始まりの行（引用 `>`・ATX の見出し・箇条書き・番号付きの箇条書き）は「\|」を含んでも表の行にせず、表をそこで終わらせる（GFM と同じ））（申し送りの解決と要件 ID ↔ テストの対応表に適用する）。コードフェンスに言語の指定がある。1 文書が 2,000 行未満である。フェンスは開始行の文字（バッククォートかチルダ）と長さを覚え、同じ文字が開始以上の長さで並び、その後が空白だけの行でだけ閉じる（長いフェンスの中の短いフェンスで切り替わらない）。開始・終了とも字下げは空白 0〜3 個に限り、4 個以上の字下げの行はコードの本文として扱う（CommonMark）。この判定は ID の走査・節の区切り・コード片の照合と書き直しで共通に使う。リンクとするのは、エスケープされていない「]」に対になるエスケープされていない「[」がある「](」だけ（`\[例](…)` は確かめない。リンクの文は同じ段落の中で行をまたげる）。リンク先のバックスラッシュのエスケープ（`\(`・`\)` など）は括弧の対として数えず、エスケープを戻し、クエリ（`?`）と節（`#`）を除き、最後にパーセント符号化を戻して解決する（`%3F`・`%23` はファイル名の文字）（エスケープした括弧と、しない括弧は同じファイルを指す） |
| 節の構成 | 基本設計・詳細設計（配下のディレクトリの文書を含む）の各節の冒頭に「満たす要件:」がある（節が小節から始まる場合も、親の節は自身の「満たす要件:」を持つ。小節の「満たす要件:」で代えない。小節の「満たす要件:」は MVP の網羅にだけ数える。コードブロックは節の冒頭を終わらせるので、コードブロックの後ろの「満たす要件:」は冒頭の行として数えず、MVP の網羅にも数えない）（「制約:」だけの行は認めない。制約は「／」の後に書く）。節の冒頭の判定では、空行と同じ行で閉じる HTML のコメントだけの行（表示されない）を読み飛ばす。「満たす要件:」の後（「／」の前まで）には、定義済みの要件 ID（FR・NFR・SEC・TST・CON・INV）、要件の群、「SEC 全般」、満たす要件がないことを示す「—」のいずれかがある。各文書に「セキュリティ」節と「テスト観点」節がある（索引の `README.md` は設計の文書ではないので求めない。節の冒頭の「満たす要件:」は求め、詳細設計の索引のコード片も照合する）。詳細設計には「申し送り」の節と「要件 ID ↔ テスト」の節もあり、後者には先頭の列に定義済みの要件 ID を個別に持ち（群の表記・「SEC 全般」だけの行は数えない）、検査・試験の列（先頭以外で、見出しに「試験」「検査」「テスト」を含む列）が空でない対応表の行が 1 行以上ある（「実行する場所」「状態」などほかの列の値だけでは数えない）。節・小節の見出しは、印（`##`・`###`）と見出しの文の間が空白でもタブでもよい（`##\t節` も節として数える） |
| 分冊 | 対応する基本設計がなく、基本設計の名前に `-<名前>` を付けた名前で、本冊（同じ名前の詳細設計）がある文書は分冊とし（配下のディレクトリの文書も、同じディレクトリの基本設計と本冊で同じく判定する。`sub/foo.md` の分冊は `sub/foo-<名前>.md` で、別のディレクトリ `foo-x/` の文書は `foo.md` の分冊としない）、各節の冒頭の「満たす要件:」とコード片だけを照合する（必須の節・申し送り・対応表を求めない）。本冊のない分冊は、対応する基本設計がない文書として失敗する |
| 申し送りの解決 | 対応する基本設計（詳細設計からの相対パスが同じ文書）の「詳細設計への申し送り」の各項目（「- 」の箇条書きと「1. 」の番号付きの行）が、詳細設計の「申し送り」の節の表の先頭の列に、項目名（前後の空白と末尾の「。」を除く）で一度ずつ現れる。数えるのは、見出しに解決の列（2 列目。見出しに「解決」を含む）を持つ表の行だけとする（1 列の表の本文の行に余分な列があっても、2 列目が「担当者」など別の列でも、解決と見なさない）。欠落・重複・基本設計にない行を失敗にする（行数だけを比べると、重複した行が欠落を埋めて合格になるため） |
| コード片 | フェンスの外にある印の直後のコード片が実ファイルと一致する（下）。フェンスの中と字下げしたコードブロックの中の印は説明用の例として、照合にも書き直しにも使わない。印は、同じ箇条書きの項目の中（項目の外なら最上位）にあるフェンスだけに対応させる（次の項目を始める行のフェンスや、印の項目の外のフェンスは対応させず、不整合として報告する）。字下げしたフェンスは、本文の行から開始行の字下げの分を除いて照合し、書き直しでは同じ字下げを付ける。書き直しはコード片の本文だけを置き換え、ほかの行の改行（CRLF など）を変えない。字下げを除いた本文が元のファイルと一致するコード片は、行も改行も変えない。元のファイルは、シンボリックリンクを解決した後も `--root` の中にあるものに限る（外を指す印は不整合として報告し、書き直さない）。元のファイルを読めない（UTF-8 でないなど）場合は、印の行で不整合として報告し、そのコード片を書き直さずに残りの文書の検査を続ける |
| 確認日 | 最も古い確認日から暦の 3 か月後の日（その月に同じ日がなければ月の最後の日）以降、その文書を警告する（失敗にはしない） |
| 読むもの | `--root` の下に `docs/` と定義文書（要件・SEC・TST・COST・`docs/README.md`）がなければ、何も調べずに通らないよう終了コード 2 で失敗する。UTF-8 として読めない文書は、そのパスで報告して読み飛ばし、ほかの文書の検査を続けて終了コード 1 にする |
| 読む文書の経路 | 文書は、読む前に解決の経路を確かめる。シンボリックリンクは 1 段ずつたどり、各段のリンク先とその置き場所が `--root` の中で、Git が追跡しない場所（`.git`・`.tools`・`.build`・`Packages/MinutesKit/.build`。プッシュ前のフックの専用の作業ツリーで消さずに残す場所）の外にあること、置き場所のディレクトリの途中にシンボリックリンクがないこと、最後の実体が通常のファイルであることを求める。経路の要素はファイルシステムがたどる順に 1 つずつ確かめる（「..」を字面で先に取り除かない。リンクのディレクトリの後の「..」は実際とは別の場所を指しうる）。絶対パスのリンク先は起点の実体のパスの中を指すものだけを認める。読み込み・読み直し・`--update-snippets` の書き直しは、確かめた実体のパスだけを使い、元の綴りを開き直さない。同じ実体を指す 2 つの名前（リンクの文書）は 1 回だけ書き直し、両方の読んだ内容を更新する。満たさない文書は読まずに報告する（専用の作業ツリーではこれらの場所の外はすべて送信するコミットの追跡したファイルなので、追跡したものだけをたどったことになる）。経路の途中のリンク先を読めない（`readlink` の失敗）場合も、調べられない場合と同じく、その文書を読まずに報告し、ほかの文書の検査を続ける |
| 行と表の読み方 | フェンスの字下げ（空白 0〜3 個）は、囲む箇条書きの項目の本文の位置から数え、項目が終わればフェンスも終わる（閉じていないものとして失敗する）。箇条書きの印と同じ行のフェンス（「- 」の直後）も、その項目の本文の位置から読む。項目の外の空白 4 個以上は字下げしたコード。リンクの URL の scheme は大文字小文字を区別しない。行は LF・CRLF・CR だけで区切る（U+2028 などは行の本文。照合と書き直しで同じ）。表は見出しと区切りの行で始まり、空行・新しいブロックの始まり・HTML のコメントの始まり・区切りの線まで続く（「\|」を含まない行もデータ行。GFM と同じ）。HTML の `<pre>`〜`</pre>` の行は、コードフェンスと同じ走査で除く（中のフェンスの印・`<!--`・リンク・表・見出しは生の文字列として扱い、`</pre>` を含む行の次から通常の判定に戻る。ブロックの行は表の境界になり、ID の定義を集める表としても数えない。文字は表示されるので、ID の参照は確かめる）。HTML のコメントで始まる行は見出しの候補にしない（始まった表のセルの中のコメントはセルの一部）。複数行の HTML のコメントの中（コードフェンスと同じ走査で状態を追い、コメントの中のフェンスの印をフェンスとして扱わない。フェンスの外の、同じ行で閉じない `<!--` から `-->` まで）は、節・表として数えない。「要件 ID ↔ テスト」の対応表と申し送りの解決の表の判定では、HTML の `<pre>`〜`</pre>` の行も表として数えず、ブロックの始まりと終わりを表の境界にする |

コード片の印:

| 印 | 意味 |
| --- | --- |
| `<!-- file: パス -->` | 直後のコード片が、そのファイルの全文と一致する |
| `<!-- excerpt: パス -->` | 直後のコード片が、そのファイルの連続した一部と一致する |

パスは区切り（`:` と `-->`）の間の全体で、前後の空白を除き、途中の空白を含んでよい。照合と `--update-snippets` は同じ読み取りを使う。パスのない印は不整合として報告する。元のファイルは、文書と同じ判定で、解決の経路が起点の外・追跡しない場所（`.git`・`.tools`・`.build` など）を通るものを使わない（照合でも書き直しでも不整合として報告する。プッシュ前のフックの専用の作業ツリーに残る生成物と照合しないため）。

- 実ファイルを変えたら、`scripts/verify-docs.py --update-snippets` で全文のコード片を書き直す（全文のコード片でほかの詳細設計を引用する文書は、引用される文書を先に書き直し、1 回でそろえる。引用の循環は不整合として報告し、循環に属する文書（引用をたどって自身に戻れる文書のすべて。見つけた順序によらない）は書き直さない。同じディレクトリの一時ファイルに書いて閉じてから置き換える。保存に失敗したら元の文書を残す）。一部のコード片は手で直す。どちらも、食い違いが残れば手順 10 が失敗する。
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
| 1 | `ContentView.swift` の import を並べ替える（作業単位 2 の最初に `.tools/bin/swiftformat .` で整形して解消する。§7） |
| 4 | §12.1 の変更（Bundle Identifier を除く） |
| 7〜9 | §11 の Package とテストターゲット（手順 8 のカバレッジの集計も Package がないと失敗する） |

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
- 録音データそのものは置かない。置くのは様式（上）の項目（日付・ビルドのコミット・端末機種・OS 版・試験 ID・結果・観測値（件数・ハッシュを含む）・不合格の理由）だけで、音声とその書き起こしの本文は置かない（SEC-SUP-05）。`.gitignore` が音声ファイルを除外する。
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
| 独自ルールと入れ子の設定 | `scripts/test-lint-rules.py` | 24 規則・72 件 | `no_text_selection` がコピーの型の場所でも検出すること。`no_direct_random` が、改行が CR だけのファイルで行コメントの後の `using:` を認め、`using:` のない呼び出しを検出すること。 単独のファイルの例外が、接尾辞の付いた名前（`AppLogger.swift.swift` など 6 件）を除外しないこと。 `no_empty_catch` が、改行が CR だけのファイルで本体がコメントだけの `catch` を検出し、コメントの後に実行文がある `catch` を検出しないこと（`violation/CarriageReturn.swift`）。違反例の印の行と検出行が一致する。正常例と例外の場所では検出しない。入れ子の設定は、通常は作業ツリーを走査して Git が無視するものも含めて列挙し（`.git` と、SwiftLint が解析しない excluded の場所は飛ばす。`tools/lint-fixtures` は試験で明示して解析するので走査する。走査の範囲の外を指すディレクトリと Swift のファイルのリンクは、SwiftLint がたどる先の設定を列挙できないので失敗にする）、`--staged`（`check.sh --staged` が渡す）ではインデックスにあるものだけを列挙する。入れ子の設定の試験用のファイル（`_test-code/Sample.swift`）は、通常の設定（`--config` で入れ子の設定を無効にする）では組み込みの 3 規則のすべてに違反することも確かめる（違反のないファイルで「外れる」を確かめても証拠にならない）。入れ子の設定の `disabled_rules` が組み込みの 3 規則だけであること、入れ子の設定の下でも `no_print_logging` が検出されることも確かめる。`custom_rules:` の行と規則名の行は末尾のコメントを許し、規則名として読めない行は失敗にする。SwiftLint が試験用のファイルを読めなかった（標準エラーの「Could not read contents of」。このときも終了コード 0 で検出が 0 件になる）場合と、作業ツリーの走査に失敗した場合と、試験用のファイルの置き場所（`tools/lint-fixtures`）を列挙できない場合は終了コード 2 で止める。作業ツリーの走査では入れ子の設定の名前を大文字小文字を区別せずに探す（既定の APFS では `.SwiftLint.yml` も設定として読まれる）|
| 設定の検査 | `scripts/test-checks.py settings` | 245 件 | 配列の中の配列（何段でも）の中の辞書の禁止の鍵。必須の設定の変数の展開。重複した鍵の plist。必須の設定を実際にビルドする arch ごとに求めること。深い・循環する plist。欠落の判定を実際にビルドする arch に限り、決められない ARCHS では弱めないこと。arch の条件付きの書き方の変数を、合う arch だけで展開すること。形の違う日付の plist を終了コード 2 とすること。`arch=` の条件付きの利用目的・生成の指定・参照先（空を含む）を、その arch にだけ効くものとして判定すること。`--tracked-only` で、名前で見つけた Info.plist・entitlements の実体にも同じ条件を求めること。`--tracked-only` で、参照先の実体が起点の外・追跡していないものを終了コード 2 とし、追跡したファイルを指すリンクは認めること。通常の実行では求めないこと。変数を展開しない・展開するかを決められない構成で、参照先の plist の有効にしない鍵の変数を展開後の値で判定しないこと。参照先の plist のマイクの利用目的の変数を、`INFOPLIST_EXPAND_BUILD_SETTINGS` が NO の構成で認めず（YES なら認める）、変数のない値は NO でも認めること。同じオブジェクトの中の重複した鍵の `project.xcproj` で終了コード 2。向きのビルド設定の変数を構成・SDK ごとに展開して判定する（縦に展開される変数は通り、横に展開される・展開できない変数は違反）。合格する設定で通り、参照先が循環するリンクなら終了コード 2、修飾子つきの向きの鍵（`-iphoneos~iphone`・`-iphoneos`。標準の鍵が縦だけでも）の横向きを違反にすること、相対パス・絶対パスの `--project` で `$(SRCROOT)` から同じ参照先を解決すること、項目ごとの違反で失敗する。必須の設定を SDK ごとに求めること（両方の SDK の条件付きの値で通り、片方だけなら違反）も確かめる。標準の JSON にない定数（NaN・Infinity）は終了コード 2。マイクの利用目的の型・変数の展開、空の条件付きの参照先、生成しない構成の向きの指定も確かめる。`GENERATE_INFOPLIST_FILE` の変数（展開できない場合は生成する・しないの両方を検査する）と、一部の構成だけが参照する plist の変数をその構成・SDK の値だけで展開すること（参照しない構成の値で違反にしない）も確かめる。マイクの利用目的は生成の有無・SDK の条件・参照先の Info.plist の組み合わせと、iPhone の修飾子つきの鍵（`~iphone`・`-iphoneos`・`-iphoneos~iphone`。plist とビルド設定の両方で、最も具体的な鍵の値を判定し、元の鍵だけの plist は認める）で、entitlements は `CODE_SIGN_ENTITLEMENTS` の参照先（拡張子が違うものを含む）で確かめる。読めない設定・参照先がない場合は終了コード 2。画面の向きは Info.plist の生成の有無で判定し、生成する構成で向きを plist だけに置いた場合は通り、どこにもなければ違反、生成しない構成では向きのビルド設定がなくても通ること。参照先の plist の向きの配列の要素の変数を、その構成・SDK のビルド設定で展開してから判定する（縦に展開されれば通り、横に展開される・展開できなければ失敗する）。参照先の plist の変数をビルドが展開しない構成（`INFOPLIST_EXPAND_BUILD_SETTINGS = NO`）では、縦に展開される向きの変数でも認めないこと（`YES` なら認める。設定がなければ Xcode の既定の YES とする）。 |
| 依存の検査 | `scripts/test-checks.py dependencies` | 268 件 | 暦にない承認日（月・日の範囲外、平年の 2 月 29 日）を終了コード 2 とし、うるう年の 2 月 29 日は認めること。URL はパスの末尾のスラッシュ・`.git`・クエリをそのまま照合し、scheme とホストの大文字小文字だけをそろえること。古い形式の名前を取得元で照合すること。形式の版と `pins` の位置。同じ `Package.resolved` の中の名前の重複。ローカルのバイナリの途中と中のリンク。ローカルの Package のディレクトリのリンクが、測定の写しの中を指す相対パスだけであること。コメントの後の tools-version（6.0 以降）と、ツールより新しい `Package.swift` での版別の manifest の選択。三項演算子の中の `url :` を取得元と見なさないこと。版別の manifest の宣言を混ぜず、ツールの版ごとに選ばれる manifest の組でたどること。リンクの後の「..」を字面で取り除いて間接の依存をたどること。間接の依存の確定版が起点にない・版の指定と合わない・許可リストにない場合の失敗と、循環するローカルの Package。閉じていない宣言の引数と、調べられない（権限のない）リンクの先を終了コード 2 とすること。正規表現のリテラルのエスケープした区切り（`\/#`）を終わりと見なさないこと。ローカルの Package の manifest のリンクを、`Packages/` の中を指す相対パス（経由するリンクを含む）だけ認めること。`Packages/` の外（字面・実体）のローカルのバイナリを、承認と内容が一致しても認めないこと。補間の中の宣言のコメントを空白として読むこと。名前が合うディレクトリは終了コード 2。ローカルのバイナリの中のリンクを、リンク先の生の文字列で記録すること（末尾の「/」だけが違うリンクを別の内容のハッシュにする）。重複した鍵の `Package.resolved`・許可リストで終了コード 2。checksum を持つリモートのバイナリの承認で、同じ識別子・版・取得元の Package の確定版と `.package(url:)` を承認しないこと。宣言の最上位の引数の `branch:`・`revision:` だけを版で固定していないものとし、`traits` の中の入れ子の `revision:` では拒否しない。正規化すると同じパスになるローカルのバイナリの承認が 2 つあれば終了コード 2。壊れたシンボリックリンクの `Package.resolved`・`Package.swift` で終了コード 2 になることも確かめる。正規表現のリテラル（`#/…/#`）の中の `"`・`//` を取り違えないこと、区切りのない `/` と深い入れ子の JSON で終了コード 2 になることも確かめる。読めないディレクトリ（走査の失敗）と循環するリンク（成果物の中・ローカルの Package の参照先）で終了コード 2、URL の正規化でユーザー情報の大文字小文字を区別すること、依存のファイルがなければ通ること、前後に空白のあるローカルのバイナリのパスを拒否することも確かめる。除外の階層（下位の `build` は検査し、直下の `build` と全階層の `.build` は除外する）も確かめる。ローカルのバイナリの内容の変更（配下のファイルの増減を含む）・リモートのバイナリの checksum の不一致・CR で終わる行コメント（`strip_comments` に直接渡す）も確かめる。許可リストにない依存・版の不一致・取得元の不一致・版で固定していない依存・取得元を判定できない宣言・対象外のライセンスで失敗する。コメントだけの宣言（行・ブロック・入れ子）は通り、閉じていないブロックコメントは終了コード 2。補間の中の文字列で宣言を隠せない。`.upToNextMinor` の中の `from:` を、名前と「(」の間の空白・コメント・改行によらず `.package` の `from:` と取り違えないこと、未知の呼び出しの中の `from:` と版の指定が 2 つの宣言を判定できないとすること、先行版の `exact`（`1.2.0-rc.1`）が同じ先行版の確定版とだけ一致し、範囲の境界の先行版は判定できないとすることも確かめる。補間の中の正規表現のリテラル（`#/…/#`・`##/…/##`）の「)」で補間が閉じたと見なさないこと、同じ URL のリモートのバイナリの承認の重複と、深い入れ子の補間（RecursionError）で終了コード 2 になること |
| 版の照合 | `scripts/test-checks.py versions` | 351 件 | `--require-clean` とプッシュ前のフックが、専用の作業ツリーの `scripts/check.sh` がリンク・実行権限なしなら起動せずに止まること。相対パスの gitdir の記録をこの実行の登録として外すこと。手順 7 が sparse checkout の外のファイルをインデックスの blob のまま測ること。版の表示の上限（子孫ごと止めること）。sparse checkout での `--require-clean`。`gitdir` を読めない・ない・空の登録。復元の途中で移った場所の案内。登録から印を書くまでの中断と、先にある登録に印を書かないこと。ビルドの成果物の中の `.git` のファイル。列挙できない登録。`gh` での取得の上限。`--staged` の照会の失敗。重複した鍵のシミュレータの一覧。試験の git の環境が利用者の属性を読まないこと。登録だけが残った専用の作業ツリーの置き場所で止まること。引き継いだパス指定の変数で検査する対象を変えないこと。`xcrun` の標準エラーを示すこと。展開の前後に登録された作業ツリーを変えずに止め、移したまま終わった専用の作業ツリーを戻せること。`--steps` をファイル名に展開しないこと。`GIT_DIR`・`GIT_WORK_TREE` を専用の作業ツリーの検査へ渡さないこと。測った後に移した作業ツリーで集計できること。手順 8 の入力・JSON の照合を分けて確かめること。対応していない配布ファイルの形式で導入を始めないこと。プッシュ前のフックが、判定の後に登録された作業ツリー・追跡していない複製を消さずに止め、元の作業ツリーに `scripts/` がなくても検査できること。リンクを経由して下にある作業ツリーの登録。ランタイムの識別子の重複。失敗した測定の後に前のカバレッジの印と JSON が残らないこと。手順の並びごとの照合と検査の呼び出しの順と回数。改行で終わるインデックスの `check.sh --staged` の通しの試験。配布ファイルの中の改行を含むパスと find の失敗。確定版の記録がある場合だけの `--force-resolved-versions` と、`git add -f` したファイルを測ること。親がファイルになった作業ツリーの登録。入れ子の検査に専用の作業ツリーの印が届くこと。導入のロックの解放の途中の中断（`pre-push`・`copy_tools`）。カバレッジのロックの取得の途中の中断と競合。リンクの設定のステージ。専用の作業ツリーの下の別の作業ツリーと、リンクの `.build`。走査の範囲の外を指す Swift のファイルのリンク。改行を含む導入先の SHA-256 の照合。内部の展開が post-checkout のフックを動かさないこと。手順の前に止めた `--staged` が一時のファイルを残さないこと。公開するリンクの先の末尾の改行。`check.sh` が渡す `DEVELOPER_DIR`（値の観測）。走査の範囲の外を指すディレクトリのリンク。`worktree-location.py` の壊れたリンクの設定（1）と調べられない設定（2）。手順 7・8 が置き換えのオブジェクトを使わないこと。`gh` の取得の上限。名前が改行で終わる `GIT_INDEX_FILE`。`setup-tools.sh` のロックの取得の途中の中断と `bin` のリンク。「-」で始まるステージしたファイル。手順 4 の `--tracked-only`。名前が改行で終わる共通のディレクトリの再利用。読めない `excluded` の書き方と行番号のない検出。公開するリンクの置き換えの前後の中断と失敗。末尾に改行のある・空の `core.hooksPath` の上書き。ロックを取った後に上限を過ぎた写しが自分のロックを外すこと。プッシュ前のフックがオブジェクトの置き場所の変数を検査に渡さないこと。ほかの設定への参照の拒否。空のシミュレータの一覧で終了コード 1。導入のロックの取得の途中の中断（`check.sh`・プッシュ前のフック）。部分的なコミットの導入し直す手順。改行で終わる起点・導入先・ディレクトリ。`find-simulator.py` の `DEVELOPER_DIR`。対照の設定が規則の例外だけを除くこと。開発ツールの写しの上限。通常の実行の手順 1〜3 が照合した写しだけを使い、引き継いだ `MINUTES_TOOLS_DIR` を使わないこと。`--require-clean` の入れ子の引数とインデックス。プッシュ前のロックの取得の途中の中断。`worktree-location.py` が git を起動できないとき終了コード 2。設定の読み取りの失敗で `--hooks` が書かずに止まること。起点の末尾の改行を保つこと。外へのリンクの版のディレクトリを認めないこと。版の数字の形。同じ行の `excluded` を拒むこと。強制終了の試験が展開の途中に達したことを印で確かめること。開発ツールの写しが導入のロックに従うこと。退避の削除の失敗で新しい導入を巻き戻さないこと。コミット前のフックが `check.sh` と `worktree-location.py` もステージしたものを使い、部分的なコミットのインデックスと元の `.tools` で検査すること、`--root` の誤用を拒むこと。導入し直す手順が bash・zsh の両方で終了コードを返し一時ファイルを残さないこと。プッシュ前のフックが展開の途中の強制終了の後に検査し直せ（sparse checkout でも全体を展開する）、印のない作業ツリーを変更しないこと、中断のない初回の作成も通ること。書き出した補助のスクリプトと同じ場所のモジュールを読み込まないこと。手順 7 が写しの外を指すリンクで測らないこと。`check.sh --staged` とコミット前のフックが、照合にステージした `setup-tools.sh` を使うこと（作業ツリーだけを成功に戻しても照合の失敗で止まる）。`check.sh --staged` が、検査の間に HEAD が変わったら合格にせず、ステージした内容を書き出す一時ディレクトリが作業ツリーの中（`TMPDIR` が作業ツリーの中）なら書き出す前に止まること。`setup-tools.sh` が公開するリンクを更新できないときに失敗し、既存のリンクと導入を残すこと。`find-simulator.py` が深い入れ子の一覧を取得の失敗（2）とすること。`test-lint-rules.py` が字下げ 4 以外の規則の属性・字下げ 6 以外の `excluded` の項目・属性の階層と違う字下げの `excluded` を報告すること。`check.sh --staged` が、検査の間にステージした内容が変わったら合格にしないこと、末尾に改行を含むリンク先のシンボリックリンクをそのまま書き出すこと（ステージした後に作業ツリーだけを別の内容にしても、ステージした blob を読むこと）。`find-simulator.py` が、使える iOS のランタイムの identifier が null・空・数値の一覧で終了コード 2。`check.sh --require-clean` が、smudge で変わり clean で打ち消された展開を検査せずに失敗すること。`setup.sh --hooks` が、共有の設定が取り込む `includeIf.onbranch` の `core.hooksPath` が別の値なら書かずに止まること。`find-simulator.py` が、候補になりうる使える機種の `deviceTypeIdentifier` がない・空の一覧で終了コード 2。`check.sh --staged` の規則の試験と文書の整合が、smudge のフィルタを通さずにステージした blob を書き出し、実行するスクリプトの実行権限を保つこと、静的解析の失敗で手順 2 に止まり手順 3 へ進まないこと。`setup.sh --hooks` が `GIT_CONFIG` によらずリポジトリの設定に書くこと、フックのファイル（`pre-commit`・`pre-push`）がない・実行できない場合は設定を書かずに失敗すること、手順 8 が測定の記録のないときに専用の作業ツリーでは手順 7 と 8 を一緒に実行する案内を出すこと、`find-simulator.py` が真偽値でない `isAvailable` で終了コード 2・成功では選んだ UDID だけを出力すること、`check.sh` の空の `--steps` と範囲外の番号（bash の整数の範囲を超える桁数を含む。正しい番号と並べても）が使い方の誤りになること（写した `check.sh` で試す）、`setup.sh` を相対パスで実行しても CDPATH によらず起点を解決し、`setup-tools.sh` を絶対パスで呼ぶこと、`check.sh --require-clean` が開始時のコミットを専用の作業ツリーで検査して終了後に片付けること、`check.sh --staged` が種類を変えてステージした Swift のシンボリックリンクを手順の前に止めること、`setup-tools.sh --verify` が導入のロックを待つこと、`find-simulator.py` が形の違う一覧（name・udid・deviceTypeIdentifier・ランタイムの version の型と、ない・空・文字列でない platform を含む）で終了コード 2・27.2.0 を 27.2 以上とすること・空の一覧と iPad だけの一覧と正しい形の watchOS だけの一覧で 1 になること、公開するリンクの位置がディレクトリなら止まることも確かめる。書き込めない導入先・ロックの位置のファイルではロックを待たずに止まることも確かめる。実行ファイル名が名前と違う台帳でも `bin/<名前>` のリンクで照合することも確かめる。版・SHA-256・形式・行の不足（必須のツールの行の欠落を含む）・`gh` の行の順序（タブ区切りの台帳を含む）・版の表示の失敗・導入記録のない場所を指すリンク・版のディレクトリの中の実行ファイルがシンボリックリンク（通常のファイルでない）で失敗する。`setup.sh --hooks` が、git の選ぶ作業ツリーが別のリポジトリのとき・より優先される `core.hooksPath`（`-c`・`GIT_CONFIG_*`・作業ツリーの設定）が別の値を指すときに共有の設定を変えずに失敗し、リンクした作業ツリーからは通ること。入れ子の `check.sh` の試験は外側の `MINUTES_CHECK_ISOLATED` を引き継がないこと。最後の行だけが不正な台帳で導入を実行しても、前の行のツールを取得・展開せず導入先に何も残さないこと（台帳の全体を検証してから導入する）。`setup.sh --hooks` が、引き継いだ `GIT_DIR`・`GIT_WORK_TREE` が別のリポジトリを指していても外して、自身のリポジトリにだけ書くこと。`check.sh --require-clean` が、一時の置き場所（`TMPDIR`）が SwiftFormat の設定の下・作業ツリーの中なら、専用の作業ツリーを作らずに失敗すること。版を表示するコマンドが、正しい台帳で `setup-tools.sh` の標準入力に渡した既知の行を読めないこと（重複した行の拒否は別の試験）。`check.sh --staged` が、外から本来のインデックスを変えた場合も合格にしないこと。`check.sh --require-clean` が、引き継いだ `MINUTES_CHECK_ISOLATED=1` があっても専用の作業ツリーに展開すること。`find-simulator.py` が UUID でない UDID を取得の失敗とすること。 |
| 秘密情報の検出 | `scripts/test-checks.py gitleaks` | 75 件 | コミットのメッセージの値をどの形式の報告にも残さないこと。ない `diff.orderFile` で読める履歴を検査すること。全履歴の走査が 1 回だけであること。差分の接頭辞を変える設定でもファイルの名前を正しく報告すること。`diff.relative` のサブディレクトリの外の鍵を検出すること。設定 `format.pretty`・`log.abbrevCommit` によらず完全なコミットの ID で報告すること。ない注記の参照の警告を走査の失敗と取り違えず、注記のコミットの鍵を検出すること。名前の変更の検出の上限の警告を走査の失敗と取り違えないこと。`log.showSignature` の署名の検証の出力を走査の失敗と取り違えないこと。名前が改行で終わるリポジトリを、改行のない名前のものと取り違えないこと。gitleaks 自身の `git log` だけが失敗・中断・標準エラーに出力した場合に終了コード 1。手順 0 が手順 1〜3 より後にあるときは先に照合すること。手順 0 なしで手順 3 を実行するとき、先に開発ツールを照合し、照合に失敗したら手順 3 を実行しないこと。`gitleaks-history.sh` が、解決できないリポジトリの引数で終了コード 1、相対パスの引数を実体のパスとして検査すること（「-」という名前のディレクトリも、`cd` が直前の場所と解釈しないよう `./` を付けて解決する）。欠けたオブジェクトで全履歴を読めないリポジトリは終了コード 1。`gitleaks-history.sh` の追加の引数は報告の指定（`--report-format`・`--report-path`）だけを通し、ほかの指定（`--exit-code`・`--no-redact`・`--log-opts`・`--baseline-path`）・値のない指定・重複した指定は終了コード 2。実行時に組み立てた偽の鍵を検出する。浅いクローンは検査せずに失敗する（一時のリポジトリは外側の `GIT_DIR` などを外した環境で検査する）。`.gitattributes` の textconv で差分から鍵を消しても検出する。`.gitattributes` で `binary` にしたテキストのファイルの鍵も検出する。作業ツリーにだけある `.gitleaksignore`（検出の指紋）と行の `gitleaks:allow` のコメントで、全履歴の検査とコミット前の検査（`check.sh --staged`）の検出が隠れないこと（対照として、指定のない gitleaks では抑止されること）、引き継いだ `GIT_DIR` が別のリポジトリを指していても引数のリポジトリを検査することも確かめる。`log.showRoot=false` でも最初のコミットの鍵を検出する。通常の色の設定と `color.diff=always`・`color.ui=always` のどれでも全履歴の鍵を検出する。手順 3 と同じ `gitleaks-history.sh` で、通常のコミットとマージのコミットだけで足した行の両方を検出する。置き換えのオブジェクト（`git replace`）で隠した鍵も全履歴の検査で検出すること。grafts（`info/grafts`）があれば、有効な行があってもコメントと空行だけでも検査せずに失敗すること。コミット前の検査が `.gitattributes` の `binary`・textconv によらずステージした内容の鍵を検出すること（対照として `gitleaks git --staged` は見落とすこと）、`.gitleaksignore` とコメントの場合ごとの診断を確かめる |
| カバレッジの集計 | `scripts/test-checks.py coverage` | 41 件 | 同じファイルの記録の重複。記録した起点を解決し直さないこと。起点が「/」でも配下を数え、名前の似た隣のディレクトリを数えないこと。重複した鍵の記録で終了コード 2。浮動小数点では目標ちょうどに丸まる不足を警告し、目標ちょうどは警告しないこと。桁の多すぎる目標は終了コード 2。大きな行数（10 の 400 乗）でも桁あふれせずに集計すること。目標以上・未満（警告）の集計と、不正な行数の記録（整数でない・真偽値・範囲外）で終了コード 2。目標（`--target`）は 0〜100 の有限の数だけを認め、NaN・無限大・範囲外・数でない値で終了コード 2。目標にわずかに届かない値（89.96%）を丸めて目標以上に見せず、小数の目標を丸めずに表示し、深い入れ子の記録で終了コード 2数える対象かを記録したパスで決めること（`Sources` の中のリンクは数え、外のリンクは数えない）、相対パスの記録で終了コード 2。 |
| 文書の検査 | `scripts/test-checks.py docs` | 442 件 | 引用の中のコード片を同じ深さの引用のフェンスとだけ対応させ、引用の印を保って書き直すこと。未定義のラベルに続く参照と、参照の直後の短縮形の参照のリンク先を確かめること。箇条書きの印と同じ行のフェンス。印と別の箇条書きの項目のフェンスを対応させないこと。箇条書きの中のフェンスとコード片。大文字の scheme。次の行のリンク先。SEC の MASVS 分類の列と値。次の行のタイトルのあるリンクと、その診断の行。処理済みの文書を経由する循環。文書を引用する文書の書き直しの順序と、循環の報告。リンク先とタイトルの中の参照の形を参照と見なさないこと。クエリと符号化した `%3F`。リンク先の文字参照。見出しのコメントの中の語を必須の節と見なさないこと。絶対パスの元のファイルの「..」を字面で取り除かないこと。リンク先の後に完全なタイトル以外の文字が続く行を定義にしないこと。字下げしたコードの中の印を照合・書き直しの対象にせず、箇条書きの中の印は読むこと。次の行にリンク先を書いた定義のリンク、引用の中のフェンスの ID を参照に数えないこと。段落の続きの定義の形の行、引用の中のフェンスの本文のリンク、通常のファイルの「..」を経由する文書、追跡しない場所のコード片の元のファイル。字下げしたコードブロックのリンク（段落の続き・箇条書きの中は確かめる）、引用の中のフェンスの言語の指定。`<pre>` の中のフェンスの印・コメントの始まり、コメントで始まる行の後ろのリンク、入れ子のリンクの外側を確かめないこと（画像を含むリンクは確かめる）。`<pre>` の中の定義を数えないこと、一致するコード片の改行を変えないこと、確認日の暦の 3 か月の境界。短縮形の参照のリンク、コメントの行を表の見出しにしないこと、リンク先を読めない文書より後の文書も読むこと。対になるエスケープされていない「[」のない「](」をリンクにしないこと（ふつうのリンク・画像を含むリンク・行をまたぐ文は確かめる）。空白を含むパスの印を照合・書き直しの両方で読み、パスのない印を報告すること。エスケープした括弧（`\(`・`\)`）を含むリンク先を、エスケープを戻して解決すること。印と見出しの文の間がタブの節の見出しも節として数えること。文書の解決の経路のリンク先を読めない（`os.readlink` の失敗）とき、その文書のパスで報告してほかの文書を続けること。同じ行で閉じる HTML のコメントだけの行の後の「満たす要件:」を節の冒頭とし、網羅に数えること（コメントと本文の後のものは冒頭としない）。`--update-snippets` が、詳細設計のディレクトリの外を実体とするリンクの文書を書き直さない。同じ行の HTML のコメントの中の ID を参照として検査せず、インラインのコードの中の ID は検査する。「満たす要件」の同じ行の HTML のコメントの中の ID を網羅と節の冒頭の要件に数えず、コメントの後の見える ID と「—」は認めること、設計決定（BD）を各文書の §1（`architecture.md` は §3）の表からだけ定義として集めることも確かめる。分冊（必須の節を求めず、節の冒頭とコード片を照合する。本冊のない分冊は失敗する。配下のディレクトリの分冊も同じ）も確かめる。表の後の引用の行を表の行にしないこと、2 列目の見出しが「解決」でない申し送りの表を解決と見なさないこと、コード片の元のファイルを読めない（UTF-8 でない）場合に印の行で報告して検査を続け、`--update-snippets` でそのコード片を書き直さないことも確かめる。循環するリンクの後に検査する別の文書の不一致も報告されること（検査が続くこと）を確かめる。行頭の `\|` を省いた表の行、字下げしたコードブロックの表の形の行を表にしないこと、インラインのコードの中のリンクを確かめないこと、小節だけが「満たす要件:」を持つ親の節を不合格にすること、コード片の元のファイルが循環するリンクでも止まらずに報告することも確かめる。列の区切りの前後に空白のない表の定義（要件・設計決定・重複）と、番号付き（`1. `）の申し送りの項目の照合（解決の行の欠落・解決の表がない場合）も確かめる。節の後半の「満たす要件:」を網羅に数えないこと（小節の冒頭は数える）・フェンスをまたいだ行を 1 つの表にしないこと・`--update-snippets` が末尾の改行だけでは書き直さず、`--root` の外の文書に書き込まず、書き直しを保存できない（一時ファイルを作れない）ときに元の文書を残すことも確かめる。対応表の行は、検査・試験の列が空なら「状態」などほかの列に値があっても数えないことも確かめる。山括弧のリンク先・区切りの行のない表・群の表記だけの対応表の行・字下げしたフェンスのコード片・`--root` の外のコード片の元のファイルも確かめる。未定義の ID・切れたリンク・言語のないコードフェンス・閉じていないフェンス・食い違うコード片・ID のない「満たす要件:」・申し送りの欠落と重複と余分な行・入れ子の設計書の不備などで失敗する。長いフェンス・チルダのフェンスで誤って切り替わらない。フェンスの中の印は照合にも書き直しにも使わない。U+2028 を行の区切りにしないこと（照合と書き直し）、コメントの中のフェンスの印をフェンスとしないこと、コメントの中の見出しと「満たす要件:」を MVP の網羅に数えないこと、`--update-snippets` で文書を読み直せない（OSError）ときにその文書のパスで報告して書き直さないこと、読めない文書をパスで報告して検査を続けること、括弧を含むリンク先、「\|」を含まない表のデータ行、複数行の HTML のコメントの中の対応表・定義を数えないことも確かめる。「／」の後の関連の参照だけでは網羅にならない。存在しない root・定義文書の欠落は終了コード 2。字下げ（空白 0〜3 個）した見出しを節として扱い 4 個以上は扱わないこと、同じ行で閉じるコメントの中のリンクを確かめないこと、「(」の直後の空白を読み飛ばしてリンク先を確かめること、読めないディレクトリを報告して失敗すること、先の文書の読み直しの失敗の後も後の文書を書き直すこと。起点の外・追跡しない場所（`.tools`）を指すリンクの文書を読まずに報告すること。対応表が HTML の `<pre>` の中だけなら対応表の行がないとし、`<pre>` の後の表は数えること。リンクのディレクトリの後の「..」を字面で取り除かずに起点の外の経路として報告すること、同じ実体を指す 2 つの名前を 1 回だけ書き直すこと、申し送りの解決の表が `<pre>` の中だけなら解決がないとすること。 |

自己試験の入力は、試験スクリプトの中で組み立てて一時ディレクトリに置く。リポジトリに違反例の設定ファイルを置くと、本物の検査がそれを避けるための除外が要り、抜け道になるためである。

### 17.2 自己試験が効いていることの確認

検査の側をわざと壊し（突然変異）、自己試験が失敗することを確かめる。検査を足す・変えるたびに行い、壊したもの・件数・結果と、この確認で見つけて直した不具合を [../04-test-records/check-self-tests.md](../04-test-records/check-self-tests.md) に記録する（2026-11-16 からの全件。行は消さない）。

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
| TST-CI-07 | `check.sh` 手順 8、`coverage-report.py`、`test-checks.py coverage` | プッシュ前 | Package の集計は確認済み。アプリ側は未実装 |
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
- [GitHub: cli/cli v2.104.0](https://github.com/cli/cli/releases/tag/v2.104.0)（公開 2026-11-12。GitHub の API（`https://api.github.com/repos/cli/cli/releases/tags/v2.104.0`）の配布ファイルの `digest` で、`gh_2.104.0_macOS_arm64.zip` と `gh_2.104.0_macOS_amd64.zip` の SHA-256 が `tools/versions` の値と一致することを確認した。確認日 2026-11-19）
- [GitHub: gitleaks/gitleaks releases](https://github.com/gitleaks/gitleaks/releases)（v8.30.1。`gitleaks_8.30.1_checksums.txt`、タグ v8.30.1 の `LICENSE`）
- 各ツールのヘルプ（`swiftlint lint --help`・`swiftformat --help`・`gitleaks git --help`。導入した版で確認）
- Xcode 27.2 の定義ファイル（`Swift.xcspec`: `SWIFT_VERSION`・`SWIFT_TREAT_WARNINGS_AS_ERRORS` ほか。`INFOPLIST_KEY_…` の一覧）、`xcrun simctl list`
- iOS 27.2 SDK の `Speech.framework`（`SFSpeechRecognizer.h`、Swift インターフェース）
- [Apple Developer: Describing use of required reason API](https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api)、[NSPrivacyAccessedAPIType](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacyaccessedapitypes/nsprivacyaccessedapitype)（理由コードの一覧）
- [Apple Developer: Asking permission to use speech recognition](https://developer.apple.com/documentation/speech/asking-permission-to-use-speech-recognition)、[SpeechAnalyzer](https://developer.apple.com/documentation/speech/speechanalyzer)
