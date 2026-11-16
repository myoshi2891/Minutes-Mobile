#!/usr/bin/python3
"""検査スクリプトそのものを試験する（基本設計 dev-environment.md §13）。

合格するはずの入力で通り、不合格にするはずの入力で失敗することを確かめる。
試験用の設定はこのスクリプトの中で組み立て、一時ディレクトリに置く
（リポジトリに「検査の対象外にするディレクトリ」を作らないため）。

使い方: scripts/test-checks.py [settings] [dependencies] [versions] [gitleaks] [docs]   （省略時は全部）
終了コード: 0 = 全件が期待どおり / 1 = 期待と違う結果あり / 2 = 使い方の誤り
"""
import json
import platform
import plistlib
import shutil
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
    settings_case("読めない設定は合格にしない", 2, "読めない", project=lambda t: t + "{")
    settings_case("ターゲットがない設定は合格にしない", 2, "読めない", project=lambda t: '{"build-settings": {}, "targets": []}')


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
    pin = {"identity": "sample-lib", "kind": "remoteSourceControl", "location": "https://example.com/owner/sample-lib.git",
           "state": {"revision": "0000000000000000000000000000000000000000", "version": "1.2.3"}}
    resolved = json.dumps({"pins": [pin], "version": 3})
    branch = json.dumps({"pins": [dict(pin, state={"branch": "main", "revision": "0" * 40})], "version": 3})
    approved = {"identity": "sample-lib", "url": "https://example.com/owner/sample-lib", "version": "1.2.3",
                "license": "MIT", "approvedOn": "2026-11-16", "ledger": "dependencies-and-costs.md §3"}
    manifest = 'let package = Package(name: "A", dependencies: [.package(url: "https://example.com/owner/sample-lib.git", from: "1.2.3")])\n'
    local = 'let package = Package(name: "A", dependencies: [.package(path: "../Local")])\n'
    project = '{"targets": [], "packages": [{"location": "https://example.com/owner/sample-lib"},],}'

    dependencies_case("依存がない", 0, "OK", {})
    dependencies_case("許可リストにない依存（Package.resolved）", 1, "sample-lib 1.2.3", {"Package.resolved": resolved})
    dependencies_case("承認済みの依存", 0, "OK", {"Package.resolved": resolved, "Package.swift": manifest}, [approved])
    dependencies_case("承認した版と違う", 1, "sample-lib 1.2.3", {"Package.resolved": resolved}, [dict(approved, version="1.2.2")])
    dependencies_case("版で固定していない", 1, "版で固定されていない", {"Package.resolved": branch}, [approved])
    dependencies_case("許可リストにない依存（Package.swift）", 1, "sample-lib.git", {"Sub/Package.swift": manifest})
    dependencies_case("ローカルの Package は認める", 0, "OK", {"Package.swift": local})
    dependencies_case("許可リストにない依存（project.xcproj）", 1, "example.com", {"App.xcodeproj/project.xcproj": project})
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
                      {"Package.swift": manifest + '.target(name: "T", dependencies: [.product(name: "S", package: "sample-lib")])\n'},
                      [approved])


