#!/usr/bin/python3
"""プロジェクト設定・Info.plist・entitlements の検査（TST-CI-06、TST-SEC-03）。

基本設計 dev-environment.md §3 の「変更後の値」になっていること、
「設定しないもの」が入っていないことを確かめる。

使い方: scripts/check-project-settings.py [--project <project.xcproj>] [--root <探す起点>]
終了コード: 0 = 問題なし / 1 = 違反あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import fnmatch
import plistlib
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Tuple
from xml.parsers.expat import ExpatError

from xcproj import Unreadable, load_project

ROOT = Path(__file__).resolve().parent.parent
# 検査の対象にしないディレクトリ（成果物と導入したツール）
SKIPPED_DIRECTORIES = {".git", ".tools", ".build", "build", "DerivedData", "xcuserdata"}

# すべてのターゲットで満たす値: 設定名 → (許す値, 根拠)
REQUIRED_ALL_TARGETS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "TARGETED_DEVICE_FAMILY": (("1",), "CON-03"),
    "SUPPORTED_PLATFORMS": (("iphoneos iphonesimulator", "iphonesimulator iphoneos"), "CON-03"),
    "SWIFT_VERSION": (("6.0", "6"), "CON-04"),
    "SWIFT_TREAT_WARNINGS_AS_ERRORS": (("YES",), "TST-CI-01"),
    "IPHONEOS_DEPLOYMENT_TARGET": (("27.2",), "CON-01"),
}
# アプリのターゲットだけで満たす値
REQUIRED_APPLICATION: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone": (("UIInterfaceOrientationPortrait",), "BD-UI-10"),
}
# アプリのターゲットで空でない値が要る設定
REQUIRED_NONEMPTY_APPLICATION = {"INFOPLIST_KEY_NSMicrophoneUsageDescription": "SEC-PRV-01"}
# あってはならないビルド設定
FORBIDDEN_BUILD_SETTINGS = {"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad": "BD-UI-10"}

# Info.plist: あるだけで違反になる鍵
FORBIDDEN_PLIST_KEYS = {
    "NSAppTransportSecurity": "SEC-NET-05",
    "CFBundleURLTypes": "SEC-PLT-04",
    "NSUserActivityTypes": "SEC-PLT-02",
}
FORBIDDEN_PLIST_KEY_PREFIXES = {"NSAllows": "SEC-NET-05", "NSException": "SEC-NET-05"}
# Info.plist: 有効にすると違反になる鍵
FORBIDDEN_WHEN_ENABLED = {
    "UIFileSharingEnabled": "SEC-DAT-04",
    "LSSupportsOpeningDocumentsInPlace": "SEC-DAT-04",
    "UISupportsDocumentBrowser": "SEC-DAT-04",
}
# 利用目的の文言は、使う権限のものだけを置く（TST-SEC-03）
ALLOWED_USAGE_DESCRIPTIONS = {"NSMicrophoneUsageDescription"}
ALLOWED_BACKGROUND_MODES = {"audio", "processing"}
# 拡張はウィジェット（Live Activity）だけを認める。共有拡張などは置かない（SEC-PLT-04）
ALLOWED_EXTENSION_POINTS = {"com.apple.widgetkit-extension"}

# entitlements: あってはならない鍵（Personal Team で使えない、または使わない capability）
FORBIDDEN_ENTITLEMENTS = {
    "aps-environment": "CON-02（Push 通知）",
    "com.apple.developer.associated-domains": "SEC-PLT-04",
    "com.apple.developer.siri": "SEC-PLT-02",
    "com.apple.developer.usernotifications.time-sensitive": "CON-02",
}
FORBIDDEN_ENTITLEMENT_PREFIXES = {
    "com.apple.developer.icloud": "SEC-NET-01（iCloud）",
    "com.apple.developer.ubiquity": "SEC-NET-01（iCloud）",
}


def as_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def variants(settings: dict, name: str) -> List[Tuple[str, str]]:
    """条件付き（NAME[config=Debug] など）を含めて、その設定の全ての書き方を返す。"""
    return [(key, as_text(value)) for key, value in settings.items() if key.split("[", 1)[0] == name]


def enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"yes", "true", "1"}


def conditions(key: str) -> Dict[str, str]:
    """NAME[config=Debug][sdk=iphoneos*] の条件を辞書で返す。"""
    return dict(re.findall(r"\[(\w+)=([^\]]*)\]", key))


def resolve(own: dict, shared: dict, name: str, configurations: List[str]) -> Tuple[Dict[Tuple[str, str], List[str]], List[str]]:
    """構成ごとに効く書き方を求める。戻り値は（鍵, 値）→ 構成の一覧 と、どの段でも決まらない構成の一覧。

    ターゲットの設定がプロジェクトの設定を上書きし、同じ段では構成の条件が一致する書き方が条件のない書き方を上書きする。
    構成以外の条件（sdk・arch）の書き方は、その条件のビルドで効きうるので、すべて検査の対象にする。
    """
    effective: Dict[Tuple[str, str], List[str]] = {}
    missing: List[str] = []
    for configuration in configurations:
        covered = False
        for level in (own, shared):
            applicable = []
            for key, value in variants(level, name):
                condition = conditions(key)
                if "config" in condition and not fnmatch.fnmatchcase(configuration, condition["config"]):
                    continue
                applicable.append((key, value, condition))
            if any(set(condition) == {"config"} for _, _, condition in applicable):
                applicable = [item for item in applicable if item[2]]  # 条件のない書き方は、この構成では効かない
            for key, value, _ in applicable:
                effective.setdefault((key, value), []).append(configuration)
            # 構成以外の条件を持たない書き方があれば、この段で決まる（下の段は効かない）
            if any(set(condition) <= {"config"} for _, _, condition in applicable):
                covered = True
                break
        if not covered:
            missing.append(configuration)
    return effective, missing


def check_required(
    target: str, own: dict, shared: dict, configurations: List[str],
    rules: Dict[str, Tuple[Tuple[str, ...], str]], errors: List[str]
) -> None:
    for name, (allowed, reason) in rules.items():
        effective, missing = resolve(own, shared, name, configurations)
        if missing:
            errors.append(f"[{reason}] {target}: {name} が設定されていない（構成 {'・'.join(missing)}。必要な値: {allowed[0]}）")
        for (key, value), used in effective.items():
            if value not in allowed:
                errors.append(f"[{reason}] {target}: {key} = {value}（構成 {'・'.join(used)}。必要な値: {allowed[0]}）")


def check_nonempty(
    target: str, own: dict, shared: dict, configurations: List[str], rules: Dict[str, str], errors: List[str]
) -> None:
    """すべての構成で、効く書き方がすべて空でないこと（条件付きの書き方が空なら違反）。"""
    for name, reason in rules.items():
        effective, missing = resolve(own, shared, name, configurations)
        if missing:
            errors.append(f"[{reason}] {target}: {name} が設定されていない（構成 {'・'.join(missing)}）")
        for (key, value), used in effective.items():
            if not value.replace("$(inherited)", "").strip():
                errors.append(f"[{reason}] {target}: {key} が空（構成 {'・'.join(used)}）")


def check_plist_entry(source: str, key: str, value: object, errors: List[str]) -> None:
    """Info.plist の 1 項目を調べる。入れ子の辞書と配列もたどる。"""
    if key in FORBIDDEN_PLIST_KEYS:
        errors.append(f"[{FORBIDDEN_PLIST_KEYS[key]}] {source}: {key} を設定しない")
    for prefix, reason in FORBIDDEN_PLIST_KEY_PREFIXES.items():
        if key.startswith(prefix):
            errors.append(f"[{reason}] {source}: {key} を設定しない")
    if key in FORBIDDEN_WHEN_ENABLED and enabled(value):
        errors.append(f"[{FORBIDDEN_WHEN_ENABLED[key]}] {source}: {key} を有効にしない")
    if re.fullmatch(r"NS\w*UsageDescription", key) and key not in ALLOWED_USAGE_DESCRIPTIONS:
        errors.append(f"[TST-SEC-03] {source}: 使わない権限の文言 {key} を置かない")
    if key == "UIBackgroundModes":
        modes = value if isinstance(value, list) else str(value).split()
        for mode in modes:
            if str(mode) not in ALLOWED_BACKGROUND_MODES:
                errors.append(f"[FR-REC-05] {source}: UIBackgroundModes に {mode} を入れない")
    if key == "NSExtensionPointIdentifier" and str(value) not in ALLOWED_EXTENSION_POINTS:
        errors.append(f"[SEC-PLT-04] {source}: 拡張 {value} を置かない")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            check_plist_entry(source, str(child_key), child_value, errors)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for child_key, child_value in item.items():
                    check_plist_entry(source, str(child_key), child_value, errors)


def check_build_settings(target: str, settings: dict, errors: List[str]) -> None:
    for key, value in settings.items():
        name = key.split("[", 1)[0]
        if name in FORBIDDEN_BUILD_SETTINGS:
            errors.append(f"[{FORBIDDEN_BUILD_SETTINGS[name]}] {target}: {key} を削除する")
        if name.startswith("INFOPLIST_KEY_"):
            check_plist_entry(f"{target} の {key}", name[len("INFOPLIST_KEY_"):], value, errors)


def find_files(root: Path, suffixes: Tuple[str, ...]) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not path.name.endswith(suffixes):
            continue
        if SKIPPED_DIRECTORIES.intersection(path.relative_to(root).parts):
            continue
        yield path


def load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError, ExpatError) as error:  # 壊れた XML は ExpatError になる
        raise Unreadable(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise Unreadable(f"{path}: 最上位が辞書でない")
    return data


def check(project_path: Path, root: Path) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    project = load_project(project_path)
    shared = project.get("build-settings", {})
    targets = project.get("targets", [])
    if not isinstance(shared, dict) or not isinstance(targets, list) or not targets:
        raise Unreadable(f"{project_path}: build-settings または targets の形式が違う")
    configurations = project.get("configurations")
    if not isinstance(configurations, list) or not configurations or not all(isinstance(c, str) for c in configurations):
        raise Unreadable(f"{project_path}: configurations の形式が違う")
    check_build_settings("プロジェクト", shared, errors)
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("build-settings"), dict):
            raise Unreadable(f"{project_path}: ターゲットの形式が違う")
        name = f"ターゲット {target.get('name', '?')}"
        own = target["build-settings"]
        check_required(name, own, shared, configurations, REQUIRED_ALL_TARGETS, errors)
        if target.get("product-type") == "application":
            check_required(name, own, shared, configurations, REQUIRED_APPLICATION, errors)
            check_nonempty(name, own, shared, configurations, REQUIRED_NONEMPTY_APPLICATION, errors)
        check_build_settings(name, own, errors)
        for key, value in variants(own, "PRODUCT_BUNDLE_IDENTIFIER"):
            if "devplaceholder" in value:
                warnings.append(f"[CON-09] {name}: {key} が仮の値のまま。最初の実録音の前に確定する")
    for path in find_files(root, ("Info.plist",)):
        for key, value in load_plist(path).items():
            check_plist_entry(str(path.relative_to(root)), str(key), value, errors)
    for path in find_files(root, (".entitlements",)):
        for key in load_plist(path):
            source = str(path.relative_to(root))
            if key in FORBIDDEN_ENTITLEMENTS:
                errors.append(f"[{FORBIDDEN_ENTITLEMENTS[key]}] {source}: {key} を設定しない")
            for prefix, reason in FORBIDDEN_ENTITLEMENT_PREFIXES.items():
                if str(key).startswith(prefix):
                    errors.append(f"[{reason}] {source}: {key} を設定しない")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="プロジェクト設定・Info.plist・entitlements の検査")
    parser.add_argument("--project", type=Path, default=ROOT / "MinutesMobile.xcodeproj" / "project.xcproj")
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    try:
        errors, warnings = check(arguments.project, arguments.root.resolve())
    except Unreadable as error:
        print(f"NG: 設定を読めない: {error}", file=sys.stderr)
        return 2
    for message in warnings:
        print(f"警告: {message}")
    for message in errors:
        print(f"NG: {message}")
    if errors:
        print(f"NG: 設定の違反 {len(errors)} 件")
        return 1
    print("OK: プロジェクト設定・Info.plist・entitlements に違反なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
