let number = Int.random(in: 0..<10) // expect: no_direct_random
let element = items.randomElement() // expect: no_direct_random
let shuffled = items.shuffled() // expect: no_direct_random
var generator = SystemRandomNumberGenerator() // expect: no_direct_random
