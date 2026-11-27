# MinutesMobile 詳細設計書 ── 文字起こし

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版・実装前） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | FR-STT-01〜09・12・13、FR-TRV-02・03、FR-RCV-04、NFR-REL-03、NFR-PRF-03、NFR-PWR-02・03、NFR-L10N-02、SEC-NET-03、SEC-INT-03、INV-03・04・05 |
| 関係する制約 | CON-01・04・09 |
| 対応する基本設計 | [../02-basic-design/transcription.md](../02-basic-design/transcription.md)（設計決定 BD-STT-01〜10 を引用する） |
| 関連文書 | [architecture.md](architecture.md)・[recording.md](recording.md)・[storage.md](storage.md)・[ui.md](ui.md)・[export.md](export.md) |

本書は、確定した会議の録音を Chunk 単位のジョブで文字起こしにし、統合して版として保存するまでを、型とアルゴリズムの単位で定める。Chunk とメタデータの保存は [storage.md](storage.md)、画面は [ui.md](ui.md) にある。FR-STT-10（VAD によるスキップ）と FR-STT-11（代替エンジン）は P2 で、本書は扱わない。

**本書のコード片は、実装前に一時の Package でビルドとテストを通したものである**（§11.3）。実ファイルはまだないので、照合の印（`<!-- file: -->`）は付けていない。実装の作業で印を付ける（[README.md](README.md) §3）。

---

## 1. 位置づけと範囲

満たす要件: FR-STT-01・02、NFR-MNT-02・04、INV-03 ／ 決定: BD-STT-01〜03、UD-10

| 本書で決めること | 節 | 置き場所（[architecture.md](architecture.md) §2） |
| --- | --- | --- |
| 設計値 | §2 | `MinutesDomain/Transcription/` |
| エンジンの protocol、可否の判定、`SpeechAnalyzer` への入力の組み立て | §3 | protocol は Domain、実装は `MinutesTranscription` |
| Chunk の認識の手順、Overlap、時刻の変換、結果ファイル | §4 | Domain（純粋ロジック）。手順は `MinutesApplication/TranscriptionService` |
| ジョブの状態遷移、失敗の分類、実行世代、次のジョブの選び方、起動時の回収 | §5 | 判定は Domain、表の操作は `MinutesStorage/Jobs/` |
| 統合の規則、版のファイル、版の保存と完全性 | §6 | Domain（純粋ロジック）と `MinutesStorage` |
| 版管理と再文字起こし、失敗区間の再試行 | §7 | `TranscriptionService` |
| 開始条件と延期、録音の開始による中止 | §8 | 判定は Domain |
| バックグラウンドでの実行 | §9 | protocol は Domain、実装はアプリの `Platform/` |

基本設計の決定（BD-STT-01〜10）は変えていない。本書で細部を決めたことは次のとおりである。

| 事項 | 内容 | 節 |
| --- | --- | --- |
| 直前の Chunk のヘッダが不正な場合の Overlap の理由（基本設計 §5.2 の宿題） | **`hash_mismatch` に含める。** ハッシュの記録がない Chunk も同じ理由にする。隔離した書きかけは `file_missing` にする。理由の種類は増やさないので、FR-STT-03 ③は変えない | §4.2 |
| 1 回の認識の単位 | 1 つの Chunk のジョブが、1 つの解析（`SpeechAnalyzer` を作る → 入力を渡す → 入力の終わりまで確定 → 終了）である。結果の時刻は入力の先頭から数える | §3.3 |
| 入力の変換 | iOS 27 の `AnalyzerInputConverter` を使う（エンジンが受け付ける形式への変換を OS に任せる） | §3.3 |
| 継続処理タスクの投入の名前 | iOS 27 で同期の `submit(_:)` が非推奨になった。`try await submitTaskRequest(_:)` を使う | §9 |

---

## 2. 値と設計値

満たす要件: FR-STT-03・08、NFR-REL-03 ／ 決定: BD-STT-06

```swift
/// 文字起こしの設計値（初期値）。試験では差し替える
public struct TranscriptionTuning: Equatable, Sendable {
    /// Overlap（前の Chunk の末尾からつなぐサンプル数。48,000 = 3 秒）
    public var overlapSamples = 48000
    /// 重複を比べる範囲（境界の前後。56,000 = 3.5 秒）
    public var boundaryWindowSamples: Int64 = 56000
    /// 時間の重なり（重なる長さ ÷ 短い方の長さ）の下限
    public var timeOverlapThreshold = 0.5
    /// 本文の類似の下限
    public var similarityThreshold = 0.8
    /// 包含とみなす長さの比（短い方 ÷ 長い方）の範囲
    public var containmentRatio = 0.3 ... 0.95
    /// 低信頼度として表示するしきい値（FR-STT-08）
    public var lowConfidenceThreshold = 0.4
    /// 試行回数の上限
    public var maxAttempts = 5
    /// 再実行までの間隔（秒）。試行回数が配列より多ければ最後の値を使う
    public var retryDelaysSeconds = [2, 5, 10, 30, 60]
    /// 間隔のゆらぎ（±20%）
    public var retryJitter = 0.2
    /// 1 件のジョブの上限時間（音声の長さの倍数）
    public var timeoutFactor = 10
    /// 1 件のジョブの上限時間の下限（ms）。空でない入力は、倍数で求めた値が短くてもこの値を下回らない
    public var minimumTimeoutMs: Int64 = 30000
    /// 打ち切ったエンジンの実行の停止（cancel() の戻り）を待つ上限（ms）
    public var stopConfirmTimeoutMs: Int64 = 5000

    public init() {}
}
```

| 設計値 | 値 | 出どころ |
| --- | --- | --- |
| Overlap | 48,000 サンプル（3 秒） | FR-STT-03、基本設計 §5.2 |
| 重複を比べる範囲 | 境界の前後 56,000 サンプル（3.5 秒） | 基本設計 §6.1 |
| 時間の重なり・本文の類似・包含の比 | 0.5 以上・0.8 以上・0.3〜0.95 | 基本設計 §6.1（最終値として確定） |
| 低信頼度のしきい値 | 0.4 | 基本設計 §5.3。実測して見直す |
| 試行回数の上限 | 5 | 基本設計 §4.2 |
| 再実行までの間隔 | 2・5・10・30・60 秒に ±20% のゆらぎ | 基本設計 §4.2 |
| 1 件のジョブの上限時間 | 入力の長さの 10 倍（30 秒の Chunk + 3 秒の Overlap なら 330 秒）。空でない入力は 30 秒（`minimumTimeoutMs`）を下回らない | 基本設計 §4.2。下限は本書で決めた初期値で、エンジンの起動から結果までの固定の時間を実測して見直す（§14） |
| 打ち切った実行の停止を待つ上限 | 5 秒（上限を過ぎても待ち続け、その間は次の試行を始めない。§5.2） | 基本設計 §4.2 の「止まっていない実行」。実エンジンで止まるまでの時間を実測して見直す（§14） |

値はすべて差し替えられる（試験では小さい値を使う）。境界での取りこぼし・重複を記録して見直す（§14）。

全体の流れは基本設計 §2 の図のとおりである。本書の各節との対応を示す。

| 段 | 内容 | 節 |
| --- | --- | --- |
| 開始条件の評価 | 録音中でない・エンジンが使える・温度・低電力・自動開始 | §8 |
| 実行の開始 | 実行番号の発番、ジョブの作成、`transcribing` への遷移 | §5.4・§7 |
| ジョブの実行 | Chunk を 1 件ずつ認識して結果ファイルを書く | §4・§5 |
| 統合 | 全ジョブが終わりになったら、新しい版を保存して `transcribed` にする | §6 |

---

## 3. エンジンの抽象と可否の判定

満たす要件: FR-STT-01・06・07・09、NFR-L10N-02、SEC-NET-03、TST-GEN-04 ／ 決定: BD-STT-01・08・10

### 3.1 エンジンの protocol

```swift
/// エンジンの可否（FR-STT-06）。available 以外では、会議を finalized のまま待たせる
public enum EngineAvailability: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case available
    case assetMissing = "asset_missing"
    case unsupportedDevice = "unsupported_device"
    case unsupportedLocale = "unsupported_locale"
    case temporarilyUnavailable = "temporarily_unavailable"
}

public enum EngineKind: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    /// OS 標準（SpeechAnalyzer + SpeechTranscriber）
    case speechAnalyzer = "speech_analyzer"
    /// テストの差し替え
    case scripted
}

/// エンジンが返す確定した結果 1 件。位置は、渡した入力の先頭からのサンプル番号（16 kHz）
public struct RecognizedSpan: Equatable, Sendable {
    public let startSample: Int
    public let endSample: Int
    public let text: String
    /// 取得できなければ nil（FR-STT-08）
    public let confidence: Double?

    public init(startSample: Int, endSample: Int, text: String, confidence: Double?) {
        self.startSample = startSample
        self.endSample = endSample
        self.text = text
        self.confidence = confidence
    }
}

/// エンジンの失敗。OS のエラーの説明文は持たない（発話が含まれうる。SEC-LOG-03・04）
public enum EngineFailure: Error, Equatable, Sendable {
    case assetMissing
    case unavailable
    /// 中止（録音の開始・バックグラウンドの打ち切り・利用者の中止・延期）
    case cancelled
    case insufficientResources
    case timedOut
    case invalidAudio
    case other(osCode: Int32)

    /// ジョブの失敗の分類への写像（§5.2）
    public var failureClass: TranscriptionFailureClass {
        switch self {
        case .assetMissing: .assetMissing
        case .unavailable: .engineUnavailable
        case .cancelled: .systemExpired
        case .insufficientResources: .insufficientResources
        case .timedOut: .timeout
        case .invalidAudio: .invalidAudio
        case .other: .internal
        }
    }
}

/// 音声認識エンジン（注入点。BD-STT-01）。本番は AnalyzerSpeechEngine、テストは ScriptedSpeechEngine
public protocol SpeechEngine: Sendable {
    func availability() async -> EngineAvailability
    /// 可否が available でなければ nil
    func info() async -> EngineInfo?
    /// 言語アセットを取得して導入する。**利用者の明示操作からだけ呼ぶ**（FR-STT-07）。progress は 0〜1
    func installAssets(progress: @escaping @Sendable (Double) -> Void) async throws(EngineFailure)
    /// 16 kHz・モノラルの PCM を認識し、確定した結果だけを返す（途中経過は返さない）
    func transcribe(_ samples: [Int16]) async throws(EngineFailure) -> [RecognizedSpan]
    /// 実行中の認識を中止する。認識の操作全体（準備の await を含む）が終わり、解析が止まり、
    /// これから解析が始まることもない状態になってから戻る。実行中でなければ何もしない
    func cancel() async
}
```

| 項目 | 設計 |
| --- | --- |
| 入力 | 16 kHz・モノラルの `[Int16]`（Overlap の分と当該 Chunk をつないだもの）。エンジンは、どの会議のどの Chunk かを知らない |
| 出力 | 確定した結果だけ（`RecognizedSpan`）。位置は入力の先頭からのサンプル番号。途中経過は要求しない |
| 失敗 | `EngineFailure`（7 種）。OS のエラーの説明文は持たない。ジョブの失敗の分類への写像は `failureClass`（§5.2） |
| 中止 | `cancel()` は、認識の操作全体が終わってから戻る（準備の `await` の途中でも、解析の作成・開始の前でも、解析の実行中でも）。戻った時点で、解析は止まっていて、これから始まることもない。**戻ったことが停止の確認である**（§5.2 の「止まっていない実行」）。録音の開始は、これを待たない（§8.3） |
| エンジンの情報（`EngineInfo`） | エンジンの種類・ロケール・OS の版・OS のビルド番号の 4 つ。文字列の 3 つは、英数字と `.`・`_`・`-` だけ・32 文字までに限る（永続データから読んだ値をそのまま画面に出すため） |
| 差し替え（`ScriptedSpeechEngine`） | 入力の長さ・呼ばれた回数に応じて、決めた結果・失敗・遅延を返す。`cancel()` が呼ばれたことを記録する |

### 3.2 可否の判定と言語アセット

| 可否（`EngineAvailability`） | 本番の実装での条件 | 利用者ができること |
| --- | --- | --- |
| `unsupported_device` | `SpeechTranscriber.isAvailable` が偽 | なし（録音と書き出しは使える） |
| `unsupported_locale` | `SpeechTranscriber.supportedLocale(equivalentTo:)` が `nil`、またはアセットの状態が `unsupported` | なし |
| `asset_missing` | アセットの状態が `supported`（取得できる）・`downloading`（取得中） | 明示の操作で取得を始める |
| `available` | アセットの状態が `installed` | — |
| `temporarily_unavailable` | SDK に将来足される未知の状態 | 後で再試行する |

- 可否は、開始条件を評価するたびに問い合わせる（§8）。特定の OS の版で日本語が提供されていることを前提にしない（基本設計 §3.1 の OQ-05 の結論）。
- ロケールは `ja_JP` を渡し、エンジンが返した同等のロケール（`supportedLocale(equivalentTo:)` の結果）を使う。
- **言語アセットの取得**（FR-STT-07）は `installAssets(progress:)` で、設定画面または会議詳細の、利用者の操作からだけ呼ぶ（[ui.md](ui.md) §4.6）。呼ぶ前に、画面が「OS が音声認識モデルをダウンロードします。会議データは送信されません」を表示する。取得は OS が行う通信で、アプリのコードは通信 API を使わない（SEC-NET-03）。自動では呼ばない。完了したら、開始条件を評価し直す。
- `SFSpeechRecognizer` は使わない（静的解析の `no_legacy_speech_recognizer` が禁止している。BD-STT-10）。

### 3.3 本番の実装（`AnalyzerSpeechEngine`）

```swift
/// SpeechAnalyzer + SpeechTranscriber を包むエンジン（BD-STT-01）。
/// 1 回の transcribe が 1 つの解析（開始 → 入力の終わりまで確定 → 終了）で、結果の時刻は入力の先頭から数える
public actor AnalyzerSpeechEngine: SpeechEngine {
    private let locale: Locale
    /// 認識の操作全体（ロケールの問い合わせ・入力の変換・解析）。cancel() はこれの終了を待つ
    private var operation: Task<[RecognizedSpan], any Error>?
    /// 作成して開始した解析。operation の中でだけ設定する
    private var running: SpeechAnalyzer?

    public init(locale: Locale = Locale(identifier: "ja_JP")) {
        self.locale = locale
    }

    public func availability() async -> EngineAvailability {
        guard SpeechTranscriber.isAvailable else { return .unsupportedDevice }
        guard let supported = await SpeechTranscriber.supportedLocale(equivalentTo: locale) else {
            return .unsupportedLocale
        }
        switch await AssetInventory.status(forModules: [Self.makeTranscriber(supported)]) {
        case .installed: return .available
        case .supported, .downloading: return .assetMissing
        case .unsupported: return .unsupportedLocale
        @unknown default: return .temporarilyUnavailable
        }
    }
```

