# MinutesMobile

iPhone だけで会議を録音し、アプリ自身は端末の外へデータを送らずに文字起こしまで行う議事録アプリ。PC/Web 版 Minutes の原則（Recording is Source of Truth / Zero External Data Egress）を iOS へ移植する。

現在の状態: 要件定義（2026-11-14 初版）と基本設計（2026-11-15 初版・MVP の 7 文書）。詳細設計は 1 文書目（開発環境と検査。2026-11-16）を作成し、検査の仕組み（`scripts/`）を入れた。アプリの実装は未着手。

## ドキュメント

設計文書はすべて [docs/](docs/) にある。目次と規約は [docs/README.md](docs/README.md)。

- [要件定義書](docs/01-requirements/requirements.md)
- [セキュリティ・プライバシー要件](docs/01-requirements/security-requirements.md)
- [テスト要件](docs/01-requirements/test-requirements.md)
- [依存・ライセンス・費用台帳](docs/01-requirements/dependencies-and-costs.md)
- [基本設計書](docs/02-basic-design/README.md)（[アーキテクチャ](docs/02-basic-design/architecture.md)・[録音](docs/02-basic-design/recording.md)・[保存](docs/02-basic-design/storage.md)・[文字起こし](docs/02-basic-design/transcription.md)・[画面](docs/02-basic-design/ui.md)・[書き出し](docs/02-basic-design/export.md)・[開発環境](docs/02-basic-design/dev-environment.md)）
- [詳細設計書](docs/03-detailed-design/README.md)（[開発環境と検査](docs/03-detailed-design/dev-environment.md)。残り 6 文書は未着手）
- [実機試験の記録](docs/04-test-records/README.md)（様式のみ）

## 検査

```bash
scripts/setup-tools.sh   # 開発ツール（SwiftLint・SwiftFormat・gitleaks）を tools/versions の版で .tools/ に入れる
scripts/check.sh         # 手順 0〜10 を順に実行する（詳細は docs/03-detailed-design/dev-environment.md §4）
```
