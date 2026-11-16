# 進捗

作業の順番・今の作業・止まっている理由・作業の記録だけをここに置く。各文書の状態は下の「状態の正」の表を見る（ここへ写さない。写すと食い違うため）。

更新の手順: 作業単位を始めたとき・終えたとき・止まったときに、§1 の状態・§2 の次のアクション・§4 の記録を同じコミットで直す（スキル `update-specs` / `tdd-implement` の最後の手順）。**§2 の次のアクションは必ず書く。**

## 状態の正

| 知りたいこと | 正 |
| --- | --- |
| 文書の一覧と版 | [docs/README.md](docs/README.md) §1 |
| 詳細設計の作成状況 | [docs/03-detailed-design/README.md](docs/03-detailed-design/README.md) §1 |
| `scripts/check.sh` の各手順の現在の結果 | [docs/03-detailed-design/dev-environment.md](docs/03-detailed-design/dev-environment.md) §4.1 |
| 未解決事項（OQ）の解決状況 | [docs/02-basic-design/README.md](docs/02-basic-design/README.md) §3 |
| 実機試験の結果 | [docs/04-test-records/README.md](docs/04-test-records/README.md) |
| 実機で人が操作する試験の観点・骨子・未確定事項 | [docs/04-test-records/device-test-design.md](docs/04-test-records/device-test-design.md) |
| 機能・結合テストと E2E の項目、要件からの逆引き | [integration-test-items.md](docs/04-test-records/integration-test-items.md)・[e2e-test-items.md](docs/04-test-records/e2e-test-items.md) |
| セキュリティの確認項目と、SEC ID・脅威からの逆引き | [security-check-items.md](docs/04-test-records/security-check-items.md) |

## 1. 作業の順番

詳細設計は、実装より先に全文書を作ってある（UD-10）。実装の各行は「該当する詳細設計を再確認して改訂する → 実装してテストが通る」の順で進める（再確認の内容は [詳細設計 README](docs/03-detailed-design/README.md) §2）。前の行が「完了」になるまで次の行に着手しない。

| 順 | 作業 | 種別 | 正 | 状態 |
| --- | --- | --- | --- | --- |
| 1 | 作業単位 1: 検査基盤（`scripts/`・静的解析の規則・フックのファイル） | 実装 | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §1〜§10 | 完了（2026-11-16） |
| 2 | 詳細設計の残り 6 文書（`architecture`・`recording`・`storage`・`transcription`・`ui`・`export`） | 設計 | [詳細設計 README](docs/03-detailed-design/README.md) §1 | 完了（2026-11-17。初版・実装前） |
| 3 | 作業単位 2: プロジェクト設定（`project.xcproj` の変更・`Packages/MinutesKit`・テストターゲット・プライバシーマニフェスト・フックの有効化） | 実装 | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §11〜§14 | **次** |
| 4 | 共通基盤の実装（注入点の protocol・時計・一時ディレクトリなど） | 再確認・実装 | [architecture.md](docs/03-detailed-design/architecture.md) | 未着手 |
| 5 | 録音の実装（WAV・リサンプラ・Chunk 分割・割り込みの状態遷移） | 再確認・実装 | [recording.md](docs/03-detailed-design/recording.md) | 未着手 |
| 6 | 保存の実装（ファイル配置・SQLite・Chunk の保存・空き容量） | 再確認・実装 | [storage.md](docs/03-detailed-design/storage.md) | 未着手 |
| 7 | 確定の実装 | 実装 | [storage.md](docs/03-detailed-design/storage.md) | 未着手 |
| 8 | 復旧の実装 | 実装 | [storage.md](docs/03-detailed-design/storage.md) | 未着手 |
| 9 | 文字起こしの実装 | 再確認・実装 | [transcription.md](docs/03-detailed-design/transcription.md) | 未着手 |
| 10 | 画面の実装（Live Activity のターゲットを含む） | 再確認・実装 | [ui.md](docs/03-detailed-design/ui.md) | 未着手 |
| 11 | 書き出しの実装 | 再確認・実装 | [export.md](docs/03-detailed-design/export.md) | 未着手 |
| 12 | P2 の基本設計 `summary-and-notes.md`（OQ-06 を扱う） | 設計 | [基本設計 README](docs/02-basic-design/README.md) §1 | MVP の後（UD-07） |

