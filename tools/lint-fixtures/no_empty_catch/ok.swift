func sample() throws {
    do { try work() } catch { recorder.record(.failed) }
    do {
        try work()
    } catch {
        throw SampleError.wrapped
    }
}
