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

- **次の 1 手**: [dev-environment.md](docs/03-detailed-design/dev-environment.md) §12.1 の表のとおり `MinutesMobile.xcodeproj/project.xcproj` の設定を変更し、`scripts/check.sh --steps 4` を通す
- **その後**: §11 の Package（`Packages/MinutesKit`）とテストターゲットを追加 → §14.1 のプライバシーマニフェスト → `scripts/check.sh` の全手順が通る → `scripts/setup.sh --hooks` でフックを有効にする
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
| 2026-11-18 | レビュー対応（8 件）: `verify-docs.py` がスラッシュ区切りの省略（`SEC-NET-03 / 04`）を展開し、節の冒頭に「満たす要件:」だけを認める（基本設計 dev-environment.md §10 を「満たす要件: — ／ 制約: CON-02」に。update-specs スキルも更新）。設定の検査は `.build`・`DerivedData`・`xcuserdata` をどの階層でも除外。`check.sh --require-clean` を足し、プッシュ前のフックは未コミットの変更・HEAD 以外のコミットの送信で失敗する（注釈付きのタグはコミットまでたどる）。FR-SAV-04 ③に保持の上限で停止する例外（NFR-REL-05 の例外・`disk_full`・利用者への通知）を明記。FR-INT-02 に通知の権限がない場合の例外（開始時の表示・前面復帰時の表示）を明記（TST-INT-02・TST-SAV-03・TST-DEV-06・12、recording.md §5.1・§5.2・§11・§13、ui.md §4.1・§4.2・§8・§12、storage.md §6、architecture.md の差し替えの表）。export.md §2 に連結した録音の RIFF の 4 バイトの上限（総サンプル数 2,147,483,629。超えたら作成を拒否）と境界の試験。README の概要に「アプリ自身は」。§2 の次のアクションは変わらない |
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
