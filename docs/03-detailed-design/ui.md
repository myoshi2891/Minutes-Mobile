# MinutesMobile 詳細設計書 ── 画面

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版・実装前） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | FR-CNS-01・03、FR-REC-06、FR-INT-06、FR-SAV-05、FR-FIN-02、FR-STT-07、FR-TRV-01〜03、FR-MTG-01〜03、FR-SET-01〜03、NFR-PRF-04、NFR-CAP-02、NFR-UX-01〜04、NFR-L10N-01、SEC-APP-01、SEC-PLT-01・02・06・07、SEC-PRV-02・04 |
| 関係する制約 | CON-01・03・04 |
| 対応する基本設計 | [../02-basic-design/ui.md](../02-basic-design/ui.md)（設計決定 BD-UI-01〜10 を引用する） |
| 関連文書 | [architecture.md](architecture.md)・[recording.md](recording.md)・[storage.md](storage.md)・[transcription.md](transcription.md)・[export.md](export.md)・[dev-environment.md](dev-environment.md) |

本書は、画面ごとの配置と文言、Store の状態の型、問題の文言、プライバシー保護の UI（覆い・キーボード・コピー）、ロック画面の表示を定める。書き出しの画面（SC-07）は [export.md](export.md) にある。

**本書のコード片は、実装前に一時の Package でビルドとテストを通したものである**（§11.3）。実ファイルはまだないので、照合の印（`<!-- file: -->`）は付けていない。実装の作業で印を付ける（[README.md](README.md) §3）。

---

## 1. 位置づけと範囲

満たす要件: NFR-MNT-02・04、NFR-UX-04 ／ 決定: BD-UI-01・09、UD-10

| 本書で決めること | 節 | 置き場所（[architecture.md](architecture.md) §2） |
| --- | --- | --- |
| 画面の構成、用語の言い換え、文言の決まり | §2・§3 | アプリの `Features/`、String Catalog |
| 画面ごとの配置・文言・操作 | §4 | アプリの `Features/{画面}/` |
| Store の状態の型、サービスから受け取る値 | §5 | 値の型は `MinutesDomain/Interface/`、Store はアプリの `Features/` |
| 問題の種類ごとの重大度と 3 点の文言 | §6 | `MinutesDomain/Interface/`、String Catalog |
| 覆い、キーボードの拒否、コピー、入力の検証 | §7 | アプリの `App/`・`Clipboard/`、Domain |
| ロック画面の表示（Live Activity） | §8 | protocol は Domain、実装はアプリの `Platform/` とウィジェット拡張 |

基本設計の決定（BD-UI-01〜10）は変えていない。本書で細部を決めたことは次のとおりである。

| 事項（基本設計の申し送り・宿題） | 決定 | 節 |
| --- | --- | --- |
| 停止の確認の方法（確認か長押しか） | **確認のダイアログ**にする。長押しは VoiceOver と支援機能で操作しにくく、操作に気付きにくい | §4.3 |
| 覆いを出す条件 | シーンが「前面で操作中」でなくなる通知で出し、前面で操作中になった通知で外す。**専用のウィンドウ**で覆う（ルートの View への重ね描きでは、シートやアラートを隠せないため） | §7.1 |
| Live Activity の更新の頻度 | 状態が変わったときは直ちに。録音中は、累積サンプル数が前回の更新から 30 秒分（480,000 サンプル）進むたび（Chunk の確定・保存の成否によらない）。取り出しが起きない間（中断中など）は、注入した `Scheduler` で 30 秒ごとに同じ内容を更新して古くなる時刻だけを延ばす。古くなる時刻は最後の更新の 90 秒後 | §8 |
| 起動の引数 `-uiTestConsent` の意味 | `accepted` は初回説明を読了済みにするだけである。**録音ごとの同意確認は省略しない**（FR-CNS-01） | §11.2 |

---

## 2. 画面の構成

満たす要件: FR-CNS-01、FR-INT-06、FR-RCV-01、SEC-PLT-04、SEC-APP-01 ／ 決定: BD-UI-05・10

画面の一覧（SC-01〜SC-11）と遷移は、基本設計 §2・§3 のとおりである。

| 項目 | 設計 |
| --- | --- |
| ルート | `NavigationStack`。最初の画面は会議一覧（SC-02）。初回説明（SC-01）・同意確認（SC-03）・権限の案内（SC-09）・復旧の報告（SC-10）・設定（SC-08）・書き出し（SC-07）はシートで出す |
| 録音中（SC-04） | 全画面で重ねて出す（`fullScreenCover`）。下へ引いて閉じられない。「一覧へ」で閉じても録音は続き、一覧の上端に「録音中 0:00」の帯を常に出す（タップで SC-04 へ戻る） |
| 起動の順序 | ①起動の画面（アプリ名だけ）→ ②起動時の復旧（[storage.md](storage.md) §9）→ ③初回なら SC-01 → ④復旧した会議があれば SC-10 → ⑤SC-02。復旧が終わるまで、録音の開始を出さない（[architecture.md](architecture.md) §10.1） |
| 保護データを読めない間 | 起動の画面のまま「iPhone のロックを解除すると使えます」を出す（[storage.md](storage.md) §3.5） |
| 覆い（SC-11） | どの画面の上にも、専用のウィンドウで出す（§7.1） |
| 画面の向き | 縦だけ（BD-UI-10。プロジェクト設定で縦だけにする。[dev-environment.md](dev-environment.md) §12.1） |
| 外部からの起動 | 経路を作らない。Live Activity をタップするとアプリが開くだけで、値は受け取らない（SEC-PLT-04） |

---

## 3. 用語と文言の決まり

満たす要件: NFR-UX-04、NFR-L10N-01、SEC-LOG-04

| 決まり | 内容 |
| --- | --- |
| 文言の置き場所 | すべて String Catalog（`Localizable.xcstrings`）。コードには鍵だけを書く。基本の言語は日本語（NFR-L10N-01） |
| 鍵の形 | `{画面}.{要素}`（例: `recording.stop`）。問題の文言は `problem.{種類}.{what・safety・action}`（§6） |
| 差し込む値 | 整数（件数・分数・秒数）と、書式化した日時・長さだけ。**エラーの説明文・パス・本文を文に差し込まない** |
| 内部の用語 | 画面に出さない。下の表で言い換える |
| 日時と長さ | 開始日時は「2026/11/16 14:30」の形（端末のロケールの書式）。長さと経過時間は `ElapsedFormat.clock`（`0:05`・`12:34`・`1:02:03`） |

用語の言い換え（基本設計 §6 の申し送り）:

| 内部の用語 | 画面での言い方 |
| --- | --- |
| Chunk | 「録音データ」（件数で数える。必要なら「1 件は最長 30 秒」を添える） |
| 確定（finalize）・Finalization Barrier | 「録音の確定」「確定前の確認」 |
| 確定待ち（`stop_requested`） | 「確定待ち」（一覧）、「録音を確定できません」（理由があるとき） |
| 例外確定（`acknowledged_loss`） | 「欠けた部分を承知のうえで確定」。確定した会議の印は「一部欠落」 |
| ハッシュの不一致（`hash_mismatch`） | 「保存時と内容が一致しません」 |
| 欠番・ファイル欠損・不正なヘッダ | 「録音データが欠けています」「録音データを読み込めません」 |
| 隔離（quarantine） | 「復元できなかった録音」 |
| 未保存の Chunk | 「まだ保存できていない録音」 |
| 空白区間 | 「録音が中断していた時間」 |
| 言語アセット | 「音声認識モデル」 |
| ジョブ・実行番号・実行世代 | 出さない（進み具合は「30 件中 12 件」の形で出す） |
| 版 | 「文字起こし（N 回目）」 |
| 失敗区間 | 「文字起こしできなかった区間」 |
| Overlap・重複の除外 | 「重なりの整理」 |
| 保護クラス・バックアップ除外 | 「このアプリの中だけに保存」「バックアップに含まれません」 |
| 低信頼度 | 「聞き取りに自信がない部分」 |

---

## 4. 画面ごとの設計

満たす要件: FR-CNS-01・03、FR-REC-06、FR-SAV-05、FR-FIN-02、FR-STT-07、FR-TRV-01〜03、FR-MTG-01〜03、FR-SET-01〜03、FR-INT-06、FR-RCV-01・02、NFR-CAP-02、NFR-PRF-04、SEC-PRV-02・04

配置は上から順に書く。文言は初版で、実装の作業で String Catalog に入れるときに見直してよい（意味を変える場合は本書を直す）。

### 4.1 初回説明（SC-01）

5 ページを順に進める。各ページは見出し・本文・「次へ」で、最後のページだけ「はじめる」。途中で閉じられない。設定からの読み直しでは、いつでも閉じられる。

| ページ | 見出し | 本文 |
| --- | --- | --- |
| 1 | この iPhone の中だけで処理します | 録音と文字起こしは、この iPhone の中だけで行います。録音や文字起こしを外部へ送信しません。 |
| 2 | それでも残るリスク | iPhone の紛失・盗難に備えて、パスコードを設定してください。書き出したファイルは、このアプリの保護の外に出ます。保存先と共有先はご自身で管理してください。 |
| 3 | 録音の前に同意を得てください | 国や地域によっては、参加者全員の同意が必要です。録音を始めるたびに、同意を得たかを確認します。 |
| 4 | 録音はバックアップに含まれません | 録音は iPhone のバックアップに含まれません。**アプリを削除すると、録音はすべて消えます。** 残したい録音は「書き出し」で保存してください。 |
| 5 | 録音の中断をお知らせします | 着信などで録音が中断したとき、通知でお知らせします。通知を許可しなくても録音できます。 |

- 5 ページ目の「はじめる」で、通知の権限を要求する（[recording.md](recording.md) §7）。結果によらず、`SettingsStoring.markOnboardingCompleted()` を呼んで一覧へ進む。
- P2 で手動ノートを提供するときに、4 ページ目へ「手動ノートもアプリの削除で消える。残すには Markdown の書き出しが必要」を足す（FR-CNS-03）。

### 4.2 同意確認と録音の開始（SC-03）

| 順 | 要素 | 内容 |
| --- | --- | --- |
| 1 | 見出し | 「録音を始める前に」 |
| 2 | 問い | 「この会議の参加者に、録音の同意を得ましたか」 |
| 3 | タイトルの入力欄（任意） | プレースホルダは既定名（開始日時から作る。例「2026/11/16 14:30 の会議」）。入力は `MeetingTitle.sanitized` で検証する（§7.4） |
| 4 | 空き容量の表示 | 段階 1 なら「あと約 N 分録音できます」。段階 2 なら「空き容量がほとんどありません。録音を始めても、約 N 分で保存できなくなります。先に不要なデータを削除することをおすすめします」と、「空き容量が少ないまま録音する」のスイッチ（オンにしないと開始できない）。**問題 `storage_critical` の 3 点は使わない**（その「録音は無事か」は録音中の文で、開始の前には当てはまらない。§6） |
| 5 | 主ボタン | 「同意を得たので録音を始める」 |
| 6 | 副ボタン | 「やめる」 |

