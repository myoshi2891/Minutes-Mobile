let now = Date() // expect: no_direct_clock
let today = Date.now // expect: no_direct_clock
let clock = ContinuousClock() // expect: no_direct_clock
let seconds = CFAbsoluteTimeGetCurrent() // expect: no_direct_clock
let initialized = Date.init() // expect: no_direct_clock
let sinceNow = Date(timeIntervalSinceNow: 0) // expect: no_direct_clock
let remaining = deadline.timeIntervalSinceNow // expect: no_direct_clock
let spaced = deadline . timeIntervalSinceNow // expect: no_direct_clock
