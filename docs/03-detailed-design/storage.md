# MinutesMobile 詳細設計書 ── 保存・確定・復旧

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版・実装前） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | FR-CNS-02、FR-SAV-01〜05、FR-FIN-01〜03、FR-RCV-01・02・04、FR-MTG-03、NFR-REL-04、NFR-CAP-01・02、NFR-MNT-05、SEC-DAT-01〜05、SEC-INP-01・03、SEC-INT-01・02、INV-04・05・10 |
| 関係する制約 | CON-04・09 |
| 対応する基本設計 | [../02-basic-design/storage.md](../02-basic-design/storage.md)（設計決定 BD-STO-01〜09 を引用する） |
| 関連文書 | [architecture.md](architecture.md)・[recording.md](recording.md)・[transcription.md](transcription.md)・[ui.md](ui.md)・[export.md](export.md) |

本書は、Chunk とメタデータをファイルと SQLite に保存する方法、停止から確定までの手順、起動時の復旧、削除を、型とアルゴリズムの単位で定める。Chunk を作るまでは [recording.md](recording.md)、文字起こしのジョブと版は [transcription.md](transcription.md) にある。

**本書のコード片は、実装前に一時の Package でビルドとテストを通したものである**（§12.3）。実ファイルはまだないので、照合の印（`<!-- file: -->`）は付けていない。実装の作業で印を付ける（[README.md](README.md) §3）。

---

## 1. 位置づけと範囲

満たす要件: FR-SAV-01・02、NFR-MNT-02・04、INV-10 ／ 決定: BD-STO-01・02、UD-10

| 本書で決めること | 節 | 置き場所（[architecture.md](architecture.md) §2） |
| --- | --- | --- |
| パスの組み立て、保存ルート、属性 | §2 | `MinutesDomain/Storage/StorageLayout.swift`、`MinutesStorage/Storage/StorageRoot.swift` |
| `meeting.json`・ハッシュの記録・削除要求の形式、表の定義、移行 | §3 | 型は Domain、DDL と移行は `MinutesStorage/SQLite/` |
| ファイル操作、SQLite のラッパー、Chunk の保存の手順 | §4 | protocol と判定は Domain、実装は `MinutesStorage/Files/`・`SQLite/`・`Chunks/`・`Meetings/` |
| 状態の保存の順序と、失敗したときの扱い | §5 | `MinutesStorage/Meetings/MeetingStore.swift` |
| Finalization Barrier、確定の手順 | §6 | 判定は Domain、手順は `MinutesApplication/FinalizationService` |
| 空き容量の縮退、未保存の Chunk の保持 | §7 | 判定は Domain、手順は `RecordingService` |
| 保護クラスの切り替え、バックアップ除外 | §8 | 判定は Domain、属性の操作は `StorageRoot.swift` |
| 起動時の復旧（照合） | §9 | `MinutesApplication/RecoveryService` |
| 削除、データ使用量 | §10 | `MinutesApplication/DeletionService` |

基本設計の決定（BD-STO-01〜09）は変えていない。基本設計を書いた後に分かったことと、本書で細部を決めたことは次のとおりである。

| 事項 | 内容 | 節 |
| --- | --- | --- |
| バックアップ除外の読み出し | 親のディレクトリが除外されていると、配下も「除外」と読める（Mac 上で確認）。起動時の検証で意味を持つのは保存ルートの属性である。また、`URL` は属性の値をキャッシュするので、検証では読む前にキャッシュを捨てる | §2.3 |
| 保護クラスの付け方 | 新しいファイルは、作った直後に必ず「最初のロック解除まで」の保護クラスを明示して付ける（ディレクトリからの引き継ぎに頼らない） | §4.1 |
| 記録のない Chunk と内容の異なる Chunk | 同じ連番の正式名のファイルがあり、ハッシュの記録がなく、内容が新しい Chunk と異なる場合は、記録を作らない（基本設計 §4 に同じ内容を追記した） | §4.4 |
| 保護データを読めない間の起動 | 端末の再起動後・最初のロック解除の前は DB を開けない。この間の失敗を「壊れた DB」として扱わない | §3.5 |

---

## 2. ファイル配置と保存ルート

満たす要件: FR-SAV-02、SEC-DAT-03・04、SEC-INP-01、TST-GEN-02 ／ 決定: BD-STO-05・06

### 2.1 パスの組み立て

配置は基本設計 §2 のとおりである。パスを組み立てるのは `StorageLayout` だけで、部品は会議 ID・連番・版・実行番号・書き出し ID に限る（BD-STO-06）。

```swift
import Foundation

/// 保存ルートの下のパス（FR-SAV-02）。部品は会議 ID・連番・版・実行番号だけで、利用者の入力を受け取らない（BD-STO-06）
public struct StorageLayout: Equatable, Sendable {
    /// 保存ルート（{Application Support}/MinutesMobile）
    public let root: URL

    public init(root: URL) {
        self.root = root
    }

    public var databaseDirectory: URL {
        root.appendingPathComponent("db", isDirectory: true)
    }

    public var database: URL {
        databaseDirectory.appendingPathComponent("minutes.sqlite")
    }

    public var rebuildPending: URL {
        databaseDirectory.appendingPathComponent("rebuild-pending")
    }

    /// 移行の前に作る DB の複製（1 世代だけ残す）
    public var migrationBackup: URL {
        databaseDirectory.appendingPathComponent("minutes.sqlite.premigration")
    }

    public var recordings: URL {
        root.appendingPathComponent("recordings", isDirectory: true)
    }

    public var databaseQuarantine: URL {
        root.appendingPathComponent("quarantine", isDirectory: true)
    }

    /// 壊れた DB の退避先。名前に退避の時刻（経過ミリ秒）を入れる
    public func quarantinedDatabase(at time: Timestamp) -> URL {
        databaseQuarantine.appendingPathComponent("minutes-\(time.milliseconds).sqlite")
    }

    public var tombstones: URL {
        root.appendingPathComponent("tombstones", isDirectory: true)
    }

    public var exportRoot: URL {
        root.appendingPathComponent("tmp/export", isDirectory: true)
    }

    public func meetingDirectory(_ meeting: MeetingID) -> URL {
        recordings.appendingPathComponent(meeting.description, isDirectory: true)
    }

    public func meetingFile(_ meeting: MeetingID) -> URL {
        meetingDirectory(meeting).appendingPathComponent("meeting.json")
    }

    public func chunkDirectory(_ meeting: MeetingID) -> URL {
        meetingDirectory(meeting).appendingPathComponent("mic", isDirectory: true)
    }

    public func chunk(_ meeting: MeetingID, _ sequence: ChunkSequence) -> URL {
        chunkDirectory(meeting).appendingPathComponent("\(sequence.fileStem).wav")
    }

    public func partFile(_ meeting: MeetingID, _ sequence: ChunkSequence) -> URL {
        chunkDirectory(meeting).appendingPathComponent("\(sequence.fileStem).wav.part")
    }

    public func hashRecord(_ meeting: MeetingID, _ sequence: ChunkSequence) -> URL {
        chunkDirectory(meeting).appendingPathComponent("\(sequence.fileStem).sha256.json")
    }

    public func meetingQuarantine(_ meeting: MeetingID) -> URL {
        meetingDirectory(meeting).appendingPathComponent("quarantine", isDirectory: true)
    }

    /// 救済できなかった書きかけの退避先。ファイル名に連番を残す
    public func quarantinedPart(_ meeting: MeetingID, _ sequence: ChunkSequence) -> URL {
        meetingQuarantine(meeting).appendingPathComponent("part-\(sequence.fileStem).wav.part")
    }

    /// 同じ連番で内容が異なる Chunk（競合の版）の退避先。digestPrefix は SHA-256 の先頭 16 桁
    public func conflictingChunk(_ meeting: MeetingID, _ sequence: ChunkSequence, digestPrefix: String) -> URL? {
        guard digestPrefix.utf8.count == 16, digestPrefix.utf8.allSatisfy(Self.isLowerHex) else { return nil }
        return meetingQuarantine(meeting).appendingPathComponent("conflict-\(sequence.fileStem)-\(digestPrefix).wav")
    }

    public func tombstone(_ meeting: MeetingID) -> URL {
        tombstones.appendingPathComponent(meeting.description)
    }

    public func exportDirectory(_ meeting: MeetingID) -> URL {
        exportRoot.appendingPathComponent(meeting.description, isDirectory: true)
    }

    public func exportDirectory(_ meeting: MeetingID, export: UUID) -> URL {
        exportDirectory(meeting).appendingPathComponent(export.uuidString.lowercased(), isDirectory: true)
    }

    /// 実行番号・版は 1〜9,999（ファイル名は 4 桁）
    public func runDirectory(_ meeting: MeetingID, run: Int) -> URL? {
        guard let stem = Self.fourDigits(run) else { return nil }
        return meetingDirectory(meeting).appendingPathComponent("stt/run-\(stem)", isDirectory: true)
    }

    public func transcriptVersion(_ meeting: MeetingID, version: Int) -> URL? {
        guard let stem = Self.fourDigits(version) else { return nil }
        return meetingDirectory(meeting).appendingPathComponent("transcript/v\(stem).json")
    }

    public func transcriptHashRecord(_ meeting: MeetingID, version: Int) -> URL? {
        guard let stem = Self.fourDigits(version) else { return nil }
        return meetingDirectory(meeting).appendingPathComponent("transcript/v\(stem).sha256.json")
    }

    private static func fourDigits(_ value: Int) -> String? {
        guard (1 ... 9999).contains(value) else { return nil }
        let digits = String(value)
        return String(repeating: "0", count: 4 - digits.count) + digits
    }

    private static func isLowerHex(_ byte: UInt8) -> Bool {
        (48 ... 57).contains(byte) || (97 ... 102).contains(byte)
    }
}
```

- `StorageLayout` は文字列を引数に取らない。会議 ID は `MeetingID`（UUID の形式を検証済み）、連番は `ChunkSequence`（0〜999,999）で受け取る（[architecture.md](architecture.md) §3）。`../` や `/` を含む値は、これらの型を作る時点で拒否される（SEC-INP-01、TST-SEC-06）。
- 版・実行番号は 1〜9,999 の整数で、範囲の外なら `nil` を返す。呼び出し側は `StorageFailure`（種類 `invalid_path`）に写す。
- 競合の版のファイル名は `conflict-{連番 6 桁}-{SHA-256 の先頭 16 桁}.wav`、救済できなかった書きかけは `part-{連番 6 桁}.wav.part` とする。どちらも会議の `quarantine/` に置き、連番を名前に残す（DB を作り直したときに、どの連番の隔離かを復元するため。§9）。
- ディレクトリの中身を一覧するときは、名前を上の形式に照らして解釈する。形式に合わない名前のファイルは、会議のデータとして扱わない（削除もしない）。

### 2.2 保存ルート

ディレクトリを作るのは `StorageRoot` だけである（BD-STO-05。静的解析の `no_direct_directory_access` の例外の場所。[architecture.md](architecture.md) §2.4）。`FileSystem`（§4.1）にはディレクトリを作る操作がない。

```swift
/// 保存ルート。**ディレクトリを作ってよいのはこの型だけ**で、作るときに保護クラスとバックアップ除外を付ける
/// （BD-STO-05。.swiftlint.yml の no_direct_directory_access の例外）
public struct StorageRoot: Sendable {
    public let layout: StorageLayout

    /// base は {Application Support}。テストでは一時ディレクトリを渡す
    public init(base: URL) {
        layout = StorageLayout(root: base.appendingPathComponent("MinutesMobile", isDirectory: true))
    }

    /// 本番の保存先（Application Support）。Documents は使わない（SEC-DAT-04）
    public static func applicationSupport() throws(StorageFailure) -> URL {
        do {
            return try FileManager.default.url(
                for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true,
            )
        } catch {
            throw FileAttributes.failure(error, .createDirectory)
        }
    }

    /// 起動のたびに呼ぶ。足りないディレクトリを作り、属性を検証して、外れていれば付け直す。
    /// 戻り値は付け直した数（0 でなければ storage_attributes_repaired を記録する。TST-SEC-04）
    public func prepare() throws(StorageFailure) -> Int {
        var repaired = 0
        let fixed = [layout.root, layout.databaseDirectory, layout.recordings, layout.databaseQuarantine]
        for directory in fixed + [layout.tombstones, layout.exportRoot] {
            let existed = FileManager.default.fileExists(atPath: directory.path)
            try ensureDirectory(directory)
            if existed, try repairAttributes(of: directory) {
                repaired += 1
            }
        }
        return repaired
    }

    /// 会議ディレクトリと、その下の mic・stt・transcript・quarantine を作る
    public func createMeetingDirectories(_ meeting: MeetingID) throws(StorageFailure) {
        let base = layout.meetingDirectory(meeting)
        let names = ["mic", "stt", "transcript", "quarantine"]
        let children = names.map { base.appendingPathComponent($0, isDirectory: true) }
        for directory in [base] + children {
            try ensureDirectory(directory)
        }
    }

    /// 文字起こしの実行番号のディレクトリ（transcription.md §5）。番号が範囲の外なら invalidPath
    public func createRunDirectory(_ meeting: MeetingID, run: Int) throws(StorageFailure) {
        guard let directory = layout.runDirectory(meeting, run: run) else {
            throw StorageFailure(kind: .invalidPath, stage: .createDirectory)
        }
        try ensureDirectory(directory)
    }

    /// 書き出しの一時ディレクトリ（export.md §6）
    public func createExportDirectory(_ meeting: MeetingID, export: UUID) throws(StorageFailure) {
        try ensureDirectory(layout.exportDirectory(meeting, export: export))
    }

    private func ensureDirectory(_ url: URL) throws(StorageFailure) {
        guard !FileManager.default.fileExists(atPath: url.path) else { return }
        do {
            try FileManager.default.createDirectory(
                at: url, withIntermediateDirectories: true,
                attributes: [.protectionKey: ProtectionLevel.untilFirstUnlock.fileProtection],
            )
        } catch {
            throw FileAttributes.failure(error, .createDirectory)
        }
        try FileAttributes.setExcludedFromBackup(url)
    }

    /// 付け直したら真
    private func repairAttributes(of url: URL) throws(StorageFailure) -> Bool {
        var repaired = false
        if !FileAttributes.isExcludedFromBackup(url) {
            try FileAttributes.setExcludedFromBackup(url)
            repaired = true
        }
        // 保護クラスを読める環境（実機）でだけ検証する
        let expected = ProtectionLevel.untilFirstUnlock.fileProtection
        if let current = FileAttributes.protection(of: url), current != expected {
            try FileAttributes.setProtection(.untilFirstUnlock, at: url)
            repaired = true
        }
        return repaired
    }
}
```

| ディレクトリ | 作る時点 | 作る関数 |
| --- | --- | --- |
| 保存ルート、`db/`、`recordings/`、`quarantine/`、`tombstones/`、`tmp/export/` | 起動のたび（なければ） | `prepare()` |
| `recordings/{meetingId}/` と、その下の `mic/`・`stt/`・`transcript/`・`quarantine/` | 会議の作成（[recording.md](recording.md) §3.4 の手順 5） | `createMeetingDirectories(_:)` |
| `stt/run-{実行番号}/` | 実行番号の発番の直後（[transcription.md](transcription.md) §5） | `createRunDirectory(_:run:)` |
| `tmp/export/{meetingId}/{exportId}/` | 書き出しの開始（[export.md](export.md) §6） | `createExportDirectory(_:export:)` |

- 本番の保存先は `StorageRoot.applicationSupport()`（Application Support）で、`Documents` と一時領域（`NSTemporaryDirectory`）は使わない（SEC-DAT-04）。テストは `TemporaryDirectory` の URL を `StorageRoot(base:)` に渡す（TST-GEN-02）。
- **墓標のある会議にはディレクトリを作らない。** `createMeetingDirectories`・`createRunDirectory`・`createExportDirectory` を呼ぶ `MeetingStore` の入口が、会議に削除要求がないことを先に確かめる（§10）。

### 2.3 属性（保護クラスとバックアップ除外）

属性の読み書きは、`StorageRoot.swift` の `FileAttributes` に集める（保護クラスの設定と読み出し、バックアップ除外の設定と読み出しの 4 つ）。

