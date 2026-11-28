#!/usr/bin/python3
"""プロジェクト設定・Info.plist・entitlements・プライバシーマニフェストの検査（TST-CI-06、TST-SEC-03）。

基本設計 dev-environment.md §3 の「変更後の値」になっていること、
「設定しないもの」が入っていないこと、プライバシーマニフェストの申告が決めたとおりであることを確かめる。

使い方: scripts/check-project-settings.py [--project <project.xcproj>] [--root <探す起点>] [--tracked-only]
  --tracked-only  ビルド設定が指すファイル（INFOPLIST_FILE・CODE_SIGN_ENTITLEMENTS）と、名前で見つけたファイルの実体が、起点の中の、HEAD のコミットで
                  追跡したファイルであることを求める（check.sh が専用の作業ツリーの検査で渡す）。満たさなければ終了コード 2
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
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Set, Tuple
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
# アプリのターゲットで、Info.plist を生成する構成の向きのビルド設定（BD-UI-10）。生成するかは構成・SDK ごとに決まるので、
# 一律の必須の設定にせず check_plist_orientation で生成の有無に合わせて確かめる
ORIENTATION_SETTING = "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone"
ORIENTATION_SETTING_VALUES = ("UIInterfaceOrientationPortrait",)
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

# プライバシーマニフェスト（SEC-PRV-03）。アプリのターゲットは <ターゲット名>/PrivacyInfo.xcprivacy に置く
PRIVACY_MANIFEST_NAME = "PrivacyInfo.xcprivacy"
# 空の配列で明示する申告（トラッキングのドメイン・収集するデータ）
PRIVACY_EMPTY_KEYS = ("NSPrivacyTrackingDomains", "NSPrivacyCollectedDataTypes")
# 申告してよい API の種類 → 理由コード。アプリのマニフェストは、この全部をちょうど申告する
PRIVACY_ACCESSED_API_REASONS: Dict[str, Tuple[str, ...]] = {
    "NSPrivacyAccessedAPICategoryFileTimestamp": ("C617.1",),
    "NSPrivacyAccessedAPICategoryDiskSpace": ("E174.1",),
    "NSPrivacyAccessedAPICategoryUserDefaults": ("CA92.1",),
}


# SDK の版つきの名前（iphoneos27.2 など）。条件 [sdk=…] はこの名前と照合する。取得できなければ None
_SDK_NAMES: Dict[str, Optional[str]] = {}
# SDK の版の問い合わせ（xcrun）を待つ上限（秒）。応答しない xcrun で検査を止めない。過ぎたら版を取得できないものとする
SDK_QUERY_TIMEOUT = 30


def sdk_name(sdk: str) -> Optional[str]:
    """Xcode が SDK の条件を照合する、版つきの SDK 名を返す。取得できなければ None。"""
    if sdk not in _SDK_NAMES:
        env = dict(os.environ)
        env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
        try:
            result = subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-version"], env=env,
                                    capture_output=True, text=True, check=True, timeout=SDK_QUERY_TIMEOUT)
            version = result.stdout.strip()
            _SDK_NAMES[sdk] = f"{sdk}{version}" if re.fullmatch(r"\d+(\.\d+)*", version) else None
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
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


class Variant(NamedTuple):
    """ビルドの SDK と arch。arch が None なら、arch の条件の書き方は決まらないものとして扱う。"""
    sdk: str
    arch: Optional[str] = None

    def label(self) -> str:
        return self.sdk if self.arch is None else f"{self.sdk}・{self.arch}"


# arch の条件を判定する代表の値。書き方に現れる値と、どの名前とも一致しない値（条件の外のビルド）を possible_variants が足す
ARCHS = ("arm64", "arm64e", "x86_64")
OTHER_ARCH = "その他の arch"


# ARCHS の指定がないときに Xcode が使う値（ARCHS_STANDARD。2026-11-25 に Xcode 27.2 の xcodebuild -showBuildSettings で確かめた）
STANDARD_ARCHS = {"iphoneos": "arm64", "iphonesimulator": "arm64 x86_64"}


def built_archs(own: dict, shared: dict, configuration: str, sdk: str) -> Optional[List[str]]:
    """その構成・SDK で実際にビルドする arch（ARCHS から EXCLUDED_ARCHS を除いたもの）。決められなければ None。"""
    # Xcode の既定を、プロジェクトの段より弱い値として置く（ONLY_ACTIVE_ARCH は、ARCHS のどれにもなりうるので見ない）
    defaults = {"ARCHS": STANDARD_ARCHS[sdk], "ARCHS_STANDARD": STANDARD_ARCHS[sdk], "EXCLUDED_ARCHS": ""}
    base = {**defaults, **shared}
    values: Dict[str, List[str]] = {}
    for name in ("ARCHS", "EXCLUDED_ARCHS"):
        found = setting_value(own, base, name, configuration, Variant(sdk))
        if found is None:
            return None
        expanded = expand_value(found[0], own, base, configuration, Variant(sdk), setting=name, from_target=found[1])
        if expanded is None:
            return None
        values[name] = expanded.split()
    archs = [arch for arch in dict.fromkeys(values["ARCHS"]) if arch not in values["EXCLUDED_ARCHS"]]
    return archs or None


def build_variants(own: dict, shared: dict, configuration: str) -> List[Variant]:
    """その構成で実際にビルドする SDK と arch の組。必要な値の欠落の判定と、変数の展開に使う。

    ビルドする arch を決められない SDK は、possible_variants の組をすべて使う（判定を弱めない）。
    """
    chosen: List[Variant] = []
    for variant in possible_variants(own, shared):
        archs = built_archs(own, shared, configuration, variant.sdk) if variant.arch is not None else None
        if archs is None:
            chosen.append(variant)
        else:
            chosen.extend(found for found in (Variant(variant.sdk, arch) for arch in archs) if found not in chosen)
    return chosen


def possible_variants(own: dict, shared: dict) -> List[Variant]:
    """条件の書き方が当たりうる SDK と arch の組（ビルドしない arch を含む）。禁止する値を探す参照先の列挙に使う。

    arch の条件の書き方がなければ SDK だけの組を返す（arch で結果が変わらない）。
    """
    patterns = {conditions(key)["arch"] for level in (own, shared) for key in level if "arch" in conditions(key)}
    if not patterns:
        return [Variant(sdk) for sdk in SDKS]
    literals = sorted({pattern.replace("*", "").replace("?", "x") for pattern in patterns} - {""})
    return [Variant(sdk, arch) for sdk in SDKS for arch in dict.fromkeys([*ARCHS, *literals, OTHER_ARCH])]


def resolve(
    own: dict, shared: dict, name: str, configurations: List[str], variant: Optional[Variant] = None
) -> Tuple[Dict[Tuple[str, str], List[str]], List[str]]:
    """構成ごとに効く書き方を求める。戻り値は（鍵, 値）→ 構成の一覧 と、どの段でも決まらない構成の一覧。

    ターゲットの設定がプロジェクトの設定を上書きし、同じ段では構成の条件が一致する書き方が条件のない書き方を上書きする。
    variant を指定しない場合、構成以外の条件（sdk・arch）の書き方は、その条件のビルドで効きうるので、すべて検査の対象にする。
    指定した場合は、sdk の条件（arch も指定していれば arch の条件）も構成と同じく一致するものだけを使い、それらの条件だけを持つ
    書き方で決まったとみなす。
    """
    decided = {"config"}
    if variant is not None:
        decided |= {"sdk"} if variant.arch is None else {"sdk", "arch"}
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
                if variant is not None and "sdk" in condition and not sdk_matches(variant.sdk, condition["sdk"], key):
                    continue
                if (variant is not None and variant.arch is not None and "arch" in condition
                        and not fnmatch.fnmatchcase(variant.arch, condition["arch"])):
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
    """各設定を、実際にビルドする構成・SDK・arch の組ごとに求めて確かめる。どれかの組で値が決まらない・許す値でなければ違反とする
    （SDK・arch の条件付きの書き方だけで、一部の組の値を欠く・違う値にすることを見逃さない）。"""
    builds = [(c, v) for sdk in SDKS for c in configurations for v in build_variants(own, shared, c) if v.sdk == sdk]
    for name, (allowed, reason) in rules.items():
        missing: List[str] = []
        wrong: Dict[str, List[str]] = {}
        for configuration, variant in builds:
            build = f"{configuration}（{variant.label()}）"
            effective, undecided = resolve(own, shared, name, [configuration], variant)
            if undecided:
                missing.append(build)
            for key, value in effective:
                # 変数はその組のビルド設定で展開してから比べる。展開できない値は許す値と見なさない
                expanded = expand_value(value, own, shared, configuration, variant, setting=name,
                                        from_target=key in own and as_text(own[key]) == value)
                if expanded is None:
                    wrong.setdefault(f"{key} = {value}（展開できない）", []).append(build)
                elif expanded not in allowed:
                    label = f"{key} = {value}" if expanded == value else f"{key} = {value}（{expanded} に展開される）"
                    wrong.setdefault(label, []).append(build)
        if missing:
            errors.append(f"[{reason}] {target}: {name} が設定されていない（構成 {'・'.join(missing)}。必要な値: {allowed[0]}）")
        for label, used in wrong.items():
            errors.append(f"[{reason}] {target}: {label}（構成 {'・'.join(used)}。必要な値: {allowed[0]}）")


# 変数の展開の深さの上限（循環する参照で止まらないように）
MAX_EXPANSION_DEPTH = 8
PORTRAIT_ONLY = ["UIInterfaceOrientationPortrait"]
# 機種・OS の修飾子つきの鍵（<鍵>-<OS>~<機種>）も、iPhone で使われうるものは同じく縦だけを求める
ORIENTATION_KEYS = ("UISupportedInterfaceOrientations~iphone", "UISupportedInterfaceOrientations",
                    "UISupportedInterfaceOrientations-iphoneos", "UISupportedInterfaceOrientations-iphoneos~iphone")


def setting_value(own: dict, shared: dict, name: str, configuration: str, variant: Variant) -> Optional[Tuple[str, bool]]:
    """その構成・SDK で効く設定の値と、値がターゲットの段のものかを返す。1 つの値に決まらなければ None。

    resolve と同じく、ターゲットの段で決まればプロジェクトの段は効かない。ターゲットの段に条件（arch など）付きの
    書き方だけがある場合は、どちらの段の値が効くか決まらないので None とする。
    """
    effective, undecided = resolve(own, {}, name, [configuration], variant)
    from_target = not undecided
    if not from_target:
        if effective:
            return None
        effective, undecided = resolve({}, shared, name, [configuration], variant)
        if undecided:
            return None
    values = {value for (_, value) in effective}
    return (values.pop(), from_target) if len(values) == 1 else None


def expand_value(
    text: str, own: dict, shared: dict, configuration: str, variant: Variant, depth: int = 0,
    setting: Optional[str] = None, from_target: bool = False
) -> Optional[str]:
    """値の中の $(NAME)・${NAME} を、その構成・SDK のビルド設定で展開する。決められなければ None（未確定）。

    設定がない変数（Xcode が組み込みで持つ変数を含む）、条件（arch など）なしに 1 つの値に決まらない変数、
    修飾子つきの参照（$(NAME:lower) など）は展開できないものとして None を返す。展開できない値を空でない値と見なさない。
    $(inherited) は、値がターゲットの段の設定 setting のものなら、プロジェクトの段の同じ設定の値で展開する。
    プロジェクトの段の値・plist の値・設定の名前が分からない値では継承元（SDK の既定値など）を決められないので None とする。
    """
    if depth > MAX_EXPANSION_DEPTH:
        return None
    unresolved = False

    def substitute(found: "re.Match[str]") -> str:
        nonlocal unresolved
        name = found.group(1) or found.group(2)
        if name == "inherited":
            if setting is None or not from_target:
                unresolved = True
                return ""
            effective, undecided = resolve({}, shared, setting, [configuration], variant)
            values = {value for (_, value) in effective}
            if undecided or len(values) != 1:
                unresolved = True
                return ""
            inherited = expand_value(values.pop(), own, shared, configuration, variant, depth + 1, setting, False)
            if inherited is None:
                unresolved = True
                return ""
            return inherited
        found_value = setting_value(own, shared, name, configuration, variant)
        if found_value is None:
            unresolved = True
            return ""
        expanded = expand_value(found_value[0], own, shared, configuration, variant, depth + 1, name, found_value[1])
        if expanded is None:
            unresolved = True
            return ""
        return expanded

    result = re.sub(r"\$\((\w+)\)|\$\{(\w+)\}", substitute, text)
    if unresolved or re.search(r"\$[({]", result):
        return None
    return result


# Info.plist のファイルの値の変数を、ビルドが展開するかの設定（Xcode の既定は YES）
EXPAND_PLIST_SETTING = "INFOPLIST_EXPAND_BUILD_SETTINGS"


def plist_expansion(own: dict, shared: dict, configuration: str, variant: Variant) -> Optional[bool]:
    """その構成・SDK で、INFOPLIST_FILE の参照先の値の変数をビルドが展開するか。決められなければ None。

    設定がなければ Xcode の既定（YES）とする。条件（arch など）付きの書き方だけで決まらない場合、展開できない値の場合、
    値が書き方によって分かれる場合は None（展開される・されないの両方がありうる）。
    """
    effective, undecided = resolve(own, shared, EXPAND_PLIST_SETTING, [configuration], variant)
    if not effective:
        return True
    results = set()
    for key, value in effective:
        expanded = expand_value(value, own, shared, configuration, variant, setting=EXPAND_PLIST_SETTING,
                                from_target=key in own and as_text(own[key]) == value)
        if expanded is None:
            return None
        results.add(enabled(expanded))
    if undecided:
        results.add(True)  # 条件に当たらないビルドでは既定（YES）が効く
    return results.pop() if len(results) == 1 else None


def infoplist_sources(
    project_path: Path, target_name: str, own: dict, shared: dict, configuration: str, variant: Variant
) -> Tuple[set, List[Tuple[Path, dict]], bool]:
    """その構成・SDK で、生成するか（GENERATE_INFOPLIST_FILE の取りうる値）と、INFOPLIST_FILE の参照先の内容と、
    参照先が条件なしに決まらない（または明示的に空の書き方がある）かを返す。"""
    generate, generate_undecided = resolve(own, shared, "GENERATE_INFOPLIST_FILE", [configuration], variant)
    modes = set()
    for key, value in generate:
        # 変数（$(NAME)）はその構成・SDK で展開してから判定する。展開できなければ、生成する・しないの両方の経路を検査する
        expanded = expand_value(value, own, shared, configuration, variant, setting="GENERATE_INFOPLIST_FILE",
                                from_target=key in own and as_text(own[key]) == value)
        modes.update({True, False} if expanded is None else {enabled(expanded)})
    # 条件（arch など）なしに決まらない構成では、設定のない場合（生成しない）も起こりうる
    if generate_undecided or not modes:
        modes.add(False)
    plists, plists_undecided = referenced_files(
        project_path, target_name, own, shared, configuration, "INFOPLIST_FILE", variant)
    return modes, [(path, load_plist(path)) for path in plists], plists_undecided


# iPhone のビルドで使われうる修飾子つきの鍵の、優先の高い順の組。Info.plist は修飾子つきの鍵を元の鍵より優先して読む。
# -iphoneos と ~iphone の間の優先は文書に定めがないので、両方があれば両方を検査する（どちらが使われてもよいように）
def iphone_key_groups(name: str) -> List[List[str]]:
    return [[f"{name}-iphoneos~iphone"], [f"{name}-iphoneos", f"{name}~iphone"], [name]]


def applicable_keys(name: str, present: Callable[[str], bool]) -> List[str]:
    """iPhone のビルドで使われる鍵（最も具体的な組のうち、あるもの）。どれもなければ元の鍵だけを返す。"""
    for group in iphone_key_groups(name):
        found = [key for key in group if present(key)]
        if found:
            return found
    return [name]


def check_plist_values(
    project_path: Path, target: str, target_name: str, own: dict, shared: dict, configurations: List[str],
    root: Path, errors: List[str]
) -> None:
    """各構成・各 SDK で最終的に生成される Info.plist に、空でない値があること。

    GENERATE_INFOPLIST_FILE が有効な構成では、ビルド設定の INFOPLIST_KEY_… と INFOPLIST_FILE の参照先が合わさる。
    無効（設定がない場合も無効）な構成では、INFOPLIST_FILE の参照先だけが使われる。
    どちらが優先されるかに頼らないよう、効きうる値はすべて空でないことを求め、どこにもなければ欠落とする。
    ビルド設定の値が SDK の条件付きだけの場合、条件に合わない SDK では参照先の plist で補われていなければ欠落とする。
    iPhone の修飾子つきの鍵（<鍵>-iphoneos~iphone・<鍵>-iphoneos・<鍵>~iphone）があれば、元の鍵より優先されるので、
    plist・ビルド設定（INFOPLIST_KEY_…）のそれぞれで最も具体的な鍵の値を判定する（元の鍵だけの plist はそのまま認める）。
    値は構成・SDK ごとに変数（$(NAME)）を展開してから判定する。展開後が空なら空、展開できなければ欠落（未確定）とする。
    ビルド設定の値は文字列化する前の元の値で型を確かめ、文字列でない値（配列など）は認めない。
    """
    for name, reason in REQUIRED_PLIST_VALUES_APPLICATION.items():
        # 文字列化（variants）の前に、元の値の型を確かめる（配列を空白で連結した文字列を利用目的と見なさない）。
        # iPhone の修飾子つきの鍵のビルド設定も同じく確かめる
        candidate_settings = {f"INFOPLIST_KEY_{key}" for group in iphone_key_groups(name) for key in group}
        for label, level in (("ターゲット", own), ("プロジェクト", shared)):
            for key, value in level.items():
                if key.split("[", 1)[0] in candidate_settings and not isinstance(value, str):
                    errors.append(f"[{reason}] {target}: {label}の {key} が文字列でない（{type(value).__name__}）")
        missing: List[str] = []
        empty: Dict[str, List[str]] = {}
        # 効きうる候補のうち展開できないもの。ほかの候補で補えるかによらず違反とする（展開できない値を見逃さない）
        unresolved: Dict[str, List[str]] = {}
        for configuration, variant in ((c, v) for c in configurations for v in build_variants(own, shared, c)):
            build = f"{configuration}（{variant.label()}）"
            modes, plists, plists_undecided = infoplist_sources(project_path, target_name, own, shared, configuration, variant)
            from_files: List[Tuple[str, str]] = []
            lacking = False  # 参照先の plist のうち、鍵を持たない・値を決められないものがある
            for path, data in plists:
                source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
                # iPhone のビルドで使われる鍵（修飾子つきの鍵があればそれ。なければ元の鍵）だけを検査する
                for key in applicable_keys(name, lambda candidate, data=data: candidate in data):
                    if key not in data:
                        lacking = True
                        continue
                    value = data[key]
                    # ビルドが参照先の plist の変数を展開しない（INFOPLIST_EXPAND_BUILD_SETTINGS が NO）、または展開するかを
                    # 決められない構成・SDK では、変数を含む値を展開後の値で認めない（向きの検査と同じ。ビルドの結果に変数の
                    # 文字列がそのまま残りうる）。変数を含まない値は、これまでどおり空でなければ認める
                    if (isinstance(value, str) and VARIABLE.search(value)
                            and plist_expansion(own, shared, configuration, variant) is not True):
                        lacking = True
                        unresolved.setdefault(f"{source} の {key}（{EXPAND_PLIST_SETTING} で変数が展開されない構成）",
                                              []).append(build)
                        continue
                    expanded = expand_value(value, own, shared, configuration, variant) if isinstance(value, str) else ""
                    if expanded is None:
                        lacking = True
                        unresolved.setdefault(f"{source} の {key}", []).append(build)
                        continue
                    from_files.append((f"{source} の {key}", expanded.strip()))
            from_settings: List[Tuple[str, str]] = []
            settings_cover = False  # ビルド設定の値が、この構成・SDK で条件（arch など）なしに効く
            if True in modes:
                # ビルド設定も、iPhone のビルドで使われる鍵（最も具体的な組のうち、その構成・SDK で効く書き方があるもの）を検査する
                chosen = applicable_keys(name, lambda candidate, c=configuration, v=variant: bool(
                    resolve(own, shared, f"INFOPLIST_KEY_{candidate}", [c], v)[0]))
                settings_unresolved = False
                undecided: List[str] = []
                for candidate in chosen:
                    setting = f"INFOPLIST_KEY_{candidate}"
                    effective, missing_here = resolve(own, shared, setting, [configuration], variant)
                    undecided.extend(missing_here)
                    for key, value in effective:
                        expanded = expand_value(value, own, shared, configuration, variant, setting=setting,
                                                from_target=key in own and as_text(own[key]) == value)
                        if expanded is None:
                            settings_unresolved = True
                            unresolved.setdefault(key, []).append(build)
                            continue
                        from_settings.append((key, expanded.strip()))
                settings_cover = bool(from_settings) and not undecided and not settings_unresolved
            # 生成しない書き方がありうる構成では、ビルド設定だけにある値を数えない。
            # 参照先が条件（arch など）で複数ある場合はどれが使われてもよいように、1 つでも鍵がなければ補えないとする。
            # 参照先が条件なしに決まらない構成（arch の条件付きだけ・明示的に空の書き方があるなど）は、別の参照先で補わない
            files_cover = bool(plists) and not lacking and not plists_undecided
            if (False in modes and not files_cover) or (True in modes and not (settings_cover or files_cover)):
                missing.append(build)
            for label, value in from_files + from_settings:
                if not value:
                    empty.setdefault(label, []).append(build)
        if missing:
            errors.append(
                f"[{reason}] {target}: 生成される Info.plist に {name} がない（構成 {'・'.join(missing)}。"
                f"GENERATE_INFOPLIST_FILE = YES で INFOPLIST_KEY_{name} に置くか、INFOPLIST_FILE の参照先に置く。"
                "変数を使う場合は、その構成で 1 つの値に展開できること）"
            )
        for label, used in empty.items():
            errors.append(f"[{reason}] {target}: {label} が空（構成 {'・'.join(used)}）")
        for label, used in unresolved.items():
            errors.append(f"[{reason}] {target}: {label} の値を展開できない（構成 {'・'.join(used)}。"
                          "その構成で 1 つの値に展開できる変数だけを使う）")


def check_plist_orientation(
    project_path: Path, target: str, target_name: str, own: dict, shared: dict, configurations: List[str],
    root: Path, errors: List[str]
) -> None:
    """画面の向きを縦だけにする（BD-UI-10）。構成・SDK ごとの Info.plist の生成の有無に合わせて確かめる。

    GENERATE_INFOPLIST_FILE = NO の構成では INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone は使われないので、
    INFOPLIST_FILE の参照先に UISupportedInterfaceOrientations~iphone か UISupportedInterfaceOrientations が要る。
    生成する構成では、ビルド設定の値か参照先の plist の指定のどちらかで縦だけに決まること（参照先の plist が縦だけを
    持てば、ビルド設定がなくても違反にしない）。生成する・しないの両方がありうる構成では、両方の条件を求める。
    どの構成でも、効きうるビルド設定の値と参照先にある向きの指定は縦だけであること（合わさっても横向きを足さない）。
    参照先の plist の向きの配列の各要素は、その構成・SDK のビルド設定で変数を展開してから比べる（展開できなければ違反）。
    """
    missing: List[str] = []
    wrong: Dict[str, List[str]] = {}
    for configuration, variant in ((c, v) for c in configurations for v in build_variants(own, shared, c)):
        build = f"{configuration}（{variant.label()}）"
        modes, plists, plists_undecided = infoplist_sources(project_path, target_name, own, shared, configuration, variant)
        covered = bool(plists) and not plists_undecided
        for path, data in plists:
            present = [key for key in ORIENTATION_KEYS if key in data]
            if not present:
                covered = False
            source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            for key in present:
                # 各要素の変数（$(NAME)）はその構成・SDK のビルド設定で展開してから比べる。配列でない値・文字列でない要素・
                # 展開できない要素は縦だけと見なさない。ビルドが参照先の plist の変数を展開しない（INFOPLIST_EXPAND_BUILD_SETTINGS
                # が NO）、または展開するかを決められない構成・SDK では、変数を含む要素は展開後の値で認めない（ビルドの結果には
                # 変数の文字列がそのまま残りうる）
                value = data[key]
                items = value if isinstance(value, list) else None
                expands = plist_expansion(own, shared, configuration, variant)
                if items is not None and expands is not True and any(isinstance(item, str) and VARIABLE.search(item)
                                                                       for item in items):
                    wrong.setdefault(f"{source} の {key}（{EXPAND_PLIST_SETTING} で変数が展開されない構成）", []).append(build)
                    continue
                expanded = ([expand_value(item, own, shared, configuration, variant) if isinstance(item, str) else None
                             for item in items] if items is not None else None)
                if expanded is not None and any(item is None for item in expanded):
                    wrong.setdefault(f"{source} の {key}（展開できない）", []).append(build)
                elif expanded != PORTRAIT_ONLY:
                    wrong.setdefault(f"{source} の {key}", []).append(build)
        settings_cover = False  # ビルド設定の値が、この構成・SDK で条件（arch など）なしに効く
        if True in modes:
            effective, undecided = resolve(own, shared, ORIENTATION_SETTING, [configuration], variant)
            for (key, value), _ in effective.items():
                # 変数（$(NAME)）はその構成・SDK で展開してから判定する（GENERATE_INFOPLIST_FILE と同じ）。
                # 展開できない値は縦だけと見なさない
                expanded = expand_value(value, own, shared, configuration, variant, setting=ORIENTATION_SETTING,
                                        from_target=key in own and as_text(own[key]) == value)
                if expanded is None:
                    wrong.setdefault(f"{key} = {value}（展開できない）", []).append(build)
                elif expanded not in ORIENTATION_SETTING_VALUES:
                    wrong.setdefault(f"{key} = {value}", []).append(build)
            settings_cover = bool(effective) and not undecided
        if (False in modes and not covered) or (True in modes and not (settings_cover or covered)):
            missing.append(build)
    if missing:
        errors.append(
            f"[BD-UI-10] {target}: 縦だけの向きの指定がない（構成 {'・'.join(missing)}。Info.plist を生成する構成では "
            f"{ORIENTATION_SETTING} を UIInterfaceOrientationPortrait にするか、使われる plist に置く。生成しない構成では、"
            "使われる plist の UISupportedInterfaceOrientations~iphone を [UIInterfaceOrientationPortrait] にする）"
        )
    for label, used in wrong.items():
        errors.append(f"[BD-UI-10] {target}: {label} が縦だけでない（構成 {'・'.join(used)}。必要な値: [UIInterfaceOrientationPortrait]）")


# 値の中の変数を、検査する構成・SDK のすべてについて展開した結果を返す関数。展開できない組の値は None
Expander = Callable[[str], List[Optional[str]]]
VARIABLE = re.compile(r"\$[({]")


def target_expander(
    own: dict, shared: dict, configurations: List[str], key: Optional[str] = None, from_target: bool = False
) -> Expander:
    """そのターゲットの各構成・各 SDK（SDKS）のビルド設定で値を展開する関数を返す。

    key を指定した場合（ビルド設定の値を展開する場合）は、その書き方の条件（config・sdk）に合う構成・SDK だけで展開し
    （Debug だけの書き方を Release の値で判定しない）、$(inherited) をその設定の継承元で展開する。
    arch の条件も同じく、合う arch だけで展開する（arm64 だけの書き方が参照する arm64 だけの設定を、展開できないとしない）。
    """
    condition = conditions(key) if key is not None else {}
    setting = key.split("[", 1)[0] if key is not None else None

    def applies(configuration: str, variant: Variant) -> bool:
        if "config" in condition and not fnmatch.fnmatchcase(configuration, condition["config"]):
            return False
        # arch の条件の書き方があれば build_variants・possible_variants は arch を持つ組を返す（arch が None の組には arch の条件がない）
        if "arch" in condition and variant.arch is not None and not fnmatch.fnmatchcase(variant.arch, condition["arch"]):
            return False
        return "sdk" not in condition or sdk_matches(variant.sdk, condition["sdk"], str(key))

    # ビルドする arch に当たる組がない書き方（armv7 だけなど）は、当たりうる組で展開する（展開した値がないものを合格にしない）
    builds = ([(c, v) for c in configurations for v in build_variants(own, shared, c) if applies(c, v)]
              or [(c, v) for c in configurations for v in possible_variants(own, shared) if applies(c, v)])
    return lambda text: [expand_value(text, own, shared, c, v, setting=setting, from_target=from_target) for c, v in builds]


def combined_expander(expanders: List[Expander]) -> Expander:
    """複数のターゲットの展開の結果をすべて合わせる（どのターゲットのビルドでも違反しないことを求める）。"""
    return lambda text: [value for expand in expanders for value in expand(text)]


# Info.plist の鍵の、iPhone のビルドで使われうる修飾子（<鍵>-<OS>・<鍵>~<機種>・<鍵>-<OS>~<機種>）。
# 修飾子つきの鍵も元の鍵と同じ規則で判定する（UIFileSharingEnabled~iphone で禁止を迂回させない）
IPHONE_KEY_MODIFIERS = re.compile(r"(?P<base>[A-Za-z]\w*?)(?:-iphoneos)?(?:~iphone)?")


def unmodified_key(key: str) -> str:
    """iPhone の修飾子を除いた鍵の名前（修飾子がない・iPhone 以外の修飾子なら、そのままの名前）。"""
    found = IPHONE_KEY_MODIFIERS.fullmatch(key)
    return found.group("base") if found else key


def check_enabled(source: str, key: str, value: object, errors: List[str], expand: Optional[Expander],
                  rule: Optional[str] = None) -> None:
    """有効にすると違反になる鍵の値を判定する。変数を含む値は、構成・SDK ごとに展開してから判定する。

    展開できない値（どのターゲットのビルド設定で展開するか決まらない plist の値を含む）は、無効とみなさず違反として報告する。
    """
    reason = FORBIDDEN_WHEN_ENABLED[rule if rule is not None else key]
    if not (isinstance(value, str) and VARIABLE.search(value)):
        if enabled(value):
            errors.append(f"[{reason}] {source}: {key} を有効にしない")
        return
    values = expand(value) if expand is not None else [None]
    if any(item is None for item in values):
        errors.append(f"[{reason}] {source}: {key} の値を展開できない（{value}。リテラルの NO にするか、鍵を置かない）")
    if any(item is not None and enabled(item) for item in values):
        errors.append(f"[{reason}] {source}: {key} を有効にしない（{value} が有効な値に展開される）")


def check_plist_entry(source: str, key: str, value: object, errors: List[str], expand: Optional[Expander] = None,
                      visited: Optional[Set[int]] = None) -> None:
    """Info.plist の 1 項目を調べる。入れ子の辞書と配列もたどる。

    expand は値の変数を展開する関数（その plist・ビルド設定を使うターゲットのもの）。None なら変数は判定できないものとする。
    visited は、1 つの plist（同じ展開の文脈）のたどりの中で中を調べた辞書・配列の id。バイナリ形式の plist は同じ辞書・配列を
    複数の場所から参照でき、参照のたびに下りると段数に対して指数の手間になるので、同じものの中は 1 回だけ調べる。
    None なら、この呼び出しから新しいたどりを始める（展開の文脈が違う検査の間で使い回さない）。
    """
    # iPhone の修飾子（~iphone・-iphoneos・-iphoneos~iphone）を除いた鍵で規則を判定する（修飾子つきの鍵で規則を迂回させない）。
    # 報告には元の鍵を使う。接頭辞の規則（FORBIDDEN_PLIST_KEY_PREFIXES）は元の鍵のままで当たる
    base = unmodified_key(key)
    if base in FORBIDDEN_PLIST_KEYS:
        errors.append(f"[{FORBIDDEN_PLIST_KEYS[base]}] {source}: {key} を設定しない")
    for prefix, reason in FORBIDDEN_PLIST_KEY_PREFIXES.items():
        if key.startswith(prefix):
            errors.append(f"[{reason}] {source}: {key} を設定しない")
    if base in FORBIDDEN_WHEN_ENABLED:
        check_enabled(source, key, value, errors, expand, base)
    if re.fullmatch(r"NS\w*UsageDescription", base) and base not in ALLOWED_USAGE_DESCRIPTIONS:
        errors.append(f"[TST-SEC-03] {source}: 使わない権限の文言 {key} を置かない")
    if base == "UIBackgroundModes":
        modes = value if isinstance(value, list) else str(value).split()
        for mode in modes:
            if str(mode) not in ALLOWED_BACKGROUND_MODES:
                errors.append(f"[FR-REC-05] {source}: UIBackgroundModes に {mode} を入れない")
    if base == "NSExtensionPointIdentifier" and str(value) not in ALLOWED_EXTENSION_POINTS:
        errors.append(f"[SEC-PLT-04] {source}: 拡張 {value} を置かない")
    if visited is None:
        visited = set()
    # 鍵ごとの規則（上）は参照のたびに判定し、中の項目は同じ辞書・配列について 1 回だけ調べる。
    # 配列の中の配列（[[{…}]] など）も、どの深さでもたどり、その中の辞書の鍵を調べる（入れ子の配列で禁止の鍵を隠させない）
    pending: List[object] = [value] if isinstance(value, (dict, list)) else []
    while pending:
        container = pending.pop()
        if id(container) in visited:
            continue
        visited.add(id(container))
        if isinstance(container, dict):
            for child_key, child_value in container.items():
                check_plist_entry(source, str(child_key), child_value, errors, expand, visited)
        elif isinstance(container, list):
            # 書かれた順に調べる（報告の順を決定的にする）
            pending.extend(reversed([item for item in container if isinstance(item, (dict, list))]))


def check_build_settings(
    target: str, settings: dict, errors: List[str], expanders: Optional[Callable[[str], Expander]] = None
) -> None:
    """ビルド設定を調べる。expanders は書き方（条件つきの鍵）ごとに、その条件に合う構成・SDK で値を展開する関数を返す。"""
    for key, value in settings.items():
        name = key.split("[", 1)[0]
        if name in FORBIDDEN_BUILD_SETTINGS:
            errors.append(f"[{FORBIDDEN_BUILD_SETTINGS[name]}] {target}: {key} を削除する")
        if name.startswith("INFOPLIST_KEY_"):
            expand = expanders(key) if expanders is not None else None
            check_plist_entry(f"{target} の {key}", name[len("INFOPLIST_KEY_"):], value, errors, expand)


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


def resolved(path: Path) -> Path:
    """リンクを解決した絶対パス。解決できない（循環するリンクなど）場合は、読めないものとして扱う。"""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as error:  # Python 3.9〜3.12 は循環するリンクで RuntimeError を送出する
        raise Unreadable(f"{path}: パスを解決できない: {error!r}") from error


MAX_PLIST_DEPTH = 100  # plist の入れ子の段数の上限


class UniqueKeys(dict):
    """重複した鍵を拒否する辞書（plistlib は XML・バイナリ形式のどちらも、既定で後の値を黙って採用し、前の値を検査から隠す）。"""

    def __setitem__(self, key: object, value: object) -> None:
        if key in self:
            raise ValueError(f"重複した鍵 {key!r}")
        super().__setitem__(key, value)


def load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle, dict_type=UniqueKeys)
    # 壊れた XML は ExpatError、形の違う日付（<date>）は AttributeError になる（plistlib が照合の失敗を確かめない）
    # 重複した鍵は ValueError（UniqueKeys）
    # バイナリ形式の深い入れ子は、plistlib の読み込みが RecursionError になる
    # 辞書の外の <key>（最上位の <key> など）は、plistlib が空の入れ子を参照して IndexError になる
    except (OSError, plistlib.InvalidFileException, ValueError, ExpatError, AttributeError, RecursionError, IndexError) as error:
        raise Unreadable(f"{path}: {error!r}") from error
    if not isinstance(data, dict):
        raise Unreadable(f"{path}: 最上位が辞書でない")
    # 検査は入れ子をたどるので、深すぎるものと循環するもの（バイナリ形式では作れる）を読めないものとする。深さごとに、
    # 同じオブジェクトを 1 つにまとめて下りる（循環していれば、いつまでも空にならない）
    level: List[object] = [data]
    for _ in range(MAX_PLIST_DEPTH):
        children = [child for item in level for child in (item.values() if isinstance(item, dict) else item)]
        level = list({id(child): child for child in children if isinstance(child, (dict, list))}.values())
        if not level:
            return data
    raise Unreadable(f"{path}: 入れ子が深すぎるか循環している（{MAX_PLIST_DEPTH} 段を超える）")


def declared_api_reasons(path: Path, manifest: dict) -> Dict[str, List[str]]:
    """マニフェストの申告を「API の種類 → 理由コードの一覧」にする。形式が違うものは合格にしない。"""
    entries = manifest.get("NSPrivacyAccessedAPITypes", [])
    if not isinstance(entries, list):
        raise Unreadable(f"{path}: NSPrivacyAccessedAPITypes が配列でない")
    declared: Dict[str, List[str]] = {}
    for entry in entries:
        category = entry.get("NSPrivacyAccessedAPIType") if isinstance(entry, dict) else None
        reasons = entry.get("NSPrivacyAccessedAPITypeReasons") if isinstance(entry, dict) else None
        if not isinstance(category, str) or not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            raise Unreadable(f"{path}: NSPrivacyAccessedAPITypes の要素の形式が違う")
        declared.setdefault(category, []).extend(reasons)
    return declared


def check_privacy_manifest(source: str, path: Path, complete: bool, errors: List[str]) -> None:
    """プライバシーマニフェストの申告を調べる。complete（アプリのマニフェスト）なら、決めた申告がすべてあることも確かめる。"""
    manifest = load_plist(path)
    if manifest.get("NSPrivacyTracking") is not False:
        errors.append(f"[SEC-PRV-03] {source}: NSPrivacyTracking を false にする")
    for key in PRIVACY_EMPTY_KEYS:
        if manifest.get(key) != []:
            errors.append(f"[SEC-PRV-03] {source}: {key} を空の配列にする")
    declared = declared_api_reasons(path, manifest)
    # 申告した API の種類には、理由コードが 1 つ以上要る（空の配列では申告にならない）。拡張のマニフェスト（complete が偽）も
    # 同じ。種類ごとに見るので、同じ種類の要素が複数あれば、どれか 1 つでも空なら違反にする
    for entry in manifest.get("NSPrivacyAccessedAPITypes", []):
        if not entry["NSPrivacyAccessedAPITypeReasons"]:
            errors.append(f"[SEC-PRV-03] {source}: {entry['NSPrivacyAccessedAPIType']} の理由コードが空（1 つ以上申告する）")
    for category, reasons in declared.items():
        allowed = PRIVACY_ACCESSED_API_REASONS.get(category)
        if allowed is None:
            errors.append(f"[SEC-PRV-03] {source}: 決めていない API の種類 {category} を申告しない（使うなら設計書と検査を同じ作業で直す）")
            continue
        for reason in reasons:
            if reason not in allowed:
                errors.append(f"[SEC-PRV-03] {source}: {category} の理由コード {reason} は、決めたもの（{'・'.join(allowed)}）と違う")
    if complete:
        for category, allowed in PRIVACY_ACCESSED_API_REASONS.items():
            missing = [reason for reason in allowed if reason not in declared.get(category, [])]
            if missing:
                errors.append(f"[SEC-PRV-03] {source}: {category} の申告（理由コード {'・'.join(missing)}）がない")



def tracked_files(root: Path) -> Set[Path]:
    """root の下の、HEAD のコミットで追跡した通常のファイルのパス。取得できなければ、読めないものとして扱う。"""
    # 外から引き継いだリポジトリを指す変数（GIT_DIR など）を外し、置き換えのオブジェクトを使わない
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", "HEAD"], env=env, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise Unreadable(f"{root}: 追跡したファイルの一覧を取得できない: {error}") from error
    entries = [entry.split(b"\t", 1) for entry in listed.stdout.split(b"\0") if entry]
    return {root / os.fsdecode(path) for attributes, path in entries if attributes.startswith(b"100")}


def require_tracked(source: str, path: Path, root: Path, tracked: Optional[Set[Path]]) -> None:
    """tracked を渡した場合（--tracked-only）に、リンクを解決した path が root の中の追跡したファイルでなければ Unreadable とする。"""
    if tracked is None:
        return
    if not path.is_relative_to(root):
        raise Unreadable(f"{source}（{path}）が起点の外にある")
    if path not in tracked:
        raise Unreadable(f"{source}（{path}）を検査するコミットで追跡していない")


def referenced_files(
    project_path: Path, target: str, own: dict, shared: dict, configuration: str, setting: str,
    variant: Optional[Variant] = None, root: Optional[Path] = None, tracked: Optional[Set[Path]] = None
) -> Tuple[List[Path], bool]:
    """ビルド設定（INFOPLIST_FILE・CODE_SIGN_ENTITLEMENTS）がその構成で指すファイルと、
    参照先が条件（arch など）なしには決まらないか（resolve の未確定、または空の書き方が効きうる）を返す。

    ファイル名や拡張子にかかわらず検査するために使う。
    相対パスは SRCROOT（.xcodeproj のあるディレクトリ）から解決する。展開できない変数を含む値は合格にしない。
    tracked を渡した場合（--tracked-only）は、リンクを解決した参照先が root の中の追跡したファイルでなければ Unreadable とする
    （check が最初に全部の構成・SDK の参照先を確かめるので、ほかの呼び出しは渡さなくてよい）。
    """
    # 絶対パスにしてから SRCROOT を決める（相対の --project では、展開した $(SRCROOT)/… が相対パスのまま残り、
    # さらに source_root から解決されて二重になる）
    parent = resolved(project_path).parent
    source_root = parent.parent if parent.suffix == ".xcodeproj" else parent
    known = {"SRCROOT": str(source_root), "PROJECT_DIR": str(source_root), "TARGET_NAME": target}
    effective, undecided = resolve(own, shared, setting, [configuration], variant)
    paths: List[Path] = []
    explicit_empty = False  # 空の書き方（INFOPLIST_FILE[arch=arm64] = "" など）が効きうる
    for (key, value), _ in effective.items():
        text = value.strip()
        if not text:
            # 空の書き方は、その条件のビルドでは参照先がないことを表す。読み飛ばして別の書き方の参照先で補わず、
            # 参照先が条件なしに決まらないものとして呼び出し側に伝える
            explicit_empty = True
            continue

        def expand(found: "re.Match[str]") -> str:
            name = found.group(1) or found.group(2)
            if name not in known:
                raise Unreadable(f"{project_path}: {key} = {value} の変数 {name} を展開できない")
            return known[name]

        expanded = re.sub(r"\$\((\w+)\)|\$\{(\w+)\}", expand, text)
        path = Path(expanded)
        target_path = resolved(path if path.is_absolute() else source_root / path)
        if root is not None:
            require_tracked(f"{project_path}: {key} = {value} の参照先", target_path, root, tracked)
        paths.append(target_path)
    return paths, bool(undecided) or explicit_empty


def discovered_files(root: Path, suffixes: Tuple[str, ...], tracked: Optional[Set[Path]]) -> List[Path]:
    """名前で見つけたファイルの、リンクを解決したパス。tracked を渡した場合は、参照先と同じく追跡したファイルに限る。"""
    paths: List[Path] = []
    for path in find_files(root, suffixes):
        target = resolved(path)
        require_tracked(f"{path} の実体", target, root, tracked)
        paths.append(target)
    return paths


def check(project_path: Path, root: Path, tracked_only: bool = False) -> Tuple[List[str], List[str]]:
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
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("build-settings"), dict):
            raise Unreadable(f"{project_path}: ターゲットの形式が違う")
    # プロジェクトの設定はすべてのターゲットのビルドに効くので、すべてのターゲットの構成・SDK で展開する
    check_build_settings("プロジェクト", shared, errors, lambda key: combined_expander([
        target_expander(target["build-settings"], shared, configurations, key, False) for target in targets]))
    # INFOPLIST_FILE の参照先 → それを参照する構成・SDK の展開（参照するターゲットの、その構成・SDK のビルド設定だけで
    # 値の変数を展開する。参照しない構成の値で判定しない）
    referenced: Dict[Path, List[Expander]] = {}
    unbuilt: Dict[Path, List[Expander]] = {}  # ビルドしない arch の参照先（上と同じ形）
    entitlements: List[Path] = []
    application_manifests: List[Path] = []
    tracked = tracked_files(root) if tracked_only else None
    for target in targets:
        name = f"ターゲット {target.get('name', '?')}"
        own = target["build-settings"]
        target_name = str(target.get("name", ""))
        for configuration in configurations:
            built = build_variants(own, shared, configuration)
            for variant in [*built, *(found for found in possible_variants(own, shared) if found not in built)]:
                for path in referenced_files(project_path, target_name, own, shared, configuration, "INFOPLIST_FILE", variant,
                                             root, tracked)[0]:
                    # 値の変数は、ビルドする組で展開する（ビルドしない arch だけの参照先は、その当たりうる組で展開する）
                    (referenced if variant in built else unbuilt).setdefault(path, []).append(
                        # ビルドが plist の変数を展開しない・展開するかを決められない構成では、展開後の値で判定しない
                        # （None = 判定できない。check_plist_values・check_plist_orientation と同じ）
                        lambda text, own=own, c=configuration, v=variant: [
                            expand_value(text, own, shared, c, v) if plist_expansion(own, shared, c, v) is True else None])
            entitlements.extend(
                referenced_files(project_path, target_name, own, shared, configuration, "CODE_SIGN_ENTITLEMENTS",
                                 None, root, tracked)[0])
        check_required(name, own, shared, configurations, REQUIRED_ALL_TARGETS, errors)
        if target.get("product-type") == "application":
            check_plist_values(project_path, name, target_name, own, shared, configurations, root, errors)
            check_plist_orientation(project_path, name, target_name, own, shared, configurations, root, errors)
            # プライバシーマニフェスト（SEC-PRV-03）は <ターゲット名>/PrivacyInfo.xcprivacy に置く
            parent = resolved(project_path).parent
            source_root = parent.parent if parent.suffix == ".xcodeproj" else parent
            manifest = resolved(source_root / target_name / PRIVACY_MANIFEST_NAME)
            if manifest.is_file():
                require_tracked(f"{name} のプライバシーマニフェスト", manifest, root, tracked)
                application_manifests.append(manifest)
            else:
                errors.append(f"[SEC-PRV-03] {name}: {target_name or '?'}/{PRIVACY_MANIFEST_NAME} がない")
        check_build_settings(name, own, errors,
                             lambda key, own=own: target_expander(own, shared, configurations, key, True))
        for key, value in variants(own, "PRODUCT_BUNDLE_IDENTIFIER"):
            if "devplaceholder" in value:
                warnings.append(f"[CON-09] {name}: {key} が仮の値のまま。最初の実録音の前に確定する")
    for path, expanders in unbuilt.items():
        referenced.setdefault(path, expanders)
    found = discovered_files(root, ("Info.plist",), tracked)
    # INFOPLIST_FILE が指すファイルも読む。ないファイル・読めないファイルは load_plist が Unreadable にする
    for path in found + [path for path in referenced if path not in found]:
        source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        # どのターゲットも参照しない plist の値の変数は、展開するビルド設定が決まらないので判定できないものとする
        expand = combined_expander(referenced[path]) if path in referenced else None
        visited: Set[int] = set()  # この plist のたどりだけで使う（ほかの plist・ビルド設定の検査とは分ける）
        for key, value in load_plist(path).items():
            check_plist_entry(source, str(key), value, errors, expand, visited)
    # プライバシーマニフェストは、アプリのもの以外（拡張など）も調べる。アプリのものだけ、決めた申告がすべてあることを求める
    manifests = discovered_files(root, (".xcprivacy",), tracked)
    for path in manifests + [path for path in application_manifests if path not in manifests]:
        source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        check_privacy_manifest(source, path, path in application_manifests, errors)
    # CODE_SIGN_ENTITLEMENTS が指すファイルも、拡張子にかかわらず読む。ないファイル・読めないファイルは Unreadable
    scanned = discovered_files(root, (".entitlements",), tracked)
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
    parser = argparse.ArgumentParser(description="プロジェクト設定・Info.plist・entitlements・プライバシーマニフェストの検査")
    parser.add_argument("--project", type=Path, default=ROOT / "MinutesMobile.xcodeproj" / "project.xcproj")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tracked-only", action="store_true")
    arguments = parser.parse_args()
    try:
        errors, warnings = check(arguments.project, arguments.root.resolve(), arguments.tracked_only)
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
    print("OK: プロジェクト設定・Info.plist・entitlements・プライバシーマニフェストに違反なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
