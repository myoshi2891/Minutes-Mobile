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

from xcproj import Unreadable, load_project, reject_duplicate_keys

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
# scheme は大文字小文字を区別しない（RFC 3986。HTTPS:// も取得元の URL として照合する）。URL の本体は変えない
REMOTE_URL = re.compile(r"^(https?|ssh|git)://|^git@", re.IGNORECASE)


def normalize(url: str) -> str:
    """同じリポジトリを指す書き方の違い（末尾の .git・スラッシュ、scheme とホストの大文字小文字）をそろえる。

    パスと、authority のユーザー情報（user@ の部分）の大文字小文字は区別する（別の取得元を同じものと見なさない）。
    """
    text = url.strip().rstrip("/")
    found = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/]*)(.*)$", text)
    if found:
        # authority は最後の「@」でユーザー情報とホストに分ける（ユーザー情報に「@」を含みうるため）
        userinfo, at, host = found.group(2).rpartition("@")
        text = found.group(1).lower() + userinfo + at + host.lower() + found.group(3)
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


def load_allowlist(path: Path) -> Tuple[Dict[Tuple[str, str, str], dict], Dict[str, dict], Dict[str, dict]]:
    """許可リストを読み、(identity, 版, 正規化した URL) → 項目、URL → 項目、ローカルのバイナリのパス → 項目 を返す。
    形式が違えば読めないものとして扱う。"""
    try:
        # 重複した鍵は後の値で前の値を隠すので読めないものとする（resolved_pins と同じ。ValueError で送出される）
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:  # 深い入れ子は RecursionError
        raise Unreadable(f"{path}: {error!r}") from error
    if not isinstance(data, dict) or data.get("formatVersion") != 1:
        raise Unreadable(f"{path}: formatVersion が 1 でない")
    licenses = data.get("allowedLicenses")
    packages = data.get("approvedPackages")
    if not isinstance(licenses, list) or not all(isinstance(item, str) for item in licenses):
        raise Unreadable(f"{path}: allowedLicenses の形式が違う")
    if not isinstance(packages, list):
        raise Unreadable(f"{path}: approvedPackages の形式が違う")
    by_pin: Dict[Tuple[str, str, str], dict] = {}
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
            # 承認の path はリポジトリの起点からの相対パスだけ（絶対パス・起点の外を指すものは照合の基準にできない）
            location = normalize_path(package["path"])
            if posixpath.isabs(location) or location == ".." or location.startswith("../"):
                raise Unreadable(f"{path}: {package['identity']} の path は起点からの相対パスで、起点の中を指す（{package['path']}）")
            # 正規化して同じ場所を指す承認が 2 つあると、後の項目が先の sha256 を黙って上書きする。読めないものとする
            if location in by_path:
                raise Unreadable(f"{path}: ローカルのバイナリの承認が同じパスで重複している（{package['path']}）")
            by_path[location] = package
            continue
        # 同じ identity と版でも取得元が違えば別の承認とする（取得元ごとに承認する）
        url = normalize(package["url"])
        # リモートのバイナリの承認は URL ごとに 1 つ（checksum を URL で引くので、2 つ目で 1 つ目を黙って上書きしない）。
        # Package の承認（checksum を持たない）は版ごとに複数を持てる（版の照合は by_pin で行う）
        previous = by_url.get(url)
        if previous is not None and ("checksum" in package or "checksum" in previous):
            raise Unreadable(f"{path}: リモートのバイナリの承認が同じ URL で重複している（{package['url']}）")
        # checksum を持つ承認はリモートのバイナリ（.binaryTarget(url:checksum:)）の配布物だけを承認し、Package の依存
        # （.package(url:) と Package.resolved の確定版）は承認しない。版の照合の索引（by_pin）に入れない
        if "checksum" not in package:
            by_pin[(package["identity"].lower(), package["version"], url)] = package
        by_url[url] = package
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
        # 名前が合うディレクトリ（ディレクトリへのリンクを含む）は、ファイルとして検査できないので、ないものとせず読めないものとする
        for entry in sorted(subdirectories):
            if fnmatch.fnmatchcase(entry, name):
                raise Unreadable(f"{Path(directory) / entry}: 通常のファイルでない（ディレクトリ）")
        found.extend(Path(directory) / entry for entry in files if fnmatch.fnmatchcase(entry, name))
    # 名前が合うのに通常のファイルとして確かめられないもの（壊れたシンボリックリンクなど）は、黙って除かず読めないものとする
    for path in found:
        try:
            regular = path.is_file()
        except OSError as error:  # 調べられない（リンクの先の権限など）
            raise Unreadable(f"{path}: {error}") from error
        if not regular:
            raise Unreadable(f"{path}: 通常のファイルとして読めない（壊れたシンボリックリンクなど）")
    yield from sorted(found)


def pin_identity(pin: dict, legacy: bool) -> str:
    """同じ Package.resolved の中の重複の判定と、承認（許可リスト）の照合に使う名前。なければ空文字列（名前のない依存は check が報告する）。

    SwiftPM と同じく、identity の項目（大文字小文字を区別しない）で決まる。古い形式（version 1）は package ではなく
    repositoryURL の最後の要素（末尾の「/」と .git を除く）で決まる。2026-11-25 に Xcode 27.2 の swift package resolve で確かめた。
    """
    value = pin.get("repositoryURL") if legacy else pin.get("identity")
    if not isinstance(value, str):
        return ""
    if legacy:
        value = value.rstrip("/").rsplit("/", 1)[-1]
        value = value[:-4] if value.endswith(".git") else value
    return value.lower()


