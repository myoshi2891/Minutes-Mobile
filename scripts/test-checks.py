#!/usr/bin/python3
"""検査スクリプトそのものを試験する（基本設計 dev-environment.md §13）。

合格するはずの入力で通り、不合格にするはずの入力で失敗することを確かめる。
試験用の設定はこのスクリプトの中で組み立て、一時ディレクトリに置く
（リポジトリに「検査の対象外にするディレクトリ」を作らないため）。

使い方: scripts/test-checks.py [settings] [dependencies] [versions] [gitleaks] [docs] [coverage]   （省略時は全部）
終了コード: 0 = 全件が期待どおり / 1 = 期待と違う結果あり / 2 = 使い方の誤り
"""
import importlib.util
import json
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True  # scripts/__pycache__ を作らない
from xcproj import strip_trailing_commas  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
failures: List[str] = []
count = 0

# 合格する設定。末尾カンマと条件付きの設定を含める（実際の project.xcproj と同じ書き方）
OK_PROJECT = """{
  "configurations": [
    "Debug",
    "Release",
  ],
  "targets": [
    {
      "name": "Sample",
      "product-type": "application",
      "build-settings": {
        "GENERATE_INFOPLIST_FILE": "YES",
        "INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",
        "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": "UIInterfaceOrientationPortrait",
        "PRODUCT_BUNDLE_IDENTIFIER": "com.example.sample",
        "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
        "SWIFT_TREAT_WARNINGS_AS_ERRORS": "YES",
        "SWIFT_VERSION": "6.0",
        "TARGETED_DEVICE_FAMILY": "1",
      },
    },
  ],
  "build-settings": {
    "GCC_PREPROCESSOR_DEFINITIONS[config=Debug]": [
      "DEBUG=1",
      "$(inherited)",
    ],
    "IPHONEOS_DEPLOYMENT_TARGET": "27.2",
  },
}
"""
OK_INFO_PLIST = {"UIBackgroundModes": ["audio", "processing"], "BGTaskSchedulerPermittedIdentifiers": ["a.b"]}


def run(command: List[str]) -> Tuple[int, str]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def expect(label: str, command: List[str], want_code: int, want_text: str = "") -> None:
    """終了コードと、出力に含まれるはずの文字列を確かめる。"""
    global count
    count += 1
    code, output = run(command)
    if code != want_code:
        failures.append(f"{label}: 終了コードが {code}（期待 {want_code}）\n{output.strip()}")
    elif want_text and want_text not in output:
        failures.append(f"{label}: 出力に「{want_text}」がない\n{output.strip()}")


