# MinutesMobile 基本設計書 ── 開発環境と検査

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版） |
| 作成日 | 2026-11-15（技術情報の確認日 2026-11-14） |
| 満たす要件 ID | NFR-MNT-01・03、SEC-NET-05、SEC-DAT-04、SEC-LOG-01、SEC-PRV-01・03、SEC-SUP-01〜05、TST-CI-01〜08 |
| 関係する制約 | CON-01〜04・07〜09 |
| 解決する未解決事項 | — |
| 参照元 | Minutes `CLAUDE.md`、Minutes `scripts/verify-design-docs.sh` |
| 関連文書 | [architecture.md](architecture.md)・[../01-requirements/dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md)・[../01-requirements/test-requirements.md](../01-requirements/test-requirements.md) §4 |

本書は、コードを書き始める前に整える環境・設定・検査の仕組みを定める。**ここに書いた作業は、機能の実装より先に行う**（シフトレフト。UD-06）。

---

## 1. 設計決定

満たす要件: SEC-SUP-01、TST-CI-02・03 ／ 制約: CON-07・09

| ID | 決定 | 理由 | 退けた代替案 |
| --- | --- | --- | --- |
| BD-DEV-01 | 自動検査はローカルの Mac で実行する（検査スクリプトと Git のフック）。クラウドの CI は使わない | GitHub Actions の macOS ランナーと Xcode Cloud は費用が発生しうる（COST-02・03）。オーナーの承認があるまで採用しない | クラウドの CI を先に組む |
| BD-DEV-02 | プロジェクト設定の変更・検査スクリプト・静的解析の規則・`.gitignore` を、最初の作業単位として機能の実装より前に入れる | 規則がない状態で書いたコードは、後から規則に合わせ直す手間が大きい | 機能ができてから検査を足す |
| BD-DEV-03 | 開発ツール（SwiftLint・SwiftFormat・gitleaks）は Homebrew で導入し、版を台帳に記録する。検査スクリプトは実行時の版を台帳と照合し、違えば失敗する | SPM の外のツールは版を固定しにくい。照合で食い違いを検出する（SEC-SUP-01） | 版を確かめずに使う |
| BD-DEV-04 | 中核ロジックの Package は Mac 上の `swift test` で回し、同じテストをシミュレータでも実行する | 速さ（数秒）と、実行環境の違いの検出を両立する（[architecture.md](architecture.md) BD-ARC-02） | シミュレータだけで実行する |
| BD-DEV-05 | 静的解析の独自ルールには、違反例のファイルを使った試験を付ける | 規則が実際に検出することを確かめないと、効いていない規則に気づけない | 規則を書くだけ |
| BD-DEV-06 | Bundle Identifier は、最初の実録音の前にオーナーが決める。決まるまで実機で実際の会議を録音しない | 変えるとアプリのデータ領域が別物になり、既存の録音を読めなくなる（CON-09） | 仮の値のまま使い始める |

---

## 2. 環境

満たす要件: NFR-MNT-01 ／ 制約: CON-01・04

確認日 2026-11-14。更新したら本表と台帳を同じ作業で直す。

| 項目 | 版 | 確認方法 |
| --- | --- | --- |
| Xcode | 27.2（27B52） | `xcodebuild -version` |
| iOS SDK | 27.2 | `xcodebuild -showsdks` |
| Swift | 6.4.2 | `swift --version` |
| 最低 OS | iOS 27.2（CON-01） | プロジェクト設定 |
| OS 同梱の SQLite | 3.54.0 | SDK の `sqlite3.h` |
| プロジェクトの形式 | JSON 形式の `MinutesMobile.xcodeproj/project.xcproj`（`project.pbxproj` はない） | リポジトリ |

`xcode-select` が Command Line Tools を指している場合は、`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` を付けて `xcodebuild` を実行する。検査スクリプトはこれを自分で設定する。

---

## 3. プロジェクト設定の変更

満たす要件: NFR-MNT-01、SEC-NET-05、SEC-DAT-04、SEC-PRV-01 ／ 制約: CON-03・04・09

### 3.1 ビルド設定