def resolved_pins(path: Path) -> List[Tuple[str, dict]]:
    """Package.resolved の依存を（名前, 記録）で返す。名前は pin_identity の値（重複の判定と承認の照合に同じ値を使う）。"""
    try:
        # 重複した鍵は後の値で前の値を隠すので読めないものとする（xcproj.py と同じ。ValueError で送出される）
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:  # 深い入れ子は RecursionError
        raise Unreadable(f"{path}: {error!r}") from error
    # 形式の版ごとに pins の位置が決まる（版 1 は object の中、版 2・3 は最上位）。知らない版・位置が合わないものは読まない
    version = data.get("version") if isinstance(data, dict) else None
    if type(version) is not int or version not in (1, 2, 3):
        raise Unreadable(f"{path}: 対応していない形式の版（version {version!r}）")
    legacy = version == 1
    if ("object" in data) != legacy or ("pins" in data) == legacy:
        raise Unreadable(f"{path}: 形式の版（version {version}）と pins の位置が合わない")
    holder = data["object"] if legacy else data
    pins = holder.get("pins") if isinstance(holder, dict) else None
    if not isinstance(pins, list) or not all(isinstance(pin, dict) for pin in pins):
        raise Unreadable(f"{path}: pins の形式が違う")
    # 同じ名前の依存が重なると、どの確定版が使われるか決まらない（SwiftPM も壊れたファイルとして拒否する）
    seen: Set[str] = set()
    named = [(pin_identity(pin, legacy), pin) for pin in pins]
    for identity in filter(None, (name for name, _ in named)):
        if identity in seen:
            raise Unreadable(f"{path}: {identity} が重複している")
        seen.add(identity)
    return named


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
        elif starts_regex(text, index):
            # 拡張の区切りの正規表現のリテラル（#/…/#）は、閉じるまでそのまま写す（中の「"」「//」を文字列・コメントと
            # 取り違えない）
            end = regex_end(text, index)
            out.append(text[index:end])
            index = end
        elif starts_string(text, index):
            # 文字列リテラル（"…"・"""…"""・#"…"#）は、閉じるまでそのまま写す
            # （補間の中の式のコメントだけは空白に置き換える。式の中の宣言の引数を、補間の外と同じに読むため）
            end = string_end(text, index)
            out.append(strip_interpolations(text, index, end))
            index = end
        elif text[index] == "/":
            # 区切りのない正規表現のリテラル（/…/）と除算は字句だけでは区別できない。取り違えると中の「"」で後ろの宣言を
            # 文字列として隠しうるので、判定できないものとして扱う（manifest では #/…/# の形で書く）
            raise Unreadable("「/」を判定できない（区切りのない正規表現のリテラルか除算。正規表現は #/…/# の形で書く）")
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def strip_interpolations(text: str, index: int, end: int) -> str:
    """index から end までの文字列リテラルを、補間の中の式のコメントだけを空白に置き換えて返す（本文と位置は変えない）。"""
    hashes = len(text[index:]) - len(text[index:].lstrip("#"))
    escape = "\\" + "#" * hashes
    out: List[str] = []
    cursor = index
    while cursor < end:
        if text.startswith(escape, cursor):
            after = cursor + len(escape)
            if text.startswith("(", after):
                close = interpolation_end(text, after)
                out.append(text[cursor:after + 1] + strip_comments(text[after + 1:close - 1]) + ")")
                cursor = close
            else:
                out.append(text[cursor:after + 1])  # エスケープされた 1 文字
                cursor = after + 1
            continue
        out.append(text[cursor])
        cursor += 1
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
        if starts_regex(text, index):
            end = regex_end(text, index)
            out.append(blank(text[index:end]))  # 正規表現のリテラルの中は宣言として数えない
            index = end
        elif starts_string(text, index):
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


def starts_regex(text: str, index: int) -> bool:
    """拡張の区切りの正規表現のリテラル（#/…/#・##/…/##）の始まりか。"""
    return text[index] == "#" and re.match(r"#+/", text[index:]) is not None


def regex_end(text: str, index: int) -> int:
    """index で始まる拡張の区切りの正規表現のリテラルの終わりの次の位置を返す。閉じていなければ読めないものとして扱う。"""
    hashes = len(text[index:]) - len(text[index:].lstrip("#"))
    closing = "/" + "#" * hashes
    cursor = index + hashes + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2  # エスケープした 1 文字（「\\/#」の「/#」を終わりと取り違えない）
        elif text.startswith(closing, cursor):
            return cursor + len(closing)
        else:
            cursor += 1
    raise Unreadable("正規表現のリテラルが閉じていない")


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
        if starts_regex(text, cursor):
            # 正規表現のリテラル（#/…/#）の中の「(」「)」「"」を括弧・文字列として数えない
            cursor = regex_end(text, cursor)
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
    """start の位置の「(」に対応する「)」までの引数を返す。文字列リテラルの中の括弧は数えない。
    閉じていなければ読めないものとして扱う。"""
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
    raise Unreadable("宣言の引数の括弧が閉じていない")