| 項目 | 設計 |
| --- | --- |
| 作成時 | ディレクトリは、保護クラス「最初のロック解除まで」（`completeUntilFirstUserAuthentication`）を指定して作り、続けてバックアップ除外を付ける |
| 起動時の検証 | `prepare()` が、すでにあったディレクトリの属性を読み、外れていれば付け直す。付け直した数を返し、0 でなければログ `storage_attributes_repaired` を出す（TST-SEC-04） |
| 除外の読み出し | `isExcludedFromBackup` は、**親のディレクトリが除外されていると配下も真になる**（Mac 上で確かめた）。そのため、検証で外れを検出できるのは保存ルートだけである。配下にも付けるのは、保存ルートの属性だけが外れた場合の備えである |
| 値のキャッシュ | `URL` は属性の値をキャッシュする。検証では、読む前に `removeAllCachedResourceValues()` でキャッシュを捨てる（捨てないと、同じプロセスの中で古い値が返ることを確かめた） |
| 保護クラスの読み出し | 読めない環境（シミュレータなど）では検証を飛ばす。この Mac（Apple シリコン）では読み書きできたが、ロックで実際に読めなくなることは実機でしか確かめられない（§15） |

---

## 3. 永続データの形式

満たす要件: FR-CNS-02、NFR-MNT-05、SEC-INP-03、SEC-DAT-05、INV-04・10 ／ 決定: BD-STO-01・02・09

### 3.1 `meeting.json`（形式の版 1）

会議のメタデータの原本である。DB の `meetings`・`recording_gaps`・`route_events`・`unsaved_chunks`・`loss_entries` の行は、ここから作り直せる（BD-STO-02）。

```swift
/// meeting.json（形式の版 1）。会議のメタデータの原本で、DB の行はここから作り直せる（BD-STO-02）
public struct MeetingDocument: Equatable, Sendable, Codable {
    public static let currentFormatVersion = 1
    public static let maxTitleLength = 100

    public var formatVersion: Int
    public var meetingId: MeetingID
    public var title: String
    public var status: MeetingStatus
    public var consentConfirmedAt: Timestamp
    public var startedAt: Timestamp
    public var totalSamples: Int64
    public var stop: StopRecord?
    public var finalization: FinalizationRecord?
    public var lossRecord: LossRecord?
    public var gaps: [GapRecord]
    public var routes: [RouteRecord]
    public var unsavedChunks: [UnsavedChunkRecord]
    public var quarantinedSequences: [ChunkSequence]
    public var transcription: TranscriptionRecord
    public var updatedAt: Timestamp

    public struct StopRecord: Equatable, Sendable, Codable {
        public var reason: StopReason
        public var detectedAt: Timestamp
    }

    public struct FinalizationRecord: Equatable, Sendable, Codable {
        public var mode: FinalizationMode
        public var finalizedAt: Timestamp
    }

    public struct GapRecord: Equatable, Sendable, Codable {
        public var gapNo: Int
        /// 空白の直後に来るサンプルの番号
        public var position: Int64
        /// 長さが分からない空白は null（強制終了で開いたままになった空白）
        public var durationMs: Int64?
        public var reason: GapReason
        public var startedAt: Timestamp
        public var endedAt: Timestamp?
        /// 開いている間は null
        public var resumed: Bool?
    }

    public struct RouteRecord: Equatable, Sendable, Codable {
        public var samplePosition: Int64
        public var kind: InputRouteKind
        public var sampleRate: Int
        public var channelCount: Int
        public var reason: RouteEventReason
    }

    public struct UnsavedChunkRecord: Equatable, Sendable, Codable {
        public var sequenceNo: ChunkSequence
        /// 元の範囲。書き換えない
        public var original: SampleRange
        /// 救済済みのサンプル数（元の範囲の先頭から）。なければ null
        public var salvagedCount: Int64?
        public var heldSince: Timestamp
        public var cause: StorageFailureKind
    }

    public struct LossRecord: Equatable, Sendable, Codable {
        public var acknowledgedAt: Timestamp
        public var entries: [LossEntry]
        /// 確定時に破棄した、メモリ上の未保存の Chunk の数
        public var discardedUnsavedCount: Int
    }

    public struct LossEntry: Equatable, Sendable, Codable {
        public var kind: LossKind
        public var sequenceNo: ChunkSequence
        /// 範囲が分かる場合だけ（未保存の記録に元の範囲がある場合）
        public var range: SampleRange?
    }

    public struct TranscriptionRecord: Equatable, Sendable, Codable {
        public var currentVersion: Int?
        public var currentRun: Int?
        /// 失敗区間の再試行の実行番号（transcription.md §7）
        public var retryRun: Int?
    }

    /// 型と範囲の検証（SEC-INP-03）。各欄の型は復号の時点で検証済みなので、ここでは欄をまたぐ整合を見る
    public func validated() throws(PersistedDataError) -> MeetingDocument {
        guard formatVersion == Self.currentFormatVersion else { throw .unsupportedFormatVersion(found: formatVersion) }
        guard title.count <= Self.maxTitleLength, !title.unicodeScalars.contains(where: Self.isControl) else {
            throw .invalidValue(field: .title)
        }
        guard totalSamples >= 0 else { throw .invalidValue(field: .sampleRange) }
        let positions = gaps.map(\.position)
        let durations = gaps.compactMap(\.durationMs)
        guard positions.allSatisfy({ (0 ... totalSamples).contains($0) }), durations.allSatisfy({ $0 >= 0 }),
              Set(gaps.map(\.gapNo)).count == gaps.count
        else { throw .invalidValue(field: .gap) }
        let validRoutes = routes.allSatisfy { route in
            let format = InputFormat(sampleRate: route.sampleRate, channelCount: route.channelCount)
            return route.samplePosition >= 0 && format != nil
        }
        guard validRoutes else { throw .invalidValue(field: .route) }
        let runs = [transcription.currentVersion, transcription.currentRun, transcription.retryRun].compactMap(\.self)
        guard runs.allSatisfy({ (1 ... 9999).contains($0) }) else { throw .invalidValue(field: .status) }
        return self
    }

    private static func isControl(_ scalar: Unicode.Scalar) -> Bool {
        scalar.properties.generalCategory == .control
    }
}
```

| 項目 | 規則 |
| --- | --- |
| 日時 | すべて UTC の経過ミリ秒（整数）。`Timestamp` は 2000-01-01〜2100-01-01 の外の値を拒否する。壁時計の値で、表示と記録にだけ使う（INV-08） |
| 空白区間の長さ | 開いている間と、長さが分からない空白（強制終了で開いたままになったもの）は `null`（§9）。推定値で埋めない |
| 経路 | 種別・sample rate・チャンネル数・理由だけ。機器の名前は持たない（[recording.md](recording.md) §8） |
| 未保存の記録 | 元の範囲（`original`）は書き換えない。救済済みのサンプル数（`salvagedCount`）を並べて持つ（§9） |
| 文字起こし | 現在の版・現在の実行番号・失敗区間の再試行の実行番号（[transcription.md](transcription.md) §7） |
| 書き方 | 欄の順序を固定して符号化し（`PersistedJSON.encode`）、一時ファイルに書いて媒体まで同期してから rename で置き換える（`FileSystem.replaceAtomically`。§4.1） |
| 読み方 | `PersistedJSON.decode` の後に `validated()` を呼ぶ。欄ごとの型（会議 ID・連番・状態・日時・範囲）は復号の時点で検証し、`validated()` が欄をまたぐ整合（形式の版、タイトルの長さと制御文字、空白区間の位置が総サンプル数の中にあること、番号の重複）を見る |
| 検証に通らない | `PersistedDataError` を返す。会議は「読めない会議」として一覧に出し、ファイルは削除も上書きもしない（TST-RCV-04。[architecture.md](architecture.md) §8.2 の `unreadable_meeting`） |
| 形式の版 | 知らない版（1 以外）は `unsupportedFormatVersion` で拒否する。欄を足す・意味を変えるときは版を上げ、古い版を読んで新しい版へ書き直す変換を、同じ作業で足す |

書き換える時点は、基本設計 §3.1 のとおり、会議の作成・空白区間の開閉・経路の変更・未保存の記録の追加と解消・停止・確定・文字起こしの開始と完了である（Chunk ごとには書かない）。順序は §5 にある。

### 3.2 ハッシュの記録と削除要求

どちらも DB の外の独立した記録で、**作ったら書き換えない**。形式の版は 1 で、`PersistedJSON` で読み書きする。

| ファイル | 型 | 内容（例） |
| --- | --- | --- |
| `mic/{連番}.sha256.json` | `ChunkHashRecord` | `{"formatVersion":1,"sampleCount":480000,"sequenceNo":7,"sha256":"（小文字の 16 進 64 桁）"}` |
| `tombstones/{meetingId}` | `Tombstone` | `{"formatVersion":1,"meetingId":"（UUID）","requestedAt":1800000000000}` |

- ハッシュは、WAV ファイル全体（44 バイトのヘッダ + PCM）の SHA-256 である。`SHA256Digest` は小文字の 16 進 64 桁だけを受け付ける。
- ハッシュの記録は、一時ファイル（`{連番}.sha256.json.tmp`）に書いて媒体まで同期し、置換しない rename（§4.1）で公開する。一時ファイルは記録として扱わない。
- 記録を読むときは、形式の版が 1 で、連番がファイル名と一致することを確かめる。合わない・読めない記録は「読めない記録」として扱い、その Chunk を `invalid` にする。**再計算した値で記録を作り直さない**（基本設計 §3.1）。
- 削除要求は、ファイル名の会議 ID と中身の会議 ID が一致することを確かめる。読めない削除要求は削除せず、ファイル名の会議 ID の削除要求として扱う（消し残しより、削除の続行を優先する。§10）。
- 文字起こしの版のハッシュの記録（`transcript/v{版}.sha256.json`）は [transcription.md](transcription.md) §6 にある。

### 3.3 SQLite の表（移行 1）

```swift
    /// 移行 1（MVP の表）。日時は UTC の経過ミリ秒。列挙型の値は Swift の側で検証する
    private static let initial: [StaticString] = [
        """
        CREATE TABLE meetings (
            meeting_id TEXT PRIMARY KEY CHECK (length(meeting_id) = 36),
            title TEXT NOT NULL CHECK (length(title) <= 100),
            status TEXT NOT NULL,
            consent_confirmed_at INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            total_samples INTEGER NOT NULL DEFAULT 0 CHECK (total_samples >= 0),
            stop_reason TEXT,
            stop_detected_at INTEGER,
            finalization_mode TEXT,
            finalized_at INTEGER,
            loss_acknowledged_at INTEGER,
            discarded_unsaved_count INTEGER NOT NULL DEFAULT 0 CHECK (discarded_unsaved_count >= 0),
            protection_state TEXT NOT NULL DEFAULT 'open',
            current_version INTEGER CHECK (current_version BETWEEN 1 AND 9999),
            current_run INTEGER CHECK (current_run BETWEEN 1 AND 9999),
            waiting_reason TEXT,
            deletion_requested_at INTEGER,
            updated_at INTEGER NOT NULL
        ) STRICT
        """,
        // 録音中の会議は 1 件だけ（FR-REC-07）
        "CREATE UNIQUE INDEX meetings_single_recording ON meetings (status) WHERE status = 'recording'",
        "CREATE INDEX meetings_by_start ON meetings (started_at)",
        """
        CREATE TABLE audio_chunks (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            sequence_no INTEGER NOT NULL CHECK (sequence_no BETWEEN 0 AND 999999),
            start_sample INTEGER NOT NULL CHECK (start_sample >= 0),
            sample_count INTEGER NOT NULL CHECK (sample_count BETWEEN 0 AND 480000),
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
            vad_permille INTEGER CHECK (vad_permille BETWEEN 0 AND 1000),
            has_voice INTEGER CHECK (has_voice IN (0, 1)),
            file_state TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (meeting_id, sequence_no)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE chunk_conflicts (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            sequence_no INTEGER NOT NULL CHECK (sequence_no BETWEEN 0 AND 999999),
            kind TEXT NOT NULL,
            incoming_sha256 TEXT NOT NULL CHECK (length(incoming_sha256) = 64),
            existing_sha256 TEXT CHECK (existing_sha256 IS NULL OR length(existing_sha256) = 64),
            detected_at INTEGER NOT NULL,
            PRIMARY KEY (meeting_id, sequence_no, kind, incoming_sha256)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE unsaved_chunks (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            sequence_no INTEGER NOT NULL CHECK (sequence_no BETWEEN 0 AND 999999),
            original_start INTEGER NOT NULL CHECK (original_start >= 0),
            original_count INTEGER NOT NULL CHECK (original_count >= 0),
            salvaged_count INTEGER CHECK (salvaged_count >= 0),
            held_since INTEGER NOT NULL,
            cause TEXT NOT NULL,
            PRIMARY KEY (meeting_id, sequence_no)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE recording_gaps (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            gap_no INTEGER NOT NULL CHECK (gap_no >= 0),
            position_sample INTEGER NOT NULL CHECK (position_sample >= 0),
            duration_ms INTEGER CHECK (duration_ms >= 0),
            reason TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            resumed INTEGER CHECK (resumed IN (0, 1)),
            PRIMARY KEY (meeting_id, gap_no)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE route_events (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            event_no INTEGER NOT NULL CHECK (event_no >= 0),
            sample_position INTEGER NOT NULL CHECK (sample_position >= 0),
            kind TEXT NOT NULL,
            sample_rate INTEGER NOT NULL CHECK (sample_rate BETWEEN 8000 AND 384000),
            channel_count INTEGER NOT NULL CHECK (channel_count BETWEEN 1 AND 32),
            reason TEXT NOT NULL,
            PRIMARY KEY (meeting_id, event_no)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE loss_entries (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            entry_no INTEGER NOT NULL CHECK (entry_no >= 0),
            kind TEXT NOT NULL,
            sequence_no INTEGER NOT NULL CHECK (sequence_no BETWEEN 0 AND 999999),
            range_start INTEGER CHECK (range_start >= 0),
            range_count INTEGER CHECK (range_count >= 0),
            PRIMARY KEY (meeting_id, entry_no)
        ) STRICT, WITHOUT ROWID
        """,
        // 連番は transcribe_chunk では Chunk の連番、merge_transcript では −1（一意キーに NULL を入れない）
        """
        CREATE TABLE processing_jobs (
            job_id INTEGER PRIMARY KEY,
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            run_no INTEGER NOT NULL CHECK (run_no BETWEEN 1 AND 9999),
            kind TEXT NOT NULL,
            sequence_no INTEGER NOT NULL CHECK (sequence_no BETWEEN -1 AND 999999),
            state TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            execution_generation TEXT CHECK (execution_generation IS NULL OR length(execution_generation) = 36),
            failure_class TEXT,
            reuse_rejection TEXT,
            updated_at INTEGER NOT NULL,
            UNIQUE (meeting_id, run_no, kind, sequence_no)
        ) STRICT
        """,
        "CREATE INDEX processing_jobs_by_state ON processing_jobs (state, meeting_id, run_no, sequence_no)",
        """
        CREATE TABLE transcript_versions (
            meeting_id TEXT NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 9999),
            run_no INTEGER NOT NULL CHECK (run_no BETWEEN 1 AND 9999),
            sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
            integrity TEXT NOT NULL,
            segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
            excluded_count INTEGER NOT NULL CHECK (excluded_count >= 0),
            failed_chunk_count INTEGER NOT NULL CHECK (failed_chunk_count >= 0),
            engine TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (meeting_id, version)
        ) STRICT, WITHOUT ROWID
        """,
        """
        CREATE TABLE usage_metrics (
            metric_id INTEGER PRIMARY KEY,
            meeting_id TEXT REFERENCES meetings (meeting_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            value INTEGER NOT NULL,
            run_no INTEGER,
            sequence_no INTEGER,
            recorded_at INTEGER NOT NULL
        ) STRICT
        """,
        "CREATE INDEX usage_metrics_by_meeting ON usage_metrics (meeting_id, name)",
    ]
```

