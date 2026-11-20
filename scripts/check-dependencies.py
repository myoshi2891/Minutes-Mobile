#!/usr/bin/python3
"""アプリに組み込む依存の検査（TST-CI-05、SEC-SUP-01・02）。

Package.resolved・Package.swift・project.xcproj に現れる外部のパッケージが、
許可リスト（tools/dependency-allowlist.json）にあることを確かめる。MVP の許可リストは空（依存 0 件）。
Package.swift の依存は、版の指定を同じディレクトリの Package.resolved の確定版と照合する。

使い方: scripts/check-dependencies.py [--root <探す起点>] [--allowlist <許可リスト>]
終了コード: 0 = 問題なし / 1 = 許可リスト外の依存あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from xcproj import Unreadable, load_project

ROOT = Path(__file__).resolve().parent.parent
# ビルドの成果物と個人の設定は全階層で除外する。.git・.tools・build はリポジトリ直下だけを除外する
# （下位の build という名前のディレクトリにある manifest・設定も検査する）
SKIPPED_ANYWHERE = {".build", "DerivedData", "xcuserdata"}
SKIPPED_AT_ROOT = {".git", ".tools", "build"}
PACKAGE_FIELDS = ("identity", "url", "version", "license", "approvedOn", "ledger")
# ローカルのバイナリ（.binaryTarget(path:)）の承認済みの項目は、url の代わりに path（リポジトリの起点からの相対パス）と、
# 承認したときの内容のハッシュ sha256（content_digest の値）を持つ
BINARY_FIELDS = ("identity", "path", "sha256", "version", "license", "approvedOn", "ledger")
SHA256 = re.compile(r"[0-9a-f]{64}")
REMOTE_URL = re.compile(r"^(https?|ssh|git)://|^git@")


def normalize(url: str) -> str:
    """同じリポジトリを指す書き方の違い（末尾の .git・スラッシュ、scheme とホストの大文字小文字）をそろえる。

    パスの大文字小文字は区別する（パスだけが違う取得元を同じものと見なさない）。
    """
    text = url.strip().rstrip("/")
    found = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/]*)(.*)$", text)
    if found:
        text = found.group(1).lower() + found.group(2).lower() + found.group(3)
    else:
        scp = re.match(r"^([^@/:]+@)([^:/]+)(:.*)$", text)  # git@host:owner/repo の形
        if scp:
            text = scp.group(1) + scp.group(2).lower() + scp.group(3)
    return text[:-4] if text.endswith(".git") else text


def normalize_path(path: str) -> str:
    """リポジトリの起点からの相対パスを、区切りと「.」「..」をそろえた形にする。

    前後の空白は除かない（除くと、宣言が指すものと別のファイルを照合しうる）。空白を含むパスは manifest_errors が拒否する。
    """
    return posixpath.normpath(path).rstrip("/")


def load_allowlist(path: Path) -> Tuple[Dict[Tuple[str, str], dict], Dict[str, dict], Dict[str, dict]]:
    """許可リストを読み、(identity, 版) → 項目、URL → 項目、ローカルのバイナリのパス → 項目 を返す。
    形式が違えば読めないものとして扱う。"""
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
    by_path: Dict[str, dict] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise Unreadable(f"{path}: approvedPackages の項目が辞書でない")
        if ("url" in package) == ("path" in package):
            raise Unreadable(f"{path}: 承認済みの依存は url と path のどちらか一方だけを持つ")
        for field in (PACKAGE_FIELDS if "url" in package else BINARY_FIELDS):
            if not isinstance(package.get(field), str) or not package[field].strip():
                raise Unreadable(f"{path}: 承認済みの依存に {field} がない")
        # リモートのバイナリ（.binaryTarget(url:checksum:)）を承認する項目は checksum（配布物の SHA-256）を持つ。
        # ローカルのバイナリの項目は sha256（内容のハッシュ）を持つ。どちらも 64 桁の小文字の 16 進数
        for field in ("checksum", "sha256"):
            if field in package and not (isinstance(package[field], str) and SHA256.fullmatch(package[field])):
                raise Unreadable(f"{path}: {package['identity']} の {field} が 64 桁の小文字の 16 進数でない")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", package["approvedOn"]):
            raise Unreadable(f"{path}: {package['identity']} の approvedOn が YYYY-MM-DD でない")
        # 許可するライセンス以外は、オーナーの決定（UD-nn）の記録が要る
        decision = package.get("ownerDecision", "")
        if package["license"] not in licenses and not re.fullmatch(r"UD-\d+", str(decision)):
            raise Unreadable(
                f"{path}: {package['identity']} のライセンス {package['license']} は許可の対象外。"
                "ownerDecision にオーナーの決定（UD-nn）を記録する"
            )
        if "path" in package:
            by_path[normalize_path(package["path"])] = package
            continue
        by_pin[(package["identity"].lower(), package["version"])] = package
        by_url[normalize(package["url"])] = package
    return by_pin, by_url, by_path


def find_files(root: Path, name: str) -> Iterator[Path]:
    """root の下で名前（glob の形）に合うファイルを列挙する。除外するディレクトリは降りる前に枝刈りする。

    走査の失敗（読めないディレクトリ）は合格にせず、読めないものとして扱う（rglob は権限の失敗を黙って読み飛ばすため使わない）。
    """
    def failed(error: OSError) -> None:
        raise Unreadable(f"{error.filename}: {error}")

    found: List[Path] = []
    for directory, subdirectories, files in os.walk(root, onerror=failed):
        skipped = SKIPPED_ANYWHERE | (SKIPPED_AT_ROOT if Path(directory) == root else set())
        subdirectories[:] = [entry for entry in subdirectories if entry not in skipped]
        found.extend(Path(directory) / entry for entry in files if fnmatch.fnmatchcase(entry, name))
    yield from sorted(path for path in found if path.is_file())


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


def strip_comments(text: str) -> str:
    """Swift のコメントを空白に置き換える（改行は残し、位置を変えない）。文字列リテラルの中の「//」「/*」は残す。

    ブロックコメントは Swift と同じく入れ子にできる。閉じていないブロックコメントは読めないものとして扱う
    （以降をすべてコメントと見なすと、宣言を見落として合格にしうるため）。
    """
    out: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("//", index):
            end = line_comment_end(text, index)
            out.append(" " * (end - index))
            index = end
        elif text.startswith("/*", index):
            start = index
            index = block_comment_end(text, index)
            out.append("".join(char if char in "\r\n" else " " for char in text[start:index]))
        elif starts_string(text, index):
            # 文字列リテラル（"…"・"""…"""・#"…"#）は、閉じるまでそのまま写す
            end = string_end(text, index)
            out.append(text[index:end])
            index = end
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def blank(text: str) -> str:
    """改行を残して空白に置き換える（長さと改行の位置は変えない）。"""
    return "".join(char if char in "\r\n" else " " for char in text)


def mask_strings(text: str) -> str:
    """strip_comments の後の内容の文字列リテラルの本文を空白に置き換える（長さと改行の位置は変えない）。

    宣言やキーワードの検索をこの内容に対して行い、文字列リテラルの本文に現れる「.package(」「url:」を宣言と見なさない。
    補間（\\(…)、生の文字列では \\#(…)）の中の式は実行されるコードなので空白にせず、検査の対象に残す（mask_literal）。
    """
    out: List[str] = []
    index = 0
    while index < len(text):
        if starts_string(text, index):
            masked, index = mask_literal(text, index)
            out.append(masked)
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def mask_literal(text: str, index: int) -> Tuple[str, int]:
    """index で始まる文字列リテラルを置き換えた内容と、リテラルの終わりの次の位置を返す。

    リテラルは「"…"」の形にし（開始と終了の区切りの最後の 1 文字を「"」、ほかを空白）、本文は空白にする。
    補間は「"」で文字列を閉じてから式を置き、「"」で開き直す形にする（\\(式) → "␣式"。引用符の対が崩れず、
    式の中の宣言は呼び出しの形のまま見つかる）。式の中のコメントは strip_comments で、文字列は再帰的に置き換える。
    """
    end = string_end(text, index)  # 閉じていない文字列・補間はここで読めないものとして扱う
    hashes = len(text[index:]) - len(text[index:].lstrip("#"))
    quote_at = index + hashes
    quotes = '"""' if text.startswith('"""', quote_at) else '"'
    closing = quotes + "#" * hashes
    escape = "\\" + "#" * hashes
    content_end = end - len(closing)
    out = ['"' + " " * (quote_at + len(quotes) - index - 1)]
    cursor = quote_at + len(quotes)
    while cursor < content_end:
        if text.startswith(escape, cursor):
            after = cursor + len(escape)
            if after < content_end and text[after] == "(":
                close = interpolation_end(text, after)
                expression = text[after + 1:close - 1]
                out.append('"' + " " * len(escape) + mask_strings(strip_comments(expression)) + '"')
                cursor = close
            else:
                out.append(blank(text[cursor:after + 1]))  # エスケープされた 1 文字
                cursor = after + 1
            continue
        out.append(blank(text[cursor]))
        cursor += 1
    out.append(" " * (len(closing) - 1) + '"')
    masked = "".join(out)
    if len(masked) != end - index:
        raise Unreadable("文字列リテラルの補間を判定できない")  # 長さが変わると宣言の位置がずれるので合格にしない
    return masked, end


def line_comment_end(text: str, index: int) -> int:
    """index で始まる行コメントの終わり（改行の位置）を返す。Swift と同じく CR・LF・CRLF のどれでも終わる。"""
    found = re.compile(r"[\r\n]").search(text, index)
    return found.start() if found else len(text)


def block_comment_end(text: str, index: int) -> int:
    """index で始まるブロックコメント（入れ子にできる）の終わりの次の位置を返す。閉じていなければ読めないものとして扱う。"""
    depth = 0
    while index < len(text):
        if text.startswith("/*", index):
            depth += 1
            index += 2
        elif text.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
        else:
            index += 1
    raise Unreadable("ブロックコメントが閉じていない")


def starts_string(text: str, index: int) -> bool:
    return text[index] == '"' or (text[index] == "#" and re.match(r'#+"', text[index:]) is not None)


def string_end(text: str, index: int) -> int:
    """index で始まる文字列リテラルの終わりの次の位置を返す。閉じていなければ読めないものとして扱う
    （末尾まで文字列と見なすと、後ろの宣言を本文として隠したまま検査を続けるため）。

    補間（\\(…)、生の文字列では \\#(…)）の中は括弧の対応で読み、中の文字列リテラルで外側が閉じたと見なさない
    （見なすと、補間の中の「/*」「//」をコメントの始まりと取り違え、後ろの宣言を隠すため）。
    """
    hashes = len(text[index:]) - len(text[index:].lstrip("#"))
    quote_at = index + hashes
    quotes = '"""' if text.startswith('"""', quote_at) else '"'
    closing = quotes + "#" * hashes
    escape = "\\" + "#" * hashes
    cursor = quote_at + len(quotes)
    while cursor < len(text):
        if text.startswith(closing, cursor):
            return cursor + len(closing)
        if text.startswith(escape, cursor):
            cursor += len(escape)
            if cursor < len(text) and text[cursor] == "(":
                cursor = interpolation_end(text, cursor)
            else:
                cursor += 1  # エスケープされた 1 文字
            continue
        cursor += 1
    raise Unreadable("文字列リテラルが閉じていない")


def interpolation_end(text: str, start: int) -> int:
    """start の位置の「(」に対応する「)」の次の位置を返す。中の文字列リテラルは string_end で、
    コメント（行コメントと入れ子のブロックコメント）は括弧や引用符を解釈する前に読み飛ばす
    （コメントの中の「)」「"」を補間や外側の文字列の終わりと取り違えないため）。閉じていなければ読めないものとして扱う。
    """
    depth = 0
    cursor = start
    while cursor < len(text):
        if text.startswith("//", cursor):
            cursor = line_comment_end(text, cursor)
            continue
        if text.startswith("/*", cursor):
            cursor = block_comment_end(text, cursor)
            continue
        if starts_string(text, cursor):
            cursor = string_end(text, cursor)
            continue
        if text[cursor] == "(":
            depth += 1
        elif text[cursor] == ")":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    raise Unreadable("文字列の補間が閉じていない")


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


# 取得元を持つ宣言（Package の依存と、バイナリのターゲット）。識別子はバッククォートで囲んでも同じ（.`package`(…)）
SOURCE_DECLARATION = re.compile(r"\.(`?)(package|binaryTarget)\1\s*\(")
# 同じ識別子のメンバーの参照。呼び出しの形（SOURCE_DECLARATION）でないもの（関数の値として渡すなど）は、
# 取得元を判定できないので合格にしない。変数名の package（let package = Package(…)）は「.」がないので当たらない
MEMBER_REFERENCE = re.compile(r"\.(`?)(package|binaryTarget)\1(?![\w`])")
# 文字列リテラルだけの引数（連結・補間・変数を含まない）。引数の終わりは「,」か末尾
LITERAL_URL = re.compile(r'url:\s*"([^"\\]*)"\s*(?:,|$)')
LITERAL_PATH = re.compile(r'path:\s*"([^"\\]*)"\s*(?:,|$)')
LITERAL_CHECKSUM = re.compile(r'checksum:\s*"([^"\\]*)"\s*(?:,|$)')


def content_digest(path: Path) -> str:
    """ローカルのバイナリの内容のハッシュ（SHA-256 の 16 進数）を返す。

    ファイルはその内容から、ディレクトリ（.xcframework など）は配下のすべての項目から決定的に作る。
    配下の項目は相対パスの順に「種別・相対パス・内容の SHA-256（シンボリックリンクはリンクの先の文字列）」を連ねる。
    シンボリックリンクはたどらずにリンクの先の文字列を記録する（外のファイルを内容に取り込まない）。
    ないパス・読めない項目は読めないものとして扱う（承認と照合できないものを合格にしない）。
    """
    def file_hash(item: Path) -> str:
        digest = hashlib.sha256()
        with item.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    try:
        if path.is_symlink():
            raise Unreadable(f"{path}: ローカルのバイナリの起点がシンボリックリンク")
        if path.is_file():
            return file_hash(path)
        if not path.is_dir():
            raise Unreadable(f"{path}: ローカルのバイナリがない")
        digest = hashlib.sha256()
        for item in sorted(path.rglob("*"), key=lambda found: found.relative_to(path).as_posix()):
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                # リンクは成果物の中を指すものだけを認める（ビルドはリンクをたどるので、外を指すリンクは
                # 内容のハッシュに入らない外のファイルを成果物に取り込む）。連鎖したリンクもたどった先で判定する
                link = item.readlink()
                if link.is_absolute() or not item.resolve().is_relative_to(path.resolve()):
                    raise Unreadable(f"{item}: ローカルのバイナリの中のリンクが成果物の外を指す（{link}）")
                entry = f"L\0{relative}\0{link}"
            elif item.is_file():
                entry = f"F\0{relative}\0{file_hash(item)}"
            elif item.is_dir():
                entry = f"D\0{relative}\0"
            else:
                raise Unreadable(f"{item}: ローカルのバイナリの中に通常のファイル・ディレクトリ以外の項目がある")
            digest.update(entry.encode("utf-8") + b"\n")
        return digest.hexdigest()
    except OSError as error:
        raise Unreadable(f"{path}: {error}") from error


def literal_argument(arguments: str, masked: str, label: str, pattern: "re.Pattern[str]") -> Tuple[bool, Optional[str]]:
    """引数のうちコードに現れる「label:」を探し、その値が文字列リテラルだけならその内容を返す。

    戻り値は（キーワードがコードにあるか, 文字列リテラルの内容。リテラルでなければ None）。
    """
    found = re.search(rf"\b{label}:", masked)
    if not found:
        return False, None
    literal = pattern.match(arguments, found.start())
    return True, literal.group(1) if literal else None


def version_errors(arguments: str, masked: str, summary: str, approved: Set[str], pinned: Optional[List[object]]) -> List[str]:
    """.package(url:) の版の指定を、承認した版と、同じディレクトリの Package.resolved の確定版で照合する。"""
    if UNPINNED.search(masked):
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


def local_package_errors(directory: str, path: str, summary: str, root: Optional[Path]) -> List[str]:
    """.package(path:) の参照先を、参照元の Package.swift のディレクトリから解決して確かめる。

    起点の外・検査で除外するディレクトリの中・manifest のない参照先は、中の依存を検査できないので認めない。
    """
    location = posixpath.normpath(posixpath.join(directory, path))
    if posixpath.isabs(location) or location == ".." or location.startswith("../"):
        return [f"ローカルの Package {path} は起点の外にあり、中の依存を検査できない: .package({summary})"]
    parts = location.split("/")
    if parts[0] in SKIPPED_AT_ROOT or SKIPPED_ANYWHERE.intersection(parts):
        return [f"ローカルの Package {location} は検査の対象外のディレクトリにある: .package({summary})"]
    if root is None:
        return []
    target = (root / location).resolve()
    if not target.is_relative_to(root.resolve()):
        return [f"ローカルの Package {path} は起点の外にあり、中の依存を検査できない: .package({summary})"]
    # シンボリックリンクを解決した先にも同じ除外の規則を当てる（リンクで検査の対象外のディレクトリを指させない）
    resolved = target.relative_to(root.resolve()).parts
    if resolved and (resolved[0] in SKIPPED_AT_ROOT or SKIPPED_ANYWHERE.intersection(resolved)):
        return [f"ローカルの Package {location} の実体（{'/'.join(resolved)}）は検査の対象外のディレクトリにある: .package({summary})"]
    if not (target / "Package.swift").is_file():
        return [f"ローカルの Package {location} に manifest がない（Package.swift）: .package({summary})"]
    return []


def manifest_errors(
    manifest: str, by_url: Dict[str, dict], approved: Optional[Dict[str, Set[str]]] = None,
    pins: Optional[Dict[str, List[object]]] = None, by_path: Optional[Dict[str, dict]] = None, directory: str = "",
    root: Optional[Path] = None
) -> List[str]:
    """Package.swift の取得元と版を調べる。取得元・版を文字列リテラルから判定できない宣言は合格にしない。

    approved は URL → 承認した版、pins は URL → 同じディレクトリの Package.resolved の確定版、
    by_path はローカルのバイナリのパス → 承認済みの項目、directory は Package.swift のディレクトリ（起点からの相対）、
    root は探す起点（ローカルのバイナリの内容のハッシュを承認と照合するために読む）。
    コメントは先に除く。宣言とキーワードは文字列リテラルの本文を除いた内容から探し、
    取得元と版は、その宣言の引数の中だけで調べる。
    """
    code = strip_comments(manifest)
    masked = mask_strings(code)
    approved = approved or {}
    pins = pins or {}
    by_path = by_path or {}
    errors: List[str] = []
    for match in MEMBER_REFERENCE.finditer(masked):
        if not SOURCE_DECLARATION.match(masked, match.start()):
            errors.append(f"宣言を判定できない（.{match.group(2)}(…) の呼び出しの形で書く）: "
                          f"{' '.join(masked[match.start():match.start() + 60].split())}")
    for match in SOURCE_DECLARATION.finditer(masked):
        kind = match.group(2)
        start = match.end() - 1
        masked_arguments = call_arguments(masked, start)
        arguments = code[start + 1:start + 1 + len(masked_arguments)]  # 長さを変えずに置き換えたので位置は同じ
        summary = " ".join(arguments.split())[:80]
        has_url, url = literal_argument(arguments, masked_arguments, "url", LITERAL_URL)
        has_path, path = literal_argument(arguments, masked_arguments, "path", LITERAL_PATH)
        if kind == "package" and re.match(r"\s*id:", masked_arguments):
            errors.append(f"レジストリの依存は認めない: .package({summary})")
        elif has_url:
            if url is None:
                errors.append(f"取得元の URL を判定できない（文字列リテラルで書く）: .{kind}({summary})")
            elif normalize(url) not in by_url:
                errors.append(f"{url} は許可リストにない")
            elif kind == "package":
                errors.extend(version_errors(arguments, masked_arguments, summary,
                                             approved.get(normalize(url), set()), pins.get(normalize(url))))
            else:
                # リモートのバイナリは URL だけでなく、承認した配布物の checksum と宣言の checksum の一致を求める
                # （同じ URL の配布物が承認の後に差し替えられても通さない）
                approved_checksum = by_url[normalize(url)].get("checksum")
                has_checksum, checksum = literal_argument(arguments, masked_arguments, "checksum", LITERAL_CHECKSUM)
                if not approved_checksum:
                    errors.append(f"リモートのバイナリ {url} の承認に checksum がない（承認した配布物の SHA-256 を記録する）: "
                                  f".binaryTarget({summary})")
                elif not has_checksum or checksum is None:
                    errors.append(f"checksum を判定できない（文字列リテラルで書く）: .binaryTarget({summary})")
                elif checksum != approved_checksum:
                    errors.append(f"リモートのバイナリ {url} の checksum {checksum} が承認したもの（{approved_checksum}）と違う")
        elif not has_path or path is None:
            errors.append(f"取得元を判定できない（url: か path: を文字列リテラルで書く）: .{kind}({summary})")
        elif path != path.strip():
            # 前後に空白のあるパスは、空白を除いた別のファイルと取り違えないよう、照合せずに認めない
            errors.append(f"前後に空白のあるパスは認めない: .{kind}({summary})")
        elif kind == "binaryTarget":
            # ローカルのバイナリは、承認済みの項目（版・ライセンス・承認日を持つ）がある場合だけ認める
            location = normalize_path(posixpath.join(directory, path))
            if location not in by_path:
                errors.append(f"ローカルのバイナリ {location} は許可リストにない: .binaryTarget({summary})")
            elif root is not None:
                # 場所が一致しても、承認したときの内容と違えば認めない（再承認を求める）
                actual = content_digest(root / location)
                if actual != by_path[location]["sha256"]:
                    errors.append(f"ローカルのバイナリ {location} の内容が承認したものと違う（現在 {actual}）。"
                                  "確かめてから許可リストの sha256 を更新し、再承認を得る")
        else:
            # ローカルの Package（.package(path:)）は、起点の中で検査の対象になる manifest を指す場合だけ認める
            # （起点の中の manifest はすべて manifests() で検査するので、参照先の未承認の依存・バイナリも検出される）
            errors.extend(local_package_errors(directory, path, summary, root))
    return errors


def manifests(root: Path) -> Iterator[Path]:
    """Package.swift と、版別の manifest（Package@swift-<版>.swift）を返す。版別の manifest も同じ検査にかける
    （SwiftPM はツールの版に合う版別の manifest を Package.swift より優先して読むため）。"""
    found = set(find_files(root, "Package.swift")) | set(find_files(root, "Package@swift-*.swift"))
    yield from sorted(found)


def check(root: Path, allowlist: Path) -> List[str]:
    # 存在しない起点を走査すると対象が 0 件になり合格してしまうので、読めないものとして扱う
    if not root.is_dir():
        raise Unreadable(f"{root}: 探す起点のディレクトリがない")
    by_pin, by_url, by_path = load_allowlist(allowlist)
    # by_url は URL ごとに 1 項目しか持たないので、版の照合には by_pin から作った URL → 承認した版を使う
    approved_versions: Dict[str, Set[str]] = {}
    for package in by_pin.values():
        approved_versions.setdefault(normalize(package["url"]), set()).add(package["version"])
    # ディレクトリ → URL → 確定版（Package.swift と同じディレクトリの Package.resolved とだけ照合する）
    pins_by_directory: Dict[Path, Dict[str, List[object]]] = {}
    # Package.resolved の位置 → 確定した取得元（project.xcproj の依存の照合に使う）
    pinned_locations: Dict[Path, Set[str]] = {}
    errors: List[str] = []
    for path in find_files(root, "Package.resolved"):
        source = path.relative_to(root)
        pinned_locations.setdefault(path, set())
        for pin in resolved_pins(path):
            pin_location = pin.get("location") or pin.get("repositoryURL")
            pin_state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            if isinstance(pin_location, str):
                pins_by_directory.setdefault(path.parent, {}).setdefault(normalize(pin_location), []).append(
                    pin_state.get("version"))
                pinned_locations[path].add(normalize(pin_location))
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
    for path in manifests(root):
        try:
            manifest = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise Unreadable(f"{path}: {error}") from error
        pins = pins_by_directory.get(path.parent, {})
        try:
            found = manifest_errors(manifest, by_url, approved_versions, pins, by_path,
                                    path.parent.relative_to(root).as_posix(), root)
        except Unreadable as error:
            raise Unreadable(f"{path}: {error}") from error
        errors.extend(f"{path.relative_to(root)}: {message}" for message in found)
    for path in find_files(root, "project.xcproj"):
        source = path.relative_to(root)
        # 同じ .xcodeproj の中（project.xcworkspace/xcshareddata/swiftpm など）の Package.resolved だけと照合する
        pinned: Set[str] = set()
        for resolved, locations in pinned_locations.items():
            if resolved.is_relative_to(path.parent):
                pinned |= locations
        for text in strings(load_project(path)):
            if not REMOTE_URL.search(text):
                continue
            if normalize(text) not in by_url:
                errors.append(f"{source}: {text} は許可リストにない")
                continue
            if normalize(text) not in pinned:
                errors.append(f"{source}: {text} の確定版がない（{path.parent.name} の中の Package.resolved に記録する）")
            # 要求条件（版の指定）の書き方は未確認（dev-environment.md の「断定しない事項」）。
            # 推測で読むと誤って合格にしうるので、確認して検査を実装するまでは合格にしない
            errors.append(f"{source}: {text} の要求条件を判定できない（project.xcproj での版の指定の書き方が未確認。"
                          "最初の依存を足すときに書き方を確かめ、確定版の指定であることの検査を実装する）")
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
