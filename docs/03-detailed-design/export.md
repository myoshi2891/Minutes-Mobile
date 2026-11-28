# MinutesMobile 詳細設計書 ── 書き出し

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版・実装前） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | FR-EXP-01〜04、SEC-DAT-06、SEC-INP-02、SEC-INT-01・03 |
| 関係する制約 | CON-04 |
| 対応する基本設計 | [../02-basic-design/export.md](../02-basic-design/export.md)（設計決定 BD-EXP-01〜08 を引用する） |
| 関連文書 | [architecture.md](architecture.md)・[storage.md](storage.md)・[transcription.md](transcription.md)・[ui.md](ui.md) |

本書は、録音と文字起こしをアプリの外へ出す手順、アーカイブ（ZIP）のバイト配置、目録、ファイル名の無害化、一時ファイルの寿命、緊急書き出しを、型とアルゴリズムの単位で定める。書き出しは、データがアプリの保護の外へ出る唯一の経路である。

**本書のコード片は、実装前に一時の Package でビルドとテストを通したものである**（§10.3）。実ファイルはまだないので、照合の印（`<!-- file: -->`）は付けていない。実装の作業で印を付ける（[README.md](README.md) §3）。

---

## 1. 位置づけと範囲

満たす要件: FR-EXP-01、NFR-MNT-02・04 ／ 決定: BD-EXP-01・02、UD-10

| 本書で決めること | 節 | 置き場所（[architecture.md](architecture.md) §2） |
| --- | --- | --- |
| 書き出しの種類、連結した録音の計画 | §2 | `MinutesDomain/Export/` |
| 手順、失敗の分類、書き出し ID、書き出しの画面、共有シートを包む部品 | §3 | 手順は `MinutesApplication/ExportService`、画面と部品はアプリの `Features/Export/` |
| ZIP のバイト配置、目録、書き込み | §4 | 配置と目録は Domain、書き込みは `MinutesStorage/Archive/` |
| ファイル名の無害化 | §5 | Domain |
| 一時ファイルの寿命 | §6 | `ExportService`・`RecoveryService` |
| 緊急書き出し、メモリ上のデータを渡す経路 | §7 | Domain とアプリ |
| 文字起こしの形式 | §8 | Domain |

基本設計の決定（BD-EXP-01〜08）は変えていない。本書で細部を決めたことは次のとおりである。

| 事項 | 決定 | 節 |
| --- | --- | --- |
| ZIP のバイト配置の基準 | 参照元（Minutes `src/export/zip.ts`）が手元にないため、**公開されている ZIP の仕様（PKWARE の APPNOTE）の、無圧縮・単一ディスク・ZIP64 なしの形**から起こした。OS 標準の `unzip` で検査・展開できることを確かめた | §4.1 |
| CRC-32 を計算する時点 | エントリ（1 つの Chunk は 1 MB 弱）をメモリに読み、CRC-32 を計算してから、ヘッダとデータを続けて書く。ヘッダを後から書き直さない・データ記述子を使わない | §4.3 |
| ZIP の日時の時間帯 | UTC に固定する（端末の時間帯で、出力のバイト列が変わらないようにする） | §4.1 |
| 外側のファイル名の日時の時間帯 | 端末の時間帯（利用者が見る名前のため）。無害化の関数には、時間帯を引数で渡す | §5 |

---

## 2. 書き出しの種類と連結した録音

満たす要件: FR-EXP-01・02・04 ／ 決定: BD-EXP-05

```swift
/// 書き出しの種類（FR-EXP-01・02・04）
public enum ExportKind: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    /// 録音一式（ZIP）
    case archive
    /// 連結した録音（WAV）
    case concatenatedAudio = "concatenated_audio"
    case transcriptText = "transcript_text"
    case transcriptMarkdown = "transcript_markdown"
    /// 緊急書き出し（メモリ上の未保存の Chunk）
    case emergency

    /// 書き出すファイルの拡張子。緊急書き出しは、作る中身と同じく Chunk の数で決まる（1 件なら WAV、複数なら ZIP。§7 の手順 3）。
    /// ほかの種類は chunkCount を見ない
    public func fileExtension(chunkCount: Int) -> String {
        switch self {
        case .archive: "zip"
        case .emergency: chunkCount == 1 ? "wav" : "zip"
        case .concatenatedAudio: "wav"
        case .transcriptText: "txt"
        case .transcriptMarkdown: "md"
        }
    }
}
```

| 種類 | 中身 | 対象の状態 |
| --- | --- | --- |
| `archive`（録音一式） | 保存済みの全 Chunk の WAV と目録（§4） | 録音中以外のすべて（確定待ちを含む） |
| `concatenated_audio`（連結した録音） | 保存済みの Chunk を順につないだ 1 つの WAV。欠落は無音で補う | 同上 |
| `transcript_text`・`transcript_markdown` | 時刻つきの本文（§8） | 表示に使える版があるとき |
| `emergency`（緊急書き出し） | メモリ上にしかない未保存の Chunk（§7） | 未保存があるとき（録音中を含む） |

- 録音一式と連結した録音の対象は、**保存済みの Chunk（正式名の WAV）だけ**である。メモリ上の未保存の Chunk は含めず、緊急書き出しで出す（基本設計 §2）。
- 入れる Chunk は、ファイルの状態が `present` と `hash_mismatch` のもの（ヘッダの検証に通るもの）である。`hash_mismatch` は、中身をそのまま入れて、目録に印を付ける（BD-EXP-05）。`missing`・`invalid`・`quarantined` は入れず、欠落として扱う。
- 隔離したファイル（会議の `quarantine/`）・書きかけ（`.part`）・SQLite のファイルは、どの種類にも含めない。

**連結した録音の計画**（基本設計 §2 の規則）:

```swift
/// 連結した録音に入れる保存済みの Chunk
public struct ConcatenationSource: Equatable, Sendable {
    public let sequence: ChunkSequence
    public let range: SampleRange

    public init(sequence: ChunkSequence, range: SampleRange) {
        self.sequence = sequence
        self.range = range
    }
}

/// 連結した録音の 1 つの区間
public enum ConcatenationPiece: Equatable, Sendable {
    /// 保存済みの Chunk の PCM を、そのまま入れる
    case audio(ConcatenationSource)
    /// 欠落した区間を、同じサンプル数の無音で補う
    case silence(SampleRange)
}

/// 連結した録音の計画（基本設計 §2）。純粋関数
public enum ConcatenationPlan {
    /// sources: 入れる Chunk（保存済みで、ヘッダの検証に通ったもの。ハッシュが合わないものも入れる）
    /// knownEnd: 範囲の分かっている欠落（未保存の記録の元の範囲など）を含めた、音声の終わりの位置
    /// hasUnknownLengthLoss: 最後の Chunk より後に、長さの分からない欠落（末尾の隔離など）がある
    public static func make(
        sources: [ConcatenationSource], knownEnd: Int64, hasUnknownLengthLoss: Bool,
    ) throws(ExportFailure) -> [ConcatenationPiece] {
        // 長さの分からない欠落は、推定の長さの無音で補わない。連結した録音は作らない
        guard !hasUnknownLengthLoss else { throw .unknownLengthLoss }
        var pieces: [ConcatenationPiece] = []
        var cursor: Int64 = 0
        for source in sources.sorted(by: { $0.range.start < $1.range.start }) {
            // 範囲が重なるのは記録の不整合。同じ範囲を 2 回出力しない
            guard source.range.start >= cursor else { throw .inconsistentRanges }
            if let silence = SampleRange(start: cursor, count: source.range.start - cursor), silence.count > 0 {
                pieces.append(.silence(silence))
            }
            pieces.append(.audio(source))
            cursor = source.range.end
        }
        if let tail = SampleRange(start: cursor, count: knownEnd - cursor), tail.count > 0 {
            pieces.append(.silence(tail))
        }
        return pieces
    }

    /// 連結した録音の全体のサンプル数
    public static func totalSamples(of pieces: [ConcatenationPiece]) -> Int64 {
        pieces.reduce(0) { total, piece in
            switch piece {
            case let .audio(source): total + source.range.count
            case let .silence(range): total + range.count
            }
        }
    }
}
```

| 規則 | 内容 |
| --- | --- |
| 位置 | 各 Chunk の位置は、その開始サンプル（書き出しの開始時のスナップショットの値。§3.4）で決める。開始サンプルの順に並べる |
| 無音で補う区間 | **保存済みの Chunk で覆われない区間だけ**を、同じサンプル数の無音にする。欠番・ファイル欠損・不正なヘッダ・途中の隔離は、前後の Chunk の終わりと始まりから区間が決まる。一部だけ救済した Chunk は、救済した分をそのまま入れ、残りだけが無音になる |
| 末尾 | 範囲の分かっている欠落（未保存の記録の元の範囲、失われた未保存）が最後の Chunk より後にあれば、その終わり（`knownEnd`）までを無音にする |
| 長さの分からない欠落 | 最後の Chunk より後に、範囲を持たない欠落（末尾の隔離など）がある場合は、**作らない**（`unknownLengthLoss`）。推定の長さで無音を補わない。録音一式は作れる |
| 範囲の重なり | 保存済みの Chunk の範囲が重なる場合は、記録の不整合として作らない（`inconsistentRanges`）。同じ範囲を 2 回出力しない |
| 空白区間 | 無音を入れない（長い中断で巨大になるため）。そのため、連結した録音の中の位置は、会議全体のサンプル番号（音声時刻）と一致する |
| 大きさ | WAV のヘッダの上限（4 GiB 未満）に収まらない場合は、作らずに理由を表示する（`archiveLimitExceeded`）。16 kHz・16 bit・モノラルでは約 37 時間分に当たる |