def replaced(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"置換の対象が 1 か所でない: {old}"
    return text.replace(old, new)


def settings_case(
    label: str,
    want_code: int,
    want_text: str = "",
    project: Optional[Callable[[str], str]] = None,
    plists: Optional[Dict[str, dict]] = None,
    raw: Optional[Dict[str, bytes]] = None,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        text = project(OK_PROJECT) if project else OK_PROJECT
        (root / "project.xcproj").write_text(text, encoding="utf-8")
        files = {"App/Info.plist": OK_INFO_PLIST}
        files.update(plists or {})
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            with (root / name).open("wb") as handle:
                plistlib.dump(content, handle)
        for name, data in (raw or {}).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        command = [str(SCRIPTS / "check-project-settings.py"), "--project", str(root / "project.xcproj"), "--root", directory]
        expect(f"settings: {label}", command, want_code, want_text)


def test_settings() -> None:
    global count
    count += 1
    sample = '{"a": ["x,]", "y",], "b": {"c": "d,}",},}'
    if json.loads(strip_trailing_commas(sample)) != {"a": ["x,]", "y"], "b": {"c": "d,}"}}:
        failures.append("settings: 末尾カンマの除去で、文字列の中のカンマが変わった")
    # 開き括弧の直後のカンマは除かない（壊れた入力を空の辞書・配列として読まない）
    for broken in ("{,}", "[,]", '{"a": [ , ]}'):
        count += 1
        try:
            json.loads(strip_trailing_commas(broken))
            failures.append(f"settings: 壊れた入力 {broken} を読めてしまった")
        except json.JSONDecodeError:
            pass
    settings_case("開き括弧の直後のカンマは読めないものとして扱う", 2, "読めない",
                  project=lambda t: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n  },', '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n  },\n  "extra": {,},'))

    # 標準の JSON にない定数（NaN・Infinity）は読めないものとして扱う
    for constant in ("NaN", "Infinity", "-Infinity"):
        settings_case(f"標準の JSON にない定数 {constant}", 2, "読めない",
                      project=lambda t, c=constant: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                                             f'"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "X": {c},'))
    # 入れ子が深すぎる入力（Xcode 同梱の python3 では RecursionError になる）も読めないものとして扱う
    settings_case("入れ子が深すぎる設定", 2, "読めない",
                  project=lambda t: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "X": ' + "[" * 100000 + "]" * 100000 + ","))
    settings_case("合格する設定", 0, "OK")
    settings_case("対象デバイスが iPhone 以外を含む", 1, "TARGETED_DEVICE_FAMILY",
                  project=lambda t: replaced(t, '"TARGETED_DEVICE_FAMILY": "1"', '"TARGETED_DEVICE_FAMILY": "1,2,7"'))
    settings_case("対応プラットフォームが iOS 以外を含む", 1, "SUPPORTED_PLATFORMS",
                  project=lambda t: replaced(t, "iphoneos iphonesimulator", "iphoneos iphonesimulator macosx"))
    settings_case("Swift 5 の言語モード", 1, "SWIFT_VERSION",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0"', '"SWIFT_VERSION": "5.0"'))
    settings_case("条件付きの設定だけが Swift 5", 1, "SWIFT_VERSION[config=Debug]",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0",',
                                             '"SWIFT_VERSION": "6.0",\n        "SWIFT_VERSION[config=Debug]": "5.0",'))
    settings_case("ターゲットの Debug は Swift 6、共有の Release は Swift 5", 1, "SWIFT_VERSION[config=Release] = 5.0",
                  project=lambda t: replaced(replaced(t, '        "SWIFT_VERSION": "6.0",\n', '        "SWIFT_VERSION[config=Debug]": "6.0",\n'),
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "SWIFT_VERSION[config=Release]": "5.0",'))
    settings_case("ターゲットの Debug と共有の Release がどちらも Swift 6", 0, "OK",
                  project=lambda t: replaced(replaced(t, '        "SWIFT_VERSION": "6.0",\n', '        "SWIFT_VERSION[config=Debug]": "6.0",\n'),
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "SWIFT_VERSION[config=Release]": "6.0",'))
    settings_case("ターゲットの条件のない値は共有の条件付きの値を上書きする", 0, "OK",
                  project=lambda t: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "SWIFT_VERSION[config=Release]": "5.0",'))
    settings_case("Release だけ設定がない", 1, "構成 Release",
                  project=lambda t: replaced(t, '        "SWIFT_VERSION": "6.0",\n', '        "SWIFT_VERSION[config=Debug]": "6.0",\n'))
    settings_case("SDK の条件付きの値も検査する", 1, "SWIFT_VERSION[sdk=iphoneos*] = 5.0",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0",',
                                             '"SWIFT_VERSION": "6.0",\n        "SWIFT_VERSION[sdk=iphoneos*]": "5.0",'))
    settings_case("警告をエラーにする設定がない", 1, "SWIFT_TREAT_WARNINGS_AS_ERRORS",
                  project=lambda t: replaced(t, '        "SWIFT_TREAT_WARNINGS_AS_ERRORS": "YES",\n', ""))
    settings_case("最低 OS が違う", 1, "IPHONEOS_DEPLOYMENT_TARGET",
                  project=lambda t: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2"', '"IPHONEOS_DEPLOYMENT_TARGET": "26.0"'))
    settings_case("横向きを含む", 1, "UISupportedInterfaceOrientations_iPhone",
                  project=lambda t: replaced(t, '"UIInterfaceOrientationPortrait"',
                                             '"UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft"'))
    settings_case("iPad の向きの設定が残っている", 1, "UISupportedInterfaceOrientations_iPad",
                  project=lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                             '"GENERATE_INFOPLIST_FILE": "YES",\n        '
                                             '"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad": "UIInterfaceOrientationPortrait",'))
    settings_case("マイクの利用目的がない", 1, "NSMicrophoneUsageDescription",
                  project=lambda t: replaced(t, '        "INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n', ""))
    settings_case("マイクの利用目的が Release だけ空", 1, "NSMicrophoneUsageDescription[config=Release] が空",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n'
                                             '        "INFOPLIST_KEY_NSMicrophoneUsageDescription[config=Release]": "",'))
    settings_case("マイクの利用目的が空の配列", 1, "NSMicrophoneUsageDescription が空",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": [],'))
    settings_case("構成の一覧がない設定は合格にしない", 2, "configurations",
                  project=lambda t: replaced(t, '"configurations": [\n    "Debug",\n    "Release",\n  ],', '"configurations": [],'))
    settings_case("ファイル共有を有効にしている（ビルド設定）", 1, "UIFileSharingEnabled",
                  project=lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                             '"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_KEY_UIFileSharingEnabled": "YES",'))
    settings_case("使わない権限の文言（ビルド設定）", 1, "NSCameraUsageDescription",
                  project=lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                             '"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_KEY_NSCameraUsageDescription": "x",'))
    settings_case("音声認識の利用目的（SpeechAnalyzer では不要）", 1, "NSSpeechRecognitionUsageDescription",
                  plists={"App/Info.plist": {"NSSpeechRecognitionUsageDescription": "x"}})
    settings_case("ATS の例外", 1, "NSAllowsArbitraryLoads",
                  plists={"App/Info.plist": {"NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True}}})
    settings_case("ATS の辞書そのもの", 1, "NSAppTransportSecurity を設定しない",
                  plists={"App/Info.plist": {"NSAppTransportSecurity": {}}})
    settings_case("ATS の例外ドメイン", 1, "NSExceptionDomains",
                  plists={"App/Info.plist": {"NSAppTransportSecurity": {"NSExceptionDomains": {"example.com": {}}}}})
    settings_case("ファイル共有を有効にしている（Info.plist）", 1, "UIFileSharingEnabled",
                  plists={"App/Info.plist": {"UIFileSharingEnabled": True}})
    settings_case("ファイル共有を無効と明記するのは認める", 0, "OK",
                  plists={"App/Info.plist": {"UIFileSharingEnabled": False}})
    settings_case("文書ブラウザ", 1, "UISupportsDocumentBrowser",
                  plists={"App/Info.plist": {"UISupportsDocumentBrowser": True}})
    settings_case("NSUserActivity の種類", 1, "NSUserActivityTypes",
                  plists={"App/Info.plist": {"NSUserActivityTypes": ["a.b"]}})
    settings_case("ファイルをその場で開く設定", 1, "LSSupportsOpeningDocumentsInPlace",
                  plists={"App/Info.plist": {"LSSupportsOpeningDocumentsInPlace": True}})
    settings_case("URL スキーム", 1, "CFBundleURLTypes",
                  plists={"App/Info.plist": {"CFBundleURLTypes": [{"CFBundleURLSchemes": ["sample"]}]}})
    settings_case("許可していないバックグラウンドの種類", 1, "fetch",
                  plists={"App/Info.plist": {"UIBackgroundModes": ["audio", "fetch"]}})
    settings_case("共有拡張", 1, "com.apple.share-services",
                  plists={"Share/Info.plist": {"NSExtension": {"NSExtensionPointIdentifier": "com.apple.share-services"}}})
    settings_case("ウィジェット拡張は認める", 0, "OK",
                  plists={"Widgets/Info.plist": {"NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"}}})
    settings_case("Push 通知の entitlement", 1, "aps-environment",
                  plists={"App/App.entitlements": {"aps-environment": "development"}})
    settings_case("ユニバーサルリンクの entitlement", 1, "associated-domains",
                  plists={"App/App.entitlements": {"com.apple.developer.associated-domains": ["applinks:example.com"]}})
    settings_case("Siri の entitlement", 1, "com.apple.developer.siri",
                  plists={"App/App.entitlements": {"com.apple.developer.siri": True}})
    settings_case("Time Sensitive 通知の entitlement", 1, "time-sensitive",
                  plists={"App/App.entitlements": {"com.apple.developer.usernotifications.time-sensitive": True}})
    settings_case("iCloud の鍵と値の保存の entitlement", 1, "ubiquity",
                  plists={"App/App.entitlements": {"com.apple.developer.ubiquity-kvstore-identifier": "x"}})
    settings_case("App Groups の entitlement は認める", 0, "OK",
                  plists={"App/App.entitlements": {"com.apple.security.application-groups": ["group.sample"]}})
    settings_case("読めない Info.plist は合格にしない", 2, "読めない", raw={"App/Broken/Info.plist": b"<plist"})
    settings_case("iCloud の entitlement", 1, "icloud",
                  plists={"App/App.entitlements": {"com.apple.developer.icloud-services": ["CloudKit"]}})
    settings_case("仮の Bundle Identifier は警告にとどめる", 0, "CON-09",
                  project=lambda t: replaced(t, "com.example.sample", "devplaceholder.sample"))
    # INFOPLIST_FILE が指すファイルは、名前が Info.plist で終わらなくても検査する
    def info_plist_file(value: str) -> Callable[[str], str]:
        return lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                  f'"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_FILE": "{value}",')
    settings_case("INFOPLIST_FILE が指すファイルの違反", 1, "NSAppTransportSecurity",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": {"NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True}}})
    settings_case("INFOPLIST_FILE の変数を展開する", 1, "UIFileSharingEnabled",
                  project=info_plist_file("$(SRCROOT)/$(TARGET_NAME)/Settings.plist"),
                  plists={"Sample/Settings.plist": {"UIFileSharingEnabled": True}})
    settings_case("INFOPLIST_FILE が指す合格のファイル", 0, "OK",
                  project=info_plist_file("Config/App-Settings.plist"), plists={"Config/App-Settings.plist": OK_INFO_PLIST})
    settings_case("INFOPLIST_FILE が指すファイルがない", 2, "読めない", project=info_plist_file("Config/Missing.plist"))
    settings_case("INFOPLIST_FILE が指すファイルが壊れている", 2, "読めない",
                  project=info_plist_file("Config/Broken.plist"), raw={"Config/Broken.plist": b"<plist"})
    settings_case("INFOPLIST_FILE の展開できない変数", 2, "展開できない", project=info_plist_file("$(UNKNOWN)/Info.plist"))
    # マイクの利用目的は、各構成で最終的に生成される Info.plist で判定する
    generate_no = lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",', '"GENERATE_INFOPLIST_FILE": "NO",')  # noqa: E731
    without_key = lambda t: replaced(t, '        "INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n', "")  # noqa: E731
    # 生成しない構成で使われる plist は、向きの指定（縦だけ）も持つ（BD-UI-10）
    microphone = {"NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",
                  "UISupportedInterfaceOrientations~iphone": ["UIInterfaceOrientationPortrait"]}
    with_file = lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t))  # noqa: E731
    # ビルド設定のマイクの利用目的は、文字列化する前の値で型を確かめる（配列を連結した文字列を認めない）
    settings_case("ビルド設定のマイクの利用目的が配列", 1, "文字列でない",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": ["会議を録音するためにマイクを使用します。"],'))
    # 空の条件付きの INFOPLIST_FILE は、その条件のビルドに参照先がないことを表す。条件のない参照先で補わない
    settings_case("arm64 だけ参照先を空にした生成しない構成", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: replaced(with_file(t), '"INFOPLIST_FILE": "Config/App-Settings.plist",',
                                             '"INFOPLIST_FILE": "Config/App-Settings.plist",\n        "INFOPLIST_FILE[arch=arm64]": "",'),
                  plists={"Config/App-Settings.plist": microphone})
    # 生成しない構成では、使われる plist に縦だけの向きの指定が要る
    settings_case("生成しない plist に向きの指定がない", 1, "縦だけの向きの指定がない", project=with_file,
                  plists={"Config/App-Settings.plist": {"NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。"}})
    settings_case("参照先の plist の向きが横を含む", 1, "が縦だけでない", project=with_file,
                  plists={"Config/App-Settings.plist": dict(microphone, UISupportedInterfaceOrientations=[
                      "UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft"])})
    # plist の値の変数は、構成・SDK ごとに展開してから判定する
    mic_variable = dict(microphone, NSMicrophoneUsageDescription="$(MIC_TEXT)")
    define = lambda value: lambda t: replaced(with_file(t), '"INFOPLIST_FILE": "Config/App-Settings.plist",',  # noqa: E731
                                              f'"INFOPLIST_FILE": "Config/App-Settings.plist",\n        "MIC_TEXT": "{value}",')
    settings_case("plist の値の変数が空でない値に展開される", 0, "OK", project=define("会議を録音します。"),
                  plists={"Config/App-Settings.plist": mic_variable})
    settings_case("plist の値の変数が空に展開される", 1, "NSMicrophoneUsageDescription が空", project=define(""),
                  plists={"Config/App-Settings.plist": mic_variable})
    settings_case("plist の値の変数を展開できない", 1, "NSMicrophoneUsageDescription がない", project=with_file,
                  plists={"Config/App-Settings.plist": mic_variable})
    settings_case("ビルド設定の値の変数を展開できない", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "$(MIC_TEXT)",'))
    # 効きうる候補のうち 1 つでも展開できなければ、ほかの候補が合格でも構成全体を合格にしない
    settings_case("ビルド設定は合格でも参照先の plist の値を展開できない", 1, "展開できない",
                  project=info_plist_file("Config/App-Settings.plist"), plists={"Config/App-Settings.plist": mic_variable})
    settings_case("参照先の plist は合格でもビルド設定の値を展開できない", 1, "展開できない",
                  project=lambda t: replaced(info_plist_file("Config/App-Settings.plist")(t),
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "$(MIC_TEXT)",'),
                  plists={"Config/App-Settings.plist": microphone})
    # 有効にすると違反になる鍵（UIFileSharingEnabled など）の値の変数は、構成・SDK ごとに展開してから判定する
    sharing = lambda value, define="": lambda t: replaced(  # noqa: E731
        t, '"GENERATE_INFOPLIST_FILE": "YES",',
        f'"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_KEY_UIFileSharingEnabled": "{value}",{define}')
    settings_case("ビルド設定の変数が YES に展開される", 1, "UIFileSharingEnabled を有効にしない",
                  project=sharing("$(SHARING)", '\n        "SHARING": "YES",'))
    settings_case("ビルド設定の変数が NO に展開される", 0, "OK", project=sharing("$(SHARING)", '\n        "SHARING": "NO",'))
    settings_case("ビルド設定の変数を展開できない", 1, "UIFileSharingEnabled の値を展開できない", project=sharing("$(SHARING)"))
    # $(inherited) は空にせず継承元の値で展開する。継承元を決められなければ展開できないものとして扱う
    project_level = lambda define: lambda t: replaced(  # noqa: E731
        t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n  },', f'"IPHONEOS_DEPLOYMENT_TARGET": "27.2",{define}\n  }},')
    settings_case("$(inherited) がプロジェクトの YES を継承する", 1, "UIFileSharingEnabled を有効にしない",
                  project=lambda t: project_level('\n    "SHARING": "YES",')(
                      sharing("$(SHARING)", '\n        "SHARING": "$(inherited)",')(t)))
    settings_case("$(inherited) がプロジェクトの NO を継承する", 0, "OK",
                  project=lambda t: project_level('\n    "SHARING": "NO",')(
                      sharing("$(SHARING)", '\n        "SHARING": "$(inherited)",')(t)))
    settings_case("プロジェクトの段の $(inherited) は継承元を決められない", 1, "UIFileSharingEnabled の値を展開できない",
                  project=project_level('\n    "INFOPLIST_KEY_UIFileSharingEnabled": "$(inherited)",'))
    # 条件付きの書き方の値は、その条件に合う構成でだけ展開して判定する
    for label, debug_value, want_code, want_text in (("Debug は NO", "NO", 0, "OK"), ("Debug は YES", "YES", 1, "有効にしない")):
        settings_case(f"Debug だけの書き方を Release の値で判定しない（{label}）", want_code, want_text,
                      project=lambda t, v=debug_value: replaced(
                          t, '"GENERATE_INFOPLIST_FILE": "YES",',
                          '"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_KEY_UIFileSharingEnabled[config=Debug]": "$(SHARING)",'
                          f'\n        "SHARING[config=Debug]": "{v}",\n        "SHARING[config=Release]": "YES",'))
    settings_case("参照先の plist の変数が YES に展開される", 1, "UIFileSharingEnabled を有効にしない",
                  project=lambda t: replaced(info_plist_file("Config/App-Settings.plist")(t), '"GENERATE_INFOPLIST_FILE": "YES",',
                                             '"GENERATE_INFOPLIST_FILE": "YES",\n        "SHARING": "YES",'),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, UIFileSharingEnabled="$(SHARING)")})
    settings_case("どのターゲットも参照しない plist の変数は判定できない", 1, "UIFileSharingEnabled の値を展開できない",
                  plists={"Other/Info.plist": {"UIFileSharingEnabled": "$(SHARING)"}})
    settings_case("Info.plist を生成しないとビルド設定のマイクの利用目的は使われない", 1, "NSMicrophoneUsageDescription がない",
                  project=generate_no)
    settings_case("GENERATE_INFOPLIST_FILE がない（既定は生成しない）", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: replaced(t, '        "GENERATE_INFOPLIST_FILE": "YES",\n', ""))
    settings_case("生成しない Info.plist にマイクの利用目的がある", 0, "OK",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": microphone})
    settings_case("生成しない Info.plist のマイクの利用目的が空", 1, "Config/App-Settings.plist の NSMicrophoneUsageDescription が空",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": {"NSMicrophoneUsageDescription": " "}})
    settings_case("生成しない構成で、条件付きの参照先の 1 つにマイクの利用目的がない", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: generate_no(replaced(
                      info_plist_file("Config/App-Settings.plist")(t), '"INFOPLIST_FILE": "Config/App-Settings.plist",',
                      '"INFOPLIST_FILE": "Config/App-Settings.plist",\n        "INFOPLIST_FILE[sdk=iphoneos*]": "Config/Device.plist",')),
                  plists={"Config/App-Settings.plist": microphone, "Config/Device.plist": OK_INFO_PLIST})
    settings_case("生成しない構成で、条件付きの参照先のすべてにマイクの利用目的がある", 0, "OK",
                  project=lambda t: generate_no(replaced(
                      info_plist_file("Config/App-Settings.plist")(t), '"INFOPLIST_FILE": "Config/App-Settings.plist",',
                      '"INFOPLIST_FILE": "Config/App-Settings.plist",\n        "INFOPLIST_FILE[sdk=iphoneos*]": "Config/Device.plist",')),
                  plists={"Config/App-Settings.plist": microphone, "Config/Device.plist": microphone})
    settings_case("マイクの利用目的を参照先の Info.plist だけに置く", 0, "OK",
                  project=lambda t: info_plist_file("Config/App-Settings.plist")(without_key(t)),
                  plists={"Config/App-Settings.plist": microphone})
    settings_case("生成する場合も参照先の Info.plist のマイクの利用目的が空なら違反", 1, "NSMicrophoneUsageDescription が空",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": {"NSMicrophoneUsageDescription": ""}})
    # SDK の条件付きの値は、条件に合わない SDK を参照先の Info.plist で補えているかを確かめる
    settings_case("条件のないマイクの利用目的はすべての SDK で効く", 0, "OK")
    settings_case("シミュレータの SDK だけにマイクの利用目的があり、実機用が欠ける", 1, "Debug（iphoneos）",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription":', '"INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphonesimulator*]":'))
    settings_case("シミュレータの SDK だけの値を、参照先の Info.plist が実機用に補う", 0, "OK",
                  project=lambda t: info_plist_file("Config/App-Settings.plist")((lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription":', '"INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphonesimulator*]":'))(t)),
                  plists={"Config/App-Settings.plist": microphone})
    # SDK の条件は版つきの SDK 名（iphoneos27.2 など）と照合する。空の条件はどの SDK にも当たらない
    settings_case("版つきの SDK の条件も検査する", 1, "SWIFT_VERSION[sdk=iphoneos2*] = 5.0",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0",',
                                             '"SWIFT_VERSION": "6.0",\n        "SWIFT_VERSION[sdk=iphoneos2*]": "5.0",'))
    settings_case("版つきの SDK の条件で実機とシミュレータの両方を満たす", 0, "OK",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphoneos2*]": "会議を録音するためにマイクを使用します。",\n'
                                             '        "INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=iphonesimulator*]": "会議を録音するためにマイクを使用します。",'))
    settings_case("空の SDK の条件の値は、条件のない値を上書きしない", 1, "Debug（iphoneos）",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription":',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=]":'))
    # arch の条件付きの参照先は、別の arch の構成を補わない（条件なしに決まらない）
    settings_case("arch の条件付きの参照先だけでは補えない", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t).replace(
                      '"INFOPLIST_FILE":', '"INFOPLIST_FILE[arch=arm64]":')),
                  plists={"Config/App-Settings.plist": microphone})
    # 生成の指定が arch の条件付きだけの構成は、生成しない場合も起こりうるので参照先の plist も求める
    settings_case("生成の指定が arch の条件付きだけ", 1, "NSMicrophoneUsageDescription がない",
                  project=lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",', '"GENERATE_INFOPLIST_FILE[arch=arm64]": "YES",'))
    settings_case("生成の指定が arch の条件付きだけでも、参照先の plist があれば合格", 0, "OK",
                  project=lambda t: info_plist_file("Config/App-Settings.plist")(t).replace(
                      '"GENERATE_INFOPLIST_FILE": "YES",', '"GENERATE_INFOPLIST_FILE[arch=arm64]": "YES",'),
                  plists={"Config/App-Settings.plist": microphone})
    # CODE_SIGN_ENTITLEMENTS が指すファイルは、拡張子にかかわらず検査する
    def entitlements_file(value: str) -> Callable[[str], str]:
        return lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                  f'"GENERATE_INFOPLIST_FILE": "YES",\n        "CODE_SIGN_ENTITLEMENTS": "{value}",')
    settings_case("CODE_SIGN_ENTITLEMENTS が指すファイルの違反", 1, "aps-environment",
                  project=entitlements_file("Config/App-Signing.plist"),
                  plists={"Config/App-Signing.plist": {"aps-environment": "development"}})
    settings_case("CODE_SIGN_ENTITLEMENTS が指す合格のファイル", 0, "OK",
                  project=entitlements_file("$(SRCROOT)/Config/App-Signing.plist"),
                  plists={"Config/App-Signing.plist": {"com.apple.security.application-groups": ["group.sample"]}})
    settings_case("CODE_SIGN_ENTITLEMENTS が指すファイルがない", 2, "読めない", project=entitlements_file("Config/Missing.plist"))
    settings_case("CODE_SIGN_ENTITLEMENTS の展開できない変数", 2, "展開できない",
                  project=entitlements_file("$(UNKNOWN)/App.entitlements"))
    settings_case("読めない設定は合格にしない", 2, "読めない", project=lambda t: t + "{")
    settings_case("ターゲットがない設定は合格にしない", 2, "読めない", project=lambda t: '{"build-settings": {}, "targets": []}')
    # 除外するディレクトリ（build など）は root 直下だけ。下位の同じ名前のディレクトリは検査する
    ats = {"NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True}}
    settings_case("root 直下の除外するディレクトリは検査しない", 0, "OK", plists={"build/Info.plist": ats})
    settings_case("下位の階層の同じ名前のディレクトリは検査する", 1, "NSAppTransportSecurity",
                  plists={"Sources/build/Info.plist": ats})
    # 成果物・個人の設定に固有の名前（DerivedData・.build・xcuserdata）は、どの階層でも除外する
    for generated in ("Sources/DerivedData", "Packages/MinutesKit/.build", "MinutesMobile.xcodeproj/xcuserdata"):
        settings_case(f"下位の階層の {generated.rsplit('/', 1)[1]} は検査しない", 0, "OK",
                      plists={f"{generated}/Info.plist": ats})


def dependencies_case(label: str, want_code: int, want_text: str, files: Dict[str, str], packages: Optional[List[dict]] = None,
                      licenses: Optional[List[str]] = None) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content, encoding="utf-8")
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps({
            "formatVersion": 1,
            "allowedLicenses": licenses if licenses is not None else ["MIT", "Apache-2.0"],
            "approvedPackages": packages or [],
        }), encoding="utf-8")
        command = [str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)]
        expect(f"dependencies: {label}", command, want_code, want_text)


