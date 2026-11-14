# 基本設計書

要件定義書（[../01-requirements/](../01-requirements/)）を受けて、アーキテクチャ・データの持ち方・状態遷移・画面構成・技術の選定を確定する。MVP の 7 文書を作成済み（2026-11-15。UD-07）。

各文書は冒頭に「満たす要件 ID」を列挙し、「セキュリティ」節と「テスト観点」節を持つ（[../README.md](../README.md) §3、[../01-requirements/security-requirements.md](../01-requirements/security-requirements.md) §2）。設計決定には `BD-<領域>-nn` の ID を振っている。

## 1. 文書一覧（読む順）

| 順 | 文書 | 内容 | 主な要件 | 状態 |
| --- | --- | --- | --- | --- |
| 1 | [architecture.md](architecture.md) | 全体構成と信頼境界、層とモジュール、並行性、状態モデル、2 つの時間軸、注入点、**セキュリティとテストの注意点の総覧** | NFR-MNT 群、TST-GEN 群、SEC 全般 | 初版 |
| 2 | [recording.md](recording.md) | 録音パイプライン、オーディオセッション、割り込み・経路変更の状態遷移、Chunk の分割 | FR-REC 群、FR-INT 群、NFR-PRF-01 | 初版 |
| 3 | [storage.md](storage.md) | ファイル配置、SQLite、Chunk の保存、確定、空き容量の縮退、保護クラス、復旧、削除 | FR-SAV 群、FR-FIN 群、FR-RCV 群、SEC-DAT 群 | 初版 |
| 4 | [transcription.md](transcription.md) | エンジンの抽象、ジョブ基盤、Overlap と統合、版管理、延期、バックグラウンド実行 | FR-STT 群、FR-TRV 群 | 初版 |
| 5 | [ui.md](ui.md) | 画面一覧と遷移、各画面、問題の伝え方、プライバシー保護の UI、ロック画面の表示、アクセシビリティ | FR-CNS 群、FR-MTG 群、FR-SET 群、NFR-UX 群 | 初版 |
| 6 | [export.md](export.md) | 書き出しの種類と手順、アーカイブ、ファイル名、一時ファイル、緊急書き出し | FR-EXP 群、SEC-DAT-06 | 初版 |
| 7 | [dev-environment.md](dev-environment.md) | 環境の版、プロジェクト設定の変更、依存と版の固定、ローカルの検査、静的解析の規則 | TST-CI 群、SEC-SUP 群 | 初版 |
| 8 | `summary-and-notes.md`（P2） | 端末内 LLM による要約、検証、版管理、手動ノート | FR-SUM 群、FR-NOT 群、SEC-AI 群 | 未着手（MVP の実装後。接続点は [architecture.md](architecture.md) §10） |

## 2. 設計の要点

| 要点 | 内容 | 文書 |
| --- | --- | --- |
| 原本はファイル、SQLite は索引 | 音声・文字起こし本文（D1・D2）は会議ディレクトリのファイルにだけ置く。SQLite には D3 と処理状態だけを置く。処理が終わった会議を丸ごと最も強い保護クラスへ上げられる | architecture.md BD-ARC-03、storage.md |
| 時間軸は 2 本 | 音声の正は累積サンプル数。画面の時刻はそこに空白区間を足して導く | architecture.md §6 |
| 判断は純粋関数 | 状態遷移・Barrier・Chunk 分割・統合・再試行・縮退の判定を、I/O を持たない関数にして表形式のテストで網羅する | architecture.md BD-ARC-01 |
| 通信コードを持たない | MVP には通信を集約する型も置かない。静的解析と自動テストで確かめる | architecture.md BD-ARC-05 |
| 組み込みの依存は 0 件 | OS 標準だけで構成する。開発ツール 3 種は無償（MIT） | dev-environment.md §5 |
| Personal Team の制約 | Push 通知は使えないのでローカル通知で設計する。Live Activity と継続処理タスクは実機で確かめ、動かない場合の代替を用意している | architecture.md §1 |

## 3. 未解決事項（OQ）の解決状況

| ID | 結論 | 文書 | 実機での確認 |
| --- | --- | --- | --- |
| OQ-01 | 同じ会議の続きとして自動で再開し、中断中は空白区間として記録する（UD-08） | recording.md §5 | TST-DEV-02 |
| OQ-02 | iOS 同梱の SQLite を薄い自作ラッパー経由で直接使う（UD-09） | storage.md §3 | — |
| OQ-03 | 継続処理タスク（GPU を要求しない）を使う。使えない・打ち切られた場合は、前面に戻ったときに未完了のジョブから再開する | transcription.md §9 | TST-DEV-11 |
| OQ-04 | Live Activity を使う。Personal Team で動かない場合は、アプリ内の表示と OS の録音インジケータで代える | ui.md §8 | TST-DEV-12 |
| OQ-05 | 日本語の対応と言語アセットの有無は実行時に判定する。特定の版での提供を前提にしない | transcription.md §3 | TST-STT-07 |
| OQ-06 | **持ち越し**（P2 の基本設計で扱う） | — | — |
| OQ-07 | 無圧縮の ZIP を自作する（Minutes の実装を移植）。サードパーティーは使わない | export.md §4 | — |
| OQ-08 | 予備 200 MB、警告は残り 30 分（開始時は 120 分）、最上位の警告は残り 10 分。値は設計値で、実測して見直す | storage.md §6 | TST-DEV-06 |
| OQ-09 | 書きかけファイルへの逐次追記で、強制終了時の損失を約 1 秒に縮めることを目標にする。保証値は実測まで 30 秒のまま | storage.md §4 | TST-DEV-05 |
| OQ-10 | 処理中は `completeUntilFirstUserAuthentication`、未完了の作業がなくなったら `complete`。再処理は利用者の操作（ロック解除中）から始め、一時的に戻す | storage.md §7 | TST-DEV-03・10 |

## 4. オーナーの確認が必要な事項

| 事項 | 内容 | 期限 |
| --- | --- | --- |
| Bundle Identifier | 確定後は変更できない（CON-09）。決まるまで実機で実際の会議を録音しない | 最初の実録音の前 |
| 費用 | 基本設計で新たに費用が発生する項目はなかった。費用が必要になる場面は [dev-environment.md](dev-environment.md) §5.3 に列挙した | 該当したとき |

## 5. 詳細設計へ進む前に

- 各文書の末尾に「詳細設計への申し送り」がある。詳細設計は基本設計と 1 対 1 で対応させる（[../03-detailed-design/README.md](../03-detailed-design/README.md)）。
- 実装の最初の作業単位は [dev-environment.md](dev-environment.md) の内容（設定の変更・検査・`.gitignore`）で、機能の実装より先に行う。
- 「実機で確かめるまで断定しない」とした事項は、各文書の「断定しない事項」節にある。結果が出たら、同じ作業で該当の設計決定を確定または改める。