| 項目 | 現在 | 変更後 | 根拠 |
| --- | --- | --- | --- |
| 対象デバイス（`TARGETED_DEVICE_FAMILY`） | `1,2,7`（iPhone・iPad・visionOS） | `1`（iPhone のみ） | CON-03 |
| 対応プラットフォーム（`SUPPORTED_PLATFORMS`） | iOS・macOS・visionOS | iOS（実機とシミュレータ）のみ | CON-03 |
| Swift の言語モード（`SWIFT_VERSION`） | 5.0 | 6 | CON-04 |
| 警告の扱い | 警告のまま | エラーとして扱う | TST-CI-01 |
| 既定の actor 分離 | MainActor（アプリターゲット） | そのまま。ロジックは Package に置く | [architecture.md](architecture.md) BD-ARC-02 |
| 画面の向き | 縦・横（iPhone）、全方向（iPad） | 縦のみ。iPad の設定は削除 | [ui.md](ui.md) BD-UI-10 |
| Bundle Identifier | テンプレートの仮の値 | オーナーが決めた値 | CON-09、BD-DEV-06 |
| 署名 | 自動 | 自動（Personal Team） | CON-02 |

設定の名前と値は、変更時に Xcode 27.2 の画面で確かめる。

### 3.2 Info.plist と capability

| 項目 | 設定 | 根拠 |
| --- | --- | --- |
| マイクの利用目的（`NSMicrophoneUsageDescription`） | 「会議を録音し、この iPhone の中だけで文字起こしするためにマイクを使用します。音声は端末外へ送信されません。」 | SEC-PRV-01 |
| 音声認識の利用目的 | `SpeechAnalyzer` の利用に必要かを詳細設計で確認し、必要な場合だけ追加する | SEC-PRV-01 |
| Background Modes | `audio`（録音の継続）。継続処理タスクに必要なら `processing` | FR-REC-05、[transcription.md](transcription.md) §9 |
| 継続処理タスクの識別子 | `{Bundle Identifier}.transcription.*` を許可する識別子として宣言する | [transcription.md](transcription.md) §9 |
| Live Activity の対応 | ウィジェット拡張を足す時点で有効にする | [ui.md](ui.md) §8 |
| Data protection の capability | 使わない（保護クラスはファイルごとに明示する） | [storage.md](storage.md) §7 |

**設定しないもの（検査で確かめる）:**

| 項目 | 理由 |
| --- | --- |
| ATS の例外（`NSAllowsArbitraryLoads` など） | SEC-NET-05 |
| ファイル共有（`UIFileSharingEnabled`・`LSSupportsOpeningDocumentsInPlace`） | SEC-DAT-04 |
| URL スキーム・ユニバーサルリンク・共有拡張 | SEC-PLT-04 |
| Push 通知・Siri・iCloud の capability | Personal Team で使えない、または使わない |
| 使わない権限の文言（カメラ・位置情報など） | TST-SEC-03 |

---

## 4. ターゲットとテストの構成

満たす要件: TST-CI-01・02・07・08

| ターゲット | 内容 | フレームワーク | 実行 |
| --- | --- | --- | --- |
| `MinutesKit`（ローカル Package） | 中核ロジックとそのテスト | Swift Testing | Mac 上の `swift test`、およびシミュレータ |
| `MinutesMobile` | アプリ | — | — |
| `MinutesMobileTests` | アプリ側の単体・結合（Store、iOS 固有のアダプタ） | Swift Testing | シミュレータ |
| `MinutesMobileUITests` | UI テスト | XCUITest | シミュレータ |
| `MinutesMobileWidgets` | Live Activity（後から追加） | — | 実機 |

- テストは並列・順不同で実行する設定にし、順序や共有の状態に依存したテストを早く見つける。
- UI テストは、録音・音声認識・空き容量・保存ルートを差し替えた構成でアプリを起動する（起動時の引数で切り替える。本番のビルドでは差し替えを無効にする）。
- カバレッジは Package と アプリの両方で取り、中核ロジック 90%・全体 70% を初期の目標にする（TST-CI-07。数値は実績を見て見直す）。

---

## 5. 依存と版の固定

満たす要件: NFR-MNT-03、SEC-SUP-01・02 ／ 制約: CON-07

### 5.1 アプリに組み込む依存

**MVP では 0 件**（OS 標準のフレームワークだけ。[architecture.md](architecture.md) BD-ARC-07）。`Package.resolved` に外部のパッケージが現れたら検査が失敗する。

### 5.2 開発ツール

| ツール | 用途 | ライセンス | 最新の安定版（確認日 2026-11-14） | 導入 |
| --- | --- | --- | --- | --- |
| SwiftLint | 静的解析 | MIT | 0.65.1（2026-08-21 公開） | Homebrew（`swiftlint`） |
| SwiftFormat | 整形 | MIT | 0.64.7（2026-11-14 公開。Homebrew は確認時点で 0.64.6） | Homebrew（`swiftformat`） |
| gitleaks（CLI） | 秘密情報の検出 | MIT | 8.30.1（2026-03-21 公開） | Homebrew（`gitleaks`） |

