# MinutesMobile 詳細設計書 ── 録音

| 項目 | 内容 |
| --- | --- |
| 版 | 1.0（初版・実装前） |
| 作成日 | 2026-11-16（技術情報の確認日 2026-11-16） |
| 満たす要件 ID | FR-CNS-02、FR-REC-01〜08、FR-INT-01〜06、NFR-REL-01・04・05、NFR-PRF-01・02、NFR-PWR-01、SEC-PRV-01・02、SEC-LOG-03、SEC-PLT-07、INV-01・07・08・09 |
| 関係する制約 | CON-01・02・04 |
| 対応する基本設計 | [../02-basic-design/recording.md](../02-basic-design/recording.md)（設計決定 BD-REC-01〜08 を引用する） |
| 関連文書 | [architecture.md](architecture.md)・[storage.md](storage.md)・[transcription.md](transcription.md)・[ui.md](ui.md) |

本書は、マイクの音声を 16 kHz・16 bit・モノラルの Chunk にするまでと、割り込み・経路変更への対応を、型とアルゴリズムの単位で定める。Chunk をファイルへ書く手順は [storage.md](storage.md) §4 にある。

**本書のコード片は、実装前に一時の Package でビルドとテストを通したものである**（§11.3）。実ファイルはまだないので、照合の印（`<!-- file: -->`）は付けていない。実装の作業で印を付ける（[README.md](README.md) §3）。

---

## 1. 位置づけと範囲

満たす要件: FR-REC-01、NFR-MNT-02・04 ／ 決定: BD-REC-01・02、UD-10

| 本書で決めること | 節 | 置き場所（[architecture.md](architecture.md) §2） |
| --- | --- | --- |
| 同意確認の値、設計値、入力の形式と経路の型 | §2 | `MinutesDomain/Recording/` |
| 音声入力とオーディオセッションの protocol、セッションの設定、開始と停止の手順 | §3 | protocol は Domain、実装は `MinutesAudio` とアプリの `Platform/` |
| リングバッファ、取り出し、変換、Chunk の組み立て、WAV ヘッダ、簡易 VAD | §4 | `MinutesAudio`（I/O）と Domain（純粋ロジック） |
| 録音セッションの状態遷移（状態 × 事象 × 副作用の全件） | §5 | Domain。実行は `MinutesApplication/RecordingService` |
| 空白区間の理由と、開く・閉じる規則 | §6 | Domain |
| 中断通知の文言と予約までの時間 | §7 | アプリの `Platform/` |
| 入力経路の記録、計測値、画面へ渡す値 | §8・§9 | Domain |

**基本設計を書いた後に分かったこと（2026-11-16 に SDK で確認）。** iOS 27 で次の 2 つが非推奨になった。最低 OS が iOS 27.2 で、警告をエラーとして扱うので（CON-01・04）、非推奨の API を使うとビルドが通らない。本書は置き換え先の API で設計する。基本設計の決定（BD-REC-01〜08）は変わらない。

| 非推奨になった API | 置き換え先（iOS 27 以降） | 使う節 |
| --- | --- | --- |
| `AVAudioSession.interruptionNotification`（割り込みの開始と終了を 1 つの通知で受ける） | `AVAudioSession` の `didBecomeInactive`（無効化の原因つき）と `resumptionRecommendation`（再開の勧め）の 2 つの通知 | §3.3 |
| `AVAudioNode.installTap(onBus:bufferSize:format:block:)` | `installAudioTap(onBus:bufferSize:format:tapProvider:)`（失敗を `throws` で返し、読み取り専用のバッファを渡す） | §4.2 |

---

## 2. 値と設計値

満たす要件: FR-CNS-02、FR-REC-08、SEC-PRV-02、INV-09 ／ 決定: BD-REC-07・08

```swift
import Foundation

/// 同意確認の値（BD-REC-08）。録音の開始はこの値を引数に取る。
/// 公開の初期化子がないので、作れるのは confirmedByUser(clock:) だけである
public struct ConsentConfirmation: Equatable, Sendable {
    /// 確認した時刻。会議の consentConfirmedAt になる（FR-CNS-02）
    public let confirmedAt: Date

    /// 同意確認の画面で、利用者が「同意を得た」を選んだときにだけ呼ぶ
    public static func confirmedByUser(clock: some WallClock) -> ConsentConfirmation {
        ConsentConfirmation(confirmedAt: clock.now())
    }
}

/// 録音の設計値（初期値）。試験では差し替える
public struct RecordingTuning: Equatable, Sendable {
    /// リングバッファの容量（ネイティブ形式での秒数）
    public var ringSeconds = 5
    /// あふれの記録列の容量（件）
    public var overrunRecordCapacity = 64
    /// 最初のバッファを待つ上限（ms）
    public var firstBufferTimeoutMs: Int64 = 3000
    /// 中断通知を予約してから届くまで（秒）
    public var interruptionNoticeDelaySeconds = 60
    /// 割り込みの開始の処理（排出・短い Chunk の確定・空白区間の開設・割り込みの記録の永続化の待機）の上限（ms）
    public var interruptionBeginTimeoutMs: Int64 = 5000
    /// 再構築の④（作り直しと入力の開始の呼び出し）の上限（ms）
    public var rebuildRestartTimeoutMs: Int64 = 3000
    /// 再構築の途中に届いた停止が、①〜③の完了を待つ上限（ms）
    public var rebuildStopWaitTimeoutMs: Int64 = 5000
    /// 割り込みの終了の①〜④の呼び出し 1 つごとの上限（ms）
    public var resumeStepTimeoutMs: Int64 = 3000
    /// 停止の経路で空白区間を閉じる処理の上限（ms）
    public var stopGapCloseTimeoutMs: Int64 = 5000
    /// 割り込みの終了の②の後・④の前に届いた停止で、末尾の Chunk の確定を待つ上限（ms）
    public var stopSealTimeoutMs: Int64 = 5000
    /// 入力の閉鎖の手続き（閉鎖済みにする → 停止の指示 → 閉じたことの確認）の上限（ms）
    public var inputCloseTimeoutMs: Int64 = 2000
    /// 画面へ値を送る最小の間隔（16 kHz のサンプル数。1,600 = 100 ms）
    public var snapshotIntervalSamples: Int64 = 1600

    public init() {}
}

/// 入力のネイティブ形式。sample rate とチャンネル数は実行時に読む（基本設計 §3）
public struct InputFormat: Equatable, Sendable {
    public let sampleRate: Int
    public let channelCount: Int

    /// 受け付ける範囲の外（0 以下・384 kHz 超・32 チャンネル超）なら nil
    public init?(sampleRate: Int, channelCount: Int) {
        guard (8000 ... 384_000).contains(sampleRate), (1 ... 32).contains(channelCount) else { return nil }
        self.sampleRate = sampleRate
        self.channelCount = channelCount
    }
}

/// 入力の種別（FR-REC-08）。機器の名前は持たない（BD-REC-07）
public enum InputRouteKind: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case builtInMic = "built_in_mic"
    case wired
    case bluetooth
    case usb
    case other
}

/// 経路を記録した理由
public enum RouteEventReason: String, Sendable, CaseIterable, Codable, LogSymbolSource {
    case recordingStarted = "recording_started"
    case resumed
    case deviceAvailable = "device_available"
    case deviceUnavailable = "device_unavailable"
    case configurationChanged = "configuration_changed"
    case mediaReset = "media_reset"
    case other
}

/// 事象を受け取った時刻。空白区間の開始時刻と長さの起点に使う（基本設計 §5.1）
public struct EventTime: Equatable, Sendable {
    public let monotonic: MonotonicInstant
    public let wall: Date

    public init(monotonic: MonotonicInstant, wall: Date) {
        self.monotonic = monotonic
        self.wall = wall
    }
}
```

- **`ConsentConfirmation` には公開の初期化子がない。** 値を作れるのは `confirmedByUser(clock:)` だけで、これを呼ぶのは同意確認の画面の Store の 1 か所に限る（[ui.md](ui.md) §4.2）。録音の開始（§3.4）はこの値を引数に取るので、同意確認を通らずに録音を始めるコードは、値を用意できずビルドできない（BD-REC-08、T-06）。呼び出し箇所が 1 つであることは、レビューと `grep` で確かめる（§10 の NS-R-01）。
- `RecordingTuning` の値はすべて設計値（初期値）で、実機試験（TST-DEV-01・02）の結果で見直す。試験では小さい値に差し替える。
- 入力の形式は実行時に読む。44.1 kHz・48 kHz や 1 チャンネルを仮定しない（基本設計 §3）。範囲の外の値は `InputFormat` を作れず、入力の開始の失敗になる。
- 経路の種別は、OS のポートの種別を次のとおり写す。機器の名前（`portName`）と識別子（`uid`）は読まない（BD-REC-07）。

| 種別 | OS のポートの種別 |
| --- | --- |
| `built_in_mic` | `builtInMic` |
| `wired` | `headsetMic`・`lineIn` |
| `bluetooth` | `bluetoothHFP`・`bluetoothLE` |
| `usb` | `usbAudio` |
| `other` | 上のどれでもないもの（CarPlay・仮想の入力など） |

---

## 3. 音声入力とオーディオセッション

満たす要件: FR-REC-05・07、FR-INT-01〜04・06、NFR-PRF-02、SEC-PRV-02、TST-GEN-04 ／ 決定: BD-REC-01・06

### 3.1 注入点

```swift
import Foundation

/// 音声コールバックからの書き込み先（リングバッファの書き手の側）。
/// 3 つとも、メモリ確保・ロック待ち・I/O・ログ出力をしない（NFR-PRF-01）
public protocol CaptureSink: Sendable {
    /// frames 分の書き込みを始める。空きがなければ false を返し、あふれとして数える
    func beginWrite(frames: Int) -> Bool
    /// beginWrite が true を返した後、チャンネルごとに 1 回ずつ呼ぶ
    func writeChannel(_ channel: Int, _ samples: UnsafeBufferPointer<Float>)
    func endWrite()
}

public enum AudioInputFailure: Error, Equatable, Sendable {
    /// 使える入力がない（入力のチャンネル数が 0 など）
    case noInputAvailable
    case unsupportedFormat
    case engineStartFailed(osCode: Int32)
}

/// 音声入力（注入点）。本番は EngineAudioInput、テストは ScriptedAudioInput
public protocol AudioInput: Sendable {
    /// 入力を作り直し、ネイティブ形式を読む（まだ開始しない）。使える入力がなければ失敗する
    func prepare() async throws(AudioInputFailure) -> InputFormat
    /// prepare で読んだ形式のまま開始する。コールバックは sink へ書くだけにする
    func start(sink: any CaptureSink) async throws(AudioInputFailure)
    /// 入力を止めて tap を外す。止まっていれば何もしない
    func stop() async
}

/// オーディオセッションから届く事象（iOS 27 の通知を写したもの。§3.3）
public enum AudioSessionEvent: Equatable, Sendable {
    case interruptionBegan(GapReason)
    case resumptionRecommended(shouldResume: Bool)
    case routeChanged(RouteEventReason)
    case configurationChanged
    case mediaServicesReset
}

public enum MicrophonePermission: String, Sendable, CaseIterable, LogSymbolSource {
    case undetermined, denied, granted
}

/// オーディオセッション（注入点）。本番は SystemAudioSession（アプリターゲット）
public protocol AudioSessionControlling: Sendable {
    func microphonePermission() async -> MicrophonePermission
    func requestMicrophonePermission() async -> Bool
    /// category・mode・オプションを設定して有効化する（§3.2）。失敗は OS のコードだけを返す
    func configureAndActivate() async -> Int32?
    func deactivate() async
    func currentRoute() async -> InputRouteKind
    func events() -> AsyncStream<AudioSessionEvent>
}

/// 中断の通知（注入点）。文言は固定で、引数に本文を渡せない（SEC-PLT-07）。
/// 録音中の会議は 1 件だけなので、予約は常に 1 つ（同じ識別子で置き換える）
public protocol InterruptionNoticeScheduling: Sendable {
    /// 予約する。予約済みなら置き換える。予約できなければ（通知の権限がないなど）false
    func schedule(afterSeconds: Int) async -> Bool
    /// 未配信の予約を取り消す。取り消しを確かめられなければ false
    func cancel() async -> Bool
}
```

| protocol | 本番の実装 | テストの差し替え |
| --- | --- | --- |
| `CaptureSink` | `CaptureRingBuffer`（§4.1） | 本番と同じ型を使う（I/O がないため） |
| `AudioInput` | `EngineAudioInput`（§4.2。`MinutesAudio`） | `ScriptedAudioInput`: テストが渡した合成音声を、決めた大きさのバッファで `sink` へ書く。形式・開始の失敗・「入力なし」を指定できる |
| `AudioSessionControlling` | `SystemAudioSession`（§3.3。アプリ） | `FakeAudioSession`: 権限・有効化の結果・経路を指定でき、事象を任意の順で流せる |
| `InterruptionNoticeScheduling` | `LocalNoticeScheduler`（§7。アプリ） | `NoticeSchedulerSpy`: 予約と取り消しの呼び出しを記録し、失敗を指定できる |