def test_dependencies() -> None:
    global count
    spec = importlib.util.spec_from_file_location("check_dependencies", SCRIPTS / "check-dependencies.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pin = {"identity": "sample-lib", "kind": "remoteSourceControl", "location": "https://example.com/owner/sample-lib.git",
           "state": {"revision": "0000000000000000000000000000000000000000", "version": "1.2.3"}}
    resolved = json.dumps({"pins": [pin], "version": 3})
    branch = json.dumps({"pins": [dict(pin, state={"branch": "main", "revision": "0" * 40})], "version": 3})
    approved = {"identity": "sample-lib", "url": "https://example.com/owner/sample-lib", "version": "1.2.3",
                "license": "MIT", "approvedOn": "2026-11-16", "ledger": "dependencies-and-costs.md §3"}
    manifest = 'let package = Package(name: "A", dependencies: [.package(url: "https://example.com/owner/sample-lib.git", from: "1.2.3")])\n'
    local = 'let package = Package(name: "A", dependencies: [.package(path: "Local")])\n'
    local_manifest = 'let package = Package(name: "Local")\n'
    project = '{"targets": [], "packages": [{"location": "https://example.com/owner/sample-lib"},],}'

    dependencies_case("依存がない", 0, "OK", {})
    dependencies_case("許可リストにない依存（Package.resolved）", 1, "sample-lib 1.2.3", {"Package.resolved": resolved})
    dependencies_case("承認済みの依存", 0, "OK", {"Package.resolved": resolved, "Package.swift": manifest}, [approved])
    dependencies_case("承認した版と違う", 1, "sample-lib 1.2.3", {"Package.resolved": resolved}, [dict(approved, version="1.2.2")])
    dependencies_case("版で固定していない", 1, "版で固定されていない", {"Package.resolved": branch}, [approved])
    dependencies_case("許可リストにない依存（Package.swift）", 1, "sample-lib.git", {"Sub/Package.swift": manifest})
    dependencies_case("ローカルの Package は認める", 0, "OK", {"Package.swift": local, "Local/Package.swift": local_manifest})
    dependencies_case("許可リストにない依存（project.xcproj）", 1, "example.com", {"App.xcodeproj/project.xcproj": project})
    # project.xcproj の承認済みの依存は、同じ .xcodeproj の中の Package.resolved に確定版が要る
    workspace_resolved = "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
    dependencies_case("project.xcproj の依存に確定版がない", 1, "の確定版がない",
                      {"App.xcodeproj/project.xcproj": project}, [approved])
    dependencies_case("project.xcproj の依存を別の場所の Package.resolved とは照合しない", 1, "の確定版がない",
                      {"App.xcodeproj/project.xcproj": project, "Package.resolved": resolved}, [approved])
    # 確定版があっても、要求条件の書き方が未確認のうちは合格にしない
    dependencies_case("project.xcproj の依存の要求条件は判定できない", 1, "要求条件を判定できない",
                      {"App.xcodeproj/project.xcproj": project, workspace_resolved: resolved}, [approved])
    dependencies_case("許可の対象外のライセンスは、オーナーの決定の記録が要る", 2, "ownerDecision",
                      {}, [dict(approved, license="GPL-3.0")])
    dependencies_case("オーナーの決定を記録した依存", 0, "OK",
                      {"Package.resolved": resolved}, [dict(approved, license="GPL-3.0", ownerDecision="UD-99")])
    dependencies_case("承認日のない項目は読まない", 2, "approvedOn", {}, [{k: v for k, v in approved.items() if k != "approvedOn"}])
    dependencies_case("読めない Package.resolved は合格にしない", 2, "読めない", {"Package.resolved": "{"})
    moved = json.dumps({"pins": [dict(pin, location="https://example.org/other/sample-lib.git")], "version": 3})
    dependencies_case("identity と版が同じでも取得元が違う", 1, "取得元", {"Package.resolved": moved}, [approved])
    unknown_source = json.dumps({"pins": [{k: v for k, v in pin.items() if k != "location"}], "version": 3})
    dependencies_case("取得元のない pin は合格にしない", 1, "取得元", {"Package.resolved": unknown_source}, [approved])
    legacy = json.dumps({"object": {"pins": [{"package": "sample-lib", "repositoryURL": "https://example.com/owner/sample-lib",
                                              "state": {"version": "1.2.3"}}]}, "version": 1})
    dependencies_case("古い形式の取得元（repositoryURL）", 0, "OK", {"Package.resolved": legacy}, [approved])
    declare = 'let package = Package(name: "A", dependencies: [{}])\n'
    for label, declaration in {
        "URL が変数": "let u = \"https://example.com/owner/sample-lib\"\n.package(url: u, from: \"1.2.3\")",
        "URL が連結": '.package(url: "https://example.com/owner/sample-lib" + suffix, from: "1.2.3")',
        "URL が補間": '.package(url: "https://\\(host)/owner/sample-lib", from: "1.2.3")',
        "名前つきの URL が変数": '.package(name: "sample-lib", url: u, from: "1.2.3")',
        "バイナリのターゲットの URL が変数": '.binaryTarget(name: "B", url: u, checksum: "0")',
        "path が変数": ".package(path: local)",
    }.items():
        dependencies_case(f"取得元を判定できない宣言（{label}）", 1, "判定できない",
                          {"Package.swift": declare.format(declaration)}, [approved])
    dependencies_case("レジストリの依存は認めない", 1, "レジストリ",
                      {"Package.swift": declare.format('.package(id: "owner.sample-lib", from: "1.2.3")')}, [approved])
    dependencies_case("製品の参照（package:）は宣言として数えない", 0, "OK",
                      {"Package.resolved": resolved,
                       "Package.swift": manifest + '.target(name: "T", dependencies: [.product(name: "S", package: "sample-lib")])\n'},
                      [approved])
    # Package.swift の版の指定は、同じディレクトリの Package.resolved の確定版と照合する
    dependencies_case("Package.resolved がない依存は合格にしない", 1, "確定版がない", {"Package.swift": manifest}, [approved])
    dependencies_case("別のディレクトリの Package.resolved とは照合しない", 1, "確定版がない",
                      {"Package.resolved": resolved, "Sub/Package.swift": manifest}, [approved])
    url = "https://example.com/owner/sample-lib.git"
    for label, declaration in {
        "branch 指定": f'.package(url: "{url}", branch: "main")',
        "revision 指定": f'.package(url: "{url}", revision: "{"0" * 40}")',
        "branch の関数": f'.package(url: "{url}", .branch("main"))',
    }.items():
        dependencies_case(f"版で固定していない宣言（{label}）", 1, "版で固定されていない",
                          {"Package.resolved": resolved, "Package.swift": declare.format(declaration)}, [approved])
    dependencies_case("宣言の版と承認済みの確定版が違う", 1, "合わない",
                      {"Package.resolved": resolved, "Package.swift": declare.format(f'.package(url: "{url}", exact: "2.0.0")')},
                      [approved])
    dependencies_case("宣言の下限が確定版より新しい", 1, "合わない",
                      {"Package.resolved": resolved, "Package.swift": declare.format(f'.package(url: "{url}", from: "1.3.0")')},
                      [approved])
    dependencies_case("版の指定を判定できない宣言", 1, "版の指定を判定できない",
                      {"Package.resolved": resolved, "Package.swift": declare.format(f'.package(url: "{url}", from: version)')},
                      [approved])
    for label, declaration in {
        "exact": f'.package(url: "{url}", exact: "1.2.3")',
        "upToNextMinor": f'.package(url: "{url}", .upToNextMinor(from: "1.2.0"))',
        "半開区間": f'.package(url: "{url}", "1.0.0"..<"2.0.0")',
        "閉区間": f'.package(url: "{url}", "1.0.0"..."1.2.3")',
    }.items():
        dependencies_case(f"確定版を含む版の指定（{label}）", 0, "OK",
                          {"Package.resolved": resolved, "Package.swift": declare.format(declaration)}, [approved])
    # コメントの中の宣言は依存として数えない（文字列の中の「//」はコメントではない）
    unlisted = '.package(url: "https://example.org/other/unlisted.git", from: "1.0.0")'
    for label, declaration in {
        "行コメント": f"// {unlisted}",
        "ブロックコメント": f"/* {unlisted} */",
        "入れ子のブロックコメント": f"/* /* 内側 */ {unlisted} */",
    }.items():
        dependencies_case(f"コメントだけの依存宣言（{label}）", 0, "OK", {"Package.swift": declare.format(declaration)})
    dependencies_case("コメントの後ろの宣言は検査する", 1, "unlisted.git", {"Package.swift": declare.format(f"/* x */ {unlisted}")})
    # 補間の中の文字列で外側の文字列が閉じたと見なすと、補間の中の「/*」「//」がコメントの始まりに見え、宣言を隠す
    for label, declaration in {
        "ブロックコメントの記号": f'let s = "\\(f("/*"))"\n{unlisted}\n// */',
        "行コメントの記号": f'let s = "\\(f("//"))"; {unlisted}',
        "生の文字列": f'let s = #"\\#(f("/*"))"#\n{unlisted}\n// */',
        "括弧を含む文字列": f'let s = "\\(g(")") + "/*")"\n{unlisted}\n// */',
    }.items():
        dependencies_case(f"補間の中の文字列（{label}）で宣言を隠さない", 1, "unlisted.git",
                          {"Package.swift": declare.format(declaration)})
    # 補間の中のコメントは、括弧や引用符を解釈する前に読み飛ばす。コメントの中の「)」で補間が早く閉じたと見なすと、
    # 補間の中の文字列の引用符で外側の文字列が閉じ、その後ろの「/*」がコメントの始まりに見えて宣言を隠す
    for label, declaration in {
        "ブロックコメントの閉じ括弧": f'let s = "\\(f(x /* ) */) + "/*")"\n{unlisted}\n// */',
        "入れ子のブロックコメント": f'let s = "\\(f(x /* /* ) */ ) */) + "/*")"\n{unlisted}\n// */',
        "行コメント（複数行の文字列）": f'let s = """\n\\(f(x // )\n), g("""\n/*\n"""))\n"""\n{unlisted}\n// */',
    }.items():
        dependencies_case(f"補間の中のコメント（{label}）で宣言を隠さない", 1, "unlisted.git",
                          {"Package.swift": declare.format(declaration)})
    # 補間の中の式は実行されるコードなので、中の宣言を検査する。本文の宣言らしい文字列は宣言と見なさない
    dependencies_case("補間の中の宣言は検査する", 1, "unlisted.git", {"Package.swift": declare.format(f'"\\({unlisted})"')})
    dependencies_case("生の文字列の補間の中の宣言は検査する", 1, "unlisted.git",
                      {"Package.swift": declare.format(f'#"\\#({unlisted})"#')})
    dependencies_case("文字列の本文の .binaryTarget は宣言と見なさない", 0, "OK",
                      {"Package.swift": declare.format('"see .binaryTarget(name: B, url: x) here"')})
    dependencies_case("通常の補間の後ろのコメントは除く", 0, "OK",
                      {"Package.swift": declare.format(f'let s = "\\(f(x))"\n// {unlisted}')})
    # 行コメントは CR・LF・CRLF のどれでも終わる（CR だけの改行で後ろの宣言をコメントに含めない）
    for label, newline in {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}.items():
        text = declare.format(f"// コメント\n{unlisted}\n").replace("\n", newline)
        dependencies_case(f"行コメントの後ろの宣言は検査する（{label}）", 1, "unlisted.git", {"Package.swift": text})
    # CR だけの改行で終わる行コメントを、strip_comments に直接渡して確かめる（ファイルの読み書きで改行が変わらないように）
    count += 1
    stripped = module.strip_comments(f"// コメント\r{unlisted}\r")
    if unlisted not in stripped or "コメント" in stripped:
        failures.append(f"dependencies: CR で終わる行コメントの後ろの宣言が strip_comments の結果に残らない: {stripped!r}")
    dependencies_case("閉じていないブロックコメントは合格にしない", 2, "閉じていない",
                      {"Package.swift": declare.format(f"/* {unlisted}")})
    # URL の正規化は scheme とホストだけ。パスの大文字小文字が違う取得元は別のものとして認めない
    upper_path = json.dumps({"pins": [dict(pin, location="https://example.com/Owner/sample-lib.git")], "version": 3})
    dependencies_case("パスの大文字小文字だけが違う取得元", 1, "取得元", {"Package.resolved": upper_path}, [approved])
    upper_host = json.dumps({"pins": [dict(pin, location="HTTPS://EXAMPLE.COM/owner/sample-lib.git")], "version": 3})
    dependencies_case("scheme とホストの大文字小文字は同一視する", 0, "OK", {"Package.resolved": upper_host}, [approved])
    dependencies_case("Package.swift でパスの大文字小文字だけが違う URL", 1, "許可リストにない",
                      {"Package.resolved": resolved,
                       "Package.swift": declare.format('.package(url: "https://example.com/Owner/sample-lib.git", from: "1.2.3")')},
                      [approved])
    # 文字列リテラルの本文に現れる宣言は依存として数えない（文字列の外の宣言は検査する）
    for label, declaration in {
        "通常の文字列": f'let s = "{unlisted.replace(chr(34), chr(92) + chr(34))}"',
        "生の文字列": f'let s = #"{unlisted}"#',
        "複数行の文字列": f'let s = """\n{unlisted}\n"""',
    }.items():
        dependencies_case(f"文字列リテラルの中の宣言（{label}）", 0, "OK", {"Package.swift": declare.format(declaration)})
    dependencies_case("文字列リテラルの後ろの宣言は検査する", 1, "unlisted.git",
                      {"Package.swift": declare.format(f'let s = #"{unlisted}"#\n{unlisted}')})
    dependencies_case("引数の中の文字列にある url: を取得元と見なさない", 1, "取得元を判定できない",
                      {"Package.swift": declare.format('.package(name: "url: \\"https://example.com/owner/sample-lib\\",", path: local)')})
    # ローカルのバイナリ（.binaryTarget(path:)）は、承認済みの項目がある場合だけ認める
    binary = '.binaryTarget(name: "B", path: "Frameworks/B.xcframework")'
    # 承認したときの内容（ディレクトリ形式の成果物。配下のファイルも内容のハッシュに入る）
    binary_files = {"Sub/Frameworks/B.xcframework/Info.plist": "info", "Sub/Frameworks/B.xcframework/ios/B": "bin"}
    with tempfile.TemporaryDirectory() as directory:
        for name, content in binary_files.items():
            (Path(directory) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(directory) / name).write_text(content, encoding="utf-8")
        binary_digest = module.content_digest(Path(directory) / "Sub/Frameworks/B.xcframework")
    approved_binary = {"identity": "b", "path": "Sub/Frameworks/B.xcframework", "sha256": binary_digest, "version": "1.0.0",
                       "license": "MIT", "approvedOn": "2026-11-18", "ledger": "dependencies-and-costs.md §3"}
    dependencies_case("記録のないローカルのバイナリ", 1, "ローカルのバイナリ Sub/Frameworks/B.xcframework",
                      {"Sub/Package.swift": declare.format(binary)})
    dependencies_case("承認済みのローカルのバイナリ", 0, "OK", dict(binary_files, **{"Sub/Package.swift": declare.format(binary)}),
                      [approved_binary])
    # 場所が一致しても、承認の後に内容（配下のファイルを含む）が変われば再承認を求める
    changed = dict(binary_files, **{"Sub/Frameworks/B.xcframework/ios/B": "replaced", "Sub/Package.swift": declare.format(binary)})
    dependencies_case("承認の後に内容が変わったローカルのバイナリ", 1, "内容が承認したものと違う", changed, [approved_binary])
    added = dict(binary_files, **{"Sub/Frameworks/B.xcframework/ios/extra": "x", "Sub/Package.swift": declare.format(binary)})
    dependencies_case("承認の後にファイルが増えたローカルのバイナリ", 1, "内容が承認したものと違う", added, [approved_binary])
    dependencies_case("承認済みのローカルのバイナリがない", 2, "ローカルのバイナリがない",
                      {"Sub/Package.swift": declare.format(binary)}, [approved_binary])
    dependencies_case("ローカルのバイナリの項目に sha256 がない", 2, "sha256",
                      {}, [{k: v for k, v in approved_binary.items() if k != "sha256"}])
    # リモートのバイナリは、承認した配布物の checksum と宣言の checksum の一致を求める
    remote = "https://example.com/owner/b-binary/releases/download/1.0.0/B.xcframework.zip"
    remote_binary = {"identity": "b-binary", "url": remote, "version": "1.0.0", "license": "MIT", "approvedOn": "2026-11-18",
                     "ledger": "dependencies-and-costs.md §3", "checksum": "a" * 64}
    remote_declaration = '.binaryTarget(name: "B", url: "' + remote + '", checksum: "{}")'
    dependencies_case("承認した checksum のリモートのバイナリ", 0, "OK",
                      {"Package.swift": declare.format(remote_declaration.replace("{}", "a" * 64))}, [remote_binary])
    dependencies_case("checksum が承認と違うリモートのバイナリ", 1, "承認したもの",
                      {"Package.swift": declare.format(remote_declaration.replace("{}", "b" * 64))}, [remote_binary])
    dependencies_case("承認に checksum のないリモートのバイナリ", 1, "checksum がない",
                      {"Package.swift": declare.format(remote_declaration.replace("{}", "a" * 64))},
                      [{k: v for k, v in remote_binary.items() if k != "checksum"}])
    dependencies_case("checksum が変数のリモートのバイナリ", 1, "checksum を判定できない",
                      {"Package.swift": declare.format('.binaryTarget(name: "B", url: "' + remote + '", checksum: sum)')}, [remote_binary])
    dependencies_case("checksum の形式が不正な承認", 2, "16 進数", {}, [dict(remote_binary, checksum="xyz")])
    dependencies_case("別のディレクトリのローカルのバイナリは承認と一致しない", 1, "ローカルのバイナリ Frameworks/B.xcframework",
                      {"Package.swift": declare.format(binary)}, [approved_binary])
    dependencies_case("ローカルのバイナリの項目に版がない", 2, "version",
                      {}, [{k: v for k, v in approved_binary.items() if k != "version"}])
    dependencies_case("url と path の両方を持つ項目は読まない", 2, "どちらか一方",
                      {}, [dict(approved_binary, url="https://example.com/owner/b")])
    dependencies_case("起点の中のローカルの Package（.package(path:)）は認める", 0, "OK",
                      {"Sub/Package.swift": declare.format('.package(path: "../Local")'), "Local/Package.swift": local_manifest})
    # ローカルの Package の参照先は、起点の中で検査される manifest であること（検査できない参照先を合格にしない）
    dependencies_case("起点の外のローカルの Package", 1, "起点の外",
                      {"Package.swift": declare.format('.package(path: "../Outside")')})
    dependencies_case("manifest のないローカルの Package", 1, "manifest がない",
                      {"Package.swift": declare.format('.package(path: "Local")')})
    dependencies_case("除外するディレクトリの中のローカルの Package", 1, "検査の対象外",
                      {"Package.swift": declare.format('.package(path: "build/Local")'), "build/Local/Package.swift": local_manifest})
    # シンボリックリンクを解決した先が除外するディレクトリなら、リンクの名前が通常の場所でも認めない
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "build" / "Local").mkdir(parents=True)
        (root / "build" / "Local" / "Package.swift").write_text(local_manifest, encoding="utf-8")
        (root / "Local").symlink_to("build/Local")
        (root / "Package.swift").write_text(declare.format('.package(path: "Local")'), encoding="utf-8")
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
        expect("dependencies: 除外するディレクトリを指すリンクのローカルの Package", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 1, "検査の対象外")
    # 参照先の manifest の未承認のバイナリも検査する（起点の中の manifest はすべて走査する）
    dependencies_case("ローカルの Package の中の未承認のバイナリ", 1, "ローカルのバイナリ Local/Frameworks/B.xcframework",
                      {"Package.swift": declare.format('.package(path: "Local")'), "Local/Package.swift": declare.format(binary)})
    # バッククォートで囲んだ識別子（.`package`(…)）も同じ宣言として検査する
    for label, declaration in {
        "package": '.`package`(url: "https://example.org/other/unlisted.git", from: "1.0.0")',
        "binaryTarget": '.`binaryTarget`(name: "B", url: "https://example.org/other/unlisted.zip", checksum: "' + "a" * 64 + '")',
    }.items():
        dependencies_case(f"バッククォートの宣言（{label}）", 1, "unlisted",
                          {"Package.swift": declare.format(declaration)})
    # 呼び出しの形でないメンバーの参照（関数の値として渡すなど）は、宣言を判定できないので合格にしない
    dependencies_case("呼び出しでない .package の参照", 1, "判定できない",
                      {"Package.swift": declare.format("let make = Package.Dependency.package\nmake(url: u, from: v)")})
    dependencies_case("let package = Package( は宣言として数えない", 0, "OK", {"Package.swift": declare.format("")})
    # ローカルのバイナリの中のシンボリックリンクは、成果物の中を指すものだけを認める
    with tempfile.TemporaryDirectory() as directory:
        framework = Path(directory) / "B.xcframework"
        (framework / "ios").mkdir(parents=True)
        (framework / "ios" / "B").write_text("bin", encoding="utf-8")
        (framework / "Current").symlink_to("ios")
        count += 1
        try:
            module.content_digest(framework)
        except module.Unreadable as error:
            failures.append(f"dependencies: 成果物の中を指すリンクを拒否した: {error}")
        for label, target in {"外の相対パス": "../../outside", "絶対パス": "/etc/hosts", "外を指すリンクの連鎖": "Chain"}.items():
            (framework / "Bad").unlink() if (framework / "Bad").is_symlink() else None
            if label == "外を指すリンクの連鎖":
                (framework / "Chain").symlink_to("../outside")
            (framework / "Bad").symlink_to(target)
            count += 1
            try:
                module.content_digest(framework)
                failures.append(f"dependencies: 成果物の外を指すリンク（{label}）を受け入れた")
            except module.Unreadable as error:
                # 拒否した項目が Bad であること（連鎖では、外を指す Chain だけを拒否して Bad を通すと合格にしない）
                if not str(error).startswith(f"{framework / 'Bad'}:"):
                    failures.append(f"dependencies: 成果物の外を指すリンク（{label}）で Bad 以外を拒否した: {error}")
    dependencies_case("区間の上限は含まない", 1, "合わない",
                      {"Package.resolved": resolved, "Package.swift": declare.format(f'.package(url: "{url}", "1.0.0"..<"1.2.3")')},
                      [approved])
    # 閉じていない文字列・補間は、後ろの宣言を本文として隠さないよう読めないものとして扱う
    dependencies_case("閉じていない文字列", 2, "文字列リテラルが閉じていない",
                      {"Package.swift": declare.format(unlisted) + 'let s = "abc\n'})
    dependencies_case("閉じていない補間", 2, "補間が閉じていない", {"Package.swift": declare.format(unlisted) + 'let s = "\\(x\n'})
    # 版別の manifest（Package@swift-<版>.swift）も同じ検査にかける
    # 除外の階層: 下位の build は検査し、直下の build と全階層の .build は除外する
    dependencies_case("下位の build の中の許可リスト外の依存", 1, "Sub/build/Package.swift",
                      {"Sub/build/Package.swift": declare.format(unlisted)})
    dependencies_case("直下の build は除外", 0, "OK", {"build/Package.swift": declare.format(unlisted)})
    dependencies_case("下位の .build は除外", 0, "OK", {"Sub/.build/Package.swift": declare.format(unlisted)})
    dependencies_case("版別の manifest の許可リスト外の依存", 1, "Package@swift-6.0.swift",
                      {"Package@swift-6.0.swift": declare.format(unlisted)})
    expect("dependencies: 存在しない起点", [str(SCRIPTS / "check-dependencies.py"), "--root", str(ROOT / "no-such-directory")],
           2, "起点のディレクトリがない")


