let now = Date() // expect: no_direct_clock
let today = Date.now // expect: no_direct_clock
let clock = ContinuousClock() // expect: no_direct_clock
let seconds = CFAbsoluteTimeGetCurrent() // expect: no_direct_clock
