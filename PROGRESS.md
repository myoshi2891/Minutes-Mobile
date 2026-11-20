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
| 3 | 詳細設計 `architecture.md`（層・注入点の protocol・並行性・エラー） | 設計 | [基本設計 architecture.md](docs/02-basic-design/architecture.md) §14 の申し送り | 未着手 |
| 4 | 共通基盤の実装（注入点の protocol・時計・一時ディレクトリなど） | 実装 | 詳細設計 `architecture.md` | 未着手 |
| 5 | 詳細設計 `recording.md` | 設計 | [基本設計 recording.md](docs/02-basic-design/recording.md) §14 の申し送り | 未着手 |
| 6 | 録音の実装（WAV・リサンプラ・Chunk 分割・割り込みの状態遷移） | 実装 | 詳細設計 `recording.md` | 未着手 |
| 7 | 詳細設計 `storage.md`（保存・確定・復旧を含む） | 設計 | [基本設計 storage.md](docs/02-basic-design/storage.md) | 未着手 |
| 8 | 保存の実装（ファイル配置・SQLite・Chunk の保存・空き容量） | 実装 | 詳細設計 `storage.md` | 未着手 |
| 9 | 確定の実装 | 実装 | 詳細設計 `storage.md` | 未着手 |
| 10 | 復旧の実装 | 実装 | 詳細設計 `storage.md` | 未着手 |
| 11 | 詳細設計 `transcription.md` → 文字起こしの実装 | 設計・実装 | [基本設計 transcription.md](docs/02-basic-design/transcription.md) | 未着手 |
| 12 | 詳細設計 `ui.md` → 画面の実装（Live Activity のターゲットを含む） | 設計・実装 | [基本設計 ui.md](docs/02-basic-design/ui.md) | 未着手 |
| 13 | 詳細設計 `export.md` → 書き出しの実装 | 設計・実装 | [基本設計 export.md](docs/02-basic-design/export.md) | 未着手 |
| 14 | P2 の基本設計 `summary-and-notes.md`（OQ-06 を扱う） | 設計 | [基本設計 README](docs/02-basic-design/README.md) §1 | MVP の後（UD-07） |

11〜13 は着手時に「設計」と「実装」の 2 行へ分ける。

## 2. 次のアクション

**この節は空にしない。** 作業を終えたら、次に手を付ける 1 手を具体的に（ファイル・節・コマンドの単位で）書き換えてからコミットする。止まっているなら、何を待っているかと、その間にできる作業を書く。

