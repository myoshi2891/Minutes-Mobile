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
| BD-DEV-03 | 開発ツール（SwiftLint・SwiftFormat・gitleaks）は、台帳に記録した版の配布ファイルを公式の GitHub releases から取得し、台帳の SHA-256 と照合してからリポジトリ内の `.tools/`（Git の管理外）に展開する。どの環境でも同じ導入スクリプトで同じ版を入れる。検査スクリプトは `.tools/` のツールだけを使い、実行時の版を台帳と照合し、違えば失敗する | 台帳の版をどの環境でも再現できる。Homebrew は formula の最新版を入れるため、過去の版に固定できない（SEC-SUP-01） | Homebrew で導入する（版を再現できない）。`brew extract` で過去の formula を自前の tap に取り出す（開発者向けの機能で、ツールごとに tap の保守が要る） |
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
| 音声認識の利用目的 | **置かない**。Apple の文書と SDK で、許可の手続きは `SFSpeechRecognizer` にだけ当てはまることを確かめた（確認日 2026-11-16。詳細設計 §14.2）。実機では TST-STT-07 で確かめる | SEC-PRV-01 |
| Background Modes | `audio`（録音の継続）。継続処理タスクに必要なら `processing`。ビルド設定の鍵としては用意されていないので、Info.plist のファイルに書く（詳細設計 §12.2） | FR-REC-05、[transcription.md](transcription.md) §9 |
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

| ツール | 用途 | ライセンス | 最新の安定版（確認日 2026-11-14） | 導入（GitHub releases の配布ファイル。確認日 2026-11-16） |
| --- | --- | --- | --- | --- |
| SwiftLint | 静的解析 | MIT | 0.65.1（2026-08-21 公開） | `portable_swiftlint.zip`（チェックサムのファイルの配布はない。release API の `digest` と照合できる） |
| SwiftFormat | 整形 | MIT | 0.64.7（2026-11-14 公開。Homebrew は確認時点で 0.64.6） | `swiftformat.zip`（同上） |
| gitleaks（CLI） | 秘密情報の検出 | MIT | 8.30.1（2026-03-21 公開） | `gitleaks_{版}_darwin_arm64.tar.gz`（Intel の Mac は `darwin_x64`）。`gitleaks_{版}_checksums.txt` が配布されている |
| GitHub CLI（`gh`） | 上の 3 つの配布ファイルの取得だけ | MIT | 2.104.0（2026-11-12 公開。確認日 2026-11-16） | `gh_{版}_macOS_arm64.zip`（Intel の Mac は `macOS_amd64`）。`gh_{版}_checksums.txt` が配布されている |

- いずれも無償で、アプリには組み込まない。費用は発生しない。
- **導入（台帳の版の再現）:** 導入スクリプト（`scripts/setup-tools.sh`。中身は [詳細設計](../03-detailed-design/dev-environment.md) §3）が、台帳の機械可読の写し（`tools/versions`。ツールごとの版・配布ファイルの名前・SHA-256）を読み、その版の配布ファイルを取得する。取得に使う `gh` も台帳で版を固定し、導入スクリプトが `curl` で公式のリリースから取得・照合して `.tools/` に入れ、ほかのツールはその `gh`（`gh release download`）だけで取得する（PATH や Homebrew の `gh` は使わない。版は §6.1 の手順 0 で照合する）。SHA-256 が台帳と一致した場合だけ `.tools/` に展開し、一致しなければ展開せずに失敗する。Homebrew の版は使わない（PATH にあっても検査スクリプトは `.tools/` のものだけを呼ぶ）。
- **最初の記録:** チェックサムのファイルが配布されていないツール（SwiftLint・SwiftFormat）は、最初の導入の PR で配布ファイルの SHA-256 を計算して記録し、レビューで公式の releases の配布ファイルであることを確かめる。gitleaks は配布されたチェックサムと一致することも確かめる。リポジトリの LICENSE も直接読む（台帳 §4 の手順 3）。**2026-11-16 に実施した**: 3 つとも、計算した値が release API の `digest` と一致した（詳細設計 §3.3）。
- **版の更新（全環境で一括）:** 版を上げるときは、1 つの PR で ①台帳（[dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md) §3）の版・SHA-256・確認日、②`tools/versions`、③版の変更で必要になった規則や整形の差分を同時に直す。導入スクリプトは `tools/versions` を読むだけなので、導入手順を別に直す必要はない。統合の後、各環境で導入スクリプトを実行し直す。実行し直していない環境では §6.1 の手順 0 が失敗するので、古い版のまま検査が通ることはない。
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
| 10 | 文書の整合（ID の参照、リンク、節の構成、詳細設計のコード片と実ファイルの一致。詳細設計で追加） | スクリプト | NFR-MNT-04 |

