# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクトの現状

iPhone 専用・端末内完結の議事録アプリ（録音 → 端末内文字起こし → Phase 2 で端末内 LLM 要約）。PC/Web 版 Minutes（別リポジトリ `Minutes` の `docs/design-local-phase*.md`、読み取り専用）の原則を iOS へ移植する。

**アプリの実装は未着手**。`MinutesMobile/` は Xcode テンプレートの Hello World のまま。検査の仕組み（`scripts/`・静的解析の規則・フックのファイル）だけを先に入れてある。リポジトリの正は `docs/` の要件定義書であり、コードを書く前に必ず該当する要件 ID を確認する。

- 文書索引・規約・オーナー決定事項（UD）: `docs/README.md`
- 要件定義（FR / NFR / CON / INV / OQ）: `docs/01-requirements/requirements.md`
- セキュリティ要件（SEC）と静的解析の禁止事項: `docs/01-requirements/security-requirements.md`
- テスト要件（TST）と CI ゲート: `docs/01-requirements/test-requirements.md`
- 依存・費用台帳（COST）と依存追加手順: `docs/01-requirements/dependencies-and-costs.md`
- 基本設計（MVP の 7 文書。設計決定は `BD-<領域>-nn`）: `docs/02-basic-design/`。入口は `README.md`（索引・OQ の解決状況）と `architecture.md`（全体構成・セキュリティとテストの注意点の総覧）
- 詳細設計: `docs/03-detailed-design/`。1 文書目の `dev-environment.md`（検査スクリプト・静的解析の規則・プロジェクト設定の変更手順）だけ作成済み。残り 6 文書は未着手で、作る順は README にある
- 実機試験の記録: `docs/04-test-records/`（様式のみ）

## ビルド・テスト

Xcode プロジェクトは JSON 形式の `MinutesMobile.xcodeproj/project.xcproj` を使う（`project.pbxproj` は存在しない）。ビルド設定はこの JSON を直接読む。ただし末尾カンマを含むため、標準の JSON パーサー（`json.load`・`jq`・`JSONSerialization` など）では解析できない。スクリプトで読む場合は JSON5 対応のパーサーを使う。

`xcode-select` が Command Line Tools を指しているため、`xcodebuild` には `DEVELOPER_DIR` を付ける（または `sudo xcode-select -s /Applications/Xcode.app` を一度実行する）。

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer

# スキーム・ターゲット一覧
xcodebuild -list -project MinutesMobile.xcodeproj

# ビルド（シミュレータ。機種を特定しない）
xcodebuild -project MinutesMobile.xcodeproj -scheme MinutesMobile \
  -destination 'generic/platform=iOS Simulator' build

# テスト全件（テストターゲット追加後）。シミュレータは最低 OS（iOS 27.2）以上の iPhone を選ぶ
SIM="platform=iOS Simulator,id=$(scripts/find-simulator.py)"
xcodebuild -project MinutesMobile.xcodeproj -scheme MinutesMobile -destination "$SIM" test

# 単一テスト（Swift Testing: -only-testing:<TestTarget>/<Suite>/<test>）
xcodebuild -project MinutesMobile.xcodeproj -scheme MinutesMobile -destination "$SIM" \
  test -only-testing:MinutesMobileTests/RecordingStateMachineTests/startFromIdle
