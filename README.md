# MinutesMobile

iPhone だけで会議を録音し、端末の外へデータを送らずに文字起こしまで行う議事録アプリ。PC/Web 版 Minutes の原則（Recording is Source of Truth / Zero External Data Egress）を iOS へ移植する。

現在の状態: 要件定義（2026-11-14 初版）と基本設計（2026-11-15 初版・MVP の 7 文書）。詳細設計は MVP の 7 文書の初版（2026-11-16）を実装より先に作り、検査の仕組み（`scripts/`）を入れた。アプリの実装は未着手で、各作業単位の実装の直前に該当する詳細設計を再確認して改訂する。

## ドキュメント

設計文書はすべて [docs/](docs/) にある。目次と規約は [docs/README.md](docs/README.md)。

- [要件定義書](docs/01-requirements/requirements.md)
- [セキュリティ・プライバシー要件](docs/01-requirements/security-requirements.md)
- [テスト要件](docs/01-requirements/test-requirements.md)
- [依存・ライセンス・費用台帳](docs/01-requirements/dependencies-and-costs.md)
- [基本設計書](docs/02-basic-design/README.md)（[アーキテクチャ](docs/02-basic-design/architecture.md)・[録音](docs/02-basic-design/recording.md)・[保存](docs/02-basic-design/storage.md)・[文字起こし](docs/02-basic-design/transcription.md)・[画面](docs/02-basic-design/ui.md)・[書き出し](docs/02-basic-design/export.md)・[開発環境](docs/02-basic-design/dev-environment.md)）
- [詳細設計書](docs/03-detailed-design/README.md)（[開発環境と検査](docs/03-detailed-design/dev-environment.md)・[アーキテクチャ](docs/03-detailed-design/architecture.md)・[録音](docs/03-detailed-design/recording.md)・[保存](docs/03-detailed-design/storage.md)・[文字起こし](docs/03-detailed-design/transcription.md)・[画面](docs/03-detailed-design/ui.md)・[書き出し](docs/03-detailed-design/export.md)）
- [実機試験の記録](docs/04-test-records/README.md)（様式のみ）
- [実機手動試験のテスト設計書](docs/04-test-records/device-test-design.md)（土台。観点の一覧と試験ケースの骨子）
- [機能・結合テスト項目書](docs/04-test-records/integration-test-items.md)・[E2E テスト項目書](docs/04-test-records/e2e-test-items.md)（初版。自動テストの項目と要件からの逆引き）
- [セキュリティ確認項目書](docs/04-test-records/security-check-items.md)（初版。セキュリティの観点ごとの確認項目と、SEC ID・脅威からの逆引き）

## 検査

```bash
scripts/setup-tools.sh   # 開発ツール（SwiftLint・SwiftFormat・gitleaks）を tools/versions の版で .tools/ に入れる
scripts/check.sh         # 手順 0〜10 を順に実行する（詳細は docs/03-detailed-design/dev-environment.md §4）
```