| 決めたこと | 理由 |
| --- | --- |
| すべての表を `STRICT` にする | 列の型に合わない値を SQLite が拒否する（文字列を整数の列に入れられない。Mac 上で確かめた） |
| 日時は整数（UTC の経過ミリ秒） | `meeting.json` と同じ表現にして、作り直しで値が変わらないようにする |
| 列挙型の値（`status`・`file_state`・`reason` など）の一覧を `CHECK` に書かない | 値を足すたびに表を作り直す移行が要るため。値の検証は、読むときに Swift の列挙型への変換で行う（未知の値は `PersistedDataError`） |
| 範囲は `CHECK` に書く（連番・サンプル数・版・実行番号・真偽値） | 型（`ChunkSequence` など）と同じ範囲を、DB の側でも守る |
| `meetings` に、`status = 'recording'` の行だけを対象にした一意索引を置く | 録音中の会議を 2 件にできない（FR-REC-07）。2 件目の登録は一意制約の違反（拡張コード 2067）として返る |
| 子の表は `ON DELETE CASCADE` | 会議の行を消すと、Chunk・競合・未保存・空白区間・経路・欠落・ジョブ・版・計測値の行が同じ取引で消える（§10） |
| 主キーが複合の表は `WITHOUT ROWID` | 主キーの索引がそのまま表になる（追加の索引が要らない） |
| `processing_jobs.sequence_no` は、`merge_transcript` では −1 | 一意キー（会議 ID・実行番号・種類・連番）に `NULL` を入れると、一意にならないため |
| ジョブの「次の実行時刻」の列を持たない | 待ち時間はメモリ上だけで持つ。再起動の後はすぐ実行できる（[transcription.md](transcription.md) §5） |
| 本文（D1・D2）の列がない | 文字起こしの本文はファイルに置く（BD-STO-02、SEC-DAT-07）。`SQLValue` も BLOB と実数を扱わない（§4.2） |

表ごとの補足:

| 表 | 補足 |
| --- | --- |
| `meetings` | `waiting_reason` は文字起こしを待っている理由（[transcription.md](transcription.md) §8）。`deletion_requested_at` が DB の中の墓標（§10）。`protection_state` は §8 |
| `audio_chunks` | `sha256` は保存時の値（ハッシュの記録と同じ値）。隔離した書きかけ（`quarantined`）の行は `sha256` が `NULL`、サンプル数が 0。`vad_permille` は VAD の割合を 1,000 倍した整数 |
| `chunk_conflicts` | `kind` は `different_content`（同じ連番で内容の異なる Chunk）・`record_mismatch`（既存のハッシュの記録と合わない）・`record_unreadable`（既存の記録が読めない）。主キーにより、同じ競合を何度記録しても 1 行である |
| `unsaved_chunks` | 書き込めずにメモリへ保持した Chunk（§7）。保存に成功したら、Chunk の登録と同じ取引で消す |
| `route_events`・`loss_entries` | 番号（`event_no`・`entry_no`）は `meeting.json` の配列の位置。作り直しても同じ行になる |
| `transcript_versions` | `integrity` は `valid`・`corrupt`・`invalid`（[transcription.md](transcription.md) §6）。`engine` はエンジンの識別名（本文ではない） |
| `usage_metrics` | 計測値の名前は閉じた列挙型で、値は整数（ミリ秒・件数・割合の 1,000 倍）。会議を消すと一緒に消える |

### 3.4 接続と移行

```swift
public enum Migrator {
    /// アプリが知っている最新の版
    public static var latestVersion: Int {
        Schema.migrations.map(\.version).max() ?? 0
    }

    /// 適用済みの最大の番号。schema_version の表がなければ作る
    public static func currentVersion(of connection: SQLiteConnection) throws(StorageFailure) -> Int {
        try connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY CHECK (version >= 1),
                applied_at INTEGER NOT NULL
            ) STRICT
            """,
            stage: .migrate,
        )
        let rows = try connection.query("SELECT COALESCE(MAX(version), 0) FROM schema_version", stage: .migrate)
        return Int(rows.first?.first?.integerValue ?? 0)
    }

    /// 未適用の移行を番号順に、1 つの移行を 1 取引で適用する。適用した番号を返す。
    /// backup は、既存の DB（版が 1 以上）に移行を当てる直前に 1 回だけ呼ぶ（複製を 1 世代残す。NFR-MNT-05）。
    /// DB の版がアプリの知る最新より新しければ databaseCorrupt を返す（呼び出し側が退避して作り直す）
    public static func migrate(
        _ connection: SQLiteConnection,
        now: Timestamp,
        backup: () throws(StorageFailure) -> Void,
    ) throws(StorageFailure) -> [Int] {
        let current = try currentVersion(of: connection)
        guard current <= latestVersion else { throw StorageFailure(kind: .databaseCorrupt, stage: .migrate) }
        let pending = Schema.migrations.filter { $0.version > current }.sorted { $0.version < $1.version }
        guard !pending.isEmpty else { return [] }
        if current > 0 {
            try backup()
        }
        for migration in pending {
            try connection.transaction { () throws(StorageFailure) in
                for statement in migration.statements {
                    try connection.execute(statement, stage: .migrate)
                }
                try connection.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?1, ?2)",
                    [.integer(Int64(migration.version)), .integer(now.milliseconds)],
                    stage: .migrate,
                )
            }
        }
        return pending.map(\.version)
    }
}
```

| 項目 | 設計 |
| --- | --- |
| 接続 | 1 本だけ。`MeetingStore`（actor）が持つ（§4.2）。読み取り専用の別の接続は作らない（削除の後の切り詰めを、ほかの接続に妨げられないため。§10） |
| 開くときのフラグ | `READWRITE`・`CREATE`・`FULLMUTEX`・`EXRESCODE`（拡張コードを返す）・`FILEPROTECTION_COMPLETEUNTILFIRSTUSERAUTHENTICATION` |
| PRAGMA | `journal_mode = WAL`、`foreign_keys = ON`、`synchronous = NORMAL`、`secure_delete = ON`（開いた直後に毎回設定する） |
| 版の記録 | `schema_version`（適用済みの番号と適用日時）。`PRAGMA user_version` は使わない |
| 番号 | 1 から連番。**いちど出荷した移行の中身は変えない。** 変更は新しい番号の移行として足す |
| 適用 | 番号順に、1 つの移行を 1 取引で適用する。途中で失敗したら、その移行は取り消される |
| 移行の前の複製 | 既存の DB（版が 1 以上）に移行を当てる直前に、`SQLiteConnection.backup(to:)`（オンラインバックアップ API）で `db/minutes.sqlite.premigration`（`StorageLayout.migrationBackup`）を作る。古い複製があれば先に消す（1 世代だけ残す。NFR-MNT-05）。`sqlite3_backup_step` が `SQLITE_DONE` を返し、`sqlite3_backup_finish` が成功したことを確かめてから移行を始める |
| 知らない新しい版 | DB の版がアプリの知る最新より新しければ、`database_corrupt` を返す。呼び出し側は §3.5 の「壊れた DB」と同じく退避して作り直す（古いビルドを入れ直した場合。ファイルから作り直せる。INV-10） |

### 3.5 壊れた DB

| 順 | 処理 |
| --- | --- |
| 1 | **保護データを読めるかを先に確かめる**（`DeviceConditionsProviding.current()`）。読めない間（端末の再起動後・最初のロック解除の前）は DB を開かず、読めるようになった通知を待ってから手順 2 へ進む。**この間の失敗で DB を退避しない** |
| 2 | DB を開く。開けたら `PRAGMA quick_check` を実行する（`passesQuickCheck()`） |
| 3 | 開けない・検査に通らない・移行が `database_corrupt` を返した場合は、基本設計 §3.3 の順で退避する: ①`db/rebuild-pending` を原子的に作り、ファイルと `db/` を同期する → ②DB と `-wal`・`-shm` を `quarantine/` へ移す（名前は `minutes-{退避の時刻の経過ミリ秒}.sqlite`。`StorageLayout.quarantinedDatabase(at:)`。置換しない rename） → ③新しい DB を作って移行 1 を適用する |
| 4 | §9 の照合で、全会議を作り直す。完了したら印を消す（条件は §9 の手順 2） |

- 開けない理由が容量不足（`disk_full`）・権限（`permission_denied`）の場合は退避しない。DB は壊れていないので、理由を表示して起動を止める（退避しても新しい DB を作れない）。退避するのは、種類が `database_corrupt` の場合と、検査に通らない場合だけである。
- 退避した DB には D3 が入っている。保存ルートの下に置き、会議を削除するときにファイルごと消す（§10 の手順 5）。

---

## 4. ファイル操作・SQLite ラッパー・Chunk の保存

満たす要件: FR-SAV-01・03、NFR-REL-04、SEC-INT-01、SEC-INP-03、SEC-LOG-04、TST-GEN-04 ／ 決定: BD-STO-01・03・07

### 4.1 ファイル操作

```swift
/// ファイル操作（注入点）。本番は PosixFileSystem、テストは FaultInjectingFileSystem（指定した段で失敗させる）。
/// どの操作も、失敗を StorageFailure（分類・段・errno）で返す。パスと OS の説明文は返さない
public protocol FileSystem: Sendable {
    /// 新しいファイルを作って書く。すでにあれば alreadyExists
    func create(_ url: URL, contents: [UInt8], stage: StorageStage) throws(StorageFailure)
    /// 末尾に追記する
    func append(_ bytes: [UInt8], to url: URL) throws(StorageFailure)
    /// offset の位置から上書きする（WAV ヘッダの書き換え）
    func overwrite(_ bytes: [UInt8], at offset: Int64, in url: URL) throws(StorageFailure)
    func truncate(_ url: URL, to size: Int64) throws(StorageFailure)
    /// ファイルの内容を記憶域まで書き出す。durable が真なら媒体まで（F_FULLFSYNC）
    func synchronize(_ url: URL, durable: Bool) throws(StorageFailure)
    func synchronizeDirectory(_ url: URL) throws(StorageFailure)
    func read(_ url: URL) throws(StorageFailure) -> [UInt8]
    func size(of url: URL) throws(StorageFailure) -> Int64
    /// 移動先がすでにあれば失敗する rename（alreadyExists）。置き換えない
    func renameExclusive(from source: URL, to destination: URL) throws(StorageFailure)
    /// 一時ファイルに書いて同期し、rename で置き換える（meeting.json 用）
    func replaceAtomically(_ url: URL, contents: [UInt8]) throws(StorageFailure)
    func remove(_ url: URL) throws(StorageFailure)
    func exists(_ url: URL) -> Bool
    /// ディレクトリの直下の名前（昇順）。なければ空
    func names(in directory: URL) throws(StorageFailure) -> [String]
}

public enum ErrnoMapping {
    /// errno から保存の失敗の分類へ（値は Darwin の errno.h のもの）
    public static func kind(for code: Int32) -> StorageFailureKind {
        switch code {
        case 28, 69: .diskFull // ENOSPC・EDQUOT
        case 1, 13: .permissionDenied // EPERM・EACCES
        case 17: .alreadyExists // EEXIST
        case 2: .notFound // ENOENT
        default: .ioError
        }
    }
}
```

本番の実装（`MinutesStorage/Files/PosixFileSystem.swift`）は POSIX の呼び出しだけを使い、状態を持たない（操作のたびに開いて閉じる）。要点は次の 4 つである。

```swift
    public func create(_ url: URL, contents: [UInt8], stage: StorageStage) throws(StorageFailure) {
        let descriptor = try open(url, O_WRONLY | O_CREAT | O_EXCL, stage)
        defer { Darwin.close(descriptor) }
        // 新しいファイルは必ず「最初のロック解除まで」の保護クラスにする（保管への切り替えは §8）
        try FileAttributes.setProtection(.untilFirstUnlock, at: url)
        try write(contents, to: descriptor, at: nil, stage)
    }
```

```swift
    public func synchronize(_ url: URL, durable: Bool) throws(StorageFailure) {
        let descriptor = try open(url, O_RDONLY, .sync)
        defer { Darwin.close(descriptor) }
        // durable: 媒体まで書き出す（F_FULLFSYNC）。そうでなければ fsync
        let result = durable ? fcntl(descriptor, F_FULLFSYNC) : fsync(descriptor)
        guard result != -1 else { throw Self.failure(.sync) }
    }
```

```swift
    public func renameExclusive(from source: URL, to destination: URL) throws(StorageFailure) {
        // RENAME_EXCL: 移動先があれば EEXIST で失敗し、何も変えない
        guard renamex_np(source.path, destination.path, UInt32(RENAME_EXCL)) == 0 else { throw Self.failure(.rename) }
    }

    public func replaceAtomically(_ url: URL, contents: [UInt8]) throws(StorageFailure) {
        let temporary = url.appendingPathExtension("tmp")
        // 前回の途中で残った一時ファイルは、ここで消してから作り直す
        if exists(temporary) {
            try remove(temporary)
        }
        try create(temporary, contents: contents, stage: .writeMeetingJSON)
        try synchronize(temporary, durable: true)
        guard rename(temporary.path, url.path) == 0 else { throw Self.failure(.rename) }
        try synchronizeDirectory(url.deletingLastPathComponent())
    }
```

| 決めたこと | 内容 |
| --- | --- |
| 置換しない rename | `renamex_np` の `RENAME_EXCL` を使う。移動先があれば `EEXIST` で失敗し、どちらのファイルも変えない（Mac 上で確かめた）。`FileManager` の移動は使わない（失敗を errno で分類できないため） |
| 新しいファイルの作成 | `O_CREAT` と `O_EXCL` を付けて作る（既存のファイルを上書きしない）。作った直後に保護クラス「最初のロック解除まで」を付けてから、中身を書く。新しいファイルを作るのは処理中（`open`）の会議と、DB・墓標・書き出しの一時領域だけなので、作成時の保護クラスは常にこの 1 つである（保管への切り替えは §8） |
| 開き方 | すべて `O_NOFOLLOW`（シンボリックリンクをたどらない）と `O_CLOEXEC` を付ける。権限は `0600` |
| 同期 | `synchronize(_:durable:)` の `durable` が偽なら `fsync`、真なら `F_FULLFSYNC`（媒体まで書き出す）。ディレクトリは `fsync` |
| 原子的な置き換え | `replaceAtomically` は、`{名前}.tmp` に書いて `F_FULLFSYNC` し、`rename` で置き換え、ディレクトリを同期する（`meeting.json`・`db/rebuild-pending`・削除要求に使う）。前回の途中で残った一時ファイルは、作る前に消す |
| 書きかけの扱い | `.part` は追記のたびに開いて閉じる。ファイルの記述子を持ち続けない（保存の actor が持つ状態を減らし、ロックや強制終了の後に開いたままの記述子を残さない） |
| 失敗の値 | `StorageFailure`（種類・段・errno）。パスと OS の説明文は持たない（SEC-LOG-04） |

errno から種類への写像（`ErrnoMapping`）:

| errno | 種類（`StorageFailureKind`） |
| --- | --- |
| `ENOSPC`（28）・`EDQUOT`（69） | `disk_full` |
| `EPERM`（1）・`EACCES`（13） | `permission_denied`。保護データを読めない間（`isProtectedDataAvailable` が偽）に起きたものは、`MeetingStore` が `protected_data_unavailable` に読み替える |
| `EEXIST`（17） | `already_exists` |
| `ENOENT`（2） | `not_found` |
| そのほか | `io_error` |

### 4.2 SQLite のラッパー

