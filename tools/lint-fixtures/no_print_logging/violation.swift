func sample() {
    print("x") // expect: no_print_logging
    debugPrint("x") // expect: no_print_logging
    NSLog("x") // expect: no_print_logging
    Swift.print("x") // expect: no_print_logging
}
