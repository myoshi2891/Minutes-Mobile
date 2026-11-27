// print("コメントの中は検出しない")
func sample(printer: Printer) {
    let text = "print(文字列の中は検出しない)"
    printer.blueprint(text)
    printer.render(text)
}
