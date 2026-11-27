// NSTemporaryDirectory は使わない
let directory = storageRoot.meetingDirectory(for: meetingID)
let temporaryDirectory = storageRoot.meetingDirectory(for: meetingID)
let child = temporaryDirectory.appending(path: "child")
