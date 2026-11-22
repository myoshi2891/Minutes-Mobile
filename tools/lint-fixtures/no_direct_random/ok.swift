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
