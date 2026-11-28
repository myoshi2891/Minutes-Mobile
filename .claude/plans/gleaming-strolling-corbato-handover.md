# 引き継ぎ: 詳細設計書（残り 6 文書）の作成

作成 2026-11-16。計画は同じディレクトリの `gleaming-strolling-corbato.md`。この文書は、計画のどこまで終わり、次に何をするか、途中で決めたこと・分かったことを書く。

> **この文書は 2026-11-16 時点の記録で、今は使わない（履歴）。** 下の §1・§2 の「途中」「未着手」「失敗する」はその時点の状態であり、作業はすべて済んでいる: 残り 6 文書（`storage`・`transcription`・`ui`・`export` を含む）は 2026-11-17 に初版を作り（[PROGRESS.md](../../PROGRESS.md) §1 の順 2「完了」）、`scripts/check.sh` の手順 10（文書の整合）は 2026-11-27 から通っている（[dev-environment.md](../../docs/03-detailed-design/dev-environment.md) §4.1）。**§2 の手順を繰り返さない。** 次に行う作業は [PROGRESS.md](../../PROGRESS.md) §2 の「次のアクション」を正とする（2026-11-28 時点では、作業単位 2: プロジェクト設定）。§3〜§9 の事実と決定がいまの文書と食い違う場合は、各詳細設計の本文と PROGRESS.md を正とする。

## 1. 今の状態

| 項目 | 状態 |
| --- | --- |
| ブランチ | `docs/detailed-design`（`dev` の 8b0f419 から）。コミットはまだない |
| `docs/03-detailed-design/architecture.md` | 完成（1,393 行・未追跡）。前のセッションが書き、今回 `Scheduler`（§6.1・§6.2・§6.3）と `StorageFailureKind` の `Codable` を足した |
| `docs/03-detailed-design/recording.md` | 完成（1,268 行・未追跡） |
| `storage.md` | **設計とコード片の途中**（文書は未作成。§4 に決めたことを書いた） |
| `transcription.md`・`ui.md`・`export.md` | 未着手（SDK の確認だけ済み。§5） |
| 規則と状態の更新（計画の「規則と状態の更新」の表） | 未着手。`PROGRESS.md` だけ、作業中であることを書いた |
| `scripts/verify-docs.py` | 失敗する。理由は 2 種類だけ: ①未作成の 4 文書へのリンク、②`UD-10` が未定義（`docs/README.md` §5 に最後に足す）。ほかの不整合はない |

## 2. 残りの作業（この順）

1. `storage.md` を書く（§4 の節構成と決定に沿う。コード片は §6 の一時 Package に途中まである）
2. `transcription.md` → `ui.md` → `export.md`
3. `architecture.md` の参照の節番号を、出来上がった文書に合わせて直す（§7）
4. 計画の「既存の『仮』の確定」: `dev-environment.md` §6.3・§11.1・§13・§14.1・§19・§20 を直す（§8）
5. 計画の「規則と状態の更新」の表（`docs/README.md` に UD-10、`docs/03-detailed-design/README.md`、`PROGRESS.md`、`README.md`、`CLAUDE.md`、`dependencies-and-costs.md` §1）
6. 計画の「検証」のコマンドを全部通す。6 文書の「要件 ID ↔ テスト」に MVP の TST が漏れなく現れることを `grep` で突き合わせる
7. コミットは依頼があったときだけ（`docs(design): …`）

## 3. 今回分かった事実（基本設計の後に SDK で確認。2026-11-16）

- **iOS 27 で `AVAudioSession.interruptionNotification` が非推奨**。置き換えは `didBecomeInactive`（原因が `systemInterruption` か `appDeactivated`）と `resumptionRecommendation` の 2 つの通知。`recording.md` §3.3 に反映済み
- **iOS 27 で `installTap(onBus:bufferSize:format:block:)` が非推奨**。置き換えは `installAudioTap(onBus:bufferSize:format:tapProvider:)`（`throws`・`AVReadOnlyAudioPCMBuffer`）。バッファ長の範囲は 100〜400 ms。`recording.md` §4.2 に反映済み
- 最低 OS が 27.2 で警告はエラーなので、非推奨の API はビルドが通らない。**ほかの文書でも、使う API は必ず型検査してから書く**（§6 の `ios.sh`）
- `AVAudioConverter`（品質最大・Mastering）は Mac 上で TST-AUD-02 を満たした（折り返しは量子化で 0、通過帯域 ±0.001 dB）
- 参照元の Minutes リポジトリはこの Mac にない（ローカルの Minutes リポジトリなし）。VAD の数値と ZIP のバイト配置は、基本設計の記述と公開仕様から起こす。その旨を各文書の「断定しない事項」に書く（`recording.md` §14 は記載済み）
- `architecture.md` §6.2 は「`dev-environment.md` §14.1 を同じ作業で直した」と書いているが、`dev-environment.md` は未変更（§8 で直す）