```swift
    public func transcribe(_ samples: [Int16]) async throws(EngineFailure) -> [RecognizedSpan] {
        // 前の操作が終わる前には始めない（ジョブの側は、停止を確かめてからしか呼ばない。§5.2）
        guard operation == nil else { throw .cancelled }
        let task = Task { try await self.run(samples) }
        operation = task
        defer { operation = nil }
        do {
            return try await task.value
        } catch {
            throw Self.classify(error)
        }
    }

    /// 認識の操作の本体。await の後ごとに中止を確かめ、中止されていれば解析を作らず・始めない
    private func run(_ samples: [Int16]) async throws -> [RecognizedSpan] {
        guard let supported = await SpeechTranscriber.supportedLocale(equivalentTo: locale) else {
            throw EngineFailure.unavailable
        }
        try Task.checkCancellation()
        let transcriber = Self.makeTranscriber(supported)
        let inputs = try await Self.inputs(for: samples, module: transcriber)
        // ここから解析を start するまで中断点がないので、cancel() は「作る前」か「running が設定された後」にだけ入る
        try Task.checkCancellation()
        // モデルは、使い終わってもしばらく保持する（Chunk ごとの読み込み直しを避ける）
        let options = SpeechAnalyzer.Options(priority: .utility, modelRetention: .lingering)
        let analyzer = SpeechAnalyzer(modules: [transcriber], options: options)
        running = analyzer
        defer { running = nil }
        async let spans = Self.collect(from: transcriber)
        let (stream, continuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
        for input in inputs {
            continuation.yield(input)
        }
        continuation.finish()
        try await analyzer.start(inputSequence: stream)
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        return try await spans
    }

    public func cancel() async {
        // transcribe が待っている間に呼ばれる（actor の再入）
        guard let task = operation else { return }
        // 準備の await の途中なら、次の checkCancellation で解析を作らずに終わる
        task.cancel()
        // 解析の実行中なら、解析を止める（入力の列は終わっている。エンジンに新しい音声を渡さない）
        if let analyzer = running {
            await analyzer.cancelAndFinishNow()
        }
        // 操作全体の終了を待つ。戻った時点で解析は止まっていて、これから始まることもない
        _ = await task.result
    }

    /// 途中経過（volatile）は要求しない。時刻範囲と信頼度の属性を要求する
    private static func makeTranscriber(_ locale: Locale) -> SpeechTranscriber {
        SpeechTranscriber(
            locale: locale, transcriptionOptions: [], reportingOptions: [],
            attributeOptions: [.audioTimeRange, .transcriptionConfidence],
        )
    }

    /// 16 kHz・Int16・モノラルの PCM を、エンジンが受け付ける形式の入力にする
    private static func inputs(for samples: [Int16], module: SpeechTranscriber) async throws -> [AnalyzerInput] {
        let frames = AVAudioFrameCount(samples.count)
        let sampleRate = Double(AudioConstants.sampleRate)
        guard let format = AVAudioFormat(
                  commonFormat: .pcmFormatInt16, sampleRate: sampleRate, channels: 1, interleaved: true,
              ), let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let channel = buffer.int16ChannelData else { throw EngineFailure.other(osCode: 0) }
        samples.withUnsafeBufferPointer { source in
            if let base = source.baseAddress {
                channel[0].update(from: base, count: samples.count)
            }
        }
        buffer.frameLength = frames
        let converter = try await AnalyzerInputConverter.converter(compatibleWith: [module])
        return try converter.convert(buffer, at: nil) + converter.flush()
    }

    private static func collect(from transcriber: SpeechTranscriber) async throws -> [RecognizedSpan] {
        var spans: [RecognizedSpan] = []
        for try await result in transcriber.results where result.isFinal {
            let text = String(result.text.characters)
            guard !text.isEmpty else { continue }
            spans.append(RecognizedSpan(
                startSample: sample(at: result.range.start), endSample: sample(at: result.range.end),
                text: text, confidence: confidence(of: result.text),
            ))
        }
        return spans
    }

    private static func sample(at time: CMTime) -> Int {
        time.isNumeric ? Int((time.seconds * Double(AudioConstants.sampleRate)).rounded()) : 0
    }

    /// 区間ごとの信頼度を、文字数で重み付けして平均する（FR-STT-08）。1 つも取れなければ nil
    private static func confidence(of text: AttributedString) -> Double? {
        var weighted = 0.0
        var total = 0
        for run in text.runs {
            guard let value = run.transcriptionConfidence else { continue }
            let length = text[run.range].characters.count
            weighted += value * Double(length)
            total += length
        }
        return total == 0 ? nil : weighted / Double(total)
    }

    /// OS のエラーを分類する。説明文は読まない（発話が含まれうる。SEC-LOG-03・04）
    private static func classify(_ error: any Error) -> EngineFailure {
        if let failure = error as? EngineFailure {
            return failure
        }
        if error is CancellationError {
            return .cancelled
        }
        let detail = error as NSError
        let code = Int32(truncatingIfNeeded: detail.code)
        guard detail.domain == SFSpeechError.errorDomain, let known = SFSpeechError.Code(rawValue: detail.code) else {
            return .other(osCode: code)
        }
        if known == .noModel || known == .assetLocaleNotAllocated {
            return .assetMissing
        }
        if known == .insufficientResources {
            return .insufficientResources
        }
        if known == .timeout {
            return .timedOut
        }
        return .other(osCode: code)
    }
```

| 決めたこと | 内容 |
| --- | --- |
| モジュール | `SpeechTranscriber` を、途中経過なし（`reportingOptions` が空）・時刻範囲と信頼度の属性つき（`attributeOptions` に `audioTimeRange`・`transcriptionConfidence`）で作る |
| 解析の単位 | ジョブごとに `SpeechAnalyzer` を作り、`start(inputSequence:)` → `finalizeAndFinishThroughEndOfInput()` で終える。解析をまたいで状態を持たないので、ジョブが独立で、何度実行しても同じ形の結果になる |
| モデルの保持 | `modelRetention` は `lingering`（使い終わってもしばらく保持する）。Chunk ごとにモデルを読み込み直さない（基本設計 §5.1 の申し送り）。`processLifetime` にしないのは、文字起こしの後もメモリを占め続けるためである |
| 優先度 | `utility`。前面の操作と録音より下にする |
| 入力の組み立て | `[Int16]` を 16 kHz・Int16・モノラルの `AVAudioPCMBuffer` に写し、`AnalyzerInputConverter.converter(compatibleWith:)`（iOS 27）で、エンジンが受け付ける形式の `AnalyzerInput` の列にする。変換の末尾は `flush()` で吐き出す |
| 結果の読み出し | `results` の列から、確定した結果（`isFinal`）だけを集める。本文は `AttributedString` の文字列、時刻は `range`（`CMTimeRange`）を 16,000 倍してサンプル番号にする |
| 信頼度 | 結果の中の区間ごとの値（`transcriptionConfidence`）を、文字数で重み付けして平均する。1 つも取れなければ `nil` |
| 中止 | 認識の操作全体を `operation`（`Task`）として持つ。`cancel()` は、①`operation` を中止する（準備の `await` の後ごとの `checkCancellation()` で、解析を作らず・始めずに終わる）、②解析が実行中なら `cancelAndFinishNow()` を呼ぶ、③`operation` の終了を待つ、の順に行い、③が終わってから戻る。`checkCancellation()` から `running` の設定と `start` の呼び出しまでの間に中断点がないので、中止が「解析を作った後・`running` に入れる前」に入り込むことはない。actor の再入により、`transcribe` が待っている間に実行される。前の操作が終わる前の `transcribe` は始めずに `cancelled` を投げる |
| エラーの分類 | `SFSpeechError` のコードだけを見る。`noModel`・`assetLocaleNotAllocated` → `assetMissing`、`insufficientResources` → `insufficientResources`、`timeout` → `timedOut`、`CancellationError` → `cancelled`、そのほか → `other(osCode:)`。**説明文（`localizedDescription`）は読まない** |
| エンジンの情報 | OS の版は `ProcessInfo.operatingSystemVersion`、ビルド番号は `operatingSystemVersionString` の `Build` の後ろから取り出す（取り出せなければ `unknown`） |

`AnalyzerSpeechEngine` は macOS でもビルドできる（`MinutesTranscription` を Mac 上のテストの対象にするため。[architecture.md](architecture.md) §2.1）。この Mac では、可否が `asset_missing`（日本語に対応しているが、言語アセットが入っていない）と判定された（§11.3）。言語アセットの取得は OS の通信を伴うので、本作業では行っていない。実際に日本語を認識できることは、実機で確かめる（TST-STT-07）。

---

## 4. Chunk の認識

満たす要件: FR-STT-01・03・04・08・09、INV-03・05 ／ 決定: BD-STT-02・03

### 4.1 手順

`TranscriptionService` が、`transcribe_chunk` のジョブ 1 件につき次を行う（基本設計 §5.1）。

| 順 | 処理 | 失敗時 |
| --- | --- | --- |
| 1 | ジョブを実行開始にする（試行回数を増やし、新しい実行世代を記録する。§5.3） | 取れなければ何もしない（別の実行が先に取った） |
| 2 | 当該 Chunk を読む。`MeetingStore` が、読み取り専用で開き、ヘッダを検証し（[recording.md](recording.md) §4.5）、ファイル全体の SHA-256 をハッシュの記録と照合してから、PCM を返す | `INVALID_AUDIO`。Chunk のファイルの状態を更新する（[storage.md](storage.md) §6.1） |
| 3 | Overlap の可否を判定する（§4.2）。可なら、直前の Chunk を同じ検証つきで読み、末尾を取り出す | 直前の Chunk を読めなければ、Overlap なし（理由を記録）で続ける |
| 4 | Overlap の分と当該 Chunk をつないで、エンジンに渡す。上限時間（§2）を過ぎたら `cancel()` を呼ぶ | `EngineFailure` を分類する（§5.2） |
| 5 | 結果をセグメントにする（§4.3）。Overlap の区間の中で終わる結果を捨てる | — |
| 6 | 結果ファイルを原子的に書く（§4.4） | 保存の失敗は `INTERNAL` |
| 7 | 実行世代を照合して、ジョブを `succeeded` にする（§5.3） | 照合に通らなければ、何も変えずに捨てる |

- 録音ファイルは読むだけで、変更も削除もしない（INV-03）。保存の入口（`TranscriptStoring`）には、Chunk を書く操作がない。
- 手順 6 と 7 の間で落ちた場合、ジョブは再実行され、同じ名前の結果ファイルを置き換える。結果は 1 つのままである（INV-05）。
- 手順 2 の読み出しと手順 6・7 の書き込みは、会議に削除要求がないことを `MeetingStore` が確かめてから行う（[storage.md](storage.md) §10）。

### 4.2 Overlap

```swift
/// Overlap を行わない理由（FR-STT-03 ③）
public enum OverlapSkipReason: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case firstChunk = "first_chunk"
    /// 直前の連番が欠番
    case missing
    /// 直前の Chunk のファイルがない（隔離した書きかけを含む）
    case fileMissing = "file_missing"
    /// 直前の Chunk のハッシュが合わない（ヘッダが不正・ハッシュの記録がない場合を含む）
    case hashMismatch = "hash_mismatch"
    /// 両 Chunk の間に空白区間がある
    case gap
}

/// 直前の連番の Chunk の状態
public struct PreviousChunk: Equatable, Sendable {
    public let state: ChunkFileState
    public let sampleCount: Int

    public init(state: ChunkFileState, sampleCount: Int) {
        self.state = state
        self.sampleCount = sampleCount
    }
}

/// Overlap の可否（FR-STT-03 ①・②）。純粋関数
public enum OverlapDecision: Equatable, Sendable {
    /// 前の Chunk の末尾 sampleCount サンプルを先頭につなぐ
    case overlap(sampleCount: Int)
    case none(OverlapSkipReason)

    /// previous: 直前の連番の行（なければ nil）。
    /// gapAtChunkStart: 位置が当該 Chunk の開始サンプルと等しい空白区間があるか
    public static func decide(
        sequence: ChunkSequence, previous: PreviousChunk?, gapAtChunkStart: Bool,
        tuning: TranscriptionTuning = TranscriptionTuning(),
    ) -> OverlapDecision {
        guard sequence.value > 0 else { return .none(.firstChunk) }
        guard let previous else { return .none(.missing) }
        switch previous.state {
        case .missing, .quarantined: return .none(.fileMissing)
        case .hashMismatch, .invalid: return .none(.hashMismatch)
        case .present: break
        }
        guard !gapAtChunkStart else { return .none(.gap) }
        return .overlap(sampleCount: min(tuning.overlapSamples, previous.sampleCount))
    }

    public var sampleCount: Int {
        if case let .overlap(count) = self {
            count
        } else {
            0
        }
    }
}
```

| 直前の連番の Chunk | 間の空白区間 | 判定 | 記録する理由 |
| --- | --- | --- | --- |
| —（当該が連番 0） | — | なし | `first_chunk` |
| 行がない（欠番） | — | なし | `missing` |
| `missing`・`quarantined` | — | なし | `file_missing` |
| `hash_mismatch`・`invalid` | — | なし | `hash_mismatch` |
| `present` | ある | なし | `gap` |
| `present` | ない | 直前の Chunk の末尾 48,000 サンプル（それより短ければ全体）を先頭につなぐ | — |

- 「間の空白区間」は、**位置が当該 Chunk の開始サンプルと等しい空白区間**があるかどうかで判定する。割り込みの空白区間は必ず Chunk の境界にある（割り込みの前で短い Chunk を確定するため。[recording.md](recording.md) §6）。Chunk の途中にある空白区間（`overrun`・`route_change`・`media_reset`）は、Overlap の可否に関係しない。
- 直前の Chunk の状態は、DB の行の値ではなく、**手順 3 で読んだときの検証の結果**を使う（行が `present` でも、読んだときにハッシュが合わなければ `hash_mismatch` として扱い、行の状態も更新する）。
- Overlap なしの場合、当該 Chunk は先頭から通常どおり認識する。先頭の区間を空白として扱わない（FR-STT-03 ②）。
- 認識の段階では、隣の Chunk の結果を見ない（ジョブを独立・冪等に保つ）。重複は統合で解決する（§6）。

### 4.3 時刻の変換

```swift
public enum OverlapMapping {
    /// エンジンの結果を、会議全体のサンプル番号のセグメントにする。
    /// Overlap の区間の中で終わる結果は捨てる（前の Chunk のジョブが担当する）。境界をまたぐ結果は残す
    public static func segments(
        from spans: [RecognizedSpan], chunkStart: Int64, chunkSampleCount: Int, overlapSamples: Int,
    ) -> (segments: [TranscriptSegment], discardedCount: Int) {
        let origin = chunkStart - Int64(overlapSamples)
        let limit = chunkStart + Int64(chunkSampleCount)
        let ordered = spans.sorted { ($0.startSample, $0.endSample) < ($1.startSample, $1.endSample) }
        let kept = ordered.filter { $0.endSample > overlapSamples && !$0.text.isEmpty }
        let segments = kept.enumerated().map { index, span in
            // 入力の範囲の外にはみ出した時刻は、入力の範囲に収める
            let start = min(max(origin + Int64(span.startSample), origin), limit)
            let end = min(max(origin + Int64(span.endSample), start), limit)
            return TranscriptSegment(
                index: index, startSample: start, endSample: end, text: span.text, confidence: span.confidence,
            )
        }
        return (segments, ordered.count - kept.count)
    }
}
```

- つないだ入力の中の位置 p（サンプル）は、`当該 Chunk の開始サンプル − Overlap のサンプル数 + p` で会議全体のサンプル番号になる（基本設計 §5.2）。
- 結果のうち、**終わりの位置が Overlap の区間の中にあるもの**は捨てる（前の Chunk のジョブが担当する）。境界をまたぐものは残す。
- 入力の範囲の外にはみ出した時刻は、入力の範囲に収める。本文が空の結果は捨てる。
- セグメントの番号（`index`）は、残した結果を開始位置の順に並べて 0 から振る。同じ入力から同じ番号になる（FR-STT-04）。
- 会議時刻（空白区間を含む時刻）への変換は、表示と書き出しのときに `AudioTimeline` で行う（[architecture.md](architecture.md) §4）。ファイルには、サンプル番号だけを保存する（INV-08）。

### 4.4 結果ファイル

`stt/run-{実行番号 4 桁}/{連番 6 桁}.json`（型は `ChunkTranscript`。形式の版 1）。