- `knownEnd` と `hasUnknownLengthLoss` は、`ExportService` が、書き出しの開始時のスナップショット（§3.4）の Chunk の一覧・未保存の記録・欠落の記録から求める。DB と `meeting.json` は読まない。
- 連結した録音での位置（秒）は、`サンプル番号 ÷ 16,000` である。会議時刻（空白区間を含む）との対応は、[architecture.md](architecture.md) §4 の式で求める。文字起こしの書き出し（§8）に、連結した録音での位置を併記する。
- 書き方は、WAV のヘッダ（全体のサンプル数）を先に書き、区間ごとに追記する。無音は、0 のバイト列を 1 MB ずつ追記する（全体をメモリに載せない）。注記があれば、最後に下の `LIST` チャンクを追記する。

**連結した録音の注記（`LIST`（`INFO`）チャンク。基本設計 §2 の申し送り）:** 補った欠落区間とハッシュの不一致がある場合だけ、`data` チャンクの**後ろ**に 1 つ置く（`data` は常に 44 バイト目から始まる）。

```swift
/// 連結した録音の注記のチャンク（基本設計 §2）。comment は固定の書式の ASCII だけ（タイトルを入れない）
public enum WAVAnnotation {
    /// "LIST" + 大きさ + "INFO" + "ICMT" + 大きさ + 本文 + NUL + 詰め物（必要なら 1 バイト）
    public static func listChunk(comment: String) -> [UInt8]? {
        guard comment.utf8.allSatisfy({ (0x20 ... 0x7E).contains($0) }) else { return nil }
        let text = Array(comment.utf8) + [0]
        let padding: [UInt8] = text.count.isMultiple(of: 2) ? [] : [0]
        let subchunk = Array("ICMT".utf8) + littleEndian(UInt32(text.count)) + text + padding
        let body = Array("INFO".utf8) + subchunk
        return Array("LIST".utf8) + littleEndian(UInt32(body.count)) + body
    }

    /// RIFF の大きさの欄に収まるか（ファイル全体 − 8 が 32 bit に収まる）。上限の判定と書き込みで同じ計算を使う
    public static func fits(totalSamples: Int64, annotationBytes: Int) -> Bool {
        let fileBytes = Int64(WAVHeader.byteCount) + totalSamples * 2 + Int64(annotationBytes)
        return fileBytes - 8 <= Int64(UInt32.max)
    }

    private static func littleEndian(_ value: UInt32) -> [UInt8] {
        [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF), UInt8((value >> 16) & 0xFF), UInt8(value >> 24)]
    }
}
```

| 位置（`LIST` の先頭から） | 大きさ | 内容 |
| --- | --- | --- |
| 0 | 4 | `LIST` |
| 4 | 4 | 以後の大きさ（リトルエンディアン）= 4 + 8 + `ICMT` の大きさ + 詰め物 |
| 8 | 4 | `INFO` |
| 12 | 4 | `ICMT` |
| 16 | 4 | `ICMT` の大きさ = 本文のバイト数 + 1（終端の NUL を含む。詰め物は含めない） |
| 20 | 本文 + 1 | 本文（ASCII の 0x20〜0x7E だけ）と終端の NUL |
| 20 + 本文 + 1 | 0 か 1 | 詰め物（`ICMT` の大きさが奇数のときだけ 0x00 を 1 バイト。チャンクを偶数長にそろえる） |

- RIFF の大きさの欄（4 バイト目）は「ファイル全体 − 8」（`LIST` を含む）にする。ヘッダは先に書くので、注記の本文は書き始める前に、スナップショット（§3.4）から作って長さを決めておく。
- **上限の判定は、注記を含めた大きさで行う**（`WAVAnnotation.fits`。詰め物の 1 バイトで上限が変わるため、`listChunk` の戻り値の長さをそのまま渡す）。収まらなければ `archiveLimitExceeded`。
- 本文の書式（`MinutesMobile hash-mismatch: …`・`MinutesMobile loss: …`・`MinutesMobile unverified: …` を `;`（直後に空白 1 つ）で区切って並べる）は基本設計 §2 のとおりである。数値と英字の理由だけで、タイトル・本文を入れない。

---

## 3. 手順・失敗・書き出し ID

満たす要件: FR-EXP-01〜03、SEC-DAT-06、SEC-INT-01・03、TST-GEN-05 ／ 決定: BD-EXP-03・04・07・08、BD-UI-08

### 3.1 手順

| 順 | 処理 | 失敗（`ExportFailure`） |
| --- | --- | --- |
| 1 | 画面（SC-07）で種類を選ぶ。警告「書き出したファイルは、このアプリの保護の外に出ます」を出し、「了承して書き出す」を押したときだけ進む（毎回。省く設定は作らない。BD-EXP-04） | — |
| 2 | `ExportService.begin(meeting:kind:)`。録音中の会議なら拒否する（緊急書き出しを除く）。削除を始めた会議なら拒否する | `recordingInProgress`・`meetingBeingDeleted` |
| 3 | 書き出し ID を発番する（`IDGenerator`）。`tmp/export/{meetingId}/{exportId}/` を作る（[storage.md](storage.md) §2.2）。**緊急書き出しでは、作れなくても失敗にせず、§7 のメモリ上の経路へ進む** | `storage`（緊急書き出しを除く） |
| 4 | 必要な大きさ（下の式）を見積もり、空き容量が「必要な大きさ + 予備（200 MB）」以上であることを確かめる（BD-EXP-07）。**緊急書き出しでは行わない**（下の注記） | `insufficientSpace(requiredBytes:)`（緊急書き出しを除く） |
| 5 | 保管中（`sealed`）の会議なら、処理中の保護クラスへ戻す（BD-EXP-08。[storage.md](storage.md) §8） | `storage` |
| 6 | 各 Chunk を検証つきで読む（ヘッダとハッシュ。[storage.md](storage.md) §6.1）。文字起こしを含む場合は、版のファイルの完全性を照合する（[transcription.md](transcription.md) §6.3） | `transcriptVersionRejected` |
| 7 | ハッシュが合わない Chunk があれば、件数を画面に返し、続けるかを利用者が選ぶ（問題 `export_integrity_warning`）。続ける場合は、そのまま入れて目録に印を付ける | `cancelled`（やめた場合） |
| 8 | 一時ファイルを作る（順に書く。進み具合を「済んだ Chunk の数 ÷ 全体」で流す） | `archiveLimitExceeded`・`unknownLengthLoss`・`inconsistentRanges`・`storage` |
| 9 | 一時ファイルの場所を画面へ返す。画面が共有シートを出す（§3.3） | — |
| 10 | 共有シートの完了・取り消しで、`ExportService.finish(exportId:)` を呼ぶ。一時のディレクトリを消し、保護クラスを見直す | — |

- **緊急書き出しは、事前の空き容量の確認（手順 4）をせず、書き込みを試してから戻る**（決定）。緊急書き出しを使うのは容量の枯渇で Chunk を保存できない場面なので、事前の確認は常に通らず、メモリ上の経路に届かなくなるためである。手順 3 の一時のディレクトリの作成、または §7 の一時ファイルの書き込み・同期のどれかに失敗したら（`disk_full` を含む）、作りかけの一時ファイルを消し（消せなくても続ける）、§7 の手順 5 のメモリ上の経路で渡す。

必要な大きさの見積もり: 録音一式は「入れる Chunk のバイト数の合計 + エントリごとに 76 バイトと名前の長さの 2 倍 + 目録」、連結した録音は「44 + 全体のサンプル数 × 2」、文字起こしは「版のファイルの大きさの 2 倍」（上限の目安）。

- Chunk は 2 回読む。1 回目（手順 6）は検証だけを行い、結果を利用者に示す（手順 7）。2 回目（手順 8）は、読みながら SHA-256 と CRC-32 を計算して書く。2 回目の SHA-256 が 1 回目と違えば、途中で内容が変わったものとして中止する（`storage` の `verification_mismatch`）。
- 手順 8 の途中で失敗・中止した場合も、一時のディレクトリを消してから失敗を返す。
- 文字起こしの版が照合に通らない場合は、その版を書き出さない（続ける選択肢を出さない）。録音だけの書き出しは続けられる（基本設計 §3）。
- ログは、`export_started`（種類）と `export_finished`（種類・結果・バイト数）を出す。**タイトルとファイル名は出さない**（[architecture.md](architecture.md) §9.2）。

### 3.2 失敗と書き出し ID

| 失敗（`ExportFailure`。[architecture.md](architecture.md) §8.1） | 起きる条件 | 問題の種類 |
| --- | --- | --- |
| `recordingInProgress` | その会議が録音中（緊急書き出しを除く） | `export_unavailable` |
| `meetingBeingDeleted` | 削除を始めた会議（確認の後から。墓標の後も） | `export_unavailable` |
| `insufficientSpace(requiredBytes:)` | 空き容量が足りない（緊急書き出しを除く。緊急書き出しは確認をせず、書けなければ §7 のメモリ上の経路へ移る） | `export_insufficient_space` |
| `archiveLimitExceeded` | 4 GiB 以上・65,535 件以上（§4.1）、または連結した録音が 4 GiB 以上 | `export_unavailable` |
| `unknownLengthLoss` | 連結した録音で、長さの分からない欠落がある | `export_unavailable`（「録音一式で書き出してください」を添える） |
| `inconsistentRanges` | 連結した録音で、Chunk の範囲が重なる | `export_unavailable` |
| `transcriptVersionRejected` | 版のファイルが照合に通らない | `transcript_version_rejected` |
| `cancelled` | 利用者の中止、削除による中止 | `export_failed`（利用者の中止では表示しない） |
| `storage(StorageFailure)` | 読み書きの失敗 | `export_failed` |