- 主ボタンを押したときにだけ、`ConsentStore` が `ConsentConfirmation.confirmedByUser(clock:)` を呼ぶ（**呼び出しはこの 1 か所だけ**。[recording.md](recording.md) §2）。得た値・タイトル・段階 2 の承知を `RecordingService.start` に渡す。
- 確認を省略する設定・「次回から表示しない」は作らない（FR-CNS-01）。
- 開始が失敗したら、問題（§6）を出してこの画面に留まる。マイクの権限がない場合は、権限の案内（SC-09）へ切り替える。
- **権限の案内（SC-09）**: 問題 `microphone_denied` の 3 点と、「設定を開く」（設定アプリの本アプリの画面を開く）・「閉じる」。

### 4.3 録音中（SC-04）

| 順 | 要素 | もとになる値 |
| --- | --- | --- |
| 1 | 最上位の問題（あれば）。重大度が最上位のものを 1 件、3 点で出す | `RecordingStore.topProblem`（§6） |
| 2 | 状態（アイコンと文字）: 「録音中」「中断中」「入力を切り替え中」「準備中」「停止処理中」 | `RecordingSnapshot.state` |
| 3 | 経過時間（大きな等幅の数字） | `RecordingStore.elapsedText`（§5.2） |
| 4 | 入力レベルの表示（棒）。「動きを減らす」の設定では、変化をゆるやかにする | `inputLevel` |
| 5 | 入力の種別: 「内蔵マイク」「有線のマイク」「Bluetooth」「USB」「その他」 | `route` |
| 6 | 「保存済み N 件」「あと約 N 分録音できます」 | `savedChunks`、残りの分数 |
| 7 | そのほかの問題（あれば、一覧で） | `RecordingStore.problems` |
| 8 | 「停止」（中断中は、その上に「再開」） | — |
| 9 | 「一覧へ」 | — |

- **停止は確認のダイアログを挟む**: 「録音を停止しますか」・「停止する」・「続ける」。「停止する」で `RecordingService.stop()` を呼ぶ。停止処理中は、ボタンを無効にして「保存しています」を出す。
- 停止が終わると、会議詳細（SC-05）へ進む。確定できなければ、そこから確定待ち（SC-06）へ進める。
- 中断中は、問題 `recording_interrupted` を最上位に出す（FR-INT-02）。「再開」は `RecordingService.resume()` を呼ぶ。
- 状態の表示は、色・アイコン・文字の 3 つで示す（NFR-UX-02）。状態が変わったら、VoiceOver に読み上げさせる（§9）。

### 4.4 会議一覧（SC-02）と会議詳細（SC-05）

**一覧:**

| 要素 | 内容 |
| --- | --- |
| 上端の帯 | 録音中だけ「録音中 0:00」（タップで SC-04 へ） |
| 行 | タイトル、開始日時、長さ、状態（`MeetingListStatus`: 「録音中」「確定待ち」「文字起こし待ち」「文字起こし中」「完了」）、印（「一部欠落」「読み込めません」） |
| 並び | 開始日時の新しい順。`LazyVStack` で遅延読み込みにする（NFR-PRF-04） |
| 行の操作 | タップで詳細へ。スワイプで「削除」（確認のダイアログ: 「この会議を削除しますか。録音と文字起こしは元に戻せません」・「削除する」・「やめる」）。録音中の会議は削除できない |
| 主ボタン | 「録音を始める」（SC-03 へ。録音中と復旧の完了前は無効） |
| ツールバー | 「設定」 |
| 空のとき | 「まだ会議がありません」「『録音を始める』で最初の録音を作れます」 |

**詳細:**

| 順 | 要素 | 内容 |
| --- | --- | --- |
| 1 | タイトル | 表示と「編集」。編集は入力欄のシートで行い、`MeetingTitle.sanitized` で検証する。タイトルの表示には、標準のテキスト選択を付けない（§7.3） |
| 2 | 状態の帯 | 会議の状態と、待っている理由（`TranscriptionWaitReason` の文言。下の表）。文字起こし中は「30 件中 12 件」と進み具合の棒。DB の作り直しで付けた保留（`rebuild_hold`。基本設計 [transcription.md](../02-basic-design/transcription.md) §4.3・§8）のある `transcribing` の会議では、待っている理由より先に、保留の理由「会議の一覧を作り直したため、文字起こしを止めています。続けるには『文字起こしを再開する』を押してください」を出す |
| 3 | 問題（あれば） | `merge_failed`・`transcript_version_rejected`・`finalization_blocked` などを 3 点で出す |
| 4 | 文字起こし | セグメントを、会議時刻（`AudioTimeline`）つきで並べる。`LazyVStack`（NFR-PRF-04）。空白区間・利用できなかった区間・文字起こしできなかった区間を、その位置に挟む |
| 5 | エンジンの情報 | 「音声認識: OS 標準 ／ ja_JP ／ iOS 27.2（27C100）」を常に出す（FR-STT-09） |
| 6 | 操作 | 「文字起こしを始める」「文字起こしを再開する」「再文字起こし」「書き出す」「重なりの整理を見る」「削除」。3 つの文字起こしの操作は別の操作として示す（下の「文字起こしの操作の区別」） |

| 待っている理由 | 文言 | 添える操作 |
| --- | --- | --- |
| `recording` | 「録音中のため、文字起こしを見合わせています」 | — |
| `asset_missing` | 「日本語の音声認識モデルが必要です」 | 「モデルを取得する」（§4.6 と同じ説明を出してから） |
| `unsupported_device`・`unsupported_locale` | 「この iPhone では文字起こしを使えません。録音と書き出しは使えます」 | — |
| `engine_unavailable` | 「いまは文字起こしを使えません」 | 「もう一度試す」 |
| `thermal` | 「iPhone が熱くなっているため、文字起こしを見合わせています」 | 「このまま実行する」 |
| `low_power` | 「低電力モードのため、文字起こしを見合わせています」 | 「このまま実行する」 |
| `auto_start_disabled` | 「文字起こしはまだ行っていません」 | 「文字起こしを始める」 |
| `engine_not_stopped` | 「前の文字起こしの処理が止まるのを待っています。録音は無事です」 | —（止まったら自動で再開する。[transcription.md](transcription.md) §5.2） |

| 文字起こしの中の要素 | 表示 |
| --- | --- |
| セグメント | 時刻（`12:34`）と本文。タップで、その位置から原音を再生する |
| 聞き取りに自信がない部分（信頼度がしきい値未満） | 本文に下線の点線と、行頭の印（「?」のアイコン）。色だけで区別しない（FR-STT-08、NFR-UX-02）。VoiceOver では「聞き取りに自信がない部分」と読む |
| 文字起こしできなかった区間（`failedChunks`） | 「この区間（12:00〜12:30）は文字起こしできませんでした」と「再試行」（FR-TRV-02） |
| 空白区間（`gaps`） | 「録音が中断していました（約 2 分）」と理由（「着信などの割り込み」「マイクの切り替え」「処理が追いつかなかった」）。長さが分からない場合は「長さは不明」（FR-TRV-03） |
| 利用できなかった区間（`unavailableChunks`） | 「この区間の録音は使えません（保存時と内容が一致しません）」など、ファイルの状態ごとの文 |

- **文字起こしの操作の区別**（基本設計 §4.4 の SC-05 の操作）: 「文字起こしを始める」は、一度も文字起こししていない `finalized` の会議から最初の実行を始める。「文字起こしを再開する」は、作り直しの保留（`rebuild_hold`）のある `transcribing` の会議**だけ**に出し、押すと保留を外し、同じ実行番号の不足したジョブを作ってから（基本設計 [transcription.md](../02-basic-design/transcription.md) §4.3）、開始条件（[transcription.md](transcription.md) §8.1）を評価して続きを実行する。新しい実行番号を作らず、新しい版も作らない。「再文字起こし」は、既存の版から新しい実行を作り、新しい版を作る。保留のない会議には「文字起こしを再開する」を出さない
- **再生**（FR-TRV-01）: セグメントの開始サンプル s から、`開始サンプル ≤ s < 開始サンプル + サンプル数` を満たす Chunk を選び、その中の `(s − 開始サンプル) ÷ 16,000` 秒の位置から再生する。Chunk の終わりで、次の連番の Chunk を続けて再生する（欠けていれば止める）。再生の前に、Chunk を検証つきで読む（[storage.md](storage.md) §6.1）。録音中は再生できない（ボタンを無効にする）。
- **言語アセットの取得**（FR-STT-07）: 「モデルを取得する」を押すと、「OS が音声認識モデルをダウンロードします。会議データは送信されません」と「ダウンロードする」「やめる」を出す。「ダウンロードする」でだけ `SpeechEngine.installAssets` を呼び、進み具合を出す（[transcription.md](transcription.md) §3.2）。
- **重なりの整理を見る**（FR-STT-03）: 版のファイルの `boundaries` と `excluded` を一覧にする。境界ごとに「重なりの整理: 1 件を除外」「重なりなし（前の録音データが欠けているため）」などと出し、除外したセグメントの本文と、残したセグメントを並べる。
- 使えない版（`corrupt`・`invalid`）のときは、問題 `transcript_version_rejected` を出し、破損していない以前の版があればそれを表示する（[transcription.md](transcription.md) §6.3）。

### 4.5 確定待ち（SC-06）

| 順 | 要素 | 内容 |
| --- | --- | --- |
| 1 | 問題 `finalization_blocked` の 3 点 | — |
| 2 | 確認中の表示 | 「録音を確認しています（120 件中 45 件）」と進み具合の棒（[storage.md](storage.md) §6.1） |
| 3 | 理由の一覧 | `FinalizationBlocker` ごとに 1 行（下の表）。範囲が分かる場合は、時刻（`12:00〜12:30`）を添える |
| 4 | 「再試行」 | 未保存の録音の保存をやり直してから、確認し直す（[storage.md](storage.md) §6.2） |
| 5 | 「未保存分を書き出す」（未保存があるときだけ） | 緊急書き出し（[export.md](export.md) §7） |
| 6 | 「欠けた部分を承知のうえで確定」 | 確認のダイアログへ |

