#!/usr/bin/python3
"""静的解析の規則そのものを試験する（BD-DEV-05）。

tools/lint-fixtures/<規則名>/ の各ファイルを SwiftLint にかけ、
「// expect: <規則名>」の付いた行だけがその規則で検出されることを確かめる。

  violation.swift  検出される例（印が 1 行以上）
  violation/**     ほかの規則の例外の場所などのパスに置いた違反（印が 1 行以上。検出されること）
  ok.swift         検出されない例（印なし）
  allowed/**       例外を認める場所のパスに置いた違反（印なし。検出されないこと）。対照として、その規則の
                   例外だけを除いた設定では、同じパスのまま検出されることも確かめる

規則に試験用のファイルがない場合も失敗する（試験のない規則を作らせない）。
使い方: scripts/test-lint-rules.py [--staged]
  --staged  入れ子の設定の列挙をインデックス（git ls-files）に対して行う（check.sh --staged がステージした内容で試験するとき）。
            指定しなければ作業ツリーを走査し、Git が無視するファイルも含めて列挙する
終了コード: 0 = 全件一致 / 1 = 不一致あり / 2 = 実行できない
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".swiftlint.yml"
FIXTURES = ROOT / "tools" / "lint-fixtures"
# 開発ツールの場所。check.sh は照合した写しを MINUTES_TOOLS_DIR で渡す（なければリポジトリの .tools）
SWIFTLINT = Path(os.environ.get("MINUTES_TOOLS_DIR") or ROOT / ".tools") / "bin" / "swiftlint"
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
    規則の属性（excluded など）は、規則名の直下の階層（最初の属性の行の字下げ）で判定する。この検査と without_excluded が
    読めるのは字下げ 4 の属性と字下げ 6 の excluded の項目だけなので、ほかの字下げで書いた属性・項目は、YAML として正しくても
    読み落とさないよう errors に入れる（excluded を見落として例外のない規則と取り違えない）。
    """
    rules: Dict[str, bool] = {}
    in_block = False
    current = ""
    attribute_indent: Optional[int] = None  # 規則の属性の階層の字下げ（規則ごとに最初の属性の行で決める）
    in_excluded = False  # excluded の値（項目の並び）の中
    excluded_key = re.compile(r"""(?:excluded|'excluded'|"excluded")\s*:""")
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
            attribute_indent = None
            in_excluded = False
        elif re.match(r"^  [^\s#]", line):
            errors.append(f".swiftlint.yml の custom_rules の行を規則名として読めない: {line.strip()}")
        elif current and not line.lstrip(" ").startswith("#"):
            indent = len(line) - len(line.lstrip(" "))
            body = line.strip()
            if attribute_indent is None:
                attribute_indent = indent
                if indent != 4:
                    errors.append(f".swiftlint.yml の規則 {current} の属性は字下げ 4 で書く（字下げ {indent} は読めない）: {body}")
            if in_excluded and body.startswith("-") and indent != 6:
                # YAML は属性と同じ字下げの項目の並びも認めるが、without_excluded は字下げ 6 の項目だけを読む
                errors.append(f".swiftlint.yml の規則 {current} の excluded の項目は字下げ 6 で書く（字下げ {indent} は読めない）: {body}")
            elif indent == attribute_indent:
                # 「excluded :」のようにコロンの前の空白も許す。引用符で囲んだ鍵（'excluded'・"excluded"）も同じ鍵として扱う
                in_excluded = excluded_key.match(body) is not None
                if in_excluded:
                    rules[current] = True
                    # 値を同じ行に書いた形（文字列・[a, b] の流れの形など）は without_excluded が正しく除けないので認めない
                    # （複数行にわたる値の続きを残し、対照の設定を壊さない）
                    value = body[excluded_key.match(body).end():].strip()
                    if value and not value.startswith("#"):
                        errors.append(f".swiftlint.yml の規則 {current} の excluded の値は同じ行に書かない（字下げ 6 の「- 」の並びで書く）: {body}")
            elif excluded_key.match(body) and indent < attribute_indent:
                errors.append(f".swiftlint.yml の規則 {current} の excluded の字下げ {indent} が属性の階層と違う: {body}")
    return rules