| 項目 | 内容 |
| --- | --- |
| `formatVersion`・`runNo`・`sequenceNo` | 形式の版、実行番号、連番（読むときに、ファイル名の値と一致することを確かめる） |
| `chunkSha256`・`chunkStart`・`chunkSampleCount` | どの音声に対する結果かの証跡（認識したときの Chunk のハッシュ・開始サンプル・サンプル数） |
| `engine` | `EngineInfo`（§3.1） |
| `processingMs` | エンジンの実行にかかった時間（単調時計） |
| `overlap` | `OverlapRecord`: 行ったか・行わなかった理由・つないだサンプル数・捨てた結果の数。行った場合は、つないだ直前の Chunk の連番と SHA-256 |
| `segments` | `TranscriptSegment` の配列: Chunk の中の番号、開始・終了のサンプル番号（会議全体）、本文、信頼度（なければ `null`） |

- 書き方は `PersistedJSON.encode` と `FileSystem.replaceAtomically`（[storage.md](storage.md) §4.1）。同じジョブの再実行では、同じ名前のファイルを置き換える。
- 読むときは `validated(run:sequence:)` で検証する: 形式の版、番号の一致、範囲（セグメントが「Chunk の開始 − Overlap」から Chunk の終わりまでに収まる）、番号が 0 からの連番、本文（空でない・4,000 文字まで・制御文字を含まない）、信頼度（0〜1）、件数（2,000 件まで）。通らない結果ファイルは使わない（そのジョブを再実行する）。
- セグメントの ID は「連番-Chunk 内の番号」（`SegmentID`）で、同じ版の中で一意である。版をまたいで参照する場合は、版番号と組にする（FR-STT-04）。
- 本文（D2）は、会議ディレクトリのファイルにだけ置く。DB・ログ・エラーの値には入れない（BD-STT-03）。

**失敗の記録**（`stt/run-{実行番号 4 桁}/{連番 6 桁}.failed.json`。型は `ChunkFailureRecord`。形式の版 1。基本設計 §4.3）:

| 項目 | 内容 |
| --- | --- |
| `formatVersion`・`runNo`・`sequenceNo` | 形式の版、実行番号、連番（読むときに、ファイル名の値と一致することを確かめる） |
| `failureClass` | 失敗の分類（`TranscriptionFailureClass` の生の値。§5.2） |
| `reason` | 記録した理由（`retry_not_selected`・`retry_result_unavailable`・`attempts_exhausted` などの英小文字・数字・`_` の 64 文字まで）。なければ `null` |
| `attemptGeneration` | `failed` にした試行の実行世代（UUID の小文字の文字列。§5.3） |
| `retryID` | 失敗区間の再試行の中で `failed` にした場合の、その再試行の識別子（UUID の小文字の文字列。§7）。なければ `null` |
| `recordedAtMs` | 記録した日時（UTC の経過ミリ秒） |

- 書き方は結果ファイルと同じ（`PersistedJSON.encode` と `FileSystem.replaceAtomically`。同じ連番の記録があれば置き換える）。`failed` にする DB の取引より前に公開する。
- 読むときは `validated(run:sequence:)` で検証する: 形式の版、番号の一致、`failureClass` が既知の値、`reason` の文字の種類と長さ、UUID の形、日時が 0 以上。**通らない記録は、`failed` の記録があるものとして扱う**（結果ファイルを再利用しない側に倒す）。例外は基本設計 §4.3 の 2 つ（失敗区間の再試行で選んだ連番は `pending`、`processing` のジョブの回収では記録がないものとする）。
- 本文を含めない（D2 を持たない）。

計測値（NFR-PRF-03。`usage_metrics` に記録する。数値は保証せず、合否にも使わない）:

| 値 | 単位 | 記録する時点 |
| --- | --- | --- |
| 実時間比（処理時間 ÷ 入力の長さ）の 1,000 倍 | Chunk ごと | ジョブの成功 |
| 失敗の分類ごとの件数、再試行の回数 | 会議・実行番号ごと | 統合の完了 |
| Overlap で除外した件数、低信頼度のセグメントの割合の 1,000 倍 | 版ごと | 統合の完了 |
| バックグラウンドで打ち切られた回数 | 会議ごと | 打ち切りのたび |

---

## 5. ジョブと失敗の分類

満たす要件: FR-STT-02、FR-RCV-04、NFR-REL-03、INV-04・05、TST-GEN-05・06 ／ 決定: BD-STT-04・05・09

### 5.1 状態遷移

```swift
public enum JobKind: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case transcribeChunk = "transcribe_chunk"
    case mergeTranscript = "merge_transcript"
}

/// ジョブの状態（FR-STT-02）。skipped は P2 の VAD スキップ用で、MVP では使わない
public enum JobState: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case pending
    case processing
    case succeeded
    case failed
    case skipped

    /// 終わり（これ以上、自動では実行されない）
    public var isTerminal: Bool {
        self == .succeeded || self == .failed || self == .skipped
    }
}

public enum JobEvent: Equatable, Sendable {
    /// 実行開始
    case started
    /// 結果を保存した
    case succeeded
    /// 実行が失敗・中止で終わった
    case attemptFailed(TranscriptionFailureClass)
    /// 起動時の回収（processing のまま残っていた）
    case reclaimed
    /// 利用者の再試行（失敗区間・統合の失敗）
    case userRetry
    /// 失敗区間の再試行に伴って、統合をやり直す（merge_transcript だけ）
    case mergeReset
}

public struct JobSnapshot: Equatable, Sendable {
    public var kind: JobKind
    public var state: JobState
    public var attemptCount: Int

    public init(kind: JobKind, state: JobState, attemptCount: Int) {
        self.kind = kind
        self.state = state
        self.attemptCount = attemptCount
    }
}

/// 遷移の結果。保存の actor が、この値のとおりに 1 取引で書く
public struct JobTransition: Equatable, Sendable {
    public var next: JobState
    public var attemptCount: Int
    public var failureClass: TranscriptionFailureClass?
    /// 真なら、新しい実行世代を発行して記録する
    public var issuesGeneration = false
    /// 待ってから再実行する場合の、間隔を決める試行回数（1 から）。待たないなら nil
    public var retryAfterAttempt: Int?
}

/// ジョブの状態遷移（純粋関数。TST-GEN-06）。許されない組み合わせは nil
public enum JobStateMachine {
    public static func reduce(
        _ job: JobSnapshot, _ event: JobEvent, tuning: TranscriptionTuning = TranscriptionTuning(),
    ) -> JobTransition? {
        switch (job.state, event) {
        case (.pending, .started):
            // 試行回数は実行開始で増やす（実行中に落ちても、起動時の回収で数えられる）
            return JobTransition(next: .processing, attemptCount: job.attemptCount + 1, issuesGeneration: true)
        case (.processing, .succeeded):
            return JobTransition(next: .succeeded, attemptCount: job.attemptCount)
        case let (.processing, .attemptFailed(failure)):
            return afterFailure(job, failure, tuning)
        case (.processing, .reclaimed):
            guard job.attemptCount < tuning.maxAttempts else {
                return JobTransition(next: .failed, attemptCount: job.attemptCount, failureClass: .internal)
            }
            return JobTransition(next: .pending, attemptCount: job.attemptCount)
        case (.failed, .userRetry):
            return JobTransition(next: .pending, attemptCount: 0)
        case (.succeeded, .mergeReset), (.failed, .mergeReset), (.pending, .mergeReset):
            guard job.kind == .mergeTranscript else { return nil }
            return JobTransition(next: .pending, attemptCount: 0)
        default:
            return nil
        }
    }

    private static func afterFailure(
        _ job: JobSnapshot, _ failure: TranscriptionFailureClass, _ tuning: TranscriptionTuning,
    ) -> JobTransition {
        switch failure {
        case .assetMissing, .engineUnavailable, .systemExpired:
            // 回数に数えない。実行開始で増やした 1 を戻し、開始条件が戻るのを待つ
            return JobTransition(next: .pending, attemptCount: max(0, job.attemptCount - 1), failureClass: failure)
        case .invalidAudio:
            return JobTransition(next: .failed, attemptCount: job.attemptCount, failureClass: failure)
        case .insufficientResources, .timeout, .internal:
            guard job.attemptCount < tuning.maxAttempts else {
                return JobTransition(next: .failed, attemptCount: job.attemptCount, failureClass: failure)
            }
            return JobTransition(
                next: .pending, attemptCount: job.attemptCount, failureClass: failure,
                retryAfterAttempt: job.attemptCount,
            )
        }
    }
}
```

| 現在の状態 | 事象 | 次の状態 | 試行回数 | 補足 |
| --- | --- | --- | --- | --- |
| `pending` | `started` | `processing` | +1 | 新しい実行世代を発行する |
| `processing` | `succeeded` | `succeeded` | そのまま | 結果ファイル（または版）を保存した後 |
| `processing` | `attemptFailed`（回数に数えない分類） | `pending` | −1（実行開始で増やした分を戻す） | 開始条件が戻るのを待つ |
| `processing` | `attemptFailed`（回数に数える分類・回数が残る） | `pending` | そのまま | 間隔を空けて再実行する |
| `processing` | `attemptFailed`（回数に数える分類・回数切れ、または `INVALID_AUDIO`） | `failed` | そのまま | 失敗区間になる |
| `processing` | `reclaimed`（起動時の回収） | `pending`（回数が残る）／`failed`（回数切れ。分類 `INTERNAL`） | そのまま | §5.4 |
| `failed` | `userRetry` | `pending` | 0 に戻す | 失敗区間の再試行・統合の再試行 |
| `succeeded`・`failed`・`pending`（`merge_transcript` だけ） | `mergeReset` | `pending` | 0 に戻す | 失敗区間の再試行に伴う統合のやり直し（§7） |

この表にない組み合わせは、すべて拒否する（`reduce` が `nil` を返す）。`skipped` は P2 の VAD スキップ用で、MVP では、この状態へ入る遷移を持たない。

ジョブの種類は `transcribe_chunk`（Chunk ごと）と `merge_transcript`（実行番号ごとに 1 件）の 2 つである。一意キーは（会議 ID、実行番号、種類、連番）で、`merge_transcript` の連番は −1 とする（[storage.md](storage.md) §3.3）。

### 5.2 失敗の分類と再試行

| 分類（`TranscriptionFailureClass`） | もとになる `EngineFailure`・事象 | 再試行 | 試行回数 |
| --- | --- | --- | --- |
| `ASSET_MISSING` | `assetMissing` | アセットの導入を待つ | 数えない |
| `ENGINE_UNAVAILABLE` | `unavailable` | 開始条件が戻るのを待つ | 数えない |
| `SYSTEM_EXPIRED` | `cancelled`（録音の開始・バックグラウンドの打ち切り・延期・削除・利用者の中止） | 次の機会に再開する | 数えない |
| `INSUFFICIENT_RESOURCES` | `insufficientResources` | 間隔を空けて再実行する | 数える |
| `TIMEOUT` | `timedOut`、上限時間による中止 | 間隔を空けて再実行する | 数える |
| `INVALID_AUDIO` | Chunk の検証の失敗（§4.1 の手順 2）、`invalidAudio` | **しない** | — |
| `INTERNAL` | `other`、結果ファイルの保存の失敗、起動時の回収での回数切れ | 間隔を空けて再実行する | 数える |

```swift
public enum RetryPolicy {
    /// 再実行までの間隔（ミリ秒）。unit は 0 以上 1 未満の乱数（注入する）
    public static func delayMs(
        afterAttempt attempt: Int, unit: Double, tuning: TranscriptionTuning = TranscriptionTuning(),
    ) -> Int64 {
        guard let last = tuning.retryDelaysSeconds.last else { return 0 }
        let index = max(0, attempt - 1)
        let base = index < tuning.retryDelaysSeconds.count ? tuning.retryDelaysSeconds[index] : last
        let factor = 1 + tuning.retryJitter * (2 * min(max(unit, 0), 1) - 1)
        return Int64((Double(base) * 1000 * factor).rounded())
    }

    /// 1 件のジョブの上限時間（ミリ秒）。音声の長さの timeoutFactor 倍を、ミリ秒に切り上げて求める。
    /// 空でない入力は minimumTimeoutMs を下回らない。空の入力は、これまでどおり 0
    public static func timeoutMs(inputSamples: Int, tuning: TranscriptionTuning = TranscriptionTuning()) -> Int64 {
        guard inputSamples > 0 else { return 0 }
        let rate = Int64(AudioConstants.sampleRate)
        // 倍数を掛けてから割る（先に割ると、1 ミリ秒に満たない入力が 0 になる）。端数は切り上げる
        let scaled = (Int64(inputSamples) * 1000 * Int64(tuning.timeoutFactor) + rate - 1) / rate
        return max(scaled, tuning.minimumTimeoutMs)
    }
}

/// 次に実行するジョブの選び方（BD-STT-09。同時に実行するのは 1 件）
public enum JobSelector {
    public struct Candidate: Equatable, Sendable {
        public var kind: JobKind
        /// transcribe_chunk は Chunk の連番、merge_transcript は −1
        public var sequence: Int
        public var state: JobState

        public init(kind: JobKind, sequence: Int, state: JobState) {
            self.kind = kind
            self.sequence = sequence
            self.state = state
        }
    }

    public enum Choice: Equatable, Sendable {
        /// この連番の transcribe_chunk を実行する
        case transcribe(sequence: Int)
        /// 全 Chunk のジョブが終わった。統合する
        case merge
        /// 実行中のジョブがある・再実行の待ち時間中など。いまは何もしない
        case wait
        /// この実行番号には、実行するものが残っていない
        case finished
    }

    /// jobs: 1 つの会議の、現在の実行番号のジョブ
    public static func next(among jobs: [Candidate]) -> Choice {
        guard !jobs.contains(where: { $0.state == .processing }) else { return .wait }
        let chunks = jobs.filter { $0.kind == .transcribeChunk }
        if let pending = chunks.filter({ $0.state == .pending }).min(by: { $0.sequence < $1.sequence }) {
            return .transcribe(sequence: pending.sequence)
        }
        guard let merge = jobs.first(where: { $0.kind == .mergeTranscript }) else {
            // 全 Chunk のジョブが終わっていて、統合のジョブがまだない
            return chunks.isEmpty ? .finished : .merge
        }
        return merge.state == .pending ? .merge : .finished
    }
}
```

- 再実行までの待ち時間は、`RetryPolicy.delayMs` で求め、注入した `Scheduler` で待つ（[architecture.md](architecture.md) §6.2）。乱数は `RandomSource` から取る。**待ち時間は DB に保存しない。** 再起動の後は、待たずに実行できる（[storage.md](storage.md) §3.3）。
- 上限時間は、手順が `Scheduler` で待ち、過ぎたら `engine.cancel()` を呼んで、分類を `TIMEOUT` にする（エンジンが返す `cancelled` を、`TIMEOUT` に読み替える）。
- **上限時間の下限:** 上限時間は入力の長さの `timeoutFactor` 倍を、倍数を掛けてから割ってミリ秒に切り上げて求め（短い入力を切り捨てで 0 にしない）、空でない入力では `minimumTimeoutMs`（初期値 30,000 ms = 30 秒）を下回らないようにする。末尾の短い Chunk（数サンプル〜数秒）でも、エンジンの起動から結果までの固定の時間を待たずに `TIMEOUT` にしない。空の入力（0 サンプル）は 0 のまま（認識に渡す入力がない）。下限の値はエンジンの実測で見直す（§14）。
- 上限時間による中止と、録音の開始などによる中止が重なった場合は、**回数に数えない方（`SYSTEM_EXPIRED`）を優先する**（利用者の操作で止めた実行を、失敗の回数に数えない）。
- **止まっていない実行（停止の確かめ方と待機の上限。基本設計 §4.2）:** `TIMEOUT` と中止（`SYSTEM_EXPIRED`）で実行を打ち切ったときは、`engine.cancel()` が戻ったことを停止の確認とする（§3.1・§3.3。戻った時点で、認識の操作全体が終わり、解析は止まっていて、これから始まることもない）。手順は `engine.cancel()` の戻りを注入した `Scheduler` で上限（`stopConfirmTimeoutMs`。5 秒）まで待つ。
  - 上限までに戻れば、ジョブの完了の処理（`pending` への戻し・再試行の予定）を行い、次の試行を許す。
  - 上限までに戻らなければ、**同じジョブの次の試行も別のジョブの試行も始めない**（`processing` に入れない。`JobSelector` の結果によらず、手順の側が止める）。会議の待っている理由を `engine_not_stopped` にして表示し、ログ `engine_stop_unconfirmed` を出す。`engine.cancel()` の戻りは上限の後も待ち続け（問い合わせを繰り返す間隔は持たない。戻りを 1 回待つだけである）、戻った時点でログ `engine_stop_confirmed`（打ち切りからの時間）を出し、開始条件を評価し直して再開する。
  - 照合（§5.3）と `processing` の件数は、停止の確認の代わりにしない。
  - プロセスが終了すれば実行は止まるので、次の起動では起動時の回収（§5.4）から再開する。
