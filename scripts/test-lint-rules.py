#!/usr/bin/python3
"""静的解析の規則そのものを試験する（BD-DEV-05）。

tools/lint-fixtures/<規則名>/ の各ファイルを SwiftLint にかけ、
「// expect: <規則名>」の付いた行だけがその規則で検出されることを確かめる。

  violation.swift  検出される例（印が 1 行以上）
  violation/**     ほかの規則の例外の場所などのパスに置いた違反（印が 1 行以上。検出されること）
  ok.swift         検出されない例（印なし）
  allowed/**       例外を認める場所のパスに置いた違反（印なし。検出されないこと）

規則に試験用のファイルがない場合も失敗する（試験のない規則を作らせない）。
終了コード: 0 = 全件一致 / 1 = 不一致あり / 2 = 実行できない
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".swiftlint.yml"
FIXTURES = ROOT / "tools" / "lint-fixtures"
SWIFTLINT = ROOT / ".tools" / "bin" / "swiftlint"
# テストコードで外す組み込みの規則（入れ子の設定で無効にする）
BUILTIN_RULES = ("force_try", "force_cast", "force_unwrapping")
# テストコード用の入れ子の設定を試験するディレクトリと、同じ内容を置くテストのディレクトリ
TEST_CODE_FIXTURE = "_test-code"
TEST_CODE_DIRS = ("MinutesMobileTests", "MinutesMobileUITests", "Packages/MinutesKit/Tests")
MARKER = re.compile(r"//\s*expect:\s*([a-z_]+)\s*$")


def custom_rules(config_text: str) -> Dict[str, bool]:
    """.swiftlint.yml の custom_rules から、規則名 → 例外（excluded）の有無 を取り出す。"""
    rules: Dict[str, bool] = {}
    in_block = False
    current = ""
    for line in config_text.splitlines():
        if re.match(r"^custom_rules:\s*$", line):
            in_block = True
            continue
        if not in_block:
            continue
        if re.match(r"^\S", line):  # 次の最上位の鍵で終わる
            break
        name = re.match(r"^  ([a-z][a-z0-9_]*):\s*$", line)
        if name:
            current = name.group(1)
            rules[current] = False
        elif current and re.match(r"^    excluded:", line):
            rules[current] = True
    return rules


def expected_lines(path: Path, rule: str, errors: List[str]) -> Set[int]:
    lines: Set[int] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        found = MARKER.search(line)
        if not found:
            continue
        if found.group(1) != rule:
            errors.append(f"{path.relative_to(ROOT)}:{number}: 印の規則名が違う（{found.group(1)}）")
        lines.add(number)
    return lines


def collect(rules: Dict[str, bool], errors: List[str]) -> List[Tuple[Path, str, Set[int]]]:
    """試験するファイル・規則名・検出されるはずの行 の一覧を作る。"""
    cases: List[Tuple[Path, str, Set[int]]] = []
    for rule, has_exception in sorted(rules.items()):
        directory = FIXTURES / rule
        violation, ok = directory / "violation.swift", directory / "ok.swift"
        if not violation.is_file() or not ok.is_file():
            errors.append(f"規則 {rule} の試験用ファイル（violation.swift と ok.swift）がない")
            continue
        violation_lines = expected_lines(violation, rule, errors)
        if not violation_lines:
            errors.append(f"{violation.relative_to(ROOT)}: 印（// expect: {rule}）が 1 行もない")
        if expected_lines(ok, rule, errors):
            errors.append(f"{ok.relative_to(ROOT)}: 正常例に印がある")
        cases.append((violation, rule, violation_lines))
        cases.append((ok, rule, set()))
        # 例外の場所に似たパスでも検出されることを確かめる例（例外を持たない規則で、パスによる抜け道がないこと）
        placed = sorted((directory / "violation").rglob("*.swift")) if (directory / "violation").is_dir() else []
        for path in placed:
            placed_lines = expected_lines(path, rule, errors)
            if not placed_lines:
                errors.append(f"{path.relative_to(ROOT)}: 印（// expect: {rule}）が 1 行もない")
            cases.append((path, rule, placed_lines))
        allowed = sorted((directory / "allowed").rglob("*.swift")) if (directory / "allowed").is_dir() else []
        if has_exception and not allowed:
            errors.append(f"規則 {rule} には例外の場所があるのに、allowed/ の試験用ファイルがない")
        if allowed and not has_exception:
            errors.append(f"規則 {rule} には例外の場所がないのに、allowed/ がある")
        for path in allowed:
            if expected_lines(path, rule, errors):
                errors.append(f"{path.relative_to(ROOT)}: 例外の場所の例に印がある")
            cases.append((path, rule, set()))
    return cases


def check_test_code(errors: List[str]) -> List[Tuple[Path, str, Set[int]]]:
    """テストコード用の入れ子の設定で、組み込みの 3 規則が外れることを試験する。"""
    directory = FIXTURES / TEST_CODE_FIXTURE
    nested, sample = directory / ".swiftlint.yml", directory / "Sample.swift"
    if not nested.is_file() or not sample.is_file():
        errors.append(f"{TEST_CODE_FIXTURE}/ に .swiftlint.yml と Sample.swift がない")
        return []
    template = nested.read_bytes()
    # 実際のテストのディレクトリには、試験した設定と同じ内容だけを置ける
    for name in TEST_CODE_DIRS:
        real = ROOT / name / ".swiftlint.yml"
        if (ROOT / name).is_dir() and not real.is_file():
            errors.append(f"{name}/.swiftlint.yml がない（{TEST_CODE_FIXTURE}/.swiftlint.yml と同じ内容で置く）")
        elif real.is_file() and real.read_bytes() != template:
            errors.append(f"{name}/.swiftlint.yml が {TEST_CODE_FIXTURE}/.swiftlint.yml と一致しない")
    # それ以外の場所に入れ子の設定を置かせない（規則を外す抜け道になる）
    allowed = {ROOT / name / ".swiftlint.yml" for name in TEST_CODE_DIRS} | {CONFIG, nested}
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", ":(glob)**/.swiftlint.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        # 入れ子の設定を列挙できなければ抜け道を検査できないので、実行できないとして止める
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(detail, file=sys.stderr)
        print("NG: git ls-files を実行できない（入れ子の設定を列挙できない）", file=sys.stderr)
        sys.exit(2)
    for relative in listed:
        if ROOT / relative not in allowed:
            errors.append(f"許可していない場所に入れ子の設定がある: {relative}")
    return [(sample, rule, set()) for rule in BUILTIN_RULES]


def run_swiftlint(paths: List[Path]) -> List[dict]:
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    # --config を付けると入れ子の設定が無効になるので付けない。リポジトリ直下で実行する
    command = [str(SWIFTLINT), "lint", "--quiet", "--no-cache", "--reporter", "json"]
    result = subprocess.run(command + [str(p) for p in paths], cwd=ROOT, env=env, capture_output=True, text=True)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr, file=sys.stderr)
        print("NG: SwiftLint の出力を読めない", file=sys.stderr)
        sys.exit(2)
    if not isinstance(report, list):
        print("NG: SwiftLint の出力の形式が違う", file=sys.stderr)
        sys.exit(2)
    return report


def main() -> int:
    if not SWIFTLINT.exists():
        print("NG: .tools/bin/swiftlint がない。scripts/setup-tools.sh を実行する", file=sys.stderr)
        return 2
    if not CONFIG.is_file():
        print("NG: .swiftlint.yml がない", file=sys.stderr)
        return 1
    errors: List[str] = []
    rules = custom_rules(CONFIG.read_text(encoding="utf-8"))
    if not rules:
        errors.append(".swiftlint.yml に custom_rules が 1 つもない")
    for rule in BUILTIN_RULES:
        rules[rule] = False
    known = set(rules) | {TEST_CODE_FIXTURE}
    for directory in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        if directory.name not in known:
            errors.append(f"tools/lint-fixtures/{directory.name}/ に対応する規則がない")
    cases = collect(rules, errors) + check_test_code(errors)
    if not cases:
        for message in errors:
            print(f"NG: {message}")
        return 1
    reported: Dict[Tuple[str, str], Set[int]] = {}
    for item in run_swiftlint(sorted({path for path, _, _ in cases})):
        key = (os.path.realpath(str(item.get("file", ""))), str(item.get("rule_id", "")))
        reported.setdefault(key, set()).add(int(item.get("line", 0)))
    for path, rule, expected in cases:
        actual = reported.get((os.path.realpath(str(path)), rule), set())
        if actual != expected:
            errors.append(
                f"{path.relative_to(ROOT)}: 規則 {rule} の検出行が違う"
                f"（期待 {sorted(expected)} / 実際 {sorted(actual)}）"
            )
    for message in errors:
        print(f"NG: {message}")
    if errors:
        return 1
    print(f"OK: 規則の試験 {len(rules)} 規則・{len(cases)} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