# 版の指定（文字列リテラルだけ）。ブランチ・コミットの指定は版で固定していないものとして認めない。
# 版は SwiftPM と同じく、先行版（-rc.1 など）とビルドのメタデータ（+build）の接尾辞を持てる
VERSION = r'"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)"'
UNPINNED = re.compile(r"\b(branch|revision)\s*:|\.(branch|revision)\s*\(")
# 版の指定の引数（最上位の「,」で区切った 1 つ）の全体の形。引数の全体が一致するものだけを認める（「"1.0.0" + x」のような
# 式の一部を版と見なさない）。呼び出しの名前と「(」の間の空白（コメントを除いた跡を含む）は Swift と同じく許す。
# from: は、引数の先頭にあるもの（.package の引数）だけを upToNextMajor とする（.upToNextMinor(from:) などの中の from: と区別する）
# ラベルとコロンの間の空白（コメントを除いた跡を含む。from /* … */ : "1.2.3"）も Swift と同じく許す
REQUIREMENTS = (
    ("exact", re.compile(rf"(?:exact\s*:\s*{VERSION}|\.exact\s*\(\s*{VERSION}\s*\))\s*")),
    ("minor", re.compile(rf"\.upToNextMinor\s*\(\s*from\s*:\s*{VERSION}\s*\)\s*")),
    ("major", re.compile(rf"(?:\.upToNextMajor\s*\(\s*from\s*:\s*{VERSION}\s*\)|from\s*:\s*{VERSION})\s*")),
    ("closed", re.compile(rf"{VERSION}\s*\.\.\.\s*{VERSION}\s*")),
    ("half-open", re.compile(rf"{VERSION}\s*\.\.<\s*{VERSION}\s*")),
)
# 版の指定ではない引数のラベル（取得元・名前・trait）
OTHER_ARGUMENTS = re.compile(r"(?:url|name|traits)\s*:")
Version = Tuple[int, int, int]
# 版の指定: (exact で指定した版の文字列。範囲なら None, 下限, 上限, 上限を含むか)
Rule = Tuple[Optional[str], Version, Version, bool]


def parse_version(text: object) -> Optional[Version]:
    """接尾辞のない版（1.2.3）だけを読む。先行版などの接尾辞のある版は範囲の比較に使わない（None）。"""
    found = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text) if isinstance(text, str) else None
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None


