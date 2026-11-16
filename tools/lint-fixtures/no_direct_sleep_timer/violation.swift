func sample() async throws {
    try await Task.sleep(for: .seconds(1)) // expect: no_direct_sleep_timer
    Thread.sleep(forTimeInterval: 1) // expect: no_direct_sleep_timer
    sleep(1) // expect: no_direct_sleep_timer
    Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in } // expect: no_direct_sleep_timer
    queue.asyncAfter(deadline: deadline) {} // expect: no_direct_sleep_timer
}
