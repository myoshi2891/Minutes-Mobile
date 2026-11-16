#!/usr/bin/python3
"""アプリに組み込む依存の検査（TST-CI-05、SEC-SUP-01・02）。

Package.resolved・Package.swift・project.xcproj に現れる外部のパッケージが、
許可リスト（tools/dependency-allowlist.json）にあることを確かめる。MVP の許可リストは空（依存 0 件）。
Package.swift の依存は、版の指定を同じディレクトリの Package.resolved の確定版と照合する。

使い方: scripts/check-dependencies.py [--root <探す起点>] [--allowlist <許可リスト>]
終了コード: 0 = 問題なし / 1 = 許可リスト外の依存あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from xcproj import Unreadable, load_project

ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRECTORIES = {".git", ".tools", ".build", "build", "DerivedData", "xcuserdata"}
PACKAGE_FIELDS = ("identity", "url", "version", "license", "approvedOn", "ledger")
REMOTE_URL = re.compile(r"^(https?|ssh|git)://|^git@")


def normalize(url: str) -> str:
    """同じリポジトリを指す書き方の違い（末尾の .git・スラッシュ・大文字小文字）をそろえる。"""
    text = url.strip().lower().rstrip("/")
    return text[:-4] if text.endswith(".git") else text


def load_allowlist(path: Path) -> Tuple[Dict[Tuple[str, str], dict], Dict[str, dict]]:
    """許可リストを読み、(identity, 版) → 項目 と URL → 項目 を返す。形式が違えば読めないものとして扱う。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Unreadable(f"{path}: {error}") from error
    if not isinstance(data, dict) or data.get("formatVersion") != 1:
        raise Unreadable(f"{path}: formatVersion が 1 でない")
    licenses = data.get("allowedLicenses")
    packages = data.get("approvedPackages")
    if not isinstance(licenses, list) or not all(isinstance(item, str) for item in licenses):
        raise Unreadable(f"{path}: allowedLicenses の形式が違う")
    if not isinstance(packages, list):
        raise Unreadable(f"{path}: approvedPackages の形式が違う")
    by_pin: Dict[Tuple[str, str], dict] = {}
    by_url: Dict[str, dict] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise Unreadable(f"{path}: approvedPackages の項目が辞書でない")
        for field in PACKAGE_FIELDS:
            if not isinstance(package.get(field), str) or not package[field].strip():
                raise Unreadable(f"{path}: 承認済みの依存に {field} がない")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", package["approvedOn"]):
            raise Unreadable(f"{path}: {package['identity']} の approvedOn が YYYY-MM-DD でない")
        # 許可するライセンス以外は、オーナーの決定（UD-nn）の記録が要る
        decision = package.get("ownerDecision", "")
        if package["license"] not in licenses and not re.fullmatch(r"UD-\d+", str(decision)):
            raise Unreadable(
                f"{path}: {package['identity']} のライセンス {package['license']} は許可の対象外。"
                "ownerDecision にオーナーの決定（UD-nn）を記録する"
            )
        by_pin[(package["identity"].lower(), package["version"])] = package
        by_url[normalize(package["url"])] = package
    return by_pin, by_url


def find_files(root: Path, name: str) -> Iterator[Path]:
    for path in sorted(root.rglob(name)):
        if path.is_file() and not SKIPPED_DIRECTORIES.intersection(path.relative_to(root).parts):
            yield path