| 理由（`FinalizationBlocker`） | 文言 |
| --- | --- |
| `missingSequences`・`missingFiles` | 「録音データが N 件欠けています」 |
| `invalidHeaders` | 「録音データ N 件を読み込めません」 |
| `quarantined` | 「復元できなかった録音が N 件あります」 |
| `hashMismatches` | 「録音データ N 件が、保存時と内容が一致しません」 |
| `unsavedInMemory` | 「まだ保存できていない録音が N 件あります。アプリが終了すると失われます」 |
| `lostUnsaved` | 「保存できないまま失われた録音が N 件あります」 |

例外確定の確認のダイアログ: 「欠けた部分は元に戻せません。欠けた部分（合計 約 N 分）を除いて確定しますか」・「確定する」・「やめる」。未保存の録音があるときは、「まだ保存できていない N 件は破棄されます」を加える。

### 4.6 設定（SC-08）

| 区分 | 項目 | 内容 |
| --- | --- | --- |
| 端末の対応状況 | 音声認識 | `EngineAvailability` の文言（「使えます」「音声認識モデルが必要です」「この iPhone では使えません」など）。モデルが必要なら「モデルを取得する」（§4.4 と同じ手順） |
| | 空き容量 | 「あと約 N 分録音できます」 |
| | 録音データの使用量 | 全体と、会議ごとの一覧（大きい順。NFR-CAP-02。[storage.md](storage.md) §10） |
| | Apple Intelligence | `SummaryModelAvailability` の文言（対応「使えます」・非対応「この iPhone では使えません」・無効「現在は使えません。設定アプリで Apple Intelligence を確認してください」）。どの値でも、別の行に「要約は今後の版で提供します」を出し、要約の操作は出さない（MVP。FR-SET-01） |
| 文字起こし | 「確定後に自動で文字起こしする」 | スイッチ。`SettingsStoring.autoTranscribe`（FR-SET-02。既定はオン） |
| データについて | 保存場所 | 「録音と文字起こしは、このアプリの中だけに保存します。『ファイル』アプリやパソコンからは見えません」 |
| | バックアップ | 「録音はバックアップに含まれません。アプリを削除すると、録音はすべて消えます。残したい録音は書き出してください」 |
| | 失う可能性のある音声 | 「アプリが強制終了したとき、直前の最長 30 秒の録音が失われることがあります」（NFR-REL-04 の上限。実測の後に見直す。FR-SET-03） |
| | ロック画面の表示 | Live Activity が使えない場合だけ: 「ロック画面では、録音中かどうかを OS の録音インジケータで確認してください」（NFR-UX-03 ②） |
| 説明 | 「はじめの説明を読む」 | SC-01 を開く |

サードパーティー製キーボードの解除・アプリロック（P2）・同意確認の省略の項目は置かない。

```swift
/// 設定値（注入点。D4 だけ）。本番は SettingsStore（UserDefaults）、テストは InMemorySettings
public protocol SettingsStoring: Sendable {
    /// 確定の後に、自動で文字起こしを始める（FR-SET-02。既定は true）
    var autoTranscribe: Bool { get }
    func setAutoTranscribe(_ enabled: Bool)
    /// 初回説明を最後まで読んだか
    var hasCompletedOnboarding: Bool { get }
    func markOnboardingCompleted()
}
```

- 本番の実装（`MinutesSystem/Settings/SettingsStore.swift`）だけが `UserDefaults` を使う（`no_direct_user_defaults` の例外。[architecture.md](architecture.md) §2.4）。鍵は `settings.autoTranscribe` と `settings.onboardingCompleted` の 2 つで、どちらも真偽値（D4）である。文字列を保存しない（SEC-DAT-07）。

Apple Intelligence の可否（FR-SET-01。申し送り）:

```swift
/// 要約に使う端末内モデルの可否（設定画面の 3 状態）
public enum SummaryModelAvailability: String, Sendable, CaseIterable {
    case supported
    case unsupported
    case disabled
}

/// 可否の問い合わせ（注入点）。本番は SystemSummaryModelAvailability、テストは FixedSummaryModelAvailability
public protocol SummaryModelAvailabilityProviding: Sendable {
    func current() -> SummaryModelAvailability
}
```

| 項目 | 設計 |
| --- | --- |
| 問い合わせの手段 | 本番の実装（アプリの `Platform/SystemSummaryModelAvailability.swift`）が FoundationModels の `SystemLanguageModel.default.availability` を読む（iOS 26 以降。iOS 27.2 SDK の `FoundationModels.swiftinterface` の宣言で確認。確認日 2026-11-27）。読むだけで、セッションを作らず、モデルを呼ばない（要約は P2）。オンデバイスのモデルだけを使い、`PrivateCloudComputeLanguageModel` は使わない（SEC-NET-02。`no_private_cloud_model`） |
| 3 状態への対応づけ | `.available` → `supported`。`.unavailable(.deviceNotEligible)` → `unsupported`。`.unavailable(.appleIntelligenceNotEnabled)`・`.unavailable(.modelNotReady)`（モデルの準備中）・未知の理由（`@unknown default`）→ `disabled`（端末は対応しうるが、今は使えない） |
| 読む時点 | 設定画面を開いたときと、前面に戻ったとき。値を保存しない |
| 試験 | 差し替え（`FixedSummaryModelAvailability`）に 3 つの値を与え、それぞれの文言になり、どの値でも「要約は今後の版で提供します」が別に出て要約の操作が出ないこと（基本設計 §12）。対応づけは、本番の実装の写像を純粋関数（`SummaryModelAvailability.init(_:)`）に分け、列挙の全件を表で確かめる。実際の端末での表示は実機で確かめる |

### 4.7 復旧の報告（SC-10）

起動時の復旧（[storage.md](storage.md) §9）の結果に、利用者へ知らせることがあるときだけ、1 回だけ出す。

| 条件 | 表示 |
| --- | --- |
| 録音の途中で終了した会議があった | 問題 `recovered_after_termination` の 3 点と、会議ごとに「保存できた録音 N 件」「確定しました」または「確定できませんでした（確認する）」 |
| 書きかけを救済した | 「保存の途中だった録音 N 件を復元しました」 |
| 書きかけを隔離した | 問題 `part_files_quarantined` の 3 点 |
| 位置の分からない録音を登録した（ハッシュの記録がない・読めない WAV。[storage.md](storage.md) §9 の照合 d） | 「位置の分からない録音 N 件があります。この会議は欠けた部分を承知のうえで確定できます」と「確認する」 |
| DB を作り直した | 復旧の結果の、作り直しの照合でハッシュが合わなかった Chunk の数と、ハッシュの記録を読めなかった Chunk の数（[storage.md](storage.md) §9）が**どちらも 0 のときだけ**、問題 `database_rebuilt`（「録音は無事です」）の 3 点。どちらかが 1 以上なら、「録音は無事」を出さず、問題 `database_rebuilt_damaged` の 3 点（N は 2 つの数の和） |
| 削除の続きを行った | 表示しない（利用者が求めた削除の完了である） |

「確認する」は確定待ち（SC-06）へ、「閉じる」は一覧へ進む。

---

## 5. Store と値の流れ

満たす要件: FR-REC-06、NFR-MNT-02、TST-GEN-05、INV-08 ／ 決定: BD-UI-01・02

### 5.1 規約

| 規約 | 内容 |
| --- | --- |
| Store | 画面ごとに 1 つ。`@Observable` のクラスで、MainActor（アプリターゲットの既定の分離）。View は Store の値を描くだけにする |
| 判断の置き場所 | 表示の判断（どの文を出すか、ボタンを有効にするか）は Store か、Domain の純粋関数に置く。View に `if` の条件式を書かない（Store の真偽値を読むだけにする） |
| I/O | Store は I/O を持たない。サービス（actor）へ要求を出し、値の列（`AsyncStream`）を受け取る |
| 古い応答 | Store は世代番号（`Generation`。[architecture.md](architecture.md) §3）を持ち、要求のたびに進める。応答・値の列には、要求したときの世代を添えて受け取り、**現在の世代と合わないものは捨てる**（TST-GEN-05） |
| 値の型 | サービスから受け取る値は、`Sendable` の値型だけ。`MinutesDomain/Interface/` に置く |
| 時間 | 画面のタイマーで時間を数えない。経過時間は、流れてきた値から導く（§5.2） |

| Store | 画面 | 状態の型（主な値） | 使うサービス |
| --- | --- | --- | --- |
| `RootStore` | 起動・SC-10・SC-11 | 起動の段（復旧中・準備完了・保護データ待ち）、復旧の結果、録音中か | `RecoveryService` |
| `OnboardingStore` | SC-01 | ページの番号 | `SettingsStoring` |
| `MeetingListStore` | SC-02 | 会議の行（ID・タイトル・開始日時・長さ・`MeetingListStatus`・印）の配列 | `MeetingRecordStoring`（[storage.md](storage.md) §4.3） |
| `ConsentStore` | SC-03・SC-09 | タイトルの入力、空き容量の段階、開始の進行中か、問題 | `RecordingService` |
| `RecordingStore` | SC-04 | `RecordingSnapshot`、問題の配列、残りの分数 | `RecordingService` |
| `MeetingDetailStore` | SC-05 | 会議の状態、待っている理由、版の内容（表示用に並べた行の配列）、進み具合、再生の位置 | `TranscriptionService`、再生 |
| `FinalizationStore` | SC-06 | 確認の進み具合、理由の配列、失われる範囲 | `FinalizationService` |
| `SettingsScreenStore` | SC-08 | エンジンの可否、空き容量、使用量、自動開始 | `SettingsStoring` ほか |
| `ExportStore` | SC-07 | [export.md](export.md) §3 | `ExportService` |

### 5.2 録音中の値（スナップショットの契約）

```swift
/// 録音パイプラインから画面へ流す値（BD-UI-02）。経過時間そのものは持たない
public struct RecordingSnapshot: Equatable, Sendable {
    public var state: RecordingSessionState
    /// 累積サンプル数（16 kHz）
    public var totalSamples: Int64
    /// 閉じた空白区間の長さの合計（ミリ秒）。開いている空白区間は含めない
    public var closedGapTotalMs: Int64
    /// 入力レベル（0〜1）
    public var inputLevel: Double
    public var savedChunks: Int
    public var unsavedChunks: Int
    public var route: InputRouteKind

    public init(
        state: RecordingSessionState, totalSamples: Int64, closedGapTotalMs: Int64, inputLevel: Double,
        savedChunks: Int, unsavedChunks: Int, route: InputRouteKind,
    ) {
        self.state = state
        self.totalSamples = totalSamples
        self.closedGapTotalMs = closedGapTotalMs
        self.inputLevel = inputLevel
        self.savedChunks = savedChunks
        self.unsavedChunks = unsavedChunks
        self.route = route
    }

    /// 経過時間（会議時刻）。この式以外から時間を作らない（INV-08）
    public var elapsedMs: Int64 {
        AudioTimeline.elapsedMs(totalSamples: totalSamples, closedGapTotalMs: closedGapTotalMs)
    }
}
```

