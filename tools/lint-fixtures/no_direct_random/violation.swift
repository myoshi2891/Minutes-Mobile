let number = Int.random(in: 0..<10) // expect: no_direct_random
let element = items.randomElement() // expect: no_direct_random
let shuffled = items.shuffled() // expect: no_direct_random
var generator = SystemRandomNumberGenerator() // expect: no_direct_random
let fromRange = Int.random(in: 0..<count(of: items)) // expect: no_direct_random
items.shuffle() // expect: no_direct_random
let flag = Bool.random() // expect: no_direct_random
// using: の外（次の呼び出し）にある using: で、既定の生成器の呼び出しを見逃さない
let mixed = Int.random(in: 0..<10) + pick(using: &generator) // expect: no_direct_random
// コメントの中の using: は引数ではない
let inComment = Int.random(in: 0..<10 /* using: &generator */) // expect: no_direct_random
let withComment = Int.random(in: 0..<10 /* ( */) // expect: no_direct_random
// 文字列の中の using: は引数ではない
let inString = Int.random(in: 0..<length(of: "using: (")) // expect: no_direct_random
// 入れ子のブロックコメントの中の using: は引数ではない
let inNestedComment = Int.random(in: 0..<10 /* 外 /* 内 */ using: &generator */) // expect: no_direct_random
// 入れ子（辞書のリテラル・入れ子の呼び出し）の中の using: は、この呼び出しの引数のラベルではない
let inLiteral = Int.random(in: 0..<[using: 1].count) // expect: no_direct_random
let inNestedCall = Int.random(in: 0..<pick(using: &generator)) // expect: no_direct_random
let inClosure = Int.random(in: 0..<items.map { pick(using: $0) }.count) // expect: no_direct_random
// 3 段の入れ子でも using: がなければ検出する。4 段以上の入れ子は読めないので、最上位の using: があっても検出する側に倒れる
let deepWithout = Int.random(in: 0..<f(g(h(1)))) // expect: no_direct_random
let deeper = Int.random(in: 0..<f(g(h(i(1)))), using: &generator) // expect: no_direct_random
// 拡張区切りの文字列の中の「"」「,」「using:」は、文字列の終わりや最上位の引数ではない
let inRawString = Int.random(in: 0..<#"a", using: &generator"#.count) // expect: no_direct_random
let inDoubleRaw = Int.random(in: 0..<##"a"#, using: &generator"##.count) // expect: no_direct_random
let inNestedRaw = Int.random(in: 0..<length(of: #"a"), using: &generator"#)) // expect: no_direct_random
// 「.」と名前の間の空白があっても検出する
let spaced = Int . random(in: 0...1) // expect: no_direct_random
