func sample() {
    UIPasteboard.general.string = text // expect: no_direct_pasteboard
}
let view = Text(title).textSelection(.enabled) // expect: no_direct_pasteboard
let spacedView = Text(title) . textSelection(.enabled) // expect: no_direct_pasteboard