### 6.2 実行する時点

| 時点 | 実行する範囲 | 仕組み |
| --- | --- | --- |
| コミット前 | ステージしたファイルに対する 1〜3 と、10（数秒）。1・2 は作業ツリーではなく、ステージした内容（インデックス）を検査する | Git のフック（`.githooks/pre-commit`） |
| プッシュ前 | 0〜8 と 10。送信するコミットが HEAD と同じで、未コミットの変更がない場合だけ検査し、そうでなければ push を失敗させる（作業ツリーだけにある修正で合格させない） | Git のフック（`.githooks/pre-push`） |
| PR の作成前 | 0〜10（`--require-clean`。開始時のコミットを専用の作業ツリーで検査する）。結果を PR の説明に貼る | 手動 |
| PR の更新後（PR の作成後に push した場合） | 統合の対象になる最新のコミットに対して 0〜10 を再実行し、結果と対象のコミットのハッシュを PR の説明に記録し直す | 手動 |

- フックは `git config core.hooksPath .githooks` で有効にする（セットアップ用のスクリプトで行う）。
- フックを飛ばす指定（`--no-verify`）は使わない。クラウドの CI がない間は、PR の説明に記録した、統合の対象のコミットに対する 0〜10 の検査結果が統合の条件になる。記録されたコミットのハッシュが統合するコミットと違う場合（古いコミットの結果しかない場合）は統合しない。唯一の例外は、下の初期導入の手順である。
- **初期導入の手順（作業単位 2 が済むまで）。** 手順 1・4・7〜9 は作業単位 2 が済むまで通らないので、その前の PR（文書・検査基盤）は次をすべて満たす場合だけ統合できる。作業単位 2 が済んだ時点でこの例外は終わる。
  1. 統合するコミットで `scripts/check.sh --require-clean` を実行し、失敗の出力（コミットのハッシュと失敗した手順）をそのまま PR に記録する。`check.sh` は最初の失敗で止まるので、残りの手順も `scripts/check.sh --require-clean --steps <番号>` で 1 つずつ実行し、手順 0〜10 のすべての結果（合格・失敗と理由）を記録する。
  2. 失敗してよいのは手順 1・4・7・8・9 で、理由が [詳細設計](../03-detailed-design/dev-environment.md) §4.1 に書いたものと同じ場合だけとする。それ以外の失敗があれば統合しない。
  3. 記録には「初期導入。全手順の合格ではない」と書く。**`--steps` の結果を、合わせても全手順の合格として扱わない。**
  4. オーナーが失敗の一覧を見たうえで統合を承認したことを、対象のコミットのハッシュとともに PR に記録する。承認の記録がなければ統合しない。PR を更新したら、1〜4 を最新のコミットでやり直す。
- 検査を無効にする・迂回する変更は認めない（TST-CI-05）。規則を変える場合は、理由を PR に書いてレビューを受ける。

---

## 7. 静的解析の規則

満たす要件: SEC-LOG-01、SEC-NET-05、SEC-SUP 群（security-requirements.md §10.1 の実現）

SwiftLint の組み込みの規則と独自ルールで検出する。例外を認める場所（その機能を集約した型のファイル）は規則ごとに明示する。

