#!/usr/bin/python3
"""設計文書の整合を検査する（docs/README.md §2〜§4、NFR-MNT-04）。

  1. ID の定義が重複していない。文書が参照する ID がすべて定義されている
  2. MVP の FR / NFR / SEC が、基本設計の「満たす要件」のどこかに現れる
  3. 相対リンクの先がある。コードフェンスに言語の指定がある。1 文書が 2,000 行未満である
  4. 基本設計・詳細設計の各文書に「セキュリティ」節と「テスト観点」節があり、各節の冒頭に要件 ID がある
  5. 詳細設計が、対応する基本設計の「詳細設計への申し送り」の項目をすべて表で解決している
  6. 詳細設計のコード片が実ファイルと一致する（<!-- file: パス --> は全文、<!-- excerpt: パス --> は連続した一部）

使い方: scripts/verify-docs.py [--root <リポジトリの起点>] [--update-snippets]
  --update-snippets  全文のコード片（<!-- file: パス -->）を実ファイルの内容で書き直してから検査する。
                     実ファイルを変えたあと、文書を同じ作業で直すために使う（一部のコード片は手で直す）
終了コード: 0 = 問題なし / 1 = 不整合あり / 2 = 読むもの（docs/・定義文書）がない
"""
import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
MAX_LINES = 2000
STALE_DAYS = 92  # 確認日から 3 か月（docs/README.md §4）

AREA_ID = r"(?:FR|NFR|SEC|TST|BD)-[A-Z]+-\d+"
PLAIN_ID = r"(?:CON|INV|OQ|UD|COST)-\d+"
# 省略した書き方: 範囲（〜）・並び（・）・スラッシュ区切りの並び（「/」の前後に空白 1 つまで）
SEPARATOR = r"(・|〜| ?/ ?)"
ID_PATTERN = re.compile(rf"\b({AREA_ID}|{PLAIN_ID})((?:(?:[・〜]| ?/ ?)\d+)*)")
GROUP_PATTERN = re.compile(r"\b(FR|NFR|SEC|TST)-([A-Z]+(?:・[A-Z]+)*) ?群")
DEFINITION_ROW = re.compile(rf"^\| (?:\*\*)?({AREA_ID}|{PLAIN_ID})(?:\*\*)? \|")
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
DETAILED = "docs/03-detailed-design"


Fence = Tuple[str, int]  # フェンスの文字（` か ~）と長さ
FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})(.*)$")


def fence_open(line: str) -> Optional[Tuple[Fence, str]]:
    """フェンスの開始行なら（フェンス, 情報文字列）を返す。バッククォートのフェンスの情報文字列にはバッククォートを含められない。"""
    found = FENCE_OPEN.match(line.lstrip())
    if not found:
        return None
    marker, info = found.group(1), found.group(2)
    if marker[0] == "`" and "`" in info:
        return None
    return (marker[0], len(marker)), info.strip()


def fence_closes(line: str, fence: Fence) -> bool:
    """開始と同じ文字が開始以上の長さで並び、その後が空白だけの行だけが、フェンスを閉じる。"""
    char, length = fence
    return re.fullmatch(rf"{re.escape(char)}{{{length},}}\s*", line.lstrip()) is not None


def fence_end(lines: List[str], start: int) -> int:
    """lines[start] で開いたフェンスを閉じる行の位置を返す。閉じていなければ len(lines)。"""
    opened = fence_open(lines[start])
    assert opened is not None
    end = start + 1
    while end < len(lines) and not fence_closes(lines[end], opened[0]):
        end += 1
    return end


def fenced_lines(text: str) -> Iterator[Tuple[int, str, bool, Optional[Tuple[Fence, str]]]]:
    """各行を（行番号, 行, フェンスの中か, この行で開いたフェンス）で返す。フェンスの開始・終了の行は「中」とする。"""
    fence: Optional[Fence] = None
    for number, line in enumerate(text.splitlines(), start=1):
        if fence is not None:
            if fence_closes(line, fence):
                fence = None
            yield number, line, True, None
            continue
        opened = fence_open(line)
        if opened:
            fence = opened[0]
        yield number, line, opened is not None, opened


def prose_lines(text: str) -> Iterator[Tuple[int, str]]:
    """コードフェンスの外の行を、行番号つきで返す。"""
    for number, line, inside, _ in fenced_lines(text):
        if not inside:
            yield number, line


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


