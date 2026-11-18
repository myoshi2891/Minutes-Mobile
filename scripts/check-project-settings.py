#!/usr/bin/python3
"""プロジェクト設定・Info.plist・entitlements の検査（TST-CI-06、TST-SEC-03）。

基本設計 dev-environment.md §3 の「変更後の値」になっていること、
「設定しないもの」が入っていないことを確かめる。

使い方: scripts/check-project-settings.py [--project <project.xcproj>] [--root <探す起点>]
終了コード: 0 = 問題なし / 1 = 違反あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import fnmatch
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from xml.parsers.expat import ExpatError

from xcproj import Unreadable, load_project

ROOT = Path(__file__).resolve().parent.parent
# 検査の対象にしないディレクトリ（成果物と導入したツール）。
# 名前がビルドの成果物・個人の設定に固有のものは、どの階層にあっても除外する
SKIPPED_EVERYWHERE = {".build", "DerivedData", "xcuserdata"}
# 一般的な名前（build など）は root 直下だけで除外し、下位の同じ名前のディレクトリ（Sources/build など）は検査する
SKIPPED_AT_ROOT = {".git", ".tools", "build"}

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
# アプリのターゲットで、各構成で最終的に生成される Info.plist に空でない文字列が要る鍵
REQUIRED_PLIST_VALUES_APPLICATION = {"NSMicrophoneUsageDescription": "SEC-PRV-01"}
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
# 利用目的の文言を SDK ごとに確かめるときの SDK（CON-03 の SUPPORTED_PLATFORMS と同じ）
SDKS = ("iphoneos", "iphonesimulator")

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

# SDK の版つきの名前（iphoneos27.2 など）。条件 [sdk=…] はこの名前と照合する。取得できなければ None
_SDK_NAMES: Dict[str, Optional[str]] = {}


def sdk_name(sdk: str) -> Optional[str]:
    """Xcode が SDK の条件を照合する、版つきの SDK 名を返す。取得できなければ None。"""
    if sdk not in _SDK_NAMES:
        env = dict(os.environ)
        env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
        try:
            result = subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-version"], env=env,
                                    capture_output=True, text=True, check=True)
            version = result.stdout.strip()
            _SDK_NAMES[sdk] = f"{sdk}{version}" if re.fullmatch(r"\d+(\.\d+)*", version) else None
        except (OSError, subprocess.CalledProcessError):
            _SDK_NAMES[sdk] = None
    return _SDK_NAMES[sdk]


def sdk_matches(sdk: str, pattern: str, key: str) -> bool:
    """条件 [sdk=pattern] が SDK に当たるか。空の条件はどの SDK にも当たらない。
    版つきの SDK 名を取得できない場合は、条件を無視せず判定できないものとして扱う。"""
    if not pattern:
        return False
    name = sdk_name(sdk)
    if name is None:
        raise Unreadable(f"{key}: SDK {sdk} の版を取得できず、条件 sdk={pattern} を判定できない")
    return fnmatch.fnmatchcase(name, pattern)


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


def resolve(
    own: dict, shared: dict, name: str, configurations: List[str], sdk: Optional[str] = None
) -> Tuple[Dict[Tuple[str, str], List[str]], List[str]]:
    """構成ごとに効く書き方を求める。戻り値は（鍵, 値）→ 構成の一覧 と、どの段でも決まらない構成の一覧。

    ターゲットの設定がプロジェクトの設定を上書きし、同じ段では構成の条件が一致する書き方が条件のない書き方を上書きする。
    sdk を指定しない場合、構成以外の条件（sdk・arch）の書き方は、その条件のビルドで効きうるので、すべて検査の対象にする。
    sdk を指定した場合は、sdk の条件も構成と同じく一致するものだけを使い、構成と sdk の条件だけを持つ書き方で決まったとみなす。
    """
    decided = {"config"} if sdk is None else {"config", "sdk"}
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
                if sdk is not None and "sdk" in condition and not sdk_matches(sdk, condition["sdk"], key):
                    continue
                applicable.append((key, value, condition))
            if any(condition and set(condition) <= decided for _, _, condition in applicable):
                applicable = [item for item in applicable if item[2]]  # 条件のない書き方は、この構成では効かない
            for key, value, _ in applicable:
                effective.setdefault((key, value), []).append(configuration)
            # 決まった条件以外を持たない書き方があれば、この段で決まる（下の段は効かない）
            if any(set(condition) <= decided for _, _, condition in applicable):
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


def check_plist_values(
    project_path: Path, target: str, target_name: str, own: dict, shared: dict, configurations: List[str],
    root: Path, errors: List[str]
) -> None:
    """各構成・各 SDK で最終的に生成される Info.plist に、空でない値があること。

    GENERATE_INFOPLIST_FILE が有効な構成では、ビルド設定の INFOPLIST_KEY_… と INFOPLIST_FILE の参照先が合わさる。
    無効（設定がない場合も無効）な構成では、INFOPLIST_FILE の参照先だけが使われる。
    どちらが優先されるかに頼らないよう、効きうる値はすべて空でないことを求め、どこにもなければ欠落とする。
    ビルド設定の値が SDK の条件付きだけの場合、条件に合わない SDK では参照先の plist で補われていなければ欠落とする。
    """
    for name, reason in REQUIRED_PLIST_VALUES_APPLICATION.items():
        missing: List[str] = []
        empty: Dict[str, List[str]] = {}
        for configuration, sdk in ((c, s) for c in configurations for s in SDKS):
            build = f"{configuration}（{sdk}）"
            generate, generate_undecided = resolve(own, shared, "GENERATE_INFOPLIST_FILE", [configuration], sdk)
            modes = {enabled(value) for (_, value) in generate}
            # 条件（arch など）なしに決まらない構成では、設定のない場合（生成しない）も起こりうる
            if generate_undecided or not modes:
                modes.add(False)
            from_files: List[Tuple[str, str]] = []
            plists, plists_undecided = referenced_files(
                project_path, target_name, own, shared, configuration, "INFOPLIST_FILE", sdk)
            lacking = False  # 参照先の plist のうち、鍵を持たないものがある
            for path in plists:
                data = load_plist(path)
                if name not in data:
                    lacking = True
                    continue
                value = data[name]
                source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
                from_files.append((f"{source} の {name}", value.strip() if isinstance(value, str) else ""))
            from_settings: List[Tuple[str, str]] = []
            settings_cover = False  # ビルド設定の値が、この構成・SDK で条件（arch など）なしに効く
            if True in modes:
                effective, undecided = resolve(own, shared, f"INFOPLIST_KEY_{name}", [configuration], sdk)
                from_settings = [(key, value.replace("$(inherited)", "").strip()) for (key, value) in effective]
                settings_cover = bool(from_settings) and not undecided
            # 生成しない書き方がありうる構成では、ビルド設定だけにある値を数えない。
            # 参照先が条件（arch など）で複数ある場合はどれが使われてもよいように、1 つでも鍵がなければ補えないとする。
            # 参照先が条件なしに決まらない構成（arch の条件付きだけなど）は、別の条件の参照先で補わない
            files_cover = bool(plists) and not lacking and not plists_undecided
            if (False in modes and not files_cover) or (True in modes and not (settings_cover or files_cover)):
                missing.append(build)
            for label, value in from_files + from_settings:
                if not value:
                    empty.setdefault(label, []).append(build)
        if missing:
            errors.append(
                f"[{reason}] {target}: 生成される Info.plist に {name} がない（構成 {'・'.join(missing)}。"
                f"GENERATE_INFOPLIST_FILE = YES で INFOPLIST_KEY_{name} に置くか、INFOPLIST_FILE の参照先に置く）"
            )
        for label, used in empty.items():
            errors.append(f"[{reason}] {target}: {label} が空（構成 {'・'.join(used)}）")


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
    """root の下のファイルを列挙する。除外するディレクトリは降りる前に枝刈りする。

    SKIPPED_EVERYWHERE はどの階層でも、SKIPPED_AT_ROOT は root 直下だけで除外する。
    下位の階層にある一般的な名前のディレクトリ（Sources/build など）は除外せず、検査の対象に残す。
    走査の失敗（読めないディレクトリ）は合格にせず、読めないものとして扱う。
    """
    def failed(error: OSError) -> None:
        raise Unreadable(f"{error.filename}: {error}")

    found: List[Path] = []
    for directory, subdirectories, files in os.walk(root, onerror=failed):
        skipped = SKIPPED_EVERYWHERE | (SKIPPED_AT_ROOT if Path(directory) == root else set())
        subdirectories[:] = [name for name in subdirectories if name not in skipped]
        found.extend(Path(directory) / name for name in files if name.endswith(suffixes))
    yield from sorted(found)


def load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError, ExpatError) as error:  # 壊れた XML は ExpatError になる
        raise Unreadable(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise Unreadable(f"{path}: 最上位が辞書でない")
    return data


def referenced_files(
    project_path: Path, target: str, own: dict, shared: dict, configuration: str, setting: str,
    sdk: Optional[str] = None
) -> Tuple[List[Path], bool]:
    """ビルド設定（INFOPLIST_FILE・CODE_SIGN_ENTITLEMENTS）がその構成で指すファイルと、
    参照先が条件（arch など）なしには決まらないか（resolve の未確定）を返す。

    ファイル名や拡張子にかかわらず検査するために使う。
    相対パスは SRCROOT（.xcodeproj のあるディレクトリ）から解決する。展開できない変数を含む値は合格にしない。
    """
    parent = project_path.parent
    source_root = parent.parent if parent.suffix == ".xcodeproj" else parent
    known = {"SRCROOT": str(source_root), "PROJECT_DIR": str(source_root), "TARGET_NAME": target}
    effective, undecided = resolve(own, shared, setting, [configuration], sdk)
    paths: List[Path] = []
    for (key, value), _ in effective.items():
        text = value.strip()
        if not text:
            continue

        def expand(found: "re.Match[str]") -> str:
            name = found.group(1) or found.group(2)
            if name not in known:
                raise Unreadable(f"{project_path}: {key} = {value} の変数 {name} を展開できない")
            return known[name]

        expanded = re.sub(r"\$\((\w+)\)|\$\{(\w+)\}", expand, text)
        path = Path(expanded)
        paths.append((path if path.is_absolute() else source_root / path).resolve())
    return paths, bool(undecided)


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
    referenced: List[Path] = []
    entitlements: List[Path] = []
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("build-settings"), dict):
            raise Unreadable(f"{project_path}: ターゲットの形式が違う")
        name = f"ターゲット {target.get('name', '?')}"
        own = target["build-settings"]
        target_name = str(target.get("name", ""))
        for configuration in configurations:
            referenced.extend(referenced_files(project_path, target_name, own, shared, configuration, "INFOPLIST_FILE")[0])
            entitlements.extend(
                referenced_files(project_path, target_name, own, shared, configuration, "CODE_SIGN_ENTITLEMENTS")[0])
        check_required(name, own, shared, configurations, REQUIRED_ALL_TARGETS, errors)
        if target.get("product-type") == "application":
            check_required(name, own, shared, configurations, REQUIRED_APPLICATION, errors)
            check_plist_values(project_path, name, target_name, own, shared, configurations, root, errors)
        check_build_settings(name, own, errors)
        for key, value in variants(own, "PRODUCT_BUNDLE_IDENTIFIER"):
            if "devplaceholder" in value:
                warnings.append(f"[CON-09] {name}: {key} が仮の値のまま。最初の実録音の前に確定する")
    found = [path.resolve() for path in find_files(root, ("Info.plist",))]
    # INFOPLIST_FILE が指すファイルも読む。ないファイル・読めないファイルは load_plist が Unreadable にする
    for path in found + [path for path in dict.fromkeys(referenced) if path not in found]:
        source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        for key, value in load_plist(path).items():
            check_plist_entry(source, str(key), value, errors)
    # CODE_SIGN_ENTITLEMENTS が指すファイルも、拡張子にかかわらず読む。ないファイル・読めないファイルは Unreadable
    scanned = [path.resolve() for path in find_files(root, (".entitlements",))]
    for path in scanned + [path for path in dict.fromkeys(entitlements) if path not in scanned]:
        source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        for key in load_plist(path):
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
