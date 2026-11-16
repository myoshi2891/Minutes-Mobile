#!/usr/bin/python3
"""アプリに組み込む依存の検査（TST-CI-05、SEC-SUP-01・02）。

Package.resolved・Package.swift・project.xcproj に現れる外部のパッケージが、
許可リスト（tools/dependency-allowlist.json）にあることを確かめる。MVP の許可リストは空（依存 0 件）。

使い方: scripts/check-dependencies.py [--root <探す起点>] [--allowlist <許可リスト>]
終了コード: 0 = 問題なし / 1 = 許可リスト外の依存あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

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


def check(root: Path, allowlist: Path) -> List[str]:
    by_pin, by_url = load_allowlist(allowlist)
    errors: List[str] = []
    for path in find_files(root, "Package.resolved"):
        source = path.relative_to(root)
        for pin in resolved_pins(path):
            identity = str(pin.get("identity") or pin.get("package") or "").lower()
            state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            version = state.get("version")
            if not identity:
                raise Unreadable(f"{path}: 名前のない依存がある")
            if not isinstance(version, str) or not version:
                errors.append(f"{source}: {identity} は版で固定されていない（ブランチやコミットの指定は認めない）")
            elif (identity, version) not in by_pin:
                errors.append(f"{source}: {identity} {version} は許可リストにない")
    for path in find_files(root, "Package.swift"):
        try:
            manifest = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise Unreadable(f"{path}: {error}") from error
        for url in re.findall(r'\burl:\s*"([^"]+)"', manifest):
            if normalize(url) not in by_url:
                errors.append(f"{path.relative_to(root)}: {url} は許可リストにない")
        for identifier in re.findall(r'\.package\s*\(\s*id:\s*"([^"]+)"', manifest):
            errors.append(f"{path.relative_to(root)}: レジストリの依存 {identifier} は認めない")
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