| 規則 | 内容 |
| --- | --- |
| 書き出し ID | 書き出しごとに `IDGenerator.makeUUID()` で作る。一時のディレクトリの名前と、要求と応答の対応付けに使う |
| 同時の書き出し | 1 つの会議につき、同時に 1 つ。進行中に次の要求が来たら、前の書き出しを中止して片付けてから始める |
| 古い応答 | 進み具合・完了・失敗には、書き出し ID を添える。`ExportStore` は、現在の書き出し ID と合わない応答を捨てる（TST-GEN-05） |
| 削除との関係 | `DeletionService` は、確認の後すぐに `ExportService.rejectExports(for:)` を呼ぶ。以後、その会議の `begin` は `meetingBeingDeleted` を返す（書き出し中でなくても）。削除要求を作れずに削除を中止した場合は、`DeletionService` が同じプロセスの中で `ExportService.acceptExports(for:)` を呼んで拒否を解除する（再起動を待たない）。書き出し中なら、削除要求を永続化した後に中止し、中止の完了（一時ファイルへの書き込みが止まったこと）を返してから、削除が進む（上限 10 秒。[storage.md](storage.md) §10 の手順 1） |
| 保存の入口 | `ExportService` は、Domain の protocol（`ExportSource`: Chunk の一覧と検証つきの読み出し、版の読み出し、会議のメタデータ。`ExportSink`: 一時ファイルの作成・追記・削除）越しに `MeetingStore` を使う。一時ファイルを作る関数は、会議に削除要求がないことを確かめる |

### 3.3 書き出しの画面（SC-07）と共有シート

| 順 | 要素 | 内容 |
| --- | --- | --- |
| 1 | 種類の選択 | 「録音一式（ZIP）」「連結した録音（WAV）」「文字起こし（テキスト）」「文字起こし（Markdown）」。選べない種類は、理由を添えて無効にする |
| 2 | 未保存の案内（未保存があるとき） | 「まだ保存できていない録音が N 件あります。この分は含まれません。『未保存分を書き出す』で保存してください」と、緊急書き出しへの操作 |
| 3 | 警告 | 「書き出したファイルは、このアプリの保護の外に出ます。保存先と共有先は、ご自身で管理してください」 |
| 4 | 主ボタン | 「了承して書き出す」 |
| 5 | 進み具合 | 「書き出しを準備しています（120 件中 45 件）」と「中止」 |
| 6 | 共有シート | 一時ファイルができたら出す |

```swift
/// 共有シートへ渡すもの
enum ShareItem {
    /// 一時ファイル（tmp/export/ の下）
    case file(URL)
    /// メモリ上のデータ（緊急書き出しで、一時ファイルを作れない場合）
    case data(Data, typeIdentifier: String, suggestedName: String)

    var activityItem: Any {
        switch self {
        case let .file(url):
            return url
        case let .data(bytes, typeIdentifier, suggestedName):
            let provider = NSItemProvider()
            provider.suggestedName = suggestedName
            provider.registerDataRepresentation(forTypeIdentifier: typeIdentifier, visibility: .all) { completion in
                completion(bytes, nil)
                return nil
            }
            return provider
        }
    }
}

/// 共有シート（UIActivityViewController）を包む部品（BD-UI-08）。ShareLink は使わない。
/// 完了・取り消しのどちらでも onFinish を呼び、後始末（一時ファイルの削除）へつなぐ
struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [ShareItem]
    /// 引数は、共有が完了したか（取り消しなら false）
    let onFinish: (Bool) -> Void

    func makeUIViewController(context _: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(
            activityItems: items.map(\.activityItem), applicationActivities: nil,
        )
        controller.completionWithItemsHandler = { _, completed, _, _ in
            onFinish(completed)
        }
        return controller
    }

    func updateUIViewController(_: UIActivityViewController, context _: Context) {}
}
```

| 決めたこと（申し送り） | 内容 |
| --- | --- |
| 包む部品 | `ActivityShareSheet`（`UIViewControllerRepresentable`）。`ShareLink` は使わない（BD-UI-08。静的解析の `no_share_link`） |
| 完了の受け取り方 | `UIActivityViewController.completionWithItemsHandler` で受け取る。完了でも取り消しでも `onFinish` を呼び、`ExportStore` が `ExportService.finish(exportId:)` を呼ぶ |
| 入口 | 共有シートを出すのは、`ExportStore` の 1 か所だけである（警告と後始末を必ず通す） |
| 完了が届かない場合 | アプリが終了すると届かない。次の起動の復旧で `tmp/export/` を丸ごと消す（§6） |
| 覆いとの関係 | 共有シートを出している間も、覆い（[ui.md](ui.md) §7.1）は専用のウィンドウで上に出る |

### 3.4 書き出しの開始時のスナップショット（基本設計 §2 の申し送り）

```swift
/// 書き出しに使う値のすべて（基本設計 §2）。MeetingStore のメモリ上の状態の写しで、DB と meeting.json を読まずに作る
public struct ExportSnapshot: Equatable, Sendable {
    public let meeting: MeetingID
    /// 保存済みの Chunk（連番・開始サンプル・サンプル数・記録したハッシュ・ファイルの状態）。
    /// 開始位置が不明な Chunk（storage.md §9 の照合 d）は開始サンプルが nil で、連結した録音と目録では欠落として扱う
    public let chunks: [SavedChunkEntry]
    /// メモリ上の未保存 Chunk と永続化した未保存の記録（連番・元の範囲）
    public let unsaved: [UnsavedChunkEntry]
    public let gaps: [MeetingDocument.GapRecord]
    /// lossRecord・隔離した連番・長さが不明な欠落
    public let losses: LossSummary
    /// タイトル・開始日時・確定方式・入力経路の履歴・会議の状態
    public let metadata: MeetingMetadata
}

public protocol ExportSource: Sendable {
    /// 中断点（await）を含まない 1 つのメソッドで、actor のメモリ上の状態から写しを作る
    func exportSnapshot(of meeting: MeetingID) async throws(ExportFailure) -> ExportSnapshot
    // ほかの要件（検証つきの Chunk の読み出し・版の読み出し）は §3.2 の「保存の入口」のとおり
}
```

| 項目 | 設計 |
| --- | --- |
| 取得の方法 | `MeetingStore.exportSnapshot(of:)`（actor のメソッド）は、中に `await` を持たない。呼び出し側の `await` は actor に入るための待ちで、メソッドの中では別の処理（保存の再試行の成功など）が割り込まない。値はすべて、同じ時点の actor のメモリ上の状態（会議ごとの `MeetingState`。[storage.md](storage.md) §5.1）から取る |
| メモリ上に持つ形 | `MeetingStore` は会議ごとに `MeetingState`（値型）を持つ: 保存済みの Chunk の一覧（連番 → `SavedChunkEntry`）、未保存（連番 → `UnsavedChunkEntry`。メモリ上の保持と永続化した記録の両方）、空白区間、欠落の記録、会議のメタデータ。起動の後は、起動時の照合（[storage.md](storage.md) §9）の結果から作る |
| 書き込みの前に状態へ反映する順序 | どの変更も、**同じ同期のメソッドの中で、先に `MeetingState` を更新してから** DB・`meeting.json` の書き込みを発行する（Chunk の登録は、登録の取引と `MeetingState` の更新を同じメソッドの中で続けて行う。未保存の記録の削除も同じ）。書き込みの I/O が終わっていなくても、状態に反映済みの登録・追加・削除・タイトルの変更はスナップショットに入る |
| 再試行の成功がスナップショットを変えないこと | スナップショットは値型の写しで、`MeetingState` への参照を持たない。書き出しは、手順 2 で取った写しの Chunk の一覧と未保存の記録だけを使い、その後の再試行の成功（未保存から保存済みへの移動）を見ない。1 つの写しの中で、同じ Chunk が保存済みと未保存の両方に現れるのは、一部だけ救済した連番（正しい状態）だけである |
| 使う手順 | §3.1 の手順 2 の直後に 1 回だけ取り、目録（§4.2）・連結した録音（§2）・文字起こしの書き出し（§8）・ファイル名（§5）・注記（§2）のすべてをこの写しから作る |
| 試験 | 保存の再試行の成功をスナップショットの取得の直後に起こし（差し替えで順序を決める）、書き出しの中身が取得の時点の写しのままであること。DB の書き込みを止めた状態でも、状態に反映済みの登録がスナップショットに入ること |

---

## 4. アーカイブ

満たす要件: FR-EXP-01、SEC-INT-01、SEC-INP-02 ／ 決定: BD-EXP-01・02・06

### 4.1 バイト配置

無圧縮（stored）・単一ディスク・ZIP64 なし・暗号化なし・コメントなしの ZIP である。数値はすべてリトルエンディアンで書く。

