let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
func sample() throws {
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
}
