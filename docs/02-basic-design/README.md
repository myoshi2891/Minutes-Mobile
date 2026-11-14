# 基本設計書（作成予定）

要件定義書（[../01-requirements/](../01-requirements/)）を受けて、アーキテクチャ・データモデル・状態遷移・画面構成・技術選定を確定する。各文書の冒頭に「満たす要件 ID」を列挙する（[../README.md](../README.md) §3）。

## 作成予定の文書

| 順 | 文書（予定のファイル名） | 内容 | 主な要件 | 解決する未解決事項 |
| --- | --- | --- | --- | --- |
| 1 | `architecture.md` | 全体構成、層の分割、protocol の境界、並行性モデル（Swift 6）、信頼境界を書き込んだデータフロー図 | NFR-MNT 群、TST-GEN 群、SEC 全般 | — |
| 2 | `recording.md` | 録音パイプライン（オーディオセッション・入力 tap・変換・Chunk 分割）、割り込みと経路変更の扱い | FR-REC 群、FR-INT 群、NFR-PRF-01 | OQ-01 |
| 3 | `storage.md` | ファイル配置、原子的な書き込み、保護クラス、バックアップ除外、空き容量の縮退、確定と復旧 | FR-SAV 群、FR-FIN 群、FR-RCV 群、SEC-DAT 群 | OQ-02、OQ-08、OQ-09、OQ-10 |
| 4 | `transcription.md` | 文字起こしのジョブ基盤、エンジンの抽象化、Overlap と統合、言語アセット、バックグラウンド実行 | FR-STT 群、FR-TRV 群 | OQ-03、OQ-05 |
| 5 | `ui.md` | 画面一覧と遷移、録音画面・会議一覧・文字起こし表示・設定、アクセシビリティ | FR-CNS 群、FR-MTG 群、FR-SET 群、NFR-UX 群 | OQ-04 |
| 6 | `export.md` | 書き出しの形式と手順 | FR-EXP 群、SEC-DAT-06 | OQ-07 |
| 7 | `dev-environment.md` | Xcode・SDK の版、プロジェクト設定の変更（CON-03・CON-04・CON-09）、ローカルでの検査スクリプト、コミット前のフック | TST-CI 群、SEC-SUP 群 | — |
| 8 | `summary-and-notes.md`（P2） | 端末内 LLM による要約、検証、版管理、手動ノート | FR-SUM 群、FR-NOT 群、SEC-AI 群 | OQ-06 |

各文書には「セキュリティ」節と「テスト観点」節を必ず置く（[../01-requirements/security-requirements.md](../01-requirements/security-requirements.md) §2）。