```swift
/// アーカイブの中の名前（BD-EXP-06）。固定の規則だけで作り、会議タイトルを使わない
public enum ArchiveEntryName {
    public static let manifest = "manifest.json"

    public static func chunk(_ sequence: ChunkSequence) -> String {
        "mic/\(sequence.fileStem).wav"
    }
}

/// 無圧縮（stored）の ZIP のバイト配置（BD-EXP-01）。純粋関数で、I/O を持たない
public enum ZIPFormat {
    /// ZIP64 は実装しない。合計は 4 GiB 未満、エントリは 65,535 件未満に限る
    public static let maxArchiveBytes: Int64 = 0xFFFF_FFFF
    public static let maxEntries = 0xFFFF

    private static let versionNeeded: UInt16 = 20
    /// 汎用フラグの bit 11（名前が UTF-8）
    private static let utf8Flag: UInt16 = 0x0800

    /// MS-DOS 形式の日時（UTC。1980 年より前は 1980-01-01 にする）
    public struct DOSDateTime: Equatable, Sendable {
        public let time: UInt16
        public let date: UInt16

        public init(_ instant: Date) {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
            let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: instant)
            let year = parts.year ?? 1980
            guard (1980 ... 2107).contains(year) else {
                time = 0
                date = (1 << 5) | 1
                return
            }
            let clock = (parts.hour ?? 0) << 11 | (parts.minute ?? 0) << 5 | (parts.second ?? 0) / 2
            let day = (year - 1980) << 9 | (parts.month ?? 1) << 5 | (parts.day ?? 1)
            time = UInt16(truncatingIfNeeded: clock)
            date = UInt16(truncatingIfNeeded: day)
        }
    }

    /// ローカルファイルヘッダ（30 バイト + 名前）。この直後にデータを置く
    public static func localHeader(_ entry: ZIPEntry, stamp: DOSDateTime) -> [UInt8] {
        let name = Array(entry.name.utf8)
        var bytes = littleEndian(UInt32(0x0403_4B50))
        bytes += littleEndian(versionNeeded) + littleEndian(utf8Flag) + littleEndian(UInt16(0))
        bytes += littleEndian(stamp.time) + littleEndian(stamp.date)
        bytes += littleEndian(entry.crc32) + littleEndian(entry.size) + littleEndian(entry.size)
        bytes += littleEndian(UInt16(name.count)) + littleEndian(UInt16(0))
        return bytes + name
    }

    /// 中央ディレクトリのヘッダ（46 バイト + 名前）
    public static func centralHeader(_ entry: ZIPEntry, stamp: DOSDateTime) -> [UInt8] {
        let name = Array(entry.name.utf8)
        var bytes = littleEndian(UInt32(0x0201_4B50))
        bytes += littleEndian(versionNeeded) + littleEndian(versionNeeded)
        bytes += littleEndian(utf8Flag) + littleEndian(UInt16(0))
        bytes += littleEndian(stamp.time) + littleEndian(stamp.date)
        bytes += littleEndian(entry.crc32) + littleEndian(entry.size) + littleEndian(entry.size)
        // 名前の長さ、拡張・コメントの長さ（0）、ディスク番号（0）、内部の属性（0）
        bytes += littleEndian(UInt16(name.count)) + [UInt8](repeating: 0, count: 8)
        // 外部の属性（0）、ローカルヘッダの位置
        bytes += littleEndian(UInt32(0)) + littleEndian(entry.offset)
        return bytes + name
    }

    /// 中央ディレクトリの終端（22 バイト）
    public static func endOfCentralDirectory(
        entryCount: Int, directorySize: UInt32, directoryOffset: UInt32,
    ) -> [UInt8] {
        let count = UInt16(truncatingIfNeeded: entryCount)
        var bytes = littleEndian(UInt32(0x0605_4B50)) + [UInt8](repeating: 0, count: 4)
        bytes += littleEndian(count) + littleEndian(count)
        bytes += littleEndian(directorySize) + littleEndian(directoryOffset)
        return bytes + littleEndian(UInt16(0))
    }

    /// 作る前に、上限に収まることを確かめる。sizes は各エントリの「名前の長さ・データの大きさ」
    public static func fits(entries: [(nameLength: Int, size: Int64)]) -> Bool {
        guard entries.count < maxEntries else { return false }
        let total = entries.reduce(Int64(22)) { sum, entry in
            // ローカルヘッダ 30 + 中央ディレクトリ 46 + 名前 2 回 + データ
            sum + 76 + Int64(entry.nameLength) * 2 + entry.size
        }
        return total < maxArchiveBytes && entries.allSatisfy { $0.size < maxArchiveBytes }
    }

    private static func littleEndian(_ value: some FixedWidthInteger) -> [UInt8] {
        withUnsafeBytes(of: value.littleEndian) { Array($0) }
    }
}
```

**ローカルファイルヘッダ**（30 バイト + 名前。この直後にデータを置く）:

| 位置 | 長さ | 項目 | 値 |
| --- | --- | --- | --- |
| 0 | 4 | 署名 | `50 4B 03 04` |
| 4 | 2 | 展開に必要な版 | 20 |
| 6 | 2 | 汎用フラグ | `0x0800`（bit 11。名前が UTF-8） |
| 8 | 2 | 圧縮の方式 | 0（無圧縮） |
| 10 | 2 | 時刻（MS-DOS 形式） | 会議の開始日時（UTC） |
| 12 | 2 | 日付（MS-DOS 形式） | 同上 |
| 14 | 4 | CRC-32 | データの CRC-32 |
| 18 | 4 | 圧縮後の大きさ | データの大きさ |
| 22 | 4 | 圧縮前の大きさ | データの大きさ（同じ値） |
| 26 | 2 | 名前の長さ | 名前のバイト数 |
| 28 | 2 | 拡張の長さ | 0 |
| 30 | — | 名前 | `manifest.json`・`mic/000000.wav` |

**中央ディレクトリのヘッダ**（46 バイト + 名前）:

| 位置 | 長さ | 項目 | 値 |
| --- | --- | --- | --- |
| 0 | 4 | 署名 | `50 4B 01 02` |
| 4 | 2 | 作成した版 | 20 |
| 6 | 2 | 展開に必要な版 | 20 |
| 8 | 2 | 汎用フラグ | `0x0800` |
| 10 | 2 | 圧縮の方式 | 0 |
| 12 | 4 | 時刻・日付 | ローカルファイルヘッダと同じ |
| 16 | 12 | CRC-32・圧縮後の大きさ・圧縮前の大きさ | ローカルファイルヘッダと同じ |
| 28 | 2 | 名前の長さ | 名前のバイト数 |
| 30 | 8 | 拡張の長さ・コメントの長さ・ディスク番号・内部の属性 | すべて 0 |
| 38 | 4 | 外部の属性 | 0 |
| 42 | 4 | ローカルファイルヘッダの位置 | アーカイブの先頭からのバイト数 |
| 46 | — | 名前 | ローカルファイルヘッダと同じ |

**中央ディレクトリの終端**（22 バイト）:

| 位置 | 長さ | 項目 | 値 |
| --- | --- | --- | --- |
| 0 | 4 | 署名 | `50 4B 05 06` |
| 4 | 4 | ディスク番号・中央ディレクトリのあるディスク | 0 |
| 8 | 2 | このディスクのエントリの数 | エントリの数 |
| 10 | 2 | エントリの総数 | 同じ値 |
| 12 | 4 | 中央ディレクトリの大きさ | バイト数 |
| 16 | 4 | 中央ディレクトリの位置 | アーカイブの先頭からのバイト数 |
| 20 | 2 | コメントの長さ | 0 |

| 項目 | 決まり |
| --- | --- |
| 並び | ①`manifest.json` のヘッダとデータ → ②`mic/{連番}.wav` を連番の順に → ③中央ディレクトリ（同じ順） → ④終端 |
| 名前 | ASCII の固定の規則だけ（`ArchiveEntryName`）。会議タイトルを使わない。`../`・絶対パス・空の名前を作る経路がない（BD-EXP-06） |
| 日時 | 全エントリを、会議の開始日時（UTC）に固定する。MS-DOS 形式は「時 × 2048 + 分 × 32 + 秒 ÷ 2」「（年 − 1980）× 512 + 月 × 32 + 日」。1980 年より前・2107 年より後は 1980-01-01 にする。**同じ入力から、同じバイト列になる** |
| 上限 | ZIP64 は実装しない。全体が 4 GiB 以上、またはエントリが 65,535 件以上になる場合は、作る前に `ZIPFormat.fits` で判定して拒否する（`archiveLimitExceeded`） |

### 4.2 目録（`manifest.json`。形式の版 1）

```swift
/// 目録（manifest.json。形式の版 1）。受け取った側が、欠落と改変の有無を確かめられるようにする
public struct ExportManifest: Equatable, Sendable, Codable {
    public static let currentFormatVersion = 1

    /// Chunk の検証の結果
    public enum Verification: String, Sendable, CaseIterable, Codable, LogSymbolSource {
        case verified
        /// 保存時のハッシュと合わない（中身はそのまま入れている）
        case hashMismatch = "hash_mismatch"
    }

    public struct Chunk: Equatable, Sendable, Codable {
        public var sequenceNo: ChunkSequence
        public var startSample: Int64
        public var sampleCount: Int
        /// 保存時の値（ハッシュの記録）
        public var sha256: SHA256Digest
        public var verification: Verification
    }

    /// 含めていない理由
    public enum OmissionReason: String, Sendable, CaseIterable, Codable, LogSymbolSource {
        case missingSequence = "missing_sequence"
        case missingFile = "missing_file"
        case invalidHeader = "invalid_header"
        case quarantined
        /// 保存できないまま失われた
        case lostUnsaved = "lost_unsaved"
        /// まだ保存できておらず、メモリ上にだけある（緊急書き出しで出す）
        case unsavedInMemory = "unsaved_in_memory"
    }

    /// 含めていない区間（欠落・未保存）。長さが分からない場合は sampleCount が null
    public struct Omission: Equatable, Sendable, Codable {
        public var sequenceNo: ChunkSequence
        public var startSample: Int64?
        public var sampleCount: Int64?
        public var reason: OmissionReason
    }

    public var formatVersion: Int
    /// アプリの版（例: "1.0 (12)"）
    public var appVersion: String
    public var meetingId: MeetingID
    public var title: String
    public var startedAt: Timestamp
    /// 既知の分の総サンプル数
    public var totalSamples: Int64
    /// 長さの分からない欠落があり、totalSamples が既知の分だけである
    public var totalSamplesIsPartial: Bool
    public var finalizationMode: FinalizationMode?
    public var chunks: [Chunk]
    public var omissions: [Omission]
    public var gaps: [MeetingDocument.GapRecord]
    public var routes: [MeetingDocument.RouteRecord]
}
```

