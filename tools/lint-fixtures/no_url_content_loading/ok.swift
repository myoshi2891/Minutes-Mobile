// Data(contentsOf: url) は使わない
let local = FileManager.default.contents(atPath: "/a/b")
let text = try String(contentsOfFile: "/a/b", encoding: .utf8)
let note = "NSData(contentsOf: url) という文字列"
var samples: [Int16] = []
samples.append(contentsOf: other)
samples.insert(contentsOf: other, at: 0)
let localBytes: NSMutableData? = .init(contentsOfFile: "/a/b")
var buffer: NSMutableData = .init()
