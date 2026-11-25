#!/usr/bin/python3
"""設計文書の整合を検査する（docs/README.md §2〜§4、NFR-MNT-04）。

  1. ID の定義が重複していない。文書が参照する ID がすべて定義されている
  2. MVP の FR / NFR / SEC が、基本設計の「満たす要件」のどこかに現れる
  3. 相対リンクの先がある。コードフェンスに言語の指定がある。1 文書が 2,000 行未満である
  4. 基本設計・詳細設計の各文書に「セキュリティ」節と「テスト観点」節があり、各節の冒頭に要件 ID がある
  5. 詳細設計が、対応する基本設計の「詳細設計への申し送り」の項目をすべて表で解決している
     （対応する基本設計の名前に「-<名前>」を付けた詳細設計は本冊の分冊とし、節の冒頭とコード片の照合だけを求める）
  6. 詳細設計のコード片が実ファイルと一致する（<!-- file: パス --> は全文、<!-- excerpt: パス --> は連続した一部）

使い方: scripts/verify-docs.py [--root <リポジトリの起点>] [--update-snippets]
  --update-snippets  全文のコード片（<!-- file: パス -->）を実ファイルの内容で書き直してから検査する。
                     実ファイルを変えたあと、文書を同じ作業で直すために使う（一部のコード片は手で直す）
終了コード: 0 = 問題なし / 1 = 不整合あり / 2 = 読むもの（docs/・定義文書）がない
"""
import argparse
import calendar
import contextlib
import datetime
import html
import html.entities
import os
import re
import stat
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
MAX_LINES = 2000
STALE_MONTHS = 3  # 確認日から再確認までの月数（docs/README.md §4）

AREA_ID = r"(?:FR|NFR|SEC|TST|BD)-[A-Z]+-\d+"
PLAIN_ID = r"(?:CON|INV|OQ|UD|COST)-\d+"
# 省略した書き方: 範囲（〜）・並び（・）・スラッシュ区切りの並び（「/」の前後に空白 1 つまで）
SEPARATOR = r"(・|〜| ?/ ?)"
ID_PATTERN = re.compile(rf"\b({AREA_ID}|{PLAIN_ID})((?:(?:[・〜]| ?/ ?)\d+)*)")
GROUP_PATTERN = re.compile(r"\b(FR|NFR|SEC|TST)-([A-Z]+(?:・[A-Z]+)*) ?群")
# 定義の行の先頭の列（table_cells で前後の空白を除いた値）。太字（**…**）の ID も定義とする
DEFINITION_CELL = re.compile(rf"(?:\*\*)?({AREA_ID}|{PLAIN_ID})(?:\*\*)?")
# 申し送りの項目の行（「- 」の箇条書きと「1. 」の番号付き）。印の後ろを項目名とする
HANDOVER_ITEM = re.compile(r"^(?:- |\d+\. )(.*)$")
# 接頭辞ごとの定義文書（docs/README.md §3）
DEFINED_IN = {
    "FR": "docs/01-requirements/requirements.md",
    "NFR": "docs/01-requirements/requirements.md",
    "CON": "docs/01-requirements/requirements.md",
    "INV": "docs/01-requirements/requirements.md",
    "OQ": "docs/01-requirements/requirements.md",
    "SEC": "docs/01-requirements/security-requirements.md",
    "TST": "docs/01-requirements/test-requirements.md",
    "COST": "docs/01-requirements/dependencies-and-costs.md",
    "UD": "docs/README.md",
}
# 「満たす要件:」に書ける要件 ID の接頭辞（設計決定・未解決事項・オーナー決定は要件ではない）
REQUIREMENT_PREFIX = r"(FR|NFR|SEC|TST|CON|INV)-"
BASIC = "docs/02-basic-design"
# 設計決定（BD）を定義する節の番号（docs/README.md §3。各文書の §1、architecture.md は §3）
BD_SECTION = "1"
BD_SECTION_OF = {f"{BASIC}/architecture.md": "3"}
DETAILED = "docs/03-detailed-design"


Fence = Tuple[str, int, int]  # （文字, 長さ, 囲む箇条書きの項目の本文の位置）
# フェンスの字下げは空白 0〜3 個まで（CommonMark）。4 個以上の字下げの行はコードの本文として扱う
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
PHASES = re.compile(r"^(MVP|P2|P3)\b")
# sections() が節の本文でコードフェンスの行の代わりに置く行。空行と違って節の冒頭を終わらせ、
# 「|」で始まらないので表のまとまりも区切る（「- 」「### 」「満たす要件:」とも一致しない）
FENCE_LINE = "\0fence"
# リンク先の後に続けて書けるタイトル（"…"・'…'・(…)）
LINK_TITLE = re.compile(r"""\s+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))""")
# 参照形式のリンクの定義（[ラベル]: リンク先）。字下げは空白 0〜3 個まで（CommonMark）
REFERENCE_DEFINITION = re.compile(r"""^ {0,3}\[([^\]]+)\]:\s*(<[^<>\n]+>|\S+)""")
# 定義のラベルだけの行と、次の行に書いたリンク先（CommonMark。リンク先の後はタイトルだけを書ける）
REFERENCE_LABEL = re.compile(r"""^ {0,3}\[([^\]]+)\]:[ \t]*$""")
REFERENCE_DESTINATION = re.compile(r"""^[ \t]*(<[^<>\n]+>|[^\s<]\S*)""")
# 参照形式のリンクの使用（[文][ラベル] と、省略形の [ラベル][]）
REFERENCE_LINK = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
# 短縮形の参照のリンク（[ラベル]）。直後に「[」「(」が続くもの（ほかの形のリンク）・「]」の直後のもの（[文][ラベル] の後半）・
# エスケープした「[」・脚注（[^1]）は除く
SHORTCUT_LINK = re.compile(r"(?<![\\\]])\[(?!\^)([^\[\]]+)\](?![\[(])")
# インラインのコード（`…`・``…``。開始と同じ長さのバッククォートで閉じる）。中のリンクの書き方はリンクにならない
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
DETAILED_MAPPING = "要件 ID ↔ テスト"
# 対応表で検査・試験を書く列の見出しに含まれる語
TEST_COLUMN_WORDS = ("試験", "検査", "テスト")


# 行の区切りは LF・CRLF・CR だけ（str.splitlines は U+2028 などでも区切り、行の本文を分けてしまう）
LINE_BREAK = re.compile(r"\r\n|\r|\n")


def split_lines(text: str, keepends: bool = False) -> List[str]:
    """LF・CRLF・CR だけで行に分ける（str.splitlines と同じく、末尾の改行の後に空の行を作らない）。
    照合と --update-snippets の書き直しで同じ規則を使う。"""
    lines: List[str] = []
    start = 0
    for found in LINE_BREAK.finditer(text):
        lines.append(text[start:found.end() if keepends else found.start()])
        start = found.end()
    if start < len(text):
        lines.append(text[start:])
    return lines


def in_item(line: str, base: int) -> Optional[str]:
    """行から、囲む箇条書きの項目の本文の位置（base）までの字下げを除く。字下げが足りなければ（項目が終わる行なら）None。"""
    if not base:
        return line
    expanded = line.expandtabs(4)
    return None if expanded[:base].strip() else expanded[base:]


def fence_open(line: str, base: int = 0) -> Optional[Tuple[Fence, str]]:
    """フェンスの開始行なら（フェンス, 情報文字列）を返す。バッククォートのフェンスの情報文字列にはバッククォートを含められない。
    字下げは、囲む箇条書きの項目の本文の位置（base）から数える。"""
    content = in_item(line, base)
    found = FENCE_OPEN.match(content) if content is not None else None
    if not found:
        return None
    marker, info = found.group(1), found.group(2)
    if marker[0] == "`" and "`" in info:
        return None
    return (marker[0], len(marker), base), info.strip()


def fence_closes(line: str, fence: Fence) -> bool:
    """字下げが空白 0〜3 個で、開始と同じ文字が開始以上の長さで並び、その後が空白だけの行だけが、フェンスを閉じる。"""
    char, length, base = fence
    content = in_item(line, base)
    return content is not None and re.fullmatch(rf" {{0,3}}{re.escape(char)}{{{length},}}\s*", content) is not None


def fence_end(lines: List[str], start: int, fence: Fence) -> int:
    """lines[start] で開いたフェンスを閉じる行の位置を返す。閉じていなければ（閉じる前に囲む項目が終われば） len(lines)。"""
    end = start + 1
    while end < len(lines) and not fence_closes(lines[end], fence):
        if in_item(lines[end], fence[2]) is None:
            return len(lines)
        end += 1
    return end


def fences(text: str) -> Dict[int, Tuple[Fence, str]]:
    """フェンスの開始行の位置（0 始まり）→（フェンス, 情報文字列）。"""
    return {number - 1: opened for number, _, _, opened in fenced_lines(text) if opened}


# 複数行の HTML のコメントの始まり（字下げは空白 0〜3 個）
COMMENT_OPEN = re.compile(r"^ {0,3}<!--")
# 同じ行で閉じる HTML のコメント。表示されないので、中のリンクは確かめない（コード片の印の照合は印の行で別に行う）
INLINE_COMMENT = re.compile(r"<!--.*?-->")


def visible(line: str) -> str:
    """同じ行で閉じる HTML のコメントを除いた、表示される部分（「満たす要件」の ID・「—」の判定に使う）。"""
    return INLINE_COMMENT.sub("", line)
# 節（「## 」）と小節（「### 」）の見出し。字下げは空白 0〜3 個まで（CommonMark。4 個以上は字下げしたコードブロック）。
# 印と見出しの文の間は空白かタブ（CommonMark。「##\t節」も節として数え、「満たす要件:」の検査から漏らさない）。
# sections() と requirement_fields() が同じ判定を使う
HEADING = re.compile(r"^ {0,3}(#{2,3})[ \t](.*)$")