```swift
/// SQLite の薄いラッパー（BD-STO-01）。**MeetingStore（actor）の中だけで持ち、外へ渡さない**（Sendable ではない）。
/// SQL は StaticString でしか受け取らない。実行時の文字列から SQL を組み立てられず、値は束縛でしか渡せない
public final class SQLiteConnection {
    private let handle: OpaquePointer

    /// 開いて、WAL・外部キー・同期 NORMAL・secure_delete を設定する。保護クラスは「最初のロック解除まで」
    public init(path: String) throws(StorageFailure) {
        var opened: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_EXRESCODE
            | SQLITE_OPEN_FILEPROTECTION_COMPLETEUNTILFIRSTUSERAUTHENTICATION
        let code = sqlite3_open_v2(path, &opened, flags, nil)
        guard code == SQLITE_OK, let opened else {
            sqlite3_close_v2(opened)
            throw Self.failure(code, .query)
        }
        handle = opened
        for pragma: StaticString in [
            "PRAGMA journal_mode = WAL", "PRAGMA foreign_keys = ON",
            "PRAGMA synchronous = NORMAL", "PRAGMA secure_delete = ON",
        ] {
            _ = try query(pragma)
        }
    }

    deinit {
        sqlite3_close_v2(handle)
    }

    /// 結果を返さない文（INSERT・UPDATE・DELETE・DDL）。戻り値は変更した行数
    @discardableResult
    public func execute(
        _ sql: StaticString, _ values: [SQLValue] = [], stage: StorageStage = .query,
    ) throws(StorageFailure) -> Int {
        _ = try query(sql, values, stage: stage)
        return Int(sqlite3_changes(handle))
    }

    /// 行を返す文。列は左から順に SQLValue にする
    public func query(
        _ sql: StaticString, _ values: [SQLValue] = [], stage: StorageStage = .query,
    ) throws(StorageFailure) -> [[SQLValue]] {
        var prepared: OpaquePointer?
        let code = sql.withUTF8Buffer { buffer in
            buffer.withMemoryRebound(to: CChar.self) { text in
                sqlite3_prepare_v2(handle, text.baseAddress, Int32(text.count), &prepared, nil)
            }
        }
        guard code == SQLITE_OK, let statement = prepared else {
            sqlite3_finalize(prepared)
            throw Self.failure(code, stage)
        }
        defer { sqlite3_finalize(statement) }
        try bind(values, to: statement, stage)
        var rows: [[SQLValue]] = []
        while true {
            let step = sqlite3_step(statement)
            guard step == SQLITE_ROW else {
                guard step == SQLITE_DONE else { throw Self.failure(step, stage) }
                return rows
            }
            rows.append((0 ..< sqlite3_column_count(statement)).map { Self.column(statement, $0) })
        }
    }

    /// 1 つの取引にまとめる。body が失敗したら取り消して、その失敗をそのまま返す
    public func transaction<Value>(_ body: () throws(StorageFailure) -> Value) throws(StorageFailure) -> Value {
        try execute("BEGIN IMMEDIATE")
        do {
            let value = try body()
            try execute("COMMIT")
            return value
        } catch {
            // SQLite が自動で取り消していなければ取り消す。取り消しの結果ではなく、元の失敗を返す
            if sqlite3_get_autocommit(handle) == 0 {
                _ = sqlite3_exec(handle, "ROLLBACK", nil, nil, nil)
            }
            throw error
        }
    }
```

```swift
    /// SQLite の結果コードを分類する。説明文（sqlite3_errmsg）は値を含みうるので読まない
    static func failure(_ code: Int32, _ stage: StorageStage) -> StorageFailure {
        let kind: StorageFailureKind = switch code & 0xFF {
        case SQLITE_CONSTRAINT: .constraintViolation
        case SQLITE_BUSY, SQLITE_LOCKED: .databaseBusy
        case SQLITE_CORRUPT, SQLITE_NOTADB: .databaseCorrupt
        case SQLITE_FULL: .diskFull
        case SQLITE_PERM, SQLITE_AUTH, SQLITE_READONLY: .permissionDenied
        default: .ioError
        }
        return StorageFailure(kind: kind, stage: stage, osCode: code)
    }
}
```

| 決めたこと | 内容 |
| --- | --- |
| SQL の引数は `StaticString` | 実行時に作った文字列を SQL として渡せない（ビルドできない）。値は `SQLValue` の束縛でしか渡せないので、文字列の連結で SQL を作る余地がない（SEC-INP-03） |
| 値の型 | 整数・文字列・`NULL` の 3 つ。BLOB と実数は扱わない（本文と音声を DB に入れる経路を作らない） |
| 文字列の寿命 | 束縛は `SQLITE_TRANSIENT`（SQLite が複製する）。Swift の文字列の寿命に依存しない |
| 取引 | `transaction` は `BEGIN IMMEDIATE` で始め、中の処理が失敗したら取り消して、その失敗をそのまま返す |
| エラー | 拡張コードを `osCode` に入れ、下位 8 bit で種類に分ける。**`sqlite3_errmsg` は読まない**（制約の違反の説明に値が含まれうる） |
| 並行性 | `SQLiteConnection` は `Sendable` ではない。`MeetingStore`（actor）が作って持ち、外へ渡さない。`@unchecked Sendable` は付けていない（Swift 6 の検査で、actor の外へ渡すコードはビルドできない） |
| そのほかの操作 | `passesQuickCheck()`（起動時の検査）、`checkpointTruncate()`（削除の後の WAL の切り詰め。§10）、`backup(to:)`（移行の前の複製。§3.4） |

結果コードの写像: `SQLITE_CONSTRAINT` → `constraint_violation`、`SQLITE_BUSY`・`SQLITE_LOCKED` → `database_busy`、`SQLITE_CORRUPT`・`SQLITE_NOTADB` → `database_corrupt`、`SQLITE_FULL` → `disk_full`、`SQLITE_PERM`・`SQLITE_AUTH`・`SQLITE_READONLY` → `permission_denied`、そのほか → `io_error`。

### 4.3 保存の入口と同期の周期

手順（`MinutesApplication`）は `MinutesStorage` を参照できないので、保存の入口は Domain の protocol として切る（[architecture.md](architecture.md) §2.1）。録音が使う入口は次の `ChunkStoring` である。

```swift
/// 書きかけファイルの同期の周期（BD-STO-07）。追記したサンプル数で数え、タイマーを使わない（INV-08）
public struct SyncCadence: Equatable, Sendable {
    /// 80,000 サンプル = 5 秒分（設計値）
    public let intervalSamples: Int
    private var sinceLastSync = 0

    public init(intervalSamples: Int = 80000) {
        self.intervalSamples = max(1, intervalSamples)
    }

    /// 追記のたびに呼ぶ。真を返したら同期する
    public mutating func shouldSynchronize(afterAppending sampleCount: Int) -> Bool {
        sinceLastSync += sampleCount
        guard sinceLastSync >= intervalSamples else { return false }
        sinceLastSync = 0
        return true
    }

    /// Chunk を確定したら数え直す
    public mutating func reset() {
        sinceLastSync = 0
    }
}

/// 録音パイプラインと確定の手順が使う、Chunk の保存の入口（注入点）。
/// 本番は MeetingStore（MinutesStorage）、テストは InMemoryChunkStore
public protocol ChunkStoring: Sendable {
    /// 書きかけファイルへ追記する。なければ仮のヘッダを書いて作る
    func appendPart(meeting: MeetingID, sequence: ChunkSequence, samples: [Int16]) async throws(StorageFailure)
    /// Chunk を確定する。同じ Chunk を何度渡しても、保存される結果は 1 つである
    func seal(meeting: MeetingID, chunk: AssembledChunk, voice: VADResult) async throws(StorageFailure) -> SealOutcome
    /// 書き込めずにメモリへ保持し始めた Chunk を、未保存の記録に残す
    func recordUnsaved(
        meeting: MeetingID, chunk: AssembledChunk, cause: StorageFailureKind,
    ) async throws(StorageFailure)
}
```

| protocol（Domain） | 使う手順 | 内容 | 定義 |
| --- | --- | --- | --- |
| `ChunkStoring` | 録音・確定 | 書きかけへの追記、Chunk の確定、未保存の記録 | 上のコード |
| `MeetingRecordStoring` | 録音・確定・復旧・削除 | 会議の作成、状態の保存、空白区間・経路・確定の記録、Chunk の検査、一覧 | §5 の表（シグネチャは保存の実装で確定する） |
| `JobStoring`・`TranscriptStoring` | 文字起こし | ジョブの状態、結果ファイル、版 | [transcription.md](transcription.md) §5・§6 |
| `ExportSource` | 書き出し | 書き出す Chunk と版の読み出し | [export.md](export.md) §3 |

本番の実装はすべて `MeetingStore`（1 つの actor）で、テストの差し替えは `MinutesTestSupport` のメモリ上の実装（`InMemoryChunkStore` など）である。`MinutesStorageTests` は、本物の `MeetingStore` を一時ディレクトリで動かす。

| 項目 | 設計値 | 根拠 |
| --- | --- | --- |
| 追記 | 取り出しのたび（約 100 ms 分。[architecture.md](architecture.md) §7.2） | プロセスの終了（強制終了・クラッシュ）で失うのを、リングバッファと取り出し 1 回分に抑える（BD-STO-03） |
| 途中の同期 | 80,000 サンプル（5 秒分）を追記するたびに `fsync` | 電源断・OS のクラッシュで失うのを約 5 秒に抑える。**サンプル数で数え、タイマーを使わない**（BD-STO-07、INV-08） |
| 確定時の同期 | ヘッダの書き換えの後に `F_FULLFSYNC`。ハッシュの記録も `F_FULLFSYNC` | 正式名で公開する前に、媒体まで書き出す |

損失の上限は目標であり、保証値ではない（基本設計 §4。要件の上限は 1 Chunk = 30 秒のまま。TST-RCV-01・TST-DEV-05 で実測する）。

### 4.4 Chunk の確定

同じ連番の既存のファイルとの突き合わせは、純粋関数 `SealDecision.decide` で決める。

```swift
/// 確定しようとする Chunk と、同じ連番の既存のファイルの突き合わせ（FR-SAV-03）。純粋関数で、I/O を持たない
public enum SealDecision: Equatable, Sendable {
    /// 正式名へ置換しない rename をして登録する。writeHashRecord が真なら、先にハッシュの記録を作る
    case publish(writeHashRecord: Bool)
    /// 既存のハッシュの記録と合わない。記録は書き換えず、rename して hash_mismatch として登録し、競合を記録する
    case publishAsMismatch
    /// 同じ内容の正式名のファイルがすでにある。DB に行がなければ既存のファイルを登録し、新しい方を破棄する
    case discardDuplicate(registerExisting: Bool, writeHashRecord: Bool)
    /// 内容の異なる正式名のファイルがすでにある。既存を残し、新しい方を競合の版として隔離する
    case quarantineConflict

    /// incoming: 書きかけファイルを読み直して計算した値
    /// existingRecord: 同じ連番のハッシュの記録の値（なければ nil）
    /// existingChunk: 同じ連番の正式名のファイルを読み直して計算した値（なければ nil）
    public static func decide(
        incoming: SHA256Digest,
        existingRecord: SHA256Digest?,
        existingChunk: SHA256Digest?,
        hasRow: Bool,
    ) -> SealDecision {
        guard let existingChunk else {
            guard let existingRecord else { return .publish(writeHashRecord: true) }
            return existingRecord == incoming ? .publish(writeHashRecord: false) : .publishAsMismatch
        }
        guard existingChunk == incoming else { return .quarantineConflict }
        return .discardDuplicate(registerExisting: !hasRow, writeHashRecord: existingRecord == nil)
    }
}
```

| 正式名の WAV | ハッシュの記録 | 判定 | 結果 |
| --- | --- | --- | --- |
| ない | ない | 記録を作る → 公開 → 登録（`present`） | `saved` |
| ない | ある・一致 | 記録の作成を省く → 公開 → 登録（`present`） | `saved` |
| ない | ある・不一致 | 記録を書き換えない → 公開 → 登録（`hash_mismatch`）→ 競合 `record_mismatch` を記録 | `saved_with_mismatch` |
| ない | ある・読めない | 記録を書き換えない → 公開 → 登録（`invalid`）→ 競合 `record_unreadable` を記録 | `saved_with_mismatch` |
| ある・同じ内容 | ない | 記録を作る → DB に行がなければ既存を登録 → 新しい方を破棄 | `saved` |
| ある・同じ内容 | ある | DB に行がなければ既存を登録（状態は、既存のハッシュと記録の照合で決める）→ 新しい方を破棄 | `saved` |
| ある・異なる内容 | 問わない | **記録を作らない。** 既存を残す → 新しい方を `quarantine/conflict-…` へ移す → 競合 `different_content` を記録 | `conflict_quarantined` |

`saved_with_mismatch` と `conflict_quarantined` でも、書き込み自体は成功している。メモリ上の PCM は解放し、未保存の件数に数えない（基本設計 §4）。`hash_mismatch`・`invalid` の行は Barrier の不成立の理由になり、確定待ちで利用者に示す（§6）。

手順（`MeetingStore`）:

```swift
    public func appendPart(meeting: MeetingID, sequence: ChunkSequence, samples: [Int16]) throws(StorageFailure) {
        let part = layout.partFile(meeting, sequence)
        if !files.exists(part) {
            try files.create(part, contents: WAVHeader.provisional(), stage: .createFile)
        }
        try files.append(ChunkHasher.bytes(of: samples), to: part)
        if cadence.shouldSynchronize(afterAppending: samples.count) {
            try files.synchronize(part, durable: false)
        }
    }

    public func seal(
        meeting: MeetingID, chunk: AssembledChunk, voice: VADResult,
    ) throws(StorageFailure) -> SealOutcome {
        cadence.reset()
        let part = layout.partFile(meeting, chunk.sequence)
        let published = layout.chunk(meeting, chunk.sequence)
        let incoming = try completePart(part, samples: chunk.samples)
        var row = ChunkRow(
            meeting: meeting, sequence: chunk.sequence, startSample: chunk.startSample,
            sampleCount: chunk.samples.count, byteCount: Int64(WAVHeader.byteCount + chunk.samples.count * 2),
            digest: incoming, voice: voice, fileState: .present,
        )
        let record = try readRecord(meeting, chunk.sequence)
        guard record != .unreadable else {
            // 読めない記録は書き換えない。Chunk は公開し、照合できないものとして invalid にする
            try publish(part, as: published)
            row.fileState = .invalid
            return try register(row, conflict: .recordUnreadable, existing: nil)
        }
        let existingChunk = try files.exists(published) ? ChunkHasher.digest(of: files.read(published)) : nil
        let decision = try SealDecision.decide(
            incoming: incoming, existingRecord: record.digest, existingChunk: existingChunk, hasRow: hasRow(row),
        )
        switch decision {
        case let .publish(writeHashRecord):
            if writeHashRecord {
                try publishRecord(row, digest: incoming)
            }
            try publish(part, as: published)
            return try register(row, conflict: nil, existing: nil)
        case .publishAsMismatch:
            try publish(part, as: published)
            row.fileState = .hashMismatch
            return try register(row, conflict: .recordMismatch, existing: record.digest)
        case let .discardDuplicate(registerExisting, writeHashRecord):
            if writeHashRecord {
                try publishRecord(row, digest: incoming)
            }
            if registerExisting {
                // 既存のファイルの状態は、読み直したハッシュと記録の照合で決める
                row.fileState = record.digest == nil || record.digest == incoming ? .present : .hashMismatch
                _ = try ChunkTable.register(row, at: Timestamp(clock.now()), in: connection)
            }
            try files.remove(part)
            return .saved
        case .quarantineConflict:
            return try quarantine(part, row: row, incoming: incoming, existing: existingChunk)
        }
    }

    /// 内容の異なる Chunk がすでにある。既存を残し、新しい方を競合の版として隔離して記録する
    private func quarantine(
        _ part: URL, row: ChunkRow, incoming: SHA256Digest, existing: SHA256Digest?,
    ) throws(StorageFailure) -> SealOutcome {
        let prefix = String(incoming.description.prefix(16))
        guard let destination = layout.conflictingChunk(row.meeting, row.sequence, digestPrefix: prefix) else {
            throw StorageFailure(kind: .invalidPath, stage: .rename)
        }
        // 同じ内容の競合の版をすでに隔離していれば、新しい方を破棄する
        if files.exists(destination) {
            try files.remove(part)
        } else {
            try publish(part, as: destination)
        }
        let now = Timestamp(clock.now())
        try ChunkTable.recordConflict(row, kind: .differentContent, existing: existing, at: now, in: connection)
        return .conflictQuarantined
    }
```

```swift
    /// 書きかけファイルを完全な Chunk にする: ヘッダを書き直し、媒体まで同期し、読み直してメモリ上の内容と照合する。
    /// 書きかけがない・途中までしかない（追記に失敗していた）場合は、メモリ上の PCM から全体を書き直す
    private func completePart(_ part: URL, samples: [Int16]) throws(StorageFailure) -> SHA256Digest {
        let expected = ChunkHasher.fileImage(of: samples)
        if files.exists(part) {
            try files.overwrite(WAVHeader.encode(sampleCount: samples.count), at: 0, in: part)
            if try files.read(part) != expected {
                try files.truncate(part, to: 0)
                try files.overwrite(expected, at: 0, in: part)
            }
        } else {
            try files.create(part, contents: expected, stage: .createFile)
        }
        try files.synchronize(part, durable: true)
        // ヘッダと PCM を連結した全体で照合する（PCM だけでは照合しない）
        guard try files.read(part) == expected, let digest = ChunkHasher.digest(of: expected) else {
            throw StorageFailure(kind: .verificationMismatch, stage: .readBack)
        }
        return digest
    }

    /// 置換しない rename で公開し、ディレクトリを同期する
    private func publish(_ source: URL, as destination: URL) throws(StorageFailure) {
        try files.renameExclusive(from: source, to: destination)
        try files.synchronizeDirectory(destination.deletingLastPathComponent())
    }
```