- いずれも無償で、アプリには組み込まない。費用は発生しない。
- 導入するときに、その時点で Homebrew が配布している最新の安定版を入れ、版を台帳（[dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md) §3）に記録する。リポジトリの LICENSE も直接読む（台帳 §4 の手順 3）。
- gitleaks の GitHub Action は使わない（独自の条件に変わっている。台帳 §3）。

### 5.3 基本設計で見つかった費用の有無

**新たに費用が発生する項目はなかった。** 次のいずれかが必要になった場合は、COST-01（Apple Developer Program）の確認が要る。採用の前にオーナーへ確認する。

| 必要になる場面 | 現在の設計での扱い |
| --- | --- |
| Push 通知 | 使わない（ローカル通知で代える） |
| 継続処理タスクでの GPU の利用 | 要求しない |
| Live Activity・継続処理タスクが Personal Team で動かない | 無償の代替で設計済み（[ui.md](ui.md) §8、[transcription.md](transcription.md) §9） |
| 署名の約 7 日での失効をなくす、TestFlight で配布する | 対象外（UD-02） |
| クラウドでの自動検査 | 使わない（BD-DEV-01。COST-02・03） |

---

## 6. ローカルの検査

満たす要件: TST-CI-01〜08、SEC-SUP-03

### 6.1 検査スクリプト

`scripts/check.sh` が次を順に実行し、1 つでも失敗したら止まる。

| 順 | 検査 | 道具 | 要件 |
| --- | --- | --- | --- |
| 0 | 開発ツールの版が台帳と一致する | 各ツールの版の表示 | SEC-SUP-01 |
| 1 | 整形 | SwiftFormat（確認だけ。書き換えない） | TST-CI-03 |
| 2 | 静的解析（独自ルールを含む。警告もエラー） | SwiftLint | TST-CI-03 |
| 3 | 秘密情報の検出 | gitleaks | TST-CI-04 |
| 4 | Info.plist とプロジェクト設定（§3 の「設定しないもの」、対象デバイス、言語モード） | スクリプト | TST-CI-06、TST-SEC-03 |
| 5 | 依存（外部パッケージが許可リストにあること。MVP では 0 件であること） | スクリプト | TST-CI-05 |
| 6 | ビルド（Swift 6、警告はエラー） | `xcodebuild` | TST-CI-01 |
| 7 | 単体・結合テスト（Package は `swift test` とシミュレータの両方） | `swift test`・`xcodebuild test` | TST-CI-02 |
| 8 | カバレッジの集計（初期は警告だけ） | 同上 | TST-CI-07 |
| 9 | UI テスト | `xcodebuild test` | TST-CI-08 |

### 6.2 実行する時点

| 時点 | 実行する範囲 | 仕組み |
| --- | --- | --- |
| コミット前 | 変更したファイルに対する 1〜3（数秒） | Git のフック（`.githooks/pre-commit`） |
| プッシュ前 | 0〜8 | Git のフック（`.githooks/pre-push`） |
| PR の作成前 | 0〜9。結果を PR の説明に貼る | 手動 |

- フックは `git config core.hooksPath .githooks` で有効にする（セットアップ用のスクリプトで行う）。
- フックを飛ばす指定（`--no-verify`）は使わない。クラウドの CI がない間は、PR の説明に貼った検査結果が統合の条件になる。
- 検査を無効にする・迂回する変更は認めない（TST-CI-05）。規則を変える場合は、理由を PR に書いてレビューを受ける。

---

## 7. 静的解析の規則

満たす要件: SEC-LOG-01、SEC-NET-05、SEC-SUP 群（security-requirements.md §10.1 の実現）

SwiftLint の組み込みの規則と独自ルールで検出する。例外を認める場所（その機能を集約した型のファイル）は規則ごとに明示する。

