let number = randomSource.nextInt(upperBound: 10)
// 注入した生成器を渡す呼び出しは認める
let injected = Int.random(in: 0..<10, using: &generator)
let nested = Int.random(in: 0..<count(of: items), using: &generator)
let element = items.randomElement(using: &generator)
let shuffled = items.shuffled(using: &generator)
items.shuffle(using: &generator)
