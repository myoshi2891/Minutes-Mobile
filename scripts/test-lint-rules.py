#!/usr/bin/python3
"""静的解析の規則そのものを試験する（BD-DEV-05）。

tools/lint-fixtures/<規則名>/ の各ファイルを SwiftLint にかけ、
「// expect: <規則名>」の付いた行だけがその規則で検出されることを確かめる。

  violation.swift  検出される例（印が 1 行以上）
  violation/**     ほかの規則の例外の場所などのパスに置いた違反（印が 1 行以上。検出されること）
  ok.swift         検出されない例（印なし）
  allowed/**       例外を認める場所のパスに置いた違反（印なし。検出されないこと）。対照として、同じ内容を
                   例外の場所でないパス（リポジトリの外の一時ディレクトリ）に写すと検出されることも確かめる

規則に試験用のファイルがない場合も失敗する（試験のない規則を作らせない）。
使い方: scripts/test-lint-rules.py [--staged]
  --staged  入れ子の設定の列挙をインデックス（git ls-files）に対して行う（check.sh --staged がステージした内容で試験するとき）。
            指定しなければ作業ツリーを走査し、Git が無視するファイルも含めて列挙する
終了コード: 0 = 全件一致 / 1 = 不一致あり / 2 = 実行できない
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".swiftlint.yml"
FIXTURES = ROOT / "tools" / "lint-fixtures"
SWIFTLINT = ROOT / ".tools" / "bin" / "swiftlint"
# テストコードで外す組み込みの規則（入れ子の設定で無効にする）
BUILTIN_RULES = ("force_try", "force_cast", "force_unwrapping")
# テストコード用の入れ子の設定を試験するディレクトリと、同じ内容を置くテストのディレクトリ
TEST_CODE_FIXTURE = "_test-code"
TEST_CODE_DIRS = ("MinutesMobileTests", "MinutesMobileUITests", "Packages/MinutesKit/Tests")
# 作業ツリーを走査するときに飛ばすディレクトリ（リポジトリ直下からの相対パス）。.swiftlint.yml の excluded のうち、
# SwiftLint が解析しない場所（設定が対象のソースに適用されない）と、Git の管理領域の .git。
# tools/lint-fixtures は excluded にあるが、この試験が明示したパスで解析し入れ子の設定も効くので、走査の対象にする
WALK_SKIPPED = {".git", ".tools", ".build", "build", "DerivedData"}
# SwiftLint がファイルを読めなかったときに標準エラーへ出す診断（0.65.1 で、不正な UTF-8 のファイルと読み取り権限のない
# ファイルを渡して採取した）。このときも終了コード 0 で、そのファイルを除いた正しい JSON を出すので、出力だけでは気づけない
UNREADABLE_DIAGNOSTIC = re.compile(r"^Could not read contents of `(.+)`$", re.MULTILINE)
# 規則名は custom_rules の規則名と同じ形（先頭は英小文字、以降は英小文字・数字・下線）
MARKER = re.compile(r"//\s*expect:\s*([a-z][a-z0-9_]*)\s*$")


def custom_rules(config_text: str, errors: List[str]) -> Dict[str, bool]:
    """.swiftlint.yml の custom_rules から、規則名 → 例外（excluded）の有無 を取り出す。

    規則名の行は末尾のコメント（「# …」）を許す。字下げ 2 の行で規則名として読めないものは、
    規則を黙って読み飛ばさないよう errors に入れる。
    """
    rules: Dict[str, bool] = {}
    in_block = False
    current = ""
    for line in config_text.splitlines():
        if re.match(r"^custom_rules:\s*(?:#.*)?$", line):
            in_block = True
            continue
        if not in_block:
            continue
        if not line.strip() or re.match(r"^#", line):  # 空行と字下げのないコメントは読み飛ばす
            continue
        if re.match(r"^\S", line):  # 次の最上位の鍵で終わる
            break
        name = re.match(r"^  ([a-z][a-z0-9_]*):\s*(?:#.*)?$", line)
        if name:
            current = name.group(1)
            rules[current] = False
        elif re.match(r"^  [^\s#]", line):
            errors.append(f".swiftlint.yml の custom_rules の行を規則名として読めない: {line.strip()}")
        elif current and re.match(r"^    excluded:", line):
            rules[current] = True
    return rules


def expected_lines(path: Path, rule: str, errors: List[str]) -> Set[int]:
    lines: Set[int] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        # 読めない試験用のファイルは、検出行の不一致（終了コード 1）と区別して、実行できないとして止める
        print(f"NG: 試験用のファイル {path} を読めない: {error}", file=sys.stderr)
        sys.exit(2)
    for number, line in enumerate(text.splitlines(), start=1):
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


def nested_configs_in_worktree() -> List[str]:
    """作業ツリーの .swiftlint.yml を、Git が無視するものも含めて列挙する（リポジトリ直下からの相対パス）。"""
    def failed(error: OSError) -> None:
        # 読めないディレクトリを黙って読み飛ばすと、その中の入れ子の設定（規則を外す抜け道）を見落とすので止める
        print(f"NG: {error.filename} を走査できない（入れ子の設定を列挙できない）: {error}", file=sys.stderr)
        sys.exit(2)

    found: List[str] = []
    for current, directories, files in os.walk(ROOT, onerror=failed):
        relative = Path(current).relative_to(ROOT).as_posix()
        directories[:] = sorted(
            name for name in directories
            if (name if relative == "." else f"{relative}/{name}") not in WALK_SKIPPED
        )
        if ".swiftlint.yml" in files:
            found.append(".swiftlint.yml" if relative == "." else f"{relative}/.swiftlint.yml")
    return found


def nested_configs_in_index() -> List[str]:
    """インデックスにある .swiftlint.yml を列挙する。列挙できなければ実行できないとして止める。"""
    try:
        return subprocess.run(
            ["git", "ls-files", "--cached", "--", ":(glob)**/.swiftlint.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        # 入れ子の設定を列挙できなければ抜け道を検査できないので、実行できないとして止める
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(detail, file=sys.stderr)
        print("NG: git ls-files を実行できない（入れ子の設定を列挙できない）", file=sys.stderr)
        sys.exit(2)


def check_test_code(errors: List[str], staged: bool) -> List[Tuple[Path, str, Set[int]]]:
    """テストコード用の入れ子の設定で、組み込みの 3 規則が外れることを試験する。"""
    directory = FIXTURES / TEST_CODE_FIXTURE
    nested, sample = directory / ".swiftlint.yml", directory / "Sample.swift"
    if not nested.is_file() or not sample.is_file():
        errors.append(f"{TEST_CODE_FIXTURE}/ に .swiftlint.yml と Sample.swift がない")
        return []
    template = nested.read_bytes()
    # 入れ子の設定が外してよいのは組み込みの 3 規則だけ（独自の規則などを外す抜け道にさせない）
    for rule in disabled_rules(template.decode("utf-8", errors="replace"), errors):
        if rule not in BUILTIN_RULES:
            errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml が組み込みの 3 規則以外の規則 {rule} を外している")
    # 実際のテストのディレクトリには、試験した設定と同じ内容だけを置ける
    for name in TEST_CODE_DIRS:
        real = ROOT / name / ".swiftlint.yml"
        if (ROOT / name).is_dir() and not real.is_file():
            errors.append(f"{name}/.swiftlint.yml がない（{TEST_CODE_FIXTURE}/.swiftlint.yml と同じ内容で置く）")
        elif real.is_file() and real.read_bytes() != template:
            errors.append(f"{name}/.swiftlint.yml が {TEST_CODE_FIXTURE}/.swiftlint.yml と一致しない")
    # それ以外の場所に入れ子の設定を置かせない（規則を外す抜け道になる）
    allowed = {ROOT / name / ".swiftlint.yml" for name in TEST_CODE_DIRS} | {CONFIG, nested}
    # 通常は作業ツリー（Git が無視するファイルも含む）、--staged ではインデックスにある設定だけを調べる
    listed = nested_configs_in_index() if staged else nested_configs_in_worktree()
    for relative in listed:
        if ROOT / relative not in allowed:
            errors.append(f"許可していない場所に入れ子の設定がある: {relative}")
    # 入れ子の設定の下でも独自の規則は効いていること（試験用のファイルの print( の行で no_print_logging が検出される）
    print_lines = {number for number, line in enumerate(sample.read_text(encoding="utf-8").splitlines(), start=1)
                   if re.search(r"\bprint\s*\(", line)}
    if not print_lines:
        errors.append(f"{sample.relative_to(ROOT)}: print( の行がない（入れ子の設定で独自の規則が効くことを試験できない）")
    return [(sample, rule, set()) for rule in BUILTIN_RULES] + [(sample, "no_print_logging", print_lines)]


def disabled_rules(config_text: str, errors: List[str]) -> List[str]:
    """設定の disabled_rules の項目を返す。字下げした「- 規則名」の並びだけを読み、読めない行は errors に入れる。"""
    rules: List[str] = []
    in_block = False
    for line in config_text.splitlines():
        if re.match(r"^disabled_rules:\s*(?:#.*)?$", line):
            in_block = True
            continue
        if not in_block or not line.strip() or re.match(r"^\s*#", line):
            continue
        if re.match(r"^\S", line):  # 次の最上位の鍵で終わる
            in_block = False
            continue
        item = re.match(r"^\s+-\s+([a-z][a-z0-9_]*)\s*(?:#.*)?$", line)
        if item:
            rules.append(item.group(1))
        else:
            errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml の disabled_rules の行を読めない: {line.strip()}")
    return rules


def check_sample_violates(errors: List[str]) -> None:
    """入れ子の設定の試験用のファイルが、通常の設定では組み込みの 3 規則すべてに違反すること。

    違反のないファイルで「入れ子の設定では検出されない」を確かめても、規則を外したことの証拠にならないため。
    --config を付けると入れ子の設定が無効になるので、通常の設定だけで検査できる。
    """
    sample = FIXTURES / TEST_CODE_FIXTURE / "Sample.swift"
    if not sample.is_file():
        return  # 欠落は check_test_code が報告する
    found = {str(item.get("rule_id", "")) for item in run_swiftlint([sample], CONFIG)}
    for rule in BUILTIN_RULES:
        if rule not in found:
            errors.append(f"{sample.relative_to(ROOT)}: 通常の設定で規則 {rule} の違反が検出されない（入れ子の設定の試験にならない）")


def run_swiftlint(paths: List[Path], config: Optional[Path] = None) -> List[dict]:
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    # --config を付けると入れ子の設定が無効になるので、通常は付けない（入れ子の設定を無効にして調べるときだけ渡す）。
    # リポジトリ直下で実行する
    command = [str(SWIFTLINT), "lint", "--quiet", "--no-cache", "--reporter", "json"]
    if config is not None:
        command += ["--config", str(config)]
    try:
        result = subprocess.run(command + [str(p) for p in paths], cwd=ROOT, env=env, capture_output=True, text=True)
    except OSError as error:
        print(f"NG: SwiftLint（{SWIFTLINT}）を起動できない: {error}", file=sys.stderr)
        sys.exit(2)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr, file=sys.stderr)
        print("NG: SwiftLint の出力を読めない", file=sys.stderr)
        sys.exit(2)
    if not isinstance(report, list):
        print("NG: SwiftLint の出力の形式が違う", file=sys.stderr)
        sys.exit(2)
    # 読めなかったファイルは検出が 0 件になり、正常例と区別できないので、試験を実行できなかったものとして止める。
    # 標準エラーのほかの出力や終了コードでは判定しない（重大な違反があると終了コード 2 になるため）
    unreadable = UNREADABLE_DIAGNOSTIC.findall(result.stderr)
    if unreadable:
        for path in unreadable:
            print(f"NG: SwiftLint が {path} を読めなかった（試験にならない）", file=sys.stderr)
        sys.exit(2)
    return report


def check_allowed_controls(cases: List[Tuple[Path, str, Set[int]]], rules: Dict[str, bool], errors: List[str]) -> None:
    """allowed/ の各ファイルを、例外の場所でないパスに写すと規則が検出すること（例外が検出を止めていることの対照）。

    allowed/ の中で検出されないことだけでは、そもそも違反を含まない内容でも通ってしまう。
    写す先はリポジトリの外の一時ディレクトリ（作業ツリーを変えない）で、例外のパターンに当たる階層の名前を含まない。
    入れ子の設定が効かない場所なので、通常の設定を --config で渡す。
    """
    allowed = [(path, rule) for path, rule, _ in cases if rules.get(rule) and "allowed" in path.relative_to(FIXTURES).parts]
    if not allowed:
        return
    with tempfile.TemporaryDirectory() as directory:
        copies: Dict[Path, Tuple[Path, str]] = {}
        for index, (path, rule) in enumerate(allowed):
            copy = Path(directory) / "Control" / f"Control{index}.swift"
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copy)
            copies[copy] = (path, rule)
        found: Set[Tuple[str, str]] = set()
        for item in run_swiftlint(sorted(copies), CONFIG):
            found.add((os.path.realpath(str(item.get("file", ""))), str(item.get("rule_id", ""))))
        for copy, (path, rule) in copies.items():
            if (os.path.realpath(str(copy)), rule) not in found:
                errors.append(f"{path.relative_to(ROOT)}: 例外の場所でないパスに写しても規則 {rule} が検出されない"
                              "（例外の場所の試験にならない。違反する式を含める）")


def main() -> int:
    arguments = sys.argv[1:]
    if arguments not in ([], ["--staged"]):
        print(f"使い方: {sys.argv[0]} [--staged]", file=sys.stderr)
        return 2
    staged = arguments == ["--staged"]
    if not SWIFTLINT.exists():
        print("NG: .tools/bin/swiftlint がない。scripts/setup-tools.sh を実行する", file=sys.stderr)
        return 2
    if not CONFIG.is_file():
        print("NG: .swiftlint.yml がない", file=sys.stderr)
        return 1
    errors: List[str] = []
    rules = custom_rules(CONFIG.read_text(encoding="utf-8"), errors)
    if not rules:
        errors.append(".swiftlint.yml に custom_rules が 1 つもない")
    for rule in BUILTIN_RULES:
        rules[rule] = False
    known = set(rules) | {TEST_CODE_FIXTURE}
    for directory in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        if directory.name not in known:
            errors.append(f"tools/lint-fixtures/{directory.name}/ に対応する規則がない")
    cases = collect(rules, errors) + check_test_code(errors, staged)
    check_sample_violates(errors)
    if not cases:
        for message in errors:
            print(f"NG: {message}")
        return 1
    reported: Dict[Tuple[str, str], Set[int]] = {}
    for item in run_swiftlint(sorted({path for path, _, _ in cases})):
        key = (os.path.realpath(str(item.get("file", ""))), str(item.get("rule_id", "")))
        reported.setdefault(key, set()).add(int(item.get("line", 0)))
    check_allowed_controls(cases, rules, errors)
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