def heading(line: str) -> Optional[Tuple[int, str]]:
    """節・小節の見出しの行なら（段の深さ 2 か 3, 見出しの文）を返す。"""
    found = HEADING.match(line)
    return (len(found.group(1)), found.group(2).strip()) if found else None


# HTML のコードブロック（<pre>…</pre>）の始まりと終わり。中の行は表示どおりの文字で、Markdown（フェンス・表・リンク）にならない
HTML_PRE_OPEN = re.compile(r"^ {0,3}<pre(?:\s|>|$)", re.IGNORECASE)
HTML_PRE_CLOSE = re.compile(r"</pre\s*>", re.IGNORECASE)


def scanned_lines(text: str) -> Iterator[Tuple[int, str, bool, Optional[Tuple[Fence, str]], bool, bool]]:
    """各行を（行番号, 行, フェンス・コメント・<pre> の中か, この行で開いたフェンス, 複数行の HTML の
    コメントの中か, <pre> の中か）で返す。

    コードフェンスと複数行の HTML のコメント（同じ行で閉じない「<!--」から「-->」を含む行まで）の状態を同じ走査で追う。
    コメントの中のフェンスの印は、フェンスとして扱わない（コメントが閉じた後に通常の判定に戻る）。
    フェンスの中の「<!--」は、コードの本文として扱う。同じ行で閉じるコメント（コード片の印 <!-- file: … --> など）は含めない。
    HTML の <pre> ブロック（始まりと終わりの行を含む）は「中」とする。中のフェンスの印・「<!--」は文字のまま扱い、</pre> を含む
    行の次から通常の判定に戻る。
    """
    fence: Optional[Fence] = None
    comment = False
    pre = False
    code = IndentedCode()  # 箇条書きの項目の本文の位置（フェンスの字下げは、囲む項目の本文の位置から数える）
    for number, line in enumerate(split_lines(text), start=1):
        # 閉じる前に囲む項目が終われば、フェンスも終わる（CommonMark）
        if fence is not None and in_item(line, fence[2]) is None:
            fence = None
        if comment:
            comment = "-->" not in line
            yield number, line, True, None, True, False
            continue
        if fence is not None:
            if fence_closes(line, fence):
                fence = None
            yield number, line, True, None, False, False
            continue
        if pre or HTML_PRE_OPEN.match(line):
            pre = HTML_PRE_CLOSE.search(line) is None
            code.interrupt()
            yield number, line, True, None, False, True
            continue
        found = COMMENT_OPEN.match(line)
        if found and "-->" not in line[found.end():]:
            comment = True
            code.interrupt()
            yield number, line, True, None, True, False
            continue
        indent = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))
        opened = fence_open(line, code.base(indent)) if line.strip() else None
        if opened:
            fence = opened[0]
            code.leave(indent)
            code.interrupt()
        else:
            code.is_code(line)
        yield number, line, opened is not None, opened, False, False


def fenced_lines(text: str) -> Iterator[Tuple[int, str, bool, Optional[Tuple[Fence, str]]]]:
    """各行を（行番号, 行, 中か, この行で開いたフェンス）で返す。フェンスの開始・終了の行と、複数行の HTML のコメント・
    <pre> の行は「中」とする（本文として検査しない）。"""
    for number, line, inside, opened, _, _ in scanned_lines(text):
        yield number, line, inside, opened


def prose_lines(text: str) -> Iterator[Tuple[int, str]]:
    """コードフェンス・複数行の HTML のコメント・<pre> の外の行を、行番号つきで返す。"""
    for number, line, inside, _ in fenced_lines(text):
        if not inside:
            yield number, line


# 引用の印（字下げは空白 0〜3 個。直後の空白 1 つは印の一部）
QUOTE_MARK = re.compile(r"^ {0,3}> ?")


def strip_quote(line: str, limit: Optional[int] = None) -> Tuple[int, str]:
    """行頭の引用の印を（limit があればその数まで）除き、除いた数と残りを返す。"""
    depth = 0
    while limit is None or depth < limit:
        found = QUOTE_MARK.match(line)
        if not found:
            break
        line = line[found.end():]
        depth += 1
    return depth, line


def quoted_fences(text: str) -> Iterator[Tuple[int, Optional[str]]]:
    """引用の中のコードフェンスの行を（行番号, 開いた行なら情報文字列・本文と閉じる行なら None）で返す
    （ふつうのフェンス・コメント・<pre> の中の行は見ない）。

    開いたときと同じ深さの引用が続く間だけ、閉じるフェンスまでを中の行とする（引用が終わればフェンスも終わる。CommonMark）。
    """
    current: Optional[Tuple[int, Fence]] = None
    for number, line, inside, _, _, _ in scanned_lines(text):
        if inside:
            current = None
            continue
        if current is not None:
            depth, content = strip_quote(line, current[0])
            if depth == current[0]:
                if fence_closes(content, current[1]):
                    current = None
                yield number, None
                continue
            current = None
        depth, content = strip_quote(line)
        opened = fence_open(content) if depth else None
        if opened:
            current = (depth, opened[0])
            yield number, opened[1]


# 箇条書きの項目の始まり（字下げ・印・印の後の空白）と、段落を終わらせる行（ATX の見出し・下線の見出し・区切りの線）
LIST_ITEM = re.compile(r"^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+|$)")
PARAGRAPH_END = re.compile(r"^ {0,3}(?:#{1,6}(?:\s|$)|=+[ \t]*$|-+[ \t]*$|([-*_])(?:[ \t]*\1){2,}[ \t]*$)")


class IndentedCode:
    """字下げしたコードブロックの行かを、本文の行を順に受け取って判定する。

    空行などで段落が切れた後の、空白 4 個以上（タブは 4 桁）の行をコードとする。段落の続きの行は、字下げがあってもコードにしない。
    箇条書きの中では、項目の本文の位置からの字下げで判定する（項目の中の段落・入れ子の項目をコードと取り違えない）。
    """

    def __init__(self) -> None:
        self.paragraph = False  # 直前の行が段落で、次の行が続きになりうる
        self.items: List[int] = []  # 開いている箇条書きの項目の、本文の位置（外側から）

    def interrupt(self) -> None:
        self.paragraph = False

    def leave(self, indent: int) -> int:
        """字下げ indent より深い位置の項目を閉じ、残った項目の本文の位置を返す。"""
        while self.items and indent < self.items[-1]:
            self.items.pop()
        return self.items[-1] if self.items else 0

    def base(self, indent: int) -> int:
        """字下げ indent の行を囲む項目の本文の位置（項目を閉じない）。"""
        return max([at for at in self.items if at <= indent], default=0)

    def is_code(self, line: str) -> bool:
        expanded = line.expandtabs(4)
        if not expanded.strip():
            self.paragraph = False
            return False
        indent = len(expanded) - len(expanded.lstrip(" "))
        if not self.paragraph and indent - self.leave(indent) >= 4:
            return True
        ends = PARAGRAPH_END.match(expanded)
        # 見出し・区切りの線・引用などは段落の続きにならず、その字下げより深い項目を閉じる
        if ends or BLOCK_START.match(expanded):
            self.leave(indent)
        item = None if ends else LIST_ITEM.match(expanded)
        # 段落の続きの行は、項目の本文の位置から 4 個以上の字下げなら、項目の始まりではなく続き
        if item and not (self.paragraph and indent - self.base(indent) >= 4):
            self.leave(indent)
            spaces = len(item.group(3))
            self.items.append(indent + len(item.group(2)) + (spaces if 1 <= spaces <= 4 else 1))
            self.paragraph = bool(expanded[item.end():].strip())
            return False
        self.paragraph = not ends
        return False


def reference_definitions(text: str, skipped: Set[int]) -> Iterator[Tuple[int, str, str]]:
    """参照形式のリンクの定義（[ラベル]: リンク先）を（行番号, ラベル, リンク先）で返す。skipped の行（引用の中のフェンス）は本文として見ない。

    定義は段落の始まりにだけ書ける（段落の文の続きの、定義の形の行は定義にならない。CommonMark）。定義の直後の行と、定義の
    次の行に書いたタイトルの直後の行は、続けて定義を書ける。リンク先はラベルの次の行にも書ける（行番号はラベルの行）。
    """
    code = IndentedCode()
    previous = 0
    untitled = False  # 直前の行が、タイトルのない定義
    pending: Optional[Tuple[int, str]] = None  # 直前の行が、リンク先をまだ書いていない定義のラベル（行番号, ラベル）
    for number, line in prose_lines(text):
        if number != previous + 1:
            code.interrupt()
            untitled = False
            pending = None
        previous = number
        label, pending = pending, None
        if number in skipped or COMMENT_OPEN.match(line):
            code.interrupt()
            untitled = False
            continue
        if label is not None and not BLOCK_START.match(line) and not PARAGRAPH_END.match(line):
            destination = REFERENCE_DESTINATION.match(line)
            rest = line[destination.end():].rstrip() if destination else ""
            if destination and (not rest or LINK_TITLE.fullmatch(rest)):
                yield label[0], label[1], destination.group(1)
                code.interrupt()
                untitled = not rest
                continue
        starts = not code.paragraph
        found = REFERENCE_DEFINITION.match(line)
        if code.is_code(line):
            untitled = False
            continue
        bare = REFERENCE_LABEL.match(line)
        if bare and starts:
            pending = (number, bare.group(1))
            untitled = False
            continue
        if found and starts:
            # リンク先の後には完全なタイトルだけを書ける。ほかの文字が続く行は定義ではなく段落（CommonMark）
            rest = line[found.end():].rstrip()
            if not rest or LINK_TITLE.fullmatch(rest):
                yield number, found.group(1), found.group(2)
                code.interrupt()
                untitled = not rest
                continue
        title = LINK_TITLE.match(" " + line) if untitled else None
        if title and title.end() == len(line.rstrip()) + 1:
            code.interrupt()
        untitled = False