7・8 は 6 と同じ文書（`storage.md`）なので、再確認は 6 の着手時に 1 回行い、6・7 の実装で文書を改訂した場合だけ読み直す。

## 2. 次のアクション

**この節は空にしない。** 作業を終えたら、次に手を付ける 1 手を具体的に（ファイル・節・コマンドの単位で）書き換えてからコミットする。止まっているなら、何を待っているかと、その間にできる作業を書く。

- **次の 1 手**: 作業単位 2 を始める。[dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 の表のとおりに `MinutesMobile.xcodeproj/project.xcproj` のターゲット `MinutesMobile` の設定を変える（`TARGETED_DEVICE_FAMILY`・`SUPPORTED_PLATFORMS`・`SWIFT_VERSION` など）。スキル `tdd-implement` の手順で行い、変更の前に `scripts/check.sh --steps 4` が失敗することを確かめておく（Red）
- **その前に（オーナーの依頼待ち）**: ブランチ `docs/detailed-design` の `dev` への取り込み。プッシュ・PR・マージは依頼があったときだけ行う。作業単位 2 は、取り込んだ後の `dev` から作業用のブランチを切って始める
- **その後**: §12.2（Info.plist）→ §11（`Packages/MinutesKit` とテストターゲット。ターゲットの分割は [architecture.md](docs/03-detailed-design/architecture.md) §2.2）→ §14（プライバシーマニフェスト）→ `scripts/setup.sh` でフックを有効にする
- **作業**: 作業単位 2（プロジェクト設定）
- **終わりの条件**: `scripts/check.sh` の手順 0〜10 がすべて通る（**今は手順 1・4・7〜9 が失敗する**。[dev-environment.md](docs/03-detailed-design/dev-environment.md) §4.1・§12.3）。Bundle Identifier が仮の値のままなら警告にとどまる（§3）。手順 4 は、§14.1 のプライバシーマニフェスト（`MinutesMobile/PrivacyInfo.xcprivacy`）を作るまで通らない（2026-11-17 に検査へ足した。今の違反は 8 件）
- **Red の記録**: —（作業単位 2 はまだ始めていない）

## 3. 止まっていること・オーナーの判断待ち

| 事項 | 何を止めるか | 何を止めないか | 正 |
| --- | --- | --- | --- |
| Bundle Identifier が未確定 | 実機での実際の会議の録音（CON-09） | 作業単位 2 のほかの変更（検査は警告にとどめる） | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 |
| Git のフックが無効 | — | 作業単位 2 の最後に有効にする | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §5 |
| OQ-06 は持ち越し | P2 の基本設計 | MVP の全作業 | [基本設計 README](docs/02-basic-design/README.md) §3 |
| 試験の候補（TST・詳細設計に定義のないもの）の採否: 実機 28 件・結合 13 件・E2E 13 件・セキュリティの確認 2 件（実機 1 件・検査に足すもの 1 件。ほかの 3 件は 2026-11-17 に採用して検査に入れた） | 候補を TST と詳細設計の一覧に足す作業 | 定義済みの試験と、実装の全作業 | [device-test-design.md](docs/04-test-records/device-test-design.md) §7、[integration-test-items.md](docs/04-test-records/integration-test-items.md) §6、[e2e-test-items.md](docs/04-test-records/e2e-test-items.md) §6、[security-check-items.md](docs/04-test-records/security-check-items.md) §7 |

## 4. 記録

新しいものを上に書く。1 行 1 件。

| 日付 | 内容 |
| --- | --- |
| 2026-11-17 | セキュリティ確認項目書の候補のうち、採用を推奨した 3 件を検査に入れた（オーナーの指示）。(1) 規則 `no_spotlight_user_activity` の対象に `import AppIntents` を追加（SEC-PLT-02）。(2) 設定の検査がプライバシーマニフェストの申告を照合（SEC-PRV-03。自己試験 15 件。実物はマニフェストがまだなく、手順 4 の違反が 7 件から 8 件になった）。(3) `scripts/check-tracked-files.py` を追加し、手順 3 で `.gitignore` の必須の行と追跡しているファイルを照合（SEC-SUP-04。自己試験 20 件。コミット前にも効く）。どれも、足す前に自己試験が失敗すること・検査を壊すと自己試験が失敗することを確かめた。TST-SEC-03・TST-CI-04 と SEC の検証の列、基本設計・詳細設計 `dev-environment.md` を同じ作業で直した。詳細設計 §17.1 の自己試験の件数を実際の値に直した（依存 23 件 → 36 件、文書 28 件 → 29 件。前から食い違っていた）。任意（SCI-PRV-16）と保留（SCI-SUP-15）は候補のまま。手順 0・2・3・5・10 が通る |
| 2026-11-17 | セキュリティ確認項目書 `docs/04-test-records/security-check-items.md` を追加（10 領域・158 件: 定義済み 145・既出の候補 8・新しい候補 5。P2・P3 は 13 行だけ）。SEC 48 件・脅威 12 件・設計書の注意点 73 件からの逆引きを置いた。SEC・TST・設計書は変えていない。分かったこと: 定義済みの確認がレビューだけの MVP の要件が 3 件（SEC-PRV-03・04、SEC-SUP-04）、App Intents が静的解析の対象外（SEC-PLT-02）。未確定事項: リリース前の確認の記録の様式、対応するテストの行がない注意点 2 件（NS-U-07・11）。`scripts/verify-docs.py` が通る |
| 2026-11-17 | 機能・結合テスト項目書 `integration-test-items.md`（定義済み 54・候補 13。MVP の FR 52 件からの逆引き）と、E2E テスト項目書 `e2e-test-items.md`（定義済み 8・候補 13）を `docs/04-test-records/` に追加。TST と詳細設計の対応表は変えていない。未確定事項: 2 層以上をつなぐ試験の置き場所、UI テストで確定待ち・失敗区間を作る手段、差し替えた入力が音声を流す速さ。`scripts/verify-docs.py` が通る |
| 2026-11-17 | 実機手動試験のテスト設計書の土台 `docs/04-test-records/device-test-design.md` を追加（観点 77 件: 定義済み 48（うち P2 が 2）・候補 28・対象外 1。試験ケースの骨子 14 件）。TST は足していない。実機で値を読む手段が設計書にないもの 4 件（計測値・実時間比・保護クラスの属性・ファイルの有無）を同書 §4 に挙げた。`scripts/verify-docs.py` が通る |
| 2026-11-17 | 詳細設計の残り 6 文書が揃った（`storage.md`・`transcription.md`・`ui.md`・`export.md` の初版は 2026-11-16）。UD-10（詳細設計を実装より先にすべて作り、実装の直前に再確認する）を記録し、作業の順番・各文書の状態・スキル `tdd-implement` の着手条件を UD-10 の順に直した。依存台帳に詳細設計での確認結果を追記（新しい依存・費用は 0 件）。`architecture.md` の対応表に TST-SEC-02・TST-DEV-07 を追加。`scripts/verify-docs.py` が通る |
| 2026-11-16 | 詳細設計 `architecture.md`・`recording.md` の初版を作成。`storage.md` は作成中。iOS 27 で割り込みの通知と従来の入力 tap が非推奨になったことを SDK で確認し、`recording.md` に反映 |
| 2026-11-16 | 進捗の管理（本書）と、スキル `update-specs`（仕様書の更新）・`tdd-implement`（TDD での実装）を追加 |
| 2026-11-16 | 作業単位 1（検査基盤）を完了。詳細設計 `dev-environment.md` 初版 |
| 2026-11-15 | 基本設計 MVP の 7 文書の初版 |
| 2026-11-14 | 要件定義 4 文書の初版 |