### 3.2 セッションの設定

基本設計 §3 の表を、呼び出す API で確定する。設定は `configureAndActivate()` の 1 か所で行い、有効化のたびに毎回設定し直す（メディアサービスのリセットの後は、設定が失われているため）。

| 項目 | 呼び出し | 値 |
| --- | --- | --- |
| category・mode・オプション | `setCategory(_:mode:options:)` | `.record`・`.default`・`[.allowBluetoothHFP]` |
| システムの警告音による割り込みを避ける | `setPrefersNoInterruptionsFromSystemAlerts(_:)` | `true` |
| 経路の切断で割り込ませない | `setPrefersInterruptionOnRouteDisconnect(_:)` | `false` |
| 有効化 | `activate()`（iOS 27 の非同期の API） | — |
| 無効化 | `deactivate(options:)`（同上） | `[.notifyOthersOnDeactivation]`（中断させた他のアプリに再開を知らせる） |
| sample rate・バッファ長 | 設定しない | OS の値を読む |

`.allowBluetooth` は非推奨で、`.allowBluetoothHFP` を使う（SDK のヘッダで確認）。

### 3.3 セッションの事象

OS の通知は `SystemAudioSession` が `AudioSessionEvent` に写し、`RecordingService` が状態遷移の事象（§5）にする。通知の値（`userInfo`）は外部入力として扱い、型が合わなければ「その他」に写す（強制キャストをしない）。

| OS の通知 | 条件 | `AudioSessionEvent` | 状態遷移の事象（§5） |
| --- | --- | --- | --- |
| `AVAudioSession` の `didBecomeInactive` | 原因が `systemInterruption` | `interruptionBegan(理由)` | `interruptionBegan` |
| 同上 | 原因が `appDeactivated`（アプリ自身の無効化） | 流さない | — |
| `AVAudioSession` の `resumptionRecommendation` | `shouldResume` | `resumptionRecommended(shouldResume: true)` | `resumeRequested` |
| 同上 | `shouldNotResume` | `resumptionRecommended(shouldResume: false)` | `resumeDenied` |
| `routeChangeNotification` | 理由が `newDeviceAvailable`・`oldDeviceUnavailable`・その他 | `routeChanged(理由)` | `routeChanged` |
| 同上 | 理由が `noSuitableRouteForCategory`（使える入力がない） | `configurationChanged` | `configurationChanged`（再構築の中で「入力なし」を検出する） |
| `AVAudioEngineConfigurationChange` | — | `configurationChanged` | `configurationChanged` |
| `mediaServicesWereResetNotification` | — | `mediaServicesReset` | `mediaServicesReset` |
| アプリが前面に戻った（画面の側から届く） | — | — | `resumeRequested` |
| 利用者の「再開」「停止」 | — | — | `resumeRequested`・`stopRequested(manual)` |

割り込みの理由は、`AVAudioSession.InterruptionReason` を空白区間の理由（§6）に写す。

```swift
/// AVAudioSession のアダプタ。割り込みは iOS 27 の通知（無効化・再開の勧め）で受け取る
nonisolated final class SystemAudioSession: AudioSessionControlling {
    func microphonePermission() async -> MicrophonePermission {
        switch AVAudioApplication.shared.recordPermission {
        case .granted: .granted
        case .denied: .denied
        case .undetermined: .undetermined
        @unknown default: .undetermined
        }
    }

    func requestMicrophonePermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func configureAndActivate() async -> Int32? {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.record, mode: .default, options: [.allowBluetoothHFP])
            try session.setPrefersNoInterruptionsFromSystemAlerts(true)
            try session.setPrefersInterruptionOnRouteDisconnect(false)
            try await session.activate()
            return nil
        } catch {
            // OS の説明文は捨て、コードだけを返す（architecture.md §8）
            return Int32(truncatingIfNeeded: (error as NSError).code)
        }
    }

    func deactivate() async {
        do {
            try await AVAudioSession.sharedInstance().deactivate(options: [.notifyOthersOnDeactivation])
        } catch {
            // 無効化の失敗は録音の結果に影響しない。次の開始で設定し直す
            _ = error
        }
    }

    func currentRoute() async -> InputRouteKind {
        guard let port = AVAudioSession.sharedInstance().currentRoute.inputs.first?.portType else { return .other }
        switch port {
        case .builtInMic: return .builtInMic
        case .headsetMic, .lineIn: return .wired
        case .bluetoothHFP, .bluetoothLE: return .bluetooth
        case .usbAudio: return .usb
        default: return .other
        }
    }

    func events() -> AsyncStream<AudioSessionEvent> {
        AsyncStream(bufferingPolicy: .unbounded) { continuation in
            let center = NotificationCenter.default
            let inactive = center.addObserver(of: AVAudioSession.self, for: .didBecomeInactive) { message in
                // アプリ自身の無効化（停止の手順）は割り込みではない
                if case let .systemInterruption(context) = message.deactivationResult {
                    continuation.yield(.interruptionBegan(Self.reason(context.reason)))
                }
            }
            let resumption = center.addObserver(of: AVAudioSession.self, for: .resumptionRecommendation) { message in
                continuation.yield(.resumptionRecommended(shouldResume: message.recommendation == .shouldResume))
            }
            let task = Task {
                await withTaskGroup(of: Void.self) { group in
                    group.addTask {
                        for await note in center.notifications(named: AVAudioSession.routeChangeNotification) {
                            let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
                            continuation.yield(Self.routeEvent(raw.flatMap(AVAudioSession.RouteChangeReason.init)))
                        }
                    }
                    group.addTask {
                        for await _ in center.notifications(named: AVAudioSession.mediaServicesWereResetNotification) {
                            continuation.yield(.mediaServicesReset)
                        }
                    }
                    group.addTask {
                        for await _ in center.notifications(named: .AVAudioEngineConfigurationChange) {
                            continuation.yield(.configurationChanged)
                        }
                    }
                }
            }
            continuation.onTermination = { _ in
                center.removeObserver(inactive)
                center.removeObserver(resumption)
                task.cancel()
            }
        }
    }

    private static func reason(_ reason: AVAudioSession.InterruptionReason) -> GapReason {
        switch reason {
        case .builtInMicMuted: .builtInMicMuted
        case .routeDisconnected: .routeDisconnected
        default: .interruption
        }
    }

    private static func routeEvent(_ reason: AVAudioSession.RouteChangeReason?) -> AudioSessionEvent {
        switch reason {
        case .newDeviceAvailable: .routeChanged(.deviceAvailable)
        case .oldDeviceUnavailable: .routeChanged(.deviceUnavailable)
        // 使える入力がない場合は、再構築の中で検出する（§5）
        case .noSuitableRouteForCategory: .configurationChanged
        default: .routeChanged(.other)
        }
    }
}
```

- アプリターゲットは既定の分離が MainActor なので、型に `nonisolated` を付けて MainActor に縛られないようにする（[architecture.md](architecture.md) §7.1）。このコードは iOS 27.2 SDK に対してビルドできることを確かめた（§11.3）。
- 事象の列は取りこぼさない（`bufferingPolicy: .unbounded`）。事象は数秒に 1 回程度しか起きず、貯まり続けることはない。
- **事象を受け取った時刻は、`RecordingService` が事象を受け取った直後に、単調時計と壁時計の両方で読んで事象に持たせる**（`EventTime`）。排出や確定を終えた後の時刻を使わない（基本設計 §5.1）。

### 3.4 開始と停止の手順

`RecordingService.start(consent:title:lowSpaceAcknowledged:)` が、基本設計 §4 の順に行う。途中で失敗したら、そこで止めて `RecordingStartFailure`（[architecture.md](architecture.md) §8.1）を返す。

| 順 | 処理 | 失敗の分類 |
| --- | --- | --- |
| 1 | 同意確認の値を受け取る（§2）。開始までの時間の計測を始める（単調時計） | — |
| 2 | 録音セッションが `idle` であることを確かめる（FR-REC-07） | `alreadyRecording` |
| 3 | マイクの権限を確かめる。未決定なら要求する | `microphoneDenied` |
| 4 | 空き容量を確かめる（[storage.md](storage.md) §7）。段階 2 で `lowSpaceAcknowledged` が偽なら、確認を求めて止める | —（画面が確認を出して呼び直す） |
| 5 | 会議を作る（会議 ID の発番、ディレクトリ、`meeting.json`、DB の行。状態 `recording`、`consentConfirmedAt`） | `meetingCreationFailed` |
| 6 | 実行中の文字起こしがあれば中止を指示する（`suspendForRecording()`。[transcription.md](transcription.md) §8.3）。**停止の確認を待たず**、続けて手順 7 へ進む（基本設計 §4 の手順 6。停止を確かめられない実行があることを理由に録音を拒否・延期しない） | — |
| 7 | 事象 `startRequested` を状態遷移へ渡す。副作用 `beginCapture` が、セッションの有効化 → 入力の開始 → 取り出しの開始を行う | `sessionActivationFailed`・`inputStartFailed` |
| 8 | 最初のバッファが届いたら `capturing` になり、手順 1 からの時間を記録する（NFR-PRF-02） | `firstBufferTimedOut`（上限 3 秒・設計値） |

- 手順 5 より後で失敗し、音声が 1 サンプルもなければ、その会議を削除する（副作用 `discardMeetingIfEmpty`）。
- 手順 7 より後で失敗した場合は、入力を止め、セッションを無効化する（§5 の表の `startFailed` の行）。録音していない間にマイクを開いたままにしない（SEC-PRV-02）。
- 時間切れは、注入した `Scheduler`（[architecture.md](architecture.md) §6.2）で待ち、事象 `startFailed` として届ける。すでに `capturing` になっていれば、世代が合わないので捨てられる。
- 停止は、事象 `stopRequested(理由)` を渡すだけである。順序（入力を止める → 排出 → 末尾の Chunk の確定 → 空白区間を閉じる → 会議を `stop_requested` にする）は §5 の副作用の並びで決まる。

---

## 4. 録音パイプライン

満たす要件: FR-REC-01〜04・06、NFR-PRF-01、NFR-REL-04、INV-07・08 ／ 決定: BD-REC-01〜03

### 4.1 リングバッファ

書き手（音声コールバック）と読み手（録音パイプライン）が、それぞれ自分の側のカウンタだけを進める。カウンタは `Synchronization.Atomic` で持ち、ロックもメモリ確保も使わない（BD-REC-02）。