def reversed_range(match: "re.Match[str]") -> bool:
    """「FR-AAA-03〜01」のように、範囲の終点が直前の番号より小さい書き方か。"""
    previous = int(match.group(1).rsplit("-", 1)[1])
    for separator, digits in re.findall(rf"{SEPARATOR}(\d+)", match.group(2)):
        if separator == "〜" and int(digits) < previous:
            return True
        previous = int(digits)
    return False


def expand(match: "re.Match[str]") -> List[str]:
    """「SEC-SUP-01〜05」「NFR-MNT-01・03」「SEC-NET-03 / 04」のような省略した書き方を、個々の ID に展開する。

    範囲が逆順の書き方は、どの ID も参照したことにしない（空を返す。check_references が不正として報告する）。
    """
    if reversed_range(match):
        return []
    first = match.group(1)
    stem, number = first.rsplit("-", 1)
    width = len(number)
    identifiers = [first]
    previous = int(number)
    for separator, digits in re.findall(rf"{SEPARATOR}(\d+)", match.group(2)):
        value = int(digits)
        if separator == "〜":
            identifiers.extend(f"{stem}-{n:0{width}d}" for n in range(previous + 1, value + 1))
        else:
            identifiers.append(f"{stem}-{value:0{width}d}")
        previous = value
    return identifiers


