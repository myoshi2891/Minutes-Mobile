let number = randomSource.nextInt(upperBound: 10)
// 注入した生成器を渡す呼び出しは認める
let injected = Int.random(in: 0..<10, using: &generator)
let nested = Int.random(in: 0..<count(of: items), using: &generator)
let element = items.randomElement(using: &generator)
let shuffled = items.shuffled(using: &generator)
items.shuffle(using: &generator)
// 引数の中のコメント（括弧を含む）があっても、後ろの using: を認める
let commented = Int.random(in: 0..<10, /* 上限は ( を含む */ using: &generator)
let lineCommented = Int.random(
    in: 0..<10, // 範囲 (0 以上)
    using: &generator
)
// 引数の中の文字列（括弧を含む）があっても、後ろの using: を認める
let quoted = Int.random(in: 0..<length(of: ")"), using: &generator)
// 引数の中の入れ子のブロックコメント（内側を閉じた後に ) を含む）があっても、後ろの using: を認める
let nestedComment = Int.random(in: 0..<10, /* 外 /* 内 */ ) を含む */ using: &generator)
// 引数のラベル using と「:」の間のコメントは空白として扱い、注入した生成器を渡す呼び出しと認める
let labelComment = Int.random(in: 0..<10, using /* 生成器 */ : &generator)
// 前の引数に角括弧・波括弧があっても、最上位の using: を認める
let withLiteral = Int.random(in: 0..<[1, 2].count, using: &generator)
let withClosure = Int.random(in: 0..<items.map { $0 }.count, using: &generator)
// 前の引数の括弧が 3 段の入れ子でも、最上位の using: を認める
let deepNested = Int.random(in: 0..<f(g(h(1))), using: &generator)
