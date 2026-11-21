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
    do { try work() } catch /* {} */ { recorder.record(.failed) }
    do { try work() } catch let error /* { */ where error.code == 5 { recorder.record(.failed) }
    do {
        try work()
    } catch let error
        where error.message == "{}" {
        recorder.record(.failed)
    }
    // 行コメントの中の「{」「}」を本体の始まりや終わりと取り違えない
    do { try work() } catch // {}
    { recorder.record(.failed) }
    do {
        try work()
    } catch { // }
        recorder.record(.failed)
    }
    // 入れ子のブロックコメントの後に処理がある本体は空ではない
    do { try work() } catch { /* 外 /* 内 */ 外 */ recorder.record(.failed) }
    // 条件の括弧の中の空のクロージャーを空の本体と取り違えない
    do { try work() } catch let error where handlers.allSatisfy(where: {}) { recorder.record(.failed) }
    do { try work() } catch let error where check(error, label: "(", { _ in }) { recorder.record(.failed) }
    // 条件の括弧の中のコメントの「)」で括弧が閉じたと見なさない（コメントの中の「{ }」を空の本体と取り違えない）
    do { try work() } catch let error where check(/* ) { } */ error) { recorder.record(.failed) }
    do { try work() } catch let error where check(error, [/* ] { } */ 5]) { recorder.record(.failed) }
}
