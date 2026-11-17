func sample() {
    do { try work() } catch {} // expect: no_empty_catch
    do { try work() } catch let error as SampleError { } // expect: no_empty_catch
    do {
        try work()
    } catch { // expect: no_empty_catch
        // 何もしない
    }
    do { try work() } catch { /* 何もしない */ } // expect: no_empty_catch
    do { try work() } catch let error where error.code == 5 { } // expect: no_empty_catch
    do { try work() } catch let error as SampleError where error.message == "x" { } // expect: no_empty_catch
    do { try work() } catch let error where error.message == "{ }" { } // expect: no_empty_catch
}