Chunk の登録（1 取引）:

```swift
    /// Chunk の登録・総サンプル数の更新・未保存の記録の削除を 1 取引で行う。何度呼んでも結果は同じ
    static func register(
        _ row: ChunkRow, at now: Timestamp, in connection: SQLiteConnection,
    ) throws(StorageFailure) -> ChunkRegistration {
        let key: [SQLValue] = [.text(row.meeting.description), .integer(Int64(row.sequence.value))]
        let incoming: SQLValue = row.digest.map { .text($0.description) } ?? .null
        return try connection.transaction { () throws(StorageFailure) in
            let existing = try connection.query(
                "SELECT sha256 FROM audio_chunks WHERE meeting_id = ?1 AND sequence_no = ?2", key, stage: .register,
            )
            if let found = existing.first?.first {
                guard found != incoming, case .text = incoming else { return .duplicate }
                let stored = found.textValue.flatMap(SHA256Digest.init(hex:))
                try recordConflict(row, kind: .differentContent, existing: stored, at: now, in: connection)
                return .conflict
            }
            let voice: [SQLValue] = row.voice.map {
                [.integer(Int64(($0.score * 1000).rounded())), .integer($0.hasVoice ? 1 : 0)]
            } ?? [.null, .null]
            let sampleCount = Int64(row.sampleCount)
            let sizes: [SQLValue] = [.integer(row.startSample), .integer(sampleCount), .integer(row.byteCount)]
            try connection.execute(
                """
                INSERT INTO audio_chunks
                    (meeting_id, sequence_no, start_sample, sample_count, byte_count, sha256,
                     vad_permille, has_voice, file_state, created_at)
                VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                """,
                key + sizes + [incoming] + voice + [.text(row.fileState.rawValue), .integer(now.milliseconds)],
                stage: .register,
            )
            try connection.execute(
                "UPDATE meetings SET total_samples = MAX(total_samples, ?2), updated_at = ?3 WHERE meeting_id = ?1",
                [key[0], .integer(row.startSample + sampleCount), .integer(now.milliseconds)], stage: .register,
            )
            // 保存できたので、同じ連番の未保存の記録を消す（基本設計 §5.1）
            try connection.execute(
                "DELETE FROM unsaved_chunks WHERE meeting_id = ?1 AND sequence_no = ?2", key, stage: .register,
            )
            return .inserted
        }
    }
```

| 順 | 処理 | 段（`StorageStage`） | ここで失敗・終了した場合 |
| --- | --- | --- | --- |
| 1 | `.part` のヘッダを最終のヘッダに書き換える。中身がメモリ上の内容と違う・`.part` がない場合は、メモリ上の PCM から全体を書き直す | `rewrite_header`・`create_file` | `.part` が残る（または、ない）。再試行で同じ手順をやり直す |
| 2 | `.part` を媒体まで同期する | `sync` | 同上 |
| 3 | `.part` の全体を読み直し、「最終のヘッダ + メモリ上の PCM」と照合する。合わなければ `verification_mismatch` | `read_back` | 同上 |
| 4 | 同じ連番のハッシュの記録・正式名の WAV・DB の行を調べ、`SealDecision.decide` で扱いを決める | `read_back`・`register` | 同上 |
| 5 | （記録を作る場合）一時ファイルに書いて同期し、置換しない rename で公開、ディレクトリを同期 | `write_hash_record`・`sync`・`rename`・`sync_directory` | `.part` と記録が残る。再試行では「記録あり・一致」になる |
| 6 | `.part` を正式名へ置換しない rename、ディレクトリを同期 | `rename`・`sync_directory` | rename の前なら 5 と同じ。後なら正式名の WAV と記録がある → 再試行では「WAV あり・同じ内容」になる |
| 7 | Chunk の登録・総サンプル数の更新・未保存の記録の削除（1 取引） | `register` | 正式名の WAV と記録がある。再試行、または起動時の照合（§9）で登録される |

- **正式名のファイルは、常に完全な Chunk である**（FR-SAV-01）。書きかけは `.part` の名前でしか存在しない。手順 1〜7 のどこで失敗しても、正式名のファイルは「ない」か「完全」のどちらかになる（各段で失敗を注入して確かめた。§12.3）。
- 同じ Chunk を何度確定しても、ファイルは 1 つ、行は 1 つである（FR-SAV-03、INV-05）。
- 照合は、ヘッダと PCM を連結した**ファイルの全体**で行う。PCM だけでは照合しない（ヘッダの書き損じを見逃さないため）。
- `MeetingStore` の `appendPart`・`seal` は、関数の中に `await` を置かない。actor の中で最後まで続けて実行されるので、手順 4 の判定と手順 6 の rename の間に、別の確定が割り込まない。それでも rename が `already_exists` で失敗した場合（アプリの外からファイルが置かれたなど）は、失敗として返し、再試行で手順 4 からやり直す。
- 失敗した Chunk は、`RecordingService` がメモリに保持し、`recordUnsaved` で未保存の記録を残す（§7）。
- ログは、成功で `chunk_sealed`、失敗で `chunk_write_failed`（連番・種類・段・コード）、競合で `chunk_conflict` を出す（[architecture.md](architecture.md) §9.2）。

---

## 5. 状態の保存の順序

満たす要件: FR-FIN-03、FR-INT-05、FR-CNS-02、INV-04・10、NFR-REL-05 ／ 決定: BD-STO-02

[architecture.md](architecture.md) §5 の「状態を保存」（副作用 `persistStatus` など）は、SQLite と `meeting.json` の両方への保存を指す。順序は次のとおりである。

| 規則 | 内容 |
| --- | --- |
| 原則 | **①DB の取引 → ②`meeting.json` の原子的な置き換え**。①の後・②の前に落ちた場合は、次の起動の照合（§9 の手順 4）で、DB の状態を `meeting.json` に書き直す（DB が正しい間は DB を正とする） |
| 例外 1（会議の作成） | ①会議ディレクトリを作る → ②`meeting.json` を書く → ③DB の行を作る。③の前に落ちた場合は、ディレクトリと `meeting.json` だけが残り、起動時の照合が「音声が 1 サンプルもない会議」として片付ける（§9） |
| 例外 2（失敗区間の再試行の実行番号） | DB の取引より前に `meeting.json` へ書く。再文字起こしの開始では、DB の取引より前に `meeting.json` から消す（[transcription.md](transcription.md) §7） |
| 例外 3（削除要求） | DB の外の削除要求（`tombstones/`）を先に作り、続けて DB の墓標を記録する（§10） |
| DB を作り直したとき | `meeting.json` から行を作るので、書き直しは起きない |

何を保存するか:

| きっかけ | DB（1 取引） | `meeting.json` |
| --- | --- | --- |
| 会議の作成 | `meetings` の行（状態 `recording`、同意の時刻、開始日時） | 全体を新しく書く |
| 経路の記録（`recordRoute`） | `route_events` に 1 行 | `routes` に 1 件 |
| 空白区間を開く・閉じる | `recording_gaps` の行を作る・更新する | `gaps` の該当の件 |
| Chunk の確定 | `audio_chunks` の行、`meetings.total_samples`、`unsaved_chunks` の削除（§4.4） | 書かない（未保存の記録を消した場合だけ、`unsavedChunks` を書き直す） |
| 未保存の記録の追加 | `unsaved_chunks` に 1 行 | `unsavedChunks` に 1 件 |
| 停止（`recording` → `stop_requested`） | `status`、`stop_reason`、`stop_detected_at`、`total_samples` | `status`、`stop`、`totalSamples` |
| 確定の開始・不成立（`finalizing` ↔ `stop_requested`） | `status` | `status` |
| 確定（`finalized`） | `status`、`finalization_mode`、`finalized_at`。例外確定では `loss_acknowledged_at`・`discarded_unsaved_count` と `loss_entries` | `status`、`finalization`、`lossRecord` |
| 書きかけの隔離（復旧） | `audio_chunks` に `quarantined` の行 | `quarantinedSequences` |
| 保護の状態の変更 | `protection_state` | 書かない（保護の状態は、ファイルの属性から読み直せる） |
| 文字起こしの開始・完了 | [transcription.md](transcription.md) §7 | `status`、`transcription` |

失敗したときの扱い:

| 場面 | 扱い |
| --- | --- |
| 録音中の記録（経路・空白区間）の保存の失敗 | **録音を止めない**（Recording-First）。録音中の正はメモリ上の値である（[recording.md](recording.md) §6）。次の Chunk の確定のたびに、保存できていない記録をまとめて保存し直す。保存は、同じ番号の行を置き換える形（`INSERT … ON CONFLICT DO UPDATE`）なので、何度行っても結果は同じである |
| 停止の保存の失敗 | メモリ上の状態は `stop_requested` に進め、問題（`chunk_unsaved` と同じ扱いの保存の失敗）を表示する。前面への復帰と、確定の再試行の操作のたびに保存し直す。保存できないままプロセスが終了した場合は、起動時の復旧が `recording` の会議を `stop_requested` にする（停止理由は `terminated` になり、もとの理由は失われる。§9 の手順 5） |
| 確定・例外確定の保存の失敗 | 状態を進めない（`finalizing` のまま、または確定待ちのまま）。理由を表示し、利用者の再試行を待つ。`reduce` が返した遷移を保存できたときにだけ、メモリ上の状態を進める |
| ①は成功・②が失敗 | 失敗を返さず、ログに記録する。次の書き換え、または起動時の照合で `meeting.json` が DB に追いつく |

- 状態の変更は、`MeetingStateMachine.reduce` が返した遷移だけを保存する。保存の側に、状態を直接書き換える入口（`setStatus` のような関数）を作らない（[architecture.md](architecture.md) §5.3）。`MeetingRecordStoring` の関数は、遷移（`MeetingTransition`）と、その遷移に伴う記録（停止理由・確定方式・欠落の記録）を引数に取る。
- ジョブの状態だけは SQLite が唯一の記録である（INV-04）。`meeting.json` には書かない。

---

## 6. 停止と確定

満たす要件: FR-FIN-01〜03、FR-SAV-05、SEC-INT-01・02 ／ 決定: BD-ARC-01

### 6.1 Finalization Barrier

```swift
/// Finalization Barrier（FR-FIN-01）。純粋関数で、I/O を持たない
public enum FinalizationBarrier {
    /// 不成立の理由を返す。空なら通過
    public static func evaluate(
        chunks: [ChunkInspection],
        unsavedInMemory: Int,
        lostUnsaved: [ChunkSequence],
    ) -> [FinalizationBlocker] {
        let lost = Set(lostUnsaved)
        let known = Set(chunks.map(\.sequence))
        let last = (chunks.map(\.sequence.value) + lostUnsaved.map(\.value)).max() ?? -1
        // 欠番: 0 から最大の連番までで、行がなく、失われた未保存としても記録されていないもの
        let expected = (0 ..< max(0, last + 1)).compactMap(ChunkSequence.init)
        let missing = expected.filter { !known.contains($0) && !lost.contains($0) }
        func sequences(_ state: ChunkFileState) -> [ChunkSequence] {
            chunks.filter { $0.state == state }.map(\.sequence).sorted()
        }
        var blockers: [FinalizationBlocker] = []
        if !missing.isEmpty {
            blockers.append(.missingSequences(missing))
        }
        if !sequences(.missing).isEmpty {
            blockers.append(.missingFiles(sequences(.missing)))
        }
        if !sequences(.invalid).isEmpty {
            blockers.append(.invalidHeaders(sequences(.invalid)))
        }
        if !sequences(.quarantined).isEmpty {
            blockers.append(.quarantined(sequences(.quarantined)))
        }
        if !sequences(.hashMismatch).isEmpty {
            blockers.append(.hashMismatches(sequences(.hashMismatch)))
        }
        if unsavedInMemory > 0 {
            blockers.append(.unsavedInMemory(count: unsavedInMemory))
        }
        if !lost.isEmpty {
            blockers.append(.lostUnsaved(lost.sorted()))
        }
        return blockers
    }
}
```

Barrier は純粋関数で、I/O を持たない。入力（`ChunkInspection` の一覧）は、`MeetingStore` が DB の行ごとにファイルを読み直して作る。

| 順 | 検査（DB の行 1 件につき） | 結果の状態 |
| --- | --- | --- |
| 1 | 行の状態が `quarantined`（復旧時に隔離した書きかけ） | `quarantined`（ファイルを調べない） |
| 2 | 正式名のファイルがない | `missing` |
| 3 | 先頭 44 バイトとファイルの大きさを `WAVHeader.validate` で検証する（[recording.md](recording.md) §4.5）。通らない、またはサンプル数が行と違う | `invalid` |
| 4 | ハッシュの記録がない・読めない | `invalid` |
| 5 | ファイル全体の SHA-256 を計算し、ハッシュの記録と照合する。合わない、または行の `sha256` と記録が食い違う | `hash_mismatch` |
| 6 | 上のどれでもない | `present` |

- 検査の結果は、行の `file_state` に書き戻す（次の表示と、文字起こしの対象の判定に使う）。ファイルと記録は変えない。
- 欠番は、連番 0 から「行の最大の連番」と「失われた未保存の連番」の大きい方までを数え、行がなく、失われた未保存としても記録されていない連番とする。失われた末尾の Chunk は、欠番ではなく「失われた未保存」（`lostUnsaved`）として不成立になる（後に Chunk がないので、欠番では検出できないため。基本設計 §5.1）。
- `unsavedInMemory` は、`RecordingService` がメモリに保持している未保存の Chunk の数である。`lostUnsaved` は、起動時の照合で「失われた」と判定した未保存の記録の連番である（§9）。
- 120 分の会議で約 220 MiB を読み直す。`MeetingStore` の中で 1 Chunk ずつ読み、Chunk ごとに進み具合（済んだ数 ÷ 全体）を流す。画面は進み具合を表示する（[ui.md](ui.md) §4.5）。1 Chunk を読む間（約 1 MB）だけ actor を占有する。

### 6.2 確定の手順

`FinalizationService`（actor）が、[architecture.md](architecture.md) §5 の遷移の表に沿って進める。

| 順 | 処理 | 事象 |
| --- | --- | --- |
| 1 | 停止の並び（[recording.md](recording.md) §5.2）の最後で、会議が `stop_requested` になる | `stopRequested(理由)` |
| 2 | メモリ上に未保存の Chunk があれば、古い順に保存を再試行する（§4.4 の手順。成功したものは件数から除く） | — |
| 3 | `finalizing` にして保存する | `finalizationStarted` |
| 4 | 全 Chunk を検査し（§6.1）、Barrier を評価する。結果をログ `barrier_evaluated` に出す | — |
| 5a | 通過: 確定方式 `barrier` を記録して `finalized` にする | `barrierPassed` |
| 5b | 不成立: `stop_requested` に戻し、理由の一覧（`FinalizationBlocker`）を確定待ちの画面へ渡す | `barrierFailed` |
| 6 | （5b の後）利用者が「再試行」を選んだら手順 2 へ戻る。「欠落を承知して確定」を選んだら手順 7 へ | — |
| 7 | 例外確定: `lossRecord` を組み立て、確定方式 `acknowledged_loss` を記録して `finalized` にする。メモリ上の未保存の Chunk を破棄する | `lossAcknowledged` |
| 8 | 文字起こしの開始条件を評価し（[transcription.md](transcription.md) §8）、保護クラスを見直す（§8） | — |

`lossRecord` の組み立て（手順 7）:

| 不成立の理由 | `LossEntry` の種類 | 範囲 |
| --- | --- | --- |
| `missingSequences` | `missing_sequence` | なし（前後の Chunk から求められる場合も、推定値を書かない） |
| `missingFiles` | `missing_file` | DB の行の開始サンプルとサンプル数 |
| `invalidHeaders` | `invalid_header` | DB の行の値 |
| `quarantined` | `quarantined` | なし |
| `hashMismatches` | `hash_mismatch` | DB の行の値 |
| `lostUnsaved` | `lost_unsaved` | 未保存の記録の「元の範囲 − 救済済みの範囲」（§9） |
| `unsavedInMemory(count:)` | 連番ごとに `lost_unsaved`（保持していた Chunk の範囲） | 件数は `discardedUnsavedCount` にも入れる |