| 検出するもの | 例外を認める場所 | 要件 |
| --- | --- | --- |
| `print(`・`NSLog(`・`debugPrint(` | なし | SEC-LOG-01 |
| `Logger(` の直接の利用 | ログの型 | [architecture.md](architecture.md) BD-ARC-06 |
| `URLSession`・`URLRequest`・`NWConnection`・`import Network`・WebSocket | なし（MVP） | SEC-NET-01 |
| `SFSpeechRecognizer` | なし | [transcription.md](transcription.md) BD-STT-10 |
| `PrivateCloudComputeLanguageModel` | なし | SEC-NET-02 |
| `try!`・`as!`・強制アンラップ | テストコード | SEC-INP-03 |
| 空の `catch` | なし | SEC-LOG-04 |
| `UserDefaults` の直接の利用 | 設定の型 | SEC-DAT-07 |
| `.documentDirectory`・`NSTemporaryDirectory`・ディレクトリの直接の作成 | 保存ルートの型 | SEC-DAT-03・04 |
| `UIPasteboard` の直接の利用、`.textSelection(` | コピーの型 | SEC-PLT-01 |
| `ShareLink` | なし | SEC-DAT-06 |
| `WKWebView`・`import WebKit` | なし | SEC-PLT-05 |
| `CoreSpotlight`・`NSUserActivity` | なし | SEC-PLT-02 |
| `Date()`・`Date.now`・`UUID()`・`Task.sleep`・乱数の直接の利用 | 注入用の本番実装 | TST-GEN-01・07 |

- 正規表現による規則は、見逃しと誤検出がありうる。規則ごとに違反例と正常例のファイルを用意し、検査スクリプトの中で規則そのものを試験する（BD-DEV-05）。
- 規則で検出しきれないもの（ログに D1〜D3 を入れていないか、など）は、PR のチェックリスト（security-requirements.md §10.2）で確認する。

---

## 8. リポジトリの衛生

満たす要件: SEC-SUP-03〜05

**現状の問題（最初の作業単位で直す）:**

- `.gitignore` がない。
- 個人の Xcode 設定（`MinutesMobile.xcodeproj/xcuserdata/`）が Git の管理対象になっている。

**`.gitignore` に入れるもの:**

| 対象 | 理由 |
| --- | --- |
| `xcuserdata/`・`DerivedData/`・`.build/`・`build/`・`*.xcresult` | 個人の設定とビルドの成果物 |
| `*.wav`・`*.m4a`・`*.caf`・`*.zip` | 録音データを誤って入れない（SEC-SUP-04・05） |
| `*.sqlite`・`*.sqlite-wal`・`*.sqlite-shm` | 試験で作った DB |
| `.env`・`.env.*` | 秘密情報 |
| `.DS_Store` | — |

試験用の音声は合成するので、音声ファイルをリポジトリに置く必要はない（TST-GEN-08）。実機試験の記録には、件数・結果・ハッシュだけを残す。

---

## 9. プライバシーマニフェスト

満たす要件: SEC-PRV-03

`PrivacyInfo.xcprivacy` を用意し、次のとおり申告する。理由コードは候補で、詳細設計で Apple の一覧と照合して確定する。

| 項目 | 申告 |
| --- | --- |
| トラッキング | しない |
| 収集するデータ | なし |
| ファイルの日時を扱う API | アプリ自身のファイルの確認（候補: `C617.1`） |
| ディスクの空き容量を扱う API | 書き込めるかの確認（候補: `E174.1`） |
| `UserDefaults` | アプリ自身の設定（候補: `CA92.1`） |
| システムの起動時間を扱う API（単調時計） | 経過時間の計測（候補: `35F9.1`） |

---

## 10. Personal Team での運用

制約: CON-02

| 事項 | 運用 |
| --- | --- |
| 署名の失効（約 7 日） | **失効する前に、残したい録音を定期的に書き出す**（失効するとアプリが起動せず、その時点では書き出せない）。失効したら、アプリを削除せずに同じ Bundle Identifier のビルドを上書きで入れ直す。上書きでデータが残り復旧できるかは実機で手順を確かめて記録するまで当てにせず、確認済みの手順ができるまでは定期的な書き出しを唯一の保全策とする（アプリを削除すると録音は消える） |
| 使える capability | Background modes・Data protection・App groups など 9 項目（[architecture.md](architecture.md) §1） |
| 使えないもの | Push 通知・Time Sensitive Notifications・Siri・iCloud |
| ウィジェット拡張 | 別の App ID が要る。無償アカウントの App ID の数には上限があるため、ターゲットを増やしすぎない |
| 実機試験の前 | ビルドを入れ直してから行う（test-requirements.md §3.7） |

---

## 11. 断定しない事項

満たす要件: SEC-SUP-01、SEC-PRV-03、TST-CI-03 ／ 制約: CON-02