```swift
/// 録音中の画面の Store。View は、この値を描くだけにする
@Observable
final class RecordingStore {
    private(set) var snapshot: RecordingSnapshot?
    private(set) var problems: [ProblemPresentation] = []
    private(set) var remainingMinutes: Int?
    private var generation = Generation()

    /// 経過時間の表示。流れてきた値だけから作る（画面のタイマーで数えない）
    var elapsedText: String {
        ElapsedFormat.clock(milliseconds: snapshot?.elapsedMs ?? 0)
    }

    /// 最上位に出す問題（重大度の高い順の先頭）
    var topProblem: ProblemPresentation? {
        problems.max { $0.severity < $1.severity }
    }

    /// 録音の開始のたびに呼ぶ。返した世代を、値の列の購読に渡す
    func beginSession() -> Generation {
        snapshot = nil
        problems = []
        return generation.advance()
    }

    /// 古い録音の値（世代が合わない）は捨てる（TST-GEN-05）
    func receive(_ value: RecordingSnapshot, for session: Generation) {
        guard session == generation else { return }
        snapshot = value
    }

    func receive(problems value: [ProblemPresentation], remainingMinutes minutes: Int?, for session: Generation) {
        guard session == generation else { return }
        problems = value
        remainingMinutes = minutes
    }
}
```

| 契約（BD-UI-02） | 内容 |
| --- | --- |
| 流す時点 | 状態が変わったときと、前回から 1,600 サンプル（100 ms 分）以上進んだとき。**サンプル数で間引く**（毎秒 10 回以下。[recording.md](recording.md) §9） |
| 経過時間 | `累積サンプル数 ÷ 16,000 + 閉じた空白区間の長さの合計`（`RecordingSnapshot.elapsedMs`）。Store は、この値以外から時間を作らない |
| 中断中 | 累積サンプル数も、閉じた空白区間の合計も増えないので、表示は止まる。再開して空白区間が閉じた時点で、その長さが加わる |
| 世代 | 録音の開始のたびに `beginSession()` で世代を進める。前の録音の値（遅れて届いたもの）は捨てる |
| 問題 | サービスが、`ProblemPresentation` の配列（重大度つき）を流す。Store は、重大度の高いものを最上位に出す |

時計を進めても、スナップショットが同じなら表示は変わらない（Store は時計を持たない）。

---

## 6. 問題の伝え方

満たす要件: NFR-UX-04、SEC-LOG-04 ／ 決定: BD-UI-09

```swift
/// 問題の重大度（基本設計 §6）。録音が止まっている・失われうる状態は critical にする
public enum ProblemSeverity: String, Sendable, CaseIterable, Comparable, LogSymbolSource {
    /// お知らせ
    case notice
    /// 警告
    case warning
    /// 最上位の警告
    case critical

    public static func < (lhs: ProblemSeverity, rhs: ProblemSeverity) -> Bool {
        lhs.order < rhs.order
    }

    private var order: Int {
        switch self {
        case .notice: 0
        case .warning: 1
        case .critical: 2
        }
    }
}

/// 利用者に見せる問題（BD-UI-09）。「何が起きたか・録音は無事か・すべきこと」の 3 点を必ず持つ。
/// 文言は String Catalog にあり、鍵は問題の種類から決まる。**文に差し込めるのは整数だけ**である
public struct ProblemPresentation: Equatable, Sendable {
    public let kind: ProblemKind
    public let severity: ProblemSeverity
    /// 文に差し込む数（件数・分数）。不要なら nil
    public let count: Int?

    public init(_ kind: ProblemKind, count: Int? = nil) {
        self.kind = kind
        severity = Self.severity(of: kind)
        self.count = count
    }

    /// String Catalog の鍵（例: "problem.chunk_unsaved.what"）
    public var whatKey: String {
        "problem.\(kind.rawValue).what"
    }

    public var safetyKey: String {
        "problem.\(kind.rawValue).safety"
    }

    public var actionKey: String {
        "problem.\(kind.rawValue).action"
    }

    /// default を書かない。問題の種類を足すと、重大度を決めるまでビルドが通らない
    public static func severity(of kind: ProblemKind) -> ProblemSeverity {
        switch kind {
        case .recordingInterrupted, .inputLost, .chunkUnsaved, .storageExhausted, .storageCritical: .critical
        case .microphoneCloseUnconfirmed: .critical
        case .microphoneDenied, .recordingStartFailed, .audioDropped, .storageLow, .finalizationBlocked: .warning
        case .unreadableMeeting, .partFilesQuarantined, .transcriptionSegmentFailed, .mergeFailed: .warning
        case .transcriptVersionRejected, .exportInsufficientSpace, .exportIntegrityWarning: .warning
        case .exportFailed, .deletionIncomplete, .engineUnavailable, .databaseRebuiltDamaged: .warning
        case .alreadyRecording, .recoveredAfterTermination, .databaseRebuilt, .transcriptionDeferred: .notice
        case .exportUnavailable, .notificationsDenied: .notice
        }
    }
}
```

- 問題は、種類（`ProblemKind`。28 種。[architecture.md](architecture.md) §8.2）と、差し込む数（あれば）だけで表す。**文字列を差し込めない**ので、エラーの説明文・パス・本文が画面に出る経路がない。
- 3 点の文言は、種類ごとに String Catalog に 1 組だけ置く（鍵は `problem.{種類}.what`・`.safety`・`.action`）。画面ごとに文を組み立てない。
- 重大度は 3 段階で、色・アイコン・文字（「お知らせ」「警告」「重要」）の 3 つで示す。録音が止まっている・失われうる状態（6 種）は最上位にする。
- `severity(of:)` は `default` を書かない。問題の種類を足すと、重大度を決めるまでビルドが通らない。String Catalog に 3 つの鍵が揃っていることは、単体テストで確かめる（§11.1）。

文言（初版。N は差し込む数）:

| 種類 | 重大度 | 何が起きたか | 録音は無事か | すべきこと |
| --- | --- | --- | --- | --- |
| `microphone_denied` | 警告 | マイクの使用が許可されていません | 録音は始まっていません | 「設定を開く」から、マイクを許可してください |
| `already_recording` | お知らせ | すでに録音中です | 進行中の録音は続いています | 新しく録音するには、先に今の録音を停止してください |
| `recording_start_failed` | 警告 | 録音を開始できませんでした | 録音は始まっていません。保存済みの会議に影響はありません | もう一度お試しください。繰り返す場合は、iPhone を再起動してください |
| `recording_interrupted` | 重要 | 録音が止まっています | 中断の前までの音声は、保存済みか、保存を続けています。保存できていない分がある場合、その分はアプリが終了すると失われます。今は録音できていません | 「再開」を押すか、録音を停止してください |
| `input_lost` | 重要 | 使えるマイクがなくなったため、録音を停止しました | 停止までの録音は保存されています | マイクの接続を確かめて、新しい録音を始めてください |
| `microphone_close_unconfirmed` | 重要 | マイクを閉じたことを確認できていません | 保存済みの録音は無事です。新しい音声は録音に入れていません | しばらく待っても消えない場合は、アプリを終了してください（終了するとマイクは閉じます） |
| `audio_dropped` | 警告 | 録音の一部（約 N 秒）を記録できませんでした | 録音は続いています。記録できなかった部分は、空白として残ります | 操作は不要です。繰り返す場合は、ほかのアプリを閉じてください |
| `storage_low` | 警告 | 空き容量が少なくなっています | 録音は続いています。あと約 N 分録音できます | 不要なデータを削除してください |
| `storage_critical` | 重要 | 空き容量がほとんどありません | 録音は続いています。あと約 N 分で保存できなくなります | すぐに不要なデータを削除するか、録音を停止してください |
| `chunk_unsaved` | 重要 | 録音を保存できていません | 録音は続いていますが、直近の N 件（1 件は最長 30 秒）はまだ保存できていません。アプリが終了すると、この分は失われます | 不要なデータを削除して、空き容量を作ってください。アプリを終了しないでください |
| `storage_exhausted` | 重要 | 保存できない状態が続いたため、録音を停止しました | 保存済みの録音は無事です。未保存の N 件は、アプリが終了すると失われます | 空き容量を作ってから「再試行」を押してください。すぐに空けられない場合は「未保存分を書き出す」で退避できます |
| `finalization_blocked` | 警告 | 録音を確定できません | 録音の一部（N か所）が欠けているか、保存時と一致しません。残りの録音は無事です | 内容を確かめて「再試行」するか、欠けた部分を承知のうえで確定してください |
| `unreadable_meeting` | 警告 | この会議の情報を読み込めません | 録音のファイルは、削除されずに残っています | 操作は不要です。不要であれば、この会議を削除できます |
| `recovered_after_termination` | お知らせ | 前回、録音の途中でアプリが終了しました | 保存済みの録音は無事です。終了の直前の録音は、失われた可能性があります | 会議を開いて、内容を確かめてください |
| `part_files_quarantined` | 警告 | 録音の一部（N 件）を復元できませんでした | 復元できなかった分は、文字起こしに使えません。ファイルは削除せずに残しています | 会議を開いて、欠けた部分を確かめてください |
| `database_rebuilt` | お知らせ | 会議の一覧を作り直しました | 録音は無事です。録音のファイルから一覧を復元しました | 操作は不要です。文字起こしの途中だった会議は、会議を開いて「文字起こしを再開する」を押してください |
| `database_rebuilt_damaged` | 警告 | 会議の一覧を作り直しました | 作り直しで、保存時と内容が一致しない録音か、確かめられない録音（合わせて N 件）が見つかりました。残りの録音は無事です | 会議を開いて、使えない部分を確かめてください。文字起こしの途中だった会議は、「文字起こしを再開する」を押してください |
| `engine_unavailable` | 警告 | いま、文字起こしを使えません | 録音は無事です。録音と書き出しは使えます | 「設定」で対応状況を確かめてください |
| `transcription_deferred` | お知らせ | 文字起こしを一時的に見合わせています | 録音は無事です | 条件が戻ると自動で再開します。すぐに実行することもできます |
| `transcription_segment_failed` | 警告 | 一部の区間（N か所）を文字起こしできませんでした | 録音は無事です。ほかの区間の文字起こしは表示しています | その区間の「再試行」を押してください |
| `merge_failed` | 警告 | 文字起こしの結果をまとめられませんでした | 録音は無事です。以前の文字起こしがあれば、それを表示しています | 「再試行」を押してください |
| `transcript_version_rejected` | 警告 | この文字起こしは、保存時と内容が一致しないため表示できません | 録音は無事です | 「再文字起こし」で作り直してください |
| `export_insufficient_space` | 警告 | 書き出しに必要な空き容量が足りません | 録音は無事です | 不要なデータを削除してから、もう一度お試しください |
| `export_integrity_warning` | 警告 | 録音の一部（N 件）が、保存時と内容が一致しません | 一致しない部分は、内容が変わっている可能性があります。残りは無事です | このまま書き出すか、やめるかを選んでください |
| `export_unavailable` | お知らせ | いまは書き出せません | 録音は無事です | 録音の停止と確定の後に、もう一度お試しください |
| `export_failed` | 警告 | 書き出せませんでした | 録音は無事です。アプリの中のデータは変わっていません | もう一度お試しください |
| `deletion_incomplete` | 警告 | 削除を最後まで完了できませんでした | ほかの会議の録音は無事です。この会議は一覧から消えています | 次にアプリを開いたときに、残りを自動で削除します |
| `notifications_denied` | お知らせ | 通知が許可されていません | 録音に影響はありません | 録音の中断を通知で知りたい場合は、「設定」アプリで通知を許可してください |