- `merge_transcript` が `failed` になった場合は、会議を `transcribing` のままにして、問題 `merge_failed` を表示する（基本設計 §4.2。[ui.md](ui.md) §6）。以前の版があれば表示し続ける。

### 5.3 実行世代による照合と、次のジョブの選び方

```swift
/// processing_jobs の操作。状態の変更は、JobStateMachine が返した遷移だけを書く
enum JobTable {
    /// ジョブの行を作る。一意キーにより、何度呼んでも 1 行である
    static func ensure(
        _ key: JobKey, state: JobState, at now: Timestamp, in connection: SQLiteConnection,
    ) throws(StorageFailure) {
        try connection.execute(
            """
            INSERT OR IGNORE INTO processing_jobs (meeting_id, run_no, kind, sequence_no, state, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            """,
            [.text(key.meeting.description), .integer(Int64(key.run)), .text(key.kind.rawValue)]
                + [.integer(Int64(key.sequence))]
                + [.text(state.rawValue), .integer(now.milliseconds)],
            stage: .register,
        )
    }

    /// 実行開始。pending の行だけを processing にして、実行世代を記録する。取れなければ偽（別の実行が先に取った）
    static func claim(
        jobID: Int64, transition: JobTransition, generation: UUID, at now: Timestamp, in connection: SQLiteConnection,
    ) throws(StorageFailure) -> Bool {
        try connection.execute(
            """
            UPDATE processing_jobs
            SET state = ?2, attempt_count = ?3, execution_generation = ?4, failure_class = NULL, updated_at = ?5
            WHERE job_id = ?1 AND state = 'pending'
            """,
            [.integer(jobID), .text(transition.next.rawValue), .integer(Int64(transition.attemptCount))]
                + [.text(generation.uuidString.lowercased()), .integer(now.milliseconds)],
            stage: .register,
        ) == 1
    }

    /// 完了の通知（成功・失敗・中止・時間切れ）を反映する。
    /// **行が今も processing で、実行世代が通知のものと一致するときだけ書く。** 合わなければ何も変えずに偽を返す
    static func complete(
        jobID: Int64, generation: UUID, transition: JobTransition, at now: Timestamp, in connection: SQLiteConnection,
    ) throws(StorageFailure) -> Bool {
        let failure: SQLValue = transition.failureClass.map { .text($0.rawValue) } ?? .null
        return try connection.execute(
            """
            UPDATE processing_jobs
            SET state = ?3, attempt_count = ?4, failure_class = ?5, execution_generation = NULL, updated_at = ?6
            WHERE job_id = ?1 AND state = 'processing' AND execution_generation = ?2
            """,
            [.integer(jobID), .text(generation.uuidString.lowercased()), .text(transition.next.rawValue)]
                + [.integer(Int64(transition.attemptCount)), failure, .integer(now.milliseconds)],
            stage: .register,
        ) == 1
    }
}
```

| 規則 | 内容 |
| --- | --- |
| 実行世代 | `processing` に入るたびに、`IDGenerator` で作った UUID を行に記録する。再試行・回収・利用者の再試行のいずれでも再利用しない |
| 実行開始（`claim`） | `pending` の行だけを書き換える（`WHERE state = 'pending'`）。書き換えた行数が 1 でなければ、実行しない |
| 完了の通知（`complete`） | 成功・失敗・中止・時間切れのすべてで、**行が今も `processing` で、実行世代が通知のものと一致するときだけ**書き換える。一致しない通知は、結果ファイルを書かず、状態・試行回数にも反映せずに捨てて、ログ `stale_completion_dropped` を出す |
| 照合と保存の順序 | 成功では、①実行世代が一致することを確かめる → ②結果ファイルを書く → ③`complete` で行を書き換える、を `MeetingStore`（actor）の 1 つの関数の中で続けて行う（間に `await` を置かない）。①と③の間に、別の実行開始・回収・利用者の再試行が入らない |
| 試行回数を照合に使わない | 回数に数えない失敗の後や、利用者の再試行で回数を戻した後に、古い実行と同じ値になりうるため（同じ回数でも古い通知が捨てられることを確かめた。§11.3） |

次に実行するジョブは `JobSelector.next` で選ぶ（同時に実行するのは 1 件。BD-STT-09）。

```swift
/// 次に実行するジョブの選び方（BD-STT-09。同時に実行するのは 1 件）
public enum JobSelector {
    public struct Candidate: Equatable, Sendable {
        public var kind: JobKind
        /// transcribe_chunk は Chunk の連番、merge_transcript は −1
        public var sequence: Int
        public var state: JobState

        public init(kind: JobKind, sequence: Int, state: JobState) {
            self.kind = kind
            self.sequence = sequence
            self.state = state
        }
    }

    public enum Choice: Equatable, Sendable {
        /// この連番の transcribe_chunk を実行する
        case transcribe(sequence: Int)
        /// 全 Chunk のジョブが終わった。統合する
        case merge
        /// 実行中のジョブがある・再実行の待ち時間中など。いまは何もしない
        case wait
        /// この実行番号には、実行するものが残っていない
        case finished
    }

    /// jobs: 1 つの会議の、現在の実行番号のジョブ
    public static func next(among jobs: [Candidate]) -> Choice {
        guard !jobs.contains(where: { $0.state == .processing }) else { return .wait }
        let chunks = jobs.filter { $0.kind == .transcribeChunk }
        if let pending = chunks.filter({ $0.state == .pending }).min(by: { $0.sequence < $1.sequence }) {
            return .transcribe(sequence: pending.sequence)
        }
        guard let merge = jobs.first(where: { $0.kind == .mergeTranscript }) else {
            // 全 Chunk のジョブが終わっていて、統合のジョブがまだない
            return chunks.isEmpty ? .finished : .merge
        }
        return merge.state == .pending ? .merge : .finished
    }
}
```

| 規則 | 内容 |
| --- | --- |
| 対象の会議 | `transcribing` の会議を、開始日時の古い順に 1 件ずつ進める（1 つの会議の実行番号が終わるまで、次の会議へ移らない） |
| 対象のジョブ | その会議の現在の実行番号（`meetings.current_run`）のジョブだけ |
| 順序 | `pending` の `transcribe_chunk` を、連番の小さい順に。全 `transcribe_chunk` が終わり（`succeeded`・`failed`・`skipped`）になったら `merge_transcript`（行がなければ、その時点で作る） |
| 待つ場合 | `processing` のジョブがある・再実行の待ち時間中・開始条件を満たさない（§8.2） |
| 統合の後 | 新しい版を保存して `transcribed` にする（§6.3）。`merge_transcript` が `failed` なら、`transcribing` のまま待つ |

### 5.4 実行番号・ジョブの作成・起動時の回収

| 処理 | 規則 |
| --- | --- |
| 実行番号の発番 | 「DB の最大の実行番号」と「`stt/` にある `run-*` の最大の番号」の大きい方 + 1（基本設計 §4.3）。発番したら、ジョブを作る前に `stt/run-{実行番号}/` を作る（[storage.md](storage.md) §2.2）。空のディレクトリが残っても消さない |
| ジョブの作成 | 連番 0 から最大の連番まで、ファイルの状態が `present` の Chunk に `transcribe_chunk` を `pending` で作る。利用できない Chunk（欠番・`missing`・`hash_mismatch`・`invalid`・`quarantined`）には作らず、版のファイルの「利用できなかった Chunk」に理由を書く（§6.2） |
| 起動時の回収 | `processing` のまま残った行に、事象 `reclaimed` を適用する（回数が残れば `pending`、尽きていれば `failed`）。実行世代を消す |
| 不足分の作成 | 起動のたびに、`transcribing` の会議について行う（基本設計 §4.3。[storage.md](storage.md) §9 の手順 7）。使う実行番号は、①`meeting.json` の失敗区間の再試行の実行番号（あれば）、②その会議のジョブ行の最大の実行番号、③`stt/` の最大の番号、④なければ 1、の順で決める。新しく発番しない |
| 結果ファイルの再利用 | 不足分を作るとき、その実行番号の結果ファイルがあり、下の 3 つをすべて満たす Chunk は `succeeded`、そうでなければ `pending` で作る。既存のジョブ行は変更しない |

結果ファイルを再利用できる条件:

| 条件 | 満たさないとき（`reuse_rejection` に記録する値） |
| --- | --- |
| 結果ファイルが検証（§4.4）に通る | `invalid_result` |
| 結果ファイルの `chunkSha256` が、現在のハッシュの記録と一致する | `chunk_changed` |
| 結果ファイルの `overlap` が、現在の状態で判定し直した Overlap と一致する（`OverlapRecord.matches`: 行ったか・理由・つないだサンプル数・直前の Chunk の連番と SHA-256） | `overlap_mismatch` |

`ensure` は一意キーに対する `INSERT OR IGNORE` なので、不足分の作成を何度実行しても行は重複しない。途中で落ちて行が一部だけ作られた状態からも、次の起動で残りが作られる。

---

## 6. 統合と版のファイル

満たす要件: FR-STT-03・05、FR-TRV-02・03、SEC-INT-03 ／ 決定: BD-STT-05・06

### 6.1 統合の規則

入力は、その実行番号のうち**最終状態が `succeeded` のジョブに対応する結果ファイル**だけである（`failed` のジョブの結果ファイルは、残っていても渡さない）。

```swift
/// 統合（BD-STT-06）。純粋関数で、同じ入力から必ず同じ結果を返す（TST-STT-04）
public enum TranscriptMerger {
    /// transcripts: 最終状態が succeeded のジョブの結果ファイルだけを渡す（順序は問わない）
    public static func merge(
        _ transcripts: [ChunkTranscript], tuning: TranscriptionTuning = TranscriptionTuning(),
    ) -> MergeResult {
        let ordered = transcripts.sorted { $0.sequenceNo < $1.sequenceNo }
        var excludedBy: [SegmentID: TranscriptVersionDocument.Excluded] = [:]
        var boundaries: [TranscriptVersionDocument.Boundary] = []
        for (offset, current) in ordered.enumerated() {
            var count = 0
            // 直前の連番の結果があり、Overlap を行った境界だけを比べる
            let isAdjacent = offset > 0 && ordered[max(0, offset - 1)].sequenceNo.value + 1 == current.sequenceNo.value
            if current.overlap.applied, isAdjacent {
                let found = duplicates(before: ordered[offset - 1], after: current, already: excludedBy, tuning)
                count = found.count
                excludedBy.merge(found) { first, _ in first }
            }
            if current.sequenceNo.value > 0 {
                boundaries.append(TranscriptVersionDocument.Boundary(
                    sequenceNo: current.sequenceNo, overlapApplied: current.overlap.applied,
                    reason: current.overlap.reason, excludedCount: count,
                ))
            }
        }
        let all = ordered.flatMap { transcript in transcript.segments.map { segment(transcript.sequenceNo, $0) } }
        let adopted = all.filter { excludedBy[$0.id] == nil }
            .sorted { ($0.startSample, $0.id) < ($1.startSample, $1.id) }
        let excluded = excludedBy.values.sorted { $0.segment.id < $1.segment.id }
        return MergeResult(segments: adopted, excluded: excluded, boundaries: boundaries)
    }

    /// 境界の前後 boundaryWindowSamples に入るセグメントの組だけを比べる（総当たりにしない）
    private static func duplicates(
        before: ChunkTranscript, after: ChunkTranscript,
        already: [SegmentID: TranscriptVersionDocument.Excluded], _ tuning: TranscriptionTuning,
    ) -> [SegmentID: TranscriptVersionDocument.Excluded] {
        let boundary = after.chunkStart
        let earlier = before.segments.filter { $0.endSample > boundary - tuning.boundaryWindowSamples }
            .map { segment(before.sequenceNo, $0) }
        let later = after.segments.filter { $0.startSample < boundary + tuning.boundaryWindowSamples }
            .map { segment(after.sequenceNo, $0) }
        var found: [SegmentID: TranscriptVersionDocument.Excluded] = [:]
        for first in earlier where already[first.id] == nil {
            for second in later where found[second.id] == nil && found[first.id] == nil {
                guard let verdict = compare(first, second, tuning) else { continue }
                let loser = verdict.keepsFirst ? second : first
                found[loser.id] = TranscriptVersionDocument.Excluded(
                    segment: loser, duplicateOf: verdict.keepsFirst ? first.id : second.id, rule: verdict.rule,
                    timeOverlapPermille: permille(verdict.timeOverlap),
                    similarityPermille: permille(verdict.similarity),
                )
            }
        }
        return found
    }

    struct Verdict: Equatable {
        var rule: ExclusionRule
        var keepsFirst: Bool
        var timeOverlap: Double
        var similarity: Double
    }

    /// first は前の Chunk 側、second は後ろの Chunk 側。重複でなければ nil
    static func compare(
        _ first: TranscriptVersionDocument.Segment, _ second: TranscriptVersionDocument.Segment,
        _ tuning: TranscriptionTuning,
    ) -> Verdict? {
        let overlap = timeOverlap(first, second)
        guard overlap >= tuning.timeOverlapThreshold else { return nil }
        let left = normalize(first.text)
        let right = normalize(second.text)
        let score = similarity(left, right)
        if isContained(left, right, tuning) {
            // 包含なら長い方を残す（同じ長さにはならない。比の上限が 1 未満のため）
            let keepsFirst = left.count > right.count
            return Verdict(rule: .contained, keepsFirst: keepsFirst, timeOverlap: overlap, similarity: score)
        }
        guard score >= tuning.similarityThreshold else { return nil }
        // 信頼度の高い方を残す。同点・どちらかが取得できない場合は前の Chunk 側を残す
        // （取得できない値を 0 と見なして比べない。前の側だけが取得できないと、後ろの側を残してしまう）
        guard let firstConfidence = first.confidence, let secondConfidence = second.confidence else {
            return Verdict(rule: .similar, keepsFirst: true, timeOverlap: overlap, similarity: score)
        }
        let keepsFirst = firstConfidence >= secondConfidence
        return Verdict(rule: .similar, keepsFirst: keepsFirst, timeOverlap: overlap, similarity: score)
    }

    /// 重なる長さ ÷ 短い方の長さ。どちらかの長さが 0 なら 0
    static func timeOverlap(
        _ first: TranscriptVersionDocument.Segment, _ second: TranscriptVersionDocument.Segment,
    ) -> Double {
        let shorter = min(first.endSample - first.startSample, second.endSample - second.startSample)
        guard shorter > 0 else { return 0 }
        let shared = min(first.endSample, second.endSample) - max(first.startSample, second.startSample)
        return Double(max(0, shared)) / Double(shorter)
    }

    /// 比較用の正規化（表示は元の本文）: NFKC、句読点・記号・空白の除去、英字の小文字化
    static func normalize(_ text: String) -> [Character] {
        text.precomposedStringWithCompatibilityMapping.lowercased()
            .filter { !$0.isPunctuation && !$0.isSymbol && !$0.isWhitespace }
    }

    /// 1 − 編集距離 ÷ 長い方の長さ。どちらかが空なら 0
    static func similarity(_ left: [Character], _ right: [Character]) -> Double {
        guard !left.isEmpty, !right.isEmpty else { return 0 }
        return 1 - Double(editDistance(left, right)) / Double(max(left.count, right.count))
    }

    /// 一方が他方の部分列（連続）で、長さの比（短い方 ÷ 長い方）が範囲の中
    static func isContained(_ left: [Character], _ right: [Character], _ tuning: TranscriptionTuning) -> Bool {
        let (shorter, longer) = left.count <= right.count ? (left, right) : (right, left)
        guard !shorter.isEmpty, tuning.containmentRatio.contains(Double(shorter.count) / Double(longer.count)) else {
            return false
        }
        return (0 ... longer.count - shorter.count).contains { Array(longer[$0 ..< $0 + shorter.count]) == shorter }
    }

    static func editDistance(_ left: [Character], _ right: [Character]) -> Int {
        var previous = Array(0 ... right.count)
        for (row, leftCharacter) in left.enumerated() {
            var current = [row + 1] + [Int](repeating: 0, count: right.count)
            for (column, rightCharacter) in right.enumerated() {
                let substitution = previous[column] + (leftCharacter == rightCharacter ? 0 : 1)
                current[column + 1] = min(substitution, previous[column + 1] + 1, current[column] + 1)
            }
            previous = current
        }
        return previous[right.count]
    }

    private static func segment(
        _ sequence: ChunkSequence, _ source: TranscriptSegment,
    ) -> TranscriptVersionDocument.Segment {
        TranscriptVersionDocument.Segment(
            id: SegmentID(sequenceNo: sequence, index: source.index), startSample: source.startSample,
            endSample: source.endSample, text: source.text, confidence: source.confidence,
        )
    }

    private static func permille(_ value: Double) -> Int {
        Int((min(max(value, 0), 1) * 1000).rounded())
    }
}
```