```

シミュレータの機種名を決め打ちにしない（`name=iPhone 17` など）。導入済みのランタイムは Mac ごとに違い、最低 OS より古いランタイムの機種を指定すると「destination が見つからない」で失敗する。

テストフレームワークは Swift Testing（単体・結合）、XCUITest（UI）、XCTest 計測 API（性能）。

## 検査（`scripts/`）

詳細は `docs/03-detailed-design/dev-environment.md`。SwiftLint 0.65.1・SwiftFormat 0.64.7・gitleaks 8.30.1、およびそれらの取得に使う gh 2.104.0 は `tools/versions` の版を `.tools/`（Git の管理外）に入れて使う。Homebrew や PATH の版は使わない。

```bash
scripts/setup-tools.sh            # 開発ツールを導入する（SHA-256 が一致した配布ファイルだけを展開する）
scripts/check.sh                  # 手順 0〜10 を順に実行する（1 つでも失敗したら止まる）
scripts/check.sh --steps 2        # 指定した手順だけ（例: 静的解析と規則の試験）
scripts/check.sh --staged         # コミット前の範囲（ステージしたファイルの整形・静的解析・秘密情報と、文書の整合）
scripts/test-lint-rules.py        # 静的解析の規則の試験（違反例・正常例）
scripts/test-checks.py            # 検査スクリプトの自己試験
scripts/verify-docs.py            # 文書の整合（--update-snippets で詳細設計のコード片を実ファイルから書き直す）
```

- **現在、手順 1・4・7〜9 は失敗する**（テンプレートの import の並び順、プロジェクト設定が未変更、Package とテストターゲットがない）。次の作業単位（プロジェクト設定の変更。詳細設計 §12）で解消する。検査を弱めて通さない。
- Git のフック（`.githooks/`）はまだ有効にしていない。上の失敗が解消したら `scripts/setup.sh` で有効にする。
- 規則を足す・変えるときは、`tools/lint-fixtures/<規則名>/` の違反例（`// expect: <規則名>` の印）と正常例も同じ作業で直す。検査を足したら、自己試験に加えて、検査の側をわざと壊して自己試験が失敗することも確かめる。
- スクリプトは macOS 同梱の bash 3.2 と Xcode 同梱の python3（標準ライブラリだけ）で動かす。bash の注意点（全角文字の直前の変数は `${name}`、手順の関数を条件の中で呼ばない、EXIT の trap で明示的に `exit` する）は詳細設計 §2.3 にある。

## 実装着手時に必ず行うプロジェクト設定変更（CON-03 / 04 / 09）

- `TARGETED_DEVICE_FAMILY` を `1,2,7` → `1`（iPhone のみ）に変更。`SUPPORTED_PLATFORMS` も iOS のみに絞る
- `SWIFT_VERSION` を `5.0` → Swift 6 言語モードに変更し、厳格な並行性チェックで警告ゼロ（警告はエラー扱い）
- **最初の実録音の前に `PRODUCT_BUNDLE_IDENTIFIER` を確定する。** 現在は `devplaceholder.$(PROJECT_UNIQUE_VALUE…)` の仮値で、変更するとアプリのデータ領域が別物になり既存録音を読めなくなる。確定後は変更しない
- 配布は無料の Personal Team のみ（UD-02）。Personal Team で使えない capability に依存しない

## アーキテクチャの原則（設計・実装・テストの全てで守る）

**Recording is Source of Truth。** 成果物の依存順序は一方向で、下流が失敗しても上流から再生成できる:

```text
録音 Chunk（WAV 原音）→ 文字起こしセグメント（Chunk 単位）→ 確定文字起こし（版番号付き）
  → AI 議事録（P2、版管理・上書きしない）
```

**手動ノート（P2）は上の連鎖に含めない独立した原本**。利用者所有で AI・バックグラウンド処理は書き込まず、録音からは再生成できない。会議ディレクトリにノート単独のファイルとして保存し、復元・Markdown 書き出しができるようにする。文字起こし・AI 議事録の再生成でノートのファイルを変更・削除しない（FR-NOT-01・02・05）。

- **Zero External Data Egress**: 音声・文字起こし・要約・ノートはどの段階でも端末外へ出ない。アプリコードはネットワーク API を使わない（例外は SEC-NET-03 / 04 のみで、通信コードは 1 つの型に集約する）
- **Recording-First**: 録音の継続を他の全機能より優先する。文字起こし・UI・保存の不調で録音を止めない
- **At-Least-Once + 冪等**: 処理の再実行を前提とし、二重実行しても成果物が重複しない
- **Device-Aware Degradation**: Apple Intelligence 対応・言語アセット・温度・空き容量に応じて縮退するが、録音は止めない。例外は物理的に続けられない場合だけ（使える入力がない、書き込み先がなく未保存 Chunk をメモリにも保持できない、など。NFR-REL-05）で、そのときは保存済みの音声を確定可能な状態で守ってから停止し、停止理由を利用者に通知する
- **断定しない**: 精度・速度・電池消費は保証値にせず実測して記録する
- 不変条件 INV-01〜10（`requirements.md` §7）を破る設計・実装は行わない。特に INV-08: 時刻はサンプル数で数え、タイマーに依存しない