```swift
public final class CaptureRingBuffer: CaptureSink, @unchecked Sendable {
    private struct OverrunRecord {
        var position = 0
        var frames = 0
    }

    public let channelCount: Int
    public let capacityFrames: Int
    private let recordCapacity: Int
    private let storage: UnsafeMutablePointer<Float>
    private let records: UnsafeMutablePointer<OverrunRecord>
    /// 受け付けたフレームの累積。書き手だけが進める
    private let writtenFrames = Atomic<Int>(0)
    /// 取り出したフレームの累積。読み手だけが進める
    private let readFrames = Atomic<Int>(0)
    private let recordsWritten = Atomic<Int>(0)
    private let recordsRead = Atomic<Int>(0)
    /// 落としたフレームの累積（計測用）
    private let droppedTotal = Atomic<Int>(0)
    // 書き手だけが触る値（入力を止めた後に限り、読み手が takeTrailingOverrun で読む）
    private var pendingFrames = 0
    private var openDropPosition = 0
    private var openDropFrames = 0

    // （初期化と解放は省略。容量は sample rate × 秒数のフレームで、作成時に確保する）

    // MARK: 書き手（音声コールバック）

    public func beginWrite(frames: Int) -> Bool {
        let written = writtenFrames.load(ordering: .relaxed)
        let free = capacityFrames - (written - readFrames.load(ordering: .acquiring))
        // 空きが足りない、または開いているあふれを記録列へ出せない間は、受け付けずに数える
        guard frames > 0, frames <= free, publishOpenDrop() else {
            if openDropFrames == 0 {
                openDropPosition = written
            }
            openDropFrames += max(0, frames)
            droppedTotal.add(max(0, frames), ordering: .relaxed)
            return false
        }
        pendingFrames = frames
        return true
    }

    public func writeChannel(_ channel: Int, _ samples: UnsafeBufferPointer<Float>) {
        guard channel >= 0, channel < channelCount, samples.count >= pendingFrames else { return }
        let written = writtenFrames.load(ordering: .relaxed)
        for index in 0 ..< pendingFrames {
            storage[((written + index) % capacityFrames) * channelCount + channel] = samples[index]
        }
    }

    public func endWrite() {
        writtenFrames.add(pendingFrames, ordering: .releasing)
        pendingFrames = 0
    }

    /// 連続するあふれを 1 組にして記録列へ出す。記録列が満杯なら false
    private func publishOpenDrop() -> Bool {
        guard openDropFrames > 0 else { return true }
        let count = recordsWritten.load(ordering: .relaxed)
        guard count - recordsRead.load(ordering: .acquiring) < recordCapacity else { return false }
        records[count % recordCapacity] = OverrunRecord(position: openDropPosition, frames: openDropFrames)
        recordsWritten.add(1, ordering: .releasing)
        openDropFrames = 0
        return true
    }

    // MARK: 読み手（録音パイプライン）

    /// インターリーブしたフレームを destination へ取り出す。あふれの記録の位置では、音声より先に記録を返す
    public func read(into destination: UnsafeMutableBufferPointer<Float>) -> RingRead {
        let read = readFrames.load(ordering: .relaxed)
        var limit = writtenFrames.load(ordering: .acquiring) - read
        let recordIndex = recordsRead.load(ordering: .relaxed)
        if recordIndex < recordsWritten.load(ordering: .acquiring) {
            let record = records[recordIndex % recordCapacity]
            if record.position <= read {
                recordsRead.add(1, ordering: .releasing)
                return .overrun(droppedFrames: record.frames)
            }
            limit = min(limit, record.position - read)
        }
        let frames = min(limit, destination.count / channelCount)
        guard frames > 0 else { return .empty }
        for index in 0 ..< frames * channelCount {
            let frame = (read + index / channelCount) % capacityFrames
            destination[index] = storage[frame * channelCount + index % channelCount]
        }
        readFrames.add(frames, ordering: .releasing)
        return .frames(frames)
    }

    /// 入力を止めて tap を外し、read が empty を返した後にだけ呼ぶ。記録列へ出していない末尾のあふれを返す
    public func takeTrailingOverrun() -> Int {
        let frames = openDropFrames
        openDropFrames = 0
        return frames
    }
}
```

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| 原子操作の方法 | `Synchronization` の `Atomic<Int>`（iOS 18 以降）。相手の側のカウンタは `acquiring` で読み、自分の側は `releasing` で進める | 標準ライブラリだけで書ける。相手が公開した分のデータは、カウンタを読んだ時点で見える |
| 中身 | ネイティブの sample rate・チャンネル数のまま、Float32 をフレーム単位に並べて持つ | コールバックの処理をコピーだけにする。モノラル化と変換は読み手の側で行う |
| 容量 | 5 秒分（48 kHz・1 チャンネルで 240,000 フレーム、約 0.9 MiB） | 取り出しの合図（100 ms）が大きく遅れても落とさない |
| あふれ | 書き込まずに、落とした位置（それまでに受け付けたフレームの累積）とフレーム数を組にする。**連続するあふれは 1 組にまとめる** | 基本設計 §2 |
| あふれの記録列 | 64 組を事前に確保する。組は、次に書き込みが成功する直前に記録列へ出す | 書き込めたフレームと同じ順序で読み手に渡る |
| 記録列が満杯のとき | **空きがあっても音声を受け付けず、開いている組にフレーム数を足し続ける** | 記録を失うと、空白区間の位置が狂う。記録列が満杯になるのは読み手が長く止まっているときだけで、失う音声は増えるが、位置は正しいままになる |
| 形式が変わったとき | 新しい形式で作り直す（再構築の中で、古い方を空にしてから。§5） | 容量とチャンネル数が形式で決まるため |

- 読み手は、あふれの組の位置より先を読まない。位置に着いたら、音声より先に `.overrun` を返す。未処理の音声が残る間に複数回あふれても、各空白区間は音声の間の正しい位置に入る（確かめた例は §11.3）。
- 入力を止めた後、記録列へ出していない末尾のあふれは `takeTrailingOverrun()` で読む。
- `@unchecked Sendable` を付けるのは、書き手と読み手の分担を型で表せないためである。**書き手の 3 つの関数に、ロック・メモリ確保・ログ・actor の呼び出しを足さない**（§10 の NS-R-02）。

### 4.2 入力の tap

```swift
public actor EngineAudioInput: AudioInput {
    private var engine: AVAudioEngine?

    // （prepare・stop は省略。prepare はエンジンを作り直し、入力ノードの形式を読む）

    public func start(sink: any CaptureSink) throws(AudioInputFailure) {
        guard let engine else { throw .noInputAvailable }
        let node = engine.inputNode
        let native = node.outputFormat(forBus: 0)
        let channels = Int(native.channelCount)
        // バッファ長は要求値で、届く長さは毎回読む（一定と仮定しない）
        let requested = AVAudioFrameCount(native.sampleRate / 10)
        do {
            try node.installAudioTap(onBus: 0, bufferSize: requested, format: nil) { buffer, _ in
                Self.copy(buffer, channels: channels, to: sink)
            }
            engine.prepare()
            try engine.start()
        } catch {
            node.removeTap(onBus: 0)
            self.engine = nil
            // OS の説明文は捨て、コードだけを残す（architecture.md §8）
            throw .engineStartFailed(osCode: Int32(truncatingIfNeeded: (error as NSError).code))
        }
    }

    public func stop() {
        guard let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
    }

    /// コールバックの中の処理はこの関数だけ。コピーとカウンタの更新しかしない（NFR-PRF-01）
    private static func copy(_ buffer: AVReadOnlyAudioPCMBuffer, channels: Int, to sink: any CaptureSink) {
        guard sink.beginWrite(frames: buffer.frameLength) else { return }
        for channel in 0 ..< channels {
            if case let .float(span) = buffer.channelData(channel) {
                span.withUnsafeBufferPointer { sink.writeChannel(channel, $0) }
            }
        }
        sink.endWrite()
    }
}
```

- **開始のたびに `AVAudioEngine` を作り直す。** 経路の変更・メディアサービスのリセットの後も同じ手順で済み、古いエンジンの状態を引き継がない。
- tap のバッファ長は要求値で 100 ms（SDK の説明では、受け付ける範囲は 100〜400 ms）。届く長さは毎回 `frameLength` を読む。
- tap に形式を渡さない（`format: nil`）。入力ノードのネイティブ形式のまま受け取る（BD-REC-01）。
- コールバックの中の処理は `copy` の 1 つの関数に閉じ込める。チャンネル数は開始時に読んでおき、コールバックの中で形式のオブジェクトに触れない。

### 4.3 取り出しと変換

取り出しは録音パイプライン（actor。[architecture.md](architecture.md) §7.2）が行う。合図のたび、または排出の指示で、次を繰り返す。

| 順 | 処理 |
| --- | --- |
| 1 | リングバッファから取り出す（事前に確保した作業用の配列へ。最大 0.5 秒分ずつ） |
| 2 | `.frames(n)` なら、全チャンネルの平均でモノラルにし、変換器へ渡す。出てきた 16 kHz・Int16 のサンプルを手順 4 へ |
| 3 | `.overrun(d)` なら、変換器の残りを吐き出して手順 4 へ渡し、変換器を作り直す。その位置（Chunk の組み立ての累積サンプル数）に、長さ `d × 1000 ÷ sample rate`（ms。切り捨て）の空白区間（理由 `overrun`）を開いてすぐ閉じる（§6） |
| 4 | 受け取ったサンプルを、入力レベルの計算（§4.6）と Chunk の組み立て（§4.4）へ渡す。空白区間が開いている間（再開の途中）は渡さずに保持する（§5 の `restart`） |
| 5 | `.empty` になったら終える |

```swift
public final class StreamConverter {
    private let converter: AVAudioConverter
    private let inputFormat: AVAudioFormat
    private let outputFormat: AVAudioFormat
    private let ratio: Double

    public init?(inputSampleRate: Int) {
        let rate = Double(inputSampleRate)
        let target = Double(AudioConstants.sampleRate)
        guard let input = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false),
              let output = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: target, channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: input, to: output)
        else { return nil }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        converter.sampleRateConverterAlgorithm = AVSampleRateConverterAlgorithm_Mastering
        self.converter = converter
        inputFormat = input
        outputFormat = output
        ratio = target / rate
    }

    // （convert は、渡したサンプルからその時点で出せる分を返す。flush は残りを吐き出す）
}
```

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| モノラル化 | 取り出しの段で、全チャンネルの平均を取る（変換器の機能は使わない） | 結果が決まっていて、単体テストで数値を確かめられる |
| 変換器の設定 | 品質は `AVAudioQuality.max`、方式は `AVSampleRateConverterAlgorithm_Mastering` | 「最高品質の設定」（BD-REC-03）。Mac 上の測定で TST-AUD-02 を満たした（§11.3） |
| 入力が足りないとき | 変換器へ「今はデータがない」と答える。残りは変換器の中に残り、次の取り出しで出てくる | 取り出しの大きさによらず、出力が同じになる |
| 吐き出し | 「入力の終わり」と答えて残りを受け取る。**吐き出した変換器は使わず、次の開始で作り直す** | 入力の形式ごとに変換器を 1 つ持つ（基本設計 §2）。旧形式のサンプルを新しい変換器に渡さない |
| 時刻 | 変換器が出したサンプルの数だけで数える（INV-08）。入力のフレーム数から計算しない | 変換の遅れ・端数で狂わない |

**形式が変わるときの手順**（基本設計 §5.1 の再構築の①〜④）: ①入力を止めて tap を外す → ②リングバッファを空になるまで取り出す（末尾のあふれを含む） → ③変換器を吐き出す → ④入力を作り直して形式を読み、新しい形式でリングバッファと変換器を作って開始する。①〜③が副作用 `stopInput`・`drainAndFlush`、④が `restart` である（§5）。

### 4.4 Chunk の組み立て

```swift
/// 確定する Chunk の中身（16 kHz・Int16・モノラル）
public struct AssembledChunk: Equatable, Sendable {
    public let sequence: ChunkSequence
    /// 録音開始からの累積サンプル番号（空白区間を含まない）
    public let startSample: Int64
    public let samples: [Int16]
}

public enum AssemblyStep: Equatable, Sendable {
    /// 組み立て中の Chunk の書きかけファイルへ追記する
    case append(sequence: ChunkSequence, startSample: Int64, samples: [Int16])
    /// 組み立て中の Chunk を確定する
    case seal(AssembledChunk)
    /// 連番の上限（999,999）を超えた。これ以上は組み立てられない
    case sequenceExhausted
}

/// Chunk の組み立て（純粋ロジック。FR-REC-01・03、TST-AUD-03）。
/// 入力の区切り方によらず、確定の要求がない限り各 Chunk はちょうど chunkSamples になる
public struct ChunkAssembler: Equatable, Sendable {
    public let chunkSamples: Int
    /// 受け取ったサンプルの累積（次に来るサンプルの番号）
    public private(set) var totalSamples: Int64 = 0
    public private(set) var nextSequence = 0
    private var pending: [Int16] = []

    public init(chunkSamples: Int = AudioConstants.chunkSamples) {
        self.chunkSamples = max(1, chunkSamples)
        pending.reserveCapacity(self.chunkSamples)
    }

    /// 組み立て中のサンプル数
    public var pendingCount: Int {
        pending.count
    }

    public mutating func append(_ samples: [Int16]) -> [AssemblyStep] {
        var steps: [AssemblyStep] = []
        var offset = 0
        while offset < samples.count {
            guard let sequence = ChunkSequence(nextSequence) else {
                steps.append(.sequenceExhausted)
                return steps
            }
            let take = min(chunkSamples - pending.count, samples.count - offset)
            let slice = Array(samples[offset ..< offset + take])
            let start = totalSamples - Int64(pending.count)
            steps.append(.append(sequence: sequence, startSample: start, samples: slice))
            pending += slice
            totalSamples += Int64(take)
            offset += take
            if pending.count == chunkSamples, let chunk = sealPartial() {
                steps.append(.seal(chunk))
            }
        }
        return steps
    }

    /// 停止・割り込みで、その時点までを短い Chunk にする。0 サンプルなら nil（Chunk を作らない）
    public mutating func sealPartial() -> AssembledChunk? {
        guard !pending.isEmpty, let sequence = ChunkSequence(nextSequence) else { return nil }
        let chunk = AssembledChunk(sequence: sequence, startSample: totalSamples - Int64(pending.count), samples: pending)
        pending.removeAll(keepingCapacity: true)
        nextSequence += 1
        return chunk
    }
}
```

- `append` の手順（`.append` は書きかけファイルへの追記、`.seal` は確定）を、録音パイプラインがそのまま保存（[storage.md](storage.md) §4）へ渡す。組み立て中の PCM は `pending` に持ち、確定のときに全体を渡す（ハッシュの計算と、書き込み失敗時の保持に使う）。
- 0 サンプルの Chunk は作らない（`sealPartial` が `nil` を返す）。
- 連番が上限（999,999。約 347 日分）を超えたら `.sequenceExhausted` を返す。録音パイプラインは、書き込み先がなくなった場合と同じく停止を要求する（停止理由 `disk_full`）。実用上は届かない。

### 4.5 WAV ヘッダ