基本設計 §6.1 の規則を、次のとおり確定する（申し送りの「擬似コード」に当たる。上のコードが正である）。

| 順 | 規則 |
| --- | --- |
| 1 | 結果ファイルを連番の順に並べる。連番が隣り合い、後ろ側の結果ファイルが Overlap を行った境界だけを比べる |
| 2 | 境界（後ろ側の Chunk の開始サンプル）の手前 3.5 秒より後に終わる前側のセグメントと、境界の後 3.5 秒より前に始まる後ろ側のセグメントを、候補にする |
| 3 | 前側の候補を番号の順に、後ろ側の候補を番号の順に組にして比べる。すでに除外したセグメントは比べない（1 つのセグメントを 2 回除外しない） |
| 4 | **時間の重なり**（重なる長さ ÷ 短い方の長さ。どちらかの長さが 0 なら 0）が 0.5 未満なら、重複ではない |
| 5 | 本文を正規化する: NFKC、小文字化、句読点・記号・空白の除去（比較にだけ使う。表示は元の本文）。長音・促音のゆれは同一視しない |
| 6 | **包含**: 一方が他方の連続した一部で、長さの比（短い方 ÷ 長い方）が 0.3〜0.95 なら、重複とみなして**長い方を残す**（根拠 `contained`） |
| 7 | 包含でなければ、**類似**（1 − 編集距離 ÷ 長い方の長さ。どちらかが空なら 0）が 0.8 以上なら重複とみなして、**信頼度の高い方を残す**。同点の場合と、どちらか一方でも信頼度を取得できない場合は前の Chunk 側を残す（取得できない値を 0 として比べない。数値で比べるのは両方が取得できたときだけ。根拠 `similar`） |
| 8 | しきい値に届かない組は、別の発話として両方残す（取りこぼしより、二重に残す方を選ぶ） |
| 9 | 採用したセグメントを、開始サンプル・連番・番号の順に並べる |
| 10 | 境界ごとに記録を作る（後ろ側の Chunk の連番、Overlap を行ったか、行わなかった理由、除外した件数）。Overlap を行わなかった境界は、理由を記録して除外 0 件にする（FR-STT-03 ③） |

統合は時計・乱数・I/O を使わない。同じ結果ファイルの集まりからは、渡す順序によらず、同じ結果になる（TST-STT-04）。

### 6.2 版のファイル

`transcript/v{版 4 桁}.json`（型は `TranscriptVersionDocument`。形式の版 1）。これだけで表示・書き出しができる。

| 項目 | 内容 | 用途 |
| --- | --- | --- |
| `version`・`runNo`・`createdAt`・`engine` | 版、実行番号、作成日時、エンジンの情報 | FR-STT-09 |
| `segments` | 採用したセグメント（ID・開始と終了のサンプル番号・本文・信頼度）を時刻順に | 表示・再生位置・書き出し |
| `excluded` | 除外したセグメントと、どのセグメントの重複とみなしたか・根拠・判定に使った値（1,000 倍の整数） | 判定の確認（FR-STT-03） |
| `boundaries` | 境界ごとの記録 | 同上 |
| `failedChunks` | 文字起こしに失敗した Chunk（連番と範囲） | 失敗区間の表示と再試行（FR-TRV-02） |
| `unavailableChunks` | 利用できなかった Chunk（連番、範囲、ファイルの状態。欠番は範囲と状態が `null`） | 空白の表示（FR-TRV-03） |
| `gaps` | 空白区間の写し（位置・長さ・理由） | 空白の表示（FR-TRV-03） |

- 版のファイルは、統合の結果（`MergeResult`）に、ジョブの最終状態から分かる「失敗した Chunk」と、Chunk の一覧・`meeting.json` から分かる「利用できなかった Chunk」「空白区間」を足して作る（`TranscriptVersionDocument` の初期化）。
- 読むときは `validated(version:)` で検証する（形式の版、番号の一致、セグメントの範囲と本文、ID の重複、採用したセグメントが時刻順であること）。
- `transcript_versions` の行には、版・実行番号・件数（採用・除外・失敗した Chunk）・ハッシュ・完全性・エンジンの種類だけを置く（本文は置かない）。

### 6.3 版の保存と完全性

`merge_transcript` のジョブが、次の順で保存する（基本設計 §6.2）。

| 順 | 処理 | ここで落ちた場合 |
| --- | --- | --- |
| 1 | 版番号を決める: 「`transcript_versions` の最大の版」と「`transcript/` にある最大の版（版のファイル・ハッシュの記録・それらの一時ファイル）」の大きい方 + 1 | 何も残らない |
| 2 | 版のファイルを一時の名前（`v{版}.json.tmp`）に書いて、媒体まで同期する。全体の SHA-256 を計算する | 一時ファイルが残る → 次の統合で消して作り直す |
| 3 | ハッシュの記録（`v{版}.sha256.json`。型は `TranscriptHashRecord`: 版・実行番号・SHA-256）を、一時ファイル → 置換しない rename で公開する。**これが版番号の予約になる** | 記録だけが残る → その番号は欠番として飛ばす（記録は消さない） |
| 4 | 版のファイルを、置換しない rename で `v{版}.json` として公開する | 同上（記録と一時ファイルが残る） |
| 5 | DB の 1 取引: `transcript_versions` の行（完全性 `valid`）、`merge_transcript` を `succeeded`（実行世代を照合）、`meetings.current_version`、会議を `transcribed` | 版のファイルと記録がある → 起動時に `merge_transcript` が回収されて再実行され、新しい番号の版ができる（公開済みの版は残り、索引は照合で戻る。[storage.md](storage.md) §9） |
| 6 | `meeting.json` を書き換える（状態、現在の版、失敗区間の再試行の実行番号を消す）。保護クラスを見直す | 起動時の照合が、DB の状態を `meeting.json` に書き直す |

完全性（SEC-INT-03）:

| 場面 | 規則 |
| --- | --- |
| 読むたび（表示・書き出し・失敗区間の再試行） | 版のファイルの全体から SHA-256 を計算し直し、`transcript_versions` の値と照合する。合わなければ、完全性を `corrupt` にして、その版を使わない |
| DB を作り直したとき | `transcript_versions` の行を、ハッシュの記録（`v{版}.sha256.json`）から復元する。記録の値と、版のファイルから計算し直した値を照合し、合わなければ `corrupt` にする。**計算し直した値を記録として採用しない** |
| 記録がない・読めない・版の番号が合わない | 完全性を `invalid` にして、その版を使わない。記録を作り直さない |
| DB の値とハッシュの記録が食い違う | 記録を書き換えず、`corrupt` にする |
| 使えない版 | 版のファイルと記録は、上書きも削除もしない。会議詳細に、その版が読めないことと再文字起こしの操作を示す。破損していない以前の版があれば、それを表示する（[ui.md](ui.md) §4.4）。ログは `transcript_version_rejected` |

---

## 7. 版管理と再文字起こし

満たす要件: FR-STT-05、FR-TRV-02、FR-FIN-03 ／ 決定: BD-STT-04

| 操作 | 実行番号 | 版 | 状態の遷移（事象 `transcriptionStarted` → `transcriptionCompleted`） |
| --- | --- | --- | --- |
| 最初の文字起こし | 1 | 完了時に 1 | `finalized` → `transcribing` → `transcribed` |
| 失敗区間の再試行 | 表示中の版の実行番号のまま | 完了時に +1 | `transcribed` → `transcribing` → `transcribed` |
| 再文字起こし（全体） | 新しく発番（§5.4） | 完了時に +1 | 同上 |

実行の開始（最初の文字起こし・再文字起こし）:

| 順 | 処理 |
| --- | --- |
| 1 | 開始条件を評価する（§8）。満たさなければ、理由を `meetings.waiting_reason` に記録して待つ |
| 2 | 保管中（`sealed`）の会議なら、処理中の保護クラスへ戻す（[storage.md](storage.md) §8） |
| 3 | 全 Chunk を検査して、ファイルの状態を最新にする（[storage.md](storage.md) §6.1） |
| 4 | 実行番号を発番し、`stt/run-{実行番号}/` を作る |
| 5 | 再文字起こしでは、`meeting.json` から失敗区間の再試行の実行番号を消す（DB の取引より前。基本設計 §7） |
| 6 | DB の 1 取引: ジョブを作る（§5.4）、`meetings.current_run`、会議を `transcribing`（事象 `transcriptionStarted`） |
| 7 | `meeting.json` に状態と現在の実行番号を書く |
| 8 | 継続処理タスクを投入し（§9）、ジョブの実行を始める |

失敗区間の再試行の開始（基本設計 §7 の「再試行の開始時の不足行の作成」）:

| 順 | 処理 |
| --- | --- |
| 1 | 開始条件を評価する。表示中の版のファイルを読み、完全性の照合（§6.3）に通ることを確かめる。通らなければ再試行せず、再文字起こし（全体）を案内する |
| 2 | 版のファイルの実行番号を、この再試行の実行番号とする |
| 3 | その実行番号を、失敗区間の再試行の実行番号として `meeting.json` に書く（**DB の取引より前**） |
| 4 | DB の 1 取引: ①版のファイルの `failedChunks` の連番ごとに、`transcribe_chunk` がなければ `pending` で作り、`failed` なら事象 `userRetry` で `pending` に戻す。②失敗区間でない利用できる Chunk に行がなければ、§5.4 の再利用の条件で `succeeded` または `pending` として作る。③`merge_transcript` がなければ `pending` で作り、あれば事象 `mergeReset` で `pending` に戻す。④`meetings.current_run` をこの実行番号にし、会議を `transcribing` にする |
| 5 | `meeting.json` に状態を書き、ジョブの実行を始める |

- 新しい版ができるまで、以前の版を表示し続ける。画面が読むのは版のファイルだけなので、処理の途中の結果が混ざらない。
- 以前の版のファイルは消さない。
- **結果ファイル（`stt/run-{r}/`）を消してよい条件**は、基本設計 §7 のとおり、「r が現在の実行番号でなく、かつ r から作った版がない、またはすべて `corrupt`・`invalid` になった」場合に限る。MVP では、この掃除を実装しない（結果ファイルは小さい。会議の削除で一緒に消える）。
- 同じ会議の実行の開始が重なった場合（操作の連打など）は、2 つ目が、会議の状態遷移の拒否（`transcribing` に `transcriptionStarted`）で止まる（[architecture.md](architecture.md) §5）。

---

## 8. 開始条件と延期

満たす要件: FR-STT-06・12・13、NFR-PWR-02 ／ 決定: BD-STT-08、BD-ARC-08

### 8.1 開始条件

```swift
/// 開始条件の入力（すべて注入点から組み立てる）
public struct StartConditions: Equatable, Sendable {
    public var isRecording: Bool
    public var engine: EngineAvailability
    public var thermal: ThermalLevel
    public var isLowPowerMode: Bool
    /// 確定後に自動で文字起こしする（SettingsStoring.autoTranscribe。FR-STT-12）
    public var autoStartEnabled: Bool
    /// この会議の文字起こしが、すでに始まっている（ジョブ行がある実行番号がある）か、
    /// このプロセスの中で利用者が開始を指示した（メモリ上だけ。再起動の後は消える）
    public var startAuthorized: Bool
    /// 打ち切った実行の停止をまだ確かめていない（§5.2 の「止まっていない実行」）
    public var hasUnconfirmedStop: Bool
}

public enum StartTrigger: Equatable, Sendable {
    /// 確定の直後（自動開始の設定を見る）
    case finalized
    /// 条件の変化による評価し直し（中断していた実行の再開）
    case conditionsChanged
    /// 利用者の開始・再試行・再文字起こしの操作
    case userRequested
    /// 利用者が、温度・低電力による延期を承知して実行する操作
    case userOverride
}

/// 開始条件（純粋関数。基本設計 §8）。満たさなければ理由を返す
public enum TranscriptionStartPolicy {
    public static func evaluate(_ conditions: StartConditions, trigger: StartTrigger) -> TranscriptionWaitReason? {
        // 録音中・止まっていない実行・エンジンの可否は、利用者の操作でも上書きできない
        if conditions.isRecording {
            return .recording
        }
        if conditions.hasUnconfirmedStop {
            return .engineNotStopped
        }
        switch conditions.engine {
        case .available: break
        case .assetMissing: return .assetMissing
        case .unsupportedDevice: return .unsupportedDevice
        case .unsupportedLocale: return .unsupportedLocale
        case .temporarilyUnavailable: return .engineUnavailable
        }
        if trigger != .userOverride {
            if conditions.thermal >= .serious {
                return .thermal
            }
            if conditions.isLowPowerMode {
                return .lowPower
            }
        }
        // 自動開始が無効なら、まだ始まっていない会議は、きっかけによらず利用者の指示を待つ
        // （finalized だけでなく conditionsChanged でも自動では始めない）。
        // 利用者が始めた・すでに始まっている実行の続き（conditionsChanged）は止めない
        let byUser = trigger == .userRequested || trigger == .userOverride
        if !byUser, !conditions.startAuthorized, !conditions.autoStartEnabled {
            return .autoStartDisabled
        }
        return nil
    }
}
```

| 条件（評価の順） | 満たさないときの理由（`TranscriptionWaitReason`） | 利用者による上書き |
| --- | --- | --- |
| 録音中でない | `recording` | できない |
| 打ち切った実行の停止を確かめている（§5.2） | `engine_not_stopped` | できない（停止を確かめた時点で評価し直す） |
| エンジンが使える | `asset_missing`・`unsupported_device`・`unsupported_locale`・`engine_unavailable` | できない（アセットの取得はできる） |
| 温度が `serious` 未満 | `thermal` | できる（きっかけ `userOverride`） |
| 低電力モードでない | `low_power` | できる（同上） |
| 自動開始が有効。または、その会議の文字起こしが始まっている・利用者が開始を指示した（`startAuthorized`） | `auto_start_disabled` | 利用者の開始の指示（きっかけ `userRequested`）、または設定で自動開始を有効にする（[ui.md](ui.md) §4.6） |

