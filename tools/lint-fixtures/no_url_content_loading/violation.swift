let remote = NSData(contentsOf: url) // expect: no_url_content_loading
let data = try Data(contentsOf: url) // expect: no_url_content_loading
let text = try String(contentsOf: url, encoding: .utf8) // expect: no_url_content_loading
let string = try NSString(contentsOf: url, encoding: 4) // expect: no_url_content_loading
let dictionary = NSDictionary(contentsOf: url) // expect: no_url_content_loading
let parser = XMLParser(contentsOf: url) // expect: no_url_content_loading
let initialized = try Data.init(contentsOf: url) // expect: no_url_content_loading
let annotated: Data = try .init(contentsOf: url) // expect: no_url_content_loading
