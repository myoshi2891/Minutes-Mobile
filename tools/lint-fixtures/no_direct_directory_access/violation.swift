let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask) // expect: no_direct_directory_access
let documentsURL = URL.documentsDirectory // expect: no_direct_directory_access
let temporaryPath = NSTemporaryDirectory() // expect: no_direct_directory_access
let temporaryURL = FileManager.default.temporaryDirectory // expect: no_direct_directory_access
func sample() throws {
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true) // expect: no_direct_directory_access
}