## 4. `storage.md` で決めたこと（まだ文書にしていない）

### 4.1 節の構成（ほかの文書がこの番号で参照している）

1 位置づけ／2 ファイル配置と保存ルート（`StorageLayout`・`StorageRoot`・属性）／3 永続データの形式（`meeting.json`・ハッシュの記録・墓標・DDL・移行）／4 ファイル操作・SQLite ラッパー・Chunk の保存／5 状態の保存の順序／6 停止と確定（Barrier・例外確定）／7 空き容量の縮退／8 保護クラスとバックアップ除外／9 起動時の復旧／10 削除とデータ使用量／11 セキュリティ／12 テスト観点／13 要件 ID ↔ テスト／14 申し送り（**表は 6 行**）／15 断定しない事項／16 出典

`recording.md` は storage.md の §3・§4・§5・§7・§9 を、この意味で参照している。

### 4.2 決定

| 事項 | 決定 |
| --- | --- |
| 置換しない rename | `renamex_np(…, RENAME_EXCL)`（SDK の `sys/stdio.h` で確認）。`FileManager` の移動は使わない。**Mac 上の試験はまだ**（移動先があると `EEXIST` で失敗することを確かめる） |
| 競合の版のファイル名 | `quarantine/conflict-{連番 6 桁}-{SHA-256 の先頭 16 桁}.wav`。救済できない書きかけは `quarantine/part-{連番 6 桁}.wav.part` |
| `.part` への追記 | 取り出しのたび（約 100 ms 分）。ファイルは追記のたびに開いて閉じる（`FileSystem` を状態なしにする） |
| 同期 | 途中は 80,000 サンプル（5 秒分）ごとに `fsync`。Chunk の確定時（ヘッダの書き換えの後）は `F_FULLFSYNC`。**周期はサンプル数で数え、タイマーを使わない**（BD-STO-07・INV-08） |
| 空き容量の閾値 | 基本設計の初期値をそのまま最終値にする（予備 200 MB、開始時 120 分・録音中 30 分・10 分、保持 20 Chunk）。TST-DEV-06 で見直す |
| SQL | ラッパーの SQL の引数を `StaticString` にする（文字列の連結で SQL を作れない）。値は束縛だけ |
| SQLite の接続 | `MeetingStore`（actor）の中だけで持つ。`@unchecked Sendable` は付けない見込み（付けずに済めば `architecture.md` NS-A-08 の「2 か所」を「1 か所」に直す） |
| 開くときのフラグ | `READWRITE`・`CREATE`・`FULLMUTEX`・`EXRESCODE`・`FILEPROTECTION_COMPLETEUNTILFIRSTUSERAUTHENTICATION`。PRAGMA は WAL・外部キー・`synchronous=NORMAL`・`secure_delete=ON` |
| 表 | `STRICT`。日時は UTC の経過ミリ秒（整数）。`meetings` に「録音中は 1 件」の部分一意索引（FR-REC-07）。欠落の記録は `loss_entries` の表。ジョブの「次の実行時刻」は DB に持たない（待ち時間はメモリ上だけ。再起動後はすぐ実行できる） |
| `meeting.json` | 形式の版 1。型は一時 Package の `MeetingDocument.swift`（ビルド済み）。日時は経過ミリ秒。欄ごとの型が復号で検証し、`validated()` が欄をまたぐ整合を見る |
| `seal_pending` を解消するきっかけ | ①保護データが読めるようになった通知（`DeviceConditionsProviding.changes()`）、②前面への復帰、③起動時の復旧の手順 9 |
| errno の写像 | `ENOSPC`・`EDQUOT` → `disk_full`、`EPERM`・`EACCES` → `permission_denied`（保護データを読めない間なら `protected_data_unavailable` に読み替える）、`EEXIST` → `already_exists`、`ENOENT` → `not_found`、ほかは `io_error` |
| 空白区間と Chunk | 割り込みの空白区間は必ず Chunk の境界にある。`overrun`・`route_change`・`media_reset` は Chunk の途中にありうる（`recording.md` §6）。文字起こしの Overlap の判定は、Chunk の開始位置にある空白区間だけを見る（`transcription.md` で書く） |