### 層の分割とテスト容易性（NFR-MNT-02、TST-GEN 群）

- 録音・保存・文字起こし・要約の各層は protocol で境界を切り、テストで差し替える
- 時刻（壁時計・単調時計）・タイマー・ファイルルートディレクトリ・UUID・乱数・オーディオセッション通知・STT エンジン・LLM・空き容量・温度状態・低電力モードはすべて注入する。実時間を待つテスト（`sleep` / `Task.sleep`）は書かない
- 状態遷移は純粋関数（現在の状態 + 事象 → 次の状態 + 副作用の指示）として書き、全遷移を表形式テストで網羅する
- 非同期の完了通知は要求 ID / 世代番号で対応付け、古い応答を別の要求の完了として扱わない
- テスト名またはタグに対応する要件 ID を含める（TST-GEN-03）。テストごとに一意の一時ディレクトリを使う（Swift Testing は並列実行）
- 試験用音声は合成する。実際の会議録音をリポジトリに入れない

### 静的解析で禁止するもの（SEC §10.1。`.swiftlint.yml` の独自ルールで検出する。全件は詳細設計 dev-environment.md §6）

- `print(` / `NSLog(` / `debugPrint(` → ログは `os.Logger` に一本化（privacy 指定必須）
- 通信集約型の外での `URLSession` / `NWConnection` / `URLRequest` / `import Network`
- `try!` / `as!` / 強制アンラップ（テストコード以外）、空の `catch {}`
- `UserDefaults` への文字列保存（許可した鍵以外）
- `FileManager` の `.documentDirectory` → 保存先は Application Support に限る
- `NSAllowsArbitraryLoads`（ATS 例外）

## 依存の追加

OS 標準フレームワーク優先。サードパーティーは無償・MIT / Apache-2.0 / BSD 系・外部通信なし・最新安定版に限る。追加前に `dependencies-and-costs.md` §4 の 7 手順を踏み、台帳 §3 に 1 行追記して PR 説明にも書く。**費用が発生しうるもの・GPL 系・独自ライセンスはオーナー確認が記録されるまで採用しない。** 解析・広告・クラッシュ報告 SDK は組み込まない（SEC-NET-06）。

## 文書と実装の同期ルール

- 要件 ID は再利用しない。廃止は行を残して「廃止（理由・日付）」と書く
- FR / NFR / CON / INV には「出典」、FR / NFR / SEC には「フェーズ」（MVP / P2 / P3）を付ける
- 基本設計・詳細設計の各節冒頭に満たす要件 ID を列挙し、各文書に「セキュリティ」節と「テスト観点」節を置く
- 詳細設計書のコード片は実装ファイルと一致させる。どちらかを変えたら同じ作業でもう一方も直す（NFR-MNT-04）。コード片の直前の `<!-- file: パス -->`（全文）・`<!-- excerpt: パス -->`（一部）の印を `scripts/verify-docs.py` が照合する
- 基本設計・詳細設計の各節（`##`）の冒頭に「満たす要件:」の行を置く（満たす要件がない節は「満たす要件: —」）。文書を変えたら `scripts/verify-docs.py` を実行する
- 要件を変えたら、影響する設計書・テスト要件も同じ作業で更新する
- 技術の版数・提供状況は記憶で書かず、確認日と出典 URL を添える。確認日から 3 か月以上経った記述は再確認する
- 実装順序は上流から: 録音 → 保存 → 確定 → 復旧 → 文字起こし → 表示 → 書き出し。前の単位のテストが通ってから次に着手する
- 文書はファイル名は英小文字ケバブケース、本文は日本語、図は Mermaid、コードフェンスは言語指定必須

## Git

- `dev` ブランチで編集し、`main` へは PR
- コミット形式: `<type>(<scope>): <subject>`。文書は `docs(<scope>): <subject>`

## シミュレータの限界

マイク入力経路・割り込み（着信 / Siri / Bluetooth）・ファイル保護クラス・バックグラウンド継続・言語アセットと Apple Intelligence の有無はシミュレータで再現できない。単体テストは依存を差し替えて行い、これらは実機試験（TST-DEV 群）で確かめる。