| 事項 | 断定しない理由 | 担保の方法 |
| --- | --- | --- |
| Homebrew が配布する版が、公式の最新の安定版と一致する | 反映に時間差がある（SwiftFormat で確認した） | 導入時に両方を確かめ、入れた版を台帳に記録する |
| 独自ルールが違反をすべて検出する | 正規表現の限界 | 規則の試験（BD-DEV-05）と PR のチェックリスト |
| ローカルの検査が毎回実行される | フックは飛ばせる | PR の説明に結果を貼ることを統合の条件にする |
| 入れ直しでアプリのデータが残る | 実機で確かめていない | 失効の前に定期的に書き出す運用にする。失効後に削除せず上書きで復旧する手順を実機で確認して記録する |
| プライバシーマニフェストの理由コード | Apple の一覧と照合していない | 詳細設計で確定する |

---

## 12. セキュリティ

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| 依存の混入 | 組み込み依存 0 件。開発ツールは版を照合 | SEC-SUP-01・02、T-10 |
| 秘密情報 | コミット前と検査スクリプトで gitleaks | SEC-SUP-03、T-11 |
| 録音データの混入 | `.gitignore` と運用（§8） | SEC-SUP-04・05 |
| 意図しない通信 | 静的解析（§7）と、通信ゼロの自動テスト（TST-SEC-01） | SEC-NET-01、T-03 |
| 設定の逸脱 | Info.plist とプロジェクト設定の検査（§6.1 の 4） | SEC-NET-05、SEC-DAT-04 |
| 費用・ライセンス | 追加の前に台帳 §4 の手順。費用が発生しうるものはオーナーに確認 | CON-07、UD-05 |

**設計・実装時の注意点:**

- 検査スクリプト自体を変更する PR は、検査を弱めていないかを必ず確認する。
- 開発ツールを更新するときは、ライセンスが変わっていないかも確かめる（無償だったものが有償・独自の条件に変わった例がある）。
- 差し替え用の起動の引数（§4）が本番のビルドで効かないことを、試験で確かめる。
- 実機で録音した音声を、試験記録・Issue・PR に貼らない。

---

## 13. テスト観点

| 対象 | 方法 |
| --- | --- |
| 独自ルール | 違反例のファイルで検出されること、正常例で検出されないこと |
| Info.plist・プロジェクト設定の検査 | 禁止の項目を入れた設定で失敗すること |
| 依存の検査 | 外部パッケージを足した `Package.resolved` で失敗すること |
| 版の照合 | 台帳と違う版で失敗すること |
| 差し替えの無効化 | 本番の構成で起動の引数が無視されること |
| 通信ゼロ（TST-SEC-01） | 通信の読み込みを差し替え、録音から文字起こしまで通して要求が 0 件であること |

**設計・実装時の注意点:**

- 検査の所要時間を記録する。コミット前の検査が遅くなると、飛ばされるようになる。
- テストが並列・順不同で通ることを、最初のテストを書いた時点から保つ。
- 「検査が通った」ことと「実機試験が済んだ」ことを分けて記録する（test-requirements.md §6）。

---

## 14. 詳細設計への申し送り

- 検査スクリプトとフックの中身、独自ルールの正規表現と試験用のファイル。
- 依存の許可リストの置き場所と形式（TST-CI-05）。
- Package の構成（プラットフォームの指定、ターゲット）。
- 差し替え用の起動の引数の一覧。
- 実機試験の記録の置き場所（案: `docs/04-test-records/`）と様式。
- 音声認識の利用目的の文言の要否、プライバシーマニフェストの理由コード。

---

## 15. 出典

確認日 2026-11-14。

- 手元の環境: `xcodebuild -version`・`xcodebuild -showsdks`・`swift --version`、`MinutesMobile.xcodeproj/project.xcproj`
- [GitHub: realm/SwiftLint releases](https://github.com/realm/SwiftLint/releases)（0.65.1、MIT）
- [GitHub: nicklockwood/SwiftFormat releases](https://github.com/nicklockwood/SwiftFormat/releases)（0.64.7、MIT）
- [GitHub: gitleaks/gitleaks releases](https://github.com/gitleaks/gitleaks/releases)（v8.30.1、MIT）
- Homebrew の formula 情報（`brew info`。swiftlint 0.65.1・swiftformat 0.64.6・gitleaks 8.30.1）
- [Apple: Supported capabilities (iOS)](https://developer.apple.com/help/account/reference/supported-capabilities-ios/)