def without_excluded(config_text: str, rule: str) -> str:
    """.swiftlint.yml から、規則 rule の excluded（鍵の行と字下げ 6 の項目）だけを除いた設定を返す（ほかの行は字面のまま）。"""
    lines: List[str] = []
    in_block = in_excluded = False
    current = ""
    for line in config_text.splitlines():
        if re.match(r"^custom_rules:\s*(?:#.*)?$", line):
            in_block = True
        elif re.match(r"^[^\s#]", line):
            in_block = False
        name = re.match(r"^  ([a-z][a-z0-9_]*):\s*(?:#.*)?$", line) if in_block else None
        if name:
            current = name.group(1)
        if in_block and current == rule and re.match(r"""^    (?:excluded|'excluded'|"excluded")\s*:""", line):
            in_excluded = True
            continue
        if in_excluded and (re.match(r"^      -\s", line) or re.match(r"^\s*(?:#.*)?$", line)):
            continue
        in_excluded = False
        lines.append(line)
    return "\n".join(lines) + "\n"


def read_fixture(path: Path) -> str:
    """試験用のファイルを読む。読めない試験用のファイルは、検出行の不一致（終了コード 1）と区別して、実行できないとして止める。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"NG: 試験用のファイル {path} を読めない: {error}", file=sys.stderr)
        sys.exit(2)


def read_config(path: Path) -> bytes:
    """設定（直下と入れ子の .swiftlint.yml）を読む。読めない・UTF-8 として読めない設定は、検出行の不一致（終了コード 1）と
    区別して、実行できないとして止める（read_fixture と同じ扱い）。"""
    try:
        data = path.read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"NG: 設定 {path} を読めない: {error}", file=sys.stderr)
        sys.exit(2)
    return data


def expected_lines(path: Path, rule: str, errors: List[str]) -> Set[int]:
    lines: Set[int] = set()
    text = read_fixture(path)
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
        # 大文字小文字を区別しない（既定の APFS では .SwiftLint.yml も SwiftLint が .swiftlint.yml として読む）。
        # 結果には実際のファイル名を入れる
        for name in sorted(name for name in files if name.casefold() == ".swiftlint.yml"):
            found.append(name if relative == "." else f"{relative}/{name}")
    return found


def nested_configs_in_index() -> List[str]:
    """インデックスにある .swiftlint.yml を列挙する。列挙できなければ実行できないとして止める。"""
    try:
        listed = subprocess.run(
            # 大文字小文字を区別しない（nested_configs_in_worktree と同じ。.SwiftLint.yml も SwiftLint が読む）。結果は実際のパス。
            # -z で NUL 区切りの生のパスを読み、os.fsdecode で戻す（-z なしでは ASCII 以外の文字を含むパスが引用符と
            # 8 進数のエスケープで出力され、報告と照合に実際のパスを使えない）
            ["git", "ls-files", "--cached", "-z", "--", ":(glob,icase)**/.swiftlint.yml"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        return [os.fsdecode(path) for path in listed.split(b"\0") if path]
    except (OSError, subprocess.CalledProcessError) as error:
        # 入れ子の設定を列挙できなければ抜け道を検査できないので、実行できないとして止める
        detail = (os.fsdecode(error.stderr).strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr
                  else str(error))
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
    template = read_config(nested)
    # 入れ子の設定が外してよいのは組み込みの 3 規則だけ（独自の規則などを外す抜け道にさせない）
    for rule in disabled_rules(template.decode("utf-8"), errors):
        if rule not in BUILTIN_RULES:
            errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml が組み込みの 3 規則以外の規則 {rule} を外している")
    # 実際のテストのディレクトリには、試験した設定と同じ内容だけを置ける
    for name in TEST_CODE_DIRS:
        real = ROOT / name / ".swiftlint.yml"
        if (ROOT / name).is_dir() and not real.is_file():
            errors.append(f"{name}/.swiftlint.yml がない（{TEST_CODE_FIXTURE}/.swiftlint.yml と同じ内容で置く）")
        elif real.is_file() and read_config(real) != template:
            errors.append(f"{name}/.swiftlint.yml が {TEST_CODE_FIXTURE}/.swiftlint.yml と一致しない")
    # それ以外の場所に入れ子の設定を置かせない（規則を外す抜け道になる）
    allowed = {ROOT / name / ".swiftlint.yml" for name in TEST_CODE_DIRS} | {CONFIG, nested}
    # 通常は作業ツリー（Git が無視するファイルも含む）、--staged ではインデックスにある設定だけを調べる
    listed = nested_configs_in_index() if staged else nested_configs_in_worktree()
    for relative in listed:
        if ROOT / relative not in allowed:
            errors.append(f"許可していない場所に入れ子の設定がある: {relative}")
    # 入れ子の設定の下でも独自の規則は効いていること（試験用のファイルの print( の行で no_print_logging が検出される）
    # 期待する行は、print の呼び出しの行に付けた印（// expect: no_print_logging）で決める（本文の文字の一致では決めない）
    print_lines = expected_lines(sample, "no_print_logging", errors)
    if not print_lines:
        errors.append(f"{sample.relative_to(ROOT)}: 印（// expect: no_print_logging）の付いた print の行がない"
                      "（入れ子の設定で独自の規則が効くことを試験できない）")
    return [(sample, rule, set()) for rule in BUILTIN_RULES] + [(sample, "no_print_logging", print_lines)]


def disabled_rules(config_text: str, errors: List[str]) -> List[str]:
    """入れ子の設定の内容を確かめ、disabled_rules の項目を返す。

    認める最上位の鍵は disabled_rules だけで、値は字下げした「- 規則名」の並び（ブロックの形）に限る。
    ほかの鍵（custom_rules で許可した規則を上書きする・only_rules・excluded など）、流れの形（[a, b]）や同じ行の値、
    読めない行は errors に入れる（読み飛ばすと、規則を外す抜け道を見落とすため）。
    """
    rules: List[str] = []
    state = ""  # "rules" = disabled_rules の中、"rejected" = 認めない鍵の中（中の行は重ねて報告しない）
    for line in config_text.splitlines():
        if not line.strip() or re.match(r"^\s*#", line):
            continue
        if re.match(r"^\S", line):  # 最上位の行
            state = "rejected"
            key = re.match(r"^([A-Za-z_][\w-]*)\s*:(.*)$", line)
            if not key:
                errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml の最上位の行を読めない: {line.strip()}")
            elif key.group(1) != "disabled_rules":
                errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml に認めない設定 {key.group(1)} がある（disabled_rules だけを置く）")
            elif re.sub(r"\s+#.*$", "", key.group(2)).strip():
                errors.append(f"{TEST_CODE_FIXTURE}/.swiftlint.yml の disabled_rules は字下げした「- 規則名」の並びで書く"
                              f"（読めない形: {line.strip()}）")
            else:
                state = "rules"
            continue
        if state == "rejected":
            continue
        item = re.match(r"^\s+-\s+([a-z][a-z0-9_]*)\s*(?:#.*)?$", line) if state == "rules" else None
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
    """allowed/ の各ファイルが、その規則の excluded だけを除いた設定では、同じパスのまま検出されること
    （例外が検出を止めていることの対照）。

    allowed/ の中で検出されないことだけでは、そもそも違反を含まない内容でも通ってしまう。パスを変えずに設定だけを変えるので、
    検出されない理由をパスの違い（ほかの例外の場所など）と取り違えない。本来の試験は元の設定で行う（ここでは変えない）。
    --config に渡す設定は一時ディレクトリに置く（入れ子の設定は効かない）。
    """
    by_rule: Dict[str, List[Path]] = {}
    for path, rule, _ in cases:
        if rules.get(rule) and "allowed" in path.relative_to(FIXTURES).parts:
            by_rule.setdefault(rule, []).append(path)
    text = read_config(CONFIG).decode("utf-8")
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "control.yml"
        for rule, paths in sorted(by_rule.items()):
            config.write_text(without_excluded(text, rule), encoding="utf-8")
            found = {os.path.realpath(str(item.get("file", ""))) for item in run_swiftlint(paths, config)
                     if item.get("rule_id") == rule}
            for path in paths:
                if os.path.realpath(path) not in found:
                    errors.append(f"{path.relative_to(ROOT)}: 例外を除いた設定でも規則 {rule} が検出されない"
                                  "（例外の場所の試験にならない。違反する式を含める）")


def main() -> int:
    arguments = sys.argv[1:]
    if arguments not in ([], ["--staged"]):
        print(f"使い方: {sys.argv[0]} [--staged]", file=sys.stderr)
        return 2
    staged = arguments == ["--staged"]
    if not SWIFTLINT.exists():
        print(f"NG: {SWIFTLINT} がない。scripts/setup-tools.sh を実行する", file=sys.stderr)
        return 2
    if not CONFIG.is_file():
        # 設定がなければ試験を実行できない（read_config の読めない設定と同じく 2。検出行の不一致の 1 と区別する）
        print("NG: .swiftlint.yml がない", file=sys.stderr)
        return 2
    errors: List[str] = []
    rules = custom_rules(read_config(CONFIG).decode("utf-8"), errors)
    if not rules:
        errors.append(".swiftlint.yml に custom_rules が 1 つもない")
    for rule in BUILTIN_RULES:
        rules[rule] = False
    known = set(rules) | {TEST_CODE_FIXTURE}
    # 試験用のファイルの置き場所を列挙できなければ、規則の不一致（終了コード 1）と区別して実行できないとして止める
    try:
        fixture_directories = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    except OSError as error:
        print(f"NG: 試験用のファイルの置き場所 {FIXTURES} を列挙できない: {error}", file=sys.stderr)
        return 2
    for directory in fixture_directories:
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