def top_level_arguments(arguments: str, masked: str) -> List[str]:
    """引数を最上位の「,」で区切る。括弧の深さは文字列リテラルの本文を除いた内容（masked）で数え、
    区切った位置で元の引数（arguments。長さは masked と同じ）を切り出す。"""
    parts: List[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(masked):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(arguments[start:index])
            start = index + 1
    parts.append(arguments[start:])
    return parts


def requirement(arguments: str, masked: str) -> Optional[Rule]:
    """.package の版の指定を返す。判定できなければ None。

    版の指定になりうる引数（取得元・名前・trait 以外の最上位の引数）がちょうど 1 つで、その全体が既知の形に一致する場合だけ
    判定する。先行版の接尾辞を持つ版は exact でだけ認め、確定版と文字列で照合する（1.2.0-rc.1 と 1.2.0 を区別する）。
    範囲（from・upToNext…・区間）の境界に接尾辞がある指定は、順序を確実に決められないので判定できないものとする。
    """
    candidates = [part for part, masked_part in zip(top_level_arguments(arguments, masked), top_level_arguments(masked, masked))
                  if masked_part.strip() and not OTHER_ARGUMENTS.match(masked_part.strip())]
    if len(candidates) != 1:
        return None
    candidate = candidates[0].strip()
    for kind, pattern in REQUIREMENTS:
        found = pattern.fullmatch(candidate)
        if not found:
            continue
        values = [value for value in found.groups() if value is not None]
        if kind == "exact":
            return values[0], (0, 0, 0), (0, 0, 0), True
        bounds = [parse_version(value) for value in values]
        if any(bound is None for bound in bounds):
            return None  # 範囲の境界に接尾辞がある
        low = bounds[0]
        assert low is not None
        if kind == "minor":
            return None, low, (low[0], low[1] + 1, 0), False
        if kind == "major":
            return None, low, (low[0] + 1, 0, 0), False
        high = bounds[1]
        assert high is not None
        return None, low, high, kind == "closed"
    return None


def satisfies(version: str, rule: Rule) -> bool:
    exact, low, high, inclusive = rule
    if exact is not None:
        return version == exact
    parsed = parse_version(version)
    if parsed is None:
        return False  # 先行版などの確定版は、範囲の指定では認めない
    return low <= parsed and (parsed <= high if inclusive else parsed < high)


# 取得元を持つ宣言（Package の依存と、バイナリのターゲット）。識別子はバッククォートで囲んでも同じ（.`package`(…)）
SOURCE_DECLARATION = re.compile(r"\.(`?)(package|binaryTarget)\1\s*\(")
# 同じ識別子のメンバーの参照。呼び出しの形（SOURCE_DECLARATION）でないもの（関数の値として渡すなど）は、
# 取得元を判定できないので合格にしない。変数名の package（let package = Package(…)）は「.」がないので当たらない
MEMBER_REFERENCE = re.compile(r"\.(`?)(package|binaryTarget)\1(?![\w`])")
# 文字列リテラルだけの引数（連結・補間・変数を含まない）。引数の終わりは「,」か末尾
LITERAL_URL = re.compile(r'url\s*:\s*"([^"\\]*)"\s*(?:,|$)')
LITERAL_PATH = re.compile(r'path\s*:\s*"([^"\\]*)"\s*(?:,|$)')
LITERAL_CHECKSUM = re.compile(r'checksum\s*:\s*"([^"\\]*)"\s*(?:,|$)')


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

    def failed(error: OSError) -> None:
        raise Unreadable(f"{error.filename}: {error}")

    def resolved(item: Path) -> Path:
        """リンクを解決した実体のパス。循環するリンクは解決できないので、読めないものとして扱う。"""
        try:
            return item.resolve()
        except RuntimeError as error:  # Python 3.9〜3.12 は循環するリンクで RuntimeError を送出する
            raise Unreadable(f"{item}: リンクを解決できない（循環している）: {error}") from error

    def entries(base: Path) -> List[Path]:
        """配下のすべての項目を返す。読めないディレクトリは読めないものとして扱う
        （rglob は権限の失敗を黙って読み飛ばし、欠けた内容のハッシュを作るため使わない）。
        ディレクトリへのシンボリックリンクはたどらず、項目として返す。"""
        found: List[Path] = []
        for directory, subdirectories, files in os.walk(base, onerror=failed):
            found.extend(Path(directory) / entry for entry in subdirectories + files)
        return found

    try:
        if path.is_symlink():
            raise Unreadable(f"{path}: ローカルのバイナリの起点がシンボリックリンク")
        if path.is_file():
            return file_hash(path)
        if not path.is_dir():
            raise Unreadable(f"{path}: ローカルのバイナリがない")
        digest = hashlib.sha256()
        for item in sorted(entries(path), key=lambda found: found.relative_to(path).as_posix()):
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                # リンクは成果物の中を指すものだけを認める（ビルドはリンクをたどるので、外を指すリンクは
                # 内容のハッシュに入らない外のファイルを成果物に取り込む）。連鎖したリンクもたどった先で判定する
                # 記録するのは os.readlink の生の文字列（Path にすると末尾の「/」などの違いが消え、別のリンクを同じ内容とみなす）。
                # 外を指すかの判定は、これまでどおり解決した実体のパスで行う
                link = os.readlink(item)
                if os.path.isabs(link) or not resolved(item).is_relative_to(resolved(path)):
                    raise Unreadable(f"{item}: ローカルのバイナリの中のリンクが成果物の外を指す（{link}）")
                entry = f"L\0{relative}\0{link}"
            elif item.is_file():
                entry = f"F\0{relative}\0{file_hash(item)}"
            elif item.is_dir():
                entry = f"D\0{relative}\0"
            else:
                raise Unreadable(f"{item}: ローカルのバイナリの中に通常のファイル・ディレクトリ以外の項目がある")
            # UTF-8 にできないパス（os.walk が surrogateescape で返す、UTF-8 でない名前）は、記録の形を決められないので
            # 読めないものとする（有効な UTF-8 のパスの記録の形は変えない）
            try:
                encoded = entry.encode("utf-8")
            except UnicodeEncodeError as error:
                raise Unreadable(f"{item}: ローカルのバイナリの中のパスを UTF-8 にできない: {error}") from error
            digest.update(encoded + b"\n")
        return digest.hexdigest()
    except OSError as error:
        raise Unreadable(f"{path}: {error}") from error


def top_level_only(masked: str) -> str:
    """引数（文字列リテラルの本文を除いた内容）のうち、括弧の中（入れ子の呼び出し・配列・クロージャー）を空白に置き換える。

    長さと改行の位置は変えない（見つけた位置で元の引数を読むため）。最上位の括弧の文字そのものは残す。
    """
    out: List[str] = []
    depth = 0
    for char in masked:
        if char in ")]}" and depth > 0:
            depth -= 1
        out.append(char if depth == 0 or char in "\r\n" else " ")
        if char in "([{":
            depth += 1
    return "".join(out)


def literal_argument(arguments: str, masked: str, label: str, pattern: "re.Pattern[str]") -> Tuple[bool, Optional[str]]:
    """引数のうちコードに現れる「label:」を探し、その値が文字列リテラルだけならその内容を返す。

    戻り値は（キーワードがコードにあるか, 文字列リテラルの内容。リテラルでなければ None）。
    """
    # コメントを除いた跡の空白（url /* … */ : "…"）があってもラベルとして読む。探すのは宣言の最上位の引数の先頭だけ
    # （入れ子の呼び出しの引数、例えば name: targetName(url: …) の url: や、引数の式の中の「条件 ? url : …」の url : を、
    # 宣言の取得元と見なさない）。文字列リテラルの中の「,」で区切らないよう、区切りは masked で決める
    for argument, masked_argument in zip(top_level_arguments(arguments, masked), top_level_arguments(masked, masked)):
        found = re.match(rf"\s*{label}\s*:", masked_argument)
        if found:
            literal = pattern.match(argument.lstrip())
            return True, literal.group(1) if literal else None
    return False, None


def version_errors(arguments: str, masked: str, summary: str, approved: Set[str], pinned: Optional[List[object]]) -> List[str]:
    """.package(url:) の版の指定を、承認した版と、同じディレクトリの Package.resolved の確定版で照合する。"""
    # 宣言の最上位の引数だけを見る（traits の中の selectedTraits(revision: 2) のような入れ子の引数を版の指定と見なさない）
    if UNPINNED.search(top_level_only(masked)):
        return [f"版で固定されていない（ブランチやコミットの指定は認めない）: .package({summary})"]
    rule = requirement(arguments, masked)
    if rule is None:
        return [f"版の指定を判定できない（版を文字列リテラルで書く）: .package({summary})"]
    if not pinned:
        return [f"確定版がない（同じディレクトリの Package.resolved に記録する）: .package({summary})"]
    errors: List[str] = []
    for version in pinned:
        if not isinstance(version, str) or not satisfies(version, rule):
            errors.append(f"宣言の版の指定と確定版 {version} が合わない: .package({summary})")
        elif version not in approved:
            errors.append(f"確定版 {version} は許可リストにない: .package({summary})")
    return errors


def contained(root: Path, location: str) -> bool:
    """起点からの相対パス location が、リンクを解決した後も起点の中を指すか。循環するリンクは読めないものとして扱う。"""
    try:
        return (root / location).resolve().is_relative_to(root.resolve())
    except RuntimeError as error:  # 循環するリンク（Python 3.9〜3.12）
        raise Unreadable(f"{root / location}: リンクを解決できない（循環している）: {error}") from error


# ローカルの Package とローカルのバイナリを置ける場所（check.sh の手順 7 がカバレッジを測る写しに Packages/ だけを展開するので、その外の
# ものを参照すると、写しの中で解決できない）
LOCAL_PACKAGE_ROOT = "Packages"


def leaves_coverage_input(root: Path, link: str, seen: Set[str]) -> bool:
    """リンクが、カバレッジの測定の写し（Packages/ だけを展開する）の外を指すか。check.sh の verify_coverage_links と同じ規則で、
    リンク先は相対パスに限り、途中の要素と経由するリンクもすべて Packages/ の中にあることを求める（起点そのものは通ってよい）。
    """
    if link in seen:
        return False  # 循環するリンクは manifest を読めないので、manifest の有無の確認で止まる
    seen.add(link)
    top = os.path.realpath(root)
    packages = os.path.join(top, LOCAL_PACKAGE_ROOT)
    target = os.readlink(link)
    if os.path.isabs(target):
        return True
    current = os.path.realpath(os.path.dirname(link))
    for part in target.split(os.sep):
        step = os.path.join(current, part)
        if os.path.islink(step) and leaves_coverage_input(root, step, seen):
            return True
        current = os.path.realpath(step)
        if current not in (top, packages) and not current.startswith(packages + os.sep):
            return True
    return False


def outside_link(root: Path, location: str) -> Optional[str]:
    """location の途中の要素（自身を含む）のうち、カバレッジの測定の写しの外を指すリンク。なければ None。"""
    parts = location.split("/")
    try:
        for depth in range(1, len(parts) + 1):
            alias = os.path.join(root, *parts[:depth])
            if os.path.islink(alias) and leaves_coverage_input(root, alias, set()):
                return "/".join(parts[:depth])
    except OSError as error:
        raise Unreadable(f"{root / location}: {error}") from error
    return None


def outside_inner_link(root: Path, location: str) -> Optional[str]:
    """ローカルのバイナリ（content_digest が読めたもの）の中の、カバレッジの測定の写しの外を経由するリンク。なければ None。"""
    def failed(error: OSError) -> None:
        raise Unreadable(f"{error.filename}: {error}")

    try:
        for directory, subdirectories, files in os.walk(root / location, onerror=failed):
            for name in sorted(subdirectories + files):
                link = os.path.join(directory, name)
                if os.path.islink(link) and leaves_coverage_input(root, link, set()):
                    return os.path.relpath(link, root)
    except OSError as error:
        raise Unreadable(f"{root / location}: {error}") from error
    return None


def local_package_errors(directory: str, path: str, summary: str, root: Optional[Path]) -> List[str]:
    """.package(path:) の参照先を、参照元の Package.swift のディレクトリから解決して確かめる。

    起点の外・検査で除外するディレクトリの中・manifest のない参照先は、中の依存を検査できないので認めない。
    """
    location = posixpath.normpath(posixpath.join(directory, path))
    if posixpath.isabs(location) or location == ".." or location.startswith("../"):
        return [f"ローカルの Package {path} は起点の外にあり、中の依存を検査できない: .package({summary})"]
    parts = location.split("/")
    if parts[0] != LOCAL_PACKAGE_ROOT:
        return [f"ローカルの Package {location} は {LOCAL_PACKAGE_ROOT}/ の外にある（カバレッジの測定の写しに含まれない）: "
                f".package({summary})"]
    if parts[0] in SKIPPED_AT_ROOT or SKIPPED_ANYWHERE.intersection(parts):
        return [f"ローカルの Package {location} は検査の対象外のディレクトリにある: .package({summary})"]
    if root is None:
        return []
    # 参照先の途中の要素を含め、測定の写しの外を指す（絶対パスで中を指すものも含む）ディレクトリのリンクは、解決した先が Packages/ の
    # 中でも写しの中で解決できないので、解決する前に認めない
    alias = outside_link(root, location)
    if alias is not None:
        return [f"ローカルの Package {location} の {alias} が {LOCAL_PACKAGE_ROOT}/ の外を指すリンク: .package({summary})"]
    try:
        target = (root / location).resolve()
    except RuntimeError as error:  # 循環するリンク（Python 3.9〜3.12）
        raise Unreadable(f"{root / location}: ローカルの Package の参照先のリンクを解決できない（循環している）: {error}") from error
    if not target.is_relative_to(root.resolve()):
        return [f"ローカルの Package {path} は起点の外にあり、中の依存を検査できない: .package({summary})"]
    # シンボリックリンクを解決した先にも同じ除外の規則を当てる（リンクで検査の対象外のディレクトリを指させない）
    resolved = target.relative_to(root.resolve()).parts
    if not resolved or resolved[0] != LOCAL_PACKAGE_ROOT:
        return [f"ローカルの Package {location} の実体（{'/'.join(resolved)}）は {LOCAL_PACKAGE_ROOT}/ の外にある: .package({summary})"]
    if resolved and (resolved[0] in SKIPPED_AT_ROOT or SKIPPED_ANYWHERE.intersection(resolved)):
        return [f"ローカルの Package {location} の実体（{'/'.join(resolved)}）は検査の対象外のディレクトリにある: .package({summary})"]
    if not (target / "Package.swift").is_file():
        return [f"ローカルの Package {location} に manifest がない（Package.swift）: .package({summary})"]
    # 測定の写しの外を指す manifest のリンクは、写しの中で解決できない（check.sh の手順 7 が拒否する）ので先に認めない
    try:
        manifest = os.path.join(target, "Package.swift")
        if os.path.islink(manifest) and leaves_coverage_input(root, manifest, set()):
            return [f"ローカルの Package {location} の manifest（Package.swift）が {LOCAL_PACKAGE_ROOT}/ の外を指すリンク: "
                    f".package({summary})"]
    except OSError as error:
        raise Unreadable(f"{target}: {error}") from error
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
            if posixpath.isabs(location) or location == ".." or location.startswith("../"):
                errors.append(f"ローカルのバイナリ {path} は起点の外にある: .binaryTarget({summary})")
            elif location not in by_path:
                errors.append(f"ローカルのバイナリ {location} は許可リストにない: .binaryTarget({summary})")
            elif root is not None and not contained(root, location):
                errors.append(f"ローカルのバイナリ {location} の実体が起点の外にある（シンボリックリンクなど）: .binaryTarget({summary})")
            elif location.split("/")[0] != LOCAL_PACKAGE_ROOT:
                errors.append(f"ローカルのバイナリ {location} は {LOCAL_PACKAGE_ROOT}/ の外にある（カバレッジの測定の写しに含まれない）: "
                              f".binaryTarget({summary})")
            elif root is not None and (root / location).resolve().relative_to(root.resolve()).parts[:1] != (LOCAL_PACKAGE_ROOT,):
                errors.append(f"ローカルのバイナリ {location} の実体が {LOCAL_PACKAGE_ROOT}/ の外にある（シンボリックリンクなど）: "
                              f".binaryTarget({summary})")
            elif root is not None and outside_link(root, location) is not None:
                # 実体が Packages/ の中でも、絶対パスなどのリンクは測定の写しで解決できない（check.sh の verify_coverage_links が拒否する）
                errors.append(f"ローカルのバイナリ {location} の {outside_link(root, location)} が {LOCAL_PACKAGE_ROOT}/ の外を指すリンク: "
                              f".binaryTarget({summary})")
            elif root is not None:
                # 場所が一致しても、承認したときの内容と違えば認めない（再承認を求める）
                actual = content_digest(root / location)
                if actual != by_path[location]["sha256"]:
                    errors.append(f"ローカルのバイナリ {location} の内容が承認したものと違う（現在 {actual}）。"
                                  "確かめてから許可リストの sha256 を更新し、再承認を得る")
                inner = outside_inner_link(root, location)
                if inner is not None:
                    errors.append(f"ローカルのバイナリ {location} の中の {inner} が {LOCAL_PACKAGE_ROOT}/ の外を経由するリンク: "
                                  f".binaryTarget({summary})")
        else:
            # ローカルの Package（.package(path:)）は、起点の中で検査の対象になる manifest を指す場合だけ認める
            # （起点の中の manifest はすべて manifests() で検査するので、参照先の未承認の依存・バイナリも検出される）
            errors.extend(local_package_errors(directory, path, summary, root))
    return errors


def package_declarations(manifest: str, directory: str, root: Path) -> Tuple[List[Tuple[str, str, str, str]], List[Path]]:
    """Package.swift の Package の依存を、リモート（URL, 引数, 文字列を除いた引数, 表示用の引数）とローカル（実体のディレクトリ）で返す。

    取得元を判定できない宣言・認めないローカルの Package は含めない（manifest_errors が報告する）。
    """
    code = strip_comments(manifest)
    masked = mask_strings(code)
    remote: List[Tuple[str, str, str, str]] = []
    local: List[Path] = []
    for match in SOURCE_DECLARATION.finditer(masked):
        if match.group(2) != "package":
            continue
        start = match.end() - 1
        masked_arguments = call_arguments(masked, start)
        arguments = code[start + 1:start + 1 + len(masked_arguments)]
        summary = " ".join(arguments.split())[:80]
        has_url, url = literal_argument(arguments, masked_arguments, "url", LITERAL_URL)
        _, path = literal_argument(arguments, masked_arguments, "path", LITERAL_PATH)
        if has_url:
            if url is not None:
                remote.append((normalize(url), arguments, masked_arguments, summary))
        elif path is not None and path == path.strip() and not local_package_errors(directory, path, summary, root):
            # SwiftPM と同じく「..」を字面で取り除いてから解決する（local_package_errors が確かめたパスと同じものをたどる）
            local.append((root / posixpath.normpath(posixpath.join(directory, path))).resolve())
    return remote, local


Edition = Tuple[Tuple[int, ...], Path]  # 版別の manifest の（接尾辞の版, パス）
VARIANT_NAME = re.compile(r"Package@swift-(\d+(?:\.\d+){0,2})\.swift")
# 行頭の「//」で始まる指定の行（行の区切りは LF・CR）。最初に一致した行を読む
TOOLS_VERSION = re.compile(r"(?:\A|[\r\n])[^\S\r\n]*//[^\S\r\n]*swift-tools-version[^\S\r\n]*:[^\S\r\n]*(\d+)\.(\d+)(?:\.(\d+))?",
                           re.IGNORECASE)


def padded(version: Tuple[int, ...]) -> Version:
    values = (*version, 0, 0)
    return values[0], values[1], values[2]


def tools_version(text: str) -> Optional[Version]:
    """manifest の swift-tools-version。読み取れなければ None。

    6.0 以降の指定は、先頭の行でなくてもよい（ほかのコメントの後など）。5.x までの指定は、空行を除く先頭の行だけを認める
    （後の行の 5.x の指定は SwiftPM がエラーにする）。2026-11-25 に Swift 6.4.2 の swift package dump-package で確かめた。
    """
    found = TOOLS_VERSION.search(text)
    if found is None or (int(found.group(1)) < 6 and text[:found.start()].strip()):
        return None
    return int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)


