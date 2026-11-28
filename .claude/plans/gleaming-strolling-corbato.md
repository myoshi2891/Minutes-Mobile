# 詳細設計書（残り 6 文書）の作成計画

## Context

- 詳細設計は 7 文書中 `dev-environment.md` だけ作成済み。残りは `architecture` / `recording` / `storage` / `transcription` / `ui` / `export` の 6 文書（[docs/03-detailed-design/README.md](../../docs/03-detailed-design/README.md) §1）。
- オーナーの決定（2026-11-16、本セッション）: **残り 6 文書をすべて実装前に作る**。現行規則「1 文書書く → 実装 → 次の文書」は書き換える。
- 依頼のほかの点はすでに規則として存在するので、新設せず守る:
  - 置き場所: `docs/03-detailed-design/`（索引は `docs/README.md`）。新しい置き場所は作らない
  - 最新安定版・無償のみ・費用は確認: UD-05、`dependencies-and-costs.md` §4 の 7 手順
  - シフトレフト: UD-06。各文書に「セキュリティ」「テスト観点」節と注意点の一覧を置く
- 作業はスキル `update-specs` の手順で行う（Swift の実装はしない）。

## 成果物

新規 6 ファイル（名前は基本設計と同じにする。`scripts/verify-docs.py` の `check_handover` が同名の基本設計を要求するため、別名への分割はできない）:

| 順 | ファイル | 基本設計の申し送り（解決の表の行数） | 主な内容 |
| --- | --- | --- | --- |
| 1 | `docs/03-detailed-design/architecture.md` | 5 | Package のターゲット分割（MinutesDomain / Storage / Audio / Transcription）と依存の向き、注入点 15 種の protocol と本番・テスト実装の型名、会議状態の `reduce`（状態 × 事象 × 副作用の全表）、2 つの時間軸の変換関数、エラー分類の列挙と利用者向け 3 点への写像、型付きログのイベント一覧と privacy 指定、取り出しの合図の方式、起動の引数の確定名 |
| 2 | `recording.md` | 5 | リングバッファ（原子操作・あふれの記録列）、変換器の設定とモノラル化、形式変更時の吐き出し、録音セッションの状態遷移の全表、空白区間の理由の列挙、同意の値の型（BD-REC-08）、中断通知の文言と予約時間 |
| 3 | `storage.md` | 6 | 表の DDL・索引・制約・移行番号、`meeting.json`／ハッシュ記録／墓標の形式と版、置換しない rename の方式、SQLite ラッパーの API、`.part` の同期方式と周期、Finalization Barrier、空き容量の閾値、保護クラスの切り替えと `seal_pending` のきっかけ、照合（復旧）と削除の手順 |
| 4 | `transcription.md` | 5 | エンジンの protocol と `SpeechAnalyzer` への入力の組み立て、結果・版ファイルの項目、ジョブ表と次のジョブの選び方（fencing）、統合の擬似コードと設計値、延期の判定、継続処理タスクの識別子と進捗の頻度 |
| 5 | `ui.md` | 5 | 画面ごとの配置・文言・用語の言い換え表、Store の状態型とスナップショットの契約、問題の一覧と 3 点の文言、覆いの条件とキーボード拒否、Live Activity の状態型・更新頻度・停止の確認方法 |
| 6 | `export.md` | 4 | ZIP 各ヘッダのバイト配置、目録の項目と版、共有シートを包む部品と完了の受け取り、メモリ上のデータを渡す経路、ファイル名の無害化関数、一時ファイルの掃除 |

各文書 2,000 行未満（`verify-docs.py` の上限）。`storage.md` が超えそうなら、コードは型とアルゴリズムの要点に絞り表で書く。それでも収まらなければ作業を止めてオーナーに相談する（分割には検査スクリプトの変更が要るため）。

## 各文書の共通構成（`dev-environment.md` を手本にする）

1. 冒頭の表（版・作成日・技術情報の確認日・満たす要件 ID・対応する基本設計と引用する BD）
2. 本文の各 `##` 節の冒頭に「満たす要件:」（ない節は「満たす要件: —」）
3. 「セキュリティ」節: 脅威 T-nn と対策の表 + **実装・レビュー時の注意点**（基本設計 architecture.md §12.2 の NS-01〜12 を、その文書の型・関数の単位まで具体化）
4. 「テスト観点」節: **先に書いて失敗させるテストの一覧**（Red の順）、失敗の注入点、表形式テストの次元（状態 × 事象）、古い応答・二重実行・途中で落ちた状態の各ケース、シミュレータで確かめられず実機試験（TST-DEV）に回す項目（NT-01〜12 を具体化）
5. 「要件 ID ↔ テスト」の対応表（TST-GEN-03。テスト名の案まで書く。状態は全件「未実装」）
6. 「基本設計の申し送りの解決」の表（行数 = 基本設計の箇条書きの数。上の表のとおり）
7. 「断定しない事項・確かめていない事項」（実機で未確認のものは保証値にしない）
8. 「出典」（確認日 2026-11-16 と URL・SDK のファイル名）

## 書き方の規則

