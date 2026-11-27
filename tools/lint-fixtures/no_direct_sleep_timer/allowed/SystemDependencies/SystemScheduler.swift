func sample() async throws {
    try await Task.sleep(for: .seconds(1))
    let timer = Timer(timeInterval: 1, repeats: true) { _ in }
}
