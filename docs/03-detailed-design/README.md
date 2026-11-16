# 詳細設計書

基本設計書（[../02-basic-design/](../02-basic-design/)）の単位ごとに、型定義・アルゴリズム・設定値・テストコードの粒度まで落とし込む。設計書だけを読んで実装とレビューができる状態を目標にする（Minutes P1 の「位置づけ」と同じ）。

## 1. 文書一覧（作る順）

満たす要件: NFR-MNT-04 ／ 決定: BD-DEV-02、UD-07

基本設計書と 1 対 1 で対応させる。上流から順に作り、前の単位の実装とテストが通ってから次に着手する。

| 順 | 文書 | 対応する基本設計 | 状態 |
| --- | --- | --- | --- |
| 1 | [dev-environment.md](dev-environment.md) | [dev-environment.md](../02-basic-design/dev-environment.md) | 初版（2026-11-16）。検査基盤は実装し、動かして確かめた。プロジェクト設定の変更は次の作業単位 |
| 2 | `architecture.md` | [architecture.md](../02-basic-design/architecture.md) | 未着手 |
| 3 | `recording.md` | [recording.md](../02-basic-design/recording.md) | 未着手 |
| 4 | `storage.md` | [storage.md](../02-basic-design/storage.md) | 未着手 |
| 5 | `transcription.md` | [transcription.md](../02-basic-design/transcription.md) | 未着手 |
| 6 | `ui.md` | [ui.md](../02-basic-design/ui.md) | 未着手 |
| 7 | `export.md` | [export.md](../02-basic-design/export.md) | 未着手 |

## 2. 方針

満たす要件: NFR-MNT-04、TST-GEN-03 ／ 決定: UD-06

- 基本設計の設計決定（`BD-<領域>-nn`）を引用し、各文書の末尾にある「詳細設計への申し送り」をすべて解決する。解決は「申し送り」の節の表に、項目ごとに 1 行で書く。
- 最初に作るのは `dev-environment.md`（検査スクリプト・静的解析の規則・プロジェクト設定）とする。機能の実装より先に検査の仕組みを入れるため（BD-DEV-02）。
- 詳細設計書のコード片は、実装ファイルと一致させる。どちらかを変えたら、同じ作業でもう一方と本文を直す（NFR-MNT-04）。
- 各文書に「セキュリティ」節・「テスト観点」節・「要件 ID ↔ テスト」の対応表を置く（[../01-requirements/test-requirements.md](../01-requirements/test-requirements.md) §6）。
- 実装の順序は、上流（録音 → 保存 → 確定 → 復旧）から下流（文字起こし → 表示 → 書き出し）へ進める。前の単位のテストが通ってから次に着手する。

## 3. 文書の検査

満たす要件: NFR-MNT-04

文書の整合は `scripts/verify-docs.py` が検査する（`scripts/check.sh` の手順 10。内容は [dev-environment.md](dev-environment.md) §10）。

| 書き方 | 検査の内容 |
| --- | --- |
| 各節（`##` の見出し）の冒頭に「満たす要件:」（満たす要件がない節は「満たす要件: —」。制約だけなら「制約:」） | 行がなければ失敗する |
| コード片の直前に `<!-- file: パス -->` | コード片がそのファイルの全文と一致する。`scripts/verify-docs.py --update-snippets` で実ファイルから書き直せる |
| コード片の直前に `<!-- excerpt: パス -->` | コード片がそのファイルの連続した一部と一致する |
| まだ作っていないファイルのコード片 | 印を付けない。ファイルを作る作業で印を付ける |