- `storage_critical` は録音中に出す。録音の開始の前（同意確認の画面の段階 2）には、この 3 点を使わず §4.2 の文を使う（「録音は続いています」が当てはまらないため）。

---

## 7. プライバシー保護の UI

満たす要件: SEC-APP-01、SEC-PLT-01・02・06、SEC-INP-01 ／ 決定: BD-UI-05〜07

### 7.1 覆い（SC-11）

```swift
/// 前面で操作中でなくなったら、専用のウィンドウで全画面を覆う。
/// ルートの View の上に出るシート・アラート・共有シートも隠すため、ウィンドウの重なりの最上位に置く
final class PrivacyCoverController {
    private var coverWindow: UIWindow?

    /// シーンが「前面で操作中」でなくなる通知（UIScene.willDeactivateNotification）で呼ぶ
    func cover(_ scene: UIWindowScene, isRecording: Bool) {
        guard coverWindow == nil else { return }
        let window = UIWindow(windowScene: scene)
        window.windowLevel = .alert + 1
        window.rootViewController = UIHostingController(rootView: PrivacyCoverView(isRecording: isRecording))
        window.isHidden = false
        coverWindow = window
    }

    /// シーンが前面で操作中になった通知（UIScene.didActivateNotification）で呼ぶ
    func uncover() {
        coverWindow?.isHidden = true
        coverWindow = nil
    }
}

/// 覆いに出すのは、アプリ名と録音の状態だけ（会議タイトル・本文を出さない）
struct PrivacyCoverView: View {
    let isRecording: Bool

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: isRecording ? "record.circle" : "lock")
                .font(.largeTitle)
                .accessibilityHidden(true)
            Text("cover.app_name")
                .font(.title2)
            Text(isRecording ? "cover.recording" : "cover.idle")
                .font(.body)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemBackground))
    }
}
```

| 項目 | 設計 |
| --- | --- |
| 出す条件（申し送り） | シーンが「前面で操作中」でなくなる通知（`UIScene.willDeactivateNotification`）で出す。アプリ切り替え画面・コントロールセンター・通知センター・着信・システムの確認（権限の要求など）のどれでも出る |
| 外す条件 | シーンが前面で操作中になった通知（`UIScene.didActivateNotification`） |
| 方式 | **専用のウィンドウ**（重なりの順はアラートより上）を、覆いの間だけ作る。ルートの View に重ねる方式では、シート・アラート・共有シートを隠せないため、採らない |
| 出す内容 | アプリ名と、録音の状態（「録音中です」「録音していません」）だけ。会議タイトル・本文・経過時間を出さない |
| 購読する場所 | `RootStore` が 2 つの通知を購読し、`PrivacyCoverController` を呼ぶ。画面ごとに実装しない（BD-UI-05） |
| P2 のアプリロック | この覆いの上に、認証を足して実現する（本書では扱わない） |

### 7.2 キーボード

```swift
/// アプリのデリゲート。サードパーティー製キーボードをアプリ全体で拒否する（解除の設定は作らない）
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _: UIApplication, shouldAllowExtensionPointIdentifier identifier: UIApplication.ExtensionPointIdentifier,
    ) -> Bool {
        KeyboardPolicy.allows(identifier)
    }
}

enum KeyboardPolicy {
    /// キーボードの拡張機能だけを拒否する
    nonisolated static func allows(_ identifier: UIApplication.ExtensionPointIdentifier) -> Bool {
        identifier != .keyboard
    }
}
```

- アプリのデリゲートが、拡張機能の種類ごとに許可するかを答える。**キーボードの拡張機能（`.keyboard`）だけを拒否する。** アプリ全体に効くので、入力欄ごとの設定は要らない（BD-UI-06）。
- SwiftUI のアプリでは、`@UIApplicationDelegateAdaptor` でこのデリゲートをつなぐ。デリゲートの役目は、これと継続処理タスクの登録だけである（[architecture.md](architecture.md) §10.1）。
- OS 標準のキーボード（日本語入力を含む）は、そのまま使える。解除の設定は作らない（SEC-PLT-06）。P2 の入力欄にも、自動的に適用される（SEC-PLT-03）。
- 判定は純粋関数（`KeyboardPolicy.allows`）に切り出し、単体テストで確かめる（TST-SEC-10 の単体の部分）。

### 7.3 コピーと共有

```swift
/// UIPasteboard を直接使ってよいのはこのファイルだけ（.swiftlint.yml の no_direct_pasteboard の例外）
enum ClipboardWriter {
    /// 有効期限（秒）
    nonisolated static let lifetimeSeconds: TimeInterval = 300

    /// 端末内限定（ほかの端末へ同期しない）と有効期限を付けてコピーする
    static func copy(_ text: String, clock: some WallClock) {
        UIPasteboard.general.setItems([[UTType.utf8PlainText.identifier: text]], options: options(now: clock.now()))
    }

    nonisolated static func options(now: Date) -> [UIPasteboard.OptionsKey: Any] {
        [.localOnly: true, .expirationDate: now.addingTimeInterval(lifetimeSeconds)]
    }
}
```

| 操作 | 設計 |
| --- | --- |
| 文字起こし・会議タイトルのコピー | セグメントの長押しのメニューと、詳細の「コピー」からだけ行う。`ClipboardWriter.copy` を通し、端末内限定（`localOnly`）と有効期限（5 分）を付ける（SEC-PLT-01） |
| コピーの型の置き場所 | `MinutesMobile/Clipboard/ClipboardWriter.swift`（`no_direct_pasteboard` の例外の場所。[architecture.md](architecture.md) §2.4）。ほかの場所で `UIPasteboard` を使うと、静的解析が失敗する（一時の Package のコード片で、規則が検出することを確かめた） |
| 有効期限の時刻 | 注入した壁時計（`WallClock`）から作る |
| 標準のテキスト選択 | D2 と会議タイトルの表示に、`.textSelection` を付けない（静的解析の `no_direct_pasteboard` が検出する） |
| ドラッグ・アンド・ドロップ | D2 を表示する部品に、`.draggable`・`.onDrag` を付けない |
| 共有 | 書き出しの操作からだけ（[export.md](export.md) §3）。`ShareLink` は使わない（静的解析の `no_share_link`） |
| Spotlight・Siri | `NSUserActivity`・App Intents・Core Spotlight を使わない（静的解析の `no_spotlight_user_activity`。SEC-PLT-02） |

タイトルの入力欄で、OS 標準の編集メニューからコピーした場合は、この制御の外になる（基本設計 §7.3 の残る危険）。

### 7.4 入力の検証

```swift
/// 会議タイトルの検証（SEC-INP-01）。タイトルはパスに使わない
public enum MeetingTitle {
    public static let maxLength = MeetingDocument.maxTitleLength

    /// 改行・制御文字を除き、前後の空白を落として、上限の長さで切る
    public static func sanitized(_ input: String) -> String {
        let scalars = input.unicodeScalars.filter { scalar in
            scalar.properties.generalCategory != .control && !CharacterSet.newlines.contains(scalar)
        }
        let trimmed = String(String.UnicodeScalarView(scalars)).trimmingCharacters(in: .whitespaces)
        return String(trimmed.prefix(maxLength))
    }
}
```

- アプリの入力欄は、会議タイトルの 1 つだけである（MVP）。入力のたびに `sanitized` を通し、保存する前にもう一度通す。
- 空のタイトルは、既定名（開始日時から作る）にする（FR-MTG-02）。
- タイトルは、パスの組み立てに使わない（[storage.md](storage.md) §2.1）。書き出しのファイル名に使うときの無害化は [export.md](export.md) §5 にある。`../` を含むタイトルも、そのまま表示用の文字列として保存してよい（パスにならないため）。

---

## 8. ロック画面の表示

満たす要件: NFR-UX-03、SEC-PLT-07、SEC-PRV-02 ／ 決定: BD-UI-03・04

```swift
/// ロック画面とダイナミックアイランドに出す状態（NFR-UX-03）
public enum LockScreenStatus: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case recording
    case interrupted
}

/// ロック画面に出す値。**状態と経過時間だけ**で、文字列の欄がない（会議タイトル・本文を渡せない。SEC-PLT-07）
public struct LockScreenContent: Equatable, Sendable {
    public let status: LockScreenStatus
    /// この値を作った時点の経過時間（秒）
    public let elapsedSeconds: Int

    public init(status: LockScreenStatus, elapsedSeconds: Int) {
        self.status = status
        self.elapsedSeconds = elapsedSeconds
    }

    /// 録音セッションの状態から決める。録音していない状態（idle・preparing・stopping）では nil
    public init?(snapshot: RecordingSnapshot) {
        switch snapshot.state {
        case .capturing, .rebuilding: status = .recording
        case .interrupted: status = .interrupted
        case .idle, .preparing, .stopping: return nil
        }
        elapsedSeconds = Int(snapshot.elapsedMs / 1000)
    }
}

/// 録音の状態表示（注入点）。本番は LiveActivityPresenter、テストは StatusPresenterSpy
public protocol RecordingStatusPresenting: Sendable {
    /// 表示を始める。使えなければ（Live Activity が無効・未対応）false
    func start(_ content: LockScreenContent) async -> Bool
    func update(_ content: LockScreenContent) async
    func end() async
}
```