```swift
/// 16 kHz・16 bit・モノラルの PCM の WAV ヘッダ（44 バイト。FR-REC-01）
public enum WAVHeader {
    public static let byteCount = AudioConstants.wavHeaderBytes

    /// 確定した Chunk のヘッダ。sampleCount は 1〜480,000
    public static func encode(sampleCount: Int) -> [UInt8] {
        let dataBytes = UInt32(sampleCount * AudioConstants.bytesPerSample)
        let byteRate = UInt32(AudioConstants.sampleRate * AudioConstants.bytesPerSample)
        var bytes: [UInt8] = []
        bytes.reserveCapacity(byteCount)
        bytes += Array("RIFF".utf8) + littleEndian(36 + dataBytes)
        bytes += Array("WAVE".utf8) + Array("fmt ".utf8) + littleEndian(UInt32(16))
        bytes += littleEndian(UInt16(1)) + littleEndian(UInt16(1))
        bytes += littleEndian(UInt32(AudioConstants.sampleRate)) + littleEndian(byteRate)
        bytes += littleEndian(UInt16(AudioConstants.bytesPerSample)) + littleEndian(UInt16(16))
        bytes += Array("data".utf8) + littleEndian(dataBytes)
        return bytes
    }

    /// 書きかけファイルの仮のヘッダ。data の大きさを 0 にしておく（正式な Chunk の検証には通らない）
    public static func provisional() -> [UInt8] {
        encode(sampleCount: 0)
    }

    /// 13 項目と、ファイルの大きさとの整合を検証する。通ればサンプル数を返す
    public static func validate(header: [UInt8], fileSize: Int64) -> Result<Int, WAVHeaderIssue> {
        guard header.count >= byteCount, fileSize >= Int64(byteCount) else { return .failure(.tooShort) }
        let signatures = text(header, 0) == "RIFF" && text(header, 8) == "WAVE"
        guard signatures, text(header, 12) == "fmt ", text(header, 36) == "data" else {
            return .failure(.badSignature)
        }
        let pcm = uint32(header, 16) == 16 && uint16(header, 20) == 1 && uint16(header, 22) == 1
        let rate = uint32(header, 24) == 16000 && uint32(header, 28) == 32000
        guard pcm, rate, uint16(header, 32) == 2, uint16(header, 34) == 16 else {
            return .failure(.unsupportedFormat)
        }
        let dataBytes = Int64(uint32(header, 40))
        let sizes = Int64(uint32(header, 4)) == 36 + dataBytes && fileSize == Int64(byteCount) + dataBytes
        guard sizes, dataBytes % 2 == 0 else { return .failure(.sizeMismatch) }
        guard dataBytes > 0 else { return .failure(.emptyData) }
        return .success(Int(dataBytes / 2))
    }

    // （リトルエンディアンの読み書きの補助関数は省略）
}
```

| 位置 | 項目 | 値 |
| --- | --- | --- |
| 0 | `RIFF` | 固定 |
| 4 | 全体の大きさ − 8 | 36 + data の大きさ |
| 8・12 | `WAVE`・`fmt ` | 固定 |
| 16 | fmt の大きさ | 16 |
| 20・22 | 形式・チャンネル数 | 1（PCM）・1 |
| 24・28 | sample rate・1 秒あたりのバイト数 | 16,000・32,000 |
| 32・34 | 1 フレームのバイト数・ビット数 | 2・16 |
| 36 | `data` | 固定 |
| 40 | data の大きさ | サンプル数 × 2 |

- 検証は 13 項目と、ファイルの大きさとの一致を見る（SEC-INT-02）。通らないファイルは文字起こしに渡さない。
- 書きかけファイルの仮のヘッダは data の大きさを 0 にする。正式な Chunk の検証には通らない（`emptyData`）ので、書きかけを誤って Chunk として扱うことがない。

### 4.6 簡易 VAD と入力レベル

```swift
/// 簡易 VAD の設計値（初期値。基本設計 §7）
public struct VADTuning: Equatable, Sendable {
    /// 1 フレームのサンプル数（160 = 10 ms）
    public var frameSamples = 160
    /// 0 に写す下限（dBFS）
    public var floorDecibels = -60.0
    /// 有声とみなすレベル（0〜1）
    public var threshold = 0.15
    /// 有声の後も有声として扱うフレーム数（20 = 200 ms）
    public var hangoverFrames = 20
    /// hasVoice にする有声フレーム数の下限（10 = 100 ms）
    public var minimumVoicedFrames = 10

    public init() {}
}
```

| 項目 | 定義 |
| --- | --- |
| レベル（0〜1） | 10 ms（160 サンプル）ごとの RMS を dBFS にし、−60 dBFS 以下を 0、0 dBFS を 1 に直線で写す |
| 有声 | レベルが 0.15（−51 dBFS）以上のフレーム。有声の後の 200 ms も有声として数える（ハングオーバー） |
| `vadScore` | Chunk の中の有声フレームの割合（0〜1） |
| `hasVoice` | ハングオーバーを除いた有声フレームが 10 以上（100 ms 以上） |
| 入力レベル（画面用） | 取り出した直近のブロックのレベル（同じ写し方） |

Minutes P1 §3.2 の方式（RMS の対数・しきい値・ハングオーバー）に合わせたが、参照元のリポジトリが手元にないため、数値は本書で定めた設計値である（§14）。MVP では表示と記録にだけ使い、音声を捨てる判断には使わない（INV-07）。

---

## 5. 録音セッションの状態遷移

満たす要件: FR-INT-01〜05、FR-REC-07、NFR-REL-05、SEC-PRV-02、TST-GEN-05・06 ／ 決定: BD-REC-04・05、BD-ARC-01

### 5.1 状態と段

基本設計 §5 の 6 つの状態を、処理の段で 9 つに分ける。画面と Live Activity に出すのは 6 つの状態だけである。

| 段（`RecordingPhase`） | 状態 | 意味 |
| --- | --- | --- |
| `idle` | `idle` | 録音していない |
| `preparing` | `preparing` | 入力を開始し、最初のバッファを待っている |
| `capturing` | `capturing` | 録音中 |
| `interrupted_draining` | `interrupted` | 割り込みの開始の処理中（排出・短い Chunk の確定・空白区間の開設） |
| `interrupted_waiting` | `interrupted` | 再開の許可、または利用者の操作を待っている |
| `interrupted_resuming` | `interrupted` | 再開の処理中 |
| `rebuilding_draining` | `rebuilding` | 再構築の前半（入力の停止・排出・変換器の吐き出し） |
| `rebuilding_restarting` | `rebuilding` | 再構築の後半（入力の作り直し） |
| `stopping` | `stopping` | 停止の処理中 |

```swift
/// 再開の一連のうち、どこで失敗したか
public enum RestartStep: String, Sendable, CaseIterable, LogSymbolSource {
    case activateSession = "activate_session"
    case startInput = "start_input"
    case cancelNotice = "cancel_notice"
    case closeGap = "close_gap"
}

public enum RecordingEvent: Equatable, Sendable {
    /// 同意確認・権限・会議の作成を終えた開始要求
    case startRequested
    case firstBufferArrived(Generation)
    case startFailed(Generation, RecordingStartFailure)
    case interruptionBegan(GapReason, EventTime)
    /// OS の再開の許可・前面への復帰・利用者の「再開」
    case resumeRequested
    /// OS が再開を勧めなかった
    case resumeDenied
    case configurationChanged(EventTime)
    case mediaServicesReset(EventTime)
    /// 入力の構成が変わらない経路の変更
    case routeChanged(RouteEventReason)
    case drainCompleted(Generation)
    case restartSucceeded(Generation)
    case restartFailed(Generation, RestartStep)
    case noInputFound(Generation)
    case stopRequested(StopReason)
    case stopCompleted(Generation)
}

/// サービスが順に実行する副作用の指示。reduce 自身は何も実行しない（BD-ARC-01）
public enum RecordingEffect: Equatable, Sendable {
    /// セッションを設定して有効化し、入力を開始する。最初のバッファを上限つきで待つ
    case beginCapture(Generation)
    /// 入力を止めて tap を外す（止まっていれば何もしない）
    case stopInput
    /// リングバッファが空になるまで取り出し、変換器の残りを吐き出す
    case drainAndFlush
    /// 組み立て中の音声を短い Chunk として確定する（0 サンプルなら何もしない）
    case sealPartialChunk
    case openGap(GapReason, EventTime)
    /// 開いている空白区間があれば、再開しなかったものとして閉じる
    case closeGapUnresumed
    /// 再開の一連: 有効化 → 入力の開始 → 通知の取り消し → 空白区間を閉じる → 保持したサンプルを渡す
    case restart(Generation)
    /// 再開した入力を止め、保持したサンプルを捨て、セッションを無効化する
    case abortRestart(scheduleNotice: Bool)
    case scheduleNotice
    case discardHeldSamples
    case deactivateSession
    case discardMeetingIfEmpty
    case recordRoute(RouteEventReason)
    case recordStartLatency
    case report(RecordingIssue)
    case reportStartFailure(RecordingStartFailure)
    /// 会議を stop_requested にする（会議の状態遷移へ渡す。architecture.md §5）
    case submitMeetingStop(StopReason)
    /// 前の指示をすべて実行し終えたら、この事象を自分に届ける
    case post(RecordingEvent)
}

/// 録音セッションの状態と、遷移の判断に要る最小の文脈
public struct RecordingSession: Equatable, Sendable {
    public var phase = RecordingPhase.idle
    public var generation = Generation()
    /// 割り込みの開始の処理中に届いた再開の要求（処理の完了まで保留する）
    public var resumeRequested = false
    /// 開いている空白区間の理由
    public var openGap: GapReason?
    /// 入力の開始の途中で届いた事象。開始に成功したら配り直す
    public var deferred: [RecordingEvent] = []

    public init() {}
}

public struct RecordingTransition: Equatable, Sendable {
    public let next: RecordingSession
    public let effects: [RecordingEffect]
}
```

### 5.2 遷移の表（全件）

**この表にない組み合わせと、世代の合わない完了は、すべて拒否する**（`reduce` が `nil` を返し、状態を変えない）。「世代+1」は、その遷移で世代を進めることを示す。完了の事象（`firstBufferArrived`・`startFailed`・`drainCompleted`・`restartSucceeded`・`restartFailed`・`noInputFound`・`stopCompleted`）は、現在の世代と一致するものだけを受け付ける。