### 4.3 コード片の状態（一時 Package）

- ビルド済み: `Sources/MinutesDomain/Storage/` の `StorageLayout`・`FileSystem`（protocol と errno の写像）・`FinalizationBarrier`・`StoragePolicies`（空き容量・保護クラス・書きかけの救済・未保存の突き合わせ）・`MeetingDocument`（`Timestamp`・`ChunkHashRecord`・`Tombstone`・`SHA256Digest`・`PersistedJSON`）
- **未作成**: `MinutesStorage` の `PosixFileSystem`・`SQLiteConnection`・スキーマ（DDL）・`StorageRoot`、および保存のテスト（DDL を実際に流す、一意制約の違反の写像、置換しない rename、Barrier、空き容量、救済、`meeting.json` の壊れた入力）

## 5. 残りの文書のために確認済みの SDK の事実（iOS 27.2 SDK・2026-11-16）

| 領域 | 確認したこと |
| --- | --- |
| Speech | `SpeechAnalyzer(modules:options:)`、`start(inputSequence:)`・`analyzeSequence(_:)`、`finalizeAndFinishThroughEndOfInput()`・`cancelAndFinishNow()`。`Options(priority:modelRetention:)` の `modelRetention` は `whileInUse`・`lingering`・`processLifetime`。`SpeechTranscriber(locale:transcriptionOptions:reportingOptions:attributeOptions:)`、`attributeOptions` に `audioTimeRange`・`transcriptionConfidence`。結果は `text`（`AttributedString`）・`range`・`isFinal`。`AnalyzerInput(buffer:bufferStartTime:)`（`buffer` の読み出しは 27 で非推奨）。`AnalyzerInputConverter`、`AssetInventory.status(forModules:)`（`unsupported`・`supported`・`downloading`・`installed`）・`assetInstallationRequest(supporting:)`・`downloadAndInstall()`。エラーコードは `SFSpeechError.Code` の `noModel`・`insufficientResources`・`timeout`・`audioReadFailed`・`unexpectedAudioFormat` など。macOS でもビルドできる |
| BackgroundTasks | `BGContinuedProcessingTaskRequest(identifier:title:subtitle:)`。識別子は `<Bundle ID>.<意味>.*` の形で宣言し、投入時に末尾を具体化する。`strategy`（`fail`・`queue`。既定は `queue`）、`requiredResources`（GPU は使わない）。タスクは `Progress` を報告し続ける。`updateTitle(_:subtitle:)`。投入は `submit(_:)`（同期版は非推奨で、`try await` の版を使う） |
| ActivityKit | `Activity.request(attributes:content:pushType:)`（`pushType: nil`）、`update(_:)`、`end(_:dismissalPolicy:)`、`ActivityContent(state:staleDate:)`、`areActivitiesEnabled` |
| UIKit | `application(_:shouldAllowExtensionPointIdentifier:)` と `.keyboard`、`UIPasteboard.setItems(_:options:)` の `.localOnly`・`.expirationDate`、`UIActivityViewController.completionWithItemsHandler`、`isProtectedDataAvailable` と保護データの 2 つの通知 |
| SQLite・zlib・ファイル | SQLite 3.54.0、`SQLITE_OPEN_FILEPROTECTION_*`、`sqlite3_backup_*`、`sqlite3_wal_checkpoint_v2` と `SQLITE_CHECKPOINT_TRUNCATE`。zlib 1.2.12 の `crc32`。`renamex_np`・`RENAME_EXCL`、`F_FULLFSYNC`。`URLResourceKey` の空き容量（重要用途）・バックアップ除外・保護クラス |
| 通知 | `UNUserNotificationCenter` の予約・取り消し。Time Sensitive は Personal Team で使えない |

SDK の宣言からは判断できなかったもの: `SpeechAnalyzer` に音声認識の利用目的の文言が要るか（基本設計 transcription.md §11 の宿題）。

## 6. 作業の道具

検証済みのコード片と文書のひな形は、同じディレクトリの `gleaming-strolling-corbato-scratch.tgz` に保存した（スクラッチパッドは再起動で消えるため）。新しいセッションのスクラッチパッドへ展開して使う。

```bash
tar -xzf .claude/plans/gleaming-strolling-corbato-scratch.tgz -C <スクラッチパッド>
```