本番の実装（アプリの `Platform/LiveActivityPresenter.swift`。iOS 27.2 SDK に対してビルドできた）:

```swift
/// Live Activity の属性。ウィジェット拡張のターゲットと共有する。固定の属性は持たない
nonisolated struct RecordingActivityAttributes: ActivityAttributes {
    /// 表示する値は状態と時間だけ（会議タイトル・本文の欄を作らない。SEC-PLT-07）
    struct ContentState: Codable, Hashable {
        var status: LockScreenStatus
        /// 中断中に出す、止まった経過時間（秒）
        var elapsedSeconds: Int
        /// 録音中に OS の時間表示の起点にする時刻（現在の時刻 − 経過時間）。中断中は nil
        var timerStart: Date?
    }
}

/// Live Activity の表示（RecordingStatusPresenting の本番の実装）。
/// Activity は Sendable ではないので保持しない。更新と終了は、そのつど OS から一覧を取り直して行う
nonisolated struct LiveActivityPresenter: RecordingStatusPresenting {
    /// 最後の更新から古くなるまでの秒数（初期値。TST-DEV-12 で見直す）
    static let staleAfterSeconds: TimeInterval = 90

    private let clock: any WallClock

    init(clock: any WallClock) {
        self.clock = clock
    }

    func start(_ content: LockScreenContent) async -> Bool {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return false }
        // 前のプロセスが残した表示があれば、先に終わらせる
        await end()
        do {
            // pushType は nil（Push を使わない。アプリ自身が開始・更新する）
            _ = try Activity.request(
                attributes: RecordingActivityAttributes(), content: makeContent(content), pushType: nil,
            )
            return true
        } catch {
            // 使えない（Personal Team で動かないなど）。アプリ内の表示と OS のインジケータで代える
            return false
        }
    }

    func update(_ content: LockScreenContent) async {
        for activity in Activity<RecordingActivityAttributes>.activities {
            await activity.update(makeContent(content))
        }
    }

    func end() async {
        for activity in Activity<RecordingActivityAttributes>.activities {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }

    private func makeContent(
        _ content: LockScreenContent,
    ) -> ActivityContent<RecordingActivityAttributes.ContentState> {
        let elapsed = TimeInterval(content.elapsedSeconds)
        let start = content.status == .recording ? clock.now().addingTimeInterval(-elapsed) : nil
        let state = RecordingActivityAttributes.ContentState(
            status: content.status, elapsedSeconds: content.elapsedSeconds, timerStart: start,
        )
        // 古くなる時刻: 最後の更新の 90 秒後（通常の更新の間隔 30 秒より長い余裕。基本設計 §8）
        return ActivityContent(state: state, staleDate: clock.now().addingTimeInterval(Self.staleAfterSeconds))
    }
}
```

| 項目 | 設計 |
| --- | --- |
| 状態の型（申し送り） | `LockScreenContent`: 状態（`recording`・`interrupted`）と経過時間（秒）だけ。**文字列の欄がない**ので、会議タイトル・本文を渡せない（SEC-PLT-07） |
| Live Activity の値 | `RecordingActivityAttributes.ContentState`: 状態、止まった経過時間、OS の時間表示の起点の時刻（録音中だけ）。固定の属性は持たない |
| 始める・終える | 録音の状態が `capturing` になったら `start`、`idle`・`stopping` になったら `end`。Push は使わない（`pushType` は `nil`） |
| 更新の頻度（申し送り） | 契機は録音パイプラインが届けるスナップショット（`RecordingSnapshot`。§5.2）である。**状態が変わったとき**（録音中 ↔ 中断中）は直ちに更新する。録音中は、**累積サンプル数が前回の更新から 30 秒分（480,000 サンプル。初期値）進むたび**に更新し、時間表示の起点をスナップショットの値に合わせ直す。**Chunk の確定・保存の成否によらない**（保存が遅れている・失敗している段階 3 でも、録音が続いていれば更新する）。取り出しが起きない状態（中断中など）では、アプリが動ける間、注入した `Scheduler` で 30 秒ごとに同じ内容を更新し、古くなる時刻だけを延ばす（タイマーの値から経過時間を作らない。INV-08）。更新は保存の actor の I/O を待たない |
| 古くなる時刻 | 開始と更新のたびに、`staleDate` を最後の更新の 90 秒後にする（`staleAfterSeconds`。初期値で、TST-DEV-12 で見直す）。中断中の静止した表示にも付ける。アプリが更新できない場合（サスペンド・終了・クラッシュ）にだけ古くなる |
| 古くなった表示 | ウィジェットの `isStale` が真のとき、中断中以外の状態（録音中）なら、経過時間を出さず、「状態を更新できていません。アプリを開いて確認してください」の固定の文だけを出す。中断中なら、「中断中」の状態と中断した時点で止めた経過時間を残したまま、同じ文を加える（基本設計 §8、BD-UI-04） |
| 経過時間の見せ方 | 録音中は、OS の時間表示（起点の時刻からの経過を、OS が自分で進める）を使う。中断したら、直ちに「中断中」と止まった時間の表示に更新する |
| 起点の時刻 | 「現在の壁時計の時刻 − 経過時間」。表示のためだけに使う（録音の位置には使わない。INV-08）。壁時計が変わっても、次の更新で合わせ直される |
| 使えない場合 | `start` が `false` を返す（Live Activity が無効・Personal Team で動かないなど）。アプリ内の表示と OS の録音インジケータで代え、設定画面にその旨を出す（§4.6。NFR-UX-03 ②） |
| 残った表示の片付け | `start` の前と、起動時の復旧の後に `end()` を呼ぶ（前のプロセスが残した表示を消す）。`Activity` は `Sendable` ではないので保持せず、そのつど OS から一覧を取り直す |
| 停止の操作 | Live Activity からは提供しない（アプリを開いて停止する） |

ウィジェット拡張（Live Activity の見た目を置くターゲット）:

| 項目 | 設計 |
| --- | --- |
| ターゲット | `MinutesMobileWidgets`（ウィジェット拡張）。`RecordingActivityAttributes` を、アプリと共有する 1 ファイルに置く |
| ロック画面 | アイコン、状態（「録音中」「中断中」）、経過時間。中断中は、色とアイコンを変え、「アプリを開くと再開を試みます」を添える。古くなった表示は上の「古くなった表示」の行に従う |
| ダイナミックアイランド | 小さい表示は、アイコンと経過時間。広げた表示は、ロック画面と同じ内容 |
| 設定 | アプリの Info.plist に `NSSupportsLiveActivities`（真）。拡張の Bundle Identifier は、アプリの Bundle Identifier に `.widgets` を付けたもの（CON-09 の確定の後に決まる） |
| 実装の順序 | 画面の実装の最後。Live Activity がなくても、ほかの機能が完結する（`RecordingStatusPresenting` の差し替えで動く） |
| 通信・依存 | 拡張はネットワークを使わず、依存を持たない（SEC-NET-01。静的解析と設定の検査の対象に含める） |

ロック画面に出るそのほかの表示: 中断の通知は [recording.md](recording.md) §7、継続処理タスクの表示は [transcription.md](transcription.md) §9 にある。どれも固定の文言で、差し込む値を持たない。

---

## 9. アクセシビリティとローカライズ

満たす要件: NFR-UX-01・02、NFR-L10N-01

| 観点 | 設計 | 確かめ方 |
| --- | --- | --- |
| Dynamic Type | すべての文字に、テキストスタイル（`.body` など）を使う。固定の高さを指定しない。経過時間の数字も拡大に追従する。最大の文字サイズで、「録音を始める」「停止」が画面の中に見える（画面はスクロールできる） | UI テスト（最大の文字サイズ） |
| VoiceOver の名前 | 「録音を始める」「録音を停止」「録音を再開」を、それぞれ一意のアクセシビリティの名前にする。アイコンだけのボタンを作らない | UI テスト（名前が一意であること） |
| 状態の読み上げ | 録音の状態が変わったとき（録音中・中断中・停止）に、読み上げを 1 回出す。入力レベルの変化と、経過時間の毎秒の変化は読み上げない | 単体（Store が、状態の変化でだけ読み上げの要求を出すこと） |
| 色に頼らない | 状態・重大度・聞き取りに自信がない部分は、アイコン・文字・下線でも示す | レビュー、UI テスト |
| ダークモード | 色は、システムの色（`systemBackground` など）と、アセットの両配色の色だけを使う | 両方の配色で確認する |
| 動きを減らす | 設定が有効なら、入力レベルの棒の変化をゆるやかにし、ほかの動きを付けない | レビュー |
| 文言 | String Catalog。コードに文言を直接書かない（§3） | 単体（鍵の存在）、レビュー |
| 識別子 | UI テストが使う要素には、アクセシビリティの識別子（`recording.stop` など。文言の鍵と同じ形）を付ける。文言の変更で試験が壊れないようにする | — |

---

## 10. セキュリティ

満たす要件: SEC-APP-01、SEC-PLT-01・02・04〜07、SEC-PRV-02・04、SEC-INP-01、SEC-LOG-04 ／ 脅威: T-04・05・06

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| スナップショット | 専用のウィンドウの覆い。どの画面でも同じ仕組み（§7.1） | SEC-APP-01、T-05 |
| キーボード | アプリ全体で拒否、解除なし（§7.2） | SEC-PLT-06、T-04 |
| コピー | 専用の操作・端末内限定・有効期限。標準のテキスト選択を使わない（§7.3） | SEC-PLT-01、T-04 |
| Spotlight・Siri | 渡さない（§7.3） | SEC-PLT-02 |
| ロック画面 | 状態と経過時間だけ。型に文字列の欄がない（§8） | SEC-PLT-07、T-04・05 |
| 外部からの起動 | 経路を作らない（§2） | SEC-PLT-04 |
| WebView | 使わない（静的解析の `no_webkit`） | SEC-PLT-05 |
| 同意と録音の明示 | 同意確認の値がないと開始できない。録音中は、帯・SC-04・ロック画面で示す（§4.2・§4.3・§8） | SEC-PRV-02、T-06 |
| 初回説明 | 保存場所・バックアップ除外・書き出しの責任・同意（§4.1） | SEC-PRV-04 |
| 問題の文言 | 種類と整数だけから作る。エラーの説明文を出さない（§6） | SEC-LOG-04 |
| 入力 | タイトルを検証する。パスに使わない（§7.4） | SEC-INP-01 |