def table_cells(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


class Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.texts: Dict[str, str] = {}
        self.defined: Dict[str, str] = {}
        self.mvp: Set[str] = set()

    def error(self, path: str, message: str, line: Optional[int] = None) -> None:
        where = f"{path}:{line}" if line else path
        self.errors.append(f"{where}: {message}")

    def load(self) -> None:
        paths = sorted((self.root / "docs").rglob("*.md"))
        paths += [self.root / name for name in ("README.md", "CLAUDE.md") if (self.root / name).is_file()]
        for path in paths:
            self.texts[path.relative_to(self.root).as_posix()] = path.read_text(encoding="utf-8")

    def collect_definitions(self) -> None:
        """定義文書の表から ID を集める。要件表の「フェーズ」列が MVP の FR / NFR / SEC も集める。"""
        for name, text in self.texts.items():
            phase_column = -1
            for number, line in prose_lines(text):
                if not line.startswith("|"):
                    phase_column = -1
                    continue
                cells = table_cells(line)
                if "フェーズ" in cells and not DEFINITION_ROW.match(line):
                    phase_column = cells.index("フェーズ")
                found = DEFINITION_ROW.match(line)
                if not found:
                    continue
                identifier = found.group(1)
                prefix = identifier.split("-", 1)[0]
                home = DEFINED_IN.get(prefix, BASIC if prefix == "BD" else "")
                if not (name == home or (prefix == "BD" and name.startswith(BASIC + "/"))):
                    continue
                # 基本設計では、設計決定の表（先頭の列が ID）の行だけを定義として扱う
                if prefix == "BD" and not re.match(r"^\| BD-[A-Z]+-\d+ \| ", line):
                    continue
                if identifier in self.defined:
                    self.error(name, f"{identifier} の定義が重複している（{self.defined[identifier]}）", number)
                    continue
                self.defined[identifier] = f"{name}:{number}"
                if prefix in ("FR", "NFR", "SEC") and 0 <= phase_column < len(cells):
                    if cells[phase_column].startswith("MVP"):
                        self.mvp.add(identifier)

    def check_references(self) -> None:
        prefixes = {identifier.rsplit("-", 1)[0] for identifier in self.defined}
        for name, text in self.texts.items():
            for number, line in prose_lines(text):
                for match in ID_PATTERN.finditer(line):
                    if reversed_range(match):
                        self.error(name, f"逆順の範囲: {match.group(0)}（終点が直前の番号より小さい）", number)
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
            for _, line in prose_lines(text):
                if not re.match(r"^(\| )?満たす要件", line):
                    continue
                # names_requirements と同じく「／」の前だけを見る（後ろの決定・関連の参照は網羅に数えない）
                line = line.split("／", 1)[0]
                for match in ID_PATTERN.finditer(line):
                    covered.update(expand(match))
                for match in GROUP_PATTERN.finditer(line):
                    for area in match.group(2).split("・"):
                        stem = f"{match.group(1)}-{area}-"
                        covered.update(identifier for identifier in self.defined if identifier.startswith(stem))
                if "SEC 全般" in line:
                    covered.update(identifier for identifier in self.defined if identifier.startswith("SEC-"))
        for identifier in sorted(self.mvp - covered):
            self.error(BASIC, f"MVP の要件 {identifier} が、どの文書の「満たす要件」にも現れない")

    def check_format(self) -> None:
        for name, text in self.texts.items():
            lines = text.splitlines()
            if len(lines) >= MAX_LINES:
                self.error(name, f"{len(lines)} 行ある（{MAX_LINES} 行未満に分割する）")
            for number, _, _, opened in fenced_lines(text):
                if opened is None:
                    continue
                if not opened[1]:
                    self.error(name, "コードフェンスに言語の指定がない", number)
                if fence_end(lines, number - 1) >= len(lines):
                    self.error(name, "コードフェンスが閉じていない", number)
            for number, line in prose_lines(text):
                for target in re.findall(r"\]\(([^)\s]+)\)", line):
                    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                        continue
                    path = (self.root / name).parent / target.split("#", 1)[0]
                    if not path.exists():
                        self.error(name, f"リンクの先がない: {target}", number)

    def sections(self, text: str) -> List[Tuple[int, str, List[str]]]:
        """「## 」の節ごとに、行番号・見出し・本文の行を返す。"""
        result: List[Tuple[int, str, List[str]]] = []
        for number, line in prose_lines(text):
            if line.startswith("## "):
                result.append((number, line[3:].strip(), []))
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
                # 節が小節から始まる場合は、最初の小節の冒頭を見る
                first = next((line for line in body if line.strip() and not line.startswith("### ")), "")
                if not first.startswith("満たす要件:"):
                    self.error(name, f"節「{heading}」の冒頭に「満たす要件:」の行がない", number)
                elif not self.names_requirements(first):
                    self.error(name, f"節「{heading}」の「満たす要件:」に要件 ID も「—」の指定もない", number)
            if name.endswith("/README.md"):
                # 索引には必須の節と申し送りを求めないが、コード片は照合する
                if directory == DETAILED:
                    self.check_snippets(name, text)
                continue
            headings = [heading for _, heading, _ in sections]
            required = ["セキュリティ", "テスト観点"]
            if directory == DETAILED:
                required += ["申し送り", "要件 ID ↔ テスト"]
            for word in required:
                if not any(word in heading for heading in headings):
                    self.error(name, f"「{word}」の節がない")
            if directory == DETAILED:
                self.check_handover(name, sections)
                self.check_snippets(name, text)

    def names_requirements(self, line: str) -> bool:
        """「満たす要件:」の後（「／」の前まで）に、定義済みの要件 ID・要件の群・「SEC 全般」・「—」（満たす要件なし）のどれかがあるか。"""
        spec = line.split(":", 1)[1].split("／", 1)[0].strip()
        if spec.startswith("—") or "SEC 全般" in spec:
            return True
        for match in ID_PATTERN.finditer(spec):
            if any(identifier in self.defined and re.match(REQUIREMENT_PREFIX, identifier) for identifier in expand(match)):
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
            item_name(line[2:]) for _, heading, body in self.sections(self.texts[basic_name]) if "申し送り" in heading
            for line in body if line.startswith("- ")
        ]
        # 表ごとに（連続した「|」の行のまとまりごとに）区切り、各表の見出しの行を除く
        rows: List[str] = []
        for _, heading, body in sections:
            if "申し送り" not in heading:
                continue
            table: List[str] = []
            for line in body + [""]:
                if line.startswith("|"):
                    if not re.match(r"^\|[ :|-]+\|$", line):
                        table.append(line)
                    continue
                rows.extend(table[1:])
                table = []
        # 解決の列（2 列目）が空・空白だけの行は、解決したことにしない
        resolved = []
        for line in rows:
            cells = table_cells(line)
            if len(cells) < 2 or not cells[1].strip():
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
        lines = text.splitlines()
        for number, line, inside, _ in fenced_lines(text):
            if inside:
                continue  # フェンスの中の印は説明用の例であり、照合しない
            index = number - 1
            marker = re.match(r"^<!-- (file|excerpt): (\S+) -->$", line.strip())
            if not marker:
                continue
            start = index + 1
            while start < len(lines) and not lines[start].strip():
                start += 1
            if start >= len(lines) or not fence_open(lines[start]):
                self.error(name, "印の直後にコードフェンスがない", index + 1)
                continue
            end = fence_end(lines, start)
            snippet = lines[start + 1:end]
            path = self.root / marker.group(2)
            if not path.is_file():
                self.error(name, f"コード片の元のファイルがない: {marker.group(2)}", index + 1)
                continue
            actual = path.read_text(encoding="utf-8").splitlines()
            if marker.group(1) == "file":
                matches = snippet == actual
            else:
                size = len(snippet)
                matches = size > 0 and any(actual[at:at + size] == snippet for at in range(len(actual) - size + 1))
            if not matches:
                self.error(name, f"コード片が {marker.group(2)} と一致しない", index + 1)

    def update_snippets(self) -> None:
        """全文のコード片を、実ファイルの内容で書き直す。"""
        for name, text in sorted(self.texts.items()):
            if not name.startswith(DETAILED + "/"):
                continue
            lines = text.splitlines()
            # フェンスの外の行の位置（フェンスの中の印は説明用の例であり、書き直さない）
            outside = {number - 1 for number, _, inside, _ in fenced_lines(text) if not inside}
            out: List[str] = []
            index = 0
            while index < len(lines):
                out.append(lines[index])
                marker = re.match(r"^<!-- file: (\S+) -->$", lines[index].strip()) if index in outside else None
                index += 1
                if not marker:
                    continue
                start = index
                while start < len(lines) and not lines[start].strip():
                    start += 1
                path = self.root / marker.group(1)
                opened = fence_open(lines[start]) if start < len(lines) else None
                if not opened or not path.is_file():
                    continue  # 不備は検査の側で報告する
                end = fence_end(lines, start)
                content = path.read_text(encoding="utf-8").splitlines()
                # このフェンスを閉じてしまう行を含む内容は書き込まない（長いフェンスに手で直す）
                if end >= len(lines) or any(fence_closes(line, opened[0]) for line in content):
                    continue
                out.extend(lines[index:start + 1] + content + [lines[end]])
                index = end + 1
            updated = "\n".join(out) + "\n"
            if updated != text:
                (self.root / name).write_text(updated, encoding="utf-8")
                self.texts[name] = updated
                print(f"コード片を書き直した: {name}")

    def check_freshness(self) -> None:
        """技術情報の確認日が 3 か月を過ぎた文書を警告する（失敗にはしない）。"""
        today = datetime.date.today()
        for name, text in self.texts.items():
            dates = []
            for found in re.findall(r"確認日 ?(\d{4})-(\d{2})-(\d{2})", text):
                try:
                    dates.append(datetime.date(*(int(part) for part in found)))
                except ValueError:
                    self.error(name, f"確認日の日付が不正: {'-'.join(found)}")
            if dates and (today - min(dates)).days > STALE_DAYS:
                self.warnings.append(f"{name}: 確認日 {min(dates)} から 3 か月を過ぎた記述がある。再確認する")

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
