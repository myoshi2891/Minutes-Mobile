let logger = Logger(subsystem: "a", category: "b") // expect: no_direct_logger
let initialized = Logger.init(subsystem: "a", category: "b") // expect: no_direct_logger
func sample() {
    os_log("x") // expect: no_direct_logger
}