| 検出するもの | 例外を認める場所 | 要件 |
| --- | --- | --- |
| `print(`・`NSLog(`・`debugPrint(` | なし | SEC-LOG-01 |
| `Logger(` の直接の利用 | ログの型 | [architecture.md](architecture.md) BD-ARC-06 |
| `URLSession`・`URLRequest`・`NWConnection`・`import Network`・WebSocket・BSD ソケット（`socket(`・`connect(`）・CFNetwork（`import CFNetwork`）・CFStream（`CFStreamCreatePairWithSocket` 系）・Foundation の `Stream.getStreamsToHost(withName:port:inputStream:outputStream:)` | なし（MVP） | SEC-NET-01 |
| `SFSpeechRecognizer` | なし | [transcription.md](transcription.md) BD-STT-10 |
| `PrivateCloudComputeLanguageModel` | なし | SEC-NET-02 |
| `try!`・`as!`・強制アンラップ | テストコード | SEC-INP-03 |
| 空の `catch` | なし | SEC-LOG-04 |
| `UserDefaults` の直接の利用 | 設定の型 | SEC-DAT-07 |
| `.documentDirectory`・`NSTemporaryDirectory`・ディレクトリの直接の作成 | 保存ルートの型（`NSTemporaryDirectory` とディレクトリの作成だけ。`.documentDirectory` はどこでも認めない） | SEC-DAT-03・04 |
| `UIPasteboard` の直接の利用 | コピーの型 | SEC-PLT-01 |
| `.textSelection(` | なし | SEC-PLT-01 |
| `ShareLink` | なし | SEC-DAT-06 |
| `WKWebView`・`import WebKit` | なし | SEC-PLT-05 |
| `CoreSpotlight`・`NSUserActivity` | なし | SEC-PLT-02 |
| `Date()`・`Date.now`・`UUID()`・`Task.sleep`・乱数の直接の利用 | 注入用の本番実装 | TST-GEN-01・07 |

- 正規表現による規則は、見逃しと誤検出がありうる。規則ごとに違反例と正常例のファイルを用意し、検査スクリプトの中で規則そのものを試験する（BD-DEV-05）。
- 規則の名前・正規表現・例外のパスと、同じ経路になる書き方（`@AppStorage`・`Thread.sleep` など）への拡張は、[詳細設計](../03-detailed-design/dev-environment.md) §6 にある。
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
| `.tools/` | 導入スクリプトが展開した開発ツール（§5.2）。版は `tools/versions` で管理する |
| `*.wav`・`*.m4a`・`*.caf`・`*.zip` | 録音データを誤って入れない（SEC-SUP-04・05） |
| `*.sqlite`・`*.sqlite-wal`・`*.sqlite-shm` | 試験で作った DB |
| `.env`・`.env.*` | 秘密情報 |
| `.DS_Store` | — |

試験用の音声は合成するので、音声ファイルをリポジトリに置く必要はない（TST-GEN-08）。実機試験の記録には、試験の様式（[test-requirements.md](../01-requirements/test-requirements.md) §3.7）の項目（日付・ビルドのコミット・端末機種・OS 版・試験 ID・結果・観測値（件数・ハッシュを含む）・不合格の理由）だけを残す（SEC-SUP-05）。

---

## 9. プライバシーマニフェスト

満たす要件: SEC-PRV-03

`PrivacyInfo.xcprivacy` を用意し、次のとおり申告する。理由コードは Apple の一覧と照合して確定した（確認日 2026-11-16。[詳細設計](../03-detailed-design/dev-environment.md) §14.1）。

| 項目 | 申告 |
| --- | --- |
| トラッキング | しない |
| 収集するデータ | なし |
| ファイルの日時を扱う API | アプリ自身のファイルの確認（`C617.1`） |
| ディスクの空き容量を扱う API | 書き込めるかの確認（`E174.1`） |
| `UserDefaults` | アプリ自身の設定（`CA92.1`） |
| システムの起動時間を扱う API（単調時計） | 経過時間の計測（`35F9.1`。単調時計の実装が対象の API を使わない場合は外す） |

---

## 10. Personal Team での運用

満たす要件: — ／ 制約: CON-02

| 事項 | 運用 |
| --- | --- |
| 署名の失効（約 7 日） | **失効する前に、残したい録音と、残したい手動ノート（P2。FR-NOT-05 の Markdown の書き出し）を定期的に書き出す**（失効するとアプリが起動せず、その時点では録音もノートも書き出せない）。失効したら、アプリを削除せずに同じ Bundle Identifier のビルドを上書きで入れ直す。上書きでデータが残り復旧できるかは実機で手順を確かめて記録するまで当てにせず、確認済みの手順ができるまでは定期的な書き出しを唯一の保全策とする（アプリを削除すると録音もノートも消える） |
| 使える capability | Background modes・Data protection・App groups など 9 項目（[architecture.md](architecture.md) §1） |
| 使えないもの | Push 通知・Time Sensitive Notifications・Siri・iCloud |
| ウィジェット拡張 | 別の App ID が要る。無償アカウントの App ID の数には上限があるため、ターゲットを増やしすぎない |
| 実機試験の前 | ビルドを入れ直してから行う（test-requirements.md §3.7） |

