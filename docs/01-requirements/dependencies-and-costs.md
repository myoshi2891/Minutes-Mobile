# MinutesMobile 依存・ライセンス・費用台帳

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版） |
| 作成日 | 2026-11-14 |
| 運用ルール | UD-05: ライブラリは最新安定版を使う。サードパーティーは無償で使えるものに限る。**費用が発生するものは、オーナーの確認を得るまで採用しない** |
| 関連文書 | [requirements.md](requirements.md)・[security-requirements.md](security-requirements.md) §9・[test-requirements.md](test-requirements.md) §4 |

---

## 1. 費用が発生しうる項目（COST）── オーナー確認台帳

| ID | 項目 | 費用（調査時点の目安） | いつ必要になるか | 状態 |
| --- | --- | --- | --- | --- |
| COST-01 | Apple Developer Program | 年額 99 米ドル（円の金額は Apple の公式ページで確認する） | TestFlight・App Store で配布するとき。Personal Team の制約（約 7 日での失効、一部 capability が使えない）を外したいとき | **要確認**（UD-02 により当面は契約しない） |
| COST-02 | GitHub Actions の macOS ランナー | 非公開リポジトリでは、プランの無償枠を超えた分が分単位で課金される。macOS の単価は Linux の約 10 倍（2026-01-01 改定後で 1 分あたり約 0.062 米ドルとする二次情報あり）。公開リポジトリの標準ランナーは無償 | CI をクラウドで自動実行したいとき | **要確認**（当面はローカルの Mac で検査を実行する。test-requirements.md §4） |
| COST-03 | Xcode Cloud | Apple Developer Program の会員に毎月 25 時間分が含まれる。超過分は有償 | COST-01 を契約し、クラウドでのビルドを使うとき | **要確認**（COST-01 が前提） |
| COST-04 | 有償の SDK・有償版のライブラリ（例: WhisperKit Pro / Argmax Pro SDK） | 有償・独自ライセンス | — | **使用しない**。無償版（MIT）の範囲で実現する |
| COST-05 | 端末内 LLM・STT のモデル重みのうち、利用条件付きのもの（商用利用の制限・利用者数の上限・申請が必要なもの） | ライセンス条件による | P2 で代替モデルを検討するとき | **要確認**（採用前に個別に確認する） |
| COST-06 | 実機試験用の追加端末・周辺機器（Apple Intelligence 対応機・Bluetooth マイクなど） | 機器の購入費 | 手元の端末で試験できない要件があるとき | **要確認** |

確認の記録: オーナーが承認したら、この表の「状態」を「承認（日付・範囲）」に更新し、[../README.md](../README.md) §5 に UD として記録する。

---

## 2. OS 標準のフレームワーク（無償・追加の依存なし）

最優先で使う。Xcode と iOS SDK に含まれ、追加の費用もライセンス上の制約もない。

| フレームワーク | 用途 | フェーズ | 注意点 |
| --- | --- | --- | --- |
| SwiftUI / Observation | 画面・状態管理 | MVP | — |
| AVFoundation（AVAudioEngine / AVAudioSession / AVAudioConverter / AVAudioFile） | 録音・変換・再生・WAV の読み書き | MVP | 割り込み・経路変更の通知に対応する（FR-INT 群） |
| Speech（SpeechAnalyzer / SpeechTranscriber） | 端末内の文字起こし | MVP | 言語アセットは OS が取得する（SEC-NET-03） |
| CryptoKit | SHA-256 | MVP | — |
| SwiftData または SQLite（OS 同梱） | メタデータ・ジョブ表 | MVP | どちらにするかは OQ-02 |
| OSLog | ログ | MVP | privacy の指定（SEC-LOG 群） |
| BackgroundTasks | 長時間の文字起こし | MVP | OQ-03 |
| ActivityKit | ロック画面の録音状態表示 | MVP | Personal Team で使えるか確認する（OQ-04） |
| FoundationModels | 端末内の要約 | P2 | オンデバイスモデルだけを使う（SEC-NET-02） |
| LocalAuthentication | アプリのロック | P2 | — |
| Swift Testing / XCTest / XCUITest | テスト | MVP | — |

---

## 3. サードパーティー製品（無償・オープンソース）

版数は**調査時点の候補**。導入するときに公式のリリースページで最新の安定版を確認し、確認日とともに記録する。