def selected_manifests(texts: Dict[Path, str], directory: Path, editions: List[Edition], toolchain: Version) -> List[Path]:
    """そのツールの版で SwiftPM が読む manifest。tools-version を読み取れず 1 つに決まらなければ、ありうるものをすべて返す。

    選び方（2026-11-25 に Swift 6.4.2 の swift package dump-package で確かめた）: 接尾辞が major.minor.patch・major.minor・major と
    一致するものをこの順に選ぶ。なければ、接尾辞がツールの版以下で最も新しいものを、その tools-version が Package.swift の
    tools-version より新しい場合と、Package.swift の tools-version がツールより新しい（そのツールで読めない）場合に選び、
    それ以外は Package.swift を選ぶ。選んだ manifest の tools-version がツールより新しければ、そのツールでは読めないので
    たどらない（次に新しいものへは移らない）。
    """
    regular = directory / "Package.swift"
    by_suffix = dict(editions)
    chosen: List[Path] = []
    for length in (3, 2, 1):
        if toolchain[:length] in by_suffix:
            chosen = [by_suffix[toolchain[:length]]]
            break
    else:
        lower = [edition for edition in editions if padded(edition[0]) <= toolchain]
        highest = max((padded(suffix) for suffix, _ in lower), default=None)
        base = tools_version(texts[regular]) if regular in texts else None
        # 同じ版を表す接尾辞（6 と 6.0）が並ぶときの優先は確かめていないので、どちらもありうるものとする
        for newest in [path for suffix, path in lower if padded(suffix) == highest]:
            ours = tools_version(texts[newest])
            if regular not in texts or ours is None or base is None or ours > base or base > toolchain:
                chosen.append(newest)
            if regular in texts and (ours is None or base is None or (ours <= base and base <= toolchain)):
                chosen.append(regular)
        if not lower and regular in texts:
            chosen = [regular]
    return [path for path in chosen if (tools_version(texts[path]) or toolchain) <= toolchain]


