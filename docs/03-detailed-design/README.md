# 詳細設計書（作成予定）

基本設計書（[../02-basic-design/](../02-basic-design/)）の単位ごとに、型定義・アルゴリズム・設定値・テストコードの粒度まで落とし込む。設計書だけを読んで実装とレビューができる状態を目標にする（Minutes P1 の「位置づけ」と同じ）。

## 方針

- 基本設計書と 1 対 1 で対応させる（例: `02-basic-design/recording.md` → `03-detailed-design/recording.md`）。
- 詳細設計書の Swift コード片は、実装ファイルと一致させる。どちらかを変えたら、同じ作業でもう一方と本文を直す（NFR-MNT-04）。
- 各文書に「要件 ID ↔ テスト」の対応表を置く（[../01-requirements/test-requirements.md](../01-requirements/test-requirements.md) §6）。
- 実装の順序は、上流（録音 → 保存 → 確定 → 復旧）から下流（文字起こし → 表示 → 書き出し）へ進める。前の単位のテストが通ってから次に着手する。