def test_versions() -> None:
    setup = str(SCRIPTS / "setup-tools.sh")
    tools = str(ROOT / ".tools")
    original = (ROOT / "tools" / "versions").read_text(encoding="utf-8")
    expect("versions: 導入済みの版が一致する", [setup, "--verify"], 0, "OK")
    cases = {
        "版が違う": (lambda t: replaced(t, "swiftlint   0.65.1", "swiftlint   0.65.0"), "swiftlint"),
        "SHA-256 が違う": (lambda t: replaced(t, "c1e429b0", "00000000"), "SHA-256"),
        "形式が不正": (lambda t: replaced(t, "c1e429b0", "zzzzzzzz"), "不正"),
        "ツールの行がない": (lambda t: "\n".join(line for line in t.splitlines() if line.startswith("#")) + "\n", "1 つもない"),
        "gh の行がほかのツールより後": (lambda t: "\n".join(
            [line for line in t.splitlines() if not line.startswith("gh ")] + [line for line in t.splitlines() if line.startswith("gh ")]
        ) + "\n", "gh の行が"),
        "この Mac 向けの行がない": (lambda t: "\n".join(
            line for line in t.splitlines() if not (line.startswith("gitleaks") and f" {platform.machine()} " in line)
        ) + "\n", "向けの行がない"),
    }
    for label, (change, want_text) in cases.items():
        with tempfile.TemporaryDirectory() as directory:
            versions = Path(directory) / "versions"
            versions.write_text(change(original), encoding="utf-8")
            expect(f"versions: {label}", [setup, "--verify", "--versions", str(versions), "--dest", tools], 1, want_text)
    # 導入記録は一致するが、実行時の gh が別の版を名乗る（取得に使う gh の版を実行して確かめていること）
    gh_rows = [line for line in original.splitlines() if line.startswith("gh ") and f" {platform.machine()} " in line]
    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "tools"
        version, sha = gh_rows[0].split()[1], gh_rows[0].split()[-1]
        stub = dest / "gh" / version / "gh"
        stub.parent.mkdir(parents=True)
        stub.write_text("#!/bin/sh\necho 'gh version 2.0.0 (2000-01-01)'\n", encoding="utf-8")
        stub.chmod(0o755)
        (stub.parent / ".archive-sha256").write_text(sha + "\n", encoding="utf-8")
        (dest / "bin").mkdir()
        (dest / "bin" / "gh").symlink_to(f"../gh/{version}/gh")
        versions = Path(directory) / "versions"
        versions.write_text("\n".join(gh_rows) + "\n", encoding="utf-8")
        expect("versions: 実行時の gh の版が違う", [setup, "--verify", "--versions", str(versions), "--dest", str(dest)], 1,
               "実行時: 2.0.0")


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
    "docs/03-detailed-design/sample.md": (
        "# 詳細設計\n\n## 1. 実装\n\n満たす要件: FR-AAA-01〜02 ／ 決定: BD-SMP-01、UD-01\n\n"
        f"<!-- file: scripts/sample.sh -->\n\n{FENCE}bash\necho one\necho two\necho three\n{FENCE}\n\n"
        f"<!-- excerpt: scripts/sample.sh -->\n\n{FENCE}bash\necho two\necho three\n{FENCE}\n\n"
        "## 2. セキュリティ\n\n満たす要件: SEC-AAA-01\n\n## 3. テスト観点\n\n満たす要件: TST-AAA-01\n\n"
        "## 4. 要件 ID ↔ テストの対応表\n\n満たす要件: TST-AAA-01\n\n"
        "## 5. 基本設計の申し送りの解決\n\n満たす要件: —\n\n| 申し送り | 解決 |\n| --- | --- |\n| 一つ目 | 済 |\n| 二つ目 | 済 |\n"
    ),
    "scripts/sample.sh": "echo one\necho two\necho three\n",
}


def docs_case(label: str, want_code: int, want_text: str, name: str = "", old: str = "", new: str = "",
              options: Optional[List[str]] = None) -> Path:
    directory = Path(tempfile.mkdtemp())
    for path, content in DOCS.items():
        if path == name:
            content = replaced(content, old, new)
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(content, encoding="utf-8")
    expect(f"docs: {label}", [str(SCRIPTS / "verify-docs.py"), "--root", str(directory)] + (options or []), want_code, want_text)
    return directory