**実装・レビュー時の注意点**（基本設計 [architecture.md](../02-basic-design/architecture.md) §12.2 の NS-06・10・12 と、基本設計 §11 の注意点を、本書の型の単位に具体化したもの）:

| No. | 注意点 | 確かめ方 |
| --- | --- | --- |
| NS-U-01 | `ConsentConfirmation.confirmedByUser` を呼ぶのは、`ConsentStore` の「同意を得たので録音を始める」の処理の 1 か所だけにする。UI テストの差し替えでも呼ばない | `grep` で数える。UI テスト（確認しないと始まらない） |
| NS-U-02 | 新しい画面・シート・アラートを足しても、覆いの仕組み（専用のウィンドウ）を変えない。覆いより上の重なりの順のウィンドウを作らない | レビュー。UI テスト（シートを出した状態で覆いが出ること） |
| NS-U-03 | D2 と会議タイトルを表示する部品に、`.textSelection`・`.draggable`・`.onDrag`・`ShareLink` を付けない | 静的解析（`no_direct_pasteboard`・`no_share_link`）、レビュー |
| NS-U-04 | `LockScreenContent`・`RecordingActivityAttributes`・`ProblemPresentation` に、文字列の欄を足さない | レビュー。単体（値の型をたどって、文字列がないこと） |
| NS-U-05 | 画面に出す文に、エラーの説明文（`localizedDescription`）・パス・`String(describing:)` の結果を使わない。文は String Catalog の鍵から作る | レビュー |
| NS-U-06 | アクセシビリティの読み上げ用の文（`accessibilityLabel`）にも、同じ決まりを適用する。ロック画面の表示の読み上げに、タイトルを入れない | レビュー |
| NS-U-07 | 同意確認の省略・キーボードの解除・コピーの制限の解除の設定を足さない | レビュー。UI テスト（設定に項目がないこと） |
| NS-U-08 | `UserDefaults` に保存するのは、2 つの真偽値だけにする。タイトル・会議 ID・日時を保存しない | 静的解析（`no_direct_user_defaults`）、レビュー |
| NS-U-09 | 画面のタイマー（`Timer`・`TimelineView` の周期）で、録音の時間を数えない | 静的解析（`no_direct_sleep_timer`）、単体（時計を進めても表示が変わらない） |
| NS-U-10 | ウィジェット拡張に、会議のデータを読むコードを置かない（拡張が受け取るのは `ContentState` だけ） | レビュー |
| NS-U-11 | 言語アセットの取得の説明（「会議データは送信されません」）を出さずに、`installAssets` を呼ぶ経路を作らない | UI テスト、レビュー |

---

## 11. テスト観点

満たす要件: FR-CNS-01、FR-FIN-02、NFR-UX-01・04、TST-INT-06、TST-SEC-08〜10、TST-DEV-12、TST-GEN-05、TST-CI-08

### 11.1 先に書いて失敗させるテスト（画面の実装の順）

| 順 | テスト | 確かめること | 要件 |
| --- | --- | --- | --- |
| 1 | 問題の網羅 | 28 種のすべてが、重大度と 3 つの鍵を持つ。String Catalog に 84 の鍵がすべてあり、空でない。`microphone_close_unconfirmed` が最上位になる | NFR-UX-04 |
| 2 | 経過時間 | スナップショットの値から式どおりになる。中断中に変わらない。書式（`0:05`・`1:02:03`） | FR-REC-06 |
| 3 | ロック画面の値 | 状態の写像。録音していない状態では `nil`。値の型に文字列がない | SEC-PLT-07 |
| 4 | タイトルの検証 | 改行・制御文字の除去、100 文字、空白だけ | TST-SEC-06 |
| 5 | 一覧の状態 | 会議の状態からの写像 | FR-MTG-01 |
| 6 | キーボードの判定 | `.keyboard` を拒否し、ほかを許可する | TST-SEC-10 |
| 7 | コピーの指定 | `ClipboardWriter.options` が、端末内限定と、現在の時刻 + 300 秒の有効期限を返す | TST-SEC-08 |
| 8 | 録音中の Store | 値を流して表示の値を確かめる。古い世代の値が捨てられる。最上位の問題が、重大度の高い順で選ばれる | TST-GEN-05 |
| 9 | 同意確認の Store | 主ボタンの処理でだけ、同意確認の値が作られる。段階 2 では、承知のスイッチがないと開始できない | FR-CNS-01 |
| 10 | 確定待ちの Store | 理由の一覧と、失われる範囲の表示。例外確定は、確認の後にだけ要求される | FR-FIN-02 |
| 11 | 詳細の Store | 版の内容から、セグメント・空白区間・失敗区間・利用できなかった区間が、時刻順に並ぶ。使えない版では、以前の版が出る。作り直しの保留のある `transcribing` の会議だけに「文字起こしを再開する」が出て、状態の帯が保留の理由を示す（保留のない `transcribing`・`finalized` の会議には出ない） | FR-TRV-02・03 |
| 12 | 再生の位置 | セグメントの開始サンプルから、Chunk と位置が求まる。Chunk をまたぐ場合 | FR-TRV-01 |
| 13 | UI テスト: 同意と開始 | 確認しないと録音が始まらない。設定に省略の項目がない | FR-CNS-01 |
| 14 | UI テスト: 権限 | 権限が拒否のとき、案内と「設定を開く」が出る | TST-INT-06 |
| 15 | UI テスト: 録音から詳細 | 合成の入力で録音し、停止の確認を経て、詳細に進む | FR-REC-06 |
| 16 | UI テスト: 確定待ち | 理由の表示と、例外確定の確認 | FR-FIN-02 |
| 17 | UI テスト: 覆い | 前面でなくなると覆いが出る。シートを出した状態でも出る | TST-SEC-09 |
| 18 | UI テスト: アクセシビリティ | 開始・停止の名前が一意。最大の文字サイズで、開始と停止に届く | NFR-UX-01 |

### 11.2 差し替えと起動の引数

UI テストは、差し替えた構成でアプリを起動する（[architecture.md](architecture.md) §10.2 の 7 つの引数。名前は確定済み）。

| 引数 | 値の意味（本書で確定） |
| --- | --- |
| `-uiTestConsent` | `none`: 初回起動の状態（初回説明から始まる）。`accepted`: 初回説明を読了済みにする（一覧から始まる）。**どちらの値でも、録音ごとの同意確認（SC-03）は出る**（FR-CNS-01。差し替えで同意確認を飛ばす経路を作らない） |
| `-uiTestMicrophone` | `granted`・`denied`・`undetermined`: `AudioSessionControlling` の差し替えが返す権限 |
| `-uiTestAudioInput` | `sine`: 正弦波、`silence`: 無音、`none`: 使える入力がない（`ScriptedAudioInput`） |
| `-uiTestSpeechEngine` | `fake`: 決まった結果を返す、`unavailable`: 可否が `unsupported_device` |
| `-uiTestFreeSpace` | `FixedFreeSpace` が返すバイト数 |

- Store の試験は、サービスを差し替えて値を流し込むだけで書く（View を起動しない）。
- UI テストは、遷移と導線に絞る。表示の判断は、Store と Domain の単体テストで網羅する。
- Live Activity と通知は、`StatusPresenterSpy`・`NoticeSchedulerSpy` で呼び出しを記録する。

### 11.3 実装前に確かめたこと（2026-11-16）

| 項目 | 結果 |
| --- | --- |
| ビルド（Mac） | `MinutesDomain/Interface/` のコード片（問題・スナップショット・ロック画面の値・設定・タイトル・一覧の状態・書式）が、Swift 6.4.2・Swift 6 言語モード・警告をエラーとして扱う設定でビルドできた |
| ビルド（iOS） | 覆い・デリゲート・コピー・Live Activity・`RecordingStore` を、既定の分離を MainActor にした設定で、iOS 27.2 SDK に対してビルドできた |
| テスト | 画面に関わる 5 件が通った（問題の網羅、経過時間、ロック画面の値、タイトル、一覧の状態）。一時の Package の全体では 78 件 |
| 見つけて直したこと | `Activity`（ActivityKit）は `Sendable` ではなく、actor に保持して更新すると Swift 6 の検査でエラーになった。保持せず、そのつど一覧を取り直す形に直した（前のプロセスが残した表示の片付けも、同じ形でできる） |
| 静的解析 | コード片の `UIPasteboard` の利用を、規則 `no_direct_pasteboard` が検出した（例外の場所に置けば通る）。そのほかは SwiftLint 0.65.1 で違反なし |

**設計・実装時の注意点:**

- 表示の時間を、画面のタイマーで作らない。時間の表示は、流れてきた値で検証する。
- UI テストで、実際のマイク・音声認識・通知の権限のダイアログを使わない（差し替える）。
- 文言の試験は、鍵の存在と「空でない」ことまでにする。文の内容は、レビューで確かめる。

### 11.4 シミュレータで確かめられないこと

| 事項 | 単体・UI テストでの扱い | 実機での確認 |
| --- | --- | --- |
| 覆いが、アプリ切り替え画面のスナップショットの撮影に間に合う | 通知で覆いが出ることを確かめる | TST-SEC-09（スナップショットに内容が写らないこと） |
| サードパーティー製キーボードが出ない | デリゲートの判定を確かめる | TST-SEC-10 |
| Personal Team で Live Activity が動く。ロック画面とダイナミックアイランドの表示 | 呼び出しを記録する | TST-DEV-12 |
| ペーストボードが、ほかの端末へ同期されない | 指定を確かめる | 同じ Apple Account の別の端末で、貼り付けられないこと（リリース前） |
| VoiceOver での一連の操作 | 名前が一意であることを確かめる | 録音の開始から停止までを VoiceOver で行う |
| 60 分の文字起こしのスクロール | — | 実機で確認し、遅延読み込みの単位を調整する（NFR-PRF-04） |

---

## 12. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

状態はすべて「未実装」である（実装の作業で、テスト名と状態を書き換える）。