| 段 | 事象 | 次の段 | 副作用の指示（順に実行する） |
| --- | --- | --- | --- |
| `idle` | `startRequested` | `preparing`（世代+1） | `beginCapture` |
| `preparing` | `firstBufferArrived` | `capturing` | `recordStartLatency` → `recordRoute(recording_started)` → 保留した事象を配り直す |
| `preparing` | `startFailed(分類)` | `idle` | `stopInput` → `deactivateSession` → `discardMeetingIfEmpty` → `reportStartFailure(分類)` |
| `preparing` | `interruptionBegan`・`configurationChanged`・`mediaServicesReset` | `preparing` | なし（事象を保留する） |
| `preparing` | `stopRequested(理由)` | `stopping`（世代+1。保留した事象を捨てる） | `stopInput` → `deactivateSession` → `discardMeetingIfEmpty` → `post(stopCompleted)`。開始の取り消し（基本設計 §4 の「`preparing` 中の停止要求」）で、停止の並びにも会議の `stop_requested` にも進まず、停止理由を記録しない。`stopInput` の前に、その世代の閉鎖の印（§5.4 の①）を**必ず**立てる（開く呼び出しが戻ったかによらない）。組み立て中の音声（印の前に受け付けたサンプル）も確定せずに捨てるので、その世代のサンプルは Chunk に入らず、会議は音声のないものとして削除される。開く呼び出しが戻っていなければ、`stopInput` は戻るのを待たずに §5.4 の②〜④を行い、後で戻った入力・セッションを閉じる。世代が進むので、遅れて届いた `firstBufferArrived`・`startFailed` は拒否されて `capturing` へ進まない。`stopCompleted` まで `startRequested` を受け付けず、その後の新しい開始の入力を開く呼び出しも、§5.4 の②の入力の制御の直列キューに並ぶので、遅れて戻った呼び出しの停止と取り外しより先には実行されない |
| `capturing` | `interruptionBegan(理由, 時刻)` | `interrupted_draining`（世代+1） | `scheduleNotice` → `stopInput` → `drainAndFlush` → `sealPartialChunk` → `openGap(理由, 時刻)` → `post(drainCompleted)` |
| `capturing` | `configurationChanged(時刻)` | `rebuilding_draining`（世代+1） | `stopInput` → `drainAndFlush` → `openGap(route_change, 時刻)` → `post(drainCompleted)` |
| `capturing` | `mediaServicesReset(時刻)` | `rebuilding_draining`（世代+1） | `stopInput` → `drainAndFlush` → `openGap(media_reset, 時刻)` → `post(drainCompleted)` |
| `capturing` | `routeChanged(理由)` | `capturing` | `recordRoute(理由)` |
| `capturing` | `stopRequested(理由)` | `stopping`（世代+1） | 停止の並び（下） |
| `interrupted_draining` | `drainCompleted`（再開の要求が保留されていない） | `interrupted_waiting` | なし |
| `interrupted_draining` | `drainCompleted`（再開の要求が保留されている） | `interrupted_resuming`（世代+1） | `restart` |
| `interrupted_draining` | `resumeRequested` | `interrupted_draining` | なし（再開の要求を保留する） |
| `interrupted_draining` | `resumeDenied` | `interrupted_draining`（保留した再開の要求を取り消す: `resumeRequested` を偽にする） | `report(interruptedNotResumed)`（取り消した後の `drainCompleted` は、保留のない行で `interrupted_waiting` へ進み、`restart` しない） |
| `interrupted_waiting` | `resumeDenied` | 変わらない | `report(interruptedNotResumed)` |
| `interrupted_draining`・`interrupted_waiting` | `configurationChanged`・`mediaServicesReset`・`routeChanged` | 変わらない | なし（再開のときに形式とセッションを読み直す） |
| `interrupted_draining`・`interrupted_waiting` | `stopRequested(理由)` | `stopping`（世代+1） | 停止の並び |
| `interrupted_waiting` | `resumeRequested` | `interrupted_resuming`（世代+1） | `restart` |
| `interrupted_resuming` | `restartSucceeded` | `capturing` | `recordRoute(resumed)` → 保留した事象を配り直す |
| `interrupted_resuming` | `restartFailed(段)` | `interrupted_waiting` | `abortRestart(通知の予約: 段が close_gap のときだけ)` → `report(interruptedNotResumed)` |
| `rebuilding_draining` | `drainCompleted` | `rebuilding_restarting`（世代+1） | `restart` |
| `rebuilding_draining` | `interruptionBegan` | `interrupted_draining`（世代+1） | `scheduleNotice` → `stopInput` → `drainAndFlush` → `sealPartialChunk` → `post(drainCompleted)`（空白区間は再構築の理由のまま） |
| `rebuilding_draining` | `configurationChanged`・`mediaServicesReset`・`routeChanged` | `rebuilding_draining` | なし |
| `rebuilding_draining` | `stopRequested(理由)` | `stopping`（世代+1） | 停止の並び |
| `rebuilding_restarting` | `restartSucceeded` | `capturing` | `recordRoute(configuration_changed または media_reset)` → 保留した事象を配り直す |
| `rebuilding_restarting` | `restartFailed(段)` | `interrupted_waiting` | `abortRestart(通知の予約: 行う)` → `report(rebuildFailed)` |
| `interrupted_resuming`・`rebuilding_restarting` | `noInputFound` | `stopping`（世代+1） | `report(inputLost)` → 停止の並び（理由 `no_input`） |
| `interrupted_resuming`・`rebuilding_restarting` | `interruptionBegan`・`configurationChanged`・`mediaServicesReset` | 変わらない | なし（事象を保留する） |
| `interrupted_resuming`・`rebuilding_restarting` | `stopRequested(理由)` | `stopping`（世代+1） | 停止の並び |
| `stopping` | `stopCompleted` | `idle` | なし |

**停止の並び:** `stopInput` → `drainAndFlush` → `discardHeldSamples` → `sealPartialChunk` → `closeGapUnresumed` → `submitMeetingStop(理由)` → `post(stopCompleted)`。受け付ける理由は `manual`・`disk_full`・`write_failed`・`no_input` の 4 つで（`write_failed` の並びは `disk_full` と同じ。違うのは記録する理由と利用者への文言だけ）、`terminated`・`interruption_unresumed` は起動時の復旧だけが使う（[storage.md](storage.md) §9）。`submitMeetingStop` は会議の状態遷移（[architecture.md](architecture.md) §5）へ事象 `stopRequested` を渡し、そこで停止理由の記録・中断通知の取り消し・セッションの無効化が指示される。**中断通知の取り消しは、どの段から停止しても必ず通る。**

表の行は 45 通り（段 × 事象の種類）で、テストはこの表を配列で持ち、全部の段 × 全部の事象を回す（§11.1）。

```swift
    private static func capturing(_ session: RecordingSession, _ event: RecordingEvent) -> RecordingTransition? {
        switch event {
        case let .interruptionBegan(reason, time):
            var next = session
            let generation = next.generation.advance()
            next.phase = .interruptedDraining
            next.openGap = reason
            next.resumeRequested = false
            let drain: [RecordingEffect] = [.scheduleNotice, .stopInput, .drainAndFlush, .sealPartialChunk]
            return RecordingTransition(next: next, effects: drain + [.openGap(reason, time), .post(.drainCompleted(generation))])
        case let .configurationChanged(time):
            return beginRebuild(session, reason: .routeChange, time: time)
        case let .mediaServicesReset(time):
            return beginRebuild(session, reason: .mediaReset, time: time)
        case let .routeChanged(reason):
            return RecordingTransition(next: session, effects: [.recordRoute(reason)])
        case let .stopRequested(reason):
            return stop(session, reason: reason)
        default:
            return nil
        }
    }

    /// 停止の順序（基本設計 §4）。停止理由は manual・disk_full・write_failed・no_input だけを受け付ける
    private static func stop(_ session: RecordingSession, reason: StopReason) -> RecordingTransition? {
        let accepted: [StopReason] = [.manual, .diskFull, .writeFailed, .noInput]
        guard accepted.contains(reason) else { return nil }
        var next = session
        let generation = next.generation.advance()
        next.phase = .stopping
        next.resumeRequested = false
        let drain: [RecordingEffect] = [.stopInput, .drainAndFlush, .discardHeldSamples, .sealPartialChunk]
        let finish: [RecordingEffect] = [.closeGapUnresumed, .submitMeetingStop(reason), .post(.stopCompleted(generation))]
        return RecordingTransition(next: next, effects: drain + finish)
    }
```

### 5.3 副作用の実行

`RecordingService`（actor）は、事象を受け取るたびに `reduce` を呼んで**その場で状態を変え**、返った副作用を待ち行列の末尾に足す。副作用は 1 本の流れで、足した順に 1 つずつ実行する。

| 規則 | 理由 |
| --- | --- |
| 状態は、事象を受け取った時点で変える（副作用の完了を待たない） | 停止の要求を保留しない。画面はすぐ「停止処理中」になる（基本設計 §5.1 の利用者の停止） |
| 副作用は足した順に実行し、並行して実行しない | 排出・確定・空白区間の開設が終わる前に、再開や停止の処理が録音パイプラインに触れない |
| 副作用の完了は、世代を持つ事象として届ける | 停止や次の再構築を始めた後に届いた古い完了を、新しい処理の完了として扱わない（TST-GEN-05） |
| 入力の開始の途中（`preparing`・`interrupted_resuming`・`rebuilding_restarting`）に届いた割り込み・構成の変更は保留し、開始に成功した直後に配り直す | 開始に成功した時点で入力がすでに止まっている場合に、気づかないまま `capturing` に居続けない |

| 副作用 | 実行すること | 失敗したとき |
| --- | --- | --- |
| `beginCapture(世代)` | セッションを設定して有効化する → 入力を作り直して形式を読む → リングバッファと変換器を作る → 入力を開始する → 取り出しを始める → 3 秒の時間切れを仕掛ける。最初のサンプルを取り出したら `firstBufferArrived(世代)` を届ける | `startFailed(世代, 分類)` を届ける |
| `stopInput` | 入力を止めて tap を外す。取り出しの合図を止める | 失敗しない |
| `drainAndFlush` | リングバッファが空になるまで取り出す（§4.3）。末尾のあふれを空白区間にする。変換器を吐き出す | 失敗しない（保存の失敗は [storage.md](storage.md) §7 の段階 3） |
| `sealPartialChunk` | 組み立て中の音声を短い Chunk として確定する。0 サンプルなら何もしない | 保存の失敗は段階 3（メモリに保持して続ける） |
| `openGap(理由, 時刻)` | メモリ上の空白区間の一覧に足す（位置 = 累積サンプル数、開始 = 時刻）。続けて DB と `meeting.json` に保存する | 保存の失敗は記録して続ける。メモリ上の一覧を正とし、次の Chunk の確定のたびに保存を再試行する |
| `closeGapUnresumed` | 開いている空白区間があれば閉じる（§6 の「再開しなかった」の行） | 同上 |
| `restart(世代)` | ①セッションを設定して有効化する → ②入力を作り直して開始する（サンプルは保持する） → ③中断通知を取り消す → ④空白区間を閉じる → ⑤保持したサンプルを Chunk の組み立てへ渡す。成功したら `restartSucceeded(世代)`。**途中の停止（基本設計 §5.1 の「割り込みの終了」「利用者の停止」の行）:** 停止の要求は `restart` の実行中にも受け付けて世代を進め（§5.2）、停止の並びは `restart` の後ろに並ぶ。`restart` は ②・③・④・⑤ の前（段の境目）と、呼び出しが戻った時点に世代を確かめ、進んでいれば残りの段を行わず、`restartSucceeded` を届けずに戻る（①・②の呼び出しの待機は `resumeStepTimeoutMs`、再構築の作り直しと入力の開始は `rebuildRestartTimeoutMs` までで、上限を超えたら §5.4 の入力の閉鎖の手続きを行って戻る。その世代のサンプルは捨てる）。②で入力を開く前に止めた場合は、保持したサンプルはなく、空白区間は開いたまま残り、後ろの `closeGapUnresumed` が停止時刻で閉じる。②で入力を開いた後に止めた場合は、`restart` の中で入力を止める → 受け付けたサンプルを取り出し終える → 空白区間を閉じる（再開後のサンプルが 1 つ以上あれば最初のサンプルで閉じ、長さは事象時刻から再開まで。なければ停止時刻で閉じる）→ 保持したサンプルと取り出したサンプルを Chunk の組み立てへ渡す、を行ってから戻る（停止の要求までに受け付けたサンプルは捨てない。Recording-First）。後ろの停止の並びの `discardHeldSamples` は捨てるものがなく、`closeGapUnresumed` は閉じる空白区間がなく、**`sealPartialChunk` は `restart` が渡したサンプルを含めて末尾の Chunk として確定する**。停止による世代の前進では §5.4 の閉鎖の印を立てない（印を立てるのは入力の閉鎖の手続きだけ。立てると、渡すと決めたサンプルが消費側で捨てられる） | 最初に失敗した段で止め、`restartFailed(世代, 段)` を届ける。②で使える入力がなければ `noInputFound(世代)`。世代が進んだ後の失敗は届けない（世代で捨てられる） |
| `abortRestart(通知の予約)` | 入力を止める → 保持したサンプルを捨てる → セッションを無効化する → 指定があれば中断通知を予約する | 失敗しない |
| `discardHeldSamples` | 保持したサンプルを捨てる（空白区間が開いたままなので、Chunk に入れない） | — |
| `scheduleNotice` | 中断通知を予約する（§7） | `report(noticeSchedulingFailed)`（録音には影響しない） |
| `submitMeetingStop(理由)` | 会議の状態遷移へ `stopRequested(理由)` を渡す | 状態の保存の失敗は [storage.md](storage.md) §5 |
| `report(問題)`・`reportStartFailure(分類)` | 画面へ問題を渡し、ログと計測値に記録する（[architecture.md](architecture.md) §8.2） | — |

- **保持するサンプルの上限は 10 秒分（160,000 サンプル）とする。** 超えた場合は、段 `close_gap` の失敗として扱う。
- `restart` の④が失敗した場合、③で通知を取り消し済みなので、`abortRestart` が同じ内容で予約し直す。①〜③で失敗した場合は予約が残っているので、予約し直さない（基本設計 §5.1）。再構築の失敗では、通知をまだ予約していないので、常に予約する。
- 再開（`restart`）は、利用者が開始した同じ会議に限る。新しい会議を作る経路を持たない。

### 5.4 待機の時間の上限・入力の閉鎖・割り込みの記録（基本設計 §5.1）

**待機の時間の上限**（申し送り）。どれも注入した `Scheduler` で測り（INV-08 の時刻には使わない）、`RecordingTuning` の値で差し替えられる。上限の後の扱いは基本設計 §5.1 のとおりである（受け付け済みのサンプルは捨てず、確定できない Chunk は未保存として保持し、停止・再開を止めない）。

| 待機 | 上限（設計値） | `RecordingTuning` |
| --- | --- | --- |
| 割り込みの開始の処理（停止要求がない場合を含む。排出・短い Chunk の確定・空白区間の開設と、割り込みの記録の永続化の完了の待機） | 5 秒 | `interruptionBeginTimeoutMs` |
| 再構築の④（作り直しと入力の開始の呼び出し） | 3 秒 | `rebuildRestartTimeoutMs` |
| 再構築の途中に届いた停止の待機（①〜③の完了。`rebuilding_draining` で停止を受け付けると世代が進み、遅れて届いた `drainCompleted` は拒否されるので `restart` は並ばない。停止の並びは①〜③の副作用の後ろに並び、それを終えたところで進む。`rebuilding_restarting` での停止は §5.3 の `restart` の「途中の停止」のとおり） | 5 秒 | `rebuildStopWaitTimeoutMs` |
| 割り込みの終了の①〜④の呼び出しの待機（1 つごと） | 3 秒 | `resumeStepTimeoutMs` |
| 停止の経路で空白区間を閉じる処理の待機 | 5 秒 | `stopGapCloseTimeoutMs` |
| 割り込みの終了の②の後・④の前に届いた停止での末尾の Chunk の確定の待機 | 5 秒 | `stopSealTimeoutMs` |
| 入力の閉鎖の手続き（下） | 2 秒 | `inputCloseTimeoutMs` |

