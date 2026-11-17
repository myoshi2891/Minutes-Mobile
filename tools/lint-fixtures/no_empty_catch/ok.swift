func sample() throws {
    do { try work() } catch { recorder.record(.failed) }
    do {
        try work()
    } catch {
        throw SampleError.wrapped
    }
    // 説明のコメントの中の catch {} は検出しない
    /* do { try work() } catch {} */
    let text = "catch {}"
    do { try work() } catch { /* 前 */ recorder.record(.failed) /* 後 */ }
    do { try work() } catch let error where error.message == "{}" { recorder.record(.failed) }
    do { try work() } catch let error where error.message == "x" { recorder.record(.failed) }
}
