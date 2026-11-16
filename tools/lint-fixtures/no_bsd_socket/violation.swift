let descriptor = socket(AF_INET, SOCK_STREAM, 0) // expect: no_bsd_socket
let result = connect(descriptor, address, length) // expect: no_bsd_socket