def resolved_pins(path: Path) -> List[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Unreadable(f"{path}: {error}") from error
    pins = data.get("pins") if isinstance(data, dict) else None
    if pins is None and isinstance(data, dict) and isinstance(data.get("object"), dict):
        pins = data["object"].get("pins")  # 古い形式（version 1）
    if not isinstance(pins, list) or not all(isinstance(pin, dict) for pin in pins):
        raise Unreadable(f"{path}: pins の形式が違う")
    return pins


def strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from strings(item)


def call_arguments(text: str, start: int) -> str:
    """start の位置の「(」に対応する「)」までの引数を返す。文字列リテラルの中の括弧は数えない。閉じていなければ末尾まで。"""
    depth = 0
    index = start
    in_string = False
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
        index += 1
    return text[start + 1:]


# 版の指定（文字列リテラルだけ）。ブランチ・コミットの指定は版で固定していないものとして認めない
VERSION = r'"(\d+\.\d+\.\d+)"'
UNPINNED = re.compile(r"\b(branch|revision)\s*:|\.(branch|revision)\s*\(")
REQUIREMENTS = (
    ("exact", re.compile(rf"(?:\bexact:\s*|\.exact\(\s*){VERSION}")),
    ("minor", re.compile(rf"\.upToNextMinor\(\s*from:\s*{VERSION}")),
    ("major", re.compile(rf"(?:\.upToNextMajor\(\s*from:\s*|(?<![.\w])from:\s*){VERSION}")),
    ("closed", re.compile(rf"{VERSION}\s*\.\.\.\s*{VERSION}")),
    ("half-open", re.compile(rf"{VERSION}\s*\.\.<\s*{VERSION}")),
)
Version = Tuple[int, int, int]


def parse_version(text: object) -> Optional[Version]:
    found = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text) if isinstance(text, str) else None
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None


def requirement(arguments: str) -> Optional[Tuple[Version, Version, bool]]:
    """.package の版の指定を（下限, 上限, 上限を含むか）で返す。判定できなければ None。"""
    for kind, pattern in REQUIREMENTS:
        found = pattern.search(arguments)
        if not found:
            continue
        low = parse_version(found.group(1))
        assert low is not None
        if kind == "exact":
            return low, low, True
        if kind == "minor":
            return low, (low[0], low[1] + 1, 0), False
        if kind == "major":
            return low, (low[0] + 1, 0, 0), False
        high = parse_version(found.group(2))
        assert high is not None
        return low, high, kind == "closed"
    return None


def satisfies(version: Version, rule: Tuple[Version, Version, bool]) -> bool:
    low, high, inclusive = rule
    return low <= version and (version <= high if inclusive else version < high)


# 取得元を持つ宣言（Package の依存と、バイナリのターゲット）
SOURCE_DECLARATION = re.compile(r"\.(package|binaryTarget)\s*\(")
# 文字列リテラルだけの引数（連結・補間・変数を含まない）。引数の終わりは「,」か末尾
LITERAL_URL = re.compile(r'\burl:\s*"[^"\\]*"\s*(?:,|$)')
LITERAL_PATH = re.compile(r'\bpath:\s*"[^"\\]*"\s*(?:,|$)')


def version_errors(arguments: str, summary: str, approved: Set[str], pinned: Optional[List[object]]) -> List[str]:
    """.package(url:) の版の指定を、承認した版と、同じディレクトリの Package.resolved の確定版で照合する。"""
    if UNPINNED.search(arguments):
        return [f"版で固定されていない（ブランチやコミットの指定は認めない）: .package({summary})"]
    rule = requirement(arguments)
    if rule is None:
        return [f"版の指定を判定できない（版を文字列リテラルで書く）: .package({summary})"]
    if not pinned:
        return [f"確定版がない（同じディレクトリの Package.resolved に記録する）: .package({summary})"]
    errors: List[str] = []
    for version in pinned:
        parsed = parse_version(version)
        if parsed is None or not satisfies(parsed, rule):
            errors.append(f"宣言の版の指定と確定版 {version} が合わない: .package({summary})")
        elif version not in approved:
            errors.append(f"確定版 {version} は許可リストにない: .package({summary})")
    return errors