- 値は初期値で、TST-DEV-01・02 の結果で見直す（§14）。排出の量はリングバッファの容量（5 秒分）で上限があり、上限を待つ間も取り出しと変換は止めない。

**入力の閉鎖の手続き**（申し送り。入力を開く呼び出しが上限までに戻らない場合。基本設計 §5.1）:

| 段 | 手段 |
| --- | --- |
| ① 閉鎖済みにする | 録音パイプラインの世代ごとの閉鎖の印（`Synchronization.Atomic` の世代の番号。リングバッファと同じく原子操作）を立てる。tap のブロックは書き込みの前に印を読み、閉鎖済みの世代のバッファをリングバッファに入れずに捨てる（これは量を減らすための早い除外で、保証は次の消費側で行う。印を立てる前に印を読んだ tap は、立てた後にリングバッファへ書きうる）。**保証は消費側の破棄で行う:** リングバッファは世代ごとに作る（§5.3 の `beginCapture`）ので、取り出したサンプルの世代はリングバッファで決まる。取り出し側は、Chunk の組み立てに渡す直前に、そのリングバッファの世代の印を確かめ、閉鎖済みなら渡さずに捨てる。この「印の確認 → Chunk の組み立てへの追加」と①の印の設定は、同じ排他（`Synchronization.Mutex`。取り出し側と①だけが取り、tap の音声コールバックは取らない）の中で行う。①は、取り出し側が 1 回分の追加（メモリ上の操作だけ）を終えるのを待つことはあるが、戻っていない入力の呼び出しは待たない。①が戻った後は、その世代のサンプルは、tap がいつ書いたものでも Chunk に入らない。閉鎖済みの世代のリングバッファに残ったサンプルも同じく捨てる |
| ② 停止の指示 | `AudioInput.close(generation:)` が、入力の制御の直列キュー（入力を開く呼び出しと同じキュー）の外から、その世代の tap の取り外しと入力の停止を指示する。開く呼び出しが戻っていなければ、戻った直後に同じキューで停止と取り外しを行う予約になる |
| ③ 閉じたことの確認 | `AudioInput.isClosed(generation:)` が、その世代の入力が止まり（`isRunning` が偽）、tap が外れていることを返す。入力の制御のキューで確かめるので、開く呼び出しが戻っていなければ確かめられない |
| ④ セッションの無効化 | ③を確かめた後にだけ行う |

- 手続きの上限は 2 秒（`inputCloseTimeoutMs`）。上限までに③を確かめられなければ、④を行わずに `interrupted`・停止・`idle` へ進み、問題 `microphone_close_unconfirmed`（マイクを閉じられたことを確かめられない）を表示してログに記録する。③の確認は、開く呼び出しが戻った時点で続け、確かめた時点で④を行う（残りの後始末）。
- 後始末が終わるまで、新しい録音・次の再開を始めない（§3.4 の手順 6）。手続きと後始末は元の世代のものだけを対象にする。
- 戻っていない呼び出しの外から入力を止められるか（②）は OS の実装による。実機で確かめる（§14）。

**割り込みの記録**（申し送り）:

| 項目 | 設計 |
| --- | --- |
| 形式と置き場所 | `meeting.json` の `pendingInterruption`（`PendingInterruptionRecord`: 識別子（UUID）・事象時刻（壁時計）・理由。開始位置は持たない。[storage.md](storage.md) §3.1）。DB には置かない（起動時の復旧は `meeting.json` を読む。[storage.md](storage.md) §9） |
| 書く時点 | 割り込みの通知を受け取り、事象時刻を記録して中断通知を予約した直後、排出・確定より前に、保存の actor のメモリ上の状態に入れて `meeting.json` の新しい世代の書き込みを依頼する（[storage.md](storage.md) §5.1）。排出と変換は永続化の完了を待たない |
| 永続化できなかった場合 | 記録は保存の actor のメモリ上の状態に残る。`meeting.json` はファイル全体の世代で書くので、再試行は「新しい世代の書き込み」である（時間の上限による失敗・失敗の後に新しい世代で書き直す。[storage.md](storage.md) §5.1）。割り込みの開始の処理の上限（5 秒）までに永続化が終わらなくても、停止・再開へ進む |
| 空白区間の記録で置き換える取引 | 排出が終わって開始位置が決まったら、保存の actor が 1 つのメソッドの中で、メモリ上の状態から `pendingInterruption` を除き、同じ識別子（`interruptionId`）を持つ空白区間を足す。続けて ①DB の取引（`recording_gaps` の行（`interruption_id` つき）と `meetings.total_samples` の更新）→ ②その状態の全体から `meeting.json` の新しい世代を書く（記録の削除と空白区間の追加が 1 つのファイルの置き換えで同時に起きる） |
| 起動時の復旧での扱い | `pendingInterruption` があり、同じ識別子の空白区間が DB と `meeting.json` のどちらにもなければ、「開始位置が未確定の割り込みの記録」として基本設計 storage.md §8 の規則で空白区間を作る。同じ識別子の空白区間があれば、記録だけを除く（①の後・②の前に落ちた場合） |

---

## 6. 空白区間

満たす要件: FR-INT-01・02、FR-REC-04、FR-TRV-03、INV-08 ／ 決定: BD-REC-04、BD-ARC-04

理由の一覧（`GapReason`。[architecture.md](architecture.md) §8.1 の `RecordingIssue` と同じ型）:

| 理由 | 起きるとき | 通知の予約 | 短い Chunk の確定 |
| --- | --- | --- | --- |
| `interruption` | 他のアプリやシステムの音声（着信・Siri・アラームなど）で、セッションが無効化された | する | する |
| `built_in_mic_muted` | 内蔵マイクが消音された | する | する |
| `route_disconnected` | 経路の切断で無効化された（§3.2 の設定で避けているが、届いた場合） | する | する |
| `overrun` | リングバッファがあふれた | しない | しない |
| `route_change` | 入力の構成が変わり、入力を作り直した | 失敗したときだけ | しない |
| `media_reset` | メディアサービスがリセットされ、入力を作り直した | 失敗したときだけ | しない |

| 項目 | 決め方 |
| --- | --- |
| 位置 | 空白の直後に来るサンプルの番号（＝開いた時点の、Chunk の組み立ての累積サンプル数）。閉じても変えない |
| 開始時刻（表示用） | 事象を受け取った時刻（壁時計）。`overrun` は、あふれを取り出した時刻 |
| 長さ（再開した） | 閉じた時点の単調時計 − 事象を受け取った時点の単調時計（ms） |
| 長さ（`overrun`） | 落としたフレーム数 × 1000 ÷ ネイティブの sample rate（ms。切り捨て）。時計を使わない |
| 長さ（再開しないまま停止した） | 停止を受け付けた時点の単調時計 − 事象を受け取った時点の単調時計。「再開したか」は「いいえ」 |
| 長さ（開いたままプロセスが終了した） | 不明（`nil`）。起動時の復旧が閉じる（[storage.md](storage.md) §9） |
| 同時に開く数 | 1 つまで。開いている間に届いた割り込み・構成の変更では、新しい空白区間を開かない（§5 の表） |

- 割り込みの理由（`interruption`・`built_in_mic_muted`・`route_disconnected`）では、空白区間は必ず Chunk の境界にある（開く前に短い Chunk を確定するため）。`overrun`・`route_change`・`media_reset` の空白区間は、Chunk の途中にありうる。文字起こしの Overlap の判定（[transcription.md](transcription.md) §4.2）は、Chunk の開始位置にある空白区間だけを見る。
- 空白区間の一覧は、録音中は録音パイプラインがメモリに持つ（これが正）。保存は DB の `recording_gaps` と `meeting.json`（[storage.md](storage.md) §3）で、保存に失敗しても録音を止めない。

---

## 7. 中断通知

満たす要件: FR-INT-02、SEC-PLT-07、NFR-UX-03 ／ 決定: BD-REC-05

| 項目 | 値 |
| --- | --- |
| 識別子 | `recording-interruption`（固定。同じ識別子で予約すると、前の予約を置き換える） |
| 題名 | 「録音が中断しています」 |
| 本文 | 「アプリを開くと再開を試みます。」 |
| 予約までの時間 | 60 秒（設計値。`RecordingTuning.interruptionNoticeDelaySeconds`） |
| 音 | 既定の通知音 |
| 重要度 | 既定（`active`）。Time Sensitive は Personal Team で使えない（基本設計 [architecture.md](../02-basic-design/architecture.md) §1） |
| 取り消し | 未配信の予約を消す（`removePendingNotificationRequests`）。配信済みの通知も消す（`removeDeliveredNotifications`） |

- 文言は String Catalog の固定の文で、**差し込む値を持たない**（会議タイトル・経過時間を入れない。SEC-PLT-07）。`InterruptionNoticeScheduling` の引数は秒数だけである。
- 通知の権限がない場合、`schedule` は `false` を返す。録音は続け、画面にお知らせ（`notifications_denied`）を出す。権限の要求は初回説明で行う（[ui.md](ui.md) §4.1）。
- 短い割り込み（60 秒以内に再開できた）では、取り消しが間に合うので通知は出ない。
- 予約は、割り込みの開始の副作用の先頭に置く（§5.2）。排出や確定の途中でアプリがサスペンドされても、通知が届く。

---

## 8. 入力経路の記録と計測値

満たす要件: FR-REC-08、NFR-PRF-02、NFR-PWR-01、INV-09 ／ 決定: BD-REC-07

`recordRoute(理由)` は、次の 5 つを会議のメタデータ（DB の `route_events` と `meeting.json`。[storage.md](storage.md) §3）に足す: サンプル番号（累積）、入力の種別（§2）、ネイティブの sample rate、チャンネル数、理由（`RouteEventReason`）。機器の名前は記録しない。入力経路は話者ではない（INV-09）。

| 計測値（`usage_metrics`） | 単位 | 記録する時点 |
| --- | --- | --- |
| 開始から取り込み開始までの時間（ms） | 録音ごと | `recordStartLatency` |
| リングバッファのあふれ（回数・フレーム数） | 録音ごと | 停止時 |
| 単調時計の経過時間と「総サンプル数 ÷ 16,000 + 空白区間の合計」の差（ms） | Chunk ごと | Chunk の確定時（補正はしない。TST-DEV-01 ③） |
| 割り込みの回数、再開できなかった回数と理由 | 録音ごと | 停止時 |
| 電池残量・温度状態 | 開始時・停止時と Chunk ごと | Chunk の確定時 |

計測値は数値と閉じた列挙型だけで、D1〜D3 を含めない。

---

## 9. 画面へ渡す値

満たす要件: FR-REC-06、FR-SAV-05、INV-08 ／ 決定: BD-UI-02

録音パイプラインは、次の値を持つスナップショット（`RecordingSnapshot`。`Sendable` の値型）を `RecordingService` 経由で Store へ流す（[ui.md](ui.md) §5.2）。

| 項目 | 内容 |
| --- | --- |
| `state` | 録音セッションの状態（6 つ） |
| `totalSamples` | 累積サンプル数（16 kHz） |
| `closedGapTotalMs` | 閉じた空白区間の長さの合計。開いている空白区間は含めない |
| `inputLevel` | 入力レベル（0〜1） |
| `savedChunks`・`unsavedChunks` | 保存済みの Chunk の数、メモリ上の未保存の Chunk の数 |
| `route` | 入力の種別 |

- 流すのは、状態が変わったときと、前回から 1,600 サンプル（100 ms 分）以上進んだときである。**間引きはサンプル数で数え、タイマーを使わない**（INV-08）。毎秒 10 回を超えない。
- 経過時間は Store が `AudioTimeline.elapsedMs`（[architecture.md](architecture.md) §4）で導く。録音パイプラインは経過時間そのものを渡さない。

---

## 10. セキュリティ

満たす要件: SEC-PRV-01・02、SEC-LOG-03・04、SEC-PLT-07 ／ 脅威: T-04・05・06