| 項目 | 内容 |
| --- | --- |
| `formatVersion`・`appVersion` | 目録の形式の版（1）、アプリの版（`CFBundleShortVersionString` とビルド番号） |
| `meetingId`・`title`・`startedAt` | 会議 ID、タイトル、開始日時（UTC の経過ミリ秒） |
| `totalSamples`・`totalSamplesIsPartial` | 既知の分の総サンプル数と、長さの分からない欠落があるか |
| `finalizationMode` | 確定方式（`barrier`・`acknowledged_loss`）。確定待ちなら `null` |
| `chunks` | 入れた Chunk ごとに、連番・開始サンプル・サンプル数・保存時の SHA-256・検証の結果（`verified`・`hash_mismatch`） |
| `omissions` | 入れていない区間ごとに、連番・開始サンプル・サンプル数（分からなければ `null`）・理由（欠番・ファイル欠損・不正なヘッダ・隔離・失われた未保存・メモリ上の未保存） |
| `gaps` | 空白区間（位置・長さ・理由・開始日時） |
| `routes` | 入力経路の履歴（種別・sample rate・チャンネル数・理由）。機器の名前は、アプリが記録していないので入らない |

- 欄の順序を固定して符号化する（`PersistedJSON.encode`）。同じ内容から、同じバイト列になる。
- 受け取った側は、`chunks` の SHA-256 と、展開した WAV のハッシュを比べて、改変の有無を確かめられる。`verification` が `hash_mismatch` の Chunk は、保存時と内容が違うことを示す。
- 目録には会議のメタデータ（D3）が入る。書き出しは利用者の意思なので含める（基本設計 §10）。文字起こしの本文（D2）は入れない。

### 4.3 書き込み

```swift
/// 無圧縮の ZIP を、一時ファイルへ順に書く（BD-EXP-02）。メモリに載せるのは 1 エントリ分だけである
public struct ArchiveWriter {
    private let files: any FileSystem
    private let destination: URL
    private let stamp: ZIPFormat.DOSDateTime
    private var entries: [ZIPEntry] = []
    private var offset: UInt32 = 0

    /// destination は tmp/export/{meetingId}/{exportId}/ の下。日時は、全エントリで会議の開始日時に固定する
    public init(destination: URL, startedAt: Date, files: any FileSystem) throws(StorageFailure) {
        self.files = files
        self.destination = destination
        stamp = ZIPFormat.DOSDateTime(startedAt)
        try files.create(destination, contents: [], stage: .createFile)
    }

    /// エントリを 1 つ足す（ローカルヘッダ → データ）。CRC-32 は zlib で計算する
    public mutating func add(name: String, data: [UInt8]) throws(StorageFailure) {
        let entry = ZIPEntry(name: name, crc32: Self.checksum(data), size: UInt32(data.count), offset: offset)
        let header = ZIPFormat.localHeader(entry, stamp: stamp)
        try files.append(header + data, to: destination)
        offset += UInt32(header.count + data.count)
        entries.append(entry)
    }

    /// 中央ディレクトリと終端を書いて、同期する
    public mutating func finish() throws(StorageFailure) {
        let directory = entries.flatMap { ZIPFormat.centralHeader($0, stamp: stamp) }
        let end = ZIPFormat.endOfCentralDirectory(
            entryCount: entries.count, directorySize: UInt32(directory.count), directoryOffset: offset,
        )
        try files.append(directory + end, to: destination)
        try files.synchronize(destination, durable: false)
    }

    public static func checksum(_ data: [UInt8]) -> UInt32 {
        let value = data.withUnsafeBufferPointer { crc32(0, $0.baseAddress, uInt($0.count)) }
        return UInt32(truncatingIfNeeded: value)
    }
}
```

- メモリに載せるのは、1 エントリ分（1 つの Chunk は最大 960,044 バイト）だけである。全体をメモリに載せない（BD-EXP-02）。
- CRC-32 は、OS 同梱の zlib（1.2.12）の `crc32` で計算する。エントリのデータを読んだ時点で値が分かるので、ヘッダを後から書き直さない。
- 目録は、全 Chunk の検証の結果が要るので、**先に全 Chunk を検証してから**作る（§3.1 の手順 6）。ZIP の書き込みでは、目録 → Chunk の順に書き、Chunk の中身は、このときにもう一度読む。
- 書き込み先は `tmp/export/{meetingId}/{exportId}/{外側のファイル名}` である。作成は `FileSystem.create`（保護クラス「最初のロック解除まで」。[storage.md](storage.md) §4.1）で行う。

---

## 5. ファイル名

満たす要件: SEC-INP-02

```swift
/// 外側のファイル名（SEC-INP-02）。会議タイトルを使うのは、ここだけである
public enum ExportFileName {
    public static let maxTitleBytes = 80
    public static let fallbackTitle = "会議"
    private static let forbidden: Set<Character> = ["/", "\\", ":", "?", "*", "\"", "<", ">", "|"]
    private static let reserved: Set<String> = ["CON", "PRN", "AUX", "NUL"]

    /// "{タイトル}_{開始日時 yyyyMMdd-HHmm}.{拡張子}"
    public static func make(title: String, startedAt: Date, timeZone: TimeZone, fileExtension: String) -> String {
        "\(sanitizedTitle(title))_\(stamp(startedAt, timeZone)).\(fileExtension)"
    }

    /// タイトルを、1 つのファイル名の部品として安全な形にする
    public static func sanitizedTitle(_ title: String) -> String {
        // 1. 正規化（NFC）。2. パス区切り・制御文字・改行・Windows で使えない記号を取り除く
        let kept = title.precomposedStringWithCanonicalMapping.filter { character in
            !forbidden.contains(character) && !character.isNewline
                && !character.unicodeScalars.contains { $0.properties.generalCategory == .control }
        }
        // 3. 前後の空白とドットを取り除く（先頭のドットも含む）
        var name = kept.trimmingCharacters(in: CharacterSet(charactersIn: " .\u{3000}"))
        // 4. OS が予約する名前（CON・NUL・COM1 など。拡張子つきも含む）を避ける
        if isReserved(name) {
            name = "_" + name
        }
        // 5. UTF-8 で 80 バイトまで。文字の途中で切らない
        var limited = ""
        for character in name {
            guard limited.utf8.count + String(character).utf8.count <= maxTitleBytes else { break }
            limited.append(character)
        }
        let trimmed = limited.trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        // 6. 空になったら既定名
        return trimmed.isEmpty ? fallbackTitle : trimmed
    }

    private static func isReserved(_ name: String) -> Bool {
        let stem = name.split(separator: ".", omittingEmptySubsequences: false).first.map { $0.uppercased() } ?? ""
        if reserved.contains(stem) {
            return true
        }
        let numbered = stem.hasPrefix("COM") || stem.hasPrefix("LPT")
        return numbered && stem.count == 4 && stem.last.map { ("1" ... "9").contains($0) } == true
    }

    private static func stamp(_ instant: Date, _ timeZone: TimeZone) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: instant)
        let padded = { (value: Int?, width: Int) -> String in
            let digits = String(value ?? 0)
            return String(repeating: "0", count: max(0, width - digits.count)) + digits
        }
        let day = padded(parts.year, 4) + padded(parts.month, 2) + padded(parts.day, 2)
        return day + "-" + padded(parts.hour, 2) + padded(parts.minute, 2)
    }
}
```

外側のファイル名は `{タイトル}_{開始日時 yyyyMMdd-HHmm}.{拡張子}` で、タイトルを基本設計 §5 の 6 つの処理で無害化する。

| 順 | 処理 | 例（入力 → 結果） |
| --- | --- | --- |
| 1 | 正規化（NFC） | — |
| 2 | パス区切り（`/`・`\`・`:`）、制御文字、改行、Windows で使えない記号（`?`・`*`・`"`・`<`・`>`・縦棒）を取り除く | `../../etc/passwd` → `etcpasswd` |
| 3 | 前後の空白とドットを取り除く（先頭のドットも） | `  .hidden. ` → `hidden` |
| 4 | OS が予約する名前（`CON`・`PRN`・`AUX`・`NUL`・`COM1`〜`COM9`・`LPT1`〜`LPT9`。拡張子つき・大文字小文字の違いも含む）は、先頭に `_` を付ける | `nul.txt` → `_nul.txt` |
| 5 | UTF-8 で 80 バイトまでに切る。文字の途中で切らない | 「あ」× 100 → 「あ」× 26（78 バイト） |
| 6 | 空になったら既定名（`会議`） | `...` → `会議` |

- 結果は、必ず 1 つのファイル名になる（区切りを含まない）。`../` を含むタイトルでも、ディレクトリをまたがない（TST-SEC-06）。
- 日時は、端末の時間帯で `yyyyMMdd-HHmm` にする（数字とハイフンだけ）。時間帯は引数で渡す（試験で固定できる）。
- 拡張子は `ExportKind.fileExtension(chunkCount:)`（`zip`・`wav`・`txt`・`md`）で、利用者の入力から作らない。`ExportFileName.make` の `fileExtension` には、この関数が返した値を渡す。
- 緊急書き出しの拡張子は、書き出す未保存の Chunk の数（§7 の手順 1 で受け取った写しの件数）を `chunkCount` に渡して決める。1 件なら `wav`、複数なら `zip` で、§7 の手順 3 で作る中身（1 件なら WAV、複数なら ZIP）と一致させる（`NSItemProvider` のデータの種類も同じ件数で選ぶ）。
- 緊急書き出しの名前は、タイトルの後ろに `_未保存` を付ける（例: 複数なら `定例_未保存_20261116-1430.zip`、1 件なら `定例_未保存_20261116-1430.wav`）。

---

## 6. 一時ファイルの寿命

満たす要件: SEC-DAT-06、SEC-DAT-05 ／ 決定: BD-EXP-03

