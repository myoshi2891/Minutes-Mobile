func sample() async throws {
    try await scheduler.sleep(for: .seconds(1))
    try await scheduler . sleep(for: .seconds(1))
}

// 注入する型のメソッドの宣言は検出しない
struct ManualScheduler {
    func sleep(for duration: Duration) async throws {}
    func usleep(_ microseconds: UInt32) {}
}