| ファイル | 用途 |
| --- | --- |
| `snippets/` | 一時の Package（リポジトリには置かない）。`architecture.md`・`recording.md` のコード片と、保存の純粋ロジック。Mac 上のテスト 26 件が通る |
| `b.sh build`・`b.sh test` | `swift build`・`swift test` の結果から、エラーと結果の行だけを出す |
| `ios.sh` | `Sources/AppSide/`（`#if os(iOS)` で包む。既定の分離は MainActor）を iOS 27.2 SDK に対してビルドする。アプリ側のアダプタ（UIKit・ActivityKit・BackgroundTasks・通知）はこれで確かめる |
| `render.py <ひな形> <出力>` | ひな形の `@@SRC パス@@`（全文）・`@@SRC パス :: 開始 :: 終了@@`（一部。終了に `zzzEND` と書くと末尾まで）を実ファイルの内容に置き換える。**文書のコード片は手で写さず、これで差し込む** |
| `braces.py <文書>` | 文書の Swift のコード片の波かっこの釣り合いを確かめる |
| `docsrc/recording.md` | `recording.md` のひな形。直すときは、ひな形を直して `render.py` で作り直す |

## 7. `architecture.md` でまだ直していない箇所

`storage.md` が §4.1 の節構成で出来上がったら、次の参照を直す（番号は今の行の内容）。

| 今の記述 | 直す先 |
| --- | --- |
| §10.1 の手順 3「`StorageRoot` を作り、属性を検証する（storage.md §3）」 | §2 |
| §4・§6.2 の「長さが分からない空白…（storage.md §8）」（2 か所） | §9 |
| §14 の TST-GEN-02「`TemporaryDirectory` を使う（storage.md §13）」 | §12 |
| §12 の NS-A-08「`@unchecked Sendable`…は 2 か所」 | SQLite の接続に付けずに済めば 1 か所に |

`transcription.md`・`ui.md`・`export.md` への参照（§3.2・§6.1・§8.1・§8.2・§10.1 など）は、次の節番号を前提にしている。合わせて書くか、書いた後に直す: transcription §3（エンジン）・§5（ジョブと失敗の分類）・§9（バックグラウンド実行）、ui §4.6（設定）・§5（Store）・§6（3 点の文言）・§7（キーボード）・§8（Live Activity）、export §3（失敗と書き出し ID）。`recording.md` は transcription §4.2（Overlap）・§8.3（録音の開始による中止）・§11.2（実機の合成音声）、ui §4.1・§4.2・§5.2・§11 を参照している。

## 8. `dev-environment.md` の「仮」の確定（計画どおり、名前とパスは変えない）

- §6.3: 見出しと本文の「仮」を外す（`architecture.md` §2.4 で確定）
- §11.1・§11.2: Package のターゲットを 7 つ + テスト 5 つに直す（`architecture.md` §2.2）。`Package.swift` はまだないので文書だけ
- §13: 「名前は仮」を外す。`-uiTestConsent` の意味を決めて書く（案: `accepted` は初回説明を既読にするだけで、録音ごとの同意確認は省略しない。FR-CNS-01。`ui.md` で確定する）
- §14.1: `35F9.1`（システムの起動時間）の行と、コード片の該当の要素を外す（`ContinuousClock` を使うため）。**`scripts/check-project-settings.py` がこの申告を検査していないかを先に確かめる**
- §19・§20: 上の 3 点の状態を直す
- `.swiftlint.yml` と `tools/lint-fixtures/` は変えない見込み。変えた場合だけ `scripts/test-lint-rules.py` を通す

## 9. 守ること（検査で落ちやすい点）

- 各 `##` の節の最初の行は「満たす要件:」。ID は定義済みのものだけを書く（`verify-docs.py` が本文の `FR-…`・`TST-…`・`UD-…` などをすべて照合する）
- 「申し送り」の節には表を 1 つだけ置き、行数を基本設計の箇条書きの数に合わせる（recording 5・storage 6・transcription 5・ui 5・export 4）
- 各文書 2,000 行未満。コードフェンスには言語を書く
- 実ファイルがないので `<!-- file: -->` の印は付けない
- コード片では `print`・強制アンラップ・`try!`・`.documentDirectory`・通信の API を使わない。識別子は 3 文字以上、1 行 120 文字まで
- 新しい依存は 0 件のまま。必要に見えたら、書く前に止めてオーナーに確認する