- 入力（`StartConditions`）は、録音セッションの状態、`SpeechEngine.availability()`、`DeviceConditionsProviding`（温度・低電力）、`SettingsStoring`（自動開始）から組み立てる。すべて注入点なので、試験で差し替えられる（TST-GEN-04）。
- 評価し直すきっかけ: 録音の確定（副作用 `evaluateTranscriptionStart`。[architecture.md](architecture.md) §5）、録音の開始の失敗、前面への復帰、`DeviceConditionsProviding.changes()` の変化、言語アセットの導入の完了、打ち切った実行の停止の確認、自動開始の設定の変更、利用者の操作。
- **自動開始が無効な場合（FR-STT-12）:** まだ始まっていない会議（`startAuthorized` が偽）は、どのきっかけ（`finalized`・`conditionsChanged`）で評価し直しても、利用者が開始を指示するか、設定で自動開始を有効にするまで、自動では始めない。例: 自動開始が無効なまま、言語アセットの導入の完了や温度の回復で評価し直しても始めない。利用者が開始を指示した会議（エンジンが使えず待っていたものを含む）と、すでに実行番号のジョブがある会議の続き（中断していた実行の再開）は、`conditionsChanged` で評価し直して進める（設定を無効にしたことで止めない）。利用者の指示はメモリ上だけで持ち、再起動の後に、自動開始が無効でジョブのない会議は、もう一度指示を待つ（利用者の操作なしに始める側に倒さない）。
- 満たさない理由は `meetings.waiting_reason` に記録し、画面に表示する（[ui.md](ui.md) §4.4）。エンジンが使えない間は、会議を `finalized` のまま待たせ、ジョブを作らない（BD-STT-08）。録音と確定には影響しない（INV-03）。

### 8.2 実行中の延期

- ジョブとジョブの間で、開始条件を評価し直す（きっかけは `conditionsChanged`。利用者が上書きした実行では `userOverride`）。満たさなければ次のジョブを始めず、会議は `transcribing` のまま、理由を表示する。
- 実行中のジョブは、温度・低電力の変化では中止しない（1 Chunk 分で終わる）。中止するのは、録音の開始（§8.3）・バックグラウンドの打ち切り（§9）・削除（[storage.md](storage.md) §10）・上限時間（§5.2）だけである。
- 利用者の上書きは、その実行番号が終わるまで有効とし、メモリ上だけで持つ（再起動の後は、条件を評価し直す）。

### 8.3 録音の開始による中止

録音の開始（[recording.md](recording.md) §3.4 の手順 6）が、`TranscriptionService.suspendForRecording()` を呼ぶ。**録音の開始は、その完了（エンジンの実行の停止の確認）を待たない**（基本設計 §8。録音の継続を最優先にする。INV-01、BD-ARC-08）。`suspendForRecording()` は、次の 1・2 を中断点なしに行って直ちに戻り、3 以降は文字起こしの側で続ける。

| 順 | 処理 |
| --- | --- |
| 1 | 新しいジョブの実行開始を受け付けなくする（理由 `recording`。録音の確定まで） |
| 2 | 止まっていないすべての実行（実行中のジョブと、それより前に `TIMEOUT`・中止で打ち切って停止をまだ確かめていない実行）に中止を指示する（`engine.cancel()` を別のタスクで呼ぶ。待たない） |
| 3 | 実行世代の照合で、遅れて届いた結果を捨てる。結果ファイル・版のファイルを書かない。中止されたジョブは、分類 `SYSTEM_EXPIRED` で `pending` に戻る（試行回数を戻す） |
| 4 | `engine.cancel()` が戻ったことを停止の確認とする（§5.2）。録音の確定の後も、停止を確かめるまで次の試行を始めない |

- **防止（原則。基本設計 §14）:** `cancel()` は、入力の列を終了した解析を `cancelAndFinishNow()` で止め、認識の操作全体（`operation`）の終了まで待つ（§3.3）。中止を指示した後、エンジンに新しい音声を渡さず（入力の列は開始の前に終わっている）、解析を作らず・始めない（準備の `await` の後ごとの `checkCancellation()`）。実エンジンで中止から `cancel()` が戻るまでの時間は、文字起こしの実装の最初に Mac と実機（TST-STT-07）で実測して §14 に記録する。
- **例外（防止できない場合。NFR-PWR-02 の例外。基本設計 §14）:** 認めるのは、録音の開始より前に始まり、録音の開始時に中止を指示済みで、`cancel()` が戻らない実行だけとする（アプリからそれ以上止める手段がない）。録音の開始の後に始まる実行は 1 の受け付けの停止で作られず、中止を指示していない実行は 2 で残らない。扱いは、①新しい音声を渡さず、新しい試行・別のジョブを始めない（`engine_not_stopped`）、②結果を捨て、結果ファイル・版のファイルを書かない（3）、③`cancel()` の戻りを待ち続け、戻った時点で例外を終える（問い合わせの間隔は持たない。戻りを待つだけ）、④例外の発生をログ `engine_stop_unconfirmed`（実行番号・連番）で、終わりを `engine_stop_confirmed`（中止の指示からの時間）で記録し（本文を含めない）、会議の状態の帯に理由 `engine_not_stopped` を表示する、⑤録音の優先度・処理を変えない（録音の開始はこれを待たず、録音経路は文字起こしの状態を参照しない）、⑥録音の確定後も、停止を確かめるまで次の試行を始めない（§5.2）。**この例外の採用は、オーナーの確認を待っている**（[PROGRESS.md](../../PROGRESS.md) §3）。確認が得られるまで、文字起こしの実装に着手しない。
- 中止を指示するのは録音の開始の**手順**であり、録音パイプラインは文字起こしの型を参照しない（INV-01、BD-ARC-08）。
- 利用者が温度・低電力を上書きした実行にも適用する。
- 録音の確定の後と、録音の開始の失敗の後（入力を止め、セッションを無効化した後。[recording.md](recording.md) §3.4）に、開始条件を評価し直す。満たせば、中断したジョブから再開する（`pending` の小さい連番から。止まっていない実行が残っていれば、停止を確かめてから）。
- 削除の前の中止（[storage.md](storage.md) §10 の手順 1。削除要求を永続化した後）は、録音の開始と違い、`engine.cancel()` の戻り（停止の確認）とジョブの完了の処理の終わりを、上限つきで待つ（[storage.md](storage.md) §9）。削除では、その会議の実行世代を無効にして、遅れて届いた結果を保存しない（§5.3 の照合と、削除要求の確認）。

---

## 9. バックグラウンドでの実行

満たす要件: NFR-PWR-03、FR-RCV-04、SEC-PLT-07 ／ 決定: BD-STT-07

```swift
/// バックグラウンドでの継続の可否
public enum BackgroundGrant: String, Equatable, Sendable, CaseIterable, LogSymbolSource {
    /// 背面に回っても続けられる
    case granted
    /// 投入できなかった。前面にいる間だけ進める
    case refused
}

/// バックグラウンドでの実行（注入点。BD-STT-07）。本番は ContinuedProcessingExecution、テストは FakeBackgroundExecution。
/// 引数に文字列がないので、会議タイトルや本文を表示へ渡せない（SEC-PLT-07）
public protocol BackgroundExecution: Sendable {
    /// 前面で、文字起こしの開始と同時に呼ぶ
    func begin(totalUnits: Int) async -> BackgroundGrant
    /// ジョブが 1 つ終わるたびに呼ぶ（進捗を報告し続けないと、システムが打ち切る）
    func report(completedUnits: Int, totalUnits: Int) async
    func end(success: Bool) async
    /// システムが打ち切ったら要素を 1 つ流す
    func expirations() -> AsyncStream<Void>
}
```

本番の実装（アプリの `Platform/ContinuedProcessingExecution.swift`。iOS 27.2 SDK に対してビルドできた）:

```swift
/// 継続処理タスク（BGContinuedProcessingTask）のアダプタ（BD-STT-07）。
/// 専用の直列キューを actor の実行器にし、タスクにはこのキューの上でだけ触れる
actor ContinuedProcessingExecution: BackgroundExecution {
    /// 表示する文言は固定で、差し込む値を持たない（会議タイトル・本文を出さない。SEC-PLT-07）
    private static let title = "文字起こしを実行しています"
    private static let subtitle = "アプリを開くと進み具合を確認できます"

    private let queue = DispatchSerialQueue(label: "MinutesMobile.continued-processing")
    /// "<Bundle Identifier>.transcription"。許可する識別子は、末尾に ".*" を付けた形で宣言する
    private let identifierPrefix: String
    private let ids: any IDGenerator
    private let expirationStream: AsyncStream<Void>
    private let expirationContinuation: AsyncStream<Void>.Continuation
    /// いまの投入のタスク（currentIdentifier と同じ識別子のものだけを持つ）
    private var task: BGContinuedProcessingTask?
    /// いまの投入の識別子。end(success:) はこの投入にだけ当てる。拒否された・終わった投入では nil
    private var currentIdentifier: String?
    /// タスクが届く前に終わった投入の結果（投入の識別子ごと）。届いたタスクには、同じ識別子の結果だけを当てる
    private var recordedOutcomes: [String: Bool] = [:]
    private var completedUnits: Int64 = 0
    private var totalUnits: Int64 = 0

    nonisolated var unownedExecutor: UnownedSerialExecutor {
        queue.asUnownedSerialExecutor()
    }

    init(bundleIdentifier: String, ids: any IDGenerator) {
        identifierPrefix = "\(bundleIdentifier).transcription"
        self.ids = ids
        (expirationStream, expirationContinuation) = AsyncStream.makeStream(of: Void.self)
    }

    /// アプリの起動時に 1 回だけ呼ぶ（同じ識別子を 2 回登録すると、システムがアプリを終了させる）。
    /// 識別子が Info.plist の宣言に含まれていなければ false
    nonisolated func registerLaunchHandler() -> Bool {
        let pattern = "\(identifierPrefix).*"
        return BGTaskScheduler.shared.register(forTaskWithIdentifier: pattern, using: queue) { [weak self] task in
            guard let self, let continued = task as? BGContinuedProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            // ハンドラは queue（この actor の実行器）の上で呼ばれるので、actor の中として扱える。
            // BGTask は Sendable ではない。システムから渡された 1 つの参照を、この actor だけが持つ
            nonisolated(unsafe) let handedOver = continued
            assumeIsolated { $0.attach(handedOver) }
        }
    }

    func begin(totalUnits: Int) async -> BackgroundGrant {
        self.totalUnits = Int64(totalUnits)
        completedUnits = 0
        // 前の投入のタスクが残っていれば完了させる（この投入の end(success:) で前の投入のタスクを完了させない）
        task?.setTaskCompleted(success: false)
        task = nil
        let identifier = "\(identifierPrefix).\(ids.makeUUID().uuidString.lowercased())"
        // 投入の await の間に届くタスクを照らせるよう、await の前にこの投入の識別子にする
        currentIdentifier = identifier
        let request = BGContinuedProcessingTaskRequest(
            identifier: identifier, title: Self.title, subtitle: Self.subtitle,
        )
        // すぐに始められなければ失敗させる（待ち行列に入れない）。GPU は要求しない
        request.strategy = .fail
        do {
            try await BGTaskScheduler.shared.submitTaskRequest(request)
            return .granted
        } catch {
            // 拒否・未対応。この投入のタスクは届かないので、結果を記録する先にしない。前面にいる間だけ進める
            if currentIdentifier == identifier {
                currentIdentifier = nil
            }
            return .refused
        }
    }

    func report(completedUnits: Int, totalUnits: Int) async {
        self.completedUnits = Int64(completedUnits)
        self.totalUnits = Int64(totalUnits)
        task?.progress.totalUnitCount = self.totalUnits
        task?.progress.completedUnitCount = self.completedUnits
    }

    func end(success: Bool) async {
        guard let identifier = currentIdentifier else {
            // いまの投入がない（拒否された・もう終わった）。前の投入のタスクに結果を当てない
            return
        }
        currentIdentifier = nil
        guard let attached = task else {
            // タスクがまだ届いていない（投入の直後に終わった）。届いたら、この投入の結果ですぐに完了させる
            recordedOutcomes[identifier] = success
            return
        }
        attached.setTaskCompleted(success: success)
        task = nil
    }

    nonisolated func expirations() -> AsyncStream<Void> {
        expirationStream
    }

    private func attach(_ continued: BGContinuedProcessingTask) {
        if let outcome = recordedOutcomes.removeValue(forKey: continued.identifier) {
            // その投入の実行はもう終わっている。進行表示を残さないよう、受け取ったタスクをその投入の結果ですぐに完了させる
            continued.setTaskCompleted(success: outcome)
            return
        }
        guard continued.identifier == currentIdentifier, task == nil else {
            // いまの投入のものでないタスク（前の投入のもので結果の記録もない）。いまのタスクとして扱わず、すぐに完了させる
            continued.setTaskCompleted(success: false)
            return
        }
        task = continued
        continued.progress.totalUnitCount = totalUnits
        continued.progress.completedUnitCount = completedUnits
        let continuation = expirationContinuation
        // システムが打ち切る。手順の側が、処理中のジョブを中止してから end(success: false) を呼ぶ
        continued.expirationHandler = { continuation.yield() }
    }
}
```

| 決めたこと | 内容 |
| --- | --- |
| 識別子（申し送り） | Info.plist の `BGTaskSchedulerPermittedIdentifiers` に **`$(PRODUCT_BUNDLE_IDENTIFIER).transcription.*`** を宣言する。投入のたびに、末尾を UUID にした `{Bundle Identifier}.transcription.{UUID}` を使う（SDK のヘッダが示す形） |
| 背景モード | `UIBackgroundModes` に `processing` を足す（`audio` は録音用に設定済み。[dev-environment.md](dev-environment.md) §12.1）。SDK のヘッダは「適切なモード」としか書いていないので、`processing` で投入できることは TST-DEV-11 で確かめる |
| 表示する文言 | 題名「文字起こしを実行しています」、副題「アプリを開くと進み具合を確認できます」。**固定の文言で、差し込む値を持たない**（会議タイトル・本文を出さない。protocol の引数に文字列がない） |
| 投入の方針 | `strategy` は `fail`（すぐに始められなければ失敗させる）。失敗したら `refused` を返し、前面にいる間だけ進める。`requiredResources` は既定のまま（GPU を要求しない。GPU の entitlement は Personal Team で使えない） |
| 進捗の報告の頻度（申し送り） | 投入の直後と、**ジョブが 1 件終わるたび**に、`Progress` の「完了した数 ÷ 全体」を更新する。全体は、その実行番号の `transcribe_chunk` の数 + 1（統合） |
| 登録 | アプリの起動時に 1 回だけ `registerLaunchHandler()` を呼ぶ（アプリのデリゲート。[architecture.md](architecture.md) §10.1）。同じ識別子を 2 回登録しない |
| 並行性 | 専用の直列キューを actor の実行器にし、起動ハンドラも同じキューで呼ばせる。`BGTask` は `Sendable` ではないので、ハンドラから actor への受け渡しの 1 か所にだけ `nonisolated(unsafe)` を使う（[architecture.md](architecture.md) §12 の NS-A-08 に挙げる） |

