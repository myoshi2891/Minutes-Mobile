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
| 基本設計 | [02-basic-design/README.md](02-basic-design/README.md) | 初版（2026-11-15） | 基本設計書の索引。MVP の 7 文書（アーキテクチャ・録音・保存・文字起こし・画面・書き出し・開発環境）と、未解決事項の解決状況 |
| 詳細設計 | [03-detailed-design/README.md](03-detailed-design/README.md) | 作成中（2026-11-16） | 詳細設計書の索引。1 文書目の [dev-environment.md](03-detailed-design/dev-environment.md)（開発環境と検査）を作成済み。残り 6 文書は未着手 |
| 試験記録 | [04-test-records/README.md](04-test-records/README.md) | 様式のみ（2026-11-16） | 実機試験の記録の規則と様式。記録はまだない |
| 試験記録 | [04-test-records/check-self-tests.md](04-test-records/check-self-tests.md) | 記録中（2026-11-20） | 検査の自己試験が効いていることの確認（突然変異）の記録。[dev-environment.md](03-detailed-design/dev-environment.md) §17.2 から移した |

参照元（別リポジトリ・読み取り専用）: 別リポジトリ `Minutes` の `docs/design-local-phase*.md`（PC/Web 版 Minutes の設計書）。
本書群で「Minutes P1 §n」は `design-local-phase1.md` の §n、「Minutes P2 §n」は `design-local-phase2.md` の §n を指す。Minutes の `archive/` は参照しない。

## 2. ディレクトリ規約

```text
docs/
├── README.md                 # 本書（索引・規約）
├── 01-requirements/          # 何を満たすか（WHAT）。実装技術は「候補」までに留める
├── 02-basic-design/          # どう構成するか（アーキテクチャ・データモデル・状態遷移・画面）
├── 03-detailed-design/       # どう実装するか（型・アルゴリズム・テストコード・設定値）
└── 04-test-records/          # 実機試験の記録（録音データそのものは置かない）
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
| `BD-xxx-nn` | 基本設計での設計決定（`xxx` は領域。例: `BD-STO-04`）。決定・理由・退けた代替案を持つ | 02-basic-design の各文書 §1（architecture.md は §3） |

規則:

- ID は一度振ったら再利用しない。廃止する場合は行を残して「廃止（理由・日付）」と書く。
- FR / NFR / CON / INV には「出典」を必ず付け、FR / NFR には「フェーズ」（`MVP` / `P2` / `P3`）も付ける。出典は由来ごとに次のとおり書き、由来をたどれるようにする（「新規」だけで済ませるのは最後の箇条に限る）。
  - Minutes から継承した要件: Minutes の設計書の §・Invariant・用語（`P1 §n`・`P2 §n`・`P1 Invariant n` など）、または Minutes の README・UI・CLAUDE.md・ソースの具体的な場所（項目名・関数名）。読み替えた場合は「の読み替え」を添える
  - オーナーの決定に由来する要件: `UD-nn`
  - プラットフォーム（iOS）の仕様に由来する要件: 「新規（iOS 固有）」に、根拠とする OS の仕様（API・通知・制約の名前。可能なら Apple の文書の URL と確認日）を添える。要件定義書の確認済みの技術情報の節に出典と確認日を置いた場合は「Web 確認（§n）」と書く
  - ほかの要件から派生した要件: 派生元の要件 ID（FR・NFR・SEC・CON など）
  - プロジェクトの設定に由来する要件: 「プロジェクト設定」に、設定名と、その値を持つファイル（`project.xcproj` など）を添える
  - 本プロジェクトで新たに定めた要件で、外部の由来がないもの: 「新規」。関連する要件 ID がある場合は添える。
- SEC は「フェーズ」と「MASVS 分類」、TST は「対応する要件」を付ける（TST のフェーズは対応する要件に従う）。
- 基本設計・詳細設計の各節は、満たす要件 ID を節冒頭に列挙する（逆引き用）。
- テストは対応する要件 ID をテスト名またはタグに含める（TST-GEN-03）。

## 4. 版管理と更新ルール

- 文書の正は Git（`dev` ブランチで編集し、`main` へは PR）。コミット形式は `docs(<scope>): <subject>`。
- 要件を変更したら、同じ作業の中で影響を受ける設計書・テスト要件も更新する（Minutes CLAUDE.md §3 の流儀）。
- 技術の版数・提供状況は記憶で書かず、確認日と出典 URL を添える。確認日から 3 か月以上経った記述は、設計着手時に再確認する。
- 費用が発生しうる項目は、オーナーの承認が記録されるまで採用しない（[dependencies-and-costs.md](01-requirements/dependencies-and-costs.md)）。
- ID の参照・リンク・節の構成・詳細設計のコード片と実ファイルの一致は、`scripts/verify-docs.py` で検査する（[03-detailed-design/dev-environment.md](03-detailed-design/dev-environment.md) §10）。文書を変えたら実行する。

## 5. オーナー決定事項（UD）

| ID | 日付 | 決定 | 影響 |
| --- | --- | --- | --- |
| UD-01 | 2026-11-14 | 処理構成は **端末内で完結**（録音・文字起こし・要約をすべて iPhone 内で行う）。PC 版サーバーとの連携は対象外 | Zero External Data Egress を iOS で維持。PC 連携は将来の拡張点として記述のみ |
| UD-02 | 2026-11-14 | 配布は当面 **個人実機のみ（無料の Personal Team）** | 有償の Apple Developer Program は未契約。TestFlight 移行時に COST-01 を再確認 |
| UD-03 | 2026-11-14 | 対象は **iPhone のみ**。iPad / visionOS / Android は対象外 | CON-03。Xcode の対象デバイス設定変更が必要 |
| UD-04 | 2026-11-14 | MVP は **録音保存 + 確定後の文字起こし**。AI 要約・手動ノートは Phase 2 | 要件表の「フェーズ」列で区別 |
| UD-05 | 2026-11-14 | ライブラリは最新安定版、サードパーティーは無償で使えるものに限定。費用が発生するものは必ずオーナーに確認する | dependencies-and-costs.md の運用ルール |
| UD-06 | 2026-11-14 | テストとセキュリティは初期段階からシフトレフトで扱う | security-requirements.md / test-requirements.md |
| UD-07 | 2026-11-14 | 基本設計は **MVP の 7 文書**を先に作る。P2 の `summary-and-notes.md` は MVP の実装後に作る | 02-basic-design/README.md。P2 の接続点だけ architecture.md §10 に記述 |
| UD-08 | 2026-11-14 | 割り込み後は**同じ会議の続きとして自動で再開**し、中断中は空白区間として記録する | OQ-01 を解決。FR-INT-02 の仮置きを確定。recording.md §5 |
| UD-09 | 2026-11-14 | メタデータの保存は **iOS 同梱の SQLite を薄い自作ラッパー経由で直接使う**（追加の依存なし） | OQ-02 を解決。storage.md §3 |