| 時点 | 動作 | 行う処理 |
| --- | --- | --- |
| 作成 | `tmp/export/{meetingId}/{exportId}/` を作る（バックアップ除外・保護クラス「最初のロック解除まで」） | `StorageRoot.createExportDirectory`（[storage.md](storage.md) §2.2） |
| 共有の完了・取り消し | `{exportId}/` を丸ごと消す。`{meetingId}/` が空になったら、それも消す | `ExportService.finish(exportId:)` |
| 書き出しの失敗・中止 | 同上（失敗を返す前に消す） | `ExportService` |
| 次の起動 | `tmp/export/` の下をすべて消す | 起動時の復旧の手順 8（[storage.md](storage.md) §9） |
| 会議の削除 | その会議の新しい書き出しを拒否し、書き出し中なら中止の完了を待ってから、`tmp/export/{meetingId}/` を丸ごと消す | [storage.md](storage.md) §10 の手順 0〜2 |

- 一時ファイルは、保存ルートの下にだけ作る。OS の一時ディレクトリ（`NSTemporaryDirectory`）を使わない（静的解析の `no_direct_directory_access`）。
- 共有シートに渡すのは、この一時ファイルの URL である。共有先が複製を作った後は、アプリからは消せない（警告で伝える。§3.3）。
- `finish` は、同じ書き出し ID で 2 回呼ばれても、2 回目は何もしない（消すものがない）。
- 片付けの結果は、ログ `export_cleaned`（消した数）に出す。

---

## 7. 緊急書き出し

満たす要件: FR-EXP-04、FR-SAV-05 ／ 決定: BD-EXP-01

```swift
/// メモリ上の PCM から WAV のバイト列を作る（緊急書き出し。FR-EXP-04）
public enum WAVImage {
    /// PCM のバイト列（リトルエンディアン）
    public static func bytes(of samples: [Int16]) -> [UInt8] {
        var bytes: [UInt8] = []
        bytes.reserveCapacity(samples.count * 2)
        for sample in samples {
            let value = UInt16(bitPattern: sample)
            bytes.append(UInt8(value & 0xFF))
            bytes.append(UInt8(value >> 8))
        }
        return bytes
    }

    /// 44 バイトのヘッダ + PCM
    public static func file(of samples: [Int16]) -> [UInt8] {
        WAVHeader.encode(sampleCount: samples.count) + bytes(of: samples)
    }
}
```

| 順 | 処理 |
| --- | --- |
| 1 | `RecordingService` から、メモリに保持している未保存の Chunk（連番・開始サンプル・PCM）の写しを受け取る（[storage.md](storage.md) §7）。**保持は解放しない** |
| 2 | Chunk ごとに `WAVImage.file(of:)` で WAV のバイト列にする（確定のときと同じヘッダ） |
| 3 | 1 件なら WAV、複数なら ZIP にする。ZIP の中身は、`manifest.json`（未保存の Chunk の連番・開始サンプル・サンプル数。`omissions` ではなく `chunks` に入れ、SHA-256 はこの WAV のもの）と `mic/{連番}.wav` |
| 4 | 一時のディレクトリ（§3.1 の手順 3）を作れていれば、`tmp/export/{meetingId}/{exportId}/` に書いて同期し、URL を共有シートへ渡す（事前の空き容量の確認はしない。§3.1） |
| 5 | **一時のディレクトリを作れない・一時ファイルを書けない・同期できない場合**（容量の枯渇・権限など）は、作りかけの一時ファイルを消し、メモリ上のバイト列を、`ShareItem.data` として共有シートへ直接渡す（下の表） |
| 6 | 共有の完了・取り消しの後も、メモリ上の保持と未保存の記録は消さない（端末内の保存に成功するまで、Barrier は通らない。[storage.md](storage.md) §6） |

| メモリ上のデータを渡す経路（申し送り） | 内容 |
| --- | --- |
| 方法 | `NSItemProvider` に、データの種類（WAV は `com.microsoft.waveform-audio`、ZIP は `public.zip-archive`）と、データを返す処理を登録して、共有シートの項目にする（§3.3 の `ShareItem.data`）。名前は `suggestedName` で渡す |
| 複数の Chunk | ZIP をメモリ上で組み立てる（`ZIPFormat` の関数を、配列への追記で使う）。上限は、保持の上限（20 Chunk・約 18 MiB）なので、メモリに収まる |
| 制約 | 共有先によっては、ファイルの URL を要求して、メモリ上のデータを受け取れない。その場合は、共有シートに出る共有先が減る。画面に「保存先が見つからない場合は、空き容量を作ってから、もう一度お試しください」を添える |

- 録音中でも実行できる。録音を止めない（`RecordingService` から受け取るのは写しで、録音パイプラインを待たせない）。
- 書き出したこと（連番と時刻）は、ログ `export_finished`（種類 `emergency`）に残す。未保存の記録には書かない（保存できていないことは変わらないため）。

---

## 8. 文字起こしの形式

満たす要件: FR-EXP-02、FR-TRV-02・03

```swift
public enum MarkdownEscaper {
    private static let special: Set<Character> = [
        "\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", "|", "<", ">", "~",
    ]

    /// マークアップとして解釈されうる文字の前に、バックスラッシュを置く（FR-EXP-02）
    public static func escape(_ text: String) -> String {
        var result = ""
        for character in text {
            if special.contains(character) {
                result.append("\\")
            }
            result.append(character)
        }
        return result
    }
}

public enum TranscriptLine {
    /// 行頭の時刻（会議時刻）。"[HH:MM:SS]"
    public static func stamp(milliseconds: Int64) -> String {
        let total = max(0, milliseconds) / 1000
        let padded = { (value: Int64) in value < 10 ? "0\(value)" : "\(value)" }
        return "[\(padded(total / 3600)):\(padded(total / 60 % 60)):\(padded(total % 60))]"
    }
}
```

形式は基本設計 §8 のとおりである。版のファイル（`TranscriptVersionDocument`。[transcription.md](transcription.md) §6.2）から、次の規則で行を作る。

| 行の種類 | もとになる項目 | テキストの行 |
| --- | --- | --- |
| 冒頭 | タイトル、開始日時、長さ、エンジンの情報、版 | 1 行目にタイトル、2 行目に「開始: … ／ 長さ: … ／ エンジン: … ／ 版: N」、3 行目は空 |
| 欠落の概要（例外確定の会議） | `lossRecord` | 「この会議は、録音の一部（N か所）が欠けた状態で確定しています」 |
| セグメント | `segments` | `[HH:MM:SS] 本文` |
| 空白区間 | `gaps` | `[HH:MM:SS] （録音が中断していました。約 N 分。連結した録音では HH:MM:SS の位置）` |
| 失敗区間 | `failedChunks` | `[HH:MM:SS] （この区間は文字起こしに失敗しました）` |
| 利用できなかった区間 | `unavailableChunks` | `[HH:MM:SS] （この区間の録音は失われました。約 N 秒。連結した録音では HH:MM:SS の位置から無音）` |
| 長さの分からない欠落 | 範囲が `null` のもの | `（この区間の録音は失われました。長さは不明）`（時刻・長さ・位置を書かない） |

| 規則 | 内容 |
| --- | --- |
| 並び | 会議時刻の順。同じ時刻なら、空白区間 → 欠落 → セグメントの順 |
| 行頭の時刻 | 会議時刻（空白区間を含む。`AudioTimeline` で求める）を `TranscriptLine.stamp` で書式化する |
| 「連結した録音では」の位置 | サンプル番号 ÷ 16,000（§2）を、同じ書式で書く |
| Markdown | タイトルを見出し（`#`）、冒頭の情報を箇条書きにする。**タイトルと本文は `MarkdownEscaper.escape` を通す**（見出し・リンク・強調として解釈されうる文字の前に、バックスラッシュを置く）。丸かっこの注記の行は、エスケープせずに斜体にする |
| 文字コード・改行 | UTF-8（BOM なし）、LF |
| 本文の中の改行 | セグメントの本文には改行がない（制御文字を、読むときの検証で拒否している。[transcription.md](transcription.md) §4.4） |
| 低信頼度 | 書き出しでは区別しない（本文をそのまま出す） |

---

## 9. セキュリティ

満たす要件: SEC-DAT-05・06、SEC-NET-01、SEC-INT-01・03、SEC-INP-02、FR-EXP-03 ／ 脅威: T-07・09・12

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| 明示操作 | 書き出しは、利用者の操作からだけ始まる。自動の書き出し・同期は作らない。通信はしない（共有先への受け渡しは、OS の共有シートが行う） | SEC-DAT-06、SEC-NET-01 |
| 警告 | 毎回出す。了承しないと、書き出しを始めない（§3.1） | FR-EXP-03 |
| 一時ファイル | 保存ルートの下だけ。完了・取り消し・失敗・次の起動・会議の削除で消す（§6） | SEC-DAT-05・06、T-12 |
| 完全性 | 書き出す前に、各 Chunk のハッシュと、版のファイルのハッシュを照合する。結果を目録に入れる。合わない版は書き出さない（§3.1・§4.2） | SEC-INT-01・03、T-07 |
| ファイル名 | 外側の名前は、無害化の純粋関数で作る。アーカイブの中の名前は、固定の規則だけ（§4.1・§5） | SEC-INP-02、T-09 |
| 目録の中身 | 会議のメタデータ（D3）を含む。本文（D2）と、アプリが記録していない情報（機器の名前など）は入らない（§4.2） | BD-REC-07 |
| ログ | 種類・結果・バイト数だけ。タイトルとファイル名を出さない（§3.1） | SEC-LOG-03 |

**実装・レビュー時の注意点**（基本設計 [architecture.md](../02-basic-design/architecture.md) §12.2 の NS-04・09 と、基本設計 §10 の注意点を、本書の型の単位に具体化したもの）:

| No. | 注意点 | 確かめ方 |
| --- | --- | --- |
| NS-E-01 | 共有シート（`ActivityShareSheet`）を出すのは、`ExportStore` の 1 か所だけにする。ほかの画面から、直接出さない | `grep` で数える。UI テスト（警告を了承しないと、共有シートが出ない） |
| NS-E-02 | `ShareLink`・`.fileExporter`・`UIDocumentPickerViewController` など、警告と後始末を通らない書き出しの入口を足さない | 静的解析（`no_share_link`）、レビュー |
| NS-E-03 | アーカイブの中の名前を、`ArchiveEntryName` 以外から作らない。タイトル・利用者の入力を、中の名前に使わない | レビュー。単体（名前が固定の形であること） |
| NS-E-04 | 書き出しの対象に、会議の `quarantine/`・`.part`・`stt/`（Chunk ごとの認識結果）・SQLite のファイル・ほかの会議のファイルを足さない | 結合（アーカイブの中身の一覧が、目録と Chunk だけであること） |
| NS-E-05 | 一時ファイルを、`tmp/export/{meetingId}/{exportId}/` の外に作らない。OS の一時ディレクトリを使わない | 静的解析（`no_direct_directory_access`）、結合（TST-SEC-05） |
| NS-E-06 | 失敗・中止のすべての経路で、一時のディレクトリを消す。消す処理を、成功の経路にだけ書かない | 結合（各段で失敗を注入して、`tmp/export/` に何も残らないこと） |
| NS-E-07 | ハッシュが合わない Chunk を、黙って除外・修復しない。利用者に知らせ、目録に印を付ける | 結合（1 バイト変えた Chunk で、警告と印が出ること） |
| NS-E-08 | 長さの分からない欠落を、推定の長さの無音で補わない | 単体（`unknownLengthLoss` で作らないこと） |
| NS-E-09 | ログ・エラーの値に、外側のファイル名・タイトル・一時ファイルのパスを入れない | 単体（[architecture.md](architecture.md) §13.1 のエラーの値の検査・ログの検査） |
| NS-E-10 | 緊急書き出しの後に、メモリ上の保持と未保存の記録を消さない | 結合（TST-SAV-04。保持が解放されないこと） |
| NS-E-11 | P2 で AI の生の応答を保存する場合、書き出しの対象から外す（基本設計 §10） | レビュー（P2） |

---

## 10. テスト観点

満たす要件: FR-EXP-01〜04、TST-SAV-04、TST-SEC-05・06、TST-GEN-05

### 10.1 先に書いて失敗させるテスト（書き出しの実装の順）

| 順 | テスト | 確かめること | 要件 |
| --- | --- | --- | --- |
| 1 | ZIP のヘッダ | 3 種のヘッダが、§4.1 の表のバイト配置になること（既知の値で、バイト単位に比べる）。MS-DOS 形式の日時 | FR-EXP-01 |
| 2 | ZIP の上限 | 4 GiB 以上・65,535 件以上を拒否すること | FR-EXP-01 |
| 3 | ファイル名 | `../`・`/`・制御文字・Windows で使えない記号・先頭のドット・予約名・非常に長いタイトル・絵文字・空文字 | TST-SEC-06 |
| 4 | 連結の計画 | 欠番の無音、一部だけ救済した Chunk の残りの無音、末尾の未保存、空の会議。長さの分からない欠落と範囲の重なりの拒否 | FR-EXP-01 |
| 5 | 文字起こしの行 | 行頭の時刻の書式、Markdown のエスケープ。空白区間・失敗区間・例外確定を含む版から、期待どおりの行ができること | FR-EXP-02 |
| 6 | WAV のバイト列 | メモリ上の PCM から作った WAV が、ヘッダの検証に通ること | FR-EXP-04 |
| 7 | CRC-32 | 既知の値（`123456789` → `CBF43926`） | FR-EXP-01 |
| 8 | アーカイブの書き込み（結合） | 同じ入力から同じバイト列になること。OS の標準機能で展開でき、各ファイルの内容が元と一致すること | FR-EXP-01 |
| 9 | 録音一式（結合） | 目録の Chunk の一覧・SHA-256・検証の結果が、会議の内容と一致すること。アーカイブの中身が、目録と Chunk だけであること | FR-EXP-01 |
| 10 | 連結した録音（結合） | 全体のサンプル数が計画と一致し、`AVAudioFile` で開けること。欠落した区間が無音であること | FR-EXP-01 |
| 11 | 完全性（結合） | Chunk を 1 バイト変えてから書き出す。警告が出て、目録に印が付くこと。版のファイルを変えると、文字起こしの書き出しが拒否されること | SEC-INT-01・03 |
| 12 | 未保存の Chunk がある会議（結合） | 案内が出ること。ZIP に未保存の Chunk が入らず、目録の `omissions` に入ること。連結した録音で、その区間が無音になること | FR-EXP-01 |
| 13 | 長さの分からない欠落（結合） | 連結した録音が作られず、理由が出ること。録音一式は作れ、目録に長さ `null` で入ること。文字起こしが長さを書かないこと | FR-EXP-01 |
| 14 | 一時ファイル（結合） | 完了・取り消し・失敗・中止のそれぞれで、`tmp/export/` に何も残らないこと。起動時の復旧で消えること | SEC-DAT-06 |
| 15 | 削除との関係（結合） | 削除を始めた会議の書き出しが拒否されること。書き出し中の会議を削除すると、中止の完了の後に `tmp/export/{meetingId}/` が消え、ほかの会議の一時ファイルが残ること | TST-SEC-05 |
| 16 | 容量（結合） | 緊急書き出し以外の種類で、空き容量が足りないとき、作成を始めずに `insufficientSpace` を返すこと | FR-EXP-01 |
| 17 | 緊急書き出し（結合） | 書き込みを失敗させてメモリに保持させ、書き出した WAV が再生できること。保持が解放されないこと。次の 3 つのそれぞれで `insufficientSpace`・`storage` を返さずにメモリ上のデータの経路になり、一時ファイルが残らないこと: ①一時のディレクトリの作成の失敗、②空き容量が予備（200 MB）より少ない（`FixedFreeSpace`。事前の確認をしない）、③一時ファイルの書き込みの失敗（`FaultInjectingFileSystem`） | TST-SAV-04 |
| 20 | 連結した録音の注記 | `ICMT` の本文の長さが偶数・奇数のそれぞれで、§2 の表のバイト配置（詰め物の有無）になること。RIFF の大きさがファイル全体 − 8 と一致し、`data` が 44 バイト目から始まること。上限の判定が注記の長さ（詰め物を含む）で変わること。ASCII 以外の本文を拒否すること | FR-EXP-01、SEC-INT-01 |
| 21 | スナップショット（結合） | §3.4 の試験の行 | FR-EXP-01 |
| 18 | 古い応答（単体） | 前の書き出し ID の進み具合・完了が、`ExportStore` に捨てられること | TST-GEN-05 |
| 19 | UI テスト: 警告 | 警告を了承しないと、共有シートが出ないこと | FR-EXP-03 |

### 10.2 差し替え

| 部品 | 内容 |
| --- | --- |
| `TemporaryDirectory`・`FaultInjectingFileSystem`・`FixedFreeSpace` | 保存ルート・書き込みの失敗・空き容量（[storage.md](storage.md) §12.2） |
| `SequentialIDGenerator` | 書き出し ID を決定的にする |
| 大きな入力 | 上限の判定（`ZIPFormat.fits`）は、大きさの値だけを渡して確かめる。実際に数百 MB を書かない |
| 共有シート | 自動テストの対象にしない。入口（警告）と後始末（`finish`）を、`ExportStore` の単体テストと UI テストで押さえる |

### 10.3 実装前に確かめたこと（2026-11-16）

| 項目 | 結果 |
| --- | --- |
| ビルド（Mac） | `MinutesDomain/Export/` と `ArchiveWriter` が、Swift 6.4.2・Swift 6 言語モード・警告をエラーとして扱う設定でビルドできた |
| ビルド（iOS） | `ActivityShareSheet`・`ShareItem` を、iOS 27.2 SDK に対してビルドできた |
| テスト | 書き出しに関わる 9 件が通った（ZIP のヘッダ、上限、ファイル名、連結の計画 2 件、文字起こしの書式、WAV のバイト列、CRC-32、アーカイブの書き込み）。一時の Package の全体では 87 件 |
| ZIP の展開 | 2 つのエントリ（目録と 1 つの WAV）を持つアーカイブを作り、macOS の `/usr/bin/unzip` で展開できた。展開したファイルの内容は、元と一致した。同じ入力から 2 回作ったアーカイブは、同じバイト列だった |
| CRC-32 | zlib の `crc32` が、既知の値（`123456789` → `CBF43926`）を返した |
| ファイル名 | `../../etc/passwd` → `etcpasswd`、`nul.txt` → `_nul.txt`、`COM10` → そのまま、空・`...`・`/` → `会議`、「あ」× 100 → 26 文字（78 バイト）を確かめた |
| 整形・静的解析 | SwiftFormat 0.64.7 で整形済み。SwiftLint 0.65.1 は、複数行の配列の末尾カンマ（[architecture.md](architecture.md) §11.2 の既知の食い違い）を除いて違反なし |

**設計・実装時の注意点:**

- 書き出しの試験でも、テストごとの一時ディレクトリを保存ルートとして使う。
- ZIP の試験は、バイト配置（単体）と、展開できること（結合）の両方を行う。片方だけにしない。
- 共有先への受け渡しは、実機で確かめる（§10.4）。

### 10.4 シミュレータ・自動テストで確かめられないこと

| 事項 | 自動テストでの扱い | 手動・実機での確認 |
| --- | --- | --- |
| 自作の ZIP が、Windows と iOS の標準機能でも展開できる | macOS の `unzip` で確かめる | Windows のエクスプローラーと、iOS の「ファイル」アプリで展開する（リリース前） |
| 共有シートの完了・取り消しが届く | `onFinish` から後始末までを単体で確かめる | 実機で、保存・AirDrop・取り消しのそれぞれの後に、`tmp/export/` が空であること |
| 大きなファイル（120 分の録音一式は約 220 MiB）を、共有先が受け取れる | — | 実機（TST-DEV-09 と合わせて） |
| メモリ上のデータを、共有先が受け取れる | `ShareItem.data` のビルドだけ | 実機で、容量を埋めた状態から緊急書き出しを行い、受け取れる共有先を記録する（TST-DEV-06 と合わせて） |

