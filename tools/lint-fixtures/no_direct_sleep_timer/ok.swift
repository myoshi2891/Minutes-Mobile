func sample() async throws {
    try await scheduler.sleep(for: .seconds(1))
    try await scheduler . sleep(for: .seconds(1))
}

// 注入する型のメソッドの宣言は検出しない
struct ManualScheduler {
    func sleep(for duration: Duration) async throws {}
    func usleep(_ microseconds: UInt32) {}
}

// 名前の一部が Timer の、注入する型の生成と呼び出しは検出しない
func injected(scheduler: TimerScheduler) {
    let timer = ManualTimer(interval: 1)
    let made = TimerScheduler(clock: clock)
    scheduler.scheduledTimer(interval: 1) {}
}