def test_docs() -> None:
    global count
    basic, detailed = "docs/02-basic-design/sample.md", "docs/03-detailed-design/sample.md"
    requirements = "docs/01-requirements/requirements.md"
    created = [
        docs_case("整合した文書", 0, "MVP の要件 3 件"),
        docs_case("定義されていない ID", 1, "定義されていない ID: FR-AAA-09", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-AAA-09"),
        docs_case("範囲の省略の中に定義されていない ID", 1, "定義されていない ID: FR-AAA-04", detailed, "FR-AAA-01〜02", "FR-AAA-01〜04"),
        docs_case("定義されていない ID の群", 1, "定義されていない ID の群: FR-ZZZ", detailed, "決定: BD-SMP-01", "決定: BD-SMP-01、FR-ZZZ 群"),
        docs_case("定義されていない設計決定", 1, "定義されていない ID: BD-SMP-02", detailed, "BD-SMP-01", "BD-SMP-02"),
        docs_case("ID の定義の重複", 1, "定義が重複", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-01 | 二 | MVP |"),
        docs_case("MVP の要件が基本設計に現れない", 1, "MVP の要件 FR-AAA-02", basic, "FR-AAA-01・02、", "FR-AAA-01、"),
        docs_case("P2 の要件は網羅の対象にしない", 0, "OK", requirements, "| FR-AAA-02 | 二 | MVP |", "| FR-AAA-02 | 二 | P2 |"),
        docs_case("切れたリンク", 1, "リンクの先がない", basic, "../01-requirements/requirements.md", "../01-requirements/missing.md"),
        docs_case("言語のないコードフェンス", 1, "言語の指定がない", detailed, f"{FENCE}bash\necho one", f"{FENCE}\necho one"),
        docs_case("セキュリティ節がない", 1, "「セキュリティ」の節がない", detailed, "## 2. セキュリティ", "## 2. その他"),
        docs_case("テスト観点の節がない", 1, "「テスト観点」の節がない", basic, "## 3. テスト観点", "## 3. その他"),
        docs_case("節の冒頭に要件 ID がない", 1, "冒頭に「満たす要件:」", basic, "満たす要件: TST-AAA-01\n\n", "本文\n\n"),
        docs_case("満たす要件の行に ID がない", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件: 未定\n"),
        docs_case("満たす要件の行が空", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件:\n"),
        docs_case("満たす要件が設計決定だけ", 1, "要件 ID も「—」の指定もない", basic, "満たす要件: TST-AAA-01\n", "満たす要件: BD-SMP-01\n"),
        docs_case("満たす要件に要件の群", 0, "OK", basic, "満たす要件: TST-AAA-01\n", "満たす要件: TST-AAA 群\n"),
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
        docs_case("申し送りの解決が足りない", 1, "申し送りの解決の表が 1 行", detailed, "| 二つ目 | 済 |\n", ""),
        docs_case("全文のコード片が実ファイルと違う", 1, "コード片が scripts/sample.sh と一致しない", detailed, "echo one\necho two\necho three", "echo one\necho two"),
        docs_case("一部のコード片が実ファイルと違う", 1, "コード片が scripts/sample.sh と一致しない", detailed, f"bash\necho two\necho three\n{FENCE}", f"bash\necho two\necho four\n{FENCE}"),
        docs_case("コード片の元のファイルがない", 1, "元のファイルがない", detailed, "<!-- file: scripts/sample.sh -->", "<!-- file: scripts/missing.sh -->"),
    ]
    # --update-snippets は、全文のコード片を実ファイルに合わせて書き直す
    fixed = docs_case("全文のコード片の書き直し", 0, "コード片を書き直した", detailed, "echo one\necho two\necho three", "echo old",
                      options=["--update-snippets"])
    count += 1
    if "echo one\necho two\necho three" not in (fixed / detailed).read_text(encoding="utf-8"):
        failures.append("docs: 書き直した後の文書に、実ファイルの内容がない")
    for directory in created + [fixed]:
        shutil.rmtree(directory)


TESTS = {"settings": test_settings, "dependencies": test_dependencies, "versions": test_versions, "gitleaks": test_gitleaks,
         "docs": test_docs}


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