- 例外確定は、利用者の操作でだけ行う。起動時の復旧や自動の処理は、Barrier を通らない会議を `finalized` にしない（FR-FIN-02）。
- 例外確定の前に、失われる範囲（連番と、分かる場合は時間）を画面で示す（[ui.md](ui.md) §4.5）。
- ハッシュが合わない Chunk・ヘッダが不正な Chunk は削除しない。文字起こしの対象から外し、その区間を「利用できなかった区間」として表示する（[transcription.md](transcription.md) §6.2）。
- 同じ事象が 2 回届いても、2 回目は遷移の表にない組み合わせとして拒否される（例: `finalized` に `barrierPassed`）。

---

## 7. 空き容量の縮退

満たす要件: FR-SAV-04・05、NFR-CAP-01、NFR-REL-05 ／ 決定: BD-STO-07

```swift
/// 空き容量の設計値（初期値。基本設計 §6）
public struct FreeSpaceTuning: Equatable, Sendable {
    /// DB・文字起こしの結果・書き出しの一時ファイルのために残す
    public var reserveBytes: Int64 = 200_000_000
    /// 1 分あたりの録音のデータ量（16,000 × 2 × 60）
    public var bytesPerMinute: Int64 = 1_920_000
    public var warnAtStartMinutes: Int64 = 120
    public var warnWhileRecordingMinutes: Int64 = 30
    public var criticalMinutes: Int64 = 10
    /// メモリ上に保持する未保存の Chunk の上限
    public var maxUnsavedChunks = 20

    public init() {}
}

public enum FreeSpaceStage: String, Sendable, CaseIterable, Comparable, LogSymbolSource {
    case normal
    /// 段階 1（警告）
    case low
    /// 段階 2（最上位の警告。開始は確認つき）
    case critical

    public static func < (lhs: FreeSpaceStage, rhs: FreeSpaceStage) -> Bool {
        lhs.order < rhs.order
    }

    private var order: Int {
        switch self {
        case .normal: 0
        case .low: 1
        case .critical: 2
        }
    }
}

public enum FreeSpacePolicy {
    /// 録音できる残り時間（分）。予備を引いて、負なら 0
    public static func remainingMinutes(availableBytes: Int64, tuning: FreeSpaceTuning = FreeSpaceTuning()) -> Int64 {
        max(0, availableBytes - tuning.reserveBytes) / max(1, tuning.bytesPerMinute)
    }

    public static func stage(
        availableBytes: Int64, isRecording: Bool, tuning: FreeSpaceTuning = FreeSpaceTuning(),
    ) -> FreeSpaceStage {
        let minutes = remainingMinutes(availableBytes: availableBytes, tuning: tuning)
        if minutes < tuning.criticalMinutes {
            return .critical
        }
        return minutes < (isRecording ? tuning.warnWhileRecordingMinutes : tuning.warnAtStartMinutes) ? .low : .normal
    }
}
```

基本設計 §6 の初期値を、そのまま最終値にする（TST-DEV-06 の結果で見直す）。

| 設計値 | 値 | 意味 |
| --- | --- | --- |
| 予備（`reserveBytes`） | 200 MB | DB・文字起こしの結果・書き出しの一時ファイルのために残す |
| 1 分あたりのデータ量 | 1,920,000 バイト | 16,000 × 2 × 60 |
| 段階 1（開始時） | 残り 120 分未満 | `low`。録音の開始の前に「あと約 N 分録音できます」を表示する |
| 段階 1（録音中） | 残り 30 分未満 | `low`。録音は続ける |
| 段階 2 | 残り 10 分未満 | `critical`。最上位の警告。新しい録音の開始は確認つきにする（[recording.md](recording.md) §3.4 の手順 4） |
| 保持の上限（`maxUnsavedChunks`） | 20 Chunk | メモリ上に保持する未保存の Chunk の上限（約 10 分・約 18 MiB） |

| 項目 | 設計 |
| --- | --- |
| 値の出どころ | `FreeSpaceProviding.availableBytes()`。本番の実装（`VolumeFreeSpace`）は、保存ルートの `volumeAvailableCapacityForImportantUsage` を読む |
| 確かめる時点 | 録音の開始の前と、**Chunk の確定のたび**（タイマーで監視しない。BD-STO-07）。段階が変わったらログ `free_space_stage_changed` を出し、画面へ渡す（[recording.md](recording.md) §9） |
| 値を読めない | 段階を変えない（前の段階のまま）。書き込みの失敗の捕捉（段階 3）が最後の防衛線である |

段階 3（書き込みの失敗）と物理的な限界:

| 順 | 処理 |
| --- | --- |
| 1 | `seal` が失敗したら、`RecordingService` がその Chunk（`AssembledChunk` と VAD の結果）をメモリの保持の列に入れる。**録音は止めない** |
| 2 | `recordUnsaved` で未保存の記録を DB に残し、`meeting.json` にも書く（§5）。記録の書き込みが失敗したら、次の Chunk の確定のたびに再試行する |
| 3 | 問題 `chunk_unsaved` を表示する（保持中の件数と、「アプリが終了するとこの分は失われる」こと。FR-SAV-05） |
| 4 | 次の Chunk の確定のたびに、保持の列を古い順に `seal` し直す。成功したものを列から除く（未保存の記録は、Chunk の登録と同じ取引で消える）。1 つ失敗したら、それより新しいものは試さない（順序を保つ） |
| 5 | 保持の列が上限（20）に達したら、録音の状態遷移へ `stopRequested(disk_full)` を渡す（[recording.md](recording.md) §5.2）。保持中の Chunk はメモリに残したまま、会議は確定待ちになる（Barrier は `unsavedInMemory` で不成立） |
| 6 | 確定待ちの間は、前面への復帰時と、確定待ちの画面の表示時に空き容量が「保持分のバイト数 + 予備」以上になっていたときに、1 回ずつ自動で再試行する（§6.2 の手順 2）。利用者はいつでも再試行を操作できる |

- 原因は容量に限らない（権限・破損など）。原因によらず同じ動作にし、原因は `StorageFailureKind` として未保存の記録とログに残す。
- 追記（`appendPart`）の失敗は、その Chunk の確定まで保留する。確定のときに、メモリ上の PCM から全体を書き直すので（§4.4 の手順 1）、追記の失敗だけでは音声を失わない。
- 緊急書き出し（[export.md](export.md) §7）は保持分を書き出すだけで、未保存の記録は消さない。
- 上限を置くのは、際限なく貯めると OS にアプリごと終了させられ、保持分をすべて失うためである（NFR-REL-05）。

---

## 8. 保護クラスとバックアップ除外

満たす要件: SEC-DAT-01〜04 ／ 決定: BD-STO-04・05

```swift
/// 会議ディレクトリの保護の状態（基本設計 §7.1）
public enum ProtectionState: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    /// 処理中（completeUntilFirstUserAuthentication）
    case open
    /// 保管に上げたいが、端末がロック中で保留している
    case sealPending = "seal_pending"
    /// 保管（complete）
    case sealed
}

public enum ProtectionAction: Equatable, Sendable {
    case none
    /// 会議ディレクトリの下をすべて complete にする
    case seal
    /// 会議ディレクトリの下をすべて処理中の保護クラスに戻す
    case unseal
}

public enum ProtectionPolicy {
    /// hasUnfinishedWork: 録音中・確定待ち・未完了のジョブ・書き出し中・未保存の Chunk のどれかがある
    public static func review(
        current: ProtectionState,
        hasUnfinishedWork: Bool,
        isProtectedDataAvailable: Bool,
    ) -> (next: ProtectionState, action: ProtectionAction) {
        if hasUnfinishedWork {
            // 保管中の会議を再処理するのは利用者の操作からだけで、そのとき端末はロック解除されている
            return current == .sealed ? (.open, .unseal) : (.open, .none)
        }
        switch current {
        case .sealed: return (.sealed, .none)
        case .open, .sealPending: return isProtectedDataAvailable ? (.sealed, .seal) : (.sealPending, .none)
        }
    }
}
```

| 項目 | 設計 |
| --- | --- |
| 未完了の作業（`hasUnfinishedWork`） | 次のどれかがある: 会議の状態が `recording`・`stop_requested`・`finalizing`・`transcribing`、`pending`・`processing` のジョブ、メモリ上の未保存の Chunk、未保存の記録、進行中の書き出し、進行中の削除 |
| 見直す時点 | 副作用 `reviewProtection`（確定・文字起こしの完了。[architecture.md](architecture.md) §5）、書き出しの完了、再文字起こし・失敗区間の再試行・書き出しの開始（処理中へ戻す）、および下の「保留の解消」 |
| 保管への切り替え（`seal`） | 会議ディレクトリの下のファイルを 1 つずつ `complete` にし、最後に各ディレクトリを `complete` にする。全部が成功してから、`protection_state` を `sealed` にする。途中で失敗したら `open` のままにして、次の見直しでやり直す（属性の設定は、何度行っても同じ結果になる） |
| 処理中へ戻す（`unseal`） | 先に `protection_state` を `open` にし、各ディレクトリ、ファイルの順に「最初のロック解除まで」へ戻す。全部が成功してから、ジョブの作成・書き出しを始める |
| 新しく作るファイル | 常に「最初のロック解除まで」で作る（§4.1）。保管中の会議にはファイルを作らない（作る前に必ず処理中へ戻す） |
| SQLite | 常に「最初のロック解除まで」（開くときのフラグ。§3.4）。`-wal`・`-shm` と、移行の前の複製も同じ |
| 対象の外 | `db/`・`tombstones/`・`tmp/`・保存ルートの直下の `quarantine/` は、常に「最初のロック解除まで」のままにする |

**保留（`seal_pending`）を解消するきっかけ**（基本設計の申し送り）は、次の 3 つである。どれも、`ProtectionPolicy.review` を `sealPending` の会議に対して呼び直す。

| きっかけ | 検知の方法 |
| --- | --- |
| 保護データを読めるようになった | `DeviceConditionsProviding.changes()` の `isProtectedDataAvailable` が偽から真になった（アプリ側のアダプタが、保護データの通知を写す。[architecture.md](architecture.md) §6） |
| 前面への復帰 | 画面の Store が、前面に戻ったことを手順へ渡す（[ui.md](ui.md) §5）。前面にいるとき、端末はロック解除されている |
| 起動 | 起動時の復旧の手順 9（§9） |

- 保管中（`sealed`）の会議を再処理するのは、利用者の操作から始まる場合だけである。操作の時点で端末はロック解除されているので、そこで処理中へ戻す（基本設計 §7.1）。
- 原音の再生は、保管中のまま読む（前面でしか行わない）。
- バックアップ除外は §2.3 のとおりである。除外の結果、アプリの削除・端末の復元・Personal Team の失効後の削除で録音はすべて消える。このことを初回説明と設定画面で示す（[ui.md](ui.md) §4.1・§4.6）。
- アプリ独自の暗号化（SEC-DAT-08）は MVP で行わない（基本設計 §7.3）。

---

## 9. 起動時の復旧

満たす要件: FR-RCV-01・02・04、SEC-INP-03、INV-10 ／ 決定: BD-STO-08

`RecoveryService.run()` が、録音を開始できるようになる前に 1 回実行する（[architecture.md](architecture.md) §10.1 の手順 6）。手順は基本設計 §8 の 9 つで、順序を変えない。

| 順 | 処理 | 使うもの |
| --- | --- | --- |
| 1 | 保存ルートの属性を検証する | `StorageRoot.prepare()`（§2.3） |
| 2 | DB を開く。壊れていれば退避して作り直す | §3.5。作り直した・`db/rebuild-pending` が残っている場合は、手順 4 の対象を全会議にする |
| 3 | 削除の続きを行う | `tombstones/` の記録と、DB の墓標のある会議（§10 の手順 2 から）。**照合より先に行う** |
| 4 | 会議を照合する | 下の「照合」。`transcribing`・`transcribed` の会議は、状態を DB と `meeting.json` で比べて `meeting.json` を書き直すだけ |
| 5 | `recording` のまま残っていた会議を `stop_requested` にする | 事象 `stopRequested`。理由は、開いたままの空白区間があれば `interruption_unresumed`、なければ `terminated`。検出時刻は起動の時刻 |
| 6 | `stop_requested`・`finalizing` の会議の Barrier を評価し、通れば `finalized` にする | §6.2 の手順 3〜5（メモリ上の未保存は 0 件）。通らなければ確定待ちとして残す。`finalizing` のまま残っていた会議は、先に `barrierFailed` で `stop_requested` に戻してから評価する |
| 7 | `processing` のジョブを回収し、`transcribing` の会議の不足したジョブを作る | [transcription.md](transcription.md) §5 |
| 8 | 書き出しの一時ファイルを削除する | [export.md](export.md) §6 |
| 9 | 保留中の保護クラスの切り替えを行う | §8（保護データを読めるときだけ） |

- 何度実行しても同じ結果になり、途中で落ちても次の起動で続きを行える（各手順が、状態を読んでから必要な分だけを行う）。
- 手順 2 の印（`db/rebuild-pending`）を消す条件は、基本設計 §8 のとおりである: 全会議の照合が完了し、かつ保護データを読めずに照合できなかった会議が残っていないこと。壊れていて読めない `meeting.json` は、「読めない会議」として一覧に出した時点で照合の済んだ会議として数える。
- 復旧の結果（`stop_requested` にした会議の数、救済した Chunk の数、隔離した数、DB を作り直したか）を値として返し、起動後に 1 回表示する（[ui.md](ui.md) §4.7）。ログは `recovery_completed`・`part_file_salvaged`・`part_file_quarantined`・`database_quarantined` を出す。

**照合**（会議 1 件について、会議ディレクトリと DB を突き合わせる。BD-STO-08）:

| 順 | 処理 |
| --- | --- |
| a | `meeting.json` を読んで検証する。読めなければ「読めない会議」として記録し、この会議の残りの手順を行わない（ファイルは変えない）。DB に行がなければ、`meeting.json` から行を作る（`tombstones/` に記録がある会議は作らない） |
| b | `mic/` を一覧する。`{連番}.sha256.json.tmp` は削除する（記録として扱わない） |
| c | 書きかけ（`{連番}.wav.part`）を、連番の小さい順に救済する（下の「書きかけの救済」） |
| d | 正式名のファイルがあり DB に行がないものを、検査（§6.1 の順 2〜5）のうえで登録する。開始サンプルは、連番 0 から順に、前の Chunk の終わりの位置とする |
| e | DB に行がありファイルがないものを `missing` にする |
| f | 未保存の記録を突き合わせる（下の「未保存の記録」） |
| g | 開いたままの空白区間を閉じる: 長さと終了時刻を「不明」（`null`）、再開を「いいえ」にする（基本設計 §8）。単調時計の値はプロセスをまたいで比べられないので、推定値で埋めない（[architecture.md](architecture.md) §6.2） |
| h | 音声が 1 サンプルもなく、会議の `quarantine/` が空で、未保存の記録もない会議を削除する（§10 の手順で。件数だけ記録する）。隔離したデータまたは未保存の記録があれば、削除せず一覧に残す |

**書きかけの救済**（FR-RCV-02）は、ファイルの長さだけから計画を決める。

```swift
/// 書きかけファイルの救済の計画（FR-RCV-02）
public enum PartSalvage: Equatable, Sendable {
    /// truncatedSize まで切り詰め、ヘッダを書き直して Chunk にする
    case salvage(sampleCount: Int, truncatedSize: Int64)
    /// 0 サンプル・ヘッダより短い・Chunk の上限を超える。削除せず隔離する
    case quarantine

    public static func plan(fileSize: Int64) -> PartSalvage {
        let header = Int64(AudioConstants.wavHeaderBytes)
        let samples = (fileSize - header) / Int64(AudioConstants.bytesPerSample)
        guard fileSize > header, samples >= 1, samples <= Int64(AudioConstants.chunkSamples) else { return .quarantine }
        let truncatedSize = header + samples * Int64(AudioConstants.bytesPerSample)
        return .salvage(sampleCount: Int(samples), truncatedSize: truncatedSize)
    }
}
```

| 計画 | 処理 |
| --- | --- |
| `salvage` | ①ファイルを `truncatedSize`（44 + サンプル数 × 2）まで切り詰める（奇数バイトの端数を捨てる）→ ②ヘッダを書き直す → ③媒体まで同期する → ④全体を読み直して SHA-256 を計算する → ⑤§4.4 の判定（`SealDecision`）で公開・登録する。メモリ上の PCM はないので、照合の相手は読み直したファイル自身である。VAD は読み直した PCM から計算し直す |
| `quarantine` | 0 サンプル・44 バイト以下・Chunk の上限（480,000 サンプル）を超える・読めない場合。削除せず、会議の `quarantine/part-{連番}.wav.part` へ置換しない rename で移す。連番を `audio_chunks` に `quarantined` として登録し、`meeting.json` の `quarantinedSequences` にも書く。隔離した連番は Barrier の不成立の理由になる（§6.1） |

