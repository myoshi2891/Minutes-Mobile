# MinutesMobile ドキュメント索引

MinutesMobile（iPhone 向け・端末内完結の議事録アプリ）の設計文書をこのディレクトリに集約する。
要件定義 → 基本設計 → 詳細設計の順に番号付きディレクトリへ配置し、すべての設計判断が要件 ID まで逆引きできる状態を保つ。

## 1. 文書一覧

| 区分 | 文書 | 状態 | 概要 |
| --- | --- | --- | --- |
| 要件定義 | [01-requirements/requirements.md](01-requirements/requirements.md) | 初版（2026-11-14） | 目的・スコープ・機能要件（FR）・非機能要件（NFR）・制約（CON）・Invariant |
| 要件定義 | [01-requirements/security-requirements.md](01-requirements/security-requirements.md) | 初版（2026-11-14） | セキュリティ・プライバシー要件（SEC）。OWASP MASVS 対応表付き |
| 要件定義 | [01-requirements/test-requirements.md](01-requirements/test-requirements.md) | 初版（2026-11-14） | シフトレフトのテスト要件（TST）・必須異常系シナリオ・CI ゲート |
| 要件定義 | [01-requirements/dependencies-and-costs.md](01-requirements/dependencies-and-costs.md) | 初版（2026-11-14） | 依存・ライセンス・費用の台帳（COST）。有償項目の確認状況 |
| 基本設計 | [02-basic-design/README.md](02-basic-design/README.md) | 未着手 | 作成予定の基本設計書一覧 |
| 詳細設計 | [03-detailed-design/README.md](03-detailed-design/README.md) | 未着手 | 作成予定の詳細設計書一覧 |

参照元（別リポジトリ・読み取り専用）: 別リポジトリ `Minutes` の `docs/design-local-phase*.md`（PC/Web 版 Minutes の設計書）。
本書群で「Minutes P1 §n」は `design-local-phase1.md` の §n、「Minutes P2 §n」は `design-local-phase2.md` の §n を指す。Minutes の `archive/` は参照しない。

## 2. ディレクトリ規約

```text
docs/
├── README.md                 # 本書（索引・規約）
├── 01-requirements/          # 何を満たすか（WHAT）。実装技術は「候補」までに留める
├── 02-basic-design/          # どう構成するか（アーキテクチャ・データモデル・状態遷移・画面）
└── 03-detailed-design/       # どう実装するか（型・アルゴリズム・テストコード・設定値）
```

- ファイル名は英小文字のケバブケース、本文は日本語。
- 1 文書が 2,000 行を超えそうなら、機能単位（例: `recording.md` / `transcription.md`）に分割する。
- 図は Mermaid、コードフェンスには必ず言語を指定する（`text` / `swift` / `mermaid`）。

## 3. 要件 ID 体系

| 接頭辞 | 意味 | 定義文書 |
| --- | --- | --- |
| `FR-xxx-nn` | 機能要件（`xxx` は機能領域。例: `FR-REC-01`） | requirements.md |
| `NFR-xxx-nn` | 非機能要件 | requirements.md |
| `CON-nn` | 制約・前提 | requirements.md |
| `INV-nn` | 不変条件（Invariant） | requirements.md |
| `SEC-xxx-nn` | セキュリティ・プライバシー要件 | security-requirements.md |
| `TST-xxx-nn` | テスト要件 | test-requirements.md |
| `COST-nn` | 費用が発生しうる項目（要確認台帳） | dependencies-and-costs.md |
| `UD-nn` | 利用者（オーナー）の決定事項 | 本書 §5 |
| `OQ-nn` | 未解決事項（Open Question） | requirements.md |

規則:

- ID は一度振ったら再利用しない。廃止する場合は行を残して「廃止（理由・日付）」と書く。
- FR / NFR / CON / INV には「出典」（Minutes の § または `UD-nn`）を必ず付け、FR / NFR には「フェーズ」（`MVP` / `P2` / `P3`）も付ける。SEC は「フェーズ」と「MASVS 分類」、TST は「対応する要件」を付ける（TST のフェーズは対応する要件に従う）。
- 基本設計・詳細設計の各節は、満たす要件 ID を節冒頭に列挙する（逆引き用）。
- テストは対応する要件 ID をテスト名またはタグに含める（TST-GEN-03）。

## 4. 版管理と更新ルール

- 文書の正は Git（`dev` ブランチで編集し、`main` へは PR）。コミット形式は `docs(<scope>): <subject>`。
- 要件を変更したら、同じ作業の中で影響を受ける設計書・テスト要件も更新する（Minutes CLAUDE.md §3 の流儀）。
- 技術の版数・提供状況は記憶で書かず、確認日と出典 URL を添える。確認日から 3 か月以上経った記述は、設計着手時に再確認する。
- 費用が発生しうる項目は、オーナーの承認が記録されるまで採用しない（[dependencies-and-costs.md](01-requirements/dependencies-and-costs.md)）。

## 5. オーナー決定事項（UD）

| ID | 日付 | 決定 | 影響 |
| --- | --- | --- | --- |
| UD-01 | 2026-11-14 | 処理構成は **端末内で完結**（録音・文字起こし・要約をすべて iPhone 内で行う）。PC 版サーバーとの連携は対象外 | Zero External Data Egress を iOS で維持。PC 連携は将来の拡張点として記述のみ |
| UD-02 | 2026-11-14 | 配布は当面 **個人実機のみ（無料の Personal Team）** | 有償の Apple Developer Program は未契約。TestFlight 移行時に COST-01 を再確認 |
| UD-03 | 2026-11-14 | 対象は **iPhone のみ**。iPad / visionOS / Android は対象外 | CON-03。Xcode の対象デバイス設定変更が必要 |
| UD-04 | 2026-11-14 | MVP は **録音保存 + 確定後の文字起こし**。AI 要約・手動ノートは Phase 2 | 要件表の「フェーズ」列で区別 |
| UD-05 | 2026-11-14 | ライブラリは最新安定版、サードパーティーは無償で使えるものに限定。費用が発生するものは必ずオーナーに確認する | dependencies-and-costs.md の運用ルール |
| UD-06 | 2026-11-14 | テストとセキュリティは初期段階からシフトレフトで扱う | security-requirements.md / test-requirements.md |