- **事実は記憶で書かない。** OS の API（Speech・AVFAudio・BackgroundTasks・ActivityKit・sqlite3・zlib）は Xcode 27.2 同梱の iOS 27.2 SDK の `.swiftinterface`・ヘッダを読んで確かめ、確認日と出典を書く。確かめられないものは「断定しない事項」へ置く。
- **Swift のコード片はコンパイルして確かめる。** スクラッチパッドに一時の Package を作り（リポジトリには置かない）、Swift 6 言語モード・警告はエラーで `swift build` が通ったものだけを載せる（`dev-environment.md` §11.1 と同じやり方）。確かめた範囲を各文書に書く。
- 実ファイルがまだないので、コード片に `<!-- file: -->`／`<!-- excerpt: -->` の印は付けない（README §3）。印は実装の作業で付ける。
- 基本設計の BD を引用する。BD と食い違う結論が必要になったら、基本設計の側を同じ作業で直す（BD の変更は影響を `grep` で調べる）。
- 禁止事項（`print`・通信 API・`try!`・強制アンラップ・`.documentDirectory` など）をコード片でも使わない。

## 既存の「仮」の確定（architecture.md・ui.md で行う）

- `dev-environment.md` §6.3 の例外の場所（仮のパス）、§13 の起動の引数の名前、§20 の「`35F9.1` の要否」を確定する。
- **原則として仮の名前・パスをそのまま確定名にする**（`.swiftlint.yml` と `tools/lint-fixtures/` を変えずに済ませる）。変える必要が出た場合だけ、`.swiftlint.yml`・`tools/lint-fixtures/<規則名>/allowed/`・`dev-environment.md` のコード片（`scripts/verify-docs.py --update-snippets`）を同じ作業で直し、`scripts/test-lint-rules.py` を通す。
- `dev-environment.md` §11.1 の Package のターゲット分割は、architecture.md の結論に合わせて本文を直す（`Package.swift` は未作成なので文書だけ）。

## 依存と費用

- 想定は**新しい依存 0 件**（BD-ARC-07。SQLite・zlib・CryptoKit は OS 同梱）。
- 設計中にサードパーティー・有償の項目・GPL 系・利用条件付きのものが必要に見えたら、**書く前に作業を止めてオーナーに確認する**。
- `dependencies-and-costs.md` §1 に「詳細設計（2026-11-16）での確認結果」を 1 段落追記する（基本設計の段落と同じ形式）。

## 規則と状態の更新（同じ作業で直す）

| ファイル | 変更 |
| --- | --- |
| [docs/README.md](../../docs/README.md) | §1 の詳細設計の行の状態。§5 に **UD-10**（残り 6 文書を実装前にまとめて作る。各単位の実装直前に該当文書を再確認して改訂する。2026-11-16）を追加 |
| [docs/03-detailed-design/README.md](../../docs/03-detailed-design/README.md) | §1 の状態を「初版（実装前）」に。§1・§2 の「前の単位の実装とテストが通ってから次に着手する」を、UD-10 の順（全文書を先に書く → 単位ごとに「再確認 → 実装」）へ書き換える。実装の順序（上流から）は変えない |
| [PROGRESS.md](../../PROGRESS.md) | §1 の表を組み替える（設計の行は完了、各実装の行の前提を「詳細設計の再確認」に）。§2 の次のアクション（作業単位 2 のまま）、§4 の記録 |
| [README.md](../../README.md)・[CLAUDE.md](../../CLAUDE.md) | 「現在の状態」「残り 6 文書は未着手」の記述を直す |
| [docs/01-requirements/dependencies-and-costs.md](../../docs/01-requirements/dependencies-and-costs.md) | 上記の確認結果 |
| 基本設計の各文書 | 詳細設計で BD・設計値を改めた場合だけ |

## ブランチ

- `dev` から作業用のブランチを切り、すべての変更をそこで行う（オーナーの指示、2026-11-16）。`dev` には直接書かない。

```bash
git switch dev && git status --short   # 作業ツリーが clean であることを確かめる
git switch -c docs/detailed-design     # dev の先頭（8b0f419）から作る
```

- 取り込み先は `dev`。プッシュ・PR の作成・`dev` へのマージは依頼があったときだけ行う。

## 作業の順番

0. 上のブランチを作る（最初のファイル編集より前）
1. `architecture.md`（ほかの 5 文書が型名・エラー・ログ・注入点を引用するため最初）
2. `recording.md` → `storage.md` → `transcription.md` → `ui.md` → `export.md`（上流から）
3. 各文書を書く前に、対応する基本設計の全文と、関係する要件（`requirements.md`・`security-requirements.md`・`test-requirements.md` の該当群）を読む
4. 1 文書書くごとに `scripts/verify-docs.py` を実行して直す
5. 最後に規則と状態の更新、全体の検査

コミットは依頼があったときだけ、作業用ブランチ `docs/detailed-design` に行う（`docs(design): …`）。

## 検証

```bash
scripts/verify-docs.py            # ID・リンク・節の構成・申し送りの行数・2,000 行・確認日
scripts/test-checks.py docs       # 文書の検査の自己試験が変わらず通る
scripts/check.sh --steps 10       # 手順 10（文書の整合）
scripts/test-lint-rules.py        # .swiftlint.yml に触れた場合だけ
wc -l docs/03-detailed-design/*.md
```

- スクラッチパッドの一時 Package で、載せた Swift のコード片が Swift 6・警告エラーでビルドできること
- 6 文書それぞれに: 申し送りの表の行数が一致、MVP の該当 TST が「要件 ID ↔ テスト」に漏れなく現れる（`grep` で TST-AUD・INT・SAV・FIN・RCV・STT・SEC・GEN を突き合わせる）
- 検査を弱めて通さない。`check.sh` の手順 1・4・7〜9 の失敗は作業単位 2 の範囲で、今回は変わらない