| 局面 | 動作 |
| --- | --- |
| 前面で開始する | 実行の開始（§7）と同時に `begin(totalUnits:)` を呼ぶ |
| 背面に回る・ロックされる | `granted` なら、ジョブを進める。`refused` なら、実行中のジョブが終わったところで止まる（OS がアプリをサスペンドする） |
| システムが打ち切る（`expirations()`） | 実行中のジョブを中止し（`engine.cancel()`。分類 `SYSTEM_EXPIRED`）、完了の処理を待ってから `end(success: false)` を呼ぶ |
| 前面に戻る | 開始条件を評価し直し、`pending` のジョブがあれば `begin` からやり直す |
| 実行番号が終わる | `end(success: true)` |
| タスクが届く前に終わる（投入の直後に実行番号が終わる・打ち切る） | `end(success:)` が結果を**投入の識別子ごとに**記録し、後から届いたタスク（`attach`）を、タスクの識別子（`identifier`。投入の要求と同じ値）と同じ投入の結果ですぐに完了させる。いまの投入（`currentIdentifier`）のタスクだけを `task` として持ち、`end(success:)` はいまの投入にだけ当てる。前の投入のタスクが次の投入（B）の `begin` の後に届いても、B のタスクとして扱わず、B の `end` で完了させない（記録があればその結果で、なければ失敗として、届いた時点で完了させる）。拒否された投入は、タスクが届かないので記録しない。届かないまま残る記録は、許可されて届かなかった投入の数だけで、プロセスの終了で消える |
| アプリが終了した | 次の起動で回収して再開する（§5.4） |

- どの局面でも、Chunk 単位で状態が永続化されているので、失うのは処理中の 1 Chunk 分の計算だけである。
- テストの差し替え（`FakeBackgroundExecution`）は、`begin` の戻り値を指定でき、打ち切りを任意の時点で流せる。呼び出しの記録（進捗の値）を検査できる。
- 古典的な `BGProcessingTask` は MVP で使わない（基本設計 §9）。

---

## 10. セキュリティ

満たす要件: SEC-NET-01・03、SEC-INT-01〜03、SEC-LOG-03・04、SEC-INP-03、SEC-PLT-07、INV-03 ／ 脅威: T-03・04・05・07

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| 端末内での認識 | `SpeechAnalyzer` だけを使う。`SFSpeechRecognizer` は静的解析が禁止している（§3.2） | SEC-NET-01、T-03 |
| 言語アセットの取得 | `installAssets` を呼ぶのは、利用者の明示操作の 1 経路だけ。事前に説明を表示する。アプリは通信しない | SEC-NET-03 |
| 録音の保護 | Chunk は読み取り専用で開く。保存の入口に、Chunk を書く操作がない（§4.1） | INV-03 |
| 完全性 | 認識の前に、ヘッダとハッシュを検証する。合わない Chunk はエンジンに渡さない。版のファイルは、読むたびに照合する（§6.3） | SEC-INT-01〜03、T-07 |
| 本文の置き場所 | 結果ファイルと版のファイルだけ。DB・ログ・エラーの値・継続処理タスクの表示に入れない | BD-STT-03、SEC-LOG-03 |
| エンジンのエラー | 説明文を読まず、分類とコードだけを残す（§3.3） | SEC-LOG-03・04 |
| 永続データの読み込み | 結果ファイル・版のファイル・ハッシュの記録を、検証つきで読む。通らないものは使わない | SEC-INP-03 |
| ロック画面に出る文 | 継続処理タスクの題名と副題は固定の文言（§9） | SEC-PLT-07、T-04 |

**実装・レビュー時の注意点**（基本設計 [architecture.md](../02-basic-design/architecture.md) §12.2 の NS-01・05・08・11 と、基本設計 §12 の注意点を、本書の型の単位に具体化したもの）:

| No. | 注意点 | 確かめ方 |
| --- | --- | --- |
| NS-T-01 | `installAssets` を、利用者の操作以外（起動時・開始条件の評価・自動の再試行）から呼ばない | レビュー。`grep` で呼び出し箇所を数える。単体（可否が `asset_missing` のとき、`installAssets` が呼ばれないこと） |
| NS-T-02 | `EngineFailure`・`TranscriptionFailureClass` に、文字列の欄を足さない。`localizedDescription`・`userInfo` を読まない | レビュー。単体（[architecture.md](architecture.md) §13.1 のエラーの値の検査） |
| NS-T-03 | `BackgroundExecution` に、題名・副題などの文字列の引数を足さない。「処理中」の表示に、会議タイトルを入れない | ビルド（protocol に引数がない）、レビュー、TST-DEV-11 |
| NS-T-04 | 完了の通知の反映に、実行世代の照合を通らない経路を作らない。試行回数や連番だけで照合しない | 単体（古い世代の成功・失敗・中止が捨てられること） |
| NS-T-05 | 文字起こしの手順から、Chunk・ハッシュの記録を書く関数を呼ばない。読むのは `TranscriptStoring` の読み出しだけ | 結合（TST-STT-01。前後で全 Chunk のハッシュが変わらないこと） |
| NS-T-06 | 版のファイルとハッシュの記録を、上書き・削除・再計算で作り直さない。公開は置換しない rename だけ | 単体（TST-SEC-11） |
| NS-T-07 | 結果ファイルを、最終状態が `failed` のジョブについて統合に渡さない | 単体（失敗したジョブの結果ファイルが残っていても、失敗区間になること） |
| NS-T-08 | エンジンに渡す補助情報（固有名詞の一覧など）を足す場合は、利用者の入力だけを使う（P2 以降） | レビュー |
| NS-T-09 | 文字起こしの完了を知らせる通知を足す場合も、本文とタイトルを入れない | レビュー |
| NS-T-10 | `nonisolated(unsafe)` を使うのは、継続処理タスクの受け渡しの 1 か所だけにする | レビュー。`grep` で数える |

---

## 11. テスト観点

満たす要件: TST-STT-01〜09、TST-RCV-03、TST-SEC-11、TST-DEV-09〜11、TST-GEN-04・05・06

### 11.1 先に書いて失敗させるテスト（文字起こしの実装の順）

| 順 | テスト | 確かめること | 要件 |
| --- | --- | --- | --- |
| 1 | Overlap の判定 | §4.2 の表の全行。直前の Chunk が 3 秒より短い場合 | TST-STT-08 |
| 2 | 時刻の変換 | 既知の値で、会議全体のサンプル番号になること。Overlap の中で終わる結果が捨てられること | FR-STT-03 |
| 3 | ジョブの状態遷移の網羅 | 全状態 × 全事象。§5.1 の表だけが許されること | TST-GEN-06 |
| 4 | 失敗の分類と回数 | 回数に数えない分類で回数が戻ること。数える分類が上限で `failed` になること | FR-STT-02 |
| 5 | 再実行の間隔と上限時間 | 2・5・10・30・60 秒と ±20%。入力の 10 倍。空でない短い入力（1 サンプル・1 秒）が下限の 30 秒になり、0 にならないこと。330 秒の入力の値が変わらないこと。空の入力が 0 | NFR-REL-03 |
| 6 | 次のジョブの選び方 | 連番の小さい `pending` から。全 Chunk が終わったら統合。実行中があれば待つ | FR-STT-02 |
| 7 | 開始条件 | §8.1 の表の全行と、上書き。自動開始が無効で始まっていない会議が、`finalized`・`conditionsChanged` のどちらでも `auto_start_disabled` になり、`userRequested` と、始まっている会議の `conditionsChanged` では止まらないこと。停止を確かめていない実行があると、上書きでも `engine_not_stopped` になること | TST-STT-05・06、FR-STT-12 |
| 8 | 統合（重複の除外） | 境界で 2 回出た発話が 1 回になること。判定の記録。渡す順序を変えても同じ結果 | TST-STT-04 |
| 9 | 統合の境界値 | 重なり 0.5、類似 0.8、包含の比 0.3 と 0.95、空文字列、正規化。類似の判定で、前の側だけ・後ろの側だけ・両方の信頼度が `nil` のどれでも前の側を残すこと（前の側が `nil` で後ろの側が 0.9 でも前を残す） | FR-STT-03 |
| 10 | 結果ファイル・失敗の記録・版のファイルの検証 | 番号の不一致・範囲の外・制御文字・形式の版の違いが拒否されること。読めない失敗の記録が `failed` の側で扱われること（基本設計 §4.3 の 2 つの例外を含む） | SEC-INP-03 |
| 11 | 実行世代の照合（DB） | 古い世代の完了の通知が、試行回数が同じでも捨てられ、行が変わらないこと。2 重の実行開始が 1 つだけ成功すること | TST-GEN-05 |
| 12 | 二重実行（結合） | 同じジョブを 2 回実行する・結果ファイルの保存の直後に落として再実行する。結果ファイルとセグメントが重複しないこと | TST-STT-03 |
| 13 | 録音を変えない（結合） | 文字起こしの前後で、全 Chunk のハッシュが変わらないこと | TST-STT-01 |
| 14 | 再開（結合） | 途中でジョブの実行を止め、未完了の Chunk だけが再実行されること | TST-STT-02 |
| 15 | 起動時の回収（結合） | `processing` のまま残した DB で回収する。回数切れが `failed` になること。不足分の作成が、既存の行を変えず、2 回実行しても件数が変わらないこと | TST-RCV-03 |
| 16 | 結果ファイルの再利用（結合） | 基本設計 §13 の「結果ファイルの再利用と Overlap」の行（Overlap の条件を変えると `pending` になり、理由が記録される） | FR-STT-03 |
| 17 | エンジンが使えない（結合） | 可否を差し替える。会議が `finalized` のまま、ジョブが作られず、録音と確定に影響しないこと | TST-STT-05 |
| 18 | 延期と録音の開始（結合） | 温度・低電力・上書き。実行中に録音を開始し、処理中の Chunk が `pending` に戻ること、**録音の開始が `cancel()` の戻りを待たないこと**（差し替えのエンジンの `cancel()` を戻さないままでも録音の入力が始まる）、遅れて届いた結果が保存されないこと、確定の後に（停止を確かめてから）再開すること。試行回数が増えないこと | TST-STT-06 |
| 19 | 版の保存（結合） | §6.3 の各段で落として再実行する。公開済みの版が変わらず、記録だけの番号が欠番になること | FR-STT-05 |
| 20 | 再文字起こしと失敗区間の再試行（結合） | 以前の版が残ること。完成まで以前の版が読めること。失敗区間の再試行で、成功済みの区間の本文が新しい版に残ること。基本設計 §13 の「失敗区間の再試行（ジョブ行がない）」「再試行の実行番号の記録」「統合の失敗」の各行 | TST-STT-09 |
| 21 | 版のファイルの破損（結合） | 1 バイト書き換える・切り詰める。読み込み時に検出され、その版が使われず、ファイルと記録が変わらないこと。DB を作り直した後も検出されること。記録のない版が `invalid` になること | TST-SEC-11 |
| 22 | バックグラウンド（結合） | 投入の成功・拒否・打ち切りを差し替えて、ジョブの状態が正しく戻ること。進捗がジョブごとに報告されること。タスクが届く前に `end(success:)` を呼ぶと、後から届いたタスクがその結果ですぐに完了し、次の `begin` の後に届いたタスクには前の結果が当たらないこと。投入 A を終えて投入 B を始めた後に A のタスクが届くと、A の結果で完了し、B のタスクとして持たれず、B の `end` が B のタスクだけを完了させること（A のタスクを二重に完了させない） | NFR-PWR-03 |
| 23 | エンジンの中止の契約 | `AnalyzerSpeechEngine` の準備の 2 つの `await`（ロケールの問い合わせ・入力の変換）と解析の作成・開始を、試験用の初期化子で差し替えられる関数にする（本番は §3.3 の SDK の呼び出し）。それぞれの `await` で止め、そこで `cancel()` を呼ぶ。`cancel()` が操作の終了の後に戻り、解析が作られず・始まらず（作成と開始の回数が 0）、その後に何も始まらないこと。解析の実行中の `cancel()` では、`cancelAndFinishNow()` の後に戻ること。実行中でなければ直ちに戻ること。前の操作の終了前の `transcribe` が始まらないこと | NFR-PWR-02、TST-STT-06 |
| 24 | 止まっていない実行（結合） | 差し替えのエンジンの `cancel()` を戻さず、上限（`stopConfirmTimeoutMs`）を時計の注入で過ぎさせる。次の試行も別のジョブも `processing` に入らず、理由が `engine_not_stopped` で、ログ `engine_stop_unconfirmed` が出ること。`cancel()` を戻すと `engine_stop_confirmed` が出て、再開すること | NFR-PWR-02 |

### 11.2 合成音声と差し替え

| 部品（`MinutesTestSupport`） | 内容 |
| --- | --- |
| `ScriptedSpeechEngine` | 決めた結果・失敗・遅延を返す。可否を切り替えられる。`cancel()` の呼び出しと、渡された入力の長さを記録する |
| `FakeBackgroundExecution` | `begin` の戻り値を指定でき、打ち切りを流せる。進捗の値を記録する |
| `ManualScheduler`・`FixedRandomSource`・`SequentialIDGenerator` | 待ち時間・ゆらぎ・実行世代を決定的にする（[architecture.md](architecture.md) §6.3） |
| `FakeDeviceConditions`・`InMemorySettings` | 温度・低電力・自動開始を切り替える |

- 単体・結合の試験は、エンジンを差し替えて行う。入力は合成した信号でよい（差し替えたエンジンは、中身を見ない）。
- **実機の合成音声（TST-STT-07 用）**: 端末内の音声合成（`AVSpeechSynthesizer` の `write(_:toBufferCallback:)`）で、既知の日本語の文を 16 kHz・モノラルの PCM にして、試験用の会議の Chunk として保存する。これを実エンジンで認識させ、文字起こしが空でないこと・時刻が音声の範囲の中にあることを確かめる（精度の数値は記録だけ）。合成した音声は、リポジトリにも試験記録にも入れない（TST-GEN-08）。作り方は、実機試験の手順として `docs/04-test-records/` の様式に書く。
- 待ち時間のある試験（再実行の間隔・上限時間・中止の待ち）は、`ManualScheduler` を進めて行う。実時間を待たない（TST-GEN-01）。

### 11.3 実装前に確かめたこと（2026-11-16）

| 項目 | 結果 |
| --- | --- |
| ビルド（Mac） | Swift 6.4.2・Swift 6 言語モード・警告をエラーとして扱う設定で、本書のコード片（Domain・`AnalyzerSpeechEngine`・`JobTable`）がビルドできた |
| ビルド（iOS） | `ContinuedProcessingExecution` を、既定の分離を MainActor にした設定で、iOS 27.2 SDK に対してビルドできた（`xcodebuild` の `generic/platform=iOS`） |
| テスト | 文字起こしに関わる 17 件が通った（Overlap 2 件、ジョブの状態遷移・間隔・選び方 6 件、開始条件 2 件、統合と検証 4 件、実行世代の照合 1 件、エンジン 2 件）。一時の Package の全体では 73 件 |
| 実行世代の照合 | 実際の SQLite で、①2 重の実行開始は 1 つだけ成功した、②中止（回数に数えない）の後の 2 回目の実行は、試行回数が 1 回目と同じ 1 になった、③遅れて届いた 1 回目の成功の通知は捨てられ、行が変わらなかった |
| 統合の境界値 | 重なり 0.5（満たす）と 0.499（満たさない）、類似 0.8（満たす）と 0.7（満たさない）、包含の比 0.3・0.95（満たす）と 0.2・1.0（包含としては満たさない）を確かめた。編集距離は既知の値（kitten と sitting で 3）で確かめた |
| エンジンの可否（この Mac） | macOS 27.2.1 で、`SpeechTranscriber` は使え、日本語（`ja_JP`）に対応し、言語アセットは未導入だった（可否 `asset_missing`）。認識そのものは実行していない |
| 見つけて直したこと | 継続処理タスクの投入は、同期の `submit(_:)` が iOS 27 で非推奨（警告がエラーになる）だった。`submitTaskRequest(_:)` に直した。`BGTask` を actor へ渡す箇所は、Swift 6 の検査でエラーになり、`nonisolated(unsafe)` が要ることが分かった |
| 整形・静的解析 | SwiftFormat 0.64.7 で整形済み。SwiftLint 0.65.1 で違反なし |

**設計・実装時の注意点:**