---

## 11. 断定しない事項

満たす要件: SEC-SUP-01、SEC-PRV-03、TST-CI-03 ／ 制約: CON-02

| 事項 | 断定しない理由 | 担保の方法 |
| --- | --- | --- |
| 台帳の版の配布ファイルを取得して、この Mac で起動できる | **確認済み（2026-11-16）**: 3 つとも導入スクリプトで入れ、版の表示が台帳と一致した（Apple シリコンの Mac）。Intel の Mac では実行していない | 詳細設計 §3.3・§17.3。Intel の Mac を使うことになったら、導入して確かめる |
| 過去の版の配布ファイルが取得できなくなる | 公開側が削除しうる | SHA-256 を台帳に記録しているので、取得できた場合の改変は検出できる。取得できなければ版を上げる PR で対応する |
| 独自ルールが違反をすべて検出する | 正規表現の限界 | 規則の試験（BD-DEV-05）と PR のチェックリスト |
| ローカルの検査が毎回実行される | フックは飛ばせる | PR の説明に結果を貼ることを統合の条件にする |
| 入れ直しでアプリのデータが残る | 実機で確かめていない | 失効の前に定期的に書き出す運用にする。失効後に削除せず上書きで復旧する手順を実機で確認して記録する |
| プライバシーマニフェストの理由コード | **確定（2026-11-16）**: 4 つとも Apple の一覧にあり、説明が用途に合う。システムの起動時間の項目の要否だけ、単調時計の実装による | 詳細設計 §14.1 |

---

## 12. セキュリティ

満たす要件: SEC-SUP-01〜05、SEC-NET-01・05、SEC-DAT-04 ／ 制約: CON-07 ／ 脅威: T-03・10・11 ／ 決定: UD-05

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

満たす要件: TST-SEC-01（表の各行が対応する検査は §6.1 の TST-CI-03・05・06、TST-SEC-03、SEC-SUP-01）

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

満たす要件: —（詳細設計への引き継ぎ事項。満たす要件はない）

- 検査スクリプトとフックの中身、独自ルールの正規表現と試験用のファイル。
- 開発ツールの導入スクリプト（`scripts/setup-tools.sh`）と、台帳の機械可読の写し（`tools/versions`）の置き場所と形式（§5.2）。
- 依存の許可リストの置き場所と形式（TST-CI-05）。
- Package の構成（プラットフォームの指定、ターゲット）。
- 差し替え用の起動の引数の一覧。
- 実機試験の記録の置き場所（案: `docs/04-test-records/`）と様式。
- 音声認識の利用目的の文言の要否、プライバシーマニフェストの理由コード。

---

## 15. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-14。

- 手元の環境: `xcodebuild -version`・`xcodebuild -showsdks`・`swift --version`、`MinutesMobile.xcodeproj/project.xcproj`
- [GitHub: realm/SwiftLint releases](https://github.com/realm/SwiftLint/releases)（0.65.1、MIT）
- [GitHub: nicklockwood/SwiftFormat releases](https://github.com/nicklockwood/SwiftFormat/releases)（0.64.7、MIT）
- [GitHub: gitleaks/gitleaks releases](https://github.com/gitleaks/gitleaks/releases)（v8.30.1、MIT）
- Homebrew の formula 情報（`brew info`。swiftlint 0.65.1・swiftformat 0.64.6・gitleaks 8.30.1）
- 各版の配布ファイルの一覧（`gh release view`。gh v2.104.0・gitleaks v8.30.1・SwiftLint 0.65.1・SwiftFormat 0.64.7。確認日 2026-11-16）
- [GitHub: cli/cli releases](https://github.com/cli/cli/releases)（gh 2.104.0、MIT。配布ファイルと `gh_2.104.0_checksums.txt` の SHA-256 の根拠。確認日 2026-11-16）
- [Apple: Supported capabilities (iOS)](https://developer.apple.com/help/account/reference/supported-capabilities-ios/)