- **次の 1 手**: 設定を変える前に `scripts/check.sh --steps 4` を実行し、失敗の出力（違反の件数と各行）を下の「Red の記録」に書く。その後 [dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 の表のとおり `MinutesMobile.xcodeproj/project.xcproj` の設定を変更し、`scripts/check.sh --steps 4` を通す
- **その後**: テンプレートの Swift ファイルを既存の手順（[dev-environment.md](docs/03-detailed-design/dev-environment.md) §7 の「直すときは `.tools/bin/swiftformat .` を実行する」）で整形し、`scripts/check.sh --steps 1` が通ることを確かめる → §11 の Package（`Packages/MinutesKit`）とテストターゲットを追加 → §14.1 のプライバシーマニフェスト → `scripts/check.sh` の全手順が通る → `scripts/setup.sh --hooks` でフックを有効にする
- **作業**: 順 2（作業単位 2: プロジェクト設定）
- **終わりの条件**: `scripts/check.sh` の手順 0〜10 がすべて通る（Bundle Identifier の警告を除く）
- **Red の記録**: —（PR を作るまで、失敗を確かめたテスト名と失敗の理由をここに書き、PR の説明へ移す。[test-requirements.md](docs/01-requirements/test-requirements.md) §6 の 2）

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
| 2026-11-20 | レビュー対応（3 回目。21 件。見送り 1 件）: 検査は、プッシュ前のフックが `git status` の失敗で止まる・専用の作業ツリーの `.tools` のリンク先が違えば張り直す、`setup-tools.sh` が導入先ごとのロック（`.tools/.install.lock`。取得してからキャッシュを判定）、`check-dependencies.py` が補間の中の式を検査の対象に残す・リンクを解決した先にも除外の規則を当てる、`check-project-settings.py` が `$(inherited)` を継承元の値で展開（決まらなければ展開できない）・条件付きのビルド設定を条件に合う構成・SDK だけで展開、`verify-docs.py` が区切りの行を列ごとに検査・要件の定義を表のデータ行からだけ集める・コード片の書き直しで読み込み後の変更を検出、新しい独自ルール `no_url_content_loading`（`NSData(contentsOf:)` など URL を受け取る読み込み。詳細設計 §6.1）。`test-checks.py` は SwiftLint の行を位置で書き換える・SwiftFormat の行を列で除く・リンクの連鎖で拒否した項目が Bad であることを確かめる・git のローカルの環境変数を外す。文書は export.md §3 の続ける選択をハッシュだけが合わない Chunk に限定、recording.md §4 の通常の停止にも確定の待機の上限、storage.md §4 で確定の前に追記の完了を確かめる（上限・段階 3・閉じた `.part` への追記を捨てる）と §6 の保持の上限に追記待ちの PCM を含める。自己試験・規則の試験・`check.sh` の手順 0・2・3・5・6・10 が通ることを確かめた（自己試験は `$(inherited)`・Debug だけの書き方・補間の中の宣言・リンクの先の除外・区切りの行・フェンスで切れた定義の負例を追加。`setup-tools.sh` のロックの待機は自動の試験がない）。見送り: `project.xcproj` のローカルの Package の参照の検査（書き方が未確認で、推測で解析を足さない。現在の `project.xcproj` に Package の参照はない） |
| 2026-11-19 | レビュー対応（2 回目。27 件。変更なし 0 件）: 検査は `xcproj.py` が RecursionError を読めないものに（Xcode 同梱の python3 3.9 で再現）、`setup.sh` が空文字列の引数を拒否、`check-dependencies.py` がバッククォートの宣言・呼び出しでないメンバーの参照を扱い、`.package(path:)` は起点の中の manifest を指す場合だけ認め、成果物の外を指すリンクを拒否、`check-project-settings.py` が候補の展開の失敗を独立の違反に・有効にすると違反になる鍵の変数を構成・SDK ごとに展開、`check.sh` がカバレッジの JSON の SHA-256 を記録・照合（未試験。手順 7・8 は Package の作成後）、プッシュ前のフックがリポジトリの外の専用の作業ツリー（`<ホーム>/Library/Caches/MinutesMobile/pre-push/`）で検査（`.gitignore` を `/.tools` に）、`test-lint-rules.py` が `allowed/` の対照を検査、`verify-docs.py` が表の列の数・節の冒頭のコードブロック・タイトル付きのリンク・CRLF を扱う、（ここまでは自己試験を追加し、変更前の版で失敗することを確かめた）。`test-checks.py` の適用行の選択の共有・gh の実行ファイル名・git の設定の分離・gitleaks の終了コードは試験の側の堅牢化で、変更前の試験が誤って通る入力は現在の台帳にない（通ることだけを確かめた）。文書は D3 の例外として録音中の経過時間をロック画面に表示（§1.1・SEC-PLT-07・NFR-UX-03・TST-DEV-12・ui.md）、TST-SAV-03 に停止の通知と通知の権限がない場合、export.md の再生位置の式（終了時刻が同じ空白区間も差し引く）・連続した欠落は全体の区間だけ・緊急書き出しの直接渡す経路は容量で拒否しない、storage.md §8 の手順 9 で処理中のまま残った会議を保管に戻す、recording.md の排出と変換は `.part` への追記を待たない、transcription.md §8 で録音の開始の失敗の後に開始条件を評価し直す。§2 の次の 1 手からコミットの手順を除いた |
| 2026-11-19 | レビュー対応（31 件。変更なし 1 件・一部対応 2 件）: 文書は FR-STT-03 の Overlap の条件（空白区間の記録で判定。サンプル番号の連続では判定しない）、FR-FIN-02 の lossRecord に破棄した未保存 Chunk の範囲、TST-FIN-03 に例外確定の `finalizationMode`・承認日時、会議時刻と空白区間の長さを ms に統一（BD-UI-02・recording.md §6・§7・export.md の再生位置の式・TST-DEV-01 ②③・`recording_gaps`）、export.md の欠落区間を WAV の `ICMT` にも記録・ヘッダ不正の WAV を被覆に数えない、recording.md の停止の保留の時間の上限（上限で未保存として保持）、storage.md の rename の失敗でハッシュを読み直す、transcription.md の失敗区間の再試行で成功区間の結果ファイルを版の SHA-256 と照合（合わなければ再試行しない・起動時は `retry_result_unavailable`。再試行の記録に元の版の番号を追加）、ui.md の復元・失効後の削除、試験記録に残せる項目を様式の列に（SEC-SUP-05 ほか）、gh の出典、update-specs の `--staged` の順序。検査は `no_direct_sleep_timer` が `func sleep(` を除く、`test-lint-rules.py` が作業ツリーを走査（`--staged` はインデックス）、`check-dependencies.py` の除外の階層、`setup-tools.sh` のリンク名をツール名に・中断時の旧い導入の復元、`setup.sh` の引数の数、`test-checks.py` の `--no-ff` と現在のアーキテクチャの行の選択、`verify-docs.py` の網羅の欄の位置・フェンスで表を区切る・コード片が変わったときだけ書き直す・root の外に書かない（それぞれ自己試験を追加し、修正前のスクリプトで失敗することを確かめた）。`setup-tools.sh` の中断時の復元は自動の試験がない（未試験）。変更なし: `.swiftlint.yml` の除外と明示したパス（Sample の 3 規則の診断を確認）。一部対応: export.md の SDK の版は URL がない（ローカルの確認として dev-environment.md §2 を参照）、詳細設計の作業単位の記述（本文は既に 7〜9。§12.3 の表を直した） |
| 2026-11-19 | レビュー対応（47 件。4 件見送り）: 検査は `no_empty_catch` が条件の括弧の中のクロージャーを読み飛ばす（2 段の入れ子まで）、`no_direct_sleep_timer` が `Task . sleep` を検出（`Timer.publish`・`makeTimerSource` の違反例も追加）、`test-lint-rules.py` が数字を含む規則名の印と字下げのないコメントを読む、プッシュ前のフックが削除だけの送信で HEAD を解決しない、`check.sh` の `--steps` を正規の番号に限る・カバレッジの記録を未コミットの作業ツリーではツリーのハッシュに・gitleaks の履歴の検査でマージの差分も見る（`-m`）、`coverage-report.py` が行数を検証、`setup-tools.sh` がリンクの先を照合・置き換えの失敗で旧い導入を戻す、`check-dependencies.py` がリモートのバイナリの checksum とローカルのバイナリの内容のハッシュを承認と照合、`check-project-settings.py` がマイクの利用目的の型・変数の展開・空の条件付きの参照先・生成しない構成の向きを検査、`xcproj.py` が NaN・Infinity を拒否、`verify-docs.py` が表の区切りの行・個別の要件 ID の対応表の行・山括弧のリンク・字下げしたフェンス・`--root` の外のコード片を扱う。文書は FR-INT-06（未決定は同意確認の後に許可を求める）・FR-STT-09（版ごとに記録）・SEC-INT-03（下流の処理にも使わない）・TST-AUD-05・TST-INT-06・TST-SEC-09・TST-SEC-11、FR-REC-08・FR-INT-02・04 の Apple の文書の出典、recording.md の停止と割り込みの終了の順序・再開後のサンプルがない停止、ui.md・export.md の長さが不明な空白区間、export.md の失敗時の保護クラス、基本設計 README の保護クラスの範囲、gh 2.104.0 の出典、CLAUDE.md の `load_project`。見送り: `.swiftlint.yml` の除外を外す一時ツリー（明示したパスは除外されず、Sample の違反も試験済み）、`project.xcproj` の依存の宣言だけの検査（書き方が未確認。§20）、PR のタイトルと説明の 2 件（リポジトリのファイルの外。PR の上で直す） |
| 2026-11-18 | レビュー対応（40 件。1 件は一部見送り）: 検査は `no_empty_catch` が 1 段の入れ子のブロックコメントを読む（字句解析への置き換えは SwiftLint の規則が正規表現だけのため見送り）、`no_bsd_socket`・`no_direct_sleep_timer` が空白を挟むメンバーの呼び出しを除く、`test-lint-rules.py` が Sample の違反を通常の設定で確かめる・末尾コメント付きの規則名を読む・SwiftLint の起動失敗を終了コード 2 に、`check.sh` の未追跡ファイルの明示・テストのディレクトリの変更で規則の試験・カバレッジを測ったコミットの照合、プッシュ前のフックが削除だけの送信で検査を起動しない、`setup-tools.sh` が版の表示の失敗を不一致にし・置き換えの前に版を照合し・必須のツール 4 種を求める（`--require`）、`check-dependencies.py` が閉じていない文字列・補間を読めないものに・版別の manifest も検査・起点を確認、`check-project-settings.py` が版つきの SDK 名で照合・arch の条件付きの参照先と生成の指定を扱う、`xcproj.py` が `{,}` を拒否、`find-simulator.py` が末尾の 0 を同一視、`verify-docs.py` がフェンスの字下げ 0〜3・対応表の行・広すぎる範囲・フェーズ・網羅の欄を検査。文書は状態遷移（停止後は保存の試行の終了で finalizing へ、FR-SUM-05 の再生成 completed → summarizing と失敗時の保持）、INV の出典の列、NFR-UX-03・TST-DEV-12 をダイナミックアイランドのある機種に限定、TST-RCV-01・TST-DEV-05 の 30 秒の判定を書き込み成功中に限定、出典の書き方の規則（docs/README.md §3）と FR-REC-08・FR-INT-02・04・CON-01 の出典、export.md の連結した録音のハッシュ不一致の注記（`LIST`/`ICMT` とファイル名の印）、transcription.md §7 ③で選ばなかった失敗区間の `succeeded` を `failed` に、台帳 §4 の手順 7 に許可リストの同時更新、詳細設計 §3.4 の手順の順序、update-specs スキルの `--staged` の注意。§2 の次の 1 手を「先に手順 4 で Red を記録」に変更 |
| 2026-11-18 | レビュー対応（8 件）: `verify-docs.py` がスラッシュ区切りの省略（`SEC-NET-03 / 04`）を展開し、節の冒頭に「満たす要件:」だけを認める（基本設計 dev-environment.md §10 を「満たす要件: — ／ 制約: CON-02」に。update-specs スキルも更新）。設定の検査は `.build`・`DerivedData`・`xcuserdata` をどの階層でも除外。`check.sh --require-clean` を足し、プッシュ前のフックは未コミットの変更・HEAD 以外のコミットの送信で失敗する（注釈付きのタグはコミットまでたどる）。FR-SAV-04 ③に保持の上限で停止する例外（NFR-REL-05 の例外・`disk_full`・利用者への通知）を明記。FR-INT-02 に通知の権限がない場合の例外（開始時の表示・前面復帰時の表示）を明記（TST-INT-02・TST-SAV-03・TST-DEV-06・12、recording.md §5.1・§5.2・§11・§13、ui.md §4.1・§4.2・§8・§12、storage.md §6、architecture.md の差し替えの表）。export.md §2 に連結した録音の RIFF の 4 バイトの上限（総サンプル数 2,147,483,629。超えたら作成を拒否）と境界の試験。README の概要に「アプリ自身は」。§2 の次のアクションは変わらない |
| 2026-11-18 | レビュー対応（10 件）: `check.sh --expect-head <SHA>` を足し、プッシュ前のフックは照合した HEAD を渡す（開始までに HEAD が変われば失敗）。`no_direct_clock` に `Date.init()`・`Date(timeIntervalSinceNow:)`、`no_direct_logger` に型注釈付きの `.init(` を追加（fixture も）。`check-dependencies.py` は URL の正規化を scheme とホストに限定、文字列リテラルの本文を宣言と見なさない、`.binaryTarget(path:)` は許可リストの `path` の項目がある場合だけ認める（詳細設計 §9.1・§9.2）。`verify-docs.py` の申し送りの表を表ごとに見出しを除く。`test-checks.py` の形式不正の負例を列から作る。台帳の SQLite の行に確認日と出典。transcription.md §7 ③で選んだ失敗区間の `succeeded` の行も `pending` に戻す（状態遷移図・§13）。§2 の次のアクションは変わらない |
| 2026-11-18 | レビュー対応（7 件）: `force_unwrapping` の既定の例外（`URL(string:)`・`UIImage(named:)` など）を空にし違反例を追加。FR-FIN-01 ③に永続化した未保存の記録 0 件を追加し、記録を永続化できない場合（④〜⑥: 自動確定を許容・表示に追記・`lossRecord` に記録しない）を明記（TST-FIN-01・02、storage.md §5.1、ui.md §4 も更新）。NFR-REL-04 に「書き込み失敗でメモリ保持中は 1 Chunk の上限を保証しない」の但し書きを追加し、storage.md §6 に整合の説明を追加。`check.sh --staged` の手順 10 をインデックス全体で照合。依存の検査で補間の中のコメントを読み飛ばし、行コメントを CR・LF・CRLF で終える。設定の検査の除外を root 直下に限り枝刈り。`test_destination` の失敗を伝播 |
| 2026-11-18 | レビュー対応（3 件）: Documents の検出を例外の場所のない `no_documents_directory` に分離（保存ルートの型の中の指定も違反例 `violation/Storage/StorageRoot.swift` で試験。`test-lint-rules.py` が `violation/` 配下を読む）、`check.sh` は未コミットの変更がある・検査中に HEAD が変わった場合にハッシュ付きの合格を出さない、目的文を「アプリ自身は送信しない」に改め標準のコピー・切り取りのユニバーサルクリップボード同期を SEC-PLT-01 の残る危険に明記。§2 の次のアクションは変わらない |
| 2026-11-18 | レビュー対応（7 件）: `no_empty_catch` が行コメントの中の `{`・`}` を取り違えない、`no_direct_logger` が `Logger.init(` を検出、マイクの利用目的を構成と SDK の組ごとに判定、`check.sh --staged` の規則の試験をインデックスの内容で実行、欠落区間を空白区間と別の区分に（FR-FIN-02 ③・FR-TRV-03・用語集・export.md §2・storage.md・ui.md・transcription.md）、TST-DEV-01 ②の必要サンプル数から記録した空白区間を差し引く、Apple Intelligence の可否を MVP の設定画面に表示（ui.md §4.6・architecture.md §7）。§2 の次のアクションは変わらない |
| 2026-11-18 | レビュー対応（4 件）: `setup-tools.sh` が末尾に改行のない最終行を読む・導入済みのツールの再利用に実行時の版の一致を追加、`verify-docs.py` が申し送りの解決の列が空の行を解決と見なさない、P2 のノートの編集欄の標準のコピー・切り取りを残る危険として扱う（SEC-PLT-01・ui.md §7.3） |
| 2026-11-18 | レビュー対応（14 件）: `no_empty_catch` が条件のブロックコメントを読み飛ばす、`no_direct_sleep_timer` が `Darwin.sleep`・`Darwin.usleep`、`no_direct_uuid` が `UUID.init()` を検出、`check-project-settings.py` が生成しない構成の参照先の plist を 1 つずつ判定、`check.sh --staged` の gitleaks がインデックスの設定を使用、`check-dependencies.py` が `project.xcproj` の依存に同じ `.xcodeproj` の確定版を要求（要求条件は書き方が未確認のため判定できないとして失敗させる）、`verify-docs.py` が逆順の範囲を報告、版の自己試験の負例を現在の値から生成。文書: TST-GEN・CI に対応する要件の列、失敗区間の再試行を選んだ区間だけに（transcription.md §4.3・§7、storage.md §8、ui.md）、状態の変化でスナップショットを送る（ui.md BD-UI-02・recording.md §7）、実機試験の完了条件を §6 に合わせる、スキル 2 件を補足 |
| 2026-11-17 | レビュー対応（8 件。1 件見送り）: `no_empty_catch` が複数行の `where` に対応、`check.sh --staged` で `git diff` の失敗を検出、`setup.sh` は `--hooks` を付けたときだけフックを有効に、`verify-docs.py` の網羅判定を「／」の前に限定・`docs/` と定義文書の欠落を終了コード 2 に、storage.md §9 に削除の開始後の文字起こしの拒否（transcription.md §8 の開始条件にも追加）、スキル 2 件を補足。手順 7・8 を Package がないときに飛ばす案は、検査を弱めるため見送り |
| 2026-11-17 | レビュー対応（検査 7 件）: `no_empty_catch` の一致範囲を `catch` に限定（条件に文字列を含む空の catch の見逃し）、`check-dependencies.py` で Package.swift のコメントを除外、`check-project-settings.py` でマイクの利用目的を構成ごとの生成後の Info.plist で判定・`CODE_SIGN_ENTITLEMENTS` の参照先を検査、`verify-docs.py` で申し送りを項目名で照合・設計書の配下のディレクトリも検査・フェンスの中の印を無視。dev-environment.md §19 の表の先頭の列を基本設計の項目名に合わせた。突然変異の確認で、条件の文字列の中の `{` の誤検出と、補間の中の文字列による宣言の隠蔽も見つけて直した（§17） |
| 2026-11-17 | レビュー対応: `no_empty_catch` の誤検出 2 件を修正、MVP の網羅の判定から基本設計の索引を除外、`test-lint-rules.py` の git 失敗を終了コード 2 に、recording.md（割り込みの開始中の停止・④の後の停止）と transcription.md（再試行の失敗区間の結果ファイルを復旧で再利用しない）を修正、スキル 2 件を補足 |
| 2026-11-16 | 進捗の管理（本書）と、スキル `update-specs`（仕様書の更新）・`tdd-implement`（TDD での実装）を追加 |
| 2026-11-16 | 作業単位 1（検査基盤）を完了。詳細設計 `dev-environment.md` 初版 |
| 2026-11-15 | 基本設計 MVP の 7 文書の初版 |
| 2026-11-14 | 要件定義 4 文書の初版 |
