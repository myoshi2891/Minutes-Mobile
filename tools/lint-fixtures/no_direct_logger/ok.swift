// Logger(subsystem:category:) はログの型の中だけで使う
let logger = AppLogger()
let other = MyLogger(name: "a")
let wrapped: AppLogger = .init(category: "b")
let custom: MyLogger = .init(subsystem: "a", category: "b")