---

## 11. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

状態はすべて「未実装」である（実装の作業で、テスト名と状態を書き換える）。

| 要件 | テスト（Suite / 表示名の先頭） | 置き場所 | 状態 |
| --- | --- | --- | --- |
| FR-EXP-01 | 書き出しの純粋ロジック / FR-EXP-01 ZIP のヘッダは…、FR-EXP-01 4 GiB 以上・65,535 件以上になる…、FR-EXP-01 連結した録音は…、FR-EXP-01 長さの分からない欠落… | MinutesDomainTests | 未実装 |
| FR-EXP-01 | アーカイブの書き込み / FR-EXP-01 CRC-32 は既知の値と一致する、FR-EXP-01 作ったアーカイブは… | MinutesStorageTests | 未実装 |
| FR-EXP-01、SEC-INT-01 | 書き出しの手順 / FR-EXP-01 目録が会議の内容と一致する、SEC-INT-01 ハッシュが合わない Chunk は警告と印が付く | MinutesStorageTests | 未実装 |
| FR-EXP-02 | 書き出しの純粋ロジック / FR-EXP-02 文字起こしの行頭の時刻と…。文字起こしの形式 / FR-EXP-02 空白区間・失敗区間・例外確定を含む版から… | MinutesDomainTests | 未実装 |
| FR-EXP-03 | UI: FR-EXP-03 警告を了承しないと共有シートが出ない | MinutesMobileUITests | 未実装 |
| FR-EXP-04、TST-SAV-04 | 書き出しの純粋ロジック / FR-EXP-04 メモリ上の PCM から作った WAV は…。緊急書き出し / TST-SAV-04 メモリにしかない Chunk を書き出せ、保持が解放されない | MinutesDomainTests・MinutesApplicationTests | 未実装 |
| SEC-INP-02、TST-SEC-06 | 書き出しの純粋ロジック / TST-SEC-06 タイトルから作るファイル名は… | MinutesDomainTests | 未実装 |
| SEC-DAT-06 | 書き出しの手順 / SEC-DAT-06 完了・取り消し・失敗・中止のどれでも一時ファイルが残らない | MinutesStorageTests | 未実装 |
| SEC-DAT-05、TST-SEC-05 | 削除 / TST-SEC-05 書き出し中の会議を削除すると、中止の後に一時ファイルが消える（[storage.md](storage.md) §13 と対） | MinutesStorageTests | 未実装 |
| SEC-INT-03 | 書き出しの手順 / SEC-INT-03 照合に通らない版は書き出さない（[transcription.md](transcription.md) §12 の TST-SEC-11 と対） | MinutesStorageTests | 未実装 |
| TST-GEN-05 | 書き出しの Store / TST-GEN-05 前の書き出し ID の応答は捨てられる | MinutesMobileTests | 未実装 |
| SEC-NET-01 | 通信ゼロの試験の対象に、書き出しまでを含める（[architecture.md](architecture.md) §13.1 の 15） | MinutesMobileTests | 未実装 |
| FR-EXP-01・04 | 手動・実機での確認（§10.4） | 実機 | 未実施 |

---

## 12. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [export.md](../02-basic-design/export.md) §12 の 6 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| ZIP の各ヘッダのバイト配置（Minutes `src/export/zip.ts` を基準にする） | ローカルファイルヘッダ（30 バイト）・中央ディレクトリのヘッダ（46 バイト）・終端（22 バイト）の配置を表にし、`ZIPFormat` として実装した。参照元が手元にないため、公開の仕様から起こし、OS 標準の機能で展開できることを確かめた | §4.1 |
| 目録の項目と形式の版 | `ExportManifest`（形式の版 1）。入れた Chunk・入れていない区間・空白区間・経路・確定方式 | §4.2 |
| 書き出しの開始時のスナップショット（保存済みの Chunk の一覧・未保存の記録・空白区間・欠落の記録・会議のメタデータを、保存の actor の中断点を含まない 1 つのメソッドの中で、DB を読まずに actor のメモリ上の状態から取得する問い合わせ）の型と、これらの値を actor のメモリ上に持つ形・書き込みの前に状態へ反映する順序、書き出しの途中の保存の再試行の成功がスナップショットを変えないことの保証の方法（§2・§4.1） | 型は `ExportSnapshot`（`ExportSource.exportSnapshot(of:)`）。`MeetingStore` が会議ごとの `MeetingState`（値型）を持ち、どの変更も同じ同期のメソッドの中で状態を先に更新してから書き込みを発行する。スナップショットは `await` のないメソッドで作る値型の写しで、後の再試行の成功を見ない | §3.4 |
| 共有シートを包む部品と、完了の受け取り方 | `ActivityShareSheet`。`completionWithItemsHandler` で完了と取り消しを受け取り、後始末へつなぐ | §3.3 |
| メモリ上のデータを直接渡す経路の実装 | `ShareItem.data`: `NSItemProvider` にデータの種類と中身を登録して、共有シートの項目にする。緊急書き出しは事前の空き容量の確認をせず、一時のディレクトリ・ファイルを作れなければこの経路へ移る | §3.1・§7 |
| 連結した録音の注記（不一致・補った欠落区間）の `LIST`（`INFO`）チャンクのバイト配置（`ICMT` の終端の NUL と、偶数長にするための詰め物を含む。詰め物の 1 バイトで総サンプル数の上限が変わるため、上限の判定と同じ計算にする） | `WAVAnnotation.listChunk(comment:)`。`data` の後ろに `LIST`・大きさ・`INFO`・`ICMT`・大きさ（本文 + NUL）・本文・NUL・詰め物（奇数のときだけ 1 バイト）。上限は `WAVAnnotation.fits` で、注記の長さ（詰め物を含む）を足して判定する | §2 |

---

## 13. 断定しない事項・確かめていない事項

満たす要件: FR-EXP-01・04、SEC-DAT-06 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| 自作の ZIP が、どの環境でも展開できる | macOS の `unzip` でだけ確かめた | Windows と iOS の標準機能で展開する（§10.4） |
| ZIP のバイト配置が、Minutes `src/export/zip.ts` と一致する | 参照元が手元にないため、照合していない。公開の仕様に従っている | 参照元を見られるようになったら、差を確かめる（差があっても、展開できることを優先する） |
| 共有シートの完了が、必ず届く | アプリが終了すると届かない | 次の起動での削除（§6） |
| 大きなファイルを、共有先が受け取れる | 共有先のアプリによる | 連結した録音とアーカイブの両方を用意する。実機で確かめる |
| メモリ上のデータ（`NSItemProvider`）を、共有先が受け取れる | ビルドだけを確かめた。共有先によっては、ファイルを要求する | 実機で確かめ、受け取れない共有先があれば画面に示す |
| 共有シートが、SwiftUI のシートの中から正しく出る | 確かめていない | 書き出しの実装 |
| 120 分の録音一式を作るのにかかる時間 | 測っていない | 書き出しの実装（進み具合を表示する） |
| Markdown のエスケープの対象の文字が、十分である | 一般的な記法の文字を対象にした。表示するアプリの方言による | 書き出した Markdown を、代表的なアプリで開いて確かめる |
| 連結した録音の上限（4 GiB 未満。約 37 時間分） | 実用上は届かない | — |
| 2026-11-27 に足したコード片（§2 の `WAVAnnotation`、§3.4 の `ExportSnapshot`・`ExportSource`）がビルドできる | ビルドしていない（§10.3 の確認は、それより前の版のもの） | 書き出しの実装の最初にビルドとテストで確かめ、本書を直す |

---

## 14. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- iOS 27.2 SDK の `zlib.h`（zlib 1.2.12 の `crc32`）、`UIActivityViewController.h`（`completionWithItemsHandler`）、`NSItemProvider.h`（データの種類と中身の登録・`suggestedName`）
- Xcode 27.2（27B52）・Swift 6.4.2 の `swift build`・`swift test`・`xcodebuild`（一時の Package で実行。§10.3 の結果はこの実行による）
- macOS 27.2 の `/usr/bin/unzip`（作ったアーカイブの展開の確認）
- ZIP の形式の仕様: [PKWARE: APPNOTE.TXT - .ZIP File Format Specification](https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT)（版 6.3.10、2022-11-01 改訂。確認日 2026-11-27。ローカルファイルヘッダ（4.3.7）・中央ディレクトリ（4.3.12）・終端（4.3.16）の節があることを確かめた）。§4.1 のバイト配置は、この仕様の無圧縮・単一ディスク・ZIP64 なしの形である。§4.1 の表と仕様のバイト単位の照合はしておらず、**配置の正しさは、OS 標準の `unzip` で検査・展開できたことで確かめた**
- **出典の URL について（確認日 2026-11-27）:** 上の SDK のヘッダ・`.swiftinterface` は、Xcode に同梱されたローカルのファイルで、公開の URL はない。版の根拠は、SDK の版（iOS 27.2）は `xcodebuild -showsdks`、Xcode 27.2（27B52）は `xcodebuild -version`、Swift 6.4.2 は `swift --version`、各 API の対応 OS は SDK の宣言（`@available`・`API_AVAILABLE`）で確かめたものである（[dev-environment.md](dev-environment.md) §2）。Apple の文書のページは、本文が JavaScript で描かれ、対応 OS の記述を取得できないので、版の根拠にしない
- Minutes `src/export/zip.ts`、`docs/design-local-phase1.md` §3.4・§4.5（別リポジトリ・読み取り専用。本作業では参照できず、基本設計の記述に基づいた）