def toolchains(texts: Dict[Path, str]) -> List[Version]:
    """manifest の選び方が変わりうる境目の版（接尾辞と tools-version の版と、その次の patch・minor・major）。"""
    versions: Set[Version] = {(0, 0, 0)}
    for path, text in texts.items():
        found = VARIANT_NAME.fullmatch(path.name)
        suffix = padded(tuple(int(part) for part in found.group(1).split("."))) if found else None
        for version in (suffix, tools_version(text)):
            if version is not None:
                major, minor, patch = version
                versions |= {version, (major, minor, patch + 1), (major, minor + 1, 0), (major + 1, 0, 0)}
    return sorted(versions)


def transitive_errors(root: Path, texts: Dict[Path, str], by_url: Dict[str, dict], approved: Dict[str, Set[str]],
                      pins: Dict[Path, Dict[str, List[object]]]) -> List[str]:
    """ビルドの起点になる各 Package について、ローカルの Package をたどった先のリモートの依存を、起点の Package.resolved と照合する。

    SwiftPM は起点の Package.resolved だけを使い、ローカルの Package 自身のものは読まない（自身のものは単独の起点として照合する）。
    """
    # 宣言は manifest ごとに持つ（同じディレクトリの版別の manifest の宣言を混ぜると、同時には読まれない辺がつながる）
    remote: Dict[Path, List[Tuple[str, str, str, str]]] = {}
    local: Dict[Path, List[Path]] = {}
    editions: Dict[Path, List[Edition]] = {}
    for path, text in texts.items():
        remote[path], local[path] = package_declarations(text, path.parent.relative_to(root).as_posix(), root)
        editions.setdefault(path.parent, [])
        found = VARIANT_NAME.fullmatch(path.name)
        if found:
            editions[path.parent].append((tuple(int(part) for part in found.group(1).split(".")), path))
    errors: Dict[str, None] = {}  # 同じ報告を、ツールの版ごとに繰り返さない
    for toolchain in toolchains(texts):
        selected = {directory: selected_manifests(texts, directory, items, toolchain) for directory, items in editions.items()}
        for start in sorted(selected):
            visited = {start}
            waiting = [target for path in selected[start] for target in local[path]]
            while waiting:
                directory = waiting.pop()
                if directory in visited:
                    continue
                visited.add(directory)
                for path in selected.get(directory, []):
                    waiting.extend(local[path])
                    for url, arguments, masked, summary in remote[path]:
                        if url not in by_url:
                            continue  # 許可リストにないことは、その manifest の検査が報告する
                        for message in version_errors(arguments, masked, summary, approved.get(url, set()), pins.get(start, {}).get(url)):
                            errors[f"{start.relative_to(root).as_posix()} を起点とする {path.relative_to(root)}: {message}"] = None
    return list(errors)