- 遅れて届いた古い実行の結果・失敗・中止の通知が捨てられ、状態と試行回数が変わらないことを試験する。試行回数が同じになる場合を含める。
- 統合の試験は、判定の境界値を必ず含める。
- エンジンを差し替えた試験だけで完了にしない。実エンジンの試験（TST-STT-07）の記録を完了条件にする。
- 時刻の変換（Overlap のずれ）は、既知の値で数値を検証する。

### 11.4 シミュレータで確かめられないこと

| 事項 | 単体での扱い | 実機での確認 |
| --- | --- | --- |
| 実エンジンで日本語を認識できる。時刻範囲と信頼度が取れる | 差し替えたエンジンで、信頼度が `nil` の場合を含めて試験する | TST-STT-07 |
| 言語アセットの取得と導入 | `installAssets` の呼び出しを記録する | TST-STT-07 の準備として行い、OS の通信として記録する（TST-DEV-07） |
| 継続処理タスクを投入できる。背面・ロック中にジョブが進む | 投入の成功・拒否・打ち切りを差し替える | TST-DEV-10・11 |
| 120 分の録音の文字起こしが完走する | — | TST-DEV-09 |
| ロック中に、Chunk の読み出しと結果ファイルの書き込みが保護クラスで失敗しない | — | TST-DEV-10 |

---

## 12. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

状態はすべて「未実装」である（実装の作業で、テスト名と状態を書き換える）。

| 要件 | テスト（Suite / 表示名の先頭） | 置き場所 | 状態 |
| --- | --- | --- | --- |
| FR-STT-01、INV-03、TST-STT-01 | 文字起こしの手順 / TST-STT-01 文字起こしの前後で全 Chunk のハッシュが変わらない | MinutesStorageTests | 未実装 |
| FR-STT-02、TST-STT-02 | ジョブの状態遷移 / TST-STT-02 実行開始で試行回数が増え…。文字起こしの手順 / TST-STT-02 止めた後は未完了の Chunk だけが再実行される | MinutesDomainTests・MinutesApplicationTests | 未実装 |
| FR-STT-02、TST-GEN-06 | ジョブの状態遷移 / TST-GEN-06 表にある遷移だけが許され… | MinutesDomainTests | 未実装 |
| FR-STT-04、INV-05、TST-STT-03 | 文字起こしの手順 / TST-STT-03 同じジョブを 2 回実行してもセグメントが重複しない | MinutesApplicationTests | 未実装 |
| FR-STT-03・05、TST-STT-04 | 統合 / TST-STT-04 境界で 2 回出た発話は 1 回になり… | MinutesDomainTests | 未実装 |
| FR-STT-03、TST-STT-08 | Overlap / TST-STT-08 Overlap は、直前の Chunk が利用でき…。統合 / TST-STT-08 Overlap を行わなかった境界は… | MinutesDomainTests | 未実装 |
| FR-STT-03 | Overlap / FR-STT-03 つないだ入力の時刻を…。統合 / FR-STT-03 判定の境界値… | MinutesDomainTests | 未実装 |
| FR-STT-06、TST-STT-05 | 開始条件 / TST-STT-05 エンジンが使えない間は…。文字起こしの手順 / TST-STT-05 会議が finalized のまま待つ | MinutesDomainTests・MinutesApplicationTests | 未実装 |
| FR-STT-07、SEC-NET-03 | 言語アセット / FR-STT-07 取得は利用者の操作からだけ呼ばれる | MinutesApplicationTests | 未実装 |
| FR-STT-08 | エンジン / FR-STT-08 信頼度は文字数で重み付けして平均する。統合 / FR-STT-08 信頼度のない結果も保存できる | MinutesTranscriptionTests・MinutesDomainTests | 未実装 |
| FR-STT-09 | エンジン / FR-STT-09 OS の版の文字列からビルド番号を取り出す。版のファイルにエンジンの情報が入る | MinutesTranscriptionTests | 未実装 |
| FR-STT-12・13、NFR-PWR-02、TST-STT-06 | 開始条件 / TST-STT-06 高温・低電力では延期し…。文字起こしの手順 / TST-STT-06 録音の開始で処理中の Chunk が pending に戻る | MinutesDomainTests・MinutesApplicationTests | 未実装 |
| FR-STT-05、FR-FIN-03、TST-STT-09 | 再文字起こし / TST-STT-09 新しい版ができ、以前の版が残る | MinutesApplicationTests | 未実装 |
| FR-TRV-02 | 失敗区間 / FR-TRV-02 失敗した Chunk が版のファイルに失敗区間として残り、再試行できる | MinutesApplicationTests | 未実装 |
| FR-TRV-03 | 版のファイル / FR-TRV-03 空白区間と利用できなかった Chunk が版に写される | MinutesApplicationTests | 未実装 |
| FR-RCV-04、NFR-REL-03、INV-04、TST-RCV-03 | ジョブの状態遷移 / TST-RCV-03 起動時の回収は…。復旧 / TST-RCV-03 processing のジョブが回収されて再実行される | MinutesDomainTests・MinutesStorageTests | 未実装 |
| NFR-REL-03 | ジョブの状態遷移 / NFR-REL-03 再実行の間隔は… | MinutesDomainTests | 未実装 |
| TST-GEN-05 | ジョブの表 / TST-GEN-05 古い実行世代の完了の通知は… | MinutesStorageTests | 未実装 |
| SEC-INT-03、TST-SEC-11 | 版のファイル / TST-SEC-11 書き換えた版は読み込み時に検出され… | MinutesStorageTests | 未実装 |
| SEC-INP-03 | 統合 / SEC-INP-03 結果ファイルは、番号・範囲・本文を検証してから使う | MinutesDomainTests | 未実装 |
| NFR-PWR-03 | バックグラウンド / NFR-PWR-03 打ち切りで処理中のジョブが pending に戻り、進捗がジョブごとに報告される | MinutesApplicationTests | 未実装 |
| NFR-PRF-03 | 計測値 / NFR-PRF-03 実時間比が Chunk ごとに記録される | MinutesApplicationTests | 未実装 |
| NFR-L10N-02 | エンジン / NFR-L10N-02 日本語のロケールで可否を問い合わせる | MinutesTranscriptionTests | 未実装 |
| TST-GEN-04 | `SpeechEngine`・`BackgroundExecution` の差し替え（§11.2） | MinutesApplicationTests | 未実装 |
| SEC-NET-01 | 規則 `no_legacy_speech_recognizer`（[dev-environment.md](dev-environment.md) §6） | 静的解析 | 通過 |
| FR-STT-01、NFR-PRF-03 | 実機試験 TST-STT-07 | 実機 | 未実施 |
| FR-STT-12、SEC-DAT-01・02 | 実機試験 TST-DEV-10 | 実機 | 未実施 |
| NFR-PWR-03、FR-STT-02 | 実機試験 TST-DEV-11 | 実機 | 未実施 |
| NFR-REL-03 | 実機試験 TST-DEV-09 | 実機 | 未実施 |

---

## 13. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [transcription.md](../02-basic-design/transcription.md) §14 の 7 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| エンジンの protocol と、`SpeechAnalyzer` への入力の組み立て方（変換・モデルの保持の設定） | `SpeechEngine`（可否・情報・アセットの取得・認識・中止）。入力は `AnalyzerInputConverter` で変換し、1 Chunk を 1 つの解析にする。モデルの保持は `lingering` | §3 |
| 結果ファイル・失敗の記録（`{連番}.failed.json`。§4.3）・版のファイルの項目 | `ChunkTranscript`・`ChunkFailureRecord`・`TranscriptVersionDocument`・`TranscriptHashRecord`（どれも形式の版 1）と、読むときの検証。読めない失敗の記録は `failed` の側で扱う | §4.4・§6.2 |
| ジョブの表の列、一意制約、次に実行するジョブの選び方 | 列と一意制約は storage.md §3.3。実行開始と完了は実行世代で照合する。次のジョブは、連番の小さい `pending` から、全 Chunk が終わったら統合 | §5.3 |
| 統合の規則の擬似コードと設計値の最終値 | `TranscriptMerger`（10 の規則）。設計値は基本設計の初期値のまま確定（重なり 0.5・類似 0.8・包含の比 0.3〜0.95・範囲 3.5 秒） | §2・§6.1 |
| 継続処理タスクの識別子と、進捗の報告の頻度 | 識別子は `{Bundle Identifier}.transcription.*`。進捗は、投入の直後とジョブが 1 件終わるたび | §9 |
| 打ち切ったエンジンの実行の停止の確かめ方と、停止の待機の時間の上限（§4.2 の「止まっていない実行」） | `engine.cancel()` が戻ったこと（認識の操作全体の `Task` の終了まで待つ）を停止の確認とする。待機の上限は 5 秒（`stopConfirmTimeoutMs`）。過ぎたら次の試行を始めず `engine_not_stopped` を表示し、戻りを待ち続けて、戻った時点で再開する | §3.3・§5.2 |
| 録音の開始の時点で停止を確かめられない実行の扱い（§8。録音の開始は待たず・拒否しない）。次の 2 段で決める | 防止は `cancel()` の 3 段（中止・`cancelAndFinishNow()`・操作の終了の待機）で、止まるまでの時間は実装の最初に実測する。例外の扱い①〜⑥は §8.3 のとおりで、確認は `cancel()` の戻りを待つ（間隔を持つ問い合わせはしない）。記録はログ `engine_stop_unconfirmed`・`engine_stop_confirmed` と理由 `engine_not_stopped` の表示。例外の採用はオーナーの確認待ち | §8.3 |

---

## 14. 断定しない事項・確かめていない事項

満たす要件: FR-STT-01・03・08、NFR-PRF-03、NFR-PWR-03 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| `AnalyzerSpeechEngine` で、実際に日本語を認識できる | ビルドできることと、可否の判定が動くことだけを Mac 上で確かめた。認識は実行していない（言語アセットの取得は OS の通信を伴うため） | TST-STT-07（実機）。文字起こしの実装の最初に、Mac 上でも確かめる |
| `SpeechAnalyzer` の利用に、音声認識の利用目的の文言（`NSSpeechRecognitionUsageDescription`）が要るか | SDK の宣言からは判断できない（基本設計 §11 の宿題のまま）。[dev-environment.md](dev-environment.md) §14 は「置かない」としている | TST-STT-07。要る場合は、文言を足して dev-environment.md を同じ作業で直す |
| 1 件のジョブの上限時間の下限 30 秒（`minimumTimeoutMs`。§5.2）で、短い入力の認識が上限までに終わる | 初期値。エンジンの起動から結果までの固定の時間を測っていない | 文字起こしの実装の最初（Mac・実機）に、1 秒以下の入力で認識の時間を測って見直す |
| 確定した結果の時刻範囲（`range`）が、入力の先頭から数えた値で返る | SDK の宣言による。確かめていない | TST-STT-07（時刻が音声の範囲の中にあること） |
| 信頼度の属性が取れる。しきい値 0.4 が妥当 | 取れない場合は `nil` として保存する設計にした | TST-STT-07。実測して見直す |
| `modelRetention` の `lingering` で、Chunk ごとのモデルの読み込み直しが避けられる | SDK の列挙の名前による。効果は測っていない | 計測値（実時間比）で見る。効果がなければ、解析を会議の実行の間だけ使い回す形に改める |
| 1 Chunk を 1 つの解析にする方式で、結果の列（`results`）が解析の終了で終わる | 確かめていない。終わらない場合、認識が戻らず、上限時間で `TIMEOUT` になる | 文字起こしの実装の最初（Mac・実機） |
| 30 秒の Chunk と 3 秒の Overlap が `SpeechTranscriber` に適している | エンジンの結果の区切り方による | 境界での取りこぼし・重複を記録して見直す（設計値は差し替えられる） |
| Personal Team で継続処理タスクを投入できる。`processing` の背景モードで足りる | 基本設計 §11 のまま。識別子のワイルドカードが一致しないという報告がある | TST-DEV-11。使えなければ「前面で進め、戻ったら再開」になる（設計済み） |
| 継続処理タスクの進捗を、ジョブごと（1 Chunk の処理時間ごと）に報告すれば、システムに打ち切られない | SDK のヘッダは「停滞して見えると打ち切られうる」としか書いていない | TST-DEV-11。打ち切られるなら、ジョブの途中でも進捗を細かく進める |
| バックグラウンド・ロック中に音声認識が動く | エンジンが必要とする資源による | TST-DEV-10・11 |
| 起動ハンドラが、登録のときに渡したキューの上で呼ばれる | SDK のヘッダの説明による | TST-DEV-11（`assumeIsolated` が停止しないこと） |
| 日本語の精度・速度 | 端末・話者・音質による。保証しない | 実時間比と信頼度を記録する |
| 2026-11-27 に変えた・足したコード片（§2 の `TranscriptionTuning`、§3.3 の `transcribe`・`run`・`cancel`、§8.1 の `StartConditions`・`evaluate`、§9 の `ContinuedProcessingExecution`）がビルドできる | ビルドしていない（§11.3 の確認は、それより前の版のもの） | 文字起こしの実装の最初にビルドとテストで確かめ、本書を直す |
| 実エンジンで、中止から `cancel()` が戻るまでの時間。上限 5 秒が足りる | 測っていない（言語アセットを取得していないので、認識を実行していない） | 文字起こしの実装の最初（Mac・実機の TST-STT-07）。測った値をここに記録し、上限を見直す |
| 統合の規則が Minutes P2 §11 と一致する | 参照元のリポジトリが手元にないため、基本設計の記述から起こした | 基本設計と照合済み。差があれば、基本設計を正とする |

---

## 15. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- iOS 27.2 SDK・macOS 27.2 SDK の `Speech.swiftinterface`（`SpeechAnalyzer`・`SpeechAnalyzer.Options` と `ModelRetention`・`SpeechTranscriber` とその属性の指定・`AnalyzerInput`・`AnalyzerInputConverter`・`AssetInventory` と状態・`AssetInstallationRequest`・`SFSpeechError.Code` の拡張）、`SFErrors.h`
- iOS 27.2 SDK の `BGTaskRequest.h`（`BGContinuedProcessingTaskRequest`・投入の方針・要求する資源・識別子の形）、`BGTask.h`（`BGContinuedProcessingTask`・進捗の報告）、`BGTaskScheduler.h`（登録・非同期の投入・同期の投入の非推奨の宣言・エラーコード）
- Xcode 27.2（27B52）・Swift 6.4.2 の `swift build`・`swift test`・`xcodebuild`（一時の Package で実行。§11.3 の結果はこの実行による。エンジンの可否は macOS 27.2.1 で調べたもの）
- 基本設計 [transcription.md](../02-basic-design/transcription.md) §15 の出典（Apple Developer Forums の継続処理タスクの識別子に関する報告など。本作業では再確認していない）
- Minutes `docs/design-local-phase2.md` §9〜§11（別リポジトリ・読み取り専用。本作業では参照できず、基本設計の記述に基づいた）
- Apple のシンボルの文書: [SpeechTranscriber](https://developer.apple.com/documentation/speech/speechtranscriber)・[AssetInventory](https://developer.apple.com/documentation/speech/assetinventory)・[BGContinuedProcessingTaskRequest](https://developer.apple.com/documentation/backgroundtasks/bgcontinuedprocessingtaskrequest)（ページの存在を確認。確認日 2026-11-20。基本設計 §15 と同じ）
- **出典の URL について（確認日 2026-11-27）:** 上の SDK のヘッダ・`.swiftinterface` は、Xcode に同梱されたローカルのファイルで、公開の URL はない。版の根拠は、SDK の版（iOS 27.2）は `xcodebuild -showsdks`、Xcode 27.2（27B52）は `xcodebuild -version`、Swift 6.4.2 は `swift --version`、各 API の対応 OS は SDK の宣言（`@available`・`API_AVAILABLE`）で確かめたものである（[dev-environment.md](dev-environment.md) §2）。Apple の文書のページは、本文が JavaScript で描かれ、対応 OS の記述を取得できないので、版の根拠にしない
