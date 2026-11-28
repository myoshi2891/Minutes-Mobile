import CoreSpotlight // expect: no_spotlight_user_activity
let activity = NSUserActivity(activityType: "a") // expect: no_spotlight_user_activity
let item = CSSearchableItem(uniqueIdentifier: nil, domainIdentifier: nil, attributeSet: attributes) // expect: no_spotlight_user_activity
let view = content.userActivity("a") { _ in } // expect: no_spotlight_user_activity
import AppIntents // expect: no_spotlight_user_activity
@preconcurrency import AppIntents // expect: no_spotlight_user_activity
import struct AppIntents.IntentDescription // expect: no_spotlight_user_activity
import /* 区切りのコメント */ AppIntents // expect: no_spotlight_user_activity
import /* 外 /* 内 */ 外 */ struct /* 種類の後 */ AppIntents.IntentDescription // expect: no_spotlight_user_activity
import `AppIntents` // expect: no_spotlight_user_activity
import /* 区切り */ `CoreSpotlight` // expect: no_spotlight_user_activity
import // expect: no_spotlight_user_activity
    AppIntents