| 製品 | 用途 | ライセンス | 調査時点の版（2026-11-14） | 組み込み方 | フェーズ | 状態 |
| --- | --- | --- | --- | --- | --- | --- |
| SwiftLint（realm/SwiftLint） | 静的解析・独自ルール（security-requirements.md §10.1） | MIT | 0.65 系（二次情報。公式の releases で確定する） | 開発ツール（アプリに組み込まない） | MVP | 採用候補 |
| SwiftFormat（nicklockwood/SwiftFormat） | 整形 | MIT | 0.64 系（二次情報。公式の releases で確定する） | 開発ツール | MVP | 採用候補 |
| gitleaks（CLI） | 秘密情報の検出 | MIT | 8.30 系（二次情報。公式の releases で確定する） | 開発ツール（コミット前のフック） | MVP | 採用候補 |
| WhisperKit（argmaxinc/argmax-oss-swift） | 代替の端末内 STT（OS 標準が使えない端末向け） | MIT（同梱の一部は Apache-2.0） | v1.1.0（2026-08-06） | アプリに組み込む（SPM） | P2 | 条件付き候補（FR-STT-11）。モデルの取得は SEC-NET-04 に従う |
| SpeakerKit（同上の SDK に同梱） | 話者分離 | MIT | v1.1.0 に同梱 | アプリに組み込む（SPM） | P3 | 検討候補 |

**採用しないもの:**

| 製品 | 理由 |
| --- | --- |
| gitleaks の GitHub Action（`gitleaks/gitleaks-action` v2 以降） | v2 で MIT から独自の条件に変わり、組織のリポジトリではライセンスキーが必要になる。CLI（MIT）を直接使う |
| WhisperKit Pro / Argmax Pro SDK | 有償・独自ライセンス（COST-04） |
| 解析・広告・クラッシュ報告の SDK（Firebase Analytics・Crashlytics など） | 外部へ通信する（SEC-NET-06） |
| 音声認識・要約のクラウド API | Zero External Data Egress に反する（SEC-NET-01） |

出典（確認日 2026-11-14）:

- [GitHub: realm/SwiftLint releases](https://github.com/realm/SwiftLint/releases)
- [GitHub: nicklockwood/SwiftFormat releases](https://github.com/nicklockwood/SwiftFormat/releases)
- [GitHub: gitleaks/gitleaks](https://github.com/gitleaks/gitleaks)
- [GitHub Marketplace: Gitleaks Action](https://github.com/marketplace/actions/gitleaks)
- [Swift Package Registry: argmax-oss-swift](https://swiftpackageregistry.com/argmaxinc/argmax-oss-swift)
- [Apple Developer Program のメンバーシップ内容](https://developer.apple.com/programs/whats-included/jp)
- [Tenki: GitHub Actions runner pricing 2026](https://www.tenki.cloud/blog/github-actions-runner-pricing-2026)（二次情報。GitHub の公式料金ページで要確認）

---

## 4. 依存を追加するときの手順

新しいライブラリ・ツール・モデルを入れる前に、次の順で確認し、結果をこの台帳に追記する。**1 つでも満たさなければ採用しない**（または、オーナーに確認する）。

1. **必要性**: OS 標準のフレームワークで代わりにならないか（NFR-MNT-03）。
2. **費用**: 無償で使えるか。有償版・有償プラン・利用量による課金・利用者数の上限がないか。あれば COST に起票して**オーナーに確認する**。
3. **ライセンス**: MIT / Apache-2.0 / BSD 系か。それ以外（GPL 系・独自ライセンス・モデルの利用規約）はオーナーに確認する。リポジトリの LICENSE ファイルを直接読む（二次情報で判断しない）。**候補そのものだけでなく、同梱されているコード（vendored のソース・サブディレクトリ単位で別ライセンスのもの）と間接依存（`Package.resolved` に現れるすべてのパッケージ）も、同じ基準で確認する**。Apache-2.0 など帰属表示の義務があるライセンスを含む場合は、NOTICE ファイルの有無と内容、アプリ内での帰属表示（ライセンス表示画面など）の要否を §3 の該当行に記録する。
4. **通信**: そのライブラリが外部へ通信しないか（解析・テレメトリ・自動更新・モデルの自動取得を含む）。通信する場合は SEC-NET-04 の条件を満たすか。
5. **最新安定版**: 公式のリリースページで最新の安定版（プレリリースではない）を確認し、確認日とともに記録する。`Package.resolved` で版を固定する（SEC-SUP-01）。
6. **保守状況**: 直近の更新・未対応の脆弱性・iOS と Swift の最新版への対応。
7. **記録**: この台帳の §3 に 1 行追加し、PR の説明にも書く。

更新時も同じ手順をとる。特に、**無償だったものが有償・独自ライセンスに変わっていないか**を確認する（例: gitleaks の GitHub Action）。