| 観点 | 設計 | 要件・脅威 |
| --- | --- | --- |
| 同意のない録音 | 開始は `ConsentConfirmation` を引数に取り、その値は同意確認の画面でしか作れない（§2） | SEC-PRV-02、T-06 |
| マイクを開く範囲 | 有効化は `beginCapture` と `restart` だけ。開始の失敗・再開の失敗・停止のすべての行で、入力の停止と無効化が指示される（§5.2） | SEC-PRV-02 |
| 録音中であることの明示 | スナップショットの状態を画面と Live Activity に出す（§9）。中断は通知で知らせる（§7） | SEC-PRV-02、NFR-UX-03 |
| ロック画面に出る文 | 通知の文言は固定で、差し込む値を持たない（§7） | SEC-PLT-07、T-04 |
| 経路の情報 | 種別だけを記録する。機器の名前と識別子を読まない（§2） | SEC-LOG-03 の趣旨 |
| 音声データ | リングバッファ・変換器・Chunk の組み立ての外へ出さない。ログと計測値は数値と列挙型だけ | SEC-LOG-03 |
| OS のエラー | 説明文を捨て、コードだけを残す（`engineStartFailed(osCode:)` など） | SEC-LOG-04 |
| 利用目的の文言 | `NSMicrophoneUsageDescription`（[dev-environment.md](dev-environment.md) §12.1） | SEC-PRV-01 |

**実装・レビュー時の注意点**（基本設計 [architecture.md](../02-basic-design/architecture.md) §12.2 の NS-03・06・12 と、基本設計 §12 の注意点を、本書の型の単位に具体化したもの）:

| No. | 注意点 | 確かめ方 |
| --- | --- | --- |
| NS-R-01 | `ConsentConfirmation.confirmedByUser` を呼ぶのは、同意確認の Store の 1 か所だけにする。UI テストの差し替えでも呼ばず、同意確認の画面を通す | レビュー。`grep` で呼び出し箇所を数える |
| NS-R-02 | コールバックから呼ばれる関数（`EngineAudioInput.copy` と、`CaptureRingBuffer` の `beginWrite`・`writeChannel`・`endWrite`）に、ロック・メモリ確保・ログ・actor の呼び出し・ファイル I/O を足さない | レビュー（対象はこの 4 つの関数だけ）。TST-DEV-01 |
| NS-R-03 | 録音の経路（`MinutesAudio`）から文字起こしの型を参照しない | ビルド（ターゲットの依存にない。INV-01） |
| NS-R-04 | 経路の記録で `portName`・`uid` を読まない。読むのは `portType` だけ | レビュー |
| NS-R-05 | 通知・Live Activity に渡す値に、文字列の引数を足さない | レビュー、単体（protocol の引数が秒数だけであること） |
| NS-R-06 | セッションを有効化する経路を足すときは、失敗のすべての分岐で無効化されることを、遷移の表の試験に足す | 単体（§11.1 の 9・10） |
| NS-R-07 | 再開で新しい会議を作らない。`restart` は会議 ID を引数に取らない | レビュー |
| NS-R-08 | 試験用の音声は合成する。実機で録った音声をリポジトリと試験記録に入れない | レビュー、`.gitignore`（`*.wav`） |

---

## 11. テスト観点

満たす要件: TST-AUD-01〜06、TST-INT-01〜05・07、TST-GEN-01・05・06・08、TST-DEV-01・02・04、NFR-PRF-01

### 11.1 先に書いて失敗させるテスト（録音の実装の順）

| 順 | テスト | 確かめること | 要件 |
| --- | --- | --- | --- |
| 1 | WAV ヘッダの配置 | 44 バイト・13 項目の値。サンプル数 480,000 で、全体の大きさが 960,036、data が 960,000 | TST-AUD-01 |
| 2 | WAV ヘッダの検証 | 大きさの不一致・形式の違い・data が 0・44 バイト未満を、それぞれの分類で拒否する | SEC-INT-02 |
| 3 | Chunk の境界 | 入力を不規則な長さ（1 サンプル・Chunk より長いブロックを含む）で流し、末尾以外がちょうど 480,000、欠落と重複がないこと。確定の要求を混ぜても、開始位置が連続すること。0 サンプルの確定が Chunk を作らないこと | TST-AUD-03 |
| 4 | 簡易 VAD | 無音が 0・`hasVoice` 偽、既知の振幅の正弦波が計算どおりのレベルになること | FR-REC-03 |
| 5 | リングバッファの順序 | 未処理の音声が残る間に 2 回あふれさせ、あふれの記録が音声の間の正しい位置に出ること。連続するあふれが 1 組になること | NFR-PRF-01 |
| 6 | 記録列の満杯 | 記録列が満杯の間、空きがあっても受け付けずに数え続けること | NFR-PRF-01 |
| 7 | 折り返しと通過帯域 | 48 kHz・44.1 kHz の 12 kHz 正弦波で、4 kHz の成分が −40 dB 以下。1 kHz・3 kHz の振幅が ±0.5 dB 以内。取り出しの大きさを変えても結果が同じ | TST-AUD-02 |
| 8 | 遷移の表の網羅 | 9 つの段 × 15 種類の事象。表の 45 通りだけが許されること | TST-INT-01〜05、TST-GEN-06 |
| 9 | 古い世代 | 世代の合わない完了が、どの段でも拒否されること。入力の閉鎖の手続きの①の後に、閉鎖した世代のリングバッファへ書いたサンプル（印を立てる前に印を読んだ tap の書き込みを模す）が、Chunk に入らずに捨てられること | TST-GEN-05 |
| 10 | 割り込みの開始の並び | 通知の予約が先頭、空白区間の開設が短い Chunk の確定の後にあること。排出の途中で届いた再開の要求が保留され、排出の完了で再開が始まること。保留の後に `resumeDenied` が届くと保留が取り消され、`interruptedNotResumed` が報告され、排出の完了で `restart` せずに `interrupted_waiting` へ進むこと | TST-INT-01 |
| 11 | 再開の失敗 | 4 つの段のそれぞれで失敗させ、入力の停止・保持分の破棄・無効化が指示されること。通知の予約し直しが `close_gap` のときだけであること | TST-INT-02 |
| 12 | 停止の並びと理由 | `capturing`・`interrupted_waiting`・`rebuilding_draining`・`rebuilding_restarting` のそれぞれから、同じ並びの停止が指示されること。`interrupted_resuming`・`rebuilding_restarting` で停止した場合（結合）: ②の後に再開後のサンプルを届けてから停止すると、残りの段が行われず `restartSucceeded` が届かず、そのサンプルが末尾の Chunk に入り、空白区間が再開後の最初のサンプルで閉じること。②の前に停止すると、空白区間が停止時刻で閉じ、新しい入力のサンプルが Chunk に入らないこと。`.stopRequested(.manual)`・`.stopRequested(.diskFull)`・`.stopRequested(.writeFailed)`・`.stopRequested(.noInput)` を受け付け、`submitMeetingStop` に同じ理由が渡ること（`write_failed` の並びが `disk_full` と同じであること）。`terminated` を受け付けないこと | TST-INT-07 |
| 13 | 入力の喪失 | 再構築で使える入力がなければ、停止理由 `no_input` で停止の並びが指示されること | TST-INT-04 |
| 14 | 保留した事象の配り直し | 入力の開始の途中で届いた割り込みが、開始の成功の後に配り直されること | TST-INT-01・05 |
| 15 | sample rate の変化（結合） | 合成音声を 48 kHz で流し、途中で 24 kHz に変えて再構築する。出力のサンプル番号が連続し、空白区間が 1 つ記録されること | TST-AUD-05 |
| 16 | 時刻の正（結合） | 録音の途中で壁時計を進める・戻す。Chunk の位置が変わらないこと | TST-AUD-06 |
| 17 | 割り込みと再開（結合） | 差し替えたセッションで割り込みを流す。割り込み前の音声が短い Chunk になり、再開後のサンプルが次の連番の Chunk に入り、空白区間の長さが手動の時計で進めた分になること | TST-INT-01、FR-INT-01 |
| 18 | 開始の失敗・開始の取り消し（結合） | 入力の開始を失敗させる・最初のバッファを届けない。`idle` に戻り、無効化が呼ばれ、会議が残らないこと。`preparing` で停止を要求すると、`stop_requested` を経ずに同じ後始末で `idle` に戻り、その後に届いた最初のバッファ・開始の失敗が拒否されること。入力の開始の呼び出しを戻さないまま停止すると、閉鎖の手続きが行われ、後始末の前の `startRequested` が拒否されること | SEC-PRV-02 |
| 19 | 単体での再生（結合） | 保存した Chunk を `AVAudioFile` で開き、長さが記録と一致すること | TST-AUD-04 |

### 11.2 合成音声と差し替え

| 部品（`MinutesTestSupport`） | 内容 |
| --- | --- |
| `SyntheticAudio.sine(frequency:amplitude:sampleRate:frames:)` | 既知の周波数・振幅・長さの正弦波（Float32） |
| `SyntheticAudio.silence(frames:)`・`whiteNoise(seed:frames:)` | 無音と、種を固定した雑音（同じ種から同じ値。TST-GEN-07） |
| `ScriptedAudioInput` | 形式と、ブロックの大きさの並びを指定して、合成音声を `sink` へ書く。`prepare`・`start` の失敗を指定できる |
| `FakeAudioSession`・`NoticeSchedulerSpy` | §3.1 |

- 取り出しは、テストが `drainOnce()` を直接呼んで進める（[architecture.md](architecture.md) §7.2）。合図も実時間も待たない。
- 合否は数値で決める（振幅・サンプル数・位置）。聞いて確かめる試験にしない。
- 日本語の発話の合成音声（TST-STT-07 用）は実機で作る（[transcription.md](transcription.md) §11.2）。リポジトリには入れない。

### 11.3 実装前に確かめたこと（2026-11-16）

| 項目 | 結果 |
| --- | --- |
| ビルド | 本書のコード片（§2〜§5）を一時の Package に置き、Swift 6.4.2・Swift 6 言語モード・警告をエラーとして扱う設定でビルドできた |
| アプリ側のコード | `SystemAudioSession`（§3.3）を、既定の分離を MainActor にした設定で、iOS 27.2 SDK に対してビルドできた（`xcodebuild` の `generic/platform=iOS`） |
| テスト | 15 件が通った（WAV・Chunk の境界・VAD の 3 件、遷移の表・世代・副作用の並びの 6 件、リングバッファの 2 件、変換の 3 件、待ち合わせの差し替えの 1 件ほか） |
| 遷移の表 | 9 段 × 15 種類の事象のうち、許されるのは 45 通りで、§5.2 の表と一致した |
| リングバッファ | 容量 8,000 フレームで、6,000 を書いた後に 3,000 と 2,500 を続けて落とし、64 を読んでから 50 を書き、4,000 を落とした。読み手には「6,000 フレーム → あふれ 5,500 → 50 フレーム」の順で届き、末尾のあふれ 4,000 は入力の停止後に読めた |
| 変換（Mac 上の `AVAudioConverter`） | 48 kHz・44.1 kHz の 12 kHz 正弦波（振幅 0.5）で、出力の 4 kHz の成分は Int16 の量子化で 0 になった（−40 dB の基準を満たす）。1 kHz・3 kHz の振幅の差は ±0.001 dB 以内。2 秒の入力から、ちょうど 32,000 サンプルが出た |

**設計・実装時の注意点:**

- 遷移の試験は、通知の順序が入れ替わった場合（終了が先に届く、同じ通知が 2 回届く）を含める。2 回目の通知は、表にない組み合わせとして拒否される。
- 変換の測定は Mac 上の結果である。実機の変換器でも同じ試験を通す（TST-AUD-02 はシミュレータと実機の両方で実行する）。
- リングバッファの試験は 1 本のスレッドで順序を確かめるものである。書き手と読み手が同時に動く場合の正しさは、原子操作の順序づけ（§4.1）と実機の長時間録音（TST-DEV-01 ③。記録のない欠落がないこと）で確かめる。

### 11.4 シミュレータで確かめられないこと

| 事項 | 単体での扱い | 実機での確認 |
| --- | --- | --- |
| 割り込みの通知（iOS 27 の 2 つの通知）が届く順序と内容 | 事象を差し替えて、あらゆる順序を流す | TST-DEV-02（着信に出る / 出ない・Siri・アラーム）。届いた通知と理由を記録する |
| 経路の切断で録音が続く | 構成の変更の事象を流す | TST-DEV-04 |
| ロック中・バックグラウンドで 60 分録音できる | — | TST-DEV-01 |
| 中断通知が届く | 予約と取り消しの呼び出しを記録する | TST-DEV-02・12 |
| 失敗の後に OS の録音インジケータが消える | 無効化の呼び出しを記録する | TST-DEV-02 |

---

## 12. 要件 ID ↔ テストの対応表

満たす要件: TST-GEN-03 ／ 決定: UD-06

状態はすべて「未実装」である（実装の作業で、テスト名と状態を書き換える）。

