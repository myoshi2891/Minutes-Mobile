let now = clock.now()
let epoch = Date(timeIntervalSince1970: 0)
let candidate: Date? = nil
let epochInit = Date.init(timeIntervalSince1970: 0)
let elapsed = deadline.timeIntervalSince(now)
let systemUptime = clock.uptime
