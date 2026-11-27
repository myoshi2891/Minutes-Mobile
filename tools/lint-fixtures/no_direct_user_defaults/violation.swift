func sample() {
    UserDefaults.standard.set(true, forKey: "k") // expect: no_direct_user_defaults
}
struct SampleView {
    @AppStorage("k") var flag = false // expect: no_direct_user_defaults
}
