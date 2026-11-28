#!/usr/bin/python3
"""検査スクリプトそのものを試験する（基本設計 dev-environment.md §13）。

合格するはずの入力で通り、不合格にするはずの入力で失敗することを確かめる。
試験用の設定はこのスクリプトの中で組み立て、一時ディレクトリに置く
（リポジトリに「検査の対象外にするディレクトリ」を作らないため）。

使い方: scripts/test-checks.py [settings] [dependencies] [versions] [gitleaks] [tracked] [docs] [coverage]   （省略時は全部）
終了コード: 0 = 全件が期待どおり / 1 = 期待と違う結果あり / 2 = 使い方の誤り
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True  # scripts/__pycache__ を作らない
from xcproj import strip_trailing_commas  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
# 開発ツールの場所。check.sh は照合した写しを MINUTES_TOOLS_DIR で渡す（なければリポジトリの .tools）
TOOLS = Path(os.environ.get("MINUTES_TOOLS_DIR") or ROOT / ".tools")
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
# 合格するプライバシーマニフェスト（アプリのターゲット Sample のもの。SEC-PRV-03）
PRIVACY_MANIFEST = "Sample/PrivacyInfo.xcprivacy"
OK_PRIVACY_MANIFEST = {
    "NSPrivacyTracking": False,
    "NSPrivacyTrackingDomains": [],
    "NSPrivacyCollectedDataTypes": [],
    "NSPrivacyAccessedAPITypes": [
        {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryFileTimestamp", "NSPrivacyAccessedAPITypeReasons": ["C617.1"]},
        {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryDiskSpace", "NSPrivacyAccessedAPITypeReasons": ["E174.1"]},
        {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults", "NSPrivacyAccessedAPITypeReasons": ["CA92.1"]},
    ],
}


def run(command: List[str], timeout: Optional[float] = None, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    return result.returncode, result.stdout + result.stderr


def expect(label: str, command: List[str], want_code: int, want_text: str = "", timeout: Optional[float] = None,
           env: Optional[Dict[str, str]] = None) -> None:
    """終了コードと、出力に含まれるはずの文字列を確かめる。timeout（秒）を過ぎても終わらなければ失敗とする。
    env を渡すと、その環境で実行する（git のリポジトリを指す外側の変数（GIT_DIR など）を引き継がせないときに使う）。"""
    global count
    count += 1
    try:
        code, output = run(command, timeout, env)
    except subprocess.TimeoutExpired:
        failures.append(f"{label}: {timeout} 秒を過ぎても終わらない（待ち続けている）")
        return
    except OSError as error:
        failures.append(f"{label}: {command[0]} を起動できない: {error}")
        return
    if code != want_code:
        failures.append(f"{label}: 終了コードが {code}（期待 {want_code}）\n{output.strip()}")
    elif want_text and want_text not in output:
        failures.append(f"{label}: 出力に「{want_text}」がない\n{output.strip()}")


def launched(label: str, command: List[str], **options: object) -> "subprocess.CompletedProcess[str]":
    """対照などで開発ツールを直接起動する。起動できなければ（導入されていないなど）実行ファイルのパスを失敗として記録し、終了コード 127 の
    結果を返す（自己試験を途中で止めず、残りの試験と集計を続ける）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True, **options)
    except OSError as error:
        failures.append(f"{label}: {command[0]} を起動できない: {error}")
        return subprocess.CompletedProcess(command, 127, "", str(error))


# ロックを作れない場合の試験の時間の上限（秒）。ロックを待つ実装なら、この時間を過ぎて失敗する
LOCK_ERROR_TIMEOUT = 15


def replaced(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"置換の対象が 1 か所でない: {old}"
    return text.replace(old, new)


def write_privacy_manifest(root: Path) -> None:
    """合格するアプリのプライバシーマニフェストを root の下に置く（自前のフィクスチャを組み立てる試験用）。"""
    (root / PRIVACY_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (root / PRIVACY_MANIFEST).write_bytes(plistlib.dumps(OK_PRIVACY_MANIFEST))


def settings_case(
    label: str,
    want_code: int,
    want_text: str = "",
    project: Optional[Callable[[str], str]] = None,
    plists: Optional[Dict[str, dict]] = None,
    raw: Optional[Dict[str, bytes]] = None,
    timeout: Optional[float] = None,
    without: Tuple[str, ...] = (),
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        text = project(OK_PROJECT) if project else OK_PROJECT
        (root / "project.xcproj").write_text(text, encoding="utf-8")
        files = {"App/Info.plist": OK_INFO_PLIST, PRIVACY_MANIFEST: OK_PRIVACY_MANIFEST}
        files.update(plists or {})
        for name, content in files.items():
            if name in without:
                continue
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            with (root / name).open("wb") as handle:
                plistlib.dump(content, handle)
        for name, data in (raw or {}).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        command = [str(SCRIPTS / "check-project-settings.py"), "--project", str(root / "project.xcproj"), "--root", directory]
        expect(f"settings: {label}", command, want_code, want_text, timeout)


def settings_sdk_timeout_case() -> None:
    """SDK の版の問い合わせ（xcrun）が上限を過ぎても終わらなければ、版を取得できないものとし、SDK の条件を判定できない
    （終了コード 2 の経路の Unreadable）とすること。上限の中で終われば、その版を使うこと。"""
    global count
    spec = importlib.util.spec_from_file_location("check_project_settings", SCRIPTS / "check-project-settings.py")
    assert spec is not None and spec.loader is not None
    for label, body, want in (("応答しない xcrun", "exec sleep 30", None), ("上限の中で終わる xcrun", "echo 27.2", "iphoneos27.2")):
        count += 1
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SDK_QUERY_TIMEOUT = 1
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / "xcrun"
            stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            stub.chmod(0o755)
            saved = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{directory}:{saved}"
            started = time.monotonic()
            try:
                name = module.sdk_name("iphoneos")
                raised = False
                if want is None:
                    try:
                        module.sdk_matches("iphoneos", "iphoneos*", "SETTING[sdk=iphoneos*]")
                    except module.Unreadable:
                        raised = True
            except subprocess.TimeoutExpired:
                name, raised = "TimeoutExpired を送出した", False
            finally:
                os.environ["PATH"] = saved
            elapsed = time.monotonic() - started
        if name != want or (want is None and not raised) or elapsed > 15:
            failures.append(f"settings: {label}: SDK の版の問い合わせの扱いが違う（{name!r}・判定できない {raised}・{elapsed:.1f} 秒）")


def test_settings() -> None:
    global count
    settings_sdk_timeout_case()
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
    # 同じオブジェクトの中の重複した鍵は、後の値で前の値を隠すので読めないものとして扱う
    settings_case("重複した鍵", 2, "重複した鍵",
                  project=lambda t: replaced(t, '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
                                             '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "IPHONEOS_DEPLOYMENT_TARGET": "27.2",'))
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
    # 配列の中の配列（何段でも）の中の辞書も調べる（入れ子の配列で禁止の鍵を隠させない）
    settings_case("配列の中の配列の中の辞書の禁止の鍵", 1, "NSAppTransportSecurity を設定しない",
                  plists={"App/Info.plist": {"Wrapped": [[{"NSAppTransportSecurity": {}}]]}})
    settings_case("3 段の配列の中の辞書の禁止の鍵", 1, "UIFileSharingEnabled を有効にしない",
                  plists={"App/Info.plist": {"Wrapped": [[[{"UIFileSharingEnabled": True}]]]}})
    # iPhone の修飾子つきの鍵も同じ規則で判定する
    for modified in ("UIFileSharingEnabled~iphone", "UIFileSharingEnabled-iphoneos~iphone", "UIFileSharingEnabled-iphoneos"):
        settings_case(f"ファイル共有を有効にしている（Info.plist・{modified}）", 1, modified,
                      plists={"App/Info.plist": {modified: True}})
    # ほかの鍵の規則（あるだけで違反・使わない権限の文言・Background Modes・拡張の種類）も、修飾子を除いた鍵で判定する
    for modifier in ("~iphone", "-iphoneos", "-iphoneos~iphone"):
        settings_case(f"修飾子つきの ATS（{modifier}）", 1, f"NSAppTransportSecurity{modifier} を設定しない",
                      plists={"App/Info.plist": {f"NSAppTransportSecurity{modifier}": {}}})
        settings_case(f"修飾子つきの使わない権限の文言（{modifier}）", 1, f"使わない権限の文言 NSCameraUsageDescription{modifier}",
                      plists={"App/Info.plist": {f"NSCameraUsageDescription{modifier}": "x"}})
        settings_case(f"修飾子つきの Background Modes（{modifier}）", 1, "UIBackgroundModes に fetch を入れない",
                      plists={"App/Info.plist": {f"UIBackgroundModes{modifier}": ["audio", "fetch"]}})
        settings_case(f"修飾子つきの拡張の種類（{modifier}）", 1, "拡張 com.apple.share-services を置かない",
                      plists={"Share/Info.plist": {"NSExtension": {
                          f"NSExtensionPointIdentifier{modifier}": "com.apple.share-services"}}})
    # arch の条件つきの書き方の変数は、その条件に合う arch の値だけで展開する
    for value, want in (("NO", 0), ("YES", 1)):
        settings_case(f"arch の条件つきのファイル共有の変数（{value}）", want, "有効な値に展開される" if want else "OK",
                      project=lambda t, value=value: replaced(
                          t, '"GENERATE_INFOPLIST_FILE": "YES",',
                          '"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_KEY_UIFileSharingEnabled[arch=arm64]": "$(SHARE)",\n'
                          f'        "SHARE[arch=arm64]": "{value}",'))
    settings_case("修飾子つきのファイル共有の無効は認める", 0, "OK",
                  plists={"App/Info.plist": {"UIFileSharingEnabled~iphone": False}})
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
    # 形の違う日付は plistlib が AttributeError を送出する（正しい日付は認める）
    for value, want_code, want_text in (("bad", 2, "設定を読めない"), ("2026-13-45T00:00:00Z", 2, "設定を読めない"),
                                        ("2026-11-25T00:00:00Z", 0, "OK")):
        settings_case(f"Info.plist の日付が {value}", want_code, want_text, raw={
            "App/Dated/Info.plist": f'<?xml version="1.0"?><plist version="1.0"><dict><key>K</key><date>{value}</date></dict></plist>'.encode()})
    # 辞書の外の <key>（最上位の <key>）は plistlib が IndexError を送出する。読めないものとする（辞書の中の鍵は認める）
    settings_case("Info.plist の最上位の <key>", 2, "設定を読めない", raw={
        "App/Keyed/Info.plist": b'<?xml version="1.0"?><plist version="1.0"><key>K</key></plist>'})
    settings_case("Info.plist の辞書の中の <key>", 0, "OK", raw={
        "App/Keyed/Info.plist": b'<?xml version="1.0"?><plist version="1.0"><dict><key>K</key><string>v</string></dict></plist>'})
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
    # 相対パスの --project でも、絶対パスと同じファイルを $(SRCROOT) から解決する（SRCROOT は .xcodeproj の親）
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "sub" / "A.xcodeproj" / "project.xcproj"
        project.parent.mkdir(parents=True)
        project.write_text(info_plist_file("$(SRCROOT)/$(TARGET_NAME)/Settings.plist")(OK_PROJECT), encoding="utf-8")
        (Path(directory) / "sub" / "Sample").mkdir()
        with (Path(directory) / "sub" / "Sample" / "Settings.plist").open("wb") as handle:
            plistlib.dump({"UIFileSharingEnabled": True}, handle)
        for label, given in (("相対パス", "sub/A.xcodeproj/project.xcproj"), ("絶対パス", str(project))):
            count += 1
            result = subprocess.run([str(SCRIPTS / "check-project-settings.py"), "--project", given, "--root", directory],
                                    cwd=directory, capture_output=True, text=True)
            if result.returncode != 1 or "UIFileSharingEnabled" not in result.stdout:
                failures.append(f"settings: {label}の --project で $(SRCROOT) の参照先を解決できない（終了コード {result.returncode}）\n"
                                f"{(result.stdout + result.stderr).strip()}")
    # 参照先のパスを解決できない（循環するリンク）場合は、例外で落ちずに終了コード 2
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "Config").mkdir()
        (Path(directory) / "Config" / "Loop.plist").symlink_to("Loop.plist")
        (Path(directory) / "project.xcproj").write_text(info_plist_file("Config/Loop.plist")(OK_PROJECT), encoding="utf-8")
        expect("settings: 参照先が循環するリンク", [str(SCRIPTS / "check-project-settings.py"), "--project",
                                          str(Path(directory) / "project.xcproj"), "--root", directory], 2, "読めない")
    # 機種・OS の修飾子つきの向きの鍵（-iphoneos~iphone）に横向きがあれば違反
    settings_case("修飾子つきの向きの鍵に横向き", 1, "UISupportedInterfaceOrientations-iphoneos~iphone",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, **{
                      "UISupportedInterfaceOrientations-iphoneos~iphone": ["UIInterfaceOrientationLandscapeLeft"]})})
    settings_case("修飾子つきの向きの鍵が縦だけ", 0, "OK",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, **{
                      "UISupportedInterfaceOrientations-iphoneos~iphone": ["UIInterfaceOrientationPortrait"]})})
    # OS の修飾子だけの鍵（-iphoneos）も iPhone で使われる。標準の鍵が縦だけでも、こちらの横向きは違反
    settings_case("OS の修飾子の向きの鍵に横向き", 1, "UISupportedInterfaceOrientations-iphoneos が縦だけでない",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, **{
                      "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait"],
                      "UISupportedInterfaceOrientations-iphoneos": ["UIInterfaceOrientationLandscapeRight"]})})
    settings_case("標準の鍵だけで縦だけ", 0, "OK",
                  project=info_plist_file("Config/App-Settings.plist"),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, **{
                      "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait"]})})
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
    # 生成する構成では、向きのビルド設定か使われる plist のどちらかで縦だけに決まればよい（plist だけでも違反にしない）
    without_orientation = lambda t: replaced(  # noqa: E731
        t, '        "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": "UIInterfaceOrientationPortrait",\n', "")
    settings_case("生成する構成で向きを plist だけに置く", 0, "OK",
                  project=lambda t: without_orientation(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, **{
                      "UISupportedInterfaceOrientations~iphone": ["UIInterfaceOrientationPortrait"]})})
    settings_case("生成する構成で向きがどこにもない", 1, "縦だけの向きの指定がない", project=without_orientation)
    # 向きのビルド設定の変数は、その構成・SDK で展開してから判定する
    orientation_variable = lambda value: lambda t: replaced(  # noqa: E731
        t, '"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": "UIInterfaceOrientationPortrait",',
        '"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": "$(MINUTES_ORIENTATION)",\n'
        f'        "MINUTES_ORIENTATION": "{value}",')
    settings_case("縦に展開される向きの変数", 0, "OK", project=orientation_variable("UIInterfaceOrientationPortrait"))
    settings_case("横に展開される向きの変数", 1, "縦だけでない", project=orientation_variable("UIInterfaceOrientationLandscapeLeft"))
    settings_case("展開できない向きの変数", 1, "展開できない",
                  project=lambda t: replaced(t, '"UIInterfaceOrientationPortrait",\n        "PRODUCT_BUNDLE_IDENTIFIER"',
                                             '"$(MINUTES_UNDEFINED)",\n        "PRODUCT_BUNDLE_IDENTIFIER"'))
    settings_case("生成しない構成では向きのビルド設定がなくてよい", 0, "OK", project=lambda t: without_orientation(with_file(t)),
                  plists={"Config/App-Settings.plist": microphone})
    # 生成しない構成では、使われる plist に縦だけの向きの指定が要る
    settings_case("生成しない plist に向きの指定がない", 1, "縦だけの向きの指定がない", project=with_file,
                  plists={"Config/App-Settings.plist": {"NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。"}})
    settings_case("参照先の plist の向きが横を含む", 1, "が縦だけでない", project=with_file,
                  plists={"Config/App-Settings.plist": dict(microphone, UISupportedInterfaceOrientations=[
                      "UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft"])})
    # 参照先の plist の向きの要素の変数も、その構成・SDK のビルド設定で展開してから判定する
    plist_orientation = lambda value: {"Config/App-Settings.plist": dict(  # noqa: E731
        microphone, **{"UISupportedInterfaceOrientations~iphone": [value]})}
    with_orientation_setting = lambda value: lambda t: replaced(with_file(t), '"GENERATE_INFOPLIST_FILE": "NO",',  # noqa: E731
                                                               f'"GENERATE_INFOPLIST_FILE": "NO",\n        "MINUTES_ORIENTATION": "{value}",')
    settings_case("plist の向きの変数が縦に展開される", 0, "OK",
                  project=with_orientation_setting("UIInterfaceOrientationPortrait"),
                  plists=plist_orientation("$(MINUTES_ORIENTATION)"))
    settings_case("plist の向きの変数が横に展開される", 1, "が縦だけでない",
                  project=with_orientation_setting("UIInterfaceOrientationLandscapeLeft"),
                  plists=plist_orientation("$(MINUTES_ORIENTATION)"))
    settings_case("plist の向きの変数が展開できない", 1, "展開できない", project=with_file,
                  plists=plist_orientation("$(MINUTES_UNDEFINED)"))
    # ビルドが参照先の plist の変数を展開しない構成（INFOPLIST_EXPAND_BUILD_SETTINGS = NO）では、縦に展開される変数でも認めない
    without_expansion = lambda value: lambda t: replaced(  # noqa: E731
        with_orientation_setting("UIInterfaceOrientationPortrait")(t), '"GENERATE_INFOPLIST_FILE": "NO",',
        f'"GENERATE_INFOPLIST_FILE": "NO",\n        "INFOPLIST_EXPAND_BUILD_SETTINGS": "{value}",')
    settings_case("plist の変数を展開しない構成の向きの変数", 1, "変数が展開されない", project=without_expansion("NO"),
                  plists=plist_orientation("$(MINUTES_ORIENTATION)"))
    settings_case("plist の変数を展開する構成の向きの変数", 0, "OK", project=without_expansion("YES"),
                  plists=plist_orientation("$(MINUTES_ORIENTATION)"))
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
    # ビルドが参照先の plist の変数を展開しない構成では、空でない値に展開される変数でも認めない（向きの検査と同じ）。
    # 変数を含まない値は、展開しない構成でも認める
    mic_expansion = lambda value: lambda t: replaced(define("会議を録音します。")(t), '"GENERATE_INFOPLIST_FILE": "NO",',  # noqa: E731
                                                     f'"GENERATE_INFOPLIST_FILE": "NO",\n        "INFOPLIST_EXPAND_BUILD_SETTINGS": "{value}",')
    settings_case("plist の変数を展開しない構成のマイクの利用目的の変数", 1, "変数が展開されない", project=mic_expansion("NO"),
                  plists={"Config/App-Settings.plist": mic_variable})
    settings_case("plist の変数を展開する構成のマイクの利用目的の変数", 0, "OK", project=mic_expansion("YES"),
                  plists={"Config/App-Settings.plist": mic_variable})
    settings_case("plist の変数を展開しない構成の変数のないマイクの利用目的", 0, "OK", project=mic_expansion("NO"),
                  plists={"Config/App-Settings.plist": microphone})
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
    # ビルドが参照先の plist の変数を展開しない構成では、NO に展開される変数も判定できないものとする（向きの検査と同じ）。
    # リテラルの YES・NO は、展開しない構成でもそのまま判定する
    for label, expansion, sharing_value, flag, want_code, want_text in (
        ("展開しない構成の NO に展開される変数", "NO", "NO", "$(SHARING)", 1, "UIFileSharingEnabled の値を展開できない"),
        ("展開するかを決められない構成の変数", "$(MINUTES_UNDEFINED)", "NO", "$(SHARING)", 1, "UIFileSharingEnabled の値を展開できない"),
        ("展開する構成の NO に展開される変数", "YES", "NO", "$(SHARING)", 0, "OK"),
        ("展開する構成の YES に展開される変数", "YES", "YES", "$(SHARING)", 1, "有効な値に展開される"),
        ("展開しない構成のリテラルの YES", "NO", "NO", "YES", 1, "UIFileSharingEnabled を有効にしない"),
        ("展開しない構成のリテラルの NO", "NO", "NO", "NO", 0, "OK"),
    ):
        settings_case(f"参照先の plist の有効にしない鍵: {label}", want_code, want_text,
                      project=lambda t, e=expansion, s=sharing_value: replaced(
                          info_plist_file("Config/App-Settings.plist")(t), '"GENERATE_INFOPLIST_FILE": "YES",',
                          f'"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_EXPAND_BUILD_SETTINGS": "{e}",\n        "SHARING": "{s}",'),
                      plists={"Config/App-Settings.plist": dict(OK_INFO_PLIST, UIFileSharingEnabled=flag)})
    # --tracked-only（専用の作業ツリーの検査）では、参照先の実体が起点の中の、HEAD のコミットで追跡したファイルであること
    for label, target, options, want_code, want_text in (
        ("追跡したファイル", "file", ["--tracked-only"], 0, "OK"),
        ("追跡したファイルを指すリンク", "link", ["--tracked-only"], 0, "OK"),
        ("起点の外を指すリンク", "outside", ["--tracked-only"], 2, "起点の外"),
        ("無視するファイル", "ignored", ["--tracked-only"], 2, "追跡していない"),
        ("無視するファイルを指すリンク", "link-ignored", ["--tracked-only"], 2, "追跡していない"),
        ("ステージしただけのファイル", "staged", ["--tracked-only"], 2, "追跡していない"),
        ("起点の外を指すリンク（通常の実行）", "outside", [], 0, "OK"),
        ("無視するファイル（通常の実行）", "ignored", [], 0, "OK"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            root, env = Path(directory) / "root", isolated_git_env()
            reference = root / "Config" / "App-Settings.plist"
            reference.parent.mkdir(parents=True)
            contents = plistlib.dumps(OK_INFO_PLIST)
            (root / "project.xcproj").write_text(info_plist_file("Config/App-Settings.plist")(OK_PROJECT), encoding="utf-8")
            (root / ".gitignore").write_text("Ignored.plist\n" + ("App-Settings.plist\n" if target == "ignored" else ""), encoding="utf-8")
            write_privacy_manifest(root)
            real = {"link": root / "Config" / "Real.plist", "link-ignored": root / "Config" / "Ignored.plist",
                    "outside": Path(directory) / "Outside.plist"}.get(target, reference)
            if real != reference:
                reference.symlink_to(real)
            if target != "staged":
                real.write_bytes(contents)
            for command in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *command], env=env, check=True,
                               capture_output=True)
            if target == "staged":
                real.write_bytes(contents)
                subprocess.run(["git", "-C", str(root), "add", "-A"], env=env, check=True, capture_output=True)
            expect(f"settings: 参照先: {label}", [str(SCRIPTS / "check-project-settings.py"), "--project", str(root / "project.xcproj"),
                                                  "--root", str(root), *options], want_code, want_text, env=env)
    # 名前で見つけたファイルも、実体が起点の中の追跡したファイルであること（通常の実行では求めない）
    for name in ("Info.plist", "App.entitlements"):
        for label, options, want_code, want_text in (
            ("追跡したファイル", ["--tracked-only"], 0, "OK"), ("追跡したファイルを指すリンク", ["--tracked-only"], 0, "OK"),
            ("起点の外を指すリンク", ["--tracked-only"], 2, "起点の外"), ("無視するファイルを指すリンク", ["--tracked-only"], 2, "追跡していない"),
            ("無視するファイル", ["--tracked-only"], 2, "追跡していない"), ("起点の外を指すリンク", [], 0, "OK"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                root, env = Path(directory) / "root", isolated_git_env()
                found = root / "Other" / name
                found.parent.mkdir(parents=True)
                (root / "project.xcproj").write_text(OK_PROJECT, encoding="utf-8")
                (root / ".gitignore").write_text("ignored.data\n" + (f"{name}\n" if label == "無視するファイル" else ""), encoding="utf-8")
                write_privacy_manifest(root)
                real = {"追跡したファイルを指すリンク": found.parent / "real.data", "起点の外を指すリンク": Path(directory) / "outside.data",
                        "無視するファイルを指すリンク": found.parent / "ignored.data"}.get(label, found)
                real.write_bytes(plistlib.dumps({}))
                if real != found:
                    found.symlink_to(real)
                commit_all(root, env)
                expect(f"settings: 見つけた {name}: {label}（{options}）", [
                    str(SCRIPTS / "check-project-settings.py"), "--project", str(root / "project.xcproj"), "--root", str(root), *options],
                    want_code, want_text, env=env)
    for label, ignored, options, want_code, want_text in (
        ("追跡したアプリのマニフェスト", False, ["--tracked-only"], 0, "OK"),
        ("追跡していないアプリのマニフェスト", True, ["--tracked-only"], 2, "追跡していない"),
        ("追跡していないアプリのマニフェスト（通常の実行）", True, [], 0, "OK"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            root, env = Path(directory) / "root", isolated_git_env()
            root.mkdir()
            (root / "project.xcproj").write_text(OK_PROJECT, encoding="utf-8")
            (root / ".gitignore").write_text("PrivacyInfo.xcprivacy\n" if ignored else "", encoding="utf-8")
            write_privacy_manifest(root)
            commit_all(root, env)
            expect(f"settings: マニフェスト: {label}", [
                str(SCRIPTS / "check-project-settings.py"), "--project", str(root / "project.xcproj"), "--root", str(root), *options],
                want_code, want_text, env=env)
    # 起点の外のプロジェクトのアプリのマニフェストは、名前の走査で見つからないので、参照先と同じく位置から照合する
    for options, want_code, want_text in ((["--tracked-only"], 2, "起点の外"), ([], 0, "OK")):
        with tempfile.TemporaryDirectory() as directory:
            root, env = Path(directory) / "root", isolated_git_env()
            root.mkdir()
            (Path(directory) / "project.xcproj").write_text(OK_PROJECT, encoding="utf-8")
            write_privacy_manifest(Path(directory))
            (root / ".gitignore").write_text("", encoding="utf-8")  # 空のリポジトリはコミットできない
            commit_all(root, env)
            expect(f"settings: マニフェスト: 起点の外のアプリのマニフェスト（{options}）", [
                str(SCRIPTS / "check-project-settings.py"), "--project", str(Path(directory) / "project.xcproj"), "--root", str(root),
                *options], want_code, want_text, env=env)
    settings_case("どのターゲットも参照しない plist の変数は判定できない", 1, "UIFileSharingEnabled の値を展開できない",
                  plists={"Other/Info.plist": {"UIFileSharingEnabled": "$(SHARING)"}})

    # plist の変数は、その plist を参照する構成・SDK のビルド設定だけで展開する
    def debug_plist(debug: str, release: str) -> Callable[[str], str]:
        return lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",',
                                  '"GENERATE_INFOPLIST_FILE": "YES",\n        "INFOPLIST_FILE[config=Debug]": "Config/Debug.plist",'
                                  f'\n        "SHARING[config=Debug]": "{debug}",\n        "SHARING[config=Release]": "{release}",')
    debug_only = {"Config/Debug.plist": dict(OK_INFO_PLIST, UIFileSharingEnabled="$(SHARING)")}
    settings_case("Debug だけが参照する plist の変数が Debug で YES に展開される", 1, "UIFileSharingEnabled を有効にしない",
                  project=debug_plist("YES", "NO"), plists=debug_only)
    settings_case("参照しない Release の値は plist の判定に含めない", 0, project=debug_plist("NO", "YES"), plists=debug_only)

    # GENERATE_INFOPLIST_FILE の変数は展開してから判定する。展開できなければ、生成しない経路も検査する
    def generate(value: str, define: str = "") -> Callable[[str], str]:
        return lambda t: replaced(t, '"GENERATE_INFOPLIST_FILE": "YES",', f'"GENERATE_INFOPLIST_FILE": "{value}",{define}')
    settings_case("GENERATE_INFOPLIST_FILE の変数が YES に展開される", 0,
                  project=generate("$(GENERATE)", '\n        "GENERATE": "YES",'))
    settings_case("GENERATE_INFOPLIST_FILE の変数が NO に展開される", 1, "NSMicrophoneUsageDescription がない",
                  project=generate("$(GENERATE)", '\n        "GENERATE": "NO",'))
    settings_case("GENERATE_INFOPLIST_FILE の変数を展開できない", 1, "NSMicrophoneUsageDescription がない",
                  project=generate("$(GENERATE)"))
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
    # iPhone の修飾子つきの鍵（~iphone・-iphoneos・-iphoneos~iphone）は元の鍵より優先されるので、最も具体的なものを検査する
    for modifier in ("~iphone", "-iphoneos", "-iphoneos~iphone"):
        settings_case(f"生成しない Info.plist の {modifier} のマイクの利用目的が空", 1,
                      f"Config/App-Settings.plist の NSMicrophoneUsageDescription{modifier} が空",
                      project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                      plists={"Config/App-Settings.plist": dict(microphone, **{f"NSMicrophoneUsageDescription{modifier}": " "})})
    settings_case("生成しない Info.plist の -iphoneos~iphone のマイクの利用目的が文字列でない", 1,
                  "NSMicrophoneUsageDescription-iphoneos~iphone が空",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": dict(microphone, **{"NSMicrophoneUsageDescription-iphoneos~iphone": []})})
    settings_case("生成しない Info.plist の -iphoneos~iphone の変数を展開できない", 1, "の値を展開できない",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": dict(microphone, **{"NSMicrophoneUsageDescription-iphoneos~iphone": "$(MIC_TEXT)"})})
    # 使われない元の鍵が空でも、より具体的な鍵に空でない値があれば iPhone のビルドでは足りる
    settings_case("生成しない Info.plist の元の鍵が空でも ~iphone に利用目的がある", 0, "OK",
                  project=lambda t: generate_no(info_plist_file("Config/App-Settings.plist")(t)),
                  plists={"Config/App-Settings.plist": dict(microphone, NSMicrophoneUsageDescription="",
                                                            **{"NSMicrophoneUsageDescription~iphone": "会議を録音するためにマイクを使用します。"})})
    settings_case("ビルド設定の ~iphone のマイクの利用目的が空", 1, "INFOPLIST_KEY_NSMicrophoneUsageDescription~iphone が空",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n'
                                             '        "INFOPLIST_KEY_NSMicrophoneUsageDescription~iphone": " ",'))
    settings_case("ビルド設定の -iphoneos のマイクの利用目的が配列", 1, "INFOPLIST_KEY_NSMicrophoneUsageDescription-iphoneos が文字列でない",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n'
                                             '        "INFOPLIST_KEY_NSMicrophoneUsageDescription-iphoneos": ["a"],'))
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
    # 必須の設定は SDK ごとに求める。両方の SDK に条件付きの正しい値があれば通り、片方の SDK の値がなければ違反
    settings_case("SDK ごとの条件付きの必須の値", 0, "OK",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0",',
                                             '"SWIFT_VERSION[sdk=iphoneos*]": "6.0",\n        "SWIFT_VERSION[sdk=iphonesimulator*]": "6.0",'))
    settings_case("片方の SDK にだけある必須の値", 1, "SWIFT_VERSION が設定されていない（構成 Debug（iphonesimulator）",
                  project=lambda t: replaced(t, '"SWIFT_VERSION": "6.0",', '"SWIFT_VERSION[sdk=iphoneos*]": "6.0",'))
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
    settings_case("空の SDK の条件の空の値は、条件のない有効な値を上書きしない", 0, "OK",
                  project=lambda t: replaced(t, '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",',
                                             '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",\n'
                                             '        "INFOPLIST_KEY_NSMicrophoneUsageDescription[sdk=]": "",'))
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
    # arch の条件は、その arch のビルドにだけ効く。条件に合わない arch では、元の鍵・条件のない書き方で判定する
    mic = '"INFOPLIST_KEY_NSMicrophoneUsageDescription": "会議を録音するためにマイクを使用します。",'
    for label, added, base_value, want, want_text in (
        ("~iphone が arm64 だけで、元の鍵がある", "~iphone[arch=arm64]", "会議を録音する。", 0, "OK"),
        ("~iphone が arm64 だけで、元の鍵が空", "~iphone[arch=arm64]", "", 1, "が空（構成 Debug（iphonesimulator・x86_64）"),
        ("~iphone のワイルドカードの arch と元の鍵", "~iphone[arch=arm64*]", "会議を録音する。", 0, "OK"),
        ("~iphone がすべての arch なら元の鍵は効かない", "~iphone[arch=*]", "", 0, "OK"),
    ):
        settings_case(f"arch の条件: {label}", want, want_text, project=lambda t, added=added, base_value=base_value: replaced(
            t, mic, f'"INFOPLIST_KEY_NSMicrophoneUsageDescription": "{base_value}",\n'
                    f'        "INFOPLIST_KEY_NSMicrophoneUsageDescription{added}": "会議を録音する。",'))
    settings_case("arch の条件: 元の鍵がなければ条件の外の arch が欠落", 1, "x86_64", project=lambda t: replaced(
        t, mic, '"INFOPLIST_KEY_NSMicrophoneUsageDescription~iphone[arch=arm64]": "会議を録音する。",'))
    # 欠落の判定は、実際にビルドする arch（ARCHS から EXCLUDED_ARCHS を除いたもの。指定がなければ Xcode の既定）だけで行う
    only = '"INFOPLIST_KEY_NSMicrophoneUsageDescription[arch=arm64]": "会議を録音する。"'
    for label, extra, want, want_text in (
        ("ARCHS が arm64", '"ARCHS": "arm64"', 0, "OK"),
        ("除いた x86_64", '"EXCLUDED_ARCHS[sdk=iphonesimulator*]": "x86_64"', 0, "OK"),
        ("継承した値から除いた x86_64", '"EXCLUDED_ARCHS": "$(inherited) x86_64"', 0, "OK"),
        ("既定の変数", '"ARCHS": "$(ARCHS_STANDARD)"', 1, "がない（構成 Debug（iphonesimulator・x86_64）・Release（iphonesimulator・x86_64）。"),
        ("既定の継承", '"ARCHS": "$(inherited)"', 1, "がない（構成 Debug（iphonesimulator・x86_64）・Release（iphonesimulator・x86_64）。"),
        ("既定のまま", "", 1, "がない（構成 Debug（iphonesimulator・x86_64）・Release（iphonesimulator・x86_64）。"),
        ("構成で違う ARCHS", '"ARCHS[config=Debug]": "arm64"', 1, "がない（構成 Release（iphonesimulator・x86_64）。"),
        ("実機にもほかの arch", '"ARCHS[sdk=iphoneos*]": "arm64 arm64e"', 1, "Debug（iphoneos・arm64e）"),
        # 決められない値では判定を弱めない
        ("展開できない ARCHS", '"ARCHS": "$(UNKNOWN)"', 1, "Debug（iphoneos・arm64e）"),
        ("空の ARCHS", '"ARCHS": ""', 1, "Debug（iphoneos・arm64e）"),
        ("arch の条件付きだけの ARCHS", '"ARCHS[arch=arm64]": "arm64"', 1, "Debug（iphoneos・arm64e）"),
    ):
        settings_case(f"ビルドする arch: {label}", want, want_text, project=lambda t, extra=extra: replaced(
            t, mic, f"{only},\n        {extra}," if extra else f"{only},"))
    # ビルドしない arch だけの参照先の変数も、当たりうる組で展開して判定する
    for value, want, want_text in (("YES", 1, "有効な値に展開される"), ("NO", 0, "OK"), ("$(UNKNOWN)", 1, "UIFileSharingEnabled の値を展開できない")):
        settings_case(f"ビルドする arch: armv7 だけの参照先の変数（{value}）", want, want_text, project=lambda t, value=value: replaced(
            info_plist_file("Config/App-Settings.plist")(t), '"INFOPLIST_FILE":', f'"SHARE": "{value}",\n        "INFOPLIST_FILE[arch=armv7]":'),
            plists={"Config/App-Settings.plist": dict(microphone, UIFileSharingEnabled="$(SHARE)")})
    settings_case("ビルドする arch: armv7 だけの書き方の変数", 1, "有効な値に展開される", project=lambda t: replaced(
        t, mic, f'{mic}\n        "INFOPLIST_KEY_UIFileSharingEnabled[arch=armv7]": "$(SHARE)",\n        "SHARE": "YES",'))
    settings_case("ビルドする arch: ARCHS の arch だけの変数", 0, "OK", project=lambda t: replaced(
        t, mic, f'{mic}\n        "ARCHS": "arm64",\n        "INFOPLIST_KEY_UIFileSharingEnabled": "$(SHARE)",\n'
                '        "SHARE[arch=arm64]": "NO",'))
    # 必須の設定も、実際にビルドする構成・SDK・arch の組ごとに求める
    version = '"SWIFT_VERSION": "6.0",'
    for label, text, want, want_text in (
        ("ビルドする arch だけの値", '"ARCHS": "arm64",\n        "SWIFT_VERSION[arch=arm64]": "6.0",', 0, "OK"),
        ("一部の arch の値を欠く", '"SWIFT_VERSION[arch=arm64]": "6.0",', 1,
         "SWIFT_VERSION が設定されていない（構成 Debug（iphonesimulator・x86_64）・Release（iphonesimulator・x86_64）。"),
        ("arch ごとの値がそろう", '"SWIFT_VERSION[arch=arm64]": "6.0",\n        "SWIFT_VERSION[arch=x86_64]": "6.0",', 0, "OK"),
        ("ビルドする arch の許さない値", f'{version}\n        "SWIFT_VERSION[arch=x86_64]": "5.0",', 1,
         "SWIFT_VERSION[arch=x86_64] = 5.0（構成 Debug（iphonesimulator・x86_64）・Release（iphonesimulator・x86_64）。"),
        ("ビルドしない arch の値", f'{version}\n        "SWIFT_VERSION[arch=armv7]": "5.0",', 0, "OK"),
        ("決められない ARCHS", '"ARCHS": "$(UNKNOWN)",\n        "SWIFT_VERSION[arch=arm64]": "6.0",', 1, "Debug（iphoneos・arm64e）"),
    ):
        settings_case(f"必須の設定の arch: {label}", want, want_text, project=lambda t, text=text: replaced(t, version, text))
    # 必須の設定の値の変数は、その組のビルド設定で展開してから比べる。展開できない値・許さない値に展開される値は違反
    for label, text, want, want_text in (
        ("許す値", '"MINUTES_SWIFT": "6.0",\n        "SWIFT_VERSION": "$(MINUTES_SWIFT)",', 0, "OK"),
        ("許さない値", '"MINUTES_SWIFT": "5.0",\n        "SWIFT_VERSION": "$(MINUTES_SWIFT)",', 1,
         "SWIFT_VERSION = $(MINUTES_SWIFT)（5.0 に展開される）"),
        ("構成で違う値", '"MINUTES_SWIFT[config=Debug]": "6.0",\n        "MINUTES_SWIFT[config=Release]": "5.0",\n'
         '        "SWIFT_VERSION": "$(MINUTES_SWIFT)",', 1, "（5.0 に展開される）（構成 Release（iphoneos）・Release（iphonesimulator）。"),
        ("展開できない値", '"SWIFT_VERSION": "$(MINUTES_SWIFT)",', 1, "SWIFT_VERSION = $(MINUTES_SWIFT)（展開できない）"),
        ("継承元のない $(inherited)", '"SWIFT_VERSION": "$(inherited)",', 1, "SWIFT_VERSION = $(inherited)（展開できない）"),
    ):
        settings_case(f"必須の設定の変数: {label}", want, want_text, project=lambda t, text=text: replaced(t, version, text))
    settings_case("必須の設定の変数: 継承元のある $(inherited)", 0, "OK", project=lambda t: replaced(
        replaced(t, version, '"SWIFT_VERSION": "$(inherited)",'), '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",',
        '"IPHONEOS_DEPLOYMENT_TARGET": "27.2",\n    "SWIFT_VERSION": "6.0",'))
    # plist の重複した鍵は、後の値で前の値を隠すので読めないものとする（XML・バイナリ形式、入れ子の中を含む）
    for label, form in (("XML", plistlib.FMT_XML), ("バイナリ形式", plistlib.FMT_BINARY)):
        for place, data in (("最上位", {"KeyAAA": "x", "KeyBBB": "y"}), ("入れ子", {"Top": [{"KeyAAA": "x", "KeyBBB": "y"}]})):
            dumped = plistlib.dumps(data, fmt=form)
            settings_case(f"plist の重複のない鍵（{label}・{place}）", 0, "OK", raw={"Sub/Info.plist": dumped})
            settings_case(f"plist の重複した鍵（{label}・{place}）", 2, "重複した鍵" if form == plistlib.FMT_XML else "設定を読めない",
                          raw={"Sub/Info.plist": dumped.replace(b"KeyBBB", b"KeyAAA")})
    # plist の深い入れ子・循環（バイナリ形式）は読めないものとする。ふつうの入れ子は認める
    def deep_plist(depth: int) -> bytes:
        return ('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">' + "<dict><key>a</key>" * depth + "<string>x</string>"
                + "</dict>" * depth + "</plist>").encode("utf-8")

    cyclic = (b"bplist00" + b"\xd1\x01\x00" + b"\x51a" + b"\x08\x0b" + bytes(6) + b"\x01\x01" + (2).to_bytes(8, "big")
              + (0).to_bytes(8, "big") + (13).to_bytes(8, "big"))
    for label, data, want, want_text in (("ふつうの入れ子", deep_plist(50), 0, "OK"), ("深すぎる入れ子", deep_plist(2000), 2, "設定を読めない"),
                                         ("循環", cyclic, 2, "循環している")):
        settings_case(f"plist の{label}", want, want_text, raw={"Sub/Info.plist": data})
        if want:
            settings_case(f"plist の{label}のパス", want, "Sub/Info.plist", raw={"Sub/Info.plist": data})
    # バイナリ形式の plist は同じ辞書・配列を複数の場所から参照できる。同じものの中は 1 回だけ調べ、段数に対して指数の手間に
    # ならないこと（各段で 4 回参照する 40 段。参照のたびに下りると終わらない）。中の違反は 1 件だけ報告する
    shared: object = {"UIFileSharingEnabled": True}
    for _ in range(40):
        shared = {"a": shared, "b": shared, "c": [shared, shared]}
    settings_case("plist の共有された辞書・配列", 1, "NG: 設定の違反 1 件",
                  raw={"Sub/Info.plist": plistlib.dumps({"Top": shared}, fmt=plistlib.FMT_BINARY)}, timeout=60)
    # 生成する arch は参照先が空でもビルド設定の値で、生成しない arch は参照先の plist で判定する
    for label, generated, want, want_text in (("生成する arch だけ参照先が空", "arm64", 0, "OK"),
                                              ("生成しない arch の参照先が空", "x86_64", 1, "Debug（iphoneos・arm64）")):
        settings_case(f"arch の条件: {label}", want, want_text, project=lambda t, generated=generated: replaced(
            with_file(t), '"INFOPLIST_FILE": "Config/App-Settings.plist",',
            '"INFOPLIST_FILE": "Config/App-Settings.plist",\n        "INFOPLIST_FILE[arch=arm64]": "",\n'
            f'        "GENERATE_INFOPLIST_FILE[arch={generated}]": "YES",'), plists={"Config/App-Settings.plist": microphone})
    # arch の条件付きの参照先も、禁止する鍵を検査する
    for pattern in ("armv7", "arm?9*"):
        settings_case(f"arch の条件: {pattern} だけの参照先の違反", 1, "NSAppTransportSecurity", project=lambda t, pattern=pattern: replaced(
            info_plist_file("Config/App-Settings.plist")(t), '"INFOPLIST_FILE":', f'"INFOPLIST_FILE[arch={pattern}]":'),
            plists={"Config/App-Settings.plist": dict(microphone, NSAppTransportSecurity={})})
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
    # プライバシーマニフェスト（SEC-PRV-03）
    def manifest(**changes: object) -> Dict[str, dict]:
        content = dict(OK_PRIVACY_MANIFEST, **changes)
        return {PRIVACY_MANIFEST: {key: value for key, value in content.items() if value is not None}}

    def accessed(category: str, *reasons: str) -> dict:
        return {"NSPrivacyAccessedAPIType": f"NSPrivacyAccessedAPICategory{category}", "NSPrivacyAccessedAPITypeReasons": list(reasons)}

    declared = OK_PRIVACY_MANIFEST["NSPrivacyAccessedAPITypes"]
    settings_case("アプリのプライバシーマニフェストがない", 1, "Sample/PrivacyInfo.xcprivacy がない", without=(PRIVACY_MANIFEST,))
    settings_case("トラッキングをすると申告している", 1, "NSPrivacyTracking を false", plists=manifest(NSPrivacyTracking=True))
    settings_case("トラッキングの申告がない", 1, "NSPrivacyTracking を false", plists=manifest(NSPrivacyTracking=None))
    settings_case("トラッキングのドメインがある", 1, "NSPrivacyTrackingDomains を空",
                  plists=manifest(NSPrivacyTrackingDomains=["example.com"]))
    settings_case("収集するデータの申告がある", 1, "NSPrivacyCollectedDataTypes を空",
                  plists=manifest(NSPrivacyCollectedDataTypes=[{"NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeAudioData"}]))
    settings_case("収集するデータの申告の鍵がない", 1, "NSPrivacyCollectedDataTypes を空", plists=manifest(NSPrivacyCollectedDataTypes=None))
    settings_case("決めていない API の種類を申告している", 1, "NSPrivacyAccessedAPICategorySystemBootTime",
                  plists=manifest(NSPrivacyAccessedAPITypes=declared + [accessed("SystemBootTime", "35F9.1")]))
    settings_case("理由コードが決めたものと違う", 1, "理由コード 1C8F.1",
                  plists=manifest(NSPrivacyAccessedAPITypes=declared[:2] + [accessed("UserDefaults", "CA92.1", "1C8F.1")]))
    settings_case("決めた申告が足りない", 1, "NSPrivacyAccessedAPICategoryDiskSpace の申告",
                  plists=manifest(NSPrivacyAccessedAPITypes=[declared[0], declared[2]]))
    settings_case("申告の一覧がない", 1, "NSPrivacyAccessedAPICategoryFileTimestamp の申告", plists=manifest(NSPrivacyAccessedAPITypes=None))
    settings_case("申告の形式が違うマニフェストは合格にしない", 2, "読めない", plists=manifest(NSPrivacyAccessedAPITypes=["C617.1"]))
    settings_case("壊れたマニフェストは合格にしない", 2, "読めない", raw={PRIVACY_MANIFEST: b"<plist"}, without=(PRIVACY_MANIFEST,))
    extension = dict(OK_PRIVACY_MANIFEST, NSPrivacyAccessedAPITypes=[])
    settings_case("拡張のマニフェストは、決めた申告の一部だけでよい", 0, "OK", plists={"Widgets/PrivacyInfo.xcprivacy": extension})
    settings_case("拡張のマニフェストのトラッキング", 1, "Widgets/PrivacyInfo.xcprivacy: NSPrivacyTracking",
                  plists={"Widgets/PrivacyInfo.xcprivacy": dict(extension, NSPrivacyTracking=True)})
    settings_case("拡張のマニフェストの決めていない申告", 1, "NSPrivacyAccessedAPICategoryActiveKeyboards",
                  plists={"Widgets/PrivacyInfo.xcprivacy": dict(extension, NSPrivacyAccessedAPITypes=[accessed("ActiveKeyboards", "3EC4.1")])})
    # 申告した種類には理由コードが 1 つ以上要る（同じ種類の別の要素で決めた申告が揃っていても、空の要素は違反。拡張も同じ）
    settings_case("アプリのマニフェストの空の理由コード", 1, "NSPrivacyAccessedAPICategoryDiskSpace の理由コードが空",
                  plists=manifest(NSPrivacyAccessedAPITypes=declared + [accessed("DiskSpace")]))
    settings_case("拡張のマニフェストの空の理由コード", 1, "Widgets/PrivacyInfo.xcprivacy: NSPrivacyAccessedAPICategoryDiskSpace の理由コードが空",
                  plists={"Widgets/PrivacyInfo.xcprivacy": dict(extension, NSPrivacyAccessedAPITypes=[accessed("DiskSpace")])})
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
    approved = {"identity": "sample-lib", "url": "https://example.com/owner/sample-lib.git", "version": "1.2.3",
                "license": "MIT", "approvedOn": "2026-11-16", "ledger": "dependencies-and-costs.md §3"}
    manifest = 'let package = Package(name: "A", dependencies: [.package(url: "https://example.com/owner/sample-lib.git", from: "1.2.3")])\n'
    local = 'let package = Package(name: "A", dependencies: [.package(path: "Local")])\n'
    local_manifest = 'let package = Package(name: "Local")\n'
    project = '{"targets": [], "packages": [{"location": "https://example.com/owner/sample-lib.git"},],}'

    dependencies_case("依存がない", 0, "OK", {})
    # 名前が合うディレクトリは読めないものとする（manifest として検査できないものを、ないものとして通さない）
    for name in ("Package.resolved", "Sub/Package.swift", "Package@swift-6.0.swift", "App.xcodeproj/project.xcproj"):
        dependencies_case(f"{name} がディレクトリ", 2, "ディレクトリ", {f"{name}/keep.txt": ""})
    dependencies_case("除外する場所の中の同じ名前のディレクトリ", 0, "OK", {".build/Package.swift/keep.txt": ""})
    dependencies_case("許可リストにない依存（Package.resolved）", 1, "sample-lib 1.2.3", {"Package.resolved": resolved})
    dependencies_case("承認済みの依存", 0, "OK", {"Package.resolved": resolved, "Package.swift": manifest}, [approved])
    dependencies_case("承認した版と違う", 1, "sample-lib 1.2.3", {"Package.resolved": resolved}, [dict(approved, version="1.2.2")])
    dependencies_case("版で固定していない", 1, "版で固定されていない", {"Package.resolved": branch}, [approved])
    dependencies_case("許可リストにない依存（Package.swift）", 1, "sample-lib.git", {"Sub/Package.swift": manifest})
    # ローカルの Package は Packages/ の下に置く（check.sh の手順 7 がカバレッジを測る写しは Packages/ だけを展開する）
    dependencies_case("ローカルの Package は認める", 0, "OK",
                      {"Packages/A/Package.swift": local.replace('"Local"', '"../Local"'), "Packages/Local/Package.swift": local_manifest})
    # ローカルの Package の依存は、ビルドの起点の Package.resolved でも照合する（起点の確定版だけが使われる）
    nested_files = {"Packages/A/Package.swift": local.replace('"Local"', '"../B"'), "Packages/B/Package.swift": local.replace('"Local"', '"../Local"'),
                    "Packages/Local/Package.swift": manifest, "Packages/Local/Package.resolved": resolved, "Packages/B/Package.resolved": resolved}
    dependencies_case("起点に間接の依存の確定版がある", 0, "OK", {**nested_files, "Packages/A/Package.resolved": resolved}, [approved])
    dependencies_case("起点に間接の依存の確定版がない", 1, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない", nested_files, [approved])
    old_pin = json.dumps({"pins": [dict(pin, state=dict(pin["state"], version="1.2.2"))], "version": 3})
    dependencies_case("起点の確定版が間接の依存の版の指定と合わない", 1, "Packages/A を起点とする Packages/Local/Package.swift: 宣言の版の指定と確定版 1.2.2 が合わない",
                      {**nested_files, "Packages/A/Package.resolved": old_pin}, [approved, dict(approved, version="1.2.2")])
    newer_pin = json.dumps({"pins": [dict(pin, state=dict(pin["state"], version="1.2.4"))], "version": 3})
    dependencies_case("起点の確定版が許可リストにない", 1, "Packages/A を起点とする Packages/Local/Package.swift: 確定版 1.2.4 は許可リストにない",
                      {**nested_files, "Packages/A/Package.resolved": newer_pin}, [approved])
    dependencies_case("ローカルの Package 自身の確定版がない", 1, "Packages/Local/Package.swift: 確定版がない",
                      {**{name: text for name, text in nested_files.items() if name != "Packages/Local/Package.resolved"},
                       "Packages/A/Package.resolved": resolved}, [approved])
    # 版別の manifest の宣言は混ぜない（同時には読まれない辺をつながない）。選ばれた組がリモートの依存に届けば、起点の確定版を求める
    tools = "// swift-tools-version: {}\n"
    to = lambda name: local.replace('"Local"', f'"../{name}"')  # noqa: E731
    plain = 'let package = Package(name: "P")\n'
    for label, b_plain, b_variant, want in (
        ("別の版の辺は組み合わさらない", to("Local"), plain, 0),
        ("同じ版の辺は組み合わさる", plain, to("Local"), 1),
    ):
        dependencies_case(f"版別の manifest: {label}", want, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない" if want else "OK", {
            "Packages/A/Package.swift": tools.format("5.5") + plain, "Packages/A/Package@swift-6.swift": tools.format("6.0") + to("B"),
            "Packages/B/Package.swift": tools.format("5.5") + b_plain, "Packages/B/Package@swift-6.swift": tools.format("6.0") + b_variant,
            "Packages/B/Package.resolved": resolved,
            "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    # 一致する接尾辞がなければ、tools-version が Package.swift より新しい最大の下位の版を選ぶ（等しければ Package.swift）
    for label, suffix, variant_tools, regular, variant, want in (
        ("下位の版を選ぶ", "5.9", "5.9", plain, to("Local"), 1),
        ("下位の版を選べば Package.swift の辺はたどらない", "5.9", "5.9", to("Local"), plain, 0),
        ("tools-version が新しくなければ選ばない", "5.9", "5.5", to("Local"), plain, 1),
        ("tools-version が新しくない版別の辺はたどらない", "5.9", "5.5", plain, to("Local"), 0),
    ):
        # 起点は 6.0 以上でだけ読める（接尾辞 5.9 とは一致しない）
        dependencies_case(f"版別の manifest の選び方: {label}", want, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない" if want else "OK", {
            "Packages/A/Package.swift": tools.format("6.0") + to("B"), "Packages/B/Package.swift": tools.format("5.5") + regular,
            f"Packages/B/Package@swift-{suffix}.swift": tools.format(variant_tools) + variant, "Packages/B/Package.resolved": resolved,
            "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    dependencies_case("一致する接尾辞の manifest は tools-version によらず選ぶ", 1, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない", {
        "Packages/A/Package.swift": tools.format("6.0") + to("B"), "Packages/B/Package.swift": tools.format("6.0") + plain,
        "Packages/B/Package@swift-6.swift": tools.format("6.0") + to("Local"), "Packages/B/Package.resolved": resolved,
        "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    dependencies_case("起点を読めないツールでだけ選ばれる manifest はたどらない", 0, "OK", {
        "Packages/A/Package.swift": tools.format("6.0") + to("B"), "Packages/B/Package.swift": tools.format("6.0") + plain,
        "Packages/B/Package@swift-5.9.swift": tools.format("5.9") + to("Local"), "Packages/B/Package.resolved": resolved,
        "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    dependencies_case("tools-version を読み取れない版別の manifest は、ありうるものをすべてたどる", 1,
                      "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない", {
                          "Packages/A/Package.swift": tools.format("6.0") + to("B"), "Packages/B/Package.swift": plain,
                          "Packages/B/Package@swift-5.9.swift": to("Local"), "Packages/B/Package.resolved": resolved,
                          "Packages/Local/Package.swift": manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    # tools-version の指定は、6.0 以降なら先頭の行でなくてもよい（行頭の「//」で最初に一致した行）。5.x の指定は先頭の行だけ。
    # 読み取れれば、起点を読める 6.0 以上のツールでは Package.swift だけをたどる（読み取れなければ版別の辺もたどる）
    for label, head, want in (
        ("コメントの後の 6.0", "// Copyright\n\n// swift-tools-version: 6.0\n", 0),
        ("ブロックコメントの後の字下げした 6.0", "/* a\n b */\n  //swift-tools-version:6.0\n", 0),
        ("CR の後の 6.0", "// Copyright\r// swift-tools-version: 6.0\n", 0),
        ("最初に一致した行を読む", "// Copyright\n// swift-tools-version: 6.0\n// swift-tools-version: 5.5\n", 0),
        ("後の行の 5.x は読まない", "// Copyright\n// swift-tools-version: 5.9\n// swift-tools-version: 6.0\n", 1),
        ("行頭でない指定は読まない", "let x = 1 // swift-tools-version: 6.0\n", 1),
    ):
        dependencies_case(f"tools-version の位置: {label}", want, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない" if want else "OK", {
            "Packages/A/Package.swift": tools.format("6.0") + to("B"), "Packages/B/Package.swift": head + plain,
            "Packages/B/Package@swift-5.9.swift": tools.format("5.9") + to("Local"), "Packages/B/Package.resolved": resolved,
            "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    # Package.swift の tools-version がツールより新しければ、最も新しい下位の版別の manifest を選ぶ（それも読めなければ、次へは移らない）
    for label, newer, want in (("版別の manifest を選ぶ", {}, 1),
                               ("選んだ版別の manifest も読めない", {"Packages/B/Package@swift-6.0.swift": tools.format("99.0") + plain}, 0)):
        dependencies_case(f"ツールより新しい Package.swift: {label}", want, "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない" if want else "OK", {
            "Packages/A/Package.swift": tools.format("6.1") + to("B"), "Packages/B/Package.swift": tools.format("99.0") + plain,
            "Packages/B/Package@swift-5.9.swift": tools.format("5.9") + to("Local"), **newer, "Packages/B/Package.resolved": resolved,
            "Packages/Local/Package.swift": tools.format("5.5") + manifest, "Packages/Local/Package.resolved": resolved}, [approved])
    # 引数の式の中の「url :」（三項演算子）を、宣言の取得元のラベルと見なさない
    dependencies_case("三項演算子の中の url", 1, "取得元を判定できない", {
        "Package.swift": manifest.replace('url: "', 'path: flag ? url : "', 1), "Package.resolved": resolved}, [approved])
    # 間接の依存も「..」を字面で取り除いた場所をたどる（リンクの先の親ではない）
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in {"Packages/A/Package.swift": local.replace('"Local"', '"../Link/../Local"'),
                              "Packages/Local/Package.swift": manifest, "Packages/Local/Package.resolved": resolved,
                              "Packages/Deep/Local/Package.swift": local_manifest, "Packages/Deep/Inner/keep.txt": ""}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content, encoding="utf-8")
        (root / "Packages" / "Link").symlink_to("Deep/Inner")
        (root / "allowlist.json").write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": [approved]}),
                                             encoding="utf-8")
        expect("dependencies: リンクの後の「..」を字面で取り除く",
               [str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(root / "allowlist.json")], 1,
               "Packages/A を起点とする Packages/Local/Package.swift: 確定版がない")
    dependencies_case("循環するローカルの Package", 0, "OK",
                      {"Packages/A/Package.swift": local.replace('"Local"', '"../B"'), "Packages/B/Package.swift": local.replace('"Local"', '"../A"')})
    dependencies_case("Packages/ の外のローカルの Package", 1, "Packages/ の外",
                      {"Package.swift": local, "Local/Package.swift": local_manifest})
    dependencies_case("許可リストにない依存（project.xcproj）", 1, "example.com", {"App.xcodeproj/project.xcproj": project})
    # scheme の大文字小文字によらず取得元の URL として照合する（HTTPS:// を読み飛ばして合格にしない）
    upper = project.replace("https://", "HTTPS://", 1)
    dependencies_case("許可リストにない依存（project.xcproj・大文字の scheme）", 1, "HTTPS://", {"App.xcodeproj/project.xcproj": upper})
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
    # 形が YYYY-MM-DD でも、暦にない日付（月・日の範囲外、平年の 2 月 29 日）は読めないものとする。うるう年の 2 月 29 日は認める
    for label, day in (("月が範囲外", "2026-13-01"), ("日が範囲外", "2026-04-31"), ("平年の 2 月 29 日", "2026-02-29")):
        dependencies_case(f"存在しない承認日（{label}）", 2, "存在しない日付", {}, [dict(approved, approvedOn=day)])
    dependencies_case("うるう年の 2 月 29 日の承認日", 0, "OK", {"Package.resolved": resolved}, [dict(approved, approvedOn="2028-02-29")])
    dependencies_case("読めない Package.resolved は合格にしない", 2, "読めない", {"Package.resolved": "{"})
    # 名前が合うのに通常のファイルでないもの（壊れたシンボリックリンク）は、黙って除かず読めないものとする
    for name in ("Package.resolved", "Package.swift"):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Sub").mkdir()
            (Path(directory) / "Sub" / name).symlink_to("missing")
            allowlist = Path(directory) / "allowlist.json"
            allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}),
                                 encoding="utf-8")
            expect(f"dependencies: 壊れたシンボリックリンクの {name}", [
                str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 2, "読めない")
    # 同じ identity と版を取得元ごとに承認できる（後の項目が前の項目の取得元を上書きしない）
    mirror = dict(approved, url="https://example.org/mirror/sample-lib.git")
    dependencies_case("同じ identity と版の別の取得元の承認", 0, "OK",
                      {"Package.resolved": json.dumps({"pins": [dict(pin, location="https://example.org/mirror/sample-lib.git")],
                                                       "version": 3})}, [mirror, approved])
    dependencies_case("同じ identity と版の承認があっても未承認の取得元", 1, "取得元",
                      {"Package.resolved": json.dumps({"pins": [dict(pin, location="https://example.net/x/sample-lib.git")],
                                                       "version": 3})}, [mirror, approved])
    moved = json.dumps({"pins": [dict(pin, location="https://example.org/other/sample-lib.git")], "version": 3})
    dependencies_case("identity と版が同じでも取得元が違う", 1, "取得元", {"Package.resolved": moved}, [approved])
    unknown_source = json.dumps({"pins": [{k: v for k, v in pin.items() if k != "location"}], "version": 3})
    dependencies_case("取得元のない pin は合格にしない", 1, "取得元", {"Package.resolved": unknown_source}, [approved])
    legacy = json.dumps({"object": {"pins": [{"package": "sample-lib", "repositoryURL": "https://example.com/owner/sample-lib.git",
                                              "state": {"version": "1.2.3"}}]}, "version": 1})
    dependencies_case("古い形式の取得元（repositoryURL）", 0, "OK", {"Package.resolved": legacy}, [approved])
    # 古い形式の名前は、表示名（package）ではなく取得元の最後の要素で照合する（重複の判定と同じ）
    displayed = replaced(legacy, '"package": "sample-lib"', '"package": "SampleLib"')
    dependencies_case("古い形式の表示名が取得元の名前と違う", 0, "OK", {"Package.resolved": displayed}, [approved])
    dependencies_case("古い形式の表示名だけの承認", 1, "sample-lib 1.2.3", {"Package.resolved": displayed},
                      [dict(approved, identity="samplelib")])
    dependencies_case("古い形式で取得元がない", 2, "名前のない依存", {"Package.resolved": json.dumps(
        {"object": {"pins": [{"package": "sample-lib", "state": {"version": "1.2.3"}}]}, "version": 1})}, [approved])
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
    # 入れ子の引数（traits の中の呼び出し）の revision: は版の指定ではない
    dependencies_case("traits の中の revision は版の指定にしない", 0, "OK",
                      {"Package.resolved": resolved, "Package.swift": declare.format(
                          f'.package(url: "{url}", exact: "1.2.3", traits: [selectedTraits(revision: 2)])')}, [approved])
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
        "upToNextMajor": f'.package(url: "{url}", .upToNextMajor(from: "1.0.0"))',
        # ラベルとコロンの間の空白・コメントの跡も、版の指定として読む（判定できないものとして落とさない）
        "exact のラベルの後の空白": f'.package(url: "{url}", exact : "1.2.3")',
        "from のラベルの後のコメント": f'.package(url: "{url}", from /* 下限 */ : "1.0.0")',
        "upToNextMinor の from の後の空白": f'.package(url: "{url}", .upToNextMinor(from : "1.2.0"))',
    }.items():
        dependencies_case(f"確定版を含む版の指定（{label}）", 0, "OK",
                          {"Package.resolved": resolved, "Package.swift": declare.format(declaration)}, [approved])
    # 呼び出しの名前と「(」の間の空白・コメントによらず、.upToNextMinor の中の from: を .package の from:（upToNextMajor）と
    # 取り違えない（1.0.0 の upToNextMinor は 1.2.3 を含まない。upToNextMajor と見なすと含むので誤って通る）
    for label, declaration in {
        "空白": f'.package(url: "{url}", .upToNextMinor (from: "1.0.0"))',
        "コメント": f'.package(url: "{url}", .upToNextMinor /* 範囲 */ (from: "1.0.0"))',
        "改行": f'.package(url: "{url}", .upToNextMinor\n  (from: "1.0.0"))',
    }.items():
        dependencies_case(f"upToNextMinor の中の from:（{label}）", 1, "合わない",
                          {"Package.resolved": resolved, "Package.swift": declare.format(declaration)}, [approved])
    dependencies_case("コメントを挟んだ upToNextMinor の範囲", 0, "OK",
                      {"Package.resolved": resolved,
                       "Package.swift": declare.format(f'.package(url: "{url}", .upToNextMinor /* 範囲 */ (from: "1.2.0"))')},
                      [approved])
    # 既知の形でない呼び出しの中の from: は、.package の from: と見なさず、判定できないものとする
    for label, declaration in {
        "未知の呼び出し": f'.package(url: "{url}", custom(from: "1.0.0"))',
        "版の指定が 2 つ": f'.package(url: "{url}", from: "1.0.0", exact: "1.2.3")',
    }.items():
        dependencies_case(f"判定できない版の指定（{label}）", 1, "版の指定を判定できない",
                          {"Package.resolved": resolved, "Package.swift": declare.format(declaration)}, [approved])
    # 先行版の exact は、同じ先行版の確定版とだけ一致する（1.2.0-rc.1 と 1.2.0 を区別する）
    pre_pin = dict(pin, state={"revision": "0" * 40, "version": "1.2.0-rc.1"})
    pre_resolved = json.dumps({"pins": [pre_pin], "version": 3})
    pre_approved = dict(approved, version="1.2.0-rc.1")
    dependencies_case("先行版の exact と同じ確定版", 0, "OK",
                      {"Package.resolved": pre_resolved, "Package.swift": declare.format(f'.package(url: "{url}", exact: "1.2.0-rc.1")')},
                      [pre_approved])
    dependencies_case("先行版の exact と正式版の確定版", 1, "合わない",
                      {"Package.resolved": json.dumps({"pins": [dict(pin, state={"revision": "0" * 40, "version": "1.2.0"})],
                                                       "version": 3}),
                       "Package.swift": declare.format(f'.package(url: "{url}", exact: "1.2.0-rc.1")')},
                      [dict(approved, version="1.2.0")])
    dependencies_case("正式版の exact と先行版の確定版", 1, "合わない",
                      {"Package.resolved": pre_resolved, "Package.swift": declare.format(f'.package(url: "{url}", exact: "1.2.0")')},
                      [pre_approved])
    dependencies_case("範囲の境界の先行版", 1, "版の指定を判定できない",
                      {"Package.resolved": pre_resolved, "Package.swift": declare.format(f'.package(url: "{url}", from: "1.2.0-rc.1")')},
                      [pre_approved])
    # コメントを除いた跡の空白がラベルと「:」の間にあっても、取得元のラベルとして読む
    dependencies_case("コメントを挟んだ url のラベル", 0, "OK",
                      {"Package.resolved": resolved, "Package.swift": declare.format(
                          '.package(url /* 取得元 */ : "https://example.com/owner/sample-lib.git", exact: "1.2.3")')}, [approved])
    # 版のリテラルが式の一部（連結など）なら、版の指定を判定できないものとして扱う
    for label, declaration in {
        "from の連結": f'.package(url: "{url}", from: "1.0.0" + suffix)',
        "exact の連結": f'.package(url: "{url}", exact: "1.2.3" + suffix)',
        "範囲の上限の連結": f'.package(url: "{url}", "1.0.0"..<"2.0.0" + suffix)',
        "範囲の下限の連結": f'.package(url: "{url}", prefix + "1.0.0"..."1.2.3")',
    }.items():
        dependencies_case(f"式の一部の版のリテラル（{label}）", 1, "版の指定を判定できない",
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
        # 補間の中の正規表現のリテラルの「)」で補間が早く閉じたと見なすと、補間の中の「"」で外側が閉じ、「/*」で宣言を隠す
        "括弧を含む正規表現": f'let s = "\\(g(#/)/#) + "/*")"\n{unlisted}\n// */',
        # 「\\/#」の「/#」は終わりではない。終わりと見なすと、続く「"」が文字列の始まりに見え、後ろの宣言を本文として隠す
        "エスケープした区切りの正規表現": f'let s = "\\(g(#/\\/#"/#))"\n{unlisted}\nlet t = "x"',
        "拡張の区切りの正規表現": f'let s = "\\(g(##/)/#/##) + "/*")"\n{unlisted}\n// */',
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
    # 補間の中の宣言も、コメントを空白として、補間の外と同じに読む（文字列の本文の「/*」はコメントにしない）
    local_declaration = '.package(/* a */ path /* b */ : /* c */ "../Local" /* d */)'
    for label, text in (("補間の外", local_declaration), ("補間の中", f'"\\(f({local_declaration}))"'),
                        ("生の文字列の補間の中", f'#"\\#(f({local_declaration}))"#'), ("文字列の本文の記号", f'"/* \\(f({local_declaration})) // "')):
        dependencies_case(f"コメントのあるローカルの Package（{label}）", 0, "OK",
                          {"Packages/A/Package.swift": declare.format(text), "Packages/Local/Package.swift": local_manifest})
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
    # パスは末尾のスラッシュ・.git の有無も含めてそのまま照合する（別の取得元を同じものと見なさない）
    for label, location in (("末尾のスラッシュ", "https://example.com/owner/sample-lib.git/"),
                            (".git がない", "https://example.com/owner/sample-lib")):
        dependencies_case(f"パスの{label}だけが違う取得元", 1, "取得元",
                          {"Package.resolved": json.dumps({"pins": [dict(pin, location=location)], "version": 3})}, [approved])
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
    binary_files = {"Packages/Sub/Frameworks/B.xcframework/Info.plist": "info", "Packages/Sub/Frameworks/B.xcframework/ios/B": "bin"}
    with tempfile.TemporaryDirectory() as directory:
        for name, content in binary_files.items():
            (Path(directory) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(directory) / name).write_text(content, encoding="utf-8")
        binary_digest = module.content_digest(Path(directory) / "Packages/Sub/Frameworks/B.xcframework")
        # 内容のハッシュの形式を固定する（走査の方法を変えても、承認済みの値が変わらないこと）
        count += 1
        if binary_digest != "a501cc49233680d6426eb1efbe09a78d7c98d9fc4de0d9c26bd2748ab7443a29":
            failures.append(f"dependencies: ローカルのバイナリの内容のハッシュの形式が変わった（{binary_digest}）")
        # 成果物の中の読めないディレクトリは、欠けた内容のハッシュにせず、読めないものとして扱う
        locked = Path(directory) / "Packages/Sub/Frameworks/B.xcframework/ios"
        locked.chmod(0)
        try:
            count += 1
            try:
                module.content_digest(Path(directory) / "Packages/Sub/Frameworks/B.xcframework")
                failures.append("dependencies: 成果物の中の読めないディレクトリでハッシュを作った")
            except module.Unreadable:
                pass
        finally:
            locked.chmod(0o755)
    approved_binary = {"identity": "b", "path": "Packages/Sub/Frameworks/B.xcframework", "sha256": binary_digest, "version": "1.0.0",
                       "license": "MIT", "approvedOn": "2026-11-18", "ledger": "dependencies-and-costs.md §3"}
    dependencies_case("記録のないローカルのバイナリ", 1, "ローカルのバイナリ Packages/Sub/Frameworks/B.xcframework",
                      {"Packages/Sub/Package.swift": declare.format(binary)})
    # 入れ子の呼び出しの url: を宣言の取得元と見なさず、ローカルのバイナリとして path と内容のハッシュを照合する
    nested_binary = '.binaryTarget(name: targetName(url: "https://example.com/x"), path: "Frameworks/B.xcframework")'
    dependencies_case("入れ子の url: を持つ記録のないローカルのバイナリ", 1, "ローカルのバイナリ Packages/Sub/Frameworks/B.xcframework",
                      {"Packages/Sub/Package.swift": declare.format(nested_binary)})
    nested_changed = dict(binary_files, **{"Packages/Sub/Frameworks/B.xcframework/ios/B": "replaced",
                                           "Packages/Sub/Package.swift": declare.format(nested_binary)})
    dependencies_case("入れ子の url: を持つローカルのバイナリの内容の照合", 1, "内容が承認したものと違う", nested_changed,
                      [approved_binary])
    dependencies_case("承認済みのローカルのバイナリ", 0, "OK", dict(binary_files, **{"Packages/Sub/Package.swift": declare.format(binary)}),
                      [approved_binary])
    # 通常のファイルの成果物は、内容のハッシュの照合だけで認める（中のリンクの走査でファイルを読めない場所として扱わない）
    file_binary = '.binaryTarget(name: "C", path: "Frameworks/C.zip")'
    approved_file = dict(approved_binary, identity="c", path="Packages/Sub/Frameworks/C.zip",
                         sha256=hashlib.sha256(b"zip").hexdigest())
    dependencies_case("承認済みのファイルのローカルのバイナリ", 0, "OK",
                      {"Packages/Sub/Frameworks/C.zip": "zip", "Packages/Sub/Package.swift": declare.format(file_binary)}, [approved_file])
    dependencies_case("承認の後に内容が変わったファイルのローカルのバイナリ", 1, "内容が承認したものと違う",
                      {"Packages/Sub/Frameworks/C.zip": "changed", "Packages/Sub/Package.swift": declare.format(file_binary)}, [approved_file])
    # 場所が一致しても、承認の後に内容（配下のファイルを含む）が変われば再承認を求める
    changed = dict(binary_files, **{"Packages/Sub/Frameworks/B.xcframework/ios/B": "replaced", "Packages/Sub/Package.swift": declare.format(binary)})
    dependencies_case("承認の後に内容が変わったローカルのバイナリ", 1, "内容が承認したものと違う", changed, [approved_binary])
    added = dict(binary_files, **{"Packages/Sub/Frameworks/B.xcframework/ios/extra": "x", "Packages/Sub/Package.swift": declare.format(binary)})
    dependencies_case("承認の後にファイルが増えたローカルのバイナリ", 1, "内容が承認したものと違う", added, [approved_binary])
    # 承認の path は起点からの相対パスだけ（絶対パス・起点の外は許可リストとして読まない）
    for label, value in (("絶対パス", "/tmp/B.xcframework"), ("起点の外", "../B.xcframework")):
        dependencies_case(f"承認のローカルのバイナリの path が{label}", 2, "起点からの相対パス", {}, [dict(approved_binary, path=value)])
    dependencies_case("起点の外のローカルのバイナリの宣言", 1, "起点の外",
                      {"Package.swift": declare.format('.binaryTarget(name: "B", path: "../B.xcframework")')}, [approved_binary])
    # 承認した場所がリンクで起点の外を指すなら、内容のハッシュを読まずに認めない
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as directory:
        for name, content in binary_files.items():
            target = Path(outside) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (Path(directory) / "Packages" / "Sub" / "Frameworks").mkdir(parents=True)
        (Path(directory) / "Packages" / "Sub" / "Frameworks" / "B.xcframework").symlink_to(Path(outside) / "Packages/Sub/Frameworks/B.xcframework")
        (Path(directory) / "Packages" / "Sub" / "Package.swift").write_text(declare.format(binary), encoding="utf-8")
        allowlist = Path(directory) / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": [approved_binary]}),
                             encoding="utf-8")
        expect("dependencies: 起点の外を指すリンクのローカルのバイナリ", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 1, "実体が起点の外")
    # check.sh の手順 7 の写しは Packages/ だけなので、承認と内容が一致しても Packages/ の外（字面・実体）のバイナリは認めない
    outside_files = {"Frameworks/B.xcframework/Info.plist": "info", "Frameworks/B.xcframework/ios/B": "bin",
                     "Packages/Sub/Package.swift": declare.format(binary.replace('"Frameworks', '"../../Frameworks'))}
    outside_binary = dict(approved_binary, path="Frameworks/B.xcframework")
    dependencies_case("Packages/ の外の承認済みのローカルのバイナリ", 1, "B.xcframework は Packages/ の外にある", outside_files, [outside_binary])
    with tempfile.TemporaryDirectory() as directory:
        for name, content in dict(outside_files, **{"Packages/Sub/Package.swift": declare.format(binary)}).items():
            (Path(directory) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(directory) / name).write_text(content, encoding="utf-8")
        (Path(directory) / "Packages" / "Sub" / "Frameworks").symlink_to(Path(directory) / "Frameworks")
        allowlist = Path(directory) / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": [approved_binary]}),
                             encoding="utf-8")
        expect("dependencies: 実体が Packages/ の外のローカルのバイナリ", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 1, "実体が Packages/ の外")
    # 前後に空白のあるパスは、空白を除いた承認済みのバイナリと取り違えずに拒否する
    spaced = dict(binary_files, **{"Packages/Sub/Package.swift": declare.format(binary.replace('.xcframework"', '.xcframework "'))})
    dependencies_case("前後に空白のあるローカルのバイナリのパス", 1, "前後に空白のあるパス", spaced, [approved_binary])
    # 走査の失敗（読めないディレクトリ）は合格にしない。走査に失敗がなく依存のファイルもなければ通る
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "Sub" / "Locked").mkdir(parents=True)
        allowlist = Path(directory) / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}),
                             encoding="utf-8")
        command = [str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)]
        expect("dependencies: 依存のファイルがない", command, 0, "OK")
        (Path(directory) / "Sub" / "Locked").chmod(0)
        try:
            expect("dependencies: 読めないディレクトリ", command, 2, "読めない")
        finally:
            (Path(directory) / "Sub" / "Locked").chmod(0o755)
    dependencies_case("承認済みのローカルのバイナリがない", 2, "ローカルのバイナリがない",
                      {"Packages/Sub/Package.swift": declare.format(binary)}, [approved_binary])
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
    # 同じ URL のリモートのバイナリの承認が 2 つあると、後の項目が先の checksum を黙って上書きする。読めないものとする
    dependencies_case("同じ URL のリモートのバイナリの承認の重複", 2, "同じ URL で重複",
                      {"Package.swift": declare.format(remote_declaration.replace("{}", "b" * 64))},
                      [remote_binary, dict(remote_binary, version="1.0.1", checksum="b" * 64)])
    # 重複の判定は scheme とホストの大文字小文字だけをそろえた URL で行う（同じ配布物は重複、パス・クエリが違えば別の承認）
    dependencies_case("ホストの大文字小文字だけが違うリモートのバイナリの承認の重複", 2, "同じ URL で重複", {},
                      [remote_binary, dict(remote_binary, url=remote.replace("example.com", "EXAMPLE.com"), checksum="b" * 64)])
    for label, other in (("末尾のスラッシュ", remote + "/"), ("クエリ", remote + "?v=2")):
        dependencies_case(f"{label}が違うリモートのバイナリは別の承認", 0, "OK",
                          {"Package.swift": declare.format(remote_declaration.replace("{}", "b" * 64).replace(remote, other))},
                          [remote_binary, dict(remote_binary, url=other, checksum="b" * 64)])
    dependencies_case("別のディレクトリのローカルのバイナリは承認と一致しない", 1, "ローカルのバイナリ Frameworks/B.xcframework",
                      {"Package.swift": declare.format(binary)}, [approved_binary])
    # 途中のディレクトリと成果物の中のリンクは、実体が Packages/ の中でも、測定の写しで解決できるものだけを認める
    for label, alias, inner, want in (
        ("途中が相対のリンク", "Real", None, 0),
        ("途中が起点を経由する相対のリンク", "../Packages/Real", None, 0),
        ("途中が絶対パスのリンク", "absolute", None, 1),
        ("成果物の中が相対のリンク", "Real", "ios/B", 0),
        ("成果物の中が起点の外を経由するリンク", "Real", "../../../../{root}/Packages/Real/Frameworks/B.xcframework/ios/B", 1),
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            for name, content in binary_files.items():
                target = root / name.replace("Packages/Sub/", "Packages/Real/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            artifact = root / "Packages" / "Real" / "Frameworks" / "B.xcframework"
            if inner is not None:
                # 成果物から起点まで 4 段、起点の親まで 5 段（起点の名前で戻る）
                (artifact / "Link").symlink_to(inner.replace("../../../../{root}", f"../../../../../{root.name}"))
            (root / "Packages" / "Sub").symlink_to(root / "Packages" / "Real" if alias == "absolute" else alias)
            (root / "Packages" / "A").mkdir()
            (root / "Packages" / "A" / "Package.swift").write_text(
                declare.format('.binaryTarget(name: "B", path: "../Sub/Frameworks/B.xcframework")'), encoding="utf-8")
            allowlist = root / "allowlist.json"
            allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": [
                dict(approved_binary, sha256=module.content_digest(artifact))]}), encoding="utf-8")
            expect(f"dependencies: ローカルのバイナリの{label}", [
                str(SCRIPTS / "check-dependencies.py"), "--root", str(root), "--allowlist", str(allowlist)], want,
                "するリンク" if want and inner else "外を指すリンク" if want else "OK")
    # 正規化すると同じパスになる承認が 2 つあると、後の項目が先の sha256 を黙って上書きする
    dependencies_case("同じパスのローカルのバイナリの承認の重複", 2, "同じパスで重複",
                      {}, [approved_binary, dict(approved_binary, path="./" + approved_binary["path"], sha256="b" * 64)])
    dependencies_case("ローカルのバイナリの項目に版がない", 2, "version",
                      {}, [{k: v for k, v in approved_binary.items() if k != "version"}])
    dependencies_case("url と path の両方を持つ項目は読まない", 2, "どちらか一方",
                      {}, [dict(approved_binary, url="https://example.com/owner/b")])
    dependencies_case("起点の中のローカルの Package（.package(path:)）は認める", 0, "OK",
                      {"Packages/Sub/Package.swift": declare.format('.package(path: "../Local")'),
                       "Packages/Local/Package.swift": local_manifest})
    # ローカルの Package の参照先は、起点の中で検査される manifest であること（検査できない参照先を合格にしない）
    dependencies_case("起点の外のローカルの Package", 1, "起点の外",
                      {"Package.swift": declare.format('.package(path: "../Outside")')})
    dependencies_case("manifest のないローカルの Package", 1, "manifest がない",
                      {"Packages/A/Package.swift": declare.format('.package(path: "../Local")')})
    dependencies_case("除外するディレクトリの中のローカルの Package", 1, "検査の対象外",
                      {"Packages/A/Package.swift": declare.format('.package(path: "../.build/Local")'),
                       "Packages/.build/Local/Package.swift": local_manifest})
    # シンボリックリンクを解決した先が除外するディレクトリなら、リンクの名前が通常の場所でも認めない
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Packages" / ".build" / "Local").mkdir(parents=True)
        (root / "Packages" / ".build" / "Local" / "Package.swift").write_text(local_manifest, encoding="utf-8")
        (root / "Packages" / "Local").symlink_to(".build/Local")
        (root / "Packages" / "A").mkdir()
        (root / "Packages" / "A" / "Package.swift").write_text(declare.format('.package(path: "../Local")'), encoding="utf-8")
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
        expect("dependencies: 除外するディレクトリを指すリンクのローカルの Package", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 1, "検査の対象外")
    # 参照先の manifest の未承認のバイナリも検査する（起点の中の manifest はすべて走査する）
    dependencies_case("ローカルの Package の中の未承認のバイナリ", 1, "ローカルのバイナリ Packages/Local/Frameworks/B.xcframework",
                      {"Packages/A/Package.swift": declare.format('.package(path: "../Local")'),
                       "Packages/Local/Package.swift": declare.format(binary)})
    # リンクを解決した先が Packages/ の外なら、リンクの名前が Packages/ の中でも認めない
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Outside").mkdir()
        (root / "Outside" / "Package.swift").write_text(local_manifest, encoding="utf-8")
        (root / "Packages" / "A").mkdir(parents=True)
        (root / "Packages" / "Local").symlink_to("../Outside")
        (root / "Packages" / "A" / "Package.swift").write_text(declare.format('.package(path: "../Local")'), encoding="utf-8")
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
        expect("dependencies: Packages/ の外を指すリンクのローカルの Package", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 1, "Packages/ の外")
    # manifest のリンクは、測定の写し（Packages/ だけ）の中を指す相対パスだけを認める（check.sh の verify_coverage_links と同じ）
    for label, link, want in (
        ("Packages/ の中を指す相対のリンク", "../Shared/Manifest.swift", 0),
        ("起点を経由して Packages/ の中を指す相対のリンク", "../../Packages/Shared/Manifest.swift", 0),
        ("Packages/ の外を指す相対のリンク", "../../Outside/Manifest.swift", 1),
        ("Packages/ の外を経由して中に戻るリンク", "../../Outside/../Packages/Shared/Manifest.swift", 1),
        ("絶対パスのリンク", None, 1),
        ("絶対パスのリンクを経由するリンク", "../Shared/Absolute.swift", 1),
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Outside", "Packages/Shared", "Packages/Local", "Packages/A"):
                (root / name).mkdir(parents=True)
            for name in ("Outside", "Packages/Shared"):
                (root / name / "Manifest.swift").write_text(local_manifest, encoding="utf-8")
            inside = root / "Packages" / "Shared" / "Manifest.swift"
            (root / "Packages" / "Shared" / "Absolute.swift").symlink_to(inside)
            (root / "Packages" / "Local" / "Package.swift").symlink_to(link or inside)
            (root / "Packages" / "A" / "Package.swift").write_text(declare.format('.package(path: "../Local")'), encoding="utf-8")
            allowlist = root / "allowlist.json"
            allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
            expect(f"dependencies: manifest が{label}", [
                str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], want,
                "外を指すリンク" if want else "OK")
    # 参照先のディレクトリ（途中の要素を含む）のリンクも、測定の写しの中を指す相対パスだけを認める
    for label, alias, link, reference, want in (
        ("中を指す相対のリンク", "Alias", "Real", "../Alias", 0),
        ("通常のディレクトリ", "", "", "../Real", 0),
        ("絶対パスのリンク", "Alias", None, "../Alias", 1),
        ("外を経由して中に戻るリンク", "Alias", "../Outside/../Packages/Real", "../Alias", 1),
        ("途中の要素の絶対パスのリンク", "Group", None, "../Group/Sub", 1),
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Outside", "Packages/Real/Sub", "Packages/A"):
                (root / name).mkdir(parents=True)
            for name in ("Real", "Real/Sub"):
                (root / "Packages" / name / "Package.swift").write_text(local_manifest, encoding="utf-8")
            if alias:
                (root / "Packages" / alias).symlink_to(link or (root / "Packages" / "Real"))
            (root / "Packages" / "A" / "Package.swift").write_text(declare.format(f'.package(path: "{reference}")'), encoding="utf-8")
            allowlist = root / "allowlist.json"
            allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
            expect(f"dependencies: ローカルの Package のディレクトリが{label}", [
                str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], want,
                f"ローカルの Package Packages/{reference[3:]} の Packages/{alias} が Packages/ の外を指すリンク" if want else "OK")
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
        # リンクの内容はリンク先の生の文字列で記録する（末尾の「/」の有無で違う内容とする。Path にして違いを消さない）
        count += 1
        plain = module.content_digest(framework)
        (framework / "Current").unlink()
        (framework / "Current").symlink_to("ios/")
        if module.content_digest(framework) == plain:
            failures.append("dependencies: 末尾の「/」だけが違うリンクを同じ内容のハッシュにした")
        (framework / "Current").unlink()
        (framework / "Current").symlink_to("ios")
        # 外を指すリンクの先は実在するファイルにする（先が存在しないことで拒否したのを、外を指すことの拒否と取り違えない。
        # 先が存在しないリンクは下の Missing で別に確かめる）
        (Path(directory) / "outside").write_text("outside", encoding="utf-8")
        for label, target in {"外の相対パス": "../outside", "絶対パス": "/etc/hosts", "外を指すリンクの連鎖": "Chain"}.items():
            (framework / "Bad").unlink() if (framework / "Bad").is_symlink() else None
            if label == "外を指すリンクの連鎖":
                (framework / "Chain").symlink_to("../outside")
            (framework / "Bad").symlink_to(target)
            count += 1
            try:
                module.content_digest(framework)
                failures.append(f"dependencies: 成果物の外を指すリンク（{label}）を受け入れた")
            except module.Unreadable as error:
                # 拒否した項目が Bad で、理由が成果物の外を指すことであること（連鎖では、外を指す Chain だけを拒否して Bad を
                # 通すと合格にしない）
                if not str(error).startswith(f"{framework / 'Bad'}:") or "成果物の外を指す" not in str(error):
                    failures.append(f"dependencies: 成果物の外を指すリンク（{label}）で Bad 以外を拒否した: {error}")
        # 循環するリンクは、例外（RuntimeError）で落ちずに、リンクのパスを示して読めないものとして扱う
        (framework / "Bad").unlink()
        (framework / "Chain").unlink()
        (framework / "Loop1").symlink_to("Loop2")
        (framework / "Loop2").symlink_to("Loop1")
        count += 1
        try:
            module.content_digest(framework)
            failures.append("dependencies: 成果物の中の循環するリンクでハッシュを作った")
        except module.Unreadable as error:
            if str(framework / "Loop") not in str(error):
                failures.append(f"dependencies: 循環するリンクの報告にリンクのパスがない: {error}")
        except RuntimeError as error:
            failures.append(f"dependencies: 循環するリンクで例外が送出された: {error!r}")
        # 先が存在しないリンクは、成果物の中のパスを指していても読めないものとする（リンクのパスを示す）
        (framework / "Loop1").unlink()
        (framework / "Loop2").unlink()
        (framework / "Missing").symlink_to("ios/none")
        count += 1
        try:
            module.content_digest(framework)
            failures.append("dependencies: 先が存在しないリンクでハッシュを作った")
        except module.Unreadable as error:
            if not str(error).startswith(f"{framework / 'Missing'}:"):
                failures.append(f"dependencies: 先が存在しないリンクの報告にリンクのパスがない: {error}")
    # ローカルの Package の参照先が循環するリンクなら、終了コード 2（読めない）にする
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Packages" / "A").mkdir(parents=True)
        (root / "Packages" / "Loop").symlink_to("Loop")
        (root / "Packages" / "A" / "Package.swift").write_text(declare.format('.package(path: "../Loop")'), encoding="utf-8")
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps({"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": []}), encoding="utf-8")
        expect("dependencies: 循環するリンクのローカルの Package", [
            str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 2, "Loop")
    # URL の正規化はホストだけを小文字にし、authority のユーザー情報の大文字小文字は区別する
    for left, right, same in (("https://User@EXAMPLE.com/owner/a", "https://user@example.com/owner/a", False),
                              ("https://User@EXAMPLE.com/owner/a", "HTTPS://User@example.COM/owner/a", True),
                              ("https://a@b@EXAMPLE.com/owner/a", "https://a@b@example.com/owner/a", True),
                              # authority は「?」「#」の前でも終わる。クエリ・フラグメントの大文字小文字は区別する
                              ("https://EXAMPLE.com?Q=A", "https://example.com?Q=A", True),
                              ("https://example.com?Q=A", "https://example.com?q=a", False),
                              ("https://EXAMPLE.com#Frag", "https://example.com#Frag", True),
                              ("https://example.com#Frag", "https://example.com#frag", False)):
        count += 1
        if (module.normalize(left) == module.normalize(right)) != same:
            failures.append(f"dependencies: URL の正規化で {left} と {right} を{'別' if same else '同じ'}ものと見なした")
    dependencies_case("区間の上限は含まない", 1, "合わない",
                      {"Package.resolved": resolved, "Package.swift": declare.format(f'.package(url: "{url}", "1.0.0"..<"1.2.3")')},
                      [approved])
    # 閉じていない文字列・補間は、後ろの宣言を本文として隠さないよう読めないものとして扱う
    dependencies_case("閉じていない文字列", 2, "文字列リテラルが閉じていない",
                      {"Package.swift": declare.format(unlisted) + 'let s = "abc\n'})
    dependencies_case("閉じていない補間", 2, "補間が閉じていない", {"Package.swift": declare.format(unlisted) + 'let s = "\\(x\n'})
    # 深い入れ子の補間は RecursionError で止めず、ファイルの名前つきで読めないものとする（終了コード 2）
    deep = '"\\(' * 2000 + "x" + ')"' * 2000
    dependencies_case("深い入れ子の補間", 2, "Package.swift: 入れ子が深すぎて", {"Package.swift": declare.format(unlisted) + f"let s = {deep}\n"})
    # 正規表現のリテラルの中の「"」「//」を文字列・コメントと取り違えず、後ろの宣言を検査する
    dependencies_case("正規表現のリテラルの後ろの宣言", 1, "unlisted.git",
                      {"Package.swift": declare.format(f'let r = #/"//#\n{unlisted}\nlet s = "x"')})
    dependencies_case("エスケープした区切りの後の引用符で宣言を隠さない", 1, "unlisted.git",
                      {"Package.swift": declare.format(f'let r = #/\\/#"/#\n{unlisted}\nlet s = "x"')})
    dependencies_case("エスケープしたバックスラッシュの後の区切りで終わる", 1, "unlisted.git",
                      {"Package.swift": declare.format(f'let r = #/\\\\/#\n{unlisted}')})
    dependencies_case("正規表現のリテラルの中の宣言は数えない", 0, "OK",
                      {"Package.swift": declare.format(f'let r = #/{unlisted}/#')})
    dependencies_case("閉じていない正規表現のリテラル", 2, "正規表現のリテラルが閉じていない",
                      {"Package.swift": declare.format("let r = #/abc\n")})
    dependencies_case("区切りのない「/」", 2, "「/」を判定できない", {"Package.swift": declare.format("let x = 4 / 2")})
    # JSON の深い入れ子（Python 3.9 の json は RecursionError）は読めないものとして終了コード 2
    dependencies_case("深い入れ子の Package.resolved", 2, "読めない", {"Package.resolved": "[" * 100000 + "]" * 100000})
    # 重複した鍵（後の pins で前の pins を隠す）は読めないものとする
    dependencies_case("重複した鍵の Package.resolved", 2, "重複した鍵",
                      {"Package.resolved": '{"pins": [{"identity": "x", "location": "https://example.com/x", "state": {}}], '
                                           '"pins": [], "version": 3}'})
    # 形式の版と pins の位置。知らない版・位置が合わないものは読まない
    empty: List[object] = []
    for label, value, want in (
        ("版 1", {"version": 1, "object": {"pins": empty}}, 0), ("版 2", {"version": 2, "pins": empty}, 0),
        ("版 3", {"version": 3, "originHash": "x", "pins": empty}, 0), ("版がない", {"pins": empty}, 2),
        ("版 4", {"version": 4, "pins": empty}, 2), ("版 0", {"version": 0, "pins": empty}, 2),
        ("版が文字列", {"version": "3", "pins": empty}, 2), ("版が真偽値", {"version": True, "object": {"pins": empty}}, 2),
        ("版が小数", {"version": 3.0, "pins": empty}, 2), ("版 1 で最上位", {"version": 1, "pins": empty}, 2),
        ("版 3 で object の中", {"version": 3, "object": {"pins": empty}}, 2),
        ("版 1 で両方", {"version": 1, "pins": empty, "object": {"pins": empty}}, 2),
        ("版 3 で両方", {"version": 3, "pins": empty, "object": {"pins": empty}}, 2),
        ("版 1 で object が配列", {"version": 1, "object": empty}, 2), ("最上位が配列", empty, 2),
        # 版 3 の originHash は省略・null・文字列を認め、ほかの型は読まない
        ("版 3 で originHash がない", {"version": 3, "pins": empty}, 0),
        ("版 3 で originHash が null", {"version": 3, "originHash": None, "pins": empty}, 0),
        ("版 3 で originHash が数値", {"version": 3, "originHash": 1, "pins": empty}, 2),
        ("版 3 で originHash が配列", {"version": 3, "originHash": ["x"], "pins": empty}, 2),
    ):
        dependencies_case(f"Package.resolved の形式（{label}）", want, "依存を読めない" if want else "OK", {"Package.resolved": json.dumps(value)})
    # 版 2・3 の pin は kind を必ず持つ（ない・知らない値・文字列でないものは読まない）。版 1 は kind を持たない
    for version in (2, 3):
        for label, changes, want in (("kind がない", None, 2), ("知らない kind", "fileSystemX", 2), ("kind が配列", ["registry"], 2),
                                     ("kind が registry", "registry", 1), ("kind が localSourceControl", "localSourceControl", 1)):
            modern = {k: v for k, v in pin.items() if k != "kind"} if changes is None else dict(pin, kind=changes)
            dependencies_case(f"Package.resolved の版 {version} の pin（{label}）", want, "依存を読めない" if want == 2 else "sample-lib 1.2.3",
                              {"Package.resolved": json.dumps({"pins": [modern], "version": version})})
    # 同じ Package.resolved の中の依存の名前の重複（SwiftPM と同じ規則。swift package resolve で確かめた組）
    def pins(version: int, *rows: Tuple[str, str]) -> str:
        if version == 1:
            return json.dumps({"object": {"pins": [{"package": name, "repositoryURL": url, "state": {"version": "1.0.0"}}
                                                   for name, url in rows]}, "version": 1})
        return json.dumps({"pins": [{"identity": name, "kind": "remoteSourceControl", "location": url, "state": {"version": "1.0.0"}}
                                    for name, url in rows], "version": version})

    for label, version, first, second, want in (
        ("版 2 の同じ identity", 2, ("foo", "https://x.invalid/foo"), ("foo", "https://y.invalid/foo"), 2),
        ("版 2 の大文字小文字だけが違う identity", 2, ("foo", "https://x.invalid/foo"), ("Foo", "https://y.invalid/foo"), 2),
        ("版 3 の同じ identity", 3, ("foo", "https://x.invalid/foo"), ("foo", "https://y.invalid/foo"), 2),
        ("版 2 の違う identity と同じ名前の URL", 2, ("foo", "https://x.invalid/foo"), ("bar", "https://y.invalid/foo"), 1),
        ("版 1 の違う package と同じ名前の URL", 1, ("foo", "https://x.invalid/aaa.git"), ("bar", "https://y.invalid/AAA"), 2),
        ("版 1 の同じ package と違う名前の URL", 1, ("foo", "https://x.invalid/aaa"), ("foo", "https://y.invalid/bbb"), 1),
        ("版 1 の「/」のない scp の形の URL", 1, ("foo", "git@x.invalid:aaa.git"), ("bar", "https://y.invalid/aaa/"), 1),
    ):
        dependencies_case(f"依存の名前の重複（{label}）", want, "重複している" if want == 2 else "許可リストにない",
                          {"Package.resolved": pins(version, first, second)})
    dependencies_case("別の Package.resolved の同じ名前の依存", 0, "OK",
                      {"Package.resolved": resolved, "Packages/Sub/Package.resolved": resolved}, [approved])
    # 許可リストの重複した鍵（後の approvedPackages で前の承認を隠す）も読めないものとする
    with tempfile.TemporaryDirectory() as directory:
        allowlist = Path(directory) / "allowlist.json"
        allowlist.write_text('{"formatVersion": 1, "allowedLicenses": ["MIT"], "approvedPackages": [], "approvedPackages": []}',
                             encoding="utf-8")
        expect("dependencies: 重複した鍵の許可リスト",
               [str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 2, "重複した鍵")
    # formatVersion の true は Python では 1 と等しいが、形式の版として認めない（整数の 1 だけを認める）
    with tempfile.TemporaryDirectory() as directory:
        allowlist = Path(directory) / "allowlist.json"
        allowlist.write_text('{"formatVersion": true, "allowedLicenses": ["MIT"], "approvedPackages": []}', encoding="utf-8")
        expect("dependencies: formatVersion が true の許可リスト",
               [str(SCRIPTS / "check-dependencies.py"), "--root", directory, "--allowlist", str(allowlist)], 2, "formatVersion")
    # checksum を持つ承認はリモートのバイナリの配布物だけを承認する。同じ識別子・版・取得元の Package の確定版と
    # .package(url:) の宣言は承認しない
    binary_pin = {"identity": "b-binary", "kind": "remoteSourceControl", "location": remote,
                  "state": {"revision": "0" * 40, "version": "1.0.0"}}
    dependencies_case("リモートのバイナリの承認で Package の確定版を承認しない", 1, "b-binary 1.0.0 は許可リストにない",
                      {"Package.resolved": json.dumps({"pins": [binary_pin], "version": 3})}, [remote_binary])
    dependencies_case("リモートのバイナリの承認で .package(url:) を承認しない", 1, "確定版 1.0.0 は許可リストにない",
                      {"Package.resolved": json.dumps({"pins": [binary_pin], "version": 3}),
                       "Package.swift": declare.format('.package(url: "' + remote + '", exact: "1.0.0")')}, [remote_binary])
    # 版別の manifest（Package@swift-<版>.swift）も同じ検査にかける
    # 除外の階層: 下位の build は検査し、直下の build と全階層の .build は除外する
    dependencies_case("下位の build の中の許可リスト外の依存", 1, "Sub/build/Package.swift",
                      {"Sub/build/Package.swift": declare.format(unlisted)})
    dependencies_case("直下の build は除外", 0, "OK", {"build/Package.swift": declare.format(unlisted)})
    dependencies_case("下位の .build は除外", 0, "OK", {"Sub/.build/Package.swift": declare.format(unlisted)})
    dependencies_case("版別の manifest の許可リスト外の依存", 1, "Package@swift-6.0.swift",
                      {"Package@swift-6.0.swift": declare.format(unlisted)})
    # 閉じていない引数を末尾まで読んだことにしない。閉じた宣言はこれまでどおり読む
    dependencies_case("引数の括弧が閉じていない", 2, "括弧が閉じていない",
                      {"Packages/A/Package.swift": 'let p = [.package(path: "../Local"\n', "Packages/Local/Package.swift": local_manifest})
    # 調べられない（権限のない）リンクの先は、終了コード 2 でパスとともに報告する
    with tempfile.TemporaryDirectory() as directory:
        root, hidden = Path(directory) / "root", Path(directory) / "hidden"
        root.mkdir()
        hidden.mkdir()
        (hidden / "Package.swift").write_text(local_manifest, encoding="utf-8")
        (root / "Package.swift").symlink_to(hidden / "Package.swift")
        hidden.chmod(0)
        try:
            expect("dependencies: 調べられないリンクの先", [str(SCRIPTS / "check-dependencies.py"), "--root", str(root)], 2,
                   "NG: 依存を読めない: " + str(root.resolve() / "Package.swift"))
        finally:
            hidden.chmod(0o700)
    expect("dependencies: 存在しない起点", [str(SCRIPTS / "check-dependencies.py"), "--root", str(ROOT / "no-such-directory")],
           2, "起点のディレクトリがない")


def applies_here(line: str, name: str) -> bool:
    """台帳の行が、この Mac で setup-tools.sh が使う行か（名前が一致し、アーキテクチャがこの Mac のものか any）。"""
    return line.split()[:1] == [name] and line.split()[2:3] in ([platform.machine()], ["any"])


EMPTY_CONFIG_HOME = tempfile.TemporaryDirectory(prefix="minutes-config.")


def clean_temporary_base() -> "tempfile.TemporaryDirectory[str]":
    """試験の子のプロセスに渡す一時の置き場所（TMPDIR）。check.sh --require-clean・--staged は TMPDIR の下に作業の場所を作り、
    その場所か祖先に SwiftFormat の設定（.swiftformat・.swift-version）がある・Git の作業ツリーの中にあると拒否する。外から引き継いだ
    TMPDIR がそうした場所でも正の試験を失敗させないよう、どちらでもない場所を選ぶ（試験の作業ツリーは別の一時ディレクトリに作るので、
    その外になる）。候補のどれも満たさなければ、自己試験を始めずに止める（拒否されて失敗した理由を取り違えない）。"""
    def configured(place: Path) -> bool:
        """SwiftFormat の設定があるか、有無を調べられないか（worktree-location.py の check と同じく lstat で調べる。壊れたリンクも
        設定として数える）。"""
        for name in (".swiftformat", ".swift-version"):
            try:
                os.lstat(place / name)
            except FileNotFoundError:
                continue
            except OSError:
                return True
            return True
        return False

    for parent in (tempfile.gettempdir(), "/private/tmp"):
        real = Path(os.path.realpath(parent))
        if any(configured(place) for place in (real, *real.parents)):
            continue
        inside = subprocess.run(["git", "-C", str(real), "rev-parse", "--show-toplevel"], capture_output=True,
                                env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")})
        if inside.returncode == 0:
            continue
        return tempfile.TemporaryDirectory(prefix="minutes-tmp.", dir=real)
    sys.exit("NG: SwiftFormat の設定を継がず、Git の作業ツリーの外にある一時の置き場所が見つからない（TMPDIR を変えて実行し直す）")


CLEAN_TEMPORARY_BASE = clean_temporary_base()


def isolated_git_env() -> Dict[str, str]:
    """開発者のグローバル・システムの設定と属性、リポジトリを指す変数（GIT_DIR など）の影響を受けない git の環境。
    リポジトリの中の .gitattributes はそのまま効く。"""
    local_vars = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True).stdout.split()
    # 作者・コミッターの名前とメールの変数は、試験のリポジトリで設定した値（-c user.name など）より優先されるので外す
    identity = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}
    isolated = {key: value for key, value in os.environ.items()
                if key not in local_vars and key != "GIT_CONFIG" and key not in identity}
    # 利用者の属性・無視のファイル（$XDG_CONFIG_HOME/git/attributes、なければ ~/.config/git/attributes など）は、グローバルの設定を
    # 外しても読まれるので、置き場所を空の一時ディレクトリにする（自己試験の終了時に消える）
    isolated.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1", XDG_CONFIG_HOME=EMPTY_CONFIG_HOME.name)
    # 一時の置き場所は、SwiftFormat の設定を継がない、試験の作業ツリーの外の場所にする（clean_temporary_base）。置き場所を拒否する
    # 試験（require_clean_location_case など）は、呼び出しの環境で TMPDIR を明示して上書きする
    isolated["TMPDIR"] = CLEAN_TEMPORARY_BASE.name
    return isolated


def isolated_attributes_case() -> None:
    """isolated_git_env の環境で、利用者の属性を読まず、リポジトリの中の属性は読むこと。"""
    global count
    for key, place in (("XDG_CONFIG_HOME", "git"), ("HOME", ".config/git")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "user" / place).mkdir(parents=True)
            (Path(directory) / "user" / place / "attributes").write_text("*.txt minutes-user\n", encoding="utf-8")
            repository = Path(directory) / "repository"
            saved = {name: os.environ.get(name) for name in ("XDG_CONFIG_HOME", "HOME")}
            os.environ.pop("XDG_CONFIG_HOME", None)
            os.environ[key] = str(Path(directory) / "user")
            try:
                env = isolated_git_env()
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            # 対照は、隔離した環境から属性の置き場所だけを戻したもの（引き継いだ core.attributesFile の設定などで対照が変わらない）
            plain = dict(env)
            plain[key] = str(Path(directory) / "user")
            if key == "HOME":
                plain.pop("XDG_CONFIG_HOME")  # 残すと、HOME の下ではなくその場所の属性を読む
            subprocess.run(["git", "init", "-q", "--template=", str(repository)], env=env, check=True, capture_output=True)
            (repository / ".gitattributes").write_text("*.txt minutes-local\n", encoding="utf-8")
            command = ["git", "-C", str(repository), "check-attr", "minutes-user", "minutes-local", "--", "a.txt"]
            results = [subprocess.run(command, env=item, check=True, capture_output=True, text=True).stdout.splitlines()
                       for item in (plain, env)]
            # 対照: 隔離しなければ利用者の属性が効く
            if (results[0][0] != "a.txt: minutes-user: set" or results[1] != ["a.txt: minutes-user: unspecified", "a.txt: minutes-local: set"]
                    or env.get("GIT_ATTR_NOSYSTEM") != "1"):
                failures.append(f"versions: 隔離した git の環境が利用者の属性を読む（{key}・{results}）")


def setup_hooks_case() -> None:
    """setup.sh --hooks が、環境変数 GIT_CONFIG が別のファイルを指していても、リポジトリの設定に書いて読み直すこと。

    開発ツールの導入（setup-tools.sh）は何もしない代わりのスクリプトに置き換えた複製のリポジトリで実行する。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        env = isolated_git_env()
        subprocess.run(["git", "init", "-q", "--template=", str(repository)], env=env, check=True, capture_output=True)
        (repository / "scripts" / "setup.sh").write_text((SCRIPTS / "setup.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        for name in ("setup.sh", "setup-tools.sh"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".githooks").mkdir()
        for name in ("pre-commit", "pre-push"):
            (repository / ".githooks" / name).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            (repository / ".githooks" / name).chmod(0o755)
        other = Path(directory) / "other.cfg"
        other.write_text("", encoding="utf-8")
        result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], env=dict(env, GIT_CONFIG=str(other)),
                                capture_output=True, text=True)
        local = subprocess.run(["git", "-C", str(repository), "config", "--local", "--get", "core.hooksPath"], env=env,
                               capture_output=True, text=True).stdout.strip()
        if result.returncode != 0 or local != ".githooks" or "hooksPath" in other.read_text(encoding="utf-8"):
            failures.append(f"setup: GIT_CONFIG を設定した --hooks がリポジトリの設定に書かない（終了コード {result.returncode}・"
                            f"リポジトリの値「{local}」・GIT_CONFIG のファイル「{other.read_text(encoding='utf-8').strip()}」）")


def setup_missing_hook_case() -> None:
    """setup.sh --hooks が、フックのファイルがない・実行できない場合に、設定を書かずに失敗すること。"""
    global count
    for label, prepare in (("ない", lambda hook: hook.unlink()), ("実行できない", lambda hook: hook.chmod(0o644))):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            env = isolated_git_env()
            subprocess.run(["git", "init", "-q", "--template=", str(repository)], env=env, check=True, capture_output=True)
            (repository / "scripts" / "setup.sh").write_text((SCRIPTS / "setup.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            for name in ("setup.sh", "setup-tools.sh"):
                (repository / "scripts" / name).chmod(0o755)
            (repository / ".githooks").mkdir()
            for name in ("pre-commit", "pre-push"):
                (repository / ".githooks" / name).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                (repository / ".githooks" / name).chmod(0o755)
            prepare(repository / ".githooks" / "pre-push")
            result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], env=env, capture_output=True, text=True)
            configured = subprocess.run(["git", "-C", str(repository), "config", "--local", "--get", "core.hooksPath"], env=env,
                                        capture_output=True, text=True).stdout.strip()
            if result.returncode != 1 or configured or "pre-push" not in result.stderr:
                failures.append(f"setup: フックが{label}のに --hooks が失敗しない、または設定を書いた（終了コード {result.returncode}・"
                                f"設定「{configured}」）")


def setup_repository(directory: str, env: Dict[str, str]) -> Path:
    """setup.sh と、何もしない setup-tools.sh・フックを置いた一時のリポジトリを作り、最初のコミットをする。"""
    repository = Path(directory) / "repository"
    (repository / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
    (repository / "scripts" / "setup.sh").write_text((SCRIPTS / "setup.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (repository / ".githooks").mkdir()
    for name in ("pre-commit", "pre-push"):
        (repository / ".githooks" / name).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    for path in (repository / "scripts" / "setup.sh", repository / "scripts" / "setup-tools.sh",
                 repository / ".githooks" / "pre-commit", repository / ".githooks" / "pre-push"):
        path.chmod(0o755)
    for arguments in (["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                       capture_output=True)
    return repository


def setup_repeated_hooks_path_case() -> None:
    """setup.sh --hooks が、共有の設定に core.hooksPath の行が複数あっても（1 つの値の書き込みは失敗する）すべてを .githooks の
    1 件に置き換え、既に .githooks の 1 件だけなら設定のファイルを書き換えないこと。"""
    global count
    env = isolated_git_env()
    for label, values in (("複数の行", ["elsewhere", "other"]), ("既に .githooks の 1 件", [".githooks"])):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            repository = setup_repository(directory, env)
            for value in values:
                subprocess.run(["git", "-C", str(repository), "config", "--local", "--add", "core.hooksPath", value], env=env,
                               check=True, capture_output=True)
            config = repository / ".git" / "config"
            before = config.stat().st_mtime_ns, config.read_bytes()
            result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], env=env, capture_output=True, text=True)
            stored = subprocess.run(["git", "-C", str(repository), "config", "--local", "--get-all", "core.hooksPath"], env=env,
                                    capture_output=True, text=True).stdout.splitlines()
            unchanged = (config.stat().st_mtime_ns, config.read_bytes()) == before
            if result.returncode != 0 or stored != [".githooks"] or (len(values) == 1 and not unchanged):
                failures.append(f"setup: core.hooksPath が{label}の --hooks の結果が違う（終了コード {result.returncode}・"
                                f"設定 {stored}・書き換え {not unchanged}）\n{result.stderr.strip()}")


def setup_guard_cases() -> None:
    """setup.sh --hooks が、引き継いだ GIT_DIR などが別のリポジトリを指していても外して ROOT のリポジトリにだけ書くこと。
    より優先される設定が別の値を指すときに、共有の設定を変えずに失敗すること。リンクした作業ツリーからの実行は通ること。"""
    global count
    env = isolated_git_env()

    def configured(repository: Path) -> str:
        return subprocess.run(["git", "-C", str(repository), "config", "--local", "--get", "core.hooksPath"], env=env,
                              capture_output=True, text=True).stdout.strip()

    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = setup_repository(directory, env)
        other = Path(directory) / "other"
        subprocess.run(["git", "init", "-q", "--template=", str(other)], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], capture_output=True, text=True,
                                env=dict(env, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other)))
        # 引き継いだ GIT_DIR・GIT_WORK_TREE は外し、ROOT のリポジトリにだけ書く（別のリポジトリの設定を変えない）
        if result.returncode != 0 or configured(repository) != ".githooks" or configured(other):
            failures.append(f"setup: 別のリポジトリを指す GIT_DIR を外さずに --hooks を実行した（終了コード {result.returncode}・"
                            f"設定「{configured(repository)}」/「{configured(other)}」）\n{result.stderr.strip()}")
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = setup_repository(directory, env)
        result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], capture_output=True, text=True,
                                env=dict(env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0="elsewhere"))
        if result.returncode != 1 or configured(repository) or "より優先される設定" not in result.stderr:
            failures.append(f"setup: より優先される core.hooksPath があるのに共有の設定を書いた（終了コード {result.returncode}・"
                            f"設定「{configured(repository)}」）\n{result.stderr.strip()}")
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = setup_repository(directory, env)
        linked = Path(directory) / "linked"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(linked)], env=env, check=True,
                       capture_output=True)
        result = subprocess.run([str(linked / "scripts" / "setup.sh"), "--hooks"], cwd=linked, capture_output=True, text=True,
                                env=env)
        if result.returncode != 0 or configured(repository) != ".githooks":
            failures.append(f"setup: リンクした作業ツリーから --hooks が通らない（終了コード {result.returncode}）\n"
                            f"{result.stderr.strip()}")


def setup_newline_override_case() -> None:
    """setup.sh --hooks が、末尾に改行のある core.hooksPath の上書きを .githooks と取り違えず、共有の設定を書く前に止まること。
    ちょうど .githooks の上書きは通ること。"""
    global count
    for label, value, want in (("末尾の改行", ".githooks\n", 1), ("空", "", 1), ("ちょうどの値", ".githooks", 0)):
        for source in ("環境変数", "取り込む設定"):
            count += 1
            with tempfile.TemporaryDirectory() as directory:
                env = isolated_git_env()
                repository = setup_repository(directory, env)
                if source == "環境変数":
                    env_run = dict(env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=value)
                else:
                    env_run = env
                    subprocess.run(["git", "config", "--file", str(repository / ".git" / "extra.config"), "core.hooksPath", value],
                                   env=env, check=True)
                    subprocess.run(["git", "-C", str(repository), "config", "--local", "include.path", "extra.config"], env=env,
                                   check=True)
                result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], env=env_run, capture_output=True, text=True)
                local = subprocess.run(["git", "-C", str(repository), "config", "--local", "--no-includes", "--get", "core.hooksPath"],
                                       env=env, capture_output=True, text=True).stdout
                if result.returncode != want or local != ("" if want else ".githooks\n"):
                    failures.append(f"setup: {source}の core.hooksPath（{label}）の扱いが違う（終了コード {result.returncode}・"
                                    f"共有の値 {local!r}）\n{result.stderr.strip()}")


def setup_config_failure_case() -> None:
    """setup.sh --hooks が、設定の事前の読み取りの失敗（鍵がない終了コード 1 以外）を「鍵がない」と扱わず、書く前に止まること。"""
    global count
    real = shutil.which("git")
    assert real is not None
    for pattern in ("--show-scope", "--local --includes", "--local --no-includes"):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository = setup_repository(directory, env)
            shims = Path(directory) / "shims"
            shims.mkdir()
            (shims / "git").write_text(f'#!/bin/sh\ncase " $* " in *" {pattern} "*) exit 3 ;; esac\nexec "$MINUTES_TEST_GIT" "$@"\n',
                                       encoding="utf-8")
            (shims / "git").chmod(0o755)
            result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], capture_output=True, text=True,
                                    env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real))
            configured = subprocess.run(["git", "-C", str(repository), "config", "--local", "--get", "core.hooksPath"], env=env,
                                        capture_output=True, text=True).stdout.strip()
            if result.returncode != 1 or configured or "読み取れない" not in result.stderr:
                failures.append(f"setup: 設定の読み取り（{pattern}）の失敗で止まらない（終了コード {result.returncode}・"
                                f"設定「{configured}」）\n{result.stderr.strip()}")


def require_clean_child_case(pointed: bool = False) -> None:
    """check.sh --require-clean が、入れ子の check.sh に開始時のコミットの --expect-head を最後に渡し、引き継いだ
    GIT_INDEX_FILE（最初の照合だけに使う）を外して専用の作業ツリーを作ること（呼び出し側のインデックスを変えない）。

    pointed なら、GIT_DIR・GIT_WORK_TREE も元のリポジトリを指して引き継ぎ、専用の作業ツリーの検査へは渡さないことを確かめる。
    コミットした check.sh は引数と環境を記録する代わり。本物は作業ツリーにだけ置く（assume-unchanged で変更なしに見せる）。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools").mkdir()
        out = Path(directory) / "recorded"
        check = repository / "scripts" / "check.sh"
        check.write_text('#!/bin/sh\ncd "$(dirname "$0")/.." || exit 1\n'
                         '{ echo "$*"; echo "${GIT_INDEX_FILE:-none}${GIT_DIR:-}${GIT_WORK_TREE:-}"; git status --porcelain; }  > "$MINUTES_TEST_OUT"\n',
                         encoding="utf-8")
        check.chmod(0o755)
        copy_location_script(repository)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")

        def git(*arguments: str) -> str:
            return subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                   "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env,
                                  check=True, capture_output=True, text=True).stdout.strip()

        git("init", "-q", "--template=", "-b", "main")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        head = git("rev-parse", "HEAD")
        git("update-index", "--assume-unchanged", "scripts/check.sh")
        check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        index = repository / ".git" / "caller-index"
        shutil.copy2(repository / ".git" / "index", index)
        result = subprocess.run([str(check), "--steps", "10", "--require-clean", "--expect-head", "main"], cwd=repository,
                                capture_output=True, text=True, timeout=60,
                                env=dict(env, GIT_INDEX_FILE=".git/caller-index", MINUTES_TEST_OUT=str(out), **(
                                    {"GIT_DIR": str(repository / ".git"), "GIT_WORK_TREE": str(repository)} if pointed else {})))
        recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
        want = f"--steps 10 --require-clean --expect-head main --expect-head {head}\nnone\n"
        listed = subprocess.run(["git", "-C", str(repository), "ls-files", "-v"], env=dict(env, GIT_INDEX_FILE=str(index)),
                                capture_output=True, text=True).stdout
        left = (repository / ".git" / "worktrees").exists() and list((repository / ".git" / "worktrees").iterdir())
        if result.returncode != 0 or recorded != want or "h scripts/check.sh" not in listed or left:
            failures.append(f"check: --require-clean の入れ子の実行の引数・インデックスが違う（GIT_DIR {pointed}・残り {left}・終了コード {result.returncode}・"
                            f"記録 {recorded!r}・呼び出し側のインデックス {listed!r}）\n{result.stderr.strip()}")


def ordinary_tools_snapshot_case() -> None:
    """通常の check.sh の手順 1〜3 が、照合した開発ツールの写しだけを使うこと。

    照合の代わりが照合の直後に元の .tools の中身を差し替える。手順 1〜3 と、手順 2・3 が呼ぶスクリプト（MINUTES_TOOLS_DIR）の
    どれも差し替え前の写しを使い、写しが終了後に残らないこと、引き継いだ MINUTES_TOOLS_DIR を使わないことを確かめる。
    """
    global count
    for steps in ("1-3", "0-3"):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            out = Path(directory) / "recorded"
            for name in ("swiftformat", "swiftlint", "gitleaks"):
                tool = repository / ".tools" / "bin" / name
                tool.write_text(f'#!/bin/sh\nprintf "%s\\0" "{name} verified" >> "$MINUTES_TEST_OUT"\n', encoding="utf-8")
                tool.chmod(0o755)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "setup-tools.sh").write_text(
                # 記録は NUL で区切る（改行を含むパスを 1 つの記録として読む）
                '#!/bin/bash\nprintf "%s\\0" "verify $*" >> "$MINUTES_TEST_OUT"\nfor name in swiftformat swiftlint gitleaks; do\n'
                '  echo "printf \'swapped\\\\0\' >> \\"\\$MINUTES_TEST_OUT\\"" > "$(dirname "$0")/../.tools/bin/$name"\ndone\n', encoding="utf-8")
            (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (repository / "scripts" / "test-lint-rules.py").write_text('#!/bin/sh\n"$MINUTES_TOOLS_DIR/bin/swiftlint"\n',
                                                                       encoding="utf-8")
            (repository / "scripts" / "gitleaks-history.sh").write_text('#!/bin/sh\n"$MINUTES_TOOLS_DIR/bin/gitleaks"\n',
                                                                        encoding="utf-8")
            # 手順 3 の最後の録音データの混入の検査は開発ツールを使わない。代わりを置く（本物は test_tracked で試す）
            (repository / "scripts" / "check-tracked-files.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (repository / "scripts").iterdir():
                path.chmod(0o755)
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env,
                               check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", steps], capture_output=True, text=True,
                                    timeout=60, env=dict(env, MINUTES_TEST_OUT=str(out), MINUTES_TOOLS_DIR="/nonexistent"))
            recorded = [os.fsdecode(item) for item in out.read_bytes().split(b"\0")[:-1]] if out.is_file() else []
            verify = re.fullmatch(r"verify --verify --dest (/.*/\.tools)", recorded[0], re.DOTALL) if recorded else None
            want = ["swiftformat verified", "swiftlint verified", "swiftlint verified", "gitleaks verified"]
            if result.returncode != 0 or recorded[1:] != want or verify is None or os.path.exists(verify.group(1)) \
                    or Path(verify.group(1)) == repository.resolve() / ".tools" or (repository / ".tools" / ".install.lock").exists():
                failures.append(f"check: 通常の実行（--steps {steps}）が照合した写しの開発ツールだけを使わない（終了コード "
                                f"{result.returncode}・記録 {recorded}）\n{(result.stdout + result.stderr).strip()}")


def tools_override_case() -> None:
    """gitleaks-history.sh・test-lint-rules.py が、MINUTES_TOOLS_DIR の開発ツールを使うこと（check.sh が照合した写しを渡す手段）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        tools = Path(directory) / "tools"
        (tools / "bin").mkdir(parents=True)
        env = isolated_git_env()
        for name in ("gitleaks", "swiftlint"):
            (tools / "bin" / name).write_text(f'#!/bin/sh\necho "override {name}" >&2\nexit 9\n', encoding="utf-8")
            (tools / "bin" / name).chmod(0o755)
        repository = Path(directory) / "repository"
        subprocess.run(["git", "init", "-q", "--template=", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base"], env=env, check=True,
                       capture_output=True)
        for name, command in (("gitleaks", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)]),
                              ("swiftlint", [str(SCRIPTS / "test-lint-rules.py")])):
            result = subprocess.run(command, capture_output=True, text=True, env=dict(env, MINUTES_TOOLS_DIR=str(tools)))
            if result.returncode == 0 or f"override {name}" not in result.stderr:
                failures.append(f"versions: {command[0]} が MINUTES_TOOLS_DIR の {name} を使わない（終了コード {result.returncode}）")


def worktree_location_case() -> None:
    """worktree-location.py が、git を起動できないとき理由を示して終了コード 2 になること（登録なしの 1 と取り違えない）。"""
    for arguments in (["registered", str(ROOT), str(ROOT)], ["check", "/tmp/minutes-none", str(ROOT)]):
        expect(f"worktree-location: git を起動できない（{arguments[0]}）",
               ["/usr/bin/python3", str(SCRIPTS / "worktree-location.py"), *arguments], 2, "git を起動できない",
               env=dict(os.environ, PATH="/nonexistent"))
    # 設定の有無: 壊れたリンクも設定として認めず、調べられない（読めない親のディレクトリなど）ものはないものとして認めない
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        dangling, closed = Path(directory) / "dangling", Path(directory) / "closed"
        dangling.mkdir()
        (dangling / ".swift-version").symlink_to("missing")
        closed.mkdir()
        command = ["/usr/bin/python3", str(SCRIPTS / "worktree-location.py"), "check"]
        expect("worktree-location: 壊れたリンクの設定", [*command, str(dangling / "base"), str(repository)], 1, ".swift-version", env=env)
        real_closed = closed.resolve()  # 判定は実体のパスで報告する（TMPDIR がリンクを含むとき）
        closed.chmod(0)
        try:
            expect("worktree-location: 調べられない設定", [*command, str(closed), str(repository)], 2,
                   str(real_closed / ".swiftformat"), env=env)
        finally:
            closed.chmod(0o755)
        expect("worktree-location: 設定のない場所", [*command, str(closed / "base"), str(repository)], 0, env=env)
        # 登録だけが残り、ディレクトリのない作業ツリーの中は、置き場所を作る前に認めない（作るとその登録の中にできる）。
        # 名前の似た隣の場所は認める
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base"], env=env, check=True,
                       capture_output=True)
        gone = Path(directory) / "gone"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(gone)], env=env, check=True,
                       capture_output=True)
        shutil.rmtree(gone)
        expect("worktree-location: ディレクトリのない作業ツリーの中", [*command, str(gone / "cache" / "base"), str(repository)], 1,
               "ディレクトリがない", env=env)
        expect("worktree-location: ディレクトリのない作業ツリーそのもの", [*command, str(gone), str(repository)], 1, "ディレクトリがない",
               env=env)
        count_before = not gone.exists()
        expect("worktree-location: ディレクトリのない作業ツリーの隣", [*command, str(Path(directory) / "gone-sibling"), str(repository)], 0,
               env=env)
        if not count_before or gone.exists():
            failures.append("worktree-location: ディレクトリのない作業ツリーの場所に、判定でディレクトリを作った")
        # 大文字と小文字を区別しないボリュームでは、綴りの大小だけが違う置き場所も同じ作業ツリーの中として認めない。
        # 名前の前の部分が同じ隣の場所（gone と GONE-other）は認める。区別するボリュームでは別の名前として認める
        sensitive = os.pathconf(directory, 11)  # _PC_CASE_SENSITIVE（macOS の sys/unistd.h）
        expect("worktree-location: 大小だけが違う、ディレクトリのない作業ツリーの中", [*command, str(Path(directory) / "GONE" / "base"),
               str(repository)], 0 if sensitive else 1, "" if sensitive else "ディレクトリがない", env=env)
        expect("worktree-location: 大小だけが違う名前の隣", [*command, str(Path(directory) / "GONE-other"), str(repository)], 0, env=env)
        if gone.exists():
            failures.append("worktree-location: 大小の違う置き場所の判定でディレクトリを作った")
        # 判定の間に作業ツリーが登録された（2 回目の一覧にだけ登録がある）場合は認めない。git の代わりは、2 回目以降の
        # worktree list の出力に登録を 1 つ足す
        shims = Path(directory) / "shims"
        shims.mkdir()
        (shims / "git").write_text(
            '#!/bin/bash\nlisting=""\nfor argument in "$@"; do if [ "$argument" = list ]; then listing=1; fi; done\n'
            '"$MINUTES_REAL_GIT" "$@" || exit $?\n'
            'if [ -n "$listing" ]; then\n  if [ -e "$MINUTES_TEST_OUT" ]; then printf "worktree /registered-later\\0detached\\0\\0"; fi\n'
            '  touch "$MINUTES_TEST_OUT"\nfi\n', encoding="utf-8")
        (shims / "git").chmod(0o755)
        expect("worktree-location: 判定の間に登録が変わった", [*command, str(closed / "base"), str(repository)], 1, "登録が変わった",
               env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_REAL_GIT=shutil.which("git") or "git",
                        MINUTES_TEST_OUT=str(Path(directory) / "listed")))
        # 登録だけが残り、親がファイルに置き換わった作業ツリーはないものとする。置き場所の途中がファイルなら判定できない
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base"], env=env, check=True, capture_output=True)
        parent = Path(directory) / "gone"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(parent / "deep" / "tree")], env=env,
                       check=True, capture_output=True)
        shutil.rmtree(parent)
        parent.write_text("", encoding="utf-8")
        expect("worktree-location: 親がファイルになった登録", [*command, str(closed / "base"), str(repository)], 0, env=env)
        expect("worktree-location: 親がファイルになった登録（nested）", [*command[:-1], "nested", str(repository), str(closed)], 0, env=env)
        expect("worktree-location: 途中がファイルの置き場所", [*command, str(parent / "base"), str(repository)], 2, env=env)
        # 登録の後に途中の要素がリンクに置き換わった作業ツリーも、リンクを解決した場所で下にあると判定する
        (Path(directory) / "outer").mkdir()
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(Path(directory) / "outer" / "deep" / "tree")],
                       env=env, check=True, capture_output=True)
        nested_command = [*command[:-1], "nested", str(repository)]
        expect("worktree-location: リンクのない外の登録（nested）", [*nested_command, str(closed)], 0, env=env)
        (Path(directory) / "outer").rename(closed / "moved")
        (Path(directory) / "outer").symlink_to(closed / "moved")
        expect("worktree-location: リンクを経由して下にある登録（nested）", [*nested_command, str(closed)], 1, "outer/deep/tree", env=env)
        (closed / "inside").mkdir()
        expect("worktree-location: リンクの先が別の場所の登録（nested）", [*nested_command, str(closed / "inside")], 0, env=env)
        # 権限で調べられない登録は、ないものとしない
        locked = Path(directory) / "locked"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(locked / "tree")], env=env, check=True,
                       capture_output=True)
        locked.chmod(0)
        try:
            expect("worktree-location: 調べられない登録", [*command, str(closed / "base"), str(repository)], 2, "locked", env=env)
        finally:
            locked.chmod(0o755)
        # 登録の置き場所を列挙できなければ、git は登録を省いて成功するので、一覧が欠けていないとは見なさない
        registrations = repository / ".git" / "worktrees"
        for mode in (0o000, 0o111, 0o444):
            registrations.chmod(mode)
            try:
                for arguments in (["check", str(closed / "base"), str(repository)], ["registered", str(repository), str(locked / "tree")],
                                  ["nested", str(repository), str(locked)]):
                    expect(f"worktree-location: 列挙できない登録（{mode:o}・{arguments[0]}）", [*command[:-1], *arguments], 2,
                           "登録を列挙できない", env=env)
            finally:
                registrations.chmod(0o755)
        # 登録の記録（gitdir）を読めない・ない・空の登録も git は黙って省くので、その作業ツリーの中の置き場所を認めない
        inside = Path(directory) / "recorded"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(inside)], env=env, check=True,
                       capture_output=True)
        record = registrations / "recorded" / "gitdir"
        original = record.read_bytes()
        for label in ("読めない", "空", "ない"):
            if label == "読めない":
                record.chmod(0)
            elif label == "空":
                record.write_bytes(b"")
            else:
                record.unlink()
            try:
                expect(f"worktree-location: gitdir が{label}登録", [*command, str(inside / "base"), str(repository)], 2, "gitdir", env=env)
            finally:
                if record.exists():
                    record.chmod(0o644)
                record.write_bytes(original)
        (registrations / ".DS_Store").write_bytes(b"x")  # 登録ではないファイルは妨げない
        expect("worktree-location: 読める登録の中", [*command, str(inside / "base"), str(repository)], 1, "作業ツリー", env=env)
        shutil.rmtree(inside)
        # ディレクトリがなくなっても、登録が残る間はその中を認めない（作るとその登録の中にできる。2026-11-27 に変更）
        expect("worktree-location: 読める登録の、なくなった作業ツリー", [*command, str(inside / "base"), str(repository)], 1,
               "ディレクトリがない", env=env)


def pre_push_lock_case() -> None:
    """プッシュ前のフックが、ロックの取得の途中の中断で自分のロックを残さず、取れなかったほかの実行のロックを消さないこと。

    mkdir の代わりは、実際の mkdir の後（成功・失敗とも）にフックへ TERM を送る。
    """
    global count
    for label, held, suffix in (("取得できた", False, "[0-9a-f].lock"), ("取得できなかった", True, "[0-9a-f].lock"),
                                ("取得できた", False, ".install.lock"), ("取得できなかった", True, ".install.lock")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, base, shims = Path(directory) / "repository", Path(directory) / "base", Path(directory) / "shims"
            (repository / "scripts").mkdir(parents=True)
            shims.mkdir()
            copy_location_script(repository)
            (repository / ".tools").mkdir()
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            hook = Path(directory) / "pre-push"
            hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
            hook.chmod(0o755)
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                               capture_output=True)
            head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], env=env, check=True, capture_output=True,
                                  text=True).stdout.strip()
            sent = Path(directory) / "sent"
            write_lock_shim(shims, suffix, held)
            result = subprocess.run([str(hook)], cwd=repository, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                    capture_output=True, text=True, timeout=60,
                                    env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_PRE_PUSH_DIR=str(base),
                                             MINUTES_TEST_SENT=str(sent)))
            locks = list(base.glob("*.lock")) if suffix != ".install.lock" else list((repository / ".tools").glob(".install.lock"))
            if suffix == ".install.lock" and list(base.glob("*.lock")):
                failures.append(f"pre-push: 導入のロックの中断（{label}）で作業ツリーのロックが残った")
            if result.returncode != 130 or not sent.exists() or len(locks) != (1 if held else 0):
                failures.append(f"pre-push: ロック（{suffix}）を{label}ときの中断の扱いが違う（終了コード {result.returncode}・"
                                f"ロック {locks}）\n{result.stderr.strip()}")


def newline_checkout_case() -> None:
    """起点の名前が改行で終わっても、setup.sh --hooks・gitleaks-history.sh・setup-tools.sh が起点・導入先を取り違えないこと。"""
    global count
    env = isolated_git_env()
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        # 改行のない名前の別のリポジトリも置く（取り違えたときに書き換わる側）
        other = setup_repository(directory, env)
        shutil.copytree(other, Path(directory) / "repository\n", symlinks=True)
        target = Path(directory) / "repository\n"
        result = subprocess.run([str(target / "scripts" / "setup.sh"), "--hooks"], env=env, capture_output=True, text=True)
        values = [subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=path, env=env, capture_output=True,
                                 text=True).stdout.strip() for path in (target, other)]
        if result.returncode != 0 or values != [".githooks", ""]:
            failures.append(f"setup: 改行で終わる起点の --hooks が違う（終了コード {result.returncode}・設定 {values}）\n{result.stderr.strip()}")
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        checkout, repository = Path(directory) / "checkout\n", Path(directory) / "repository"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / ".tools" / "bin").mkdir(parents=True)
        shutil.copy2(SCRIPTS / "gitleaks-history.sh", checkout / "scripts" / "gitleaks-history.sh")
        (checkout / ".gitleaks.toml").write_text("", encoding="utf-8")
        # gitleaks の代わり: 設定のファイルがあることを確かめ、git log を 1 回起動する
        (checkout / ".tools" / "bin" / "gitleaks").write_text(
            '#!/bin/bash\nwhile [ $# -gt 1 ]; do if [ "$1" = --config ]; then [ -f "$2" ] || exit 5; fi; shift; done\n'
            'git -C "$1" log > /dev/null\n', encoding="utf-8")
        (checkout / ".tools" / "bin" / "gitleaks").chmod(0o755)
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
            repository.mkdir(exist_ok=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", *arguments], env=env, check=True, capture_output=True)
        history_env = {key: value for key, value in env.items() if key != "MINUTES_TOOLS_DIR"}
        result = subprocess.run([str(checkout / "scripts" / "gitleaks-history.sh"), str(repository)], env=history_env,
                                capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(f"gitleaks: 改行で終わる起点の gitleaks-history.sh が通らない（終了コード {result.returncode}）\n{result.stderr.strip()}")
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        # 起点も導入先も改行で終わる。照合する導入は代わりの gh
        checkout, dest = Path(directory) / "checkout\n", Path(directory) / "tools\n"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / "tools").mkdir()
        shutil.copy2(SCRIPTS / "setup-tools.sh", checkout / "scripts" / "setup-tools.sh")
        sha = "0" * 64
        (checkout / "tools" / "versions").write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.zip gh {sha}\n", encoding="utf-8")
        binary = dest / "gh" / "9.9.9" / "gh"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho 'gh version 9.9.9 (2000-01-01)'\n", encoding="utf-8")
        binary.chmod(0o755)
        (binary.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
        (dest / "bin").mkdir()
        (dest / "bin" / "gh").symlink_to("../gh/9.9.9/gh")
        result = subprocess.run([str(checkout / "scripts" / "setup-tools.sh"), "--verify", "--dest", str(dest), "--require", "gh"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(f"versions: 改行で終わる起点・導入先の照合が通らない（終了コード {result.returncode}）\n{result.stderr.strip()}")


def setup_relative_case() -> None:
    """setup.sh を相対パスで実行しても、CDPATH によらずリポジトリの起点を解決し、setup-tools.sh を絶対パスで呼ぶこと。

    CDPATH に「scripts」を持つ別のディレクトリを置く（外さないと、相対パスの cd がそこへ移り、移った先の表示が
    起点の値に混ざる）。開発ツールの導入は、呼ばれたパスを記録するだけの代わりのスクリプトに置き換える。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (Path(directory) / "decoy" / "scripts").mkdir(parents=True)
        (repository / "scripts" / "setup.sh").write_text((SCRIPTS / "setup.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "setup-tools.sh").write_text(
            '#!/bin/bash\nprintf \'%s\\n\' "$0" > "$(dirname "$0")/../called"\n', encoding="utf-8")
        for name in ("setup.sh", "setup-tools.sh"):
            (repository / "scripts" / name).chmod(0o755)
        result = subprocess.run(["scripts/setup.sh"], cwd=repository, capture_output=True, text=True,
                                env=dict(os.environ, CDPATH=str(Path(directory) / "decoy")))
        called = repository / "called"
        recorded = called.read_text(encoding="utf-8").strip() if called.is_file() else ""
        expected = os.path.realpath(repository / "scripts" / "setup-tools.sh")
        if result.returncode != 0 or not os.path.isabs(recorded) or os.path.realpath(recorded) != expected:
            failures.append(f"setup: 相対パスと CDPATH で、起点を解決して setup-tools.sh を絶対パスで呼ばない（終了コード "
                            f"{result.returncode}・呼ばれたパス「{recorded}」）\n{(result.stdout + result.stderr).strip()}")


def require_clean_case(inherited: bool = False) -> None:
    """check.sh --require-clean が、開始時のコミットを専用の作業ツリーに展開してそこで検査し、終了後に片付けること。

    inherited では、元の作業ツリーで MINUTES_CHECK_ISOLATED=1 を引き継いでも展開を省かないこと（変数は、この検査の
    専用の作業ツリーの印がある場合だけ認める）を確かめる。

    一時のリポジトリに check.sh と、手順 10 が呼ぶスクリプトの代わり（実行した場所と読んだ内容を記録する）を置いて試す。
    記録した場所が元の作業ツリーでなく、読んだ内容が開始時のコミットのものであること（検査中に元の作業ツリーを
    書き換えても、検査する内容は変わらない）を確かめる。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        # 外側が専用の作業ツリーで実行中（プッシュ前のフック・--require-clean）でも、入れ子の check.sh には専用の作業ツリーへ
        # 展開する経路を通らせる（引き継ぐと展開を省き、元の作業ツリーで検査してしまう）。ほかの変数は残す
        env.pop("MINUTES_CHECK_ISOLATED", None)
        if inherited:
            env["MINUTES_CHECK_ISOLATED"] = "1"
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        out = Path(directory) / "recorded"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        copy_location_script(repository)
        # 手順 10 の代わり: 先に元の作業ツリーの marker.txt を書き換え、その後で実行した場所と marker.txt の内容を記録する
        # （読む前に元の作業ツリーを変えても、専用の作業ツリーの内容が開始時のコミットのままであることを確かめる）
        (repository / "scripts" / "verify-docs.py").write_text(
            '#!/bin/sh\nprintf changed > "$MINUTES_TEST_ORIGINAL/marker.txt"\n'
            'if [ -L .tools ]; then tools=link; elif [ -f .tools/marker ]; then tools=copy; else tools=missing; fi\n'
            # 項目は NUL で区切る（専用の作業ツリーのパスに含まれる改行を、項目の区切りと取り違えない）
            'printf "%s\\0" "$(pwd -P)" "$(cat marker.txt)" "$tools" "isolated=${MINUTES_CHECK_ISOLATED:-}" '
            '"$(git worktree list --porcelain -z | tr "\\n\\0" " \\n" | grep -c "^worktree ")" > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
        for name in ("check.sh", "test-checks.py", "verify-docs.py"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "marker.txt").write_text("committed", encoding="utf-8")
        # 開発ツールの代わり（Git の管理外）。専用の作業ツリーにはリンクではなく写しが置かれること（導入のロックを取って写す）
        (repository / ".tools").mkdir()
        (repository / ".tools" / "marker").write_text("tools", encoding="utf-8")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
        # 有効な post-checkout のフックが失敗しても、専用の作業ツリーの展開では動かさない
        (repository / ".git" / "hooks").mkdir(exist_ok=True)
        (repository / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (repository / ".git" / "hooks" / "post-checkout").chmod(0o755)
        # git の代わり: 作業ツリーの作成を数え、2 回目は失敗させる（起動した入れ子の実行に印が届かず、展開を繰り返す場合に止める）
        shims, added = Path(directory) / "shims", Path(directory) / "added"
        shims.mkdir()
        (shims / "git").write_text('#!/bin/bash\ncase " $* " in *" worktree add "*) echo add >> "$MINUTES_TEST_ADDED"\n'
                                   '  if [ "$(wc -l < "$MINUTES_TEST_ADDED")" -gt 1 ]; then exit 1; fi ;; esac\n'
                                   'exec "$MINUTES_TEST_GIT" "$@"\n', encoding="utf-8")
        (shims / "git").chmod(0o755)
        # 代わりのスクリプトが書き換えた marker.txt を、終了の前に戻すことはしない（元の作業ツリーの変更は検査に影響しない）
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "10", "--require-clean"], capture_output=True,
                                text=True, timeout=60,
                                env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out), MINUTES_TEST_ADDED=str(added),
                                         MINUTES_TEST_GIT=str(shutil.which("git")), MINUTES_TEST_ORIGINAL=str(repository)))
        recorded = out.read_bytes().decode("utf-8").split("\0")[:-1] if out.is_file() else []
        worktrees = subprocess.run(["git", "-C", str(repository), "worktree", "list", "--porcelain"], env=env, capture_output=True,
                                   text=True).stdout
        # 起動した入れ子の実行に専用の作業ツリーの印（MINUTES_CHECK_ISOLATED=1）が届き、さらに専用の作業ツリーを作らないこと
        isolated = (len(recorded) == 5 and recorded[1:] == ["committed", "copy", "isolated=1", "2"]
                    and not recorded[0].startswith(os.path.realpath(repository)))
        if result.returncode != 0 or not isolated or "専用の作業ツリーで検査" not in result.stdout:
            failures.append(f"check: --require-clean が専用の作業ツリーで検査しない（引き継いだ変数 {inherited}・"
                            f"終了コード {result.returncode}・記録 {recorded}）\n"
                            f"{(result.stdout + result.stderr).strip()}")
        # 作業ツリーの数は「worktree 」で始まる行だけを数える（パスの途中の「worktree 」を数えない）
        elif sum(line.startswith("worktree ") for line in worktrees.splitlines()) != 1 or (recorded and Path(recorded[0]).exists()):
            failures.append(f"check: --require-clean の専用の作業ツリーが残った\n{worktrees.strip()}")
        elif (repository / ".tools" / ".install.lock").exists():
            failures.append("check: --require-clean が開発ツールの導入のロックを残した")


def require_clean_location_case() -> None:
    """check.sh --require-clean が、一時の置き場所（TMPDIR）が SwiftFormat の設定を継ぐ場所・作業ツリーの中なら、
    専用の作業ツリーを作らずに失敗すること（プッシュ前のフックと同じ判定）。"""
    global count
    for label in ("SwiftFormat の設定の下", "作業ツリーの中"):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            out = Path(directory) / "recorded"
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "verify-docs.py").write_text('#!/bin/sh\npwd > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
            (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for name in ("check.sh", "verify-docs.py", "test-checks.py"):
                (repository / "scripts" / name).chmod(0o755)
            copy_location_script(repository)
            (repository / ".gitignore").write_text("/.tools\n/tmp/\n", encoding="utf-8")
            (repository / ".tools").mkdir()
            identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", f"core.hooksPath={os.devnull}"]
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
            if label == "作業ツリーの中":
                temporary = repository / "tmp"
            else:
                temporary = Path(directory) / "configured" / "tmp"
                (Path(directory) / "configured" / ".swiftformat").parent.mkdir(parents=True)
                (Path(directory) / "configured" / ".swiftformat").write_text("--indent 2\n", encoding="utf-8")
            temporary.mkdir(parents=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "10", "--require-clean"],
                                    capture_output=True, text=True, env=dict(env, TMPDIR=str(temporary), MINUTES_TEST_OUT=str(out)))
            leftovers = sorted(path.name for path in temporary.iterdir())
            if result.returncode != 1 or "一時の置き場所" not in result.stderr or out.exists() or leftovers:
                failures.append(f"check: --require-clean が{label}の一時の置き場所で止まらない（終了コード {result.returncode}・"
                                f"残ったもの {leftovers}）\n{(result.stdout + result.stderr).strip()}")


def require_clean_sparse_case() -> None:
    """check.sh --require-clean が、元の作業ツリーの sparse checkout に worktree-location.py がなくても、開始時のコミットのもので置き場所を
    判定して検査すること（書き出した場所の同名のモジュールを読まない）。認めない置き場所は、そのままでも止めること。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory(dir=CLEAN_TEMPORARY_BASE.name) as clean:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        # 一時の置き場所は、SwiftFormat の設定を継がない CLEAN_TEMPORARY_BASE の下に作る（システムの一時ディレクトリの祖先に
        # 設定があっても、正の試験を置き場所の拒否で失敗させない）
        repository, out, temporary = Path(directory) / "repository", Path(directory) / "recorded", Path(clean)
        (repository / "scripts").mkdir(parents=True)
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "verify-docs.py").write_text('#!/bin/sh\npwd > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "verify-docs.py", "test-checks.py"):
            (repository / "scripts" / name).chmod(0o755)
        copy_location_script(repository)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / ".tools").mkdir()
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"],
                          ["sparse-checkout", "set", "--no-cone", "/*", "!/scripts/worktree-location.py"]):
            subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
        absent = not (repository / "scripts" / "worktree-location.py").exists()
        run_env = dict(env, TMPDIR=str(temporary), MINUTES_TEST_OUT=str(out))
        command = [str(repository / "scripts" / "check.sh"), "--steps", "10", "--require-clean"]
        result = subprocess.run(command, capture_output=True, text=True, env=run_env, timeout=60)
        checked = out.is_file()
        out.unlink(missing_ok=True)
        (temporary / ".swiftformat").write_text("--indent 2\n", encoding="utf-8")
        refused = subprocess.run(command, capture_output=True, text=True, env=run_env, timeout=60)
        if not absent or result.returncode != 0 or not checked:
            failures.append(f"check: --require-clean が sparse checkout で判定できない（なし {absent}・終了コード {result.returncode}）\n"
                            f"{(result.stdout + result.stderr).strip()}")
        elif refused.returncode != 1 or "一時の置き場所" not in refused.stderr or out.exists():
            failures.append(f"check: --require-clean が sparse checkout で置き場所を確かめない（終了コード {refused.returncode}）")


def runner_integrity_case() -> None:
    """check.sh --require-clean とプッシュ前のフックが、専用の作業ツリーの scripts/check.sh がシンボリックリンク・実行権限のない
    ファイルなら、起動せずに理由を示して止めること（リンクの先の代わりの check.sh が呼ばれないことで確かめる）。"""
    global count
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                "-c", f"core.hooksPath={os.devnull}"]
    want = "scripts/check.sh が通常の実行できるファイルでない"
    for label in ("シンボリックリンク", "実行権限のないファイル"):
        # check.sh --require-clean: 起動する check.sh は tools/runner.sh（本物）。コミットの scripts/check.sh だけを変える
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, out, temporary = Path(directory) / "repository", Path(directory) / "recorded", Path(directory) / "tmp"
            (repository / "scripts").mkdir(parents=True)
            (repository / "tools").mkdir()
            temporary.mkdir()
            runner = repository / "tools" / "runner.sh"
            runner.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            runner.chmod(0o755)
            (repository / "scripts" / "verify-docs.py").write_text('#!/bin/sh\npwd > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
            (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for name in ("verify-docs.py", "test-checks.py"):
                (repository / "scripts" / name).chmod(0o755)
            if label == "シンボリックリンク":
                (repository / "scripts" / "check.sh").symlink_to("../tools/runner.sh")
            else:
                (repository / "scripts" / "check.sh").write_text(runner.read_text(encoding="utf-8"), encoding="utf-8")
                (repository / "scripts" / "check.sh").chmod(0o644)
            copy_location_script(repository)
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            (repository / ".tools").mkdir()
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
            result = subprocess.run([str(runner), "--steps", "10", "--require-clean"], capture_output=True, text=True,
                                    env=dict(env, TMPDIR=str(temporary), MINUTES_TEST_OUT=str(out)), timeout=60)
            if result.returncode != 1 or want not in result.stderr or out.exists():
                failures.append(f"check: --require-clean が専用の作業ツリーの check.sh（{label}）を起動した・理由を示さない"
                                f"（終了コード {result.returncode}・起動 {out.exists()}）\n{result.stderr.strip()}")
        # プッシュ前のフック: リンクの先（作業ツリーの中の real/check.sh）は記録を書く代わり
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            work, base, out = Path(directory) / "work", Path(directory) / "base", Path(directory) / "recorded"
            hook = Path(directory) / "pre-push"
            hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
            hook.chmod(0o755)
            (work / "scripts").mkdir(parents=True)
            (work / "real").mkdir()
            (work / ".tools" / "bin").mkdir(parents=True)
            copy_location_script(work)
            stub = '#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\n'
            (work / "real" / "check.sh").write_text(stub, encoding="utf-8")
            (work / "real" / "check.sh").chmod(0o755)
            if label == "シンボリックリンク":
                (work / "scripts" / "check.sh").symlink_to("../real/check.sh")
            else:
                (work / "scripts" / "check.sh").write_text(stub, encoding="utf-8")
                (work / "scripts" / "check.sh").chmod(0o644)
            (work / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(work), *identity, *arguments], env=env, check=True, capture_output=True)
            head = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], env=env, check=True, capture_output=True,
                                  text=True).stdout.strip()
            result = subprocess.run([str(hook)], cwd=work, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                    capture_output=True, text=True, timeout=60,
                                    env=dict(env, MINUTES_PRE_PUSH_DIR=str(base), MINUTES_TEST_OUT=str(out)))
            if result.returncode != 1 or want not in result.stderr or out.exists():
                failures.append(f"pre-push: 専用の作業ツリーの check.sh（{label}）を起動した・理由を示さない"
                                f"（終了コード {result.returncode}・起動 {out.exists()}）\n{result.stderr.strip()}")


def require_clean_smudge_case() -> None:
    """check.sh --require-clean が、smudge のフィルタで内容を変えて展開した専用の作業ツリーを検査せず、失敗すること。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        out = Path(directory) / "recorded"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        copy_location_script(repository)
        (repository / "scripts" / "verify-docs.py").write_text('#!/bin/sh\ncat marker.txt > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "verify-docs.py", "test-checks.py"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / ".gitattributes").write_text("marker.txt filter=rewrite\n", encoding="utf-8")
        (repository / "marker.txt").write_text("committed\n", encoding="utf-8")
        (repository / ".tools").mkdir()
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"],
                          ["config", "filter.rewrite.smudge", "sed s/committed/smudged/"],
                          # clean で smudge を打ち消す（git status では変更がないように見える。status の照合だけでは見逃す）
                          ["config", "filter.rewrite.clean", "sed s/smudged/committed/"]):
            subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "10", "--require-clean"], capture_output=True,
                                text=True, env=dict(env, MINUTES_TEST_OUT=str(out)), timeout=60)
        if result.returncode == 0 or "blob と違う" not in result.stderr or out.exists():
            failures.append(f"check: --require-clean が smudge で変わった展開を検査した（終了コード {result.returncode}・"
                            f"記録 {out.read_text(encoding='utf-8') if out.exists() else None!r}）\n{(result.stdout + result.stderr).strip()}")


def setup_include_case() -> None:
    """setup.sh --hooks が、共有の設定が取り込む設定（includeIf.onbranch）の core.hooksPath が別の値なら、書かずに止めること。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        env = isolated_git_env()
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        (repository / "scripts" / "setup.sh").write_text((SCRIPTS / "setup.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        for name in ("setup.sh", "setup-tools.sh"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".githooks").mkdir()
        for name in ("pre-commit", "pre-push"):
            (repository / ".githooks" / name).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            (repository / ".githooks" / name).chmod(0o755)
        (repository / ".git" / "extra.config").write_text("[core]\n\thooksPath = elsewhere\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "config", "--local", "includeIf.onbranch:main.path", "extra.config"],
                       env=env, check=True)
        result = subprocess.run([str(repository / "scripts" / "setup.sh"), "--hooks"], env=env, capture_output=True, text=True)
        local = subprocess.run(["git", "-C", str(repository), "config", "--local", "--no-includes", "--get", "core.hooksPath"],
                               env=env, capture_output=True, text=True).stdout.strip()
        if result.returncode != 1 or local or "取り込む設定" not in result.stderr:
            failures.append(f"setup: includeIf の core.hooksPath があるのに共有の設定を書いた・止まらない（終了コード "
                            f"{result.returncode}・共有の値「{local}」）\n{result.stderr.strip()}")


def coverage_record_message_case() -> None:
    """check.sh の手順 8 が、測定の記録がないとき、専用の作業ツリーでは手順 7 と 8 を一緒に実行する案内を、通常の実行では
    手順 7 を先に実行する案内を出すこと（専用の作業ツリーは実行のたびに作り直すので、前の測定が残らない）。

    専用の作業ツリーの場合は、印（MINUTES_CHECK_ISOLATED）を渡すだけでなく、--require-clean で実際に専用の作業ツリーを作って
    試す（入れ子の check.sh が専用の作業ツリーで手順 8 を実行する経路を通す）。通常の実行は、元の作業ツリーで印を外して試す。"""
    global count
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / "Packages" / "MinutesKit").mkdir(parents=True)
        (repository / "Packages" / "MinutesKit" / "Package.swift").write_text("// 試験用\n", encoding="utf-8")
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "test-checks.py"):
            (repository / "scripts" / name).chmod(0o755)
        copy_location_script(repository)
        (repository / ".gitignore").write_text("/.tools\n.build/\n", encoding="utf-8")
        (repository / ".tools").mkdir()  # 専用の作業ツリーへ写す開発ツール（Git の管理外）
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)  # 通常の実行の場合を、外側の専用の作業ツリーの指定に左右させない
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                           capture_output=True)
        for label, arguments, want in (("専用の作業ツリー", ["--require-clean", "--steps", "8"], "--require-clean --steps 7,8"),
                                       ("通常の実行", ["--steps", "8"], "手順 7 を先に実行する")):
            count += 1
            result = subprocess.run([str(repository / "scripts" / "check.sh"), *arguments], capture_output=True, text=True,
                                    env=env, timeout=60)
            if result.returncode != 1 or want not in result.stderr:
                failures.append(f"check: 測定の記録がないときの案内（{label}）が違う（終了コード {result.returncode}）\n"
                                f"{result.stderr.strip()}")
            # 通常の実行は、専用の作業ツリーの案内を出さない
            if label == "通常の実行" and "--require-clean --steps 7,8" in result.stderr:
                failures.append(f"check: 通常の実行で、専用の作業ツリーの案内を出した\n{result.stderr.strip()}")


def stage_tool_stubs(repository: Path) -> None:
    """check.sh --staged は開発ツールを写し、ステージした tools/versions で照合してから使う。代わりのツールで試す一時の
    リポジトリには、照合の代わり（何もせず成功する scripts/setup-tools.sh）と台帳を置く（git add -A でステージする）。"""
    (repository / "tools").mkdir(parents=True, exist_ok=True)
    (repository / "tools" / "versions").write_text("# 試験用の台帳（照合は代わりの setup-tools.sh が行う）\n", encoding="utf-8")
    stub = repository / "scripts" / "setup-tools.sh"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    # 手順 3 の最後の録音データの混入の検査（SEC-SUP-04）の代わり。本物は test_tracked で試す
    stub = repository / "scripts" / "check-tracked-files.py"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    # ステージした内容を書き出す一時ディレクトリの置き場所も、本物の判定で確かめる（check.sh の make_staged_tree）
    copy_location_script(repository)


def copy_location_script(repository: Path) -> None:
    """check.sh --require-clean が専用の作業ツリーの置き場所を判定するスクリプトを、一時のリポジトリに写す。"""
    target = repository / "scripts" / "worktree-location.py"
    target.write_text((SCRIPTS / "worktree-location.py").read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o755)


def staged_raw_blob_case() -> None:
    """check.sh --staged の整形・静的解析が、smudge のフィルタを通さずに、ステージした blob そのものを検査すること。

    一時のリポジトリで、.gitattributes の smudge のフィルタが内容を書き換える Swift ファイルをステージし、ステージした後に
    作業ツリーの内容だけを変える（ステージし直さない。作業ツリーを読む実装と区別する）。整形の代わり
    （.tools/bin/swiftformat。渡されたファイルの内容を記録して成功する）と静的解析の代わり（.tools/bin/swiftlint。
    渡されたファイルの内容を記録して失敗し、手順 2 で止める）の両方が読んだ内容を確かめる。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        out = Path(directory) / "recorded"
        lint_out = Path(directory) / "recorded-lint"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").chmod(0o755)
        stage_tool_stubs(repository)
        # 整形の代わり: 渡された最後のファイルの内容を記録し、成功する（手順 2 の静的解析へ進む）
        formatter = repository / ".tools" / "bin" / "swiftformat"
        formatter.write_text('#!/bin/sh\nfor last in "$@"; do :; done\ncat "$last" > "$MINUTES_TEST_OUT"\nexit 0\n',
                             encoding="utf-8")
        formatter.chmod(0o755)
        # 静的解析の代わり: 渡された最後のファイルの内容を記録し、手順 2 で止める（後の手順を実行しない）
        linter = repository / ".tools" / "bin" / "swiftlint"
        linter.write_text('#!/bin/sh\nfor last in "$@"; do :; done\ncat "$last" > "$MINUTES_TEST_LINT_OUT"\nexit 1\n',
                          encoding="utf-8")
        linter.chmod(0o755)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / ".gitattributes").write_text("*.swift filter=rewrite\n", encoding="utf-8")
        (repository / "Sample.swift").write_text("let raw = 1\n", encoding="utf-8")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        # clean は設定しない（ステージした blob は作業ツリーの内容のまま）。smudge だけが書き出しの内容を書き換える
        subprocess.run(["git", "-C", str(repository), "config", "filter.rewrite.smudge", "sed s/raw/smudged/"], env=env, check=True)
        subprocess.run(["git", "-C", str(repository), *identity, "add", "-A"], env=env, check=True, capture_output=True)
        # ステージした後に作業ツリーだけを変える（ステージし直さない）。作業ツリーを読む実装は、この内容を記録して失敗する
        (repository / "Sample.swift").write_text("let worktree = 2\n", encoding="utf-8")
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], capture_output=True, text=True,
                                env=dict(env, MINUTES_TEST_OUT=str(out), MINUTES_TEST_LINT_OUT=str(lint_out)), timeout=60)
        for label, path in (("整形", out), ("静的解析", lint_out)):
            # バイト列で比べる（テキストとして読むと改行の違いを正規化し、CRLF への変換などを見逃す）
            recorded = path.read_bytes() if path.is_file() else None
            if recorded != b"let raw = 1\n":
                failures.append(f"check: --staged の{label}がステージした blob そのものを検査しない（読んだ内容 {recorded!r}・"
                                f"終了コード {result.returncode}）\n{(result.stdout + result.stderr).strip()}")
        # 静的解析の代わりの失敗で手順 2 に止まり、手順 3 へ進まないこと（記録だけを見ると、失敗を無視して進む実装を見逃す）
        if result.returncode == 0 or "NG: 手順 2" not in result.stderr or "== 手順 3" in result.stdout:
            failures.append(f"check: --staged が静的解析の失敗で手順 2 に止まらない（終了コード {result.returncode}）\n"
                            f"{(result.stdout + result.stderr).strip()}")


def staged_raw_tree_case() -> None:
    """check.sh --staged の規則の試験（手順 2）と文書の整合（手順 10）が、smudge のフィルタを通さずにステージした blob を
    書き出し、実行するスクリプトのモード（100755）を保つこと。

    一時のリポジトリで、smudge のフィルタが内容を書き換える試験用のファイルと文書をステージする。規則の試験の代わり
    （scripts/test-lint-rules.py）と文書の整合の代わり（scripts/verify-docs.py）は、書き出した場所の内容を記録して成功する。
    どちらも書き出した場所から直接実行するので、実行権限を失うと実行できずに失敗する。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        (repository / "tools" / "lint-fixtures" / "sample").mkdir(parents=True)
        rules_out = Path(directory) / "recorded-rules"
        docs_out = Path(directory) / "recorded-docs"
        link_out = Path(directory) / "recorded-link"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        # 規則の試験の代わり: 起点（作業ディレクトリ）の試験用のファイルを記録する。文書の整合の代わり: --root の文書を記録する
        scripts = {"test-lint-rules.py": '#!/bin/sh\ncat tools/lint-fixtures/sample/ok.swift > "$MINUTES_TEST_RULES_OUT"\n',
                   "verify-docs.py": '#!/bin/sh\ncat "$2/doc.md" > "$MINUTES_TEST_DOCS_OUT"\n'
                                     'readlink -n "$2/newline-link" > "$MINUTES_TEST_LINK_OUT"\n'}
        for name, content in scripts.items():
            (repository / "scripts" / name).write_text(content, encoding="utf-8")
        for name in ("check.sh", *scripts):
            (repository / "scripts" / name).chmod(0o755)
        stage_tool_stubs(repository)
        # 秘密情報の検出の代わり（手順 3 を通す）
        gitleaks = repository / ".tools" / "bin" / "gitleaks"
        gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gitleaks.chmod(0o755)
        (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / ".gitattributes").write_text("*.swift filter=rewrite\n*.md filter=rewrite\n", encoding="utf-8")
        (repository / "tools" / "lint-fixtures" / "sample" / "ok.swift").write_text("let raw = 1\n", encoding="utf-8")
        (repository / "doc.md").write_text("raw\n", encoding="utf-8")
        # 末尾に改行を含むリンク先のシンボリックリンク（書き出しで末尾の改行を落とさないこと）
        os.symlink("target\n", repository / "newline-link")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        # clean は設定しない（ステージした blob は作業ツリーの内容のまま）。smudge だけが書き出しの内容を書き換える
        subprocess.run(["git", "-C", str(repository), "config", "filter.rewrite.smudge", "sed s/raw/smudged/"], env=env, check=True)
        subprocess.run(["git", "-C", str(repository), *identity, "add", "-A"], env=env, check=True, capture_output=True)
        # ステージした後に作業ツリーだけを別の内容にする（ステージし直さない）。読んだ内容がステージした blob なら、作業ツリーの
        # 内容とも smudge の結果とも違う
        (repository / "tools" / "lint-fixtures" / "sample" / "ok.swift").write_text("let worktree = 1\n", encoding="utf-8")
        (repository / "doc.md").write_text("worktree\n", encoding="utf-8")
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], capture_output=True, text=True,
                                env=dict(env, MINUTES_TEST_RULES_OUT=str(rules_out), MINUTES_TEST_DOCS_OUT=str(docs_out),
                                         MINUTES_TEST_LINK_OUT=str(link_out)),
                                timeout=60)
        for label, path, want in (("規則の試験", rules_out, b"let raw = 1\n"), ("文書の整合", docs_out, b"raw\n"),
                                  ("文書の整合のリンク先", link_out, b"target\n")):
            recorded = path.read_bytes() if path.is_file() else None
            if recorded != want:
                failures.append(f"check: --staged の{label}がステージした blob そのものを書き出さない（読んだ内容 {recorded!r}・"
                                f"終了コード {result.returncode}）\n{(result.stdout + result.stderr).strip()}")
        if result.returncode != 0:
            failures.append(f"check: --staged の規則の試験・文書の整合の書き出しの試験が通らない（終了コード {result.returncode}）\n"
                            f"{(result.stdout + result.stderr).strip()}")


def staged_rules_link_case() -> None:
    """check.sh --staged の手順 2 が、ステージした scripts/test-lint-rules.py がシンボリックリンク（書き出した場所の外のスクリプトを
    指す）なら、実行せずに失敗すること。対照として、通常の実行できるファイルなら実行して通ること（staged_docs_link_case と同じ）。"""
    global count
    for label, linked in (("シンボリックリンク", True), ("通常のファイル", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            called = Path(directory) / "called"
            outside = Path(directory) / "outside.sh"
            outside.write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\n', encoding="utf-8")
            outside.chmod(0o755)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "verify-docs.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rules = repository / "scripts" / "test-lint-rules.py"
            if linked:
                rules.symlink_to(outside)
            else:
                rules.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
            for name in ("check.sh", "verify-docs.py", *(() if linked else ("test-lint-rules.py",))):
                (repository / "scripts" / name).chmod(0o755)
            stage_tool_stubs(repository)
            gitleaks = repository / ".tools" / "bin" / "gitleaks"
            gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gitleaks.chmod(0o755)
            (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            # 規則の設定をステージして、手順 2 に規則の試験を回させる
            (repository / ".swiftlint.yml").write_text("disabled_rules: []\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], capture_output=True, text=True,
                                    env=dict(env, MINUTES_TEST_OUT=str(called)), timeout=60)
            output = (result.stdout + result.stderr).strip()
            if linked and (result.returncode == 0 or called.exists() or "test-lint-rules.py が通常の実行できるファイルでない" not in output):
                failures.append(f"check: --staged の手順 2 が、リンクの test-lint-rules.py を拒否しない（終了コード {result.returncode}・"
                                f"実行 {called.exists()}）\n{output}")
            if not linked and (result.returncode != 0 or not called.exists()):
                failures.append(f"check: --staged の手順 2 が、通常の test-lint-rules.py で通らない（{label}・終了コード {result.returncode}）"
                                f"\n{output}")


def staged_docs_link_case() -> None:
    """check.sh --staged の手順 10 が、ステージした scripts/verify-docs.py がシンボリックリンク（書き出した場所の外のスクリプトを
    指す）なら、実行せずに失敗すること。対照として、通常の実行できるファイルなら実行して通ること。"""
    global count
    for label, linked in (("シンボリックリンク", True), ("通常のファイル", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            called = Path(directory) / "called"
            outside = Path(directory) / "outside.sh"
            outside.write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\n', encoding="utf-8")
            outside.chmod(0o755)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "test-lint-rules.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify = repository / "scripts" / "verify-docs.py"
            if linked:
                verify.symlink_to(outside)
            else:
                verify.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
            for name in ("check.sh", "test-lint-rules.py", *(() if linked else ("verify-docs.py",))):
                (repository / "scripts" / name).chmod(0o755)
            stage_tool_stubs(repository)
            gitleaks = repository / ".tools" / "bin" / "gitleaks"
            gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gitleaks.chmod(0o755)
            (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            (repository / "doc.md").write_text("doc\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], capture_output=True, text=True,
                                    env=dict(env, MINUTES_TEST_OUT=str(called)), timeout=60)
            output = (result.stdout + result.stderr).strip()
            if linked and (result.returncode == 0 or called.exists() or "通常の実行できるファイルでない" not in output):
                failures.append(f"check: --staged の手順 10 が、リンクの verify-docs.py を拒否しない（終了コード {result.returncode}・"
                                f"実行 {called.exists()}）\n{output}")
            if not linked and (result.returncode != 0 or not called.exists()):
                failures.append(f"check: --staged の手順 10 が、通常の verify-docs.py で通らない（{label}・終了コード {result.returncode}）"
                                f"\n{output}")


def staged_index_change_case() -> None:
    """check.sh --staged が、検査の間にステージした内容が変わったら（手順ごとに別の内容を検査しうるので）合格にしないこと。

    検査が読む専用のインデックス（引き継いだ GIT_INDEX_FILE）を変えた場合と、外から本来のインデックスを変えた場合の両方。
    """
    global count
    # HEAD: ステージした内容は変えずに HEAD だけを進める（ステージしたファイルを選んだ基点が変わる。HEAD がまだない状態から）
    for target in ("専用のインデックス", "本来のインデックス", "HEAD"):
        staged_index_change_run(target)


def staged_index_change_run(target: str) -> None:
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        stage_tool_stubs(repository)
        # 文書の整合の代わり: 検査の途中で作業ツリーの変更をステージする（手順 10 の後、合格の表示の前にインデックスが変わる）
        # 本来のインデックスは、検査が設定した GIT_INDEX_FILE を外して（git が選ぶ既定のインデックスで）変える
        stage = {"専用のインデックス": "printf changed > doc.md && git add doc.md",
                 "本来のインデックス": "printf changed > doc.md && env -u GIT_INDEX_FILE git add doc.md",
                 "HEAD": "env -u GIT_INDEX_FILE git -c user.name=test -c user.email=test@example.invalid "
                         "-c commit.gpgsign=false -c core.hooksPath=/dev/null commit -q -m moved"}[target]
        (repository / "scripts" / "verify-docs.py").write_text(f'#!/bin/sh\n{stage}\n', encoding="utf-8")
        gitleaks = repository / ".tools" / "bin" / "gitleaks"
        gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for path in (repository / "scripts" / "check.sh", repository / "scripts" / "verify-docs.py", gitleaks):
            path.chmod(0o755)
        (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / "doc.md").write_text("staged\n", encoding="utf-8")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), *identity, "add", "-A"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, capture_output=True,
                                text=True, env=env, timeout=60)
        output = result.stdout + result.stderr
        want = "検査中に HEAD が変わった" if target == "HEAD" else "ステージした内容が変わった"
        if result.returncode != 1 or want not in output or "コミット前の検査を通過" in output:
            failures.append(f"check: --staged が検査中に{target}が変わっても合格にする（終了コード {result.returncode}）\n"
                            f"{output.strip()}")


def staged_setup_tools_case() -> None:
    """check.sh --staged とコミット前のフックが、照合にステージした scripts/setup-tools.sh を使うこと（作業ツリーのものを使わない）。

    ステージした setup-tools.sh は照合に失敗し（exit 1）、作業ツリーのものは成功する（exit 0）一時のリポジトリで、
    どちらも照合の失敗で止まることを確かめる（作業ツリーのものを使う実装は通ってしまう）。
    """
    global count
    for runner in ("check.sh --staged", "pre-commit"):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            (repository / ".githooks").mkdir()
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            hook = repository / ".githooks" / "pre-commit"
            hook.write_text((ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8"), encoding="utf-8")
            for path in (repository / "scripts" / "check.sh", hook):
                path.chmod(0o755)
            stage_tool_stubs(repository)
            setup = repository / "scripts" / "setup-tools.sh"
            setup.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
            setup.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")  # 作業ツリーだけを照合の成功に戻す（ステージし直さない）
            command = [str(repository / "scripts" / "check.sh"), "--staged"] if runner != "pre-commit" else [str(hook)]
            result = subprocess.run(command, cwd=repository, capture_output=True, text=True, env=env, timeout=60)
            output = result.stdout + result.stderr
            if result.returncode != 1 or "tools/versions" not in output or "コミット前の検査を通過" in output:
                failures.append(f"check: {runner} がステージした setup-tools.sh で照合しない（終了コード {result.returncode}）\n"
                                f"{output.strip()}")


def staged_tree_location_case() -> None:
    """check.sh --staged が、ステージした内容を書き出す一時ディレクトリを作業ツリーの中に作ったら（TMPDIR が作業ツリーの中）、
    書き出す前に止めること（--require-clean の専用の作業ツリーと同じ置き場所の判定）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").chmod(0o755)
        stage_tool_stubs(repository)
        (repository / ".gitignore").write_text("/.tools\n/tmp\n", encoding="utf-8")
        (repository / "Sample.swift").write_text("let value = 1\n", encoding="utf-8")
        (repository / "tmp").mkdir()
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, capture_output=True,
                                text=True, env=dict(env, TMPDIR=str(repository / "tmp")), timeout=60)
        output = result.stdout + result.stderr
        if result.returncode != 1 or "ステージした内容の一時の置き場所" not in output:
            failures.append(f"check: --staged が作業ツリーの中の一時ディレクトリに書き出す（終了コード {result.returncode}）\n"
                            f"{output.strip()}")


def staged_symlink_case() -> None:
    """check.sh --staged が、種類を変えて（通常のファイル → シンボリックリンク）ステージした Swift のパスを、
    整形・静的解析に渡す前に、インデックスで通常のファイルでないとして失敗にすること（手順を始める前に止まる）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").chmod(0o755)
        (repository / "Sample.swift").write_text("let value = 1\n", encoding="utf-8")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(*arguments: str) -> None:
            subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)

        git("init", "-q", "--template=", "-b", "main")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        (repository / "Sample.swift").unlink()
        (repository / "Sample.swift").symlink_to("scripts/check.sh")
        git("add", "Sample.swift")
        # 一時のファイルの場所を記録する mktemp（macOS の mktemp は TMPDIR を使わない環境があるので、明示した場所に作らせる）
        temporary, shims = Path(directory) / "temporary", Path(directory) / "shims"
        temporary.mkdir()
        shims.mkdir()
        (shims / "mktemp").write_text('#!/bin/sh\nexec /usr/bin/mktemp "$@" "$MINUTES_TEST_TMP/XXXXXXXX"\n', encoding="utf-8")
        (shims / "mktemp").chmod(0o755)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, capture_output=True,
                                text=True, timeout=60,
                                env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_TMP=str(temporary)))
        output = result.stdout + result.stderr
        left = sorted(path.name for path in temporary.iterdir())
        if left:
            failures.append(f"check: 手順を始める前に止めたとき、一時のファイルが残る（{left}）")
        if result.returncode != 1 or "Sample.swift はインデックスで通常のファイルでない" not in output or "== 手順" in output:
            failures.append(f"check: 種類を変えてステージした Swift のリンクを止めない（終了コード {result.returncode}）\n{output.strip()}")


# シミュレータの一覧の試験に使う UDID（UUID の形）
SIM_X = "11111111-2222-4333-8444-555555555555"
SIM_Y = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"


def lint_rules_config_cases() -> None:
    """test-lint-rules.py が規則の excluded を属性の階層で読み、読めない字下げの属性・項目を黙って読み落とさないこと。"""
    global count
    spec = importlib.util.spec_from_file_location("test_lint_rules", SCRIPTS / "test-lint-rules.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for label, text, want_rules, want_error in (
        ("字下げ 4 の excluded", "custom_rules:\n  rule_a:\n    regex: x\n    excluded:\n      - a\n", {"rule_a": True}, ""),
        ("字下げ 6 の属性", "custom_rules:\n  rule_a:\n      regex: x\n      excluded:\n        - a\n", None, "字下げ 4 で書く"),
        ("字下げ 4 の excluded の項目", "custom_rules:\n  rule_a:\n    excluded:\n    - a\n", None, "字下げ 6 で書く"),
        ("同じ行の excluded（文字列）", "custom_rules:\n  rule_a:\n    regex: x\n    excluded: '.*/A/.*'\n", None, "同じ行に書かない"),
        ("同じ行の excluded（流れの形）", "custom_rules:\n  rule_a:\n    regex: x\n    excluded: [a, b]\n", None, "同じ行に書かない"),
        ("複数行の流れの形", "custom_rules:\n  rule_a:\n    excluded:\n      [a,\n      b]\n", None, "「- 値」の並びで書く"),
        ("項目の流れの形", "custom_rules:\n  rule_a:\n    excluded:\n      - [a, b]\n", None, "「- 値」の並びで書く"),
        ("項目の値の続き", "custom_rules:\n  rule_a:\n    excluded:\n      - a\n        b\n", None, "「- 値」の並びで書く"),
        ("次の行の文字列", "custom_rules:\n  rule_a:\n    excluded:\n      a\n", None, "「- 値」の並びで書く"),
        ("空白のない項目", "custom_rules:\n  rule_a:\n    excluded:\n      -a\n", None, "「- 値」の並びで書く"),
        ("空の項目", "custom_rules:\n  rule_a:\n    excluded:\n      -\n        a\n", None, "「- 値」の並びで書く"),
        ("ブロックの文字列の項目", "custom_rules:\n  rule_a:\n    excluded:\n      - |\n        a\n", None, "「- 値」の並びで書く"),
        ("項目の間のコメントと次の属性", "custom_rules:\n  rule_a:\n    excluded:\n      - 'a'\n        # 注\n      - b\n    match_kinds:\n      - x\n",
         {"rule_a": True}, ""),
        ("excluded の行のコメント", "custom_rules:\n  rule_a:\n    excluded:  # 注\n      - a\n", {"rule_a": True}, ""),
        ("字下げ 3 の excluded", "custom_rules:\n  rule_a:\n    regex: x\n   excluded:\n      - a\n", None, "属性の階層と違う"),
    ):
        count += 1
        errors: List[str] = []
        rules = module.custom_rules(text, errors)
        if want_error and not any(want_error in message for message in errors):
            failures.append(f"lint-rules: {label}: 読めない字下げを報告しない（{errors}・{rules}）")
        if not want_error and (errors or rules != want_rules):
            failures.append(f"lint-rules: {label}: 期待と違う（{errors}・{rules}）")
    # 対照の設定は、その規則の excluded だけを除き、ほかの規則の excluded と属性は字面のまま残す
    count += 1
    text = ("strict: true\nexcluded:\n  - x\ncustom_rules:\n  rule_a:\n    regex: a\n    excluded:  # 注\n      - '.*/A\\.swift\\z'\n"
            "      # 注\n      - b\n    severity: error\n\n  rule_b:\n    excluded:\n      - c\n    regex: b\n")
    want = text.replace("    excluded:  # 注\n      - '.*/A\\.swift\\z'\n      # 注\n      - b\n", "")
    if module.without_excluded(text, "rule_a") != want:
        failures.append(f"lint-rules: 対照の設定が違う\n{module.without_excluded(text, 'rule_a')}")
    # ほかの設定への参照は、対照の設定（一時ディレクトリに置く）から同じものを読めないので、試験の前に拒否する
    for label, text, want in (
        ("parent_config", "strict: true\nparent_config: base.yml\n", ["parent_config"]),
        ("引用符の child_config", "\"child_config\" : child.yml\n", ["child_config"]),
        ("参照のない設定", "strict: true\n# parent_config: base.yml\ncustom_rules:\n  rule_a:\n    message: parent_config\n", []),
    ):
        count += 1
        if module.config_references(text) != want:
            failures.append(f"lint-rules: {label}: 設定への参照の判定が違う（{module.config_references(text)}）")
    # 違反しない例外の例は、例外を除いても検出されないので失敗にする。元のパスのまま調べる
    count += 1
    errors = []
    sample = module.FIXTURES / "no_direct_logger" / "ok.swift"
    allowed = module.FIXTURES / "no_direct_logger" / "allowed" / "Logging" / "AppLogger.swift"
    checked: List[List[Path]] = []
    original_run = module.run_swiftlint
    module.run_swiftlint = lambda paths, config=None: checked.append(list(paths)) or original_run(paths, config)
    module.check_allowed_controls([(allowed, "no_direct_logger", set()), (sample, "no_direct_logger", set())],
                                  {"no_direct_logger": True}, errors)
    if errors or checked != [[allowed]]:
        failures.append(f"lint-rules: 例外の例の対照が元のパスで検出されない（{errors}・{checked}）")
    # 検出の行番号は、試験で見ているファイルと規則の組だけを読む。行番号のない検出は、0 行目とも「ない」とも数えずに報告する
    watched = module.FIXTURES / "no_direct_logger" / "violation.swift"
    for label, items, want_lines, want_errors in (
        ("見ている組", [{"file": str(watched), "rule_id": "no_direct_logger", "line": 3}], {3}, 0),
        ("見ている組の null", [{"file": str(watched), "rule_id": "no_direct_logger", "line": None}], set(), 1),
        ("見ている組の欠落", [{"file": str(watched), "rule_id": "no_direct_logger"}], set(), 1),
        ("見ている組の真偽値", [{"file": str(watched), "rule_id": "no_direct_logger", "line": True}], set(), 1),
        ("見ていない規則の null", [{"file": str(watched), "rule_id": "other", "line": None}], set(), 0),
        ("見ていないファイルの null", [{"file": "/x.swift", "rule_id": "no_direct_logger", "line": None}], set(), 0),
    ):
        count += 1
        line_errors: List[str] = []
        lines = module.reported_lines(items, [(watched, "no_direct_logger", {3})], line_errors)
        if lines.get((os.path.realpath(watched), "no_direct_logger"), set()) != want_lines or len(line_errors) != want_errors:
            failures.append(f"lint-rules: 検出の行番号（{label}）の扱いが違う（{lines}・{line_errors}）")
    # SwiftLint がたどるディレクトリのリンクが走査の範囲の外を指せば報告し、中を指すリンク・飛ばす場所のリンクは認める
    original_root = module.ROOT
    for label, link, target, want in (("外", "Sources", "external", 1), ("飛ばす場所の中", "Sources", "root/.build/inner", 1),
                                      ("走査の範囲の中", "Sources", "root/App", 0), ("循環する中", "App/loop", "root", 0),
                                      ("飛ばす場所のリンク", ".tools", "external", 0)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(os.path.realpath(directory))
            for path in ("root/App", "root/.build/inner", "external"):
                (base / path).mkdir(parents=True)
                (base / path / ".swiftlint.yml").write_text("disabled_rules:\n  - no_print_logging\n", encoding="utf-8")
            (base / "root" / link).symlink_to(base / target)
            module.ROOT = base / "root"
            link_errors: List[str] = []
            listed = module.nested_configs_in_worktree(link_errors)
        if listed != ["App/.swiftlint.yml"] or len(link_errors) != want or (want and link not in link_errors[0]):
            failures.append(f"lint-rules: {label}を指すディレクトリのリンクの扱いが違う（{listed}・{link_errors}）")
    for label, target, want in (("外", "external/A.swift", 1), ("飛ばす場所の中", "root/.build/A.swift", 1), ("走査の範囲の中", "root/App/A.swift", 0)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(os.path.realpath(directory))
            (base / "root" / "Sources").mkdir(parents=True)
            (base / target).parent.mkdir(parents=True, exist_ok=True)
            (base / target).write_text("", encoding="utf-8")
            (base / "root" / "Sources" / "Linked.swift").symlink_to(base / target)
            module.ROOT = base / "root"
            link_errors = []
            module.nested_configs_in_worktree(link_errors)
        if len(link_errors) != want or (want and "Sources/Linked.swift" not in link_errors[0]):
            failures.append(f"lint-rules: {label}を指す Swift のファイルのリンクの扱いが違う（{link_errors}）")
    module.ROOT = original_root
    # 参照のある設定では、SwiftLint を実行する前に終了コード 2 で止まる
    count += 1
    executed: List[bool] = []
    module.run_swiftlint = lambda paths, config=None: executed.append(True) or []
    original_read = module.read_config
    original_argv, sys.argv = sys.argv, ["test-lint-rules.py"]
    # custom_rules の読めない書き方でも、対照の設定で SwiftLint を実行する前に止まる
    count += 1
    # 実際の設定には足さない（custom_rules の後に最上位の鍵があると、足した rule_z が custom_rules の外になる）
    module.read_config = lambda path: b"custom_rules:\n  rule_z:\n    excluded:\n      [a,\n      b]\n"
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = module.main()
    if code != 1 or executed or "「- 値」の並びで書く" not in printed.getvalue():
        failures.append(f"lint-rules: 読めない excluded で止まらない（終了コード {code}・実行 {executed}）")
    module.read_config = lambda path: original_read(path) + b"\nparent_config: base.yml\n"
    with contextlib.redirect_stderr(io.StringIO()) as captured:
        code = module.main()
    sys.argv = original_argv
    module.read_config = original_read
    if code != 2 or executed or "ほかの設定への参照" not in captured.getvalue():
        failures.append(f"lint-rules: ほかの設定への参照で止まらない（終了コード {code}・実行 {executed}）")
    count += 1
    module.run_swiftlint = lambda paths, config=None: []
    module.check_allowed_controls([(allowed, "no_direct_logger", set())], {"no_direct_logger": True}, errors)
    if len(errors) != 1:
        failures.append(f"lint-rules: 検出されない対照を失敗にしない（{errors}）")


def simulator_cases() -> None:
    """find-simulator.py が、形の違う一覧を取得の失敗（終了コード 2）とし、正しい空の一覧を候補なし（1）とすること。"""
    global count
    runtime = {"identifier": "r", "version": "27.2", "platform": "iOS", "isAvailable": True}
    sim_cases_extra = [
        # UDID は UUID の形であること（空でない文字列でも、UUID でなければ取得の失敗）
        # 使える iOS のランタイムの識別子の重複（後の版で前の版を隠すと、最低 OS の判定を誤って候補なしになる）
        ("runtimes の識別子の重複", {"runtimes": [runtime, dict(runtime, version="26.0")], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 2),
        ("使えないランタイムとの識別子の重複は認める", {"runtimes": [runtime, dict(runtime, isAvailable=False)], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 0),
        ("udid が UUID でない", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": "x", "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 2),
    ]
    for label, listing, want_code in (
        ("runtimes の項目が辞書でない", {"runtimes": ["x"], "devices": {}}, 2),
        ("devices の項目が辞書でない", {"runtimes": [runtime], "devices": {"r": ["x"]}}, 2),
        ("devices の値が配列でない", {"runtimes": [runtime], "devices": {"r": "x"}}, 2),
        ("空の一覧は候補なし", {"runtimes": [], "devices": {}}, 1),
        # 重複した鍵（後の空の devices が前の候補を隠す）は、候補なしではなく取得の失敗
        ("重複した鍵", '{"runtimes": ' + json.dumps([runtime]) + ', "devices": {"r": [{"name": "iPhone A", "udid": "' + SIM_X
         + '", "isAvailable": true, "deviceTypeIdentifier": "iPhone"}]}, "devices": {}}', 2),
        # 深い入れ子の一覧は json の読み込みが RecursionError になる。取得の失敗（2）として報告する（文字列はそのまま一覧として書く）
        ("深い入れ子の一覧", "[" * 100000 + "]" * 100000, 2),
        *sim_cases_extra,
        # 使えるかの値は真偽値であること（文字列の "false" を真と取り違えない）
        ("runtimes の isAvailable が文字列", {"runtimes": [dict(runtime, isAvailable="false")], "devices": {}}, 2),
        ("devices の isAvailable が数値", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": 1, "deviceTypeIdentifier": "iPhone"}]}}, 2),
        ("使えない機種は候補にしない", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_Y, "isAvailable": False, "deviceTypeIdentifier": "iPhone"},
            {"name": "iPhone B", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 0),
        # 版が文字列でないランタイムは取得の失敗（split の例外で止まらない）
        ("runtimes の version が数値", {"runtimes": [dict(runtime, version=27.2)], "devices": {}}, 2),
        # 27.2 と 27.2.0 は同じ版として最低 OS を満たす
        ("27.2.0 は 27.2 以上", {"runtimes": [dict(runtime, version="27.2.0")], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 0),
        # 版の各要素は ASCII の数字だけ（int が読める符号・空白・下線・全角数字の版を、正しい版として扱わない）
        *[(f"runtimes の version が {value!r}", {"runtimes": [dict(runtime, version=value)], "devices": {}}, 2)
          for value in ("+27.2", "27.-2", "27. 2", "2_7.2", "２７.2", "27.")],
        ("udid が文字列でない", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": 1, "isAvailable": True, "deviceTypeIdentifier": "iPhone"},
            {"name": "iPhone B", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 2),
        # 機種の識別子が文字列でない一覧は取得の失敗（「iPhone」を含む配列を iPhone と取り違えない）
        ("deviceTypeIdentifier が配列", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": ["iPhone"]}]}}, 2),
        ("使える機種の deviceTypeIdentifier がない", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True}]}}, 2),
        ("使える機種の deviceTypeIdentifier が空", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": ""}]}}, 2),
        ("使えない機種の deviceTypeIdentifier はなくてよい", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_Y, "isAvailable": False},
            {"name": "iPhone B", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 0),
        ("iPad だけの一覧は候補なし", {"runtimes": [runtime], "devices": {"r": [
            {"name": "iPad A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPad"}]}}, 1),
        # ランタイムの platform は空でない文字列であること（形の違う項目を、iOS でないとして読み飛ばして候補なしにしない）
        ("runtimes の platform がない", {"runtimes": [{k: v for k, v in runtime.items() if k != "platform"}], "devices": {}}, 2),
        ("runtimes の platform が数値", {"runtimes": [dict(runtime, platform=1)], "devices": {}}, 2),
        ("runtimes の platform が空", {"runtimes": [dict(runtime, platform="")], "devices": {}}, 2),
        # 使える iOS のランタイムの identifier は空でない文字列であること（null・空・数値の識別子で候補なしにしない）
        ("使える iOS のランタイムの identifier が null", {"runtimes": [dict(runtime, identifier=None)], "devices": {}}, 2),
        ("使える iOS のランタイムの identifier が空", {"runtimes": [dict(runtime, identifier="")], "devices": {}}, 2),
        ("使える iOS のランタイムの identifier が数値", {"runtimes": [dict(runtime, identifier=1)], "devices": {}}, 2),
        # 正しい形の iOS 以外のランタイム（watchOS）は、これまでどおり読み飛ばす
        ("watchOS だけの一覧は候補なし", {"runtimes": [dict(runtime, platform="watchOS")], "devices": {"r": [
            {"name": "iPhone A", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 1),
        ("name が文字列でない", {"runtimes": [runtime], "devices": {"r": [
            {"name": None, "udid": SIM_Y, "isAvailable": True, "deviceTypeIdentifier": "iPhone"},
            {"name": "iPhone B", "udid": SIM_X, "isAvailable": True, "deviceTypeIdentifier": "iPhone"}]}}, 2),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "listing.json").write_text(listing if isinstance(listing, str) else json.dumps(listing),
                                                           encoding="utf-8")
            stub = Path(directory) / "xcrun"
            # 一覧は実行時に自身の位置から読む（一時ディレクトリのパスをシェルの本文に埋め込まない）
            stub.write_text('#!/bin/sh\ncat "$(dirname "$0")/listing.json"\n', encoding="utf-8")
            stub.chmod(0o755)
            result = subprocess.run([str(SCRIPTS / "find-simulator.py")], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=f"{directory}:{os.environ.get('PATH', '')}"))
            if result.returncode != want_code:
                failures.append(f"simulator: {label}: 終了コードが {result.returncode}（期待 {want_code}）\n{result.stderr.strip()}")
            elif want_code == 0 and result.stdout != f"{SIM_X}\n":
                # 成功の場合は、選んだ機種の UDID（試験用の一覧の SIM_X）だけを出力すること
                failures.append(f"simulator: {label}: 出力が {result.stdout!r}（期待 {SIM_X!r}）")


def simulator_stderr_case() -> None:
    """find-simulator.py が、xcrun の失敗の理由（標準エラー）を標準エラーに示し、標準出力には何も出さないこと。"""
    global count
    for detail in ("simctl is broken", ""):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / "xcrun"
            stub.write_text(f"#!/bin/sh\nprintf '%s' '{detail}' >&2\necho partial\nexit 72\n", encoding="utf-8")
            stub.chmod(0o755)
            result = subprocess.run([str(SCRIPTS / "find-simulator.py")], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=f"{directory}:{os.environ.get('PATH', '')}"))
            lines = result.stderr.splitlines()
            if (result.returncode != 2 or result.stdout or not lines or "シミュレータの一覧を取得できない" not in lines[0]
                    or lines[1:] != ([detail] if detail else [])):
                failures.append(f"simulator: xcrun の失敗（標準エラー {detail!r}）の報告が違う（終了コード {result.returncode}・"
                                f"{result.stdout!r}）\n{result.stderr.strip()}")


def simulator_timeout_case() -> None:
    """find-simulator.py が、上限（--timeout）を過ぎても終わらない一覧の取得を止め、取得の失敗（2）とし、標準出力に何も出さないこと。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        stub = Path(directory) / "xcrun"
        stub.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        stub.chmod(0o755)
        try:
            result = subprocess.run([str(SCRIPTS / "find-simulator.py"), "--timeout", "1"], capture_output=True, text=True, timeout=20,
                                    env=dict(os.environ, PATH=f"{directory}:{os.environ.get('PATH', '')}"))
        except subprocess.TimeoutExpired:
            failures.append("simulator: 上限（--timeout 1）を過ぎた一覧の取得を止めず、20 秒で終わらなかった")
            return
        if result.returncode != 2 or result.stdout or "シミュレータの一覧を取得できない" not in result.stderr:
            failures.append(f"simulator: 上限を過ぎた一覧の取得を失敗にしない（終了コード {result.returncode}・{result.stdout!r}）\n"
                            f"{result.stderr.strip()}")


def simulator_developer_dir_case() -> None:
    """find-simulator.py が、DEVELOPER_DIR がない・空なら check.sh と同じ既定を使い、利用者の指定は変えないこと。"""
    global count
    found = re.search(r'env\["DEVELOPER_DIR"\] = env\.get\("DEVELOPER_DIR"\) or "(.+)"', (SCRIPTS / "find-simulator.py").read_text(encoding="utf-8"))
    default = found.group(1) if found else "/Applications/Xcode.app/Contents/Developer"
    for value, want in ((None, default), ("", default), ("/custom", "/custom")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            stub, out = Path(directory) / "xcrun", Path(directory) / "recorded"
            stub.write_text('#!/bin/sh\nprintf %s "${DEVELOPER_DIR-unset}" > "$MINUTES_TEST_OUT"\necho \'{"runtimes": [], "devices": {}}\'\n',
                            encoding="utf-8")
            stub.chmod(0o755)
            env = {key: item for key, item in os.environ.items() if key != "DEVELOPER_DIR"}
            env.update(PATH=f"{directory}:{os.environ['PATH']}", MINUTES_TEST_OUT=str(out))
            if value is not None:
                env["DEVELOPER_DIR"] = value
            # /usr/bin/python3 は Xcode への中継で DEVELOPER_DIR を読む（不正な値では起動しない）ので、実体の python で起動する
            result = subprocess.run([sys.executable, str(SCRIPTS / "find-simulator.py")], capture_output=True, text=True, env=env)
            # 代わりの xcrun が返す一覧は正しいが空なので、使える機種がない（終了コード 1）
            if result.returncode != 1:
                failures.append(f"simulator: DEVELOPER_DIR が {value!r} の空の一覧の終了コードが {result.returncode}（期待 1）\n"
                                f"{result.stderr.strip()}")
            recorded = out.read_text(encoding="utf-8") if out.is_file() else None
            if recorded != want:
                failures.append(f"simulator: DEVELOPER_DIR が {value!r} のとき {recorded!r} を使う（期待 {want!r}）")
        # check.sh は、代わりの xcodebuild が受け取る値で確かめる（代入の書き方に左右されない）
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            repository, shims, out = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "recorded"
            (repository / "scripts").mkdir(parents=True)
            shims.mkdir()
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "check.sh").chmod(0o755)
            env = {key: item for key, item in isolated_git_env().items() if key != "DEVELOPER_DIR"}
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", *arguments], env=env, check=True, capture_output=True)
            # /usr/bin/git も Xcode への中継で DEVELOPER_DIR を読むので、git には渡さない
            for name, text in (("xcodebuild", 'printf %s "${DEVELOPER_DIR-unset}" > "$MINUTES_TEST_OUT"'),
                               ("git", 'unset DEVELOPER_DIR\nexec "$MINUTES_TEST_GIT" "$@"')):
                (shims / name).write_text(f"#!/bin/sh\n{text}\n", encoding="utf-8")
                (shims / name).chmod(0o755)
            env.update(PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_OUT=str(out), MINUTES_TEST_GIT=shutil.which("git") or "/usr/bin/git")
            if value is not None:
                env["DEVELOPER_DIR"] = value
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "6"], cwd=repository, env=env,
                                    capture_output=True, text=True, timeout=60)
            recorded = out.read_text(encoding="utf-8") if out.is_file() else None
            if result.returncode != 0 or recorded != want:
                failures.append(f"check: DEVELOPER_DIR が {value!r} のとき {recorded!r} を使う（期待 {want!r}・終了コード {result.returncode}）\n"
                                f"{result.stderr.strip()}")


def verify_lock_case(setup: str, versions: Path, dest: Path, stub: Path) -> None:
    """setup-tools.sh --verify が導入のロックを待ち、ロックを取ってから照合し（照合の間ロックを持つ）、終えたら外すこと。

    stub は照合が実行する gh の代わり。版を表示する前に、その時点で導入のロックがあるかを記録させる（待った後に
    ロックを取らずに照合する実装を見逃さない）。
    """
    global count
    count += 1
    import time
    lock = dest / ".install.lock"
    seen = dest.parent / "lock-seen"
    original_stub = stub.read_text(encoding="utf-8")
    shebang, _, body = original_stub.partition("\n")
    stub.write_text(f'{shebang}\nif [ -d "$MINUTES_TEST_LOCK" ]; then echo held; else echo missing; fi >> "$MINUTES_TEST_SEEN"\n'
                    f"{body}", encoding="utf-8")
    lock.mkdir()
    import queue
    import threading
    process = subprocess.Popen([setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               env=dict(os.environ, MINUTES_TEST_LOCK=str(lock), MINUTES_TEST_SEEN=str(seen)))
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    collected: List[str] = []
    try:
        # 照合がロックの取得を試みて競合を観測したこと（待機の表示）を、時間の上限つきで待つ。まだ終わっていないこと
        # （poll() が None）だけでは、ロックを試す前の段階にいるのかを区別できないので、競合の根拠にしない
        waiting = False
        deadline = time.monotonic() + 15
        while not waiting and time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                break
            collected.append(line)
            waiting = "待機:" in line
        waiting = waiting and process.poll() is None
        # 試験のロックを外した時点を記録に残す（外す前の gh の実行は、試験のロックを見て held と記録しうるので数えない）
        with seen.open("a", encoding="utf-8") as handle:
            handle.write("released\n")
        lock.rmdir()
        process.wait(timeout=30)
        thread.join(timeout=30)
        while True:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                collected.append(line)
        output = "".join(collected)
        # 後始末（finally）の前に、照合の終了時点でロックが残っていないことを控える（後始末で消えた残骸を見逃さない）
        leftover = lock.exists()
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()  # 出力は読み取りのスレッドが読むので communicate() は使わない
        failures.append("versions: ロックが外れた後も照合が終わらない")
        return
    finally:
        if lock.exists():
            lock.rmdir()
        stub.write_text(original_stub, encoding="utf-8")
    observed = seen.read_text(encoding="utf-8").split() if seen.is_file() else []
    if not waiting or process.returncode != 0 or leftover:
        failures.append(f"versions: 照合が導入のロックを待たない、またはロックの後に通らない（待った {waiting}・"
                        f"終了コード {process.returncode}）\n{output.strip()}")
    elif observed[:1] != ["released"] or len(observed) < 2 or any(state != "held" for state in observed[1:]):
        # 照合（gh の版の表示）は試験のロックが外れた後にだけ行い、その間は照合の実行が自分で取ったロックを持っていること
        failures.append(f"versions: 照合の間に導入のロックを持っていない（gh の実行時の記録 {observed}）")


# 開発ツールの写しの時間の上限（秒）。snapshot_tools_case の join の上限（60 秒）より短くする
SNAPSHOT_TIMEOUT = 45


def copy_tools_script() -> str:
    """check.sh の copy_tools と、それが使う take_lock をそのまま取り出して実行するスクリプト。"""
    text = (SCRIPTS / "check.sh").read_text(encoding="utf-8")
    found = [re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL) for name in ("take_lock", "copy_tools")]
    assert all(found), "check.sh に take_lock・copy_tools がない"
    return ('set -euo pipefail\nTOOLS_LOCK=""\nfail() { echo "NG: $*" >&2; exit 1; }\n'
            'trap \'status=$?; if [ -n "$TOOLS_LOCK" ]; then rmdir "$TOOLS_LOCK" || true; fi; exit "$status"\' EXIT\n'
            "trap 'exit 130' INT TERM\n" + "".join(match.group(0) for match in found if match) + 'copy_tools "$1" "$2"\n')


# 上限を過ぎた写しに TERM を送ってから、強制終了するまでの猶予（秒）
SNAPSHOT_GRACE = 10


def snapshot_tools(source: Path, dest: Path, timeout: float = SNAPSHOT_TIMEOUT, env: Optional[Dict[str, str]] = None) -> None:
    """開発ツールを、check.sh の copy_tools（導入のロックを取って写し、外す）そのもので写す。並行の導入の途中を写さない。

    上限を過ぎたら、子の cp なども含むプロセスのグループの全体を止めて回収する（写しを続けさせない）。まず TERM を送り、写しの
    EXIT の trap が自分のロックを外すのを待つ。猶予を過ぎても終わらなければ強制終了する（このとき残ったロックは、持ち主を
    確かめられないので消さない）。
    """
    process = subprocess.Popen(["/bin/bash", "-c", copy_tools_script(), "copy-tools", str(source), str(dest)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, env=env)
    try:
        _, stderr = process.communicate(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=SNAPSHOT_GRACE)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    if process.returncode != 0:
        raise RuntimeError(f"開発ツールを写せない: {stderr.strip()}")


def process_gone(pid: int, timeout: float = 5) -> bool:
    """プロセスがなくなったか。終わりと回収を短い間だけ待つ。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def write_unlock_shim(shims: Path) -> None:
    """rmdir の代わり: 導入のロックだけ、MINUTES_TEST_UNLOCK の種類に応じて呼び出し元へ TERM を送る（ほかは実際の rmdir）。

    before = 外す前に、呼び出し元と自身（端末の中断と同じくグループの全体）へ送る / after = 外した後に送る / successor = 外した後に別の導入がロックを取り直してから送る / fail = 最初の 1 回は外せない。
    """
    (shims / "rmdir").write_text(
        '#!/bin/bash\ncase "$1" in *.install.lock) ;; *) exec /bin/rmdir "$@" ;; esac\n'
        'if [ -e "$MINUTES_TEST_SENT" ]; then exec /bin/rmdir "$@"; fi\ntouch "$MINUTES_TEST_SENT"\n'
        'case "$MINUTES_TEST_UNLOCK" in\n'
        '  fail) exit 1 ;;\n'
        '  before) kill -TERM "$PPID" "$$"; sleep 1; exec /bin/rmdir "$@" ;;\n'
        'esac\n/bin/rmdir "$@"; result=$?\n'
        'if [ "$MINUTES_TEST_UNLOCK" = successor ]; then /bin/mkdir "$1"; fi\n'
        'kill -TERM "$PPID"; sleep 1; exit "$result"\n', encoding="utf-8")
    (shims / "rmdir").chmod(0o755)


def install_unlock_case() -> None:
    """pre-push と check.sh の copy_tools が、導入のロックの解放の途中の中断でロックを残さず、取り直された別のロックを消さないこと。
    外せなければ失敗し、持ち主の印を残して終了の後始末がもう一度外すこと。"""
    global count
    for target in ("pre-push", "copy_tools"):
        for kind, want_code, want_left in (("before", 130, False), ("after", 130, False), ("successor", 130, True), ("fail", 1, False)):
            count += 1
            with tempfile.TemporaryDirectory() as directory:
                env = isolated_git_env()
                env.pop("MINUTES_CHECK_ISOLATED", None)
                shims, sent, repository = Path(directory) / "shims", Path(directory) / "sent", Path(directory) / "repository"
                shims.mkdir()
                (repository / "scripts").mkdir(parents=True)
                (repository / ".tools" / "bin").mkdir(parents=True)
                write_unlock_shim(shims)
                env = dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_SENT=str(sent), MINUTES_TEST_UNLOCK=kind,
                           MINUTES_PRE_PUSH_DIR=str(Path(directory) / "base"))
                standard_input = ""
                if target == "pre-push":
                    copy_location_script(repository)
                    (repository / "scripts" / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    (repository / "scripts" / "check.sh").chmod(0o755)
                    (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
                    head = commit_all(repository, env)
                    hook = Path(directory) / "pre-push"
                    hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
                    command = ["/bin/bash", str(hook)]
                    standard_input = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
                else:
                    command = ["/bin/bash", "-c", copy_tools_script(), "copy-tools", str(repository / ".tools"), str(Path(directory) / "dest")]
                result = subprocess.run(command, cwd=repository, input=standard_input, capture_output=True, text=True, timeout=60, env=env)
                left = (repository / ".tools" / ".install.lock").exists()
                others = list((Path(directory) / "base").glob("*.lock"))
                if result.returncode != want_code or not sent.exists() or left != want_left or others:
                    failures.append(f"{target}: 導入のロックの解放（{kind}）の扱いが違う（終了コード {result.returncode}・残ったロック {left}・"
                                    f"作業ツリーのロック {others}）\n{result.stderr.strip()}")


def commit_all(repository: Path, env: Dict[str, str]) -> str:
    """repository を初期化してすべてをコミットし、コミットの ID を返す。"""
    for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                       capture_output=True)
    return subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def coverage_lock_case() -> None:
    """check.sh の手順 7 が、カバレッジのロックの取得の途中の中断で自分のロックを残さず、ほかの実行のロックを消さず、
    中断のない競合では待たずに失敗すること。"""
    global count
    for label, held in (("取得できた", False), ("取得できなかった", True), ("競合", True)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, shims, sent = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "sent"
            package, out = repository / "Packages" / "MinutesKit", Path(directory) / "measured"
            (repository / "scripts").mkdir(parents=True)
            package.mkdir(parents=True)
            shims.mkdir()
            (package / "Package.swift").write_text("", encoding="utf-8")
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for name in ("check.sh", "test-checks.py"):
                (repository / "scripts" / name).chmod(0o755)
            (shims / "swift").write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\nexit 1\n', encoding="utf-8")
            (shims / "swift").chmod(0o755)
            (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
            commit_all(repository, env)
            lock = repository / ".build" / "minutes-coverage.lock"
            if label == "競合":
                lock.mkdir(parents=True)
            else:
                write_lock_shim(shims, "minutes-coverage.lock", held)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True, timeout=60,
                                    env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_SENT=str(sent),
                                             MINUTES_TEST_OUT=str(out)))
            # check.sh の終了処理は中断（130）も 1 にそろえる。中断では競合の案内を出さず、測定に進まないことで区別する
            contended = ("別のカバレッジの測定・集計が実行中" in result.stderr) == (label == "競合")
            if result.returncode != 1 or out.exists() or sent.exists() != (label != "競合") or lock.exists() != held or not contended:
                failures.append(f"check: カバレッジのロックを{label}ときの扱いが違う（終了コード {result.returncode}・"
                                f"残ったロック {lock.exists()}）\n{result.stderr.strip()}")


def staged_symlink_config_case() -> None:
    """check.sh --staged が、シンボリックリンクとしてステージした整形・静的解析の設定を、ツールの実行の前に止めること。"""
    global count
    for name, with_swift in ((".swiftformat", True), (".swiftlint.yml", True), (".swift-version", True), ("Sub/.swiftlint.yml", True),
                             (".swiftlint.yml", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, out = Path(directory) / "repository", Path(directory) / "executed"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            check = repository / "scripts" / "check.sh"
            check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            check.chmod(0o755)
            stage_tool_stubs(repository)
            for tool in ("swiftformat", "swiftlint", "gitleaks"):
                (repository / ".tools" / "bin" / tool).write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\n', encoding="utf-8")
                (repository / ".tools" / "bin" / tool).chmod(0o755)
            if with_swift:
                (repository / "Sample.swift").write_text("let sample = 1\n", encoding="utf-8")
            (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            (repository / name).parent.mkdir(exist_ok=True)
            (repository / name).symlink_to("config")
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
            result = subprocess.run([str(check), "--staged"], capture_output=True, text=True, timeout=60,
                                    env=dict(env, MINUTES_TEST_OUT=str(out)))
            if result.returncode != 1 or out.exists() or "== 手順" in result.stdout or f"{name} はインデックスでシンボリックリンク" not in result.stderr:
                failures.append(f"check: --staged がリンクの設定 {name}（Swift のステージ {with_swift}）を止めない（終了コード {result.returncode}・"
                                f"ツールの実行 {out.exists()}）\n{result.stderr.strip()}")


def staged_pathspec_case() -> None:
    """check.sh --staged が、引き継いだパス指定の変数（GIT_LITERAL_PATHSPECS など）で、検査する対象を変えないこと。"""
    global count
    for variable in ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"):
        for linked in (False, True):
            count += 1
            with tempfile.TemporaryDirectory() as directory:
                env = isolated_git_env()
                env.pop("MINUTES_CHECK_ISOLATED", None)
                repository, out = Path(directory) / "repository", Path(directory) / "recorded"
                (repository / "scripts").mkdir(parents=True)
                (repository / ".tools" / "bin").mkdir(parents=True)
                (repository / "Sub").mkdir()
                check = repository / "scripts" / "check.sh"
                check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
                check.chmod(0o755)
                stage_tool_stubs(repository)
                # 整形の代わり: 渡された引数を記録し、手順 1 で止める
                formatter = repository / ".tools" / "bin" / "swiftformat"
                formatter.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$MINUTES_TEST_OUT"\nexit 1\n', encoding="utf-8")
                formatter.chmod(0o755)
                # 拡張子が大文字のファイルは、変数がなければ選ばない（ICASE で選ばれると対象が変わる）
                for name in ("Sub/Nested.swift", "Sub/Upper.SWIFT"):
                    (repository / name).write_text("let sample = 1\n", encoding="utf-8")
                (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
                if linked:
                    (repository / "Sub" / ".swiftlint.yml").symlink_to("config")
                subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
                subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
                result = subprocess.run([str(check), "--staged"], capture_output=True, text=True, timeout=60,
                                        env=dict(env, MINUTES_TEST_OUT=str(out), **{variable: "1"}))
                recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
                found = ("Sub/.swiftlint.yml はインデックスでシンボリックリンク" in result.stderr if linked
                         else "Nested.swift" in recorded and "Upper.SWIFT" not in recorded and "NG: 手順 1" in result.stderr)
                if result.returncode != 1 or not found:
                    failures.append(f"check: --staged が {variable} で検査する対象を変える（リンクの設定 {linked}・終了コード {result.returncode}・"
                                    f"記録 {recorded!r}）\n{result.stderr.strip()}")


def staged_query_failure_case() -> None:
    """check.sh --staged の手順 2 が、規則の変更の有無の照会（git diff --cached --quiet）の失敗を、変更ありと取り違えずに止まること。"""
    global count
    count += 1
    real = shutil.which("git")
    assert real is not None
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository, shims, out = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "ran"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        shims.mkdir()
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        stage_tool_stubs(repository)
        (repository / "scripts" / "test-lint-rules.py").write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\n', encoding="utf-8")
        for name in ("swiftformat", "swiftlint"):
            (repository / ".tools" / "bin" / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (repository / ".tools" / "bin" / name).chmod(0o755)
        for name in ("check.sh", "test-lint-rules.py"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (shims / "git").write_text(f'#!/bin/bash\ncase " $* " in *" diff --cached --quiet "*) exit 128 ;; esac\nexec {shlex.quote(real)} "$@"\n',
                                   encoding="utf-8")
        (shims / "git").chmod(0o755)
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], capture_output=True, text=True, timeout=60,
                                env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
        if result.returncode != 1 or out.exists() or "終了コード 128" not in result.stderr or "NG: 手順 2" not in result.stderr:
            failures.append(f"check: --staged の照会の失敗で手順 2 に止まらない（終了コード {result.returncode}・試験の実行 {out.exists()}）\n"
                            f"{result.stderr.strip()}")


def snapshot_timeout_case() -> None:
    """snapshot_tools が、上限を過ぎたら子のプロセスも止めて戻ること（ロックを待ち続ける写しを残さない）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        source, dest, shims = Path(directory) / "source", Path(directory) / "dest", Path(directory) / "shims"
        worker = Path(directory) / "worker"  # ロックの待機の sleep の PID（待機の分岐に達した印を兼ねる）
        (source / "bin").mkdir(parents=True)
        (source / ".install.lock").mkdir()
        shims.mkdir()
        (shims / "sleep").write_text('#!/bin/bash\necho "$$" > "$MINUTES_TEST_WORKER"\nexec /bin/sleep 60\n', encoding="utf-8")
        (shims / "sleep").chmod(0o755)
        try:
            snapshot_tools(source, dest, timeout=3, env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}",
                                                             MINUTES_TEST_WORKER=str(worker)))
            stopped = False
        except subprocess.TimeoutExpired:
            stopped = True
        recorded = worker.read_text(encoding="utf-8").strip() if worker.is_file() else ""
        left = not re.fullmatch(r"[1-9]\d*", recorded) or not process_gone(int(recorded))
        if not stopped or left or dest.exists() or not (source / ".install.lock").is_dir():
            failures.append(f"versions: 上限を過ぎた写しを止めない（止めた {stopped}・待機の PID {recorded!r}・残った・記録がない {left}）")
    # ロックを取った後（写しの途中）に上限を過ぎた場合は、写しが自分のロックを外して終わること
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        source, dest, shims = Path(directory) / "source", Path(directory) / "dest", Path(directory) / "shims"
        reached = Path(directory) / "reached"  # ロックを取った後の cp に達した印
        worker = Path(directory) / "worker"  # その cp の PID
        (source / "bin").mkdir(parents=True)
        shims.mkdir()
        (shims / "cp").write_text('#!/bin/bash\necho "$$" > "$MINUTES_TEST_WORKER"\n[ -d "$MINUTES_TEST_SOURCE/.install.lock" ] && touch "$MINUTES_TEST_REACHED"\nexec sleep 60\n',
                                  encoding="utf-8")
        (shims / "cp").chmod(0o755)
        try:
            snapshot_tools(source, dest, timeout=3, env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}",
                                                             MINUTES_TEST_SOURCE=str(source), MINUTES_TEST_REACHED=str(reached),
                                                             MINUTES_TEST_WORKER=str(worker)))
            stopped = False
        except subprocess.TimeoutExpired:
            stopped = True
        recorded = worker.read_text(encoding="utf-8").strip() if worker.is_file() else ""
        # cp は exec で sleep になるので、記録した PID がそのまま残る。終わって回収されるまで短い間だけ待つ
        left = not re.fullmatch(r"[1-9]\d*", recorded) or not process_gone(int(recorded))
        if not stopped or not reached.exists() or left:
            failures.append(f"versions: ロックを取った後に上限を過ぎた写しの扱いが違う（止めた {stopped}・写しに達した {reached.exists()}・"
                            f"cp の PID {recorded!r}・残った・記録がない {left}）")
        if (source / ".install.lock").exists():
            failures.append("versions: ロックを取った後に上限を過ぎた写しが、自分のロックを残した")


def staged_newline_directory_case() -> None:
    """check.sh --staged が、名前が改行で終わるディレクトリのファイルも、同じ相対位置に書き出して検査すること。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository, out = Path(directory) / "repository", Path(directory) / "recorded"
        paths = Path(directory) / "paths"  # 整形・静的解析が受け取った Swift のファイルのパス（NUL 区切り）
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        check = repository / "scripts" / "check.sh"
        check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        stage_tool_stubs(repository)
        (repository / "sub\n").mkdir()
        (repository / "sub\n" / "Sample.swift").write_text("let sample = 1\n", encoding="utf-8")
        (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        # 3 か所の書き出し（整形・静的解析、秘密情報、文書）のそれぞれで、同じ相対位置のファイルを読む
        # 整形・静的解析は、受け取ったパスの引数を記録し、その引数でファイルを読む（check.sh が渡すパスから改行を落とせば、
        # 読めずに失敗し、記録したパスも違う）
        lint_reader = ("#!/bin/sh\nfor arg in \"$@\"; do\n  case \"$arg\" in\n    *.swift) printf '%s\\0' \"$arg\" >> \"$MINUTES_TEST_PATHS\" "
                       "&& cat \"$arg\" >> \"$MINUTES_TEST_OUT\" || exit 1 ;;\n  esac\ndone\n")
        for name in ("swiftformat", "swiftlint"):
            (repository / ".tools" / "bin" / name).write_text(lint_reader, encoding="utf-8")
            (repository / ".tools" / "bin" / name).chmod(0o755)
        # 秘密情報と文書の書き出しは、名前を改行まで正確に指定して読む（glob では、改行を落として書き出した「sub」にも当たる）
        reader = "#!/bin/sh\ncat 'sub\n/Sample.swift' >> \"$MINUTES_TEST_OUT\" || exit 1\n"
        (repository / ".tools" / "bin" / "gitleaks").write_text(reader, encoding="utf-8")
        (repository / ".tools" / "bin" / "gitleaks").chmod(0o755)
        (repository / "scripts" / "verify-docs.py").write_text(reader.replace("\ncat", '\ncd "$2" || exit 1\ncat'), encoding="utf-8")
        for path in (check, repository / "scripts" / "verify-docs.py"):
            path.chmod(0o755)
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(check), "--staged"], capture_output=True, text=True, timeout=60,
                                env=dict(env, MINUTES_TEST_OUT=str(out), MINUTES_TEST_PATHS=str(paths)))
        recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
        received = paths.read_text(encoding="utf-8").split("\0")[:-1] if paths.is_file() else []
        if result.returncode != 0 or recorded != "let sample = 1\n" * 4 or received != ["./sub\n/Sample.swift"] * 2:
            failures.append(f"check: 名前が改行で終わるディレクトリを書き出せない（終了コード {result.returncode}・記録 {recorded!r}・"
                            f"整形・静的解析が受け取ったパス {received!r}）\n{result.stderr.strip()}")


def staged_dash_case() -> None:
    """check.sh --staged が、「-」で始まる名前の Swift のファイルを、整形・静的解析に指定ではなくパスとして渡すこと。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository, out = Path(directory) / "repository", Path(directory) / "recorded"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools" / "bin").mkdir(parents=True)
        check = repository / "scripts" / "check.sh"
        check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        stage_tool_stubs(repository)
        for name in ("-dash.swift", "Plain.swift"):
            (repository / name).write_text("let sample = 1\n", encoding="utf-8")
        (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        for name in ("swiftformat", "swiftlint", "gitleaks"):
            # 受け取った Swift のパスを記録し、開けることを確かめる
            (repository / ".tools" / "bin" / name).write_text(
                f'#!/bin/bash\nfor a in "$@"; do case "$a" in *.swift) [ -f "$a" ] || exit 1; echo "{name} $a" >> "$MINUTES_TEST_OUT" ;; esac; done\n',
                encoding="utf-8")
            (repository / ".tools" / "bin" / name).chmod(0o755)
        (repository / "scripts" / "verify-docs.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for path in (check, repository / "scripts" / "verify-docs.py"):
            path.chmod(0o755)
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "add", "-A"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(check), "--staged"], capture_output=True, text=True, timeout=60,
                                env=dict(env, MINUTES_TEST_OUT=str(out)))
        recorded = out.read_text(encoding="utf-8").split("\n")[:-1] if out.is_file() else []
        want = [f"{tool} ./{name}" for tool in ("swiftformat", "swiftlint") for name in ("-dash.swift", "Plain.swift")]
        if result.returncode != 0 or recorded != want:
            failures.append(f"check: 「-」で始まるファイルを正しく渡さない（終了コード {result.returncode}・記録 {recorded}）\n"
                            f"{result.stderr.strip()}")


def step4_tracked_case() -> None:
    """check.sh の手順 4 が、確かめた専用の作業ツリーの検査でだけ --tracked-only を渡すこと（変数の引き継ぎだけでは渡さない）。"""
    global count
    for label, arguments, inherited, want in (("専用の作業ツリー", ["--require-clean"], "", "--tracked-only\n"), ("通常の実行", [], "", "\n"),
                                              ("変数だけの引き継ぎ", [], "1", "\n")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, out = Path(directory) / "repository", Path(directory) / "recorded"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            check = repository / "scripts" / "check.sh"
            check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            copy_location_script(repository)
            (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (repository / "scripts" / "check-project-settings.py").write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MINUTES_TEST_OUT"\n', encoding="utf-8")
            for path in (repository / "scripts").iterdir():
                path.chmod(0o755)
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            for command in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *command], env=env, check=True,
                               capture_output=True)
            run_env = dict(env, MINUTES_TEST_OUT=str(out))
            if inherited:
                run_env["MINUTES_CHECK_ISOLATED"] = inherited
            result = subprocess.run([str(check), "--steps", "4", *arguments], cwd=repository, capture_output=True, text=True,
                                    timeout=60, env=run_env)
            recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
            if result.returncode != 0 or recorded != want:
                failures.append(f"check: 手順 4 の引数が違う（{label}。終了コード {result.returncode}・記録 {recorded!r}）\n{result.stderr.strip()}")


def pre_push_mark_failure_case() -> None:
    """プッシュ前のフックが、初回の作成で印を書けなかった場合に、この実行が作った登録だと確かめられたときだけ登録を外すこと。

    git の代わりが、worktree add の直後に印の位置へディレクトリを置く（印を書けなくする）。「確かめられない」場合は、加えて
    gitdir の記録を別の場所に書き換え、登録を外さずに止めること。gitdir の記録が相対パス（worktree.useRelativePaths）で
    この置き場所を指す場合は、記録のあるディレクトリから解決して、この実行の登録として外すこと。外した場合は、次の push が作り直して通ること。
    """
    global count
    real_git = shutil.which("git") or "git"
    for label, mode in (("この実行の登録", "own"), ("確かめられない登録", "foreign"), ("相対パスの gitdir の記録", "relative")):
        foreign = mode == "foreign"
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            work, base, shims = Path(directory) / "work", Path(directory) / "base", Path(directory) / "shims"
            hook = Path(directory) / "pre-push"
            hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
            hook.chmod(0o755)
            identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", f"core.hooksPath={os.devnull}"]

            def git(*arguments: str) -> str:
                return subprocess.run(["git", *identity, *arguments], cwd=work, env=env, check=True, capture_output=True,
                                      text=True).stdout.strip()

            work.mkdir()
            git("init", "-q", "--template=", "-b", "main")
            (work / "scripts").mkdir()
            (work / ".tools" / "bin").mkdir(parents=True)
            copy_location_script(work)
            (work / "scripts" / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (work / "scripts" / "check.sh").chmod(0o755)
            (work / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            head = git("rev-parse", "HEAD")
            shims.mkdir()
            rewrite = {"own": "",
                       "foreign": 'printf "%s\\n" /elsewhere/.git > "$private/gitdir"\n',
                       # 管理ディレクトリからの相対パスで、作った置き場所の .git を指す記録に書き換える
                       "relative": '/usr/bin/python3 -c "import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" '
                                   '"$snapshot/.git" "$private" > "$private/gitdir"\n'}[mode]
            (shims / "git").write_text(
                '#!/bin/bash\n"$MINUTES_REAL_GIT" "$@"\nstatus=$?\nprevious=""\nfor argument in "$@"; do\n'
                '  if [ "$previous" = worktree ] && [ "$argument" = add ] && [ "$status" = 0 ]; then\n'
                '    snapshot=${*: -2:1}\n'
                '    private=$("$MINUTES_REAL_GIT" -C "$snapshot" rev-parse --path-format=absolute --git-dir)\n'
                '    mkdir "$private/minutes-pre-push-owner"\n' + rewrite +
                '  fi\n  previous=$argument\ndone\nexit "$status"\n', encoding="utf-8")
            (shims / "git").chmod(0o755)

            def push(shimmed: bool) -> "subprocess.CompletedProcess[str]":
                extra = {"PATH": f"{shims}:{env['PATH']}", "MINUTES_REAL_GIT": real_git} if shimmed else {}
                return subprocess.run([str(hook)], cwd=work, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                      capture_output=True, text=True, timeout=60,
                                      env=dict(env, MINUTES_PRE_PUSH_DIR=str(base), **extra))

            refused = push(True)
            # 登録が残ったかは管理ディレクトリで見る（gitdir の記録を書き換えた場合、一覧は別の場所を示す）
            admin = work / ".git" / "worktrees"
            registered = admin.is_dir() and any(admin.iterdir())
            want = "確かめられないので外さない" if foreign else "作った登録は外した"
            if refused.returncode != 1 or want not in refused.stderr or registered != foreign:
                failures.append(f"pre-push: 印を書けない場合（{label}）の扱いが違う（終了コード {refused.returncode}・登録が残った {registered}）"
                                f"\n{refused.stderr.strip()}")
                continue
            if not foreign:
                renewed = push(False)
                if renewed.returncode != 0:
                    failures.append(f"pre-push: 印を書けずに登録を外した後に作り直せない（終了コード {renewed.returncode}）"
                                    f"\n{renewed.stderr.strip()}")


def pre_push_recheck_case() -> None:
    """プッシュ前のフックが、置き場所を作った後にも判定し直し、作るまでの間に置き場所へ作業ツリーが登録されたら止まること。

    mkdir の代わりが、置き場所を作った直後にそこへ作業ツリーを登録する（最初の判定の後・2 回目の判定の前）。
    """
    global count
    count += 1
    real_mkdir = shutil.which("mkdir") or "/bin/mkdir"
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        work, base, shims = Path(directory) / "work", Path(directory) / "base", Path(directory) / "shims"
        hook = Path(directory) / "pre-push"
        hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
        hook.chmod(0o755)
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(*arguments: str) -> str:
            return subprocess.run(["git", *identity, *arguments], cwd=work, env=env, check=True, capture_output=True,
                                  text=True).stdout.strip()

        work.mkdir()
        git("init", "-q", "--template=", "-b", "main")
        (work / "scripts").mkdir()
        (work / ".tools" / "bin").mkdir(parents=True)
        copy_location_script(work)
        (work / "scripts" / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (work / "scripts" / "check.sh").chmod(0o755)
        (work / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        head = git("rev-parse", "HEAD")
        shims.mkdir()
        (shims / "mkdir").write_text(
            '#!/bin/bash\n"$MINUTES_REAL_MKDIR" "$@" || exit $?\n'
            'if [ "${*: -1}" = "$MINUTES_TEST_BASE" ]; then\n'
            '  git -C "$MINUTES_TEST_WORK" worktree add -q --detach "$MINUTES_TEST_BASE" HEAD >/dev/null 2>&1\nfi\n', encoding="utf-8")
        (shims / "mkdir").chmod(0o755)
        resolved = os.path.realpath(base)
        result = subprocess.run([str(hook)], cwd=work, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                capture_output=True, text=True, timeout=60,
                                env=dict(env, MINUTES_PRE_PUSH_DIR=str(base), PATH=f"{shims}:{env['PATH']}",
                                         MINUTES_REAL_MKDIR=real_mkdir, MINUTES_TEST_BASE=resolved, MINUTES_TEST_WORK=str(work)))
        if result.returncode != 1 or "作業ツリー" not in result.stderr or not (base / ".git").is_file():
            failures.append(f"pre-push: 置き場所を作った後に登録された作業ツリーで止まらない（終了コード {result.returncode}）"
                            f"\n{result.stderr.strip()}")


def pre_push_newline_case() -> None:
    """プッシュ前のフックと check.sh の isolated_checkout が、共通のディレクトリの名前が改行で終わっても、作業ツリーを見分けること。

    名前が改行で終わる bare のリポジトリに登録した作業ツリーから 2 回 push し、2 回目が 1 回目の専用の作業ツリーを再利用する。
    改行のない名前の別のリポジトリも置く（取り違えないこと）。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        bare, work, base = Path(directory) / "bare\n", Path(directory) / "work", Path(directory) / "base"
        hook = Path(directory) / "pre-push"
        hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
        hook.chmod(0o755)
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(cwd: Path, *arguments: str) -> str:
            return subprocess.run(["git", *identity, *arguments], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()

        for path in (bare, Path(directory) / "bare"):
            path.mkdir()
            git(path, "init", "-q", "--bare", "--template=", "-b", "main")
        # 古い git でも動くよう --orphan（2.42 以降）を使わず、空のコミットを作って切り離した HEAD の作業ツリーを登録する
        tree = git(bare, "hash-object", "-w", "-t", "tree", os.devnull)
        git(bare, "worktree", "add", "-q", "--detach", str(work), git(bare, "commit-tree", "-m", "seed", tree))
        (work / "scripts").mkdir()
        (work / ".tools" / "bin").mkdir(parents=True)
        copy_location_script(work)
        (work / "scripts" / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (work / "scripts" / "check.sh").chmod(0o755)
        (work / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "base")
        head = git(work, "rev-parse", "HEAD")
        # 有効な post-checkout のフックが失敗しても、内部の展開（作成と再利用）は動かさない
        (bare / "hooks").mkdir(exist_ok=True)
        (bare / "hooks" / "post-checkout").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (bare / "hooks" / "post-checkout").chmod(0o755)

        def push() -> "subprocess.CompletedProcess[str]":
            return subprocess.run([str(hook)], cwd=work, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                  capture_output=True, text=True, timeout=60, env=dict(env, MINUTES_PRE_PUSH_DIR=str(base)))

        first = push()
        if first.returncode != 0:
            failures.append(f"pre-push: 改行で終わる共通のディレクトリで検査できない（終了コード {first.returncode}）\n{first.stderr.strip()}")
            return
        # 再利用なら残すビルドの成果物の印。作り直した場合の成功と区別する
        sentinel = next(path for path in base.iterdir() if path.is_dir()) / ".build" / "sentinel"
        sentinel.parent.mkdir()
        sentinel.write_text("kept", encoding="utf-8")
        second = push()
        if second.returncode != 0 or not sentinel.is_file():
            failures.append(f"pre-push: 改行で終わる共通のディレクトリで再利用できない（終了コード {second.returncode}・"
                            f"ビルドの成果物が残った {sentinel.is_file()}）\n{second.stderr.strip()}")
            return
        count += 1
        text = (SCRIPTS / "check.sh").read_text(encoding="utf-8")
        found = [re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
                 for name in ("real_directory", "git_path", "isolated_checkout")]
        snapshot = next(path for path in base.iterdir() if path.is_dir())
        script = "set -euo pipefail\nROOT=$PWD\n" + "".join(match.group(0) for match in found if match) + "isolated_checkout\n"
        result = subprocess.run(["/bin/bash", "-c", script], cwd=os.path.realpath(snapshot), env=env, capture_output=True, text=True)
        other = subprocess.run(["/bin/bash", "-c", script], cwd=work, env=env, capture_output=True, text=True)
        if result.returncode != 0 or other.returncode != 1:
            failures.append(f"check: 改行で終わる共通のディレクトリの専用の作業ツリーを見分けない（{result.returncode}・{other.returncode}）\n"
                            f"{result.stderr.strip()}")


def install_lock_case() -> None:
    """setup-tools.sh が、導入のロックの取得の途中の中断で自分のロックを残さず、ほかの実行のロックを消さないこと。"""
    global count
    for label, held in (("取得できた", False), ("取得できなかった", True)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dest, shims, sent, versions = base / "tools", base / "shims", base / "sent", base / "versions"
            dest.mkdir()
            shims.mkdir()
            versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.zip gh {'0' * 64}\n", encoding="utf-8")
            write_lock_shim(shims, ".install.lock", held)
            result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--verify", "--versions", str(versions), "--dest", str(dest),
                                     "--require", "gh"], capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_SENT=str(sent)))
            left = (dest / ".install.lock").exists()
            if result.returncode != 130 or not sent.exists() or left != held:
                failures.append(f"versions: setup-tools.sh がロックを{label}ときの中断の扱いが違う（終了コード {result.returncode}・"
                                f"残ったロック {left}）\n{result.stderr.strip()}")


def linked_bin_case() -> None:
    """setup-tools.sh が、導入先の bin がシンボリックリンクなら、導入もリンクの先も変えずに止まること。導入先自体のリンクは認める。"""
    global count
    import hashlib
    import tarfile
    for label, linked, want in (("bin", "bin", 1), ("導入先", "dest", 0)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary, archive, versions = base / "gh", base / "gh.tar.gz", base / "versions"
            binary.write_text("#!/bin/sh\necho 'gh version 9.9.9 (2000-01-01)'\n", encoding="utf-8")
            binary.chmod(0o755)
            with tarfile.open(archive, "w:gz") as stream:
                stream.add(binary, arcname="gh")
            versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {hashlib.sha256(archive.read_bytes()).hexdigest()}\n",
                                encoding="utf-8")
            real, dest, shared, shims = base / "real", base / "tools", base / "shared", base / "shims"
            for path in (real, shared, shims):
                path.mkdir()
            if linked == "dest":
                dest.symlink_to(real)
            else:
                dest.mkdir()
                (dest / "bin").symlink_to(shared)
                (dest / "gh" / "9.9.9").mkdir(parents=True)
                (dest / "gh" / "9.9.9" / "gh").write_text("old", encoding="utf-8")
            (shims / "curl").write_text('#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVE" "$2"\n',
                                        encoding="utf-8")
            (shims / "curl").chmod(0o755)
            command = [str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive)))
            if linked == "dest" and result.returncode == 0:
                # 導入が成功したら、同じ導入先（リンク）の照合も通ること
                verified = subprocess.run([*command, "--verify"], capture_output=True, text=True, timeout=60)
                if verified.returncode != 0:
                    failures.append(f"versions: 導入先がリンクの導入を照合できない（終了コード {verified.returncode}）\n{verified.stderr.strip()}")
            unchanged = linked == "dest" or (not list(shared.iterdir()) and sorted(path.name for path in (dest / "gh").iterdir()) == ["9.9.9"]
                                             and (dest / "gh" / "9.9.9" / "gh").read_text(encoding="utf-8") == "old")
            if result.returncode != want or not unchanged or (dest / ".install.lock").exists():
                failures.append(f"versions: {label}がリンクの導入の扱いが違う（終了コード {result.returncode}・変わらない {unchanged}）\n"
                                f"{result.stderr.strip()}")


def copy_tools_lock_case() -> None:
    """check.sh の copy_tools が、導入のロックの取得の途中の中断で自分のロックを残さず、ほかの実行のロックを消さないこと。"""
    global count
    for label, held in (("取得できた", False), ("取得できなかった", True)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            source, shims, sent = Path(directory) / "source", Path(directory) / "shims", Path(directory) / "sent"
            (source / "bin").mkdir(parents=True)
            shims.mkdir()
            write_lock_shim(shims, ".install.lock", held)
            result = subprocess.run(["/bin/bash", "-c", copy_tools_script(), "copy-tools", str(source), str(Path(directory) / "dest")],
                                    capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_SENT=str(sent)))
            left = (source / ".install.lock").exists()
            if result.returncode != 130 or not sent.exists() or left != held:
                failures.append(f"check: 導入のロックを{label}ときの中断の扱いが違う（終了コード {result.returncode}・"
                                f"残ったロック {left}）\n{result.stderr.strip()}")


def write_lock_shim(shims: Path, suffix: str, held: bool) -> None:
    """mkdir の代わり: 名前が suffix で終わるロックだけ、実際の mkdir の後（成功・失敗とも）に呼び出し元へ TERM を送る。
    held なら、先にほかの実行のロックを作って失敗させる。送った印のパスは、呼び出し側が MINUTES_TEST_SENT で渡す。"""
    occupy = '/bin/mkdir -p "$1"; ' if held else ""
    (shims / "mkdir").write_text(f'#!/bin/bash\ncase "$1" in\n  *{suffix}) {occupy}/bin/mkdir "$@"; result=$?; touch "$MINUTES_TEST_SENT"; '
                                 'kill -TERM "$PPID"; sleep 1; exit "$result" ;;\nesac\nexec /bin/mkdir "$@"\n', encoding="utf-8")
    (shims / "mkdir").chmod(0o755)


def snapshot_tools_case() -> None:
    """snapshot_tools が、導入のロックが外れるまで待ってから写し、ロックを写さず、写した後に元のロックを外すこと。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        source, dest = Path(directory) / "source", Path(directory) / "dest"
        (source / "bin").mkdir(parents=True)
        lock = source / ".install.lock"
        lock.mkdir()
        errors: List[BaseException] = []
        # mkdir の代わり: 導入のロックの取得を試みた後（失敗した後）に印を置く。結果は実際の mkdir のまま
        shims, attempted = Path(directory) / "shims", Path(directory) / "attempted"
        shims.mkdir()
        (shims / "mkdir").write_text('#!/bin/bash\n/bin/mkdir "$@"; result=$?\n'
                                     'case "$1" in *.install.lock) touch "$MINUTES_TEST_ATTEMPTED" ;; esac\nexit "$result"\n', encoding="utf-8")
        (shims / "mkdir").chmod(0o755)

        def snapshot() -> None:
            # スレッドの例外は呼び出し側に伝わらないので、控えて下で確かめる
            try:
                snapshot_tools(source, dest, env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}",
                                                      MINUTES_TEST_ATTEMPTED=str(attempted)))
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=snapshot)
        thread.start()
        # 写しが実際にロックの取得を試みるまで待つ（試みる前に外すと、競合を確かめたことにならない）
        deadline = time.monotonic() + 15
        while not attempted.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        waited = attempted.exists() and thread.is_alive() and not dest.exists()
        (source / "bin" / "tool").write_text("installed", encoding="utf-8")  # ロックを持つ導入が書き終える
        lock.rmdir()
        thread.join(60)
        if thread.is_alive():
            failures.append("versions: 開発ツールの写しが上限を過ぎても終わらない")
            thread.join()  # 写しの側の上限で必ず終わる。終わってから一時ディレクトリを消す
        if errors:
            failures.append(f"versions: 開発ツールの写しが失敗した: {errors[0]!r}")
        copied = (dest / "bin" / "tool").is_file() and not (dest / ".install.lock").exists()
        if not waited or not copied or lock.exists():
            failures.append(f"versions: 開発ツールの写しが導入のロックに従わない（待った {waited}・写した {copied}・"
                            f"ロックが残った {lock.exists()}）")


def archive_newline_paths_case() -> None:
    """setup-tools.sh が、改行を含むパスの実行ファイルと LICENSE を 1 件として数え、複数・なし・find の失敗を区別すること。"""
    global count
    import hashlib
    import tarfile
    for label, names, broken, want_code, want_text in (
        ("改行を含むパス", ("odd\ndir/gh", "odd\ndir/LICENSE"), "", 0, ""),
        ("複数", ("a/gh", "odd\ndir/gh"), "", 1, "が複数ある"),
        ("なし", ("odd\ndir/other",), "", 1, "実行ファイル gh がない"),
        ("実行ファイルの find の失敗", ("a/gh",), "gh", 1, "調べられない"),
        ("LICENSE の find の失敗", ("a/gh", "a/LICENSE"), "LICENSE", 1, "調べられない"),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary, archive, shims, dest = base / "gh", base / "gh.tar.gz", base / "shims", base / "tools"
            binary.write_text("#!/bin/sh\necho 'gh version 9.9.9 (2000-01-01)'\n", encoding="utf-8")
            binary.chmod(0o755)
            with tarfile.open(archive, "w:gz") as stream:
                for name in names:
                    stream.add(binary, arcname=name)
            versions = base / "versions"
            versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {hashlib.sha256(archive.read_bytes()).hexdigest()}\n", encoding="utf-8")
            shims.mkdir()
            (shims / "curl").write_text('#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVE" "$2"\n', encoding="utf-8")
            # find の代わり: 名前が MINUTES_TEST_BROKEN の検索だけ、途中まで出力してから失敗する
            (shims / "find").write_text('#!/bin/bash\n/usr/bin/find "$@" || exit\nfor word in "$@"; do [ "$word" != "$MINUTES_TEST_BROKEN" ] || exit 1; done\n',
                                        encoding="utf-8")
            for path in shims.iterdir():
                path.chmod(0o755)
            result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                    capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive),
                                             MINUTES_TEST_BROKEN=broken))
            licensed = (dest / "gh" / "9.9.9" / "LICENSE").is_file()
            if result.returncode != want_code or want_text not in result.stderr or licensed != (want_code == 0):
                failures.append(f"versions: 配布ファイルの中の検索（{label}）の扱いが違う（終了コード {result.returncode}・LICENSE {licensed}）\n"
                                f"{(result.stdout + result.stderr).strip()}")


def retired_backup_case() -> None:
    """setup-tools.sh が、リンクの公開の後に退避した旧い導入を消せなくても、警告して新しい導入を残すこと（巻き戻さない）。

    curl の代わりに手元の配布ファイルを渡し、rm の代わりに、退避（.old.）の削除だけを中身の一部を消して失敗させる。
    """
    global count
    count += 1
    import hashlib
    import tarfile
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        binary = base / "gh"
        binary.write_text("#!/bin/sh\necho 'gh version 9.9.9 (2000-01-01)'\n# new\n", encoding="utf-8")
        binary.chmod(0o755)
        archive = base / "gh.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(binary, arcname="gh")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        versions = base / "versions"
        versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {sha}\n", encoding="utf-8")
        dest = base / "tools"
        old = dest / "gh" / "9.9.9"
        old.mkdir(parents=True)
        (old / "gh").write_text("old", encoding="utf-8")
        shims = base / "shims"
        shims.mkdir()
        (shims / "curl").write_text('#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVE" "$2"\n',
                                    encoding="utf-8")
        (shims / "rm").write_text('#!/bin/bash\nfor last in "$@"; do :; done\ncase "$last" in *.old.*) '
                                  'if [ -e "$last" ]; then /bin/rm -f "$last/gh"; exit 1; fi ;; esac\nexec /bin/rm "$@"\n',
                                  encoding="utf-8")
        for path in shims.iterdir():
            path.chmod(0o755)
        result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive)))
        installed = (dest / "gh" / "9.9.9" / "gh")
        kept = installed.is_file() and installed.read_text(encoding="utf-8").endswith("# new\n")
        leftovers = [path.name for path in (dest / "gh").iterdir() if ".old." in path.name]
        link = dest / "bin" / "gh"
        published = link.is_symlink() and os.path.realpath(link) == os.path.realpath(installed)
        if result.returncode != 0 or not kept or not published or len(leftovers) != 1 or "退避した旧い gh の導入を削除できない" not in result.stderr:
            failures.append(f"versions: 退避の削除の失敗で新しい導入を巻き戻した・報告しない（終了コード {result.returncode}・"
                            f"残った新しい導入 {kept}・公開するリンク {published}・退避 {leftovers}）\n{(result.stdout + result.stderr).strip()}")


def publish_interrupted_case() -> None:
    """setup-tools.sh が、公開するリンクの置き換えの前後の中断を、退避の印の更新まで控えること（公開した導入を巻き戻さない）。
    置き換えに失敗した場合は、退避した旧い導入を戻すこと。

    mv の代わりは、公開するリンクの置き換えのときだけ、呼び出し元へ TERM を送る。
    """
    global count
    import hashlib
    import tarfile
    for label, action, want_code, want_new in (
        ("置き換えの後の中断", '/bin/mv "$@"; result=$?; kill -TERM "$PPID"; sleep 1; exit "$result"', 130, True),
        ("置き換えの途中の中断", 'kill -TERM "$PPID" $$; /bin/mv "$@"', 130, True),
        ("置き換えの失敗と中断", 'kill -TERM "$PPID"; exit 1', 1, False),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary = base / "gh"
            binary.write_text("#!/bin/sh\necho 'gh version 9.9.9 (2000-01-01)'\n# new\n", encoding="utf-8")
            binary.chmod(0o755)
            archive = base / "gh.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                stream.add(binary, arcname="gh")
            versions = base / "versions"
            versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {hashlib.sha256(archive.read_bytes()).hexdigest()}\n",
                                encoding="utf-8")
            dest = base / "tools"
            installed = dest / "gh" / "9.9.9" / "gh"
            installed.parent.mkdir(parents=True)
            installed.write_text("old", encoding="utf-8")
            (dest / "bin").mkdir()
            link = dest / "bin" / "gh"
            link.symlink_to("../gh/9.9.9/gh")
            shims, sent = base / "shims", base / "sent"
            shims.mkdir()
            (shims / "curl").write_text('#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVE" "$2"\n',
                                        encoding="utf-8")
            (shims / "mv").write_text('#!/bin/bash\nfor last in "$@"; do :; done\n'
                                      f'if [ "$last" = "$MINUTES_TEST_LINK" ]; then touch "$MINUTES_TEST_SENT"; {action}; '
                                      'else exec /bin/mv "$@"; fi\n', encoding="utf-8")
            for path in shims.iterdir():
                path.chmod(0o755)
            result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                    capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive),
                                             MINUTES_TEST_LINK=str(link), MINUTES_TEST_SENT=str(sent)))
            content = installed.read_text(encoding="utf-8") if installed.is_file() else ""
            new = content.endswith("# new\n")
            published = link.is_symlink() and os.path.realpath(link) == os.path.realpath(installed)
            if (result.returncode != want_code or not sent.exists() or new != want_new or (not want_new and content != "old")
                    or not published or (dest / ".install.lock").exists()):
                failures.append(f"versions: {label}の扱いが違う（終了コード {result.returncode}・新しい導入 {new}・公開するリンク {published}）\n"
                                f"{(result.stdout + result.stderr).strip()}")


def staged_scripts_case() -> None:
    """コミット前のフックが、check.sh とその補助（worktree-location.py）もステージしたものを使い、元のリポジトリを検査すること。

    作業ツリーの check.sh は失敗する内容、worktree-location.py は判定を拒否する内容に変える（ステージし直さない）。
    元のリポジトリの Git・.tools・部分的なコミットの GIT_INDEX_FILE を使うことは、整形の代わりが読んだ内容で確かめる。
    逆に、ステージした補助だけを拒否する内容にすると止まることも確かめる。
    """
    global count
    # 起点のパスの末尾の改行も保つ（名前を改行で終えたリポジトリでも検査できる）
    for label, staged_location, name in (("作業ツリーの版を使わない", "", "repository"),
                                         ("ステージした補助を使う", "import sys\nsys.exit(1)\n", "repository"),
                                         ("改行で終わる起点", "", "repository\n")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / name
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            out = Path(directory) / "recorded"
            check = repository / "scripts" / "check.sh"
            check.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            hook = Path(directory) / "pre-commit"
            hook.write_text((ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8"), encoding="utf-8")
            stage_tool_stubs(repository)
            location = repository / "scripts" / "worktree-location.py"
            valid_location = location.read_text(encoding="utf-8")
            if staged_location:
                location.write_text(staged_location, encoding="utf-8")
            (repository / "scripts" / "verify-docs.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            for tool, body in (("swiftformat", 'for last in "$@"; do :; done\ncat "$last" > "$MINUTES_TEST_OUT"\n'),
                               ("swiftlint", "exit 0\n"), ("gitleaks", "exit 0\n")):
                (repository / ".tools" / "bin" / tool).write_text("#!/bin/sh\n" + body, encoding="utf-8")
                (repository / ".tools" / "bin" / tool).chmod(0o755)
            # 書き出した補助のスクリプトと同じ場所に置かれたモジュールを読み込まないこと（mktemp の代わりで場所を決める）
            shims, temporary, planted = Path(directory) / "shims", Path(directory) / "tmp", Path(directory) / "planted"
            shims.mkdir()
            temporary.mkdir()
            (temporary / "subprocess.py").write_text(f"open({str(planted)!r}, 'w').close()\n", encoding="utf-8")
            (shims / "mktemp").write_text('#!/bin/sh\nif [ $# -gt 0 ]; then exec /usr/bin/mktemp "$@"; fi\n'
                                          'exec /usr/bin/mktemp "$MINUTES_TEST_TEMPORARY/t.XXXXXX"\n', encoding="utf-8")
            (shims / "mktemp").chmod(0o755)
            for path in (check, hook, repository / "scripts" / "verify-docs.py"):
                path.chmod(0o755)
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            # 本来のインデックスには入れず、部分的なコミットの一時のインデックス（相対パス）にだけ入れる
            env["GIT_INDEX_FILE"] = ".git/partial-index"
            (repository / "Sample.swift").write_text("let staged = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repository, env=env, check=True, capture_output=True)
            check.write_text("#!/bin/bash\necho 作業ツリーの check.sh >&2\nexit 1\n", encoding="utf-8")
            location.write_text(valid_location if staged_location else "import sys\nsys.exit(1)\n", encoding="utf-8")
            (repository / "Sample.swift").write_text("let worktree = 1\n", encoding="utf-8")
            result = subprocess.run([str(hook)], cwd=repository, capture_output=True, text=True, timeout=60,
                                    env=dict(env, MINUTES_TEST_OUT=str(out), PATH=f"{shims}:{env['PATH']}",
                                             MINUTES_TEST_TEMPORARY=str(temporary)))
            output = result.stdout + result.stderr
            if planted.exists():
                failures.append(f"check: コミット前のフック（{label}）が一時ディレクトリのモジュールを読み込んだ")
            if staged_location:
                if result.returncode != 1 or "一時の置き場所" not in output or out.exists():
                    failures.append(f"check: コミット前のフック（{label}）が止まらない（終了コード {result.returncode}）\n{output.strip()}")
            elif result.returncode != 0 or not out.is_file() or out.read_bytes() != b"let staged = 1\n":
                failures.append(f"check: コミット前のフック（{label}）がステージした内容で検査しない（終了コード {result.returncode}）\n"
                                f"{output.strip()}")
    check_path = str(SCRIPTS / "check.sh")
    expect("check: --root は --staged 以外で使えない", [check_path, "--root", str(ROOT), "--steps", "10"], 2, "使い方")
    expect("check: 相対パスの --root", [check_path, "--root", ".", "--staged"], 2, "絶対パス")
    expect("check: 起点でない --root", [check_path, "--root", str(SCRIPTS), "--staged"], 1, "起点でない", env=isolated_git_env())


def reinstall_command_case() -> None:
    """コミット前のフックが案内する導入し直す手順が、bash と zsh の両方で、ステージした setup-tools.sh と台帳を使い、
    終了コードを返し、一時ファイルを残さず、インデックスと作業ツリーを変えないこと。"""
    global count
    # partial: 部分的なコミットの一時のインデックスにだけステージした内容がある（手順を実行する時点では消えている）
    for shell, name, partial in (("/bin/bash", "repository", False), ("/bin/zsh", "repository", False),
                                 ("/bin/bash", "repository\n", False), ("/bin/zsh", "repository\n", False),
                                 ("/bin/bash", "repository", True), ("/bin/zsh", "repository", True)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository = Path(directory) / name
            temporary = Path(directory) / "tmp"
            temporary.mkdir()
            (repository / "scripts").mkdir(parents=True)
            (repository / "sub").mkdir()
            (repository / ".tools").mkdir()
            out = Path(directory) / "recorded"
            stage_tool_stubs(repository)
            setup = repository / "scripts" / "setup-tools.sh"
            setup.write_text('#!/bin/bash\n[ "$1" != --verify ] || exit 1\ncat "$2" > "$MINUTES_TEST_OUT"\n'
                             'printf "%s %s %s" "$1" "$3" "$4" >> "$MINUTES_TEST_OUT"\nexit 7\n', encoding="utf-8")
            (repository / "tools" / "versions").write_text("staged\n", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            hook = Path(directory) / "pre-commit"
            hook.write_text((ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8"), encoding="utf-8")
            hook.chmod(0o755)
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            hook_env = dict(env, GIT_INDEX_FILE=".git/partial-index") if partial else env
            subprocess.run(["git", "add", "-A"], cwd=repository, env=hook_env, check=True, capture_output=True)
            if partial:
                # 通常のインデックスには、台帳と導入のスクリプトのどちらにも別の内容を置く（通常のインデックスのパスから読むと、
                # こちらを使ってしまう。導入のスクリプトは別の出力と終了コードを返すので、どちらを使ったかを区別できる）
                (repository / "tools" / "versions").write_text("normal index\n", encoding="utf-8")
                setup.write_text('#!/bin/bash\nprintf "normal index installer" > "$MINUTES_TEST_OUT"\nexit 9\n', encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=repository, env=env, check=True, capture_output=True)
            setup.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            (repository / "tools" / "versions").write_text("worktree\n", encoding="utf-8")
            result = subprocess.run([str(hook)], cwd=repository, capture_output=True, text=True, env=hook_env, timeout=60)
            if partial:
                (repository / ".git" / "partial-index").unlink()
            command = result.stderr.partition("導入し直してからコミットする: ")[2].strip()

            def state() -> Tuple[str, str, bytes, bytes]:
                # 既にステージした内容と違う作業ツリーの変更は git status の表示に現れないので、作業ツリーの内容も比べる
                shown = [subprocess.run(["git", "-C", str(repository), *arguments], env=env, check=True, capture_output=True,
                                        text=True).stdout for arguments in (["status", "--porcelain"], ["ls-files", "-s"])]
                return shown[0], shown[1], (repository / "tools" / "versions").read_bytes(), setup.read_bytes()

            before = state()
            # mktemp は macOS の既定では TMPDIR を使わないので、テンプレートを付けて呼ぶ代わりで残ったファイルを調べる
            shims = Path(directory) / "shims"
            shims.mkdir()
            (shims / "mktemp").write_text('#!/bin/sh\nexec /usr/bin/mktemp "$MINUTES_TEST_TEMPORARY/t.XXXXXX"\n', encoding="utf-8")
            # 一時ファイルの削除が失敗を返しても、set -e のシェルでも、導入の終了コードを返して後始末まで進む
            (shims / "rm").write_text('#!/bin/sh\n/bin/rm "$@"\nexit 1\n', encoding="utf-8")
            for name in ("mktemp", "rm"):
                (shims / name).chmod(0o755)
            ran = subprocess.run([shell, "-ec", command], cwd=repository / "sub", capture_output=True, text=True, timeout=60,
                                 env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out),
                                          MINUTES_TEST_TEMPORARY=str(temporary)))
            recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
            want = f"staged\n--versions --dest {os.path.realpath(repository)}/.tools"
            leftovers = [path.name for path in temporary.iterdir()]
            if ran.returncode != 7 or recorded != want or leftovers or state() != before:
                failures.append(f"check: 導入し直す手順（{shell}・{name!r}・部分的 {partial}）が期待と違う（終了コード {ran.returncode}・記録 {recorded!r}・"
                                f"残ったもの {leftovers}）\n{command}\n{ran.stderr.strip()}")


def pre_push_interrupted_case() -> None:
    """プッシュ前のフックが、専用の作業ツリーの展開の途中で強制終了しても、次の検査で展開し直せること（印を展開の前に書く）。

    あわせて、sparse checkout のリポジトリでも全体を展開すること、印のない既存の作業ツリーを変更せずに止めることを確かめる。
    """
    global count
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / "other").mkdir()
        (repository / ".tools" / "bin").mkdir(parents=True)
        base, out, crash = Path(directory) / "base", Path(directory) / "recorded", Path(directory) / "crash"
        reached = Path(directory) / "reached"  # 強制終了の直前に filter が置く印
        hook = Path(directory) / "pre-push"
        hook.write_text((ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").write_text('#!/bin/sh\n'
                                                         'cd "$(dirname "$0")/.." && cat marker.txt other/file.txt > "$MINUTES_TEST_OUT"\n'
                                                         'printf %s "${GIT_OBJECT_DIRECTORY-}${GIT_ALTERNATE_OBJECT_DIRECTORIES-}${GIT_COMMON_DIR-}" '
                                                         '>> "$MINUTES_TEST_OUT"\n',
                                                         encoding="utf-8")
        for path in (hook, repository / "scripts" / "check.sh"):
            path.chmod(0o755)
        copy_location_script(repository)
        (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
        (repository / ".gitattributes").write_text("marker.txt filter=crash\n", encoding="utf-8")
        (repository / "marker.txt").write_text("marker\n", encoding="utf-8")
        (repository / "other" / "file.txt").write_text("other\n", encoding="utf-8")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(*arguments: str) -> str:
            return subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True,
                                  text=True).stdout.strip()

        git("init", "-q", "--template=", "-b", "main")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        git("sparse-checkout", "set", "scripts")
        # 展開の途中で、フックのプロセスのグループ全体を強制終了する
        git("config", "filter.crash.smudge", 'if [ -e "$MINUTES_TEST_CRASH" ]; then touch "$MINUTES_TEST_REACHED"; kill -9 0; fi; cat')
        head = git("rev-parse", "HEAD")

        def push(target: Path, **extra: str) -> "subprocess.CompletedProcess[str]":
            return subprocess.run([str(hook)], cwd=repository, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                                  capture_output=True, text=True, timeout=120, start_new_session=True,
                                  # 無視した SIGINT を引き継ぐ環境（nohup など）でも、フックが中断を受け取れるようにする
                                  preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
                                  env=dict(env, MINUTES_PRE_PUSH_DIR=str(target), MINUTES_TEST_OUT=str(out),
                                           MINUTES_TEST_CRASH=str(crash), MINUTES_TEST_REACHED=str(reached), **inherited, **extra))

        # 外から引き継いだ変数は、検査（専用の作業ツリーの git）へ渡さない（代わりの check.sh が残った値を記録に書き足す）
        objects = str(repository / ".git" / "objects")
        inherited = {"GIT_OBJECT_DIRECTORY": objects, "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects,
                     "GIT_COMMON_DIR": str(repository / ".git")}
        count += 1
        crash.write_text("", encoding="utf-8")
        first = push(base)
        crash.unlink()
        # 1 回目が展開の途中の filter に達して強制終了したこと（別の理由の失敗を中断と取り違えない）。記録は 2 回目のものだけを見る
        if not reached.exists():
            failures.append(f"pre-push: 1 回目が展開の途中の強制終了に達していない（終了コード {first.returncode}）\n{first.stderr.strip()}")
        out.unlink(missing_ok=True)
        # 強制終了で残ったロック（フックの案内と git の案内のとおり、利用者が削除するもの）
        for lock in list(base.glob("*.lock")):
            lock.rmdir()
        for lock in (repository / ".git" / "worktrees").glob("*/index.lock"):
            lock.unlink()
        second = push(base)
        recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
        if first.returncode == 0 or second.returncode != 0 or recorded != "marker\nother\n":
            failures.append(f"pre-push: 展開の途中の強制終了の後に検査し直せない（{first.returncode}・{second.returncode}・"
                            f"記録 {recorded!r}）\n{second.stderr.strip()}")
        count += 1
        out.unlink(missing_ok=True)
        # 中断のない初回の作成（登録 → 印 → 展開 → 照合）も通しで通り、印を書くこと
        fresh = push(Path(directory) / "fresh")
        recorded = out.read_text(encoding="utf-8") if out.is_file() else ""
        marks = list((repository / ".git" / "worktrees").glob("*/minutes-pre-push-owner"))
        if fresh.returncode != 0 or recorded != "marker\nother\n" or len(marks) != 2:
            failures.append(f"pre-push: 初回の作成が通らない（終了コード {fresh.returncode}・記録 {recorded!r}・印 {len(marks)}）\n"
                            f"{fresh.stderr.strip()}")
        # ここから後の試験は、強制終了の後に検査し直した専用の作業ツリー（base の下）を使う。それがない・使えない（.git の
        # ファイルがない）場合は、理由を記録して後の試験だけを行わない（初回の作成の試験は上で済ませた。記録した失敗は
        # main が報告する）
        recovered = ([path for path in base.iterdir() if path.is_dir() and not path.is_symlink() and (path / ".git").is_file()]
                     if base.is_dir() else [])
        if len(recovered) != 1:
            failures.append(f"pre-push: 強制終了の後に検査し直した専用の作業ツリーがない・使えない（{base} の下の候補 "
                            f"{[path.name for path in recovered]}）。それを使う後の試験を行わない")
            return
        # 再利用する専用の作業ツリーの下に登録された別の作業ツリーがあれば、どちらも変更せずに止める
        # 実体のパスで扱う（git に登録され、フックが表示するのは実体のパス）
        snapshot = Path(os.path.realpath(recovered[0]))
        count += 1
        out.unlink(missing_ok=True)
        inner = snapshot / "nested" / "inner"
        git("worktree", "add", "-q", "--detach", str(inner), head)
        (inner / "mine.txt").write_text("mine", encoding="utf-8")
        (snapshot / "marker.txt").write_text("changed\n", encoding="utf-8")
        refused = push(base)
        if (refused.returncode != 1 or out.exists() or not (inner / "mine.txt").is_file() or str(inner) not in refused.stderr
                or (snapshot / "marker.txt").read_text(encoding="utf-8") != "changed\n"):
            failures.append(f"pre-push: 下に登録された作業ツリーがあるのに止まらない・変更した（終了コード {refused.returncode}）\n{refused.stderr.strip()}")
        # 止まらずに clean された場合は登録だけが残るので、失敗しても後の試験を続ける
        subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(inner)], env=env, capture_output=True)
        # 残すビルドの成果物の場所（直下と Package）がリンクなら、検査の前に止める
        external = Path(directory) / "external"
        external.mkdir()
        for build in (".build", "Packages/MinutesKit/.build"):
            count += 1
            out.unlink(missing_ok=True)
            (snapshot / build).parent.mkdir(parents=True, exist_ok=True)
            (snapshot / build).symlink_to(external)
            refused = push(base)
            if refused.returncode != 1 or out.exists() or f"{build} がシンボリックリンク" not in refused.stderr or list(external.iterdir()):
                failures.append(f"pre-push: リンクの {build} を止めない（終了コード {refused.returncode}）\n{refused.stderr.strip()}")
            if (snapshot / build).is_symlink():
                (snapshot / build).unlink()
        count += 1
        out.unlink(missing_ok=True)
        resumed = push(base)
        if resumed.returncode != 0 or not out.exists():
            failures.append(f"pre-push: 下の作業ツリーとリンクを除いた後に検査し直せない（終了コード {resumed.returncode}）\n{resumed.stderr.strip()}")
        # 下の作業ツリーの判定の後、clean の直前に登録された作業ツリーも、追跡していない複製も消さずに止める。外せば、残ったほかの
        # 追跡していないファイル（無視するものを含む）を消して検査できる
        real = shutil.which("git")
        assert real is not None
        shims, late = Path(directory) / "shims", snapshot / "late" / "inner"
        shims.mkdir()
        (shims / "git").write_text('#!/bin/bash\ncase " $* " in *" clean -q "*) "$MINUTES_TEST_GIT" -C "$MINUTES_TEST_REPOSITORY" worktree add -q '
                                   '--detach "$MINUTES_TEST_LATE" && echo mine > "$MINUTES_TEST_LATE/mine.txt" ;; esac\n'
                                   'exec "$MINUTES_TEST_GIT" "$@"\n', encoding="utf-8")
        (shims / "git").chmod(0o755)
        count += 1
        out.unlink(missing_ok=True)
        refused = push(base, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real, MINUTES_TEST_REPOSITORY=str(repository),
                       MINUTES_TEST_LATE=str(late))
        if refused.returncode != 1 or out.exists() or not (late / "mine.txt").is_file() or "late/" not in refused.stderr:
            failures.append(f"pre-push: 判定の後に登録された作業ツリーを消した・止まらない（終了コード {refused.returncode}）\n{refused.stderr.strip()}")
        subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(late)], env=env, capture_output=True)

        def vacate(label: str) -> bool:
            """専用の作業ツリーを移している間に元の場所の下へ登録された作業ツリーを外した後、元の場所には空のディレクトリが残る。
            フックはそれを消さずに止まり（専用の作業ツリーは移した場所に残す）、利用者が中身を確かめて外せば、次の検査が戻す。
            ここでは止まることを確かめてから、利用者の代わりに空のディレクトリだけを外す。外せれば True。"""
            global count
            if (snapshot / ".git").exists() or not snapshot.is_dir():
                return True
            count += 1
            out.unlink(missing_ok=True)
            refused = push(base)
            held = [path for path in snapshot.parent.glob(f"{snapshot.name}.work.*") if (path / "tree" / ".git").is_file()]
            if refused.returncode != 1 or out.exists() or not snapshot.is_dir() or len(held) != 1 or "別のものができた" not in refused.stderr:
                failures.append(f"pre-push: {label}の後、元の場所に残った空のディレクトリを消した・止まらない（終了コード {refused.returncode}・"
                                f"移した場所 {len(held)}）\n{refused.stderr.strip()}")
            try:
                for path in sorted(snapshot.rglob("*"), key=lambda found: len(found.parts), reverse=True):
                    path.rmdir()  # 空でないもの（ディレクトリでないもの）があれば失敗する
                snapshot.rmdir()
            except OSError as error:
                failures.append(f"pre-push: {label}の後、元の場所に空でないものが残った（{error}）")
                return False
            return True

        vacate("判定の後に登録された作業ツリーの削除")
        # 上の試験で移したままの専用の作業ツリーを、先に元の場所へ戻す（戻さないと、複製の検査に達する前に止まる）
        restored = push(base)
        if restored.returncode != 0 or not (snapshot / ".git").is_file():
            failures.append(f"pre-push: 複製の試験の前に専用の作業ツリーを戻せない（終了コード {restored.returncode}）\n{restored.stderr.strip()}")
        count += 1
        out.unlink(missing_ok=True)
        clone = snapshot / "clone"
        subprocess.run(["git", "init", "-q", "--template=", str(clone)], env=env, check=True, capture_output=True)
        (clone / "mine.txt").write_text("mine", encoding="utf-8")
        refused = push(base)
        if refused.returncode != 1 or out.exists() or not (clone / "mine.txt").is_file() or "clone/" not in refused.stderr:
            failures.append(f"pre-push: 追跡していない複製を消した・止まらない（終了コード {refused.returncode}）\n{refused.stderr.strip()}")
        # フックが複製を消した場合（上で失敗を記録済み）は、ないことだけを認めて後続の試験を続ける。ほかの削除の失敗は記録する
        try:
            shutil.rmtree(clone)
        except FileNotFoundError:
            pass
        except OSError as error:
            failures.append(f"pre-push: 複製の試験の後片付けで {clone} を消せない（{error}）")
        # 残すビルドの成果物の中の .git のディレクトリ（依存の複製）は認め、.git のファイル（登録された作業ツリー）は止める
        for build in (".build", "Packages/MinutesKit/.build"):
            for label, is_file in (("ディレクトリ", False), ("ファイル", True)):
                count += 1
                out.unlink(missing_ok=True)
                dependency = snapshot / build / "checkouts" / "dependency"
                dependency.mkdir(parents=True)
                if is_file:
                    (dependency / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
                else:
                    (dependency / ".git").mkdir()
                (dependency / "mine.txt").write_text("mine", encoding="utf-8")
                pushed = push(base)
                if (pushed.returncode != (1 if is_file else 0) or out.exists() == is_file or not (dependency / "mine.txt").is_file()
                        or (is_file and f"{build}/checkouts/dependency/.git" not in pushed.stderr)):
                    failures.append(f"pre-push: {build} の中の .git の{label}の扱いが違う（終了コード {pushed.returncode}）\n{pushed.stderr.strip()}")
                shutil.rmtree(dependency)
        # 登録の直後に中断しても、印を書いてから終わる（次の検査が使える）。同じ場所に先に登録された作業ツリーには印を書かない
        for signal_name in ("TERM", "INT"):
            count += 1
            out.unlink(missing_ok=True)
            target = Path(directory) / f"signaled-{signal_name}"
            (shims / "git").write_text('#!/bin/bash\n"$MINUTES_TEST_GIT" "$@"\nstatus=$?\ncase " $* " in *" worktree add "*) '
                                       f'kill -{signal_name} 0 ;; esac\nexit "$status"\n', encoding="utf-8")
            stopped = push(target, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real)
            marked = [path for path in (repository / ".git" / "worktrees").glob("*/minutes-pre-push-owner")
                      if f"/{target.name}/" in path.read_text(encoding="utf-8")]
            ran = out.exists()
            again = push(target)
            if stopped.returncode != 130 or ran or len(marked) != 1 or again.returncode != 0 or not out.exists() or list(target.glob("*.lock")):
                failures.append(f"pre-push: 登録の直後の中断（{signal_name}）の後に検査し直せない（{stopped.returncode}・印 {len(marked)}・"
                                f"{again.returncode}）\n{stopped.stderr.strip()}\n{again.stderr.strip()}")
        count += 1
        out.unlink(missing_ok=True)
        target = Path(directory) / "taken"
        (shims / "git").write_text('#!/bin/bash\ncase " $* " in *" worktree add "*) "$MINUTES_TEST_GIT" "$@" && touch "${@: -2:1}/mine.txt" ;; esac\n'
                                   'exec "$MINUTES_TEST_GIT" "$@"\n', encoding="utf-8")
        refused = push(target, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real)
        marked = [path for path in (repository / ".git" / "worktrees").glob("*/minutes-pre-push-owner")
                  if f"/{target.name}/" in path.read_text(encoding="utf-8")]
        if refused.returncode != 1 or out.exists() or marked or not list(target.glob("*/mine.txt")):
            failures.append(f"pre-push: 先に登録された作業ツリーに印を書いた・止まらない（終了コード {refused.returncode}・印 {len(marked)}）\n"
                            f"{refused.stderr.strip()}")
        # 判定の後、展開の前に登録された作業ツリーの中のファイルを、送信するコミットの同じパスのファイルで上書きせずに止める
        # （専用の作業ツリーを移す直前・移した後の展開の直前・元の場所に戻す直前）。外せば検査できる
        (repository / "scripts" / "race" / "inner").mkdir(parents=True)
        (repository / "scripts" / "race" / "inner" / "mine.txt").write_text("committed\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "race")
        head = git("rev-parse", "HEAD")
        late = snapshot / "scripts" / "race" / "inner"
        for label, pattern in (("移す直前", f"worktree move {snapshot} "), ("展開の直前", " checkout -q "), ("戻す直前", f"/tree {snapshot} ")):
            count += 1
            out.unlink(missing_ok=True)
            (shims / "git").write_text(
                f'#!/bin/bash\ncase " $* " in *"{pattern}"*) if [ ! -e "$MINUTES_TEST_LATE" ]; then "$MINUTES_TEST_GIT" -C '
                '"$MINUTES_TEST_REPOSITORY" worktree add -q --detach "$MINUTES_TEST_LATE" && echo mine > "$MINUTES_TEST_LATE/mine.txt"; fi ;; esac\n'
                'exec "$MINUTES_TEST_GIT" "$@"\n', encoding="utf-8")
            refused = push(base, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real, MINUTES_TEST_REPOSITORY=str(repository),
                           MINUTES_TEST_LATE=str(late))
            hinted = f"{snapshot}/tree に移っている可能性" in refused.stderr
            if hinted != (label == "戻す直前"):
                failures.append(f"pre-push: {label}に登録された場合の、移った場所の案内が違う（案内 {hinted}）\n{refused.stderr.strip()}")
            if hinted:
                again = push(base)  # 次の検査も、確かめられない中身を変えずに同じ場所を案内して止まる
                if again.returncode != 1 or f"{snapshot}/tree に移っている可能性" not in again.stderr or not (snapshot / "tree" / ".git").exists():
                    failures.append(f"pre-push: 移った後の検査が場所を案内しない（終了コード {again.returncode}）\n{again.stderr.strip()}")
            kept = (late / "mine.txt").read_text(encoding="utf-8") if (late / "mine.txt").is_file() else ""
            if refused.returncode != 1 or out.exists() or kept != "mine\n" or "中身を確かめて外" not in refused.stderr:
                failures.append(f"pre-push: {label}に登録された作業ツリーを変えた・止まらない（終了コード {refused.returncode}・{kept!r}）\n"
                                f"{refused.stderr.strip()}")
            subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(late)], env=env, capture_output=True)
            if label == "展開の直前":
                vacate(f"{label}に登録された作業ツリーの削除")
            if label == "戻す直前":
                # 元の場所に別のものができたので、専用の作業ツリーがその中に移っている。利用者が外に出す
                subprocess.run(["git", "-C", str(repository), "worktree", "move", str(snapshot / "tree"), str(base / "moved")], env=env,
                               capture_output=True)
                shutil.rmtree(snapshot, ignore_errors=True)
                subprocess.run(["git", "-C", str(repository), "worktree", "move", str(base / "moved"), str(snapshot)], env=env,
                               capture_output=True)
        # 専用の作業ツリーを移したまま強制終了しても、次の検査が元の場所に戻して検査する（登録が古いままの場合を含む）。
        # フックと同じく、移す前に管理ディレクトリ（実体のパス）を移し先の admin に記録する。記録がない・合わない移し先は、
        # 移さず・登録も直さず・消さずに止める（印だけでは、印のある別の作業ツリーと区別できない）
        admin = os.path.realpath(git("-C", str(snapshot), "rev-parse", "--path-format=absolute", "--git-dir"))
        for label, registered, record in (("登録も移した", True, admin), ("登録が古いまま", False, admin),
                                          ("記録がない", False, None), ("記録が合わない", False, admin + "-other")):
            count += 1
            out.unlink(missing_ok=True)
            held = Path(f"{snapshot}.work.killed")
            held.mkdir()
            if not (snapshot / ".git").is_file():
                failures.append(f"pre-push: 移したまま終わった場合（{label}）を試す専用の作業ツリーがない（{snapshot}）")
                break
            if record is not None:
                (held / "admin").write_text(record, encoding="utf-8")
            if registered:
                git("worktree", "move", str(snapshot), str(held / "tree"))
            else:
                snapshot.rename(held / "tree")
            listed = git("worktree", "list", "--porcelain")
            resumed = push(base)
            if record == admin:
                if resumed.returncode != 0 or not out.exists() or held.exists() or not (snapshot / ".git").is_file():
                    failures.append(f"pre-push: 移したまま終わった専用の作業ツリー（{label}）を戻せない（終了コード {resumed.returncode}）\n"
                                    f"{resumed.stderr.strip()}")
                continue
            unchanged = ((held / "tree" / ".git").is_file() and not snapshot.exists()
                         and git("worktree", "list", "--porcelain") == listed)
            if resumed.returncode != 1 or out.exists() or not unchanged or "確かめられない" not in resumed.stderr:
                failures.append(f"pre-push: 確かめられない移し先（{label}）を変えた・止まらない（終了コード {resumed.returncode}・"
                                f"変えていない {unchanged}）\n{resumed.stderr.strip()}")
            # 後の試験のために、利用者が戻す（元のリポジトリから登録を直す）。フックが誤って戻した場合は、そのまま使う
            if (held / "tree").exists() and not snapshot.exists():
                (held / "tree").rename(snapshot)
                git("worktree", "repair", str(snapshot))
            shutil.rmtree(held, ignore_errors=True)
        # 元の作業ツリーの sparse checkout に scripts/ がなくても、置き場所と登録を判定できる
        count += 1
        out.unlink(missing_ok=True)
        git("sparse-checkout", "set", "other")
        (snapshot / "junk" / ".build").mkdir(parents=True)
        (snapshot / "junk" / ".build" / "file").write_text("junk", encoding="utf-8")
        resumed = push(base)
        if (repository / "scripts").exists() or resumed.returncode != 0 or not out.exists() or (snapshot / "junk").exists():
            failures.append(f"pre-push: 元の作業ツリーに scripts/ がないと検査できない・追跡していないものを残した（終了コード {resumed.returncode}）\n"
                            f"{resumed.stderr.strip()}")
        # ディレクトリがなく登録だけが残っていれば、登録を上書きせずに止める。登録を外せば作り直して検査できる
        count += 1
        out.unlink(missing_ok=True)
        away = Path(directory) / "away"
        snapshot.rename(away)
        listed = git("worktree", "list", "--porcelain")
        refused = push(base)
        if (refused.returncode != 1 or out.exists() or snapshot.exists() or git("worktree", "list", "--porcelain") != listed
                or f"（{snapshot}）は、ディレクトリがないのに作業ツリーとして登録されている" not in refused.stderr):
            failures.append(f"pre-push: 登録だけが残った置き場所で止まらない・登録を変えた（終了コード {refused.returncode}）\n{refused.stderr.strip()}")
        count += 1
        out.unlink(missing_ok=True)
        subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(snapshot)], env=env, capture_output=True)
        renewed = push(base)
        if renewed.returncode != 0 or not out.exists():
            failures.append(f"pre-push: 登録を外した後に作り直せない（終了コード {renewed.returncode}）\n{renewed.stderr.strip()}")
        count += 1
        out.unlink(missing_ok=True)
        key = next((path.name for path in base.iterdir() if path.is_dir()), "missing")
        foreign = Path(directory) / "foreign" / key
        git("worktree", "add", "-q", "--detach", str(foreign), head)
        (foreign / "mine.txt").write_text("mine", encoding="utf-8")
        refused = push(foreign.parent)
        if refused.returncode != 1 or not (foreign / "mine.txt").is_file() or out.exists():
            failures.append(f"pre-push: 印のない作業ツリーを変更した・止まらない（終了コード {refused.returncode}）\n{refused.stderr.strip()}")


def coverage_links_case() -> None:
    """check.sh の手順 7 が、測る入力の写しの外を指すシンボリックリンクがあれば swift test の前に止め、写しの中の相対の
    リンクは認めること（swift の代わりが呼ばれたかで確かめる）。"""
    global count
    for label, target, want_call in (("写しの中の相対のリンク", "../Shared/Real.swift", True),
                                     ("写しの中のディレクトリのリンク", "../Shared", True),
                                     ("写しの外を指すリンク", "../../../../../outside.swift", False),
                                     ("写しの外を経由して中に戻るリンク",
                                      "../../../../../.build/minutes-coverage-input/Packages/MinutesKit/Shared", False),
                                     ("絶対パスのリンク", "/etc/hosts", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            package = repository / "Packages" / "MinutesKit"
            (repository / "scripts").mkdir(parents=True)
            (package / "Sources").mkdir(parents=True)
            (package / "Shared").mkdir()
            (package / "Package.swift").write_text("", encoding="utf-8")
            (package / "Shared" / "Real.swift").write_text("", encoding="utf-8")
            (repository / "outside.swift").write_text("", encoding="utf-8")
            (package / "Sources" / "Link").symlink_to(target)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "check.sh").chmod(0o755)
            (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
            shims, out = Path(directory) / "shims", Path(directory) / "called"
            shims.mkdir()
            (shims / "swift").write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\nexit 1\n', encoding="utf-8")
            (shims / "swift").chmod(0o755)
            for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                               capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True,
                                    timeout=60, env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
            # 拒否するリンクは、診断を出し、swift を呼ばずに、終了コード 1 で止まること
            if out.exists() != want_call or (not want_call and ("写しの外を指す" not in result.stderr or result.returncode != 1)):
                failures.append(f"check: 手順 7 の入力の{label}の扱いが違う（swift の呼び出し {out.exists()}・終了コード {result.returncode}）"
                                f"\n{result.stderr.strip()}")


def coverage_filter_case() -> None:
    """check.sh の手順 7 が、未コミットの変更を含む作業ツリーを測るとき、.gitattributes の clean のフィルタを通さない作業ツリーの
    バイト列そのものを測る写しに書き出すこと（swift の代わりが、測る写しのファイルの内容を記録する）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        package = repository / "Packages" / "MinutesKit"
        (repository / "scripts").mkdir(parents=True)
        (package / "Sources").mkdir(parents=True)
        (package / "Package.swift").write_text("", encoding="utf-8")
        (package / "Sources" / "A.swift").write_text("let base = 0\n", encoding="utf-8")
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").chmod(0o755)
        (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
        (repository / ".gitattributes").write_text("*.swift filter=upper\n", encoding="utf-8")
        shims, out = Path(directory) / "shims", Path(directory) / "measured"
        shims.mkdir()
        (shims / "swift").write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do\n  if [ "$1" = --package-path ]; then cp "$2/Sources/A.swift" '
                                     '"$MINUTES_TEST_OUT"; fi\n  shift\ndone\nexit 1\n', encoding="utf-8")
        (shims / "swift").chmod(0o755)
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"],
                          ["config", "filter.upper.clean", "tr a-z A-Z"]):
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                           capture_output=True)
        # 未コミットの変更（clean のフィルタは大文字に変える）
        (package / "Sources" / "A.swift").write_text("let raw = 1\n", encoding="utf-8")
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True,
                                timeout=60, env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
        # バイト列で比べる（read_text は改行を変換して読むので、改行の変換で内容が変わっても見逃す）
        measured = out.read_bytes() if out.exists() else None
        if measured != b"let raw = 1\n":
            failures.append(f"check: 手順 7 が作業ツリーのバイト列と違う内容（{measured!r}）を測った（clean のフィルタ）"
                            f"\n{result.stderr.strip()}")


def coverage_sparse_case() -> None:
    """check.sh の手順 7 が、sparse checkout の外にある追跡したファイル（skip-worktree の印があり作業ツリーにない）を、消えたファイル
    として失敗させず、インデックスの blob のまま識別子のツリーと測る写しの両方に含めること。未コミットの変更は作業ツリーのバイト列で測る。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        package = repository / "Packages" / "MinutesKit"
        (repository / "scripts").mkdir(parents=True)
        (package / "Sources").mkdir(parents=True)
        (package / "Docs").mkdir()
        (package / "Package.swift").write_text("", encoding="utf-8")
        (package / "Sources" / "A.swift").write_text("let base = 0\n", encoding="utf-8")
        (package / "Docs" / "B.txt").write_text("outside the cone\n", encoding="utf-8")
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "check.sh").chmod(0o755)
        (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
        shims, out = Path(directory) / "shims", Path(directory) / "measured"
        shims.mkdir()
        (shims / "swift").write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do\n  if [ "$1" = --package-path ]; then cp "$2/Sources/A.swift" '
                                     '"$MINUTES_TEST_OUT"; cp "$2/Docs/B.txt" "$MINUTES_TEST_OUT.docs"; fi\n  shift\ndone\nexit 1\n',
                                     encoding="utf-8")
        (shims / "swift").chmod(0o755)
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"],
                          ["sparse-checkout", "set", "--no-cone", "/*", "!/Packages/MinutesKit/Docs/"]):
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                           capture_output=True)
        if (package / "Docs" / "B.txt").exists():
            failures.append("check: sparse checkout の試験の前提が違う（範囲の外のファイルが作業ツリーにある）")
            return
        # 未コミットの変更（作業ツリー全体の識別子で測る経路を通す）
        (package / "Sources" / "A.swift").write_text("let raw = 1\n", encoding="utf-8")
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True,
                                timeout=60, env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
        measured = out.read_bytes() if out.exists() else None
        docs = Path(f"{out}.docs")
        outside = docs.read_bytes() if docs.exists() else None
        if measured != b"let raw = 1\n" or outside != b"outside the cone\n":
            failures.append(f"check: 手順 7 が sparse checkout の外のファイルを扱えない（測った内容 {measured!r}・範囲の外 {outside!r}）"
                            f"\n{result.stderr.strip()}")


def coverage_paths_case() -> None:
    """check.sh の手順 7・8 が、カバレッジの場所（直下の .build・Package の .build）がリンクで作業ツリーの外を指せば、ロックを取る前・
    消す前に止め、リンクの先の中を変えないこと（swift の代わりが呼ばれないこと）。作業ツリーの中を指すリンクは、この検査では止めないこと。"""
    global count
    for label, link, target, inside in (("Package の .build が外を指す", "Packages/MinutesKit/.build", "outside", False),
                                           ("直下の .build が外を指す", ".build", "outside", False),
                                           ("Package の .build が作業ツリーの中を指す", "Packages/MinutesKit/.build", "repository/inside", True)):
        for step in ("7", "8"):
            count += 1
            with tempfile.TemporaryDirectory() as directory:
                env = isolated_git_env()
                env.pop("MINUTES_CHECK_ISOLATED", None)
                repository, shims, out = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "called"
                package = repository / "Packages" / "MinutesKit"
                (repository / "scripts").mkdir(parents=True)
                (package / "Sources").mkdir(parents=True)
                (package / "Package.swift").write_text("", encoding="utf-8")
                for name in ("check.sh", "test-checks.py"):
                    (repository / "scripts" / name).write_text("#!/bin/sh\nexit 0\n" if name == "test-checks.py" else
                                                               (SCRIPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
                    (repository / "scripts" / name).chmod(0o755)
                (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
                destination = Path(directory) / target
                destination.mkdir(parents=True)
                kept = destination / "minutes-coverage.json"
                kept.write_text("mine", encoding="utf-8")
                (destination / "minutes-coverage-input").mkdir()
                shims.mkdir()
                (shims / "swift").write_text('#!/bin/sh\ntouch "$MINUTES_TEST_OUT"\nexit 1\n', encoding="utf-8")
                (shims / "swift").chmod(0o755)
                for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                    subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                    "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                                   capture_output=True)
                (repository / link).symlink_to(destination)
                result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", step], capture_output=True, text=True,
                                        timeout=60, env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
                stopped = "作業ツリーの外を指す" in result.stderr
                intact = (kept.is_file() and kept.read_bytes() == b"mine" and (destination / "minutes-coverage-input").is_dir()
                          and not (destination / "minutes-coverage.lock").exists())
                if inside:
                    # 中を指すリンクは場所の検査で止めない（後の検査で止まるかは問わない。追跡していないリンクは測る入力の
                    # リンクの検査が止める）
                    wrong = stopped
                else:
                    wrong = result.returncode == 0 or not stopped or out.exists() or not intact
                if wrong:
                    failures.append(f"check.sh 手順 {step}: {label}の扱いが違う（終了コード {result.returncode}・swift の呼び出し {out.exists()}・"
                                    f"リンクの先が元のまま {intact}）\n{result.stderr.strip()}")


def coverage_resolution_case() -> None:
    """check.sh の手順 7 が、確定版の記録があるときだけ --force-resolved-versions を渡し、失敗したら記録を残さないこと。
    git add -f でステージした無視するファイルを測る入力に含め、追跡していない無視するファイルは含めないこと。"""
    global count
    for label, pinned in (("確定版の記録がある", True), ("確定版の記録がない", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository, shims, out = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "out"
            package = repository / "Packages" / "MinutesKit"
            (repository / "scripts").mkdir(parents=True)
            (package / "Sources").mkdir(parents=True)
            shims.mkdir()
            (package / "Package.swift").write_text("", encoding="utf-8")
            if pinned:
                (package / "Package.resolved").write_text("{}", encoding="utf-8")
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "check.sh").chmod(0o755)
            (repository / ".gitignore").write_text(".build/\n*.generated.swift\n", encoding="utf-8")
            # 引数と写しの中のファイルを記録する。古い確定版の代わりに、指定があれば失敗し、なければ次の呼び出し（JSON の場所）で失敗する
            (shims / "swift").write_text('#!/bin/bash\necho "$*" >> "$MINUTES_TEST_OUT"\n(cd "$3/Sources" && ls) >> "$MINUTES_TEST_OUT"\n'
                                         'case "$*" in *--force-resolved-versions*|*--show-codecov-path*) exit 1 ;; esac\n', encoding="utf-8")
            (shims / "swift").chmod(0o755)
            commit_all(repository, env)
            # 前の測定の記録（印と JSON）。失敗した測定の後に残さない
            records = [package / ".build" / name for name in ("minutes-coverage-head", "minutes-coverage.json")]
            records[0].parent.mkdir()
            for record in records:
                record.write_text("stale\n", encoding="utf-8")
            for name in ("Forced", "Ignored"):
                (package / "Sources" / f"{name}.generated.swift").write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "-f", "Packages/MinutesKit/Sources/Forced.generated.swift"], env=env,
                           check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True, timeout=60,
                                    env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out)))
            called = out.read_text(encoding="utf-8") if out.is_file() else ""
            if (result.returncode != 1 or called.count("test --package-path") != (1 if pinned else 2)
                    or ("--force-resolved-versions" in called) != pinned or "Forced.generated.swift" not in called
                    or "Ignored.generated.swift" in called or any(record.exists() for record in records)):
                failures.append(f"check: 手順 7 の入力・確定版・前の記録の扱いが違う（{label}。終了コード {result.returncode}・呼び出し {called!r}・"
                                f"残った記録 {[record.name for record in records if record.exists()]}）\n"
                                f"{result.stderr.strip()}")


def coverage_replace_case() -> None:
    """check.sh の手順 7・8 が、--require-clean がなくても置き換えのオブジェクト（git replace）を使わず、元のコミットの内容を
    測る入力として書き出し、同じ入力として照合すること（swift の代わりが写しの内容を記録する）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        package = repository / "Packages" / "MinutesKit"
        (repository / "scripts").mkdir(parents=True)
        (package / "Sources").mkdir(parents=True)
        (package / "Package.swift").write_text("", encoding="utf-8")
        source = package / "Sources" / "A.swift"
        source.write_text("original\n", encoding="utf-8")
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "test-checks.py"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".gitignore").write_text(".build/\n", encoding="utf-8")
        shims, out = Path(directory) / "shims", Path(directory) / "measured"
        shims.mkdir()
        (shims / "swift").write_text('#!/bin/sh\ncat "$3/Sources/A.swift" > "$MINUTES_TEST_OUT"\nexit 1\n', encoding="utf-8")
        (shims / "swift").chmod(0o755)

        def git(*arguments: str) -> str:
            return subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                   "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        other = Path(directory) / "other"
        other.write_text("replaced\n", encoding="utf-8")
        git("replace", git("rev-parse", "HEAD:Packages/MinutesKit/Sources/A.swift"), git("hash-object", "-w", str(other)))
        check = [str(repository / "scripts" / "check.sh"), "--steps"]
        run_env = dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_OUT=str(out))
        result = subprocess.run([*check, "7"], capture_output=True, text=True, timeout=60, env=run_env)
        measured = out.read_text(encoding="utf-8") if out.is_file() else ""
        if measured != "original\n":
            failures.append(f"check: 手順 7 が置き換えのオブジェクトを測る入力にした（{measured!r}）\n{result.stderr.strip()}")
        # 手順 8: HEAD のコミットのツリーを置き換えても、元のコミットの入力として照合する（変更があると取り違えない）
        count += 1
        head = git("rev-parse", "HEAD")
        git("replace", "-d", git("rev-parse", "HEAD:Packages/MinutesKit/Sources/A.swift"))
        git("replace", head, git("commit-tree", "-m", "replaced", git("hash-object", "-w", "-t", "tree", "/dev/null")))
        (repository / ".build").mkdir(exist_ok=True)
        variables = dict(re.findall(r'^(COVERAGE_HEAD|COVERAGE_JSON)="\$PACKAGE/([^"]+)"$',
                                    (SCRIPTS / "check.sh").read_text(encoding="utf-8"), re.MULTILINE))
        # 集計の代わり: 呼び出しの引数を記録する
        (repository / "scripts" / "coverage-report.py").write_text('#!/bin/sh\necho "$*" > "$MINUTES_TEST_OUT"\n', encoding="utf-8")
        (repository / "scripts" / "coverage-report.py").chmod(0o755)
        (repository / ".git" / "info").mkdir(exist_ok=True)
        (repository / ".git" / "info" / "exclude").write_text("/scripts/coverage-report.py\n", encoding="utf-8")
        content = b"{}"
        digest, sources = hashlib.sha256(content).hexdigest(), "/measured/Sources"
        record, report = package / variables["COVERAGE_HEAD"], package / variables["COVERAGE_JSON"]
        report.write_bytes(content)

        def step8(label: str, text: str, want: str) -> None:
            """記録を text にして手順 8 を実行する。want が空なら集計まで進み、なければその理由で集計せずに止まること。"""
            global count
            count += 1
            record.write_text(text, encoding="utf-8")
            out.unlink(missing_ok=True)
            result = subprocess.run([*check, "8"], capture_output=True, text=True, timeout=60, env=run_env)
            called = out.read_text(encoding="utf-8") if out.is_file() else ""
            stopped = result.returncode == 1 and want in result.stderr and not called
            passed = result.returncode == 0 and f"--measured-sources {sources} " in called
            if not (stopped if want else passed):
                failures.append(f"check: 手順 8 の{label}の扱いが違う（終了コード {result.returncode}・集計 {called!r}）\n{result.stderr.strip()}")

        recorded = f"commit {head}\n{digest}\n{sources}\n"
        step8("置き換えのオブジェクトがある同じ入力", recorded, "")
        # 記録は変えずに、実際の入力だけを変える
        source.write_text("changed\n", encoding="utf-8")
        step8("変わった入力", recorded, f"別の入力（commit {head}）で測った")
        source.write_text("original\n", encoding="utf-8")
        step8("戻した入力", recorded, "")
        # 入力は同じで、JSON だけが記録と違う
        report.write_bytes(b"{ }")
        step8("書き換えた JSON", recorded, "手順 7 で測ったものと違う")
        report.write_bytes(content)
        step8("測った場所のない記録", f"commit {head}\n{digest}\n", "測った場所がない")


def coverage_moved_case() -> None:
    """check.sh の手順 7 で測った後に作業ツリーを移しても、手順 8 が測ったときの Sources の行を数え、テストの行は数えないこと
    （swift の代わりが、測った場所の絶対パスの記録を書く）。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository, moved, shims = Path(directory) / "repository", Path(directory) / "moved", Path(directory) / "shims"
        package = repository / "Packages" / "MinutesKit"
        (repository / "scripts").mkdir(parents=True)
        (package / "Sources").mkdir(parents=True)
        (package / "Package.swift").write_text("", encoding="utf-8")
        (package / "Sources" / "A.swift").write_text("", encoding="utf-8")
        for name in ("check.sh", "coverage-report.py", "xcproj.py"):
            (repository / "scripts" / name).write_text((SCRIPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "coverage-report.py", "test-checks.py"):
            (repository / "scripts" / name).chmod(0o755)
        (repository / ".gitignore").write_text(".build/\n__pycache__/\n", encoding="utf-8")
        shims.mkdir()
        report = Path(directory) / "report.json"
        (shims / "swift").write_text(
            '#!/usr/bin/python3\nimport json, os, sys\n'
            'if "--show-codecov-path" in sys.argv:\n    print(os.environ["MINUTES_TEST_REPORT"])\n    sys.exit(0)\n'
            'base = os.path.realpath(sys.argv[3])\n'
            'files = [{"filename": f"{base}/{name}", "summary": {"lines": {"count": 10, "covered": covered}}}\n'
            '         for name, covered in (("Sources/A.swift", 10), ("Tests/B.swift", 0), (".build/C.swift", 0))]\n'
            'json.dump({"data": [{"files": files}]}, open(os.environ["MINUTES_TEST_REPORT"], "w"))\n', encoding="utf-8")
        (shims / "xcodebuild").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for path in shims.iterdir():
            path.chmod(0o755)
        commit_all(repository, env)
        run_env = dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_REPORT=str(report), MINUTES_SIMULATOR="platform=test")
        first = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "7"], capture_output=True, text=True, timeout=60,
                               env=run_env)
        repository.rename(moved)
        second = subprocess.run([str(moved / "scripts" / "check.sh"), "--steps", "8"], capture_output=True, text=True, timeout=60,
                                env=run_env)
        if first.returncode != 0 or second.returncode != 0 or "行 100.0%（1 ファイル" not in second.stdout:
            failures.append(f"check: 測った後に移した作業ツリーで集計できない（{first.returncode}・{second.returncode}）\n"
                            f"{first.stderr.strip()}\n{second.stdout.strip()}\n{second.stderr.strip()}")


def stalled_download_case() -> None:
    """setup-tools.sh が gh を取る curl に転送の全体の上限を付け、上限で失敗したら既存の失敗の経路で止まること（ロックを残さない）。

    curl の代わりは、上限の指定が 0 より大きく 600 より小さいときだけ、時間切れを記録して 28 で終わる。ほかは別の失敗（3）にする。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        dest, shims, versions = base / "tools", base / "shims", base / "versions"
        shims.mkdir()
        versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.zip gh {'0' * 64}\n", encoding="utf-8")
        timed_out = base / "timed-out"
        (shims / "curl").write_text('#!/bin/bash\nlimit=\nwhile [ $# -gt 0 ]; do\n  case "$1" in --max-time) limit=$2; shift ;; '
                                    '-o) : > "$2" ;; esac\n  shift\ndone\n[[ $limit =~ ^[0-9]{1,3}$ ]] && [ "$limit" -gt 0 ] && '
                                    '[ "$limit" -lt 600 ] || exit 3\n: > "$MINUTES_TEST_TIMED_OUT"\nexit 28\n', encoding="utf-8")
        (shims / "curl").chmod(0o755)
        result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_TIMED_OUT=str(timed_out)))
        left = sorted(path.name for path in dest.iterdir()) if dest.is_dir() else []
        if result.returncode != 1 or not timed_out.exists() or "配布ファイルを取得できない" not in result.stderr or left:
            failures.append(f"versions: gh の取得に上限がない・止まった後に残る（終了コード {result.returncode}・残ったもの {left}）\n"
                            f"{result.stderr.strip()}")


def stalled_gh_download_case() -> None:
    """setup-tools.sh が gh での取得に上限を付け、過ぎたら gh を止めて終わるのを待ち、ロックを外して失敗すること（上限内なら導入する）。"""
    global count
    for label, stalled in (("止まった取得", True), ("終わる取得", False)):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dest, shims, versions, worker = base / "tools", base / "shims", base / "versions", base / "worker"
            shims.mkdir()
            # TERM を無視する止まった gh も止められること
            script = ('#!/bin/bash\nif [ "$1" = --version ]; then echo "gh version 9.9.9 (2026-01-01)"; exit 0; fi\n'
                      + ("trap '' TERM\necho $$ > \"$MINUTES_TEST_WORKER\"\nexec /bin/sleep 60\n" if stalled else
                         'while [ "$1" != --dir ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVES/gitleaks.tar.gz" "$2/"\n'))
            rows = []
            for name, text in (("gh", script), ("gitleaks", "#!/bin/sh\necho 9.9.9\n")):
                (base / name).write_text(text, encoding="utf-8")
                (base / name).chmod(0o755)
                with tarfile.open(base / f"{name}.tar.gz", "w:gz") as bundle:
                    bundle.add(base / name, arcname=name)
                digest = hashlib.sha256((base / f"{name}.tar.gz").read_bytes()).hexdigest()
                rows.append(f"{name} 9.9.9 any cli/cli v9.9.9 {name}.tar.gz {name} {digest}\n")
            versions.write_text("".join(rows), encoding="utf-8")
            (shims / "curl").write_text('#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$MINUTES_TEST_ARCHIVES/gh.tar.gz" "$2"\n',
                                        encoding="utf-8")
            (shims / "curl").chmod(0o755)
            try:
                result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest),
                                         "--require", "gh gitleaks", "--download-limit", "1" if stalled else "30"],
                                        capture_output=True, text=True, timeout=30,
                                        env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVES=str(base),
                                                 MINUTES_TEST_WORKER=str(worker)))
            except subprocess.TimeoutExpired:
                failures.append(f"versions: gh の{label}に上限がない")
                continue
            recorded = worker.read_text(encoding="utf-8").strip() if worker.is_file() else ""
            stopped = bool(re.fullmatch(r"[1-9]\d*", recorded)) and process_gone(int(recorded))
            left = sorted(path.name for path in dest.iterdir() if path.name.startswith(".")) if dest.is_dir() else []
            if stalled:
                right = result.returncode == 1 and stopped and "止めた" in result.stderr and "gitleaks の配布ファイルを取得できない" in result.stderr
            else:
                right = result.returncode == 0 and (dest / "gitleaks" / "9.9.9" / "gitleaks").is_file()
            if not right or left:
                failures.append(f"versions: gh の{label}の扱いが違う（終了コード {result.returncode}・止まった {stopped}・残ったもの {left}）\n"
                                f"{result.stderr.strip()}")
    for value in ("0", "301", "x", "-1", "1.5"):
        expect(f"versions: gh での取得の上限が {value}", [str(SCRIPTS / "setup-tools.sh"), "--download-limit", value], 2, "使い方")
    # 空の導入先は ./ に変えて今の場所を導入先にせず、使い方の誤りにする
    expect("versions: 空の --dest", [str(SCRIPTS / "setup-tools.sh"), "--verify", "--dest", ""], 2, "使い方")


def stalled_probe_case() -> None:
    """setup-tools.sh が版の表示に上限を付けること。止まった導入済みのものは、導入では取得し直し、--verify では失敗する。

    止まる形は、TERM を無視して戻らないものと、すぐ終わるが標準出力を持つ子を残すもの（版の表示の結果を待つ $(…) が戻らない）。
    後者は版を表示して成功しているので、子を止めて導入済みとして使う。
    """
    global count
    version = 'if [ "$1" = --version ]; then echo "gh version 9.9.9 (2026-01-01)"; exit 0; fi\n'
    for label, body, reused in (("戻らない", "trap '' TERM\n/bin/sleep 60\n", False),
                                ("子を残す", "/bin/sleep 60 &\n" + version, True)):
        for verify in (False, True):
            count += 1
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                dest, shims, versions = base / "tools", base / "shims", base / "versions"
                shims.mkdir()
                (base / "gh").write_text("#!/bin/bash\n" + version, encoding="utf-8")
                (base / "gh").chmod(0o755)
                with tarfile.open(base / "gh.tar.gz", "w:gz") as bundle:
                    bundle.add(base / "gh", arcname="gh")
                digest = hashlib.sha256((base / "gh.tar.gz").read_bytes()).hexdigest()
                versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {digest}\n", encoding="utf-8")
                fetched = base / "fetched"
                (shims / "curl").write_text('#!/bin/bash\n: > "$MINUTES_TEST_FETCHED"\nwhile [ "$1" != -o ]; do shift; done\n'
                                            'cp "$MINUTES_TEST_ARCHIVE" "$2"\n', encoding="utf-8")
                (shims / "curl").chmod(0o755)
                installed = dest / "gh" / "9.9.9"
                installed.mkdir(parents=True)
                (dest / "bin").mkdir()
                (dest / "bin" / "gh").symlink_to("../gh/9.9.9/gh")
                (installed / "gh").write_text("#!/bin/bash\n" + body, encoding="utf-8")
                (installed / "gh").chmod(0o755)
                (installed / ".archive-sha256").write_text(digest + "\n", encoding="utf-8")
                try:
                    result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require",
                                             "gh", "--probe-limit", "2", *(["--verify"] if verify else [])], capture_output=True, text=True,
                                            timeout=30, env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}",
                                                                 MINUTES_TEST_FETCHED=str(fetched),
                                                                 MINUTES_TEST_ARCHIVE=str(base / "gh.tar.gz")))
                except subprocess.TimeoutExpired:
                    failures.append(f"versions: 版の表示（{label}・照合 {verify}）に上限がない")
                    continue
                want = 1 if verify and not reused else 0
                if (result.returncode != want or fetched.exists() != (not verify and not reused) or (dest / ".install.lock").exists()
                        or (want and "版を表示できない" not in result.stderr)):
                    failures.append(f"versions: 止まる版の表示（{label}・照合 {verify}）の扱いが違う（終了コード {result.returncode}・"
                                    f"取得 {fetched.exists()}）\n{(result.stdout + result.stderr).strip()}")
    for value in ("0", "301", "x", "-1", "1.5"):
        expect(f"versions: 版の表示の上限が {value}", [str(SCRIPTS / "setup-tools.sh"), "--probe-limit", value], 2, "使い方")


def newline_archive_case() -> None:
    """setup-tools.sh が、導入先のパスに改行があっても、配布ファイルの SHA-256 を正しく照合して導入すること。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        dest, shims, versions, archive = base / "too\nls", base / "shims", base / "versions", base / "gh.tar.gz"
        shims.mkdir()
        (base / "gh").write_text("#!/bin/sh\necho 'gh version 9.9.9 (2026-01-01)'\n", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(base / "gh", arcname="gh")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {digest}\n", encoding="utf-8")
        (shims / "curl").write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do\n  case "$1" in -o) cp "$MINUTES_TEST_ARCHIVE" "$2" ;; esac\n  shift\ndone\n',
                                    encoding="utf-8")
        (shims / "curl").chmod(0o755)
        result = subprocess.run([str(SCRIPTS / "setup-tools.sh"), "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive)))
        if result.returncode != 0 or not (dest / "gh" / "9.9.9" / "gh").is_file():
            failures.append(f"versions: 改行を含む導入先で配布ファイルを照合できない（終了コード {result.returncode}）\n{result.stderr.strip()}")


def publish_failure_case(setup: str) -> None:
    """公開するリンクを更新できないときは失敗し、既存のリンクと導入を残すこと（一時の名前のリンクを rename で置き換える）。

    1 つのツールの台帳と合成した配布ファイルで導入してから試す（この Mac に導入済みのツールに依存しない。取得の代わりは、
    導入が済んだ後に呼ばれれば記録して失敗する）。
    """
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        dest, shims, versions, archive, called = base / "tools", base / "shims", base / "versions", base / "gh.tar.gz", base / "called"
        shims.mkdir()
        (base / "gh").write_text("#!/bin/sh\necho 'gh version 9.9.9 (2026-01-01)'\n", encoding="utf-8")
        (base / "gh").chmod(0o755)
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(base / "gh", arcname="gh")
        versions.write_text(f"gh 9.9.9 any cli/cli v9.9.9 gh.tar.gz gh {hashlib.sha256(archive.read_bytes()).hexdigest()}\n", encoding="utf-8")
        curl = shims / "curl"
        curl.write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do\n  case "$1" in -o) cp "$MINUTES_TEST_ARCHIVE" "$2" ;; esac\n  shift\ndone\n',
                        encoding="utf-8")
        curl.chmod(0o755)
        env = dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_ARCHIVE=str(archive), MINUTES_TEST_CALLED=str(called))
        command = [setup, "--versions", str(versions), "--dest", str(dest), "--require", "gh"]
        recorded = len(failures)
        expect("versions: 公開の失敗の試験の導入", command, 0, env=env)
        curl.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MINUTES_TEST_CALLED"\nexit 1\n', encoding="utf-8")
        if not (dest / "bin" / "gh").is_symlink():
            if len(failures) == recorded:  # 導入の失敗なら、上の expect が記録した
                failures.append("versions: 公開の失敗の試験の導入が成功したのに、公開するリンクがない")
            return
        link = os.readlink(dest / "bin" / "gh")
        (dest / "bin").chmod(0o555)
        try:
            expect("versions: 公開するリンクを更新できない", command, 1, "公開するリンク", env=env)
        finally:
            (dest / "bin").chmod(0o755)
        expect("versions: 更新できなかった後もリンクと導入が残る", [*command, "--verify"], 0, "OK", env=env)
        kept = os.readlink(dest / "bin" / "gh") if (dest / "bin" / "gh").is_symlink() else None
        if called.exists() or kept != link:
            failures.append(f"versions: 公開の失敗の試験で取得した（{called.exists()}）・リンクが変わった")


def staged_index_newline_case() -> None:
    """check.sh --staged が、名前が改行で終わる代わりのインデックス（GIT_INDEX_FILE）を、終了時にもそのまま照合すること。

    改行を落とした名前の場所には別の内容のインデックスを置く（取り違えると、変わっていないのに失敗する）。検査の間に
    本来のインデックス（改行で終わる方）が変われば、合格にせず理由を示す（取り違えると、変わったことを見逃す）。
    """
    global count
    for changed in (False, True):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools" / "bin").mkdir(parents=True)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            stage_tool_stubs(repository)
            # 文書の整合の代わり: 検査の途中で、改行で終わるインデックスに変更をステージする
            stage = 'printf changed > doc.md && GIT_INDEX_FILE="$MINUTES_TEST_INDEX" git add doc.md' if changed else "exit 0"
            (repository / "scripts" / "verify-docs.py").write_text(f"#!/bin/sh\n{stage}\n", encoding="utf-8")
            gitleaks = repository / ".tools" / "bin" / "gitleaks"
            gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (repository / "scripts" / "check.sh", repository / "scripts" / "verify-docs.py", gitleaks):
                path.chmod(0o755)
            (repository / ".gitleaks.toml").write_text("", encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            (repository / "doc.md").write_text("staged\n", encoding="utf-8")
            own = str(Path(directory) / "index\n")
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=repository, env=dict(env, GIT_INDEX_FILE=own), check=True, capture_output=True)
            subprocess.run(["git", "add", "doc.md"], cwd=repository, env=dict(env, GIT_INDEX_FILE=own[:-1]), check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, capture_output=True, text=True,
                                    env=dict(env, GIT_INDEX_FILE=own, MINUTES_TEST_INDEX=own), timeout=60)
            output = result.stdout + result.stderr
            passed = result.returncode == 0 and "OK: コミット前の検査を通過" in output
            refused = result.returncode == 1 and "検査中にステージした内容が変わった" in output and "コミット前の検査を通過" not in output
            if not (refused if changed else passed):
                failures.append(f"check: 名前が改行で終わるインデックスを取り違えた（変えた {changed}・終了コード {result.returncode}）\n"
                                f"{output.strip()}")


def test_versions() -> None:
    global count
    snapshot_tools_case()
    coverage_replace_case()
    coverage_moved_case()
    coverage_resolution_case()
    archive_newline_paths_case()
    stalled_download_case()
    stalled_gh_download_case()
    stalled_probe_case()
    isolated_attributes_case()
    staged_query_failure_case()
    newline_archive_case()
    staged_index_newline_case()
    retired_backup_case()
    publish_interrupted_case()
    install_lock_case()
    linked_bin_case()
    staged_dash_case()
    step4_tracked_case()
    pre_push_newline_case()
    pre_push_mark_failure_case()
    pre_push_recheck_case()
    staged_scripts_case()
    reinstall_command_case()
    pre_push_interrupted_case()
    coverage_links_case()
    coverage_filter_case()
    coverage_sparse_case()
    coverage_paths_case()
    snapshot_timeout_case()
    copy_tools_lock_case()
    staged_newline_directory_case()
    newline_checkout_case()
    pre_push_lock_case()
    worktree_location_case()
    tools_override_case()
    ordinary_tools_snapshot_case()
    setup_config_failure_case()
    setup_newline_override_case()
    require_clean_child_case()
    require_clean_child_case(pointed=True)
    # 引数が 1 個あれば空文字列でも引数として扱い、--hooks との完全一致を求める（ツールの導入より前に止まる）
    expect("setup: 空文字列の引数", [str(SCRIPTS / "setup.sh"), ""], 2, "使い方")
    expect("setup: 知らない引数", [str(SCRIPTS / "setup.sh"), "--hook"], 2, "使い方")
    setup_hooks_case()
    setup_guard_cases()
    setup_repeated_hooks_path_case()
    setup_relative_case()
    setup_missing_hook_case()
    require_clean_case()
    require_clean_case(inherited=True)
    require_clean_location_case()
    require_clean_sparse_case()
    runner_integrity_case()
    require_clean_smudge_case()
    setup_include_case()
    staged_symlink_case()
    staged_symlink_config_case()
    staged_pathspec_case()
    install_unlock_case()
    coverage_lock_case()
    staged_index_change_case()
    staged_tree_location_case()
    staged_setup_tools_case()
    staged_raw_blob_case()
    staged_raw_tree_case()
    staged_docs_link_case()
    staged_rules_link_case()
    coverage_record_message_case()
    # 明示した --steps が空なら、全手順を実行せずに使い方の誤りにする。
    # リポジトリの外の一時ディレクトリに写した check.sh で試す（誤って全手順を実行しても、手順 0 がこの自己試験を
    # 呼び直して再帰しないように。写しの周りには手順のスクリプトも Git のリポジトリもないので、すぐに失敗する）
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / "scripts" / "check.sh"
        copy.parent.mkdir()
        copy.write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        copy.chmod(0o755)
        for value in ("", ",", " "):
            expect(f"check: 空の --steps（{value!r}）", [str(copy), "--steps", value], 2, "使い方", timeout=30)
        # 範囲外の番号（bash の整数の範囲を超える桁数を含む）は、ほかの正しい番号があっても手順を始めずに使い方の誤りにする
        for value in ("11", "0,99999999999999999999", "0-99999999999999999999", "99999999999999999999-0", "0,010"):
            expect(f"check: 範囲外の --steps（{value!r}）", [str(copy), "--steps", value], 2, "使い方", timeout=30)
        # 実行時の場所のファイルの名前に展開して番号として読まない。正しい並びはこれまでどおり読む
        (Path(directory) / "10").write_text("", encoding="utf-8")
        for value in ("1?", "*", "[0-9]*", "0,1[0]"):
            expect(f"check: ファイル名に展開されうる --steps（{value!r}）", [str(copy), "--steps", value], 2, "使い方", timeout=30)
        for value in ("0-8,10", "10"):
            expect(f"check: 正しい --steps（{value!r}）", [str(copy), "--steps", value], 1, "", timeout=30, env=isolated_git_env())
    simulator_cases()
    simulator_stderr_case()
    simulator_timeout_case()
    simulator_developer_dir_case()
    lint_rules_config_cases()
    setup = str(SCRIPTS / "setup-tools.sh")
    tools = str(TOOLS)
    original = (ROOT / "tools" / "versions").read_text(encoding="utf-8")
    expect("versions: 導入済みの版が一致する", [setup, "--verify", "--dest", tools], 0, "OK")
    # 相対パスの --dest「-」は、その名前のディレクトリを指す（cd が直前の場所（OLDPWD）へ移ると、導入先の実体の照合が食い違う）
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "-").symlink_to(tools)
        dashed = subprocess.run([setup, "--verify", "--dest", "-"], cwd=directory, capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, OLDPWD=str(ROOT)))
        if dashed.returncode != 0 or "OK" not in dashed.stdout:
            failures.append(f"versions: --dest - が「-」という名前のディレクトリを指さない（終了コード {dashed.returncode}）\n"
                            f"{dashed.stdout.strip()}\n{dashed.stderr.strip()}")
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
    # 台帳の全体を検証してから導入する。最後の行だけが不正な台帳で導入を実行しても、前の行（gh）を取得・展開せず、
    # 公開するリンクも作らない（取得の前に失敗するので通信もしない）
    with tempfile.TemporaryDirectory() as directory:
        versions = Path(directory) / "versions"
        lines = original.rstrip("\n").splitlines()
        last = max(at for at, line in enumerate(lines) if line.strip() and not line.split()[0].startswith("#"))
        dest = Path(directory) / "dest"
        # 検証の前に取得を始める不具合があっても、curl の代わりが記録して失敗する（通信しない）
        shims, called = Path(directory) / "shims", Path(directory) / "called"
        shims.mkdir()
        (shims / "curl").write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MINUTES_TEST_CALLED"\nexit 1\n', encoding="utf-8")
        (shims / "curl").chmod(0o755)
        for label, column, value, want_text in (("SHA-256", -1, "z" * 64, "不正"), ("対応していない配布ファイルの形式", 5, "tool.tgz", "形式に対応していない"),
                                                ("接尾辞だけ似た配布ファイル", 5, "toolzip", "形式に対応していない"),
                                                ("版の確認方法のないツール", 0, "newtool", "版の確認方法が定義されていない")):
            fields = lines[last].split()
            fields[column] = value
            versions.write_text("\n".join(lines[:last] + [" ".join(fields)] + lines[last + 1:]) + "\n", encoding="utf-8")
            expect(f"versions: 最後の行が不正な台帳で導入を始めない（{label}）", [setup, "--versions", str(versions), "--dest", str(dest)], 1,
                   want_text, env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MINUTES_TEST_CALLED=str(called)))
            if called.exists():
                failures.append(f"versions: 不正な台帳（{label}）で取得を始めた\n{called.read_text(encoding='utf-8').strip()}")
                called.unlink()
            leftovers = sorted(path.name for path in dest.iterdir()) if dest.is_dir() else []
            if leftovers:
                failures.append(f"versions: 不正な台帳（{label}）で導入先に {leftovers} を作った（検証の前に導入した）")
    publish_failure_case(setup)
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
        # 台帳の版と形式は同じで値の違う版（決め打ちの値だと、台帳の版と同じになったときに負例にならない）
        gh_parts = version.split(".")
        other_gh = ".".join(gh_parts[:-1] + [str(int(gh_parts[-1]) + 1)])
        stub.write_text(f"#!/bin/sh\necho 'gh version {other_gh} (2000-01-01)'\n", encoding="utf-8")
        stub.chmod(0o755)
        (stub.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
        (dest / "bin").mkdir()
        (dest / "bin" / "gh").symlink_to(f"../gh/{version}/{executable}")
        versions = Path(directory) / "versions"
        versions.write_text("\n".join(gh_rows) + "\n", encoding="utf-8")
        # gh の行だけの台帳なので、期待するツールの集合を gh だけと明示する
        expect("versions: 実行時の gh の版が違う",
               [setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"], 1, f"実行時: {other_gh}")
        # 版を表示するコマンドが失敗する導入は、出力によらず合格にしない
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\nexit 3\n", encoding="utf-8")
        expect("versions: 版の表示が失敗する", [setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
               1, "版を表示できない")
        # リンクの先が導入記録のある版のディレクトリでなければ、実行時の版が合っていても合格にしない
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
        expect("versions: 導入記録のある版を指すリンク", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                 "--require", "gh"], 0, "OK")
        # 版を表示するコマンドに標準入力を渡さない（/dev/null）。正しい台帳で、setup-tools.sh の標準入力に既知の行を渡し、
        # 代わりの gh が何かを読めたら失敗させる（読めなければ版を表示して照合が通る）
        stub.write_text(f'#!/bin/sh\nif IFS= read -r line; then echo "標準入力を読んだ: $line" >&2; exit 9; fi\n'
                        f"echo 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
        result = subprocess.run([setup, "--verify", "--versions", str(versions), "--dest", str(dest), "--require", "gh"],
                                input="minutes-known-input-line\n", capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or "標準入力を読んだ" in result.stderr:
            failures.append(f"versions: 版の表示のコマンドが標準入力を読めた（終了コード {result.returncode}）\n"
                            f"{(result.stdout + result.stderr).strip()}")
        stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
        # 重複した gh の行は、台帳の検証で失敗する（上の標準入力の試験とは別に確かめる）
        duplicated = Path(directory) / "versions-duplicated"
        duplicated.write_text("\n".join([gh_rows[0], gh_rows[0]]) + "\n", encoding="utf-8")
        expect("versions: 重複した gh の行", [setup, "--verify", "--versions", str(duplicated), "--dest", str(dest),
                                       "--require", "gh"], 1, "重複している")
        # 照合も導入と同じロックを取る。導入の実行中（ロックがある間）は照合を終えず、ロックが外れてから照合して通る
        verify_lock_case(setup, versions, dest, stub)
        other = dest / "gh" / "other" / executable
        other.parent.mkdir()
        other.write_text(stub.read_text(encoding="utf-8"), encoding="utf-8")
        other.chmod(0o755)
        (dest / "bin" / "gh").unlink()
        (dest / "bin" / "gh").symlink_to(f"../gh/other/{executable}")
        expect("versions: リンクが導入記録のない場所を指す", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                  "--require", "gh"], 1, "導入先を指していない")
        # 名前が改行で終わる別の実行ファイル・別の版のディレクトリを指すリンクを、改行のない名前のものと取り違えない
        newline_dir = dest / "gh" / f"{version}\n"
        newline_dir.mkdir()
        for label, place in (("実行ファイル", dest / "gh" / version / f"{executable}\n"), ("版のディレクトリ", newline_dir / executable)):
            place.write_text(stub.read_text(encoding="utf-8"), encoding="utf-8")
            place.chmod(0o755)
            (dest / "bin" / "gh").unlink()
            (dest / "bin" / "gh").symlink_to(os.path.relpath(place, dest / "bin"))
            expect(f"versions: リンクが改行で終わる{label}を指す", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                         "--require", "gh"], 1, "導入先を指していない")
        # 版のディレクトリの中の実行ファイルがシンボリックリンクなら、版の表示が合っていても合格にしない
        (dest / "bin" / "gh").unlink()
        (dest / "bin" / "gh").symlink_to(f"../gh/{version}/{executable}")
        stub.unlink()
        stub.symlink_to(other)
        expect("versions: 版のディレクトリの実行ファイルがリンク", [setup, "--verify", "--versions", str(versions), "--dest", str(dest),
                                                     "--require", "gh"], 1, "通常のファイルでない")
    # 版のディレクトリ・ツールのディレクトリが外の共有のディレクトリへのリンクなら、照合でも再利用の判定でも認めない
    for level in ("版", "ツール"):
        with tempfile.TemporaryDirectory() as directory:
            dest, shared = Path(directory) / "tools", Path(directory) / "shared"
            version, executable, sha = gh_rows[0].split()[1], gh_rows[0].split()[6], gh_rows[0].split()[-1]
            stub = shared / version / executable
            stub.parent.mkdir(parents=True)
            stub.write_text(f"#!/bin/sh\necho 'gh version {version} (2000-01-01)'\n", encoding="utf-8")
            stub.chmod(0o755)
            (stub.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
            (dest / "bin").mkdir(parents=True)
            if level == "版":
                (dest / "gh").mkdir()
                (dest / "gh" / version).symlink_to(shared / version)
            else:
                (dest / "gh").symlink_to(shared)
            (dest / "bin" / "gh").symlink_to(f"../gh/{version}/{executable}")
            versions = Path(directory) / "versions"
            versions.write_text(gh_rows[0] + "\n", encoding="utf-8")
            arguments = ["--versions", str(versions), "--dest", str(dest), "--require", "gh"]
            expect(f"versions: {level}のディレクトリが外へのリンク", [setup, "--verify", *arguments], 1, "導入先の中にない")
            # 導入は共有の実体を再利用せず、取得し直す（curl の代わりで失敗させる。通信しない）
            shims = Path(directory) / "shims"
            shims.mkdir()
            (shims / "curl").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            (shims / "curl").chmod(0o755)
            expect(f"versions: {level}のディレクトリが外へのリンクの再利用", [setup, *arguments], 1,
                   "配布ファイルを取得できない" if level == "版" else "シンボリックリンク",
                   env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}"))
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
        # 公開するリンクの位置が実体のディレクトリなら、その中にリンクを作らずに、その位置を示して止める
        # （導入済みの版を再利用する経路なので取得しない）
        (dest / "bin" / "gh").unlink()
        (dest / "bin" / "gh").mkdir()
        expect("versions: 公開するリンクの位置がディレクトリ", [setup, "--versions", str(versions), "--dest", str(dest),
                                                    "--require", "gh"], 1, f"{dest / 'bin' / 'gh'} がディレクトリ",
               timeout=LOCK_ERROR_TIMEOUT)
        if any((dest / "bin" / "gh").iterdir()):
            failures.append("versions: 公開するリンクの位置のディレクトリの中にリンクを作った")
    # ロックを作れない理由が競合でなければ、待たずに理由を示して止める（取得の前に止まるので通信しない）
    # 待ち続ける誤りを検出するため時間の上限を付ける（通常はすぐ終わる。遅い環境でも誤って失敗しない長さにする）
    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "readonly"
        dest.mkdir()
        dest.chmod(0o555)
        try:
            expect("versions: 書き込めない導入先ではロックを待たない", [setup, "--dest", str(dest)], 1, "ロック",
                   timeout=LOCK_ERROR_TIMEOUT)
        finally:
            dest.chmod(0o755)
        dest = Path(directory) / "file-lock"
        dest.mkdir()
        (dest / ".install.lock").write_text("", encoding="utf-8")
        expect("versions: ロックの位置にファイルがある", [setup, "--dest", str(dest)], 1, "ロック",
               timeout=LOCK_ERROR_TIMEOUT)


def test_gitleaks() -> None:
    global count
    gitleaks = str(TOOLS / "bin" / "gitleaks")
    base = [gitleaks, "dir", "--no-banner", "--no-color", "--redact", "--config", str(ROOT / ".gitleaks.toml")]
    # 偽の鍵は実行時に組み立てる（このファイル自体が検出されないようにする）
    fake = "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "config.txt").write_text(f'token = "{fake}"\n', encoding="utf-8")
        expect("gitleaks: 偽の鍵を検出する", base + [directory], 1, "leaks found: 1")
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "config.txt").write_text('name = "sample"\n', encoding="utf-8")
        expect("gitleaks: 鍵のないディレクトリは通る", base + [directory], 0, "no leaks found")
    gitleaks_newline_case()
    gitleaks_merge_case()
    gitleaks_broken_history_case()
    gitleaks_traversal_case()
    gitleaks_signature_case()
    gitleaks_rename_case()
    gitleaks_notes_case()
    gitleaks_relative_case()
    gitleaks_prefix_case()
    gitleaks_order_file_case()
    gitleaks_format_case()
    gitleaks_message_case()
    gitleaks_suppression_cases()
    # 浅いクローンは、省いた履歴を検査しないまま通過させない
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        source, clone = Path(directory) / "source", Path(directory) / "clone"
        source.mkdir()

        def git(*arguments: str) -> None:
            subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                            "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True, capture_output=True)

        git("init", "-q", "--template=", "-b", "main", str(source))
        for name in ("a.txt", "b.txt"):
            (source / name).write_text(name, encoding="utf-8")
            git("-C", str(source), "add", name)
            git("-C", str(source), "commit", "-q", "-m", name)
        git("clone", "-q", "--depth", "1", source.as_uri(), str(clone))
        # 外側の GIT_DIR などを引き継ぐと、一時のリポジトリではなく外側のリポジトリを検査してしまうので、同じ環境で実行する
        expect("gitleaks: 浅いクローンは通さない", [str(SCRIPTS / "gitleaks-history.sh"), str(clone)], 1, "浅いクローン", env=env)
        expect("gitleaks: 全履歴のあるリポジトリは検査する", [str(SCRIPTS / "gitleaks-history.sh"), str(source)], 0, "no leaks found",
               env=env)
        # リポジトリは実体の絶対パスに解決してから使う。解決できない引数は、判定も検査もせずに失敗する
        expect("gitleaks: 解決できないリポジトリは検査しない", [str(SCRIPTS / "gitleaks-history.sh"), str(Path(directory) / "missing")],
               1, "解決できない", env=env)
        # 相対パスの引数も、実行した場所（expect はリポジトリ直下で実行する）からの実体のパスとして検査する
        expect("gitleaks: 相対パスのリポジトリを検査する", [str(SCRIPTS / "gitleaks-history.sh"), os.path.relpath(source, ROOT)], 0,
               "no leaks found", env=env)
        # 「-」という名前のディレクトリの相対パスも、直前の場所（OLDPWD）ではなくそのディレクトリとして検査する
        dash = Path(directory) / "-"
        git("clone", "-q", source.as_uri(), str(dash))
        global count
        count += 1
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), "-"], cwd=directory, capture_output=True, text=True,
                                env=dict(env, OLDPWD=str(Path(directory) / "missing")))
        if result.returncode != 0 or "no leaks found" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: 「-」という名前のリポジトリを検査しない（終了コード {result.returncode}）\n"
                            f"{(result.stdout + result.stderr).strip()}")
        # 追加の引数は報告の指定だけ。検査の方式・終了コード・伏せ字・抑止を変える指定は gitleaks に渡さずに使い方の誤りにする
        report = Path(directory) / "report.json"
        expect("gitleaks: 報告の指定は渡せる",
               [str(SCRIPTS / "gitleaks-history.sh"), str(source), "--report-format", "json", "--report-path", str(report)],
               0, "no leaks found", env=env)
        for extra in (["--exit-code", "0"], ["--no-redact"], ["--log-opts", "HEAD"], ["--baseline-path", str(report)],
                      ["--report-path", "--exit-code"], ["--report-format", "toml"], ["--report-path"],
                      ["--report-path", str(report), "--report-path", str(report)]):
            expect(f"gitleaks: 追加の引数 {' '.join(extra)} は認めない", [str(SCRIPTS / "gitleaks-history.sh"), str(source), *extra],
                   2, "使い方", env=env)
    # 最初のコミットで足した行も、設定 log.showRoot=false によらず検査する
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        root_key = "ghp_" + "c8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        for name, content in (("root.txt", f'token = "{root_key}"\n'), ("next.txt", "next\n")):
            (repository / name).write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), *identity, "add", name], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), *identity, "commit", "-q", "-m", name], env=env, check=True,
                           capture_output=True)
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True,
                                env=dict(env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="log.showRoot", GIT_CONFIG_VALUE_0="false"))
        if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: log.showRoot=false で最初のコミットの鍵を検出しない（終了コード {result.returncode}）")
    # .gitattributes の textconv（差分の表示の前の変換）で鍵を消しても、元の内容の差分を検査する（--no-textconv）
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        hidden_key = "ghp_" + "t8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        for name, content in ((".gitattributes", "*.txt diff=hide\n"), ("config.txt", f'token = "{hidden_key}"\n')):
            (repository / name).write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), *identity, "add", name], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), *identity, "commit", "-q", "-m", name], env=env, check=True,
                           capture_output=True)
        # 変換のコマンドは何も出力しない（変換を使うと、差分から鍵の行が消える）
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True,
                                env=dict(env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="diff.hide.textconv", GIT_CONFIG_VALUE_0="true"))
        if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: textconv で変換した差分を検査し、鍵を見落とした（終了コード {result.returncode}）")
    # .gitattributes で binary（-diff）にしたテキストのファイルも、差分を本文として出して検査する（--text）
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        binary_key = "ghp_" + "u8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        for name, content in ((".gitattributes", "*.txt binary\n"), ("config.txt", f'token = "{binary_key}"\n')):
            (repository / name).write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), *identity, "add", name], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), *identity, "commit", "-q", "-m", name], env=env, check=True,
                           capture_output=True)
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True, env=env)
        if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: binary の属性のファイルの差分を検査せず、鍵を見落とした（終了コード {result.returncode}）")
    # 差分の色づけ（color.diff=always・color.ui=always）で行に制御文字が入っても、鍵を検出する（--log-opts の --no-color）。
    # 対照として、色の設定のない通常の場合も同じ履歴で検出する
    for label, settings in (
        ("通常の色の設定", ()),
        ("color.diff=always", (("color.diff", "always"),)),
        ("color.ui=always", (("color.ui", "always"),)),
        # 日時の形式を変える設定でも、履歴を読める（--pretty の日時は固定の形式の %aI）
        ("log.date=relative", (("log.date", "relative"),)),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository = Path(directory) / "repository"
            colored_key = "ghp_" + "k8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
            identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", f"core.hooksPath={os.devnull}"]
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            (repository / "config.txt").write_text(f'token = "{colored_key}"\n', encoding="utf-8")
            for arguments in (["add", "config.txt"], ["commit", "-q", "-m", "config"]):
                subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
            colored = dict(env, GIT_CONFIG_COUNT=str(len(settings)))
            for index, (key, value) in enumerate(settings):
                colored[f"GIT_CONFIG_KEY_{index}"] = key
                colored[f"GIT_CONFIG_VALUE_{index}"] = value
            result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True,
                                    env=colored)
            if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
                failures.append(f"gitleaks: {label}で履歴の鍵を検出しない（終了コード {result.returncode}）")
    # 置き換えのオブジェクト（git replace）で鍵を含む blob を別の内容に見せても、元のオブジェクトを検査する
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        replaced_key = "ghp_" + "r8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        (repository / "config.txt").write_text(f'token = "{replaced_key}"\n', encoding="utf-8")
        for arguments in (["add", "config.txt"], ["commit", "-q", "-m", "config"]):
            subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
        original = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD:config.txt"], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()
        clean = subprocess.run(["git", "-C", str(repository), "hash-object", "-w", "--stdin"], input="clean\n", env=env, check=True,
                               capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(repository), "replace", original, clean], env=env, check=True, capture_output=True)
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True,
                                env=dict(env, GIT_NO_REPLACE_OBJECTS=""))
        if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: 置き換えのオブジェクトで隠した鍵を見落とした（終了コード {result.returncode}）")
    # 旧来の grafts（info/grafts）は GIT_NO_REPLACE_OBJECTS では外れず、親を付け替えて履歴の一部を隠せる。
    # grafts のファイルがあれば検査せずに失敗する。コメントと空行だけの grafts も止める（git が非推奨の案内を標準エラーに出し、
    # gitleaks 8.30.1 は「stderr is not empty」で履歴の読み取りをやめたまま「no leaks found」・終了コード 0 で通すため）
    for label, grafts, want_code, want_text in (
        ("有効な grafts", None, 1, "grafts"),
        ("コメントと空行だけの grafts", "# memo\n\n", 1, "grafts"),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository = Path(directory) / "repository"
            grafted_key = "ghp_" + "g8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
            identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", f"core.hooksPath={os.devnull}"]
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            for name, content in (("config.txt", f'token = "{grafted_key}"\n'), ("a.txt", "a\n"), ("b.txt", "b\n")):
                (repository / name).write_text(content, encoding="utf-8")
                for arguments in (["add", name], ["commit", "-q", "-m", name]):
                    subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
            if grafts is None:
                # 2 つ目のコミットを親のないコミットに見せ、鍵を足した最初のコミットを履歴から外す
                grafts = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD~1"], env=env, check=True,
                                        capture_output=True, text=True).stdout
            (repository / ".git" / "info").mkdir(exist_ok=True)  # --template= では info/ がない
            (repository / ".git" / "info" / "grafts").write_text(grafts, encoding="utf-8")
            result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], capture_output=True, text=True,
                                    env=env)
            if result.returncode != want_code or want_text not in result.stdout + result.stderr:
                failures.append(f"gitleaks: {label}: 終了コード {result.returncode}（期待 {want_code}・「{want_text}」）\n"
                                f"{(result.stdout + result.stderr).strip()}")


def gitleaks_report(report: Path, label: str, fields: Dict[str, type]) -> Optional[List[dict]]:
    """gitleaks の JSON の報告を読み、配列で、各項目が fields の各鍵をその型の値で持つ辞書であることを確かめる（fingerprints と同じ
    考え方。null の報告や項目を、TypeError で自己試験の全体を止めずに、名前つきの失敗として記録する）。読めない・形が違えば None。"""
    try:
        decoded = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        failures.append(f"{label}: 報告を読めない: {error!r}")
        return None

    def typed(value: object, kind: type) -> bool:
        # 真偽値は int の一種なので、整数の項目（StartLine など）では除く
        return isinstance(value, kind) and not (kind is int and isinstance(value, bool))

    if not (isinstance(decoded, list) and all(
            isinstance(item, dict) and all(typed(item.get(name), kind) for name, kind in fields.items()) for item in decoded)):
        wanted = "・".join(f"{name}（{kind.__name__}）" for name, kind in fields.items())
        failures.append(f"{label}: 報告の形が違う（各項目が {wanted} を持つ辞書の配列が要る）: {decoded!r}")
        return None
    return decoded


def gitleaks_suppression_cases() -> None:
    """全履歴の検査（gitleaks-history.sh）とコミット前の検査（check.sh --staged の手順 3）が、抑止の仕組みに検出を隠させないこと。

    作業ツリーにだけある .gitleaksignore（検出の指紋を書いたもの）と、行の「gitleaks:allow」のコメントの両方で確かめる。
    対照として、指定のない gitleaks では同じ入力が抑止されること（試験が効いていること）も確かめる。
    外から引き継いだ GIT_DIR が別のリポジトリを指していても、引数のリポジトリを検査することも確かめる。
    """
    global count
    gitleaks = str(TOOLS / "bin" / "gitleaks")
    config = str(ROOT / ".gitleaks.toml")
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                "-c", f"core.hooksPath={os.devnull}"]
    env = isolated_git_env()
    key = "ghp_" + "s8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる

    def git(repository: Path, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)

    def fingerprints(command: List[str], cwd: Path, report: Path, label: str) -> List[str]:
        """抑止する前の実行で、検出（終了コード 1）と空でない指紋を得る。得られなければ試験にならないので失敗として記録する
        （空の一覧や成功の実行を、抑止を試す根拠にしない）。"""
        result = launched(f"gitleaks: 抑止の前の実行（{label}）", command + ["--report-format", "json", "--report-path", str(report)],
                          cwd=cwd, env=env)
        # 報告は、空でない配列で、各項目が空でない文字列の Fingerprint を持つ辞書であること（null の報告や null の指紋を
        # str で "None" にして、抑止の試験の入力にしない）。形が違えば読めないものとして失敗を記録し、指紋を使わない
        try:
            decoded = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            decoded = None
            failures.append(f"gitleaks: 抑止の前の実行（{label}）の報告を読めない: {error!r}")
        else:
            if not (isinstance(decoded, list) and decoded and all(
                    isinstance(item, dict) and isinstance(item.get("Fingerprint"), str) and item["Fingerprint"]
                    for item in decoded)):
                failures.append(f"gitleaks: 抑止の前の実行（{label}）の報告の形が違う（空でない配列の各項目に空でない文字列の"
                                f" Fingerprint が要る）: {decoded!r}")
                decoded = None
        found = [item["Fingerprint"] for item in decoded] if decoded is not None else []
        if result.returncode != 1 or not found or not all(found):
            failures.append(f"gitleaks: 抑止の前の実行（{label}）が鍵を検出しない（終了コード {result.returncode}・指紋 {found}）。"
                            "抑止の試験にならない")
        return found

    for label, content, ignore in (("作業ツリーの .gitleaksignore", f'token = "{key}"\n', True),
                                   ("gitleaks:allow のコメント", f'token = "{key}"  # gitleaks:allow\n', False)):
        # 全履歴の検査
        count += 2
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            (repository / "config.txt").write_text(content, encoding="utf-8")
            git(repository, "add", "config.txt")
            git(repository, "commit", "-q", "-m", "config")
            direct = [gitleaks, "git", "--no-banner", "--no-color", "--redact", "--config", config, str(repository)]
            if ignore:
                found = fingerprints(direct, repository, Path(directory) / "report.json", f"{label}・全履歴")
                (repository / ".gitleaksignore").write_text("\n".join(found) + "\n", encoding="utf-8")
            control = launched(f"gitleaks: 対照（{label}）", direct, cwd=repository, env=env)
            if control.returncode != 0:
                failures.append(f"gitleaks: 対照（{label}）で、指定のない gitleaks が抑止しない（試験にならない）")
            result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository)], cwd=repository, env=env,
                                    capture_output=True, text=True)
            # .gitleaksignore は gitleaks が指定によらず読むので、あれば検査せずに失敗する。コメントは無視して検出する
            want = ".gitleaksignore がある" if ignore else "leaks found: 1"
            if result.returncode != 1 or want not in result.stdout + result.stderr:
                failures.append(f"gitleaks: 全履歴の検査が {label} で検出を隠した（終了コード {result.returncode}）")
        # コミット前の検査（check.sh --staged の手順 3）。check.sh と設定を写した一時のリポジトリで、鍵をステージする
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "check.sh").chmod(0o755)
            (repository / ".gitleaks.toml").write_text((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n.gitleaksignore\n", encoding="utf-8")
            (repository / ".tools").symlink_to(TOOLS)
            # 開発ツールの写しの照合に使う導入スクリプトと台帳（本物の .tools を照合する）
            for name in ("scripts/setup-tools.sh", "tools/versions"):
                (repository / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, repository / name)
            git(repository, "add", "-A")
            git(repository, "commit", "-q", "-m", "base")
            (repository / "config.txt").write_text(content, encoding="utf-8")
            git(repository, "add", "config.txt")
            direct = [gitleaks, "git", "--no-banner", "--no-color", "--redact", "--config", config, "--pre-commit", "--staged", "."]
            if ignore:
                found = fingerprints(direct, repository, Path(directory) / "report.json", f"{label}・ステージ")
                (repository / ".gitleaksignore").write_text("\n".join(found) + "\n", encoding="utf-8")
            control = launched(f"gitleaks: 対照（{label}）", direct, cwd=repository, env=env)
            if control.returncode != 0:
                failures.append(f"gitleaks: 対照（{label}・ステージ）で、指定のない gitleaks が抑止しない（試験にならない）")
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, env=env,
                                    capture_output=True, text=True, timeout=120)
            # 終了コードと手順 3 の印に加えて、場合ごとの診断を確かめる（.gitleaksignore は検査の前に止まり、
            # コメントは無視して gitleaks が検出する）
            want = ".gitleaksignore がある" if ignore else "leaks found: 1"
            if result.returncode != 1 or "手順 3" not in result.stderr or want not in result.stdout + result.stderr:
                failures.append(f"gitleaks: コミット前の検査が {label} で検出を隠した（終了コード {result.returncode}・"
                                f"期待する診断「{want}」）\n{(result.stdout + result.stderr).strip()[-500:]}")
    # コミット前の検査は、.gitattributes の binary・textconv によらず、ステージした内容そのものを検査する
    # （git diff を使う gitleaks git --staged では、binary の属性で「Binary files differ」になり鍵を見落とす）
    for label, attributes, extra in (("binary の属性", "*.txt binary\n", {}),
                                     ("textconv", "*.txt diff=hide\n", {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.hide.textconv",
                                                                       "GIT_CONFIG_VALUE_0": "true"})):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / "scripts" / "check.sh").chmod(0o755)
            (repository / ".gitleaks.toml").write_text((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"), encoding="utf-8")
            (repository / ".gitignore").write_text("/.tools\n", encoding="utf-8")
            (repository / ".gitattributes").write_text(attributes, encoding="utf-8")
            (repository / ".tools").symlink_to(TOOLS)
            # 開発ツールの写しの照合に使う導入スクリプトと台帳（本物の .tools を照合する）
            for name in ("scripts/setup-tools.sh", "tools/versions"):
                (repository / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, repository / name)
            git(repository, "add", "-A")
            git(repository, "commit", "-q", "-m", "base")
            (repository / "config.txt").write_text(f'token = "{key}"\n', encoding="utf-8")
            git(repository, "add", "config.txt")
            staged_env = dict(env, **extra)
            control = launched(f"gitleaks: 対照（{label}・ステージ）", [gitleaks, "git", "--no-banner", "--no-color", "--redact", "--config",
                                                              config, "--pre-commit", "--staged", "."], cwd=repository, env=staged_env)
            if control.returncode != 0:
                failures.append(f"gitleaks: 対照（{label}・ステージ）で、gitleaks git --staged が見落とさない（試験にならない）")
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--staged"], cwd=repository, env=staged_env,
                                    capture_output=True, text=True, timeout=120)
            if result.returncode != 1 or "手順 3" not in result.stderr or "leaks found: 1" not in result.stdout + result.stderr:
                failures.append(f"gitleaks: コミット前の検査が {label} で鍵を見落とした（終了コード {result.returncode}）\n"
                                f"{(result.stdout + result.stderr).strip()[-500:]}")
    # 外から引き継いだ GIT_DIR（鍵のないリポジトリ）に引きずられず、引数のリポジトリ（鍵がある）を検査する
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        target, other = Path(directory) / "target", Path(directory) / "other"
        for repository, content in ((target, f'token = "{key}"\n'), (other, "clean\n")):
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True,
                           capture_output=True)
            (repository / "config.txt").write_text(content, encoding="utf-8")
            git(repository, "add", "config.txt")
            git(repository, "commit", "-q", "-m", "config")
        result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(target)], capture_output=True, text=True,
                                env=dict(env, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other)))
        if result.returncode != 1 or "leaks found: 1" not in result.stdout + result.stderr:
            failures.append(f"gitleaks: 引き継いだ GIT_DIR のリポジトリを検査した（終了コード {result.returncode}）")




def gitleaks_newline_case() -> None:
    """gitleaks-history.sh が、名前が改行で終わるリポジトリを、改行のない名前の別のリポジトリと取り違えずに検査すること。"""
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        key = "ghp_" + "n8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"  # 実行時に組み立てる
        for name, content in (("target", "clean"), ("target\n", f'token = "{key}"')):
            repository = Path(directory) / name
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            (repository / "config.txt").write_text(content + "\n", encoding="utf-8")
            for arguments in (["add", "config.txt"], ["commit", "-q", "-m", "config"]):
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                               capture_output=True)
        history = str(SCRIPTS / "gitleaks-history.sh")
        expect("gitleaks: 名前が改行で終わるリポジトリ", [history, str(Path(directory) / "target\n")], 1, "leaks found: 1", env=env)
        expect("gitleaks: 改行のない名前のリポジトリ", [history, str(Path(directory) / "target")], 0, "no leaks found", env=env)


def step3_tool_verify_case() -> None:
    """check.sh が手順 0 なしで手順 3 を実行するとき、先に開発ツールを照合し、照合に失敗したら手順 3 を実行しないこと。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools").mkdir()  # 写す開発ツール
        out = Path(directory) / "recorded"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/sh\necho 'NG: 照合の代わりが失敗' >&2\nexit 1\n",
                                                               encoding="utf-8")
        (repository / "scripts" / "gitleaks-history.sh").write_text('#!/bin/sh\necho called > "$MINUTES_TEST_OUT"\n',
                                                                    encoding="utf-8")
        for name in ("check.sh", "test-checks.py", "setup-tools.sh", "gitleaks-history.sh"):
            (repository / "scripts" / name).chmod(0o755)
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", "commit", "-q", "--allow-empty",
                        "-m", "base"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "3"], capture_output=True, text=True,
                                env=dict(env, MINUTES_TEST_OUT=str(out)), timeout=60)
        if result.returncode != 1 or out.exists() or "照合の代わりが失敗" not in result.stderr:
            failures.append(f"gitleaks: 手順 0 なしの手順 3 が開発ツールを照合せずに実行した（終了コード {result.returncode}・"
                            f"記録 {out.exists()}）\n{(result.stdout + result.stderr).strip()}")


def step_order_verify_case() -> None:
    """開発ツールを使う最初の手順より前に手順 0 があるときだけ、先の照合を省くこと（手順 0 が後なら照合する）。"""
    global count
    verify, versions, history, secrets, docs = "setup-tools.sh --verify", "test-checks.py versions", "gitleaks-history.sh .", "test-checks.py gitleaks tracked", "verify-docs.py"
    files = "check-tracked-files.py"  # 手順 3 の最後（SEC-SUP-04）
    for steps, want, want_calls in (
        ("3,0", True, [verify, history, secrets, files, verify, versions]), ("3", True, [verify, history, secrets, files]),
        ("0,3", False, [verify, versions, history, secrets, files]), ("10", False, ["test-checks.py docs", docs]),
        ("10,0", False, ["test-checks.py docs", docs, verify, versions]),
        ("10,0,3", False, ["test-checks.py docs", docs, verify, versions, history, secrets, files]),
        ("10,3,0", True, [verify, "test-checks.py docs", docs, history, secrets, files, verify, versions]),
    ):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            env.pop("MINUTES_CHECK_ISOLATED", None)
            repository = Path(directory) / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / ".tools").mkdir()  # 写す開発ツール
            (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
            for name in ("test-checks.py", "setup-tools.sh", "gitleaks-history.sh", "check-tracked-files.py", "verify-docs.py"):
                # 呼び出しの名前と引数を順に記録する（写しの場所を渡す --dest は除く）
                (repository / "scripts" / name).write_text('#!/bin/bash\narguments="$*"\n'
                                                           'echo "${0##*/}${arguments:+ ${arguments%% --dest*}}" >> "$MINUTES_TEST_OUT"\n',
                                                           encoding="utf-8")
            out = Path(directory) / "calls"
            for path in (repository / "scripts").iterdir():
                path.chmod(0o755)
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", "commit", "-q", "--allow-empty",
                            "-m", "base"], env=env, check=True, capture_output=True)
            result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", steps], capture_output=True, text=True,
                                    env=dict(env, MINUTES_TEST_OUT=str(out)), timeout=60)
            calls = out.read_text(encoding="utf-8").splitlines() if out.is_file() else []
            # 履歴の検査の引数は、起点に解決できるもの（「.」・絶対パスなど）を同じ呼び出しとして比べる（空の引数は認めない。
            # step3_history_case と同じ）。順序と回数はそのまま比べる
            for at, call in enumerate(calls):
                name, _, argument = call.partition(" ")
                if name == "gitleaks-history.sh" and argument and Path(repository, argument).resolve() == repository.resolve():
                    calls[at] = history
            if result.returncode != 0 or ("== 開発ツールの照合" in result.stdout) != want or calls != want_calls:
                failures.append(f"check: --steps {steps} の先の照合が期待（{want}）と違う（終了コード {result.returncode}・呼び出し {calls}）\n"
                                f"{(result.stdout + result.stderr).strip()}")


def step3_history_case() -> None:
    """check.sh の手順 3 が、履歴の検査に scripts/gitleaks-history.sh を作業ツリーの起点で呼ぶこと。

    手順 3 が別のコマンドに変わると、下の陽性の試験（gitleaks-history.sh を直接実行する）が手順 3 を試験しなくなる。
    本文の文字列ではなく、一時のリポジトリに写した check.sh の手順 3 を実行し、代わりのスクリプトが記録した
    呼び出し（実行した場所と引数）で確かめる（引用符や書き方の違いに左右されない）。
    """
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        env.pop("MINUTES_CHECK_ISOLATED", None)
        repository = Path(directory) / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / ".tools").mkdir()  # 写す開発ツール
        out = Path(directory) / "recorded"
        (repository / "scripts" / "check.sh").write_text((SCRIPTS / "check.sh").read_text(encoding="utf-8"), encoding="utf-8")
        (repository / "scripts" / "test-checks.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        # 手順 0 なしで手順 3 を実行するので、check.sh は先に開発ツールを照合する。照合の代わり（成功する）を置く
        (repository / "scripts" / "setup-tools.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (repository / "scripts" / "setup-tools.sh").chmod(0o755)
        # 履歴の検査の代わり: 実行した場所と引数を NUL で区切って記録する（改行を含むパスを 1 つの記録として読む）
        (repository / "scripts" / "gitleaks-history.sh").write_text(
            '#!/bin/sh\n{ printf \'%s\\0\' "$(pwd -P)"; for argument in "$@"; do printf \'%s\\0\' "$argument"; done; } > "$MINUTES_TEST_OUT"\n',
            encoding="utf-8")
        # 手順 3 の最後の録音データの混入の検査の代わり（本物は test_tracked で試す）
        (repository / "scripts" / "check-tracked-files.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("check.sh", "test-checks.py", "gitleaks-history.sh", "check-tracked-files.py"):
            (repository / "scripts" / name).chmod(0o755)
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", "commit", "-q", "--allow-empty",
                        "-m", "base"], env=env, check=True, capture_output=True)
        result = subprocess.run([str(repository / "scripts" / "check.sh"), "--steps", "3"], capture_output=True, text=True,
                                env=dict(env, MINUTES_TEST_OUT=str(out)), timeout=60)
        recorded = [os.fsdecode(item) for item in out.read_bytes().split(b"\0")[:-1]] if out.is_file() else []
        # 空の引数は Path(repository, "") が起点そのものになり一致してしまうので、解決する前に拒否する（「.」などは認める）
        if result.returncode != 0 or len(recorded) != 2 or Path(recorded[0]) != repository.resolve() \
                or not recorded[1] or Path(repository, recorded[1]).resolve() != repository.resolve():
            failures.append(f"gitleaks: check.sh の手順 3 が scripts/gitleaks-history.sh で全履歴を検査していない"
                            f"（終了コード {result.returncode}・記録 {recorded}）\n{(result.stdout + result.stderr).strip()}")


def gitleaks_broken_history_case() -> None:
    """gitleaks-history.sh が、欠けたオブジェクトで全履歴を読めないリポジトリを「検出なし」で通さないこと。"""
    global count
    count += 1
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]
        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        for index in range(2):
            (repository / "a.txt").write_text(f"content {index}\n", encoding="utf-8")
            for arguments in (["add", "a.txt"], ["commit", "-q", "-m", str(index)]):
                subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True)
        # 最初のコミットの blob を消す（ゆるいオブジェクトのファイルを削除する）
        blob = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD~1:a.txt"], env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
        (repository / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        expect("gitleaks: 全履歴を読めないリポジトリは通さない", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], 1,
               "git fsck", env=env)


def gitleaks_traversal_case() -> None:
    """gitleaks 自身の git log が失敗・中断・標準エラーに出力した場合に「検出なし」で通さないこと。

    全履歴の走査は gitleaks の 1 回だけであること（同じ走査を先に別に行わない）も確かめる。
    """
    global count
    real = shutil.which("git")
    assert real is not None
    for label, failure, want in (("失敗しない", "exit 0", 0), ("終了コード 3", "exit 3", 1), ("強制終了", "kill -9 $$", 1),
                                 ("標準エラーだけ", "echo warning: partial >&2; exit 0", 1)):
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository, shims, seen = Path(directory) / "repository", Path(directory) / "shims", Path(directory) / "seen"
            subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", "commit", "-q", "--allow-empty",
                            "-m", "base"], env=env, check=True, capture_output=True)
            shims.mkdir()
            (shims / "git").write_text(
                '#!/bin/bash\ncase " $* " in *" log "*) ;; *) exec "$MINUTES_TEST_GIT" "$@" ;; esac\n'
                'echo log >> "$MINUTES_TEST_SEEN"\n'
                f'"$MINUTES_TEST_GIT" "$@"\n{failure}\n', encoding="utf-8")
            (shims / "git").chmod(0o755)
            expect(f"gitleaks: 走査中の git log が{label}", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], want,
                   "最後まで終わらなかった" if want else "no leaks found",
                   env=dict(env, PATH=f"{shims}:{env['PATH']}", MINUTES_TEST_GIT=real, MINUTES_TEST_SEEN=str(seen)))
            recorded = seen.read_text(encoding="utf-8") if seen.is_file() else ""
            if recorded != "log\n":
                failures.append(f"gitleaks: 走査中の git log が{label}: 全履歴の走査が 1 回でない（{recorded!r}）")


def gitleaks_signature_case() -> None:
    """利用者の設定 log.showSignature=true で、署名の検証の出力（標準エラー）を走査の失敗と取り違えないこと。

    署名つきのコミットを作り、検証のプログラムを起動できない設定にする（git が標準エラーに書く）。走査そのものの失敗の検出は gitleaks_traversal_case。
    """
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"

        def git(*arguments: str, content: Optional[str] = None) -> str:
            return subprocess.run(["git", "-C", str(repository), *arguments], env=env, check=True, capture_output=True, text=True,
                                  input=content).stdout.strip()

        subprocess.run(["git", "init", "-q", "--template=", "-b", "main", str(repository)], env=env, check=True, capture_output=True)
        tree = git("mktree", content="")
        who = "test <test@example.invalid> 0 +0000"
        signature = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----\n"
        commit = git("hash-object", "-t", "commit", "-w", "--stdin",
                     content=f"tree {tree}\nauthor {who}\ncommitter {who}\n{signature}\nbase\n")
        git("update-ref", "refs/heads/main", commit)
        git("config", "log.showSignature", "true")
        git("config", "gpg.program", str(Path(directory) / "missing"))
        # 対照: 指定のない git log は標準エラーに書く（試験が効いていること）
        plain = subprocess.run(["git", "-C", str(repository), "log"], env=env, capture_output=True, text=True)
        if plain.returncode != 0 or not plain.stderr.strip():
            failures.append("gitleaks: 署名の試験の対照が標準エラーに書かない（試験にならない）")
        expect("gitleaks: log.showSignature の署名の表示", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], 0,
               "no leaks found", env=env)


def gitleaks_rename_case() -> None:
    """名前の変更の検出の上限（diff.renameLimit）を超えた警告（標準エラー）を、走査の失敗と取り違えないこと。"""
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(*arguments: str) -> "subprocess.CompletedProcess[str]":
            return subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True, text=True)

        repository.mkdir()
        git("init", "-q", "--template=", "-b", "main")
        for number in range(4):
            (repository / f"old{number}").write_text("".join(f"{line}\n" for line in range(number, 40)), encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        for number in range(4):
            path = repository / f"new{number}"
            (repository / f"old{number}").rename(path)
            path.write_text(path.read_text(encoding="utf-8") + f"added {number}\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "renamed")
        git("config", "diff.renameLimit", "1")
        if not git("log", "-p", "-U0", "--all").stderr.strip():
            failures.append("gitleaks: 名前の変更の試験の対照が標準エラーに書かない（試験にならない）")
        expect("gitleaks: 名前の変更の検出の上限", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], 0, "no leaks found", env=env)


def gitleaks_notes_case() -> None:
    """設定 notes.displayRef の指す参照が壊れている警告（標準エラー）を走査の失敗と取り違えず、注記のコミットの鍵は検出すること。"""
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={os.devnull}"]

        def git(*arguments: str) -> "subprocess.CompletedProcess[str]":
            return subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True, capture_output=True, text=True)

        repository.mkdir()
        git("init", "-q", "--template=", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "base")
        git("config", "notes.displayRef", "refs/notes/missing")
        if not git("log", "-p", "-U0", "--all").stderr.strip():
            failures.append("gitleaks: 注記の試験の対照が標準エラーに書かない（試験にならない）")
        expect("gitleaks: ない注記の参照の警告", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], 0, "no leaks found", env=env)
        git("notes", "add", "-m", 'token = "' + "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi" + '"', "HEAD")
        expect("gitleaks: 注記のコミットの鍵", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], 1, "leaks found: 1", env=env)


def gitleaks_relative_case() -> None:
    """設定 diff.relative=true でサブディレクトリを渡しても、その外の鍵を検出すること。"""
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        (repository / "sub").mkdir(parents=True)
        (repository / "sub" / "file.txt").write_text("file\n", encoding="utf-8")
        (repository / "config.txt").write_text('token = "' + "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi" + '"\n', encoding="utf-8")
        commit_all(repository, env)
        subprocess.run(["git", "-C", str(repository), "config", "diff.relative", "true"], env=env, check=True, capture_output=True)
        for label, target in (("サブディレクトリ", repository / "sub"), ("直下", repository)):
            expect(f"gitleaks: diff.relative の{label}", [str(SCRIPTS / "gitleaks-history.sh"), str(target)], 1, "leaks found: 1", env=env)


def gitleaks_prefix_case() -> None:
    """差分の見出しの接頭辞を変える設定があっても、鍵を検出し、報告のファイルの名前が正しいこと。"""
    global count
    for key, value in (("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"), ("diff.srcPrefix", "x/y/"), ("diff.dstPrefix", "x/y/")):
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository, report = Path(directory) / "repository", Path(directory) / "report.json"
            (repository / "sub").mkdir(parents=True)
            (repository / "sub" / "config.txt").write_text('token = "' + "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi" + '"\n', encoding="utf-8")
            commit_all(repository, env)
            subprocess.run(["git", "-C", str(repository), "config", key, value], env=env, check=True, capture_output=True)
            expect(f"gitleaks: {key}", [str(SCRIPTS / "gitleaks-history.sh"), str(repository), "--report-format", "json",
                                        "--report-path", str(report)], 1, "leaks found: 1", env=env)
            count += 1
            decoded = gitleaks_report(report, f"gitleaks: {key}", {"File": str}) if report.is_file() else None
            if report.is_file() and decoded is None:
                continue
            names = [item["File"] for item in decoded] if decoded is not None else None
            if names != ["sub/config.txt"]:
                failures.append(f"gitleaks: {key} で報告のファイルの名前が違う（{names}）")


def gitleaks_order_file_case() -> None:
    """設定 diff.orderFile が指すファイルがなくても、読める履歴を検査すること（鍵があれば検出し、なければ通す）。"""
    for label, content, code, want in (("鍵がある", 'token = "' + "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi" + '"', 1, "leaks found: 1"),
                                       ("鍵がない", "clean", 0, "no leaks found")):
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository = Path(directory) / "repository"
            repository.mkdir()
            (repository / "config.txt").write_text(content + "\n", encoding="utf-8")
            commit_all(repository, env)
            subprocess.run(["git", "-C", str(repository), "config", "diff.orderFile", str(Path(directory) / "missing")], env=env,
                           check=True, capture_output=True)
            expect(f"gitleaks: ない diff.orderFile（{label}）", [str(SCRIPTS / "gitleaks-history.sh"), str(repository)], code, want, env=env)


def gitleaks_format_case() -> None:
    """設定 format.pretty・log.abbrevCommit によらず、検出した鍵を完全なコミットの ID で報告すること。"""
    global count
    for key, value in (("format.pretty", "oneline"), ("log.abbrevCommit", "true")):
        count += 1
        with tempfile.TemporaryDirectory() as directory:
            env = isolated_git_env()
            repository, report = Path(directory) / "repository", Path(directory) / "report.json"
            identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", f"core.hooksPath={os.devnull}"]

            def git(*arguments: str) -> str:
                return subprocess.run(["git", "-C", str(repository), *identity, *arguments], env=env, check=True,
                                      capture_output=True, text=True).stdout.strip()

            repository.mkdir()
            git("init", "-q", "--template=", "-b", "main")
            (repository / "config.txt").write_text('token = "' + "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi" + '"\n', encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            git("config", key, value)
            result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository), "--report-format", "json",
                                     "--report-path", str(report)], env=env, capture_output=True, text=True)
            decoded = gitleaks_report(report, f"gitleaks: {key}={value}", {"Commit": str}) if report.is_file() else None
            if report.is_file() and decoded is None:
                continue
            commits = [item["Commit"] for item in decoded] if decoded is not None else None
            if result.returncode != 1 or commits != [git("rev-parse", "HEAD")]:
                failures.append(f"gitleaks: {key}={value} の報告が違う（終了コード {result.returncode}・{commits}）\n{result.stderr.strip()}")


def gitleaks_message_case() -> None:
    """追加した行と同じ値がコミットのメッセージにあっても、どの形式の報告にも値を残さず、コミット・ファイル・行は残すこと。"""
    global count
    fake = "ghp_" + "x8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    with tempfile.TemporaryDirectory() as directory:
        env = isolated_git_env()
        repository = Path(directory) / "repository"
        repository.mkdir()
        (repository / "config.txt").write_text(f'name = "a"\ntoken = "{fake}"\n', encoding="utf-8")
        for arguments in (["init", "-q", "--template=", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", f"subject {fake}\n\nbody {fake}"]):
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *arguments], env=env, check=True,
                           capture_output=True)
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
        for kind in ("json", "csv", "junit", "sarif"):
            count += 1
            # 形式ごとに別の場所へ書かせ、その実行が報告を作ったことを確かめてから読む（前の形式の報告を読まない）
            report = Path(directory) / f"report.{kind}"
            result = subprocess.run([str(SCRIPTS / "gitleaks-history.sh"), str(repository), "--report-format", kind, "--report-path",
                                     str(report)], env=env, capture_output=True, text=True)
            if not report.is_file():
                failures.append(f"gitleaks: {kind} の報告がない（終了コード {result.returncode}）\n{result.stderr.strip()}")
                continue
            text = report.read_text(encoding="utf-8")
            if result.returncode != 1 or fake in text + result.stdout + result.stderr or head not in text or "config.txt" not in text:
                failures.append(f"gitleaks: {kind} の報告にメッセージの値が残る・調べる手がかりがない（終了コード {result.returncode}）\n{text}")
            if kind == "json" and text:
                decoded = gitleaks_report(report, "gitleaks: メッセージの値の json の報告", {
                    "Commit": str, "File": str, "StartLine": int, "Author": str, "Email": str, "Date": str})
                if decoded is None:
                    continue
                found = [(item["Commit"], item["File"], item["StartLine"], item["Author"], item["Email"], bool(item["Date"]))
                         for item in decoded]
                if found != [(head, "config.txt", 2, "Test User", "test@example.invalid", True)]:
                    failures.append(f"gitleaks: 報告の手がかりが違う（{found}）")


def gitleaks_merge_case() -> None:
    """履歴の検査（check.sh の手順 3）が、通常のコミットとマージのコミットで足した行の両方を検出すること。

    マージの解決で足した行（どちらの親にもない行）は、git log の既定ではマージの差分を出さないため見落とされる。
    陽性の試験は手順 3 と同じコマンド（scripts/gitleaks-history.sh）を実行する。指定（--log-opts）がない gitleaks の
    既定の動作では見落とすことも対照として確かめる（試験が効いていることの確認）。
    """
    global count
    gitleaks = str(TOOLS / "bin" / "gitleaks")
    history = str(SCRIPTS / "gitleaks-history.sh")
    step3_history_case()
    step3_tool_verify_case()
    step_order_verify_case()
    count += 1
    # 偽の鍵は実行時に組み立てる（このファイル自体が検出されないようにする）
    normal_key = "ghp_" + "a8Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    merge_key = "ghp_" + "b9Kd2mP9qL4vR7tY1w" + "Z5nB3cF6hJ0sGaUeXi"
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        repository.mkdir()

        # 開発者のグローバル・システムの設定と属性（XDG_CONFIG_HOME の下を含む）、フック（core.hooksPath・テンプレートのフックを
        # 含む）の影響を受けないようにする。フックなどから実行された場合に残る、リポジトリを指す変数（GIT_DIR・GIT_INDEX_FILE
        # など）も外す（isolated_git_env）
        isolated = isolated_git_env()

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
        direct = [gitleaks, "git", "--no-banner", "--no-color", "--redact", "--config", str(ROOT / ".gitleaks.toml")]
        # （名前, 報告の指定の前の引数, 後の引数, 期待）。gitleaks-history.sh はリポジトリの後ろに追加の引数を取る
        cases = (("手順 3 のコマンド", [history, str(repository)], [], {"normal.txt", "merge.txt"}),
                 ("指定なし（既定の git log）", direct, [str(repository)], {"normal.txt"}))
        for index, (label, before, after, want) in enumerate(cases):
            count += 1
            report = Path(directory) / f"report-{index}.json"
            result = launched(f"gitleaks: マージの差分（{label}）", before + ["--report-format", "json", "--report-path", str(report)] + after,
                              env=isolated)
            # どちらの場合も鍵を検出するので、終了コードは 1（検出あり）であること
            if result.returncode != 1:
                failures.append(f"gitleaks: マージの差分（{label}）の終了コードが {result.returncode}（期待 1）\n{result.stderr.strip()}")
            # 形の違う報告も名前つきの失敗にして次へ進む（指定なしの対照と、自己試験の集計を続ける）
            decoded = gitleaks_report(report, f"gitleaks: マージの差分（{label}）", {"File": str})
            if decoded is None:
                continue
            found = {item["File"] for item in decoded}
            if found != want:
                failures.append(f"gitleaks: マージの差分（{label}）で検出したファイルが {sorted(found)}（期待 {sorted(want)}）")


def coverage_case(label: str, want_code: int, want_text: str, count_value: object, covered_value: object, nested: bool = False,
                  target: str = "90", forbidden: str = "", duplicate: bool = False, blocks: Optional[int] = None) -> None:
    global count
    with tempfile.TemporaryDirectory() as directory:
        sources = Path(directory) / "Sources"
        sources.mkdir()
        report = Path(directory) / "report.json"
        # swift test の記録と同じく、起点（Package）は実体のパスに解決して書く（一時ディレクトリの /var は /private/var へのリンク）
        recorded = Path(os.path.realpath(sources)) / "A.swift"
        report.write_text(json.dumps({"data": [{"files": [{"filename": str(recorded), "summary": {
            "lines": {"count": count_value, "covered": covered_value}}}]}]}), encoding="utf-8")
        if duplicate:
            report.write_text(replaced(report.read_text(encoding="utf-8"), '"covered":', '"covered": 20, "covered":'), encoding="utf-8")
        if blocks is not None:  # data の塊の数を変える（0 個・同じ塊を複数）
            block = json.loads(report.read_text(encoding="utf-8"))["data"][0]
            report.write_text(json.dumps({"data": [block] * blocks}), encoding="utf-8")
        if nested:
            report.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")  # json の読み込みが RecursionError になる深さ
        command = [str(SCRIPTS / "coverage-report.py"), str(report), "--sources", str(sources), "--target", target]
        # 終了コード・期待する文字列・出てはいけない文字列を、同じ 1 回の実行の結果で確かめる
        count += 1
        try:
            code, output = run(command)
        except OSError as error:
            failures.append(f"coverage: {label}: {command[0]} を起動できない: {error}")
            return
        if code != want_code:
            failures.append(f"coverage: {label}: 終了コードが {code}（期待 {want_code}）\n{output.strip()}")
        elif want_text and want_text not in output:
            failures.append(f"coverage: {label}: 出力に「{want_text}」がない\n{output.strip()}")
        if forbidden and forbidden in output:
            failures.append(f"coverage: {label}: 出力に「{forbidden}」がある")


def test_coverage() -> None:
    coverage_case("目標以上", 0, "行 95.0%", 20, 19)
    coverage_case("目標未満は警告だけ", 0, "警告", 10, 5)
    # 行数の記録は整数（bool を除く）で 0 <= covered <= count のものだけを数える
    coverage_case("covered が count を超える", 2, "不正", 10, 11)
    coverage_case("covered が負", 2, "不正", 10, -1)
    coverage_case("count が文字列", 2, "不正", "10", 5)
    coverage_case("count が真偽値", 2, "不正", True, 0)
    coverage_case("covered が小数", 2, "不正", 10, 5.0)
    # 目標は 0〜100 の有限の数だけ（NaN・無限大・範囲外で比較を壊さない）。境界の 0 と 100 は認める
    for value in ("nan", "inf", "-1", "100.5", "x"):
        coverage_case(f"目標が {value}", 2, "--target", 20, 19, target=value)
    coverage_case("目標が 0", 0, "行 95.0%", 20, 19, target="0")
    coverage_case("目標が 100（未満は警告）", 0, "警告", 20, 19, target="100")
    # 目標にわずかに届かない値を、丸めて目標以上に見せない（89.96% を 90.0% と表示しない）。小数の目標は丸めずに表示する
    coverage_case("目標にわずかに届かない", 0, "行 89.96%", 10000, 8996, target="90")
    coverage_case("小数の目標", 0, "目標の 90.5% に届いていない", 20, 18, target="90.5")
    # 浮動小数点では目標ちょうどに丸まる不足を警告し、目標ちょうどは警告しない
    coverage_case("浮動小数点で丸まる不足", 0, "警告: カバレッジが目標の 90%", 10**18, 9 * 10**17 - 1)
    coverage_case("浮動小数点で丸まる不足の表示", 0, "行 89.9999999999999999%", 10**18, 9 * 10**17 - 1)
    coverage_case("小数の目標ちょうど", 0, "行 57.1%", 1000, 571, target="57.1", forbidden="警告")
    coverage_case("割り切れない目標ちょうど", 0, "行 64.1%", 1000, 641, target="64.10", forbidden="警告")
    coverage_case("桁の多すぎる目標", 2, "--target", 20, 19, target="1e-999999999")
    coverage_case("深い入れ子の記録", 2, "RecursionError", 20, 19, nested=True)
    # 大きな行数でも、割ってから 100 を掛けるので float への変換の途中で桁あふれしない
    coverage_case("大きな行数", 0, "行 50.0%", 2 * 10**400, 10**400)
    # 矛盾する重複した鍵の記録は、後の値を採用せずに読めないものとする
    coverage_case("重複した鍵の記録", 2, "重複した鍵", 20, 5, duplicate=True)
    # data の塊は 1 つだけを認める（先頭だけを数えて、後ろの塊の記録を黙って捨てない）
    coverage_case("塊が 2 つの記録", 2, "ちょうど 1 つでない", 20, 19, blocks=2)
    coverage_case("塊のない記録", 2, "ちょうど 1 つでない", 20, 19, blocks=0)
    coverage_case("塊が 1 つの記録", 0, "行 95.0%", 20, 19, blocks=1)
    coverage_repeated_case()
    coverage_symlink_case()
    coverage_root_case()
    coverage_measured_case()


def coverage_repeated_case() -> None:
    """数える対象の同じファイルの記録が重複していれば終了コード 2（綴りの違いを含む）。対象の外の重複は関わらない。"""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        report = root / "report.json"
        for label, names, want, want_text in (("同じ綴り", ("Sources/A.swift", "Sources/A.swift"), 2, "重複している"),
                                              ("違う綴り", ("Sources/A.swift", "Sources/./Sub/../A.swift"), 2, "重複している"),
                                              ("違う綴りが先", ("Sources/./A.swift", "Sources/A.swift"), 2, "重複している"),
                                              ("対象の外", ("Sources/A.swift", "Tests/B.swift", "Tests/B.swift"), 0, "（1 ファイル"),
                                              ("別のファイル", ("Sources/A.swift", "Sources/B.swift"), 0, "（2 ファイル")):
            report.write_text(json.dumps({"data": [{"files": [
                {"filename": f"{root}/{name}", "summary": {"lines": {"count": 10, "covered": 10}}} for name in names]}]}), encoding="utf-8")
            expect(f"coverage: ファイルの記録の重複（{label}）", [str(SCRIPTS / "coverage-report.py"), str(report), "--measured-sources",
                                                        f"{root}/Sources", "--target", "90"], want, want_text)


def coverage_root_case() -> None:
    """起点が「/」でも配下のファイルを数え、名前の似た隣のディレクトリは数えないこと。"""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        (root / "Sources").mkdir()
        report = root / "report.json"
        report.write_text(json.dumps({"data": [{"files": [
            {"filename": str(root / name / "A.swift"), "summary": {"lines": {"count": 10, "covered": covered}}}
            for name, covered in (("Sources", 10), ("Sources-old", 0))]}]}), encoding="utf-8")
        for label, sources, want in (("「/」", "/", "行 50.0%（2 ファイル"), ("似た名前の隣", f"{root}/Sources", "行 100.0%（1 ファイル"),
                                     ("末尾の区切り", f"{root}/Sources//", "行 100.0%（1 ファイル")):
            expect(f"coverage: 記録した起点が{label}", [str(SCRIPTS / "coverage-report.py"), str(report), "--measured-sources", sources,
                                               "--target", "90"], 0, want)
            expect(f"coverage: 起点が{label}", [str(SCRIPTS / "coverage-report.py"), str(report), "--sources", sources, "--target", "90"], 0, want)


def coverage_measured_case() -> None:
    """--measured-sources を、現在のファイルシステムで解決せずに記録のまま使うこと。相対パス・--sources との併用は認めない。"""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        (root / "other").mkdir()
        (root / "Sources").symlink_to("other")  # 測った後にリンクへ置き換わった場所
        report = root / "report.json"
        report.write_text(json.dumps({"data": [{"files": [{"filename": str(root / "Sources" / "A.swift"), "summary": {
            "lines": {"count": 10, "covered": 10}}}]}]}), encoding="utf-8")
        command = [str(SCRIPTS / "coverage-report.py"), str(report), "--target", "90"]
        expect("coverage: 記録した起点を解決し直さない", [*command, "--measured-sources", str(root / "Sources")], 0, "行 100.0%（1 ファイル")
        expect("coverage: 相対パスの記録した起点", [*command, "--measured-sources", "Sources"], 2, "絶対パスでない")
        expect("coverage: 起点の併用", [*command, "--sources", "x", "--measured-sources", "/x"], 2, "not allowed")
        expect("coverage: 起点の指定がない", command, 2, "required")


def coverage_symlink_case() -> None:
    """数える対象かを、記録したパスで決めること（現在のファイルシステムのシンボリックリンクをたどらない）。

    Sources の中のリンク（外のファイルを指す）は記録のパスが Sources の中なので数え、Sources の外のリンク（Sources の中の
    ファイルを指す）は数えない。記録したパスが相対パスなら読めないものとする。
    """
    global count
    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        sources, other = root / "Sources", root / "Tests"
        sources.mkdir()
        other.mkdir()
        (other / "Outside.swift").write_text("", encoding="utf-8")
        (sources / "Linked.swift").symlink_to(other / "Outside.swift")
        (sources / "Real.swift").write_text("", encoding="utf-8")
        (other / "Alias.swift").symlink_to(sources / "Real.swift")
        files = [(sources / "Linked.swift", 10, 5), (other / "Alias.swift", 100, 0)]
        for label, entries, want_code, want_text in (
            ("記録したパスで数える", files, 0, "行 50.0%（1 ファイル"),
            ("相対パスの記録", [(Path("Sources/Linked.swift"), 10, 5)], 2, "絶対パスでない"),
        ):
            report = root / "report.json"
            report.write_text(json.dumps({"data": [{"files": [
                {"filename": str(path), "summary": {"lines": {"count": lines, "covered": covered}}}
                for path, lines, covered in entries]}]}), encoding="utf-8")
            expect(f"coverage: {label}", [str(SCRIPTS / "coverage-report.py"), str(report), "--sources", str(sources),
                                          "--target", "90"], want_code, want_text)


# 設計決定を §3 に置く基本設計（architecture.md）
ARCHITECTURE = (
    "# 全体構成\n\n## 1. 概要\n\n満たす要件: FR-AAA-01\n\n"
    "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n"
    "## 3. 設計決定\n\n満たす要件: FR-AAA-01\n\n| ID | 決定 |\n| --- | --- |\n| BD-ARC-01 | 決定 |\n\n"
    "## 4. テスト観点\n\n満たす要件: TST-AAA-01\n"
)


# 合格する .gitignore（check-tracked-files.py が求める行。SEC-SUP-04）
OK_GITIGNORE = "\n".join([
    "# 録音データ", "*.wav", "*.m4a", "*.caf", "*.zip", "", "*.sqlite", "*.sqlite-wal", "*.sqlite-shm",
    ".env", ".env.*", "xcuserdata/", "DerivedData/", ".build/", "/build/", "*.xcresult", ".DS_Store",
]) + "\n"


def tracked_case(label: str, want_code: int, want_text: str, ignore: Optional[str] = OK_GITIGNORE,
                 files: Optional[Dict[str, str]] = None, repository: bool = True,
                 after_add: Optional[Callable[[Path], None]] = None,
                 extra_env: Optional[Callable[[Path], Dict[str, str]]] = None) -> None:
    """一時ディレクトリに Git のリポジトリを作り、.gitignore を越えて（--force）ファイルをインデックスに入れてから検査する。
    after_add はインデックスに入れた後に作業ツリー・インデックスを変える。extra_env は検査の実行にだけ足す変数を返す。
    準備と検査は、開発者の設定とリポジトリを指す変数を引き継がない 1 つの環境（isolated_git_env）で行う。"""
    env = isolated_git_env()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contents = {"Sources/App.swift": "let a = 1\n"}
        contents.update(files or {})
        if ignore is not None:
            contents[".gitignore"] = ignore
        for name, content in contents.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content, encoding="utf-8")
        if repository:
            subprocess.run(["git", "init", "--quiet", directory], check=True, capture_output=True, env=env)
            subprocess.run(["git", "-C", directory, "add", "--force", "--all"], check=True, capture_output=True, env=env)
        if after_add is not None:
            after_add(root)
        run_env = dict(env)
        if extra_env is not None:
            run_env.update(extra_env(root))
        expect(f"tracked: {label}", [str(SCRIPTS / "check-tracked-files.py"), "--root", directory], want_code, want_text,
               env=run_env)


def tracked_git(root: Path, *arguments: str) -> None:
    """tracked_case の after_add の中で、同じ隔離した環境で git を実行する。"""
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, env=isolated_git_env())


def tracked_other_repository(root: Path) -> Dict[str, str]:
    """検査するリポジトリと別の、違反を持つリポジトリを GIT_DIR・GIT_WORK_TREE で指す（検査はこれを読まずに --root を読む）。"""
    other = root.parent / f"{root.name}-other"
    other.mkdir()
    (other / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (other / "leak.wav").write_text("RIFF", encoding="utf-8")
    env = isolated_git_env()
    subprocess.run(["git", "init", "--quiet", str(other)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(other), "add", "--force", "--all"], check=True, capture_output=True, env=env)
    return {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other), "GIT_COMMON_DIR": str(other / ".git"),
            "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects")}


def tracked_alternate_index(root: Path) -> Dict[str, str]:
    """別のインデックス（録音データを入れたもの）を GIT_INDEX_FILE で渡す。検査はこの変数を外さずに使う。"""
    index = root / ".git" / "alternate-index"
    env = isolated_git_env()
    env["GIT_INDEX_FILE"] = str(index)
    (root / "staged.wav").write_text("RIFF", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "read-tree", "--empty"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", "--force", "--all"], check=True, capture_output=True, env=env)
    return {"GIT_INDEX_FILE": str(index)}


def tracked_replace_gitignore(root: Path) -> None:
    """インデックスの .gitignore（必須の行が欠けたもの）の blob を、合格する内容の blob で置き換える（git replace）。
    検査は置き換えを使わずに、インデックスが指す元の blob を読む。"""
    env = isolated_git_env()
    good = root / ".git" / "good-gitignore"
    good.write_text(OK_GITIGNORE, encoding="utf-8")
    def output(*arguments: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, env=env)
        return result.stdout.decode("utf-8").strip()
    original = output("rev-parse", ":.gitignore")
    replacement = output("hash-object", "-w", str(good))
    output("replace", original, replacement)


def test_tracked() -> None:
    tracked_case("合格するリポジトリ", 0, "OK")
    tracked_case("似た名前のファイルは通る", 0, "OK",
                 files={"Sources/wave.swift": "", "docs/notes.wav.md": "", "docs/environment.md": "", ".envrc": "", "docs/builds.md": ""})
    tracked_case(".gitignore がない", 1, ".gitignore がない", ignore=None)
    tracked_case("録音データの行がない", 1, "*.wav", ignore=replaced(OK_GITIGNORE, "*.wav\n", ""))
    tracked_case("行がコメントになっている", 1, "*.m4a", ignore=replaced(OK_GITIGNORE, "*.m4a\n", "# *.m4a\n"))
    tracked_case("DB の行がない", 1, "*.sqlite-wal", ignore=replaced(OK_GITIGNORE, "*.sqlite-wal\n", ""))
    tracked_case("秘密情報の行がない", 1, ".env.*", ignore=replaced(OK_GITIGNORE, ".env.*\n", ""))
    tracked_case("個人の設定の行がない", 1, "xcuserdata/", ignore=replaced(OK_GITIGNORE, "xcuserdata/\n", ""))
    tracked_case("直下の build の行がない", 1, "/build/ の行がない", ignore=replaced(OK_GITIGNORE, "/build/\n", "build/\n"))
    tracked_case("追跡されている直下の build", 1, "build/App.o", files={"build/App.o": "x"})
    tracked_case("下位の build は追跡してよい", 0, "OK", files={"Sources/build/Generated.swift": "let b = 1\n"})
    tracked_case("除外を取り消す行", 1, "除外を取り消す行", ignore=OK_GITIGNORE + "!sample.wav\n")
    tracked_case("入れ子の .gitignore の取り消し", 1, "Tests/.gitignore", files={"Tests/.gitignore": "!*.wav\n"})
    tracked_case("入れ子の .gitignore に取り消しがなければ通る", 0, "OK", files={"Tests/.gitignore": "*.tmp\n"})
    tracked_case("追跡されている録音データ", 1, "Tests/Fixtures/meeting.wav", files={"Tests/Fixtures/meeting.wav": "RIFF"})
    tracked_case("大文字の拡張子", 1, "REC.WAV", files={"REC.WAV": "RIFF"})
    tracked_case("空白と日本語を含む名前", 1, "録音 1.m4a", files={"docs/録音 1.m4a": "x"})
    tracked_case("追跡されているアーカイブ", 1, "export.zip", files={"export.zip": "PK"})
    tracked_case("追跡されている DB", 1, "db/minutes.sqlite-wal", files={"db/minutes.sqlite-wal": "x"})
    tracked_case("追跡されている .env", 1, "config/.env", files={"config/.env": "TOKEN=x\n"})
    tracked_case("追跡されている .env.local", 1, ".env.local", files={".env.local": "TOKEN=x\n"})
    tracked_case("追跡されている個人の設定", 1, "xcuserdata", files={"App.xcodeproj/xcuserdata/me/state.plist": "x"})
    tracked_case("テストの結果の行がない", 1, "*.xcresult の行がない", ignore=replaced(OK_GITIGNORE, "*.xcresult\n", ""))
    tracked_case("追跡されているテストの結果", 1, "Run.xcresult/Info.plist", files={"build-logs/Run.xcresult/Info.plist": "x"})
    # 行は git と同じく LF だけで区切り、行末の CR と空白だけを除く（splitlines・rstrip の区切りと空白で必須の行を作らせない）
    tracked_case("CRLF の .gitignore は通る", 0, "OK", ignore=OK_GITIGNORE.replace("\n", "\r\n"))
    tracked_case("行末の空白は除く", 0, "OK", ignore=replaced(OK_GITIGNORE, "*.wav\n", "*.wav  \n"))
    tracked_case("コメントの中の U+2028 の後の型を行と見なさない", 1, "*.wav の行がない",
                 ignore=replaced(OK_GITIGNORE, "*.wav\n", "# 録音\u2028*.wav\n"))
    tracked_case("行末のタブは除かない", 1, "*.wav の行がない", ignore=replaced(OK_GITIGNORE, "*.wav\n", "*.wav\t\n"))
    # ファイル名の型は、祖先のディレクトリの名前にも当てはめる（git は *.wav に合うディレクトリの下をすべて除外する）
    tracked_case("名前が型に合うディレクトリの下のファイル", 1, "exports/rec.wav/notes.txt", files={"exports/rec.wav/notes.txt": "x"})
    tracked_case("名前が .env のディレクトリの下のファイル", 1, "config/.env/token", files={"config/.env/token": "x"})
    tracked_case("Git の管理下でない場所は合格にしない", 2, "読めない", repository=False)
    # .gitignore の規則は、インデックスの内容で判定する（作業ツリーだけの変更で合否を変えない）
    tracked_case("作業ツリーだけで必須の行を消しても、インデックスの内容で通る", 0, "OK",
                 after_add=lambda root: (root / ".gitignore").write_text(replaced(OK_GITIGNORE, "*.wav\n", ""), encoding="utf-8"))
    tracked_case("インデックスに必須の行がなければ、作業ツリーで直しても失敗する", 1, "*.wav の行がない",
                 ignore=replaced(OK_GITIGNORE, "*.wav\n", ""),
                 after_add=lambda root: (root / ".gitignore").write_text(OK_GITIGNORE, encoding="utf-8"))
    tracked_case("ステージした入れ子の取り消しは、作業ツリーで直しても失敗する", 1, "Tests/.gitignore",
                 files={"Tests/.gitignore": "!*.wav\n"},
                 after_add=lambda root: (root / "Tests/.gitignore").write_text("*.tmp\n", encoding="utf-8"))
    tracked_case("作業ツリーから消した入れ子の .gitignore も、インデックスの内容で判定する", 1, "Tests/.gitignore",
                 files={"Tests/.gitignore": "!*.wav\n"}, after_add=lambda root: (root / "Tests/.gitignore").unlink())
    tracked_case("追跡していない入れ子の .gitignore の取り消しは、作業ツリーの内容で判定する", 1, "Docs/.gitignore",
                 after_add=lambda root: ((root / "Docs").mkdir(), (root / "Docs/.gitignore").write_text("!*.zip\n", encoding="utf-8")))
    # それ自身が無視された .gitignore の取り消しの行も、git は使うので確かめる（無視されたディレクトリの中は git が読まない）
    tracked_case("無視された入れ子の .gitignore の取り消し", 1, "Docs/.gitignore",
                 after_add=lambda root: ((root / "Docs").mkdir(),
                                         (root / "Docs/.gitignore").write_text(".gitignore\n!*.wav\n", encoding="utf-8")))
    tracked_case("無視されたディレクトリの中の .gitignore は読まない", 0, "OK",
                 after_add=lambda root: ((root / "build/sub").mkdir(parents=True),
                                         (root / "build/sub/.gitignore").write_text("!*.wav\n", encoding="utf-8")))
    tracked_case("インデックスにない .gitignore は合格にしない", 1, "インデックスにない",
                 after_add=lambda root: tracked_git(root, "rm", "--cached", "--quiet", ".gitignore"))
    # リポジトリの場所を指す変数は外し、GIT_INDEX_FILE は残す
    tracked_case("外から引き継いだ GIT_DIR などで別のリポジトリを読まない", 0, "OK", extra_env=tracked_other_repository)
    tracked_case("GIT_INDEX_FILE で渡したインデックスを検査する", 1, "staged.wav", extra_env=tracked_alternate_index)
    # パスの指定の解釈を変える変数は外す（:(glob) を名前そのものとして読ませて、入れ子の .gitignore を見落とさない）
    tracked_case("外から引き継いだ GIT_LITERAL_PATHSPECS で入れ子の .gitignore を見落とさない", 1, "Tests/.gitignore",
                 files={"Tests/.gitignore": "!*.wav\n"}, extra_env=lambda root: {"GIT_LITERAL_PATHSPECS": "1"})
    # 置き換えのオブジェクトで、インデックスの .gitignore を合格する内容に見せかけさせない
    tracked_case("置き換えのオブジェクトを使わずにインデックスの内容を読む", 1, "*.wav の行がない",
                 ignore=replaced(OK_GITIGNORE, "*.wav\n", ""), after_add=tracked_replace_gitignore)


# 文書の検査の試験に使う、最小の文書一式（定義文書・基本設計・詳細設計・コード片の元のファイル）
FENCE = "`" * 3
DOCS = {
    "docs/README.md": "# 索引\n\n| ID | 決定 |\n| --- | --- |\n| UD-01 | 決定 |\n",
    "docs/01-requirements/requirements.md": (
        "# 要件\n\n| ID | 要件 | フェーズ |\n| --- | --- | --- |\n"
        "| FR-AAA-01 | 一 | MVP |\n| FR-AAA-02 | 二 | MVP |\n| FR-AAA-03 | 三 | P2 |\n\n"
        "| ID | 制約 |\n| --- | --- |\n| CON-01 | 制約 |\n"
    ),
    "docs/01-requirements/security-requirements.md": (
        "# SEC\n\n| ID | 要件 | フェーズ | MASVS |\n| --- | --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP | STORAGE |\n"
    ),
    "docs/01-requirements/test-requirements.md": "# TST\n\n| ID | 要件 |\n| --- | --- |\n| TST-AAA-01 | 一 |\n",
    "docs/01-requirements/dependencies-and-costs.md": "# COST\n\n| ID | 項目 |\n| --- | --- |\n| COST-01 | 一 |\n",
    "docs/02-basic-design/sample.md": (
        "# 基本設計\n\n| 項目 | 内容 |\n| --- | --- |\n| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |\n\n"
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


def linked_document_case() -> Path:
    """--update-snippets が、詳細設計のディレクトリの外を実体とするリンクの文書を書き直さないこと（起点の中でも）。"""
    global count
    directory = docs_case("リンクの文書の準備（整合した文書）", 0, "OK")
    outside = directory / "outside.md"
    content = f"# 外の文書\n\n<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho old\n{FENCE}\n"
    outside.write_text(content, encoding="utf-8")
    (directory / "docs/03-detailed-design/linked.md").symlink_to("../../outside.md")
    count += 1
    expect("docs: 詳細設計の外を指すリンクの文書は書き直さない",
           [str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"], 1, "詳細設計のディレクトリの外")
    if outside.read_text(encoding="utf-8") != content:
        failures.append("docs: --update-snippets が詳細設計の外のリンクの実体を書き換えた")
    return directory


def untracked_document_case() -> None:
    """解決の経路が起点の外や追跡しない場所（.tools など）を通る文書を、読まずに報告すること。"""
    global count
    for label, place in (("起点の外", None), ("追跡しない場所", ".tools")):
        directory = docs_case(f"{label}を指す文書の準備（整合した文書）", 0, "OK")
        with tempfile.TemporaryDirectory() as outside:
            # 読めば定義されていない ID で失敗する内容（読まずに報告したことを区別する）
            target = Path(outside) / "doc.md" if place is None else directory / place / "doc.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# 外\n\nFR-ZZZ-99\n", encoding="utf-8")
            (directory / "docs/extra.md").symlink_to(target)
            count += 1
            code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
            if code != 1 or "docs/extra.md: 文書を読まない" not in output or "FR-ZZZ-99" in output:
                failures.append(f"docs: {label}を指す文書を読まずに報告しない（終了コード {code}）\n{output.strip()}")


def resolution_order_case() -> None:
    """リンクのディレクトリの後の「..」を字面で取り除かず、ファイルシステムがたどる順に確かめること（起点の外へ出る経路の
    文書を読まない）。字面で取り除くと起点の中の別の文書（docs/sub/x.md）に見え、実際には起点の外の文書を読む。"""
    global count
    directory = docs_case("経路の順の準備（整合した文書）", 0, "OK")
    with tempfile.TemporaryDirectory() as outside:
        (Path(outside) / "inner").mkdir()
        (Path(outside) / "x.md").write_text("# 外\n\nFR-ZZZ-99\n", encoding="utf-8")
        sub = directory / "docs" / "sub"
        sub.mkdir()
        (sub / "x.md").write_text("# 中\n", encoding="utf-8")
        (sub / "link").symlink_to(Path(outside) / "inner")
        (directory / "docs" / "extra.md").symlink_to("sub/link/../x.md")
        count += 1
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
        if code != 1 or "docs/extra.md: 文書を読まない" not in output or "FR-ZZZ-99" in output:
            failures.append(f"docs: リンクのディレクトリの後の「..」を字面で取り除いて読んだ（終了コード {code}）\n{output.strip()}")


def file_parent_case() -> None:
    """通常のファイルの「..」を字面で親に戻して文書を読まないこと。実在するディレクトリの「..」を経由するリンクは読むこと。"""
    global count
    for label, target, want in (("通常のファイルの「..」", "README.md/../valid.md", 1), ("ディレクトリの「..」", "sub/../valid.md", 0)):
        directory = docs_case(f"{label}の準備（整合した文書）", 0, "OK")
        (directory / "docs" / "sub").mkdir()
        (directory / "docs" / "valid.md").write_text("# 文書\n", encoding="utf-8")
        (directory / "docs" / "extra.md").symlink_to(target)
        count += 1
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
        if code != want or (want and "docs/extra.md: 文書を読まない" not in output):
            failures.append(f"docs: {label}を経由するリンクの扱いが違う（終了コード {code}）\n{output.strip()}")


def untracked_snippet_case() -> None:
    """コード片の元のファイルが追跡しない場所（.tools など。リンクで経由するものを含む）にあれば、照合でも書き直しでも使わないこと。"""
    global count
    detailed = "docs/03-detailed-design/sample.md"
    for label, source in (("直接", ".tools/sample.sh"), ("リンクの先", "scripts/link.sh"), ("経由するリンク", "scripts/through.sh")):
        directory = docs_case(f"追跡しない元のファイル（{label}）の準備", 0, "OK")
        (directory / ".tools").mkdir()
        (directory / ".tools" / "sample.sh").write_text("echo generated\n", encoding="utf-8")
        (directory / "scripts" / "link.sh").symlink_to("../.tools/sample.sh")
        (directory / ".tools" / "back.sh").symlink_to("../scripts/sample.sh")
        (directory / "scripts" / "through.sh").symlink_to("../.tools/back.sh")
        path = directory / detailed
        content = replaced(path.read_text(encoding="utf-8"), "<!-- file: scripts/sample.sh -->", f"<!-- file: {source} -->")
        if source != "scripts/through.sh":
            content = replaced(content, "echo one\necho two\necho three\n", "echo generated\n")
        path.write_text(content, encoding="utf-8")
        for options in ([], ["--update-snippets"]):
            count += 1
            code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), *options])
            if code != 1 or "追跡しない場所" not in output or path.read_text(encoding="utf-8") != content:
                failures.append(f"docs: 追跡しない元のファイル（{label}・{options}）の扱いが違う（終了コード {code}）\n{output.strip()}")
        # 書き直しでも、追跡しない場所の内容を取り込まない
        stale = content.replace("echo generated\n", "echo old\n", 1) if "echo generated" in content else content
        path.write_text(stale, encoding="utf-8")
        count += 1
        run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
        if path.read_text(encoding="utf-8") != stale:
            failures.append(f"docs: 追跡しない元のファイル（{label}）でコード片を書き直した")


def quoted_snippet_case() -> None:
    """引用（「> 」）の中のコード片の印を、同じ深さの引用の中のフェンスとだけ対応させて照合し、--update-snippets が引用の印を
    保って書き直すこと。深さの違う引用・引用の外のフェンスとは対応させず、対応するフェンスがないと報告すること。"""
    global count
    detailed = "docs/03-detailed-design/sample.md"
    plain = f"<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho one\necho two\necho three\n{FENCE}\n"
    quoted = f"> <!-- file: scripts/sample.sh -->\n>\n> {FENCE}bash\n> echo one\n> echo two\n> echo three\n> {FENCE}\n"
    for label, block, want, text in (
            ("一致する", quoted, 0, "OK"),
            ("一致しない", quoted.replace("> echo two\n", "> echo old\n"), 1, "一致しない"),
            ("印だけが引用の中", f"> <!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho one\necho two\necho three\n{FENCE}\n",
             1, "コードフェンスがない"),
            ("フェンスだけが引用の中", quoted.replace("> <!-- file", "<!-- file", 1).replace(">\n> " + FENCE + "bash", "\n> " + FENCE + "bash", 1),
             1, "コードフェンスがない"),
            ("フェンスがさらに深い引用", quoted.replace("> " + FENCE + "bash\n> echo one\n> echo two\n> echo three\n> " + FENCE,
                                                       "> > " + FENCE + "bash\n> > echo one\n> > echo two\n> > echo three\n> > " + FENCE),
             1, "コードフェンスがない")):
        count += 1
        directory = docs_case(f"引用の中のコード片（{label}）の準備", 0, "OK")
        path = directory / detailed
        path.write_text(replaced(path.read_text(encoding="utf-8"), plain, block), encoding="utf-8")
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
        if code != want or text not in output:
            failures.append(f"docs: 引用の中のコード片（{label}）の扱いが違う（終了コード {code}・期待 {want}）\n{output.strip()}")
            continue
        if label != "一致しない":
            continue
        # 書き直しは引用の印を保ち、書き直した後の照合が通ること
        count += 1
        run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
        if quoted not in path.read_text(encoding="utf-8") or code != 0:
            failures.append(f"docs: 引用の中のコード片を引用の印を保って書き直さない（終了コード {code}）\n{path.read_text(encoding='utf-8')}")


def absolute_snippet_case() -> None:
    """コード片の元のファイルの絶対パスの「..」を字面で取り除かず、要素を順に確かめること。起点の中の通常の絶対パスは認めること。"""
    global count
    for label, place, want in (("通常", "scripts/sample.sh", 0), ("追跡しない場所を経由する「..」", ".tools/bin/../../scripts/sample.sh", 1),
                               ("ファイルの「..」", "README.md/../scripts/sample.sh", 1)):
        directory = docs_case(f"絶対パスの元のファイル（{label}）の準備", 0, "OK").resolve()
        (directory / ".tools" / "bin").mkdir(parents=True)
        path = directory / "docs/03-detailed-design/sample.md"
        path.write_text(replaced(path.read_text(encoding="utf-8"), "<!-- file: scripts/sample.sh -->", f"<!-- file: {directory}/{place} -->"),
                        encoding="utf-8")
        count += 1
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
        if code != want:
            failures.append(f"docs: 絶対パスの元のファイル（{label}）の扱いが違う（終了コード {code}）\n{output.strip()}")


def alias_rewrite_case() -> None:
    """--update-snippets が、同じ実体を指す 2 つの名前（本冊と、それを指すリンクの分冊）を 1 回だけ書き直し、
    両方の読んだ内容を更新すること（2 回目の読み直しの照合で「変更された」と止まらない）。"""
    global count
    detailed = "docs/03-detailed-design/sample.md"
    directory = docs_case("同じ実体の文書の準備（古いコード片）", 1, "一致しない", detailed,
                          "echo one\necho two\necho three\n" + FENCE + "\n\n<!-- excerpt", "echo old\n" + FENCE + "\n\n<!-- excerpt")
    (directory / "docs/03-detailed-design/sample-alias.md").symlink_to("sample.md")
    count += 1
    code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
    rewritten = output.count("コード片を書き直した")
    content = (directory / detailed).read_text(encoding="utf-8")
    if code != 0 or rewritten != 1 or "echo old" in content:
        failures.append(f"docs: 同じ実体の文書を 1 回だけ書き直さない（終了コード {code}・書き直し {rewritten} 回）\n{output.strip()}")


def snippet_order_case() -> None:
    """--update-snippets が、ほかの詳細設計を引用する文書を引用される文書の後に書き直して 1 回でそろえ、引用の循環を報告すること。"""
    global count
    detailed = "docs/03-detailed-design/"
    quote = "満たす要件: —\n\n<!-- file: {0} -->\n\n{2}{1}\nold\n{2}\n"
    directory = docs_case("引用の順序の準備（古いコード片）", 1, "一致しない", detailed + "sample.md",
                          "echo one\necho two\necho three\n" + FENCE + "\n\n<!-- excerpt", "echo old\n" + FENCE + "\n\n<!-- excerpt")

    def part(name: str, source: str, language: str = "markdown", fence: str = "````") -> None:
        (directory / detailed / name).write_text("# 分冊\n\n## 1. 写し\n\n" + quote.format(source, language, fence), encoding="utf-8")

    # 名前の順（sample-aa → sample-bb → sample）とは逆の、引用される側から書き直す
    part("sample-aa.md", detailed + "sample-bb.md", fence="`````")  # 引用する文書の中のフェンス（````）より長くする
    part("sample-bb.md", "./" + detailed + "sample.md")
    part("sample-cc.md", "scripts/sample.sh", "bash")
    count += 1
    code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
    if code != 0 or output.count("コード片を書き直した") != 4:
        failures.append(f"docs: 1 回の書き直しで、文書を引用する文書がそろわない（終了コード {code}）\n{output.strip()}")
    # 循環する引用（自身の引用を含む）は報告して書き直さず、循環していない文書は書き直す
    for label, sources in (("2 つの文書", {"sample-aa.md": "sample-bb.md", "sample-bb.md": "sample-aa.md"}),
                           ("自身", {"sample-aa.md": "sample-aa.md"})):
        count += 1
        # 先に処理する文書のフェンスを長くする（本文に相手のフェンスを含められる。書き直さないのは循環のため）
        for name, source in sources.items():
            part(name, detailed + source, fence="`````" if name == "sample-aa.md" else "````")
        part("sample-cc.md", "scripts/sample.sh", "bash")
        before = {name: (directory / detailed / name).read_bytes() for name in sources}
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
        if (code != 1 or output.count("引用が循環している") != 1 or "書き直した: " + detailed + "sample-cc.md" not in output
                or any((directory / detailed / name).read_bytes() != content for name, content in before.items())):
            failures.append(f"docs: 循環する引用（{label}）の扱いが違う（終了コード {code}）\n{output.strip()}")
        part("sample-bb.md", detailed + "sample.md")
    # 処理済みの文書を経由して循環に戻る文書（sample-dd。aa → bb → aa を先に見つけた後に、aa から読む）も循環に属する。
    # 循環を引用するだけの文書（sample-cc）は属さず、書き直す
    count += 1

    def quoting(name: str, *cited: str) -> None:
        (directory / detailed / name).write_text("# 分冊\n\n" + "".join(
            f"## {at}. 写し\n\n" + quote.format(detailed + source, "markdown", "`" * 12) for at, source in enumerate(cited, 1)),
            encoding="utf-8")

    quoting("sample-aa.md", "sample-bb.md", "sample-dd.md")
    quoting("sample-bb.md", "sample-aa.md")
    quoting("sample-dd.md", "sample-bb.md")
    (directory / detailed / "sample-cc.md").write_text(
        "# 分冊\n\n## 1. 写し\n\n" + quote.format(detailed + "sample-dd.md", "markdown", "`" * 20), encoding="utf-8")
    before = {name: (directory / detailed / name).read_bytes() for name in ("sample-aa.md", "sample-bb.md", "sample-dd.md")}
    code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory), "--update-snippets"])
    if (code != 1 or output.count("引用が循環している") != 1 or "書き直した: " + detailed + "sample-cc.md" not in output
            or any((directory / detailed / name).read_bytes() != content for name, content in before.items())):
        failures.append(f"docs: 処理済みの文書を経由する循環の扱いが違う（終了コード {code}）\n{output.strip()}")


def cyclic_snippet_case() -> Path:
    """コード片の元のファイルが循環するリンクでも、例外で止まらずに印の行を報告し、ほかの文書の検査を続けること
    （通常と --update-snippets の両方）。"""
    global count
    directory = docs_case("循環するリンクの準備（整合した文書）", 0, "OK")
    (directory / "scripts" / "loop.sh").symlink_to("loop.sh")
    path = directory / "docs/03-detailed-design/sample.md"
    path.write_text(path.read_text(encoding="utf-8") + f"\n<!-- file: scripts/loop.sh -->\n\n{FENCE}bash\necho loop\n{FENCE}\n",
                    encoding="utf-8")
    # 循環するリンクの後に検査する別の文書（sample.md より後に読む名前）の不一致も報告されること（検査が続くこと）。
    # 一部のコード片は --update-snippets でも書き直されないので、両方の実行で残る
    (directory / "docs/03-detailed-design/sample2.md").write_text(
        f"# 別の文書\n\n## 1. 実装\n\n満たす要件: FR-AAA-01\n\n<!-- excerpt: scripts/sample.sh -->\n\n{FENCE}bash\necho other\n{FENCE}\n",
        encoding="utf-8")
    for options in ([], ["--update-snippets"]):
        label = f"docs: 循環するリンクのコード片{' '.join(options)}"
        count += 1
        # 診断は文書のパスと印の行番号を含む（行番号は実行のたびに文書から求める）
        marker = path.read_text(encoding="utf-8").splitlines().index("<!-- file: scripts/loop.sh -->") + 1
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)] + options)
        separate = any("sample2.md" in line and "一致しない" in line for line in output.splitlines())
        located = f"docs/03-detailed-design/sample.md:{marker}: コード片の元のファイルのパスを解決できない" in output
        if code != 1 or not located or not separate:
            failures.append(f"{label}: 循環するリンクと、その後の別の文書の不一致の両方が報告されない（終了コード {code}）\n"
                            f"{output.strip()}")
    return directory


def unreadable_snippet_case() -> None:
    """コード片の元のファイルが UTF-8 として読めなくても、例外で止まらずに印の行で報告し、ほかの文書の検査を続けること
    （通常と --update-snippets の両方。--update-snippets はそのコード片を書き直さない）。"""
    global count
    directory = docs_case("読めないコード片の準備（整合した文書）", 0, "OK")
    (directory / "scripts" / "sample.sh").write_bytes(b"\xff\xfe\n")
    detailed = directory / "docs/03-detailed-design/sample.md"
    before = detailed.read_bytes()
    for options in ([], ["--update-snippets"]):
        label = f"docs: 読めないコード片の元のファイル{' '.join(options)}"
        count += 1
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)] + options)
        # 詳細設計の本文と、別の文書（索引）のコード片の両方で報告される
        reported = {found.group(1) for found in re.finditer(r"^NG: (\S+?):\d+: コード片の元のファイルを読めない", output, re.MULTILINE)}
        if code != 1 or "Traceback" in output or len(reported) < 2:
            failures.append(f"{label}: 読めない元のファイルを各文書の印の行で報告しない（終了コード {code}）\n{output.strip()}")
        if detailed.read_bytes() != before:
            failures.append(f"{label}: 読めない元のファイルのコード片を書き直した")


def line_separator_cases() -> None:
    """行は LF・CRLF・CR だけで区切り、U+2028 は行の本文として扱うこと（照合と --update-snippets で同じ）。"""
    global count
    detailed, readme = "docs/03-detailed-design/sample.md", "docs/03-detailed-design/README.md"
    separated = "echo one\u2028x"
    files = {"scripts/sample.sh": DOCS["scripts/sample.sh"].replace("echo one", separated),
             detailed: DOCS[detailed].replace("echo one", separated), readme: DOCS[readme].replace("echo one", separated)}
    docs_case("U+2028 を含むコード片", 0, "OK", extra=files)
    kept = docs_case("U+2028 を含むコード片は書き直さない", 0, "OK", options=["--update-snippets"], extra=files)
    count += 1
    if (kept / detailed).read_text(encoding="utf-8") != files[detailed]:
        failures.append("docs: U+2028 を含む一致したコード片を書き直した（行を U+2028 で区切った）")
    # 文書の U+2028 と実ファイルの改行は別の内容（U+2028 で区切ると一致したことにしてしまう）
    docs_case("U+2028 と改行の違い", 1, "一致しない", extra={**files, "scripts/sample.sh": DOCS["scripts/sample.sh"].replace(
        "echo one", "echo one\nx")})


def reread_failure_case() -> None:
    """--update-snippets で文書を読み直せない（OSError）とき、その文書のパスで報告して書き直さず、ほかの文書を続けること。"""
    global count
    count += 1
    directory = docs_case("読み直しの失敗の準備（整合した文書）", 0, "OK")
    spec = importlib.util.spec_from_file_location("verify_docs", SCRIPTS / "verify-docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checker = module.Checker(directory.resolve())
    checker.load()

    def failing(name: str) -> str:
        raise OSError(f"読めない: {name}")

    checker.read_raw = failing
    try:
        checker.update_snippets()
    except OSError as error:
        failures.append(f"docs: 読み直しの失敗で例外が送出された: {error!r}")
        return
    if not any("docs/03-detailed-design/sample.md" in message and "読み直せない" in message for message in checker.errors):
        failures.append(f"docs: 読み直しの失敗を文書のパスで報告しない（{checker.errors}）")


def reread_partial_failure_case() -> None:
    """--update-snippets で、書き直しの要る 2 つの文書のうち名前の順で先の文書だけ読み直しに失敗したとき、先の文書は
    変えずにパスで報告し、後の文書は書き直すこと。"""
    global count
    count += 1
    detailed = "docs/03-detailed-design/sample.md"
    later = "docs/03-detailed-design/sample2.md"
    stale = replaced(DOCS[detailed], "echo one\necho two\necho three", "echo old")
    directory = docs_case("一部の読み直しの失敗の準備（古いコード片）", 1, "一致しない",
                          extra={detailed: stale, later: f"# 別\n\n## 1. 実装\n\n満たす要件: FR-AAA-01\n\n"
                                 f"<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho old\n{FENCE}\n"})
    spec = importlib.util.spec_from_file_location("verify_docs", SCRIPTS / "verify-docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checker = module.Checker(directory.resolve())
    checker.load()
    original = checker.read_raw

    def failing(name: str) -> str:
        if name == detailed:
            raise OSError(f"読めない: {name}")
        return original(name)

    checker.read_raw = failing
    with contextlib.redirect_stdout(io.StringIO()):  # 書き直しの報告の行を試験の出力に混ぜない
        checker.update_snippets()
    if (directory / detailed).read_text(encoding="utf-8") != stale:
        failures.append("docs: 読み直しに失敗した先の文書が書き直された")
    if "echo one\necho two\necho three" not in (directory / later).read_text(encoding="utf-8"):
        failures.append("docs: 先の文書の読み直しの失敗の後に、後の文書が書き直されない")
    if not any(detailed in message and "読み直せない" in message for message in checker.errors):
        failures.append(f"docs: 先の文書の読み直しの失敗をそのパスで報告しない（{checker.errors}）")


def readlink_failure_case() -> None:
    """文書の解決の経路のリンク先を読めない（os.readlink の OSError）とき、その文書のパスで報告して読まず、ほかの文書を続けること。"""
    global count
    count += 1
    directory = docs_case("リンク先を読めない文書の準備（整合した文書）", 0, "OK")
    (directory / "docs/extra.md").symlink_to("02-basic-design/sample.md")
    (directory / "docs/later.md").write_text("# 後の文書\n", encoding="utf-8")  # 失敗する文書より後に読まれる文書
    spec = importlib.util.spec_from_file_location("verify_docs", SCRIPTS / "verify-docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checker = module.Checker(directory.resolve())
    original = module.os.readlink

    def failing(path: object) -> str:
        raise PermissionError(13, "読めない", str(path))

    module.os.readlink = failing
    try:
        checker.load()
    except OSError as error:
        failures.append(f"docs: リンク先を読めない文書で例外が送出された: {error!r}")
        return
    finally:
        module.os.readlink = original
    if not any(message.startswith("docs/extra.md: 文書を読まない") for message in checker.errors):
        failures.append(f"docs: リンク先を読めない文書をそのパスで報告しない（{checker.errors}）")
    if "docs/02-basic-design/sample.md" not in checker.texts or "docs/later.md" not in checker.texts:
        failures.append("docs: リンク先を読めない文書の後に、ほかの文書を読まない")


def freshness_case() -> None:
    """確認日から暦の 3 か月後の日（ない日は月末）以降だけを警告し、終了コードを変えないこと。"""
    global count
    spec = importlib.util.spec_from_file_location("verify_docs", SCRIPTS / "verify-docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    date = module.datetime.date
    for confirmed, last_fresh, first_stale in (("2026-11-14", date(2027, 2, 13), date(2027, 2, 14)),
                                               ("2025-11-30", date(2026, 2, 27), date(2026, 2, 28)),
                                               ("2027-11-30", date(2028, 2, 28), date(2028, 2, 29)),
                                               ("2026-07-31", date(2026, 10, 30), date(2026, 10, 31)),
                                               ("9999-12-31", date(9999, 12, 31), None)):
        for today, want in ((last_fresh, 0), (first_stale, 1)):
            if today is None:
                continue
            count += 1
            checker = module.Checker(ROOT)
            checker.texts = {"docs/a.md": f"確認日 {confirmed}。確認日 9999-12-31"}
            checker.check_freshness(today)
            if len(checker.warnings) != want or checker.errors:
                failures.append(f"docs: 確認日 {confirmed} の {today} の警告が {checker.warnings}（期待 {want} 件）")


def unreadable_directory_case() -> None:
    """docs/ の下の読めないディレクトリを黙って読み飛ばさず、そのディレクトリで報告して失敗すること（読める文書の検査は続ける）。"""
    global count
    count += 1
    directory = docs_case("読めないディレクトリの準備（整合した文書）", 0, "OK")
    locked = directory / "docs/02-basic-design/locked"
    locked.mkdir()
    (locked / "hidden.md").write_text("# 隠れた文書\n", encoding="utf-8")
    detailed = directory / "docs/03-detailed-design/sample.md"
    detailed.write_text(detailed.read_text(encoding="utf-8").replace("echo two\necho three\n" + FENCE + "\n\n## 2.",
                                                                     "echo other\n" + FENCE + "\n\n## 2."), encoding="utf-8")
    locked.chmod(0)
    try:
        code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
    finally:
        locked.chmod(0o755)
    if code != 1 or "docs/02-basic-design/locked: ディレクトリを読めない" not in output or "一致しない" not in output:
        failures.append(f"docs: 読めないディレクトリを報告して、ほかの文書の検査を続けない（終了コード {code}）\n{output.strip()}")


def unreadable_document_case() -> None:
    """UTF-8 として読めない文書は、そのパスで報告して読み飛ばし、ほかの文書の検査を続けること（例外で止まらない）。"""
    global count
    count += 1
    directory = docs_case("読めない文書の準備（整合した文書）", 0, "OK")
    (directory / "docs/02-basic-design/broken.md").write_bytes(b"\xff\xfe\n")
    detailed = directory / "docs/03-detailed-design/sample.md"
    detailed.write_text(detailed.read_text(encoding="utf-8").replace("echo two\necho three\n" + FENCE + "\n\n## 2.",
                                                                     "echo other\n" + FENCE + "\n\n## 2."), encoding="utf-8")
    code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
    if (code != 1 or "Traceback" in output or "docs/02-basic-design/broken.md: 文書を読めない" not in output
            or "一致しない" not in output):
        failures.append(f"docs: 読めない文書をパスで報告して、ほかの文書の検査を続けない（終了コード {code}）\n{output.strip()}")


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
        docs_case("コメントの中の定義されていない ID は参照にしない", 0, "OK", detailed, "決定: BD-SMP-01",
                  "決定: BD-SMP-01 <!-- FR-AAA-09 -->"),
        docs_case("インラインのコードの中の定義されていない ID も検査する", 1, "定義されていない ID: FR-AAA-09", detailed,
                  "決定: BD-SMP-01", "決定: BD-SMP-01 `FR-AAA-09`"),
        # 同じ行で閉じる HTML のコメントだけの行は表示されないので、節の冒頭を終わらせない（後の「満たす要件:」を冒頭として読み、
        # 網羅にも数える）。コメントの後の本文の行は、これまでどおり冒頭を終わらせる
        docs_case("1 行のコメントの後の「満たす要件:」を節の冒頭とする", 0, "OK", basic,
                  "## 1. 設計決定\n\n満たす要件:", "## 1. 設計決定\n\n<!-- 補足 -->\n満たす要件:"),
        docs_case("1 行のコメントの後の「満たす要件:」を網羅に数える", 0, "MVP の要件 3 件", "", "", "", None,
                  {basic: replaced(replaced(DOCS[basic], "FR-AAA-01・02、", "FR-AAA-01、"),
                                   "## 3. テスト観点\n\n満たす要件: TST-AAA-01", "## 3. テスト観点\n\n<!-- 補足 -->\n\n満たす要件: TST-AAA-01、FR-AAA-02")}),
        docs_case("1 行のコメントと本文の後の「満たす要件:」は節の冒頭でない", 1, "冒頭に「満たす要件:」の行がない", basic,
                  "## 1. 設計決定\n\n満たす要件:", "## 1. 設計決定\n\n<!-- 補足 -->\n本文\n\n満たす要件:"),
        docs_case("定義されていない ID の群", 1, "定義されていない ID の群: FR-ZZZ", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-ZZZ 群"),
        docs_case("定義されていない設計決定", 1, "定義されていない ID: BD-SMP-02", detailed, "BD-SMP-01", "BD-SMP-02"),
        docs_case("ID の定義の重複", 1, "定義が重複", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-01 | 二 | MVP |"),
        # 定義の行は、列の区切りの前後の空白の有無によらず読む
        docs_case("空白のない表の要件の定義", 0, "MVP の要件 3 件", requirements, "| FR-AAA-02 | 二 | MVP |", "|FR-AAA-02|二|MVP|"),
        docs_case("空白のない表の設計決定の定義", 0, "OK", basic, "| BD-SMP-01 | 決定 |", "|BD-SMP-01|決定|"),
        docs_case("空白のない表の重複した定義", 1, "定義が重複", requirements, "| FR-AAA-02 | 二 | MVP |", "|FR-AAA-01|二|MVP|"),
        # 行頭の「|」を省いた表の行も、表の行として読む（GFM と同じ）
        docs_case("行頭の | を省いた行の定義", 0, "MVP の要件 3 件", requirements, "| FR-AAA-02 | 二 | MVP |", "FR-AAA-02 | 二 | MVP"),
        docs_case("行頭の | を省いた表の重複した定義", 1, "定義が重複", requirements, "| CON-01 | 制約 |\n",
                  "| CON-01 | 制約 |\n\nID | 要件 | フェーズ\n--- | --- | ---\nFR-AAA-01 | 重複 | MVP\n"),
        # インラインのコードの中のリンクの書き方は確かめない。コードの外の同じ書き方は確かめる
        docs_case("インラインのコードの中のリンク", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[要件](../01-requirements/requirements.md) `[例](missing.md)` ``[例](`missing.md`)``"),
        docs_case("インラインのコードの外のリンク", 1, "リンクの先がない: missing.md", basic, "[要件](../01-requirements/requirements.md)",
                  "`コード` [例](missing.md)"),
        # 間のインラインのコードは空白に置き換える（消して前後をつなげ、リンクでない書き方をリンクとして確かめない）
        docs_case("インラインのコードを挟んだ角括弧と括弧", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[例]`コード`(missing.md)"),
        # 節が小節から始まる場合は、最初の小節の冒頭だけを見る（後ろの小節の「満たす要件:」で節を合格にしない）
        cyclic_snippet_case(),
        linked_document_case(),
        untracked_document_case(),
        resolution_order_case(),
        file_parent_case(),
        untracked_snippet_case(),
        alias_rewrite_case(),
        # 分冊（本冊の基本設計の名前に「-<名前>」を付けた詳細設計）は、必須の節と申し送りを求めず、節の冒頭とコード片を照合する
        docs_case("分冊は必須の節を求めない", 0, "OK", extra={"docs/03-detailed-design/sample-scripts.md": (
            f"# 分冊\n\n## 1. 全文\n\n満たす要件: FR-AAA-01\n\n<!-- file: scripts/sample.sh -->\n\n"
            f"{FENCE}bash\necho one\necho two\necho three\n{FENCE}\n")}),
        docs_case("分冊のコード片の不一致", 1, "コード片が scripts/sample.sh と一致しない", extra={
            "docs/03-detailed-design/sample-scripts.md": (
                f"# 分冊\n\n## 1. 全文\n\n満たす要件: FR-AAA-01\n\n<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho one\n{FENCE}\n")}),
        docs_case("分冊の節の冒頭に満たす要件がない", 1, "節「1. 全文」の冒頭", extra={
            "docs/03-detailed-design/sample-scripts.md": "# 分冊\n\n## 1. 全文\n\n本文\n"}),
        # 本冊（同じ名前の詳細設計）のない分冊は、対応する基本設計がない文書として扱う
        docs_case("本冊のない分冊", 1, "対応する基本設計", extra={
            "docs/03-detailed-design/sample.md": None,
            "docs/03-detailed-design/sample-scripts.md": "# 分冊\n\n## 1. 全文\n\n満たす要件: FR-AAA-01\n"}),
        docs_case("最初の小節に本文がない節", 1, "節「2. セキュリティ」の冒頭", basic, "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01",
                  "## 2. セキュリティ\n\n### 2.1 前\n\n### 2.2 後\n\n満たす要件: SEC-AAA-01"),
        # 親の節は自身の「満たす要件:」を持つ（最初の小節の「満たす要件:」で代えない）
        docs_case("小節だけが満たす要件を持つ節", 1, "節「2. セキュリティ」の冒頭", basic, "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01",
                  "## 2. セキュリティ\n\n### 2.1 前\n\n満たす要件: SEC-AAA-01\n\n### 2.2 後\n\n本文"),
        docs_case("親の節と小節の両方の満たす要件", 0, "OK", basic, "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01",
                  "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n### 2.1 前\n\n満たす要件: SEC-AAA-01\n\n本文"),
        # 字下げしたコードブロックの中の表の形の行は、対応表として数えない
        docs_case("字下げしたコードブロックの対応表", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |",
                  "    | 要件 | 試験 |\n    | --- | --- |\n    | FR-AAA-01 | 試験 |"),
        # 申し送りは番号付きの項目も照合する（解決の表から行が欠けた・表がない場合も欠落として報告する）
        docs_case("番号付きの申し送り", 0, "OK", basic, "- 一つ目\n- 二つ目\n", "1. 一つ目\n2. 二つ目\n"),
        docs_case("番号付きの申し送りの解決が足りない", 1, "申し送り「二つ目」の解決が表にない", detailed, "| 二つ目 | 済 |\n", "",
                  extra={basic: replaced(DOCS[basic], "- 一つ目\n- 二つ目\n", "1. 一つ目\n2. 二つ目\n")}),
        docs_case("番号付きの申し送りの解決の表がない", 1, "申し送り「一つ目」の解決が表にない", detailed,
                  "| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n", "",
                  extra={basic: replaced(DOCS[basic], "- 一つ目\n- 二つ目\n", "1. 一つ目\n2. 二つ目\n")}),
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
        docs_case("申し送りの解決の列がコメントだけ", 1, "申し送り「二つ目」の解決の列が空", detailed, "| 二つ目 | 済 |",
                  "| 二つ目 | <!-- 済 --> |"),
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
        # 配下のディレクトリの分冊も、同じディレクトリの本冊があれば分冊として扱う（本冊がなければ認めない）
        docs_case("入れ子の分冊", 0, "OK",
                  extra={"docs/02-basic-design/sub/nested.md": nested_basic,
                         "docs/03-detailed-design/sub/nested.md": DOCS[detailed],
                         "docs/03-detailed-design/sub/nested-scripts.md": "# 分冊\n\n## 1. 全文\n\n満たす要件: FR-AAA-01\n"}),
        docs_case("本冊のない入れ子の分冊", 1, "sub/nested-scripts.md: 対応する基本設計",
                  extra={"docs/02-basic-design/sub/nested.md": nested_basic,
                         "docs/03-detailed-design/sub/nested-scripts.md": "# 分冊\n\n## 1. 全文\n\n満たす要件: FR-AAA-01\n"}),
        # 申し送りの解決は、2 列目の見出しが「解決」の表だけを数える（担当者などの列を解決と見なさない）
        docs_case("2 列目が解決でない申し送りの表", 1, "申し送り「一つ目」の解決が表にない", detailed,
                  "| 申し送り | 解決 |", "| 申し送り | 担当者 |"),
        # 引用などの新しいブロックの始まりで表は終わる（引用の中の「|」を含む行を対応表の行として数えない）
        docs_case("表の後の引用の行は対応表に数えない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 試験 |\n| --- | --- |\n> FR-AAA-01 | 試験 |"),
        docs_case("行頭の | を省いた対応表の行は数える", 0, "OK", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 試験 |\n| --- | --- |\nFR-AAA-01 | 試験"),
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
        # HTML の <pre> の中の表の形の行は、対応表として数えない（外の表は数える）
        docs_case("対応表が <pre> の中だけ", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n", "<pre>\n| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n</pre>\n"),
        # 申し送りの解決の表も、HTML の <pre> の中の表の形の行を数えない
        docs_case("申し送りの解決の表が <pre> の中だけ", 1, "解決が表にない", detailed,
                  "| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n",
                  "<pre>\n| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n</pre>\n"),
        docs_case("<pre> の後の対応表は数える", 0, "OK", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n", "<pre>\n例\n</pre>\n\n| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n"),
        docs_case("対応表の行に要件 ID がない", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |", "| 未定 | 試験 |"),
        docs_case("対応表の行にテストがない", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |", "| FR-AAA-01 |  |"),
        docs_case("対応表の行のテストがコメントだけ", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |",
                  "| FR-AAA-01 | <!-- 試験 --> |"),
        docs_case("対応表の行のテストにコメントと表示される名前がある", 0, "OK", detailed, "| FR-AAA-01 | 試験 |",
                  "| FR-AAA-01 | 試験 <!-- 補足 --> |"),
        # 先頭の列の要件 ID も表示される部分だけを数える（コメントの中の ID だけの行は対応の行にしない）
        docs_case("対応表の行の要件 ID がコメントだけ", 1, "対応表の行がない", detailed, "| FR-AAA-01 | 試験 |",
                  "| <!-- FR-AAA-01 --> | 試験 |"),
        docs_case("対応表の行の要件 ID にコメントと表示される ID がある", 0, "OK", detailed, "| FR-AAA-01 | 試験 |",
                  "| FR-AAA-01 <!-- 補足 --> | 試験 |"),
        # 検査・試験の列が空なら、「状態」などほかの列に値があっても数えない
        docs_case("対応表の行の検査・試験が空で状態だけがある", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 検査・試験 | 状態 |\n| --- | --- | --- |\n| FR-AAA-01 |  | 通過 |"),
        docs_case("対応表に検査・試験の列がない", 1, "対応表の行がない", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 状態 |\n| --- | --- |\n| FR-AAA-01 | 通過 |"),
        docs_case("対応表の検査・試験の列に値があれば数える", 0, "OK", detailed,
                  "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |", "| 要件 | 検査・試験 | 状態 |\n| --- | --- | --- |\n| FR-AAA-01 | 試験 |  |"),
        # 範囲は、同じ接頭辞の定義済み ID の数を超えて展開しない
        docs_case("広すぎる範囲", 1, "広すぎる範囲: FR-AAA-01〜99", detailed, "FR-AAA-01〜02", "FR-AAA-01〜99"),
        # FR / NFR / SEC はフェーズ（MVP / P2 / P3）を持つ
        docs_case("フェーズを読めない", 1, "FR-AAA-02 のフェーズ「未定」", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-02 | 二 | 未定 |"),
        docs_case("フェーズの列がない", 1, "SEC-AAA-01 のフェーズの列がない", "docs/01-requirements/security-requirements.md",
                  "| ID | 要件 | フェーズ | MASVS |\n| --- | --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP | STORAGE |",
                  "| ID | 要件 | MASVS |\n| --- | --- | --- |\n| SEC-AAA-01 | 一 | STORAGE |"),
        # SEC は MASVS 分類を必ず持つ（列がない・値が空・コメントだけ・行に列が足りない）
        docs_case("MASVS の列がない", 1, "security-requirements.md:5: SEC-AAA-01 の MASVS 分類の列がない",
                  "docs/01-requirements/security-requirements.md",
                  "| ID | 要件 | フェーズ | MASVS |\n| --- | --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP | STORAGE |",
                  "| ID | 要件 | フェーズ |\n| --- | --- | --- |\n| SEC-AAA-01 | 一 | MVP |"),
        *[docs_case(f"MASVS 分類が{label}", 1, "SEC-AAA-01 の MASVS 分類が空", "docs/01-requirements/security-requirements.md",
                    "| MVP | STORAGE |", row)
          for label, row in (("空", "| MVP |  |"), ("コメントだけ", "| MVP | <!-- 未定 --> |"), ("行にない", "| MVP |"))],
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
        # 参照形式のリンク（[文][ラベル]・[ラベル][]）も、定義を通してリンク先を確かめる
        docs_case("参照形式のリンク", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[要件][Req]\n\n[req]: ../01-requirements/requirements.md"),
        docs_case("参照形式の切れたリンク", 1, "リンクの先がない: ../01-requirements/missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", "[要件][design]\n\n[design]: ../01-requirements/missing.md"),
        docs_case("省略形の参照形式の切れたリンク", 1, "リンクの先がない: ../01-requirements/missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", "[design][]\n\n[design]: ../01-requirements/missing.md"),
        docs_case("定義のないラベルはリンクとして扱わない", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[要件][未定義]"),
        # 未定義のラベルの後ろに続く [ラベル][ラベル] は参照のリンクになる（[foo][未定義][bar] の [未定義][bar]）
        docs_case("未定義のラベルに続く参照形式の切れたリンク", 1, "リンクの先がない: ../01-requirements/missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", "[要件][未定義][design]\n\n[design]: ../01-requirements/missing.md"),
        # 定義のある参照の直後の [ラベル] は短縮形の参照のリンクになる（[foo][bar][baz] の [baz]）
        docs_case("参照形式の直後の短縮形の切れたリンク", 1, "リンクの先がない: ../01-requirements/missing.md", basic,
                  "[要件](../01-requirements/requirements.md)",
                  "[要件][req][design]\n\n[req]: ../01-requirements/requirements.md\n\n[design]: ../01-requirements/missing.md"),
        docs_case("参照形式の直後の未定義の短縮形", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[要件][req][未定義]\n\n[req]: ../01-requirements/requirements.md"),
        # 定義は段落の始まりにだけ書ける（段落の続きの定義の形の行を先の定義として採らない）
        docs_case("段落の続きの定義の形の行", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "説明\n[req]: missing.md\n\n[req]: ../01-requirements/requirements.md\n\n[要件][req]"),
        docs_case("段落の続きの定義の形の行の短縮形の使用", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", "説明\n[req]: 文\n\n[req]: missing.md"),
        docs_case("見出しの直後の定義と、続けた定義（タイトルの行をはさむ）", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)",
                  "### 小節\n[a]: ../01-requirements/requirements.md\n\"題\"\n[b]: missing.md\n\n[一][a] [二][b]"),
        docs_case("続けた定義", 1, "リンクの先がない: missing.md", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]: ../01-requirements/requirements.md '題'\n[b]: missing.md\n\n[一][a] [二][b]"),
        # 定義が使った行（次の行のリンク先・次の行のタイトル）の中の参照の書き方は、リンクの使用として読まない。同じ書き方が
        # 本文にあれば、これまでどおり使用として確かめる
        docs_case("定義の次の行のタイトルの中の参照の書き方", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]: ../01-requirements/requirements.md\n\"[x] [一][x]\"\n\n[x]: missing.md"),
        docs_case("次の行のリンク先とタイトルの中の参照の書き方", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]:\n  ../01-requirements/requirements.md\n\"[x] [一][x]\"\n\n[x]: missing.md"),
        docs_case("本文の同じ参照の書き方は確かめる", 1, "リンクの先がない: missing.md", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]: ../01-requirements/requirements.md\n\"[x]\"\n\n[x]: missing.md\n\n本文 [x]"),
        # リンク先をラベルの次の行に書いた定義も確かめる（段落の続き・リンク先の後に余分な文がある形は定義にならない）
        docs_case("次の行にリンク先のある定義", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]:\n  ../01-requirements/requirements.md '題'\n\n[一][a]"),
        docs_case("次の行のリンク先が切れた定義", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", "[a]:\n  missing.md\n\n[一][a]"),
        docs_case("次の行のリンク先の後に続けた定義", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)",
                  "[a]:\n<../01-requirements/requirements.md>\n\"題\"\n[b]:\nmissing.md\n\n[一][a] [二][b]"),
        docs_case("段落の続きのラベルの行は定義でない", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "説明\n[a]:\nmissing.md\n\n[一][a]"),
        docs_case("次の行に余分な文のある形は定義でない", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  "[a]:\nmissing.md 余分な文\n\n[一][a]"),
        # 引用の中のフェンスの ID は参照として数えない（引用の地の文の ID は確かめる）
        docs_case("引用の中のフェンスの中の定義されていない ID", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  f"> {FENCE}text\n> FR-AAA-09\n> {FENCE}"),
        docs_case("引用の地の文の定義されていない ID", 1, "定義されていない ID: FR-AAA-09", basic,
                  "[要件](../01-requirements/requirements.md)", f"> {FENCE}text\n> 例\n> {FENCE}\n> FR-AAA-09"),
        # 引用の中のフェンスの本文はリンクとして確かめない（地の文と、フェンスの外の引用は確かめる）
        docs_case("引用の中のフェンスの中のリンク", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
                  f"> {FENCE}markdown\n> [例](missing.md)\n> {FENCE}"),
        docs_case("引用の中のフェンスの後の引用のリンク", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", f"> {FENCE}markdown\n> 例\n> {FENCE}\n> [例](missing.md)"),
        docs_case("引用が終わった後のリンク", 1, "リンクの先がない: missing.md", basic,
                  "[要件](../01-requirements/requirements.md)", f"> {FENCE}markdown\n> 例\n\n[例](missing.md)"),
        # 申し送りの解決は、解決の列（2 列目）を見出しに持つ表の行だけを数える
        docs_case("1 列の表の余分な列を解決と見なさない", 1, "申し送り「一つ目」の解決が表にない", detailed,
                  "| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n",
                  "| 申し送り |\n| --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n"),
        # 冒頭の「| 満たす要件 ID |」は、表のデータ行の場合だけ網羅に数える（表の見出しに似た単独の行は数えない）
        docs_case("冒頭の表でない満たす要件 ID の行は網羅に数えない", 1, "MVP の要件 FR-AAA-02", basic,
                  "| 項目 | 内容 |\n| --- | --- |\n| 満たす要件 ID |", "| 満たす要件 ID |"),
        # 冒頭の表は値の列（2 列目）だけを数える。見出しの列の数を超える余分な列（GFM は表示しない）の ID は網羅に数えない
        docs_case("冒頭の表の余分な列の ID は網羅に数えない", 1, "MVP の要件 FR-AAA-02", basic,
                  "| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |", "| 満たす要件 ID | FR-AAA-01、SEC-AAA-01 | FR-AAA-02 |"),
        # 「満たす要件」の HTML のコメントの中の ID は表示されないので、網羅にも節の冒頭の要件にも数えない
        docs_case("冒頭の表のコメントの中の ID は網羅に数えない", 1, "MVP の要件 FR-AAA-02", basic,
                  "| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |", "| 満たす要件 ID | FR-AAA-01、SEC-AAA-01 <!-- FR-AAA-02 --> |"),
        docs_case("節の冒頭のコメントの中の ID は網羅に数えない", 1, "MVP の要件 FR-AAA-02", extra={basic: replaced(replaced(
            DOCS[basic], "| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |", "| 満たす要件 ID | FR-AAA-01、SEC-AAA-01 |"),
            "満たす要件: FR-AAA-01\n", "満たす要件: FR-AAA-01 <!-- FR-AAA-02 -->\n")}),
        docs_case("コメントの中の ID だけの節の冒頭", 1, "要件 ID も「—」の指定もない", detailed,
                  "満たす要件: SEC-AAA-01\n\n## 3. テスト観点", "満たす要件: <!-- SEC-AAA-01 -->\n\n## 3. テスト観点"),
        docs_case("コメントの後の「—」は認める", 0, "OK", basic, "満たす要件: —\n", "満たす要件: <!-- 補足 --> —\n"),
        docs_case("コメントの後の見える ID は認める", 0, "OK", basic, "満たす要件: SEC-AAA-01\n", "満たす要件: <!-- 補足 --> SEC-AAA-01\n"),
        # 設計決定（BD）の定義は、各文書の §1（architecture.md は §3）の表だけから集める（docs/README.md）
        docs_case("§1 の外の BD は定義にしない", 1, "定義されていない ID: BD-SMP-09", extra={
            basic: replaced(DOCS[basic], "[要件](../01-requirements/requirements.md)\n",
                            "[要件](../01-requirements/requirements.md)\n\n| ID | 決定 |\n| --- | --- |\n| BD-SMP-09 | 決定 |\n"),
            detailed: replaced(DOCS[detailed], "決定: BD-SMP-01", "決定: BD-SMP-01、BD-SMP-09")}),
        docs_case("architecture.md は §3 の BD を定義にする", 0, "OK", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、BD-ARC-01",
                  extra={"docs/02-basic-design/architecture.md": ARCHITECTURE}),
        docs_case("architecture.md の §1 の BD は定義にしない", 1, "定義されていない ID: BD-ARC-02", detailed, "決定: BD-SMP-01",
                  "決定: BD-SMP-01、BD-ARC-02", extra={"docs/02-basic-design/architecture.md": replaced(
                      ARCHITECTURE, "満たす要件: FR-AAA-01\n\n## 2.",
                      "満たす要件: FR-AAA-01\n\n| ID | 決定 |\n| --- | --- |\n| BD-ARC-02 | 決定 |\n\n## 2.")}),
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
    unreadable_snippet_case()
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
    # 一致するコード片は、開始行と本文の改行が違っていても変えない（ほかのコード片を書き直すときも）
    fence = f"{FENCE}bash\necho one\necho two\necho three\n{FENCE}"
    mixed_fence = fence.replace(f"{FENCE}bash\n", f"{FENCE}bash\r\n").encode("utf-8")
    second = "\n\n<!-- file: scripts/other.sh -->\n\n" + f"{FENCE}bash\necho stale\n{FENCE}"
    mixed = docs_case("改行の混ざった文書の準備", 1, "コード片が scripts/other.sh と一致しない",
                      extra={detailed: replaced(DOCS[detailed], fence, fence + second), "scripts/other.sh": "echo fresh\n"})
    target = mixed / detailed
    target.write_bytes(target.read_bytes().replace(fence.encode("utf-8"), mixed_fence, 1))
    expect("docs: 改行の混ざった文書の書き直し", [str(SCRIPTS / "verify-docs.py"), "--root", str(mixed), "--update-snippets"], 0, "コード片を書き直した")
    count += 1
    if mixed_fence not in target.read_bytes() or b"echo fresh\n" not in target.read_bytes():
        failures.append("docs: 一致するコード片の改行が変わった、またはほかのコード片が書き直されていない")
    # HTML の <pre> の中の表の例は、定義として数えない（前後のふつうの表は数える）
    docs_case("<pre> の中の重複の定義", 0, "MVP の要件 3 件", requirements, "| CON-01 | 制約 |\n",
              "| CON-01 | 制約 |\n\n<pre>\n| ID | 要件 | フェーズ |\n| --- | --- | --- |\n| FR-AAA-01 | 重複 | MVP |\n</pre>\n")
    docs_case("<pre> の中だけの定義", 1, "定義されていない ID: CON-02", requirements, "| CON-01 | 制約 |\n",
              "| CON-01 | 制約 |\n\n<pre>\n| ID | 制約 |\n| --- | --- |\n| CON-02 | 例 |\n</pre>\n\nCON-02\n")
    docs_case("<pre> の後の表の定義", 1, "定義が重複", requirements, "| CON-01 | 制約 |\n",
              "<pre>\n例\n</pre>\n| ID | 制約 |\n| --- | --- |\n| CON-01 | 制約 |\n| CON-01 | 重複 |\n")
    freshness_case()
    # --update-snippets は、一時ファイルを作れない（ディレクトリに書けない）ときに元の文書を切り詰めず、そのまま残す
    assert docs_parent is not None
    readonly = Path(tempfile.mkdtemp(dir=docs_parent))
    stale_text = replaced(DOCS[detailed], "echo one\necho two\necho three", "echo old")
    for path, content in {**DOCS, detailed: stale_text}.items():
        (readonly / path).parent.mkdir(parents=True, exist_ok=True)
        (readonly / path).write_text(content, encoding="utf-8")
    (readonly / detailed).parent.chmod(0o555)
    try:
        expect("docs: 書けないディレクトリでは元の文書を残す", [str(SCRIPTS / "verify-docs.py"), "--root", str(readonly), "--update-snippets"],
               1, "コード片を書き直さない")
    finally:
        (readonly / detailed).parent.chmod(0o755)
    count += 1
    if (readonly / detailed).read_text(encoding="utf-8") != stale_text:
        failures.append("docs: 書き直しに失敗したのに、元の文書が変わった")
    # --update-snippets は、実パスが --root の外にある文書に書き込まない（読む前に起点の外として報告し、読みも書きもしない）
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
           1, "文書を読まない")
    count += 1
    if outside.read_text(encoding="utf-8") != stale:
        failures.append("docs: --root の外にある文書が書き直された")
    line_separator_cases()
    # 複数行の HTML のコメントの中のフェンスの印は、フェンスとして扱わない（後ろの文書をコードとして隠さない）
    docs_case("コメントの中のフェンスの印", 0, "OK", basic, "## 2. セキュリティ", f"<!--\n{FENCE}\n-->\n\n## 2. セキュリティ")
    # コメントの中の見出しと「満たす要件:」は、MVP の網羅に数えない（sections() と同じ判定）
    docs_case("コメントの中の満たす要件", 1, "MVP の要件 FR-AAA-02", extra={basic: replaced(
        DOCS[basic], "| 満たす要件 ID | FR-AAA-01・02、SEC-AAA-01 |", "| 満たす要件 ID | FR-AAA-01、SEC-AAA-01 |")
        + "\n<!--\n### 4.1 小節\n\n満たす要件: FR-AAA-02\n-->\n"})
    reread_failure_case()
    reread_partial_failure_case()
    unreadable_document_case()
    unreadable_directory_case()
    # 字下げ（空白 0〜3 個）した見出しも節として扱い、その節の「満たす要件:」を確かめる。4 個以上はコードブロックで節にしない
    docs_case("字下げした見出しの節の冒頭", 1, "節「5. 追加」の冒頭に「満たす要件:」の行がない", basic, "- 一つ目\n- 二つ目\n",
              "- 一つ目\n- 二つ目\n\n   ## 5. 追加\n\n本文\n")
    # 印だけの見出し（「##」。見出しの文が空）も節として扱い、その節の「満たす要件:」を確かめる
    docs_case("印だけの見出しの節の冒頭", 1, "節「」の冒頭に「満たす要件:」の行がない", basic, "- 一つ目\n- 二つ目\n",
              "- 一つ目\n- 二つ目\n\n##\n\n本文\n")
    # 字下げした見出しで節が終わるので、後ろの箇条書きを前の節（申し送り）の項目として数えない
    docs_case("字下げした見出しの節は前の節に含めない", 0, "OK", basic, "- 一つ目\n- 二つ目\n",
              "- 一つ目\n- 二つ目\n\n  ## 5. 追加\n\n満たす要件: —\n\n- 三つ目\n")
    docs_case("4 個の空白で字下げした見出しは節にしない", 0, "OK", basic, "- 一つ目\n- 二つ目\n",
              "- 一つ目\n- 二つ目\n\n    ## 5. 追加\n")
    # 同じ行で閉じる HTML のコメントの中のリンクは表示されないので確かめない。コード片の印の照合は続ける
    docs_case("同じ行のコメントの中のリンク", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) <!-- [旧](missing.md) -->")
    # コメントで始まる行は全体が HTML のブロックなので、後ろの文字もリンクにならない。次の行のリンクは確かめる
    for label, text in (("インライン", "<!-- 注 --> [要件](missing.md)"), ("参照形式", "<!-- 注 --> [要件][a]\n\n[a]: missing.md"),
                        ("開いた括弧", "<!-- 注 --> [要件\n続き](missing.md)"), ("前の行の括弧", "[例\n<!-- 注 -->\n続き](missing.md)")):
        docs_case(f"コメントで始まる行の後ろは確かめない: {label}", 0, "OK", basic, "[要件](../01-requirements/requirements.md)", text)
    docs_case("コメントの次の行のリンクは確かめる", 1, "リンクの先がない: missing.md", basic, "[要件](../01-requirements/requirements.md)",
              "<!-- 注 -->\n[要件](missing.md)")
    # 空行の後の空白 4 個（かタブ）の行は字下げしたコードブロック。段落の続き・箇条書きの中の段落と項目は確かめる
    for label, text, want in (
        ("コードブロック", "本文\n\n    [例](missing.md)\n    [例](missing2.md)\n\n    [例](missing.md)", 0),
        ("タブのコードブロック", "本文\n\n\t[例](missing.md)", 0),
        ("見出しの直後のコードブロック", "#### 例\n    [例](missing.md)", 0),
        ("段落の続き", "本文\n    [例](missing.md)", 1),
        ("段落の続きをまたぐ文", "本文 [例\n    続き](missing.md)", 1),
        ("コードブロックの後の本文", "本文\n\n    例\n\n[例](missing.md)", 1),
        ("箇条書きの中の段落", "- 項目\n\n    [例](missing.md)", 1),
        ("番号付きの箇条書きの中の段落", "1. 項目\n\n    [例](missing.md)", 1),
        ("入れ子の項目", "- 項目\n  - 内側\n\n    - [例](missing.md)", 1),
        ("箇条書きの中のコードブロック", "- 項目\n\n      [例](missing.md)", 0),
        ("箇条書きが終わった後のコードブロック", "- 項目\n\n本文\n\n    [例](missing.md)", 0),
    ):
        docs_case(f"字下げした行のリンク: {label}", want, "リンクの先がない: missing.md" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", f"[要件](../01-requirements/requirements.md)\n\n{text}")
    # 箇条書きの中のフェンスの字下げは、項目の本文の位置から数える。項目の外の空白 4 個以上は、これまでどおり字下げしたコード
    sample = "echo one\necho two\necho three"
    for label, text, want, want_text in (
        ("言語なし", f"1. 項目\n\n    {FENCE}\n    例\n    {FENCE}", 1, "コードフェンスに言語の指定がない"),
        ("番号付きの直後", f"1. 項目\n    {FENCE}text\n    [例](missing.md)\n    {FENCE}", 0, "OK"),
        ("中のリンク", f"1. 項目\n\n    {FENCE}text\n    [例](missing.md)\n    {FENCE}", 0, "OK"),
        ("後のリンク", f"1. 項目\n\n    {FENCE}text\n    例\n    {FENCE}\n\n    [例](missing.md)", 1, "リンクの先がない: missing.md"),
        ("入れ子の項目", f"- 外\n  - 内\n\n      {FENCE}\n      例\n      {FENCE}", 1, "コードフェンスに言語の指定がない"),
        ("印と同じ行（言語なし）", f"- {FENCE}\n  例\n  {FENCE}", 1, "コードフェンスに言語の指定がない"),
        ("印と同じ行の中のリンク", f"- {FENCE}text\n  [例](missing.md)\n  {FENCE}", 0, "OK"),
        ("番号付きの印と同じ行の中のリンク", f"10. {FENCE}text\n    [例](missing.md)\n    {FENCE}", 0, "OK"),
        ("印と同じ行の後のリンク", f"- {FENCE}text\n  例\n  {FENCE}\n\n  [例](missing.md)", 1, "リンクの先がない: missing.md"),
        ("印と同じ行の、項目の本文の位置にない閉じ", f"- {FENCE}text\n  例\n{FENCE}", 1, "コードフェンスに言語の指定がない"),
        ("印の後の字下げしたコード", f"-     {FENCE}\n\n本文", 0, "OK"),
        ("項目の中の字下げしたコード", f"1. 項目\n\n       {FENCE}\n       例", 0, "OK"),
        ("項目が終わっても閉じない", f"1. 項目\n\n   {FENCE}text\n   例\n\n本文", 1, "コードフェンスが閉じていない"),
        ("項目が終わった後の別のフェンスで閉じない", f"1. 項目\n\n   {FENCE}text\n   例\n\n本文\n\n1. 次\n\n   {FENCE}text\n   例\n   {FENCE}", 1,
         "コードフェンスが閉じていない"),
        ("項目の終わりの後のリンク", f"1. 項目\n\n   {FENCE}text\n   例\n\n[例](missing.md)", 1, "リンクの先がない: missing.md"),
        ("最上位の字下げしたコード", f"本文\n\n    {FENCE}\n    例", 0, "OK"),
        ("見出しの後の字下げしたコード", f"1. 項目\n### 小節\n\n    {FENCE}\n    例", 0, "OK"),
        ("引用の後の字下げしたコード", f"1. 項目\n> 引用\n\n    {FENCE}\n    例", 0, "OK"),
    ):
        docs_case(f"箇条書きの中のフェンス: {label}", want, want_text, basic,
                  "[要件](../01-requirements/requirements.md)", f"[要件](../01-requirements/requirements.md)\n\n{text}")
    for label, body, want, want_text in (("一致", sample, 0, "OK"), ("不一致", "echo other", 1, "コード片が scripts/sample.sh と一致しない")):
        lines = "\n".join(f"    {line}" for line in ("<!-- file: scripts/sample.sh -->", f"{FENCE}bash", *body.split("\n"), FENCE))
        docs_case(f"箇条書きの中のコード片（{label}）", want, want_text, detailed, EXAMPLE_AFTER, f"{EXAMPLE_AFTER}1. 項目\n\n{lines}\n\n")
    # 字下げは CommonMark の桁で数える（タブは 4 桁ごとのタブ位置まで）。タブで字下げした箇条書きの中のコード片は、同じ桁の
    # 空白の字下げと同じに照合し、本文の中のタブは変えない
    tabbed_sample = "printf 'a\\tb'\n\techo tab"
    for label, opener, body_indent, want in (("タブの字下げ", "\t", "\t", 0), ("空白の開始行とタブの本文", "    ", "\t", 0),
                                             ("タブの開始行と空白の本文", "\t", "    ", 0), ("字下げの足りない本文", "\t", "  ", 1)):
        tab_lines = "\n".join([f"{opener}<!-- file: scripts/tabbed.sh -->", f"{opener}{FENCE}bash",
                               *(f"{body_indent}{line}" for line in tabbed_sample.split("\n")), f"{opener}{FENCE}"])
        docs_case(f"箇条書きの中のコード片（{label}）", want, "OK" if want == 0 else "コード片が scripts/tabbed.sh と一致しない", detailed,
                  EXAMPLE_AFTER, f"{EXAMPLE_AFTER}1. 項目\n\n{tab_lines}\n\n", extra={"scripts/tabbed.sh": tabbed_sample + "\n"})
    count += 1
    stale_tab = "\n".join(["\t<!-- file: scripts/tabbed.sh -->", f"\t{FENCE}bash", "\techo old", f"\t{FENCE}"])
    rewritten = docs_case("タブで字下げした箇条書きの中のコード片の書き直し", 0, "コード片を書き直した", detailed, EXAMPLE_AFTER,
                          f"{EXAMPLE_AFTER}1. 項目\n\n{stale_tab}\n\n", options=["--update-snippets"],
                          extra={"scripts/tabbed.sh": tabbed_sample + "\n"})
    if "\n".join([f"\t{FENCE}bash", *(f"\t{line}" for line in tabbed_sample.split("\n")), f"\t{FENCE}"]) not in \
            (rewritten / detailed).read_text(encoding="utf-8"):
        failures.append("docs: タブで字下げした箇条書きの中のコード片が、同じ字下げ（本文のタブを保って）で書き直されていない")
    listed = docs_case("箇条書きの中のコード片の書き直し", 0, "コード片を書き直した", detailed, EXAMPLE_AFTER,
                       f"{EXAMPLE_AFTER}1. 項目\n\n{lines}\n\n", options=["--update-snippets"])
    if "\n" + "\n".join(f"    {line}" for line in (f"{FENCE}bash", *sample.split("\n"), FENCE)) + "\n" not in (listed / detailed).read_text(encoding="utf-8"):
        failures.append("docs: 箇条書きの中のコード片が、字下げを保って書き直されていない")
    # 印は、同じ箇条書きの項目の中（項目の外なら最上位）のフェンスだけに対応させる（照合と書き直しの両方）
    mark, stale = "<!-- file: scripts/sample.sh -->", f"{FENCE}bash\necho old\n{FENCE}"
    inner = "\n".join(f"  {line}" for line in stale.split("\n"))
    for label, text in (("項目の中の印と最上位のフェンス", f"- 項目\n\n  {mark}\n\n{stale}"),
                        ("最上位の印と項目の中のフェンス", f"{mark}\n\n- {inner.lstrip()}"),
                        ("項目の中の印と次の項目のフェンス", f"- 項目\n\n  {mark}\n\n- {inner.lstrip()}"),
                        ("内側の項目の印と外側の項目のフェンス", f"- 外\n  - 内\n\n    {mark}\n\n{inner}"),
                        ("フェンスで終わった項目の後の字下げした印", f"- 項目\n\n{FENCE}text\n例\n{FENCE}\n\n- 次\n\n  {mark}\n\n{stale}")):
        for options in ([], ["--update-snippets"]):
            directory = docs_case(f"コード片の対応: {label}（{options}）", 1, "同じ箇条書きの項目", detailed, EXAMPLE_AFTER,
                                  f"{EXAMPLE_AFTER}{text}\n\n", options=options)
            if f"{text}\n\n" not in (directory / detailed).read_text(encoding="utf-8"):
                failures.append(f"docs: コード片の対応: {label}で、別の項目のフェンスを書き直した")
    for label, text in (("最上位", f"{mark}\n\n{stale}"), ("最上位の字下げ", f"- 項目\n\n{mark}\n\n  {stale.replace(chr(10), chr(10) + '  ')}"),
                        ("項目の中", f"- 項目\n\n  {mark}\n\n{inner}"),
                        ("フェンスで終わった項目の後の字下げした印", f"- 項目\n\n{FENCE}text\n例\n{FENCE}\n\n  {mark}\n\n{stale}")):
        docs_case(f"コード片の対応の書き直し: {label}", 0, "コード片を書き直した", detailed, EXAMPLE_AFTER, f"{EXAMPLE_AFTER}{text}\n\n",
                  options=["--update-snippets"])
    # URL の scheme は大文字小文字を区別しない。相対パスのファイル名は区別する
    docs_case("大文字の scheme", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) [外](HTTPS://example.com/x) [宛先](MailTo:a@example.invalid)")
    docs_case("ASCII でない scheme の形", 1, "リンクの先がない", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) [例](ＨＴＴＰ:missing.md)")
    # 引用の中のフェンスも言語の指定を求める。閉じるフェンス・中の行は開始と数えず、引用が終わればフェンスも終わる
    for label, text, want in (
        ("指定なし", f"> {FENCE}\n> 例\n> {FENCE}", 1),
        ("指定あり", f"> {FENCE}text\n> 例\n> {FENCE}", 0),
        ("空白のない引用", f">{FENCE}\n>例\n>{FENCE}", 1),
        ("入れ子の引用", f"> > {FENCE}\n> > 例\n> > {FENCE}", 1),
        ("引用の終わりで終わるフェンス", f"> {FENCE}text\n> 例\n\n> {FENCE}\n> 例\n> {FENCE}", 1),
        ("閉じたフェンスの後のフェンス", f"> {FENCE}text\n> {FENCE}\n> {FENCE}\n> 例", 1),
        ("引用が終わった後の閉じないフェンス", f"> {FENCE}text\n> 例\n\n> {FENCE}\n> 例", 1),
        ("フェンスの中の引用の印", f"> {FENCE}text\n> > ~~~\n> {FENCE}", 0),
        ("長いフェンスの中の短いフェンス", f"> {FENCE}`text\n> {FENCE}\n> {FENCE}`", 0),
        ("引用の中の字下げしたコード", f">     {FENCE}", 0),
        ("ふつうのフェンスの中の引用", f"{FENCE}text\n> {FENCE}\n{FENCE}", 0),
    ):
        docs_case(f"引用の中のフェンス: {label}", want, "コードフェンスに言語の指定がない" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", f"[要件](../01-requirements/requirements.md)\n\n{text}")
    # <pre> の中は生の文字列。フェンスの印・コメントの始まりとして扱わず、</pre> の後は通常の検査に戻る
    link_text = "[要件](../01-requirements/requirements.md)"
    docs_case("<pre> の中のバッククォートはフェンスではない", 1, "リンクの先がない: missing.md", basic, link_text,
              f"<pre>\n{FENCE}\n</pre>\n\n[要件](missing.md)")
    docs_case("<pre> の中のコメントの始まり", 1, "リンクの先がない: missing.md", basic, link_text,
              "<pre>\n<!-- 例\n</pre>\n\n[要件](missing.md)")
    docs_case("<pre> の中のリンクとバッククォート", 0, "OK", basic, link_text,
              f"{link_text}\n\n<pre>\n{FENCE}\n[例](missing.md)\n</pre>")
    docs_case("<pre> の中の ID の参照は確かめる", 1, "定義されていない ID: FR-AAA-99", basic, link_text,
              f"{link_text}\n\n<pre>\n{FENCE}\nFR-AAA-99\n</pre>")
    docs_case("1 行の <pre> の次の行", 1, "リンクの先がない: missing.md", basic, link_text,
              f"<pre>{FENCE}</pre>\n[要件](missing.md)")
    docs_case("フェンスの中の <pre> はコードの本文", 1, "リンクの先がない: missing.md", basic, link_text,
              f"{FENCE}html\n<pre>\n{FENCE}\n\n[要件](missing.md)")
    # 「(」の直後の空白を読み飛ばしてリンク先を確かめる（CommonMark）
    docs_case("「(」の直後に空白のあるリンク", 1, "リンクの先がない: missing.md", basic, "[要件](../01-requirements/requirements.md)",
              "[設計]( missing.md)")
    # リンク先の括弧は対をとって読む（design(v2).txt の「)」をリンクの終わりと取り違えない）
    docs_case("括弧を含むリンク先", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) [版 2](design(v2).txt) [題](design(v2).txt \"題\")",
              extra={"docs/02-basic-design/design(v2).txt": "v2\n"})
    docs_case("括弧を含むリンク先がない", 1, "リンクの先がない: missing(v2).txt", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) [版 2](missing(v2).txt)")
    # エスケープした括弧（\\(・\\)）は括弧の対として数えず、エスケープを戻してから解決する（しない括弧と同じファイルを指す）
    docs_case("エスケープした括弧を含むリンク先", 0, "OK", basic, "[要件](../01-requirements/requirements.md)",
              "[要件](../01-requirements/requirements.md) [版 2](design\\(v2\\).txt) [片](design\\(v2.txt)",
              extra={"docs/02-basic-design/design(v2).txt": "v2\n", "docs/02-basic-design/design(v2.txt": "v2\n"})
    docs_case("エスケープした括弧を含むリンク先がない", 1, "リンクの先がない: missing(v2.txt", basic,
              "[要件](../01-requirements/requirements.md)", "[要件](../01-requirements/requirements.md) [片](missing\\(v2.txt)")
    # 文字参照も 1 回の走査で戻す。節の区切りは文字参照を戻した後・パーセント符号化を戻す前に分ける
    for label, target, want in (
        ("名前の文字参照", "a&amp;b.txt", ""), ("10 進の文字参照", "a&#38;b.txt", ""), ("16 進の文字参照", "a&#x26;b.txt", ""),
        ("「#」の文字参照は節の区切り", "a&amp;b.txt&#35;節", ""), ("符号化した「#」はファイルの名前", "c%23d.txt", ""),
        ("エスケープした「&」は文字参照にしない", "x\\&amp;y.txt", ""), ("「;」のない文字参照は戻さない", "x&ampy.txt", "x&ampy.txt"),
        ("知らない名前の文字参照は戻さない", "x&notit;y.txt", "x&notit;y.txt"), ("戻した名前のファイルがない", "missing&amp;.txt", "missing&.txt"),
    ):
        docs_case(f"リンク先の{label}", 1 if want else 0, f"リンクの先がない: {want}" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", f"[要件](../01-requirements/requirements.md) [例]({target})",
                  extra={"docs/02-basic-design/a&b.txt": "", "docs/02-basic-design/c#d.txt": "", "docs/02-basic-design/x&amp;y.txt": "",
                         "docs/02-basic-design/x¬it;y.txt": ""})
    docs_case("コメントの中の語だけの必須の節", 1, "「セキュリティ」の節がない", detailed, "## 2. セキュリティ", "## 2. その他 <!-- セキュリティ -->")
    docs_case("コメントのある必須の節", 0, "OK", detailed, "## 2. セキュリティ", "## 16. セキュリティ <!-- 補足 -->")
    quoted_snippet_case()
    absolute_snippet_case()
    snippet_order_case()
    # 対になるエスケープされていない「[」のない「](」はリンクにしない。ふつうのリンク・エスケープした「\\」の後のリンク・
    # 画像を含むリンク・行をまたぐリンクの文は、これまでどおり確かめる
    for label, text, want in (
        ("エスケープした「[」", "\\[例](missing.md)", ""),
        ("エスケープした「]」", "[例\\](missing.md)", ""),
        ("対になる「[」のない「]」", "例](missing.md)", ""),
        ("ふつうのリンク", "[例](missing.md)", "missing.md"),
        ("エスケープした「\\」の後のリンク", "\\\\[例](missing.md)", "missing.md"),
        ("エスケープした括弧を文に含むリンク", "[例\\]続き](missing.md)", "missing.md"),
        ("画像を含むリンク", "[![図](../README.md)](missing.md)", "missing.md"),
        ("行をまたぐリンクの文", "[例\n続き](missing.md)", "missing.md"),
        # 引用の中でも、印を除いた本文で段落の続きを読む。空の引用の行・空行での引用の終わり・深くなる変化では段落を切る。
        # 浅くなる行（「>」が減る・なくなる）は、段落の途中なら怠惰な続きの行（CommonMark）で、段落を切らない
        ("引用の行をまたぐリンクの文", "> [例\n> 続き](missing.md)", "missing.md"),
        ("入れ子の引用の行をまたぐリンクの文", "> > [例\n> > 続き](missing.md)", "missing.md"),
        ("引用の行をまたぐ、あるリンク先", "> [例\n> 続き](../README.md)", ""),
        ("空の引用の行をまたぐ括弧", "> [例\n>\n> 続き](missing.md)", ""),
        ("引用の終わりをまたぐ括弧", "> [例\n\n続き](missing.md)", ""),
        ("引用の深さが変わる括弧", "> [例\n> > 続き](missing.md)", ""),
        ("引用の中の 1 行のリンク", "> 前の行\n> [例](missing.md)", "missing.md"),
        ("引用の怠惰な続きの行をまたぐリンクの文", "> [例\n続き](missing.md)", "missing.md"),
        ("入れ子の引用の浅くなる続きの行をまたぐリンクの文", "> > [例\n> 続き](missing.md)", "missing.md"),
        ("怠惰な続きの行をまたぐ、あるリンク先", "> [例\n続き](../README.md)", ""),
        # 怠惰な続きの行の後に元の深さへ戻る行は、同じ段落の続き（段落の深さは怠惰な続きの行で浅くならない）。
        # 段落の深さより深くなる行だけが入れ子に入る
        ("浅くなった後に元の深さへ戻る続きの行", "> > [例\n> 続き\n> > 続き](missing.md)", "missing.md"),
        ("怠惰な続きの後に引用へ戻る続きの行", "> [例\n続き\n> 続き](missing.md)", "missing.md"),
        ("怠惰な続きの後に段落より深くなる括弧", "> [例\n続き\n> > 続き](missing.md)", ""),
        # リンクの中にリンクは入らない。内側がリンクになれば、外側の「[」は文字のまま表示される。画像の外側は無効にしない
        ("入れ子のリンクの外側", "[外 [内](../README.md)](missing.md)", ""),
        ("入れ子のリンクの内側", "[外 [内](missing.md)](../README.md)", "missing.md"),
        ("行をまたぐ入れ子のリンクの外側", "[外 [内](../README.md)\n続き](missing.md)", ""),
        ("入れ子のリンクの後のリンク", "[外 [内](../README.md)](x.md) [後](missing.md)", "missing.md"),
        ("画像を含むリンクの画像", "[![図](missing.png)](../README.md)", "missing.png"),
        ("画像の文の中のリンクの外側の画像", "![図 [内](../README.md)](missing.png)", "missing.png"),
        ("画像の中のリンクで無効になる外側", "[外 ![図 [内](../README.md)](../README.md)](missing.md)", ""),
        ("エスケープした「!」の入れ子の外側", "\\![外 [内](../README.md)](missing.md)", ""),
        ("段落をまたぐ括弧", "[例\n\n続き](missing.md)", ""),
        ("次の行のリンク先", '[例](\nmissing.md)', "missing.md"),
        ("次の行の字下げした山括弧のリンク先", '[例]( \n  <missing file.md>\n"題")', "missing file.md"),
        ("次の行のあるリンク先", '[例](\n../README.md)', ""),
        ("段落をまたぐリンク先はリンクでない", '[例](\n\nmissing.md)', ""),
        ("次の行のタイトル", '[例](missing.md\n"題")', "missing.md"),
        ("行をまたぐタイトル", '[例](missing.md "題\n続き"\n)', "missing.md"),
        ("段落をまたぐタイトル", '[例](missing.md\n\n"題")', ""),
        ("見出しをまたぐタイトル", '## 4. [例](missing.md\n\n満たす要件: —\n\n"題")', ""),
        ("箇条書きをまたぐタイトル", '- [例](missing.md\n- "題")', ""),
        ("次の行のタイトルの後のリンク", '[例](../README.md\n"題") [後](missing.md)', "missing.md"),
        ("次の行のタイトルの中のリンクの形", '[例](../README.md\n"[中](missing.md)")', ""),
        ("次の行のタイトルの中の参照の形", '[例](../README.md\n"[壊れた]")\n\n[壊れた]: missing.md', ""),
    ):
        docs_case(f"リンクの文の括弧: {label}", 1 if want else 0, f"リンクの先がない: {want}" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", f"[要件](../01-requirements/requirements.md)\n\n{text}")
    # 行をまたぐリンクの診断は、リンクが始まる行を示す
    directory = docs_case("行をまたぐリンクの診断の準備", 1, "missing.md", basic, "[要件](../01-requirements/requirements.md)",
                          '[要件](../01-requirements/requirements.md)\n\n前の行\n[例](missing.md\n"題") 後')
    count += 1
    line = (directory / basic).read_text(encoding="utf-8").splitlines().index("[例](missing.md") + 1
    code, output = run([str(SCRIPTS / "verify-docs.py"), "--root", str(directory)])
    if f"{basic}:{line}: リンクの先がない: missing.md" not in output or output.count("missing.md") != 1:
        failures.append(f"docs: 行をまたぐリンクの診断の行が違う\n{output.strip()}")
    # コード片の印のパスは空白を含んでよい。照合と書き直しの両方で読み、起点の外・パスのない印は不備として報告する
    spaced = {"scripts/with space.sh": "echo spaced\n"}
    file_marker = "<!-- file: scripts/sample.sh -->"
    fenced = f"{FENCE}bash\necho one\necho two\necho three\n{FENCE}"
    for label, path, body, options, want, want_text in (
        ("一致", "scripts/with space.sh", "echo spaced", [], 0, "OK"),
        ("不一致", "scripts/with space.sh", "echo stale", [], 1, "コード片が scripts/with space.sh と一致しない"),
        ("書き直し", "scripts/with space.sh", "echo stale", ["--update-snippets"], 0, "コード片を書き直した"),
        ("起点の外", "../out side.sh", "echo stale", ["--update-snippets"], 1, "--root の外にある"),
        ("パスなし", "", "echo stale", [], 1, "印にパスがない"),
        ("パスなしの書き直し", "", "echo stale", ["--update-snippets"], 1, "印にパスがない"),
    ):
        docs_case(f"コード片の印のパスの空白: {label}", want, want_text, detailed, f"{file_marker}\n\n{fenced}",
                  f"<!-- file: {path} -->\n\n{FENCE}bash\n{body}\n{FENCE}", options=options, extra=spaced)
    # 印と見出しの文の間がタブの見出しも節として数え、冒頭の「満たす要件:」を確かめる
    docs_case("タブで区切った節の見出し", 1, "節「3a. 補足」の冒頭に「満たす要件:」の行がない", basic,
              "## 3. テスト観点\n\n満たす要件: TST-AAA-01\n\n", "## 3. テスト観点\n\n満たす要件: TST-AAA-01\n\n##\t3a. 補足\n\n本文\n\n")
    readlink_failure_case()
    # 表は空行か別のブロックの始まりまで続き、「|」を含まない行もデータ行になる（GFM と同じ）
    docs_case("「|」を含まない表の行の定義", 1, "定義が重複", requirements, "| CON-01 | 制約 |\n", "| CON-01 | 制約 |\nCON-01\n")
    # コメントで始まる行（HTML のブロック）は表の見出しにならない。始まった表のセルの中のコメントは、セルの一部のまま
    docs_case("コメントの行を見出しにした対応表", 1, "対応表の行がない", detailed, "| 要件 | 試験 |\n", "<!-- 例 --> 要件 | 試験\n")
    docs_case("1 行のコメントにした見出しの対応表", 1, "対応表の行がない", detailed, "| 要件 | 試験 |\n", "<!-- 要件 | 試験 -->\n")
    docs_case("セルの中のコメントは表の一部", 0, "OK", detailed, "| FR-AAA-01 | 試験 |", "| FR-AAA-01 | <!-- 注 --> 試験 |")
    # 短縮形の参照のリンク（[ラベル]）も、定義を通してリンク先を確かめる。定義の行・定義のないラベル・脚注は使用として数えない
    link = "[要件](../01-requirements/requirements.md)"
    docs_case("短縮形の参照のリンク", 0, "OK", basic, link, "[要件] と [x]\n\n[要件]: ../01-requirements/requirements.md")
    # インラインのリンクのリンク先・タイトルの中の参照の形は、参照ではない（本文の同じ形は確かめる）
    for label, body, want in (
        ("タイトルの中", '[要件](../01-requirements/requirements.md "[文][壊れた]")', 0),
        ("タイトルの中の短縮形", '[要件](../01-requirements/requirements.md "[壊れた] [壊れた][]")', 0),
        ("山括弧のリンク先の中", "[要件](<../01-requirements/requirements.md#[文][壊れた]>)", 0),
        ("リンクの前", '[文][壊れた] [要件](../01-requirements/requirements.md "題")', 1),
        ("リンクの後", '[要件](../01-requirements/requirements.md "題") [文][壊れた]', 1),
        ("リンクにならない括弧の中", "[要件] (本文 [文][壊れた])", 1),
    ):
        docs_case(f"リンクの中の参照の形: {label}", want, "リンクの先がない: missing.md" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", body + "\n\n[壊れた]: missing.md")
    # クエリと節を除いてから、パーセント符号化を戻す（符号化した %3F・%23 はファイル名の文字）
    for label, body, want in (
        ("クエリ", "[要件](../01-requirements/requirements.md?plain=1)", 0),
        ("クエリと節", "[要件](../01-requirements/requirements.md?plain=1#L1)", 0),
        ("節の中の ?", "[要件](../01-requirements/requirements.md#a?b)", 0),
        ("符号化した ? と #", "[要件](a%3Fb%23c.txt)", 0),
        ("符号化した ? は区切らない", "[要件](../01-requirements/requirements.md%3Fplain=1)", 1),
        ("クエリの前がない", "[要件](missing.md?plain=1)", 1),
    ):
        docs_case(f"リンク先の{label}", want, "リンクの先がない" if want else "OK", basic,
                  "[要件](../01-requirements/requirements.md)", body, extra={"docs/02-basic-design/a?b#c.txt": "x\n"})
    docs_case("短縮形の参照の切れたリンク", 1, "リンクの先がない: missing.md", basic, link, "本文の [要件] を見る\n\n[要件]: missing.md")
    # 定義のリンク先の後には、完全なタイトルだけを書ける（ほかの文字が続く行は定義ではない）
    for rest in (" 続きの文", ' "題" 続き', ' "閉じない題', " '題' '題'"):
        docs_case(f"リンク先の後に文字が続く行は定義にしない（{rest.strip()}）", 0, "OK", basic, link, f"[要件]\n\n[要件]: missing.md{rest}")
    for rest in ("", "  ", ' "題"', " '題'", " (題)", ' "題 \\"引用\\""'):
        docs_case(f"完全なタイトルの定義（{rest.strip()}）", 1, "リンクの先がない: missing.md", basic, link, f"[要件]\n\n[要件]: missing.md{rest}")
    # タイトルは同じ段落の続きの行にまたがってよい（閉じる区切りの行まで読む）。閉じる前に段落が切れる・閉じた後に文字が続く場合は
    # 定義にならない（タイトルのない定義の次の行で始めたタイトルなら、定義はタイトルなしのまま残る）
    requirement = "../01-requirements/requirements.md"
    for label, body, want in (
        ("行をまたぐタイトルの定義", '[要件]\n\n[要件]: missing.md "題\n続き\n終わり"', 1),
        ("次の行で始めて行をまたぐタイトル", "[要件]\n\n[要件]: missing.md\n(題\n続き)", 1),
        ("ラベルの次の行のリンク先と行をまたぐタイトル", "[要件]\n\n[要件]:\n  missing.md '題\n続き'", 1),
        ("閉じる前に空行で切れたタイトル", '[要件]\n\n[要件]: missing.md "題\n続き\n\n本文', 0),
        ("閉じる前に見出しで切れたタイトル", '[要件]\n\n[要件]: missing.md "題\n### 小節', 0),
        ("閉じた後に文字が続くタイトル", '[要件]\n\n[要件]: missing.md "題\n続き" 文', 0),
        ("タイトルのない定義の後の閉じないタイトル", '[要件]\n\n[要件]: missing.md\n"題\n続き\n\n本文', 1),
        # 定義が使った行（タイトルの続きの行を含む）の中のリンクの形は、インラインのリンクとしても読まない
        ("定義の行のタイトルの中のインラインのリンクの形", f'[一][a]\n\n[a]: {requirement} "[中](missing.md)"', 0),
        ("行をまたぐタイトルの中のインラインのリンクの形", f'[一][a]\n\n[a]: {requirement} "題\n[中](missing.md)"', 0),
        ("次の行のタイトルの中のインラインのリンクの形", f'[一][a]\n\n[a]: {requirement}\n"題\n[中](missing.md) 続き"', 0),
        # 定義の行は段落を切る。後の行の本文のリンクはこれまでどおり確かめ、閉じないタイトルの行も本文として確かめる
        ("定義の後の本文のインラインのリンク", f"[一][a]\n\n[a]: {requirement}\n本文 [文](missing.md)", 1),
        ("閉じないタイトルの行の本文のリンク", f'[一][a]\n\n[a]: {requirement} "題\n[文](missing.md)\n\n後', 1),
    ):
        docs_case(f"参照の定義の{label}", want, "リンクの先がない: missing.md" if want else "OK", basic, link, body)
    # 字下げしたコードの中の印は説明用の例で、照合も書き直しもしない。箇条書きの中の印はこれまでどおり読む
    marker = "<!-- file: scripts/sample.sh -->"
    sample = f"{marker}\n\n```bash\n"
    for options in ([], ["--update-snippets"]):
        example = f"説明\n\n    {marker}\n\n```bash\nother\n```\n\n"
        directory = docs_case(f"字下げした例の印（{options}）", 0, "OK", detailed, sample, example + sample, options)
        count += 1
        if example not in (directory / detailed).read_text(encoding="utf-8"):
            failures.append(f"docs: 字下げした例のコード片を書き直した（{options}）")
    listed = f"- 項目\n\n  {marker}\n\n  ```bash\n  other\n  ```\n\n"
    docs_case("箇条書きの中の印", 1, "コード片が scripts/sample.sh と一致しない", detailed, sample, listed + sample)
    directory = docs_case("箇条書きの中の印の書き直し", 0, "OK", detailed, sample, listed + sample, ["--update-snippets"])
    count += 1
    if "  other" in (directory / detailed).read_text(encoding="utf-8"):
        failures.append("docs: 箇条書きの中のコード片を書き直さない")
    docs_case("使われていない定義と脚注は確かめない", 0, "OK", basic, link, "注[^1] \\[要件]\n\n[要件]: missing.md\n[^1]: missing")
    # 複数行の HTML のコメントの中の表は、対応表・定義として数えない
    docs_case("コメントにした対応表", 1, "対応表の行がない", detailed,
              "| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n",
              "<!--\n| 要件 | 試験 |\n| --- | --- |\n| FR-AAA-01 | 試験 |\n-->\n")
    docs_case("コメントにした重複の定義", 0, "OK", requirements, "| CON-01 | 制約 |\n",
              "| CON-01 | 制約 |\n\n<!--\n| ID | 要件 | フェーズ |\n| --- | --- | --- |\n| FR-AAA-01 | 重複 | MVP |\n-->\n")
    del created  # 一時ディレクトリは test_docs の TemporaryDirectory が消す
    with tempfile.TemporaryDirectory() as missing:
        expect("docs: 存在しない root", [str(SCRIPTS / "verify-docs.py"), "--root", str(Path(missing) / "none")], 2, "docs")


TESTS = {"settings": test_settings, "dependencies": test_dependencies, "versions": test_versions, "gitleaks": test_gitleaks,
         "tracked": test_tracked, "docs": test_docs, "coverage": test_coverage}


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
