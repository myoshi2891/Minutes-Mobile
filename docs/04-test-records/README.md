# 実機試験の記録

シミュレータで再現できない試験（[../01-requirements/test-requirements.md](../01-requirements/test-requirements.md) §3.7 の TST-DEV 群など）の結果を置く。様式と置き場所は [../03-detailed-design/dev-environment.md](../03-detailed-design/dev-environment.md) §15 で決めた。

試験の観点・機材・試験ケースの骨子・実施の順序は [device-test-design.md](device-test-design.md)（実機手動試験 テスト設計書。土台）にある。合格条件の正は test-requirements.md、結果の正は本書の様式で作る `device-tests.md` である。

このディレクトリには、試験の設計と項目の文書も置く。

| 文書 | 内容 | 実行 |
| --- | --- | --- |
| [integration-test-items.md](integration-test-items.md) | 機能・結合テスト項目書。複数の部品をまたぐ自動テストの項目と、要件からの逆引き | Swift Testing |
| [e2e-test-items.md](e2e-test-items.md) | E2E テスト項目書。利用者の操作から成果物までを通すシナリオ | XCUITest・Swift Testing（実機の通しは下の設計書を参照） |
| [device-test-design.md](device-test-design.md) | 実機手動試験のテスト設計書。プログラムで確かめられない観点と試験ケースの骨子 | 実機・手動 |
| [security-check-items.md](security-check-items.md) | セキュリティ確認項目書。上の 3 文書の項目と、静的解析・設定の検査・レビューの確認を、守る対象と脅威で切り直した索引。SEC ID・脅威・設計書の注意点からの逆引き | 検査スクリプト・自動テスト・実機・レビュー |

自動テストの名前と状態の正は、詳細設計の各文書の「要件 ID ↔ テストの対応表」である。項目書には写さない。

## 1. 規則

- 試験 1 回を 1 行で、`device-tests.md`（最初の記録のときに作る）の表に追記する。行は消さず、やり直した場合は新しい行を足す。
- **録音データそのものは置かない。** 置くのは件数・結果・ハッシュだけである（SEC-SUP-04・05）。実機で録音した音声を、記録・Issue・PR に貼らない。
- 自動の検査（`scripts/check.sh`）が通ったことと、実機試験が済んだことは別に扱う。実機試験が必要な要件は、この記録に「合格」があって初めて完了とする（test-requirements.md §6）。
- Personal Team の署名は約 7 日で失効する（CON-02）。試験の前にビルドを入れ直す。

## 2. 様式

| 列 | 内容 |
| --- | --- |
| 日付 | 試験した日（`YYYY-MM-DD`） |
| ビルドのコミット | 試験したビルドのコミットのハッシュ |
| 端末機種 | 例: iPhone の機種名 |
| OS 版 | 例: iOS 27.2 |
| 試験 ID | 例: TST-DEV-01 |
| 結果 | 合格・不合格・未実施・判定不能 |
| 観測値 | Chunk 数・空白区間・電池・温度など、試験の合格条件が求める値 |
| 不合格の理由 | 不合格・未実施・判定不能のときに書く。未実施なら実施の予定も書く |

## 3. 記録

まだない（実装の前のため）。