def manifest_errors(
    manifest: str, by_url: Dict[str, dict], approved: Optional[Dict[str, Set[str]]] = None,
    pins: Optional[Dict[str, List[object]]] = None
) -> List[str]:
    """Package.swift の取得元と版を調べる。取得元・版を文字列リテラルから判定できない宣言は合格にしない。

    approved は URL → 承認した版、pins は URL → 同じディレクトリの Package.resolved の確定版。
    """
    approved = approved or {}
    pins = pins or {}
    errors: List[str] = []
    for url in re.findall(r'\burl:\s*"([^"]+)"', manifest):
        if normalize(url) not in by_url:
            errors.append(f"{url} は許可リストにない")
    for match in SOURCE_DECLARATION.finditer(manifest):
        kind = match.group(1)
        arguments = call_arguments(manifest, match.end() - 1)
        summary = " ".join(arguments.split())[:80]
        if kind == "package" and re.match(r"\s*id:", arguments):
            errors.append(f"レジストリの依存は認めない: .package({summary})")
        elif re.search(r"\burl:", arguments):
            if not LITERAL_URL.search(arguments):
                errors.append(f"取得元の URL を判定できない（文字列リテラルで書く）: .{kind}({summary})")
            elif kind == "package":
                url = normalize(re.findall(r'\burl:\s*"([^"\\]*)"', arguments)[0])
                if url in by_url:  # 許可リストにない URL は上で報告済み
                    errors.extend(version_errors(arguments, summary, approved.get(url, set()), pins.get(url)))
        elif not LITERAL_PATH.search(arguments):
            errors.append(f"取得元を判定できない（url: か path: を文字列リテラルで書く）: .{kind}({summary})")
    return errors


def check(root: Path, allowlist: Path) -> List[str]:
    by_pin, by_url = load_allowlist(allowlist)
    # by_url は URL ごとに 1 項目しか持たないので、版の照合には by_pin から作った URL → 承認した版を使う
    approved_versions: Dict[str, Set[str]] = {}
    for package in by_pin.values():
        approved_versions.setdefault(normalize(package["url"]), set()).add(package["version"])
    # ディレクトリ → URL → 確定版（Package.swift と同じディレクトリの Package.resolved とだけ照合する）
    pins_by_directory: Dict[Path, Dict[str, List[object]]] = {}
    errors: List[str] = []
    for path in find_files(root, "Package.resolved"):
        source = path.relative_to(root)
        for pin in resolved_pins(path):
            pin_location = pin.get("location") or pin.get("repositoryURL")
            pin_state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            if isinstance(pin_location, str):
                pins_by_directory.setdefault(path.parent, {}).setdefault(normalize(pin_location), []).append(
                    pin_state.get("version"))
            identity = str(pin.get("identity") or pin.get("package") or "").lower()
            state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            version = state.get("version")
            if not identity:
                raise Unreadable(f"{path}: 名前のない依存がある")
            if not isinstance(version, str) or not version:
                errors.append(f"{source}: {identity} は版で固定されていない（ブランチやコミットの指定は認めない）")
            elif (identity, version) not in by_pin:
                errors.append(f"{source}: {identity} {version} は許可リストにない")
            else:
                # identity と版が同じでも、取得元が承認したものと違えば認めない
                location = pin.get("location") or pin.get("repositoryURL")  # repositoryURL は古い形式（version 1）
                approved = by_pin[(identity, version)]["url"]
                if not isinstance(location, str) or normalize(location) != normalize(approved):
                    errors.append(f"{source}: {identity} {version} の取得元 {location} が許可リスト（{approved}）と違う")
    for path in find_files(root, "Package.swift"):
        try:
            manifest = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise Unreadable(f"{path}: {error}") from error
        pins = pins_by_directory.get(path.parent, {})
        errors.extend(f"{path.relative_to(root)}: {message}" for message in manifest_errors(manifest, by_url, approved_versions, pins))
    for path in find_files(root, "project.xcproj"):
        for text in strings(load_project(path)):
            if REMOTE_URL.search(text) and normalize(text) not in by_url:
                errors.append(f"{path.relative_to(root)}: {text} は許可リストにない")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="アプリに組み込む依存の検査")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--allowlist", type=Path, default=ROOT / "tools" / "dependency-allowlist.json")
    arguments = parser.parse_args()
    try:
        errors = check(arguments.root.resolve(), arguments.allowlist)
    except Unreadable as error:
        print(f"NG: 依存を読めない: {error}", file=sys.stderr)
        return 2
    for message in errors:
        print(f"NG: {message}")
    if errors:
        print(f"NG: 許可リスト外の依存 {len(errors)} 件。追加は dependencies-and-costs.md §4 の手順による")
        return 1
    print("OK: 外部の依存は許可リストの範囲内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