def manifests(root: Path) -> Iterator[Path]:
    """Package.swift と、版別の manifest（Package@swift-<版>.swift）を返す。版別の manifest も同じ検査にかける
    （SwiftPM はツールの版に合う版別の manifest を Package.swift より優先して読むため）。"""
    found = set(find_files(root, "Package.swift")) | set(find_files(root, "Package@swift-*.swift"))
    yield from sorted(found)


def check(root: Path, allowlist: Path) -> List[str]:
    # 存在しない起点を走査すると対象が 0 件になり合格してしまうので、読めないものとして扱う
    if not root.is_dir():
        raise Unreadable(f"{root}: 探す起点のディレクトリがない")
    # 実体のパスでたどる（ローカルの Package の実体のディレクトリと、走査で見つけた manifest のディレクトリを対応させる）
    root = root.resolve()
    by_pin, by_url, by_path = load_allowlist(allowlist)
    # by_url は URL ごとに 1 項目しか持たないので、版の照合には by_pin から作った URL → 承認した版を使う
    approved_versions: Dict[str, Set[str]] = {}
    for (_, version, url) in by_pin:
        approved_versions.setdefault(url, set()).add(version)
    # ディレクトリ → URL → 確定版（Package.swift と同じディレクトリの Package.resolved とだけ照合する）
    pins_by_directory: Dict[Path, Dict[str, List[object]]] = {}
    # Package.resolved の位置 → 確定した取得元（project.xcproj の依存の照合に使う）
    pinned_locations: Dict[Path, Set[str]] = {}
    errors: List[str] = []
    for path in find_files(root, "Package.resolved"):
        source = path.relative_to(root)
        pinned_locations.setdefault(path, set())
        for identity, pin in resolved_pins(path):
            pin_location = pin.get("location") or pin.get("repositoryURL")
            pin_state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            if isinstance(pin_location, str):
                pins_by_directory.setdefault(path.parent, {}).setdefault(normalize(pin_location), []).append(
                    pin_state.get("version"))
                pinned_locations[path].add(normalize(pin_location))
            state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
            version = state.get("version")
            if not identity:
                raise Unreadable(f"{path}: 名前のない依存がある")
            location = pin.get("location") or pin.get("repositoryURL")  # repositoryURL は古い形式（version 1）
            if not isinstance(version, str) or not version:
                errors.append(f"{source}: {identity} は版で固定されていない（ブランチやコミットの指定は認めない）")
            elif isinstance(location, str) and (identity, version, normalize(location)) in by_pin:
                pass  # identity・版・取得元のすべてが承認と一致する
            else:
                # identity と版が同じでも、取得元が承認したものと違えば認めない
                same = sorted(item["url"] for (name, pinned, _), item in by_pin.items() if (name, pinned) == (identity, version))
                if same:
                    errors.append(f"{source}: {identity} {version} の取得元 {location} が許可リスト（{'・'.join(same)}）と違う")
                else:
                    errors.append(f"{source}: {identity} {version} は許可リストにない")
    texts: Dict[Path, str] = {}
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
        except RecursionError as error:  # 深い入れ子の補間は RecursionError（JSON の読み込みと同じく読めないものとする）
            raise Unreadable(f"{path}: 入れ子が深すぎて読めない: {error!r}") from error
        errors.extend(f"{path.relative_to(root)}: {message}" for message in found)
        texts[path] = manifest
    errors.extend(transitive_errors(root, texts, by_url, approved_versions, pins_by_directory))
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