| 要件 | テスト（Suite / 表示名の先頭） | 置き場所 | 状態 |
| --- | --- | --- | --- |
| FR-CNS-01、SEC-PRV-02 | 同意確認 / FR-CNS-01 主ボタンの処理でだけ同意確認の値が作られる。UI: FR-CNS-01 確認しないと録音が始まらない | MinutesMobileTests・MinutesMobileUITests | 未実装 |
| FR-CNS-03、SEC-PRV-04 | 初回説明 / FR-CNS-03 5 ページを最後まで進むと読了が記録される | MinutesMobileTests | 未実装 |
| FR-REC-06 | 画面に出す値 / FR-REC-06 経過時間は、累積サンプル数と… | MinutesDomainTests | 未実装 |
| FR-INT-06、TST-INT-06 | UI: TST-INT-06 権限が拒否のとき案内と設定への導線が出る | MinutesMobileUITests | 未実装 |
| FR-SAV-05 | 録音中の Store / FR-SAV-05 未保存の件数と「終了すると失われる」が最上位に出る | MinutesMobileTests | 未実装 |
| FR-FIN-02 | 確定待ちの Store / FR-FIN-02 例外確定は確認の後にだけ要求される。UI: FR-FIN-02 理由と確認が出る | MinutesMobileTests・MinutesMobileUITests | 未実装 |
| FR-STT-07 | 詳細の Store / FR-STT-07 説明を出してからでないとモデルの取得を要求しない | MinutesMobileTests | 未実装 |
| FR-TRV-01 | 再生 / FR-TRV-01 セグメントの位置から Chunk と再生の位置が求まる | MinutesDomainTests | 未実装 |
| FR-TRV-02・03、FR-STT-08・09 | 詳細の Store / FR-TRV-02 失敗区間・空白区間・利用できなかった区間が時刻順に並ぶ | MinutesMobileTests | 未実装 |
| FR-MTG-01 | 画面に出す値 / FR-MTG-01 一覧の状態は、会議の状態から決まる | MinutesDomainTests | 未実装 |
| FR-MTG-02、SEC-INP-01、TST-SEC-06 | 画面に出す値 / TST-SEC-06 タイトルは、改行と制御文字を除き… | MinutesDomainTests | 未実装 |
| FR-MTG-03 | 一覧の Store / FR-MTG-03 削除は確認の後にだけ要求され、録音中は要求できない | MinutesMobileTests | 未実装 |
| FR-SET-01〜03、NFR-CAP-02 | 設定の Store / FR-SET-01 対応状況・使用量・自動開始が出る | MinutesMobileTests | 未実装 |
| NFR-UX-04 | 画面に出す値 / NFR-UX-04 すべての問題の種類が… | MinutesDomainTests・MinutesMobileTests | 未実装 |
| NFR-UX-01・02 | UI: NFR-UX-01 開始と停止の名前が一意で、最大の文字サイズで届く | MinutesMobileUITests | 未実装 |
| NFR-UX-03、SEC-PLT-07 | 画面に出す値 / SEC-PLT-07 ロック画面に出す値は、状態と経過時間だけである | MinutesDomainTests | 未実装 |
| NFR-L10N-01 | 文言 / NFR-L10N-01 コードが使う鍵が String Catalog にすべてある | MinutesMobileTests | 未実装 |
| NFR-PRF-04 | 実機での確認（§11.4） | 実機 | 未実施 |
| SEC-APP-01、TST-SEC-09 | UI: TST-SEC-09 前面でなくなると覆いが出る | MinutesMobileUITests | 未実装 |
| SEC-PLT-01、TST-SEC-08 | コピー / TST-SEC-08 端末内限定と有効期限が付く | MinutesMobileTests | 未実装 |
| SEC-PLT-06、TST-SEC-10 | キーボード / TST-SEC-10 キーボードの拡張機能だけを拒否する | MinutesMobileTests | 未実装 |
| SEC-PLT-02 | 規則 `no_spotlight_user_activity`（[dev-environment.md](dev-environment.md) §6） | 静的解析 | 通過 |
| TST-GEN-05 | 録音中の Store / TST-GEN-05 古い世代の値は捨てられる | MinutesMobileTests | 未実装 |
| TST-CI-08 | 起動の引数（[architecture.md](architecture.md) §14） | MinutesApplicationTests | 未実装 |
| NFR-UX-03 | 実機試験 TST-DEV-12 | 実機 | 未実施 |
| SEC-APP-01、SEC-PLT-06 | 実機試験 TST-SEC-09・10 | 実機 | 未実施 |

---

## 13. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [ui.md](../02-basic-design/ui.md) §13 の 6 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| 各画面の配置・文言・用語の言い換え表 | SC-01〜SC-06・SC-08〜SC-11 の配置と文言を定めた（SC-07 は export.md）。内部の用語 16 個の言い換えを定めた | §3・§4 |
| Store ごとの状態の型と、サービスから受け取る値の型 | 9 つの Store と状態の型。録音中は `RecordingSnapshot` と `RecordingStore`。古い応答は世代番号で捨てる | §5 |
| 問題の種類の一覧と 3 点の文言 | 28 種の重大度と、3 点の文言（初版）。鍵は `problem.{種類}.{what・safety・action}` | §6 |
| 覆いを出す条件（アプリの状態のどの変化で出すか）と、キーボードの拒否の実装 | 覆いは、シーンが前面で操作中でなくなる通知で、専用のウィンドウを出す。キーボードは、アプリのデリゲートが `.keyboard` の拡張機能だけを拒否する | §7.1・§7.2 |
| Live Activity の状態の型と更新の頻度。停止の確認の方法（確認か長押しか） | 型は `LockScreenContent`（状態と経過時間）。更新は、状態の変化では直ちに、録音中は累積サンプル数が 30 秒分進むたび（Chunk の確定・保存の成否によらない）、中断中は注入した `Scheduler` で 30 秒ごと。古くなる時刻は最後の更新の 90 秒後。停止は確認のダイアログ | §4.3・§8 |
| Apple Intelligence の可否を OS に問い合わせる手段（SDK で確かめ、確認日と出典を添える）と、可否の 3 状態への対応づけ | FoundationModels の `SystemLanguageModel.default.availability` を `SummaryModelAvailabilityProviding` の背後で読む（iOS 27.2 SDK の宣言で確認。2026-11-27）。`.available` → 対応、`.unavailable(.deviceNotEligible)` → 非対応、`.unavailable(.appleIntelligenceNotEnabled)`・`.unavailable(.modelNotReady)`・未知の理由 → 無効 | §4.6 |

---

## 14. 断定しない事項・確かめていない事項

満たす要件: NFR-UX-03、NFR-PRF-04、SEC-APP-01、SEC-PLT-06 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| Personal Team で Live Activity（とウィジェット拡張）が動く | ビルドできることだけを確かめた | TST-DEV-12。動かなければ、アプリ内の表示と OS のインジケータで代える（設計済み） |
| Live Activity の更新（約 30 秒ごと）が、OS に間引かれない | 確かめていない。間引かれても、時間表示は OS が自分で進める | TST-DEV-12 |
| 覆いのウィンドウが、アプリ切り替え画面のスナップショットの撮影に間に合う | 通知の時点に依存する | TST-SEC-09（実機）。間に合わなければ、背面へ移る通知でも出す |
| 覆いのウィンドウが、システムが出す画面（共有シート・権限の確認）より上に出る | 確かめていない | 画面の実装（シミュレータ）と実機 |
| シーンの通知（前面で操作中でなくなる・なった）が、すべての場面で対になって届く | SDK の説明による | 画面の実装。覆いが残らないことを UI テストで確かめる |
| キーボードの拒否が、すべての入力欄に効く | OS の実装による | TST-SEC-10（実機） |
| 60 分の文字起こしが滑らかにスクロールする | 端末による | 実機で確認する |
| 文言（§4・§6）が利用者に伝わる | 初版 | オーナーの確認。実機試験で読みにくい文を直す |
| アプリターゲット（既定の分離が MainActor）での Store と View のビルド | 一時の Package（同じ設定）でだけ確かめた | 画面の実装 |
| ウィジェット拡張の見た目のコード | 書いていない（ビルドしていない） | 画面の実装の最後 |
| 2026-11-27 に変えた・足したコード片（§4.6 の `SummaryModelAvailability`・`SummaryModelAvailabilityProviding`、§8 の `LiveActivityPresenter` の `staleDate`）がビルドできる | ビルドしていない（§11.3 の確認は、それより前の版のもの） | 画面の実装の最初に確かめる |

---

## 15. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- iOS 27.2 SDK の `UIApplication.h`（拡張機能の種類ごとの許可、キーボードの拡張機能の識別子）、`UIPasteboard.h`（端末内限定・有効期限の指定、項目の設定）
- iOS 27.2 SDK の `ActivityKit.swiftinterface`（`ActivityAttributes`・`Activity.request(attributes:content:pushType:)`・`update`・`end`・`ActivityContent`・`ActivityAuthorizationInfo.areActivitiesEnabled`・古い `request` の非推奨の宣言）
- iOS 27.2 SDK の `FoundationModels.swiftinterface`（`SystemLanguageModel.default`・`availability`・`Availability.UnavailableReason` の `deviceNotEligible`・`appleIntelligenceNotEnabled`・`modelNotReady`、iOS 26 以降の宣言）（確認日 2026-11-27）
- Xcode 27.2（27B52）・Swift 6.4.2 の `swift build`・`swift test`・`xcodebuild`（一時の Package で実行。§11.3 の結果はこの実行による）
- SwiftLint 0.65.1（`no_direct_pasteboard` の検出の確認）。版と配布元は [GitHub: realm/SwiftLint releases](https://github.com/realm/SwiftLint/releases)（確認日 2026-11-14。台帳 [dependencies-and-costs.md](../01-requirements/dependencies-and-costs.md) §3）で、実行したのは `tools/versions` の SHA-256 で照合して `.tools/` に入れた版
- 基本設計 [ui.md](../02-basic-design/ui.md) §14 の出典（Apple の対応 capability の一覧など。本作業では再確認していない）
- Minutes `docs/design-local-phase1.md` §3.6・§3.9、`docs/design-local-phase2.md` §18（別リポジトリ・読み取り専用。本作業では参照できず、基本設計の記述に基づいた）
- [Apple: ActivityConfiguration](https://developer.apple.com/documentation/widgetkit/activityconfiguration)（WidgetKit の文書のページ。ページの存在を確認。確認日 2026-11-27。WidgetKit の型で iOS 16.1 以降であることの根拠は、iOS 27.2 SDK の `WidgetKit.swiftinterface` の宣言）
- **出典の URL について（確認日 2026-11-27）:** 上の SDK のヘッダ・`.swiftinterface` は、Xcode に同梱されたローカルのファイルで、公開の URL はない。版の根拠は、SDK の版（iOS 27.2）は `xcodebuild -showsdks`、Xcode 27.2（27B52）は `xcodebuild -version`、Swift 6.4.2 は `swift --version`、各 API の対応 OS は SDK の宣言（`@available`・`API_AVAILABLE`）で確かめたものである（[dev-environment.md](dev-environment.md) §2）。Apple の文書のページは、本文が JavaScript で描かれ、対応 OS の記述を取得できないので、版の根拠にしない
