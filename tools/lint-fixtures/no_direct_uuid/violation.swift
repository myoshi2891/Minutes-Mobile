let identifier = UUID() // expect: no_direct_uuid
let initialized = UUID.init() // expect: no_direct_uuid
let typed: UUID = .init() // expect: no_direct_uuid
let optionalTyped: Foundation.UUID? = . init( ) // expect: no_direct_uuid