# バックスラッシュでエスケープできる文字（CommonMark の ASCII の句読点）と、リンク先のエスケープ
ASCII_PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
# 文字参照（&amp;・&#35;・&#x23;。終わりの「;」が要る）も、エスケープと同じ 1 回の走査で戻す（「\\&amp;」は「&amp;」のまま残す）
LINK_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])|&(#[0-9]{1,7};|#[xX][0-9A-Fa-f]{1,6};|[A-Za-z][A-Za-z0-9]*;)")


def unescape_link(target: str) -> str:
    """リンク先の Markdown のエスケープと文字参照を戻す。定義のない名前の文字参照はそのまま残す（CommonMark）。"""
    def decode(found: "re.Match[str]") -> str:
        if found.group(1) is not None:
            return found.group(1)
        reference = found.group(2)
        if reference.startswith("#"):
            return html.unescape("&" + reference)
        return html.entities.html5.get(reference, found.group(0))

    return LINK_ESCAPE.sub(decode, target)


# コード片の印の行（<!-- file: パス --> と <!-- excerpt: パス -->）。パスは区切りの間の全体で、空白を含んでよい。
# 照合（check_snippets）と書き直し（update_snippets）が同じ読み取りを使う
SNIPPET_MARKER = re.compile(r"^<!--\s*(file|excerpt):(.*?)-->$")


def snippet_marker(line: str) -> Optional[Tuple[str, str]]:
    """コード片の印の行なら（種類, パス）を返す。パスが書かれていない印のパスは空文字列（check_snippets が不備として報告する）。"""
    found = SNIPPET_MARKER.match(line.strip())
    return (found.group(1), found.group(2).strip()) if found else None


def snippet_markers(text: str) -> Dict[int, Tuple[str, str]]:
    """コード片の印を、行の位置（0 始まり）→（種類, パス）で返す。照合と書き直しが同じ判定を使う。

    フェンス・複数行のコメント・<pre> の中と、字下げしたコードブロックの中の印は、説明用の例なので含めない。
    """
    markers: Dict[int, Tuple[str, str]] = {}
    code = IndentedCode()
    previous = 0
    for number, line in prose_lines(text):
        if number != previous + 1:
            code.interrupt()
        previous = number
        if code.is_code(line):
            continue
        if COMMENT_OPEN.match(line):
            code.interrupt()
        marker = snippet_marker(line)
        if marker:
            markers[number - 1] = marker
    return markers


class Opener:
    """開いたままの「[」。画像の「![」か、リンクの始まりになりうるか（中にリンクがあれば、なりえない）を持つ。"""

    def __init__(self, image: bool) -> None:
        self.image = image
        self.active = True


def link_destinations(line: str, openers: Optional[List[Opener]] = None,
                      following: Sequence[str] = ()) -> Tuple[List[str], List[Opener], List[str]]:
    """行の中のインラインのリンクのリンク先と、行末で開いたままの「[」と、リンク先とタイトルの中を空白にした行（following を含む）を返す。

    following は同じ段落の続きの行。この行で始まったリンクのタイトルと閉じる「)」は続きの行にも書ける（GFM と同じ）。
    続きの行で始まるリンクは読まない（呼び出し側が、空白にした続きの行をこの後に渡す）。

    参照形式のリンクは返した行から探す（リンク先・タイトルの中の「[文][ラベル]」は参照にならない。「(」「)」と位置は変えない）。

    リンクの文を閉じる「]」（エスケープされておらず、対になるエスケープされていない「[」がある）の直後の「(」だけをリンク先の
    始まりとする（「\\[例](missing.md)」はリンクにならない）。リンクの文は同じ段落の中で行をまたげるので、前の行から
    開いたままの「[」を openers で受け取る。リンクの中にリンクは入らないので、画像でないリンクが成立したら、それを囲む画像でない
    「[」はリンクの始まりにしない（画像を含むリンクは成立する。CommonMark と同じ）。リンク先は山括弧で囲んだもの（<…>）か、空白を含まず括弧の対が
    とれたもの（design(v2).md の「)」をリンクの終わりと取り違えない。CommonMark と同じ）。その後にタイトルを書ける。"""
    found: List[str] = []
    stack = list(openers or [])
    text = "\n".join([line, *following])
    rest = text
    position = 0
    previous = ""  # 直前に読んだ文字（エスケープは「\\」と続く文字を 1 回で読むので、エスケープした「!」は残らない）
    while position < len(line):
        char = text[position]
        before, previous = previous, char
        position += 2 if char == "\\" and text[position + 1:position + 2] in tuple(ASCII_PUNCTUATION) else 1
        if char == "[":
            stack.append(Opener(before == "!"))
        if char != "]" or not stack:
            continue
        opener = stack.pop()
        if not opener.active or not text.startswith("(", position):
            continue
        at = position + 1
        # 「(」の直後の空白と改行は読み飛ばす（CommonMark。「[設計]( missing.md)」もリンクとして確かめる。text は同じ段落の中だけ）
        while at < len(text) and text[at] in " \t\n":
            at += 1
        if text.startswith("<", at):
            close = text.find(">", at)
            if close < 0 or re.search(r"[<\n]", text[at + 1:close]) or close == at + 1:
                continue
            destination = text[at:close + 1]
            at = close + 1
        else:
            begin, depth = at, 0
            while at < len(text) and not text[at].isspace():
                # エスケープした文字（「\(」「\)」など）は括弧の対として数えない（CommonMark）。エスケープは check_link が戻す
                if text[at] == "\\" and at + 1 < len(text) and text[at + 1] in ASCII_PUNCTUATION:
                    at += 2
                    continue
                if text[at] == "(":
                    depth += 1
                elif text[at] == ")":
                    if depth == 0:
                        break
                    depth -= 1
                at += 1
            if depth != 0 or at == begin:
                continue
            destination = text[begin:at]
        title = LINK_TITLE.match(text, at)
        if title:
            at = title.end()
        while at < len(text) and text[at].isspace():
            at += 1
        if text.startswith(")", at):
            found.append(destination)
            rest = rest[:position + 1] + re.sub(r"[^\n]", " ", rest[position + 1:at]) + rest[at:]
            position = at + 1  # リンク先の中の括弧を、リンクの文の括弧と数えない
            previous = ""
            if not opener.image:
                for outer in stack:
                    outer.active = outer.active and outer.image
    return found, stack, rest.split("\n")


def table_cells(line: str) -> List[str]:
    """表の行を列に分ける。エスケープした「\\|」は列の区切りにしない（GFM と同じ）。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


SEPARATOR_CELL = re.compile(r":?-+:?")


UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
# 字下げしたコードブロックの行（空白 4 個以上かタブで始まる。CommonMark）。表の行として数えない
INDENTED_CODE = re.compile(r"^(?: {4}|\t)")


def is_row(line: str) -> bool:
    """表の行になりうる行か（エスケープしていない「|」を含む）。GFM と同じく、行頭・行末の「|」は省略できる。"""
    return UNESCAPED_PIPE.search(line) is not None


def is_separator(line: str) -> bool:
    """表の区切りの行（| --- | :-: |。行頭・行末の「|」は省略できる）か。
    「|」を 1 つ以上含み、各列の全体が「任意のコロン・1 個以上のハイフン・任意のコロン」の形であること。"""
    return is_row(line) and all(SEPARATOR_CELL.fullmatch(cell) for cell in table_cells(line))


# 新しいブロックの始まりの行（引用・ATX の見出し・箇条書き・番号付きの箇条書き。字下げは空白 0〜3 個）。
# GFM では表はこれらのブロックの始まりで終わるので、「|」を含んでいても表の行にしない
BLOCK_START = re.compile(r"^ {0,3}(?:>|#{1,6}(?:\s|$)|[-+*]\s|\d{1,9}[.)]\s)")


# 表を終わらせる行（空行・新しいブロックの始まり・HTML のコメントの始まり・区切りの線）。GFM と同じく、表は
# 見出しと区切りの行で始まった後、空行か別のブロックの始まりまで続く（「|」を含まない行もデータ行になる）
TABLE_END = re.compile(r"^\s*$|^ {0,3}<!--|^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")


def find_tables(lines: List[Tuple[int, str]]) -> List[Tuple[List[str], List[Tuple[int, str]]]]:
    """（行番号, 行）の並びから表を探し、表ごとに見出しの列とデータ行を返す（tables と table_blocks が共用する）。

    「|」を含む行の連続したまとまりの中で、区切りの行の直前の行を見出しとし、見出しと区切りの行の列の数が同じ場合だけ
    表として扱う（GFM でも、列の数が合わなければ表にならない）。見出しより前の行は表に含めない。
    表が始まった後は、空行・新しいブロックの始まりの行（引用・見出し・箇条書きなど）・HTML のコメントの始まり・区切りの線
    で終わり、それまでの行は「|」を含まなくてもデータ行とする（GFM と同じ）。区切りの形の行はデータ行から除く。
    区切りの行のないまとまり・列の数が合わないまとまりは表として数えない。
    """
    found: List[Tuple[List[str], List[Tuple[int, str]]]] = []
    block: List[Tuple[int, str]] = []
    failed = False  # このまとまりの最初の区切りの行で列の数が合わなかった（以後はまとまりが終わるまで表にしない）
    table: Optional[Tuple[List[str], List[Tuple[int, str]]]] = None
    for item in lines + [(0, "")]:
        text = item[1]
        if table is not None:
            if not TABLE_END.match(text) and not BLOCK_START.match(text):
                if not is_separator(text):
                    table[1].append(item)
                continue
            found.append(table)
            table = None
        # 字下げしたコードブロックの行は表の行にしない（コードの中の対応表の例を、表として数えない）。
        # 新しいブロック（引用など）の始まりの行と、HTML のコメントで始まる行（行の全体が HTML のブロック）は表の行にしない
        # （コメントの行を見出しに、続く区切りの行とデータ行を表と数えない。始まった表のセルの中のコメントには関わらない）
        if is_row(text) and not INDENTED_CODE.match(text) and not BLOCK_START.match(text) and not COMMENT_OPEN.match(text):
            block.append(item)
            if (not failed and len(block) >= 2 and is_separator(block[-1][1])
                    and not is_separator(block[-2][1])):
                if len(table_cells(block[-2][1])) == len(table_cells(block[-1][1])):
                    table = (table_cells(block[-2][1]), [])
                    block = []
                else:
                    failed = True
            continue
        block = []
        failed = False
    return found


def table_blocks(body: List[str]) -> List[Tuple[List[str], List[str]]]:
    """本文（節の行の並び）の中の表ごとに、見出しの列とデータ行を返す（find_tables と同じ判定）。"""
    # コードフェンスの代わりの行（FENCE_LINE）は空行と同じく表を終わらせる（「|」のない行をデータ行にしない）
    lines = [(number, "" if line == FENCE_LINE else line) for number, line in enumerate(body, start=1)]
    return [(header, [row for _, row in rows]) for header, rows in find_tables(lines)]


def tables(text: str) -> Iterator[Tuple[List[str], List[Tuple[int, str]]]]:
    """コードフェンスの外の表ごとに、見出しの列と、データ行（行番号, 行）を返す。

    コードフェンス・<pre> の中の行は表の候補のまとまりを区切る（またいだ行を 1 つの表にしない）。
    """
    hidden = commented_lines(text)
    lines = [(number, "" if inside or number in hidden else line) for number, line, inside, _ in fenced_lines(text)]
    yield from find_tables(lines)


def section_numbers(text: str) -> Dict[int, str]:
    """各行を含む節（「## 」。フェンスとコメントの中の見出しは数えない）の番号（「## 3. 設計決定」なら "3"）。
    番号のない節と最初の節より前の行は空文字列。"""
    result: Dict[int, str] = {}
    current = ""
    for number, line, inside, _, _, _ in scanned_lines(text):
        found = None if inside else heading(line)
        if found and found[0] == 2:
            numbered = re.match(r"(\d+)\.", found[1])
            current = numbered.group(1) if numbered else ""
        result[number] = current
    return result


def commented_lines(text: str) -> Set[int]:
    """複数行の HTML のコメントの行番号（scanned_lines と同じ判定）。表示されない内容なので、節・表の検査に数えない。"""
    return {number for number, _, _, _, commented, _ in scanned_lines(text) if commented}


def reference_label(label: str) -> str:
    """参照形式のリンクのラベルを照合用にそろえる（大文字小文字と連続した空白を区別しない。CommonMark）。"""
    return " ".join(label.split()).casefold()


def section_lead(body: List[str]) -> str:
    """節（「## 」）の冒頭の行（空行を除く最初の行）を返す。節が小節（「### 」）から始まる場合も小節の行で代えず、
    小節の見出しの行を返す（親の節は自身の「満たす要件:」を持つ。小節の「満たす要件:」は網羅の集計にだけ数える）。
    同じ行で閉じる HTML のコメントだけの行（表示されない）は空行と同じに読み飛ばす（requirement_fields と同じ）。"""
    return next((line for line in body if visible(line).strip()), "")


def fence_indent(line: str) -> int:
    """フェンスの開始行の字下げ（空白 0〜3 個）。"""
    return len(line) - len(line.lstrip(" "))


def dedent(line: str, indent: int) -> str:
    """CommonMark と同じく、フェンスの本文の行から開始行の字下げの分だけ（あるだけ）先頭の空白を除く。"""
    spaces = len(line) - len(line.lstrip(" "))
    return line[min(spaces, indent):]


def stale(confirmed: datetime.date, today: datetime.date) -> bool:
    """確認日から暦の STALE_MONTHS か月後の日（その月に同じ日がなければ月の最後の日）以降か。"""
    months = confirmed.month - 1 + STALE_MONTHS
    year, month = confirmed.year + months // 12, months % 12 + 1
    if year > datetime.MAXYEAR:
        return False
    return today >= datetime.date(year, month, min(confirmed.day, calendar.monthrange(year, month)[1]))


class Unresolvable(Exception):
    """パスを解決できない（循環するリンクなど）。"""


class Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.texts: Dict[str, str] = {}
        # 文書の名前 → 解決の経路を確かめた実体のパス（読み込み・読み直し・書き直しはこのパスだけを使う）
        self.paths: Dict[str, Path] = {}
        self.defined: Dict[str, str] = {}
        self.mvp: Set[str] = set()

    def error(self, path: str, message: str, line: Optional[int] = None) -> None:
        where = f"{path}:{line}" if line else path
        self.errors.append(f"{where}: {message}")

    def load(self) -> None:
        # 走査の失敗（読めないディレクトリ）は黙って読み飛ばさず、そのディレクトリで報告する（rglob は権限の失敗を
        # 黙って読み飛ばし、配下の文書を検査しないまま通すため使わない）。読めるほかの文書の検査は続ける
        def failed(error: OSError) -> None:
            where = Path(error.filename) if error.filename else self.root / "docs"
            relative = where.relative_to(self.root).as_posix() if where.is_relative_to(self.root) else str(where)
            self.error(relative, f"ディレクトリを読めない: {error.strerror or error}")

        paths: List[Path] = []
        for directory, _, files in os.walk(self.root / "docs", onerror=failed):
            paths.extend(Path(directory) / name for name in files if name.endswith(".md"))
        paths.sort()
        paths += [self.root / name for name in ("README.md", "CLAUDE.md") if (self.root / name).is_file()]
        for path in paths:
            relative = path.relative_to(self.root).as_posix()
            # 解決の経路（リンクの各段とその置き場所）と最後の実体が、起点の中の追跡されうる場所にない文書は読まない
            problem, real = self.resolve_document(path)
            if problem or real is None:
                self.error(relative, f"文書を読まない: {problem}")
                continue
            # 読めない文書は、その文書のパスで報告して読み飛ばし、ほかの文書の検査を続ける（検査の対象にも書き直しの対象にもしない）。
            # 読むのは確かめた実体のパス（元の綴りを開き直すと、確かめた後にリンクを差し替えられうる）
            try:
                self.texts[relative] = real.read_text(encoding="utf-8")
                self.paths[relative] = real
            except (OSError, UnicodeDecodeError) as error:
                self.error(relative, f"文書を読めない: {error}")

    # 起点の中で、Git が追跡しない場所（管理領域と、プッシュ前のフックの専用の作業ツリーで消さずに残す開発ツール・
    # ビルドの成果物）。専用の作業ツリーではこれら以外はすべて送信するコミットの追跡したファイルなので、文書の解決の
    # 経路がこれらの外にあれば、追跡したものだけをたどったことになる
    UNTRACKED_AREAS = ((".git",), (".tools",), (".build",), ("Packages", "MinutesKit", ".build"))
    MAX_LINK_HOPS = 40

    def outside_tracked(self, path: str) -> Optional[str]:
        """path（絶対パス）が起点の外か、追跡しない場所の中なら理由を返す。"""
        root = self.root.resolve()
        candidate = Path(path)
        if not candidate.is_relative_to(root):
            return f"{path} が起点の外にある"
        parts = candidate.relative_to(root).parts
        for area in self.UNTRACKED_AREAS:
            if parts[:len(area)] == area:
                return f"{path} が追跡しない場所（{'/'.join(area)}）にある"
        return None

    def resolve_document(self, path: Path) -> Tuple[Optional[str], Optional[Path]]:
        """文書を読む前に、解決の経路を確かめる。（問題, 実体のパス）を返す。問題がなければ問題は None。

        経路の要素は、ファイルシステムがたどる順に 1 つずつ確かめる（「..」を字面で先に取り除くと、シンボリックリンクの
        ディレクトリの後の「..」が実際とは別の場所を指しうる）。各要素とリンクの各段の行き先が、起点の中の追跡されうる場所
        （outside_tracked）にあることを求める。絶対パスのリンク先は、起点の実体のパスの中を指すものだけを認める。
        最後の実体は通常のファイルであること。
        """
        root = str(self.root.resolve())
        hops = 0

        class Rejected(Exception):
            pass

        def walk(current: str, parts: List[str]) -> str:
            nonlocal hops
            for part in parts:
                if part in ("", "."):
                    continue
                if part == "..":
                    # 通常のファイルなどの「..」はファイルシステムではたどれないので、字面だけで親に戻さない
                    try:
                        if not stat.S_ISDIR(os.lstat(current).st_mode):
                            raise Rejected(f"{current} がディレクトリでないのに「..」でたどっている")
                    except OSError as error:
                        raise Rejected(f"{current} を調べられない: {error.strerror or error}") from error
                    current = os.path.dirname(current)
                    if current != root and not current.startswith(root + os.sep):
                        raise Rejected(f"{current} が起点の外にある")
                    continue
                candidate = os.path.join(current, part)
                reason = self.outside_tracked(candidate)
                if reason:
                    raise Rejected(reason)
                try:
                    info = os.lstat(candidate)
                except OSError as error:
                    raise Rejected(f"{candidate} を調べられない: {error.strerror or error}") from error
                if stat.S_ISLNK(info.st_mode):
                    hops += 1
                    if hops > self.MAX_LINK_HOPS:
                        raise Rejected(f"{path} のシンボリックリンクが {self.MAX_LINK_HOPS} 段を超える（循環など）")
                    try:
                        target = os.readlink(candidate)
                    except OSError as error:
                        # lstat の失敗と同じく、その文書を読まない理由として報告し、ほかの文書の検査は続ける
                        raise Rejected(f"{candidate} のリンク先を読めない: {error.strerror or error}") from error
                    if os.path.isabs(target):
                        if target != root and not target.startswith(root + os.sep):
                            raise Rejected(f"{candidate} のリンク先 {target} が起点の外にある")
                        current = walk(root, target[len(root):].split("/"))
                    else:
                        current = walk(current, target.split("/"))
                    continue
                current = candidate
            return current

        try:
            final = walk(root, list(path.relative_to(self.root).parts))
            reason = self.outside_tracked(final)
            if reason:
                raise Rejected(reason)
            try:
                info = os.lstat(final)
            except OSError as error:
                raise Rejected(f"{final} を調べられない: {error.strerror or error}") from error
            if not stat.S_ISREG(info.st_mode):
                raise Rejected(f"{final} が通常のファイルでない")
        except Rejected as rejected:
            return str(rejected), None
        return None, Path(final)

    def collect_definitions(self) -> None:
        """定義文書の表から ID を集める。要件表の「フェーズ」列が MVP の FR / NFR / SEC も集める。"""
        for name, text in self.texts.items():
            # 定義は、見出しの行と区切りの行を確かめた表のデータ行からだけ集める。フェーズの列は、その表の見出しで決める
            section_of = section_numbers(text)
            for header, rows in tables(text):
                phase_column = header.index("フェーズ") if "フェーズ" in header else -1
                masvs_column = header.index("MASVS") if "MASVS" in header else -1
                for number, line in rows:
                    self.collect_definition(name, number, line, phase_column, masvs_column, section_of.get(number, ""))

    def collect_definition(self, name: str, number: int, line: str, phase_column: int, masvs_column: int,
                           section: str) -> None:
        """表のデータ行 1 行から ID の定義を集める（collect_definitions から呼ぶ）。section は行を含む節（「## 」）の番号。"""
        cells = table_cells(line)
        # 先頭の列の空白の有無（|FR-AAA-01| と | FR-AAA-01 |）によらず、正規化した値で判定する
        found = DEFINITION_CELL.fullmatch(cells[0]) if cells else None
        if not found:
            return
        identifier = found.group(1)
        prefix = identifier.split("-", 1)[0]
        home = DEFINED_IN.get(prefix, BASIC if prefix == "BD" else "")
        if not (name == home or (prefix == "BD" and name.startswith(BASIC + "/"))):
            return
        # 基本設計では、設計決定の節（各文書の §1、architecture.md は §3）の表で、先頭の列が ID の行だけを定義として扱う
        if prefix == "BD" and (cells[0] != identifier or section != BD_SECTION_OF.get(name, BD_SECTION)):
            return
        if identifier in self.defined:
            self.error(name, f"{identifier} の定義が重複している（{self.defined[identifier]}）", number)
            return
        self.defined[identifier] = f"{name}:{number}"
        if prefix in ("FR", "NFR", "SEC"):
            # FR / NFR / SEC はフェーズ（MVP / P2 / P3）を必ず持つ（docs/README.md §3）
            phase = cells[phase_column] if 0 <= phase_column < len(cells) else ""
            if not PHASES.match(phase):
                what = "フェーズの列がない" if phase_column < 0 else f"フェーズ「{phase}」を MVP・P2・P3 として読めない"
                self.error(name, f"{identifier} の{what}", number)
            elif phase.startswith("MVP"):
                self.mvp.add(identifier)
        if prefix == "SEC":
            # SEC は MASVS 分類も必ず持つ（docs/README.md §3）。表示されないコメントだけの値は空とする
            masvs = cells[masvs_column] if 0 <= masvs_column < len(cells) else ""
            if not INLINE_COMMENT.sub("", masvs).strip():
                what = "の列がない" if masvs_column < 0 else "が空"
                self.error(name, f"{identifier} の MASVS 分類{what}", number)

    def oversized_range(self, match: "re.Match[str]") -> bool:
        """範囲（〜）の要素数が、同じ接頭辞の定義済み ID の数を超えるか（巨大な範囲を展開しないため）。"""
        stem, number = match.group(1).rsplit("-", 1)
        limit = sum(1 for identifier in self.defined if identifier.rsplit("-", 1)[0] == stem)
        previous = int(number)
        for separator, digits in re.findall(rf"{SEPARATOR}(\d+)", match.group(2)):
            if separator == "〜" and int(digits) - previous > limit:
                return True
            previous = int(digits)
        return False

    def expand_ids(self, match: "re.Match[str]") -> List[str]:
        """expand と同じ。ただし広すぎる範囲は展開しない（check_references が不正として報告する）。"""
        return [] if self.oversized_range(match) else expand(match)

    def check_references(self) -> None:
        prefixes = {identifier.rsplit("-", 1)[0] for identifier in self.defined}
        for name, text in self.texts.items():
            quoted = dict(quoted_fences(text))
            for number, line, inside, _, _, pre in scanned_lines(text):
                # 引用の中のコードフェンスの行は、ふつうのフェンスの中と同じく参照として数えない（地の文の引用は確かめる）
                if number in quoted:
                    continue
                # <pre> の中は Markdown にならないが文字は表示されるので、ID の参照は確かめる
                if inside and not pre:
                    continue
                # 同じ行で閉じる HTML のコメントは表示されないので、中の ID を参照として数えない（複数行のコメントは
                # 上で除く）。visible と同じ置き換えで、インラインのコード（`…`）の中の ID は検査に残す
                line = INLINE_COMMENT.sub("", line)
                for match in ID_PATTERN.finditer(line):
                    if reversed_range(match):
                        self.error(name, f"逆順の範囲: {match.group(0)}（終点が直前の番号より小さい）", number)
                        continue
                    if self.oversized_range(match):
                        self.error(name, f"広すぎる範囲: {match.group(0)}（同じ接頭辞の定義済みの ID の数を超える）", number)
                        continue
                    for identifier in expand(match):
                        if identifier not in self.defined:
                            self.error(name, f"定義されていない ID: {identifier}", number)
                for match in GROUP_PATTERN.finditer(line):
                    for area in match.group(2).split("・"):
                        if f"{match.group(1)}-{area}" not in prefixes:
                            self.error(name, f"定義されていない ID の群: {match.group(1)}-{area}", number)

    def check_coverage(self) -> None:
        """MVP の要件が、基本設計の「満たす要件」の行のどこかに現れること。

        索引（README.md）は「SEC 全般」や群の表記で全体をまとめて挙げるため、判定に含めると
        各文書の節で挙げていない要件の欠落を隠す。各文書の節の行だけで判定する。
        """
        covered: Set[str] = set()
        for name, text in self.texts.items():
            if not name.startswith(BASIC + "/") or name.endswith("/README.md"):
                continue
            for line in self.requirement_fields(text):
                # names_requirements と同じく「／」の前だけを見る（後ろの決定・関連の参照は網羅に数えない）
                line = line.split("／", 1)[0]
                for match in ID_PATTERN.finditer(line):
                    covered.update(self.expand_ids(match))
                for match in GROUP_PATTERN.finditer(line):
                    for area in match.group(2).split("・"):
                        stem = f"{match.group(1)}-{area}-"
                        covered.update(identifier for identifier in self.defined if identifier.startswith(stem))
                if "SEC 全般" in line:
                    covered.update(identifier for identifier in self.defined if identifier.startswith("SEC-"))
        for identifier in sorted(self.mvp - covered):
            self.error(BASIC, f"MVP の要件 {identifier} が、どの文書の「満たす要件」にも現れない")

    @staticmethod
    def requirement_fields(text: str) -> List[str]:
        """正式な「満たす要件」の欄の行を返す。

        数えるのは、文書の冒頭（最初の「## 」「### 」より前）の表（見出しの行と区切りの行を持つもの）の、先頭の列が
        「満たす要件 ID」のデータ行と、見出し（「## 」「### 」）の
        直後の最初の本文行にある「満たす要件:」の行だけ（節の冒頭の検査と同じ位置。節の後半にある同名の行や
        「満たす要件の例」などの説明文は数えない）。「## 」の直後が「### 」なら、その小節の冒頭を見る。
        """
        fields: List[str] = []
        # 冒頭の終わり（最初の見出しの行番号）。見出しがなければ文書の全体が冒頭
        head_end = next((number for number, line in prose_lines(text) if heading(line)), None)
        # 表の見出しの行だけ・区切りの行のない行など、表として成り立たない「| 満たす要件 ID |」の行は数えない。
        # 数えるのは値の列（2 列目）の表示される内容だけ。値の列のない表（1 列）の行と、見出しの列の数を超える余分な列
        # （GFM は表示しない）は数えない
        for header, rows in tables(text):
            if len(header) < 2:
                continue
            for number, line in rows:
                if head_end is not None and number >= head_end:
                    continue
                # 同じ行のコメントの中の ID は表示されないので数えない
                cells = table_cells(visible(line))
                if cells[0] == "満たす要件 ID" and len(cells) >= 2:
                    fields.append(cells[1])
        after_heading = False
        for _, line, inside, _, commented, _ in scanned_lines(text):
            if commented:
                continue  # 複数行の HTML のコメントは空行と同じに扱う（sections() と同じ。中の「満たす要件:」は数えない）
            if inside:
                after_heading = False  # コードブロックは節の冒頭を終わらせる（sections() と同じ境界）
                continue
            if heading(line):
                after_heading = True
                continue
            # 空行と、同じ行で閉じる HTML のコメントだけの行（表示されない。section_lead と同じ）は節の冒頭を終わらせない
            if not visible(line).strip():
                continue
            if after_heading and line.startswith("満たす要件:"):
                fields.append(visible(line))
            after_heading = False
        return fields

    def check_format(self) -> None:
        for name, text in self.texts.items():
            lines = split_lines(text)
            if len(lines) >= MAX_LINES:
                self.error(name, f"{len(lines)} 行ある（{MAX_LINES} 行未満に分割する）")
            for number, _, _, opened in fenced_lines(text):
                if opened is None:
                    continue
                if not opened[1]:
                    self.error(name, "コードフェンスに言語の指定がない", number)
                if fence_end(lines, number - 1, opened[0]) >= len(lines):
                    self.error(name, "コードフェンスが閉じていない", number)
            # 引用の中のフェンスは、言語の指定だけを確かめる（閉じていなくても、引用の終わりで終わる）
            quoted = dict(quoted_fences(text))
            for number, info in quoted.items():
                if info == "":
                    self.error(name, "コードフェンスに言語の指定がない（引用の中）", number)
            # 参照形式のリンクは、定義（[ラベル]: リンク先）を通してリンク先を求め、インラインのリンクと同じく確かめる
            definitions: Dict[str, str] = {}
            definition_lines: Set[int] = set()
            for number, label, destination in reference_definitions(text, set(quoted)):
                definition_lines.add(number)
                definitions.setdefault(reference_label(label), destination)
            rows = {number for _, block in tables(text) for number, _ in block}
            # 確かめる行（行番号, 本文, 前の行と同じ段落の続きか）。先に集め、リンクのタイトルを同じ段落の続きの行から読めるようにする
            entries: List[Tuple[int, str, bool]] = []
            previous = 0  # 前の行の番号
            continued = False  # 前の行が段落の途中で、次の行に続きうる
            code = IndentedCode()
            for number, line in prose_lines(text):
                # 段落の切れ目（空行・フェンスなどで番号が続かない・新しいブロックの始まり）をまたいでリンクの文は続かない
                starts = number != previous + 1 or not line.strip() or BLOCK_START.match(line) is not None or not continued
                if number != previous + 1:
                    code.interrupt()
                previous = number
                continued = False
                # 引用の中のコードフェンスの行はリンクとして確かめない（言語の指定は上で確かめた）
                if number in quoted:
                    code.interrupt()
                    continue
                # 字下げしたコードブロックの中はリンクとして確かめない（段落の続きの行・箇条書きの中の段落は確かめる）
                if code.is_code(line):
                    continue
                # コメントで始まる行は、行の全体が HTML のブロックで、閉じた後ろの文字もリンクにならない（CommonMark）。
                # 段落も切れる。コード片の印の照合は check_snippets が別に行う
                if COMMENT_OPEN.match(line):
                    code.interrupt()
                    continue
                # 見出し・区切りの線・表の行は 1 行で終わり、次の行に続かない
                alone = PARAGRAPH_END.match(line) is not None or number in rows or is_separator(line) or not line.strip()
                continued = not alone
                line = CODE_SPAN.sub("", line)  # インラインのコードの中はリンクとして確かめない
                line = INLINE_COMMENT.sub("", line)  # 同じ行で閉じるコメントの中も表示されないので確かめない
                entries.append((number, line, not (starts or alone)))
            openers: List[Opener] = []  # 同じ段落の前の行から開いたままの「[」
            pending: List[str] = []  # 同じ段落の続きの行（前の行のリンクの一部を空白にしたもの）
            for at, (number, line, continues) in enumerate(entries):
                if continues:
                    line = pending.pop(0)
                else:
                    openers = []
                    pending = []
                    for _, later, joined in entries[at + 1:]:
                        if not joined:
                            break
                        pending.append(later)
                targets, openers, (line, *pending) = link_destinations(line, openers, pending)
                for text_part, label in REFERENCE_LINK.findall(line):
                    # 定義のないラベルはリンクにならない（GFM でもそのまま文字として表示される）
                    target = definitions.get(reference_label(label or text_part))
                    if target is not None:
                        targets.append(target)
                # 定義の行の [ラベル] は使用ではない。定義のないラベルはリンクにならない
                for label in [] if number in definition_lines else SHORTCUT_LINK.findall(line):
                    target = definitions.get(reference_label(label))
                    if target is not None:
                        targets.append(target)
                for target in targets:
                    self.check_link(name, target, number)

    def check_link(self, name: str, target: str, number: int) -> None:
        """リンク先（外部の URL・節へのリンクを除く）が、文書からの相対パスで存在すること。"""
        # 山括弧で囲んだリンク先（空白を含められる）は、山括弧を除いてから解決する
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        # Markdown のエスケープ（「\(」など）を戻してから解決する（エスケープした括弧と、しない括弧を同じファイル名として扱う）。
        # パーセント符号化は、その後でこれまでどおり戻す
        # 文字参照も同時に戻す
        target = unescape_link(target)
        # URL の scheme は大文字小文字を区別しない（RFC 3986）。パスの大文字小文字は区別したまま
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("#"):
            return
        # パーセント符号化（%20 など）は、節へのリンクを分けた後に戻してから解決する（GitHub の表示と同じく、符号化した
        # 名前のリンクも実際のファイルを指す）
        # クエリ（?plain=1 など）もファイル名ではない。符号化した %23・%3F は名前の文字なので、分けた後に戻す
        path = (self.root / name).parent / urllib.parse.unquote(re.split("[?#]", target, maxsplit=1)[0])
        if not path.exists():
            self.error(name, f"リンクの先がない: {target}", number)

    def sections(self, text: str) -> List[Tuple[int, str, List[str]]]:
        """「## 」の節ごとに、行番号・見出し・本文の行を返す。"""
        result: List[Tuple[int, str, List[str]]] = []
        hidden = commented_lines(text)
        for number, line, inside, _ in fenced_lines(text):
            if number in hidden:
                # 複数行の HTML のコメントの中は表示されないので、空行として扱う（節の見出し・表として数えない）
                if result:
                    result[-1][2].append("")
                continue
            if inside:
                # フェンスの中は検査の対象外。位置は FENCE_LINE として残し、フェンスをまたいだ行を 1 つの表として扱わず、
                # 節の冒頭のコードブロックの後ろの行を節の冒頭として扱わない
                if result:
                    result[-1][2].append(FENCE_LINE)
                continue
            found = heading(line)
            if found and found[0] == 2:
                result.append((number, found[1], []))
            elif result:
                result[-1][2].append(line)
        return result

    def check_design_documents(self) -> None:
        for name, text in self.texts.items():
            # 配下のディレクトリの文書も対象にする（load は docs/ を再帰的に読む）
            directory = next((base for base in (BASIC, DETAILED) if name.startswith(base + "/")), "")
            if not directory:
                continue
            sections = self.sections(text)
            for number, heading, body in sections:
                first = section_lead(body)
                if not first.startswith("満たす要件:"):
                    self.error(name, f"節「{heading}」の冒頭に「満たす要件:」の行がない", number)
                elif not self.names_requirements(first):
                    self.error(name, f"節「{heading}」の「満たす要件:」に要件 ID も「—」の指定もない", number)
            # 索引と分冊（長いコード片を置く本冊の付録）には必須の節と申し送りを求めないが、コード片は照合する
            if name.endswith("/README.md") or (directory == DETAILED and self.part_of(name)):
                if directory == DETAILED:
                    self.check_snippets(name, text)
                continue
            # コメントの中の語は表示されないので、必須の節の見出しと見なさない
            headings = [visible(heading) for _, heading, _ in sections]
            required = ["セキュリティ", "テスト観点"]
            if directory == DETAILED:
                required += ["申し送り", "要件 ID ↔ テスト"]
            for word in required:
                if not any(word in heading for heading in headings):
                    self.error(name, f"「{word}」の節がない")
            if directory == DETAILED:
                self.check_handover(name, sections)
                self.check_mapping(name, sections)
                self.check_snippets(name, text)

    def part_of(self, name: str) -> Optional[str]:
        """詳細設計 name が分冊なら、本冊（同じ名前の基本設計に対応する詳細設計）の名前を返す。分冊でなければ None。

        分冊は、対応する基本設計がなく、基本設計の名前（拡張子を除く）に「-<名前>」を付けた名前の文書で、
        本冊の詳細設計があるものに限る（本冊のない分冊は、対応する基本設計がない文書として check_handover が報告する）。
        """
        relative = name[len(DETAILED) + 1:]
        if f"{BASIC}/{relative}" in self.texts or not relative.endswith(".md"):
            return None
        # 配下のディレクトリの基本設計も、相対パスのまま候補にする（sub/foo.md の分冊は sub/foo-<名前>.md）
        stems = [basic[len(BASIC) + 1:-len(".md")] for basic in self.texts
                 if basic.startswith(BASIC + "/") and basic.endswith(".md")]
        for stem in sorted(stems, key=len, reverse=True):
            # 分冊は本冊と同じディレクトリにあること（foo-x/bar.md を foo.md の分冊と見なさない）
            if (relative.startswith(stem + "-") and "/" not in relative[len(stem) + 1:]
                    and f"{DETAILED}/{stem}.md" in self.texts):
                return f"{DETAILED}/{stem}.md"
        return None

    def check_mapping(self, name: str, sections: List[Tuple[int, str, List[str]]]) -> None:
        """「要件 ID ↔ テスト」の節に対応表があり、要件 ID とテストの両方を持つ行が 1 行以上あること。

        見出しだけがあって表のない節を合格にしない。行は、先頭の列に定義済みの要件 ID があり、
        検査・試験の列（先頭以外で、見出しに「試験」「検査」「テスト」を含む列）が空でないものを数える。
        「実行する場所」「状態」などほかの列の値だけでは数えない。検査・試験の列のない表の行は数えない。
        各表の見出しの行と区切りの行は除く。
        """
        for number, heading, body in sections:
            if DETAILED_MAPPING not in heading:
                continue
            for header, rows in table_blocks(body):
                columns = [at for at, cell in enumerate(header)
                           if at > 0 and any(word in cell for word in TEST_COLUMN_WORDS)]
                for line in rows:
                    cells = table_cells(line)
                    # 同じ行で閉じるコメントは表示されないので、コメントだけの検査・試験の列は空と見なす
                    if not any(at < len(cells) and visible(cells[at]).strip() for at in columns):
                        continue
                    # 先頭の列は個々の要件 ID を持つこと（群の表記・「SEC 全般」は対応の行として数えない）
                    # 同じ行で閉じるコメントの中の ID は表示されないので数えない
                    if self.names_individual_requirement(visible(cells[0])):
                        return
            self.error(name, f"「{heading}」の節に、要件 ID とテストの両方を持つ対応表の行がない", number)

    def names_individual_requirement(self, text: str) -> bool:
        """定義済みの要件 ID（FR・NFR・SEC・TST・CON・INV）を個別に 1 つ以上含むか。省略した書き方は展開して照合する。"""
        return any(identifier in self.defined and re.match(REQUIREMENT_PREFIX, identifier)
                   for match in ID_PATTERN.finditer(text) for identifier in self.expand_ids(match))

    def names_requirements(self, line: str) -> bool:
        """「満たす要件:」の後（「／」の前まで）に、定義済みの要件 ID・要件の群・「SEC 全般」・「—」（満たす要件なし）のどれかがあるか。"""
        # 同じ行のコメントの中の ID・「—」は表示されないので数えない
        spec = visible(line).split(":", 1)[1].split("／", 1)[0].strip()
        if spec.startswith("—") or "SEC 全般" in spec:
            return True
        for match in ID_PATTERN.finditer(spec):
            if any(identifier in self.defined and re.match(REQUIREMENT_PREFIX, identifier)
                   for identifier in self.expand_ids(match)):
                return True
        prefixes = {identifier.rsplit("-", 1)[0] for identifier in self.defined}
        return any(
            f"{match.group(1)}-{area}" in prefixes for match in GROUP_PATTERN.finditer(spec) for area in match.group(2).split("・")
        )

    def check_handover(self, name: str, sections: List[Tuple[int, str, List[str]]]) -> None:
        """基本設計の申し送りの各項目が、詳細設計の解決の表の先頭の列に、項目名で一度ずつ現れること。

        行数だけを比べると、重複した行が欠落した項目を埋めて合格になるため、項目名で照合する。
        項目名は前後の空白と末尾の「。」を除いて比べる。
        """
        basic_name = f"{BASIC}/{name[len(DETAILED) + 1:]}"
        if basic_name not in self.texts:
            self.error(name, f"対応する基本設計 {basic_name} がない")
            return

        def item_name(text: str) -> str:
            return text.strip().rstrip("。").strip()

        items = [
            item_name(found.group(1)) for _, heading, body in self.sections(self.texts[basic_name]) if "申し送り" in heading
            for found in (HANDOVER_ITEM.match(line) for line in body) if found
        ]
        # 表ごとに（連続した「|」の行のまとまりごとに）区切り、見出しと区切りの行を持つ表のデータ行だけを数える。
        # 解決の列（2 列目。見出しに「解決」を含む）を持つ表の行だけを数える（1 列の表の本文の行に余分な列があっても、
        # 2 列目が「担当者」など別の列の表でも、解決と見なさない）
        rows: List[str] = []
        for _, heading, body in sections:
            if "申し送り" in heading:
                rows.extend(row for header, block in table_blocks(body) if len(header) >= 2 and "解決" in header[1]
                            for row in block)
        # 解決の列（2 列目）が空・空白だけ・コメントだけ（表示されない）の行は、解決したことにしない
        resolved = []
        for line in rows:
            cells = table_cells(line)
            if len(cells) < 2 or not visible(cells[1]).strip():
                self.error(name, f"申し送り「{item_name(cells[0])}」の解決の列が空")
                continue
            resolved.append(item_name(cells[0]))
        for item in items:
            found = resolved.count(item)
            if found == 0:
                self.error(name, f"申し送り「{item}」の解決が表にない（表の先頭の列に基本設計の項目名をそのまま書く）")
            elif found > 1:
                self.error(name, f"申し送り「{item}」の解決が表に {found} 行ある")
        for row in dict.fromkeys(resolved):
            if row not in items:
                self.error(name, f"基本設計にない申し送り「{row}」の行が表にある")

    def check_snippets(self, name: str, text: str) -> None:
        lines = split_lines(text)
        opened_fences = fences(text)
        for index, (kind, source) in snippet_markers(text).items():
            if not source:
                self.error(name, "コード片の印にパスがない", index + 1)
                continue
            start = index + 1
            while start < len(lines) and not lines[start].strip():
                start += 1
            if start not in opened_fences:
                self.error(name, "印の直後にコードフェンスがない", index + 1)
                continue
            end = fence_end(lines, start, opened_fences[start][0])
            # 字下げしたフェンスの本文は、開始行の字下げの分を除いてから照合する
            indent = fence_indent(lines[start])
            snippet = [dedent(line, indent) for line in lines[start + 1:end]]
            try:
                path = self.snippet_source(source)
            except Unresolvable as error:
                self.error(name, f"コード片の元のファイルのパスを解決できない: {error}", index + 1)
                continue
            if path is None:
                self.error(name, f"コード片の元のファイルが --root の外にある: {source}", index + 1)
                continue
            if not path.is_file():
                self.error(name, f"コード片の元のファイルがない: {source}", index + 1)
                continue
            try:
                actual = split_lines(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as error:
                self.error(name, f"コード片の元のファイルを読めない: {source}: {error}", index + 1)
                continue
            if kind == "file":
                matches = snippet == actual
            else:
                size = len(snippet)
                matches = size > 0 and any(actual[at:at + size] == snippet for at in range(len(actual) - size + 1))
            if not matches:
                self.error(name, f"コード片が {source} と一致しない", index + 1)

    def snippet_source(self, relative: str) -> Optional[Path]:
        """コード片の元のファイルのパス。シンボリックリンクを解決した後も --root の中にある場合だけ返す。"""
        root = self.root.resolve()
        try:
            path = (root / relative).resolve()
        except (OSError, RuntimeError) as error:  # Python 3.9〜3.12 は循環するリンクで RuntimeError を送出する
            raise Unresolvable(f"{relative}: {error}") from error
        if not path.is_relative_to(root):
            return None
        # ないもの・通常のファイルでないものは、呼び出し側がこれまでどおり報告する
        if not path.is_file():
            return path
        # 文書と同じく、解決の経路が追跡しない場所（専用の作業ツリーに残す .tools など）を通るものは、検査する入力の内容では
        # ないので使わない（照合と書き直しの両方）。読むのは経路を確かめた実体
        # 絶対パスも「..」を字面で取り除かず、起点の下の要素をそのまま渡す（resolve_document が 1 つずつ確かめる）
        parts = Path(relative)
        if parts.is_absolute():
            if not parts.is_relative_to(root):
                raise Unresolvable(f"{relative}: 起点の外を経由する絶対パス")
            parts = parts.relative_to(root)
        problem, real = self.resolve_document(self.root / parts)
        if problem or real is None:
            raise Unresolvable(f"{relative}: {problem}")
        return real

    def snippet_order(self) -> List[str]:
        """全文のコード片を書き直す詳細設計の名前を、引用する側より引用される側を先にして返す。

        先に書き直さないと、書き直す前の内容を写した文書が残り、1 回の書き直しでそろわない。順序を決めるのは全文のコード片の
        元のファイルが詳細設計の文書の場合だけ（スクリプトなどの元のファイルは書き直さない）。互いに引用する文書はどの順でも
        そろわないので、循環として報告し、書き直さない。同じ実体を指す名前（リンクの文書）は 1 つの文書として扱う。
        """
        names: Dict[Path, List[str]] = {}
        for name in sorted(self.texts):
            if name.startswith(DETAILED + "/"):
                names.setdefault(self.paths[name], []).append(name)
        sources: Dict[Path, List[Path]] = {}
        for document, aliases in names.items():
            sources[document] = []
            for kind, source in snippet_markers(self.texts[aliases[0]]).values():
                try:
                    path = self.snippet_source(source) if kind == "file" and source else None
                except Unresolvable:
                    continue  # 検査の側（check_snippets）が印の行とともに報告する
                if path in names:
                    sources[document].append(path)
        # 自身に到達できる文書が循環に属する（処理の順序によらず、引用の関係だけで決める）
        reachable: Dict[Path, Set[Path]] = {}
        for document in names:
            reachable[document] = set()
            waiting = list(sources[document])
            while waiting:
                source = waiting.pop()
                if source not in reachable[document]:
                    reachable[document].add(source)
                    waiting.extend(sources[source])
        cyclic = {document for document in names if document in reachable[document]}
        reported: Set[Path] = set()
        for document in names:
            if document not in cyclic or document in reported:
                continue
            # 互いに到達できる文書のまとまりごとに 1 回報告する
            group = [path for path in names if path in reachable[document] and document in reachable[path]]
            reported.update(group)
            shown = "・".join(names[path][0] for path in group)
            self.error(names[document][0], f"全文のコード片の引用が循環している（{shown}）。コード片を書き直さない")
        order: List[Path] = []
        visited: Set[Path] = set()

        def visit(document: Path) -> None:
            if document in visited:
                return
            visited.add(document)
            for source in sources[document]:
                visit(source)
            order.append(document)

        for document in names:
            visit(document)
        return [name for document in order if document not in cyclic for name in names[document]]

    def update_snippets(self) -> None:
        """全文のコード片を、実ファイルの内容で書き直す。"""
        # 書き直した実体のパス → 書き直した内容。同じ実体を指す別の名前（リンクの文書）は書き直さず、読んだ内容を更新する
        written: Dict[Path, str] = {}
        for name in self.snippet_order():
            text = self.texts[name]
            if self.paths.get(name) in written:
                self.texts[name] = written[self.paths[name]]
                continue
            # 読み込み（load）は改行を LF にそろえるので、元の改行を知るためにファイルを改行の変換なしで読み直す。
            # 読み直した内容が load の時点の内容と違えば（その間に変更された）、書き直さずに再実行を案内する。
            # 本文の行と改行は、どちらもこの読み直した内容から取る（古い内容の本文に新しい内容の改行を組み合わせない）
            try:
                raw_text = self.read_raw(name)
            except (OSError, UnicodeDecodeError) as error:
                self.error(name, f"読み直せないので、コード片を書き直さない: {error}")
                continue
            if raw_text.replace("\r\n", "\n").replace("\r", "\n") != text:
                self.error(name, "読み込んだ後に変更された。コード片を書き直さないので、--update-snippets を実行し直す")
                continue
            raw = split_lines(raw_text, keepends=True)
            lines = [line.rstrip("\r\n") for line in raw]
            endings = [line[len(line.rstrip("\r\n")):] for line in raw]
            opened_fences = fences(text)
            markers = snippet_markers(text)  # 照合と同じ判定（説明用の例は書き直さない）
            out: List[str] = []
            out_endings: List[str] = []  # out の各行の改行
            index = 0
            while index < len(lines):
                out.append(lines[index])
                out_endings.append(endings[index])
                marker = markers.get(index)
                index += 1
                if not marker or marker[0] != "file" or not marker[1]:
                    continue  # パスのない印は検査の側（check_snippets）が報告する
                start = index
                while start < len(lines) and not lines[start].strip():
                    start += 1
                try:
                    path = self.snippet_source(marker[1])
                except Unresolvable:
                    continue  # 検査の側（check_snippets）が印の行とともに報告する
                opened = opened_fences.get(start)
                if not opened or path is None or not path.is_file():
                    continue  # 不備（root の外の参照先を含む）は検査の側で報告する
                end = fence_end(lines, start, opened[0])
                # 字下げしたフェンスには、開始行と同じ字下げを付けて書く（空行には付けない）
                indent = " " * fence_indent(lines[start])
                try:
                    source = split_lines(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue  # このコード片は書き直さない。検査の側（check_snippets）が印の行とともに報告する
                # 字下げを除いた本文が元のファイルと一致するコード片は、行も改行も変えない
                if end < len(lines) and [dedent(line, len(indent)) for line in lines[start + 1:end]] == source:
                    continue
                content = [indent + line if line else line for line in source]
                # このフェンスを閉じてしまう行を含む内容は書き込まない（長いフェンスに手で直す）
                if end >= len(lines) or any(fence_closes(line, opened[0]) for line in content):
                    continue
                out.extend(lines[index:start + 1] + content + [lines[end]])
                # 書き直した本文の行は、フェンスの開始行と同じ改行にする
                out_endings.extend(endings[index:start + 1] + [endings[start] or "\n"] * len(content) + [endings[end]])
                index = end + 1
            # 末尾の改行の有無などコード片以外の違いでは書き直さない（本文の行が変わったときだけ）
            if out == lines:
                continue
            # 書き直すのは、読み込みで解決の経路を確かめた実体のパス（元の綴りを開き直さない）
            target = self.paths[name]
            # 書き直すのは詳細設計のディレクトリの中の文書だけ（リンクした文書の実体がほかの場所にあれば上書きしない）
            if not target.is_relative_to(self.root.resolve() / DETAILED):
                self.error(name, "文書の実パスが詳細設計のディレクトリの外にあるため、コード片を書き直さない")
                continue
            # 書き込む直前にも、読み直した内容から変わっていないことを確かめる（変わっていれば上書きしない）
            try:
                current = self.read_raw(name)
            except (OSError, UnicodeDecodeError) as error:
                self.error(name, f"書き直しの直前に読み直せないので、上書きしない: {error}")
                continue
            if current != raw_text:
                self.error(name, "書き直しの途中で変更された。上書きしないので、--update-snippets を実行し直す")
                continue
            # 元の文書を切り詰めないよう、同じディレクトリの一時ファイルに書いて閉じてから置き換える。
            # 書き込み・閉じるのに失敗したら置き換えず、一時ファイルを消して元の文書を残す
            try:
                handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            except OSError as error:
                self.error(name, f"一時ファイルを作れないので、コード片を書き直さない: {error}")
                continue
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                    stream.write("".join(line + ending for line, ending in zip(out, out_endings)))
                os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
                # 一時ファイルを書いて閉じた後、置き換えの直前にもう一度読み直す（書いている間に文書が変わっていれば上書きしない）
                if self.read_raw(name) != raw_text:
                    os.unlink(temporary)
                    self.error(name, "書き直しの途中で変更された。上書きしないので、--update-snippets を実行し直す")
                    continue
                os.replace(temporary, target)
            except (OSError, UnicodeDecodeError) as error:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                self.error(name, f"書き直した内容を保存できないので、元の文書を残す: {error}")
                continue
            updated = "\n".join(out) + ("\n" if text.endswith("\n") else "")
            written[target] = updated
            # 同じ実体を指すすべての名前の読んだ内容を更新する（先に処理した名前も含む）
            for other, other_path in self.paths.items():
                if other_path == target:
                    self.texts[other] = updated
            print(f"コード片を書き直した: {name}")

    def read_raw(self, name: str) -> str:
        """文書を改行の変換なしで読む。"""
        with self.paths.get(name, self.root / name).open(encoding="utf-8", newline="") as handle:
            return handle.read()

    def check_freshness(self, today: Optional[datetime.date] = None) -> None:
        """技術情報の確認日から暦の 3 か月以上経った文書を警告する（失敗にはしない）。"""
        today = today or datetime.date.today()
        for name, text in self.texts.items():
            dates = []
            for found in re.findall(r"確認日 ?(\d{4})-(\d{2})-(\d{2})", text):
                try:
                    dates.append(datetime.date(*(int(part) for part in found)))
                except ValueError:
                    self.error(name, f"確認日の日付が不正: {'-'.join(found)}")
            if dates and stale(min(dates), today):
                self.warnings.append(f"{name}: 確認日 {min(dates)} から 3 か月以上経った記述がある。再確認する")

    def missing_inputs(self) -> List[str]:
        """読むはずの docs/ と定義文書のうち、ないもの。ないまま検査すると、何も調べずに通るため。"""
        if not (self.root / "docs").is_dir():
            return [f"{self.root / 'docs'}（docs ディレクトリ）"]
        return [path for path in sorted(set(DEFINED_IN.values())) if not (self.root / path).is_file()]

    def run(self, update: bool = False) -> int:
        missing = self.missing_inputs()
        if missing:
            for path in missing:
                print(f"NG: 読むものがない: {path}", file=sys.stderr)
            return 2
        self.load()
        if update:
            self.update_snippets()
        self.collect_definitions()
        self.check_references()
        self.check_coverage()
        self.check_format()
        self.check_design_documents()
        self.check_freshness()
        for message in self.warnings:
            print(f"警告: {message}")
        for message in self.errors:
            print(f"NG: {message}")
        if self.errors:
            print(f"NG: 文書の不整合 {len(self.errors)} 件")
            return 1
        print(f"OK: 文書 {len(self.texts)} 件・定義済みの ID {len(self.defined)} 件・MVP の要件 {len(self.mvp)} 件を網羅")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="設計文書の整合の検査")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--update-snippets", action="store_true")
    arguments = parser.parse_args()
    return Checker(arguments.root.resolve()).run(update=arguments.update_snippets)


if __name__ == "__main__":
    sys.exit(main())
