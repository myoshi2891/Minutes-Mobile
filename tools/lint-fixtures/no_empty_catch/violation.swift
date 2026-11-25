func sample() {
    do { try work() } catch {} // expect: no_empty_catch
    do { try work() } catch let error as SampleError { } // expect: no_empty_catch
    do {
        try work()
    } catch { // expect: no_empty_catch
        // 何もしない
    }
    do { try work() } catch { /* 何もしない */ } // expect: no_empty_catch
    do { try work() } catch /* x */ { } // expect: no_empty_catch
    do { try work() } catch let error /* { */ where error.code == 5 { } // expect: no_empty_catch
    do { try work() } catch let error where error.code == 5 { } // expect: no_empty_catch
    do { try work() } catch let error as SampleError where error.message == "x" { } // expect: no_empty_catch
    do { try work() } catch let error where error.message == "{ }" { } // expect: no_empty_catch
    do {
        try work()
    } catch let error // expect: no_empty_catch
        where error.message == "x"
            && error.code == 5 {
    }
    do {
        try work()
    } catch { // expect: no_empty_catch
        // 何もしない }
    }
    // 入れ子のブロックコメントだけの本体も空とみなす
    do { try work() } catch { /* 外 /* 内 */ 外 */ } // expect: no_empty_catch
    // 条件の括弧の中のクロージャーの「{」を本体と取り違えない（処理のあるクロージャーの後の空の本体を検出する）
    do { try work() } catch let error where codes.contains(where: { $0 == error.code }) {} // expect: no_empty_catch
    do { try work() } catch let error where [{ (code: Int) in code == 5 }].contains(where: { $0(error.code) }) { } // expect: no_empty_catch
    // 条件の括弧の中のコメントの「"」「)」で読み違えず、後ろの空の本体を検出する
    do { try work() } catch let error where check(/* " ) */ error) {} // expect: no_empty_catch
    do { try work() } catch let error where check(f(/* ) " */ error)) { } // expect: no_empty_catch
    // 条件に拡張区切りの文字列がある空の本体を検出する
    do { try work() } catch let error where error.message == #"a" { x }"# {} // expect: no_empty_catch
    do { try work() } catch let error where check(##"a"# ) {"##, error) { } // expect: no_empty_catch
}