| 要件 | テスト（Suite / 表示名の先頭） | 置き場所 | 状態 |
| --- | --- | --- | --- |
| FR-REC-01、TST-AUD-01 | 録音の純粋ロジック / TST-AUD-01 WAV ヘッダは 44 バイトで… | MinutesDomainTests | 未実装 |
| SEC-INT-02 | 録音の純粋ロジック / SEC-INT-02 不正なヘッダは分類して拒否される | MinutesDomainTests | 未実装 |
| FR-REC-01・03、TST-AUD-03 | 録音の純粋ロジック / TST-AUD-03 入力の区切り方によらず… | MinutesDomainTests | 未実装 |
| FR-REC-03、INV-07 | 録音の純粋ロジック / FR-REC-03 無音は 0、フルスケールの正弦波は有声になる | MinutesDomainTests | 未実装 |
| FR-REC-02、TST-AUD-02 | 変換 / TST-AUD-02 折り返しの成分と通過帯域の振幅を測る | MinutesAudioTests | 未実装 |
| FR-REC-01、TST-AUD-04 | 録音パイプライン / TST-AUD-04 保存した Chunk を単独で開ける | MinutesAudioTests | 未実装 |
| FR-REC-02・04、TST-AUD-05 | 録音パイプライン / TST-AUD-05 sample rate が変わっても位置が連続する | MinutesApplicationTests | 未実装 |
| FR-REC-04、INV-08、TST-AUD-06 | 録音パイプライン / TST-AUD-06 壁時計を動かしても Chunk の位置は変わらない | MinutesApplicationTests | 未実装 |
| NFR-PRF-01 | リングバッファ / NFR-PRF-01 未処理の音声が残る間に 2 回あふれても… | MinutesAudioTests | 未実装 |
| FR-INT-01・02、TST-INT-01 | 録音セッションの状態遷移 / TST-INT-01 表にある遷移は許され… | MinutesDomainTests | 未実装 |
| FR-INT-02、TST-INT-02 | 録音セッションの状態遷移 / TST-INT-02 空白区間を閉じる段で再開に失敗したら… | MinutesDomainTests | 未実装 |
| FR-INT-03、FR-REC-08、TST-INT-03 | 録音セッションの状態遷移 / TST-INT-03 構成の変わらない経路の変更は記録だけを指示する | MinutesDomainTests | 未実装 |
| FR-INT-03・05、TST-INT-04 | 録音セッションの状態遷移 / TST-INT-04 使える入力がなければ no_input で停止する | MinutesDomainTests | 未実装 |
| FR-INT-04、TST-INT-05 | 録音セッションの状態遷移 / TST-INT-05 リセットの後は作り直して再開する | MinutesDomainTests | 未実装 |
| FR-INT-05、TST-INT-07 | 録音セッションの状態遷移 / TST-INT-07 停止は、排出・保持分の破棄… | MinutesDomainTests | 未実装 |
| FR-INT-06、TST-INT-06 | 録音の開始 / TST-INT-06 権限がなければ microphoneDenied を返す（画面の導線は [ui.md](ui.md) §11） | MinutesApplicationTests | 未実装 |
| FR-REC-07 | 録音の開始 / FR-REC-07 録音中の開始は alreadyRecording を返す | MinutesApplicationTests | 未実装 |
| FR-CNS-02 | 録音の開始 / FR-CNS-02 同意確認の時刻が会議に記録される | MinutesApplicationTests | 未実装 |
| FR-REC-06 | 録音パイプライン / FR-REC-06 スナップショットは 1,600 サンプルごとに流れる | MinutesApplicationTests | 未実装 |
| NFR-PRF-02 | 録音の開始 / NFR-PRF-02 開始から最初のバッファまでの時間が記録される | MinutesApplicationTests | 未実装 |
| SEC-PRV-02 | 録音の開始 / SEC-PRV-02 開始に失敗したら無効化され、会議が残らない | MinutesApplicationTests | 未実装 |
| SEC-PLT-07 | 中断通知 / SEC-PLT-07 予約の引数は秒数だけである | MinutesApplicationTests | 未実装 |
| TST-GEN-05 | 録音セッションの状態遷移 / TST-GEN-05 世代の合わない完了は… | MinutesDomainTests | 未実装 |
| TST-GEN-08 | 合成音声 / TST-GEN-08 同じ種から同じ雑音ができる | MinutesAudioTests | 未実装 |
| FR-REC-05、NFR-REL-01、NFR-PWR-01 | 実機試験 TST-DEV-01 | 実機 | 未実施 |
| FR-INT-01・02 | 実機試験 TST-DEV-02 | 実機 | 未実施 |
| FR-INT-03 | 実機試験 TST-DEV-04 | 実機 | 未実施 |
| SEC-PRV-01 | 設定の検査（[dev-environment.md](dev-environment.md) §8） | 静的解析 | 作業単位 2 の後 |

---

## 13. 基本設計の申し送りの解決

満たす要件: NFR-MNT-04

基本設計 [recording.md](../02-basic-design/recording.md) §14 の 8 項目。

| 申し送り | 解決 | 節 |
| --- | --- | --- |
| リングバッファの実装（原子操作の方法）と、取り出しを起こす合図の方式。あふれの記録列の容量と、記録列自体があふれた場合の扱い | `Synchronization.Atomic` のカウンタで、書き手と読み手が自分の側だけを進める。合図は周期 100 ms（architecture.md §7.2）。記録列は 64 組で、満杯の間は音声も受け付けずに数え続ける | §4.1 |
| 変換器の設定値、モノラル化の方法、入力形式が変わるときの吐き出しの手順 | 品質は最大・方式は Mastering。モノラル化は取り出しの段で全チャンネルの平均。形式が変わるときは、排出 → 吐き出し → 作り直しの順で、吐き出した変換器は使わない | §4.3 |
| 状態遷移の表（状態 × 事象 × 副作用）の全件 | 9 つの段 × 15 種類の事象のうち 45 通りを許す。副作用は 19 種類 | §5 |
| 空白区間の理由の一覧（割り込みの理由、`overrun`、`route_change`、`media_reset`） | 6 つ（`interruption`・`built_in_mic_muted`・`route_disconnected`・`overrun`・`route_change`・`media_reset`） | §6 |
| 中断通知の文言と予約までの時間 | 題名「録音が中断しています」、本文「アプリを開くと再開を試みます。」、60 秒。差し込む値は持たない | §7 |
| 割り込みの開始の処理（停止要求がない場合を含む）・再構築の④（作り直しと入力の開始の呼び出し）・再構築の途中に届いた停止の待機・割り込みの終了の①〜④の呼び出しの待機・停止の経路で空白区間を閉じる処理の待機・割り込みの終了の②の後で④の前に届いた停止での末尾の Chunk の確定の待機の時間の上限（§5.1） | 順に 5 秒・3 秒・5 秒・3 秒（1 つごと）・5 秒・5 秒。`RecordingTuning` の値で、注入した `Scheduler` で測る | §2・§5.4 |
| 入力の閉鎖の手続き（§5.1）の手段（戻っていない呼び出しを待たずに入力を閉じ、閉じたことを確かめる方法）と時間の上限 | 世代ごとの閉鎖の印（原子操作）で tap がサンプルを捨て、取り出し側が印の設定と同じ排他の中で閉鎖済みの世代のサンプルを Chunk に渡さずに捨て（印を読んだ後の tap の書き込みも Chunk に入らない）、`AudioInput.close(generation:)` で停止と取り外しを指示し、`isClosed(generation:)` で入力の停止と tap の取り外しを確かめてからセッションを無効化する。上限 2 秒。確かめられなければ `microphone_close_unconfirmed` を表示して確認を続ける | §5.4 |
| 割り込みの記録（事象時刻・理由・未確定の開始位置）の形式と置き場所（§5.1。起動時の復旧が読む。[storage.md](storage.md) §8）。永続化できなかった割り込みの記録のメモリ上の保持と再試行、空白区間の記録で置き換える取引の形（§5.1） | `meeting.json` の `pendingInterruption`（識別子・事象時刻・理由）。保存の actor のメモリ上に持ち、再試行は `meeting.json` の新しい世代の書き込み。置き換えは、メモリ上で記録を除いて同じ識別子の空白区間を足し、DB の取引 → `meeting.json` の 1 回の置き換えで行う | §5.4、[storage.md](storage.md) §3.1・§5.1 |

---

## 14. 断定しない事項・確かめていない事項

満たす要件: FR-REC-02・05、FR-INT-02・03、NFR-REL-01、NFR-PRF-01 ／ 制約: CON-02

| 事項 | 状態 | 確かめる時期・方法 |
| --- | --- | --- |
| iOS 27 の 2 つの通知（無効化・再開の勧め）が、着信・Siri・アラームのそれぞれで届く順序と内容 | SDK の宣言を読み、ビルドできることだけを確かめた。実機では受け取っていない | TST-DEV-02。届かない場合に備え、前面への復帰でも再開を試みる |
| 無効化の原因が「アプリ自身」のときに、割り込みとして扱わなくてよい | SDK の列挙の説明による | TST-DEV-02（停止の後に割り込みの記録が増えないこと） |
| 実機の `AVAudioConverter` の変換品質 | Mac 上でだけ測った | TST-AUD-02 を実機でも実行する。満たさなければ FIR を移植する（BD-REC-03） |
| リングバッファ 5 秒分・合図 100 ms で、あふれが起きない | 確かめていない | TST-DEV-01（あふれの回数を記録する） |
| tap のバッファ長（要求値 100 ms）で届く実際の長さ | 確かめていない。長さは毎回読むので、設計は長さに依存しない | 録音の実装（計測値に記録する） |
| 最初のバッファの上限 3 秒・通知の 60 秒が妥当 | 設計値 | TST-DEV-01・02 の結果で見直す |
| 簡易 VAD の数値（−60 dBFS・0.15・200 ms・100 ms） | 本書で定めた設計値。Minutes P1 の実装とは照合していない（参照元のリポジトリが手元にない） | 文字起こしの結果と突き合わせて見直す。P2 で FR-STT-10 に使う前に再確認する |
| category `record`・mode `default` が会議の録音に最適 | 基本設計 §11 のまま | 実機で比較して記録する |
| `activate()`・`deactivate(options:)`（iOS 27 の非同期の API）の所要時間 | 確かめていない | 計測値（開始までの時間）で見る |
| 2026-11-27 に変えたコード片（§2 の `RecordingTuning`）がビルドできる | ビルドしていない（§11.3 の確認は、それより前の版のもの） | 録音の実装の最初に確かめる |
| §5.4 の待機の時間の上限（5 秒・3 秒・2 秒）が妥当 | 設計値 | TST-DEV-01・02 の結果で見直す |
| 戻っていない入力の開始の呼び出しの外から、入力を止めて閉じたことを確かめられる（§5.4 の②・③） | OS の実装による。確かめていない | 録音の実装（Mac）と TST-DEV-02 |

---

## 15. 出典

満たす要件: —（出典の一覧。満たす要件はない）

確認日 2026-11-16。

- iOS 27.2 SDK の `AVAudioSession.h`・`AVAudioSessionTypes.h`（`didBecomeInactive`・`resumptionRecommendation` の通知、`AVAudioSessionDeactivationContext`、`activateWithOptions:completionHandler:`・`deactivateWithOptions:completionHandler:`、割り込みの理由、経路変更の理由、ポートの種別、従来の割り込みの通知の非推奨の宣言）
- iOS 27.2 SDK の `AVAudioNode.h`（従来の `installTapOnBus:bufferSize:format:block:` の非推奨の宣言、バッファ長の範囲）、`AVAudioConverter.h`・`AVAudioSettings.h`（品質・方式・入力の状態）、`AVAudioApplication.h`（マイクの権限）
- iOS 27.2 SDK の `AVFAudio.swiftinterface`（`installAudioTap(onBus:bufferSize:format:tapProvider:)`、`AVReadOnlyAudioPCMBuffer`、`DidBecomeInactiveMessage`・`ResumptionRecommendationMessage`・`DeactivationResult`）、`Foundation.swiftinterface`（型付きの通知の購読）、`Synchronization.swiftinterface`（`Atomic`）
- iOS 27.2 SDK の `UNUserNotificationCenter.h`・`UNNotificationRequest.h`・`UNNotificationTrigger.h`（予約・取り消し・時間の指定）
- Xcode 27.2（27B52）・Swift 6.4.2 の `swift build`・`swift test`・`xcodebuild`（一時の Package で実行。§11.3 の測定値はこの結果である）
- Minutes `docs/design-local-phase1.md` §3.2・§3.3・§8・§14（別リポジトリ・読み取り専用。本作業では参照できず、基本設計の記述に基づいた）
- **出典の URL について（確認日 2026-11-27）:** 上の SDK のヘッダ・`.swiftinterface` は、Xcode に同梱されたローカルのファイルで、公開の URL はない。版の根拠は、SDK の版（iOS 27.2）は `xcodebuild -showsdks`、Xcode 27.2（27B52）は `xcodebuild -version`、Swift 6.4.2 は `swift --version`、各 API の対応 OS は SDK の宣言（`@available`・`API_AVAILABLE`）で確かめたものである（[dev-environment.md](dev-environment.md) §2）。Apple の文書のページは、本文が JavaScript で描かれ、対応 OS の記述を取得できないので、版の根拠にしない
