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
