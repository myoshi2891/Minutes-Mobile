func sample() async throws {
    try await Task.sleep(for: .seconds(1)) // expect: no_direct_sleep_timer
    Thread.sleep(forTimeInterval: 1) // expect: no_direct_sleep_timer
    sleep(1) // expect: no_direct_sleep_timer
    Darwin.sleep(1) // expect: no_direct_sleep_timer
    Darwin.usleep(1000) // expect: no_direct_sleep_timer
    Darwin . sleep(1) // expect: no_direct_sleep_timer
    Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    try await Task . sleep(for: .seconds(1)) // expect: no_direct_sleep_timer
    try await Task<Never, Never>.sleep(for: .seconds(1)) // expect: no_direct_sleep_timer
    try await Task<Never, Never> . sleep(nanoseconds: 1) // expect: no_direct_sleep_timer
    let ticks = Timer.publish(every: 1, on: .main, in: .common) // expect: no_direct_sleep_timer
    let source = DispatchSource.makeTimerSource(queue: queue) // expect: no_direct_sleep_timer
    queue.asyncAfter(deadline: deadline) {} // expect: no_direct_sleep_timer
    Thread . sleep(forTimeInterval: 1) // expect: no_direct_sleep_timer
    Timer . scheduledTimer(withTimeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    let timer = Timer(timeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    let fired = Timer(fire: date, interval: 1, repeats: false) { _ in } // expect: no_direct_sleep_timer
    let target = Timer.init(timeInterval: 1, target: self, selector: selector, userInfo: nil, repeats: true) // expect: no_direct_sleep_timer
    let spaced = Timer . init(timeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    let qualified = Foundation.Timer(timeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    let annotated: Timer = .init(timeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
}
