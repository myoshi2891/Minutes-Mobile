let session = URLSession.shared // expect: no_network_api
let request = URLRequest(url: url) // expect: no_network_api
let connection = NWConnection(host: "h", port: 1, using: .tcp) // expect: no_network_api
let task: URLSessionWebSocketTask? = nil // expect: no_network_api
let pair = CFStreamCreatePairWithSocketToHost(nil, host, 80, &input, &output) // expect: no_network_api
Stream.getStreamsToHost(withName: "h", port: 80, inputStream: &input, outputStream: &output) // expect: no_network_api