def applies_here(line: str, name: str) -> bool:
    """台帳の行が、この Mac で setup-tools.sh が使う行か（名前が一致し、アーキテクチャがこの Mac のものか any）。"""
    return line.split()[:1] == [name] and line.split()[2:3] in ([platform.machine()], ["any"])


def test_versions() -> None:
    # 引数が 1 個あれば空文字列でも引数として扱い、--hooks との完全一致を求める（ツールの導入より前に止まる）
    expect("setup: 空文字列の引数", [str(SCRIPTS / "setup.sh"), ""], 2, "使い方")
    expect("setup: 知らない引数", [str(SCRIPTS / "setup.sh"), "--hook"], 2, "使い方")
    setup = str(SCRIPTS / "setup-tools.sh")
    tools = str(ROOT / ".tools")
    original = (ROOT / "tools" / "versions").read_text(encoding="utf-8")
    expect("versions: 導入済みの版が一致する", [setup, "--verify"], 0, "OK")
    # 版と SHA-256 の負例は、現在の swiftlint の行から値を取り出して変える（ツールを更新しても負例を作れるように）
    # この Mac に適用される行（setup-tools.sh と同じく、アーキテクチャが一致する行、なければ any の行）を変える
    # 行の位置も覚え、負例ではその行だけを書き換える（同じ内容のコメント行や別のアーキテクチャの行を変えない）
    swiftlint_rows = [(at, line) for at, line in enumerate(original.splitlines()) if line.split()[:1] == ["swiftlint"]]
    row_index, row = (next((item for item in swiftlint_rows if item[1].split()[2:3] == [platform.machine()]), None)
                      or next(item for item in swiftlint_rows if item[1].split()[2:3] == ["any"]))
    columns = row.split()
    current_version, current_sha = columns[1], columns[-1]
    parts = current_version.split(".")
    other_version = ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])  # 形式は正しいが導入済みと違う版
    other_sha = ("0" if current_sha[0] != "0" else "1") + current_sha[1:]

    def changed_row(index: int, value: str) -> Callable[[str], str]:
        def change(text: str) -> str:
            # 空白文字（タブを含む）で列に分け、index 番目の列だけを置き換える（一時の台帳なので空白の並びは保たない）
            fields = row.split()
            fields[index] = value
            lines = text.splitlines()
            assert lines[row_index] == row, f"置換の対象の行が変わった: {row}"
            lines[row_index] = " ".join(fields)
            return "\n".join(lines) + "\n"
        return change

    cases = {
        "版が違う": (changed_row(1, other_version), "swiftlint"),
        "SHA-256 が違う": (changed_row(len(columns) - 1, other_sha), "SHA-256"),
        "形式が不正": (changed_row(len(columns) - 1, "z" * len(current_sha)), "不正"),
        "ツールの行がない": (lambda t: "\n".join(line for line in t.splitlines() if line.startswith("#")) + "\n", "1 つもない"),
        "gh の行がほかのツールより後": (lambda t: "\n".join(
            [line for line in t.splitlines() if line.split()[:1] != ["gh"]] + [line for line in t.splitlines() if line.split()[:1] == ["gh"]]
        ) + "\n", "gh の行が"),
        # この Mac に適用される行（アーキテクチャが一致する行と any の行）をすべて除く（any の行が残ると欠落にならない）
        "この Mac 向けの行がない": (lambda t: "\n".join(
            line for line in t.splitlines() if not applies_here(line, "gitleaks")
        ) + "\n", "向けの行がない"),
        # 台帳からツールの行がすべて消えても、検査が使う必須のツールとして欠落を検出する
        "必須のツールの行がない": (lambda t: "\n".join(
            line for line in t.splitlines() if line.split()[:1] != ["swiftformat"]
        ) + "\n", "swiftformat に"),
    }
    # 末尾に改行のない最終行も読む（最終行のツールを読み落とすと「行がない」で失敗する）
    with tempfile.TemporaryDirectory() as directory:
        versions = Path(directory) / "versions"
        # この Mac 向けの gitleaks の行を最終行に移す（読み落とすと、その行だけが照合されなくなる）
        own = [line for line in original.splitlines() if applies_here(line, "gitleaks")]
        rest = [line for line in original.splitlines() if line not in own]
        versions.write_text("\n".join(rest + own), encoding="utf-8")
        expect("versions: 末尾に改行のない最終行", [setup, "--verify", "--versions", str(versions), "--dest", tools], 0, "OK")
    for label, (change, want_text) in cases.items():
        with tempfile.TemporaryDirectory() as directory:
            versions = Path(directory) / "versions"
            versions.write_text(change(original), encoding="utf-8")
            expect(f"versions: {label}", [setup, "--verify", "--versions", str(versions), "--dest", tools], 1, want_text)
    # タブ区切りの台帳も同じに読む（列を空白文字の種類で決めない）
    tabbed = "\n".join(line if line.startswith("#") else "\t".join(line.split()) for line in original.splitlines()) + "\n"
    for label, text, want_code, want_text in (
        ("タブ区切りの台帳", tabbed, 0, "OK"),
        ("タブ区切りの台帳で gh の行が後", cases["gh の行がほかのツールより後"][0](tabbed), 1, "gh の行が"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            versions = Path(directory) / "versions"
            versions.write_text(text, encoding="utf-8")
            expect(f"versions: {label}", [setup, "--verify", "--versions", str(versions), "--dest", tools], want_code, want_text)
    # 導入記録は一致するが、実行時の gh が別の版を名乗る（取得に使う gh の版を実行して確かめていること）
    # 列は空白文字（タブを含む）で分けて比べる（名前とアーキテクチャの列）
    gh_rows = [line for line in original.splitlines() if applies_here(line, "gh")]
    gh_rows.sort(key=lambda line: line.split()[2] == "any")  # アーキテクチャが一致する行を any の行より先にする
    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "tools"
        # 実行ファイル名は台帳の列（7 列目）から取る。公開するリンクの名前は bin/gh のまま
        version, executable, sha = gh_rows[0].split()[1], gh_rows[0].split()[6], gh_rows[0].split()[-1]
        stub = dest / "gh" / version / executable
        stub.parent.mkdir(parents=True)
        stub.write_text("#!/bin/sh\necho 'gh version 2.0.0 (2000-01-01)'\n", encoding="utf-8")
        stub.chmod(0o755)
        (stub.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
        (dest / "bin").mkdir()
        (dest / "bin" / "gh").symlink_to(f"../gh/{version}/{executable}")
        versions = Path(directory) / "versions"
        versions.write_text("\n".join(gh_rows) + "\n", encoding="utf-8")
        # gh の行だけの台帳なので、期待するツールの集合を gh だけと明示する
        expect("versions: 実行時の gh の版が違う",
               [setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"], 1, "実行時: 2.0.0")
        # 版を表示するコマンドが失敗する導入は、出力によらず合格にしない
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\nexit 3\n", encoding="utf-8")
        expect("versions: 版の表示が失敗する", [setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
               1, "版を表示できない")
        # リンクの先が導入記録のある版のディレクトリでなければ、実行時の版が合っていても合格にしない
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
        expect("versions: 導入記録のある版を指すリンク", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                 "--require", "gh"], 0, "OK")
        other = dest / "gh" / "other" / executable
        other.parent.mkdir()
        other.write_text(stub.read_text(encoding="utf-8"), encoding="utf-8")
        other.chmod(0o755)
        (dest / "bin" / "gh").unlink()
        (dest / "bin" / "gh").symlink_to(f"../gh/other/{executable}")
        expect("versions: リンクが導入記録のない場所を指す", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                  "--require", "gh"], 1, "導入先を指していない")
    # 配布ファイルの中の実行ファイル名が名前と違っても、検査が呼ぶリンクは名前（bin/<名前>）で照合する
    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "tools"
        fields = gh_rows[0].split()
        version, sha = fields[1], fields[-1]
        stub = dest / "gh" / version / "gh-cli"
        stub.parent.mkdir(parents=True)
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
        stub.chmod(0o755)
        (stub.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
        (dest / "bin").mkdir()
        (dest / "bin" / "gh").symlink_to(f"../gh/{version}/gh-cli")
        fields[6] = "gh-cli"
        versions = Path(directory) / "versions"
        versions.write_text(" ".join(fields) + "\n", encoding="utf-8")
        expect("versions: 実行ファイル名が名前と違う", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                               "--require", "gh"], 0, "OK: gh")


def test_gitleaks() -> None:
    gitleaks = str(ROOT / ".tools" / "bin" / "gitleaks")
    base = [gitleaks, "dir", "--no-banner", "--no-color", "--redact", "--config", str(ROOT / ".gitleaks.toml")]
    # 偽の鍵は実行時に組み立てる（このファイル自体が検出されないようにする）
    fake = "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "config.txt").write_text(f'token = "{fake}"\n', encoding="utf-8")
        expect("gitleaks: 偽の鍵を検出する", base + [directory], 1, "leaks found: 1")
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "config.txt").write_text('name = "sample"\n', encoding="utf-8")
        expect("gitleaks: 鍵のないディレクトリは通る", base + [directory], 0, "no leaks found")
    gitleaks_merge_case()


def gitleaks_merge_case() -> None:
    """履歴の検査（check.sh の手順 3）が、通常のコミットとマージのコミットで足した行の両方を検出すること。

    マージの解決で足した行（どちらの親にもない行）は、git log の既定ではマージの差分を出さないため見落とされる。
    check.sh の GITLEAKS_LOG_OPTS と同じ指定で実行し、指定がない場合は見落とすことも確かめる（試験が効いていることの確認）。
    """
    global count
    gitleaks = str(ROOT / ".tools" / "bin" / "gitleaks")
    options = "--all --full-history -m"
    count += 1
    if f'GITLEAKS_LOG_OPTS="{options}"' not in (SCRIPTS / "check.sh").read_text(encoding="utf-8"):
        failures.append(f"gitleaks: check.sh の GITLEAKS_LOG_OPTS が試験の指定（{options}）と違う")
    # 偽の鍵は実行時に組み立てる（このファイル自体が検出されないようにする）
    normal_key = "ghp_" + "a8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    merge_key = "ghp_" + "b9Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        repository.mkdir()

        # 開発者のグローバル・システムの設定とフック（core.hooksPath・テンプレートのフックを含む）の影響を受けないようにする
        # フックなどから実行された場合に残る、リポジトリを指す変数（GIT_DIR・GIT_INDEX_FILE など）も外す
        local_vars = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True,
                                    check=True).stdout.split()
        isolated = {key: value for key, value in os.environ.items() if key not in local_vars}
        isolated.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

        def git(*arguments: str) -> None:
            subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                            "-c", f"core.hooksPath={os.devnull}", *arguments], cwd=repository, env=isolated, check=True,
                           capture_output=True)

        def commit(name: str, content: str, message: str) -> None:
            (repository / name).write_text(content, encoding="utf-8")
            git("add", name)
            git("commit", "-q", "-m", message)

        git("init", "-q", "--template=", "-b", "main")
        commit("base.txt", "base\n", "base")
        git("checkout", "-q", "-b", "side")
        commit("normal.txt", f'token = "{normal_key}"\n', "side")
        git("checkout", "-q", "main")
        commit("main.txt", "main\n", "main")
        # 開発者の設定（merge.ff=only など）によらず二親のマージを作る
        git("merge", "-q", "--no-ff", "--no-commit", "side")
        commit("merge.txt", f'token = "{merge_key}"\n', "merge")  # マージのコミットだけで足した行
        for label, extra, want in (("指定あり", [f"--log-opts={options}"], {"normal.txt", "merge.txt"}),
                                   ("指定なし（既定の git log）", [], {"normal.txt"})):
            count += 1
            report = Path(directory) / f"report-{len(extra)}.json"
            result = subprocess.run([gitleaks, "git", "--no-banner", "--no-color", "--redact", "--config",
                                     str(ROOT / ".gitleaks.toml"), "--report-format", "json", "--report-path", str(report),
                                     *extra, str(repository)], capture_output=True, text=True, env=isolated)
            # どちらの場合も鍵を検出するので、終了コードは 1（検出あり）であること
            if result.returncode != 1:
                failures.append(f"gitleaks: マージの差分（{label}）の終了コードが {result.returncode}（期待 1）\n{result.stderr.strip()}")
            try:
                found = {str(item.get("File", "")) for item in json.loads(report.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError) as error:
                failures.append(f"gitleaks: マージの差分（{label}）の報告を読めない: {error}")
                continue
            if found != want:
                failures.append(f"gitleaks: マージの差分（{label}）で検出したファイルが {sorted(found)}（期待 {sorted(want)}）")


def coverage_case(label: str, want_code: int, want_text: str, count_value: object, covered_value: object) -> None:
    with tempfile.TemporaryDirectory() as directory:
        sources = Path(directory) / "Sources"
        sources.mkdir()
        report = Path(directory) / "report.json"
        report.write_text(json.dumps({"data": [{"files": [{"filename": str(sources / "A.swift"), "summary": {
            "lines": {"count": count_value, "covered": covered_value}}}]}]}), encoding="utf-8")
        expect(f"coverage: {label}", [str(SCRIPTS / "coverage-report.py"), str(report), "--sources", str(sources), "--target", "90"],
               want_code, want_text)


def test_coverage() -> None:
    coverage_case("目標以上", 0, "行 95.0%", 20, 19)
    coverage_case("目標未満は警告だけ", 0, "警告", 10, 5)
    # 行数の記録は整数（bool を除く）で 0 <= covered <= count のものだけを数える
    coverage_case("covered が count を超える", 2, "不正", 10, 11)
    coverage_case("covered が負", 2, "不正", 10, -1)
    coverage_case("count が文字列", 2, "不正", "10", 5)
    coverage_case("count が真偽値", 2, "不正", True, 0)
    coverage_case("covered が小数", 2, "不正", 10, 5.0)


# 文書の検査の試験に使う、最小の文書一式（定義文書・基本設計・詳細設計・コード片の元のファイル）
FENCE = "`" * 3
DOCS = {
    "docs/README.md": "# 索引\n\n| ID | 決定 |\n| --- | --- |\n| UD-01 | 決定 |\n",
    "docs/01-requirements/requirements.md": (
        "# 要件\n\n| ID | 要件 | フェーズ |\n| --- | --- | --- |\n"
        "| FR-AAA-01 | 一 | MVP |\n| FR-AAA-02 | 二 | MVP |\n| FR-AAA-03 | 三 | P2 |\n\n"
        "| ID | 制約 |\n| --- | --- |\n| CON-01 | 制約 |\n"
    ),
    "docs/01-requirements/security-requirements.md": "# SEC\n\n| ID | 要件 | フェーズ |\n| --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP |\n",
    "docs/01-requirements/test-requirements.md": "# TST\n\n| ID | 要件 |\n| --- | --- |\n| TST-AAA-01 | 一 |\n",
    "docs/01-requirements/dependencies-and-costs.md": "# COST\n\n| ID | 項目 |\n| --- | --- |\n| COST-01 | 一 |\n",
    "docs/02-basic-design/sample.md": (
        "# 基本設計\n\n| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |\n\n"
        "## 1. 設計決定\n\n満たす要件: FR-AAA-01\n\n| ID | 決定 |\n| --- | --- |\n| BD-SMP-01 | 決定 |\n\n"
        "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n[要件](../01-requirements/requirements.md)\n\n"
        "## 3. テスト観点\n\n満たす要件: TST-AAA-01\n\n"
        "## 4. 詳細設計への申し送り\n\n満たす要件: —\n\n- 一つ目\n- 二つ目\n"
    ),
    # 基本設計の索引。群と「SEC 全般」でまとめて挙げるが、網羅の判定には含めない
    "docs/02-basic-design/README.md": "# 基本設計の索引\n\n## 1. 一覧\n\n満たす要件: FR-AAA 群、SEC 全般\n",
    "docs/03-detailed-design/sample.md": (
        "# 詳細設計\n\n## 1. 実装\n\n満たす要件: FR-AAA-01〜02 ／ 決定: BD-SMP-01、UD-01\n\n"
        f"<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho one\necho two\necho three\n{FENCE}\n\n"
        f"<!-- excerpt: scripts/sample.sh -->\n\n{FENCE}bash\necho two\necho three\n{FENCE}\n\n"
        "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n## 3. テスト観点\n\n満たす要件: TST-AAA-01\n\n"
        "## 4. 要件 ID ↔ テストの対応表\n\n満たす要件: TST-AAA-01\n\n| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n\n"
        "## 5. 基本設計の申し送りの解決\n\n満たす要件: —\n\n| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n"
    ),
    "scripts/sample.sh": "echo one\necho two\necho three\n",
    # 詳細設計の索引。必須の節はないが、コード片は照合する
    "docs/03-detailed-design/README.md": (
        "# 詳細設計の索引\n\n## 1. 一覧\n\n満たす要件: —\n\n"
        f"<!-- excerpt: scripts/sample.sh -->\n\n{FENCE}bash\necho one\n{FENCE}\n"
    ),
}
# 印の書き方を説明する、長いフェンスの中の例（実ファイルと一致しない内容とない元のファイルを含む）
EXAMPLE_AFTER = "満たす要件: FR-AAA-01〜02 ／ 決定: BD-SMP-01、UD-01\n\n"
EXAMPLE_MARKERS = (
    f"{FENCE}`markdown\n<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho example\n{FENCE}\n\n"
    f"<!-- excerpt: scripts/missing.sh -->\n\n{FENCE}bash\necho example\n{FENCE}\n{FENCE}`\n\n"
)


# test_docs が持つ一時ディレクトリ。各ケースのディレクトリはこの下に作り、例外が起きても丸ごと消えるようにする
docs_parent: Optional[Path] = None


def docs_case(label: str, want_code: int, want_text: str, name: str = "", old: str = "", new: str = "",
              options: Optional[List[str]] = None, extra: Optional[Dict[str, Optional[str]]] = None) -> Path:
    """extra の値が None のファイルは置かない（欠落の試験に使う）。"""
    assert docs_parent is not None, "docs_case は test_docs の一時ディレクトリの中で呼ぶ"
    directory = Path(tempfile.mkdtemp(dir=docs_parent))
    files = dict(DOCS)
    files.update(extra or {})
    for path, content in files.items():
        if content is None:
            continue
        if path == name:
            content = replaced(content, old, new)
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(content, encoding="utf-8")
    expect(f"docs: {label}", [str(SCRIPTS / "verify-docs.py"), "--root", str(directory)] + (options or []), want_code, want_text)
    return directory


def test_docs() -> None:
    global docs_parent
    with tempfile.TemporaryDirectory() as owner:
        docs_parent = Path(owner)
        try:
            run_docs_cases()
        finally:
            docs_parent = None


def run_docs_cases() -> None:
    global count
    basic, detailed = "docs/02-basic-design/sample.md", "docs/03-detailed-design/sample.md"
    requirements = "docs/01-requirements/requirements.md"
    # 入れ子の基本設計（設計決定の ID とリンクの相対パスを、入れ子の位置に合わせる）
    nested_basic = replaced(replaced(DOCS[basic], "| BD-SMP-01 | 決定 |", "| BD-SMP-02 | 決定 |"),
                            "](../01-requirements/", "](../../01-requirements/")
    created = [
        docs_case("整合した文書", 0, "MVP の要件 3 件"),
        docs_case("定義されていない ID", 1, "定義されていない ID: FR-AAA-09", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-AAA-09"),
        docs_case("範囲の省略の中に定義されていない ID", 1, "定義されていない ID: FR-AAA-04", detailed, "FR-AAA-01〜02", "FR-AAA-01〜04"),
        docs_case("逆順の範囲", 1, "逆順の範囲: FR-AAA-01〜00", detailed, "FR-AAA-01〜02", "FR-AAA-01〜00"),
        docs_case("定義されていない ID の群", 1, "定義されていない ID の群: FR-ZZZ", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-ZZZ 群"),
        docs_case("定義されていない設計決定", 1, "定義されていない ID: BD-SMP-02", detailed, "BD-SMP-01", "BD-SMP-02"),
        docs_case("ID の定義の重複", 1, "定義が重複", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-01 | 二 | MVP |"),
        docs_case("MVP の要件が基本設計に現れない", 1, "MVP の要件 FR-AAA-02", basic, "FR-AAA-01・02、", "FR-AAA-01、"),
        docs_case("P2 の要件は網羅の対象にしない", 0, "OK", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-02 | 二 | P2 |"),
        # 「／」の後（決定・関連などの参照）にだけある要件は、網羅したことにしない
        docs_case("関連の参照だけでは網羅にならない", 1, "MVP の要件 FR-AAA-02", basic,
                  "FR-AAA-01・02、SEC-AAA-01 |\n\n## 1. 設計決定\n\n満たす要件: FR-AAA-01\n",
                  "FR-AAA-01、SEC-AAA-01 ／ 関連: FR-AAA-02 |\n\n## 1. 設計決定\n\n満たす要件: FR-AAA-01 ／ 関連: FR-AAA-02\n"),
        # 読むものがない root・定義文書の欠落は合格にしない
        docs_case("定義文書がない", 2, "test-requirements.md", extra={"docs/01-requirements/test-requirements.md": None}),
        docs_case("切れたリンク", 1, "リンクの先がない", basic, "../01-requirements/requirements.md", "../01-requirements/missing.md"),
        docs_case("言語のないコードフェンス", 1, "言語の指定がない", detailed, f"{FENCE}bash\necho one", f"{FENCE}\necho one"),
        docs_case("セキュリティ節がない", 1, "「セキュリティ」の節がない", detailed, "## 2. セキュリティ", "## 2. その他"),
        docs_case("テスト観点の節がない", 1, "「テスト観点」の節がない", basic, "## 3. テスト観点", "## 3. その他"),
        docs_case("節の冒頭に要件 ID がない", 1, "冒頭に「満たす要件:」", basic, "満たす要件: TST-AAA-01\n\n", "本文\n\n"),
        docs_case("満たす要件の行に ID がない", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件: 未定\n"),
        docs_case("満たす要件の行が空", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件:\n"),
        docs_case("満たす要件が設計決定だけ", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件: BD-SMP-01\n"),
        docs_case("満たす要件に要件の群", 0, "OK", basic, "満たす要件: TST-AAA-01\n", "満たす要件: TST-AAA 群\n"),
        # 「制約:」だけの行は「満たす要件:」の代わりにならない。制約は「／」の後に書く
        docs_case("制約だけの行は認めない", 1, "冒頭に「満たす要件:」の行がない", basic, "満たす要件: TST-AAA-01\n", "制約: CON-01\n"),
        docs_case("満たす要件の後の制約", 0, "OK", basic, "満たす要件: TST-AAA-01\n", "満たす要件: — ／ 制約: CON-01\n"),
        # スラッシュ区切りの省略も個々の ID に展開する
        docs_case("スラッシュ区切りの省略", 0, "OK", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-AAA-01 / 02、FR-AAA-01/03"),
        docs_case("スラッシュ区切りの中に定義されていない ID", 1, "定義されていない ID: FR-AAA-04", detailed, "決定: BD-SMP-01",
                  "決定: BD-SMP-01、FR-AAA-01 / 04"),
        docs_case("スラッシュ区切りで網羅する", 0, "OK", basic, "FR-AAA-01・02、", "FR-AAA-01 / 02、"),
        docs_case("長いフェンスの中の短いフェンスで切り替わらない", 0, "OK", basic, "満たす要件: TST-AAA-01\n\n",
                  f"満たす要件: TST-AAA-01\n\n{FENCE}`markdown\n{FENCE}\n## 9. フェンスの中の見出し\n{FENCE}\n{FENCE}`\n\n"),
        docs_case("チルダのフェンス", 0, "OK", basic, "満たす要件: TST-AAA-01\n\n",
                  "満たす要件: TST-AAA-01\n\n~~~text\n## 9. フェンスの中の見出し\n~~~\n\n"),
        docs_case("後ろに文字がある行はフェンスを閉じない", 0, "OK", basic, "満たす要件: TST-AAA-01\n\n",
                  f"満たす要件: TST-AAA-01\n\n{FENCE}text\n{FENCE} 末尾に文字\n## 9. フェンスの中の見出し\n{FENCE}\n\n"),
        docs_case("閉じていないフェンス", 1, "閉じていない", basic, "満たす要件: TST-AAA-01\n\n",
                  f"満たす要件: TST-AAA-01\n\n{FENCE}`text\n{FENCE}\n\n"),
        docs_case("チルダのフェンスのコード片", 0, "OK", detailed, f"{FENCE}bash\necho two\necho three\n{FENCE}",
                  "~~~bash\necho two\necho three\n~~~"),
        docs_case("申し送りの解決が足りない", 1, "申し送り「二つ目」の解決が表にない", detailed, "| 二つ目 | 済 |\n", ""),
        docs_case("重複した行で欠落した申し送りを埋めても合格にしない", 1, "申し送り「二つ目」の解決が表にない",
                  detailed, "| 二つ目 | 済 |", "| 一つ目 | 済 |"),
        docs_case("申し送りの解決の列が空", 1, "申し送り「二つ目」の解決の列が空", detailed, "| 二つ目 | 済 |", "| 二つ目 |   |"),
        docs_case("申し送りの解決の重複", 1, "申し送り「一つ目」の解決が表に 2 行ある",
                  detailed, "| 二つ目 | 済 |", "| 二つ目 | 済 |\n| 一つ目 | 再掲 |"),
        # 申し送りの節に表が 2 つある場合も、各表の見出しの行を除く（2 つ目の見出しを項目と見なさない）
        docs_case("申し送りの解決を 2 つの表に分ける", 0, "OK", detailed, "| 二つ目 | 済 |\n",
                  "\n| 申し送り | 解決 |\n| --- | --- |\n| 二つ目 | 済 |\n"),
        docs_case("2 つ目の表の項目も重複を検査する", 1, "申し送り「一つ目」の解決が表に 2 行ある", detailed, "| 二つ目 | 済 |\n",
                  "| 二つ目 | 済 |\n\n| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 再掲 |\n"),
        docs_case("基本設計にない申し送りの行", 1, "基本設計にない申し送り「三つ目」", detailed, "| 二つ目 | 済 |", "| 二つ目 | 済 |\n| 三つ目 | 済 |"),
        # 設計書の配下のディレクトリの文書も検査する。対応する基本設計は詳細設計からの相対パスで決まる
        docs_case("入れ子の詳細設計に対応する基本設計がない", 1, "対応する基本設計 docs/02-basic-design/sub/nested.md がない",
                  extra={"docs/03-detailed-design/sub/nested.md": DOCS[detailed]}),
        docs_case("入れ子の基本設計と詳細設計の組", 0, "OK",
                  extra={"docs/02-basic-design/sub/nested.md": nested_basic,
                         "docs/03-detailed-design/sub/nested.md": DOCS[detailed]}),
        docs_case("入れ子の基本設計の必須の節", 1, "docs/02-basic-design/sub/nested.md: 「テスト観点」の節がない",
                  extra={"docs/02-basic-design/sub/nested.md": replaced(
                      nested_basic, "## 3. テスト観点", "## 3. その他")}),
        # フェンスの中の印は説明用の例であり、照合しない
        docs_case("フェンスの中の印は照合しない", 0, "OK", detailed, EXAMPLE_AFTER, EXAMPLE_AFTER + EXAMPLE_MARKERS),
        docs_case("全文のコード片が実ファイルと違う", 1, "コード片が scripts/sample.sh と一致しない", detailed, "echo one\necho two\necho three", "echo one\necho two"),
        docs_case("一部のコード片が実ファイルと違う", 1, "コード片が scripts/sample.sh と一致しない", detailed, f"bash\necho two\necho three\n{FENCE}", f"bash\necho two\necho four\n{FENCE}"),
        docs_case("コード片の元のファイルがない", 1, "元のファイルがない", detailed, "<!-- file: scripts/sample.sh -->", "<!-- file: scripts/missing.sh -->"),
        docs_case("詳細設計の索引のコード片も照合する", 1, "README.md:7: コード片が scripts/sample.sh と一致しない",
                  "docs/03-detailed-design/README.md", f"bash\necho one\n{FENCE}", f"bash\necho nine\n{FENCE}"),
        # 「要件 ID ↔ テスト」の節には、要件 ID とテストの両方を持つ対応表の行が要る
        docs_case("要件 ID ↔ テストの対応表がない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n", ""),
        docs_case("対応表の行に要件 ID がない", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |", "| 未定 | 試験 |"),
        docs_case("対応表の行にテストがない", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |", "| FR-AAA-01 |  |"),
        # 範囲は、同じ接頭辞の定義済み ID の数を超えて展開しない
        docs_case("広すぎる範囲", 1, "広すぎる範囲: FR-AAA-01〜99", detailed, "FR-AAA-01〜02", "FR-AAA-01〜99"),
        # FR / NFR / SEC はフェーズ（MVP / P2 / P3）を持つ
        docs_case("フェーズを読めない", 1, "FR-AAA-02 のフェーズ「未定」", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-02 | 二 | 未定 |"),
        docs_case("フェーズの列がない", 1, "SEC-AAA-01 のフェーズの列がない", "docs/01-requirements/security-requirements.md",
                  "| ID | 要件 | フェーズ |\n| --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP |", "| ID | 要件 |\n| --- | --- |\n| SEC-AAA-01 | 一 |"),
        # 「満たす要件の例」などの説明文は、網羅に数えない
        docs_case("説明文の要件は網羅に数えない", 1, "MVP の要件 FR-AAA-02", extra={basic: replaced(replaced(
            DOCS[basic], "FR-AAA-01・02、", "FR-AAA-01、"),
            "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n", "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n満たす要件の例: FR-AAA-02\n\n")}),
        # 節の後半にある「満たす要件:」の行は、網羅に数えない（節・小節の見出しの直後の行だけが正式な欄）
        docs_case("節の後半の満たす要件は網羅に数えない", 1, "MVP の要件 FR-AAA-02", extra={basic: replaced(replaced(
            DOCS[basic], "FR-AAA-01・02、", "FR-AAA-01、"),
            "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n", "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n本文\n\n満たす要件: FR-AAA-02\n\n")}),
        docs_case("小節の冒頭の満たす要件は網羅に数える", 0, "OK", extra={basic: replaced(replaced(
            DOCS[basic], "FR-AAA-01・02、", "FR-AAA-01、"),
            "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n",
            "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n### 2.1 小節\n\n満たす要件: FR-AAA-02\n\n")}),
        # フェンスをまたいだ行は 1 つの表にならない（フェンスの後の行だけでは、見出しと区切りのない表なので数えない）
        docs_case("フェンスをまたいだ対応表は数えない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |",
                  f"| 要件 | 試験 |\n| --- | --- |\n{FENCE}text\n例\n{FENCE}\n| FR-AAA-01 | 試験 |"),
        # 空白 4 個以上の字下げの行はフェンスを閉じない（コードの本文として扱う）
        docs_case("字下げ 4 個のフェンス状の行は開かない", 0, "OK", basic, "満たす要件: TST-AAA-01\n\n",
                  f"満たす要件: TST-AAA-01\n\n    {FENCE}\n    コードの本文\n\n"),
        docs_case("字下げ 4 個のフェンス状の行は閉じない", 0, "OK", basic, "満たす要件: TST-AAA-01\n\n",
                  f"満たす要件: TST-AAA-01\n\n{FENCE}text\n    {FENCE}\n## 9. フェンスの中の見出し\n{FENCE}\n\n"),
        # 山括弧で囲んだリンク先（空白を含められる）も、山括弧を除いて解決する
        docs_case("山括弧のリンク先", 0, "OK", basic, "](../01-requirements/requirements.md)",
                  "](<../01-requirements/requirements.md>)"),
        docs_case("山括弧の切れたリンク", 1, "リンクの先がない: ../01-requirements/missing file.md", basic,
                  "](../01-requirements/requirements.md)", "](<../01-requirements/missing file.md>)"),
        # 対応表の先頭の列は個々の要件 ID を持つ（群の表記だけの行は数えない）
        docs_case("対応表の行が群の表記だけ", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |", "| FR-AAA 群 | 試験 |"),
        # 区切りの行のない「|」の行のまとまりは表として数えない
        docs_case("対応表に区切りの行がない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 試験 |\n| FR-AAA-01 | 試験 |"),
        # 区切りの行は列ごとに「任意のコロン・1 個以上のハイフン・任意のコロン」の形であること
        docs_case("ハイフンのない区切りの行は表として数えない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |", "| 要件 | 試験 |\n| : | :: |"),
        docs_case("コロンつきの区切りの行は表として数える", 0, "OK", detailed,
                  "| 要件 | 試験 |\n| --- | --- |", "| 要件 | 試験 |\n| :-: | :--- |"),
        # 要件の定義は表のデータ行からだけ集める。フェンスで切れた後の行は、前の表のフェーズの列で定義しない
        docs_case("フェンスで切れた後の行は要件の定義にならない", 1, "定義されていない ID: FR-AAA-02", requirements,
                  "| FR-AAA-01 | 一 | MVP |\n| FR-AAA-02 | 二 | MVP |",
                  f"| FR-AAA-01 | 一 | MVP |\n{FENCE}text\n例\n{FENCE}\n| FR-AAA-02 | 二 | MVP |"),
        docs_case("申し送りの表に区切りの行がない", 1, "申し送り「一つ目」の解決が表にない", detailed,
                  "| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |", "| 申し送り | 解決 |\n| 一つ目 | 済 |"),
        # 字下げしたフェンスのコード片は、開始行の字下げの分を除いて照合する
        docs_case("字下げしたフェンスのコード片", 0, "OK", detailed, f"{FENCE}bash\necho two\necho three\n{FENCE}",
                  f"  {FENCE}bash\n  echo two\n  echo three\n  {FENCE}"),
        docs_case("字下げしたフェンスのコード片の不一致", 1, "コード片が scripts/sample.sh と一致しない", detailed,
                  f"{FENCE}bash\necho two\necho three\n{FENCE}", f"  {FENCE}bash\n  echo two\n  echo four\n  {FENCE}"),
        # コード片の元のファイルは --root の中に限る
        docs_case("--root の外のコード片の元のファイル", 1, "--root の外にある", detailed,
                  "<!-- excerpt: scripts/sample.sh -->", "<!-- excerpt: ../outside.sh -->"),
        # 見出しの行と区切りの行の列の数が違うまとまりは表として数えない（エスケープした「\\|」は列の区切りにしない）
        docs_case("対応表の区切りの行の列の数が違う", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 試験 |\n| --- | --- | --- |\n| FR-AAA-01 | 試験 |"),
        docs_case("エスケープした | を含む見出しの表", 0, "OK", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 試験 \\| 補足 |\n| --- | --- |\n| FR-AAA-01 | 試験 |"),
        # 節の冒頭のコードブロックは節の冒頭を終わらせる（その後ろの「満たす要件:」を節の冒頭の欄として数えない）
        docs_case("コードブロックの後ろの満たす要件", 1, "節「3. テスト観点」の冒頭に「満たす要件:」の行がない", basic,
                  "## 3. テスト観点\n\n満たす要件: TST-AAA-01\n", f"## 3. テスト観点\n\n{FENCE}text\nx\n{FENCE}\n\n満たす要件: TST-AAA-01\n"),
        docs_case("コードブロックの後ろの満たす要件は網羅に数えない", 1, "MVP の要件 FR-AAA-02", basic,
                  "FR-AAA-01・02、SEC-AAA-01 |\n\n## 1. 設計決定\n\n満たす要件: FR-AAA-01\n",
                  f"FR-AAA-01、SEC-AAA-01 |\n\n## 1. 設計決定\n\n{FENCE}text\nx\n{FENCE}\n\n満たす要件: FR-AAA-01・02\n"),
        # タイトル付きのリンク（[文](パス "題")）もリンク先だけを取り出して確かめる
        docs_case("タイトル付きの切れたリンク", 1, "リンクの先がない: ../01-requirements/missing.md", basic,
                  "](../01-requirements/requirements.md)", '](../01-requirements/missing.md "要件")'),
        docs_case("タイトル付きのリンク", 0, "OK", basic, "](../01-requirements/requirements.md)",
                  "](../01-requirements/requirements.md '要件')"),
        docs_case("山括弧とタイトル付きの切れたリンク", 1, "リンクの先がない: ../01-requirements/missing file.md", basic,
                  "](../01-requirements/requirements.md)", "](<../01-requirements/missing file.md> (要件))"),
    ]
    # シンボリックリンクで --root の外を指すコード片の元のファイルも認めない
    with tempfile.TemporaryDirectory() as outside:
        (Path(outside) / "sample.sh").write_text(DOCS["scripts/sample.sh"], encoding="utf-8")
        linked = docs_case("リンクを置く前の文書", 0, "OK")
        (linked / "scripts" / "linked.sh").symlink_to(Path(outside) / "sample.sh")
        text = (linked / detailed).read_text(encoding="utf-8")
        (linked / detailed).write_text(text.replace("<!-- excerpt: scripts/sample.sh -->", "<!-- excerpt: scripts/linked.sh -->"),
                                       encoding="utf-8")
        expect("docs: シンボリックリンクで --root の外を指すコード片", [str(SCRIPTS / "verify-docs.py"), "--root", str(linked)],
               1, "--root の外にある")
    # --update-snippets は、字下げしたフェンスに同じ字下げを付けて書き直す
    indented = docs_case("字下げしたフェンスの書き直し", 0, "コード片を書き直した", detailed,
                         f"{FENCE}bash\necho one\necho two\necho three\n{FENCE}", f"  {FENCE}bash\n  echo old\n  {FENCE}",
                         options=["--update-snippets"])
    count += 1
    if f"  {FENCE}bash\n  echo one\n  echo two\n  echo three\n  {FENCE}" not in (indented / detailed).read_text(encoding="utf-8"):
        failures.append("docs: 字下げしたフェンスのコード片が同じ字下げで書き直されていない")
    # --update-snippets は、全文のコード片を実ファイルに合わせて書き直す
    fixed = docs_case("全文のコード片の書き直し", 0, "コード片を書き直した", detailed, "echo one\necho two\necho three", "echo old",
                      options=["--update-snippets"])
    count += 1
    if "echo one\necho two\necho three" not in (fixed / detailed).read_text(encoding="utf-8"):
        failures.append("docs: 書き直した後の文書に、実ファイルの内容がない")
    # --update-snippets も、フェンスの中の印のコード片を書き直さない
    example = docs_case("フェンスの中の印は書き直さない", 0, "OK", detailed, EXAMPLE_AFTER, EXAMPLE_AFTER + EXAMPLE_MARKERS,
                        options=["--update-snippets"])
    count += 1
    if EXAMPLE_MARKERS not in (example / detailed).read_text(encoding="utf-8"):
        failures.append("docs: フェンスの中の印のコード片が書き直された")
    # --update-snippets は、コード片が一致していれば末尾の改行の有無だけで書き直さない
    no_newline = DOCS[detailed].rstrip("\n")
    kept = docs_case("末尾の改行だけでは書き直さない", 0, "OK", options=["--update-snippets"], extra={detailed: no_newline})
    count += 1
    if (kept / detailed).read_text(encoding="utf-8") != no_newline:
        failures.append("docs: コード片が一致しているのに、末尾の改行だけで文書が書き直された")
    # --update-snippets は、コード片以外の行の改行（CRLF）を保つ
    crlf = docs_case("CRLF の文書のコード片の書き直し", 0, "コード片を書き直した", options=["--update-snippets"],
                     extra={detailed: replaced(DOCS[detailed], "echo one\necho two\necho three", "echo old").replace("\n", "\r\n")})
    count += 1
    data = (crlf / detailed).read_bytes()
    if data.count(b"\n") != data.count(b"\r\n") or b"echo one\r\necho two\r\necho three" not in data:
        failures.append("docs: CRLF の文書の書き直しで改行が変わった、またはコード片が書き直されていない")
    # --update-snippets は、実パスが --root の外にある文書に書き込まない
    assert docs_parent is not None
    outside = Path(tempfile.mkdtemp(dir=docs_parent)) / "outside.md"
    stale = replaced(DOCS[detailed], "echo one\necho two\necho three", "echo old")
    outside.write_text(stale, encoding="utf-8")
    linked = Path(tempfile.mkdtemp(dir=docs_parent))
    for path, content in DOCS.items():
        (linked / path).parent.mkdir(parents=True, exist_ok=True)
        if path == detailed:
            (linked / path).symlink_to(outside)
        else:
            (linked / path).write_text(content, encoding="utf-8")
    expect("docs: root の外の文書は書き直さない", [str(SCRIPTS / "verify-docs.py"), "--root", str(linked), "--update-snippets"],
           1, "--root の外にあるため")
    count += 1
    if outside.read_text(encoding="utf-8") != stale:
        failures.append("docs: --root の外にある文書が書き直された")
    del created  # 一時ディレクトリは test_docs の TemporaryDirectory が消す
    with tempfile.TemporaryDirectory() as missing:
        expect("docs: 存在しない root", [str(SCRIPTS / "verify-docs.py"), "--root", str(Path(missing) / "none")], 2, "docs")


TESTS = {"settings": test_settings, "dependencies": test_dependencies, "versions": test_versions, "gitleaks": test_gitleaks,
         "docs": test_docs, "coverage": test_coverage}


def main() -> int:
    names = sys.argv[1:] or list(TESTS)
    unknown = [name for name in names if name not in TESTS]
    if unknown:
        print(f"使い方: {sys.argv[0]} [{'] ['.join(TESTS)}]（不明な対象: {' '.join(unknown)}）", file=sys.stderr)
        return 2
    for name in names:
        TESTS[name]()
    for message in failures:
        print(f"NG: {message}")
    if failures:
        print(f"NG: 検査の自己試験 {len(failures)}/{count} 件が期待と違う")
        return 1
    print(f"OK: 検査の自己試験 {count} 件（{'・'.join(names)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