**未保存の記録**（FR-SAV-05）は、毎回もとの範囲から欠落を求める。

```swift
/// 未保存の記録と、救済した WAV の突き合わせの結果（基本設計 §8）
public enum UnsavedReconcile: Equatable, Sendable {
    /// 救済済みの範囲が元の範囲と一致した。記録を消してよい
    case fullySalvaged
    /// 足りない分が欠落として残る（救済できなかった場合は元の範囲の全体）
    case lost(SampleRange)
    /// 救済した WAV が元の範囲より長い。記録と合わないので invalid にする
    case inconsistent

    /// 毎回、元の範囲から求める（前回求めた欠落から差し引かない）
    public static func compare(original: SampleRange, salvagedCount: Int64) -> UnsavedReconcile {
        guard salvagedCount <= original.count else { return .inconsistent }
        guard salvagedCount < original.count,
              let rest = SampleRange(start: original.start + salvagedCount, count: original.count - salvagedCount)
        else { return .fullySalvaged }
        return .lost(rest)
    }
}
```

| 順 | 処理 |
| --- | --- |
| 1 | DB の `unsaved_chunks` と `meeting.json` の `unsavedChunks` を、連番ごとに和集合にする。同じ連番で元の範囲が食い違う場合は、両方を含む範囲を元の範囲とする（欠落を小さく見積もらない） |
| 2 | 同じ連番の登録済みの WAV（上の c・d で救済・登録したものを含む）があれば、そのサンプル数を「救済済みのサンプル数」として、両方の記録に書く（既にあれば同じ値で上書きする） |
| 3 | `UnsavedReconcile.compare` で比べる。`fullySalvaged` なら記録を消す。`lost` なら記録を残し、その範囲を「失われた未保存」として Barrier へ渡す。`inconsistent`（救済した WAV の方が長い）なら、Chunk を `invalid` にする |

- 欠落は「元の範囲 − 救済済みの範囲」として毎回求め、前回求めた欠落から差し引かない。同じ WAV を再起動のたびに照合しても、欠落は縮まない（基本設計 §8）。
- MVP の照合の対象は、`recording`・`stop_requested`・`finalizing` の会議である。DB を作り直した場合と印が残っている場合だけ、全会議に広げる。利用者が全件を再構築する機能（FR-RCV-03）は P2 とする。

---

## 10. 削除とデータ使用量

満たす要件: FR-MTG-03、SEC-DAT-05、NFR-CAP-02 ／ 決定: BD-STO-09

`DeletionService.delete(_:)` が、基本設計 §9 の順に行う。

| 順 | 処理 | 途中で落ちた場合 |
| --- | --- | --- |
| 0 | 録音中の会議なら拒否する。その会議の新しい書き出しの要求を拒否し始める（[export.md](export.md) §3）。文字起こし中・書き出し中なら中止を要求し、中止の完了を待つ（[transcription.md](transcription.md) §8.3 と同じ「中止の完了」） | 何も消えていない |
| 1 | 削除要求（`tombstones/{meetingId}`）を原子的に作り、続けて `meetings.deletion_requested_at` を記録する。この時点で一覧から消える | 削除要求があれば、次の起動で手順 2 から続ける |
| 2 | `tmp/export/{meetingId}/` を丸ごと消す | 同上 |
| 3 | 会議ディレクトリを丸ごと消す | 同上 |
| 4 | DB の行を消し（子の表は連鎖して消える）、`checkpointTruncate()` で WAL を切り詰める | 同上 |
| 5 | 移行の前の複製を開いて、同じ会議の行を消す（開けなければ複製を消す）。`quarantine/` に退避した DB はファイルごと消す | 同上 |
| 6 | 削除要求を消す | 削除要求が残る → 次の起動で手順 2 からやり直す（消すものがなければ何もせず、6 まで進む） |

| 項目 | 設計 |
| --- | --- |
| ディレクトリの削除 | 中身を深い方から 1 つずつ消す（`FileSystem.remove`）。途中で落ちても、次の起動で残りを消す |
| WAL の切り詰め | `checkpointTruncate()` が真（busy が 0 で、書き戻したフレーム数が WAL のフレーム数と一致）を返し、`-wal` の大きさが 0 であることを確かめる。偽なら 1 回やり直し、それでも偽なら削除要求を残したまま手順 5 以降へ進まず、問題 `deletion_incomplete` を表示する（次の起動で手順 2 から続ける） |
| 消した行の内容 | `secure_delete = ON` なので、消した行の内容は空き領域に残らない。削除して切り詰めた後の DB ファイルに、会議のタイトルのバイト列がないことを Mac 上で確かめた（§12.3） |
| 中止の後に届いた結果 | 保存しない。`MeetingStore` の、結果ファイル・版のファイル・ディレクトリを書く入口は、会議に削除要求がないことと、会議ディレクトリがあることを先に確かめる（§2.2） |
| 完了の確認 | 削除の後、会議 ID に関わるファイル・行が 0 件であることを自動テストで確かめる（TST-SEC-05）。会議のデータを会議ディレクトリの外に置く変更は、この試験の対象を必ず更新する |
| ログ | 段ごとに `meeting_deleted`（`stage`）を出す |

**データ使用量**（NFR-CAP-02）: 会議ごとの値は `audio_chunks.byte_count` の合計に、会議ディレクトリの下のそのほかのファイル（認識結果・版・`meeting.json`・隔離したデータ）の大きさを足したもの。全体は保存ルートの下の合計である。一覧と設定を開いたときに `MeetingStore` が計算し、常時監視はしない。値は表示にだけ使う（空き容量の判定には使わない）。

---

## 11. セキュリティ

満たす要件: SEC-DAT-01〜05・07、SEC-INT-01・02、SEC-INP-01・03、SEC-LOG-04 ／ 脅威: T-01・02・07・09・12

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| ロック中の録音と保護の両立 | 処理中は「最初のロック解除まで」、未完了の作業がなくなったら `complete`（§8）。新しいファイルは作った直後に保護クラスを明示して付ける（§4.1） | SEC-DAT-01・02、T-01 |
| D1・D2 の置き場所 | 会議ディレクトリだけ。DB の表に本文の列がなく、`SQLValue` が BLOB と実数を扱わない（§3.3・§4.2） | SEC-DAT-07 |
| バックアップ | 保存ルートと配下のディレクトリに除外を付け、起動のたびに検証して付け直す（§2.3） | SEC-DAT-03、T-02 |
| 外部からの可視性 | Application Support の下だけ。`Documents` と一時領域を使わない（§2.2） | SEC-DAT-04 |
| 完全性 | 保存時に、ファイル全体の SHA-256 を DB の外の記録に残す。確定・文字起こし・書き出しの前に読み直して照合する。不一致は記録して残し、記録を書き換えない（§4.4・§6.1） | SEC-INT-01、T-07 |
| 不正なファイル | WAV ヘッダを読むたびに検証する。通らない Chunk は `invalid` として残し、文字起こしに渡さない（§6.1） | SEC-INT-02 |
| パス | `StorageLayout` だけが組み立てる。部品は検証済みの型（§2.1） | SEC-INP-01、T-09 |
| 永続データの読み込み | `PersistedJSON.decode` と `validated()`。強制キャスト・強制アンラップを使わない（§3.1） | SEC-INP-03 |
| SQL | `StaticString` と束縛だけ（§4.2） | SEC-INP-03 |
| 削除 | 削除要求を先に立て、途中で落ちても続きを行う。DB は `secure_delete` と WAL の切り詰め（§10） | SEC-DAT-05、T-12 |
| エラーとログ | `StorageFailure` は種類・段・コードだけ。パス・SQLite の説明文を持たない | SEC-LOG-04 |

**実装・レビュー時の注意点**（基本設計 [architecture.md](../02-basic-design/architecture.md) §12.2 の NS-02・04・07・09 と、基本設計 §12 の注意点を、本書の型の単位に具体化したもの）:

| No. | 注意点 | 確かめ方 |
| --- | --- | --- |
| NS-S-01 | ディレクトリを作るのは `StorageRoot` だけにする。`FileSystem` にディレクトリを作る操作を足さない | 静的解析（`no_direct_directory_access`）、レビュー |
| NS-S-02 | 新しい種類のファイルを足すときは、①`StorageLayout` にパスを足したか、②会議ディレクトリの下にあるか（削除と保護クラスの切り替えの対象になるか）、③D1・D2 を DB に入れていないか、を確かめる | レビュー。TST-SEC-05 の対象を更新する |
| NS-S-03 | 一時ファイルを `NSTemporaryDirectory` に作らない。保存ルートの `tmp/` を使う | 静的解析（`no_direct_directory_access`） |
| NS-S-04 | ハッシュの記録を書き換える・作り直す処理を書かない。記録を作るのは `publishRecord`（新規作成だけ）の 1 か所にする | レビュー。単体（既存の記録が変わらないこと） |
| NS-S-05 | `SQLiteConnection` の SQL の引数を `String` に変えない。表や列の名前を変数にしたくなったら、文ごとに `StaticString` を書き分ける | ビルド（`String` は渡せない）、レビュー |
| NS-S-06 | `sqlite3_errmsg` と、`NSError` の `localizedDescription`・`userInfo` を読まない | レビュー。単体（[architecture.md](architecture.md) §13.1 のエラーの値の検査） |
| NS-S-07 | 保護データを読めない間の失敗で、DB を退避しない・ファイルを隔離しない・会議を削除しない | 単体（保護データを読めない状態を差し替えて、退避が起きないこと） |
| NS-S-08 | 会議の `quarantine/` と、保存ルートの `quarantine/` の中身も D1・D3 である。削除・保護クラス・バックアップ除外の対象にする | 単体（TST-SEC-05） |
| NS-S-09 | 置換しない rename を、置き換える rename に変えない。`replaceAtomically` を Chunk とハッシュの記録に使わない | レビュー。単体（既存のファイルが変わらないこと） |
| NS-S-10 | 墓標のある会議に、ファイルとディレクトリを作らない。結果を書く入口で確かめる | 単体（中止の後に届いた結果が保存されないこと） |
| NS-S-11 | 復旧と削除の処理に、ファイルを「直す」処理（ハッシュの付け替え、欠落の補間）を足さない。記録して利用者に見せる | レビュー |

---

## 12. テスト観点

満たす要件: TST-SAV-01〜03、TST-FIN-01〜03、TST-RCV-01・02・04、TST-SEC-04〜06、TST-GEN-02・04、TST-DEV-03・05・06・08・10

### 12.1 先に書いて失敗させるテスト（保存・確定・復旧の実装の順）

| 順 | テスト | 確かめること | 要件 |
| --- | --- | --- | --- |
| 1 | パスの組み立て | 連番 7 が `000007.wav`、版 3 が `v0003.json`。範囲の外の版・実行番号が `nil`。どのパスも保存ルートの下にある | TST-SEC-06 |
| 2 | 永続データの検証 | 正しい `meeting.json` を読める。形式の版・未知の状態・不正な会議 ID・型の違い・範囲外の位置・壊れた JSON が、分類されたエラーになる | TST-RCV-04 |
| 3 | Barrier（正常・異常） | 全 Chunk が `present` で通過。欠番・ファイル欠損・不正なヘッダ・隔離・ハッシュ不一致・未保存あり・失われた未保存を 1 つずつ | TST-FIN-01・02 |
| 4 | 確定の判定 | §4.4 の表の全行 | TST-SAV-02 |
| 5 | 空き容量の段階 | 境界の値（120 分・30 分・10 分のちょうどと 1 バイト下） | TST-SAV-03 |
| 6 | 保護クラスの判定・救済の計画・未保存の突き合わせ | §8・§9 の純粋関数の表 | TST-RCV-02 |
| 7 | 保存ルート | ディレクトリに除外が付く。保存ルートの属性を外すと、次の `prepare()` が付け直す | TST-SEC-04 |
| 8 | ファイル操作 | 置換しない rename が `EEXIST` で失敗する。作成が上書きしない。追記・上書き・切り詰め・原子的な置き換え | TST-SAV-01 |
| 9 | 移行と制約 | 移行が 1 回だけ適用される。録音中の会議 2 件が一意制約の違反になる。`STRICT` が型違いを拒否する。取引が取り消される | NFR-MNT-05 |
| 10 | Chunk の確定（冪等性） | 追記して確定すると完全な Chunk が 1 つできる。同じ Chunk の再確定で増えない。内容の異なる Chunk は隔離される | TST-SAV-02 |
| 11 | Chunk の確定（原子性） | 6 つの段のそれぞれで失敗を注入し、正式名のファイルが「ない」か「完全」であること、再試行で保存できること | TST-SAV-01 |
| 12 | 既存のハッシュの記録 | 記録だけがある状態で確定する。一致なら記録が変わらず登録される。不一致なら記録が変わらず `hash_mismatch` と競合が残る | SEC-INT-01 |
| 13 | 未保存の保持と上限（結合） | 書き込みを失敗させ続ける。録音が止まらず件数が増え、20 件で `disk_full` の停止になる。失敗をやめると古い順に保存される | TST-SAV-03 |
| 14 | 確定の手順（結合） | 停止 → Barrier → `finalized`。不成立 → 再試行 → 通過。例外確定で `lossRecord` が保存される | TST-FIN-01〜03 |
| 15 | 強制終了からの復旧（結合） | 録音の途中でパイプラインを捨て、復旧する。会議が `stop_requested`（理由 `terminated`）になり、書きかけが救済され、失ったサンプル数が追記していない分だけであること | TST-RCV-01 |
| 16 | 書きかけの救済（結合） | 途中まで・奇数バイト・0 バイト・ヘッダだけの `.part`。切り詰めと隔離。末尾の Chunk を隔離した会議が `finalized` にならないこと | TST-RCV-02 |
| 17 | 未保存の末尾の Chunk の喪失（結合） | 基本設計 §13 の同名の行（記録から失われた連番が検出される、短い `.part` の救済、0 サンプルの会議が残る、DB を作り直しても同じ結果） | FR-SAV-05 |
| 18 | DB の作り直し（結合） | 壊れた DB を置いて起動する。退避され、全会議が `meeting.json` とファイルから戻る。印の作成の後・退避の後・照合の途中で落としても、次の起動で全会議の照合が行われる | TST-RCV-04、INV-10 |
| 19 | 復旧の冪等性（結合） | 復旧を 2 回実行して、DB とファイルが変わらないこと | FR-RCV-01 |
| 20 | 削除（結合） | 各段で落として復旧し、会議 ID に関わるファイル・行・一時ファイルが 0 件。墓標の後に DB を作り直しても会議が復活しない。DB ファイルにタイトルのバイト列が残らない | TST-SEC-05 |
| 21 | 保護データを読めない起動（結合） | 保護データを読めない状態で起動し、DB が退避されないこと。読めるようになってから復旧が行われること | SEC-DAT-02 |

### 12.2 失敗の注入と差し替え

| 部品（`MinutesTestSupport`） | 内容 |
| --- | --- |
| `TemporaryDirectory` | テストごとに一意のディレクトリ。`StorageRoot(base:)` に渡す。DB のファイルもこの下にできるので、テストの間で共有しない（TST-GEN-02） |
| `FaultInjectingFileSystem` | 本番の `FileSystem` を包み、`fail(_:with:count:)` で指定した段（`StorageStage`）の操作を、指定した回数だけ指定した種類で失敗させる |
| `FixedFreeSpace` | 決めたバイト数を返す。途中で値を変えられる |
| `FakeDeviceConditions` | 保護データを読めるかどうかを切り替える |
| `InMemoryChunkStore` | `ChunkStoring` のメモリ上の実装。`MinutesApplicationTests`（Storage を参照できない）が、録音から確定までの手順を通すのに使う |

注入できる段は、作成（`create_file`・`write_hash_record`・`write_meeting_json`）・追記（`append`）・ヘッダの書き換え（`rewrite_header`）・同期（`sync`・`sync_directory`）・読み直し（`read_back`）・rename（`rename`）・削除（`delete`）である。DB の失敗は、テスト用の別の接続で制約の違反を先に作る・DB ファイルを壊す、のどちらかで起こす。

