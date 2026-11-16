func sample() {
    do { try work() } catch {} // expect: no_empty_catch
    do { try work() } catch let error as SampleError { } // expect: no_empty_catch
    do {
        try work()
    } catch { // expect: no_empty_catch
        // 何もしない
    }
}
