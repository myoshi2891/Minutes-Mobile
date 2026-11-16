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

## 1. 作業の順番

詳細設計 n を書く → 実装してテストが通る → 詳細設計 n+1 へ進む（[詳細設計 README](docs/03-detailed-design/README.md) §2）。前の行が「完了」になるまで次の行に着手しない。

| 順 | 作業 | 種別 | 正 | 状態 |
| --- | --- | --- | --- | --- |
| 1 | 作業単位 1: 検査基盤（`scripts/`・静的解析の規則・フックのファイル） | 実装 | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §1〜§10 | 完了（2026-11-16） |
| 2 | 作業単位 2: プロジェクト設定（`project.xcproj` の変更・`Packages/MinutesKit`・テストターゲット・プライバシーマニフェスト・フックの有効化） | 実装 | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §11〜§14 | **次** |
| 3 | 詳細設計 `architecture.md`（層・注入点の protocol・並行性・エラー） | 設計 | [基本設計 architecture.md](docs/02-basic-design/architecture.md) §14 の申し送り | 初版を作成（2026-11-16。未コミット） |
| 4 | 共通基盤の実装（注入点の protocol・時計・一時ディレクトリなど） | 実装 | 詳細設計 `architecture.md` | 未着手 |
| 5 | 詳細設計 `recording.md` | 設計 | [基本設計 recording.md](docs/02-basic-design/recording.md) §14 の申し送り | 初版を作成（2026-11-16。未コミット） |
| 6 | 録音の実装（WAV・リサンプラ・Chunk 分割・割り込みの状態遷移） | 実装 | 詳細設計 `recording.md` | 未着手 |
| 7 | 詳細設計 `storage.md`（保存・確定・復旧を含む） | 設計 | [基本設計 storage.md](docs/02-basic-design/storage.md) | **作成中**（設計の決定とコード片の一部まで。文書は未作成） |
| 8 | 保存の実装（ファイル配置・SQLite・Chunk の保存・空き容量） | 実装 | 詳細設計 `storage.md` | 未着手 |
| 9 | 確定の実装 | 実装 | 詳細設計 `storage.md` | 未着手 |
| 10 | 復旧の実装 | 実装 | 詳細設計 `storage.md` | 未着手 |
| 11 | 詳細設計 `transcription.md` → 文字起こしの実装 | 設計・実装 | [基本設計 transcription.md](docs/02-basic-design/transcription.md) | 未着手 |
| 12 | 詳細設計 `ui.md` → 画面の実装（Live Activity のターゲットを含む） | 設計・実装 | [基本設計 ui.md](docs/02-basic-design/ui.md) | 未着手 |
| 13 | 詳細設計 `export.md` → 書き出しの実装 | 設計・実装 | [基本設計 export.md](docs/02-basic-design/export.md) | 未着手 |
| 14 | P2 の基本設計 `summary-and-notes.md`（OQ-06 を扱う） | 設計 | [基本設計 README](docs/02-basic-design/README.md) §1 | MVP の後（UD-07） |

11〜13 は着手時に「設計」と「実装」の 2 行へ分ける。

**作業中の変更（2026-11-16）:** オーナーの決定により、残りの詳細設計 6 文書を実装より先にまとめて作る（UD-10 として [docs/README.md](docs/README.md) §5 に記録する。まだ記録していない）。ブランチ `docs/detailed-design` で作業している。全文書が揃ったら、この表と上の「作業の順番」の説明を、UD-10 の順（全文書を先に書く → 単位ごとに「再確認 → 実装」）へ組み替える。

## 2. 次のアクション

**この節は空にしない。** 作業を終えたら、次に手を付ける 1 手を具体的に（ファイル・節・コマンドの単位で）書き換えてからコミットする。止まっているなら、何を待っているかと、その間にできる作業を書く。

- **次の 1 手**: 詳細設計 `docs/03-detailed-design/storage.md` を書く（ブランチ `docs/detailed-design`）。節の構成・決めたこと・コード片の状態は引き継ぎメモ `.claude/plans/gleaming-strolling-corbato-handover.md` の §4 にある
- **その後**: `transcription.md` → `ui.md` → `export.md` → `architecture.md` の参照の節番号の修正 → `dev-environment.md` の「仮」の確定 → UD-10 と各文書の状態の更新 → `scripts/verify-docs.py` と `scripts/check.sh --steps 10` を通す（引き継ぎメモ §2）。文書が揃った後の実装の最初の 1 手は、従来どおり作業単位 2（[dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 の `project.xcproj` の設定変更）
- **作業**: 詳細設計の残り 6 文書の作成（計画 `.claude/plans/gleaming-strolling-corbato.md`）。`architecture.md`・`recording.md` は初版を作成済み
- **終わりの条件**: 6 文書が揃い、`scripts/verify-docs.py` が通る。**今は失敗する**（未作成の 4 文書へのリンクと、未定義の UD-10 だけが理由）
- **Red の記録**: —（文書だけの作業。実装のテストはまだ書いていない）

## 3. 止まっていること・オーナーの判断待ち

| 事項 | 何を止めるか | 何を止めないか | 正 |
| --- | --- | --- | --- |
| Bundle Identifier が未確定 | 実機での実際の会議の録音（CON-09） | 作業単位 2 のほかの変更（検査は警告にとどめる） | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 |
| Git のフックが無効 | — | 作業単位 2 の最後に有効にする | [dev-environment.md](docs/03-detailed-design/dev-environment.md) §5 |
| OQ-06 は持ち越し | P2 の基本設計 | MVP の全作業 | [基本設計 README](docs/02-basic-design/README.md) §3 |

## 4. 記録

新しいものを上に書く。1 行 1 件。

| 日付 | 内容 |
| --- | --- |
| 2026-11-16 | 詳細設計 `architecture.md`・`recording.md` の初版を作成（未コミット）。`storage.md` は作成中。iOS 27 で割り込みの通知と従来の入力 tap が非推奨になったことを SDK で確認し、`recording.md` に反映 |
| 2026-11-16 | 進捗の管理（本書）と、スキル `update-specs`（仕様書の更新）・`tdd-implement`（TDD での実装）を追加 |
| 2026-11-16 | 作業単位 1（検査基盤）を完了。詳細設計 `dev-environment.md` 初版 |
| 2026-11-15 | 基本設計 MVP の 7 文書の初版 |
| 2026-11-14 | 要件定義 4 文書の初版 |