「途中で落ちた」状態は、失敗を注入して手順を止めたうえで、`MeetingStore` を作り直して復旧を実行することで再現する（プロセスを実際に終了させない）。

### 12.3 実装前に確かめたこと（2026-11-16）

本書のコード片を一時の Package（リポジトリの外）に置き、Mac 上で次を確かめた。

| 項目 | 結果 |
| --- | --- |
| ビルド | Swift 6.4.2・Swift 6 言語モード・警告をエラーとして扱う設定で、`MinutesDomain`・`MinutesStorage`・`MinutesTestSupport` がビルドできた。`SQLiteConnection` に `@unchecked Sendable` を付けずに、actor（`MeetingStore`）の中で持てた |
| テスト | 保存に関わる 28 件が通った（純粋ロジック 11 件、SQLite と移行 7 件、ファイル操作と保存ルート 5 件、Chunk の確定 5 件）。一時の Package の全体では 55 件 |
| 置換しない rename | 移動先があると `StorageFailure`（種類 `already_exists`・コード 17）になり、移動元も移動先も変わらなかった |
| 一意制約 | 録音中の会議の 2 件目は拡張コード 2067（`SQLITE_CONSTRAINT_UNIQUE`）、主キーの重複は 1555（`SQLITE_CONSTRAINT_PRIMARYKEY`）で、どちらも `constraint_violation` に分類された |
| 削除 | 会議の行を消すと子の行が消えた。`checkpointTruncate()` が真を返し、`-wal` の大きさが 0 になり、DB ファイルにタイトルのバイト列が残らなかった |
| 複製 | オンラインバックアップ API の複製に、WAL にだけある行が含まれた |
| Chunk の確定 | 6 つの段（ヘッダの書き換え・同期・読み直し・記録の作成・rename・ディレクトリの同期）のそれぞれで失敗させ、正式名のファイルが「ない」か「完全」で、再試行で 1 つの Chunk として保存できた。追記がすべて失敗していても、メモリ上の PCM から保存でき、未保存の記録が消えた |
| 属性 | バックアップ除外は、親が除外されていると配下も真と読めた。同じプロセスの中では、キャッシュを捨てないと古い値が返った。保護クラスの属性は、この Mac では設定した値を読み戻せた |
| 型名の衝突 | CryptoKit にも `SHA256Digest` という型がある。CryptoKit を import するファイル（`ChunkHasher.swift`）では、`MinutesDomain.SHA256Digest` と修飾して書く |
| 整形・静的解析 | SwiftFormat 0.64.7 で整形済み。SwiftLint 0.65.1 は、複数行の配列の末尾カンマ（[architecture.md](architecture.md) §11.2 の既知の食い違い）を除いて違反なし |

**設計・実装時の注意点:**

- 復旧と削除は、「2 回実行しても同じ結果」と「途中で落ちても続きができる」を必ず試験する。
- 移行の試験は、古い版の DB ファイルを作ってから適用し、既存のデータが残ることを確かめる（移行 2 を足すときに、移行 1 の DB を作る試験を足す）。
- Chunk の確定の試験は、成功だけで終わらせない。各段の失敗と、失敗の後の再試行を確かめる。
- テストで `AssembledChunk` を作るときは、`ChunkAssembler` に合成のサンプルを流して作る（`AssembledChunk` には公開の初期化子がない）。

### 12.4 シミュレータで確かめられないこと

| 事項 | 単体での扱い | 実機での確認 |
| --- | --- | --- |
| 保護クラスで、ロック中に読み書きできる・できない | 判定（`ProtectionPolicy`）を純粋関数として検証する。属性の値を読み戻す | TST-DEV-03（ロック中の録音。属性が設計どおりであること）、TST-DEV-10 |
| 保管への切り替えを、ロック中に行えるか | 行えない前提で保留にする | TST-DEV-03 と合わせて確かめ、結果を記録する |
| 強制終了で失う音声の長さ | パイプラインを捨てて、追記していないサンプル数を数える | TST-DEV-05 |
| 実際に容量の少ない端末での縮退 | 空き容量と書き込みの失敗を差し替える | TST-DEV-06 |
| バックアップから復元しても録音が戻らない | 属性の検証と付け直し | TST-DEV-08 |
| 保護データを読めない状態での起動 | 差し替えで再現する | 端末の再起動の直後（ロック解除の前）に起動する経路があるかを含めて、TST-DEV-03 で確かめる |

---

## 13. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

状態はすべて「未実装」である（実装の作業で、テスト名と状態を書き換える）。

| 要件 | テスト（Suite / 表示名の先頭） | 置き場所 | 状態 |
| --- | --- | --- | --- |
| FR-SAV-01、TST-SAV-01 | Chunk の保存 / TST-SAV-01 確定のどの段で失敗しても… | MinutesStorageTests | 未実装 |
| FR-SAV-01 | ファイル操作 / FR-SAV-01 作成は既存のファイルを上書きせず… | MinutesStorageTests | 未実装 |
| FR-SAV-02、SEC-INP-01、TST-SEC-06 | パス / TST-SEC-06 パスは会議 ID と番号だけから組み立てられ… | MinutesDomainTests | 未実装 |
| FR-SAV-03、INV-05、TST-SAV-02 | Chunk の保存 / TST-SAV-02 追記して確定すると…、TST-SAV-02 同じ連番で内容の異なる Chunk は… | MinutesStorageTests | 未実装 |
| FR-SAV-03 | 確定の判定 / TST-SAV-02 同じ連番のファイルと記録の有無で… | MinutesDomainTests | 未実装 |
| FR-SAV-04、NFR-CAP-01、TST-SAV-03 | 空き容量 / TST-SAV-03 残り時間と段階は… | MinutesDomainTests | 未実装 |
| FR-SAV-04・05、NFR-REL-05、TST-SAV-03 | 未保存の保持 / TST-SAV-03 書き込みに失敗しても録音が止まらず… | MinutesApplicationTests | 未実装 |
| FR-SAV-05 | Chunk の保存 / FR-SAV-05 追記がすべて失敗していても…。復旧 / FR-SAV-05 失われた末尾の Chunk は… | MinutesStorageTests | 未実装 |
| FR-FIN-01、TST-FIN-01 | Finalization Barrier / TST-FIN-01 欠番・未保存がなく… | MinutesDomainTests | 未実装 |
| FR-FIN-01・02、TST-FIN-02 | Finalization Barrier / TST-FIN-02 不成立の理由を 1 つずつ返す | MinutesDomainTests | 未実装 |
| FR-FIN-02 | 確定の手順 / FR-FIN-02 欠落を承知した確定で lossRecord が保存される | MinutesApplicationTests | 未実装 |
| FR-FIN-03、TST-FIN-03 | 会議の状態遷移（[architecture.md](architecture.md) §14）。確定の手順 / TST-FIN-03 不成立の後に再試行で確定できる | MinutesDomainTests・MinutesApplicationTests | 未実装 |
| FR-RCV-01、NFR-REL-04、TST-RCV-01 | 復旧 / TST-RCV-01 録音の途中で終了した会議が検出され… | MinutesStorageTests | 未実装 |
| FR-RCV-02、TST-RCV-02 | 復旧 / TST-RCV-02 書きかけファイルの救済は…（純粋関数と結合の両方） | MinutesDomainTests・MinutesStorageTests | 未実装 |
| FR-RCV-04 | 復旧の手順 7（[transcription.md](transcription.md) §11 の TST-RCV-03） | MinutesStorageTests | 未実装 |
| SEC-INP-03、TST-RCV-04 | 永続データの検証 / TST-RCV-04 壊れた meeting.json は…。復旧 / TST-RCV-04 壊れた DB は退避され… | MinutesDomainTests・MinutesStorageTests | 未実装 |
| INV-10 | 復旧 / INV-10 DB を作り直しても、会議と Chunk がファイルから戻る | MinutesStorageTests | 未実装 |
| FR-MTG-03、SEC-DAT-05、TST-SEC-05 | 削除 / TST-SEC-05 削除の後に会議 ID に関わるものが 0 件である | MinutesStorageTests | 未実装 |
| SEC-DAT-03、TST-SEC-04 | 保存ルート / TST-SEC-04 保存ルートはバックアップ除外つきで作られ… | MinutesStorageTests | 未実装 |
| SEC-DAT-02 | 保護クラスの判定 / SEC-DAT-02 保管への切り替えは…。復旧 / SEC-DAT-02 保護データを読めない間は DB を退避しない | MinutesDomainTests・MinutesStorageTests | 未実装 |
| SEC-INT-01 | Chunk の保存 / SEC-INT-01 既存のハッシュの記録と合わない Chunk は… | MinutesStorageTests | 未実装 |
| SEC-INT-02 | 確定 / SEC-INT-02 ヘッダの不正な Chunk は invalid として残る（ヘッダの検証は [recording.md](recording.md) §12） | MinutesStorageTests | 未実装 |
| NFR-MNT-05 | SQLite / NFR-MNT-05 移行は番号順に 1 回だけ適用され… | MinutesStorageTests | 未実装 |
| FR-REC-07 | SQLite / FR-REC-07 録音中の会議を 2 件にすると… | MinutesStorageTests | 未実装 |
| FR-CNS-02 | 会議の作成 / FR-CNS-02 同意の時刻が meeting.json と DB に残る（[recording.md](recording.md) §12 と対） | MinutesStorageTests | 未実装 |
| NFR-CAP-02 | データ使用量 / NFR-CAP-02 会議ごとの値が Chunk と付随ファイルの合計になる | MinutesStorageTests | 未実装 |
| TST-GEN-02 | 各結合テストが `TemporaryDirectory` を使う（§12.2） | MinutesStorageTests | 未実装 |
| TST-GEN-04 | `FileSystem`・`FreeSpaceProviding`・`DeviceConditionsProviding` の差し替え（§12.2） | MinutesStorageTests | 未実装 |
| SEC-DAT-01・02 | 実機試験 TST-DEV-03・10 | 実機 | 未実施 |
| FR-RCV-01、NFR-REL-04 | 実機試験 TST-DEV-05 | 実機 | 未実施 |
| FR-SAV-04 | 実機試験 TST-DEV-06 | 実機 | 未実施 |
| SEC-DAT-03 | 実機試験 TST-DEV-08 | 実機 | 未実施 |
| SEC-DAT-04 | 設定の検査（[dev-environment.md](dev-environment.md) §8） | 静的解析 | 作業単位 2 の後 |

---

## 14. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [storage.md](../02-basic-design/storage.md) §14 の 6 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| 表の列・索引・制約、移行の番号付け | 10 の表と `schema_version` を、移行 1 として定義した。すべて `STRICT`、録音中は 1 件の部分一意索引、子の表は連鎖削除。番号は 1 からの連番で、1 つの移行を 1 取引で適用する | §3.3・§3.4 |
| `meeting.json`・ハッシュの記録・DB の外の削除要求の項目と形式の版 | 3 つとも形式の版 1。`MeetingDocument`・`ChunkHashRecord`・`Tombstone` として型と検証を定義した。日時は UTC の経過ミリ秒 | §3.1・§3.2 |
| 置換しない rename の実現方法と、競合の版のファイル名 | `renamex_np` の `RENAME_EXCL`。競合の版は `quarantine/conflict-{連番}-{SHA-256 の先頭 16 桁}.wav` | §2.1・§4.1 |
| SQLite ラッパーの API（束縛・取引・エラー） | SQL は `StaticString`、値は 3 種の `SQLValue` の束縛、取引は `transaction`、エラーは拡張コードから `StorageFailure` に分類する | §4.2 |
| 同期の方式と周期、段階ごとの閾値の最終値 | 追記は取り出しのたび、途中の同期は 80,000 サンプルごとに `fsync`、確定時は `F_FULLFSYNC`。閾値は基本設計の初期値のまま（予備 200 MB、120 分・30 分・10 分、保持 20 Chunk） | §4.3・§7 |
| `seal_pending` の解消を起こすきっかけ | 保護データを読めるようになった通知、前面への復帰、起動時の復旧の手順 9 の 3 つ | §8 |

---

## 15. 断定しない事項・確かめていない事項

満たす要件: SEC-DAT-01・02、NFR-REL-04、FR-SAV-04・05 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| 「最初のロック解除まで」の保護クラスで、ロック中に Chunk と DB を書ける | 属性を読み書きできることだけを Mac 上で確かめた | TST-DEV-03。この結果で BD-STO-04 を確定する |
| ロック中に文字起こしがファイルを読める | 確かめていない | TST-DEV-10 |
| 保管への切り替えをロック中に行えるか | 確かめていない。行えない前提で保留にする | 実機で確かめて記録する |
| 新しいファイルに保護クラスを付ける操作が、ロック中にも成功する | 確かめていない。失敗する場合は、その失敗が Chunk の書き込みの失敗（§7 の段階 3）になる | TST-DEV-03。失敗するなら、ディレクトリからの引き継ぎに切り替える（そのときは引き継がれることを先に確かめる） |
| `isExcludedFromBackup` が、iOS の実機でも配下で真になる・キャッシュされる | Mac 上でだけ確かめた | 保存の実装（シミュレータ）と TST-DEV-08 |
| 強制終了で失う音声が約 1 秒に収まる | 設計上の目標。OS の書き込みの扱いに依存する | TST-RCV-01・TST-DEV-05 で実測する |
| 取り出しのたびに開いて追記する方式で、録音の処理が遅れない | 確かめていない | 録音の実装（計測値）、TST-DEV-01。遅れるなら、追記をまとめる・記述子を保持する方式に改める |
| 5 秒ごとの `fsync` と確定時の `F_FULLFSYNC` が、電池と性能に影響しない | 確かめていない | TST-DEV-01（電池消費の記録） |
| 書き込みに失敗する状況で、未保存の記録を書ける | 予備（200 MB）を残すが、容量の枯渇では DB の書き込みも失敗しうる | TST-DEV-06。書けなかった末尾の Chunk は再起動後に検出できないので、段階 3 の表示で「終了すると失われる」と示す |
| 空き容量の値が、実際の書き込みの可否と一致する | OS の推定値である | 書き込みの失敗の捕捉を最後の防衛線にする |
| `PRAGMA quick_check` で、壊れた DB を検出できる範囲 | 全ページの検査ではない | 壊れた DB の試験（§12.1 の 18）。読み書きの途中で `database_corrupt` が返った場合も、次の起動で退避する |
| 保護データを読めない状態でアプリが起動される経路があるか | 分からない（録音・継続処理タスクは前面から始める設計） | TST-DEV-03。経路がなくても、退避しない規則（§3.5）は残す |
| `MeetingRecordStoring` などの protocol のシグネチャ | `ChunkStoring` だけをビルドして確かめた | 保存の実装で確定し、本書に追記する |
| 120 分の会議の Barrier（約 220 MiB の読み直し）にかかる時間 | 測っていない | 保存の実装（計測値）。進み具合を表示する |

---

## 16. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- iOS 27.2 SDK の `sqlite3.h`（SQLite 3.54.0。`SQLITE_OPEN_FILEPROTECTION_COMPLETEUNTILFIRSTUSERAUTHENTICATION`・`SQLITE_OPEN_EXRESCODE`、拡張コード `SQLITE_CONSTRAINT_UNIQUE`・`SQLITE_CONSTRAINT_PRIMARYKEY`、`sqlite3_backup_init`・`_step`・`_finish`、`sqlite3_wal_checkpoint_v2` と `SQLITE_CHECKPOINT_TRUNCATE`）
- iOS 27.2 SDK の `sys/stdio.h`（`renamex_np`・`RENAME_EXCL`）、`sys/fcntl.h`（`F_FULLFSYNC`・`O_NOFOLLOW`・`O_CLOEXEC`）
- iOS 27.2 SDK の `Foundation.swiftinterface`・`NSFileManager.h`・`NSURL.h`（保護クラスの属性、バックアップ除外、重要用途向けの空き容量、属性のキャッシュの破棄）
- Xcode 27.2（27B52）・Swift 6.4.2 の `swift build`・`swift test`（一時の Package で実行。§12.3 の結果はこの実行による。`STRICT` の表・`secure_delete`・WAL の切り詰め・オンラインバックアップ・属性の挙動は、macOS 同梱の SQLite と macOS のファイルシステムで動かして確かめたもの）
- Minutes `docs/design-local-phase1.md` §3.4・§11・§13・§16・§21〜§23・§26、`docs/design-local-phase2.md` §8（別リポジトリ・読み取り専用。本作業では参照できず、基本設計の記述に基づいた）
